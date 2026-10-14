import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../../api/client';
import type { PackageInstall, ProjectEnv } from '../../api/types';
import { Badge, Button, Field, Notice } from '../../shared/ui';

// 设置 → 语言 → Python 的“项目环境”：生成的项目跑在 .tools/pyenv 里（像 Go 工具链在 .tools/go），第三方包只装进这里。
// Settings -> Languages -> Python "project environment": generated projects run in .tools/pyenv (like the Go toolchain in .tools/go); third-party packages only ever go in there.
export function ProjectEnvPanel({ language, interpreter, onInterpreter }: { language: string; interpreter: string; onInterpreter: (path: string) => void }) {
  const qc = useQueryClient();
  const key = ['project-env', language] as const;
  const env = useQuery({ queryKey: key, queryFn: () => api<ProjectEnv>(`/languages/${language}/env`), refetchInterval: 20000 });
  const [names, setNames] = useState('');
  const [message, setMessage] = useState<{ tone: 'ok' | 'bad' | 'warn'; text: string } | null>(null);
  const refresh = (fresh: ProjectEnv) => {
    qc.setQueryData(key, fresh);
    qc.invalidateQueries({ queryKey: ['languages'] });
  };
  const act = useMutation({
    mutationFn: (action: 'create' | 'reset') => api<ProjectEnv>(`/languages/${language}/env`, { action }),
    onSuccess: (fresh) => { refresh(fresh); setMessage({ tone: 'ok', text: '项目环境已就绪。' }); },
    onError: (e) => setMessage({ tone: 'bad', text: (e as Error).message }),
  });
  const install = useMutation({
    // 在设置页手动点“安装”本身就是批准（approve:true）；策略是“禁止”时后端会拒绝。 Clicking Install here is the approval (approve: true); the backend refuses when the policy is deny.
    mutationFn: () => api<PackageInstall>(`/languages/${language}/packages`, { packages: names.split(/[\s,]+/).filter(Boolean), approve: true }),
    onSuccess: (done) => {
      if (done.env) refresh(done.env);
      setNames('');
      setMessage({ tone: 'ok', text: `已安装：${(done.installed ?? []).join('、') || '（没有新包）'}；环境 ${done.size_mb} MB。` });
    },
    onError: (e) => setMessage({ tone: 'bad', text: (e as Error).message }),
  });
  // “浏览…”：在运行 masa 的这台电脑上弹出系统的“打开文件”对话框；对话框打不开（没有桌面）时给出原因。 "Browse...": the system's open-file dialog on the machine running masa; the reason is shown when it cannot open.
  const browse = useMutation({
    mutationFn: () => api<{ path: string }>('/pick-file', { kind: 'python', language }),
    onSuccess: ({ path }) => { if (path) onInterpreter(path); },
    onError: (e) => setMessage({ tone: 'bad', text: (e as Error).message }),
  });
  if (env.isLoading || !env.data) return <p className="muted">读取项目环境…</p>;
  const e = env.data;
  const known = e.candidates.some((c) => c.path === interpreter);
  return (
    <div className="stack" style={{ borderTop: '1px solid var(--line, #e5e5e5)', paddingTop: 12 }}>
      <div className="row wrap">
        <strong>项目环境</strong>
        {e.exists ? <Badge tone={e.over_limit ? 'bad' : 'ok'} dot>已创建 · Python {e.version || '?'}</Badge> : <Badge tone="warn" dot>未创建</Badge>}
        {e.exists && <span className="muted">占用 {e.size_mb} / {e.max_mb} MB · {e.packages.length} 个第三方包</span>}
        <code title="项目内的环境目录，和 .tools/go 并列">{e.path}</code>
      </div>
      <div className="row wrap">
        {!e.exists && <Button variant="primary" disabled={act.isPending} onClick={() => act.mutate('create')}>{act.isPending ? '创建中…' : '创建项目环境'}</Button>}
        {e.exists && (e.tooling_missing ?? []).length > 0 && (
          <Button variant="primary" disabled={act.isPending} onClick={() => act.mutate('create')}>{act.isPending ? '安装中…' : `补装 ${(e.tooling_missing ?? []).join('、')}`}</Button>
        )}
        {e.exists && (
          <Button disabled={act.isPending} onClick={() => { if (window.confirm('删除并重建项目环境？已安装的第三方包都会被清除。')) act.mutate('reset'); }}>
            {act.isPending ? '重建中…' : '重置为干净环境'}
          </Button>
        )}
        <span className="muted">生成项目的测试与运行都用它；它和 masa 自己的 Python、系统 Python 互相隔离。</span>
      </div>
      <Field label="使用的解释器" hint="默认是项目环境。想用本机上别的 Python 环境，请从列表里选，或点“浏览…”在文件管理器里选 python.exe；不用手写路径。">
        <div className="row wrap">
          <select value={interpreter} onChange={(ev) => onInterpreter(ev.target.value)}>
            <option value="">项目环境（推荐）{e.exists ? '' : ' — 先创建'}</option>
            {e.candidates.map((c) => <option key={c.path} value={c.path}>{c.label} — {c.path}</option>)}
            {interpreter && !known && <option value={interpreter}>已选择的文件：{interpreter}</option>}
          </select>
          <Button size="sm" disabled={browse.isPending} onClick={() => browse.mutate()}>{browse.isPending ? '等待选择…' : '浏览…'}</Button>
        </div>
      </Field>
      {e.policy === 'deny' ? (
        <Notice tone="warn">第三方包策略是“禁止”：生成的项目只能用标准库。要允许，请改上面的“第三方包”。</Notice>
      ) : (
        <Field label="手动安装第三方包" hint={`只装 PyPI 的预编译包，只进项目环境；超过 ${e.max_mb} MB 上限会撤销。你在这里点安装就等于批准。需求里点名了包时，新任务页会先问你。`}>
          <div className="row wrap">
            <input value={names} placeholder="例如 numpy pandas==2.2.0" onChange={(ev) => setNames(ev.target.value)} style={{ minWidth: 260 }} />
            <Button disabled={!names.trim() || install.isPending} onClick={() => install.mutate()}>{install.isPending ? '安装中…（可能要几十秒）' : '安装'}</Button>
          </div>
        </Field>
      )}
      {message && <Notice tone={message.tone}>{message.text}</Notice>}
      {e.packages.length > 0 && (
        <details>
          <summary className="muted">已安装的第三方包（{e.packages.length}）</summary>
          <p className="muted">{e.packages.map((p) => `${p.name} ${p.version}`).join(' · ')}</p>
        </details>
      )}
    </div>
  );
}
