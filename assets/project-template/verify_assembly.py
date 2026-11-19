"""Offline synthetic-media assembly regression; never uses production outputs."""
from pathlib import Path
from fractions import Fraction
import argparse,json,shutil,subprocess,sys,tempfile,wave

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--deps',required=True,type=Path)
    ap.add_argument('--scalar-cpu',action='store_true')
    ap.add_argument('--output',type=Path)
    args=ap.parse_args();source=Path(__file__).resolve().parent
    sys.path.insert(0,str(args.deps.resolve()))
    import av,numpy as np
    from project_config import file_signature
    from media_runtime import configure_cpu_mode
    configure_cpu_mode(av,args.scalar_cpu)
    results=[]
    with tempfile.TemporaryDirectory(prefix='r2va-assembly-verify-') as folder:
        for rate,audio_seconds in [(24,2.0),(12,0.25)]:
            root=Path(folder)/str(rate);clips=root/'完成分段';clips.mkdir(parents=True)
            (root/'补齐生成用').mkdir();qa=root/'制作分析/生成复核';qa.mkdir(parents=True)
            for name in ['assemble.py','assemble_av.py','project_config.py','media_runtime.py']:
                shutil.copy2(source/name,root/name)
            table=[('001.mp4',7),('002.mp4',4),('003.mp4',6)]
            (root/'补齐生成用/原始帧数.txt').write_text('\n'.join(f'{n}\t{k}' for n,k in table),encoding='utf-8')
            reviews={};tasks={}
            for index,(name,keep) in enumerate(table):
                width,height=(96,64) if index==2 else (64,48)
                path=clips/name
                with av.open(str(path),'w') as c:
                    v=c.add_stream('libx264',rate=rate);v.width=width;v.height=height;v.pix_fmt='yuv420p';v.time_base=Fraction(1,rate);v.codec_context.thread_count=1;v.options={'crf':'0','preset':'medium'}
                    for n in range(keep+1):
                        pixels=np.empty((height,width,3),dtype=np.uint8);pixels[:]=[35+index*65,40+n*9,70+index*20]
                        frame=av.VideoFrame.from_ndarray(pixels,format='rgb24');frame.pts=n;frame.time_base=Fraction(1,rate)
                        for packet in v.encode(frame):c.mux(packet)
                    for packet in v.encode():c.mux(packet)
                num=Path(name).stem;tasks[num]={'status':'SUCCESS','taskId':'synthetic-'+num}
                reviews[num]={'status':'passed','taskId':tasks[num]['taskId'],'reviewed_retained_frames':[0,keep],**file_signature(path)}
            (root/'rh_state.json').write_text(json.dumps({'tasks':tasks}),encoding='utf-8')
            review_path=qa/'visual-reviews.json';review_path.write_text(json.dumps(reviews),encoding='utf-8')
            audio=root/'fixture.wav';samples=np.arange(round(audio_seconds*48000))/48000
            pcm=(np.sin(2*np.pi*440*samples)*12000).astype('<i2')
            with wave.open(str(audio),'wb') as w:w.setnchannels(1);w.setsampwidth(2);w.setframerate(48000);w.writeframes(pcm.tobytes())
            command=[sys.executable,'-X','utf8',str(root/'assemble.py'),'--backend-av','--deps',str(args.deps.resolve()),'--audio',str(audio),'--output','fixture.mp4']
            if args.scalar_cpu:command.append('--scalar-cpu')
            before={name:file_signature(clips/name)['sha256'] for name,_ in table}
            r=subprocess.run(command,capture_output=True,text=True,encoding='utf-8')
            if r.returncode:raise RuntimeError(r.stdout+'\n'+r.stderr)
            report=json.loads((qa/'final-verification.json').read_text(encoding='utf-8'))
            assert report['frames']==17 and Fraction(report['fps'])==rate and report['size']==[64,48]
            assert report['technical_status']=='passed' and report['visual_status']=='pending'
            assert report['soundtrack_correlation']>.98 and len(report['ordered_start_middle_end_checks'])==9
            assert abs(report['duration']-17/rate)<1e-8
            if audio_seconds<17/rate:assert report['audio_source_samples']==len(pcm)
            final=root/'完成分段/最终组装/fixture.mp4';final_sha=file_signature(final)['sha256']
            assert before=={name:file_signature(clips/name)['sha256'] for name,_ in table}
            r=subprocess.run(command,capture_output=True,text=True,encoding='utf-8')
            assert r.returncode and file_signature(final)['sha256']==final_sha
            ffmpeg_command=[sys.executable,'-X','utf8',str(root/'assemble.py'),'--audio',str(audio),'--output','ffmpeg_fixture.mp4']
            r=subprocess.run(ffmpeg_command,capture_output=True,text=True,encoding='utf-8')
            if r.returncode:raise RuntimeError('FFmpeg backend failed:\n'+r.stdout+'\n'+r.stderr)
            ffmpeg_final=final.parent/'ffmpeg_fixture.mp4'
            verify_code="from pathlib import Path;from fractions import Fraction;import json,sys;sys.path.insert(0,sys.argv[1]);import assemble_av as m;m.RATE=Fraction(sys.argv[2]);m.AUDIO=Path(sys.argv[3]);m.FINAL=Path(sys.argv[4]);r=m.verify(m.FINAL,m.rows(),(64,48));print(json.dumps({'frames':r['frames'],'correlation':r['soundtrack_correlation']}))"
            verify_command=[sys.executable,'-X','utf8','-c',verify_code,str(root),str(rate),str(audio),str(ffmpeg_final),'--deps',str(args.deps.resolve())]
            if args.scalar_cpu:verify_command.append('--scalar-cpu')
            r=subprocess.run(verify_command,capture_output=True,text=True,encoding='utf-8')
            if r.returncode:raise RuntimeError('Independent verification of FFmpeg output failed:\n'+r.stdout+'\n'+r.stderr)
            ffmpeg_check=json.loads(r.stdout);assert ffmpeg_check['frames']==17 and ffmpeg_check['correlation']>.98
            assert not list((final.parent/'_work').glob('*_s.mp4')), 'Minority-size input must scale in its one CRF10 encode'
            ffmpeg_final.rename(final.parent/'ffmpeg_archived.mp4')
            r=subprocess.run(ffmpeg_command+['--resume'],capture_output=True,text=True,encoding='utf-8')
            if r.returncode:raise RuntimeError('FFmpeg resume failed:\n'+r.stdout+'\n'+r.stderr)
            assert all('resume '+name in r.stdout for name,_ in table)
            negative=command.copy();negative[negative.index('fixture.mp4')]='negative.mp4'
            (clips/'999.mp4').write_bytes(b'extra fixture')
            assert subprocess.run(negative,capture_output=True).returncode
            (clips/'999.mp4').unlink()
            reviews['001']['source_mtime_ns']-=1;review_path.write_text(json.dumps(reviews),encoding='utf-8')
            assert subprocess.run(negative,capture_output=True).returncode
            assert not (final.parent/'negative.mp4').exists()
            assert before=={name:file_signature(clips/name)['sha256'] for name,_ in table}
            results.append({'fps':rate,'audio_seconds':audio_seconds,'frames':17,'majority_size':[64,48],'soundtrack_correlation':report['soundtrack_correlation'],'ffmpeg_backend_independently_verified':ffmpeg_check,'source_bytes_preserved':True,'existing_final_preserved':True,'extra_segment_rejected':True,'stale_review_rejected':True,'visual_status_remains_pending':True})
            print(f'PASS: {rate}fps, majority scaling, exact ordered frames, {audio_seconds}s audio, source preservation and failure gates.')
    if args.output:
        args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps({'status':'passed','network_requests':0,'cases':results},ensure_ascii=False,indent=2),encoding='utf-8')
    print('All synthetic-media checks passed without network requests or production-file changes.')

if __name__=='__main__':main()
