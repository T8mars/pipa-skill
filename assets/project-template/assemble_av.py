"""Actual-frame CRF10 assembly using the independent FFmpeg9/PyAV backend."""
from pathlib import Path
from fractions import Fraction
from collections import Counter
import sys,json,hashlib,argparse
from project_config import frame_table, require_current_reviews
ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT/'制作分析/工具依赖'))
if '--deps' in sys.argv:
 sys.path.insert(0,str(Path(sys.argv[sys.argv.index('--deps')+1]).resolve()))
import av,numpy as np
from media_runtime import configure_cpu_mode
CPU_MODE=configure_cpu_mode(av,'--scalar-cpu' in sys.argv)
from PIL import Image,ImageDraw
av.logging.set_level(av.logging.ERROR)
OUTDIR=ROOT/'完成分段/最终组装';QA=ROOT/'制作分析/生成复核'
FINAL=OUTDIR/'琵琶曲_完整版.mp4'
AUDIO=ROOT/'41888056471-1-192.mp3'
RATE=Fraction(24)
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def rows():
 return frame_table(ROOT/'补齐生成用/原始帧数.txt')
def inspect(path,keep):
 with av.logging.Capture() as logs:
  with av.open(str(path)) as c:
   v=c.streams.video[0];v.thread_count=1;rate=v.average_rate
   n=-1
   for n,f in enumerate(c.decode(video=0)):
    assert not f.is_corrupt and f.pts is not None
    assert f.pts*f.time_base==Fraction(n,1)/rate
   count=n+1;assert count>=keep
   metadata={'frames':count,'rate':str(rate),'size':(v.width,v.height),'codec':v.codec_context.name}
 assert not logs,list(logs)
 return metadata
def headers(extra):
 assert extra[0]==1 and (extra[4]&3)+1==4
 pos=6;parts=[]
 for _ in range(extra[5]&31):
  size=int.from_bytes(extra[pos:pos+2],'big');pos+=2;parts.append(extra[pos:pos+size]);pos+=size
 count=extra[pos];pos+=1
 for _ in range(count):
  size=int.from_bytes(extra[pos:pos+2],'big');pos+=2;parts.append(extra[pos:pos+size]);pos+=size
 return b''.join(len(p).to_bytes(4,'big')+p for p in parts)
def trim(path,dest,keep,size):
 signature={'source_sha256':sha(path),'keep':keep,'size':list(size),'rate':str(RATE),'codec':'libx264','crf':10,'preset':'medium','backend':av.__version__}
 meta=dest.with_suffix('.json')
 if dest.exists() and meta.exists() and json.loads(meta.read_text())==signature:
  if inspect(dest,keep)['frames']==keep:return
 with av.open(str(path)) as ic,av.open(str(dest),'w') as oc:
  ic.streams.video[0].thread_count=1;v=oc.add_stream('libx264',rate=RATE)
  v.width,v.height=size;v.pix_fmt='yuv420p';v.time_base=Fraction(1,1)/RATE;v.codec_context.thread_count=1;v.options={'crf':'10','preset':'medium','bf':'3'}
  for n,f in enumerate(ic.decode(video=0)):
   if n>=keep:break
   assert not f.is_corrupt
   f=f.reformat(width=size[0],height=size[1],format='yuv420p');f.pts=n;f.time_base=Fraction(1,1)/RATE
   for p in v.encode(f):oc.mux(p)
  for p in v.encode():oc.mux(p)
 assert inspect(dest,keep)['frames']==keep
 meta.write_text(json.dumps(signature,indent=2),encoding='utf-8')
def audio_encode(duration):
 dest=OUTDIR/'_work/指定音轨.m4a';limit=round(duration*48000);written=0
 with av.open(str(AUDIO)) as ic,av.open(str(dest),'w') as oc:
  ic.streams.audio[0].thread_count=1;s=oc.add_stream('aac',rate=48000);s.layout='stereo';s.bit_rate=192000
  rs=av.AudioResampler(format='fltp',layout='stereo',rate=48000)
  def resampled():
   for frame in ic.decode(audio=0):yield from rs.resample(frame)
   yield from rs.resample(None)
  for f in resampled():
   if written>=limit:break
   if written+f.samples>limit:f=av.AudioFrame.from_ndarray(f.to_ndarray()[:,:limit-written],format='fltp',layout='stereo');f.sample_rate=48000
   f.pts=written;f.time_base=Fraction(1,48000);written+=f.samples
   for p in s.encode(f):oc.mux(p)
  for p in s.encode():oc.mux(p)
 return dest,written
def join(files,table,audio):
 tmp=OUTDIR/(FINAL.stem+'_待验证.mp4')
 with av.open(str(files[0])) as ic,av.open(str(audio)) as ac,av.open(str(tmp),'w') as oc:
  ov=oc.add_stream_from_template(ic.streams.video[0]);ov.codec_context.codec_tag='avc3'
  oa=oc.add_stream_from_template(ac.streams.audio[0]);offset=0
  for path,(name,keep) in zip(files,table):
   with av.open(str(path)) as c:
    v=c.streams.video[0];header=headers(v.codec_context.extradata);first=True;count=0
    for p in c.demux(v):
     if not p.size:continue
     shift=Fraction(offset,1)/RATE/p.time_base;assert shift.denominator==1
     if first:
      assert p.is_keyframe
      q=av.Packet(header+bytes(p));q.pts=p.pts;q.dts=p.dts;q.duration=p.duration;q.time_base=p.time_base;q.is_keyframe=True;p=q;first=False
     p.pts+=int(shift);p.dts+=int(shift);p.stream=ov;oc.mux(p);count+=1
    assert count==keep,(name,count,keep)
   offset+=keep
  for p in ac.demux(audio=0):
   if p.size:p.stream=oa;oc.mux(p)
 assert inspect(tmp,offset)['frames']==offset
 return tmp
