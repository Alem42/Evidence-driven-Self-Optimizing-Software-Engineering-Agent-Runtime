"""API 账户管理：一把 key 往往能用多个模型。这里按 (地址 + key) 把配置归为“账户”，向官方接口查询可用模型与余额，
并一键把新模型加成配置。密钥不出后端；账户编号是 (地址 + key 的哈希) 的前 12 位，不可逆推出 key。
API account management: one key often unlocks several models. Profiles are grouped into "accounts" by (base URL + key); the
official endpoints are queried for models and balance, and new models can be added as profiles with one click. Keys never leave the
backend; an account id is a truncated hash of (base URL + key).
"""
import hashlib
import json
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from masa.domain.models import MasaError

# 官方文档给出的价格（2026-10-04 抓取，USD / 百万 token，取高峰期、缓存未命中，偏保守）。只是“建议值”，用户可改；
# 实际可用模型与上下文以 GET /models 的返回为准。
# Prices from the official docs (fetched 2026-10-04; USD per million tokens, peak hours, cache miss: the conservative side).
# Suggestions only; the live GET /models response is the source of truth for models and context windows.
KNOWN = {
    'api.deepseek.com': {
        'source': 'https://api-docs.deepseek.com/quick_start/pricing',
        'currency': 'USD',
        'balance_path': '/user/balance',
        'models': {
            'deepseek-flash': {'level': 2, 'price_in': 0.3, 'price_out': 1.2, 'note': '轻量、便宜；适合第一层云端兜底'},
            'deepseek-v4-pro': {'level': 3, 'price_in': 1.32, 'price_out': 3.96, 'note': '更强；留给疑难与诊断'},
        },
    },
}

TIMEOUT = 15
MAX_BYTES = 262144


def account_id(base_url: str, key: str) -> str:
    return hashlib.sha256(f'{base_url.rstrip("/")}\n{key}'.encode()).hexdigest()[:12]


def host_of(base_url: str) -> str:
    return (urlsplit(base_url).hostname or '').lower()


def _get(base_url: str, key: str, path: str) -> dict:
    """带 Bearer 的只读 GET；错误信息不含 key，也不回显上游响应正文。 Authenticated read-only GET; errors never echo the key or upstream bodies."""
    request = urllib.request.Request(base_url.rstrip('/') + path, headers={'Authorization': 'Bearer ' + key, 'Accept': 'application/json'})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            raw = response.read(MAX_BYTES + 1)
    except urllib.error.HTTPError as exc:
        exc.close()
        raise MasaError({401: 'API 拒绝了这把 key（401）：请检查 key 是否有效', 403: 'API 拒绝访问（403）', 404: '该服务不提供这个查询接口（404）',
                         429: 'API 限流（429），稍后再试'}.get(exc.code, f'API 返回 HTTP {exc.code}')) from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise MasaError('无法连接到该 API 服务（网络或地址问题）') from None
    if len(raw) > MAX_BYTES:
        raise MasaError('API 响应过大，已拒绝')
    try:
        return json.loads(raw)
    except ValueError:
        raise MasaError('API 返回了无法解析的内容') from None


def fetch_models(base_url: str, key: str) -> list[dict]:
    """GET /models（OpenAI 兼容）。规范化为 {id, name, context_window, max_output_tokens, vision, efforts}。"""
    data = _get(base_url, key, '/models')
    items = data.get('data') if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise MasaError('该服务的 /models 返回格式不是 OpenAI 兼容的列表')
    out = []
    for item in items[:200]:
        if not isinstance(item, dict) or not isinstance(item.get('id'), str):
            continue
        modalities = item.get('input_modalities') or []
        out.append({'id': item['id'], 'name': item.get('name') or item['id'],
                    'context_window': item.get('context_window') if type(item.get('context_window')) is int else None,
                    'max_output_tokens': item.get('max_output_tokens') if type(item.get('max_output_tokens')) is int else None,
                    'vision': 'image' in modalities if isinstance(modalities, list) else False,
                    'efforts': (item.get('effort') or {}).get('supported_levels') if isinstance(item.get('effort'), dict) else None})
    return out


def fetch_balance(base_url: str, key: str) -> dict:
    """余额：目前只有已知提供商（DeepSeek 的 /user/balance）有官方接口；其它返回 supported=False，不猜。"""
    known = KNOWN.get(host_of(base_url))
    if not known or not known.get('balance_path'):
        return {'supported': False, 'reason': '该服务没有已知的余额查询接口'}
    data = _get(base_url, key, known['balance_path'])
    infos = data.get('balance_infos') if isinstance(data, dict) else None
    if not isinstance(infos, list):
        raise MasaError('余额接口返回格式不符合预期')
    return {'supported': True, 'available': bool(data.get('is_available')),
            'balances': [{'currency': str(i.get('currency')), 'total': str(i.get('total_balance')), 'granted': str(i.get('granted_balance')),
                          'topped_up': str(i.get('topped_up_balance'))} for i in infos if isinstance(i, dict)]}


def suggestion(base_url: str, model_id: str) -> dict | None:
    """已知模型的建议配置（等级、价格、币种与出处）；未知模型返回 None。 Suggested settings for a known model."""
    known = KNOWN.get(host_of(base_url))
    entry = known and known['models'].get(model_id)
    if not entry:
        return None
    return {**entry, 'currency': known['currency'], 'source': known['source']}
