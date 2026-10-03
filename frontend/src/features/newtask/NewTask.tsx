import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useActivity } from '../../app/activity';
import { startJob } from '../../api/jobs';
import { useProfiles } from '../../api/queries';
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
  const push = useToasts((s) => s.push);
  const [goal, setGoal] = useState('');
  const [model, setModel] = useState('');
  const [auto, setAuto] = useState<'review' | 'automatic'>('review');
  const [starting, setStarting] = useState(false);

  const ready = (profiles?.profiles ?? []).filter(profileReady);
  const profile = ready.find((p) => p.id === model)?.id || ready.find((p) => p.id === profiles?.active_id)?.id || ready[0]?.id || '';
  const blocked = !ready.length || !bootstrap?.runner_ready || working;

  async function start() {
    if (!goal.trim() || blocked || starting) return;
    setStarting(true);
    try {
      useUi.getState().set({ follow: true });
      await startJob('/projects/plan', { goal, api_profile_id: profile, auto_verify: auto === 'automatic' }, { label: '项目规划' });
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
            <Button variant="primary" disabled={!goal.trim() || blocked || starting} onClick={start}>
              {starting ? '正在启动…' : '开始任务 ⌘↵'}
            </Button>
          </div>
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
