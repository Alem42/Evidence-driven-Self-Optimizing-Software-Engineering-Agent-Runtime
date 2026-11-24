import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { api } from '../../api/client';
import type { BenchCatalog, BenchRecord, BenchResult, BenchStatus, BenchSummary } from '../../api/bench-types';
import { useBenchTasks, useProfiles } from '../../api/queries';
import { byRouting, profileLabel, profileReady } from '../../entities/profiles';
import { fmtDuration, fmtTokens } from '../../entities/report';
import { stageLabels } from '../../entities/status';
import { Badge, Button, Card, Field, Notice } from '../../shared/ui';
import { useUi } from '../../stores/ui';

const SUITE_NAMES: Record<string, string> = {
  canary: '最小检查', quick: '快速回归', standard: '标准回归', full: '全量回归', custom: '自选任务',
};
const OUTCOMES: Record<BenchStatus, string> = {
  pass: '通过', false_pass: '假通过', gate_fail: '未通过', stopped: '提前停止', timeout: '超时', error: '出错', skipped: '未运行',
};
const MECHANISMS: Record<string, string> = {
  imports_fixed: '修复导入', syntax_rewrites: '语法重写', diagnosis: '失败诊断', reconciled: '核对改判',
  rewrite: '整体重写', rounds_extended: '增加修复轮次', noop: '拒收无改动', flip_to_tests: '转修测试',
};
const suiteName = (id: string, catalog: BenchCatalog) => SUITE_NAMES[id] ?? catalog.suites[id]?.title ?? id;
const taskName = (id: string, catalog: BenchCatalog) => catalog.tasks.find((task) => task.id === id)?.title ?? id;
const duration = (seconds: number) => fmtDuration(seconds * 1000);
const percent = (value: number | null) => value == null ? '—' : `${Math.round(100 * value)}%`;
const inRange = (value: number, min: number, max: number) => Number.isInteger(value) && value >= min && value <= max;

