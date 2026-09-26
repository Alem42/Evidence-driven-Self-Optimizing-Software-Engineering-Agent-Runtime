const token = document.querySelector('meta[name="masa-token"]').content;
export async function api(path, body) {
  const response = await fetch('/api' + path, {method:body === undefined ? 'GET':'POST',headers:{'X-MASA-Token':token,'Content-Type':'application/json'},...(body === undefined ? {} : {body:JSON.stringify(body)})});
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || '请求失败');
  return data;
}
