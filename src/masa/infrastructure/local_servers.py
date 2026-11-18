"""本地 OpenAI 兼容服务（llama.cpp 的 llama-server、Strata、LM Studio 等）的只读探测：哪些配置在线、各自服务哪个模型、上下文多大，
以及本机上还有哪些没添加成配置的服务。只访问回环地址，不发送任何仓库内容，不经过系统代理。
Read-only probing of local OpenAI-compatible servers (llama.cpp llama-server, Strata, LM Studio, ...): which profiles are online, which model each serves,
its context size, and which servers on this machine are not yet profiles. Loopback only, no repository content, never through the system proxy."""
import json
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlsplit

from masa.infrastructure.llm import NoRedirect

# llama-server 与 Strata 默认 8080，LM Studio 1234，常见自选端口 8081/8082/8090。
# llama-server and Strata default to 8080, LM Studio to 1234; 8081/8082/8090 are the usual alternates.
COMMON_PORTS = (8080, 8081, 8082, 8090, 1234, 8000)
LOOPBACK = {'127.0.0.1', 'localhost', '::1'}
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())  # 空代理表：回环地址永远直连 / empty proxy map: loopback is always direct


def _get(url, timeout):
    """GET 并解析 JSON；任何失败返回 None。响应上限 256 KB。 GET and parse JSON; None on any failure; 256 KB cap."""
    try:
        with _OPENER.open(urllib.request.Request(url, headers={'Accept': 'application/json'}), timeout=timeout) as response:
            raw = response.read(262145)
        if len(raw) > 262144:
            return None
        return json.loads(raw)
    except (OSError, urllib.error.URLError, ValueError):
        return None


def probe(base_url, timeout=1.0):
    """探测一个 OpenAI 兼容地址（通常以 /v1 结尾）。离线返回 {'reachable': False}。 Probe one OpenAI-compatible base URL; offline gives {'reachable': False}."""
    base = base_url.rstrip('/')
    if urlsplit(base).hostname not in LOOPBACK:
        return {'reachable': False, 'error': 'not a loopback address'}
    models = _get(base + '/models', timeout)
    if not isinstance(models, dict):
        return {'reachable': False}
    data = models.get('data') if isinstance(models.get('data'), list) else []
    info = {'reachable': True, 'engine': 'openai-compatible',
            'served': [m['id'] for m in data if isinstance(m, dict) and isinstance(m.get('id'), str)][:20]}
    # llama-server 的 /props 给出实际上下文与模型文件；其它服务没有这个接口，保持“未知”。
    # llama-server's /props gives the real context and model file; other servers lack it and stay "unknown".
    props = _get((base[:-3] if base.endswith('/v1') else base) + '/props', timeout)
    settings = props.get('default_generation_settings') if isinstance(props, dict) else None
    if isinstance(settings, dict):
        info['engine'] = 'llama.cpp'
        n_ctx = settings.get('n_ctx')
        if type(n_ctx) is int:
            info['n_ctx'] = n_ctx
        for key, out in (('model_path', 'model_path'), ('build_info', 'build')):
            if isinstance(props.get(key), str):
                info[out] = props[key][:200]
    return info


def runtimes(profiles, active_id):
    """所有本地 OpenAI 兼容配置的实时状态，加上本机上发现的、尚未添加的服务。 Live state of every local OpenAI-compatible profile, plus servers found but not added."""
    mine = {i: p for i, p in profiles.items() if p['model_type'] == 'local' and p['protocol'] == 'openai'}
    known = {p['base_url'].rstrip('/') for p in profiles.values() if p['model_type'] == 'local'}
    extra = [f'http://127.0.0.1:{port}/v1' for port in COMMON_PORTS if f'http://127.0.0.1:{port}/v1' not in known]
    urls = [p['base_url'] for p in mine.values()] + extra
    with ThreadPoolExecutor(max_workers=8) as pool:
        probed = list(pool.map(probe, urls))
    entries = []
    for (ident, p), info in zip(mine.items(), probed):
        served = info.get('served') or []
        entries.append({'id': ident, 'name': p.get('name') or p['model'], 'model': p['model'], 'base_url': p['base_url'], 'enabled': p['enabled'],
                        'level': p['level'], 'priority': p['priority'], 'context_limit': p['context_limit'], 'is_default': ident == active_id,
                        'model_listed': (p['model'] in served) if info['reachable'] and served else None, **info})
    found = [{'base_url': url, **info} for url, info in zip(extra, probed[len(mine):]) if info.get('reachable')]
    return {'profiles': entries, 'found': found}
