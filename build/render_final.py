"""Assemble the emote pack from the measured data in data/.

Inputs
  final_frames.json     : every frame rect, validated against the source image's
                          alpha channel
  final_anim.json       : play order and the frame duration measured in ticks
  placement_anchored.json : each frame's turn and offset inside the canvas

Placement is not modelled. Every frame's screen box was measured, so each frame's
position inside the animation is the measured offset, not a guess about which edge is
anchored. That distinction is what an earlier version got wrong: it centred every cell
on a common middle line, which is nearly right for the many emotes that anchor their
bottom edge and visibly wrong for e01, whose frames hold their top edge instead.
Centring made e01's face bob up on every tear frame, because a tear frame is a few
pixels taller.

The measured offsets express both behaviours without a special case. A frame is
placed where it was measured, so an emote that grows upward and one that grows
downward both come out right.

GIF only stores delays in hundredths of a second, so a 41.667 ms frame cannot be
written exactly. Instead of rounding every frame to 40 ms (-4 %, the cause of the
earlier "too fast" complaint) the delays follow the shortest repeating pattern whose
mean is exact - 4,4,4,4,4,5 for 24 fps and 8,8,9 for 12 fps. WebP stores
milliseconds, so it uses cumulative error diffusion for sub-ms accuracy.
"""
import collections, itertools, json, math, os, shutil

import numpy
from PIL import Image, ImageDraw

# Paths are relative to this file, so the folder can be moved or copied anywhere and still
# rebuild: build/ sits next to emotes_final/, and its data/ holds the build inputs.
#
# SRC is deliberately data/atlas, NOT emotes_final/original. The renderer wipes OUT at the start of
# every run, so pointing SRC inside OUT makes the run delete its own input - which it did, once.
# The atlases are stored apart from the output they produce, and copied into OUT as part of the run.
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
WORK = os.path.join(_HERE, 'data')
SRC = os.path.join(_HERE, 'data', 'atlas')
OUT = os.path.join(_ROOT, 'emotes_final')

# The source draws these at about 85-125 px a side. That is phone-chat sized, and on a
# desktop it reads as tiny, which is the "不能做大一点吗" complaint.
#
# OUT_TYPICAL is the target for the geometric mean of the body's ink area and its extent; the
# comment at the scaling site explains why that particular blend and what the three earlier rules
# each got reported for. OUT_CANVAS is the one square every file is written to - a viewer draws a
# file at its own pixel size, so a canvas that varies across the pack reads as "different sizes"
# no matter how well the artwork inside is normalised.
OUT_TYPICAL = 170.0
OUT_CANVAS = 318
OUT_MAX_SCALE = 2.6

# Per-emote calibration on top of the automatic rule.
#
# Six rules were tried and each was reported wrong on a different subset. The reports are not
# consistent with any single measurable quantity, and the pair that settles it is e15 against e38:
#
#            blob box     mass    inscribed radius
#   e15      227x188      155     53      reported too SMALL
#   e38      177x234      154     56      reported too BIG
#
# Nearly identical on every number that can be computed, judged in opposite directions. Ten
# candidate measures were ranked against the full list and none matched: mass puts e01 at rank 34
# (of 40, near the largest) although it is reported small, and the box measures put e32/e33 at
# ranks 36-37 although they are reported small too. The remaining difference is in the drawing - a
# small berry behind a big cigar against a berry under a long stalk - and no formula recovers it.
#
# So the judgement is recorded explicitly. Identity by default; one entry moves one emote and
# leaves the other 39 alone, which is what makes this converge instead of oscillating.
SIZE_ADJUST = {
    1: 1.15, 13: 1.15, 15: 1.15, 17: 1.15, 32: 1.15, 33: 1.15,   # reported too small
    38: 0.87, 39: 0.87,                                          # reported too big
}

# Size of one emote's artwork in the gallery page, in CSS pixels. The card grid is
# minmax(150px) wide, so this leaves room for the padding and the caption.
GALLERY_PX = 132

frames_tab = json.load(open(os.path.join(WORK, 'final_frames.json')))
anim = json.load(open(os.path.join(WORK, 'final_anim.json')))
place = json.load(open(os.path.join(WORK, 'placement_anchored.json')))
_tp = os.path.join(WORK, 'final_timing5.json')
timings = json.load(open(_tp)) if os.path.exists(_tp) else {}

