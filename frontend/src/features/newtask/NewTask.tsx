import { useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useActivity } from '../../app/activity';
import { startJob } from '../../api/jobs';
import { useBenchTasks, useProfiles } from '../../api/queries';
import { languageTitle } from '../../entities/languages';
import { KIND_LABEL, pickExamples } from '../../entities/examples';
import { EstimatePanel } from './EstimatePanel';
import { PackagesNotice } from './PackagesNotice';
import { api } from '../../api/client';
import type { Triage } from '../../api/types';
import { TriageView } from '../decisions/TriageView';
import { byRouting, profileLevel } from '../../entities/profiles';
import { Link } from 'react-router-dom';
import { profileLabel, profileReady } from '../../entities/profiles';
import { useToasts, useUi } from '../../stores/ui';
import { Button, Notice, Segmented } from '../../shared/ui';

// 新任务页：一个大输入框 + 模型与执行方式。提交后任务图在任务页出现。
// New-task page: one big prompt box; the task graph appears on the task page.
export function NewTask() {
  const navigate = useNavigate();
  const { data: profiles } = useProfiles();
  const { bootstrap, working } = useActivity();
  const boot = bootstrap;
  const push = useToasts((s) => s.push);
  const [goal, setGoal] = useState('');
  // 语言：默认取后端的默认语言；只有一种语言时不显示选择器。 Language: the backend default; no selector when there is only one.
  const [language, setLanguage] = useState('');
  const languages = bootstrap?.languages ?? [];
  const chosen = language || bootstrap?.language || 'go';
  const chosenInfo = languages.find((l) => l.id === chosen);
  const [model, setModel] = useState('');
  // 运行方式：v2repair = 生成沿用标准流程，验证失败后只重开出问题的包（真实样本上最稳最便宜，默认）；v1 = 标准流程；v2 = 全程按包推进的工人循环（实验）。只在“自动执行”下生效。
  // Runtime: v2repair = standard generation, then only the failing package is reopened after a failed verification (the steadiest and cheapest on real samples, the default); v1 = the standard flow; v2 = per-package worker loops all the way (experimental). Only in automatic mode.
  const [runtime, setRuntime] = useState<'v1' | 'v2repair' | 'v2'>('v2repair');
  // 默认：自动执行 + 本地优先有界升级。需要逐步确认或固定模型时再手动切换。
  // Defaults: automatic execution + local-first bounded escalation; switch manually for step-by-step review or a fixed model.
  const [auto, setAuto] = useState<'review' | 'automatic'>('automatic');
  // 推荐任务：内置的丰富题库（含故意写得模糊的需求）+ 评测题库里的低等级题，抽 3 个不同类型的；只在题库加载或点“换一批”时重抽（输入时不闪烁）。
  // Suggestions: the built-in rich pool (with deliberately vague requirements) plus low-level benchmark tasks, three of different kinds; redrawn only when the set loads or on "refresh" (no flicker while typing).
  const { data: catalog } = useBenchTasks();
  const [batch, setBatch] = useState(0);
  const shown = useRef<string[]>([]);
  const examples = useMemo(() => {
    const drawn = pickExamples(catalog?.tasks, [], 3, 5, Math.random, shown.current);
    shown.current = drawn.map((e) => e.text);
    return drawn;
  }, [catalog, batch]);
  const [starting, setStarting] = useState(false);
  const [strategy, setStrategy] = useState<'fixed' | 'ladder'>('ladder');
  const [apiTokens, setApiTokens] = useState('');
  // API token 上限的来源：默认跟着“开跑前估计”选的档位；用户在高级设置里手填后就不再被覆盖。 Where the API token limit comes from: the tier chosen in the pre-run estimate, until the user types one by hand in the advanced settings.
  const [tokenSource, setTokenSource] = useState<'estimate' | 'manual'>('estimate');
  const selectedModels = useUi((x) => x.selectedModels);
  const [triage, setTriage] = useState<Triage | null>(null);
  const [force, setForce] = useState(false);
  // 输入时实时预检（确定性规则，不调用模型）。 Live pre-check while typing: deterministic rules, no model call.
  useEffect(() => {
    setForce(false);
    if (goal.trim().length < 6) { setTriage(null); return; }
    const t = setTimeout(() => api<Triage>('/triage', { goal, language: chosen }).then(setTriage).catch(() => setTriage(null)), 500);
    return () => clearTimeout(t);
  }, [goal, chosen]);
  const blockedByTriage = triage?.verdict === 'infeasible' && !force;
  // 点名了还没装的第三方包且策略是“询问”：先让用户决定（允许安装 / 改成只用标准库），不替用户装。 Named third-party packages not installed yet under the ask policy: the user decides first (allow / standard library only); nothing is installed for them.
  const pkg = triage?.packages;
  const blockedByPackages = Boolean(pkg && pkg.missing.length && pkg.policy === 'ask');
  const refreshTriage = () => api<Triage>('/triage', { goal, language: chosen }).then(setTriage).catch(() => undefined);

  const ready = (profiles?.profiles ?? []).filter(profileReady);
  const profile = ready.find((p) => p.id === model)?.id || ready.find((p) => p.id === profiles?.active_id)?.id || ready[0]?.id || '';
  // 就绪看所选语言的工具链（旧后端没有语言列表时退回 runner_ready）。 Readiness follows the chosen language's toolchain (falls back to runner_ready on an old backend).
  const toolchainReady = chosenInfo ? chosenInfo.ready : Boolean(bootstrap?.runner_ready);
  const blocked = !ready.length || !toolchainReady || working;

  async function start() {
    if (!goal.trim() || blocked || starting || blockedByTriage || blockedByPackages) return;
    setStarting(true);
    try {
      if (pkg && pkg.missing.length && pkg.policy === 'allow') {
        push({ tone: 'info', text: `完全访问：正在把 ${pkg.missing.join('、')} 装进项目环境…` });
        await api(`/languages/${chosen}/packages`, { packages: pkg.missing, approve: true });
      }
      useUi.getState().set({ follow: true });
      const ladder = strategy === 'ladder' && auto === 'automatic';
      await startJob(
        '/projects/plan',
        { goal, language: chosen, api_profile_id: profile, auto_verify: auto === 'automatic', ...(force ? { force: true } : {}), ...(ladder ? { routing: 'ladder', ...(runtime !== 'v1' ? { runtime } : {}), budget: apiTokens ? { max_cloud_tokens: Number(apiTokens) } : {}, ...(selectedModels ? { model_ids: selectedModels } : {}) } : {}) },
        { label: '项目规划' },
      );
      push({ tone: 'info', text: '任务已启动，正在规划…' });
    } catch (e) {
      push({ tone: 'bad', text: (e as Error).message });
    } finally {
      setStarting(false);
    }
  }

  const summary = [auto === 'automatic' ? '自动执行' : '逐步确认', strategy === 'ladder' && auto === 'automatic' ? '本地优先 · 有界升级' : '固定模型', languageTitle(languages, chosen)].join(' · ');

  return (
    <div className="newtask">
      <div className="newtask-inner">
        <span className="eyebrow">NEW TASK</span>
        <h1>描述需求，得到经过验证的代码</h1>
        <p className="muted">
          支持 {languages.length ? languages.map((l) => l.title ?? l.id).join(' / ') : 'Go'} 命令行项目：规划 → 设计测试 → 逐文件生成 → 真实运行检查 → 独立核对。运行中你可以随时追加提示、暂停或停止。
        </p>
        <div className="composer-box">
          <textarea
            autoFocus
            rows={6}
            maxLength={16000}
            value={goal}
            onChange={(e) => setGoal(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) start();
            }}
            placeholder="例如：生成一个随机整数 CLI，支持范围、数量和可选种子；无效参数返回错误，不输出结果。"
          />
          <div className="composer-bar">
            <span className="hint">⌘/Ctrl + Enter 开始</span>
            <Button variant="primary" disabled={!goal.trim() || blocked || starting || blockedByTriage || blockedByPackages} onClick={start}>
              {starting ? '正在启动…' : '开始任务'}
            </Button>
          </div>
        </div>
        <EstimatePanel goal={goal} language={chosen} enabled={tokenSource === 'estimate'} onTokens={(n) => setApiTokens(n ? String(n) : '')} />
        {pkg && pkg.missing.length > 0 && (
          <PackagesNotice
            packages={pkg}
            language={chosen}
            onInstalled={refreshTriage}
            onStdlibOnly={() => setGoal((g) => (g.includes('只用 Python 标准库') ? g : g.trim() + '（只用 Python 标准库，不要第三方包）'))}
          />
        )}
        {triage && triage.verdict !== 'ok' && (
          <div className="triage-preview">
            <TriageView triage={triage} compact />
            {triage.verdict === 'infeasible' && (
              <label className="check"><input type="checkbox" checked={force} onChange={(e) => setForce(e.target.checked)} /> 我了解风险，仍然继续（会花模型调用，且很可能失败）</label>
            )}
          </div>
        )}
        <details className="advanced">
          <summary>高级设置 <span className="muted">· {summary}</span></summary>
          <div className="advanced-body">
            <div className="row wrap">
              <Segmented value={auto} onChange={setAuto} options={[['review', '逐步确认'], ['automatic', '自动执行']]} />
              <Segmented value={strategy} onChange={setStrategy} options={[['fixed', '固定模型'], ['ladder', '本地优先 · 有界升级']]} />
              {languages.length > 1 && (
                <select aria-label="语言" value={chosen} onChange={(e) => setLanguage(e.target.value)} title="生成项目使用的语言（设置 → 语言 可配置）">
                  {languages.map((l) => (
                    <option key={l.id} value={l.id}>{(l.title ?? l.id) + (l.ready ? '' : '（未就绪）')}</option>
                  ))}
                </select>
              )}
              {auto === 'automatic' && strategy === 'ladder' && (
                <select aria-label="运行方式" value={runtime} onChange={(e) => setRuntime(e.target.value as 'v1' | 'v2repair' | 'v2')} title="验证失败后怎么修：只重开出问题的包（推荐），还是沿用整包补丁；v2 = 全程按包推进（实验）">
                  <option value="v2repair">修复时只重开出问题的包（推荐）</option>
                  <option value="v1">标准流程</option>
                  <option value="v2">全程按包推进（实验）</option>
                </select>
              )}
              {/* 有界升级下模型由设置里的次序决定，不需要在这里选；只有「固定模型」或「逐步确认」才需要选一个。 */}
              {(strategy === 'fixed' || auto !== 'automatic') && (
                <select aria-label="使用模型" value={profile} onChange={(e) => setModel(e.target.value)}>
                  {!ready.length && <option value="">没有可用模型</option>}
                  {ready.map((p) => (
                    <option key={p.id} value={p.id}>
                      {profileLabel(p)}
                    </option>
                  ))}
                </select>
              )}
            </div>
            {strategy === 'ladder' && auto !== 'automatic' && <span className="hint">升级策略只在「自动执行」下生效；逐步确认时用上面选的模型。</span>}
            {strategy === 'ladder' && auto === 'automatic' && (
              <div className="strategy-body">
                <p className="muted">
                  本任务使用的模型与次序：{[...ready].filter((p) => !selectedModels || selectedModels.includes(p.id)).sort(byRouting).map((p) => `L${profileLevel(p)} ${p.name || p.model}`).join(' → ') || '无'}。规格、测试方案、测试修订与诊断由最高等级模型负责（token 少、决定一切）；代码实现与修复从本地模型起步，失败后逐级上推；预算耗尽会停止并保留证据。
                </p>
                <div className="row wrap">
                  <Link to="/settings/order?from=new" className="btn btn-default btn-sm">模型与次序 · 调整 / 勾选</Link>
                  <Link to="/settings/accounts" className="btn btn-ghost btn-sm">API 账户</Link>
                </div>
                <label className="field inline">
                  <span className="field-label">API token 上限</span>
                  <input type="number" min={1000} step={1000} placeholder={String(boot?.routing_defaults?.budget.max_cloud_tokens ?? 200000)} value={apiTokens} onChange={(e) => { setTokenSource('manual'); setApiTokens(e.target.value); }} />
                </label>
              </div>
            )}
            <p className="hint">
              {auto === 'automatic' ? '自动采用有效草稿，真实检查失败最多修复四轮。' : '方案与代码都会停下来等你确认，可编辑后再批准。'} 会调用所选模型。
            </p>
          </div>
        </details>
        {!ready.length && (
          <Notice tone="warn">
            还没有可用模型。<button className="link" onClick={() => navigate('/settings/models')}>前往添加</button> 或 <button className="link" onClick={() => navigate('/settings/local')}>选择本地 Ollama 模型</button>。
          </Notice>
        )}
        {bootstrap && !toolchainReady && (
          <Notice tone="bad">
            {languageTitle(languages, chosen)} 的工具链尚未就绪。<button className="link" onClick={() => navigate('/settings/languages')}>前往设置 → 语言</button> 查看缺什么并配置。
          </Notice>
        )}
        {working && <Notice tone="warn">另一个任务正在执行；后端目前串行，完成后才能开始新任务。你仍可浏览历史。</Notice>}
        <div className="examples">
          {examples.slice(0, 3).map((e) => (
            <button key={e.text} className="example" onClick={() => setGoal(e.text)} title={e.kind === 'vague' ? '故意写得模糊：看系统会先问你，还是选常见约定并写进验收' : undefined}>
              <span className="tag">{e.kind === 'clear' && e.level !== null ? `L${e.level}` : KIND_LABEL[e.kind]}</span> {e.text}
            </button>
          ))}
          <button className="link" onClick={() => setBatch((b) => b + 1)} title="换三个别的推荐需求">换一批</button>
        </div>
      </div>
    </div>
  );
}
