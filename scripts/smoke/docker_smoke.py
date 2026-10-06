"""容器内的冒烟：真实的 Linux runner 能否运行 go test / vet / gofmt，超时后是否不留孤儿进程。
Smoke test inside the container: does the real Linux runner run go test / vet / gofmt, and does a timeout leave no orphan process?

用法 / usage:  docker run --rm masa python /app/scripts/smoke/docker_smoke.py     （脚本由 docker cp 或挂载放入；见 docs/guides/DEPLOY_DOCKER.md）
"""
import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

from masa.infrastructure.runner import Runner

RUNNER = Path(os.environ.get('MASA_RUNNER', '/app/bin/masa-runner'))
GO = Path(os.environ.get('MASA_GO', '/usr/local/go/bin/go'))


def request(operation, timeout=60000):
    return {'protocol_version': 1, 'request_id': uuid.uuid4().hex, 'snapshot_id': 'smoke', 'operation': operation, 'timeout_ms': timeout, 'max_output_bytes': 65536}


GOOD_TEST = 'if Add(1, 2) != 3 {' + chr(10) + chr(9) + chr(9) + 't.Fatal("bad")' + chr(10) + chr(9) + '}'


def project(root, test_body=GOOD_TEST, extra=None):
    (root / 'cmd/app').mkdir(parents=True)
    (root / 'internal/calc').mkdir(parents=True)
    (root / 'go.mod').write_text('module example.com/smoke\n\ngo 1.27.0\n')
    (root / 'cmd/app/main.go').write_text('package main\n\nimport "fmt"\n\nfunc main() { fmt.Println("hi") }\n')
    (root / 'internal/calc/calc.go').write_text('package calc\n\nfunc Add(a, b int) int { return a + b }\n')
    (root / 'internal/calc/calc_test.go').write_text('package calc\n\nimport "testing"\n\nfunc TestAdd(t *testing.T) {\n\t' + test_body + '\n}\n')
    for name, text in (extra or {}).items():
        (root / name).write_text(text)


def count(pattern):
    """读 /proc 统计命令行里含 pattern 的进程（python:slim 里没有 ps 命令）。 Count processes by reading /proc (python:slim has no ps)."""
    found = 0
    for entry in Path('/proc').iterdir():
        if entry.name.isdigit() and entry.name != str(os.getpid()):
            try:
                cmdline = (entry / 'cmdline').read_bytes().replace(bytes(1), b' ').decode('utf-8', 'replace')
            except OSError:
                continue
            found += pattern in cmdline
    return found


def main():
    runner = Runner(RUNNER, GO)
    ok = True
    with tempfile.TemporaryDirectory() as temp:
        work = Path(temp) / 'repo'
        project(work)
        for op in ('go_test', 'go_vet', 'go_fmt_check'):
            result = runner.execute(request(op), work, lambda: False)
            passed = (result['status'], result['exit_code']) == ('completed', 0)
            ok &= passed
            print(f'{"PASS" if passed else "FAIL"} {op}: {result["status"]} exit={result["exit_code"]}')
        shutil.rmtree(work)
        project(work, test_body='t.Fatal("deliberate failure")')
        result = runner.execute(request('go_test'), work, lambda: False)
        passed = result['status'] == 'completed' and result['exit_code'] != 0
        ok &= passed
        print(f'{"PASS" if passed else "FAIL"} failing test is reported as completed/non-zero: exit={result["exit_code"]}')
        # 超时：被测程序死循环。runner 必须报告 timeout，并且之后不能留下孤儿的测试进程。
        shutil.rmtree(work)
        project(work, test_body='for { }')
        started = time.monotonic()
        result = runner.execute(request('go_test', timeout=6000), work, lambda: False)
        time.sleep(1.5)
        orphans = count('calc.test')
        passed = result['status'] == 'timeout' and orphans == 0
        ok &= passed
        print(f'{"PASS" if passed else "FAIL"} timeout: status={result["status"]} after {time.monotonic() - started:.1f}s, orphan test processes={orphans}')
        # 工作区里的符号链接必须被拒绝（它可以指向工作区之外）。
        shutil.rmtree(work)
        project(work)
        os.symlink('/etc/passwd', work / 'internal/calc/leak.txt')
        result = runner.execute(request('go_test'), work, lambda: False)
        passed = result['status'] == 'rejected'
        ok &= passed
        print(f'{"PASS" if passed else "FAIL"} symlink in the workspace is rejected: status={result["status"]}')
    print('ALL PASS' if ok else 'FAILED')
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
