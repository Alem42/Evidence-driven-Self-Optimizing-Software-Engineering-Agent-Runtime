// 报告展示用的纯函数：格式化、结论文案、复制文本。 Pure helpers for the report views.
import type { TaskReport } from '../api/types';

/** 12345 → "12.3k"；未知用 "—"（未知不是 0）。 Unknown stays "—", never 0. */
export function fmtTokens(n: number | null | undefined): string {
  if (n == null) return '—';
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(2) + 'M';
  if (n >= 10_000) return (n / 1000).toFixed(1) + 'k';
  return String(n);
}

export function fmtDuration(ms: number | null | undefined): string {
  if (ms == null) return '—';
  if (ms < 1000) return Math.round(ms) + ' ms';
  const s = ms / 1000;
  if (s < 60) return s.toFixed(1) + ' 秒';
  return `${Math.floor(s / 60)} 分 ${Math.round(s % 60)} 秒`;
}

export const OUTCOME: Record<TaskReport['outcome'], { label: string; tone: 'ok' | 'bad' | 'warn' | 'wait' | 'running'; text: string }> = {
  succeeded: { label: '任务完成', tone: 'ok', text: 'Gate 已通过所选真实检查。' },
  failed: { label: '任务未完成', tone: 'bad', text: '真实检查未通过或流程中止；失败证据保留在版本记录中。' },
  cancelled: { label: '任务已取消', tone: 'warn', text: '已有代码与证据保留。' },
  waiting: { label: '等待你处理', tone: 'wait', text: '当前停在需要你确认或回答的位置。' },
  running: { label: '执行中', tone: 'running', text: '任务仍在进行。' },
};

/** 终态才值得弹出报告；等待与执行中不弹。 Only terminal outcomes pop up. */
export const isTerminal = (o: TaskReport['outcome']): boolean => o === 'succeeded' || o === 'failed' || o === 'cancelled';

export const kindLabel = (k: string): string => (k === 'local' ? '本地' : k === 'cloud' ? 'API' : '未知');

/** 复制为纯文本，便于贴进 issue 或笔记。 Plain-text copy of the report. */
export function reportText(r: TaskReport, stepName: (s: string | null) => string): string {
  const t = r.totals;
  const lines = [
    `${OUTCOME[r.outcome].label} · ${r.title.split('\n')[0].slice(0, 60)}`,
    `总耗时 ${r.wall_seconds != null ? fmtDuration(r.wall_seconds * 1000) : '—'}（模型 ${fmtDuration(t.model_ms)}）`,
    `Token 总计 ${t.total_tokens}（输入 ${t.prompt_tokens} / 输出 ${t.completion_tokens}）· 本地 ${t.local_tokens} · API ${t.cloud_tokens}` +
      (t.unknown_usage_calls ? ` · ${t.unknown_usage_calls} 次调用用量未知` : ''),
    '',
    '按模型：',
    ...r.by_model.map((m) => `  ${m.model} [${kindLabel(m.kind)}] ${m.calls} 次 · ${m.total_tokens} tok`),
    '',
    '逐次调用：',
    ...r.calls.map((c) => {
      const at = r.started_at && c.started ? '+' + fmtDuration((c.started - r.started_at) * 1000) : '';
      return `  ${at} ${stepName(c.step_id)} · ${c.model} · ${c.status} · ${c.total_tokens ?? '—'} tok · ${fmtDuration(c.duration_ms)}`;
    }),
    '',
    '工具调用：',
    ...r.tools.map((x) => `  ${x.operation} · ${x.status}${x.exit_code != null ? ' · 退出码 ' + x.exit_code : ''} · ${fmtDuration(x.duration_ms)}`),
  ];
  return lines.join('\n');
}

export const REASON_TEXT: Record<string, string> = {
  start_lowest_eligible: '从满足条件的最低等级开始',
  retry_same_level: '同级再试（自修）',
  escalate: '升级到更高等级',
  top_level: '已在最高等级',
  no_candidate: '没有可用模型',
  no_higher_level: '没有更高等级',
  escalation_limit: '升级次数到顶',
  budget_calls: '调用次数预算用完',
  budget_time: '运行时间预算用完',
  budget_cloud_tokens: 'API token 预算用完',
  budget_cost: '费用预算用完',
  price_unknown: '缺少价格，无法保证费用上限',
  context: '输入超出模型上下文',
};

export const CHAIN_TEXT: Record<string, string> = { planning: '规划', generation: '生成', fix: '修复' };
