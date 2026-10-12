"""语言包接口：流程里“只有某种语言才知道的事”都从这里问。核心不认识任何具体语言。
Language-pack interface: everything only one language knows is asked here. The core names no concrete language.

设计见 docs/design/LANGUAGE_PACKS.md。声明性的内容放在包目录的 pack.json（清单），逻辑放在包的 Python 类里。
Declarative facts live in the pack's pack.json manifest, logic in the pack's Python class (design: docs/design/LANGUAGE_PACKS.md).

依赖方向：语言包只能依赖 masa.domain 与标准库；核心只能依赖本模块与 registry，不能 import 任何具体语言包。
Dependency direction: packs depend only on masa.domain and the stdlib; the core depends only on this module and the registry, never on a concrete pack.
"""
import json
from dataclasses import dataclass, field
from pathlib import Path

API_VERSION = 1
# 操作种类：test 必需；其余可选。 Operation kinds: test is required, the rest are optional.
KINDS = ('test', 'static', 'format', 'build', 'run', 'index')
# 可选能力：没声明的功能由核心关闭，界面隐藏。 Optional capabilities: undeclared ones are switched off by the core and hidden in the UI.
CAPABILITIES = ('index', 'single_file_review', 'mutation_review', 'formatter')
# 失败归属与类别（语言无关）。 Failure ownership and kinds (language-neutral).
IMPLEMENTATION, TEST, AMBIGUOUS = 'implementation', 'test', 'ambiguous'
COMPILE, SETUP, ASSERTION, FORMAT = 'compile', 'setup', 'assertion', 'format'
OWNERS = (IMPLEMENTATION, TEST)
FAILURE_KINDS = (COMPILE, SETUP, ASSERTION, FORMAT)


@dataclass(frozen=True)
class OperationDef:
    """一个可由运行器执行的操作。 One operation the runner can execute."""
    kind: str  # KINDS 之一 / one of KINDS
    id: str  # 账本里的操作 id（Go 保持 go_test 等不变）/ operation id in the ledger
    label: str  # 展示名 / display name
    exe: str = 'toolchain'  # 可执行文件的角色：toolchain 主工具，其它名字由包的 executables() 解析 / role of the executable
    args: tuple = ()  # 参数模板，可含 {timeout_ms} {out} / argument template
    fail_if_stdout: bool = False  # 例如 gofmt -l：有输出就算失败 / e.g. gofmt -l: any output is a failure
    build: dict | None = None  # 先构建再运行（run 类）/ build step before running (run kind)


@dataclass(frozen=True)
class Diagnostic:
    """一条解析后的失败：语言无关的数据形状。 One parsed failure in a language-neutral shape."""
    owner: str  # implementation | test
    kind: str  # compile | setup | assertion | format
    path: str
    line: int | None
    message: str

    def to_dict(self):
        return {'owner': self.owner, 'kind': self.kind, 'path': self.path, 'line': self.line, 'message': self.message}


@dataclass(frozen=True)
class Readiness:
    """工具链是否就绪：界面“设置 → 语言”显示。 Whether the toolchain is usable (shown in Settings → Languages)."""
    ok: bool
    details: tuple = ()  # 人能读的说明，例如缺什么 / human-readable notes, e.g. what is missing

    def to_dict(self):
        return {'ok': self.ok, 'details': list(self.details)}


@dataclass
class ConfigField:
    """语言包可在设置里调节的一项配置。 One setting a pack exposes in Settings."""
    key: str
    label: str
    kind: str = 'path'  # path | text | bool | choice
    default: object = ''
    help: str = ''
    choices: tuple = ()
    secret: bool = False

    def to_dict(self):
        return {'key': self.key, 'label': self.label, 'kind': self.kind, 'default': self.default, 'help': self.help, 'choices': list(self.choices)}


