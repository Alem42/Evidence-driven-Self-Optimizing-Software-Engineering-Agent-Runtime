import { useEffect, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../../api/client';
import { qk } from '../../api/queries';
import type { LanguageInfo, Languages } from '../../api/types';
import { Badge, Button, Card, Field, Notice } from '../../shared/ui';

type Draft = { default: string; packs: Record<string, Record<string, unknown>>; extra_dirs: string };

const fromServer = (d: Languages): Draft => ({
  default: d.default,
  packs: Object.fromEntries(d.languages.map((l) => [l.id, { ...(l.config_values ?? {}) }])),
  extra_dirs: (d.extra_dirs ?? []).join(String.fromCharCode(10)),
});

// 设置 → 语言：语言包的清单、就绪状态、默认语言，以及每个包自己声明的可调项（例如 Python 的解释器与测试框架）。
// Settings -> Languages: the pack list, readiness, the default language and each pack's own tunables (e.g. Python's interpreter and test framework).
export function LanguagesSection() {
  const qc = useQueryClient();
  const data = useQuery({ queryKey: ['languages'] as const, queryFn: () => api<Languages>('/languages'), refetchInterval: 15000 });
  const [draft, setDraft] = useState<Draft | null>(null);
  // 服务端数据变化（15 秒轮询）时：没有未保存的编辑才整体替换，否则保留用户正在改的内容。 When server data changes (15 s polling): replace the draft only if there are no unsaved edits.
  const baseline = useRef<string>('');
  useEffect(() => {
    if (!data.data) return;
    const fresh = fromServer(data.data);
    setDraft((current) => (current === null || JSON.stringify(current) === baseline.current ? fresh : current));
    baseline.current = JSON.stringify(fresh);
  }, [data.data]);
  const save = useMutation({
    mutationFn: () => api<Languages>('/languages', { default: draft!.default, packs: draft!.packs, extra_dirs: draft!.extra_dirs.split(String.fromCharCode(10)).map((s) => s.trim()).filter(Boolean) }),
    onSuccess: (fresh) => {
      qc.setQueryData(['languages'], fresh);
      qc.invalidateQueries({ queryKey: qk.bootstrap });
    },
  });
  if (!draft || !data.data) return <p className="muted">读取中…</p>;
  const dirty = JSON.stringify(draft) !== JSON.stringify(fromServer(data.data));
  const setField = (id: string, key: string, value: unknown) => setDraft({ ...draft, packs: { ...draft.packs, [id]: { ...draft.packs[id], [key]: value } } });
  return (
    <div className="stack">
      <Notice tone="warn">每种语言是一个语言包：它决定项目布局、怎么编译/测试、怎么解析失败、提示词和评测判官。新增语言只需要放一个包目录，不改核心（见 docs/design/LANGUAGE_PACKS.md）。</Notice>
      {data.data.languages.map((l: LanguageInfo) =>
        l.error ? (
          <Card key={l.id} title={l.id} subtitle="这个语言包加载失败">
            <Notice tone="bad">{l.error}</Notice>
          </Card>
        ) : (
          <Card key={l.id} title={`${l.title} · v${l.version ?? 1}`} subtitle={`指纹 ${l.digest ?? '—'} · 能力：${(l.capabilities ?? []).join('、') || '基础'}`}>
            <div className="row wrap">
              <label className="check">
                <input type="radio" name="default-language" checked={draft.default === l.id} onChange={() => setDraft({ ...draft, default: l.id })} /> 设为默认语言
              </label>
              <Badge tone={l.readiness.ok ? 'ok' : 'bad'} dot>{l.readiness.ok ? '就绪' : '未就绪'}</Badge>
              <span className="muted">{l.readiness.details.join('；')}</span>
            </div>
            <dl className="facts">
              {Object.entries(l.operations ?? {}).map(([kind, op]) => (
                <span key={kind} style={{ display: 'contents' }}><dt>{kind}</dt><dd><code>{op.id}</code> · {op.label}</dd></span>
              ))}
            </dl>
            {(l.config ?? []).map((f) => (
              <Field key={f.key} label={f.label} hint={f.help}>
                {f.kind === 'choice' ? (
                  <select value={String(draft.packs[l.id]?.[f.key] ?? f.default ?? '')} onChange={(e) => setField(l.id, f.key, e.target.value)}>
                    {(f.choices ?? []).map((c) => <option key={c} value={c}>{c}</option>)}
                  </select>
                ) : f.kind === 'bool' ? (
                  <input type="checkbox" checked={Boolean(draft.packs[l.id]?.[f.key])} onChange={(e) => setField(l.id, f.key, e.target.checked)} />
                ) : (
                  <input value={String(draft.packs[l.id]?.[f.key] ?? '')} placeholder={String(f.default ?? '')} onChange={(e) => setField(l.id, f.key, e.target.value)} />
                )}
              </Field>
            ))}
          </Card>
        ),
      )}
      <Card title="额外的语言包目录" subtitle="每行一个目录，里面每个子目录是一个语言包（pack.json + pack.py）。外部包是在本进程里执行的 Python 代码，只加载你明确配置的目录。">
        <textarea rows={3} value={draft.extra_dirs} onChange={(e) => setDraft({ ...draft, extra_dirs: e.target.value })} placeholder="例如 D:/masa-packs" />
      </Card>
      <div className="row">
        <Button variant="primary" disabled={!dirty || save.isPending} onClick={() => save.mutate()}>{save.isPending ? '保存中…' : '保存'}</Button>
        {save.isError && <span className="error">{(save.error as Error).message}</span>}
        {save.isSuccess && !dirty && (
          <span className="muted">
            已保存；新任务按新配置运行。{data.data.languages.filter((l) => !l.error && !l.readiness.ok).map((l) => `注意：${l.title} 当前未就绪（${l.readiness.details.join('；')}）。`).join(' ')}
          </span>
        )}
      </div>
    </div>
  );
}
