"""The scheduler consumes graph data, not a hard-coded role sequence."""

from masa.domain import Graph, MasaError, Node, OPERATIONS, TERMINAL


def default_policy(operation: str = "go_test") -> Graph:
    """构建并校验单检查图。 Build and validate a single-check graph."""
    graph = Graph((Node("verify", "agent", operation=operation),
                   Node("gate", "gate", ("verify",), "all_terminal")))
    validate(graph)
    return graph


def full_verification_policy() -> Graph:
    """集中定义完整验证模板，供任意入口复用。 Centralize the full-check template for all entry points."""
    graph = Graph((Node('test', 'agent', operation='go_test'),
                   Node('vet', 'agent', operation='go_vet'),
                   Node('format', 'agent', operation='go_fmt_check'),
                   Node('gate', 'gate', ('test', 'vet', 'format'), 'all_terminal')),
                  policy_version='full-verification-v1')
    validate(graph)
    return graph


def harness_policy() -> Graph:
    """检查是工具动作，不伪装成决策角色。 Checks are tool actions, not decision-making roles."""
    graph = Graph((Node('test', 'tool', operation='go_test'),
                   Node('vet', 'tool', operation='go_vet'),
                   Node('format', 'tool', operation='go_fmt_check'),
                   Node('gate', 'gate', ('test', 'vet', 'format'), 'all_terminal')),
                  policy_version='harness-checks-v1')
    validate(graph)
    return graph


def validate(graph: Graph) -> None:
    if not 1 <= len(graph.nodes) <= 24 or graph.version < 1:
        raise MasaError("invalid graph size/version")
    nodes = {n.id: n for n in graph.nodes}
    if len(nodes) != len(graph.nodes) or any(not n.id for n in graph.nodes):
        raise MasaError("node IDs must be nonempty and unique")
    gates = [n for n in graph.nodes if n.type == "gate"]
    if len(gates) != 1:
        raise MasaError("exactly one final gate is required")
    for n in graph.nodes:
        if n.role not in {'verifier', 'planner', 'developer', 'tester', 'reviewer'}:
            raise MasaError('unknown agent role')
        if n.role != 'verifier' and n.type != 'agent':
            raise MasaError('roles require agent nodes')
        if n.type not in {"agent", "tool", "gate"} or n.operation not in OPERATIONS:
            raise MasaError("unknown node type or operation")
        if n.trigger not in {"all_succeeded", "all_terminal"}:
            raise MasaError("unknown dependency trigger")
        if len(set(n.dependencies)) != len(n.dependencies):
            raise MasaError("duplicate dependency")
        if any(d not in nodes or d == n.id for d in n.dependencies):
            raise MasaError("unknown or self dependency")
        if n.role != 'verifier' and any(nodes[d].role not in {'planner', 'developer', 'reviewer'} for d in n.dependencies):
            raise MasaError('role handoff edges must originate from read-only role results')
        if n.type != "gate" and n.trigger != "all_succeeded":
            raise MasaError("only gate nodes can consume failed dependencies")
    ordered: set[str] = set()
    while len(ordered) < len(nodes):
        ready = [n.id for n in graph.nodes if n.id not in ordered
                 and set(n.dependencies) <= ordered]
        if not ready:
            raise MasaError("workflow contains a cycle")
        ordered.update(ready)
    gate = gates[0]
    ancestors: set[str] = set()
    frontier = list(gate.dependencies)
    while frontier:
        item = frontier.pop()
        if item not in ancestors:
            ancestors.add(item)
            frontier.extend(nodes[item].dependencies)
    if gate.trigger != "all_terminal" or ancestors != set(nodes) - {gate.id}:
        raise MasaError("final gate must cover all other nodes")
    if not any(n.type == 'tool' or n.type == 'agent' and n.role in {'tester', 'verifier'} for n in graph.nodes):
        raise MasaError('a graph must include real tool verification')


def collaboration_policy(operation='go_test') -> Graph:
    """只读四角色协议演示，Tester 执行真实工具。 Read-only role protocol demo with actual Tester tools."""
    graph = Graph((Node('planner', 'agent', role='planner'),
                   Node('developer', 'agent', ('planner',), role='developer'),
                   Node('tester', 'agent', ('developer',), operation=operation, role='tester'),
                   Node('reviewer', 'agent', ('developer',), role='reviewer'),
                   Node('gate', 'gate', ('tester', 'reviewer'), 'all_terminal')),
                  policy_version='readonly-collaboration-v1')
    validate(graph)
    return graph


def ready_nodes(graph: Graph, states: dict[str, str]) -> list[Node]:
    return [n for n in graph.nodes if states[n.id] == "pending"
            and all(states[d] in (TERMINAL if n.trigger == "all_terminal"
                                  else {"succeeded"}) for d in n.dependencies)]
