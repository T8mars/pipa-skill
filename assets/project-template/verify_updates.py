"""Offline regression for author-update integration and input-version recovery."""
from pathlib import Path
import argparse,importlib.util,json,sys,tempfile,subprocess,urllib.request

def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--project',type=Path)
    args=ap.parse_args();root=(args.project or Path(__file__).resolve().parent).resolve()
    sys.path.insert(0,str(root))
    from project_config import load_cast,frame_table,file_signature,require_current_reviews
    from validate_prompts import check_prompt
    spec=importlib.util.spec_from_file_location('updated_runner',root/'rh_batch.py')
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    urllib.request.urlopen=lambda *a,**kw:(_ for _ in ()).throw(AssertionError('Network disabled'))
    cast=load_cast(root)
    added=['002','006','016','018','031']
    assert all(cast[n]=='一二' for n in added) and cast['020']=='二'
    assert sum(len(c)==2 for c in cast.values())==11
    def prompt(count):
        definitions='\n'.join(f'<Subject {i}> is the person in <Picture {i}> with current face and outfit.' for i in range(1,count+1))
        return 'subject_definitions:\n'+definitions+'\n<Video 1> is the source.\n\nsummary:\n[video editing + reference generation] Replace source people.\n\nretention_analysis:\n<Subject 1>: fully_preserved.\n<Video 1>: partially_preserved; remove Kotte Animation.\n\ndetailed_description:\n[Shot 1] At 00:00.000, retain source action. At 00:01.000, retain the next action.\n\noverall_soundscape:\nN/A\n\nnon_diegetic_music:\nN/A\n'
    good=prompt(2);assert not check_prompt('002',good,cast)[0]
    assert check_prompt('002',prompt(1),cast)[0]
    for bad in [good+'summary:\nextra',good.replace('<Picture 2>','<Picture 1>'),good.replace('00:00.000','00:02.000'),good.replace('00:01.000','00:60.000'),good.replace('the next action','<Subject 3>'),good.replace('retain the next action','retain <Picture 1>')]:
        assert check_prompt('002',bad,cast)[0]
    assert not check_prompt('001',prompt(1),cast)[1], 'Old outfit keywords must not be required'
    print('PASS: latest cast and six-section/identity/time checks reject stale single-person prompts.')
    with tempfile.TemporaryDirectory(prefix='r2va-update-verify-') as folder:
        temp=Path(folder);m.STATE=str(temp/'state.json');m.SRC=str(temp/'videos');m.PROMPT=str(temp/'prompts')
        Path(m.SRC).mkdir();Path(m.PROMPT).mkdir();m.IMG={r:str(temp/(r+'.png')) for r in ['一','二']}
        for role,path in m.IMG.items():Path(path).write_bytes(role.encode())
        for n in added+['020']:
            (Path(m.SRC)/(n+' 39.mp4')).write_bytes(b'fixture-video')
            (Path(m.PROMPT)/(n+'.txt')).write_text(prompt(len(cast[n])),encoding='utf-8')
        calls=[]
        def upload_api(method,path,data=None,files=None,**kw):
            calls.append(files);return {'code':0,'data':{'fileName':str(len(calls))}}
        m.api=upload_api;state={'uploads':{},'tasks':{}}
        path=m.IMG['一'];first=m.upload(path,state);assert m.upload(path,state)==first and len(calls)==1
        Path(path).write_bytes(b'changed-image');assert m.upload(path,state)!=first and len(calls)==2
        state.pop('upload_sha256');m.upload(path,state);assert len(calls)==3
        print('PASS: same-path changed image and legacy unversioned cache upload again; unchanged bytes reuse cache.')
        m.upload=lambda path,state:Path(path).name
        routes=[]
        def route_api(method,path,data=None,**kw):
            routes.append((path,data));return {'taskId':'offline-'+str(len(routes)),'status':'RUNNING'}
        m.api=route_api;state={'uploads':{},'tasks':{}}
        for n in added+['020']:m.submit(n,state,{'单人':'single','双人':'dual','实例':'plus'})
        for path,data in routes[:5]:
            nodes={x['nodeId']:x['fieldValue'] for x in data['nodeInfoList']}
            assert path.endswith('/dual') and nodes['137']=='一.png' and nodes['139']=='二.png'
        nodes={x['nodeId']:x['fieldValue'] for x in routes[-1][1]['nodeInfoList']}
        assert routes[-1][0].endswith('/single') and nodes['137']=='二.png' and '139' not in nodes
        old=state['tasks']['002']['input_signature'];(Path(m.PROMPT)/'002.txt').write_text(good+'\n',encoding='utf-8')
        assert old!=m.input_signature('002')
        m.submit('002',state,{'单人':'single','双人':'dual','实例':'plus'})
        assert state['tasks']['002']['attempts']==2 and state['history']['002'][0]['attempts']==1
        Path(m.IMG['一']).write_bytes(b'new identity after task submission')
        m.app_config=lambda:{'单人':'single','双人':'dual','实例':'plus'}
        m.concurrency=lambda:3
        original_argv=sys.argv[:];sys.argv=['rh_batch.py','submit','002']
        previous_calls=len(routes)
        try:
            m.main()
        except SystemExit as error:
            assert '当前输入不符' in str(error)
        else:
            raise AssertionError('Changed input must not reuse or resubmit an existing task')
        finally:
            sys.argv=original_argv
        assert len(routes)==previous_calls
        print('PASS: changed identity on an existing task stops before upload/submission; task history remains intact.')
        print('PASS: five added dual segments use both correct upload nodes; 020 uses second identity as Picture1; retries retain history.')
        state={'uploads':{},'tasks':{'002':{'taskId':'existing-success','status':'RUNNING','attempts':7}}}
        m.api=lambda *a,**kw:{'status':'SUCCESS','results':[]}
        for _ in range(3):m.refresh(state)
        assert state['tasks']['002']['status']=='RESULTS_MISSING' and not m.needs_submit(state,'002') and not m.is_active(state,'002')
        assert state['tasks']['002']['attempts']==7 and state['tasks']['002']['taskId']=='existing-success'
        print('PASS: success without result URLs retains the existing paid task and never queues regeneration.')
        lock=temp/'writer.lock';code='import sys;sys.path.insert(0,sys.argv[1]);from rh_batch import writer_lock\nwith writer_lock(sys.argv[2]):pass'
        with m.writer_lock(str(lock)):
            result=subprocess.run([sys.executable,'-X','utf8','-c',code,str(root),str(lock)],capture_output=True,text=True)
            assert result.returncode!=0 and '已有状态写入进程' in result.stderr
        result=subprocess.run([sys.executable,'-X','utf8','-c',code,str(root),str(lock)],capture_output=True,text=True)
        assert result.returncode==0
        print('PASS: a live writer excludes a second process; released lock permits restart.')
    print('All author-update checks passed without network access.')

if __name__=='__main__':main()
