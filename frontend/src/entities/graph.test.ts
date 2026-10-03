import { describe, expect, it } from 'vitest';
import { focusNode, layout, versionGraph } from './graph';

describe('graph', () => {
  it('layers a branching DAG by longest path', () => {
    const placed = layout({
      nodes: ['a', 'b', 'c', 'd'].map((id) => ({ id, label: id, kind: 'role' as const, status: 'pending' })),
      edges: [
        { from: 'a', to: 'b' },
        { from: 'a', to: 'c' },
        { from: 'b', to: 'd' },
        { from: 'c', to: 'd' },
      ],
    });
    const col = Object.fromEntries(placed.map((n) => [n.id, n.col]));
    expect(col).toEqual({ a: 0, b: 1, c: 1, d: 2 });
    expect(placed.find((n) => n.id === 'c')!.row).toBe(1);
  });
  it('does not loop on cycles', () => {
    expect(() =>
      layout({ nodes: [{ id: 'x', label: 'x', kind: 'role', status: '' }, { id: 'y', label: 'y', kind: 'role', status: '' }], edges: [{ from: 'x', to: 'y' }, { from: 'y', to: 'x' }] }),
    ).not.toThrow();
  });
  it('repairs fork in the version graph', () => {
    const g = versionGraph({
      id: 'p', title: 't', selected_run_id: 'c',
      stages: [],
      versions: [
        { run_id: 'a', status: 'failed', kind: 'verification' },
        { run_id: 'b', status: 'paused', kind: 'code', parent_run_id: 'a' },
        { run_id: 'c', status: 'paused', kind: 'code', parent_run_id: 'a' },
      ],
    });
    expect(g.edges).toHaveLength(2);
  });
  it('focuses running first', () => {
    expect(focusNode([{ id: '1', label: '', kind: 'role', status: 'succeeded' }, { id: '2', label: '', kind: 'role', status: 'running' }])?.id).toBe('2');
  });
});
