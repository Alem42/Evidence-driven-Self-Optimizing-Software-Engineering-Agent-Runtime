import { describe, expect, it } from 'vitest';
import { applyRoles, type RoleInfo } from './roles';
import { stageLabels } from './status';

const role = (id: string, label: string): RoleInfo => ({ id, label, description: '', order: 1, level: 'top', routable: true, permissions: { reads: [], writes: 'none' }, legacy: false });

describe('applyRoles', () => {
  it('writes labels of roles the frontend has never heard of, so a new role needs no frontend change', () => {
    expect(stageLabels['project_skeptic']).toBeUndefined();
    applyRoles([role('project_skeptic', 'Skeptic 质疑者')]);
    expect(stageLabels['project_skeptic']).toBe('Skeptic 质疑者');
  });

  it('lets the registry rename an existing role', () => {
    applyRoles([role('project_planner', 'Planner 规划（来自注册表）')]);
    expect(stageLabels['project_planner']).toContain('注册表');
  });
});

import { roleGroups, levelText, writesText } from './roles';
describe('roleGroups', () => {
  const role = (id: string, over: Partial<RoleInfo> = {}): RoleInfo => ({ id, label: id, description: 'd', order: 1, level: 'top', routable: true, permissions: { reads: [], writes: 'none' }, legacy: false, ...over });
  it('splits core, dynamic and offline roles and hides legacy ones', () => {
    const groups = roleGroups([role('project_planner'), role('project_conductor'), role('test_skeptic', { when: { guard: 'only_assertions' } }), role('workflow_tuner', { routable: false }), role('verifier', { legacy: true })]);
    expect(groups.core.map((r) => r.id)).toEqual(['project_planner']);
    expect(groups.dynamic.map((r) => r.id)).toEqual(['project_conductor', 'test_skeptic']);
    expect(groups.offline.map((r) => r.id)).toEqual(['workflow_tuner']);
  });
  it('words levels and permissions', () => {
    expect(levelText('top')).toBe('最高等级');
    expect(levelText(null)).toBe('只用本地模型');
    expect(levelText(3)).toBe('L3');
    expect(writesText('tests')).toBe('可改测试');
  });
});