export function BenchSection() {
  const qc = useQueryClient();
  const catalog = useBenchTasks();
  const status = useQuery({
    queryKey: ['bench', 'status'], queryFn: () => api<BenchResult>('/bench/status'),
    refetchInterval: (q) => ['running', 'starting'].includes(q.state.data?.state ?? '') ? 1500 : 5000,
  });
  const history = useQuery({
    queryKey: ['bench', 'results'], refetchInterval: 8000,
    queryFn: async () => (await api<{ results: BenchSummary[] }>('/bench/results')).results,
  });
  const selectedModels = useUi((x) => x.selectedModels);
  const profiles = useProfiles();
  const ready = (profiles.data?.profiles ?? []).filter(profileReady).sort(byRouting);
  const [pinnedProfile, setPinnedProfile] = useState('');
  // 评测可单独锁定一个模型，也可沿用新任务的路由候选。 Pin one evaluation profile or use the task's routing selection.
  const chosen = ready.filter((p) => pinnedProfile ? p.id === pinnedProfile : !selectedModels || selectedModels.includes(p.id));
  const includesCloud = chosen.some((p) => p.model_type === 'cloud');

  const [suite, setSuite] = useState('canary');
  const [repeats, setRepeats] = useState(1);
  const [tokens, setTokens] = useState(120_000);
  const [minutes, setMinutes] = useState(15);
  const [scale, setScale] = useState(100);
  const [picked, setPicked] = useState<string[]>([]);
  const [shown, setShown] = useState('');
  const [baseId, setBaseId] = useState('');

  const cat = catalog.data;
  const preset = cat?.suites[suite];
  useEffect(() => {
    if (!preset) return;
    setRepeats(preset.repeats);
    setTokens(preset.total_cloud_tokens);
    setMinutes(preset.total_minutes);
    setPicked(preset.tasks);
  }, [preset]);

  const current = status.data;
  const running = current?.state === 'running' || current?.state === 'starting';
  const refreshBench = () => qc.invalidateQueries({ queryKey: ['bench'] });
  const start = useMutation({
    mutationFn: () => api('/bench/start', {
      suite, task_ids: picked, repeats, total_cloud_tokens: tokens, total_minutes: minutes, per_run_scale: scale,
      model_ids: chosen.map((p) => p.id),
    }),
    onSuccess: () => { setShown(''); return refreshBench(); },
  });
  const stop = useMutation({ mutationFn: () => api('/bench/stop', {}), onSuccess: refreshBench });

  const results = history.data ?? [];
  const viewId = shown || current?.id || results[0]?.id || '';
  const compareId = baseId === viewId ? '' : baseId;
  const loaded = useQuery({
    queryKey: ['bench', 'result', viewId], queryFn: () => api<BenchResult>('/bench/results/' + viewId),
    enabled: !running && results.some((entry) => entry.id === viewId),
  });
  const base = useQuery({
    queryKey: ['bench', 'result', compareId], queryFn: () => api<BenchResult>('/bench/results/' + compareId),
    enabled: !running && Boolean(compareId),
  });
  const result = loaded.data ?? (current?.id === viewId ? current : undefined);
  const validLimits = inRange(repeats, 1, 10) && inRange(minutes, 1, 2000) && inRange(tokens, 1000, 50_000_000) && inRange(scale, 20, 400);
  const canStart = validLimits && picked.length > 0 && chosen.length > 0 && !start.isPending && !status.isPending;
  const actionError = start.error ?? stop.error;

  if (!cat) return catalog.error ? <Notice tone="bad">读取评测任务失败。{catalog.error.message}</Notice> : <p className="muted">正在读取评测任务…</p>;
  return (
    <div className="bench">
      <h2>开发评测</h2>
      {running ? <Progress result={current} catalog={cat} stopping={stop.isPending} onStop={() => stop.mutate()} /> : (
        <Card title="运行评测">
          <div className="bench-fields">
            <Field label="测试范围">
              <select value={suite} onChange={(e) => setSuite(e.target.value)}>
                {Object.keys(cat.suites).map((id) => <option key={id} value={id}>{suiteName(id, cat)}</option>)}
                <option value="custom">自选任务</option>
              </select>
            </Field>
            <Field label="使用模型">
              <select value={pinnedProfile} onChange={(e) => setPinnedProfile(e.target.value)}>
                <option value="">按任务路由配置</option>
                {pinnedProfile && !ready.some((p) => p.id === pinnedProfile) && <option value={pinnedProfile}>所选模型当前不可用</option>}
                {ready.map((p) => <option key={p.id} value={p.id}>{profileLabel(p)}</option>)}
              </select>
            </Field>
          </div>
          {suite === 'custom' && (
            <fieldset className="bench-picker">
              <legend>选择任务</legend>
              <ul className="bench-tasks">
                {cat.tasks.map((task) => (
                  <li key={task.id} title={task.goal}>
                    <label className="check">
                      <input type="checkbox" checked={picked.includes(task.id)} onChange={(e) => setPicked(e.target.checked ? [...picked, task.id] : picked.filter((id) => id !== task.id))} />
                      {task.title}
                    </label>
                  </li>
                ))}
              </ul>
            </fieldset>
          )}
          <div className="row wrap">
            <Button variant="primary" disabled={!canStart} onClick={() => start.mutate()}>{start.isPending ? '正在启动…' : '开始评测'}</Button>
            <span className="muted" aria-live="polite">
              {picked.length} 个任务 × {repeats} 轮 · 最多 {minutes} 分钟{includesCloud ? ` · 云端上限 ${fmtTokens(tokens)} token` : chosen.length ? ' · 仅本地模型' : ''}
            </span>
          </div>
          {profiles.isSuccess && !chosen.length && <Notice tone="warn">没有可用于本次评测的模型。<Link to="/settings/local">打开本地运行时</Link></Notice>}
          {profiles.error && <Notice tone="bad">读取模型失败。{profiles.error.message}</Notice>}
          <details className="bench-disclosure">
            <summary>运行设置与任务清单</summary>
            <div className="bench-fields">
              <Field label="每个任务的重复次数"><input type="number" min={1} max={10} value={repeats} onChange={(e) => setRepeats(Number(e.target.value))} /></Field>
              <Field label="时间上限（分钟）"><input type="number" min={1} max={2000} value={minutes} onChange={(e) => setMinutes(Number(e.target.value))} /></Field>
              <Field label="云端 token 上限"><input type="number" min={1000} max={50_000_000} step={1000} value={tokens} onChange={(e) => setTokens(Number(e.target.value))} /></Field>
              <Field label="单次任务预算（默认的百分比）"><input type="number" min={20} max={400} step={10} value={scale} onChange={(e) => setScale(Number(e.target.value))} /></Field>
            </div>
            {!validLimits && <Notice tone="warn">请检查运行设置中的数字范围。</Notice>}
            <p>{picked.map((id) => taskName(id, cat)).join('、') || '尚未选择任务'}</p>
            <p className="muted">模型候选：{chosen.map(profileLabel).join('、') || '无'}。<Link to="/settings/order">调整任务路由</Link></p>
          </details>
        </Card>
      )}
      {(actionError || status.error) && <Notice tone="bad">{(actionError ?? status.error)?.message}</Notice>}
      {!running && result?.aggregate && <ResultView key={result.id} result={result} catalog={cat} base={compareId ? base.data : undefined} />}
      {!running && loaded.isFetching && !result && <p className="muted">正在载入结果…</p>}
      {!running && (loaded.error || base.error || history.error) && <Notice tone="bad">读取评测结果失败。{(loaded.error ?? base.error ?? history.error)?.message}</Notice>}
      {!running && !result && !history.isPending && !history.error && !results.length && <p className="muted">还没有评测结果。可以从最小检查开始。</p>}
      {!running && results.length > 0 && (
        <details className="card-details bench-disclosure">
          <summary>历史结果与对比</summary>
          <div className="bench-fields">
            <Field label="查看结果">
              <select value={shown} onChange={(e) => { setShown(e.target.value); setBaseId(''); }}>
                <option value="">最新一次</option>
                {results.map((r) => <option key={r.id} value={r.id}>{new Date(r.started * 1000).toLocaleString()} · {suiteName(r.suite, cat)} · {r.passed}/{r.runs} 通过</option>)}
              </select>
            </Field>
            <Field label="对比基线">
              <select value={compareId} onChange={(e) => setBaseId(e.target.value)}>
                <option value="">不对比</option>
                {results.filter((r) => r.id !== viewId).map((r) => <option key={r.id} value={r.id}>{r.id} · {suiteName(r.suite, cat)} · {r.passed}/{r.runs} 通过</option>)}
              </select>
            </Field>
          </div>
        </details>
      )}
    </div>
  );
}

