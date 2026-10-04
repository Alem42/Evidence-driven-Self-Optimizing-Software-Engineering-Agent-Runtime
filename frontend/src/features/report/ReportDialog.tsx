import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useReport } from '../../api/queries';
import type { TaskReport } from '../../api/types';
import { CHAIN_TEXT, fmtDuration, fmtTokens, kindLabel, OUTCOME, REASON_TEXT, reportText } from '../../entities/report';
import { stageLabels } from '../../entities/status';
import { useUi } from '../../stores/ui';
import { Badge, Button, Spinner } from '../../shared/ui';

const step = (s: string | null): string => (s ? stageLabels[s] ?? s : '—');
const OP: Record<string, string> = { go_test: 'go test', go_vet: 'go vet', go_fmt_check: 'gofmt 检查', gate: 'Gate 核对', run_application: '运行程序', go_index: '代码索引' };

/** token 占比条：本地 vs API。 Local vs API share bar. */
function SplitBar({ local, cloud }: { local: number; cloud: number }) {
  const total = local + cloud;
  if (!total) return null;
  return (
    <div className="split-bar" title={`本地 ${local} · API ${cloud}`}>
      <i className="seg-local" style={{ width: (local / total) * 100 + '%' }} />
      <i className="seg-cloud" style={{ width: (cloud / total) * 100 + '%' }} />
    </div>
  );
}

/** 预算条：已用（含对未知用量请求的预留）/ 上限。 Budget bar: used (incl. reservations) vs limit. */
function BudgetBar({ label, used, limit, fmt }: { label: string; used: number; limit: number | null; fmt: (n: number) => string }) {
  if (limit == null) return <div className="budget-row"><span>{label}</span><span className="muted">{fmt(used)} · 不限</span></div>;
  const pct = Math.min(100, (used / limit) * 100);
  return (
    <div className="budget-row">
      <span>{label}</span>
      <div className="budget-bar"><i className={pct >= 90 ? 'hot' : ''} style={{ width: pct + '%' }} /></div>
      <span className="mono">{fmt(used)} / {fmt(limit)}</span>
    </div>
  );
}

function RoutingBlock({ r }: { r: NonNullable<TaskReport['routing']> }) {
  const b = r.budget;
  const t0 = r.decisions[0]?.at ?? 0;
  return (
    <>
      <h3>路由与预算 <span className="muted">{r.mode === 'ladder' ? '本地优先 · 有界升级' : '固定模型'}</span></h3>
      {r.mode === 'ladder' && (
        <div className="budget">
          <BudgetBar label="API token" used={r.spend.cloud_tokens} limit={b.max_cloud_tokens} fmt={fmtTokens} />
          <BudgetBar label="模型调用" used={r.spend.calls} limit={b.max_model_calls} fmt={String} />
          <BudgetBar label="运行时间" used={r.spend.active_seconds} limit={b.max_active_seconds} fmt={(n) => fmtDuration(n * 1000)} />
          {b.max_cost != null && <BudgetBar label="费用" used={r.spend.cost} limit={b.max_cost} fmt={(n) => n.toFixed(4)} />}
          {r.spend.reserved_calls > 0 && <p className="hint">有 {r.spend.reserved_calls} 次云调用没有返回用量，已按其上下文上限预留。</p>}
        </div>
      )}
      {r.stopped && <div className="notice notice-warn">路由已停止：{REASON_TEXT[r.stopped.reason] ?? r.stopped.reason}{r.stopped.detail ? '（' + r.stopped.detail + '）' : ''}。证据已保留，未继续花费。</div>}
      <div className="row wrap"><Badge tone={r.escalations ? 'warn' : 'ok'}>{r.escalations ? `升级 ${r.escalations} 次` : '未升级'}</Badge><span className="muted">候选：{r.candidates.map((c) => `L${c.level} ${c.model}`).join(' → ')}</span></div>
      <ol className="timeline">
        {r.decisions.map((d, i) => (
          <li key={i} className={d.escalated ? 'tl-pending' : d.action === 'stop' ? 'tl-failed' : ''}>
            <time>{t0 ? '+' + fmtDuration((d.at - t0) * 1000) : '—'}</time>
            <div className="tl-main">
              <div className="row between">
                <span><strong>{CHAIN_TEXT[d.chain] ?? d.chain}</strong> <span className="muted">· {step(d.role)}</span></span>
                <span className="mono">{d.action === 'stop' ? '停止' : `L${d.level} ${d.model}`}</span>
              </div>
              <div className="muted">{d.escalated && <strong>升级 · </strong>}{REASON_TEXT[d.reason] ?? d.reason}{d.detail ? ' · ' + d.detail : ''}</div>
            </div>
          </li>
        ))}
      </ol>
    </>
  );
}

