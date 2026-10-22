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
from masa.languages.python import checks, env, failures, oracle, prompts
from masa.languages.python.triage_rules import RISKS, RULES, relaxed_rules

HERE = Path(__file__).resolve().parent
MIN_VERSION = (3, 10)
_VERSIONS = {}  # 解释器路径 -> (版本元组, 版本字符串)；探测一次就缓存 / interpreter path -> (version tuple, text), probed once
NO_WINDOW = {'creationflags': 0x08000000} if sys.platform == 'win32' else {}


class ProjectEnvironment:
    """Python 项目环境的操作（设置页与新任务页通过它做事；策略与上限来自包的配置）。 Operations on the Python project environment (the Settings and New Task pages use it; policy and cap come from the pack's configuration)."""

    def __init__(self, pack):
        self.pack = pack

    def status(self):
        pack = self.pack
        return {**env.status(pack.env_max_mb()), 'policy': pack.package_policy(), 'uses_project_env': pack.uses_project_env(), 'interpreter': str(pack.interpreter()), 'tooling_missing': pack.tooling_missing(),
                'configured_interpreter': str(pack.config.get('interpreter') or ''), 'candidates': env.discover_interpreters()}

    def create(self):
        """创建环境；选了 pytest 时顺手补装 pytest（它是你选的测试工具，不算“项目依赖”，不再问；算进大小上限）。幂等：环境已存在就只补缺的工具。
        Create the environment; when pytest is the chosen framework it is installed too (it is the test tool you chose, not a project dependency, so no further asking; it counts toward the size cap). Idempotent: an existing environment only gets its missing tools."""
        env.create()
        missing = self.pack.tooling_missing()
        if missing:
            env.install(missing, self.pack.env_max_mb())

    def reset(self):
        env.reset()

    def install(self, names, approve=False):
        """策略 ask：没有 approve 只返回“需要批准”；allow：直接装；deny：拒绝。 Policy ask: without approve only "needs approval" comes back; allow installs at once; deny refuses."""
        pack = self.pack
        names = env.clean_requirements(names)
        if pack.package_policy() == 'deny':
            raise MasaError('installing packages is switched off (Settings -> Languages -> Python -> Third-party packages)')
        if pack.package_policy() == 'ask' and approve is not True:
            return {'needs_approval': True, 'packages': names, 'size_mb': env.size_mb() if env.exists() else 0, 'max_mb': pack.env_max_mb()}
        return {'needs_approval': False, **env.install(names, pack.env_max_mb())}

    def pick_interpreter(self):
        return env.pick_file()

    def required_packages(self, goal):
        return self.pack.required_packages(goal)

    def summary(self):
        pack = self.pack
        return {'policy': pack.package_policy(), 'env_exists': env.exists(), 'size_mb': env.size_mb() if env.exists() else 0, 'max_mb': pack.env_max_mb()}


