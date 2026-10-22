"""模型的有效上下文窗口：配置值、模型声明值、运行时实际分配值，已知的取最小；未知的跳过，既不当作 0 也不当作无穷。
The effective context window of a model: the configured value, the model's declared value and the runtime's actual allocation - the smallest known one wins; an unknown source is skipped, never treated as 0 or as infinite.

为什么 / Why
  本地模型对超出窗口的 prompt 会静默截断（实测），所以发请求之前必须知道窗口到底多大；记忆策略（application/v2/memory.py）按这个数选。
  A local server silently truncates a prompt that exceeds its window (measured), so the real window must be known before a request is sent; the memory strategy is chosen from this number.

来源 / Sources
  configured  模型配置里的 context_limit（我们发请求时也把它作为 Ollama 的 num_ctx）。 context_limit in the model profile (also sent to Ollama as num_ctx).
  declared    模型自己声明的最大上下文：Ollama `POST /api/show` 的 model_info["<架构>.context_length"]；云端用配置里的 context_window（若有）。 The model's own maximum: Ollama /api/show model_info, or the profile's context_window for a cloud model.
  runtime     Ollama `GET /api/ps` 对已加载模型报告的 context_length。 What /api/ps reports for the loaded model.
任何网络错误都只是“这个来源未知”。 Any network error simply means "this source is unknown".

effective 是**我们下一次请求**会得到的窗口：我们发请求时总是带上 num_ctx = configured，所以已加载实例的大小（runtime）只是“现在装的是什么”——
别的客户端留下的小窗口实例会被我们的请求重新加载，不该把我们的预算压低。没有配置值（没有发 num_ctx）时，才退回 runtime，再退回 declared。
`effective` is the window our NEXT request will get: we always send num_ctx = configured, so the loaded instance's size (runtime) only says what is loaded right now - a small instance left behind
by another client is reloaded by our request and must not shrink our budget. Only without a configured value (no num_ctx sent) does it fall back to runtime, then declared.
"""
import json
import time
import urllib.error
import urllib.request

TTL_SECONDS = 60
_CACHE = {}


def ollama_root(base_url):
    """Ollama 原生接口的根地址（去掉 /v1 之类的路径）。 The root of the native Ollama API (without a /v1 style path)."""
    text = str(base_url or '').strip().rstrip('/')
    scheme, _, rest = text.partition('://')
    host = rest.split('/', 1)[0]
    return f'{scheme}://{host}' if scheme and host else ''


def _http(url, body=None, timeout=3):
    """一次不重试、不跟随重定向的小请求；失败返回 None。 One small request without retries or redirects; None on failure."""
    request = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None, headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read(2_000_001)
        value = json.loads(raw) if len(raw) <= 2_000_000 else None
        return value if isinstance(value, dict) else None
    except (OSError, ValueError, urllib.error.URLError):
        return None


def _declared_from_show(info):
    for key, value in ((info or {}).get('model_info') or {}).items():
        if str(key).endswith('.context_length') and isinstance(value, int) and value > 0:
            return value
    return None


def _runtime_from_ps(info, model):
    for item in (info or {}).get('models') or []:
        if model in (item.get('name'), item.get('model')) and isinstance(item.get('context_length'), int) and item['context_length'] > 0:
            return item['context_length']
    return None


def resolve(config, *, fetch=_http, now=time.time):
    """{effective, configured, declared, runtime, source}；effective 是已知来源的最小值，source 说明它来自哪一个。
    {effective, configured, declared, runtime, source}: the smallest known value and which source it came from."""
    key = (config.get('base_url'), config.get('model'), config.get('context_limit'))
    cached = _CACHE.get(key)
    if cached and now() - cached[0] < TTL_SECONDS:
        return cached[1]
    configured = config.get('context_limit') if isinstance(config.get('context_limit'), int) and config['context_limit'] > 0 else None
    declared = runtime = None
    if config.get('model_type') == 'local' and config.get('protocol') == 'ollama':
        root = ollama_root(config.get('base_url'))
        if root:
            declared = _declared_from_show(fetch(root + '/api/show', {'model': config.get('model')}))
            runtime = _runtime_from_ps(fetch(root + '/api/ps'), config.get('model'))
    elif isinstance(config.get('context_window'), int) and config['context_window'] > 0:
        declared = config['context_window']
    if configured:  # 我们发 num_ctx = configured：得到的窗口是它，但不会超过模型自己的上限 / we send num_ctx = configured: that is what we get, capped by the model's own maximum
        source, effective = ('declared', declared) if declared and declared < configured else ('configured', configured)
    elif runtime:
        source, effective = 'runtime', runtime
    elif declared:
        source, effective = 'declared', declared
    else:
        source, effective = 'unknown', None
    result = {'effective': effective, 'configured': configured, 'declared': declared, 'runtime': runtime, 'source': source}
    _CACHE[key] = (now(), result)
    return result


def forget():
    """测试用：清空缓存。 For tests: clear the cache."""
    _CACHE.clear()
