import { useEffect, useMemo, useState, type ReactElement } from 'react';
import { buildTree, foldersAbove, type TreeNode } from '../../entities/tree';
import { FILE_STATE_LABEL, type FileState } from '../../entities/live';
import { isTestPath } from '../../entities/languages';

interface Props {
  paths: string[];
  selected?: string;
  state: (path: string) => FileState;
  /** 额外的小标记（冻结 / 已编辑）。 Extra small tags (frozen / edited). */
  tags: (path: string) => string[];
  onSelect: (path: string) => void;
}

// 文件树：像 VS Code 的资源管理器。状态点：写入中（跳动）、修改中、刚写完、本版改动；已封包的文件没有点。
// File tree like VS Code's explorer. State dots: writing (pulsing), editing, just written, changed in this version; sealed files have none.
export function FileTree({ paths, selected, state, tags, onSelect }: Props) {
  const tree = useMemo(() => buildTree(paths), [paths]);
  const [closed, setClosed] = useState<Set<string>>(new Set());

  // 选中的文件（例如正在写的文件）所在的目录自动展开。 Folders above the selected file open by themselves.
  useEffect(() => {
    if (!selected) return;
    const above = foldersAbove(selected);
    setClosed((prev) => (above.some((d) => prev.has(d)) ? new Set([...prev].filter((d) => !above.includes(d))) : prev));
  }, [selected]);

  const toggle = (path: string) => setClosed((prev) => { const next = new Set(prev); if (!next.delete(path)) next.add(path); return next; });

  const rows = (nodes: TreeNode[], depth: number): ReactElement[] =>
    nodes.flatMap((n) => {
      const indent = { paddingLeft: 8 + depth * 12 };
      if (n.dir) {
        const open = !closed.has(n.path);
        return [
          <button key={'d:' + n.path} className="tree-row dir" style={indent} onClick={() => toggle(n.path)} title={n.path}>
            <span className="caret">{open ? '▾' : '▸'}</span>
            <span className="tree-name">{n.name}</span>
          </button>,
          ...(open ? rows(n.children, depth + 1) : []),
        ];
      }
      const s = state(n.path);
      return [
        <button key={n.path} className={`tree-row file ${n.path === selected ? 'current' : ''} ${isTestPath(n.path) ? 'is-test' : ''}`} style={indent} onClick={() => onSelect(n.path)} title={n.path}>
          <span className="caret" />
          <span className="tree-name">{n.name}</span>
          {tags(n.path).map((t) => <i key={t} className="tag">{t}</i>)}
          {s !== 'sealed' && <i className={`fstate fs-${s}`} title={FILE_STATE_LABEL[s]} />}
        </button>,
      ];
    });

  if (!paths.length) return <div className="tree-empty">还没有文件<br /><small>生成开始后，文件会在这里出现</small></div>;
  return <nav className="file-tree" aria-label="项目文件">{rows(tree, 0)}</nav>;
}
