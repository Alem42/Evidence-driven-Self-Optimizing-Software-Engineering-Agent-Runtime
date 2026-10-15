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
  when?: { guard: string } | null;
}

/** 把角色名字写进共享的标签表。 Write the role labels into the shared label table. */
export function applyRoles(roles: RoleInfo[]): RoleInfo[] {
  for (const r of roles) stageLabels[r.id] = r.label;
  return roles;
}

/** 角色分组：核心流水线 / 动态（只在指挥者开启时才可能被选中） / 离线（调优）。 Role groups: core pipeline / dynamic (only reachable with the conductor on) / offline (tuning). */
export const DYNAMIC_ROLES = ['project_conductor', 'test_skeptic', 'code_reviewer'];
export function roleGroups(roles: RoleInfo[]): { core: RoleInfo[]; dynamic: RoleInfo[]; offline: RoleInfo[] } {
  const dynamic = roles.filter((r) => DYNAMIC_ROLES.includes(r.id));
  const offline = roles.filter((r) => !r.routable && !r.legacy && !DYNAMIC_ROLES.includes(r.id) && r.permissions.writes === 'none' && r.id === 'workflow_tuner');
  const rest = roles.filter((r) => !dynamic.includes(r) && !offline.includes(r) && !r.legacy);
  return { core: rest, dynamic, offline };
}

export const levelText = (level: RoleInfo['level']): string => (level === 'top' ? '最高等级' : level === 'low' ? '低等级起步' : level === null ? '只用本地模型' : 'L' + level);
export const writesText = (writes: RoleInfo['permissions']['writes']): string => ({ none: '只读', tests: '可改测试', implementation: '可改实现' })[writes];

/** 指挥者的风险提示：数字来自 2026-10-06 的真实对照评测（docs/progress/PROGRESS_2026-10-06_005）。 The conductor's risk note; numbers come from the real A/B evaluation. */
export const CONDUCTOR_RISK =
  '实验功能，默认关闭。真实对照评测（25 次运行）里未见通过率提升：关闭 10/11 通过，开启 10/14 通过；开启后每个任务平均多花 24%～96% 的费用（额外的 Pro 调用、怀疑者、审阅者）。样本很小，结论不确定。只在你想试验时开启。';

export const FALLBACK_ORDER = ['project_planner', 'project_tester', 'project_developer', 'project_repair', 'project_test_revision', 'project_diagnoser'];
