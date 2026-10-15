"""Python 的评测判官：把生成的项目放进临时目录，逐用例运行，和参考实现的输出比较。 The Python benchmark oracle: put the generated project in a temp directory, run every case and compare with the reference output.

和 Go 判官（bench/oracle.py）同一套归一化与判定；“构建”对应整个项目逐文件编译一遍。 Same normalisation and verdict as the Go oracle; "build" means compiling every file once.
"""
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

BUILD_SECONDS = 60
RUN_SECONDS = 20
OUTPUT_LIMIT = 200_000
_NO_WINDOW = {'creationflags': 0x08000000} if os.name == 'nt' else {}


def normalize(text):
    """逐行去掉行尾空白并去掉末尾空行：宽容格式噪声，不宽容内容。 Strip trailing whitespace per line and trailing blank lines."""
    lines = [line.rstrip() for line in text.replace('\r\n', '\n').split('\n')]
    while lines and not lines[-1]:
        lines.pop()
    return '\n'.join(lines)


def judge(pack, interpreter, files, task):
    """返回 {'passed','built','cases'}，格式与 Go 判官一致。 Same result shape as the Go oracle."""
    here = Path(__file__).resolve().parent
    flags = ['-E', '-s', '-B', '-X', 'utf8']
    work = Path(tempfile.mkdtemp(prefix='masa-bench-py-'))
    env = {k: v for k, v in os.environ.items() if k.upper() in ('SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP')}
    try:
        for rel, content in files.items():
            target = work / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding='utf-8', newline='\n')
        try:
            built = subprocess.run([str(interpreter), *flags, str(here / 'lint.py'), '.'], cwd=work, env=env, capture_output=True,
                                   timeout=BUILD_SECONDS, stdin=subprocess.DEVNULL, **_NO_WINDOW)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {'passed': False, 'built': False, 'cases': [], 'error': f'build did not finish: {type(exc).__name__}'}
        if built.returncode != 0:
            return {'passed': False, 'built': False, 'cases': [], 'error': (built.stdout + built.stderr).decode('utf-8', 'replace')[:300]}
        results = []
        for index, (args, stdin) in enumerate(task.cases):
            want_out, want_code = task.ref(args, stdin)
            try:
                done = subprocess.run([str(interpreter), *flags, str(here / 'launcher.py'), *args], cwd=work, input=stdin.encode('utf-8'),
                                      capture_output=True, timeout=RUN_SECONDS, env=env, **_NO_WINDOW)
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
