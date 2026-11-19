"""Independent actual-frame media check; never asserts visual approval.

python verify_media.py clip.mp4 --frames 73 --fps 24 --require-audio --output qa.json
Add --sheets folder --first 0 --last 65 to export every retained frame.
Requires PyAV and Pillow. Optional --deps points at project-local dependencies.
"""
from pathlib import Path
from fractions import Fraction
import argparse,hashlib,json,sys

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('video',type=Path)
    ap.add_argument('--frames',type=int)
    ap.add_argument('--min-frames',type=int)
    ap.add_argument('--fps',type=Fraction)
    ap.add_argument('--require-audio',action='store_true')
    ap.add_argument('--deps',type=Path)
    ap.add_argument('--scalar-cpu',action='store_true')
    ap.add_argument('--output',required=True,type=Path)
    ap.add_argument('--sheets',type=Path)
    ap.add_argument('--first',type=int,default=0)
    ap.add_argument('--last',type=int)
    args=ap.parse_args()
    if args.deps:sys.path.insert(0,str(args.deps.resolve()))
    import av
    from media_runtime import configure_cpu_mode
    cpu_mode=configure_cpu_mode(av,args.scalar_cpu)
    from PIL import Image,ImageDraw
    av.logging.set_level(av.logging.ERROR)
    record={'source':str(args.video.resolve()),'sha256':hashlib.sha256(args.video.read_bytes()).hexdigest(),'size_bytes':args.video.stat().st_size,'source_mtime_ns':args.video.stat().st_mtime_ns,'backend':f'PyAV {av.__version__}','cpu_mode':cpu_mode,'technical_status':'failed','visual_status':'pending'}
    pages=[];pictures=[];errors=[];count=0;last_time=None
    try:
        with av.logging.Capture() as logs:
            with av.open(str(args.video)) as c:
                v=c.streams.video[0];v.thread_count=1
                rate=v.average_rate
                record.update(width=v.width,height=v.height,fps=str(rate),codec=v.codec_context.name,audio_streams=len(c.streams.audio))
                if args.require_audio and not c.streams.audio:errors.append('Missing required input audio stream')
                if args.fps is not None and rate!=args.fps:errors.append('Unexpected frame rate')
                for n,frame in enumerate(c.decode(video=0)):
                    count+=1
                    if frame.is_corrupt:errors.append(f'Corrupt frame {n}')
                    if frame.pts is None:errors.append(f'Missing PTS at frame {n}')
                    else:
                        t=frame.pts*frame.time_base
                        if last_time is not None and t<=last_time:errors.append(f'Nonmonotonic timestamp {n}')
                        if n==0:first_time=t
                        if rate and abs(t-first_time-Fraction(n,1)/rate)>Fraction(1,100000):errors.append(f'Nonuniform timestamp {n}')
                        last_time=t
                    if args.sheets and n>=args.first and (args.last is None or n<args.last):
                        pictures.append((n,frame.to_image().resize((480,272),Image.Resampling.LANCZOS)))
                record['frames']=count
                if args.frames is not None and count!=args.frames:errors.append(f'Expected {args.frames} actual frames, got {count}')
                if args.min_frames is not None and count<args.min_frames:errors.append('Too few actual frames')
            if args.require_audio:
                with av.open(str(args.video)) as c:
                    if c.streams.audio:
                        c.streams.audio[0].thread_count=1
                        samples=sum(f.samples for f in c.decode(audio=0))
                        record['audio_samples']=samples
                        if samples==0:errors.append('Audio stream has no decoded samples')
        if logs:errors.extend(str(item) for item in logs)
    except Exception as exc:errors.append(str(exc))
    if pictures:
        args.sheets.mkdir(parents=True,exist_ok=True)
        for start in range(0,len(pictures),16):
            page=pictures[start:start+16];sheet=Image.new('RGB',(1920,296*((len(page)+3)//4)),'white');draw=ImageDraw.Draw(sheet)
            for k,(n,picture) in enumerate(page):
                x=k%4*480;y=k//4*296;sheet.paste(picture,(x,y));draw.text((x+6,y+276),f'frame {n}',fill='black')
            dest=args.sheets/f'page_{start//16+1}.jpg';sheet.save(dest);pages.append(str(dest.resolve()))
    record.update(errors=errors,contact_sheets=pages,technical_status='failed' if errors else 'passed')
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'technical_status':record['technical_status'],'frames':count,'errors':errors,'report':str(args.output)},ensure_ascii=False))
    if errors:raise SystemExit(1)
if __name__=='__main__':main()
