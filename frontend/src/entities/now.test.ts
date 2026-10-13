import { describe, expect, it } from 'vitest';
import type { Detail, Job, LiveCall } from '../api/types';
import { describeNow, isPaused } from './now';
import { deriveStatus } from './status';

const detail = (extra: Partial<Detail> = {}): Detail =>
  ({ run: { id: 'r1', status: 'running', reason: '', data: { goal: 'g', workspace: '' } }, role_calls: [], role_active: true, steps: [], events: [], event_count: 0, tools: [], attempts: [], active: true, ...extra }) as Detail;
const job = (extra: Partial<Job> = {}): Job => ({ status: 'running', run_id: 'r1', mode: 'auto', ...extra });
const live = (files: LiveCall['files'], extra: Partial<LiveCall> = {}): LiveCall => ({ step: 'project_developer', model: 'deepseek-flash', chars: 1200, seconds: 7, seq: 3, truncated: false, files, tail: '', ...extra });

describe('describeNow', () => {
  it('names the file being written', () => {
    const now = describeNow({ status: deriveStatus(detail(), job()), live: live([{ path: 'internal/stats/stats.go', content: 'x', complete: false, kind: 'write' }]) });
    expect(now.text).toBe('正在写 internal/stats/stats.go');
    expect(now.sub).toContain('deepseek-flash');
    expect(now.sub).toContain('1,200 字');
    expect(now).toMatchObject({ busy: true, tone: 'running' });
  });

  it('says editing for an edit fragment and thinking when nothing is visible yet', () => {
    const status = deriveStatus(detail(), job());
    expect(describeNow({ status, live: live([{ path: 'a.go', content: 'x', complete: false, kind: 'edit' }]) }).text).toBe('正在修改 a.go');
    expect(describeNow({ status, live: live([], { chars: 0, step: 'project_planner' }) }).text).toBe('Planner 规划 正在思考…');
    expect(describeNow({ status, live: live([], { step: 'project_planner' }) }).text).toBe('Planner 规划 正在回答…');
  });

  it('falls back to the job stage and file progress between model calls', () => {
    const status = deriveStatus(detail(), job({ stage: 'verification', generation_progress: { completed: 2, total: 5, current: 'a.go' } }));
    const now = describeNow({ status, job: job({ stage: 'verification', generation_progress: { completed: 2, total: 5, current: 'a.go' } }), live: null });
    expect(now.text).toBe('验证：a.go');
    expect(now.sub).toBe('文件 2/5');
  });

  it('pause and "needs you" beat everything else', () => {
    const status = deriveStatus(detail(), job());
    expect(describeNow({ status, live: live([]), paused: true })).toMatchObject({ text: '已暂停', tone: 'wait', busy: false });
    const waiting = deriveStatus(detail({ active: false, role_active: false, run: { id: 'r1', status: 'paused', reason: '', data: { goal: 'g', workspace: '', project_plan: { status: 'awaiting_review', kind: 'code' } } } }));
    expect(describeNow({ status: waiting, live: null }).text).toBe('请确认生成的代码');
  });

  it('is plain status when idle', () => {
    const idle = deriveStatus(detail({ active: false, role_active: false, run: { id: 'r1', status: 'succeeded', reason: '', data: { goal: 'g', workspace: '' } } }));
    expect(describeNow({ status: idle })).toMatchObject({ text: '检查已通过', busy: false });
  });
});

describe('isPaused', () => {
  it('uses the job flag while a job runs', () => {
    expect(isPaused(job({ paused: true }), [])).toBe(true);
    expect(isPaused(job({ paused: false }), [{ type: 'task_paused' }])).toBe(false);
  });

  it('falls back to the latest pause/resume event and ignores finished jobs', () => {
    expect(isPaused(job(), [{ type: 'task_paused' }])).toBe(true);
    expect(isPaused(job(), [{ type: 'task_paused' }, { type: 'task_resumed' }])).toBe(false);
    expect(isPaused(job({ status: 'completed' }), [{ type: 'task_paused' }])).toBe(false);
    expect(isPaused(undefined, [])).toBe(false);
  });
});