class PythonPack(LanguagePack):
    override_seeds = True

    def environment(self):
        return ProjectEnvironment(self)

    # ---------- 配置 / configuration ----------
    def interpreter(self):
        """设置里配置的解释器；留空时用**项目自己的环境**（.tools/pyenv，创建过才有），再没有才退回运行 masa 的这一个。
        The interpreter from Settings; empty means the project's own environment (.tools/pyenv, once created), and only without one the Python that runs masa."""
        configured = str(self.config.get('interpreter') or '').strip()
        if configured:
            return Path(configured)
        return env.env_python() if env.exists() else Path(sys.executable)

    def uses_project_env(self):
        return not str(self.config.get('interpreter') or '').strip() and env.exists()

    def package_policy(self):
        """第三方包策略：ask（要装就问我，默认）、allow（完全访问：只在项目环境里自动装）、deny（只用标准库）。 Third-party package policy: ask (default), allow (full access, project environment only) or deny (standard library only)."""
        value = self.config.get('package_policy')
        return value if value in ('ask', 'allow', 'deny') else 'ask'

    def tooling_missing(self):
        """项目环境里缺的测试工具（只有选了 pytest 才可能缺）。 Test tools missing from the project environment (only possible with pytest chosen)."""
        if self.framework() == 'pytest' and self.uses_project_env() and 'pytest' not in env.top_levels():
            return ['pytest']
        return []

    def env_max_mb(self):
        try:
            return max(50, int(str(self.config.get('env_max_mb') or env.DEFAULT_MAX_MB).strip()))
        except ValueError:
            return env.DEFAULT_MAX_MB

    def framework(self):
        return 'pytest' if self.config.get('test_framework') == 'pytest' else 'unittest'

    def config_schema(self):
        return [ConfigField('interpreter', 'Python 解释器', 'path', '', '留空 = 使用项目自己的 Python 环境（.tools/pyenv，在下面创建）；要用本机其它环境请在下面选择，不要手写路径'),
                ConfigField('test_framework', '测试框架', 'choice', 'unittest', 'unittest 是标准库、无需安装；pytest 需要在上面的解释器里已经安装', choices=('unittest', 'pytest')),
                ConfigField('package_policy', '第三方包', 'choice', 'ask', '需要安装第三方包时怎么办。只会装进项目环境，永远不碰系统环境', choices=('ask', 'allow', 'deny'),
                            labels=('每次都询问我（推荐）', '完全访问：允许自动安装（仅项目环境）', '禁止：只用标准库')),
                ConfigField('env_max_mb', '项目环境大小上限（MB）', 'text', str(env.DEFAULT_MAX_MB), '装完超过上限会撤销这次安装')]

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
                problems.append('项目环境里还没有 pytest：到 设置 → 语言 → Python 点“补装 pytest”（或把测试框架改回 unittest）' if self.uses_project_env()
                                else '所选解释器里没有安装 pytest（pip install pytest）')
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
        """静态检查放行的第三方模块：pytest（选了 pytest 时），以及项目环境里已经装好的包（策略不是“禁止”时）。 Third-party modules the static check lets through: pytest (when chosen) and what is installed in the project environment (unless the policy is deny)."""
        out = {'pytest'} if self.framework() == 'pytest' else set()
        if self.uses_project_env() and self.package_policy() != 'deny':
            out |= set(env.top_levels())
        return out

    def packages_note(self):
        """给模型的一句话：项目环境里有哪些第三方包可以导入；没有就是空串（提示词保持原样）。 One sentence for the models: which third-party packages of the project environment may be imported; empty when there are none (prompts stay as they were)."""
        names = sorted(self._extra_imports() - {'pytest'})
        return ('Besides the standard library you may import these third-party packages, which are installed in the project environment (no others): ' + ', '.join(names) + '. ') if names else ''

    def required_packages(self, goal):
        """目标里点名的第三方包（pip 名字），以及其中还没装的。 Third-party packages the goal names (pip names) and which of them are not installed yet."""
        from masa.languages.python.triage_rules import third_party_names
        named = third_party_names(goal)
        have = {n.lower() for n in env.top_levels()} | {p['name'].lower().replace('-', '_') for p in (env.packages() if env.exists() else [])}
        return {'named': named, 'missing': [n for n in named if n.lower().replace('-', '_') not in have]}

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
        # 测试名由包名和文件名拼成（tests/test_command_parser.py ↔ command/parser.py）：按词数打分，唯一最高分的包认领它；否则归入入口所在的包。真实运行里这类测试全被归给入口包，command 包没有自己的测试，它的工人永远封不了包。
        # The test name is a mix of package and file names (tests/test_command_parser.py <-> command/parser.py): score packages by the words they share with it and let the unique top scorer claim it; otherwise the entry's package. In a real run all such tests went to the entry package, so the command package had no tests of its own and its worker could never seal.
        words = set(stem.split('_')) - {''}
        scores = {}
        for candidate in files:
            if checks.is_test_path(candidate) or not candidate.endswith('.py') or candidate.endswith('__init__.py') or '/' not in candidate:
                continue
            directory, name = candidate.rsplit('/', 1)
            tokens = ({directory.rsplit('/', 1)[-1]} | set(name[:-3].split('_'))) - {''}
            scores[directory] = scores.get(directory, set()) | tokens
        ranked = sorted(((len(words & tokens), directory) for directory, tokens in scores.items() if directory.count('/') >= 1), reverse=True)
        if ranked and ranked[0][0] > 0 and (len(ranked) == 1 or ranked[0][0] > ranked[1][0]):
            return ranked[0][1]
        return (spec or {}).get('module', '')

    def worker_notes(self):
        return ('Python 3 project (standard library only); pyproject.toml and every __init__.py (empty) are fixed by the harness. <module>/__main__.py is a thin wrapper: import sys; '
                'from <module>.<package>.<file> import run; sys.exit(run(sys.argv[1:], sys.stdin, sys.stdout, sys.stderr)) with the FULL dotted path of the FILE that defines run; never read stdin there. '
                'Logic lives in <module>/<package>/<file>.py; tests are tests/test_<name>.py using unittest (assertEqual(actual, expected), ACTUAL first), never skipped, never assert True. '
                'Import project modules with absolute imports (from <module>.<package>.<file> import name); call run(args, io.StringIO(...), out, err) directly in tests. '
                'If a test starts the program as a process compare decoded text (text=True), never raw bytes (Windows writes CRLF). ') + self.packages_note()

    def outline(self, source):
        return checks.outline(source)

    # ---------- 失败解析 / failure parsing ----------
    def parse_failures(self, checks_results):
        return failures.parse_failures(checks_results, self.op_id('test'), self.op_id('static'))

    def assertion_diffs(self, checks, limit=3):
        return failures.assertion_diffs(checks, self.op_id('test'), limit)

    def failure_details(self, checks, limit=1400):
        return failures.details(checks, self.op_id('test'), limit)

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
        return prompts.project_roles_prompt(context, default_instruction) + self.packages_note()

    def transport_hint(self, context):
        return prompts.transport_hint(context)

    def triage_rules(self):
        # 策略不是“禁止”：第三方包不再是“不可能”，而是“要装、要你批准或授权”的风险提示。 Unless the policy is deny, a third-party package is no longer "impossible" but a risk: it needs to be installed with your approval or permission.
        return (relaxed_rules() if self.package_policy() != 'deny' else RULES), RISKS

    def judge(self, files, task, executor=None):
        return oracle.judge(self, self.interpreter(), files, task)


def create(manifest, root):
    return PythonPack(manifest=manifest, root=root)
