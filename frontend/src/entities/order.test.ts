import { describe, expect, it } from 'vitest';
import { moveRow, normalizeLevels } from './order';

const rows = (...pairs: [string, number][]) => pairs.map(([id, level]) => ({ id, level }));

describe('model order', () => {
  it('forces levels to be non-decreasing down the list', () => {
    expect(normalizeLevels(rows(['a', 2], ['b', 1], ['c', 3])).map((r) => r.level)).toEqual([2, 2, 3]);
  });
  it('a row dragged between others takes its upper neighbour level', () => {
    const moved = moveRow(rows(['l1', 1], ['l2', 1], ['api', 2]), 2, 1);
    expect(moved.map((r) => r.id)).toEqual(['l1', 'api', 'l2']);
    expect(moved.map((r) => r.level)).toEqual([1, 1, 1]); // API 被拖进本地之间，成为同级 / becomes a sibling
  });
  it('dragging to the top follows the row below it', () => {
    const moved = moveRow(rows(['l1', 1], ['api', 2]), 1, 0);
    expect(moved.map((r) => r.id)).toEqual(['api', 'l1']);
    expect(moved.map((r) => r.level)).toEqual([1, 1]);
  });
  it('ignores out-of-range or no-op moves', () => {
    const start = rows(['a', 1], ['b', 2]);
    expect(moveRow(start, 0, 0)).toBe(start);
    expect(moveRow(start, 0, 5)).toBe(start);
    expect(moveRow(start, -1, 1)).toBe(start);
  });
});
