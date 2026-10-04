import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../../api/client';
import type { BenchCatalog, BenchRecord, BenchResult, BenchRow, BenchStatus, BenchSummary } from '../../api/bench-types';
import { Badge, Button, Card, Field, Notice, Segmented } from '../../shared/ui';
import { fmtDuration, fmtTokens } from '../../entities/report';

// 一键评测：套餐 + 三层上限（单次运行 / 整个评测 / 随时停止）+ 结果表 + 与历史对比。
// One-click benchmark: presets, three layers of limits, a results table and comparison with history.
const GLYPH: Record<BenchStatus, [string, string]> = {
  pass: ['✓', '通过'],
  false_pass: ['≠', '假通过：系统的 Gate 通过了，但独立判官发现输出不对'],
  gate_fail: ['✗', '未通过'],
  stopped: ['■', '预算/路由停止'],
  timeout: ['⏱', '超时'],
  error: ['!', '出错'],
  skipped: ['·', '跳过（超出评测上限）'],
};
const MECH: Record<string, string> = {
  imports_fixed: '自动修 import',
  syntax_rewrites: '语法门重写',
  diagnosis: 'Diagnoser',
  reconciled: '核对改判',
  rewrite: '整体重写',
  rounds_extended: '延长轮数',
  noop: '无改动拒收',
  flip_to_tests: '转修测试',
};
const pct = (x: number | null | undefined) => (x == null ? '—' : Math.round(x * 100) + '%');
const num = (x: number | null | undefined) => (x == null ? '—' : String(x));

