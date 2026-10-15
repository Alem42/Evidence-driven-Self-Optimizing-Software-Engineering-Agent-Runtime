"""RoleSpec 注册表：角色是数据，注册表是角色元数据的唯一来源。
RoleSpec registry: a role is data, and the registry is the single source of role metadata.

之前角色信息散落在提示词 if 链、输出 schema、校验器、路由的角色表、配置的角色白名单和前端标签里，新增一个角色要改七八处。
现在一个角色 = `specs/<id>.json`（元数据）+ 可选的 `specs/<id>.prompt.md`（提示词），其余各处都从这里读。
Role information used to be scattered over the prompt if-chain, output schemas, validators, the router's role table, the config's role allow-list and the frontend labels, so a new
role touched seven or eight places. A role is now `specs/<id>.json` (metadata) plus an optional `specs/<id>.prompt.md` (prompt); everything else reads from here.

格式选择：JSON + Markdown，而不是 YAML，因为项目核心保持零依赖（dependencies=[]）。字段与 Claude Code subagent（name/description/tools/提示词）和
OpenAI Agents SDK（instructions/tools/output_type）对齐，便于以后导入导出。
Format: JSON + Markdown rather than YAML because the core stays dependency-free. Fields mirror Claude Code subagents and the OpenAI Agents SDK so they can be imported/exported later.
"""
import copy
import json
import re
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from masa.domain.models import MasaError

SPEC_DIR = Path(__file__).resolve().parent / 'specs'
_ID = re.compile(r'^[a-z][a-z0-9_]{1,63}$')
_KEYS = {'id', 'label', 'description', 'order', 'level', 'routable', 'config_allowed', 'permissions', 'validator', 'prompt', 'prompt_builder',
         'output_schema', 'max_output_tokens', 'when', 'legacy', 'tunable', 'tools', 'input', 'optional'}
_WRITES = {'none', 'tests', 'implementation'}


class RoleSpecError(MasaError):
    """角色定义不合法：加载时就报错，而不是等到运行时。 An invalid role definition: reported at load time, not at run time."""


@dataclass(frozen=True)
class RoleSpec:
    id: str
    label: str
    description: str
    order: int
    level: object  # None | 'top' | 'low' | int：默认等级倾向，路由器据此派生 prefer_highest_roles / default level preference, from which the router derives prefer_highest_roles
    routable: bool  # 是否进入路由器的角色表（预检、审查等只用本地模型的角色不进） / does the role appear in the router's role table
    config_allowed: bool  # 模型配置的 roles 白名单里是否允许出现 / may appear in a model profile's roles allow-list
    permissions: dict
    validator: str  # protocol.VALIDATORS 里的名字 / a name in protocol.VALIDATORS
    prompt: object = None  # 静态提示词文本；与 prompt_builder 二选一 / static prompt text; exclusive with prompt_builder
    prompt_builder: object = None  # protocol.PROMPT_BUILDERS 里的名字（提示词随上下文变化的角色） / for roles whose prompt depends on the context
    output_schema: object = None  # 输出契约（JSON Schema 子集）：约束解码 + 通用校验 / output contract: constrained decoding + generic validation
    max_output_tokens: object = None
    when: object = None  # 允许被选中的条件（guard 名 + 参数），供指挥者使用 / when the role may be chosen (guard name + params), used by the conductor
    legacy: bool = False
    optional: bool = False  # 实验性可选角色：只在对应的策略开关打开时才参与（默认流程里完全不存在）/ experimental optional role: part of the loop only when its policy switch is on
    tunable: tuple = field(default_factory=tuple)
    tools: tuple = field(default_factory=tuple)
    input: tuple = field(default_factory=tuple)


def _fail(spec_id, message):
    raise RoleSpecError(f'role {spec_id!r}: {message}')


