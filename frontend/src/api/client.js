const token = document.querySelector('meta[name="masa-token"]').content;
export async function api(path, body) {
  const response = await fetch('/api' + path, {method:body === undefined ? 'GET':'POST',headers:{'X-MASA-Token':token,'Content-Type':'application/json'},...(body === undefined ? {} : {body:JSON.stringify(body)})});
  if(response.status===403){const error=new Error('本地服务会话已变化，请刷新整个页面后重试。');error.status=response.status;throw error;}
  let data;
  try {data = await response.json();} catch {throw new Error('服务未返回有效 JSON，请确认后端已更新并重新启动。');}
  // 保留 HTTP 身份，让读取失败与确实不存在的任务分开处理。
  // Preserve the HTTP status so transient read failures do not discard saved job tracking.
  if (!response.ok){const error=new Error(data.error || '请求失败');error.status=response.status;throw error;}
  return data;
}
