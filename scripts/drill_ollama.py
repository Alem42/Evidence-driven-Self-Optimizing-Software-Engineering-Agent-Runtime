"""真实 Ollama 崩溃演练（手动，会占用 GPU，并且会杀掉再拉起 Ollama 服务）。
Real-Ollama crash drill (manual: uses the GPU and kills then restarts the Ollama server).

R1 客户端被杀：模型请求进行到一半，我们的进程被硬杀。观察 Ollama 是否继续生成（浪费 GPU）、下一次请求是否被排队阻塞。
R2 服务端被杀：模型请求进行到一半，ollama serve 被杀。观察我们这边的错误类型、账本里调用的状态，以及重启后能否恢复。
R1 client killed: our process dies mid-request. Does Ollama keep generating, and is the next request blocked behind it?
R2 server killed: ollama serve dies mid-request. What do we record, and does everything recover after a restart?

用法 / usage: python scripts/drill_ollama.py [--model gemma4:12b] [--only r1|r2]
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = 'http://127.0.0.1:11434'
OLLAMA = Path(os.environ.get('LOCALAPPDATA', '')) / 'Programs' / 'Ollama' / 'ollama.exe'


def http(path, body=None, timeout=30):
    request = urllib.request.Request(BASE + path, data=None if body is None else json.dumps(body).encode(),
                                     headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read())


def healthy():
    try:
        http('/api/version', timeout=3)
        return True
    except Exception:
        return False


def gpu_util():
    out = subprocess.run(['nvidia-smi', '--query-gpu=utilization.gpu', '--format=csv,noheader,nounits'], capture_output=True, text=True)
    try:
        return int(out.stdout.strip().splitlines()[0])
    except (ValueError, IndexError):
        return -1


def serve_pids():
    out = subprocess.run(['powershell', '-NoProfile', '-Command',
                          "Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'ollama.exe' -and $_.CommandLine -match 'serve' } | ForEach-Object { $_.ProcessId }"],
                         capture_output=True, text=True)
    return [int(x) for x in out.stdout.split()]


def restart_server():
    """被杀后拉起 ollama serve；托盘程序可能会自己拉起，所以先等一会儿。 Wait for the tray app, then start serve ourselves."""
    for _ in range(15):
        if healthy():
            return 'restarted by the tray app'
        time.sleep(1)
    subprocess.Popen([str(OLLAMA), 'serve'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     creationflags=getattr(subprocess, 'DETACHED_PROCESS', 0) | getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0))
    for _ in range(30):
        if healthy():
            return 'restarted by this script'
        time.sleep(1)
    return 'FAILED to restart'


CHILD = '''
import json, sys, time
from pathlib import Path
sys.path.insert(0, r"{root}/src"); sys.path.insert(0, r"{root}/tests")
from masa.infrastructure.llm import ChatProvider
from masa.infrastructure.store import Store
from masa.application.planning import ProjectPlanning
from masa.domain.models import MasaError
from test_runtime import FakeExecutor
state = Path(sys.argv[1]); model = sys.argv[2]
cfg = {{"base_url": "http://127.0.0.1:11434", "model": model, "model_type": "local", "protocol": "ollama", "context_limit": 8192,
       "max_output_tokens": 2048, "timeout_seconds": 300, "thinking": "disabled"}}
store = Store(state)
(state / "started").write_text("1")
t = time.time()
try:
    ProjectPlanning(store, FakeExecutor()).generate(ChatProvider(cfg, ""), "Write a Go CLI that sums integers; explain the design thoroughly.")
    print("RESULT " + json.dumps({{"ok": True, "secs": round(time.time() - t, 1)}}))
except Exception as exc:
    print("RESULT " + json.dumps({{"ok": False, "type": type(exc).__name__, "message": str(exc)[:300], "secs": round(time.time() - t, 1)}}))
store.close()
'''


def start_child(state, model):
    code = CHILD.format(root=str(ROOT).replace('\\', '/'))
    process = subprocess.Popen([sys.executable, '-c', code, str(state), model], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    deadline = time.time() + 30
    while not (state / 'started').exists() and time.time() < deadline:
        time.sleep(0.1)
    return process


def ledger(state):
    from masa.infrastructure.store import Store
    store = Store(state)
    try:
        rows = [dict(r) for r in store.db.execute('SELECT purpose, invocation_id, attempt_no, status FROM role_invocations')]
        events = [(e['type'], json.dumps({k: v for k, v in e['payload'].items() if k in ('step_id', 'usage', 'contract_diagnostic')}, ensure_ascii=False))
                  for r in store.all_runs() for e in store.events(r['id']) if e['type'].startswith('model_')]
        runs = [(r['status'], (r['data'].get('project_plan') or {}).get('status'), (r['data'].get('project_plan') or {}).get('error')) for r in store.all_runs()]
        return {'role_invocations': rows, 'model_events': events, 'runs': runs}
    finally:
        store.close()


def tiny(model):
    started = time.time()
    http('/api/generate', {'model': model, 'prompt': 'Reply OK', 'stream': False, 'options': {'num_predict': 4}}, timeout=300)
    return time.time() - started


LONG_CHILD = '''
import json, sys, time, urllib.request
body = {{"model": sys.argv[1], "stream": False, "think": False, "options": {{"num_ctx": 4096, "num_predict": 3000}},
        "messages": [{{"role": "user", "content": "Count from 1 to 2500, separated by single spaces, and output nothing else."}}]}}
open(sys.argv[2], "w").write("1")
req = urllib.request.Request("http://127.0.0.1:11434/api/chat", data=json.dumps(body).encode(), headers={{"Content-Type": "application/json"}})
urllib.request.urlopen(req, timeout=600).read()
'''


def drill_r1(model):
    print('\n=== R1 客户端被杀：一个很长的生成请求进行到一半，我们的进程被硬杀 ===')
    http('/api/generate', {'model': model, 'prompt': 'hi', 'stream': False, 'options': {'num_predict': 1}}, timeout=300)
    baseline = min(tiny(model) for _ in range(3))
    print(f'  baseline tiny-request latency (warm): {baseline:.2f}s')
    with tempfile.TemporaryDirectory() as temp:
        marker = Path(temp) / 'started'
        child = subprocess.Popen([sys.executable, '-c', LONG_CHILD.format(), model, str(marker)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        while not marker.exists():
            time.sleep(0.05)
        t0 = time.time()
        time.sleep(3)
        util = gpu_util()
        child.kill()
        child.wait()
        print(f'  killed our process {time.time() - t0:.1f}s after the request started (GPU util at that moment: {util}%)')
        # 反复发小请求：如果 Ollama 在客户端断开后继续生成，它们会排队等到生成结束。
        # Probe repeatedly: if Ollama keeps generating after the client died, the probes queue until it finishes.
        waits = []
        probe_start = time.time()
        while time.time() - probe_start < 120:
            started = time.time()
            tiny(model)
            waits.append(round(time.time() - started, 1))
            if waits[-1] < baseline * 4 + 1:
                break
        print(f'  probe latencies after the kill: {waits}  (first free after {time.time() - probe_start:.1f}s)')
        blocked = waits[0] > baseline * 4 + 1
        print('  verdict:', f'Ollama KEEPS PROCESSING the abandoned request: the next call waited {waits[0]}s (up to ~{round(time.time() - t0)}s total)'
              if blocked else 'Ollama cancelled the abandoned request promptly (next call was not delayed)')


def drill_r2(model):
    print('\n=== R2 服务端被杀：ollama serve 在请求中途被杀 ===')
    http('/api/generate', {'model': model, 'prompt': 'hi', 'stream': False, 'options': {'num_predict': 1}}, timeout=300)
    with tempfile.TemporaryDirectory() as temp:
        state = Path(temp)
        child = start_child(state, model)
        time.sleep(4)
        pids = serve_pids()
        subprocess.run(['taskkill', '/F', '/PID', str(pids[0])] if pids else ['cmd', '/c', 'echo no serve pid'], capture_output=True)
        print(f'  killed ollama serve pid(s) {pids} after 4s')
        out, err = child.communicate(timeout=300)
        line = next((l for l in out.splitlines() if l.startswith('RESULT ')), None)
        print('  our side:', line or ('no RESULT; stderr=' + err[-300:]))
        print('  ledger:', json.dumps(ledger(state), ensure_ascii=False)[:700])
        print('  server down? healthy =', healthy())
        # 杀掉 ollama serve 会留下占显存的孤儿 llama-server（真实发现）：演练结束前必须清掉。
        # Killing ollama serve leaves VRAM-holding llama-server orphans (a real finding): always clean them before finishing.
        sys.path.insert(0, str(ROOT / 'src'))
        from masa.infrastructure import orphans
        print('  orphaned model runners left behind:', [(o['pid'], o['memory_mb']) for o in orphans.orphans()])
        print('  cleaned:', orphans.clean_orphans())
        print('  restart:', restart_server())
        reply = http('/api/generate', {'model': model, 'prompt': 'Reply OK', 'stream': False, 'options': {'num_predict': 4}}, timeout=300)
        print('  after restart, a request works:', bool(reply.get('response') is not None))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--model', default='gemma4:12b')
    ap.add_argument('--only', choices=['r1', 'r2'])
    args = ap.parse_args()
    if not healthy():
        raise SystemExit('Ollama is not running')
    try:
        if args.only != 'r2':
            drill_r1(args.model)
        if args.only != 'r1':
            drill_r2(args.model)
    finally:
        sys.path.insert(0, str(ROOT / 'src'))
        from masa.infrastructure import orphans
        leftover = orphans.clean_orphans()
        if leftover['killed']:
            print('
WARNING: cleaned leftover orphan runners:', leftover)
        if healthy():
            try:
                http('/api/generate', {'model': args.model, 'keep_alive': 0, 'stream': False}, timeout=60)
            except Exception:
                pass
        print('\nmodel unloaded; healthy =', healthy())


if __name__ == '__main__':
    main()