def audio_array(path):
 chunks=[];rs=av.AudioResampler(format='flt',layout='mono',rate=8000)
 with av.open(str(path)) as c:
  c.streams.audio[0].thread_count=1
  for f in c.decode(audio=0):
   chunks.extend(x.to_ndarray().reshape(-1) for x in rs.resample(f))
  chunks.extend(x.to_ndarray().reshape(-1) for x in rs.resample(None))
 return np.concatenate(chunks)
def verify(tmp,table,size):
 selected={};offset=0
 for name,keep in table:
  with av.open(str(ROOT/'完成分段'/name)) as c:
   c.streams.video[0].thread_count=1
   for n,f in enumerate(c.decode(video=0)):
    if n in {0,keep//2,keep-1}:selected[offset+n]=np.asarray(f.to_image().resize((160,90))).astype(float)
    if n>=keep-1:break
  offset+=keep
 maes=[];overview=[];count=0
 with av.logging.Capture() as logs:
  with av.open(str(tmp)) as c:
   v=c.streams.video[0];v.thread_count=1;a=c.streams.audio[0]
   assert (v.width,v.height)==size and v.average_rate==RATE and v.codec_context.name=='h264'
   assert a.codec_context.name=='aac' and a.codec_context.sample_rate==48000 and len(a.codec_context.layout.channels)==2
   for n,f in enumerate(c.decode(video=0)):
    assert not f.is_corrupt and f.pts*f.time_base==Fraction(n,1)/RATE
    count+=1
    if n in selected:
     mae=float(np.abs(np.asarray(f.to_image().resize((160,90))).astype(float)-selected[n]).mean());assert mae<4,(n,mae);maes.append({'final_frame':n,'mae':mae})
    if n%12==0:overview.append((n,f.to_image().resize((320,184))))
  assert count==offset
  actual=audio_array(tmp);expected=audio_array(AUDIO);length=min(len(actual),len(expected),round(count/float(RATE)*8000))
  correlation=float(np.corrcoef(actual[:length],expected[:length])[0,1]);assert correlation>.98,correlation
 assert not logs,list(logs)
 folder=QA/'最终成片联系表';folder.mkdir(parents=True,exist_ok=True)
 for start in range(0,len(overview),30):
  page=overview[start:start+30];im=Image.new('RGB',(1920,206*((len(page)+5)//6)),'white');d=ImageDraw.Draw(im)
  for k,(n,pic) in enumerate(page):
   x=k%6*320;y=k//6*206;im.paste(pic,(x,y));d.text((x+3,y+186),f'{n/float(RATE):.3f}s',fill='black')
  im.save(folder/f'page_{start//30+1}.jpg',quality=95)
 return {'frames':count,'fps':str(RATE),'duration':count/float(RATE),'size':list(size),'video_codec':'h264','crf':10,'audio_codec':'aac','audio_rate':48000,'audio_channels':2,'soundtrack_correlation':correlation,'ordered_start_middle_end_checks':maes,'decode_errors':[],'technical_status':'passed','visual_status':'pending','sha256':sha(tmp),'file':str(FINAL)}
def main(argv=None):
 global FINAL,AUDIO,RATE
 ap=argparse.ArgumentParser(description=__doc__)
 ap.add_argument('--output',default='琵琶曲_完整版.mp4')
 ap.add_argument('--audio',type=Path,default=AUDIO)
 ap.add_argument('--deps',type=Path)
 ap.add_argument('--scalar-cpu',action='store_true')
 ap.add_argument('--resume',action='store_true',help='Validated trim caches are reused when their signatures match')
 args=ap.parse_args(argv)
 if Path(args.output).name!=args.output or not args.output.lower().endswith('.mp4'):
  raise SystemExit('--output 只接受最终组装中的 MP4 文件名')
 FINAL=OUTDIR/args.output;AUDIO=args.audio.resolve()
 if FINAL.exists():raise SystemExit('已有同名成片须先归档，不能覆盖：'+str(FINAL))
 if not AUDIO.is_file():raise SystemExit('缺少指定音轨：'+str(AUDIO))
 table=rows();expected={name for name,_ in table}
 if {p.name for p in (ROOT/'完成分段').glob('*.mp4')}!=expected:raise SystemExit('分段文件集合与原始帧数表不符')
 require_current_reviews(ROOT,table)
 info=[inspect(ROOT/'完成分段'/name,keep) for name,keep in table]
 rates={Fraction(r['rate']) for r in info}
 if len(rates)!=1:raise SystemExit('分段实际帧率不同，须按项目规则处理，不能直接拼接')
 RATE=rates.pop()
 if RATE<=0:raise SystemExit('无效帧率')
 size=Counter(tuple(r['size']) for r in info).most_common(1)[0][0];OUTDIR.mkdir(exist_ok=True);work=OUTDIR/'_work';work.mkdir(exist_ok=True)
 files=[]
 for name,keep in table:
  dest=work/name;trim(ROOT/'完成分段'/name,dest,keep,size);files.append(dest);print('Trimmed',name,keep,flush=True)
 audio,samples=audio_encode(sum(k for _,k in table)/float(RATE));tmp=join(files,table,audio)
 report=verify(tmp,table,size);report['audio_source_samples']=samples;report['audio_source_sha256']=sha(AUDIO);report['cpu_mode']=CPU_MODE
 assert not FINAL.exists(),'Existing final must be archived before replacement'
 tmp.replace(FINAL);report.update(file_size=FINAL.stat().st_size,mtime_ns=FINAL.stat().st_mtime_ns);(QA/'final-verification.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
 print(json.dumps({k:v for k,v in report.items() if k!='ordered_start_middle_end_checks'},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
