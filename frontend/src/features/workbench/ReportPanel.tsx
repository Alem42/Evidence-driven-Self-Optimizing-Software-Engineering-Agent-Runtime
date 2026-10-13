import { useReport } from '../../api/queries';
import { fmtDuration, fmtTokens, OUTCOME } from '../../entities/report';
import { stageLabels } from '../../entities/status';
import { Badge, Button, Spinner } from '../../shared/ui';
import { useUi } from '../../stores/ui';
import { fmtRate } from './Chips';

// 报告摘要：用量、耗时、缓存命中、花费；完整报告（逐次调用、路由决策时间线）在弹窗里。
// Report summary: usage, time, cache hits and cost; the full report (every call, the routing timeline) is in the dialog.
export function ReportPanel({ runId, live }: { runId: string; live: boolean }) {
  const report = useReport(runId, live);
  const set = useUi((s) => s.set);
  if (report.isLoading || !report.data) return <div className="center pad"><Spinner /> 汇总中…</div>;
  const r = report.data;
  const t = r.totals;
  const outcome = OUTCOME[r.outcome];
  const cost = r.routing?.spend.cost_known ? r.routing.spend.cost : null;

  return (
    <div className="report-panel">
      <div className="row between">
        <span className="row"><Badge tone={outcome.tone} dot>{outcome.label}</Badge><span className="muted">{outcome.text}</span></span>
        <Button size="sm" onClick={() => set({ reportFor: runId })}>打开完整报告</Button>
      </div>
      <div className="stat-grid">
        <div className="stat"><small>模型调用</small><strong>{t.calls}</strong><span>失败 {t.failed_calls}</span></div>
        <div className="stat"><small>Token</small><strong>{fmtTokens(t.total_tokens)}</strong><span>输入 {fmtTokens(t.prompt_tokens)} · 输出 {fmtTokens(t.completion_tokens)}</span></div>
        <div className="stat"><small>提示缓存命中</small><strong>{fmtRate(t.cache_hit_rate)}</strong><span>{t.cache_hit_rate != null ? `命中 ${fmtTokens(t.cache_hit_tokens)} · 未命中 ${fmtTokens(t.cache_miss_tokens)}` : '服务端没有报告'}</span></div>
        <div className="stat"><small>耗时</small><strong>{r.wall_seconds != null ? fmtDuration(r.wall_seconds * 1000) : '—'}</strong><span>模型 {fmtDuration(t.model_ms)}</span></div>
        <div className="stat"><small>花费（估算）</small><strong>{cost != null ? '¥' + cost.toFixed(4) : '—'}</strong><span>本地 {fmtTokens(t.local_tokens)} · API {fmtTokens(t.cloud_tokens)}</span></div>
      </div>
      <div className="table-wrap">
        <table>
          <thead><tr><th>角色</th><th className="num">调用</th><th className="num">输入</th><th className="num">输出</th><th className="num">模型耗时</th></tr></thead>
          <tbody>
            {r.by_step.map((s) => (
              <tr key={s.step_id ?? '?'}>
                <td>{s.step_id ? stageLabels[s.step_id] ?? s.step_id : '—'}</td>
                <td className="num">{s.calls}{s.failed ? ` (${s.failed} 失败)` : ''}</td>
                <td className="num">{fmtTokens(s.prompt_tokens)}</td>
                <td className="num">{fmtTokens(s.completion_tokens)}</td>
                <td className="num">{fmtDuration(s.model_ms)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
