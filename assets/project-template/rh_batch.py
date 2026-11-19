# -*- coding: utf-8 -*-
"""按段提交 RunningHub 人物替换任务。

密钥、应用、并发都从项目根目录的 txt 读，不写死在这里：
api_key.txt 一行密钥；ai应用.txt 写「单人 <id>」「双人 <id>」「实例 <规格>」；
并发数.txt 一行整数。
单人段节点 137 图 / 138 提示词 / 321 视频 / 326 帧数，双人段另加节点 139 第二张图。

成功立刻下载。画面质量重试不设次数上限；连续自动执行失败 3 次停止。
"""
import json
import os
import sys
import time
import ipaddress
import shutil
import subprocess
import urllib.request
import urllib.error
import urllib.parse
import http.client
import hashlib
import re
from contextlib import contextmanager
from project_config import load_cast
from validate_prompts import check_prompt

BASE = "https://www.runninghub.ai"
ROOT = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(ROOT, "补齐生成用")
PROMPT = os.path.join(ROOT, "提示词")
REF = os.path.join(ROOT, "替换用参考图")
OUT = os.path.join(ROOT, "完成分段")
STATE = os.path.join(ROOT, "rh_state.json")
MAX_AUTOMATIC_FAILURES = 3


class ApiConfigurationError(RuntimeError):
    """凭据、权限或余额错误，停止批量提交等待处理。"""


class ApiQueueLimitError(RuntimeError):
    """The account queue is full; cool down all new submissions."""


# 人数包括局部手袖和背景人物；提交与校验共用这一份项目映射。
CAST = load_cast(ROOT)
IMG = {"一": os.path.join(REF, "一号.png"), "二": os.path.join(REF, "二号.png")}


def read_text(name):
    path = os.path.join(ROOT, name)
    if not os.path.isfile(path):
        raise SystemExit(f"缺少 {name}")
    with open(path, encoding="utf-8-sig") as f:
        return f.read().strip()


def api_key():
    k = read_text("api_key.txt")
    if not k:
        raise SystemExit("api_key.txt 是空的")
    return k


def app_config():
    """ai应用.txt：单人 <id> / 双人 <id> / 实例 <规格>。"""
    cfg = {}
    for line in read_text("ai应用.txt").splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0] in ("单人", "双人", "实例"):
            cfg[parts[0]] = parts[1]
    missing = [k for k in ("单人", "双人", "实例") if not cfg.get(k)]
    if missing:
        raise SystemExit("ai应用.txt 缺少：" + "、".join(missing))
    return cfg


def concurrency():
    text = read_text("并发数.txt").splitlines()[0].strip()
    try:
        n = int(text)
    except ValueError:
        raise SystemExit(f"并发数.txt 不是整数：{text}")
    if n < 1:
        raise SystemExit("并发数.txt 至少是 1")
    return n


def api(method, path, data=None, files=None, timeout=300):
    key = api_key()
    url = BASE + path
    if files is None:
        body = json.dumps(data).encode("utf-8") if data is not None else None
        req = urllib.request.Request(url, data=body, method=method)
        req.add_header("Content-Type", "application/json")
        req.add_header("Authorization", "Bearer " + key)
    else:
        import uuid
        boundary = uuid.uuid4().hex
        name, content = files
        chunks = [
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="file"; filename="{name}"\r\n'.encode(),
            b"Content-Type: application/octet-stream\r\n\r\n",
            content,
            f"\r\n--{boundary}--\r\n".encode(),
        ]
        body = b"".join(chunks)
        req = urllib.request.Request(url, data=body, method="POST")
        req.add_header("Content-Type", "multipart/form-data; boundary=" + boundary)
        req.add_header("Authorization", "Bearer " + key)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace").replace(key, "[REDACTED]")
        if e.code in (401, 402, 403):
            raise ApiConfigurationError(f"HTTP {e.code} {path}: {detail[:500]}")
        raise RuntimeError(f"HTTP {e.code} {path}: {detail[:500]}")


def load_state():
    if os.path.exists(STATE):
        with open(STATE, encoding="utf-8") as f:
            return json.load(f)
    return {"uploads": {}, "tasks": {}}


def save_state(state):
    tmp = STATE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp, STATE)


