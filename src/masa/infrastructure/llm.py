"""有界 OpenAI-compatible 调用；模型只返回提案。 Bounded compatible calls; models only propose actions."""

import json
import math
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from masa.domain.models import MasaError, canonical
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
    allowed={'project_planner','project_tester','project_developer','project_repair','project_test_revision','project_test_reviewer','code_generation','verifier'}
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

    def respond(self, context, *, timeout=None):
        """调用一次模型并严格校验 JSON 提案，不自动重试计费请求。 Call once and validate JSON actions without automatic billed retries."""
        self.usage = None
        if not self.config['enabled']:raise MasaError('model profile is disabled')
        purpose=context.get('purpose','verifier')
        if self.config['roles'] and purpose not in self.config['roles']:
            raise MasaError('model profile does not allow role '+purpose)
        deadline = time.monotonic() + min(
            self.config["timeout_seconds"], timeout or self.config["timeout_seconds"]
        )
        instruction = instruction_for(context)
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
            payload={'model':self.config['model'],'messages':payload['messages'],'format':response_schema(context),'stream':False,
                     'options':{'num_ctx':self.config['context_limit'],'num_predict':self.config['max_output_tokens']}}
            if self.config['thinking']!='auto':payload['think']=self.config['thinking']=='enabled'
            endpoint='/api/chat'
        raw = canonical(payload).encode()
        if len(raw) > 262144:
            raise MasaError("model input byte limit exceeded")
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
            exc.close()
            raise MasaError(
                f"model HTTP {code}; no automatic retry; check API configuration"
            ) from None
        except (OSError, ValueError, urllib.error.URLError):
            raise MasaError(
                "model transport/JSON failure; billing may be unknown"
            ) from None
        try:
            if self.config['protocol']=='ollama':
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
                raise MasaError(f'model action JSON invalid at line {exc.lineno}, column {exc.colno}; no automatic retry') from None
        except (KeyError, IndexError, TypeError, ValueError, AttributeError):
            raise MasaError("invalid model response envelope or JSON action") from None
        return validate_response(context, action, self.key)
