import type { Detail, RunEvent } from '../api/types';

// 从账本事件还原“每个角色真正由哪个模型执行”。界面里选择框的默认模型不代表实际调用。
// Reconstruct which model actually ran each role from ledger events; a selector's default says nothing about what ran.
export interface RanModel {
  model: string;
  local: boolean;
}

export const isLocalRoute = (route: Record<string, any> | undefined): boolean => route?.provider === 'ollama-native' || /127\.0\.0\.1|localhost/.test(String(route?.base_url ?? ''));

export function ranModel(e: RunEvent): RanModel | null {
  const route = e.payload?.route;
  return route?.model ? { model: String(route.model), local: isLocalRoute(route) } : null;
}

/** 角色 → 按时间排列的实际模型（去重相邻）。 role → models in order, adjacent duplicates removed. */
export function modelsByRole(detail: Detail): Record<string, RanModel[]> {
  const out: Record<string, RanModel[]> = {};
  for (const e of detail.events) {
    if (e.type !== 'model_requested') continue;
    const m = ranModel(e);
    const role = e.payload?.step_id;
    if (!m || !role) continue;
    const list = (out[role] ??= []);
    if (!list.length || list[list.length - 1].model !== m.model) list.push(m);
  }
  return out;
}

export const modelText = (list: RanModel[] | undefined): string => (list ?? []).map((m) => `${m.local ? '本地' : 'API'} ${m.model}`).join(' → ');