function Progress({ result, catalog, stopping, onStop }: { result: BenchResult; catalog: BenchCatalog; stopping: boolean; onStop: () => void }) {
  const cur = result.current;
  const o = result.aggregate.overall;
  const elapsed = result.server_time == null ? result.used.seconds : Math.max(result.used.seconds, result.server_time - result.started);
  const recordByRun = new Map(result.records.map((r) => [`${r.task}#${r.repeat}`, r]));
  return (
    <Card title="评测进行中" actions={<Button variant="danger" disabled={stopping} onClick={onStop}>{stopping ? '正在停止…' : '停止评测'}</Button>}>
      <p aria-live="polite"><strong>{result.records.length} / {result.total} 项已处理</strong> · {o.passed} 项通过{cur && <> · 正在运行 {taskName(cur.task, catalog)}{cur.stage ? ` · ${stageLabels[cur.stage] ?? cur.stage}` : ''}</>}</p>
      <progress className="bench-progress" max={Math.max(1, result.total)} value={result.records.length} aria-label="评测进度" />
      <details className="bench-disclosure">
        <summary>运行队列与用量</summary>
        <p className="muted">已用 {duration(elapsed)} · 云端 {fmtTokens(result.used.cloud_tokens + (cur?.run_cloud_tokens ?? 0))} / {fmtTokens(result.config.total_cloud_tokens)} token{cur?.model ? ` · 当前模型 ${cur.model}` : ''}</p>
        <ol className="bench-queue">
          {(result.plan ?? result.records).map((p) => {
            const key = `${p.task}#${p.repeat}`;
            const record = recordByRun.get(key);
            return <li key={key}>{taskName(p.task, catalog)}{result.config.repeats > 1 ? ` #${p.repeat}` : ''} · {record ? OUTCOMES[record.status] : cur?.task === p.task && cur.repeat === p.repeat ? '进行中' : '待运行'}</li>;
          })}
        </ol>
      </details>
    </Card>
  );
}

