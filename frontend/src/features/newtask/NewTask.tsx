import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useActivity } from '../../app/activity';
import { startJob } from '../../api/jobs';
import { useProfiles } from '../../api/queries';
import { api } from '../../api/client';
import type { Triage } from '../../api/types';
import { TriageView } from '../thread/TriageView';
import { byRouting, profileLevel } from '../../entities/profiles';
import { Link } from 'react-router-dom';
import { profileLabel, profileReady } from '../../entities/profiles';
import { useToasts, useUi } from '../../stores/ui';
import { Button, Notice, Segmented } from '../../shared/ui';

const EXAMPLES = [
  '生成一个随机整数 CLI，支持范围、数量和可选种子；无效参数返回错误，不输出结果。',
  '写一个文本统计 CLI：读取标准输入，输出行数、单词数和字节数。',
  '实现 CSV 费用汇总命令行：按类别求和并按金额降序输出。',
];

// 新任务页：一个大输入框 + 模型与执行方式。提交后任务图在任务页出现。
// New-task page: one big prompt box; the task graph appears on the task page.
export function NewTask() {
  const navigate = useNavigate();
  const { data: profiles } = useProfiles();
  const { bootstrap, working } = useActivity();
  const boot = bootstrap;
  const push = useToasts((s) => s.push);
  const [goal, setGoal] = useState('');
  const [model, setModel] = useState('');
  const [auto, setAuto] = useState<'review' | 'automatic'>('review');
  const [starting, setStarting] = useState(false);
  const [strategy, setStrategy] = useState<'fixed' | 'ladder'>('fixed');
  const [apiTokens, setApiTokens] = useState('');
  const selectedModels = useUi((x) => x.selectedModels);
  const [triage, setTriage] = useState<Triage | null>(null);
  const [force, setForce] = useState(false);
  // 输入时实时预检（确定性规则，不调用模型）。 Live pre-check while typing: deterministic rules, no model call.
  useEffect(() => {
    setForce(false);
    if (goal.trim().length < 6) { setTriage(null); return; }
    const t = setTimeout(() => api<Triage>('/triage', { goal }).then(setTriage).catch(() => setTriage(null)), 500);
    return () => clearTimeout(t);
  }, [goal]);
  const blockedByTriage = triage?.verdict === 'infeasible' && !force;

  const ready = (profiles?.profiles ?? []).filter(profileReady);
  const profile = ready.find((p) => p.id === model)?.id || ready.find((p) => p.id === profiles?.active_id)?.id || ready[0]?.id || '';
  const blocked = !ready.length || !bootstrap?.runner_ready || working;

  async function start() {
    if (!goal.trim() || blocked || starting || blockedByTriage) return;
    setStarting(true);
    try {
      useUi.getState().set({ follow: true });
      const ladder = strategy === 'ladder' && auto === 'automatic';
      await startJob(
        '/projects/plan',
        { goal, api_profile_id: profile, auto_verify: auto === 'automatic', ...(force ? { force: true } : {}), ...(ladder ? { routing: 'ladder', budget: apiTokens ? { max_cloud_tokens: Number(apiTokens) } : {}, ...(selectedModels ? { model_ids: selectedModels } : {}) } : {}) },
        { label: '项目规划' },
      );
      push({ tone: 'info', text: '任务已启动，正在规划…' });
    } catch (e) {
      push({ tone: 'bad', text: (e as Error).message });
    } finally {
      setStarting(false);
    }
  }

  return (
    <div className="newtask">
      <div className="newtask-inner">
        <span className="eyebrow">NEW TASK</span>
        <h1>描述需求，得到经过验证的代码</h1>
        <p className="muted">
          目前支持 Go 标准库命令行项目。Planner 规划 → Tester 设计测试 → Developer 生成 → Executor 真实运行检查 → Gate 独立核对。
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
            <div className="row wrap">
              <select aria-label="使用模型" value={profile} onChange={(e) => setModel(e.target.value)}>
                {!ready.length && <option value="">没有可用模型</option>}
                {ready.map((p) => (
                  <option key={p.id} value={p.id}>
                    {profileLabel(p)}
                  </option>
                ))}
              </select>
              <Segmented value={auto} onChange={setAuto} options={[['review', '逐步确认'], ['automatic', '自动执行']]} />
            </div>
            <Button variant="primary" disabled={!goal.trim() || blocked || starting || blockedByTriage} onClick={start}>
              {starting ? '正在启动…' : '开始任务 ⌘↵'}
            </Button>
          </div>
        </div>
        {triage && triage.verdict !== 'ok' && (
          <div className="triage-preview">
            <TriageView triage={triage} compact />
            {triage.verdict === 'infeasible' && (
              <label className="check"><input type="checkbox" checked={force} onChange={(e) => setForce(e.target.checked)} /> 我了解风险，仍然继续（会花模型调用，且很可能失败）</label>
            )}
          </div>
        )}
        <div className="strategy">
          <Segmented value={strategy} onChange={setStrategy} options={[['fixed', '固定模型'], ['ladder', '本地优先 · 有界升级']]} />
          {strategy === 'ladder' && auto !== 'automatic' && <span className="hint">升级策略只在「自动执行」下生效。</span>}
          {strategy === 'ladder' && auto === 'automatic' && (
            <div className="strategy-body">
              <p className="muted">
                本任务使用的模型与次序：{[...ready].filter((p) => !selectedModels || selectedModels.includes(p.id)).sort(byRouting).map((p) => `L${profileLevel(p)} ${p.name || p.model}`).join(' → ') || '无'}。本地模型先试并自修，仍失败才升级到更高等级；预算耗尽会停止并保留证据。
              </p>
              <div className="row wrap">
                <Link to="/settings/order?from=new" className="btn btn-default btn-sm">模型与次序 · 调整 / 勾选</Link>
                <Link to="/settings/accounts" className="btn btn-ghost btn-sm">API 账户</Link>
              </div>
              <label className="field inline">
                <span className="field-label">API token 上限</span>
                <input type="number" min={1000} step={1000} placeholder={String(boot?.routing_defaults?.budget.max_cloud_tokens ?? 200000)} value={apiTokens} onChange={(e) => setApiTokens(e.target.value)} />
              </label>
            </div>
          )}
        </div>
        <p className="hint">
          {auto === 'automatic' ? '自动采用有效草稿，真实检查失败最多修复四轮。' : '方案与代码都会停下来等你确认，可编辑后再批准。'} 会调用所选模型。
        </p>
        {!ready.length && (
          <Notice tone="warn">
            还没有可用模型。<button className="link" onClick={() => navigate('/settings/models')}>前往添加</button> 或 <button className="link" onClick={() => navigate('/settings/local')}>选择本地 Ollama 模型</button>。
          </Notice>
        )}
        {bootstrap && !bootstrap.runner_ready && <Notice tone="bad">Go runner 尚未就绪，请先按环境指南构建。</Notice>}
        {working && <Notice tone="warn">另一个任务正在执行；后端目前串行，完成后才能开始新任务。你仍可浏览历史。</Notice>}
        <div className="examples">
          {EXAMPLES.map((e) => (
            <button key={e} className="example" onClick={() => setGoal(e)}>
              {e}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
