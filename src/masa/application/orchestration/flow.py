"""声明式工作流：节点与边是**数据**，边上的条件（guard）是注册的纯函数，引擎只负责“按数据走图、记录轨迹、限制步数”。
Declarative workflows: nodes and edges are DATA, edge guards are registered pure functions, and the engine only walks the graph,
records a trace and bounds the number of steps.

设计约束 / Constraints (与 DESIGN_SYSTEM_ARCHITECTURE 一致):
  · 图由人/版本控制定义并校验，**模型不能改图**（模型只提议，走向由确定性 guard 决定）。 Models never edit the graph.
  · 动作（action）是注册的执行器，由应用层提供；定义里只写名字。 Actions are registered executors; definitions only name them.
  · 每个定义有版本，并随任务保存（可审计、可复现）。 Definitions are versioned and saved with the task.
  · 渐进迁移：先接管“失败后的修复子图”；规划/生成仍由 go-cli-v1 的流程负责。 Strangler: this takes over the post-failure repair subgraph first.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from masa.domain.models import MasaError

GUARDS: dict[str, Callable[[dict, dict], bool]] = {}


def guard(name: str):
    """注册一个 guard：f(facts, params) -> bool，必须是纯函数。 Register a pure guard f(facts, params) -> bool."""
    def register(fn):
        if name in GUARDS:
            raise FlowError(f'guard {name} registered twice')
        GUARDS[name] = fn
        return fn
    return register


class FlowError(MasaError):
    """工作流定义或执行有误（不是业务失败）。 The workflow definition or execution is broken (not a business failure)."""


@dataclass
class Step:
    node: str
    action: str | None
    to: str | None
    why: str | None  # 走了哪条边（guard 名） / which edge fired
    facts: dict = field(default_factory=dict)


def validate(defn: dict) -> dict:
    """校验定义：所有节点/边/guard 存在、入口存在、每个节点都可达、非终点节点有兜底边（always）。
    Validate: every node/edge/guard exists, the entry exists, all nodes are reachable and every non-end node has an `always` fallback."""
    if not isinstance(defn, dict) or not all(k in defn for k in ('id', 'version', 'entry', 'nodes', 'edges')):
        raise FlowError('workflow needs id, version, entry, nodes and edges')
    nodes, edges = defn['nodes'], defn['edges']
    if not isinstance(nodes, dict) or not nodes or defn['entry'] not in nodes:
        raise FlowError('workflow entry must be a declared node')
    for nid, spec in nodes.items():
        if spec.get('end'):
            continue
        if not isinstance(spec.get('action'), str):
            raise FlowError(f'node {nid} needs an action or must be an end node')
    for nid, spec in nodes.items():
        # llm_choice：让指挥者在“声明过的候选节点”里选一个。候选必须是图里的节点，且每个候选都有 `chosen` 边；
        # 另外要有 always 兜底边（指挥者不可用/提议非法时回到确定性规则）。
        # llm_choice: the conductor picks one DECLARED candidate node. Every candidate must be a node with a `chosen` edge,
        # and an `always` fallback edge returns to the deterministic rules when the proposal is illegal or unavailable.
        if spec.get('kind') == 'llm_choice':
            options = spec.get('candidates')
            if not isinstance(options, list) or len(options) < 2 or any(o not in nodes or o == nid for o in options):
                raise FlowError(f'llm_choice node {nid} needs at least two candidate nodes that exist')
            chosen = {e['params'].get('node') for e in edges if e.get('from') == nid and e.get('when') == 'chosen' and isinstance(e.get('params'), dict)}
            if set(options) - chosen:
                raise FlowError(f'llm_choice node {nid} lacks a chosen edge for {sorted(set(options) - chosen)}')
    outgoing: dict[str, list[dict]] = {nid: [] for nid in nodes}
    for edge in edges:
        if edge.get('from') not in nodes or edge.get('to') not in nodes:
            raise FlowError(f'edge {edge} references an unknown node')
        if nodes[edge['from']].get('end'):
            raise FlowError(f'end node {edge["from"]} cannot have outgoing edges')
        when = edge.get('when', 'always')
        if when != 'always' and when not in GUARDS:
            raise FlowError(f'unknown guard {when}')
        outgoing[edge['from']].append(edge)
    for nid, spec in nodes.items():
        if not spec.get('end') and not any(e.get('when', 'always') == 'always' for e in outgoing[nid]):
            raise FlowError(f'node {nid} needs an `always` fallback edge')
    reachable, stack = set(), [defn['entry']]
    while stack:
        current = stack.pop()
        if current in reachable:
            continue
        reachable.add(current)
        stack += [e['to'] for e in outgoing[current]]
    missing = set(nodes) - reachable
    if missing:
        raise FlowError(f'unreachable nodes: {sorted(missing)}')
    return defn


def next_edge(defn: dict, node: str, facts: dict) -> dict:
    """按声明顺序取第一条条件成立的边；always 恒成立。 First edge whose guard holds, in declaration order."""
    for edge in defn['edges']:
        if edge['from'] != node:
            continue
        when = edge.get('when', 'always')
        if when == 'always' or GUARDS[when](facts, edge.get('params', {})):
            return edge
    raise FlowError(f'no edge fired from {node}')  # validate() 保证不会发生 / validate() makes this unreachable


class FlowEngine:
    def __init__(self, defn: dict, actions: dict[str, Callable[[dict], dict | None]], *, max_steps: int = 12, on_step: Callable[[Step], None] | None = None):
        self.defn = validate(defn)
        missing = {s['action'] for s in defn['nodes'].values() if s.get('action')} - set(actions)
        if missing:
            raise FlowError(f'no executor for actions {sorted(missing)}')
        self.actions, self.max_steps, self.on_step = actions, max_steps, on_step

    def run(self, facts: dict) -> dict:
        """从入口走到终点节点。executor 返回的字典合并进 facts；返回 {end, outcome, facts, trace}。
        Walk from the entry to an end node; each executor's returned dict is merged into the facts."""
        node, trace = self.defn['entry'], []
        for _ in range(self.max_steps):
            spec = self.defn['nodes'][node]
            if spec.get('end'):
                return {'end': node, 'outcome': spec['end'], 'facts': facts, 'trace': trace}
            produced = self.actions[spec['action']](facts) or {}
            facts.update(produced)
            edge = next_edge(self.defn, node, facts)
            step = Step(node=node, action=spec['action'], to=edge['to'], why=edge.get('when', 'always'))
            trace.append(step)
            if self.on_step:
                self.on_step(step)
            node = edge['to']
        raise FlowError(f'workflow {self.defn["id"]} exceeded {self.max_steps} steps (likely a cycle)')
