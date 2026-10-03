// 唯一的状态推导函数：界面只解释后端事实，不创造执行状态。
// The single status derivation: the UI interprets backend facts and never invents execution state.
import type { Detail, Job } from '../api/types';

export type Tone = 'neutral' | 'running' | 'wait' | 'ok' | 'bad' | 'warn';

export interface DerivedStatus {
  key: string;
  label: string; // 徽标文字 badge text
  tone: Tone;
  live: boolean; // 是否有真实工作线程在推进 a worker is really progressing
  needsUser: boolean; // 是否在等待用户 waiting for a human decision
  headline: string; // 一句话标题 one-line headline
  hint: string; // 解释 explanation
}

export const stageLabels: Record<string, string> = {
  planning: 'Planner 规划',
  project_planner: 'Planner 规划',
  project_tester: 'Tester 测试方案',
  generation: '生成代码',
  project_developer: 'Developer 生成',
  verification: 'Go 验证',
  repair: '修复实现',
  project_repair: '修复实现',
  test_revision: '修订测试',
  project_test_revision: '修订测试',
  planning_retry: '重试测试方案',
  test_format: '整理测试格式',
  application: '运行程序',
  load: 'Ollama 加载模型',
  unload: 'Ollama 释放模型',
  test: 'Ollama 真实测速',
  preparing: '准备请求',
};

export const statusText: Record<string, string> = {
  pending: '待执行',
  running: '执行中',
  succeeded: '已完成',
  failed: '失败',
  blocked: '等待确认',
  cancelled: '已取消',
  paused: '已暂停',
  created: '准备中',
  needs_attention: '需检查',
};

const make = (key: string, label: string, tone: Tone, headline: string, hint: string, live = false, needsUser = false): DerivedStatus => ({
  key,
  label,
  tone,
  live,
  needsUser,
  headline,
  hint,
});

export function deriveStatus(detail: Detail | undefined, job?: Job | null): DerivedStatus {
  if (!detail) return make('loading', '读取中', 'neutral', '正在读取任务', '正在读取保存的任务记录。');
  const run = detail.run;
  const plan = run.data.project_plan;
  // 后台任务只在指向当前版本（或尚未分配版本）时才算附着。 A job is attached only to its own version.
  const attached = !job?.run_id || job.run_id === run.id;
  const jobRunning = attached && job?.status === 'running';
  const live = Boolean(detail.active || detail.role_active || jobRunning);

  if (plan?.status === 'waiting_for_input')
    return make('clarify', '等待回答', 'wait', 'Planner 需要你补充需求', '回答问题后继续原任务；等待期间不会调用模型。', false, true);
  if (live) {
    if (run.cancel_requested || run.status === 'cancelled')
      return make('cancelling', '取消中', 'warn', '取消已记录 · 等待当前请求结束', '模型请求无法被强行中断，结果返回后不会被应用。', true);
    const stage = jobRunning ? stageLabels[job!.stage || job!.phase || ''] : undefined;
    return make('running', '执行中', 'running', stage || '任务正在执行', '角色响应和真实工具结果会自动更新，无需反复刷新。', true);
  }
  if (run.status === 'cancelled') return make('cancelled', '已取消', 'neutral', '任务已取消', '已有代码与证据保留在版本记录中。');
  if (attached && job?.status === 'interrupted')
    return make('interrupted', '已中断', 'warn', '任务已中断', '服务曾重启；请检查并恢复，未知请求不会被自动重发。');
  if (plan && run.status === 'failed')
    return make('model_failed', '模型阶段失败', 'bad', '模型阶段未完成', '查看日志中的响应或契约错误；此草稿尚未执行 Go 工具验证。');
  if (run.status === 'failed' || run.status === 'needs_attention')
    return make('verify_failed', '验证未通过', 'bad', '验证未通过', '按真实证据修复；旧版本不会被覆盖。');
  if (plan?.status === 'awaiting_review') {
    const code = plan.kind === 'code';
    return make(
      'review',
      code ? '待确认代码' : '待确认方案',
      'wait',
      code ? '请确认生成的代码' : '请确认项目方案',
      code ? '检查文件后批准，再执行真实 Go 验证。' : '确认结构与验收标准，再生成代码与测试。',
      false,
      true,
    );
  }
  if (plan?.status === 'approved')
    return make('approved', '已确认', 'ok', plan.kind === 'code' ? '代码已批准' : '方案已确认', '继续当前任务，系统复用已保存的审批内容。');
  if (plan) return make('saved', '已保存', 'neutral', '流程已保存', '当前没有后台执行；可检查记录并使用恢复入口。');
  if (run.status === 'succeeded') return make('passed', '验证通过', 'ok', '检查已通过', 'Gate 通过只代表所选检查通过，不等于业务正确。');
  return make('idle', statusText[run.status] || String(run.status), 'neutral', '等待继续', '可从当前任务继续执行。');
}
