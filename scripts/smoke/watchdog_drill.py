"""看门狗演练：在临时状态目录里起 `masa watch`，等服务可达，强杀服务进程，确认看门狗把它拉起来并写出 崩溃/重启 事件；全程不碰模型、不碰你的 .masa。
Watchdog drill: start `masa watch` in a temporary state directory, wait until the service answers, kill it hard, and check that the watchdog brings it back and writes the crash and restart events. No model, your .masa is untouched.

用法 / usage:  python scripts/smoke/watchdog_drill.py [--kills 2]
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

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
from masa.application.ops import EventSink  # noqa: E402


def api(port, token, path):
    request = urllib.request.Request(f'http://127.0.0.1:{port}{path}', headers={'X-MASA-Ops-Token': token})
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.loads(response.read())


def wait_status(port, token, predicate, seconds=60):
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            state = api(port, token, '/api/ops/status')
            if predicate(state):
                return state
        except OSError:
            pass
        time.sleep(0.5)
    raise SystemExit('timed out waiting for the service')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--kills', type=int, default=2)
    ap.add_argument('--port', type=int, default=18765)
    args = ap.parse_args()
    with tempfile.TemporaryDirectory() as temp:
        state = Path(temp)
        sink = EventSink(state)
        token = sink.config()['token']
        watch = subprocess.Popen([sys.executable, '-m', 'masa', '--state-dir', str(state), 'watch', '--port', str(args.port)], cwd=ROOT, env={**os.environ, 'PYTHONPATH': str(ROOT / 'src')})
        try:
            current = wait_status(args.port, token, lambda s: True)
            for n in range(1, args.kills + 1):
                print(f"kill #{n}: pid {current['pid']}", flush=True)
                subprocess.run(['taskkill', '/F', '/PID', str(current['pid'])], capture_output=True) if os.name == 'nt' else os.kill(current['pid'], 9)
                current = wait_status(args.port, token, lambda s, pid=current['pid'], n=n: s['pid'] != pid and s['restarts'] == n)
                print(f"  back as pid {current['pid']} after {current['restarts']} restart(s)", flush=True)
            kinds = [e['kind'] for e in sink.events()]
            print('events:', ', '.join(kinds))
            assert kinds.count('service_crashed') == args.kills and kinds.count('service_restarted') == args.kills, kinds
            assert api(args.port, token, '/api/ops/events?since=0')['events'], 'the events feed is empty'
            print('OK: the watchdog restarted the service after every kill and wrote crash and restart events')
        finally:
            watch.terminate()
            try:
                watch.wait(10)
            except subprocess.TimeoutExpired:
                watch.kill()
            # 强杀看门狗时服务可能残留：按端口清理 / after a hard kill of the watchdog the service may remain: clean it up by port
            subprocess.run(['powershell', '-NoProfile', '-Command', f'Get-NetTCPConnection -LocalPort {args.port} -State Listen -ErrorAction SilentlyContinue | ForEach-Object {{ Stop-Process -Id $_.OwningProcess -Force }}'],
                           capture_output=True) if os.name == 'nt' else None


if __name__ == '__main__':
    main()