export function BenchSection() {
  const qc = useQueryClient();
  const catalog = useQuery({ queryKey: ['bench', 'tasks'], queryFn: () => api<BenchCatalog>('/bench/tasks'), staleTime: Infinity });
  const status = useQuery({
    queryKey: ['bench', 'status'],
    queryFn: () => api<BenchResult>('/bench/status'),
    refetchInterval: (q) => (['running', 'starting'].includes(q.state.data?.state ?? '') ? 1500 : 5000),
  });
  const history = useQuery({ queryKey: ['bench', 'results'], queryFn: async () => (await api<{ results: BenchSummary[] }>('/bench/results')).results, refetchInterval: 8000 });
  const cat = catalog.data;
  const [suite, setSuite] = useState('canary');
  const [repeats, setRepeats] = useState(1);
  const [tokens, setTokens] = useState(120_000);
  const [minutes, setMinutes] = useState(15);
  const [scale, setScale] = useState(100);
  const [picked, setPicked] = useState<string[]>([]);
  const [shown, setShown] = useState<string | null>(null);
  const [baseId, setBaseId] = useState('');

  const preset = cat?.suites[suite];
  useEffect(() => {
    if (!preset) return;
    setRepeats(preset.repeats);
    setTokens(preset.total_cloud_tokens);
    setMinutes(preset.total_minutes);
    setPicked(preset.tasks);
  }, [suite, preset]);

  const running = ['running', 'starting'].includes(status.data?.state ?? '');
  const start = useMutation({
    mutationFn: () => api('/bench/start', { suite, task_ids: picked, repeats, total_cloud_tokens: tokens, total_minutes: minutes, per_run_scale: scale }),
    onSuccess: () => {
      setShown(null);
      qc.invalidateQueries({ queryKey: ['bench'] });
    },
  });
  const stop = useMutation({ mutationFn: () => api('/bench/stop', {}), onSuccess: () => qc.invalidateQueries({ queryKey: ['bench'] }) });

  const latestId = history.data?.[0]?.id;
  const viewId = running ? null : (shown ?? latestId ?? null);
  const loaded = useQuery({ queryKey: ['bench', 'result', viewId], queryFn: () => api<BenchResult>('/bench/results/' + viewId), enabled: Boolean(viewId) });
  const base = useQuery({ queryKey: ['bench', 'result', baseId], queryFn: () => api<BenchResult>('/bench/results/' + baseId), enabled: Boolean(baseId) });
  const result = running ? status.data : loaded.data;
  const ordered = useMemo(() => [...(cat?.tasks ?? [])].sort((a, b) => a.level - b.level), [cat]);

  if (!cat) return <p className="muted">读取中…</p>;
  return (
    <div className="bench">
      <h2>评测</h2>
      <p className="muted">
        一键跑一组固定的 Go 命令行任务（L0–L9 共 10 个难度等级），用<b>独立判官</b>（评测自带的参考实现，真的构建并运行生成的程序）复核系统自己的 Gate，输出通过率与成本，
        用来判断对 runtime 的修改有没有让系统变强。评测会调用真实模型：本地免费、云端计费，所以有三层上限。
      </p>

      <Card title="运行评测" subtitle="评测在独立的临时目录里跑，不会写入你的历史；它占用 GPU 和云端额度，所以不能与正在执行的任务同时运行。">
        <Segmented value={suite} onChange={setSuite} options={[...Object.entries(cat.suites).map(([id, s]) => [id, s.title.split('（')[0]] as [string, string]), ['custom', '自选']]} />
        {preset && suite !== 'custom' && (
          <p className="muted">
            {preset.title} — {preset.desc}
          </p>
        )}
        <div className="grid2">
          <Field label="每个任务重复次数" hint="重复才能区分“运气”和“能力”">
            <input type="number" min={1} max={10} value={repeats} disabled={running} onChange={(e) => setRepeats(Number(e.target.value))} />
          </Field>
          <Field label="整个评测的云端 token 上限" hint="到顶后剩下的运行标为“跳过”">
            <input type="number" min={1000} step={10000} value={tokens} disabled={running} onChange={(e) => setTokens(Number(e.target.value))} />
          </Field>
          <Field label="整个评测的时间上限（分钟）">
            <input type="number" min={1} value={minutes} disabled={running} onChange={(e) => setMinutes(Number(e.target.value))} />
          </Field>
          <Field label="单次运行上限（占默认的 %）" hint="默认按等级给：时间 3–16 分钟，云端 4–17.5 万 token">
            <input type="number" min={20} max={400} step={10} value={scale} disabled={running} onChange={(e) => setScale(Number(e.target.value))} />
          </Field>
        </div>
        <details>
          <summary>任务（{picked.length} 个）与各自的等级</summary>
          <ul className="bench-tasks">
            {ordered.map((t) => (
              <li key={t.id} title={t.goal}>
                <label className="check">
                  <input
                    type="checkbox"
                    checked={picked.includes(t.id)}
                    disabled={running}
                    onChange={(e) => {
                      setSuite('custom');
                      setPicked(e.target.checked ? [...picked, t.id] : picked.filter((x) => x !== t.id));
                    }}
                  />
                  <Badge>L{t.level}</Badge> <strong>{t.title}</strong>{' '}
                  <span className="muted">
                    {t.cases} 用例 · ≤{fmtDuration(t.max_seconds * 1000)} · ≤{fmtTokens(t.max_cloud_tokens)} tok
                  </span>
                </label>
              </li>
            ))}
          </ul>
          <p className="hint">{Object.entries(cat.levels).map(([k, v]) => `L${k} ${v}`).join(' · ')}</p>
        </details>
        <div className="row">
          {!running ? (
            <Button variant="primary" disabled={start.isPending || !picked.length} onClick={() => start.mutate()}>
              ▶ 开始评测
            </Button>
          ) : (
            <Button variant="danger" disabled={stop.isPending} onClick={() => stop.mutate()}>
              ■ 停止（取消当前运行，剩下的跳过）
            </Button>
          )}
          <span className="hint">
            最坏情况：{fmtTokens(tokens)} 云端 token · {minutes} 分钟。
          </span>
        </div>
        {(start.error || stop.error) && <Notice tone="bad">{((start.error ?? stop.error) as Error).message}</Notice>}
      </Card>

      {running && status.data && <Progress r={status.data} cat={cat} />}

      {!running && (history.data?.length ?? 0) > 0 && (
        <div className="row wrap">
          <Field label="查看历史结果">
            <select value={viewId ?? ''} onChange={(e) => setShown(e.target.value)}>
              {history.data!.map((h) => (
                <option key={h.id} value={h.id}>
                  {h.id} · {h.suite} · {h.passed}/{h.runs} 通过 · {h.commit ?? '未知版本'}
                </option>
              ))}
            </select>
          </Field>
          <Field label="与之对比（基线）">
            <select value={baseId} onChange={(e) => setBaseId(e.target.value)}>
              <option value="">不对比</option>
              {history.data!
                .filter((h) => h.id !== viewId)
                .map((h) => (
                  <option key={h.id} value={h.id}>
                    {h.id} · {h.suite} · {h.commit ?? '?'}
                  </option>
                ))}
            </select>
          </Field>
        </div>
      )}
      {result?.records && <ResultView r={result} cat={cat} base={baseId ? base.data : undefined} />}
      {!running && !history.data?.length && <Notice>还没有评测结果。先跑一次“金丝雀”（约 5 分钟）。</Notice>}
    </div>
  );
}

