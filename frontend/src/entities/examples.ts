import type { BenchTask } from '../api/bench-types';

/** 推荐任务的类型：clear = 写清楚的；vague = 故意写得模糊（系统要自己问清楚或选常见约定）；data = 文本/数据处理；logic = 小游戏/模拟/状态机；multi = 多个包协作。 */
export type SuggestionKind = 'clear' | 'vague' | 'data' | 'logic' | 'multi';
export interface Example { text: string; level: number | null; kind: SuggestionKind }

export const KIND_LABEL: Record<SuggestionKind, string> = { clear: '清晰', vague: '模糊需求', data: '数据处理', logic: '小游戏/模拟', multi: '多包协作' };

/** 随机取 n 个：不放回，调用方传入随机源便于测试。 Pick n at random without replacement; the random source is injectable for tests. */
export function sample<T>(pool: T[], n: number, rng: () => number = Math.random): T[] {
  const rest = [...pool];
  const out: T[] = [];
  while (out.length < n && rest.length) out.push(rest.splice(Math.floor(rng() * rest.length), 1)[0]);
  return out;
}

/**
 * 内置的推荐任务库。模糊需求是故意的：真实用户很少把边界写清楚，系统要么先问，要么选常见约定并写进验收。
 * The built-in suggestion pool. The vague ones are deliberate: real users rarely spell out the edges, so the system either asks first or picks the common convention and states it in the acceptance criteria.
 */
export const SUGGESTION_POOL: Example[] = [
  // 模糊需求 / vague
  { kind: 'vague', level: null, text: '帮我做个记账的小工具。' },
  { kind: 'vague', level: null, text: '写个程序把一堆日志整理一下，方便我看出哪里出了问题。' },
  { kind: 'vague', level: null, text: '做一个命令行的待办事项管理，能加、能删、能看。' },
  { kind: 'vague', level: null, text: '我想要一个统计成绩的东西，老师用的。' },
  { kind: 'vague', level: null, text: '写一个猜数字的小游戏，在终端里玩。' },
  { kind: 'vague', level: null, text: '帮我把一些文本文件里重复的行去掉。' },
  { kind: 'vague', level: null, text: '做个简单的单位换算工具。' },
  // 清晰 / clear
  { kind: 'clear', level: 1, text: '写一个 CLI：把所有命令行参数用单个空格连接后输出一行。' },
  { kind: 'clear', level: 2, text: '写一个 CLI：输出 1 到 N 的 FizzBuzz，每行一项；N 来自第一个命令行参数，缺省为 15。' },
  { kind: 'clear', level: 3, text: '实现一个括号匹配检查 CLI：读取标准输入的每一行，输出该行括号是否匹配（yes 或 no）。' },
  { kind: 'clear', level: 3, text: '写一个 CLI：计算两个日期（YYYY-MM-DD）相差的天数，参数无效时输出错误并以非零退出码结束。' },
  { kind: 'clear', level: 4, text: '写一个 CLI：把十进制整数参数转换为罗马数字（1 到 3999），超出范围输出 error。' },
  { kind: 'clear', level: 5, text: '写一个 CLI：读取标准输入的前两行字符串，输出它们最长公共子序列的长度。' },
  // 数据处理 / data
  { kind: 'data', level: 3, text: '写一个 CLI：读取标准输入的 CSV（第一行是表头），按第一列分组，对第二列求和，按总和降序输出。' },
  { kind: 'data', level: 3, text: '写一个 CLI：统计标准输入中每个单词出现的次数（忽略大小写），输出出现最多的前 5 个。' },
  { kind: 'data', level: 4, text: '写一个 CLI：读取标准输入的 JSON 对象，把嵌套的键展平成 a.b.c 的形式，每行输出“键=值”。' },
  { kind: 'data', level: 4, text: '写一个 CLI：读取标准输入的若干行时间区间“开始 结束”，合并重叠的区间后输出。' },
  { kind: 'data', level: 2, text: '写一个 CLI：读取标准输入的数字（空白分隔），输出最小值、最大值、平均值和中位数。' },
  // 小游戏 / 模拟 / 状态机
  { kind: 'logic', level: 5, text: '实现一个 LRU 缓存 CLI：标准输入第一行是容量，之后每行 get 或 put 命令，输出 get 的结果。' },
  { kind: 'logic', level: 6, text: '写一个 CLI：在迷宫（# 墙、. 路、S 起点、E 终点）里找最短路径长度，不可达输出 -1。' },
  { kind: 'logic', level: 7, text: '实现一个带事务的内存键值存储 CLI：SET/GET/DELETE/BEGIN/COMMIT/ROLLBACK，逐行读取命令。' },
  { kind: 'logic', level: 4, text: '实现一个交通灯状态机 CLI：读取一串“tick”事件，输出每一步的灯色，红 3 拍、绿 3 拍、黄 1 拍循环。' },
  { kind: 'logic', level: 6, text: '实现一个 9×9 数独求解 CLI：标准输入 9 行，. 表示空格，输出解；无解输出 no solution。' },
  // 多包协作 / multi
  { kind: 'multi', level: 10, text: '写一个银行账本命令行程序：open/deposit/withdraw/transfer/balance/report 六种命令，每种错误有固定的检查顺序；拆成解析、存储、转账规则、报表几个独立的包，每个包各带单元测试。' },
  { kind: 'multi', level: 10, text: '写一个库存管理命令行程序：add/remove/price/stock/value/low 命令；数字检查先于是否存在检查；拆成解析、存储、报表几个包，各带单元测试。' },
  { kind: 'multi', level: 10, text: '写一个成绩管理命令行程序：student/credits/grade/gpa/top 命令，绩点按学分加权、四舍五入到两位且恰好在中间时向上进位；拆成解析、存储、绩点计算几个包。' },
];

/**
 * 新任务页的三个推荐：尽量取三个不同的类型（至少混进一个模糊需求），每次“换一批”避开上一批；评测题库加载出来后，低等级的题也进候选池。
 * The three suggestions on the new-task page: three different kinds where possible (one vague requirement is always mixed in), "refresh" avoids the previous batch; the low-level benchmark tasks join the pool once loaded.
 */
export function pickExamples(tasks: BenchTask[] | undefined, extra: string[] = [], n = 3, maxLevel = 5, rng: () => number = Math.random, avoid: string[] = []): Example[] {
  const benchmark: Example[] = (tasks ?? []).filter((t) => t.level <= maxLevel && t.goal.trim()).map((t) => ({ text: t.goal, level: t.level, kind: 'clear' as const }));
  const pool = [...SUGGESTION_POOL, ...benchmark, ...extra.map((text) => ({ text, level: null, kind: 'clear' as const }))];
  const fresh = pool.filter((e) => !avoid.includes(e.text));
  const candidates = fresh.length >= n ? fresh : pool;
  const picked: Example[] = [];
  const vague = candidates.filter((e) => e.kind === 'vague');
  if (vague.length && n >= 2) picked.push(...sample(vague, 1, rng));
  const kinds = new Set(picked.map((e) => e.kind));
  const order = sample(candidates.filter((e) => !picked.includes(e)), candidates.length, rng);
  for (const e of order) {
    if (picked.length >= n) break;
    if (!kinds.has(e.kind)) { picked.push(e); kinds.add(e.kind); }
  }
  for (const e of order) {
    if (picked.length >= n) break;
    if (!picked.includes(e)) picked.push(e);
  }
  return picked.slice(0, n);
}
