// 后端 API 客户端：会话令牌通过 /api/session 获取（仅白名单来源可得），403 时自动重取一次。
// API client: obtains the session token from /api/session and refreshes it once on 403.
// 未设置 VITE_API_BASE：开发时直连本机后端；设置为空字符串（Docker 构建）：与页面同源，由同一个服务托管前端和 API。
// Unset: dev talks to the local backend; set to an empty string (Docker build): same origin, one server hosts the frontend and the API.
const RAW_BASE = import.meta.env.VITE_API_BASE as string | undefined;
export const API_BASE: string = RAW_BASE === undefined ? 'http://127.0.0.1:8765' : RAW_BASE.replace(/\/$/, '');

export class ApiError extends Error {
  status: number;
  requestId?: string;
  constructor(message: string, status: number, requestId?: string) {
    super(message);
    this.status = status;
    this.requestId = requestId;
  }
}

let tokenPromise: Promise<string> | null = null;

async function fetchToken(): Promise<string> {
  let response: Response;
  try {
    response = await fetch(API_BASE + '/api/session');
  } catch {
    throw new ApiError(`无法连接后端 ${API_BASE}。请先运行 masa serve（见 README），并确认前端来源在允许列表中。`, 0);
  }
  if (!response.ok) throw new ApiError('后端拒绝了本前端来源，请用 --origin 添加当前页面地址。', response.status);
  return (await response.json()).token as string;
}

function token(force = false): Promise<string> {
  if (force || !tokenPromise) tokenPromise = fetchToken().catch((e) => { tokenPromise = null; throw e; });
  return tokenPromise;
}

async function send(path: string, body: unknown, force: boolean, signal?: AbortSignal): Promise<Response> {
  const headers: Record<string, string> = { 'X-MASA-Token': await token(force), 'Content-Type': 'application/json' };
  try {
    return await fetch(API_BASE + '/api' + path, {
      method: body === undefined ? 'GET' : 'POST',
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      signal,
    });
  } catch (e) {
    if ((e as Error).name === 'AbortError') throw e;
    throw new ApiError('后端连接中断，请确认 masa serve 仍在运行。', 0);
  }
}

export async function api<T = any>(path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
  let response = await send(path, body, false, signal);
  if (response.status === 403) response = await send(path, body, true, signal);
  let data: any;
  try {
    data = await response.json();
  } catch {
    throw new ApiError('服务未返回有效 JSON，请确认后端已更新并重启。', response.status);
  }
  if (!response.ok) {
    // 保留 HTTP 状态：404 与暂时性失败要分开处理。 Keep the status so 404 differs from transient failures.
    throw new ApiError((data.error || '请求失败') + (data.request_id ? ' · 诊断编号 ' + data.request_id : ''), response.status, data.request_id);
  }
  return data as T;
}
