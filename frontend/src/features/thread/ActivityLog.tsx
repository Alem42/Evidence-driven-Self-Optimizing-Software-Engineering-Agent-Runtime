import type { Detail, RunEvent } from '../../api/types';
import { stageLabels } from '../../entities/status';
import { fmtSeconds } from '../../entities/text';
import { ranModel } from '../../entities/models';

const role = (e: RunEvent): string => stageLabels[e.payload.step_id] ?? e.payload.step_id ?? '角色';

type Mapper = (e: RunEvent) => { text: string; tone?: 'ok' | 'bad' | 'run' } | null;

// 事件 → 人话。未知事件不显示，避免噪音。 Event → plain language; unknown events stay hidden.
const MAP: Record<string, Mapper> = {
  run_created: () => ({ text: '任务创建' }),
  model_requested: (e) => {
    const m = ranModel(e);
    return { text: `${role(e)} · ${m ? (m.local ? '本地 ' : 'API ') + m.model : '模型'} 发起请求`, tone: 'run' };
  },
  route_decided: (e) => {
    const p = e.payload;
    if (p.action === 'stop') return { text: `路由停止：${p.reason}`, tone: 'bad' };
    return p.escalated ? { text: `升级 → L${p.level} ${p.model}（${role(e)}）`, tone: 'run' } : null;
  },
  diagnosis: (e) => ({ text: `Diagnoser 诊断：${{ implementation: '实现有问题', test: '测试有问题', both: '两侧都有问题', spec: '规格有问题', unclear: '不明确' }[e.payload.owner as string] ?? e.payload.owner}`, tone: 'run' }),
  diagnosis_failed: () => ({ text: 'Diagnoser 未能给出诊断，按规则继续' }),
  models_released: (e) => ({ text: `已释放本地模型：${(e.payload.models ?? []).join('、')}`, tone: 'ok' }),
  rounds_extended: (e) => ({ text: `仍在收敛（未解决 ${e.payload.was} → ${e.payload.unresolved}），多给一轮修复`, tone: 'ok' }),
  noop_revision: () => ({ text: '修复没有任何改动，已拒收并换更强的模型', tone: 'bad' }),
  task_stopped: (e) => ({ text: `任务停止：${e.payload.detail ?? e.payload.reason}`, tone: 'bad' }),
  transport_retry: () => ({ text: '本地服务暂不可达，等待恢复后重试' }),
  model_completed: (e) => {
    const m = e.payload.metrics;
    const rate = m?.generation_tokens_per_second;
    return { text: `${role(e)} · 响应完成${rate != null ? ' · ' + rate + ' tok/s' : ''}`, tone: 'ok' };
  },
  model_failed: (e) => ({ text: `${role(e)} · 模型调用失败`, tone: 'bad' }),
  planner_reused: () => ({ text: 'Planner 方案复用（未重新调用）', tone: 'ok' }),
  test_format_applied: () => ({ text: '测试文件已格式化（无模型调用）', tone: 'ok' }),
  check_plan_supplemented: () => ({ text: '验证方案已补充必需检查' }),
  repair_context_selected: () => ({ text: '已选取修复上下文' }),
  project_file_generation_started: () => ({ text: '开始逐文件生成', tone: 'run' }),
  project_plan_approved: () => ({ text: '方案已确认', tone: 'ok' }),
  project_code_approved: () => ({ text: '代码已批准', tone: 'ok' }),
  human_code_approved: () => ({ text: '代码已批准', tone: 'ok' }),
  source_review_report: () => ({ text: '源码检查完成' }),
  tool_intent: (e) => ({ text: `运行 ${e.payload.operation ?? e.payload.step_id ?? '检查'}`, tone: 'run' }),
  step_finished: (e) => ({ text: `${e.payload.step_id ?? '步骤'} ${e.payload.status === 'succeeded' ? '通过' : String(e.payload.status ?? '结束')}`, tone: e.payload.status === 'succeeded' ? 'ok' : e.payload.status === 'failed' ? 'bad' : undefined }),
  app_requested: () => ({ text: '运行程序', tone: 'run' }),
  app_finished: () => ({ text: '程序运行结束' }),
  cancellation_requested: () => ({ text: '已请求取消', tone: 'bad' }),
};

export function ActivityLog({ detail }: { detail: Detail }) {
  const t0 = detail.run.data.created_at ?? detail.events[0]?.created ?? 0;
  const rows = detail.events.flatMap((e) => {
    const r = MAP[e.type]?.(e);
    return r ? [{ seq: e.seq, at: Math.max(0, e.created - t0), ...r }] : [];
  });
  if (!rows.length) return null;
  return (
    <details className="activity" open={rows.length <= 8}>
      <summary>活动记录 · {rows.length} 条</summary>
      <ol>
        {rows.slice(-40).map((r) => (
          <li key={r.seq} className={r.tone ? 'a-' + r.tone : ''}>
            <i className="dot" />
            <span>{r.text}</span>
            <time>+{fmtSeconds(r.at)}</time>
          </li>
        ))}
      </ol>
    </details>
  );
}
