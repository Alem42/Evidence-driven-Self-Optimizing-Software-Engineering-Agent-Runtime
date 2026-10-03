// 图渲染：自有 SVG 连线 + 绝对定位节点，支持任意 DAG（由 entities/graph.layout 分层）。
// compact=顶部紧凑图条（横向滚动并自动对准当前节点）；否则为完整画布。
// Graph renderer over any DAG. compact = top strip that auto-scrolls to the focus node.
import { useEffect, useMemo, useRef } from 'react';
import { focusNode, layout, type Graph, type GraphNode } from '../../entities/graph';
import { statusText } from '../../entities/status';

const KIND_ICON: Record<string, string> = { role: '◆', human: '☺', tool: '⚙', gate: '⛨', router: '⇄', version: '⎇' };

interface Props {
  graph: Graph;
  selected?: string | null;
  onSelect?: (n: GraphNode) => void;
  compact?: boolean;
}

export function GraphView({ graph, selected, onSelect, compact }: Props) {
  const W = compact ? 148 : 176;
  const H = compact ? 44 : 56;
  const GX = compact ? 28 : 56;
  const GY = 14;
  const placed = useMemo(() => layout(graph), [graph]);
  const pos = new Map(placed.map((n) => [n.id, { x: n.col * (W + GX), y: n.row * (H + GY) }]));
  const width = Math.max(0, ...placed.map((n) => n.col + 1)) * (W + GX) - GX;
  const height = Math.max(0, ...placed.map((n) => n.row + 1)) * (H + GY) - GY;
  const focus = focusNode(graph.nodes)?.id;
  const scroller = useRef<HTMLDivElement>(null);

  // 自动把执行焦点带入视野，但不抢用户选中的内容。 Bring the focus node into view.
  useEffect(() => {
    const el = scroller.current?.querySelector<HTMLElement>('[data-focus="true"]');
    el?.scrollIntoView({ block: 'nearest', inline: 'center', behavior: 'smooth' });
  }, [focus, graph.nodes.length]);

  if (!placed.length) return null;
  return (
    <div className={`graph ${compact ? 'graph-compact' : ''}`} ref={scroller}>
      <div className="graph-canvas" style={{ width, height }}>
        <svg width={width} height={height} aria-hidden="true">
          {graph.edges.map((e, i) => {
            const a = pos.get(e.from);
            const b = pos.get(e.to);
            if (!a || !b) return null;
            const x1 = a.x + W;
            const y1 = a.y + H / 2;
            const x2 = b.x;
            const y2 = b.y + H / 2;
            const mid = (x1 + x2) / 2;
            return <path key={i} className={`edge edge-${e.kind ?? 'flow'}`} d={`M${x1},${y1} C${mid},${y1} ${mid},${y2} ${x2},${y2}`} />;
          })}
        </svg>
        {placed.map((n) => {
          const p = pos.get(n.id)!;
          return (
            <button
              key={n.id}
              data-focus={n.id === focus}
              className={`gnode s-${n.status} k-${n.kind} ${selected === n.id ? 'selected' : ''} ${n.id === focus ? 'focus' : ''}`}
              style={{ left: p.x, top: p.y, width: W, height: H }}
              onClick={() => onSelect?.(n)}
              title={`${n.label} · ${statusText[n.status] ?? n.status}`}
            >
              <span className="gicon">{KIND_ICON[n.kind]}</span>
              <span className="gtext">
                <strong>{n.label}</strong>
                <small>{n.sub ?? statusText[n.status] ?? n.status}</small>
              </span>
              <i className="gstate" />
            </button>
          );
        })}
      </div>
    </div>
  );
}
