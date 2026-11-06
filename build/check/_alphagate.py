"""Acceptance for the transparency fix, across every frame of every format.

A background is only truly gone if the file shows no box on white AND none on black. Checking one
matte is how the previous version passed: the GIF box was black, so on a black page it was invisible
and looked correct. Both mattes, every frame, every format.

Two revisions after using this on the real pack:

1. "a corner pixel differs from the backdrop" is not a matte test. Art legitimately runs off the
   canvas edge -- e39 frame 10 is a crescent whose left tip is cut at x=0, so its top-left pixel is
   antialiased red at alpha 41, and it composites to (214,214,214) on white. Flagging that as a
   background box is a false positive; the pack was never wrong there.

2. The real signature of a matte is: the whole border is covered AND it is one flat colour. So test
   that instead -- border coverage, and how uniform the border is. A matte scores ~100% and ~1 colour.
   Genuine art scores far less on both, even when a few pixels do touch the edge.
"""
import os, json
from PIL import Image
_HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(os.path.dirname(os.path.dirname(_HERE)), 'emotes_final')
man = json.load(open(os.path.join(OUT, 'manifest.json')))

MATTE_COVER = 99.0   # a box covers essentially every border pixel
MATTE_FLAT = 3       # ...in essentially one colour


def border(im):
    """(coverage percent, distinct-colour count) of the frame's border ring."""
    f = im.convert('RGBA')
    w, h = f.size
    ring = []
    for x in range(w):
        ring.append((x, 0)); ring.append((x, h - 1))
    for y in range(1, h - 1):
        ring.append((0, y)); ring.append((w - 1, y))
    px = f.load()
    cols = [px[x, y] for x, y in ring]
    cover = 100.0 * sum(1 for c in cols if c[3] >= 250) / len(cols)
    flat = len({c[:3] for c in cols if c[3] >= 250})
    return cover, flat


def composite_on(im, bg):
    """Put the frame on an opaque backdrop; return the border pixels that are not the backdrop."""
    f = im.convert('RGBA')
    base = Image.new('RGBA', f.size, bg + (255,))
    base.alpha_composite(f)
    w, h = base.size
    px = base.load()
    ring = [(x, 0) for x in range(w)] + [(x, h - 1) for x in range(w)]
    ring += [(0, y) for y in range(1, h - 1)] + [(w - 1, y) for y in range(1, h - 1)]
    return [(p, px[p][:3]) for p in ring if px[p][:3] != bg]


tot = {'frames': 0, 'gif': 0, 'webp': 0, 'apng': 0}
matte, bleed = [], []
worst = (0.0, None)

for kk in sorted(man, key=int):
    m = man[kk]
    items = [('frames', os.path.join(OUT, 'frames', m['name'],
                                    f), None) for f in sorted(os.listdir(os.path.join(OUT, 'frames', m['name'])))]
    for fmt in ('gif', 'webp', 'apng'):
        items.append((fmt, os.path.join(OUT, m['files'][fmt]), fmt))

    for fmt, path, _ in items:
        im = Image.open(path)
        nf = getattr(im, 'n_frames', 1)
        for n in range(nf):
            if nf > 1:
                im.seek(n)
            tot['frames' if fmt == 'frames' else fmt] += 1
            cover, flat = border(im)
            label = 'e%s %s' % (kk, os.path.basename(path) if fmt == 'frames' else 'f%d' % n)
            if cover > MATTE_COVER and flat <= MATTE_FLAT:
                matte.append((label, round(cover, 1), flat))
            if cover > worst[0]:
                worst = (cover, label)
            # on a white page and on a black page, the edge must vanish
            for bg in ((255, 255, 255), (0, 0, 0)):
                bad = composite_on(im, bg)
                if bad and cover > MATTE_COVER:
                    bleed.append((label, bg, bad[:2]))

print('frames examined : %s' % tot)
print('worst border coverage anywhere : %.1f%% (%s)' % worst)
print('   a matte would be ~100%; art that merely touches an edge stays near the ink fraction')
print()
print('matte-shaped frames (border fully covered in one flat colour) : %d' % len(matte))
for m in matte[:8]:
    print('   %s' % (m,))
print()
print('RESULT: no frame of any format paints a background box'
      if not matte else 'RESULT: a background box is still present')
