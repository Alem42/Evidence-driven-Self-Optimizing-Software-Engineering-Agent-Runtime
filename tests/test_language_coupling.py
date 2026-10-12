"""语言耦合护栏：核心代码里“具体语言的字面量”只许减少，不许增加。
Language-coupling guard: literals that name a specific language in the core may only go down, never up.

背景 / Why：把 Go 专属内容抽成“语言包”（docs/design/LANGUAGE_PACKS.md）是分阶段做的；这个测试既是进度条，也防止以后又有人把 `go_test`、`.go`、`gofmt` 写回核心。
The Go-specific code is being moved into language packs in stages; this test is both the progress bar and the guard that stops such literals from creeping back into the core.

基线文件 / baseline: tests/golden/language_coupling.json = {相对路径: {模式: 命中行数}}。每个阶段结束后用 `python tests/golden/capture_coupling.py` 重新生成（只会变小）。
Regenerate it with `python tests/golden/capture_coupling.py` after a stage (it can only shrink).
"""
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / 'src' / 'masa'
BASELINE = Path(__file__).resolve().parent / 'golden' / 'language_coupling.json'
# 语言包自己可以随意写语言名；评测任务库的参考实现不算。 Language packs may name their language freely.
EXEMPT_DIRS = ('languages',)
PATTERNS = {
    'op_ids': r'go_test|go_vet|go_fmt_check|go_index',
    'gofmt': r'gofmt|goimports',
    'go_mod': r'go\.mod|go\.sum',
    'go_file': r'\.go\b|_test\.go',
    'go_template': r'go-cli|cmd/app/main\.go',
    'go_env': r'GOROOT|GOCACHE|GOFLAGS|GOPATH|GOMODCACHE|GOTOOLCHAIN|go_executable',
    'go_word': r'\bGo\b',
}
COMPILED = {name: re.compile(pattern) for name, pattern in PATTERNS.items()}


def scan():
    """{相对路径: {模式: 命中行数}}。 {relative path: {pattern: matching lines}}."""
    found = {}
    for path in sorted(SRC.rglob('*')):
        if path.suffix not in ('.py', '.md', '.json') or '__pycache__' in path.parts:
            continue
        relative = path.relative_to(SRC)
        if relative.parts[0] in EXEMPT_DIRS:
            continue
        counts = {}
        for line in path.read_text(encoding='utf-8', errors='replace').splitlines():
            for name, regex in COMPILED.items():
                if regex.search(line):
                    counts[name] = counts.get(name, 0) + 1
        if counts:
            found[relative.as_posix()] = counts
    return found


class CouplingTests(unittest.TestCase):
    def test_core_files_do_not_gain_language_literals(self):
        baseline = json.loads(BASELINE.read_text(encoding='utf-8'))
        problems = []
        for path, counts in scan().items():
            allowed = baseline.get(path, {})
            for name, count in counts.items():
                if count > allowed.get(name, 0):
                    problems.append(f'{path}: {name} {count} > baseline {allowed.get(name, 0)}')
        self.assertEqual(problems, [], 'language literals crept back into the core (see docs/design/LANGUAGE_PACKS.md); '
                                       'move them into a language pack or lower the baseline legitimately')

    def test_the_baseline_is_not_stale(self):
        # 基线必须收紧：核心里的命中变少了，就要重新生成基线，否则进度被“白白放宽”。 When hits go down the baseline must be regenerated, or the slack lets them creep back.
        baseline = json.loads(BASELINE.read_text(encoding='utf-8'))
        current = scan()
        slack = []
        for path, counts in baseline.items():
            for name, count in counts.items():
                now = current.get(path, {}).get(name, 0)
                if now < count:
                    slack.append(f'{path}: {name} baseline {count}, now {now}')
        self.assertEqual(slack, [], 'run `python tests/golden/capture_coupling.py` to tighten the baseline')


if __name__ == '__main__':
    unittest.main()