def upload(path, state):
    key = os.path.abspath(path)
    with open(path, "rb") as f:
        content = f.read()
    signature = hashlib.sha256(content).hexdigest()
    cached = state["uploads"].get(key)
    if cached and state.get("upload_sha256", {}).get(key) == signature:
        return cached
    res = api("POST", "/openapi/v2/media/upload/binary",
              files=(os.path.basename(path), content))
    if res.get("code") != 0:
        raise RuntimeError(f"上传失败 {path}: {res}")
    name = res["data"]["fileName"]
    state["uploads"][key] = name
    state.setdefault("upload_sha256", {})[key] = signature
    save_state(state)
    print(f"uploaded {os.path.basename(path)} -> {name}", flush=True)
    return name


def video_path(num):
    hits = [f for f in os.listdir(SRC) if f.startswith(num + " ") and f.endswith(".mp4")]
    if len(hits) != 1:
        raise SystemExit(f"{num} 的源视频不唯一: {hits}")
    return os.path.join(SRC, hits[0])


def frames_of(num):
    # 326 节点填这一段要生成的帧数，用源视频文件名里的帧数
    name = os.path.basename(video_path(num))
    return name.split(" ")[1].replace(".mp4", "")


def attempts_of(state, num):
    return int(state["tasks"].get(num, {}).get("attempts") or 0)


def input_signature(num):
    def digest(path):
        with open(path, 'rb') as handle:
            return hashlib.sha256(handle.read()).hexdigest()
    return {'cast': CAST[num], 'prompt_sha256': digest(os.path.join(PROMPT, num + '.txt')),
            'source_sha256': digest(video_path(num)),
            'reference_sha256': [digest(IMG[role]) for role in CAST[num]]}


def given_up(state, num):
    t = state["tasks"].get(num) or {}
    return (t.get("status") == "FAILED"
            and int(t.get("automatic_failures") or 0) >= MAX_AUTOMATIC_FAILURES)


def failure_label(state, num):
    failures = int(state["tasks"].get(num, {}).get("automatic_failures") or 0)
    return f"attempt {attempts_of(state, num)}, API failures {failures}/{MAX_AUTOMATIC_FAILURES}"


def is_active(state, num):
    t = state["tasks"].get(num) or {}
    st = t.get("status")
    if st == "SUCCESS" and t.get("file"):
        return False
    if given_up(state, num):
        return False
    if st in ("FAILED", "DOWNLOAD_FAILED", "RESULTS_MISSING", "INPUT_INVALID"):
        return False
    return bool(t.get("taskId"))


def needs_submit(state, num):
    t = state["tasks"].get(num)
    if t is None:
        return True
    if t.get("status") == "SUCCESS" and t.get("file"):
        return False
    if t.get("status") == "SUCCESS" and not t.get("file"):
        return False
    if given_up(state, num):
        return False
    if t.get("status") in ("FAILED", "INPUT_INVALID"):
        return True
    return False


def submit(num, state, cfg):
    attempts = attempts_of(state, num) + 1
    failures = int(state["tasks"].get(num, {}).get("automatic_failures") or 0)
    if num in state['tasks']:
        state.setdefault('history', {}).setdefault(num, []).append(dict(state['tasks'][num]))
    state["tasks"][num] = {"status": "SUBMITTING", "attempts": attempts,
                           "automatic_failures": failures}
    save_state(state)
    cast = CAST[num]
    prompt = open(os.path.join(PROMPT, num + ".txt"), encoding="utf-8").read().strip()
    errors, _ = check_prompt(num, prompt, CAST)
    if errors:
        state['tasks'][num].update(status='INPUT_INVALID', error='；'.join(errors))
        save_state(state)
        raise SystemExit(num + " 提示词检查失败：" + "；".join(errors))
    signature = input_signature(num)
    state['tasks'][num]['input_signature'] = signature
    save_state(state)
    nodes = [
        {"nodeId": "137", "fieldName": "image",
         "fieldValue": upload(IMG[cast[0]], state), "description": None},
        {"nodeId": "138", "fieldName": "value", "fieldValue": prompt, "description": None},
        {"nodeId": "321", "fieldName": "video",
         "fieldValue": upload(video_path(num), state), "description": None},
        {"nodeId": "326", "fieldName": "value", "fieldValue": frames_of(num), "description": None},
    ]
    app = cfg["单人"]
    if len(cast) == 2:
        nodes.append({"nodeId": "139", "fieldName": "image",
                      "fieldValue": upload(IMG[cast[1]], state), "description": None})
        app = cfg["双人"]
    res = api("POST", "/openapi/v2/run/ai-app/" + app, {
        "nodeInfoList": nodes, "instanceType": cfg["实例"], "usePersonalQueue": "false"})
    if str(res.get("errorCode")) == "1913":
        raise ApiConfigurationError("RunningHub 应用权限错误 1913: " + str(res.get("errorMessage") or "无调用权限"))
    if str(res.get("errorCode")) == "421":
        raise ApiQueueLimitError("RunningHub 421：账户队列已满，等待释放名额")
    if not res.get("taskId"):
        raise RuntimeError(f"{num} 提交失败: {res}")
    state["tasks"][num] = {
        "taskId": res["taskId"],
        "status": res.get("status"),
        "app": app,
        "frames": frames_of(num),
        "attempts": attempts,
        "automatic_failures": failures,
        "input_signature": signature,
    }
    save_state(state)
    print(f"submitted {num} -> {res['taskId']} ({res.get('status')}) attempt {attempts}",
          flush=True)


