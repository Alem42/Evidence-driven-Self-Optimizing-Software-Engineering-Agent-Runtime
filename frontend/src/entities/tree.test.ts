import { describe, expect, it } from 'vitest';
import { buildTree, foldersAbove } from './tree';

const shape = (nodes: ReturnType<typeof buildTree>): unknown => nodes.map((n) => (n.dir ? { [n.name]: shape(n.children) } : n.name));

describe('buildTree', () => {
  it('nests folders, lists folders first and tests after the code they test', () => {
    const tree = buildTree(['go.mod', 'internal/wc/wc_test.go', 'internal/wc/wc.go', 'cmd/app/main.go', 'internal/stats/stats.go']);
    expect(shape(tree)).toEqual([{ 'cmd/app': ['main.go'] }, { internal: [{ stats: ['stats.go'] }, { wc: ['wc.go', 'wc_test.go'] }] }, 'go.mod']);
  });

  it('merges a chain of single-child folders into one row but keeps the real folder path', () => {
    const [node] = buildTree(['cmd/app/main.go']);
    expect(node).toMatchObject({ name: 'cmd/app', path: 'cmd/app', dir: true });
    expect(node.children[0]).toMatchObject({ name: 'main.go', path: 'cmd/app/main.go', dir: false });
  });

  it('does not merge a folder that also holds a file', () => {
    const tree = buildTree(['pkg/a.py', 'pkg/sub/b.py']);
    expect(shape(tree)).toEqual([{ pkg: [{ sub: ['b.py'] }, 'a.py'] }]);
  });

  it('handles an empty list and duplicate paths', () => {
    expect(buildTree([])).toEqual([]);
    expect(shape(buildTree(['a.go', 'a.go']))).toEqual(['a.go']);
  });
});

describe('foldersAbove', () => {
  it('lists every folder above a file', () => {
    expect(foldersAbove('internal/stats/stats.go')).toEqual(['internal', 'internal/stats']);
    expect(foldersAbove('go.mod')).toEqual([]);
  });
});
