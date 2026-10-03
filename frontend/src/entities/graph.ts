// 通用图模型：节点/边是后端事实的投影；后端以后产出真正的动态图时，只需替换这里的适配器。
// Generic graph model. Today it projects stages/versions; a real dynamic backend graph only needs a new adapter.
import type { ProjectView, Stage, Version } from '../api/types';

export type NodeKind = 'role' | 'human' | 'tool' | 'gate' | 'router' | 'version';

export interface GraphNode {
  id: string;
  label: string;
  kind: NodeKind;
  status: string;
  runId?: string;
  sub?: string; // 次级文字（模型、耗时等）secondary text
}

export interface GraphEdge {
  from: string;
  to: string;
  kind?: 'flow' | 'derive' | 'escalate';
}

export interface Graph {
  nodes: GraphNode[];
  edges: GraphEdge[];
}

export interface Placed extends GraphNode {
  col: number;
  row: number;
}

const kindOf = (s: Stage): NodeKind => (s.kind === 'human' ? 'human' : s.kind === 'gate' ? 'gate' : s.role === 'executor' ? 'tool' : 'role');

/** 当前版本的执行流：按顺序串联，未来阶段保持 pending。 The execution flow of the selected version. */
export function stageGraph(view: ProjectView): Graph {
  const nodes: GraphNode[] = view.stages.map((s) => ({ id: s.id, label: s.label, kind: kindOf(s), status: s.status, runId: s.run_id, sub: s.reused ? '复用' : undefined }));
  const edges: GraphEdge[] = nodes.slice(1).map((n, i) => ({ from: nodes[i].id, to: n.id, kind: 'flow' }));
  return { nodes, edges };
}

const versionLabel: Record<Version['kind'], string> = { plan: '方案', code: '代码草稿', verification: '工具验证' };

/** 版本树：parent_run_id 形成分支，修复/测试修订在这里自然成为分叉。 Version tree; repairs fork naturally. */
export function versionGraph(view: ProjectView): Graph {
  const ids = new Set(view.versions.map((v) => v.run_id));
  const nodes: GraphNode[] = view.versions.map((v) => ({ id: 'v:' + v.run_id, label: versionLabel[v.kind], kind: 'version', status: v.status, runId: v.run_id, sub: v.run_id.slice(0, 8) }));
  const edges: GraphEdge[] = view.versions.filter((v) => v.parent_run_id && ids.has(v.parent_run_id)).map((v) => ({ from: 'v:' + v.parent_run_id, to: 'v:' + v.run_id, kind: 'derive' }));
  return { nodes, edges };
}

/** 最长路径分层 + 同层顺序排布；支持任意 DAG（含并行与分叉）。 Longest-path layering; handles any DAG. */
export function layout(graph: Graph): Placed[] {
  const incoming = new Map<string, string[]>();
  graph.nodes.forEach((n) => incoming.set(n.id, []));
  graph.edges.forEach((e) => incoming.get(e.to)?.push(e.from));
  const depth = new Map<string, number>();
  const visit = (id: string, stack: Set<string>): number => {
    if (depth.has(id)) return depth.get(id)!;
    if (stack.has(id)) return 0; // 防环 guard against cycles
    stack.add(id);
    const d = Math.max(-1, ...(incoming.get(id) ?? []).map((p) => visit(p, stack))) + 1;
    stack.delete(id);
    depth.set(id, d);
    return d;
  };
  graph.nodes.forEach((n) => visit(n.id, new Set()));
  const rows = new Map<number, number>();
  return graph.nodes.map((n) => {
    const col = depth.get(n.id) ?? 0;
    const row = rows.get(col) ?? 0;
    rows.set(col, row + 1);
    return { ...n, col, row };
  });
}

/** 当前应被关注的节点：执行中 > 等待/失败 > 最后成功。 The node to focus. */
export function focusNode(nodes: GraphNode[]): GraphNode | undefined {
  return (
    nodes.find((n) => n.status === 'running') ??
    [...nodes].reverse().find((n) => n.status === 'blocked' || n.status === 'failed') ??
    [...nodes].reverse().find((n) => n.status === 'succeeded')
  );
}
