import { useEffect, useRef, useState } from 'react';
import { api } from '../../api/client';
import type { Estimate, EstimateTier } from '../../api/types';
import { Badge, Spinner } from '../../shared/ui';

export function formatTokens(n: number): string {
  return n >= 1000 ? `${Math.round(n / 1000)}k` : String(n);
}

/** 价格区间的一句话：最便宜到最贵的云端模型；没有设价格就如实说。 One line for the price range: cheapest to dearest cloud model; honest when no price is set. */
export function formatPrice(tier: EstimateTier): string {
  const { low, high, unpriced } = tier.price;
  if (low === null || high === null) return '价格未设置';
  const money = (v: number) => (v < 0.1 ? v.toFixed(3) : v.toFixed(2));
  const range = low === high ? `≈¥${money(low)}` : `≈¥${money(low)}–${money(high)}`;
  return unpriced ? `${range}（另有 ${unpriced} 个模型未设价格）` : range;
}

const CAN: Record<Estimate['can_handle'], { tone: 'ok' | 'warn' | 'bad'; text: string }> = {
  yes: { tone: 'ok', text: '系统可以处理' },
  risky: { tone: 'warn', text: '有风险' },
  no: { tone: 'bad', text: '处理不了' },
};

/**
 * 新任务页：用户一输入需求，先立刻给一个“按规则”的估计，随后本地模型快速推测任务规模，并给出云端 API token 的三档建议与价格区间。
 * New Task page: as soon as the user types, an instant rules-based estimate appears, then the local model looks at the goal and the page offers three cloud-API token tiers with a price range.
 * 选中的档位通过 onTokens 写给“API token 上限”；用户在高级设置里手填过就不再覆盖（enabled=false）。 The chosen tier is written to the API token limit through onTokens; once the user typed a limit by hand it is no longer overwritten (enabled=false).
 */
export function EstimatePanel({ goal, language, enabled, onTokens }: { goal: string; language: string; enabled: boolean; onTokens: (tokens: number | null) => void }) {
  const [result, setResult] = useState<Estimate | null>(null);
  const [asking, setAsking] = useState(false);
  const [tier, setTier] = useState<EstimateTier['key']>('standard');
  const ticket = useRef(0);
  useEffect(() => {
    const mine = ++ticket.current;
    setAsking(false);
    if (goal.trim().length < 6) { setResult(null); return; }
    const stale = () => ticket.current !== mine;
    const quick = setTimeout(() => {
      api<Estimate>('/estimate', { goal, language, mode: 'rules' }).then((r) => { if (!stale()) setResult(r); }).catch(() => undefined);
    }, 350);
    const slow = setTimeout(() => {
      setAsking(true);
      api<Estimate>('/estimate', { goal, language, mode: 'model' })
        .then((r) => { if (!stale()) setResult(r); })
        .catch(() => undefined)
        .finally(() => { if (!stale()) setAsking(false); });
    }, 1300);
    return () => { clearTimeout(quick); clearTimeout(slow); };
  }, [goal, language]);
  const chosen = result?.tiers.find((t) => t.key === tier);
  useEffect(() => {
    if (enabled) onTokens(chosen ? chosen.tokens : null);
  }, [chosen?.tokens, enabled]); // eslint-disable-line react-hooks/exhaustive-deps
  if (!result && !asking) return null;
  const can = result ? CAN[result.can_handle] : null;
  return (
    <div className="estimate" style={{ border: '1px solid var(--line, #e5e5e5)', borderRadius: 8, padding: '10px 12px', margin: '10px 0' }}>
      <div className="row wrap">
        {asking ? (
          <span className="muted"><Spinner /> 本地模型正在推测任务规模…{result ? '（下面先是按规则的估计）' : ''}</span>
        ) : (
          <span className="muted">{result?.source === 'local-model' ? `本地模型（${result.model}）的推测` : '按规则的估计'}</span>
        )}
        {result && can && (
          <>
            <Badge tone={can.tone} dot>{can.text}</Badge>
            <span>难度 L{result.level}{result.files ? ` · 约 ${result.files} 个文件` : ''}</span>
          </>
        )}
      </div>
      {result && result.reasons.length > 0 && <p className="muted" style={{ margin: '4px 0' }}>{result.reasons.join('；')}</p>}
      {result && result.note && !asking && <p className="muted" style={{ margin: '4px 0' }}>{result.note}</p>}
      {result && (
        <div className="row wrap" role="radiogroup" aria-label="云端 API token 建议" style={{ marginTop: 6 }}>
          {result.tiers.map((t) => (
            <label key={t.key} className="check" style={{ opacity: enabled ? 1 : 0.55 }}>
              <input type="radio" name="token-tier" checked={tier === t.key} disabled={!enabled} onChange={() => setTier(t.key)} />
              <strong>{t.label}</strong> {formatTokens(t.tokens)} token <span className="muted">{formatPrice(t)}</span>
            </label>
          ))}
        </div>
      )}
      <p className="hint" style={{ margin: '6px 0 0' }}>
        只算云端 API（本地模型不计费）。价格 = 档位 token × 各云端模型单价，取最便宜到最贵，输入输出按 85:15 折算，不含缓存折扣；这是建议，不是承诺。
        {!enabled && ' 你已在高级设置里手填了上限，这里不再改它。'}
      </p>
    </div>
  );
}