function Progress({ r, cat }: { r: BenchResult; cat: BenchCatalog }) {
  const cur = r.current;
  const title = cur ? cat.tasks.find((t) => t.id === cur.task)?.title : null;
  return (
    <Card title="进行中" tone="running">
      <div className="budget">
        <Bar label="进度" used={r.records.length} limit={r.total} fmt={String} />
        <Bar label="云端 token" used={r.used.cloud_tokens} limit={r.config.total_cloud_tokens} fmt={fmtTokens} />
        <Bar label="时间" used={r.used.seconds} limit={r.config.total_minutes * 60} fmt={(n) => fmtDuration(n * 1000)} />
      </div>
      <p>
        {cur ? (
          <>
            正在跑 <Badge>L{cur.level}</Badge> <strong>{title}</strong> 第 {cur.repeat} 次（{cur.index}/{r.total}）
          </>
        ) : (
          '准备中…'
        )}
      </p>
      <p className="hint">每个任务完成后会立即保存结果；关闭页面不会中断评测。</p>
    </Card>
  );
}

function Bar({ label, used, limit, fmt }: { label: string; used: number; limit: number; fmt: (n: number) => string }) {
  const share = Math.min(100, (used / Math.max(1, limit)) * 100);
  return (
    <div className="budget-row">
      <span>{label}</span>
      <div className="budget-bar">
        <i className={share >= 90 ? 'hot' : ''} style={{ width: share + '%' }} />
      </div>
      <span className="mono">
        {fmt(used)} / {fmt(limit)}
      </span>
    </div>
  );
}

function Delta({ a, b, fmt = (x: number) => String(x), goodWhen = 'up' }: { a?: number | null; b?: number | null; fmt?: (x: number) => string; goodWhen?: 'up' | 'down' }) {
  if (a == null || b == null) return <span className="muted">—</span>;
  const d = b - a;
  if (Math.abs(d) < 1e-9) return <span className="muted">±0</span>;
  const good = goodWhen === 'up' ? d > 0 : d < 0;
  return (
    <span className={good ? 'delta-good' : 'delta-bad'}>
      {d > 0 ? '+' : '-'}
      {fmt(Math.abs(d))}
    </span>
  );
}

function Kpi({ label, value, sub, d }: { label: string; value: string; sub?: string; d?: ReactNode }) {
  return (
    <div className="kpi">
      <span className="muted">{label}</span>
      <strong>
        {value} {d}
      </strong>
      {sub && <small className="muted">{sub}</small>}
    </div>
  );
}

