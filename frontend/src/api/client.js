const token = document.querySelector('meta[name="masa-token"]').content;
export async function api(path, body) {
  const response = await fetch('/api' + path, {method:body === undefined ? 'GET':'POST',headers:{'X-MASA-Token':token,'Content-Type':'application/json'},...(body === undefined ? {} : {body:JSON.stringify(body)})});
  if(response.status===403) throw new Error('本地服务会话已变化，请刷新整个页面后重试。');
  let data;
  try {data = await response.json();} catch {throw new Error('服务未返回有效 JSON，请确认后端已更新并重新启动。');}
  if (!response.ok) throw new Error(data.error || '请求失败');
  return data;
}
