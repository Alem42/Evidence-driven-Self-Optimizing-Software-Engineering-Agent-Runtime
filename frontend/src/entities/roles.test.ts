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
