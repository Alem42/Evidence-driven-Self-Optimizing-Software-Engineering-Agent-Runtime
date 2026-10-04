import { useEffect, useMemo, useState } from 'react';
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

// 建议任务池：每次打开页面随机抽 3 个（开发期使用；后续可在设置里编辑题库与数量）。
// Suggestion pool: three random picks each time the page opens (development aid; the pool and count become settings later).
const EXAMPLE_POOL = [
  '生成一个随机整数 CLI，支持范围、数量和可选种子；无效参数返回错误，不输出结果。',
  '写一个文本统计 CLI：读取标准输入，输出行数、单词数和字节数。',
  '实现 CSV 费用汇总命令行：按类别求和并按金额降序输出。',
  '写一个简单的动态规划，自己确定输入和输出，题目写在注释中。',
  '实现一个 CLI：把标准输入的每一行反转后输出，空行保持不变。',
  '写一个 CLI：统计标准输入中每个单词出现的次数，按次数降序、同次数按字典序输出前 10 个。',
  '实现一个 CLI：判断命令行参数给出的每个整数是否为素数，每行输出 yes 或 no。',
  '写一个 CLI：把十进制整数参数转换为二进制、八进制和十六进制并分别输出。',
  '实现一个简易的 LRU 缓存包和演示 CLI：从标准输入读取 get/put 命令，输出结果。',
  '写一个 CLI：读取标准输入的逗号分隔数字，输出最小值、最大值、平均值和中位数。',
  '实现一个括号匹配检查 CLI：读取标准输入的每一行，输出该行括号是否匹配。',
  '写一个 CLI：计算两个日期（YYYY-MM-DD）相差的天数，参数无效时返回错误。',
  '实现一个 CLI：把标准输入的 Markdown 标题行（# 开头）提取为带缩进的目录。',
  '写一个 CLI：对标准输入的整数做归并排序，支持 --desc 参数，每行输出一个。',
];
const pick = <T,>(pool: T[], n: number): T[] => [...pool].sort(() => Math.random() - 0.5).slice(0, n);

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
  // 默认：自动执行 + 本地优先有界升级。需要逐步确认或固定模型时再手动切换。
  // Defaults: automatic execution + local-first bounded escalation; switch manually for step-by-step review or a fixed model.
  const [auto, setAuto] = useState<'review' | 'automatic'>('automatic');
  const examples = useMemo(() => pick(EXAMPLE_POOL, 3), []);
  const [starting, setStarting] = useState(false);
  const [strategy, setStrategy] = useState<'fixed' | 'ladder'>('ladder');
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
          {strategy === 'ladder' && auto !== 'automatic' && <span className="hint">升级策略只在「自动执行」下生效；逐步确认时用上面选的模型。</span>}
          {strategy === 'ladder' && auto === 'automatic' && (
            <div className="strategy-body">
              <p className="muted">
                本任务使用的模型与次序：{[...ready].filter((p) => !selectedModels || selectedModels.includes(p.id)).sort(byRouting).map((p) => `L${profileLevel(p)} ${p.name || p.model}`).join(' → ') || '无'}。规格、测试方案、测试修订与诊断由最高等级模型负责（token 少、决定一切）；代码实现与修复从本地模型起步，失败后逐级上推；预算耗尽会停止并保留证据。模型按上面的次序执行，无需在这里选。
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
          {examples.map((e) => (
            <button key={e} className="example" onClick={() => setGoal(e)}>
              {e}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
