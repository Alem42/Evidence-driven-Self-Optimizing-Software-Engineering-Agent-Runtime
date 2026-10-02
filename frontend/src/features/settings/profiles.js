// 旧后端返回值不能让整个设置页面崩溃。 Legacy payloads must not crash the settings page.
export function normalizeProfiles(value={}) {
  const compatible=Array.isArray(value.profiles);
  return {...value, compatible, profiles:compatible?value.profiles:[]};
}

// 本地模型不需要密钥，禁用模型不能出现在执行选项中。
// Local models need no key; disabled models cannot be selected for execution.
export function profileReady(p){return p.enabled!==false&&(p.ready??(p.model_type==='local'||Boolean(p.key_configured)));}
export function profileLabel(p){return `${p.name||p.model} · ${p.model_type==='local'?'本地':'云端'} L${p.level??(p.model_type==='local'?1:2)}`;}

// 支持常见 JSON 字段别名；导入只填表，不自动发送密钥。 Import common JSON aliases into the form without sending credentials.
export function parseProfile(text) {
  let p;try{p=JSON.parse(text);}catch{throw new Error('JSON 格式错误，请检查引号和逗号。');}
  if(!p || Array.isArray(p) || typeof p!=='object') throw new Error('请输入单个 API 配置 JSON 对象');
  const result={name:p.name || 'Imported API',base_url:p.base_url ?? p.baseURL ?? p.baseUrl ?? '',model:p.model ?? '',api_key:p.api_key ?? p.apiKey ?? ''};
  if(Object.values(result).some(v=>typeof v!=='string')) throw new Error('名称、URL、模型和密钥必须是字符串');
  if(!result.base_url || !result.model) throw new Error('配置必须包含 base_url 和 model');
  // 已有基本别名继续支持，多模型字段原样交给后端校验。
  // Keep legacy aliases while delegating multi-model metadata validation to the backend.
  for(const field of ['model_type','protocol','enabled','level','priority','roles','context_limit','timeout_seconds','max_output_tokens','thinking','token_parameter','input_price_per_million','output_price_per_million'])
    if(Object.hasOwn(p,field))result[field]=p[field];
  return result;
}
