"""可调空间与补丁校验：调优器只能改**数据**（路由策略、修复子图里“可选的边”），不能执行任何模型产出的代码。
The tunable space and patch validation: the tuner may only change DATA (routing policy, the optional edges of the repair graph); it never runs model-written code.

一个“配置”是相对用户已保存设置的增量 / A config is a delta over the user's saved settings:
    {'policy': {'max_escalations': 3, 'prefer_highest_roles': [...], ...}, 'drop_edges': [{'from': 'classify', 'to': 'rewrite', 'when': 'rewrite_due'}]}
一个“补丁”是至多 3 条操作 / A patch is at most three operations:
    {'op': 'set', 'path': 'policy.max_escalations', 'value': 3}
    {'op': 'drop_edge', 'from': 'classify', 'to': 'rewrite', 'when': 'rewrite_due'}
    {'op': 'keep_edge', ...}   # 撤销之前的 drop_edge / undo an earlier drop
应用补丁后必须同时通过 validate_policy() 和 flow.validate()，否则整个补丁被丢弃。
After applying a patch the result must pass validate_policy() AND flow.validate(), otherwise the whole patch is discarded.
"""
import copy
import json

from masa.application import flow
from masa.application.routing import DEFAULT_POLICY, validate_policy
from masa.application.workflows import FIX_V1
from masa.domain.models import MasaError

MAX_OPS = 3
# 数值/布尔策略项：路径 → (类型, 下限, 上限) / numeric and boolean policy fields: path -> (type, low, high)
SCALARS = {
    'policy.max_escalations': (int, 0, 3),
    'policy.stuck_after': (int, 3, 8),
    'policy.diagnose_max': (int, 0, 4),
    'policy.planner_retries': (int, 1, 3),
    'policy.attempts_per_level.fix': (int, 1, 3),
    'policy.attempts_per_level.generation': (int, 1, 3),
    'policy.conductor': (bool, None, None),
    'policy.conductor_max_calls': (int, 1, 8),
    'policy.conductor_cascade': (bool, None, None),
    'policy.diagnose': (bool, None, None),
}
# 可以切换“直接用最高等级”的角色，以及可以调整起始等级的角色。 Roles that can be toggled to start at the top level / given a start level.
TOP_ROLES = ('project_planner', 'project_tester', 'project_test_revision', 'project_diagnoser', 'project_conductor')
START_ROLES = ('project_planner', 'project_tester', 'project_developer')
# 只允许去掉这些“可选”的边：去掉后总有 always 兜底边接手，图仍然合法。 Only these optional edges may be dropped; the `always` fallbacks keep the graph valid.
DROPPABLE = frozenset({'rewrite_due', 'needs_diagnosis', 'arbitrate_due', 'flip_to_tests', 'noop_diagnose', 'noop_retry'})


class PatchError(MasaError):
    """补丁不合法（被丢弃，不是程序错误）。 The patch is illegal (discarded; not a program error)."""


def empty():
    return {'policy': {}, 'drop_edges': []}


def fingerprint(config):
    """配置的稳定指纹，用来跳过重复评估。 A stable fingerprint, so duplicates are not evaluated twice."""
    return json.dumps({'policy': config['policy'], 'drop_edges': sorted(config['drop_edges'], key=lambda e: (e['from'], e['to'], e['when']))}, sort_keys=True, ensure_ascii=False)


def _edge_exists(edge):
    return any(e['from'] == edge['from'] and e['to'] == edge['to'] and e.get('when', 'always') == edge['when'] for e in FIX_V1['edges'])


def _set(config, path, value):
    if path in SCALARS:
        kind, low, high = SCALARS[path]
        if kind is bool:
            if type(value) is not bool:
                raise PatchError(f'{path} must be true or false')
        elif type(value) is not int or not low <= value <= high:
            raise PatchError(f'{path} must be an integer in {low}..{high}')
        parts = path.split('.')[1:]
        target = config['policy']
        for part in parts[:-1]:
            target = target.setdefault(part, {})
        target[parts[-1]] = value
        return
    if path.startswith('policy.prefer_highest.'):
        role = path.rsplit('.', 1)[1]
        if role not in TOP_ROLES or type(value) is not bool:
            raise PatchError(f'{path}: unknown role or non-boolean value')
        current = set(config['policy'].get('prefer_highest_roles', DEFAULT_POLICY['prefer_highest_roles']))
        (current.add if value else current.discard)(role)
        config['policy']['prefer_highest_roles'] = sorted(current)
        return
    if path.startswith('policy.start_level.'):
        role = path.rsplit('.', 1)[1]
        if role not in START_ROLES or type(value) is not int or not 1 <= value <= 3:
            raise PatchError(f'{path}: unknown role or level outside 1..3')
        config['policy'].setdefault('start_level_by_role', {})[role] = value
        return
    raise PatchError(f'path {path!r} is not tunable')