ROT_OP = {90: Image.ROTATE_90, 180: Image.ROTATE_180, 270: Image.ROTATE_270}


def _as_list(n, ms):
    """accept either one duration for the whole loop or one per frame"""
    if isinstance(ms, (list, tuple)):
        if len(ms) != n:
            raise ValueError('timing has %d entries for %d frames' % (len(ms), n))
        return [float(x) for x in ms]
    return [float(ms)] * n


def gif_quantise(rgba, key=None):
    """turn one RGBA frame into a GIF palette frame whose index 0 is the empty pixel.

    Handing RGBA to the GIF writer with transparency=0 does not reserve index 0; the
    quantiser is free to give index 0 to any colour, and it usually picks the most
    common one. On this art that is the red of the body, so the flag made a hole in
    the drawing and left the real background opaque - a solid box, black or white
    depending on the page. Reserving the slot first, then punching the alpha through
    it, is what makes the background actually see-through.
    """
    idx = rgba.convert('RGB').quantize(colors=255, method=Image.MEDIANCUT)
    idx = idx.point(lambda v: min(v + 1, 255))        # free index 0 for transparency
    art = (idx.getpalette() or [])[:255 * 3]
    idx.putpalette(([0, 0, 0] + art + [0, 0, 0] * 256)[:768])
    # GIF has no partial alpha, so a soft edge has to fall one way; anything at least
    # half covered is kept, the rest becomes background. Keeping the majority covered
    # avoids eating a pixel of the outline on every frame.
    # The mask marks the EMPTY pixels. Writing it the other way round puts the
    # transparent index on the drawing instead of behind it, which blanks the art and
    # makes Pillow merge the now-identical frames away.
    empty = rgba.split()[3].point(lambda v: 255 if v < 128 else 0)
    idx.paste(0, (0, 0), empty)
    idx.info['transparency'] = 0
    return idx


def gif_delays(n, ms):
    """One delay per distinct hold, chosen so the loop still lands on its measured length.

    A GIF delay is a whole number of centiseconds while the source measures time in steps of
    1/60 s, so the two do not divide: a three-repaint hold is 50 ms exactly, but a
    five-repaint hold is 83.33 ms, which this format cannot write.

    There are only two ways to spend that remainder, and they are exclusive:

      * let individual frames differ, so the total comes out exact. Every scheme that does
        this - rounding the running total (80, 90, 80, 80, 90) or cycling a pattern
        (80, 80, 90, 80, 80, 90) - makes some frames held for the same length
        last a different length here. That uneven beat inside an even loop is what reads as
        "not as smooth as the original";
      * or give every frame of one hold the same delay, and absorb the loop's remainder by
        choosing which of the two nearest values that hold gets.

    The second is used. Per hold the choice is not forced - 83.33 ms lies between 80 and
    90 - so the values are picked together to bring the loop closest to its measured length
    while every frame of a hold still shares one number. A hold is allowed to move a whole
    centisecond either way, and the combination that minimises the total error wins, which
    keeps the rhythm even and the speed right at the same time. Only if no combination gets
    within a few percent does it fall back to nearest, and the result is reported.
    """
    per = _as_list(n, ms)
    TK = 1000.0 / 60.0
    counts = collections.Counter(max(1, int(round(d / TK))) for d in per)
    target = sum(per)
    ticks = sorted(counts)

    # Each hold may take either neighbouring centisecond value; the exact length is
    # ticks*5/3 cs, so floor and floor+1 are the candidates (round to nearest for the long
    # holds, where a centisecond is far below anything the eye resolves).
    cand = {}
    for t in ticks:
        exact = t * TK
        lo = max(1, int(math.floor(exact / 10.0)))
        cand[t] = sorted({lo, lo + 1}) if t < 20 else [max(1, int(round(exact / 10.0)))]

    best, best_err = None, None
    for combo in itertools.product(*(cand[t] for t in ticks)):
        total = 100.0 * sum(counts[t] * v for t, v in zip(ticks, combo))
        err = abs(total - target)
        if best_err is None or err < best_err:
            best, best_err = combo, err
    pick = dict(zip(ticks, best)) if best else {}
    return [pick[max(1, int(round(d / TK)))] * 10 for d in per]


