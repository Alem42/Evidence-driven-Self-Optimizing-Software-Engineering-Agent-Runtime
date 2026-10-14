import { describe, expect, it } from 'vitest';
import type { BenchTask } from '../api/bench-types';
import { pickExamples, sample, SUGGESTION_POOL } from './examples';

const task = (id: string, level: number, goal = `goal ${id}`): BenchTask => ({ id, level, title: id, goal, cases: 1, probes: [], max_seconds: 1, max_cloud_tokens: 1 });
const seq = (...values: number[]) => { let i = 0; return () => values[i++ % values.length]; };

describe('the suggestion pool', () => {
  it('is rich: several kinds, vague requirements included, no duplicates', () => {
    expect(SUGGESTION_POOL.length).toBeGreaterThanOrEqual(20);
    expect(new Set(SUGGESTION_POOL.map((e) => e.kind))).toEqual(new Set(['clear', 'vague', 'data', 'logic', 'multi']));
    expect(SUGGESTION_POOL.filter((e) => e.kind === 'vague').length).toBeGreaterThanOrEqual(5);
    expect(new Set(SUGGESTION_POOL.map((e) => e.text)).size).toBe(SUGGESTION_POOL.length);
    expect(SUGGESTION_POOL.filter((e) => e.kind === 'vague').every((e) => e.level === null)).toBe(true);
  });
});

describe('pickExamples', () => {
  const tasks = [task('a', 0), task('b', 2), task('c', 3), task('d', 5), task('e', 6), task('f', 9)];

  it('always mixes in one vague requirement and prefers three different kinds', () => {
    for (let i = 0; i < 40; i++) {
      const picks = pickExamples(tasks, [], 3);
      expect(picks).toHaveLength(3);
      expect(picks.filter((p) => p.kind === 'vague')).toHaveLength(1);
      expect(new Set(picks.map((p) => p.kind)).size).toBe(3);
      expect(new Set(picks.map((p) => p.text)).size).toBe(3);
    }
  });

  it('refreshing avoids the previous batch', () => {
    for (let i = 0; i < 40; i++) {
      const first = pickExamples(tasks, [], 3);
      const next = pickExamples(tasks, [], 3, 5, Math.random, first.map((p) => p.text));
      expect(next.some((p) => first.some((q) => q.text === p.text))).toBe(false);
    }
  });

  it('draws benchmark tasks only up to the maximum level and ignores empty goals', () => {
    const seen = new Set<string>();
    for (let i = 0; i < 300; i++) pickExamples([...tasks, task('g', 1, '  ')], [], 3, 3).forEach((p) => seen.add(p.text));
    expect(seen.has('goal a')).toBe(true);
    expect(seen.has('goal d')).toBe(false); // L5 > 3
    expect(seen.has('goal f')).toBe(false);
    expect(seen.has('  ')).toBe(false);
  });

  it('is deterministic with an injected random source and works before the benchmark loads', () => {
    expect(pickExamples(tasks, [], 3, 5, seq(0, 0, 0)).map((p) => p.text)).toEqual(pickExamples(tasks, [], 3, 5, seq(0, 0, 0)).map((p) => p.text));
    expect(pickExamples(undefined, ['fallback goal'], 3)).toHaveLength(3);
  });
});

describe('sample', () => {
  it('draws without replacement and stops at the pool size', () => {
    expect(sample([1, 2, 3], 5)).toHaveLength(3);
    expect(new Set(sample([1, 2, 3, 4], 4)).size).toBe(4);
  });
});
