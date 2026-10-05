import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '../../api/client';
import { qk, useProfiles, useRoles } from '../../api/queries';
import type { RoutingBudget, RoutingPolicy } from '../../api/types';
import { useActivity } from '../../app/activity';
import { byRouting, profileLabel, profileLevel, profileReady } from '../../entities/profiles';
import { stageLabels } from '../../entities/status';
import { Badge, Button, Card, Field, Notice } from '../../shared/ui';

const FALLBACK_ROLES = ['project_planner', 'project_tester', 'project_developer', 'project_repair', 'project_test_revision'];
const CHAIN_NAMES: Record<string, string> = { planning: '规划', generation: '生成', fix: '修复' };

type Draft = { policy: RoutingPolicy; budget: RoutingBudget };

// 路由与预算：可编辑的默认策略与预算（新任务可再覆盖）。候选顺序在「模型与次序」。
// Routing & budget: editable defaults (per-task overrides still apply). Candidate order lives in "Model order".
export function RoutingSection() {
  const qc = useQueryClient();
  const profiles = useProfiles();
  // 起步等级可按角色设置：角色清单来自 RoleSpec 注册表（新增角色自动出现）。 Per-role start levels: the list comes from the RoleSpec registry (new roles appear automatically).
  const roles = useRoles().data;
  const ROLES = roles?.length ? roles.filter((r) => r.routable).map((r) => r.id) : FALLBACK_ROLES;
  const defaults = useActivity().bootstrap?.routing_defaults;
  const [draft, setDraft] = useState<Draft | null>(null);
  useEffect(() => {
    if (defaults) setDraft(structuredClone(defaults) as Draft);
  }, [defaults]);
  const save = useMutation({
    mutationFn: () => api('/routing', draft),
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.bootstrap }),
  });
  const list = [...(profiles.data?.profiles ?? [])].sort(byRouting);
  if (!draft) return <p className="muted">读取中…</p>;
  const dirty = JSON.stringify(draft) !== JSON.stringify(defaults);
  const setPolicy = (patch: Partial<RoutingPolicy>) => setDraft({ ...draft, policy: { ...draft.policy, ...patch } });
  const setBudget = (key: keyof RoutingBudget, value: number | null) => setDraft({ ...draft, budget: { ...draft.budget, [key]: value } });

  const limit = (key: keyof RoutingBudget, label: string, unit: string, step = 1, scale = 1) => {
    const value = draft.budget[key];
    return (
      <Field label={label}>
        <div className="row">
          <input
            type="number"
            min={0}
            step={step}
            disabled={value == null}
            value={value == null ? '' : value / scale}
            onChange={(e) => setBudget(key, e.target.value === '' ? null : Number(e.target.value) * scale)}
          />
          <span className="muted">{unit}</span>
          <label className="check">
            <input type="checkbox" checked={value == null} onChange={(e) => setBudget(key, e.target.checked ? null : key === 'max_cost' ? 1 : (defaults?.budget[key] ?? 1000))} />
            不限
          </label>
        </div>
      </Field>
    );
  };

  return (
    <div className="stack">
      <Notice tone="ok">选择「本地优先 · 有界升级」（需自动执行）即启用。固定模型仍是默认行为。这里的值是新任务的默认值，新建任务时还能单独覆盖 API token 上限。</Notice>

      <Card title="候选模型（等级 ↑ 优先级 ↑）" subtitle="调整顺序、勾选参与的模型：去「模型与次序」。" actions={<Link to="/settings/order" className="link">模型与次序 →</Link>}>
        <ol className="route-list">
          {list.map((p) => (
            <li key={p.id}>
              <Badge tone={p.model_type === 'local' ? 'ok' : 'running'}>L{profileLevel(p)}</Badge>
              <strong>{profileLabel(p)}</strong>
              <span className="muted">{p.model} · 上下文 {p.context_limit ?? '—'}</span>
              <Badge tone={profileReady(p) ? 'ok' : 'neutral'}>{profileReady(p) ? '可用' : '不可用'}</Badge>
            </li>
          ))}
          {!list.length && <p className="muted">还没有模型配置。</p>}
        </ol>
      </Card>

      <Card title="默认预算" subtitle="整个任务累计（修复、重规划都不重置）。运行时间不含等你确认的时间。">
        <div className="grid2">
          {limit('max_model_calls', '模型调用次数', '次')}
          {limit('max_cloud_tokens', 'API token', 'token', 1000)}
          {limit('max_active_seconds', '运行时间', '分钟', 1, 60)}
          {limit('max_cost', '费用（需配置价格）', '按你填的价格币种', 0.1)}
        </div>
      </Card>

      <Card title="默认策略">
        <h4>每级尝试次数</h4>
        <p className="hint">初次 + 自修。最高等级不限次数（受总轮次约束）。</p>
        <div className="grid2">
          {(['planning', 'generation', 'fix'] as const).map((chain) => (
            <Field key={chain} label={CHAIN_NAMES[chain] + '链'}>
              <input type="number" min={1} max={5} value={draft.policy.attempts_per_level[chain]} onChange={(e) => setPolicy({ attempts_per_level: { ...draft.policy.attempts_per_level, [chain]: Number(e.target.value) } })} />
            </Field>
          ))}
          <Field label="最多升级次数">
            <input type="number" min={0} max={5} value={draft.policy.max_escalations} onChange={(e) => setPolicy({ max_escalations: Number(e.target.value) })} />
          </Field>
          <Field label="规划重试次数">
            <input type="number" min={1} max={4} value={draft.policy.planner_retries} onChange={(e) => setPolicy({ planner_retries: Number(e.target.value) })} />
          </Field>
        </div>
        <h4>按角色的起始等级</h4>
        <p className="hint">留空 = 从最低等级起。例如让 Planner 从 L2 起：规划输出短、影响大，值得用更强的模型。</p>
        <div className="grid2">
          {ROLES.map((role) => (
            <Field key={role} label={stageLabels[role] ?? role}>
              <input
                type="number"
                min={1}
                max={100}
                placeholder="最低等级"
                value={draft.policy.start_level_by_role[role] ?? ''}
                onChange={(e) => {
                  const next = { ...draft.policy.start_level_by_role };
                  if (e.target.value === '') delete next[role];
                  else next[role] = Number(e.target.value);
                  setPolicy({ start_level_by_role: next });
                }}
              />
            </Field>
          ))}
        </div>
      </Card>

      {save.isError && <Notice tone="bad">{(save.error as Error).message}</Notice>}
      <div className="row sticky-actions">
        <Button variant="primary" disabled={!dirty || save.isPending} onClick={() => save.mutate()}>{save.isPending ? '保存中…' : '保存默认值'}</Button>
        <Button disabled={!dirty} onClick={() => defaults && setDraft(structuredClone(defaults) as Draft)}>还原</Button>
        {save.isSuccess && !dirty && <span className="ok-text">已保存</span>}
      </div>
    </div>
  );
}
