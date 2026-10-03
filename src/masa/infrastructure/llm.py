"""有界 OpenAI-compatible 调用；模型只返回提案。 Bounded compatible calls; models only propose actions."""

import json
import math
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from masa.domain.models import MasaError, TransportFailure, canonical, ContextOverflow
from masa.domain.tokens import estimate_tokens_lower
from masa.agents.protocol import instruction_for, validate_response
from masa.agents.schemas import response_schema


def validate_config(config):
    """校验目的地址和单次限制。 Validate the destination and per-request limits."""
    values = {k: config.get(k, "") for k in ("name", "base_url", "model")}
    if any(
        not isinstance(v, str) or len(v) > 2048 or "\n" in v or "\r" in v
        for v in values.values()
    ):
        raise MasaError("invalid provider metadata")
    values = {k: v.strip() for k, v in values.items()}
    values['base_url'] = values['base_url'].rstrip('/')
    if values['base_url'].endswith('/chat/completions'):
        values['base_url'] = values['base_url'][:-len('/chat/completions')]
    url = urlsplit(values["base_url"])
    kind=config.get('model_type','cloud')
    if kind not in {'local','cloud'}:raise MasaError('invalid model type')
    protocol=config.get('protocol','ollama' if kind=='local' else 'openai')
    if protocol not in {'ollama','openai'} or (protocol=='ollama' and kind!='local'):
        raise MasaError('Ollama protocol requires a local model profile')
    if kind=='local' and values['base_url'] and url.hostname not in {'127.0.0.1','localhost','::1'}:
        raise MasaError('local models must use a loopback endpoint')
    values.update(model_type=kind,protocol=protocol)
    enabled=config.get('enabled',True)
    if type(enabled) is not bool:raise MasaError('enabled must be boolean')
    values['enabled']=enabled
    for name,default,low,high in [('level',1 if kind=='local' else 2,1,100),('priority',0,0,10000),('context_limit',8192 if kind=='local' else 32768,512,262144)]:
        value=config.get(name,default)
        if type(value) is not int or not low<=value<=high:raise MasaError('invalid '+name)
        values[name]=value
    roles=config.get('roles',[])
    allowed={'project_planner','project_tester','project_developer','project_repair','project_test_revision','project_test_reviewer','project_triage','project_diagnoser','code_generation','verifier'}
    if not isinstance(roles,list) or any(not isinstance(r,str) or r not in allowed for r in roles) or len(set(roles))!=len(roles):
        raise MasaError('invalid model roles')
    values['roles']=roles
    for name in ('input_price_per_million','output_price_per_million'):
        value=config.get(name)
        if value is not None and (type(value) not in {int,float} or not math.isfinite(value) or value<0):raise MasaError('invalid '+name)
        values[name]=value
    if values["base_url"] and (
        not url.hostname
        or url.username
        or url.password
        or url.query
        or url.fragment
        or not (
            url.scheme == "https"
            or url.scheme == "http"
            and url.hostname in {"127.0.0.1", "localhost", "::1"}
        )
    ):
        raise MasaError(
            "Base URL must use HTTPS or local HTTP, without credentials/query/fragment"
        )
    for key, default, low, high in [
        ("timeout_seconds", 180 if kind=='local' else 30, 1, 600 if kind=='local' else 60),
        ("max_output_tokens", 2048, 64, 8192),
    ]:
        value = config.get(key, default)
        if type(value) is not int or not low <= value <= high:
            raise MasaError("invalid " + key)
        values[key] = value
    values["token_parameter"] = config.get("token_parameter", "max_completion_tokens")
    if values["token_parameter"] not in {"max_completion_tokens", "max_tokens"}:
        raise MasaError("invalid token parameter")
    values["thinking"] = config.get("thinking", "auto")
    if values["thinking"] not in {"auto", "enabled", "disabled"}:
        raise MasaError("invalid thinking mode")
    return values


# 每个本地请求都带保活时间：就算进程崩溃，模型最多再占 2 分钟显存，而不是 Ollama 默认的 5 分钟或更久。
# Every local request carries a keep-alive: after a crash the model holds VRAM for 2 minutes at most.
LOCAL_KEEP_ALIVE = '120s'


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        """禁止将认证头重定向到其他地址。 Never redirect authorization to another endpoint."""
        return None


