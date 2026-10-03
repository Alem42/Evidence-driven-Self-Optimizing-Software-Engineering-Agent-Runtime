import { useEffect, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { api } from '../../api/client';
import { qk, useProfiles } from '../../api/queries';
import type { Profile } from '../../api/types';
import { normalizeProfiles, parseProfile, profileLabel, profileReady } from '../../entities/profiles';
import { Button, Card, Field, Notice } from '../../shared/ui';

type Form = Partial<Profile> & { api_key?: string; persist_key?: boolean; new?: boolean };

const CLOUD: Form = {
  name: 'DeepSeek V4 Pro', base_url: 'https://api.deepseek.com', model: 'deepseek-v4-pro', api_key: '', timeout_seconds: 60,
  max_output_tokens: 8192, token_parameter: 'max_tokens', thinking: 'disabled', model_type: 'cloud', protocol: 'openai',
  enabled: true, level: 2, priority: 0, context_limit: 32768, roles: [],
};

const ROLE_NAMES: Record<string, string> = {
  project_planner: 'Planner', project_tester: 'Tester', project_developer: 'Developer', project_repair: '实现修复',
  project_test_revision: '测试修订', project_test_reviewer: '语义评审', verifier: '工具验证', code_generation: '旧单文件生成',
};

// 模型配置：左列表右表单。保存后只回填非密钥字段。 Profile list + form; only public fields are refilled.
export function ModelsSection() {
  const qc = useQueryClient();
  const query = useProfiles();
  const catalog = normalizeProfiles(query.data);
  const [values, setValues] = useState<Form>({ ...CLOUD, new: true });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const [importText, setImportText] = useState('');
  const [initialised, setInitialised] = useState(false);

  useEffect(() => {
    if (!query.data || initialised) return;
    const first = catalog.profiles.find((p) => p.id === catalog.active_id) ?? catalog.profiles[0];
    setValues(first ? { ...CLOUD, ...first, api_key: '' } : { ...CLOUD, new: true });
    setInitialised(true);
  }, [query.data, initialised, catalog]);

  const pick = (p: Profile) => { setValues({ ...CLOUD, ...p, api_key: '' }); setError(''); setMessage(''); };
  const set = (patch: Form) => setValues((v) => ({ ...v, ...patch }));
  const roles = values.roles ?? [];

  async function save(body: unknown) {
    setBusy(true); setError(''); setMessage('');
    try {
      const result = normalizeProfiles(await api('/settings', body));
      qc.setQueryData(qk.profiles, result);
      const next = result.profiles.find((p) => p.id === (values.id ?? result.active_id)) ?? result.profiles.find((p) => p.id === result.active_id);
      setValues(next ? { ...CLOUD, ...next, api_key: '' } : { ...CLOUD, new: true });
      setMessage(result.key_storage === 'local_file' ? '已保存到本地配置，重启后可继续使用。' : '已保存。密钥只在当前服务内存中。');
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }

  async function test() {
    setBusy(true); setError(''); setMessage('连接测试中…');
    try {
      const r = await api<{ message: string; usage?: { total_tokens: number } }>('/settings/test', { id: values.id });
      setMessage(r.message + (r.usage ? ' · ' + r.usage.total_tokens + ' tokens' : ''));
      qc.invalidateQueries({ queryKey: qk.profiles });
    } catch (e) { setError((e as Error).message); setMessage(''); } finally { setBusy(false); }
  }

  function importProfile() {
    try {
      const parsed = parseProfile(importText);
      set({ ...(parsed.model_type === 'local' ? { protocol: 'ollama', timeout_seconds: 180, level: 1, context_limit: 8192 } : {}), ...parsed, new: true } as Form);
      setImportText(''); setError(''); setMessage('已填入表单，请检查后保存。');
    } catch (e) { setError((e as Error).message); }
  }

  const local = values.model_type === 'local';
  const num = (key: keyof Form, min?: number, max?: number) => (
    <input type="number" min={min} max={max} value={(values[key] as number | null | undefined) ?? ''} onChange={(e) => set({ [key]: e.target.value === '' ? null : Number(e.target.value) } as Form)} />
  );

  if (query.isLoading) return <p className="muted">读取配置…</p>;
  if (!catalog.compatible) return <Notice tone="bad">后端版本过旧，无法管理模型。请重启 MASA 服务，不要继续使用旧进程。</Notice>;

  return (
    <div className="split">
      <div className="list-col">
        <Button variant="primary" disabled={busy} onClick={() => { setValues({ ...CLOUD, new: true }); setError(''); setMessage(''); }}>＋ 添加模型</Button>
        {catalog.profiles.map((p) => (
          <button key={p.id} disabled={busy} className={`list-item ${values.id === p.id ? 'current' : ''}`} onClick={() => pick(p)}>
            <strong>{profileLabel(p)}</strong>
            <small>{p.model} · 优先级 {p.priority ?? 0}</small>
            <small>
              {p.id === catalog.active_id ? '默认 · ' : ''}
              {p.enabled === false ? '已禁用' : profileReady(p) ? '可调用' : '缺少密钥'} · {p.last_test?.ok ? '测试通过' : p.last_test ? '测试失败' : '未测试'}
            </small>
          </button>
        ))}
      </div>

      <form className="form-col" onSubmit={(e) => { e.preventDefault(); save(values); }}>
        <Card title={values.new ? '新模型' : values.name || values.model}>
          <div className="grid2">
            <Field label="模型类型">
              <select
                value={values.model_type}
                onChange={(e) => {
                  const isLocal = e.target.value === 'local';
                  set({
                    model_type: e.target.value as Profile['model_type'], protocol: isLocal ? 'ollama' : 'openai',
                    base_url: isLocal ? 'http://127.0.0.1:11434' : 'https://api.deepseek.com', model: isLocal ? '' : 'deepseek-v4-pro',
                    name: isLocal ? 'Ollama 本地模型' : 'DeepSeek V4 Pro', level: isLocal ? 1 : 2, timeout_seconds: isLocal ? 180 : 60,
                    context_limit: isLocal ? 8192 : 32768, api_key: '',
                  });
                }}
              >
                <option value="cloud">云端 API</option>
                <option value="local">本地 Ollama / 兼容服务</option>
              </select>
            </Field>
            <Field label="传输协议">
              <select value={values.protocol} onChange={(e) => set({ protocol: e.target.value as Profile['protocol'] })}>
                <option value="openai">OpenAI compatible（地址通常含 /v1）</option>
                {local && <option value="ollama">Ollama 原生（地址不含 /v1）</option>}
              </select>
            </Field>
          </div>
          <label className="check"><input type="checkbox" checked={values.enabled !== false} onChange={(e) => set({ enabled: e.target.checked })} /> 启用此模型</label>
          <Field label="配置名称"><input required value={values.name ?? ''} onChange={(e) => set({ name: e.target.value })} /></Field>
          <Field label="Base URL"><input required value={values.base_url ?? ''} onChange={(e) => set({ base_url: e.target.value })} /></Field>
          <Field label="模型 ID" hint={local ? 'Ollama 填完整 tag，例如 gemma4:12b' : undefined}><input required value={values.model ?? ''} onChange={(e) => set({ model: e.target.value })} /></Field>
          {!local && (
            <>
              <Field label="API Key" hint="密钥不回显；留空保留已有密钥。修改后先保存再测试。">
                <input type="password" autoComplete="off" value={values.api_key ?? ''} onChange={(e) => set({ api_key: e.target.value })} />
              </Field>
              <label className="check">
                <input type="checkbox" disabled={catalog.key_storage === 'local_file'} checked={catalog.key_storage === 'local_file' || Boolean(values.persist_key)} onChange={(e) => set({ persist_key: e.target.checked })} />
                在本地持久保存密钥（不提交 Git）
              </label>
            </>
          )}
        </Card>

        <Card title="能力与限制" subtitle="等级由你指定，不是自动评测；目前仍是固定模型选择，尚不自动升级。">
          <div className="grid2">
            <Field label="能力等级（越大越强）">{num('level', 1, 100)}</Field>
            <Field label="同等级优先级（越小越先）">{num('priority', 0, 10000)}</Field>
            <Field label="上下文限制（token）">{num('context_limit', 512, 262144)}</Field>
            <Field label="请求超时（秒）">{num('timeout_seconds', 1, local ? 600 : 60)}</Field>
            <Field label="输出 token 上限">{num('max_output_tokens', 64, 8192)}</Field>
          </div>
        </Card>

        <details className="card-details">
          <summary>可承担角色与可选价格</summary>
          <p className="hint">不选角色表示全部允许。价格为人民币/百万 token，仅保存配置，尚不计算任务费用。</p>
          <div className="check-grid">
            {Object.entries(ROLE_NAMES).map(([key, label]) => (
              <label className="check" key={key}>
                <input type="checkbox" checked={roles.includes(key)} onChange={(e) => set({ roles: e.target.checked ? [...roles, key] : roles.filter((r) => r !== key) })} />
                {label}
              </label>
            ))}
          </div>
          <div className="grid2">
            <Field label="输入单价">{num('input_price_per_million', 0)}</Field>
            <Field label="输出单价">{num('output_price_per_million', 0)}</Field>
          </div>
        </details>
        <details className="card-details">
          <summary>高级调用参数</summary>
          <div className="grid2">
            <Field label="输出参数">
              <select value={values.token_parameter} onChange={(e) => set({ token_parameter: e.target.value })}>
                <option value="max_tokens">max_tokens · DeepSeek</option>
                <option value="max_completion_tokens">max_completion_tokens</option>
              </select>
            </Field>
            <Field label="思考模式">
              <select value={values.thinking} onChange={(e) => set({ thinking: e.target.value })}>
                <option value="disabled">关闭</option>
                <option value="enabled">开启</option>
                <option value="auto">提供商默认</option>
              </select>
            </Field>
          </div>
        </details>
        <details className="card-details">
          <summary>从 JSON 导入一组 API</summary>
          <textarea rows={4} value={importText} onChange={(e) => setImportText(e.target.value)} placeholder={'{"base_url":"https://api.deepseek.com","model":"deepseek-v4-pro","api_key":"…"}'} />
          <Button size="sm" disabled={busy || !importText.trim()} onClick={importProfile}>解析并填入表单</Button>
        </details>

        {message && <Notice tone="ok">{message}</Notice>}
        {error && <Notice tone="bad">{error}</Notice>}
        <div className="row wrap sticky-actions">
          <Button variant="primary" type="submit" disabled={busy}>保存配置</Button>
          <Button disabled={busy || !values.id} onClick={test}>测试连接（真实调用）</Button>
          <Button disabled={busy || !values.id} onClick={() => save({ action: 'select', id: values.id })}>设为默认</Button>
          <span className="spacer" />
          {!local && <Button variant="ghost" disabled={busy || !values.id} onClick={() => save({ id: values.id, clear_key: true })}>清除密钥</Button>}
          <Button variant="danger" disabled={busy || !values.id} onClick={() => save({ action: 'delete', id: values.id })}>删除</Button>
        </div>
      </form>
    </div>
  );
}
