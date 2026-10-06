import type { BenchTask } from '../api/bench-types';

export interface Example { text: string; level: number | null }

/** 随机取 n 个：不放回，调用方传入随机源便于测试。 Pick n at random without replacement; the random source is injectable for tests. */
export function sample<T>(pool: T[], n: number, rng: () => number = Math.random): T[] {
  const rest = [...pool];
  const out: T[] = [];
  while (out.length < n && rest.length) out.push(rest.splice(Math.floor(rng() * rest.length), 1)[0]);
  return out;
}

/**
 * 新任务页的三个推荐：从评测题库里随机抽，只取低等级（默认 ≤ L5：太复杂的先不推荐）；题库没加载出来就用内置的备用题。
 * The three suggestions on the new-task page: random picks from the benchmark set, low levels only (default <= L5); built-in fallbacks until the set loads.
 */
export function pickExamples(tasks: BenchTask[] | undefined, fallback: string[], n = 3, maxLevel = 5, rng: () => number = Math.random): Example[] {
  const pool = (tasks ?? []).filter((t) => t.level <= maxLevel && t.goal.trim());
  if (pool.length >= n) return sample(pool, n, rng).map((t) => ({ text: t.goal, level: t.level }));
  return sample(fallback, n, rng).map((text) => ({ text, level: null }));
}