def apply(config, patch):
    """返回应用补丁后的**新**配置；任何一步不合法就抛 PatchError，原配置不变。
    Returns a NEW config with the patch applied; any illegal step raises PatchError and the original is untouched."""
    if not isinstance(patch, list) or not 1 <= len(patch) <= MAX_OPS:
        raise PatchError(f'a patch has 1..{MAX_OPS} operations')
    out = copy.deepcopy(config)
    for op in patch:
        if not isinstance(op, dict) or op.get('op') not in ('set', 'drop_edge', 'keep_edge'):
            raise PatchError('unknown operation')
        if op['op'] == 'set':
            if set(op) - {'op', 'path', 'value'} or not isinstance(op.get('path'), str):
                raise PatchError('malformed set operation')
            _set(out, op['path'], op.get('value'))
            continue
        edge = {k: op.get(k) for k in ('from', 'to', 'when')}
        if set(op) - {'op', 'from', 'to', 'when'} or not all(isinstance(v, str) for v in edge.values()):
            raise PatchError('malformed edge operation')
        if edge['when'] not in DROPPABLE or not _edge_exists(edge):
            raise PatchError(f'edge {edge} cannot be changed')
        listed = [e for e in out['drop_edges'] if e != edge]
        out['drop_edges'] = listed + ([edge] if op['op'] == 'drop_edge' else [])
    materialize(out)  # 整体校验：策略与工作流图 / validate the whole thing: policy and graph
    return out


def materialize(config):
    """配置 → 运行用的覆盖项 {'policy': 校验过的完整策略增量, 'workflow': 图定义或 None}。不合法抛 PatchError。
    Config -> the overrides a run uses: {'policy': validated delta, 'workflow': graph or None}. Illegal raises PatchError."""
    try:
        validate_policy(config['policy'])
        workflow = None
        if config['drop_edges']:
            workflow = copy.deepcopy(FIX_V1)
            gone = {(e['from'], e['to'], e['when']) for e in config['drop_edges']}
            workflow['edges'] = [e for e in workflow['edges'] if (e['from'], e['to'], e.get('when', 'always')) not in gone]
            flow.validate(workflow)
    except MasaError as exc:
        raise PatchError(str(exc)) from None
    return {'policy': copy.deepcopy(config['policy']), 'workflow': workflow}


def describe(config):
    """人能读的一行行改动（报告用）。 Human-readable changes, one per line (for the report)."""
    lines = []

    def walk(prefix, value):
        if isinstance(value, dict):
            for key, item in sorted(value.items()):
                walk(f'{prefix}.{key}', item)
        else:
            lines.append(f'{prefix} = {value}')
    walk('policy', config['policy'])
    lines += [f"drop edge {e['from']} -> {e['to']} when {e['when']}" for e in config['drop_edges']]
    return lines or ['(与用户已保存的设置相同 / identical to the saved settings)']


def catalog():
    """给提议者看的可调空间说明（也是它唯一能用的操作）。 The space description shown to the proposer (and the only operations it may use)."""
    return {
        'set': {**{path: (f'bool' if kind is bool else f'int {low}..{high}') for path, (kind, low, high) in SCALARS.items()},
                **{f'policy.prefer_highest.{role}': 'bool (start at the top level)' for role in TOP_ROLES},
                **{f'policy.start_level.{role}': 'int 1..3' for role in START_ROLES}},
        'drop_edge / keep_edge': sorted({f"{e['from']}->{e['to']} when {e['when']}" for e in FIX_V1['edges'] if e.get('when') in DROPPABLE}),
    }
