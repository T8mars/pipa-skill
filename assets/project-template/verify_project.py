"""Offline preflight: complete input decoding, frame tables, role map and audio."""
from pathlib import Path
from fractions import Fraction
import argparse,json,sys,subprocess,datetime,shutil
from project_config import load_cast,frame_table,file_signature

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--deps',type=Path)
    ap.add_argument('--output',type=Path)
    ap.add_argument('--scalar-cpu',action='store_true')
    ap.add_argument('--resume',action='store_true')
    ap.add_argument('--recheck-decoder-errors',action='store_true',help='One fresh-process cross-check for already contradictory decoder flags; original failure remains recorded')
    args=ap.parse_args()
    root=Path(__file__).resolve().parent
    sys.path.insert(0,str(args.deps.resolve() if args.deps else root/'制作分析/工具依赖'))
    cast=load_cast(root);rows=frame_table(root/'补齐生成用/原始帧数.txt')
    if {Path(n).stem for n,_ in rows}!=set(cast):raise ValueError('原始帧数表与角色映射的段号不一致')
    padded={}
    for line in (root/'补齐生成用/补齐后帧数.txt').read_text(encoding='utf-8-sig').splitlines():
        parts=line.split()
        if parts and parts[0].endswith('.mp4'):
            num=Path(parts[0]).stem
            if num in padded:raise ValueError('补齐帧数表重复：'+num)
            padded[num]=(int(parts[1]),parts[2])
    if set(padded)!=set(cast):raise ValueError('补齐帧数表与角色映射的段号不一致')
    records=[]
    for name,keep in rows:
        num=Path(name).stem;hits=list((root/'补齐生成用').glob(num+' *.mp4'))
        if len(hits)!=1:raise ValueError('源视频不唯一：'+num)
        path=hits[0];expected=int(path.stem.split()[1]);count=0
        role_label='一号二号' if cast[num]=='一二' else cast[num]+'号'
        if padded[num]!=(expected,role_label):raise ValueError('补齐帧数或角色标签不符：'+num)
        # Isolate native decoder state per input, keeping each full report.
        folder=root/'制作分析/源素材检查';folder.mkdir(parents=True,exist_ok=True)
        run_id=datetime.datetime.now().strftime('%Y%m%d_%H%M%S_%f');failures=[]
        cached_path=folder/(num+'.json');cached=None
        if args.resume and cached_path.is_file():
            previous=json.loads(cached_path.read_text(encoding='utf-8'))
            signature=file_signature(path)
            if (previous.get('technical_status')=='passed' and previous.get('frames')==expected
                    and previous.get('audio_samples',0)>0
                    and all(previous.get(k)==v for k,v in signature.items())
                    and (not args.scalar_cpu or previous.get('cpu_mode')=='scalar(cpu_flags=0)')):
                cached=previous
        for attempt in range(2):
            if cached is not None:
                report_path=cached_path;break
            report_path=folder/(num+'_'+run_id+f'_attempt{attempt+1}.json')
            command=[sys.executable,'-X','utf8',str(root/'verify_media.py'),str(path),'--frames',str(expected),'--min-frames',str(keep),'--require-audio','--output',str(report_path)]
            command.extend(['--deps',str(args.deps.resolve() if args.deps else root/'制作分析/工具依赖')])
            if args.scalar_cpu:command.append('--scalar-cpu')
            result=subprocess.run(command,capture_output=True,text=True,encoding='utf-8')
            if not result.returncode:break
            log=report_path.with_suffix('.log')
            log.write_text('exit_code='+str(result.returncode)+'\n'+result.stdout+'\n'+result.stderr,encoding='utf-8')
            failures.append({'exit_code':result.returncode,'log':str(log),'report':str(report_path) if report_path.exists() else None})
            decoder_recheck=False
            if args.recheck_decoder_errors and result.returncode==1 and report_path.exists():
                failed=json.loads(report_path.read_text(encoding='utf-8'))
                errors=failed.get('errors',[])
                decoder_recheck=bool(errors) and all('Corrupt frame' in e or "'h264'" in e for e in errors)
            if (result.returncode not in (3221225477,-1073741819) and not decoder_recheck) or attempt==1:
                raise ValueError('独立完整解码失败：'+num+'；详见'+str(log))
        media=cached or json.loads(report_path.read_text(encoding='utf-8'))
        if not cached:shutil.copy2(report_path,cached_path)
        count=media['frames'];rate=Fraction(media['fps']);samples=media['audio_samples']
        if media['technical_status']!='passed' or rate<=0 or count!=expected or count<keep or not samples:raise ValueError('源素材帧数/音轨/解码错误：'+num)
        records.append({'num':num,'cast':cast[num],'frames':count,'keep':keep,'fps':str(rate),'audio_samples':samples,'cpu_mode':media.get('cpu_mode'),'prior_tool_crashes':failures,'decode_report':str(report_path),**file_signature(path)})
    report={'status':'passed','network_requests':0,'sources':records,'retained_frames':sum(k for _,k in rows),'padded_frames':sum(r['frames'] for r in records)}
    out=args.output or root/'制作分析/输入检查.json';out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(f'PASS: {len(records)} sources, {report["retained_frames"]} retained / {report["padded_frames"]} padded frames; roles, timestamps and audio decoded. No network requests.')

if __name__=='__main__':main()