const NODE_TEXT: Record<string, string> = {
  classify: '失败分类（归属：实现/测试）', diagnose: 'Diagnoser 诊断', format: 'gofmt 格式化',
  repair: '修复实现', revise: '修订测试', arbitrate: '测试↔规格仲裁', drafted: '得到新草稿，去验证', halt: '停止并交给人',
};
const OWNER_TEXT: Record<string, string> = { implementation: '实现有问题', test: '测试有问题', both: '两侧都有问题', spec: '规格有问题', unclear: '不明确' };

/** 修复过程：子图走过的节点、诊断、模型释放、轮数延长、无改动拒收。 Repair process events. */
function ProcessBlock({ items, t0 }: { items: NonNullable<TaskReport['process']>; t0: number }) {
  const rows = items.flatMap((p, i) => {
    const d = p.data;
    let text = '';
    let tone = '';
    if (p.kind === 'workflow_node') text = `修复子图 · ${NODE_TEXT[d.node] ?? d.node} → ${NODE_TEXT[d.to] ?? d.to}（${d.why}）`;
    else if (p.kind === 'diagnosis') text = `Diagnoser（${d.by}）：${OWNER_TEXT[d.owner] ?? d.owner} · ${d.rationale ?? ''}`;
    else if (p.kind === 'diagnosis_failed') text = 'Diagnoser 未能给出诊断，按规则继续';
    else if (p.kind === 'models_released') { text = `释放本地模型：${(d.models ?? []).join('、')}${d.reason === 'switching' ? '（切换模型）' : '（任务结束）'}`; tone = 'p-ok'; }
    else if (p.kind === 'rounds_extended') { text = `仍在收敛（未解决 ${d.was} → ${d.unresolved}），多给一轮修复`; tone = 'p-ok'; }
    else if (p.kind === 'noop_revision') { text = '修复没有任何改动，已拒收（不验证）并换更强的模型'; tone = 'p-bad'; }
    else if (p.kind === 'task_stopped') { text = `任务停止：${d.detail ?? d.reason}`; tone = 'p-bad'; }
    else if (p.kind === 'transport_retry') text = '本地服务暂不可达，等待恢复后重试';
    else return [];
    return [<li key={i} className={tone}><time>{t0 ? '+' + fmtDuration((p.at - t0) * 1000) : '—'}</time><span>{text}</span></li>];
  });
  if (!rows.length) return null;
  return (
    <>
      <h3>修复过程 <span className="muted">{rows.length} 步</span></h3>
      <ul className="process">{rows}</ul>
    </>
  );
}

