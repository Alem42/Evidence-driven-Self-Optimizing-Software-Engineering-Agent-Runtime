import { stageLabels } from './status';

// 角色清单来自后端的 RoleSpec 注册表（/api/roles）：新增一个角色，前端不用改代码就能显示它的名字与顺序。
// 静态的 stageLabels 保留为兜底（接口还没返回时）。
// The role list comes from the backend RoleSpec registry (/api/roles): a new role shows its name and order with no frontend change. The static stageLabels stay as a fallback.
export interface RoleInfo {
  id: string;
  label: string;
  description: string;
  order: number;
  level: 'top' | 'low' | number | null;
  routable: boolean;
  permissions: { reads: string[]; writes: 'none' | 'tests' | 'implementation' };
  legacy: boolean;
}

/** 把角色名字写进共享的标签表。 Write the role labels into the shared label table. */
export function applyRoles(roles: RoleInfo[]): RoleInfo[] {
  for (const r of roles) stageLabels[r.id] = r.label;
  return roles;
}

/** 角色分组：目前只有核心流水线（遗留角色不显示）。 Role groups: only the core pipeline is left (legacy roles are hidden). */
export function roleGroups(roles: RoleInfo[]): { core: RoleInfo[] } {
  return { core: roles.filter((r) => !r.legacy) };
}

export const levelText = (level: RoleInfo['level']): string => (level === 'top' ? '最高等级' : level === 'low' ? '低等级起步' : level === null ? '只用本地模型' : 'L' + level);
export const writesText = (writes: RoleInfo['permissions']['writes']): string => ({ none: '只读', tests: '可改测试', implementation: '可改实现' })[writes];

export const FALLBACK_ORDER = ['project_architect', 'project_developer', 'project_repair', 'project_test_revision', 'project_diagnoser'];
