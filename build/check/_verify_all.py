import json, os, itertools
from PIL import Image
_HERE = os.path.dirname(os.path.abspath(__file__))
_DATA = os.path.join(os.path.dirname(_HERE), 'data')
_ROOT = os.path.dirname(os.path.dirname(_HERE))
WORK = _DATA
SRC = os.path.join(_DATA, 'atlas')
OUT = os.path.join(_ROOT, 'emotes_final')
man=json.load(open(os.path.join(OUT,'manifest.json')))
pl=json.load(open(os.path.join(WORK,'placement_anchored.json')))
ff=json.load(open(os.path.join(WORK,'final_frames.json')))
tm=json.load(open(os.path.join(WORK,'final_timing5.json')))
fail=[]
def chk(c,msg):
    if not c: fail.append(msg)
def tight(im):
    bb=im.split()[3].getbbox(); return im.crop(bb) if bb else None
def mism(a,b):
    w=max(a.width,b.width); h=max(a.height,b.height)
    A=a.resize((w,h),Image.LANCZOS).split()[3]; B=b.resize((w,h),Image.LANCZOS).split()[3]
    return sum(1 for x,y in zip(A.tobytes(),B.tobytes()) if (x>96)!=(y>96))/float(w*h)

cw=ccw=amb=0; ratios={}; nfr=0
for kk in sorted(man,key=int):
    k=int(kk); m=man[kk]
    d=os.path.join(OUT,'frames',m['name']); fs=sorted(os.listdir(d))
    W,H=m['canvas']; rows=pl[kk]['frames']; order=m['order']; nfr+=len(fs)
    poses=ff[kk]['frames']            # list of [x,y,w,h]
    chk(len(rows)==len(poses),'e%02d placement %d vs pose table %d'%(k,len(rows),len(poses)))
    chk(len(order)==len(fs),'e%02d order %d vs files %d'%(k,len(order),len(fs)))
    chk(len(fs)==m['nframes'],'e%02d files %d vs nframes %d'%(k,len(fs),m['nframes']))
    chk(len(tm[kk]['ms'])==len(fs),'e%02d holds %d vs files %d'%(k,len(tm[kk]['ms']),len(fs)))
    chk(abs(tm[kk]['loop_ms']-sum(tm[kk]['ms']))<0.01,'e%02d loop %s vs holds %s'
        %(k,tm[kk]['loop_ms'],round(sum(tm[kk]['ms']),3)))
    chk(all(0<=o<len(rows) for o in order),'e%02d order indexes outside the pose table'%k)
    chk(sorted(set(order))==list(range(len(rows))),'e%02d order does not visit every pose'%k)
    chk(len({tuple(p) for p in poses})==len(poses),'e%02d duplicate poses'%k)
    # every pose rect must be a cell of the placed table
    cells={tuple(r['cell']) for r in rows}
    chk(all(tuple(p) in cells for p in poses),'e%02d pose table has a rect the placement lacks'%k)
    chk({tuple(p) for p in poses}==cells,'e%02d pose set != placement cell set'%k)
    im=Image.open(os.path.join(SRC,'emotion_%02d.png'%k)).convert('RGBA')
    areas=[]
    for n,f in enumerate(fs):
        fr=Image.open(os.path.join(d,f)).convert('RGBA')
        chk(fr.size==(W,H),'e%02d frame %d size %s vs canvas %s'%(k,n,fr.size,(W,H)))
        a=fr.split()[3]; bb=a.getbbox()
        chk(bb is not None,'e%02d frame %d empty'%(k,n))
        if bb:
            chk(0<=bb[0] and 0<=bb[1] and bb[2]<=W and bb[3]<=H,
                'e%02d frame %d ink %s outside canvas %s'%(k,n,bb,(W,H)))
            areas.append((bb[2]-bb[0])*(bb[3]-bb[1]))
        for vals in ([a.getpixel((x,0)) for x in range(W)],
                     [a.getpixel((x,H-1)) for x in range(W)],
                     [a.getpixel((0,y)) for y in range(H)],
                     [a.getpixel((W-1,y)) for y in range(H)]):
            on=[v for v in vals if v>96]
            if len(on)>=6:
                run=max(len(list(g)) for g in itertools.groupby(on))
                chk(run<10,'e%02d frame %d cut edge run=%d'%(k,n,run))
        r=rows[order[n]]
        if r.get('rot'):
            x,y,w,h=r['cell']
            cell=tight(im.crop((x,y,x+w,y+h))); ft=tight(fr)
            if cell and ft:
                s1=mism(cell.transpose(Image.ROTATE_270),ft)
                s2=mism(cell.transpose(Image.ROTATE_90),ft)
                if s1<0.03 and s1<s2: cw+=1
                elif s2<0.03 and s2<s1: ccw+=1
                else: amb+=1; fail.append('e%02d frame %d turn ambiguous (cw %.3f ccw %.3f)'%(k,n,s1,s2))
    if areas:
        areas.sort(); ratios[k]=(areas[len(areas)//2],areas[0])
chk(ccw==0,'%d frames turned the wrong direction'%ccw)
tiny=[(k,v[1],v[0]) for k,v in ratios.items() if v[1]<0.10*v[0]]
print('emotes checked                              : %d'%len(man))
print('frames checked                              : %d'%nfr)
print('turned frames, clockwise                    : %d'%cw)
print('turned frames, anticlockwise                : %d'%ccw)
print('turned frames, ambiguous                    : %d'%amb)
print('frames under 10%% of their emote median ink : %s'%(tiny if tiny else 'none'))
print()
if fail:
    print('FAILURES (%d):'%len(fail))
    for f in fail[:25]: print('   ',f)
    print('RESULT: %d FAILURES'%len(fail))
else:
    print('RESULT: ALL CHECKS PASSED')
