"""Python 语言包。 The Python language pack.

项目约定：顶层包 = 规格里的 module（小写标识符）；入口 <module>/__main__.py 是薄壳；逻辑在 <module>/<name>/ 子包；测试在 tests/test_*.py（unittest，设置里可改成 pytest）；
清单文件 pyproject.toml 由规格生成。工具链是一个可配置的解释器（设置 → 语言 → Python）。
Conventions: the top-level package is the spec's module (a lowercase identifier); <module>/__main__.py is a thin wrapper; logic lives in <module>/<name>/ subpackages; tests are tests/test_*.py
(unittest, switchable to pytest in Settings); the manifest pyproject.toml is generated from the spec. The toolchain is a configurable interpreter (Settings -> Languages -> Python).
"""
import hashlib
import re
import subprocess
import sys
from pathlib import Path

from masa.domain.models import MasaError
from masa.languages.base import ConfigField, LanguagePack, OperationDef, Readiness
from masa.languages.python import checks, failures, oracle, prompts
from masa.languages.python.triage_rules import RISKS, RULES

HERE = Path(__file__).resolve().parent
MIN_VERSION = (3, 10)
_VERSIONS = {}  # 解释器路径 -> (版本元组, 版本字符串)；探测一次就缓存 / interpreter path -> (version tuple, text), probed once
NO_WINDOW = {'creationflags': 0x08000000} if sys.platform == 'win32' else {}