def _parse(path, raw, folder):
    spec_id = raw.get('id')
    if not isinstance(spec_id, str) or not _ID.match(spec_id):
        raise RoleSpecError(f'{path.name}: id must match {_ID.pattern}')
    if path.stem != spec_id:
        _fail(spec_id, f'file name {path.name} must match the id')
    unknown = set(raw) - _KEYS
    if unknown:
        _fail(spec_id, f'unknown fields {sorted(unknown)} (typo?)')
    for name in ('label', 'description', 'validator'):
        if not isinstance(raw.get(name), str) or not raw[name].strip():
            _fail(spec_id, f'{name} must be a non-empty string')
    if len(raw['description']) > 120:
        _fail(spec_id, 'description must be at most 120 characters (it is shown to the conductor)')
    if type(raw.get('order')) is not int:
        _fail(spec_id, 'order must be an integer')
    level = raw.get('level')
    if not (level is None or level in ('top', 'low') or (type(level) is int and 1 <= level <= 100)):
        _fail(spec_id, "level must be null, 'top', 'low' or an integer 1..100")
    for name in ('routable', 'config_allowed'):
        if type(raw.get(name)) is not bool:
            _fail(spec_id, f'{name} must be true or false')
    perms = raw.get('permissions')
    if not isinstance(perms, dict) or set(perms) - {'reads', 'writes'} or perms.get('writes') not in _WRITES \
            or not isinstance(perms.get('reads', []), list) or any(not isinstance(r, str) for r in perms.get('reads', [])):
        _fail(spec_id, "permissions must be {'reads': [...], 'writes': 'none'|'tests'|'implementation'}")
    has_prompt, has_builder = 'prompt' in raw, 'prompt_builder' in raw
    if has_prompt == has_builder:
        _fail(spec_id, 'exactly one of prompt (a .prompt.md file name) and prompt_builder is required')
    prompt = None
    if has_prompt:
        prompt_path = folder / str(raw['prompt'])
        if not isinstance(raw['prompt'], str) or prompt_path.parent != folder or not prompt_path.is_file():
            _fail(spec_id, f"prompt file {raw['prompt']!r} not found next to the spec")
        text = prompt_path.read_text(encoding='utf-8')
        prompt = text[:-1] if text.endswith('\n') else text  # 编辑器加的末尾换行不算提示词的一部分 / a trailing newline added by an editor is not part of the prompt
        if not prompt.strip():
            _fail(spec_id, 'the prompt file is empty')
    elif not isinstance(raw['prompt_builder'], str) or not raw['prompt_builder']:
        _fail(spec_id, 'prompt_builder must be a name')
    schema = raw.get('output_schema')
    if schema is not None and not isinstance(schema, dict):
        _fail(spec_id, 'output_schema must be an object')
    limit = raw.get('max_output_tokens')
    if limit is not None and not (type(limit) is int and 1 <= limit <= 65536):
        _fail(spec_id, 'max_output_tokens must be an integer 1..65536')
    when = raw.get('when')
    if when is not None and not (isinstance(when, dict) and isinstance(when.get('guard'), str)):
        _fail(spec_id, "when must be {'guard': <name>, ...}")
    if type(raw.get('optional', False)) is not bool:
        _fail(spec_id, 'optional must be true or false')
    for name in ('tunable', 'tools', 'input'):
        if not isinstance(raw.get(name, []), list) or any(not isinstance(x, str) for x in raw.get(name, [])):
            _fail(spec_id, f'{name} must be a list of strings')
    return RoleSpec(id=spec_id, label=raw['label'], description=raw['description'], order=raw['order'], level=level, routable=raw['routable'],
                    config_allowed=raw['config_allowed'], permissions=perms, validator=raw['validator'], prompt=prompt, prompt_builder=raw.get('prompt_builder'),
                    output_schema=schema, max_output_tokens=limit, when=when, legacy=bool(raw.get('legacy', False)), optional=bool(raw.get('optional', False)), tunable=tuple(raw.get('tunable', [])),
                    tools=tuple(raw.get('tools', [])), input=tuple(raw.get('input', [])))


class Registry:
    def __init__(self, specs):
        self._specs = dict(sorted(specs.items(), key=lambda item: (item[1].order, item[0])))

    def get(self, spec_id):
        return self._specs.get(spec_id) if isinstance(spec_id, str) else None

    def all(self):
        return list(self._specs.values())

    def ids(self):
        return tuple(self._specs)

    def routable_ids(self):
        """路由器的角色表（按 order）。 The router's role table, ordered."""
        return tuple(s.id for s in self._specs.values() if s.routable)

    def top_ids(self):
        """默认直接用最高等级的角色（规格、测试、测试修订、诊断……）。 Roles that start at the top level by default."""
        return tuple(s.id for s in self._specs.values() if s.routable and s.level == 'top' and not s.optional)

    def optional_top_ids(self):
        """可选角色里默认用最高等级的：策略 conductor 打开时才加进 prefer_highest_roles。 Optional roles that start at the top level: added to prefer_highest_roles only when policy `conductor` is on."""
        return tuple(s.id for s in self._specs.values() if s.routable and s.level == 'top' and s.optional)

    def config_roles(self):
        """模型配置 roles 白名单允许的角色。 Roles a model profile's allow-list may name."""
        return frozenset(s.id for s in self._specs.values() if s.config_allowed)

    def public(self):
        """给前端/指挥者看的元数据（不含提示词全文）。 Metadata for the UI and the conductor (never the full prompt)."""
        return [{'id': s.id, 'label': s.label, 'description': s.description, 'order': s.order, 'level': s.level, 'routable': s.routable,
                 'permissions': copy.deepcopy(s.permissions), 'legacy': s.legacy, 'when': copy.deepcopy(s.when)} for s in self._specs.values()]


def load_directory(folder):
    """读取一个目录里的全部角色定义并校验。 Load and validate every role definition in a directory."""
    folder = Path(folder).resolve()
    specs = {}
    for path in sorted(folder.glob('*.json')):
        try:
            raw = json.loads(path.read_text(encoding='utf-8'))
        except ValueError as exc:
            raise RoleSpecError(f'{path.name}: not valid JSON ({exc})') from None
        if not isinstance(raw, dict):
            raise RoleSpecError(f'{path.name}: must be an object')
        spec = _parse(path, raw, folder)
        if spec.id in specs:
            raise RoleSpecError(f'duplicate role id {spec.id!r}')
        specs[spec.id] = spec
    if not specs:
        raise RoleSpecError(f'no role definitions found in {folder}')
    return Registry(specs)


_current = None


def current():
    global _current
    if _current is None:
        _current = load_directory(SPEC_DIR)
    return _current


def get(spec_id):
    return current().get(spec_id)


@contextmanager
def use(folder):
    """测试用：临时换成另一个目录的注册表。 For tests: temporarily swap in another directory's registry."""
    global _current
    previous, _current = _current, load_directory(folder)
    try:
        yield _current
    finally:
        _current = previous
