// 模型次序的纯函数：与后端 Settings.reorder 的规则保持一致。
// Pure helpers for the model order page; they mirror the backend Settings.reorder rules.
export interface OrderRow {
  id: string;
  level: number;
}

/** 等级沿列表非递减：路由从低等级起、逐级升级，靠前的模型不能比靠后的等级更高。 */
export function normalizeLevels(rows: OrderRow[]): OrderRow[] {
  let previous = 1;
  return rows.map((r) => {
    previous = Math.max(r.level, previous);
    return { ...r, level: previous };
  });
}

/** 移动一行。被移动的行跟随它新位置上方邻居的等级（在最前面则跟随下方邻居），之后统一规范。 */
export function moveRow(rows: OrderRow[], from: number, to: number): OrderRow[] {
  if (from === to || from < 0 || to < 0 || from >= rows.length || to >= rows.length) return rows;
  const next = [...rows];
  const [moved] = next.splice(from, 1);
  next.splice(to, 0, moved);
  const neighbour = next[to - 1] ?? next[to + 1];
  next[to] = { ...moved, level: neighbour ? neighbour.level : moved.level };
  return normalizeLevels(next);
}