class PythonPack(LanguagePack):
    override_seeds = True

    # ---------- 配置 / configuration ----------
    def interpreter(self):
        """设置里配置的解释器；留空用运行 masa 的这一个。 The interpreter from Settings; empty means the one running masa."""
        configured = str(self.config.get('interpreter') or '').strip()
        return Path(configured) if configured else Path(sys.executable)

    def framework(self):
        return 'pytest' if self.config.get('test_framework') == 'pytest' else 'unittest'

    def config_schema(self):
        return [ConfigField('interpreter', 'Python 解释器', 'path', '', '留空使用运行 masa 的解释器；建议为生成的项目单独建一个干净的虚拟环境（见 scripts/ops/setup_python_pack.py）'),
                ConfigField('test_framework', '测试框架', 'choice', 'unittest', 'unittest 是标准库、无需安装；pytest 需要在上面的解释器里已经安装', choices=('unittest', 'pytest'))]

    # ---------- 布局 / layout ----------
    def entrypoint(self, spec=None):
        template = self.layout['entrypoint']
        return template.replace('{module}', spec['module']) if isinstance(spec, dict) and isinstance(spec.get('module'), str) else template

    def entrypoint_schema(self):
        return {'type': 'string', 'pattern': r'^[a-z][a-z0-9_]*/__main__\.py$'}

    def is_test(self, path):
        return checks.is_test_path(path)

    @property
    def operations(self):
        ops = dict(super().operations)
        if self.framework() == 'pytest' and 'test' in ops:
            old = ops['test']
            ops['test'] = OperationDef(kind='test', id=old.id, label='pytest', exe=old.exe,
                                       args=('-E', '-s', '-B', '-X', 'utf8', '-m', 'pytest', '-q', '-p', 'no:cacheprovider', '--tb=short', 'tests'))
        return ops

    # ---------- 工具链 / toolchain ----------
    def _version(self, interpreter):
        key = str(interpreter)
        if key not in _VERSIONS:
            done = subprocess.run([key, '-c', 'import sys; print(sys.version_info[0], sys.version_info[1], sys.version_info[2], sys.version.split()[0])'],
                                  capture_output=True, text=True, timeout=20, **NO_WINDOW)
            parts = done.stdout.split()
            _VERSIONS[key] = (tuple(int(p) for p in parts[:3]), parts[3]) if done.returncode == 0 and len(parts) >= 4 else ((0, 0, 0), '')
        return _VERSIONS[key]

    def probe(self, executor=None):
        interpreter = self.interpreter()
        if not interpreter.is_file():
            return Readiness(False, (f'找不到解释器 {interpreter}',))
        try:
            version, text = self._version(interpreter)
        except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
            return Readiness(False, (f'无法运行解释器：{exc}',))
        problems = []
        if version < MIN_VERSION:
            problems.append(f'Python {text or "?"} 版本过低，需要 {MIN_VERSION[0]}.{MIN_VERSION[1]} 或更高')
        if self.framework() == 'pytest':
            done = subprocess.run([str(interpreter), '-c', 'import pytest'], capture_output=True, timeout=30, **NO_WINDOW)
            if done.returncode != 0:
                problems.append('所选解释器里没有安装 pytest（pip install pytest）')
        return Readiness(not problems, tuple(problems) or (f'Python {text}',))

    def fingerprint(self, executor):
        interpreter = self.interpreter()
        if not interpreter.is_file():
            raise MasaError(f'Python interpreter missing: {interpreter}')
        version, text = self._version(interpreter)
        out = {'python_executable': {'path': str(interpreter), 'sha256': hashlib.sha256(interpreter.read_bytes()).hexdigest()}, 'python_version': text}
        if self.framework() == 'pytest':
            out['test_framework'] = 'pytest'
        return out

    def executables(self, executor):
        return {'toolchain': self.interpreter()}

    def env_policy(self, executor):
        return {'allow': ['SystemRoot', 'WINDIR', 'TEMP', 'TMP', 'TMPDIR', 'HOME', 'USERPROFILE', 'LOCALAPPDATA'],
                'set': {'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONNOUSERSITE': '1'},
                'path_prepend': [str(self.interpreter().parent)]}

    def runner_ops(self, executor):
        table = super().runner_ops(executor)
        replace = {'{lint}': str(HERE / 'lint.py'), '{launcher}': str(HERE / 'launcher.py')}
        for entry in table['operations'].values():
            entry['args'] = [replace.get(a, a) for a in entry.get('args', [])]
        return table

    # ---------- 种子与规格 / seed and spec ----------
    def allow_empty(self, path):
        return str(path).rsplit('/', 1)[-1] == '__init__.py'

    def seed_files(self, spec):
        """pyproject.toml 与所有 __init__.py（空文件）由规格确定，不让模型生成。 pyproject.toml and every __init__.py (empty) are determined by the spec, never generated by a model."""
        out = {'pyproject.toml': f'[project]\nname = "{spec["module"]}"\nversion = "0.1.0"\nrequires-python = ">=3.10"\n'}
        out.update({f['path']: '' for f in spec['files'] if f['path'].endswith('__init__.py')})
        return out

    def planning_seed(self):
        return {'pyproject.toml': '[project]\nname = "planning"\nversion = "0.1.0"\nrequires-python = ">=3.10"\n'}

    def validate_spec(self, spec):
        module = spec['module']
        names = {f['path'] for f in spec['files']}
        for path in names:
            if self.is_code(path) and not (path.startswith(module + '/') or path.startswith('tests/')):
                raise MasaError(f'Python files must live under {module}/ or tests/: {path}')
        if f'{module}/__init__.py' not in names:
            raise MasaError(f'plan requires {module}/__init__.py')
        for path in sorted(names):
            if path.startswith(module + '/') and self.is_code(path):
                directory = path.rsplit('/', 1)[0]
                while directory.startswith(module):
                    if f'{directory}/__init__.py' not in names:
                        raise MasaError(f'every package directory needs an __init__.py: {directory}/')
                    if '/' not in directory:
                        break
                    directory = directory.rsplit('/', 1)[0]

    def validate_bundle(self, files, spec):
        if files['pyproject.toml'].strip() != self.seed_files(spec)['pyproject.toml'].strip():
            raise MasaError('pyproject.toml must be exactly the approved project metadata')
        planned = [f['path'] for f in spec['files']]
        problems = checks.unknown_import_messages(files, planned, spec['module'], planned, self._extra_imports())
        problems.update(checks.name_import_problems(files, planned, spec['module']))
        if problems:
            path, message = next(iter(problems.items()))
            raise MasaError(f'{path} {message}')

    def _extra_imports(self):
        return {'pytest'} if self.framework() == 'pytest' else set()

    # ---------- 确定性检查 / deterministic checks ----------
    def normalize(self, executor, files, paths, imports=True):
        out, fixed = dict(files), {}
        if not imports:
            return out, fixed
        for path in paths:
            text = files.get(path)
            if path.endswith('.py') and isinstance(text, str) and not path.endswith('__init__.py'):
                source, changes = checks.fix_imports(text)
                if changes:
                    out[path] = source
                    fixed[path] = [f'{action} {name}' for action, name in changes]
        return out, fixed

    def static_messages(self, files, paths, spec=None):
        unknown = checks.unknown_import_messages(files, paths, spec['module'], [i['path'] for i in spec['files']], self._extra_imports()) if spec else {}
        merged = {**checks.test_problem_messages(files, paths), **self.entry_problems(files, paths), **unknown}
        if spec:
            for path, message in checks.name_import_problems(files, paths, spec['module']).items():
                merged[path] = (merged[path] + ' ' if path in merged else '') + message
        return merged

    def parse_errors(self, executor, files, paths):
        return checks.parse_errors(files, paths)

    def entry_problems(self, files, paths=None):
        candidates = [p for p in (paths if paths is not None else files) if re.fullmatch(r'[a-z][a-z0-9_]*/__main__\.py', p)]
        out = {}
        for entry in candidates:
            out.update(checks.entry_problem_messages(files, [entry], entry))
        return out

    def declares(self, source, name):
        return checks.declares(source, name)

    def samples(self):
        return {'syntax_error': ('a.py', 'def f(:' + chr(10))}

    def package_of(self, path, spec=None):
        """源码按目录分包；测试文件 tests/test_<name>.py 跟着源码里叫 <name> 的文件（或目录）所在的包，找不到就归入入口所在的包。 Sources are grouped by directory; a test file follows the package of the source file or directory named <name>, else the entry's package."""
        text = str(path)
        if not checks.is_test_path(text):
            return text.rsplit('/', 1)[0] if '/' in text else ''
        stem = text.rsplit('/', 1)[-1]
        stem = stem[5:-3] if stem.startswith('test_') and stem.endswith('.py') else stem
        files = [f['path'] for f in (spec or {}).get('files', [])]
        for candidate in files:
            if candidate.endswith('.py') and not checks.is_test_path(candidate) and candidate.rsplit('/', 1)[-1][:-3] == stem and not candidate.endswith('__init__.py'):
                return candidate.rsplit('/', 1)[0] if '/' in candidate else ''
        for candidate in files:
            if not checks.is_test_path(candidate) and candidate.endswith('.py') and stem in candidate.rsplit('/', 1)[0].split('/')[1:]:
                return candidate.rsplit('/', 1)[0]
        return (spec or {}).get('module', '')

    def worker_notes(self):
        return ('Python 3 project (standard library only); pyproject.toml and every __init__.py (empty) are fixed by the harness. <module>/__main__.py is a thin wrapper: import sys; '
                'from <module>.<package>.<file> import run; sys.exit(run(sys.argv[1:], sys.stdin, sys.stdout, sys.stderr)) with the FULL dotted path of the FILE that defines run; never read stdin there. '
                'Logic lives in <module>/<package>/<file>.py; tests are tests/test_<name>.py using unittest (assertEqual(actual, expected), ACTUAL first), never skipped, never assert True. '
                'Import project modules with absolute imports (from <module>.<package>.<file> import name); call run(args, io.StringIO(...), out, err) directly in tests. '
                'If a test starts the program as a process compare decoded text (text=True), never raw bytes (Windows writes CRLF).')

    def outline(self, source):
        return checks.outline(source)

    # ---------- 失败解析 / failure parsing ----------
    def parse_failures(self, checks_results):
        return failures.parse_failures(checks_results, self.op_id('test'), self.op_id('static'))

    def needs_test_revision(self, evidence, checks_results=()):
        return failures.needs_test_revision(evidence, checks_results, self.op_id('test'), self.op_id('static'))

    def assertion_signature(self, checks_results):
        return failures.assertion_signature(checks_results, self.op_id('test'), self.op_id('static'))

    def failure_signature(self, checks_results):
        return failures.failure_signature(checks_results, self.op_id('test'), self.op_id('static'))

    def test_advice(self):
        return failures.TEST_ADVICE

    def assertion_of(self, message):
        return failures.assertion_of(message)

    def test_function_source(self, files, path, line, limit=1500):
        return checks.test_function_source(files, path, line, limit)

    def failure_markers(self):
        return r'(?i)(?:Error\b|\bFAIL\b|Traceback|File ".+\.py"|\bassert|exit code|\.py:\d+)'

    def location_regex(self):
        return r'([\w./\\:-]+\.py)(?:"?, line |:)(\d+)(?::\d+)?'

    def relative_path(self, path):
        return failures.relative(path) or path

    # ---------- 提示词、规则、判官 / prompts, rules, oracle ----------
    def project_prompt(self, context, default_instruction):
        return prompts.project_roles_prompt(context, default_instruction)

    def transport_hint(self, context):
        return prompts.transport_hint(context)

    def triage_rules(self):
        return RULES, RISKS

    def judge(self, files, task, executor=None):
        return oracle.judge(self, self.interpreter(), files, task)


def create(manifest, root):
    return PythonPack(manifest=manifest, root=root)