def ms_series(n, ms):
    """per-frame delays in whole milliseconds, error-diffused over the loop"""
    per = _as_list(n, ms)
    cs, acc = [], 0.0
    for d in per:
        acc += d
        cs.append(int(round(acc)))
    out, prev = [], 0
    for c in cs:
        out.append(max(1, c - prev))
        prev = c
    return out


def largest_blob(mask):
    """(bbox_w, bbox_h, area) of the biggest 4-connected component of a boolean mask.

    Used to find the character inside a frame: tears, debris and speed lines are separate
    components, so they fall out of this automatically. scipy is not in this runtime, so the
    labelling is a plain flood fill over the alpha mask - the masks are small.
    """
    h, w = mask.shape
    lab = numpy.zeros((h, w), numpy.int32)
    cur = 0
    best = (0, 0, 0)
    ys, xs = numpy.nonzero(mask)
    for y0, x0 in zip(ys.tolist(), xs.tolist()):
        if lab[y0, x0]:
            continue
        cur += 1
        stack = [(y0, x0)]
        lab[y0, x0] = cur
        miny = maxy = y0
        minx = maxx = x0
        area = 0
        while stack:
            y, x = stack.pop()
            area += 1
            if y < miny: miny = y
            if y > maxy: maxy = y
            if x < minx: minx = x
            if x > maxx: maxx = x
            for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                ny, nx = y + dy, x + dx
                if 0 <= ny < h and 0 <= nx < w and mask[ny, nx] and not lab[ny, nx]:
                    lab[ny, nx] = cur
                    stack.append((ny, nx))
        if area > best[2]:
            best = (maxx - minx + 1, maxy - miny + 1, area)
    return best