function ResultView({ r, cat, base }: { r: BenchResult; cat: BenchCatalog; base?: BenchResult }) {
  const o = r.aggregate.overall;
  const bo = base?.aggregate.overall;
  const levels = Object.keys(r.aggregate.by_level).sort((a, b) => Number(a) - Number(b));
  const byTask = useMemo(() => {
    const map = new Map<string, BenchRecord[]>();
    for (const rec of r.records) map.set(rec.task, [...(map.get(rec.task) ?? []), rec]);
    return [...map.entries()].sort((a, b) => a[1][0].level - b[1][0].level);
  }, [r.records]);
  const lvl = (x: number | null) => (x == null ? '—' : 'L' + x);
  return (
    <Card title={`结果 · ${r.id}`} subtitle={`${r.config.suite} · ${r.commit ?? '未知版本'} · ${(r.models ?? []).join(' / ') || '—'}`}>
      {r.state === 'error' && <Notice tone="bad">评测运行出错：{r.message}</Notice>}
      <div className="row wrap bench-kpis">
        <Kpi label="通过率" value={pct(o.rate)} sub={`${o.passed}/${o.runs}`} d={bo && <Delta a={bo.rate} b={o.rate} fmt={(x) => Math.round(x * 100) + '%'} />} />
        <Kpi label="稳定等级" value={lvl(o.stable_level)} sub="通过率 ≥ 67% 的最高等级" d={bo && <Delta a={bo.stable_level} b={o.stable_level} />} />
        <Kpi label="最高通过等级" value={lvl(o.ceiling_level)} sub="至少通过过一次" d={bo && <Delta a={bo.ceiling_level} b={o.ceiling_level} />} />
        <Kpi label="加权得分" value={num(o.weighted_score)} sub="高等级的通过更值钱" d={bo && <Delta a={bo.weighted_score} b={o.weighted_score} />} />
        <Kpi label="假通过" value={String(o.false_pass)} sub="Gate 过了但判官不认" d={bo && <Delta a={bo.false_pass} b={o.false_pass} goodWhen="down" />} />
        <Kpi
          label="每次通过的云端 token"
          value={o.cloud_tokens_per_pass == null ? '—' : fmtTokens(o.cloud_tokens_per_pass)}
          sub={`总计 ${fmtTokens(o.cloud_tokens)}`}
          d={bo && <Delta a={bo.cloud_tokens_per_pass} b={o.cloud_tokens_per_pass} fmt={fmtTokens} goodWhen="down" />}
        />
        <Kpi
          label="每次通过的耗时"
          value={o.seconds_per_pass == null ? '—' : fmtDuration(o.seconds_per_pass * 1000)}
          sub={`总计 ${fmtDuration(o.seconds * 1000)}`}
          d={bo && <Delta a={bo.seconds_per_pass} b={o.seconds_per_pass} fmt={(x) => fmtDuration(x * 1000)} goodWhen="down" />}
        />
      </div>
      {o.skipped > 0 && <Notice tone="warn">有 {o.skipped} 次运行因为超出评测上限（或被停止）而跳过，统计里不含它们。</Notice>}

      <h3>按等级</h3>
      <div className="scroll-x">
        <table className="bench-table">
          <thead>
            <tr>
              <th>等级</th>
              <th>含义</th>
              <th>通过率</th>
              <th>通过/运行</th>
              <th>云端 tok(均)</th>
              <th>秒(均)</th>
              <th>验证轮(均)</th>
              {base && <th>相对基线</th>}
            </tr>
          </thead>
          <tbody>
            {levels.map((lv) => {
              const row: BenchRow = r.aggregate.by_level[lv];
              const brow = base?.aggregate.by_level[lv];
              return (
                <tr key={lv}>
                  <td>
                    <Badge>L{lv}</Badge>
                  </td>
                  <td className="muted">{cat.levels[lv]}</td>
                  <td>
                    <div className="rate-bar">
                      <i style={{ width: row.rate * 100 + '%' }} className={row.rate >= 0.67 ? 'ok' : row.rate > 0 ? 'mid' : 'bad'} />
                    </div>{' '}
                    {pct(row.rate)}
                  </td>
                  <td className="mono">
                    {row.passed}/{row.runs}
                    {row.false_pass ? ` (≠${row.false_pass})` : ''}
                  </td>
                  <td className="mono">{row.mean_cloud_tokens == null ? '—' : fmtTokens(row.mean_cloud_tokens)}</td>
                  <td className="mono">{num(row.mean_seconds)}</td>
                  <td className="mono">{num(row.mean_rounds)}</td>
                  {base && <td>{brow ? <Delta a={brow.rate} b={row.rate} fmt={(x) => Math.round(x * 100) + '%'} /> : <span className="muted">—</span>}</td>}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <h3>
        按任务 <span className="muted">每个符号是一次运行：✓通过 ✗未通过 ≠假通过 ■停止 ⏱超时 !出错 ·跳过（悬停看原因）</span>
      </h3>
      <div className="scroll-x">
        <table className="bench-table">
          <thead>
            <tr>
              <th>等级</th>
              <th>任务</th>
              <th>运行</th>
              <th>云端 tok(均)</th>
              <th>秒(均)</th>
              <th>调用(均)</th>
              <th>升级(均)</th>
            </tr>
          </thead>
          <tbody>
            {byTask.map(([id, recs]) => {
              const t = cat.tasks.find((x) => x.id === id);
              const row = r.aggregate.by_task[id];
              return (
                <tr key={id}>
                  <td>
                    <Badge>L{recs[0].level}</Badge>
                  </td>
                  <td title={t?.goal}>{t?.title ?? id}</td>
                  <td className="runs">
                    {recs.map((x, i) => (
                      <span
                        key={i}
                        className={'run-' + x.status}
                        title={`${GLYPH[x.status][1]}${x.oracle_reason ? '：' + x.oracle_reason : ''}${x.note ? '：' + x.note : ''}${x.stop_reason ? '（' + x.stop_reason + '）' : ''}`}
                      >
                        {GLYPH[x.status][0]}
                      </span>
                    ))}
                  </td>
                  <td className="mono">{row?.mean_cloud_tokens == null ? '—' : fmtTokens(row.mean_cloud_tokens)}</td>
                  <td className="mono">{num(row?.mean_seconds)}</td>
                  <td className="mono">{num(row?.mean_calls)}</td>
                  <td className="mono">{num(row?.mean_escalations)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <h3>
        触发的 runtime 机制 <span className="muted">改动有没有在线上起作用的直接证据</span>
      </h3>
      <div className="row wrap">
        {Object.entries(MECH).map(([k, label]) => (
          <span key={k} className={'chip-static' + (o.mechanisms[k] ? '' : ' dim')}>
            {label} × {o.mechanisms[k] ?? 0}
            {bo ? `（基线 ${bo.mechanisms[k] ?? 0}）` : ''}
          </span>
        ))}
      </div>
    </Card>
  );
}
