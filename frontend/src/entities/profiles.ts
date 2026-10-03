// 模型配置的纯函数：可用性、标签与 JSON 导入。 Pure helpers for model profiles.
import type { Profile, Profiles } from '../api/types';

/** 旧后端返回值不能让设置页崩溃。 Legacy payloads must not crash the settings page. */
export function normalizeProfiles(value: Partial<Profiles> = {}): Profiles & { compatible: boolean } {
  const compatible = Array.isArray(value.profiles);
  return { ...(value as Profiles), compatible, profiles: compatible ? (value.profiles as Profile[]) : [] };
}

/** 本地模型不需要密钥；禁用的模型不能出现在执行选项里。 */
export function profileReady(p: Profile): boolean {
  return p.enabled !== false && (p.ready ?? (p.model_type === 'local' || Boolean(p.key_configured)));
}

export const profileLevel = (p: Profile): number => p.level ?? (p.model_type === 'local' ? 1 : 2);
export const profileLabel = (p: Profile): string => `${p.name || p.model} · ${p.model_type === 'local' ? '本地' : '云端'} L${profileLevel(p)}`;

/** 路由预览：按等级、优先级排序（真实路由尚未实现，这里只展示配置顺序）。 */
export const byRouting = (a: Profile, b: Profile): number => profileLevel(a) - profileLevel(b) || (a.priority ?? 0) - (b.priority ?? 0);

const FIELDS = [
  'model_type', 'protocol', 'enabled', 'level', 'priority', 'roles', 'context_limit', 'timeout_seconds',
  'max_output_tokens', 'thinking', 'token_parameter', 'input_price_per_million', 'output_price_per_million',
] as const;

/** 支持常见 JSON 别名；导入只填表，不自动发送密钥。 */
export function parseProfile(text: string): Record<string, unknown> {
  let p: any;
  try {
    p = JSON.parse(text);
  } catch {
    throw new Error('JSON 格式错误，请检查引号和逗号。');
  }
  if (!p || Array.isArray(p) || typeof p !== 'object') throw new Error('请输入单个 API 配置 JSON 对象');
  const result: Record<string, unknown> = {
    name: p.name || 'Imported API',
    base_url: p.base_url ?? p.baseURL ?? p.baseUrl ?? '',
    model: p.model ?? '',
    api_key: p.api_key ?? p.apiKey ?? '',
  };
  if (['name', 'base_url', 'model', 'api_key'].some((k) => typeof result[k] !== 'string')) throw new Error('名称、URL、模型和密钥必须是字符串');
  if (!result.base_url || !result.model) throw new Error('配置必须包含 base_url 和 model');
  for (const f of FIELDS) if (Object.hasOwn(p, f)) result[f] = p[f];
  return result;
}
