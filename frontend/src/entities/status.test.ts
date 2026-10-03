import { describe, expect, it } from 'vitest';
import { deriveStatus } from './status';
import type { Detail } from '../api/types';

const detail = (over: Record<string, any> = {}, extra: Partial<Detail> = {}): Detail => {
  const { plan, ...run } = over;
  return {
    run: { id: 'r1', status: 'paused', reason: '', data: { goal: 'g', workspace: '', project_plan: plan }, ...run } as any,
    role_calls: [],
    role_active: false,
    steps: [],
    events: [],
    event_count: 0,
    tools: [],
    attempts: [],
    active: false,
    ...extra,
  };
};

describe('deriveStatus', () => {
  it('waiting for input needs the user', () => {
    const s = deriveStatus(detail({ plan: { status: 'waiting_for_input' } }));
    expect(s.key).toBe('clarify');
    expect(s.needsUser).toBe(true);
  });
  it('persisted running without a worker is not live', () => {
    expect(deriveStatus(detail({ status: 'running' })).live).toBe(false);
  });
  it('active worker is live', () => {
    expect(deriveStatus(detail({}, { role_active: true })).key).toBe('running');
  });
  it('cancel request while busy is cancelling', () => {
    expect(deriveStatus(detail({ cancel_requested: 1 }, { active: true })).key).toBe('cancelling');
  });
  it('planning failure is a model failure, not a verify failure', () => {
    expect(deriveStatus(detail({ status: 'failed', plan: { status: 'failed' } })).key).toBe('model_failed');
    expect(deriveStatus(detail({ status: 'failed' })).key).toBe('verify_failed');
  });
  it('awaiting review distinguishes code and plan', () => {
    expect(deriveStatus(detail({ plan: { status: 'awaiting_review', kind: 'code' } })).label).toBe('待确认代码');
    expect(deriveStatus(detail({ plan: { status: 'awaiting_review' } })).label).toBe('待确认方案');
  });
  it('a job for another version does not make this one live', () => {
    expect(deriveStatus(detail({ status: 'succeeded' }), { status: 'running', run_id: 'other' }).live).toBe(false);
  });
  it('a triage-blocked run is its own state, not a model failure', () => {
    const s = deriveStatus(detail({ status: 'failed', plan: { status: 'failed', error: 'infeasible: x', triage: { verdict: 'infeasible', findings: [] } } }));
    expect(s.key).toBe('triage_blocked');
    expect(s.tone).toBe('warn');
  });
  it('succeeded run passes', () => {
    expect(deriveStatus(detail({ status: 'succeeded' })).key).toBe('passed');
  });
});
