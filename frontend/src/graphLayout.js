// 按真实依赖分层；同层节点的排列不意味着并行执行。
// Layer by actual dependencies; a shared layer does not imply parallel execution.
export function layoutGraph(nodes) {
  const layers = new Map();
  for (let pass = 0; pass < nodes.length; pass++) {
    for (const node of nodes) {
      if (!layers.has(node.id) && node.dependencies.every(id => layers.has(id))) {
        layers.set(node.id, node.dependencies.length
          ? 1 + Math.max(...node.dependencies.map(id => layers.get(id))) : 0);
      }
    }
  }
  const counts = new Map(), positions = {};
  for (const node of nodes) {
    const layer = layers.get(node.id) ?? 0;
    const row = counts.get(layer) ?? 0;
    positions[node.id] = {x: 40 + layer * 280, y: 35 + row * 125};
    counts.set(layer, row + 1);
  }
  return {positions, width: Math.max(600, (Math.max(0, ...counts.keys()) + 1) * 280 + 40),
    height: Math.max(220, Math.max(0, ...counts.values()) * 125 + 70)};
}
