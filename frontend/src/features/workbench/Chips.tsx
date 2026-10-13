import type { TaskReport } from '../../api/types';
import { fmtTokens } from '../../entities/report';

/** 提示缓存命中率：服务端报告了才显示（没有报告就是未知，不画 0%）。 Prompt-cache hit rate; shown only when the server reported it (unknown is not 0%). */
export const fmtRate = (rate: number | null | undefined): string => (rate == null ? '—' : Math.round(rate * 100) + '%');

export function TokenChip({ totals, onClick }: { totals: TaskReport['totals']; onClick: () => void }) {
  return (
    <button className="token-chip" onClick={onClick} title="点击查看任务报告：按模型、按步骤、逐次调用">
      Σ {fmtTokens(totals.total_tokens)} tok
      {totals.local_tokens > 0 && <span className="tc-local">本地 {fmtTokens(totals.local_tokens)}</span>}
      {totals.cloud_tokens > 0 && <span className="tc-cloud">API {fmtTokens(totals.cloud_tokens)}</span>}
      {totals.cache_hit_rate != null && (
        <span className="tc-cache" title={`服务端报告的提示缓存：命中 ${fmtTokens(totals.cache_hit_tokens)} · 未命中 ${fmtTokens(totals.cache_miss_tokens)}`}>
          缓存 {fmtRate(totals.cache_hit_rate)}
        </span>
      )}
    </button>
  );
}

/** 预算条：API token 与调用次数的占用；接近上限变红。 Compact budget meters; they turn red near the limit. */
export function BudgetChips({ routing }: { routing: NonNullable<TaskReport['routing']> }) {
  const b = routing.budget;
  const items: [string, number, number | null, (n: number) => string][] = [
    ['API', routing.spend.cloud_tokens, b.max_cloud_tokens, fmtTokens],
    ['调用', routing.spend.calls, b.max_model_calls, String],
  ];
  return (
    <span className="budget-chips" title="任务级预算（含对未知用量请求的预留）；详情见任务报告">
      {items.filter(([, , limit]) => limit != null).map(([label, used, limit, fmt]) => {
        const pct = Math.min(100, (used / (limit as number)) * 100);
        return (
          <span key={label} className={`bchip ${pct >= 90 ? 'hot' : pct >= 70 ? 'warm' : ''}`}>
            {label} {fmt(used)}/{fmt(limit as number)}
            <i style={{ width: pct + '%' }} />
          </span>
        );
      })}
      {routing.escalations > 0 && <span className="bchip warm">升级 {routing.escalations}</span>}
    </span>
  );
}