def mark_failed(num, state, err):
    t = state["tasks"].setdefault(num, {})
    t["status"] = "FAILED"
    t["error"] = re.sub(r'https?://\S+', '[URL omitted]', str(err))[:500]
    t["attempts"] = max(attempts_of(state, num), 1)
    t["automatic_failures"] = int(t.get("automatic_failures") or 0) + 1
    save_state(state)
    n = t["attempts"]
    if given_up(state, num):
        print(f"{num} FAILED {failure_label(state, num)}, give up: {t['error']}", flush=True)
    else:
        print(f"{num} FAILED {failure_label(state, num)}, will retry: {t['error']}", flush=True)


def try_submit(num, state, cfg):
    try:
        submit(num, state, cfg)
        state.pop("submit_not_before", None)
        save_state(state)
        return True
    except ApiQueueLimitError as e:
        mark_failed(num, state, e)
        state["submit_not_before"] = time.time() + 60
        save_state(state)
        print("账户队列限流：全批次暂停新增提交60秒，继续查询与下载。", flush=True)
        return False
    except ApiConfigurationError as e:
        mark_failed(num, state, e)
        raise SystemExit("停止批量提交：请处理 API 凭据、权限或余额问题。")
    except (RuntimeError, OSError, urllib.error.URLError, json.JSONDecodeError) as e:
        mark_failed(num, state, e)
        return False


def refresh(state):
    for num, t in list(state["tasks"].items()):
        if t.get("status") == "SUCCESS" and t.get("file") and os.path.isfile(t['file']):
            continue
        if t.get("status") in ("FAILED", "DOWNLOAD_FAILED", "RESULTS_MISSING"):
            continue
        if not t.get("taskId"):
            continue
        try:
            res = api("POST", "/openapi/v2/query", {"taskId": t["taskId"]})
        except ApiConfigurationError:
            raise SystemExit("停止查询：请处理 API 凭据、权限或余额问题。")
        except (RuntimeError, OSError, urllib.error.URLError, json.JSONDecodeError) as e:
            print(f"{num} query error: " + re.sub(r'https?://\S+', '[URL omitted]', str(e)), flush=True)
            continue
        t["status"] = res.get("status")
        if res.get("status") == "FAILED":
            t["error"] = res.get("errorMessage") or res.get("failedReason")
            t["automatic_failures"] = int(t.get("automatic_failures") or 0) + 1
            n = attempts_of(state, num)
            if given_up(state, num):
                print(f"{num} FAILED {failure_label(state, num)}, give up: {t['error']}",
                      flush=True)
            else:
                print(f"{num} FAILED {failure_label(state, num)}, will retry: {t['error']}",
                      flush=True)
        elif res.get("status") == "SUCCESS":
            urls = [r for r in (res.get("results") or []) if r.get("url")]
            t["results"] = urls
            if not urls:
                t['result_queries'] = int(t.get('result_queries') or 0) + 1
                t['status'] = 'RESULTS_MISSING' if t['result_queries'] >= 3 else 'RESULTS_PENDING'
                t['error'] = 'SUCCESS without result URL; task retained, never regenerate'
                print(f"{num} {t['status']}，保留成功任务，可用 download 恢复查询。", flush=True)
            else:
                t["automatic_failures"] = 0
                print(f"{num} SUCCESS {len(urls)} file(s)", flush=True)
        else:
            print(f"{num} {res.get('status')}", flush=True)
    save_state(state)


