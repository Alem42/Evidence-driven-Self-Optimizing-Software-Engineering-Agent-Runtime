import { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../../api/client';
import { qk } from '../../api/queries';
import type { Account, AccountBalance, AccountModel } from '../../api/types';
import { normalizeProfiles } from '../../entities/profiles';
import { Badge, Button, Card, Notice, Spinner } from '../../shared/ui';

const fmtN = (n: number | null | undefined): string => {
  if (n == null) return '—';
  if (n >= 1_000_000) return (n / 1_048_576).toFixed(n % 1_048_576 === 0 ? 0 : 1) + 'M';
  if (n >= 1000) return Math.round(n / 1024) + 'K';
  return String(n);
};

/** 每列“最优”的下标，用于高亮对比。 Index of the best row per column, for the comparison highlight. */
function best<T>(rows: T[], pick: (r: T) => number | null | undefined, lower = false): Set<number> {
  const values = rows.map(pick);
  const known = values.filter((v): v is number => v != null);
  if (known.length < 2) return new Set();
  const target = lower ? Math.min(...known) : Math.max(...known);
  if (known.every((v) => v === target)) return new Set();
  return new Set(values.flatMap((v, i) => (v === target ? [i] : [])));
}

function Balance({ account }: { account: Account }) {
  const q = useQuery({ queryKey: ['balance', account.id], queryFn: () => api<AccountBalance>('/accounts/' + account.id + '/balance'), enabled: false, retry: false });
  return (
    <div className="row wrap">
      <Button size="sm" onClick={() => q.refetch()} disabled={!account.balance_supported || q.isFetching}>
        {q.isFetching ? '查询中…' : '查询余额'}
      </Button>
      {!account.balance_supported && <span className="hint">该服务没有已知的余额接口</span>}
      {q.isError && <span className="bad-text">{(q.error as Error).message}</span>}
      {q.data?.supported && (
        <>
          <Badge tone={q.data.available ? 'ok' : 'bad'} dot>{q.data.available ? '余额充足' : '余额不足'}</Badge>
          {q.data.balances?.map((b) => (
            <span key={b.currency} className="mono">
              {b.currency} {b.total}
              <span className="muted"> （充值 {b.topped_up} · 赠送 {b.granted}）</span>
            </span>
          ))}
        </>
      )}
    </div>
  );
}

// 一把 key 往往能用多个模型：向官方 /models 实时查询，勾选要用的（勾选 = 启用，没有就添加），并排对比详情。
// One key often unlocks several models: query the official list, tick the ones to use, compare them side by side.
function AccountCard({ account }: { account: Account }) {
  const qc = useQueryClient();
  const models = useQuery({ queryKey: ['account-models', account.id], queryFn: async () => (await api<{ models: AccountModel[] }>('/accounts/' + account.id + '/models')).models, retry: false });
  const [ticked, setTicked] = useState<Set<string>>(new Set());
  const enabledIds = useMemo(() => new Set(account.profiles.filter((p) => p.enabled).map((p) => p.model)), [account]);

  useEffect(() => {
    if (models.data) setTicked(new Set(models.data.filter((m) => m.added && enabledIds.has(m.id)).map((m) => m.id)));
  }, [models.data, enabledIds]);

  const apply = useMutation({
    mutationFn: () => api('/accounts/' + account.id + '/select', { models: [...ticked] }),
    onSuccess: (result) => {
      qc.setQueryData(qk.profiles, normalizeProfiles(result as never));
      qc.invalidateQueries({ queryKey: ['accounts'] });
      qc.invalidateQueries({ queryKey: ['account-models', account.id] });
    },
  });

  const rows = models.data ?? [];
  const bestCtx = best(rows, (m) => m.context_window);
  const bestOut = best(rows, (m) => m.max_output_tokens);
  const bestPrice = best(rows, (m) => (m.suggestion ? m.suggestion.price_in + m.suggestion.price_out : null), true);
  const dirty = rows.some((m) => ticked.has(m.id) !== (m.added && enabledIds.has(m.id)));

  return (
    <Card
      title={account.host || account.base_url}
      subtitle={`${account.profiles.length} 个配置 · key 已保存（不显示）`}
      actions={<Link to="/settings/order" className="link">调整尝试次序 →</Link>}
    >
      <Balance account={account} />
      {models.isLoading && <div className="center pad"><Spinner /> 向官方查询可用模型…</div>}
      {models.isError && <Notice tone="bad">{(models.error as Error).message}</Notice>}
      {rows.length > 0 && (
        <>
          <p className="hint">勾选要使用的模型（勾选即启用，没有配置会自动添加；取消勾选只是停用，已有设置保留）。每列最优值已高亮；价格是官方文档的建议值，请核对。</p>
          <div className="table-wrap">
            <table className="compare">
              <thead>
                <tr><th>使用</th><th>模型</th><th>上下文</th><th>最大输出</th><th>图片输入</th><th>推理档位</th><th>建议等级</th><th>价格 / 百万 token</th><th>状态</th></tr>
              </thead>
              <tbody>
                {rows.map((m, i) => (
                  <tr key={m.id} className={ticked.has(m.id) ? 'picked' : ''}>
                    <td>
                      <input
                        type="checkbox"
                        aria-label={`使用 ${m.id}`}
                        checked={ticked.has(m.id)}
                        onChange={(e) => setTicked((t) => { const n = new Set(t); if (e.target.checked) n.add(m.id); else n.delete(m.id); return n; })}
                      />
                    </td>
                    <td><strong>{m.name}</strong><div className="mono muted">{m.id}</div>{m.suggestion?.note && <div className="hint">{m.suggestion.note}</div>}</td>
                    <td className={bestCtx.has(i) ? 'best' : ''}>{fmtN(m.context_window)}</td>
                    <td className={bestOut.has(i) ? 'best' : ''}>{fmtN(m.max_output_tokens)}</td>
                    <td>{m.vision ? '支持' : '—'}</td>
                    <td className="muted">{m.efforts?.join(' / ') ?? '—'}</td>
                    <td>{m.suggestion ? 'L' + m.suggestion.level : <span className="muted">未知</span>}</td>
                    <td className={bestPrice.has(i) ? 'best' : ''}>
                      {m.suggestion ? (
                        <>入 {m.suggestion.price_in} · 出 {m.suggestion.price_out} <span className="muted">{m.suggestion.currency}</span></>
                      ) : <span className="muted">官方文档未收录</span>}
                    </td>
                    <td><Badge tone={m.added ? (enabledIds.has(m.id) ? 'ok' : 'neutral') : 'warn'}>{m.added ? (enabledIds.has(m.id) ? '已启用' : '已停用') : '未添加'}</Badge></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="hint">系统上限：上下文最多按 262,144 token 计、单次输出最多按 8,192 token 计（更大的值会被夹到这个上限）。{account.pricing_source && <> 价格出处：<code>{account.pricing_source}</code></>}</p>
          {apply.isError && <Notice tone="bad">{(apply.error as Error).message}</Notice>}
          <div className="row">
            <Button variant="primary" disabled={!dirty || apply.isPending} onClick={() => apply.mutate()}>{apply.isPending ? '应用中…' : '应用勾选'}</Button>
            {!dirty && apply.isSuccess && <span className="ok-text">已应用</span>}
            <span className="hint">已选 {ticked.size} / {rows.length}</span>
          </div>
        </>
      )}
    </Card>
  );
}

export function AccountsSection() {
  const accounts = useQuery({ queryKey: ['accounts'], queryFn: async () => (await api<{ accounts: Account[] }>('/accounts')).accounts });
  if (accounts.isLoading) return <div className="center pad"><Spinner /> 读取…</div>;
  if (accounts.isError) return <Notice tone="bad">{(accounts.error as Error).message}</Notice>;
  return (
    <div className="stack">
      <Notice>
        一把 API key 往往能用多个模型。这里按「地址 + key」把云端配置归为账户，向官方接口实时查询这把 key 能用的模型和余额；key 只保存在后端，页面上从不显示。
      </Notice>
      {!accounts.data?.length && (
        <Card title="还没有 API 账户">
          <p className="muted">先在「模型配置」里添加一个带 key 的云端模型，这里就会出现对应的账户和它的全部可用模型。</p>
          <Link to="/settings/models" className="link">去添加 →</Link>
        </Card>
      )}
      {accounts.data?.map((a) => <AccountCard key={a.id} account={a} />)}
    </div>
  );
}
