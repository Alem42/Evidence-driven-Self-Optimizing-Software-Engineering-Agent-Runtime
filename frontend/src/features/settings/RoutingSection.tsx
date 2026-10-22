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

const FALLBACK_ROLES = ['project_architect', 'project_developer', 'project_repair', 'project_test_revision'];
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
            // 清空输入框得到 0 而不是“不限”（不限只由右边的勾选控制）；否则删光再输入会被误当作取消限制。 Clearing the box gives 0, not "unlimited" (only the checkbox controls that); otherwise erasing and retyping toggled the limit off.
            onChange={(e) => setBudget(key, e.target.value === '' ? 0 : Number(e.target.value) * scale)}
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

      <Card title="默认预算" subtitle="整个任务累计（修复、重规划都不重置）。只有云端调用和云端 token 受限额约束；本地模型免费，只受运行时间约束。运行时间不含等你确认的时间。">
        <div className="grid2">
          {limit('max_model_calls', '云端调用次数（付费）', '次')}
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
        <h4>用例审计（实验，默认关闭）</h4>
        <Notice tone="warn">真实配对评测（3 轮，17 个有效配对）：开启后 5 个更好、0 个更差、12 个持平；通过 16/21 对 11/20，平均费用 −36%。但样本仍小、噪声大，且发现一个共同的假通过（wc）。想试用时再开，细节见“实验结果”页。</Notice>
        <p className="hint">规划阶段，便宜的云端模型（Flash）只看用例的输入、不看期望，独立推导 3 次，再与 Architect 写的期望比较；多数认为不一致的用例被丢弃（最少保留 3 个）。每个任务约多 6 次廉价调用（≈ ¥0.01）。只在天梯模式且有云端模型时生效。</p>
        <div className="grid2">
          <Field label="启用用例审计">
            <input type="checkbox" checked={!!draft.policy.test_audit} onChange={(e) => setPolicy({ test_audit: e.target.checked })} />
          </Field>
        </div>
        <h4>可执行的验收用例（实验，默认关闭，仅 Python）</h4>
        <p className="hint">Architect 在规格里写结构化的黑盒用例（命令行参数、标准输入、期望的标准输出、退出码），程序<b>确定性地</b>生成 tests/test_acceptance.py：不经过模型翻译，Developer 和包工人改不了，失败时直接指出是哪个用例、什么输入。Go 暂不支持（保持原来的文本用例）。</p>
        <div className="grid2">
          <Field label="启用可执行的验收用例">
            <input type="checkbox" checked={!!draft.policy.acceptance_cases} onChange={(e) => setPolicy({ acceptance_cases: e.target.checked })} />
          </Field>
        </div>
        <h4>断言裁判（实验，默认关闭）</h4>
        <div className="grid2">
          <Field label="断言裁判（失败断言的两个值打乱成 A/B，便宜模型独立选 3 次，多数作为 Diagnoser 的独立证据）">
            <input type="checkbox" checked={!!draft.policy.assertion_referee} onChange={(e) => setPolicy({ assertion_referee: e.target.checked })} />
          </Field>
        </div>
        <h4>包工人的记忆策略</h4>
        <p className="hint">离开“最近几步”窗口的工具结果怎么办。<b>window</b> = 只留最近 3 步原文（原有行为，默认）；<b>digest*</b> = 每个离开窗口的结果留一行摘要，当前失败的检查和裁判结论钉住，并可按步号回查原文；<b>auto</b> = 先取得当前模型的有效上下文窗口，再按预算选一档（窗口小就选更紧的，仍放不下会明确换模型/拆任务，而不是被静默截断）。设计与验收见 docs/design/AUTO_COMPACTION.md。</p>
        <div className="grid2">
          <Field label="记忆策略">
            <select value={draft.policy.memory_strategy ?? 'window'} onChange={(e) => setPolicy({ memory_strategy: e.target.value as RoutingPolicy['memory_strategy'] })}>
              <option value="window">window · 最近 3 步原文（默认）</option>
              <option value="auto">auto · 按模型窗口自动选择</option>
              <option value="digest_wide">digest_wide · 大窗口</option>
              <option value="digest">digest · 普通窗口</option>
              <option value="digest_tight">digest_tight · 小窗口</option>
            </select>
          </Field>
        </div>
        <h4>规模自适应的分层上下文（实验，默认关闭）</h4>
        <p className="hint">任务小（全部内容放得进窗口的 35%）时原样全给，什么都不改；只有放不下窗口的大项目，才把与当前文件无关的已生成文件降级为“只有声明的轮廓”，同目录文件、测试和清单文件永远完整，并把决定写进账本。云端大窗口模型几乎不会触发；对 16k 窗口的本地模型更有意义。</p>
        <div className="grid2">
          <Field label="启用分层上下文">
            <input type="checkbox" checked={!!draft.policy.context_budget} onChange={(e) => setPolicy({ context_budget: e.target.checked })} />
          </Field>
        </div>
        <h4>按角色的起始等级</h4>
        <p className="hint">留空 = 从最低等级起。例如让 Architect 从 L2 起：规划输出短、影响大，值得用更强的模型。</p>
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