function ResultView({ result, catalog, base }: { result: BenchResult; catalog: BenchCatalog; base?: BenchResult }) {
  const o = result.aggregate.overall;
  const byTask = new Map<string, BenchRecord[]>();
  for (const record of result.records) {
    const group = byTask.get(record.task) ?? [];
    group.push(record);
    byTask.set(record.task, group);
  }
  const status = result.state === 'stopped' ? '已停止' : result.state === 'error' ? '运行出错' : '已结束';
  const activeMechanisms = Object.entries(o.mechanisms).filter(([, count]) => count > 0);
  return (
    <Card title="评测结果" subtitle={`${suiteName(result.config.suite, catalog)} · ${status}`}>
      {result.message && <Notice tone={result.state === 'error' ? 'bad' : 'warn'}>{result.message}</Notice>}
      <div className="bench-summary">
        <div><span>独立验收通过</span><strong>{o.passed} / {o.runs}</strong><small>{percent(o.rate)}</small></div>
        <div><span>总耗时</span><strong>{duration(result.used.seconds)}</strong></div>
        <div><span>云端 token</span><strong>{fmtTokens(o.cloud_tokens)}</strong></div>
      </div>
      {(o.false_pass > 0 || o.skipped > 0) && (
        <p className="row wrap">
          {o.false_pass > 0 && <Badge tone="warn">{o.false_pass} 项假通过（独立验收未通过）</Badge>}
          {o.skipped > 0 && <Badge>{o.skipped} 项未运行，未计入通过率</Badge>}
        </p>
      )}
      {base && <p className="muted">基线 {base.id} · {base.aggregate.overall.passed}/{base.aggregate.overall.runs} 通过（{percent(base.aggregate.overall.rate)}） · {duration(base.used.seconds)} · 云端 {fmtTokens(base.aggregate.overall.cloud_tokens)} token</p>}
      {byTask.size > 0 && (
        <div className="bench-outcomes" aria-label="各任务结果">
          {[...byTask].map(([id, records]) => {
            const counts = new Map<BenchStatus, number>();
            for (const r of records) counts.set(r.status, (counts.get(r.status) ?? 0) + 1);
            return (
              <details key={id} className="bench-task-result">
                <summary>
                  <strong>{taskName(id, catalog)}</strong>
                  <span className="bench-outcome-labels">{[...counts].map(([s, count]) => <span key={s} className={`run-${s}`}>{OUTCOMES[s]}{records.length > 1 ? ` ${count}` : ''}</span>)}</span>
                </summary>
                <ol className="bench-attempts">
                  {records.map((r) => (
                    <li key={r.repeat}>
                      第 {r.repeat} 轮 · {OUTCOMES[r.status]}{r.status !== 'skipped' && ` · ${duration(r.seconds)} · ${r.calls} 次模型调用 · 云端 ${fmtTokens(r.cloud_tokens)} token`}
                      {[r.oracle_reason, r.note, r.stop_reason].filter(Boolean).map((reason, i) => <p key={i}>{reason}</p>)}
                    </li>
                  ))}
                </ol>
              </details>
            );
          })}
        </div>
      )}
      <details className="bench-disclosure">
        <summary>更多指标与运行信息</summary>
        <p>运行编号 {result.id} · 版本 {result.commit ?? '未知'} · 模型 {result.models?.join('、') || '—'}</p>
        <p>每次通过的耗时 {o.seconds_per_pass == null ? '—' : duration(o.seconds_per_pass)} · 每次通过的云端 token {o.cloud_tokens_per_pass == null ? '—' : fmtTokens(o.cloud_tokens_per_pass)}</p>
        <p>稳定等级 {o.stable_level == null ? '—' : `L${o.stable_level}`} · 最高通过等级 {o.ceiling_level == null ? '—' : `L${o.ceiling_level}`} · 加权分 {o.weighted_score ?? '—'}</p>
        {Object.keys(result.aggregate.by_level).length > 0 && (
          <div className="scroll-x">
            <table className="bench-table">
              <thead><tr><th>等级</th><th>通过 / 已运行</th><th>平均耗时</th><th>平均云端 token</th></tr></thead>
              <tbody>{Object.values(result.aggregate.by_level).map((r) => (
                <tr key={r.level}><td>L{r.level} {catalog.levels[r.level]}</td><td>{r.passed} / {r.runs}</td><td>{r.mean_seconds == null ? '—' : duration(r.mean_seconds)}</td><td>{r.mean_cloud_tokens == null ? '—' : fmtTokens(r.mean_cloud_tokens)}</td></tr>
              ))}</tbody>
            </table>
          </div>
        )}
        {activeMechanisms.length > 0 && <p>触发机制：{activeMechanisms.map(([key, count]) => `${MECHANISMS[key] ?? key} × ${count}`).join('、')}</p>}
      </details>
    </Card>
  );
}
