"""Route Pipa shot IDs through the selected provider; preserve native review inputs."""
import json
import os
from pathlib import Path
import shutil
import tempfile
import time
from urllib.error import URLError

import channels
import rh_core as core


def entry_for(batch, root, num, name):
    text = Path(batch.PROMPT) / (num + '.txt')
    errors, _ = batch.check_prompt(num, text.read_text(encoding='utf-8-sig'), batch.CAST)
    if errors:
        raise ValueError(num + ': ' + '；'.join(errors))
    motion = Path(batch.video_path(num))
    frames, fps, _ = channels.probe(root, motion)
    if frames != int(batch.frames_of(num)):
        raise ValueError(num + ' decoded length differs from the source frame table')
    refs = [Path(batch.IMG[role]).relative_to(root).as_posix() for role in batch.CAST[num]]
    entry = {'window_id':num,'reference_paths':refs,'prompt_path':text.relative_to(root).as_posix(),
             'video_path':motion.relative_to(root).as_posix(),'profile':channels.profile(name),
             'generation_frames':frames,'generation_fps':str(fps)}
    channels.wire_frames(root,entry,batch.CAST[num])
    return entry


def signature(batch, entry):
    contract = {k:v for k,v in entry.items() if k != 'retry_after'}
    return core.digest_object({'contract':contract,'source':batch.input_signature(entry['window_id'])})


def publish_copy(source, dest):
    dest.parent.mkdir(exist_ok=True, parents=True)
    if not dest.exists():
        with tempfile.NamedTemporaryFile(dir=dest.parent,delete=False) as temp:
            path = Path(temp.name)
            try:
                with source.open('rb') as handle:
                    shutil.copyfileobj(handle,temp)
            except BaseException:
                temp.close()
                path.unlink(missing_ok=True)
                raise
        try:
            try:
                os.link(path,dest)
            except FileExistsError:
                pass
        finally:
            path.unlink(missing_ok=True)
    if core.sha(source) != core.sha(dest):
        raise ValueError('Archive the previous output before selecting a different candidate: ' + dest.name)


def run(batch, argv):
    root = Path(batch.ROOT).resolve()
    name = channels.selected(root)
    cmd, *nums = argv
    rejected = None
    if cmd == 'retry':
        if len(nums) != 2 or nums[0] not in batch.CAST:
            raise ValueError('Use retry <shot> <reviewed local attempt ID>')
        rejected = nums.pop()
        cmd = 'submit'
    if cmd not in ('submit','status','download') or cmd == 'submit' and (not nums or any(n not in batch.CAST for n in nums)):
        raise ValueError('Use submit <shot IDs>, status or download')
    entries = [entry_for(batch,root,n,name) for n in dict.fromkeys(nums)] if cmd == 'submit' else []
    account = channels.account_for(name)
    ledger = core.Ledger(channels.ledger_path(root,name))
    state_file = root / (name+'_state.json')
    try:
        runner = channels.make_runner(root,ledger,account)
        def rows():
            return {r['window']:r for r in ledger.db.execute('SELECT * FROM attempts WHERE account=? AND project=? ORDER BY created,rowid',(account,str(root)))}
        if rejected:
            num = nums[0]
            previous = rows().get(num)
            if not previous or previous['id'] != rejected or previous['state'] not in ('DOWNLOADED','VISUAL_FAILED'):
                raise ValueError('Retry must identify the current, visually rejected candidate')
            dest = root/'完成分段'/(num+'.mp4')
            old = Path(previous['output'])
            if not old.is_file() or dest.exists() and core.sha(dest)!=core.sha(old):
                raise ValueError('The reviewed candidate has changed')
            if dest.exists():
                archive = old.with_name('previous-selection.mp4')
                if archive.exists():
                    raise ValueError('Previous selection is already archived')
                os.rename(dest,archive)
            ledger.update(rejected,state='VISUAL_FAILED')
            ledger.event(rejected,'VISUAL_RETRY_REQUESTED',{'window':num})
        def validate(_, entry):
            return signature(batch,entry)
        while True:
            for row in rows().values():
                if row['state'] in core.POLLABLE:
                    runner.recover(row['id'])
            before = rows()
            for entry in entries:
                row = before.get(entry['window_id'])
                if row and row['state']=='VISUAL_FAILED':
                    entry['retry_after']=row['id']
                if row and row['digest'] != validate(root,entry) and row['state']!='VISUAL_FAILED':
                    raise ValueError('Changed paid inputs require a new production directory')
                if row and row['state'] not in ('DEFERRED','EXECUTION_FAILED','VISUAL_FAILED'):
                    continue
                account_row = ledger.account(account)
                unresolved = ledger.db.execute("SELECT 1 FROM attempts WHERE account=? AND state IN ('SUBMITTING','SUBMISSION_UNKNOWN')",(account,)).fetchone()
                if unresolved or account_row['stopped'] or account_row['cooldown'] > ledger.clock() or ledger.summary(account)['active'] >= 3:
                    break
                try:
                    runner.submit_one(root,entry,validate)
                except core.ApiFailure as exc:
                    if exc.code not in core.DEFERRED_CODES:
                        raise
                    ledger.cooldown(account)
                    break
                except (URLError,TimeoutError):
                    ledger.cooldown(account)
                    break
            tasks = {}
            for num, row in rows().items():
                status = row['state']
                if status == 'DOWNLOADED':
                    publish_copy(Path(row['output']),root/'完成分段'/(num+'.mp4'))
                    status = 'SUCCESS'
                tasks[num] = {'taskId':row['task_id'],'attempt':row['id'],'status':status,'digest':row['digest']}
            with tempfile.NamedTemporaryFile(dir=root,delete=False) as temp:
                path = Path(temp.name)
                temp.write(json.dumps({'provider':name,'tasks':tasks},ensure_ascii=False,indent=2).encode())
            os.replace(path,state_file)
            print(json.dumps({n:r['status'] for n,r in tasks.items()}),flush=True)
            if cmd != 'submit' or all(tasks.get(n,{}).get('status')=='SUCCESS' for n in nums):
                return
            if ledger.account(account)['stopped'] or ledger.db.execute("SELECT 1 FROM attempts WHERE account=? AND state IN ('SUBMITTING','SUBMISSION_UNKNOWN')",(account,)).fetchone():
                raise SystemExit('Account paused; reconcile the recorded attempt before continuing')
            if any(tasks.get(n,{}).get('status')=='RESULTS_MISSING' or ledger.db.execute(
                    'SELECT 1 FROM failures WHERE account=? AND project=? AND window=? AND count>=3',
                    (account,str(root),n)).fetchone() for n in nums):
                raise SystemExit('Review failed shot and original receipt; successful candidates are retained')
            time.sleep(12)
    finally:
        ledger.close()
