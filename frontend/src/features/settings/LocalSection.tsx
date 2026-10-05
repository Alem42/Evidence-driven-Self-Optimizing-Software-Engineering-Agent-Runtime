import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../../api/client';
import { startJob } from '../../api/jobs';
import { qk } from '../../api/queries';
import { useActivity } from '../../app/activity';
import { gib, nsToSeconds } from '../../entities/text';
import { Button, Card, Notice, Spinner } from '../../shared/ui';

interface OllamaModel { name: string; size: number; digest: string; details?: { parameter_size?: string; quantization_level?: string } }
interface OllamaRunning { name: string; size: number; size_vram: number; context_length?: number }
interface Catalog { version: string; models: OllamaModel[]; running: OllamaRunning[] }

// 本地运行时：模型列表、选择/加载/释放/测速（官方接口，不提供任意 shell）。
// Local runtime: list/select/load/unload/benchmark via the official API only.
export function LocalSection() {
  const qc = useQueryClient();
  const { working } = useActivity();
  const catalog = useQuery({ queryKey: qk.ollama, queryFn: () => api<Catalog>('/ollama'), refetchInterval: 8000 });
  const [selected, setSelected] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [result, setResult] = useState<any>(null);
  const [detail, setDetail] = useState<unknown>(null);

  const models = catalog.data?.models ?? [];
  const name = models.some((m) => m.name === selected) ? selected : models.find((m) => m.name.startsWith('glm-4.7-flash'))?.name ?? models[0]?.name ?? '';
  const model = models.find((m) => m.name === name);
  const running = catalog.data?.running.find((m) => m.name === name);

  function act(action: 'select' | 'show' | 'load' | 'unload' | 'test') {
    setBusy(true); setError(''); setResult(null);
    startJob('/ollama/action', { action, model: name }, {
      label: 'Ollama ' + action,
      onDone: (job) => {
        setBusy(false);
        if (job.status !== 'completed') return setError(job.error || '操作中断，请检查服务状态；不会自动重试。');
        if (action === 'show') setDetail(job.result);
        else {
          setResult(job.result);
          if (action === 'select') qc.invalidateQueries({ queryKey: qk.profiles });
        }
        qc.invalidateQueries({ queryKey: qk.ollama });
      },
    }).catch((e) => { setBusy(false); setError(e.message); });
  }

  const m = result?.metrics;
  return (
    <div className="stack">
      <Card
        title={catalog.data ? `本地模型 · Ollama ${catalog.data.version}` : '本地模型 · Ollama'}
        subtitle="固定连接 127.0.0.1:11434。选择用于新任务；加载/释放影响本机内存；执行中的任务不会切换。"
        actions={<Button size="sm" onClick={() => catalog.refetch()} disabled={catalog.isFetching}>刷新</Button>}
      >
        {catalog.isError && <Notice tone="bad">{(catalog.error as Error).message}</Notice>}
        {working && <Notice tone="warn">项目正在运行：仍可查看模型、硬件与诊断；切换、加载、释放和测速待任务结束后恢复。</Notice>}
        {error && <Notice tone="bad">{error}</Notice>}
        <div className="split">
          <div className="list-col">
            {catalog.isLoading && <Spinner />}
            {models.map((x) => (
              <button key={x.name} disabled={busy} className={`list-item ${x.name === name ? 'current' : ''}`} onClick={() => { setSelected(x.name); setDetail(null); setResult(null); }}>
                <strong>{x.name}</strong>
                <small>{gib(x.size)} · {x.details?.parameter_size} · {x.details?.quantization_level}</small>
                <small>{catalog.data?.running.some((r) => r.name === x.name) ? '● 已加载' : '○ 未加载'}</small>
              </button>
            ))}
          </div>
          <div className="form-col">
            {model && (
              <>
                <h3>{name}</h3>
                <dl className="facts">
                  <dt>磁盘大小</dt><dd>{gib(model.size)}</dd>
                  <dt>参数 / 量化</dt><dd>{model.details?.parameter_size} / {model.details?.quantization_level}</dd>
                  <dt>内存 / 显存</dt><dd>{running ? gib(running.size) + ' / ' + gib(running.size_vram) : '未加载'}</dd>
                  <dt>加载上下文</dt><dd>{running?.context_length ?? '未加载'}</dd>
                  <dt>权重摘要</dt><dd><code>{model.digest}</code></dd>
                </dl>
                <div className="row wrap">
                  <Button variant="primary" disabled={busy || working} onClick={() => act('select')}>用于项目 / 设为默认</Button>
                  <Button disabled={busy} onClick={() => act('show')}>模型详情</Button>
                  <Button disabled={busy || working} onClick={() => act('load')}>加载</Button>
                  <Button disabled={busy || working} onClick={() => act('unload')}>释放内存</Button>
                  <Button disabled={busy || working} onClick={() => act('test')}>真实调用测速</Button>
                </div>
                {busy && <Notice tone="warn"><Spinner /> 操作中，不会自动重试。</Notice>}
                {result?.message && <Notice tone="ok">{result.message}</Notice>}
                {m && (
                  <Notice tone="ok">
                    <strong>本次真实推理：{m.generation_tokens_per_second ?? '未知'} tokens/s</strong>
                    <div>提示处理 {m.prompt_tokens_per_second ?? '未知'} tok/s · 总耗时 {nsToSeconds(m.total_duration)} · 加载 {nsToSeconds(m.load_duration)}</div>
                    <div>输入 {m.prompt_eval_count ?? '未知'} tok · 输出 {m.eval_count ?? '未知'} tok · 生成耗时 {nsToSeconds(m.eval_duration)}</div>
                  </Notice>
                )}
                {detail != null && <details open><summary>模型信息 / show</summary><pre className="raw">{JSON.stringify(detail, null, 2)}</pre></details>}
                {result && <details><summary>完整操作结果</summary><pre className="raw">{JSON.stringify(result, null, 2)}</pre></details>}
              </>
            )}
          </div>
        </div>
      </Card>
      <OrphanCard />
      <HardwareCard />
    </div>
  );
}