def retrieve_result(url, dest):
    """Download over verified HTTPS; refresh stale COS DNS without changing system settings."""
    try:
        deadline=time.monotonic()+180
        with urllib.request.urlopen(url,timeout=30) as response,open(dest,'wb') as output:
            expected=response.headers.get('Content-Length')
            received=0
            while True:
                if time.monotonic()>deadline:raise TimeoutError('download exceeded180 seconds')
                chunk=response.read(1024*1024)
                if not chunk:break
                output.write(chunk);received+=len(chunk)
            if expected is not None and received!=int(expected):
                raise urllib.error.URLError('incomplete download: Content-Length mismatch')
        return
    except (urllib.error.URLError,OSError,http.client.IncompleteRead) as original:
        parsed = urllib.parse.urlsplit(url)
        host = parsed.hostname or ""
        curl = shutil.which("curl.exe") or shutil.which("curl")
        if parsed.scheme != "https" or not host.endswith(".myqcloud.com") or not curl:
            raise
        query = "https://dns.google/resolve?type=A&name=" + urllib.parse.quote(host, safe="")
        with urllib.request.urlopen(query, timeout=30) as response:
            answer = json.load(response)
        addresses = [a.get("data") for a in answer.get("Answer", []) if a.get("type") == 1]
        for address in addresses:
            if not ipaddress.ip_address(address).is_global:
                continue
            result = subprocess.run([
                curl, "--fail", "--location", "--silent", "--show-error",
                "--noproxy", "*", "--resolve", f"{host}:443:{address}",
                "--connect-timeout", "20", "--max-time", "180",
                "--output", dest, url,
            ], capture_output=True)
            if result.returncode == 0:
                return
        raise original


def download(state):
    os.makedirs(OUT, exist_ok=True)
    for num, t in state["tasks"].items():
        if t.get('status') == 'SUCCESS' and t.get('file') and not os.path.isfile(t['file']):
            t.pop('file')
        if t.get("status") != "SUCCESS" or t.get("file"):
            continue
        urls = t.get("results") or []
        if not urls:
            print(f"{num} 没有结果地址", flush=True)
            continue
        url = urls[-1]["url"]
        ext = urls[-1].get("outputType") or "mp4"
        dest = os.path.join(OUT, f"{num}.{ext}")
        ok = False
        for i in range(3):
            try:
                partial = dest + ".part"
                retrieve_result(url, partial)
                if os.path.getsize(partial) == 0:
                    raise OSError("empty download")
                os.replace(partial, dest)
                ok = True
                break
            except (OSError, urllib.error.URLError, http.client.IncompleteRead) as e:
                print(f"{num} download error ({i + 1}/3): " + re.sub(r'https?://\S+', '[URL omitted]', str(e)), flush=True)
                time.sleep(5)
        if not ok:
            t["download_fails"] = int(t.get("download_fails") or 0) + 1
            nfail = t["download_fails"]
            print(f"{num} download failed ({nfail}/3)", flush=True)
            if nfail >= 3:
                t["status"] = "DOWNLOAD_FAILED"
                t["error"] = "download failed 3 rounds; result kept, do not regenerate"
                t.pop("file", None)
            continue
        t["file"] = dest
        t["download_fails"] = 0
        print(f"downloaded {num} -> {dest}", flush=True)
    save_state(state)


def poll_and_download(state):
    """查状态；成功的立刻下载。"""
    refresh(state)
    download(state)


def active_nums(state):
    return [n for n in state["tasks"] if is_active(state, n)]