class ChatProvider:
    def __init__(self, config, key=''):
        """固定本次运行配置，密钥不进入 profile。 Freeze run configuration without exposing credentials."""
        self.config = validate_config(config)
        if not self.config["base_url"] or not self.config["model"] or (self.config['model_type']=='cloud' and not key):
            raise MasaError("provider requires Base URL, model and API key")
        if not isinstance(key, str) or len(key) > 8192 or "\n" in key or "\r" in key:
            raise MasaError("invalid API key")
        self.key = key
        self.profile = {
            "provider": "openai-compatible",
            "model": self.config["model"],
            "base_url": self.config["base_url"],
            "max_output_tokens": self.config["max_output_tokens"],
            "token_parameter": self.config["token_parameter"],
            "policy_version": "json-actions-v1",
        }
        self.profile["thinking"] = self.config["thinking"]
        # 云端旧指纹保持不变；本地传输参数明确进入调用身份。
        # Preserve legacy cloud identity; bind local transport parameters explicitly.
        if self.config['model_type']=='local':
            self.profile.update(provider='ollama-native' if self.config['protocol']=='ollama' else 'local-openai-compatible',
                context_limit=self.config['context_limit'],timeout_seconds=self.config['timeout_seconds'])
        self.usage = None
        self.metrics = None
        self.contract_diagnostic = None
        self.loaded = False  # 这个实例是否可能让本地模型驻留在显存里 / may this instance have left a local model resident in VRAM

    def unload(self):
        """立刻把本地模型从显存里释放（Ollama: keep_alive=0）。尽力而为：失败只返回 False，绝不抛异常。
        Release the local model from VRAM right away (Ollama: keep_alive=0). Best effort: never raises."""
        if self.config['model_type'] != 'local' or self.config['protocol'] != 'ollama':
            return False
        body = json.dumps({'model': self.config['model'], 'keep_alive': 0, 'stream': False}).encode()
        request = urllib.request.Request(self.config['base_url'].rstrip('/') + '/api/generate', data=body,
                                         headers={'Content-Type': 'application/json'}, method='POST')
        try:
            with urllib.request.build_opener(NoRedirect()).open(request, timeout=15) as response:
                response.read(4096)
            self.loaded = False
            return True
        except (OSError, urllib.error.URLError, ValueError):
            return False

    def reachable(self, timeout=3):
        """本地服务是否在响应（Ollama: /api/version；兼容服务: /models）。 Is the local server answering?"""
        path = '/api/version' if self.config['protocol'] == 'ollama' else '/models'
        try:
            with urllib.request.build_opener(NoRedirect()).open(self.config['base_url'].rstrip('/') + path, timeout=timeout) as response:
                response.read(1024)
            return True
        except (OSError, urllib.error.URLError, ValueError):
            return False

    def wait_until_reachable(self, max_seconds, *, interval=3, sleep=time.sleep, clock=time.monotonic):
        """等本地服务恢复（用户能容忍很长的时间）。只用于本地模型；云端没有这个概念。
        Wait for the local server to come back (the user tolerates long waits). Local models only."""
        if self.config['model_type'] != 'local':
            return False
        deadline = clock() + max_seconds
        while True:
            if self.reachable():
                return True
            if clock() >= deadline:
                return False
            sleep(interval)

    def respond(self, context, *, timeout=None):
        """调用一次模型并严格校验 JSON 提案，不自动重试计费请求。 Call once and validate JSON actions without automatic billed retries."""
        self.usage = None
        self.metrics = None
        if not self.config['enabled']:raise MasaError('model profile is disabled')
        purpose=context.get('purpose','verifier')
        if self.config['roles'] and purpose not in self.config['roles']:
            raise MasaError('model profile does not allow role '+purpose)
        deadline = time.monotonic() + min(
            self.config["timeout_seconds"], timeout or self.config["timeout_seconds"]
        )
        instruction = instruction_for(context)
        if self.config['protocol']=='ollama' and purpose=='project_tester':
            instruction+=' Local transport override: checks MUST be an OBJECT keyed by go_test (required), go_vet and/or go_fmt_check (optional), not an array. Each value has purpose, acceptance_indices and for go_test cases. Omit operation fields: the key supplies the operation. All business rules above still apply.'
        if self.config['protocol']=='ollama' and purpose=='project_developer' and context.get('generation_mode')!='files-v1':
            instruction+=' Copy this exact go.mod value: '+json.dumps(f"module {context['spec']['module']}\n\ngo 1.27.0\n")+'. Keep implementation concise. Avoid repetitive comments.'
        payload = {
            "model": self.config["model"],
            "messages": [
                {"role": "system", "content": instruction},
                {"role": "user", "content": canonical(context)},
            ],
            "response_format": {"type": "json_object"},
            self.config["token_parameter"]: self.config["max_output_tokens"],
            "stream": False,
        }
        if self.config["thinking"] != "auto":
            payload["thinking"] = {"type": self.config["thinking"]}
        endpoint='/chat/completions'
        if self.config['protocol']=='ollama':
            # 原生端点明确限制上下文/输出并关闭流，仍复用同一角色契约。
            # Native options bound context/output without streaming; role contracts remain shared.
            # 多文件源码的复杂语法约束会导致部分模型重复；JSON模式后仍严格做路径/模块校验。
            # Complex code grammars can loop on some models; JSON mode still requires strict path/module validation.
            payload={'keep_alive':LOCAL_KEEP_ALIVE,'model':self.config['model'],'messages':payload['messages'],'format':'json' if purpose=='project_developer' and context.get('generation_mode')!='files-v1' else response_schema(context),'stream':False,
                     'options':{'num_ctx':self.config['context_limit'],'num_predict':self.config['max_output_tokens']}}
            if self.config['thinking']!='auto':payload['think']=self.config['thinking']=='enabled'
            endpoint='/api/chat'
        if self.config['model_type'] == 'local':
            self.loaded = True  # 请求发出后模型就会被加载 / the request will load the model
        raw = canonical(payload).encode()
        if len(raw) > 262144:
            raise MasaError("model input byte limit exceeded")
        # 本地服务（Ollama 等）在输入超出窗口时静默截断而不是报错，所以必须在发出前拦截。云端 API 会明确报错，不需要。
        # 只在“几乎一定溢出”（下界估算仍超过窗口）时拒绝，避免误杀本来能跑的请求。
        # Local servers silently truncate an over-long prompt, so guard before sending (cloud APIs fail loudly and need no guard).
        # Reject only when even the LOWER-bound estimate exceeds the window.
        if self.config['model_type'] == 'local':
            low = estimate_tokens_lower(payload['messages'][0]['content'] + payload['messages'][1]['content'])
            if low > self.config['context_limit']:
                raise ContextOverflow(low, self.config['context_limit'], self.config['model'])
        request = urllib.request.Request(
            self.config["base_url"].rstrip("/") + endpoint,
            data=raw,
            headers={
                "Content-Type": "application/json",
                **({"Authorization": "Bearer " + self.key} if self.key and self.config['protocol']!='ollama' else {}),
            },
            method="POST",
        )
        try:
            # 不读取错误响应正文，避免上游回显密钥或请求内容进入日志。
            # Never expose upstream error bodies, which may echo credentials or request contents.
            with urllib.request.build_opener(NoRedirect()).open(
                request, timeout=max(0.1, deadline - time.monotonic())
            ) as response:
                chunks = []
                size = 0
                while True:
                    if time.monotonic() >= deadline:
                        raise MasaError(
                            "model request timed out; billing may be unknown"
                        )
                    chunk = response.read1(min(65536, 1048577 - size))
                    if not chunk:
                        break
                    chunks.append(chunk)
                    size += len(chunk)
                    if size > 1048576:
                        raise MasaError("model response exceeds byte limit")
                decoded = json.loads(b"".join(chunks))
        except urllib.error.HTTPError as exc:
            code = exc.code
            # 本地服务仅提取受限错误说明，便于判断模型/上下文配置。
            # Read only a bounded local error message to diagnose model/context configuration.
            local_error=None
            if self.config['protocol']=='ollama':
                try:
                    message=json.loads(exc.read(4096)).get('error')
                    if isinstance(message,str):local_error=message[:500].replace(self.key,'[REDACTED]') if self.key else message[:500]
                except (ValueError,AttributeError,OSError):pass
            exc.close()
            if local_error:raise MasaError(f'Ollama HTTP {code}: {local_error}; no automatic retry') from None
            raise MasaError(
                f"model HTTP {code}; no automatic retry; check API configuration"
            ) from None
        except (OSError, urllib.error.URLError):
            # 没有拿到响应：连接被拒绝/重置/超时。 No response received: refused, reset or timed out.
            raise TransportFailure("model transport failure: no response received; billing may be unknown") from None
        except ValueError:
            raise MasaError("model response was not valid JSON; billing may be unknown") from None
        try:
            if self.config['protocol']=='ollama':
                # verbose 同源的纳秒计数，只计算真实完成响应的速率。
                # Use the same nanosecond counters as verbose output; never invent live rates.
                fields=('total_duration','load_duration','prompt_eval_duration','eval_duration','prompt_eval_count','eval_count')
                self.metrics={k:decoded.get(k) if type(decoded.get(k)) is int and decoded[k]>=0 else None for k in fields}
                for name,count,duration in [('generation_tokens_per_second','eval_count','eval_duration'),('prompt_tokens_per_second','prompt_eval_count','prompt_eval_duration')]:
                    n=self.metrics[count];d=self.metrics[duration]
                    self.metrics[name]=round(n*1e9/d,2) if n is not None and d else None
                # 统一实际用量与完成标记，未知字段保持未知，不读取 thinking。
                # Normalize actual usage and completion without persisting private thinking.
                prompt=decoded.get('prompt_eval_count');completion=decoded.get('eval_count')
                valid=lambda n:type(n) is int and n>=0
                decoded={'usage':{'prompt_tokens':prompt if valid(prompt) else None,'completion_tokens':completion if valid(completion) else None,
                                  'total_tokens':prompt+completion if valid(prompt) and valid(completion) else None},
                         'choices':[{'finish_reason':decoded.get('done_reason') if decoded.get('done') is True else 'incomplete',
                                     'message':decoded.get('message')}]}
            usage = decoded.get("usage")
            if isinstance(usage, dict):
                self.usage = {
                    k: usage.get(k)
                    if type(usage.get(k)) is int and usage[k] >= 0
                    else None
                    for k in ("prompt_tokens", "completion_tokens", "total_tokens")
                }
            choice = decoded["choices"][0]
            if choice.get("finish_reason") != "stop" or choice["message"].get(
                "refusal"
            ):
                raise MasaError("model refused or returned incomplete output")
            content = choice["message"]["content"]
            if not isinstance(content, str):
                raise MasaError("model content must be text JSON")
            try:
                action = json.loads(content.replace(self.key, "[REDACTED]") if self.key else content)
            except json.JSONDecodeError as exc:
                # 已收到响应但契约无效，与结果不确定的网络失败区分；不输出原文。
                # Distinguish a received invalid response from uncertain transport, without exposing content.
                # 标记“已收到”，协调器才会带原因重试/升级，而不是把它当成网络失败直接中止（真实模拟中 DeepSeek 返回截断 JSON 即因此中止）。
                # Mark it as RECEIVED so the coordinator retries/escalates instead of aborting as if it were a transport failure.
                self.contract_diagnostic={'action_type':'invalid_json','top_keys':[]}
                raise MasaError(f'model action JSON invalid at line {exc.lineno}, column {exc.colno}; no automatic retry') from None
            # 只记录结构，不记录源码、思考或异常原文，用于定位收到后的契约失败。
            # Record response shape only, never code, thinking or raw exception text.
            self.contract_diagnostic={'action_type':type(action).__name__,
                'top_keys':[str(k).replace(self.key,'[REDACTED]')[:80] if self.key else str(k)[:80] for k in list(action)[:8]] if isinstance(action,dict) else []}
        except (KeyError, IndexError, TypeError, ValueError, AttributeError):
            raise MasaError("invalid model response envelope or JSON action") from None
        if self.config['protocol']=='ollama' and purpose=='project_tester' and isinstance(action,dict) and isinstance(action.get('checks'),dict):
            # 仅归一化本地传输形状；原业务校验仍拒绝非法工具和内容。
            # Normalize transport shape only; business validation still rejects invalid tools/content.
            checks=[]
            for operation,check in action['checks'].items():
                if not isinstance(check,dict) or 'operation' in check:raise MasaError('invalid local check object')
                checks.append({'operation':operation,**check})
            action={**action,'checks':checks}
        return validate_response(context, action, self.key)
