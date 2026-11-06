"""Final gate. The manifest may carry a turn for a cell missing from the record; what
must hold is that every MEASURED cell keeps its measured angle, and that any extra
entry belongs to an unobserved cell."""
import json, os, struct
from PIL import Image, ImageFile
ImageFile.LOAD_TRUNCATED_IMAGES = False

_HERE = os.path.dirname(os.path.abspath(__file__))
_DATA = os.path.join(os.path.dirname(_HERE), 'data')
_ROOT = os.path.dirname(os.path.dirname(_HERE))
OUT = os.path.join(_ROOT, 'emotes_final')
SRC = os.path.join(_DATA, 'atlas')
man = json.load(open(os.path.join(OUT, 'manifest.json')))
fr  = json.load(open(os.path.join(_DATA, 'frame_rot.json')))
pa  = json.load(open(os.path.join(_DATA, 'placement_anchored.json')))
ff  = json.load(open(os.path.join(_DATA, 'final_frames.json')))

ok = True
def bad(m):
    global ok; ok = False; print('  FAIL', m)

# 1 three formats, every frame decodable, counts agree
tot = 0
for k in sorted(man, key=int):
    m = man[k]; i = int(k)
    for a in ('gif', 'webp', 'apng'):
        p = os.path.join(OUT, m['files'][a])
        im = Image.open(p)
        n = getattr(im, 'n_frames', 1)
        if n != m['nframes']: bad('e%02d %s %d frames vs %d' % (i, a, n, m['nframes']))
        for f in range(n):
            im.seek(f); im.load()
        tot += n
    if len(os.listdir(os.path.join(OUT, m['files']['frames']))) != m['nframes']:
        bad('e%02d frame directory count' % i)
print('decoded %d frames x3 formats' % (tot // 3))

# 2 measured turns preserved exactly; extras only on unobserved cells
extra = {}
for k in sorted(man, key=int):
    m = man[k]; i = int(k)
    got  = {str(a): int(b) for a, b in (m.get('rot') or {}).items()}
    want = {str(a): int(b) for a, b in (fr.get(k) or {}).items()}
    for a, v in want.items():
        if got.get(a) != v: bad('e%02d cell %s measured %d, rendered %s' % (i, a, v, got.get(a)))
    for a in set(got) - set(want):
        if pa[k]['frames'][int(a)]['rot'] is not None:
            bad('e%02d cell %s has an invented turn' % (i, a))
        extra.setdefault(i, []).append(a)
print('measured turn preserved on %d cells across 40 emotes' % sum(len(v) for v in fr.values()))
print('inferred turn (cell missing from the record):', extra if extra else 'none')

# 3 the two lists the user reported
SIDE = [2, 9, 15, 18, 19, 20, 21, 26, 29, 31, 33, 37, 39]
PART = [5, 7, 8, 11, 14, 17, 22, 23, 25, 27, 32]
print()
for name, ids in (('躺倒 sideways', SIDE), ('部分帧部分帧', PART)):
    n = sum(man[str(i)]['nframes'] for i in ids)
    t = sum(len(man[str(i)]['rot']) for i in ids)
    print('%-14s %2d emotes: %d of %d frames turned' % (name, len(ids), t, n))
    print('               %s' % {i: '%d/%d' % (len(man[str(i)]['rot']), man[str(i)]['nframes']) for i in ids})
for i in SIDE:
    if not man[str(i)]['rot']: bad('e%02d reported sideways but nothing turned' % i)

# 4 e01 tears hang from a shared top edge
d = os.path.join(OUT, 'frames', 'emote_01')
tops, bots = [], []
for f in sorted(os.listdir(d)):
    b = Image.open(os.path.join(d, f)).convert('RGBA').getbbox()
    tops.append(b[1]); bots.append(b[3])
print()
print('e01: top edge %s, bottom edge spans %d..%d px' % (sorted(set(tops)), min(bots), max(bots)))
if len(set(tops)) != 1: bad('e01 frames do not share a top edge')

# 5 all 40 source atlases are complete PNGs
for k in range(40):
    b = open(os.path.join(SRC, 'emotion_%02d.png' % k), 'rb').read()
    j = 8; end = None
    while j + 8 <= len(b):
        ln = struct.unpack_from('>I', b, j)[0]; ty = b[j+4:j+8]
        j += 12 + ln
        if ty == b'IEND': end = j
    if end != len(b): bad('emotion_%02d.png not exactly IEND-terminated' % k)
print()
print('40 atlases complete and strictly framed')
print()
print('RESULT:', 'ALL CHECKS PASSED' if ok else 'FAILURES ABOVE')
