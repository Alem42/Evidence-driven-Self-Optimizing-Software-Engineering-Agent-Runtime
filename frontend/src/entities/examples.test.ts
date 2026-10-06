import { describe, expect, it } from 'vitest';
import type { BenchTask } from '../api/bench-types';
import { pickExamples, sample } from './examples';

const task = (id: string, level: number, goal = `goal ${id}`): BenchTask => ({ id, level, title: id, goal, cases: 1, probes: [], max_seconds: 1, max_cloud_tokens: 1 });
const seq = (...values: number[]) => { let i = 0; return () => values[i++ % values.length]; };

describe('pickExamples', () => {
  const tasks = [task('a', 0), task('b', 2), task('c', 3), task('d', 5), task('e', 6), task('f', 9)];

  it('draws only low-level tasks from the benchmark set', () => {
    for (let i = 0; i < 20; i++) {
      const picks = pickExamples(tasks, ['x', 'y', 'z'], 3, 5);
      expect(picks).toHaveLength(3);
      expect(picks.every((p) => (p.level ?? 99) <= 5)).toBe(true);
      expect(new Set(picks.map((p) => p.text)).size).toBe(3);
    }
  });

  it('is deterministic with an injected random source', () => {
    expect(pickExamples(tasks, [], 3, 5, seq(0, 0, 0)).map((p) => p.text)).toEqual(['goal a', 'goal b', 'goal c']);
  });

  it('falls back to the built-in pool while the set is missing or too small', () => {
    expect(pickExamples(undefined, ['x', 'y', 'z', 'w']).every((p) => p.level === null)).toBe(true);
    expect(pickExamples([task('a', 0), task('b', 9)], ['x', 'y', 'z']).map((p) => p.text).sort()).toEqual(['x', 'y', 'z']);
  });

  it('ignores tasks with an empty goal', () => {
    expect(pickExamples([task('a', 0, '  '), task('b', 1), task('c', 2), task('d', 3)], ['x', 'y', 'z']).map((p) => p.level).sort()).toEqual([1, 2, 3]);
  });

  it('sample never repeats and never exceeds the pool', () => {
    expect(sample([1, 2], 5)).toHaveLength(2);
  });
});