@dataclass
class LanguagePack:
    """语言包基类：清单 + 默认实现。具体语言继承并覆盖。 Base class: the manifest plus defaults; a concrete language subclasses and overrides."""
    manifest: dict
    root: Path | None = None
    config: dict = field(default_factory=dict)  # 用户在设置里给这个包的配置 / the user's settings for this pack

    # ---------- 身份 / identity ----------
    @property
    def id(self):
        return self.manifest['id']

    @property
    def title(self):
        return self.manifest.get('title', self.id)

    @property
    def version(self):
        return int(self.manifest.get('version', 1))

    @property
    def capabilities(self):
        return frozenset(self.manifest.get('capabilities', ()))

    def supports(self, capability):
        return capability in self.capabilities

    # ---------- 布局 / layout ----------
    @property
    def layout(self):
        return self.manifest['layout']

    @property
    def source_exts(self):
        return tuple(self.layout['source_exts'])

    @property
    def root_marker(self):
        return self.layout['root_marker']

    @property
    def manifest_files(self):
        return tuple(self.layout.get('manifest_files', ()))

    def is_test(self, path):
        """测试文件？（冻结、归属、补丁都靠它。）Is this a test file (freezing, ownership and patches rely on it)?"""
        return str(path).replace('\\', '/').endswith(tuple(self.layout.get('test_suffixes', ())))

    def is_source(self, path):
        """实现文件（语言的源文件且不是测试）。 An implementation file: a source file that is not a test."""
        text = str(path).replace('\\', '/')
        return text.endswith(self.source_exts) and not self.is_test(text)

    def is_code(self, path):
        """语言的源文件（含测试）。 A source file of the language, tests included."""
        return str(path).replace('\\', '/').endswith(self.source_exts)

    def entrypoint(self, spec=None):
        """入口文件路径（可依赖规格，例如 Python 的 <module>/__main__.py）。 The entrypoint path (may depend on the spec, e.g. <module>/__main__.py)."""
        return self.layout['entrypoint']

    def entrypoint_schema(self):
        """规划阶段给模型提供商的入口字段 schema（此时还没有规格）。 The entrypoint field's schema for the planner's provider (there is no spec yet)."""
        return {'type': 'string', 'const': self.entrypoint()}

    def keep_paths(self, paths):
        """修复时无论如何都要带上的文件（清单与测试）。 Files always kept in a repair context (manifest and tests)."""
        return {p for p in paths if p in self.manifest_files or self.is_test(p)}

    # ---------- 操作 / operations ----------
    @property
    def operations(self):
        """{kind: OperationDef}，来自清单。 From the manifest."""
        out = {}
        for kind, item in self.manifest['operations'].items():
            out[kind] = OperationDef(kind=kind, id=item['id'], label=item.get('label', item['id']), exe=item.get('exe', 'toolchain'),
                                     args=tuple(item.get('args', ())), fail_if_stdout=bool(item.get('fail_if_stdout')), build=item.get('build'))
        return out

    def operation_ids(self):
        return frozenset(op.id for op in self.operations.values())

    def op_id(self, kind):
        return self.operations[kind].id if kind in self.operations else None

    def kind_of(self, operation_id):
        return next((k for k, op in self.operations.items() if op.id == operation_id), None)

    def op_label(self, operation_id):
        return next((op.label for op in self.operations.values() if op.id == operation_id), operation_id)

    # ---------- 工具链 / toolchain ----------
    def probe(self, executor=None):
        """能不能用。 Is it usable."""
        return Readiness(True)

    def fingerprint(self, executor):
        """写进账本 profile 的工具链身份（路径与哈希）。 Toolchain identity recorded in the ledger profile (paths and hashes)."""
        return {}

    def executables(self, executor):
        """{可执行文件的角色: 绝对路径}，供操作表里的 exe 引用（例如 toolchain、format）。 {role: absolute path} the operation table refers to (e.g. toolchain, format)."""
        return {}

    def runner_env(self, executor):
        """运行器进程需要额外带上的环境变量（会经由 env_policy.allow 传给被测代码）。 Extra variables for the runner process (passed on to the code under test via env_policy.allow)."""
        return {}

    def env_policy(self, executor):
        """被测代码能看到的环境：{'allow': [...], 'set': {...}, 'path_prepend': [...]}。 The environment the code under test sees."""
        return {'allow': ['SystemRoot', 'WINDIR', 'TEMP', 'TMP', 'TMPDIR', 'HOME', 'USERPROFILE', 'LOCALAPPDATA'], 'set': {}, 'path_prepend': []}

    def runner_ops(self, executor):
        """运行器的命令清单（见 docs/design/LANGUAGE_PACKS.md 第 8 节）：由清单里的操作定义与本包的工具链路径生成。 The runner's operation table, generated from the manifest's operation definitions and this pack's toolchain paths."""
        exes = self.executables(executor)

        def resolve(role):
            return str(exes.get(role, role))
        operations = {}
        for kind, op in self.operations.items():
            if op.exe == 'builtin':
                operations[op.id] = {'builtin': op.id}
                continue
            entry = {'exe': resolve(op.exe), 'args': list(op.args)}
            if op.fail_if_stdout:
                entry['fail_if_stdout_nonempty'] = True
            if op.build:
                entry['build'] = {'exe': resolve(op.build['exe']), 'args': list(op.build.get('args', ()))}
            if kind == 'run':
                entry.update(argv_from_request=True, run=True)
            operations[op.id] = entry
        return {'version': 1, 'root_marker': self.root_marker, 'env': self.env_policy(executor), 'operations': operations}

    def config_schema(self):
        """设置页里这个包可调的项。 What the Settings page lets the user tune for this pack."""
        return []

    # ---------- 规格与文件校验 / specs and files ----------
    def seed_files(self, spec):
        """由规格确定、不由模型生成的文件。 Files determined by the spec, never written by the model."""
        return {}

    def allow_empty(self, path):
        """这个文件可以是空的吗（例如 Python 的 __init__.py）？ May this file be empty (e.g. Python's __init__.py)?"""
        return False

    def validate_spec(self, spec):
        """中立校验之外的语言规则；不合格抛 MasaError。 Language rules on top of the neutral checks; raise MasaError."""

    def validate_file_proposal(self, files, spec, target_path):
        """逐文件产物的语言规则。 Language rules for a single-file proposal."""

    def validate_bundle(self, files, spec):
        """整包的语言规则。 Language rules for the whole bundle."""

    # ---------- 确定性检查 / deterministic checks ----------
    def normalize(self, executor, files, paths, imports=True):
        """无模型的规范化（格式、导入）。返回 (新的 files, {路径: 导入改动})。 Model-free normalisation; returns (files, {path: import changes})."""
        return files, {}

    def static_messages(self, files, paths, spec=None):
        """不需要工具链的就地检查（空转测试、入口规则、未知内部导入…）：{路径: 给模型的重写说明}。 Toolchain-free in-stage checks: {path: rewrite instruction}."""
        return {}

    def parse_errors(self, executor, files, paths):
        """语法检查：{路径: 带行列号的错误}。工具链缺失视为无错误。 Parse check: {path: error with line:column}; a missing toolchain means no errors."""
        return {}

    def entry_problems(self, files, paths=None):
        """入口规则：{路径: 说明}。 The entrypoint rule: {path: explanation}."""
        return {}

    def declares(self, source, name):
        """包的源码里是否声明了这个导出名（契约检查用）。 Does the package source declare this exported name (contract check)?"""
        return True

    def format_text(self, executor, text):
        """格式化一段源码；做不到返回 None。 Format one source text, or None."""
        return None

    def failure_markers(self):
        """失败证据里值得保留的诊断行的正则（字符串）。 Regex (string) of diagnostic lines worth keeping in failure evidence."""
        return r'(?i)(?:FAIL|error|exit code)'

    def output_lines(self, text):
        """把一次检查的 stdout 拆成逻辑行（有的工具输出是 JSON 帧）。 Split a check's stdout into logical lines (some tools emit JSON frames)."""
        yield from str(text or '').splitlines()

    # ---------- 失败解析 / failure parsing ----------
    def parse_failures(self, checks):
        """checks=[(operation_id, result)] → 诊断字典列表（Diagnostic.to_dict() 的形状，最多 40 条）。 Parse raw check results into diagnostic dicts (at most 40)."""
        return []

    def needs_test_revision(self, evidence, checks=()):
        """失败证据是否表明问题在测试源码或测试准备里。 Does the evidence point at test source or test setup?"""
        return False

    def assertion_signature(self, checks):
        """失败断言的指纹（元组）；空表示没有断言失败。 Fingerprint of the failing assertions; empty means none."""
        return ()

    def failure_signature(self, checks):
        """归一化的失败签名（去掉路径、行列号和数字）。 A normalised failure signature (paths, positions and numbers stripped)."""
        return ()

    def test_advice(self):
        """[(诊断里的标记, 给小模型的具体做法)]。 [(marker in a diagnostic, concrete advice for a small model)]."""
        return ()

    def assertion_of(self, message):
        """{'call','got','want'} 或 None。 Parse an assertion message."""
        return None

    def relative_path(self, path):
        """把诊断里可能带绝对路径的文件名还原成项目内的相对路径。 Turn a path from a diagnostic (possibly absolute) into a project-relative one."""
        return path

    def location_regex(self):
        """匹配 `文件:行[:列]` 的正则（字符串）。 Regex (string) matching `file:line[:col]`."""
        return r'([\w./\\-]+):(\d+)(?::\d+)?'

    def test_function_source(self, files, path, line, limit=1500):
        return ''

    # ---------- 提示词 / prompts ----------
    def transport_hint(self, context):
        """本地（ollama）传输的补充说明。 Extra instruction text for the local (ollama) transport."""
        return ''

    def project_prompt(self, context, default_instruction):
        """Planner / Tester / Developer / Repair / TestRevision 这一族角色的提示词（随 purpose 与上下文分支）。 The prompt of the project-role family (branches on purpose and context)."""
        raise NotImplementedError

    def fragment(self, name, default=''):
        """提示词片段（见 prompts/ 目录）。 A prompt fragment."""
        return default

    # ---------- 其它 / other ----------
    def triage_rules(self):
        """(不可行规则, 风险规则) 两个列表；规则引擎在核心。 (infeasible rules, risk rules); the engine lives in the core."""
        return [], []

    def outline(self, source):
        """签名摘要。 A signature digest."""
        return source

    def internal_dirs(self, spec):
        """契约要覆盖的“内部包”目录。 Directories the package contract covers."""
        entry_dir = self.entrypoint(spec).rsplit('/', 1)[0] if '/' in self.entrypoint(spec) else ''
        return sorted({i['path'].rsplit('/', 1)[0] for i in spec['files'] if self.is_source(i['path']) and '/' in i['path'] and i['path'].rsplit('/', 1)[0] != entry_dir})

    def judge(self, files, task, executor=None):
        """评测的独立判官。 The benchmark's independent oracle."""
        raise NotImplementedError

    def ui(self):
        """给前端的提示。 Hints for the frontend."""
        return dict(self.manifest.get('ui', {}))

    def describe(self):
        return {'id': self.id, 'title': self.title, 'version': self.version, 'capabilities': sorted(self.capabilities),
                'operations': {k: {'id': op.id, 'label': op.label} for k, op in self.operations.items()}, 'ui': self.ui(),
                'config': [f.to_dict() for f in self.config_schema()]}


def load_manifest(path):
    """读并校验清单的必需字段；不合格抛 ValueError。 Read a manifest and check its required fields; raise ValueError."""
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    for key in ('id', 'api_version', 'layout', 'operations'):
        if key not in data:
            raise ValueError(f'manifest lacks {key}')
    if data['api_version'] != API_VERSION:
        raise ValueError(f"pack api_version {data['api_version']} is not supported (core speaks {API_VERSION})")
    if 'test' not in data['operations']:
        raise ValueError('a pack must define the test operation')
    for kind in data['operations']:
        if kind not in KINDS:
            raise ValueError(f'unknown operation kind {kind}')
    for cap in data.get('capabilities', ()):
        if cap not in CAPABILITIES:
            raise ValueError(f'unknown capability {cap}')
    layout = data['layout']
    for key in ('source_exts', 'root_marker', 'entrypoint', 'test_suffixes'):
        if key not in layout:
            raise ValueError(f'layout lacks {key}')
    return data
