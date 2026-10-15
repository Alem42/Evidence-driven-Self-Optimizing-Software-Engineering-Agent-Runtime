// 文件树：把路径列表变成嵌套目录。像 VS Code 一样把只有一个子目录的目录链合并成一行（internal/stats）。
// File tree: turns a path list into nested folders; single-child folder chains are merged into one row like VS Code does.
import { isTestPath } from './languages';

export interface TreeNode {
  /** 显示名；合并后的目录是 "internal/stats"。 Display name; a merged folder is "internal/stats". */
  name: string;
  /** 文件是完整路径；目录是它的路径（不带结尾斜杠）。 Full path for a file; the folder path (no trailing slash) for a folder. */
  path: string;
  dir: boolean;
  children: TreeNode[];
}

interface Raw {
  name: string;
  path: string;
  dir: boolean;
  children: Map<string, Raw>;
}

const order = (a: TreeNode, b: TreeNode): number =>
  Number(b.dir) - Number(a.dir) || Number(isTestPath(a.path)) - Number(isTestPath(b.path)) || a.name.localeCompare(b.name);

function freeze(raw: Raw): TreeNode {
  let node = raw;
  let name = raw.name;
  // 目录只有一个目录子项：合并显示。 A folder whose only child is a folder: merge the two.
  while (node.dir && node.children.size === 1) {
    const only = [...node.children.values()][0];
    if (!only.dir) break;
    name += '/' + only.name;
    node = only;
  }
  return { name, path: node.path, dir: node.dir, children: [...node.children.values()].map(freeze).sort(order) };
}

export function buildTree(paths: string[]): TreeNode[] {
  const root: Raw = { name: '', path: '', dir: true, children: new Map() };
  for (const full of paths) {
    const parts = full.split('/').filter(Boolean);
    let at = root;
    parts.forEach((part, index) => {
      const last = index === parts.length - 1;
      const path = parts.slice(0, index + 1).join('/');
      if (!at.children.has(part)) at.children.set(part, { name: part, path, dir: !last, children: new Map() });
      at = at.children.get(part)!;
    });
  }
  return [...root.children.values()].map(freeze).sort(order);
}

/** 一个文件之上的所有目录路径（合并后的目录也算，用于默认展开到它）。 Every folder path above a file. */
export function foldersAbove(path: string): string[] {
  const parts = path.split('/');
  return parts.slice(0, -1).map((_, i) => parts.slice(0, i + 1).join('/'));
}