def fill_slots(nums, state, cfg, limit):
    """在并发上限内提交还没成功、还没放弃的段。"""
    submitted = []
    active = active_nums(state)
    for num in nums:
        if not needs_submit(state, num):
            if given_up(state, num):
                print(f"skip {num}, {failure_label(state, num)}, automatic retries exhausted",
                      flush=True)
            elif state["tasks"].get(num, {}).get("status") == "SUCCESS":
                print(f"skip {num}, already SUCCESS", flush=True)
            else:
                print(f"skip {num}, already {state['tasks'][num].get('status')}",
                      flush=True)
            continue
        while state.get("submit_not_before", 0) > time.time():
            remaining = state["submit_not_before"] - time.time()
            print(f"账户队列冷却，等 {min(30, remaining):.0f} 秒", flush=True)
            time.sleep(min(30, remaining))
            poll_and_download(state)
            active = active_nums(state)
        if given_up(state, num):
            continue
        while len(active) >= limit:
            print(f"在跑 {len(active)} 个，等 30 秒", flush=True)
            time.sleep(30)
            poll_and_download(state)
            active = active_nums(state)
            retry_now = [n for n in nums if needs_submit(state, n) and not given_up(state, n)]
            if retry_now and len(active) < limit:
                break
        if not needs_submit(state, num) or given_up(state, num):
            continue
        if try_submit(num, state, cfg):
            submitted.append(num)
        else:
            time.sleep(5)
        active = active_nums(state)
    return submitted


def wait_until_done(nums, state, cfg, limit):
    while True:
        poll_and_download(state)
        pending = [n for n in nums if needs_submit(state, n) and not given_up(state, n)]
        active = [n for n in nums if is_active(state, n)]
        if pending:
            fill_slots(pending, state, cfg, limit)
            continue
        if not active:
            break
        print(f"在跑 {len(active)} 个，等 30 秒", flush=True)
        time.sleep(30)


def report(state, nums):
    ok = []
    failed = []
    for n in nums:
        t = state["tasks"].get(n) or {}
        if t.get("status") == "SUCCESS" and t.get("file"):
            ok.append(n)
        else:
            failed.append(n)
    print(f"完成 {len(ok)}/{len(nums)}", flush=True)
    if failed:
        print("失败或未完成： " + " ".join(failed), flush=True)
        for n in failed:
            t = state["tasks"].get(n) or {}
            print(f"  {n}: {t.get('status')} {failure_label(state, n)}"
                  f" {t.get('error') or ''}", flush=True)
    return failed


def main():
    if len(sys.argv) < 2:
        raise SystemExit("用法: submit <段号...> | status | download")
    cmd = sys.argv[1]
    from pathlib import Path
    from channels import selected
    if selected(Path(ROOT)) != 'rh':
        import pipa_cloud
        return pipa_cloud.run(sys.modules[__name__], sys.argv[1:])
    state = load_state()
    if cmd == "submit":
        cfg = app_config()
        limit = concurrency()
        nums = list(dict.fromkeys(sys.argv[2:]))
        if not nums or any(n not in CAST for n in nums):
            raise SystemExit('请提供角色映射中有效的三位分段号。')
        for n in nums:
            with open(os.path.join(PROMPT, n + '.txt'), encoding='utf-8') as handle:
                errors, _ = check_prompt(n, handle.read(), CAST)
            if errors:
                raise SystemExit(n + ' 提示词检查失败：' + '；'.join(errors))
            task = state['tasks'].get(n, {})
            if task.get('taskId') and task.get('status') not in ('FAILED', 'INPUT_INVALID'):
                if task.get('input_signature') != input_signature(n):
                    raise SystemExit(n + ' 已有任务与当前输入不符或缺少输入版本记录；先归档复核旧版本。已有云端任务用 status/download 恢复，不重复提交。')
        fill_slots(nums, state, cfg, limit)
        wait_until_done(nums, state, cfg, limit)
        if report(state, nums):
            raise SystemExit(1)
    elif cmd == "status":
        poll_and_download(state)
    elif cmd == "download":
        for t in state["tasks"].values():
            if t.get("status") in ("DOWNLOAD_FAILED", "RESULTS_MISSING"):
                t["status"] = "RESULTS_PENDING" if t.get('status') == 'RESULTS_MISSING' else "SUCCESS"
                t['result_queries'] = 0
                t["download_fails"] = 0
        save_state(state)
        poll_and_download(state)
    else:
        raise SystemExit("用法: submit <段号...> | status | download")


@contextmanager
def writer_lock(path):
    """An OS lock prevents two state writers; a dead process releases it."""
    with open(path, 'a+b') as handle:
        if handle.tell() == 0:
            handle.write(b'0'); handle.flush()
        handle.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise SystemExit('本项目已有状态写入进程；恢复已有进程，不重复启动。')
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == 'nt':
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)

if __name__ == "__main__":
    with writer_lock(STATE + '.lock'):
        main()
