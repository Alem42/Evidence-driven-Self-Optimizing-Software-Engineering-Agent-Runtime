import type { Triage } from '../../api/types';
import { Badge } from '../../shared/ui';

const TONE = { ok: 'ok', risky: 'warn', infeasible: 'bad' } as const;
const LABEL = { ok: '未发现问题', risky: '有风险', infeasible: '确定会失败' } as const;

// 可行性预检结果：确定性规则命中的原因与改写建议，以及本地模型的复核意见（只告警）。
// Feasibility triage: rule findings with rewrite suggestions, plus the local model's review (warnings only).
export function TriageView({ triage, compact = false }: { triage: Triage; compact?: boolean }) {
  const model = triage.model;
  const hasModel = model && (model.verdict === 'risky' || model.reasons.length > 0);
  if (!triage.findings.length && !hasModel) return null;
  return (
    <div className={`triage notice notice-${triage.verdict === 'infeasible' ? 'bad' : 'warn'}`}>
      <div className="row between">
        <strong>可行性预检{compact ? '' : '（执行前自动完成）'}</strong>
        <Badge tone={TONE[triage.verdict]} dot>{LABEL[triage.verdict]}</Badge>
      </div>
      {triage.findings.map((f) => (
        <div key={f.rule} className="finding">
          <div>
            {f.level === 'infeasible' ? '✕' : '△'} {f.reason}
            {f.matched.length > 0 && <span className="muted"> · 命中：{f.matched.map((m) => `「${m}」`).join(' ')}</span>}
          </div>
          <div className="muted">→ {f.suggestion}</div>
        </div>
      ))}
      {hasModel && model && (
        <div className="finding">
          <div><strong>本地模型复核{model.by ? `（${model.by}）` : ''}</strong>{model.verdict === 'skipped' ? '：未能完成，不影响规划' : ''}</div>
          {model.reasons.map((r, i) => <div key={i}>△ {r}</div>)}
          {model.suggestions.map((r, i) => <div key={i} className="muted">→ {r}</div>)}
          <div className="hint">本地模型只能提示风险，不能拦截，也不会使用 API。</div>
        </div>
      )}
    </div>
  );
}