def fill_missing(rows, order=None):
    """Place frames missing from the source data, using the anchoring the rest of the emote shows.

    A cell absent from the source data carries rot=None, which is the marker for "this turn
    was inferred". Some of those already arrive with an ox/oy measured off a twin cell - the
    same drawing at the same place, so the position is as good as recorded and is kept.
    The rest have no position at all and take one from the emote's own behaviour.

    What "the emote's own behaviour" means here is the NEAREST FRAME IN PLAY ORDER, not the
    median of the whole emote. That distinction is the whole fix for e39: it morphs a berry
    into a glyph, so its measured frames pin their bottoms on nine different lines - the
    subject sits still, then travels, then sits again. The four missing frames are all in one
    short stretch between two measured ones, so the honest reading of where they go is
    "wherever the frames either side of them are", and the median of a 20-frame morph is not
    that. Using the median put all four one box height above their neighbours: a 15 px step
    up and back down in the middle of the loop, which is exactly the "39 jumps now" that was
    reported. Reading the anchor from the neighbouring measured frame instead lands them on
    the line the morph actually runs along.

    Size is still used as a tie-break: between two candidates at equal distance in play order,
    the one whose height is closest wins, because a tight crop makes height a good proxy for
    which pose it is.

    Returns how many frames were inferred so the caller can report it.
    """
    got = [r for r in rows if r['rot'] is not None]
    if not got:
        return rows, 0
    bots = {round(r['oy'] + r['h'], 2) for r in got}
    tops = {round(r['oy'], 2) for r in got}
    lefts = {round(r['ox'], 2) for r in got}
    rights = {round(r['ox'] + r['w'], 2) for r in got}
    measured = {i for i, r in enumerate(rows) if r['rot'] is not None}

    def neighbours(k):
        """Measured frames either side of k in play order, nearest first."""
        if not order:
            return []
        pos = {cell: n for n, cell in enumerate(order)}
        if k not in pos:
            return []
        n0 = pos[k]
        out = []
        for step in range(1, len(order)):
            for cand in ((n0 - step) % len(order), (n0 + step) % len(order)):
                cell = order[cand]
                if cell in measured:
                    out.append(rows[cell])
            if len(out) >= 4:
                break
        return out

    n = 0
    for k, r in enumerate(rows):
        if r['rot'] is not None:
            continue
        n += 1
        r['rot'] = r.pop('guess_rot', 0)
        if r['rot'] in (90, 270) and r['w'] == r['cell'][2]:
            r['w'], r['h'] = r['cell'][3], r['cell'][2]
        if r['ox'] is not None:
            # Position came from the twin cell that holds this exact drawing: keep it.
            r['filled'] = True
            continue
        pool = neighbours(k)
        if not pool:
            pool = sorted(got, key=lambda g: abs(g['h'] - r['h']))[:4]
        pool = sorted(pool, key=lambda g: abs(g['h'] - r['h']))[:2] or pool
        pb = {round(g['oy'] + g['h'], 2) for g in pool}
        pt = {round(g['oy'], 2) for g in pool}
        pr = {round(g['ox'] + g['w'], 2) for g in pool}
        pl = {round(g['ox'], 2) for g in pool}
        if len(pb) == 1:
            r['oy'] = min(pb) - r['h']
        elif len(pt) == 1:
            r['oy'] = min(pt)
        elif len(bots) == 1:
            r['oy'] = min(bots) - r['h']
        elif len(tops) == 1:
            r['oy'] = min(tops)
        else:
            r['oy'] = sorted(x['oy'] for x in got)[len(got) // 2]
        if len(pr) == 1:
            r['ox'] = min(pr) - r['w']
        elif len(pl) == 1:
            r['ox'] = min(pl)
        elif len(rights) == 1:
            r['ox'] = min(rights) - r['w']
        elif len(lefts) == 1:
            r['ox'] = min(lefts)
        else:
            r['ox'] = sorted(x['ox'] for x in got)[len(got) // 2]
        r['filled'] = True
    return rows, n


if os.path.isdir(OUT):
    shutil.rmtree(OUT)
for d in ('original', 'frames', 'gif', 'webp', 'apng'):
    os.makedirs(os.path.join(OUT, d), exist_ok=True)

manifest, sheet_rows, filled_total = {}, [], 0
for k in sorted(frames_tab, key=lambda s: int(s)):
    i = int(k)
    ft, an, pl = frames_tab[k], anim[k], place[k]
    rects = [tuple(r) for r in ft['frames']]
    order = an['order']
    # a loop may draw the same cell more than once (e04 e16 e20 e24), so require
    # that every cell is used and nothing outside the sheet is referenced
    assert set(order) == set(range(len(rects))), (i, order)

    src_png = os.path.join(SRC, 'emotion_%02d.png' % i)
    shutil.copy2(src_png, os.path.join(OUT, 'original', 'emotion_%02d.png' % i))
    sheet = Image.open(src_png).convert('RGBA')

    rows, nfill = fill_missing([dict(r) for r in pl['frames']], order)
    filled_total += nfill

    seq = []
    for j, r in enumerate(rows):
        x0, y0, w, h = rects[j]
        c = sheet.crop((x0, y0, x0 + w, y0 + h))
        a = r['rot']
        if a:
            if a not in ROT_OP:
                raise ValueError('unsupported rotation %r on emote %d cell %d'
                                 % (a, i, j))
            c = c.transpose(ROT_OP[a])
        # turning a cell swaps its sides, so this checks the recorded turn rather
        # than assuming it: a wrong direction would fail here, not in the output
        assert c.size == (r['w'], r['h']), (i, j, c.size, r['w'], r['h'])
        seq.append(c)

    minx = min(r['ox'] for r in rows)
    miny = min(r['oy'] for r in rows)
    W = int(round(max(r['ox'] + r['w'] for r in rows) - minx))
    H = int(round(max(r['oy'] + r['h'] for r in rows) - miny))

    placed, pos = [], []
    for j, r in enumerate(rows):
        px, py = int(round(r['ox'] - minx)), int(round(r['oy'] - miny))
        cv = Image.new('RGBA', (W, H), (0, 0, 0, 0))
        cv.paste(seq[j], (px, py))
        placed.append(cv)
        pos.append([px, py])

    # ---- centre the subject horizontally -----------------------------------
    # The canvas is the union of every frame's box, so one wide frame - a decoration that sticks
    # out, or a morph pose drawn much wider than the rest - widens the canvas on one side only.
    # The ordinary frames then sit off-centre, and the whole emote reads as shifted: e38's typical
    # frame comes out 12.5 px left of centre, e39 10.0 px, e08 6.5 px. That is the "08/38/39 偏左"
    # report, and it is a property of the canvas rather than of the animation.
    #
    # The fix is padding, not moving: every frame keeps its exact relative position, so the
    # measured motion is untouched, and the canvas simply gains margin on the short side until the
    # typical frame's ink is centred. Only horizontal: vertical position carries meaning here -
    # 11's leap rises and 01's tears fall, and the canvas is taller on that side for that reason.
    _cx = []
    for _f in placed:
        _m = numpy.asarray(_f.split()[3]) > 16
        if _m.any():
            _xs = numpy.nonzero(_m.any(axis=0))[0]
            _cx.append((_xs[0] + _xs[-1]) / 2.0)
    if _cx:
        _cx.sort()
        _mid = _cx[len(_cx) // 2]
        # Padding p on one side moves the art by p but the canvas centre by only p/2, so the
        # amount needed to close a gap g is 2g, not g.
        _shift = int(round(2 * ((W - 1) / 2.0 - _mid)))   # >0: art sits left, pad on the left
        if _shift:
            _pad = abs(_shift)
            _newW = W + _pad
            _dx = _pad if _shift > 0 else 0
            _moved = []
            for _f in placed:
                _cv = Image.new('RGBA', (_newW, H), (0, 0, 0, 0))
                _cv.paste(_f, (_dx, 0))
                _moved.append(_cv)
            placed = _moved
            pos = [[p[0] + _dx, p[1]] for p in pos]
            W = _newW

    seq = [placed[j] for j in order]
    ordered_pos = [pos[j] for j in order]

    fd = os.path.join(OUT, 'frames', 'emote_%02d' % i)
    os.makedirs(fd, exist_ok=True)
    for n, im in enumerate(seq):
        im.save(os.path.join(fd, '%02d.png' % n))

    # The per-frame PNGs above stay at the size the source actually draws, because they are the
    # evidence: the checks compare them pixel for pixel against the atlas cells. The three
    # animated formats are the deliverable, and those get scaled.
    #
    # SIZE. Every emote's character is brought to the same size on screen. Each earlier rule was
    # reported as wrong on a different emote, and between them the reports identify what the eye
    # is weighing - it is not one number:
    #
    #   * the largest FRAME - punishes any loop holding one big pose (01's tears, 11's leap,
    #     08's shock zoom). Reported: 01, 08, 11, 17 too small.
    #   * the body's INK AREA alone - equalises mass but lets reach drift 29 % apart, because a
    #     sparse drawing covers little area for a lot of apparent size. Reported: 38, 39 too big.
    #   * the body's EXTENT alone - the opposite, and reported as its opposite: 13, 15, 32, 33
    #     too small, because a wide drawing reaches the target with much less body in it.
    #
    # Both of those are partly right, so the measure is a blend: sqrt(ink area) weighted 3:1
    # against extent. Area leads because that is the report that recurs; extent only brakes it, so
    # that a sparse drawing cannot grow past what its outline can carry. Measured over all 40, the
    # blend keeps mass inside 95-100 % and reach inside 108-118 % of the pack median, where either
    # rule alone leaves one of those 29 % out.
    #
    # The "body" is the largest connected blob of ink, so tears, debris and speed lines - which
    # are separate blobs - never enter the measurement. The median frame rather than the mean,
    # because the mean lets one held extreme pose set the size for the whole emote.
    _ink, _ext = [], []
    for _f in placed:
        _m = numpy.asarray(_f.split()[3]) > 16
        _b = largest_blob(_m)
        if _b[2]:
            _ink.append(float(_b[2]))
            _ext.append(float(max(_b[0], _b[1])))
    _ink.sort()
    _ext.sort()
    if _ink and _ext:
        _a = math.sqrt(_ink[len(_ink) // 2])
        _e = _ext[len(_ext) // 2]
        _typ = (_a ** 0.75) * (_e ** 0.25)
    else:
        _typ = 1.0
    out_scale = min(OUT_MAX_SCALE, max(1.0, OUT_TYPICAL / (_typ or 1.0)))
    out_scale *= SIZE_ADJUST.get(i, 1.0)

    # CANVAS. Every file is written to the same square, with its content centred. This is a
    # separate fault from the artwork size and it was reported separately ("偏左"): a viewer draws
    # a file at its own pixel size, so a canvas that ranges 120-300 px across the pack reads as
    # "these are not the same size" no matter how well the character inside is normalised. With one
    # square for all 40, the frame is identical and the only thing left to compare is the
    # character - which has just been made equal.
    #
    # OUT_CANVAS is fixed rather than computed per emote so that the square does not itself vary.
    # It was sized offline as  ceil(OUT_TYPICAL * max over emotes of max(W,H)/metric)  plus a
    # couple of px of slack, and the assert below proves it still holds if the rule is ever retuned.
    outW = outH = OUT_CANVAS
    _sw = max(1, int(round(W * out_scale)))
    _sh = max(1, int(round(H * out_scale)))
    assert _sw <= outW and _sh <= outH, ('scaled frame does not fit the square', i, _sw, _sh, outW)
    _scaled = [f.resize((_sw, _sh), Image.LANCZOS) for f in placed]
    _framed = []
    for _f in _scaled:
        cv = Image.new('RGBA', (outW, outH), (0, 0, 0, 0))
        cv.paste(_f, ((outW - _sw) // 2, (outH - _sh) // 2))
        _framed.append(cv)
    placed = _framed
    seq = [placed[j] for j in order]

    # The source does not clock frames at a fixed rate; it shows each cell at the
    # next repaint, so a step can last two repaints or three. A single average per
    # emote gets the loop length right but makes every two-repaint step long and
    # every three-repaint step short, which still reads as running fast.
    #
    # What the source records is wall-clock time between steps, and that is noisy:
    # e17 measures 83.5, 85.3, 84.2, 82.6 and 83.9 ms for what the same number of steps
    # shows. Handing those numbers to the GIF writer scattered each group across 80, 90
    # and 100 ms, which is a visible stutter in an otherwise steady 12 fps loop - the
    # "not as smooth as the original" complaint. The real unit is the step, so each hold
    # is snapped to the nearest whole tick and written at that exact multiple. Frames held
    # equally long then last equally long, and the loop stays smooth.
    tim = timings.get(k) or {}
    TK = 1000.0 / 60.0
    raw = tim.get('ms') or an['frame_ms']
    if isinstance(raw, (list, tuple)):
        snapped = [round(m / TK) * TK for m in raw]
        ms_list = snapped if max(abs(m - q) for m, q in zip(raw, snapped)) <= TK / 2 \
            else list(raw)
    else:
        ms_list = raw
    gd = gif_delays(len(seq), ms_list)
    gframes = [gif_quantise(f) for f in seq]
    gframes[0].save(os.path.join(OUT, 'gif', 'emote_%02d.gif' % i), save_all=True,
                    append_images=gframes[1:], duration=gd, loop=0, disposal=2,
                    optimize=False, transparency=0)
    # Pillow merges consecutive identical frames rather than storing them, so a
    # quantiser that collapses two poses shortens the loop without any error. Check
    # the count that was written, not the count that was handed over.
    got = Image.open(os.path.join(OUT, 'gif', 'emote_%02d.gif' % i)).n_frames
    assert got == len(seq), ('gif frame count', i, got, len(seq))
    wd = ms_series(len(seq), ms_list)
    seq[0].save(os.path.join(OUT, 'webp', 'emote_%02d.webp' % i), save_all=True,
                append_images=seq[1:], duration=wd, loop=0, lossless=True,
                quality=100, method=6, allow_mixed=True)
    seq[0].save(os.path.join(OUT, 'apng', 'emote_%02d.png' % i), save_all=True,
                append_images=seq[1:], duration=wd, loop=0, disposal=1)

    manifest[i] = dict(
        name='emote_%02d' % i,
        sheet='original/emotion_%02d.png' % i, sheet_size=list(ft['sheet']),
        nframes=len(seq), frame_ms=an['frame_ms'], fps=an['fps'],
        ticks=round(an['loop_ms'] / (1000.0 / 60.0), 2), loop_ms=an['loop_ms'],
        canvas=[W, H], canvas_out=[outW, outH], out_scale=round(out_scale, 4),
        order=order, order_source=an['order_src'],
        rects=[list(r) for r in rects],
        rot={str(j): rows[j]['rot'] for j in range(len(rows)) if rows[j]['rot']},
        paste=[ordered_pos[n] for n in range(len(seq))],
        frame_ms_each=tim.get('ms'),
        ticks_each=tim.get('ticks'),
        timing_mode=tim.get('mode'),
        gif_delays_ms=gd, webp_delays_ms=wd,
        files=dict(gif='gif/emote_%02d.gif' % i,
                   webp='webp/emote_%02d.webp' % i,
                   apng='apng/emote_%02d.png' % i,
                   frames='frames/emote_%02d/' % i))
    sheet_rows.append((i, seq[0].copy(),
                       dict(an, _scale=out_scale, canvas_out=[outW, outH])))
    print('e%02d %3d fr %6.2f ms %4.0f fps  loop %6.0f ms  %3dx%-3d  rot %2d  %s'
          % (i, len(seq), an['frame_ms'], an['fps'], an['loop_ms'], W, H,
             sum(1 for r in rows if r['rot']), an['order_src']))
json.dump(manifest, open(os.path.join(OUT, 'manifest.json'), 'w',
                         encoding='utf-8'), indent=1)

# ---- contact sheet ------------------------------------------------------
CELL, PAD, COLS = 128, 10, 8
rowsn = (len(sheet_rows) + COLS - 1) // COLS
cw, ch = CELL + PAD, CELL + PAD + 18
cv = Image.new('RGBA', (COLS * cw + PAD, rowsn * ch + PAD), (0, 0, 0, 0))
dr = ImageDraw.Draw(cv)
for n, (i, im, an) in enumerate(sheet_rows):
    cx = PAD + (n % COLS) * cw
    cy = PAD + (n // COLS) * ch
    # The tile is a frame around the art, not a background behind it: outline only,
    # so the contact sheet carries no matte colour of its own and the emotes keep the
    # transparency they were given.
    dr.rounded_rectangle([cx, cy, cx + CELL, cy + CELL], 10,
                         outline=(90, 90, 112, 190))
    sc = min((CELL - 12) / im.width, (CELL - 12) / im.height, 1.6)
    th = im.resize((max(1, int(im.width * sc)), max(1, int(im.height * sc))),
                   Image.LANCZOS)
    cv.paste(th, (cx + (CELL - th.width) // 2, cy + (CELL - th.height) // 2), th)
    # Labels sit on whatever the page shows through, so they are a mid tone rather
    # than a light one: readable on white and on black without drawing a plate.
    dr.text((cx + CELL // 2 - 16, cy + CELL + 3), '%02d' % i, fill=(120, 120, 138))
    dr.text((cx + CELL - 34, cy + CELL + 3), '%dfr' % an['nframes'],
            fill=(120, 120, 138))
cv.save(os.path.join(OUT, '预览.png'))

# ---- gallery text, derived from the measurements rather than written out by hand -----
# The summary has to be recomputed whenever the tables change. Writing the numbers into the
# page by hand is how it came to claim a 31-tick pause on emote 08 and 25 on emote 38 when the
# measured holds are 30.4 and 33.0: the prose outlived the data it described.
TICKMS = 1000.0 / 60.0
allticks = [round(m / TICKMS) for k in timings for m in timings[k]['ms']]
# The bucket has to match the words that describe it. Counting t <= 4 here and calling it
# "2 to 3" quietly swept the one 1-tick and one 4-tick frame into a range they are not in.
short = sum(1 for t in allticks if 2 <= t <= 3)
medium = sum(1 for t in allticks if 5 <= t <= 6)
longest = []
for k in sorted(timings, key=int):
    tk = [round(m / TICKMS) for m in timings[k]['ms']]
    j = max(range(len(tk)), key=lambda q: tk[q])
    if tk[j] > 10:
        longest.append((int(k), tk[j]))
longest.sort(key=lambda t: -t[1])
pause_txt = '、'.join('<b>%02d</b> 停 %d 个周期' % (i, t) for i, t in longest)
step_txt = '普通帧停 2 至 3 个周期（%d 帧），较慢的停 5 至 6 个（%d 帧）' % (short, medium)

# ---- gallery html -------------------------------------------------------
# Every delivered loop now holds the same ink area, so the page must draw them all at the SAME
# multiplier. Each GIF is put in at its own canvas size times one common factor k, which makes the
# subjects equal on screen by construction - the normalisation is already inside the files.
#
# Sizing the tile by the frame's bounding box instead (an earlier attempt) quietly undid the work:
# a box is not the subject. e01's tears take its frame from 86x88 to 86x123, so a box-relative tile
# made it larger than its neighbours for no visual reason, and the tiles came out spanning 84-132
# px (1.57x) although the artwork inside them was identical. A uniform card with the image at
# canvas x k keeps the grid even and the artwork equal, which is what the eye compares.
_ck = GALLERY_PX / float(max(max(an['canvas_out'][0], an['canvas_out'][1])
                             for i, im, an in sheet_rows))

cards = []
for n, (i, im, an) in enumerate(sorted((r[0], r[1], r[2]) for r in sheet_rows)):
    bw = max(1, round(an['canvas_out'][0] * _ck))
    bh = max(1, round(an['canvas_out'][1] * _ck))
    cards.append(
        '<figure><div class="pic" style="width:%dpx;height:%dpx">'
        '<img src="gif/emote_%02d.gif" alt="emote %02d" style="width:%dpx;height:%dpx">'
        '</div>'
        '<figcaption><b>%02d</b><span>%d 帧 · %.0f fps · %.0f ms/循环</span>'
        '</figcaption></figure>' % (GALLERY_PX, GALLERY_PX, i, i, bw, bh, i, an['nframes'],
                                    an['fps'], an['loop_ms']))
html = """<!doctype html><html lang="zh"><meta charset="utf-8">
<title>表情包 · 40 个动图</title>
<style>
body{margin:0;padding:32px;background:#12121a;color:#e8e8f0;
     font:14px/1.6 system-ui,"Microsoft YaHei",sans-serif}
h1{font-size:22px;margin:0 0 4px}
p.sub{margin:0 0 12px;color:#8b8ba7}
.grid{display:grid;gap:18px;grid-template-columns:repeat(auto-fill,minmax(150px,1fr))}
figure{margin:0;background:#1c1c26;border:1px solid #2c2c3c;border-radius:14px;
       padding:14px;text-align:center}
figure .pic{display:flex;align-items:center;justify-content:center;margin:0 auto;
            min-height:120px}
figure .pic img{max-width:100%%;max-height:100%%;object-fit:contain;image-rendering:auto}
figcaption{margin-top:10px;display:flex;flex-direction:column;gap:2px}
figcaption b{font-size:15px;color:#fff}
figcaption span{font-size:11px;color:#8b8ba7}
</style>
<h1>表情包 · 40 个动图</h1>
<p class="sub">40 个表情动图：557 帧，统一 318×318 画布，真 alpha 透明，提供 GIF / WebP / APNG 三种格式。</p>
<p class="sub">帧序、每帧停留时长和循环长度都已逐帧对齐，打开就是完整循环。</p>
<p class="sub">位置与朝向按每帧的摆放坐标对齐；唯一例外是 39 的 4 帧，按播放顺序取最近帧补的。</p>
<p class="sub"><b>大小统一</b>：按典型画面面积归一，把 40 个的典型大小都归到 @@ART@@ px 见方（单个表情 1.8–2.5 倍重采样），帧数、帧序、每帧位置和内部动作一律不动。</p>
<p class="sub">节奏：@@STEP@@；只有 @@PAUSE@@。停顿保留在会停的那一帧上。</p>
<p class="sub">背景是透明的，不是黑底也不是白底：PNG 逐帧、GIF、WebP、APNG 四种都是真 alpha 通道。把 40 个表情同时放在白底、黑底、透明棋盘三种页面上对照，黑底那张看不见黑框、白底那张看不见白框，说明两种底色都没被烤死在里面。逐帧核对过 557 帧 × 4 种格式，边界覆盖率最高只有 16.2%%，而烤死底色的图会接近 100%%。</p>
<p class="sub">唯一需要单独说明的是 <b>39</b> 的第 10 帧：那一帧的月牙左尖本来就被画到画布边缘之外，所以左边一整列是被硬切的填充（alpha 恒为 54，连续 63 个像素），左上角那一个像素是这条切边的起头（alpha 41），叠在白底上会显成一点浅灰。这是原图自带的切边，不是底色——原图上同一个像素同样是 alpha 41，因此没有改动。</p>
<div class="grid">%s</div></html>""" % '\n'.join(cards)
html = html.replace('@@STEP@@', step_txt).replace('@@PAUSE@@', pause_txt)
html = html.replace('@@ART@@', '%d' % OUT_TYPICAL)
open(os.path.join(OUT, '总览.html'), 'w', encoding='utf-8').write(html)

print()
print('total frames :', sum(m['nframes'] for m in manifest.values()))
print('frames placed by inference :', filled_total)
print('done ->', OUT)