function Body({ r }: { r: TaskReport }) {
  const t = r.totals;
  const [copied, setCopied] = useState(false);
  const o = OUTCOME[r.outcome];
  const t0 = r.started_at ?? 0;
  const maxTok = Math.max(1, ...r.calls.map((c) => c.total_tokens ?? 0));
  const toolMs = r.tools.reduce((a, x) => a + (x.duration_ms ?? 0), 0);

  return (
    <>
      <div className={`report-hero tone-${o.tone}`}>
        <div>
          <h2>{o.label}</h2>
          <p>{o.text}</p>
        </div>
        <Badge tone={o.tone} dot>{o.label}</Badge>
      </div>

      <div className="stat-grid">
        <div className="stat"><small>总耗时</small><strong>{r.wall_seconds != null ? fmtDuration(r.wall_seconds * 1000) : '—'}</strong><span>含等待你确认的时间</span></div>
        <div className="stat"><small>模型耗时</small><strong>{fmtDuration(t.model_ms)}</strong><span>{t.calls} 次调用{t.failed_calls ? ` · ${t.failed_calls} 次失败` : ''}</span></div>
        <div className="stat"><small>Token 总量</small><strong>{fmtTokens(t.total_tokens)}</strong><span>输入 {fmtTokens(t.prompt_tokens)} · 输出 {fmtTokens(t.completion_tokens)}</span></div>
        <div className="stat"><small>API token</small><strong>{fmtTokens(t.cloud_tokens)}</strong><span>本地 {fmtTokens(t.local_tokens)}</span></div>
      </div>
      <SplitBar local={t.local_tokens} cloud={t.cloud_tokens} />
      {t.unknown_usage_calls > 0 && <p className="hint">有 {t.unknown_usage_calls} 次调用服务端没有返回用量，已计为“未知”，没有当作 0。</p>}

      {r.routing && <RoutingBlock r={r.routing} />}
      {r.process && r.process.length > 0 && <ProcessBlock items={r.process} t0={t0} />}

      <h3>按模型</h3>
      <div className="table-wrap">
        <table>
          <thead><tr><th>模型</th><th>类型</th><th>调用</th><th>输入</th><th>输出</th><th>合计</th><th>耗时</th></tr></thead>
          <tbody>
            {r.by_model.map((m) => (
              <tr key={m.model + m.kind}>
                <td className="mono">{m.model}</td>
                <td><Badge tone={m.kind === 'cloud' ? 'running' : 'ok'}>{kindLabel(m.kind)}</Badge></td>
                <td>{m.calls}{m.failed ? <span className="bad-text"> ({m.failed} 失败)</span> : null}</td>
                <td>{fmtTokens(m.prompt_tokens)}</td><td>{fmtTokens(m.completion_tokens)}</td><td><strong>{fmtTokens(m.total_tokens)}</strong></td>
                <td>{fmtDuration(m.model_ms)}</td>
              </tr>
            ))}
            {!r.by_model.length && <tr><td colSpan={7} className="muted">还没有模型调用。</td></tr>}
          </tbody>
        </table>
      </div>

      <h3>按步骤</h3>
      <div className="table-wrap">
        <table>
          <thead><tr><th>步骤</th><th>调用</th><th>输入</th><th>输出</th><th>合计</th><th>耗时</th></tr></thead>
          <tbody>
            {r.by_step.map((s) => (
              <tr key={s.step_id ?? '-'}>
                <td>{step(s.step_id)}</td><td>{s.calls}</td><td>{fmtTokens(s.prompt_tokens)}</td><td>{fmtTokens(s.completion_tokens)}</td>
                <td><strong>{fmtTokens(s.total_tokens)}</strong></td><td>{fmtDuration(s.model_ms)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <h3>逐次调用时间线</h3>
      <ol className="timeline">
        {r.calls.map((c, i) => (
          <li key={i} className={'tl-' + c.status}>
            <time>{t0 && c.started ? '+' + fmtDuration((c.started - t0) * 1000) : '—'}</time>
            <div className="tl-main">
              <div className="row between">
                <span><strong>{step(c.step_id)}</strong> <span className="muted">· 第 {c.attempt_no ?? 1} 次</span></span>
                <span className="muted mono">{c.model} · {kindLabel(c.kind)}</span>
              </div>
              <div className="tl-bar"><i style={{ width: ((c.total_tokens ?? 0) / maxTok) * 100 + '%' }} className={c.kind === 'cloud' ? 'seg-cloud' : 'seg-local'} /></div>
              <div className="muted">
                {c.status === 'failed' ? <span className="bad-text">失败{c.error ? ' · ' + c.error : ''} · </span> : c.status === 'pending' ? '未返回结果 · ' : ''}
                {fmtTokens(c.total_tokens)} tok（入 {fmtTokens(c.prompt_tokens)} / 出 {fmtTokens(c.completion_tokens)}）· {fmtDuration(c.duration_ms)}
                {c.tokens_per_second != null && ` · ${c.tokens_per_second} tok/s`}
              </div>
            </div>
          </li>
        ))}
      </ol>

      <h3>工具调用 <span className="muted">共 {r.tools.length} 次 · {fmtDuration(toolMs)}</span></h3>
      <p className="hint">检查工具由 Go runner 在隔离副本中按白名单固定命令执行，模型不能直接运行命令。</p>
      <div className="table-wrap">
        <table>
          <thead><tr><th>时间</th><th>工具</th><th>结果</th><th>退出码</th><th>耗时</th><th>方式</th></tr></thead>
          <tbody>
            {r.tools.map((x, i) => (
              <tr key={i}>
                <td>{t0 && x.started ? '+' + fmtDuration((x.started - t0) * 1000) : '—'}</td>
                <td>{OP[x.operation] ?? x.operation}</td>
                <td><Badge tone={x.status === 'succeeded' || x.status === 'finished' ? 'ok' : 'bad'}>{x.status === 'succeeded' ? '通过' : x.status === 'finished' ? '结束' : x.status === 'failed' ? '未通过' : x.status}</Badge></td>
                <td>{x.exit_code ?? '—'}</td><td>{fmtDuration(x.duration_ms)}</td>
                <td className="muted">{x.kind === 'gate' ? '独立核对证据' : x.kind === 'app' ? '用户触发 · 隔离运行' : '隔离副本 · 白名单命令'}</td>
              </tr>
            ))}
            {!r.tools.length && <tr><td colSpan={6} className="muted">尚未执行工具。</td></tr>}
          </tbody>
        </table>
      </div>

      <div className="row end">
        <Button size="sm" variant="ghost" onClick={async () => { await navigator.clipboard.writeText(reportText(r, step)); setCopied(true); }}>{copied ? '已复制' : '复制为文本'}</Button>
      </div>
    </>
  );
}

// 任务报告弹窗：结论、耗时、token（总量/按模型/按步骤/逐次）、工具调用。任务结束自动弹出，也可从顶栏随时打开。
// Task report dialog: auto-opens when a task ends and can be reopened from the top bar.
export function ReportDialog() {
  const runId = useUi((s) => s.reportFor);
  const set = useUi((s) => s.set);
  const navigate = useNavigate();
  const report = useReport(runId ?? undefined, false);
  const close = () => set({ reportFor: null });

  useEffect(() => {
    if (!runId) return;
    const key = (e: KeyboardEvent) => e.key === 'Escape' && close();
    window.addEventListener('keydown', key);
    return () => window.removeEventListener('keydown', key);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId]);

  if (!runId) return null;
  return (
    <div className="overlay" onMouseDown={(e) => e.target === e.currentTarget && close()}>
      <section className="modal" role="dialog" aria-modal="true" aria-label="任务报告">
        <header className="modal-head">
          <div>
            <span className="eyebrow">任务报告</span>
            <h1>{report.data?.title.split('\n')[0].slice(0, 80) ?? '读取中…'}</h1>
          </div>
          <button className="icon-btn" aria-label="关闭" onClick={close}>×</button>
        </header>
        <div className="modal-body">
          {report.isLoading && <div className="center pad"><Spinner /> 汇总用量…</div>}
          {report.isError && <p className="bad-text">{(report.error as Error).message}</p>}
          {report.data && <Body r={report.data} />}
        </div>
        <footer className="modal-foot">
          {report.data && report.data.latest_run_id !== runId && (
            <Button size="sm" variant="ghost" onClick={() => { navigate('/task/' + report.data!.latest_run_id); close(); }}>查看最新版本</Button>
          )}
          <Button variant="primary" onClick={close}>关闭</Button>
        </footer>
      </section>
    </div>
  );
}
