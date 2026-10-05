"""独立判官：把系统生成的项目真的构建并运行，用任务的参考实现逐用例比对。
Independent oracle: really build and run the generated project and compare every case with the task's reference implementation.

它与被测系统无关：不读系统的测试、不信系统的 Gate。系统说“通过”而判官说“不对”，就是“假通过”——这是对“证据驱动”最直接的检验。
It is independent of the system under test: it ignores the system's tests and Gate. A Gate pass that the oracle rejects is a "false pass",
the most direct test of the "evidence-driven" claim.
"""
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from masa.infrastructure.proc import NO_WINDOW

BUILD_SECONDS = 90
RUN_SECONDS = 10
OUTPUT_LIMIT = 200_000


def normalize(text):
    """逐行去掉行尾空白并去掉末尾空行：宽容格式噪声，不宽容内容。 Strip trailing whitespace per line and trailing blank lines: tolerant of noise, not of content."""
    lines = [line.rstrip() for line in text.replace('\r\n', '\n').split('\n')]
    while lines and not lines[-1]:
        lines.pop()
    return '\n'.join(lines)


def _env(go):
    allowed = {'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP', 'USERPROFILE', 'LOCALAPPDATA', 'GOCACHE', 'GOMODCACHE', 'GOPATH'}
    env = {k: v for k, v in os.environ.items() if k.upper() in allowed}
    env['GOROOT'] = str(Path(go).parent.parent)
    env['GOFLAGS'] = '-mod=mod'
    env['GOTOOLCHAIN'] = 'local'
    return env


def judge(files, task, go_executable):
    """返回 {'passed': bool, 'built': bool, 'cases': [{index, ok, reason}]}。files 是 {相对路径: 内容}。
    Return {'passed', 'built', 'cases': [...]}; files maps relative paths to content."""
    go = Path(go_executable)
    work = Path(tempfile.mkdtemp(prefix='masa-bench-'))
    try:
        for rel, content in files.items():
            target = work / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding='utf-8', newline='\n')
        binary = work / ('app.exe' if os.name == 'nt' else 'app')
        try:
            built = subprocess.run([str(go), 'build', '-o', str(binary), './cmd/app'], cwd=work, env=_env(go), capture_output=True,
                                   timeout=BUILD_SECONDS, stdin=subprocess.DEVNULL, **NO_WINDOW)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {'passed': False, 'built': False, 'cases': [], 'error': f'build did not finish: {type(exc).__name__}'}
        if built.returncode != 0:
            return {'passed': False, 'built': False, 'cases': [], 'error': built.stderr.decode('utf-8', 'replace')[:300]}
        results = []
        for index, (args, stdin) in enumerate(task.cases):
            want_out, want_code = task.ref(args, stdin)
            try:
                done = subprocess.run([str(binary), *args], cwd=work, input=stdin.encode('utf-8'), capture_output=True, timeout=RUN_SECONDS,
                                      env={k: v for k, v in _env(go).items() if k in ('SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP')}, **NO_WINDOW)
            except subprocess.TimeoutExpired:
                results.append({'index': index, 'ok': False, 'reason': 'timeout'})
                continue
            got_out = done.stdout[:OUTPUT_LIMIT].decode('utf-8', 'replace')
            if done.returncode != want_code:
                results.append({'index': index, 'ok': False, 'reason': f'exit code {done.returncode}, want {want_code}'})
            elif normalize(got_out) != normalize(want_out):
                results.append({'index': index, 'ok': False, 'reason': f'stdout {normalize(got_out)[:80]!r}, want {normalize(want_out)[:80]!r}'})
            else:
                results.append({'index': index, 'ok': True, 'reason': ''})
        return {'passed': all(r['ok'] for r in results), 'built': True, 'cases': results}
    finally:
        shutil.rmtree(work, ignore_errors=True)
