// 旧后端返回值不能让整个设置页面崩溃。 Legacy payloads must not crash the settings page.
export function normalizeProfiles(value={}) {
  const compatible=Array.isArray(value.profiles);
  return {...value, compatible, profiles:compatible?value.profiles:[]};
}

// 支持常见 JSON 字段别名；导入只填表，不自动发送密钥。 Import common JSON aliases into the form without sending credentials.
export function parseProfile(text) {
  let p;try{p=JSON.parse(text);}catch{throw new Error('JSON 格式错误，请检查引号和逗号。');}
  if(!p || Array.isArray(p) || typeof p!=='object') throw new Error('请输入单个 API 配置 JSON 对象');
  const result={name:p.name || 'Imported API',base_url:p.base_url ?? p.baseURL ?? p.baseUrl ?? '',model:p.model ?? '',api_key:p.api_key ?? p.apiKey ?? ''};
  if(Object.values(result).some(v=>typeof v!=='string')) throw new Error('名称、URL、模型和密钥必须是字符串');
  if(!result.base_url || !result.model) throw new Error('配置必须包含 base_url 和 model');
  return result;
}
