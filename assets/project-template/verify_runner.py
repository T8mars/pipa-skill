"""Offline checks of retry bounds, concurrency and download-only recovery."""
from pathlib import Path
import importlib.util
import tempfile
import urllib.error
import sys

root = Path(sys.argv[2]).resolve() if len(sys.argv)==3 and sys.argv[1]=='--project' else Path(__file__).resolve().parent
sys.path.insert(0,str(root))
spec = importlib.util.spec_from_file_location('runner_under_check', root/'rh_batch.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
m.time.sleep = lambda _: None
m.urllib.request.urlopen = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError('Network disabled'))

def prompt_fixture(cast):
    definitions='\n'.join(f'<Subject {i}> is the character in <Picture {i}> with current identity and clothes.' for i in range(1,len(cast)+1))
    return 'subject_definitions:\n'+definitions+'\n<Video 1> is the source.\n\nsummary:\n[video editing + reference generation] Replace the source actors.\n\nretention_analysis:\n<Subject 1>: fully_preserved.\n<Video 1>: partially_preserved; remove Kotte Animation.\n\ndetailed_description:\n[Shot 1] At 00:00.000, follow the source motion.\n\noverall_soundscape:\nN/A\n\nnon_diegetic_music:\nN/A\n'

with tempfile.TemporaryDirectory(prefix='r2va-verify-') as temp:
    temporary = Path(temp)
    m.STATE = str(temporary/'state.json')
    m.IMG = {role:str(temporary/(role+'.png')) for role in ['一','二']}
    for path in m.IMG.values():Path(path).write_bytes(b'offline-identity-fixture')
    original_prompt = str(temporary/'prompts')
    Path(original_prompt).mkdir()
    m.SRC = str(temporary/'videos')
    Path(m.SRC).mkdir()
    for num in ['001','002','003','004','022']:
        (Path(original_prompt)/(num+'.txt')).write_text(prompt_fixture(m.CAST[num]),encoding='utf-8')
        (Path(m.SRC)/(num+' 39.mp4')).write_bytes(b'Not uploaded: stub input')
    m.PROMPT = str(temporary/'missing-prompts')
    m.poll_and_download = lambda state: None
    state = {'uploads': {}, 'tasks': {}}
    m.wait_until_done(['001'], state, {}, 3)
    assert state['tasks']['001']['attempts'] == 3
    assert state['tasks']['001']['status'] == 'FAILED'
    assert m.given_up(state, '001')
    print('PASS: failure before submission stops after exactly three attempts.')

    m.PROMPT = original_prompt
    m.upload = lambda path, state: 'offline-upload-token'
    highest = [0]
    def api_stub(method, path, data=None, **kwargs):
        active = len(m.active_nums(state))
        highest[0] = max(highest[0], active + 1)
        assert active < 3
        return {'taskId': f'offline-{len(state["tasks"])}', 'status': 'QUEUED'}
    m.api = api_stub
    def complete_one(state):
        for task in state['tasks'].values():
            if task['status'] in ('QUEUED', 'RUNNING'):
                task.update(status='SUCCESS', file='offline-placeholder')
                return
    m.poll_and_download = complete_one
    state = {'uploads': {}, 'tasks': {}}
    m.fill_slots(['001','002','003','004'], state,
                 {'单人':'offline-app','双人':'offline-dual','实例':'plus'}, 3)
    assert len(state['tasks']) == 4 and highest[0] == 3
    print('PASS: a fourth segment waits for a slot; no more than three run together.')

    m.OUT = str(temporary/'downloads')
    state = {'uploads': {}, 'tasks': {'001': {
        'status':'SUCCESS', 'attempts':1, 'taskId':'offline-success',
        'results':[{'url':'https://invalid.example/offline.mp4','outputType':'mp4'}]}}}
    m.retrieve_result = lambda *args, **kwargs: (_ for _ in ()).throw(urllib.error.URLError('offline simulated download failure'))
    for _ in range(3):
        m.download(state)
    assert state['tasks']['001']['status'] == 'DOWNLOAD_FAILED'
    assert not m.needs_submit(state, '001') and not m.is_active(state, '001')
    assert state['tasks']['001']['taskId'] == 'offline-success'
    print('PASS: failed downloads retain the successful task and never request regeneration.')

    m.retrieve_result = lambda url, dest: Path(dest).write_bytes(b'offline-download-fixture')
    m.poll_and_download = lambda state: m.download(state)
    m.api = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError('Recovery must not submit or query the API'))
    original_argv = sys.argv[:]
    sys.argv = ['rh_batch.py','download']
    try:
        m.main()
    finally:
        sys.argv = original_argv
    recovered = m.load_state()['tasks']['001']
    assert recovered['status']=='SUCCESS' and recovered['taskId']=='offline-success' and recovered['attempts']==1
    assert Path(recovered['file']).read_bytes()==b'offline-download-fixture'
    assert not Path(recovered['file']+'.part').exists()
    print('PASS: download command recovers the existing result atomically without regeneration.')

    state = {'uploads': {}, 'tasks': {}}
    m.api = lambda *args, **kwargs: {'taskId':'', 'errorCode':'1913', 'errorMessage':'permission denied'}
    try:
        m.try_submit('001', state, {'单人':'offline-app','双人':'offline-dual','实例':'plus'})
    except SystemExit:
        pass
    else:
        raise AssertionError('An application permission error must stop the batch.')
    assert state['tasks']['001']['attempts'] == 1
    assert state['tasks']['001']['status'] == 'FAILED'
    assert not state['tasks']['001'].get('taskId')
    print('PASS: application permission error 1913 stops on the first rejected segment.')

    clock = [1000.0]
    m.time.time = lambda: clock[0]
    m.time.sleep = lambda seconds: clock.__setitem__(0, clock[0] + seconds)
    state = {'uploads': {}, 'tasks': {}}
    calls = []
    def queue_once(method, path, data=None, **kwargs):
        calls.append(clock[0])
        if len(calls)==1:
            return {'taskId':'', 'errorCode':'421', 'errorMessage':'queue full'}
        assert clock[0]>=1060.0, 'The next segment must respect the account-wide cooldown.'
        return {'taskId':'offline-queue-recovered', 'status':'RUNNING'}
    m.api=queue_once
    m.poll_and_download=lambda state: None
    m.fill_slots(['001','002'],state,{'单人':'offline-app','双人':'offline-dual','实例':'plus'},3)
    assert state['tasks']['001']['attempts']==1 and state['tasks']['001']['automatic_failures']==1
    assert state['tasks']['002']['taskId']=='offline-queue-recovered'
    assert 'submit_not_before' not in state
    print('PASS: queue error 421 cools down the whole batch before the next segment, preserving retry counts.')

    state = {'uploads': {}, 'tasks': {'022': {'status':'FAILED', 'attempts':50,
             'automatic_failures':0, 'error':'Visual review: still front-facing'}}}
    assert m.needs_submit(state, '022') and not m.given_up(state, '022')
    m.api = lambda *args, **kwargs: {'taskId':'offline-quality-retry', 'status':'RUNNING'}
    m.submit('022', state, {'单人':'offline-app','双人':'offline-dual','实例':'plus'})
    assert state['tasks']['022']['attempts'] == 51
    assert state['tasks']['022']['taskId'] == 'offline-quality-retry'
    state['tasks']['022']['status'] = 'FAILED'
    state['tasks']['022']['automatic_failures'] = 3
    assert m.given_up(state, '022') and not m.needs_submit(state, '022')
    print('PASS: visual retries have no attempt cap by default; cumulative counts persist and three consecutive API failures still stop.')

print('All checks passed without a network request.')
