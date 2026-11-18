import { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../../api/client';
import { qk } from '../../api/queries';
import { Badge, Button, Card, Notice } from '../../shared/ui';

interface Probe { reachable: boolean; engine?: string; served?: string[]; n_ctx?: number; model_path?: string; build?: string }
interface ServerProfile extends Probe {
  id: string; name: string; model: string; base_url: string; enabled: boolean; level: number; priority: number;
  context_limit: number; is_default: boolean; model_listed: boolean | null;
}
interface Found extends Probe { base_url: string }
interface Runtimes { profiles: ServerProfile[]; found: Found[] }

const engineName = (e?: string) => (e === 'llama.cpp' ? 'llama.cpp' : '兼容服务（Strata / LM Studio 等）');
const fileName = (p?: string) => (p ? p.split(/[\\/]/).pop() : undefined);

// 本地 OpenAI 兼容服务（llama.cpp、Strata、LM Studio）：每个配置的在线状态、实际服务的模型与上下文，可设为默认、测试；
// 并列出本机上已经在跑但还没添加的服务，一键添加。Ollama 的模型仍在下面的卡片里。
// Local OpenAI-compatible servers: live state of each profile, the model and context it really serves, set-default and test, plus servers already running but not yet added.
export function LocalServers() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: qk.localRuntimes, queryFn: () => api<Runtimes>('/local/runtimes'), refetchInterval: 8000 });
  const [busy, setBusy] = useState('');
  const [message, setMessage] = useState<{ tone: 'ok' | 'bad'; text: string } | null>(null);

  async function run(key: string, work: () => Promise<string>) {
    setBusy(key); setMessage(null);
    try { setMessage({ tone: 'ok', text: await work() }); }
    catch (e) { setMessage({ tone: 'bad', text: (e as Error).message }); }
    finally { setBusy(''); qc.invalidateQueries({ queryKey: qk.localRuntimes }); qc.invalidateQueries({ queryKey: qk.profiles }); }
  }
  const test = (p: ServerProfile) => run(p.id, async () => {
    const r = await api<{ ok: boolean; message: string; usage?: { total_tokens: number | null } }>('/settings/test', { id: p.id });
    if (!r.ok) throw new Error(r.message);
    return `${p.name}：${r.message}`;
  });
  const makeDefault = (p: ServerProfile) => run(p.id, async () => { await api('/settings', { action: 'select', id: p.id }); return `已把 ${p.name} 设为默认。`; });
  const add = (f: Found) => run(f.base_url, async () => {
    const model = f.served?.[0] ?? '';
    if (!model) throw new Error('这个服务没有报告模型名，请到「模型配置」里手动添加。');
    await api('/settings', {
      action: 'save', new: true, name: `${f.engine === 'llama.cpp' ? 'llama.cpp' : '本地服务'} ${model}`, model_type: 'local', protocol: 'openai',
      base_url: f.base_url, model, level: 1, priority: 0, context_limit: f.n_ctx ?? 16384, max_output_tokens: 4096, timeout_seconds: 600, enabled: true,
    });
    return `已添加 ${model}。请点「测试连接」确认。`;
  });

  const profiles = q.data?.profiles ?? [];
  const found = q.data?.found ?? [];
  return (
    <Card
      title="本地模型 · llama.cpp / Strata / 兼容服务"
      subtitle="这些服务一次只服务一个模型，由你自己启动（例如 serve.ps1）；这里显示它们现在是否在线、实际服务哪个模型、上下文多大。"
      actions={<Button size="sm" onClick={() => q.refetch()} disabled={q.isFetching}>刷新</Button>}
    >
      {q.isError && <Notice tone="bad">{(q.error as Error).message}</Notice>}
      {message && <Notice tone={message.tone}>{message.text}</Notice>}
      {!q.isLoading && profiles.length === 0 && found.length === 0 && <p className="muted">还没有 llama.cpp / Strata 配置，也没有在常用端口（8080、8081、8082、8090、1234、8000）上发现正在运行的服务。启动服务后点「刷新」。</p>}
      <div className="list-col">
        {profiles.map((p) => (
          <div key={p.id} className={`list-item ${p.is_default ? 'current' : ''}`} style={{ cursor: 'default' }}>
            <div className="row wrap">
              <strong>{p.name}</strong>
              {p.is_default && <Badge tone="ok">默认</Badge>}
              <Badge tone={p.reachable ? 'ok' : 'bad'} dot>{p.reachable ? '在线' : '未启动'}</Badge>
              {!p.enabled && <Badge>已禁用</Badge>}
              <span className="spacer" />
              <Button size="sm" disabled={!!busy || !p.reachable} onClick={() => test(p)}>测试连接</Button>
              <Button size="sm" variant="primary" disabled={!!busy || p.is_default || !p.enabled} onClick={() => makeDefault(p)}>设为默认</Button>
            </div>
            <small>{p.base_url} · 配置的模型 ID：{p.model} · 等级 {p.level} / 优先级 {p.priority}</small>
            {p.reachable ? (
              <small>
                {engineName(p.engine)} · 服务端报告：{p.served?.length ? p.served.join('、') : '未知'}
                {p.n_ctx ? ` · 实际上下文 ${p.n_ctx}` : ''}{fileName(p.model_path) ? ` · ${fileName(p.model_path)}` : ''}
              </small>
            ) : (
              <small>没有服务在监听这个地址。请先启动对应的服务（端口要与上面的地址一致）。</small>
            )}
            {p.reachable && p.model_listed === false && <small>⚠ 配置的模型 ID 不在服务报告的列表里；llama.cpp 会忽略模型名，其它服务可能拒绝。</small>}
            {p.reachable && p.n_ctx && p.context_limit > p.n_ctx && <small>⚠ 配置的上下文限制（{p.context_limit}）大于服务实际上下文（{p.n_ctx}），长提示会被服务端截断。</small>}
          </div>
        ))}
      </div>
      {found.length > 0 && (
        <>
          <h4>发现但还没添加</h4>
          {found.map((f) => (
            <div key={f.base_url} className="row wrap">
              <span>{f.base_url} · {engineName(f.engine)} · {f.served?.join('、') || '未报告模型'}{f.n_ctx ? ` · 上下文 ${f.n_ctx}` : ''}</span>
              <span className="spacer" />
              <Button size="sm" disabled={!!busy} onClick={() => add(f)}>添加为配置</Button>
            </div>
          ))}
        </>
      )}
    </Card>
  );
}