interface Hardware {
  collected_at: string; cache_seconds: number;
  gpu: { available: boolean; warning?: string; devices: { index: number; name: string; total_mib?: number; used_mib?: number; free_mib?: number; utilization_percent?: number | null }[] };
  memory: { available: boolean; warning?: string; total_bytes?: number; modules: { capacity_bytes?: number; configured_speed_mhz?: number | null; speed_mhz?: number | null }[] };
}

function HardwareCard() {
  const [open, setOpen] = useState(false);
  const hw = useQuery({ queryKey: ['hardware'], queryFn: () => api<Hardware>('/hardware'), enabled: open, staleTime: 15_000 });
  const d = hw.data;
  return (
    <Card title="硬件与资源" subtitle="只读采集，不需要管理员权限；显存是采集瞬间的结果。" actions={<Button size="sm" onClick={() => { setOpen(true); if (open) hw.refetch(); }} disabled={hw.isFetching}>{open ? '刷新' : '读取硬件摘要'}</Button>}>
      {hw.isError && <Notice tone="bad">{(hw.error as Error).message}</Notice>}
      {d && (
        <>
          <h4>显卡</h4>
          {!d.gpu.available && <Notice tone="warn">暂时无法读取显卡信息。{d.gpu.warning}</Notice>}
          {d.gpu.devices.map((g) => (
            <dl className="facts" key={g.index}>
              <dt>显卡 {g.index}</dt><dd>{g.name}</dd>
              <dt>总 / 已用 / 空闲</dt><dd>{[g.total_mib, g.used_mib, g.free_mib].map((n) => (typeof n === 'number' ? (n / 1024).toFixed(2) + ' GiB' : '未知')).join(' / ')}</dd>
              <dt>GPU 利用率</dt><dd>{g.utilization_percent == null ? '未知' : g.utilization_percent + '%'}</dd>
            </dl>
          ))}
          <h4>系统内存</h4>
          <dl className="facts"><dt>物理内存总量</dt><dd>{gib(d.memory.total_bytes)}</dd></dl>
          {d.memory.modules.map((x, i) => (
            <dl className="facts" key={i}>
              <dt>模块 {i + 1}</dt><dd>{gib(x.capacity_bytes)}</dd>
              <dt>配置 / 标称频率</dt><dd>{[x.configured_speed_mhz, x.speed_mhz].map((n) => (n == null ? '未知' : n + ' MHz')).join(' / ')}</dd>
            </dl>
          ))}
          <p className="hint">最近采集 {new Date(d.collected_at).toLocaleTimeString()} · 后端缓存 {d.cache_seconds} 秒</p>
        </>
      )}
    </Card>
  );
}

interface Runner { pid: number; parent_pid: number; orphan: boolean; memory_mb: number | null; started: string | null; model_blob: string | null }

// 孤儿模型进程：Ollama 的 llama-server 在 ollama serve 被杀后可能继续占着显存。检测（只读）+ 一键清理，只会清理确认是孤儿的 Ollama 运行进程。
// Orphaned model runners: Ollama's llama-server can keep holding VRAM after `ollama serve` dies. Detect (read-only) and clean up on request.
function OrphanCard() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ['orphans'], queryFn: async () => (await api<{ runners: Runner[] }>('/ollama/orphans')).runners, refetchInterval: 15_000 });
  const clean = useMutation({
    mutationFn: () => api<{ killed: number[]; freed_memory_mb: number }>('/ollama/orphans/clean', {}),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['orphans'] }); qc.invalidateQueries({ queryKey: ['hardware'] }); },
  });
  const orphans = (q.data ?? []).filter((r) => r.orphan);
  return (
    <Card
      title="模型运行进程"
      subtitle="Ollama 的 llama-server 在 ollama serve 被杀、崩溃后可能继续占着显存和内存。"
      actions={orphans.length > 0 && <Button size="sm" variant="danger" disabled={clean.isPending} onClick={() => clean.mutate()}>{clean.isPending ? '清理中…' : `清理 ${orphans.length} 个孤儿进程`}</Button>}
    >
      {q.isError && <Notice tone="bad">{(q.error as Error).message}</Notice>}
      {orphans.length > 0 ? (
        <Notice tone="warn">
          发现 {orphans.length} 个<strong>孤儿</strong>模型进程（父进程 ollama serve 已不存在），共占用约 {orphans.reduce((a, r) => a + (r.memory_mb ?? 0), 0)} MB 内存和对应显存：
          {orphans.map((r) => <div key={r.pid} className="mono">PID {r.pid} · {r.memory_mb ?? '?'} MB · {r.model_blob ?? '未知模型'} · 启动于 {r.started ? new Date(r.started).toLocaleTimeString() : '?'}</div>)}
        </Notice>
      ) : (
        <p className="muted">{(q.data ?? []).length ? `${q.data!.length} 个运行进程，均有父进程，状态正常。` : '没有模型运行进程。'}</p>
      )}
      {clean.isSuccess && <p className="ok-text">已清理 {clean.data.killed.length} 个，释放约 {clean.data.freed_memory_mb} MB 内存。</p>}
      {clean.isError && <Notice tone="bad">{(clean.error as Error).message}</Notice>}
      <p className="hint">只会终止「确认父进程已不存在的 Ollama llama-server」，其它进程不受影响；清理前会再次核对。</p>
    </Card>
  );
}
