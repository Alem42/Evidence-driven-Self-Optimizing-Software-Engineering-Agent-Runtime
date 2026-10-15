// “现在在做什么”：顶栏那一行字。只解释后端给出的事实（实时调用、任务阶段、状态），不创造进度。
// "What is happening now": the one line in the top bar. It interprets backend facts (live call, job stage, status) and never invents progress.
import type { Job, LiveCall } from '../api/types';
import { editingFile, writingFile } from './live';
import { stageLabels, type DerivedStatus, type Tone } from './status';

export interface Now {
  /** 一句话：“正在写 internal/stats/stats.go”。 One sentence. */
  text: string;
  /** 次要信息：角色 · 模型 · 已生成字数 · 秒。 Secondary: role · model · characters · seconds. */
  sub?: string;
  tone: Tone;
  /** 是否有模型正在生成（用于跳动的小点）。 A model is generating right now. */
  busy: boolean;
}

const role = (step?: string | null): string => (step ? stageLabels[step] ?? step : '模型');

function detail(live: LiveCall): string {
  return [role(live.step), live.model, live.chars > 0 ? `${live.chars.toLocaleString()} 字` : null, live.seconds > 0 ? `${live.seconds}s` : null].filter(Boolean).join(' · ');
}

export function describeNow(input: { status: DerivedStatus; job?: Job; live?: LiveCall | null; paused?: boolean }): Now {
  const { status, job, live, paused } = input;
  if (paused) return { text: '已暂停', sub: '在下一个节点边界停住；正在进行的模型调用会先结束', tone: 'wait', busy: false };
  if (status.needsUser) return { text: status.headline, sub: status.hint, tone: 'wait', busy: false };
  if (status.live && live) {
    const writing = writingFile(live);
    if (writing && !writing.complete) return { text: `正在写 ${writing.path}`, sub: detail(live), tone: 'running', busy: true };
    const editing = editingFile(live);
    if (editing) return { text: `正在修改 ${editing.path}`, sub: detail(live), tone: 'running', busy: true };
    return { text: `${role(live.step)} 正在${live.chars > 0 ? '回答' : '思考'}…`, sub: detail(live), tone: 'running', busy: true };
  }
  if (status.live) {
    const stage = job?.stage || job?.phase;
    const progress = job?.generation_progress;
    const file = progress?.current ? `：${progress.current}` : '';
    return { text: stage ? `${stageLabels[stage] ?? stage}${file}` : status.headline, sub: progress ? `文件 ${progress.completed}/${progress.total}` : status.hint, tone: status.tone, busy: true };
  }
  return { text: status.headline, sub: status.hint, tone: status.tone, busy: false };
}

/** 暂停状态：任务对象上的标志；事件流里最近一次 task_paused / task_resumed 作为刷新后的回退。 Pause state: the job flag, falling back to the latest pause/resume event after a reload. */
export function isPaused(job: Job | undefined, events: { type: string }[] | undefined): boolean {
  if (job?.status === 'running' && typeof job.paused === 'boolean') return job.paused;
  const last = [...(events ?? [])].reverse().find((e) => e.type === 'task_paused' || e.type === 'task_resumed');
  return job?.status === 'running' && last?.type === 'task_paused';
}
