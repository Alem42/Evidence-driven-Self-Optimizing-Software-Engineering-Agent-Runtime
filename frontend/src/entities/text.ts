// 文本工具：Go 输出可读化、失败线索、`file:line` 诊断解析、行级 diff。
// Text helpers: readable Go output, failure hints, `file:line` diagnostics and a line diff.
import type { SourceFinding } from '../api/types';

/** 将 Go JSON 测试输出转成人类可读日志，其他工具原文保留。 */
export function readableOutput(value = ''): string {
  return value
    .split('\n')
    .map((line) => {
      try {
        const item = JSON.parse(line);
        return typeof item.Output === 'string' ? item.Output.trimEnd() : item.Action && item.Package ? '' : line;
      } catch {
        return line;
      }
    })
    .filter(Boolean)
    .join('\n');
}

export function failureSummary(result?: { stdout?: string; stderr?: string } | null): string {
  const lines = (readableOutput(result?.stdout || '') + '\n' + (result?.stderr || '')).split('\n').map((s) => s.trim()).filter(Boolean);
  const matched = lines.filter((s) => /FAIL|error|import cycle|imported and not used|undefined|want |exit code|failed|panic/i.test(s));
  return (matched.length ? matched : lines.slice(-4)).slice(0, 8).join('\n');
}

/** 从编译/测试输出中提取 `path.go:12:3: message`，用于编辑器标注与跳转。 */
export function parseDiagnostics(text: string): SourceFinding[] {
  const found: SourceFinding[] = [];
  const re = /^\s*(?:\.\/)?([\w./\\-]+\.go):(\d+)(?::\d+)?:?\s*(.*)$/;
  for (const line of text.split('\n')) {
    const m = re.exec(line);
    if (m) found.push({ path: m[1].replace(/\\/g, '/'), line: Number(m[2]), message: m[3] || '问题' });
  }
  return found.slice(0, 50);
}

export type DiffRow = { kind: 'same' | 'add' | 'del'; text: string };

/** 经典 LCS 行级 diff（文件很小，O(n·m) 足够）。 */
export function diffLines(a: string, b: string): DiffRow[] {
  const x = a.split('\n');
  const y = b.split('\n');
  const dp: number[][] = Array.from({ length: x.length + 1 }, () => new Array(y.length + 1).fill(0));
  for (let i = x.length - 1; i >= 0; i--)
    for (let j = y.length - 1; j >= 0; j--) dp[i][j] = x[i] === y[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
  const rows: DiffRow[] = [];
  let i = 0;
  let j = 0;
  while (i < x.length && j < y.length) {
    if (x[i] === y[j]) rows.push({ kind: 'same', text: x[i++] }), j++;
    else if (dp[i + 1][j] >= dp[i][j + 1]) rows.push({ kind: 'del', text: x[i++] });
    else rows.push({ kind: 'add', text: y[j++] });
  }
  while (i < x.length) rows.push({ kind: 'del', text: x[i++] });
  while (j < y.length) rows.push({ kind: 'add', text: y[j++] });
  return rows;
}

export const fmtSeconds = (s: number): string => (s < 60 ? `${Math.floor(s)} 秒` : `${Math.floor(s / 60)} 分 ${Math.floor(s % 60)} 秒`);
export const gib = (n?: number | null): string => (typeof n === 'number' ? (n / 1024 ** 3).toFixed(2) + ' GiB' : '未知');
export const nsToSeconds = (n?: number | null): string => (typeof n === 'number' ? (n / 1e9).toFixed(2) + ' s' : '未知');
