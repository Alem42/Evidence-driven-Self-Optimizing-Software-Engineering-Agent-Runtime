import { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
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
