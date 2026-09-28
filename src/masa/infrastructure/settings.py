"""多 API 配置与可选本地密钥。 Multiple API profiles and opt-in local credentials."""

import json
import os
import threading
import time
import uuid

from masa.infrastructure.llm import ChatProvider, validate_config
from masa.domain.models import MasaError


class Settings:
    def __init__(self, root):
        """加载配置与用户选择保存的本地密钥。 Load profiles and credentials explicitly persisted by the user."""
        self.path = root / "provider.json"
        self.secret_path = root / 'provider-keys.local.json'
        self.lock = threading.RLock()
        self.profiles = {}
        self.keys = {}
        self.tests = {}
        self.active_id = None
        if self.path.exists():
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                entries = raw.get("profiles") if isinstance(raw, dict) else None
                if entries is None and isinstance(raw, dict):
                    entries = [{**raw, "id": "legacy"}]
                for entry in entries or []:
                    self.profiles[entry.get("id") or uuid.uuid4().hex] = (
                        validate_config(entry)
                    )
                selected = raw.get("active_id")
                self.active_id = (
                    selected
                    if selected in self.profiles
                    else next(iter(self.profiles), None)
                )
            except (ValueError, TypeError, AttributeError, OSError, MasaError):
                self.profiles = {}
                self.active_id = None
        if self.secret_path.exists():
            try:
                saved = json.loads(self.secret_path.read_text(encoding='utf-8'))
                self.keys = {i:v['key'] for i,v in saved.items() if i in self.profiles
                             and isinstance(v,dict) and v.get('base_url') == self.profiles[i]['base_url']
                             and isinstance(v.get('key'),str)}
            except (ValueError, TypeError, AttributeError, OSError):
                self.keys = {}

    def public(self):
        """仅公开元数据和密钥存在状态。 Expose metadata and credential presence only."""
        with self.lock:
            profiles = [
                {
                    **p,
                    "id": i,
                    "key_configured": bool(self.keys.get(i)),
                    "last_test": self.tests.get(i),
                }
                for i, p in self.profiles.items()
            ]
            active = next(
                (p for p in profiles if p["id"] == self.active_id), validate_config({})
            )
            return {
                **active,
                "key_configured": bool(self.keys.get(self.active_id)),
                "profiles": profiles,
                "active_id": self.active_id,
                "key_storage": "local_file" if self.secret_path.exists() else "session_memory",
                "execution_connected": bool(
                    self.tests.get(self.active_id, {}).get("ok")
                ),
                "live_supported": True,
            }

    def save(self, body):
        """保存配置，仅在用户启用时持久化密钥。 Save profiles and persist keys only after user opt-in."""
        with self.lock:
            action = body.get("action", "save")
            ident = body.get("id") or self.active_id
            profiles = dict(self.profiles)
            keys = dict(self.keys)
            tests = dict(self.tests)
            active = self.active_id
            if action in {"delete", "select"}:
                if ident not in profiles:
                    raise MasaError("unknown API profile")
                if action == "delete":
                    profiles.pop(ident)
                    keys.pop(ident, None)
                    tests.pop(ident, None)
                    if active == ident:
                        active = next(iter(profiles), None)
                else:
                    active = ident
            elif action == "save":
                if body.get("new") is True:
                    ident = uuid.uuid4().hex
                ident = ident or uuid.uuid4().hex
                if not isinstance(ident, str) or len(ident) > 128:
                    raise MasaError("invalid profile ID")
                if ident not in profiles and len(profiles) >= 20:
                    raise MasaError("at most 20 profiles")
                config = validate_config({**profiles.get(ident, {}), **body})
                key = body.get("api_key", "")
                if isinstance(key, str):
                    key = key.strip()
                if (
                    not isinstance(key, str)
                    or len(key) > 8192
                    or "\n" in key
                    or "\r" in key
                ):
                    raise MasaError("invalid API key")
                # 更换目的地址不能隐式带走旧密钥。 A changed destination must not inherit a credential.
                if profiles.get(ident, {}).get("base_url") != config["base_url"]:
                    keys.pop(ident, None)
                if body.get("clear_key") is True:
                    keys.pop(ident, None)
                elif key.strip():
                    keys[ident] = key.strip()
                if (
                    config != profiles.get(ident)
                    or key.strip()
                    or body.get("clear_key")
                ):
                    tests.pop(ident, None)
                profiles[ident] = config
                active = ident
            else:
                raise MasaError("unknown settings action")
            raw = {
                "version": 2,
                "active_id": active,
                "profiles": [dict(p, id=i) for i, p in profiles.items()],
            }
            temp = self.path.with_suffix(".tmp")
            temp.write_text(
                json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            os.replace(temp, self.path)
            # 用户显式启用本地保存后，删除/清除密钥也同步持久化。
            # After opt-in, persist credential deletions and updates as well.
            if body.get('persist_key') is True or self.secret_path.exists():
                secret_temp = self.secret_path.with_suffix('.tmp')
                secret_temp.write_text(json.dumps({i:{'base_url':profiles[i]['base_url'],'key':key}
                                                   for i,key in keys.items() if i in profiles}),encoding='utf-8')
                os.replace(secret_temp,self.secret_path)
            self.profiles, self.keys, self.tests, self.active_id = (
                profiles,
                keys,
                tests,
                active,
            )
            return self.public()

    def provider(self, ident=None, expected=None):
        """绑定配置，恢复时匹配原运行身份。 Bind configuration and match frozen identity on resume."""
        with self.lock:
            candidates = (
                [ident or self.active_id] if expected is None else list(self.profiles)
            )
            for ident in candidates:
                if ident not in self.profiles or not self.keys.get(ident):
                    continue
                provider = ChatProvider(self.profiles[ident], self.keys[ident])
                if expected is None or provider.profile == expected:
                    return provider
        raise MasaError(
            "matching API configuration/key unavailable; configure it in API settings"
        )

    def test(self, ident):
        """一次真实协议探测，不发送仓库内容。 Probe the real protocol once without repository content."""
        provider = self.provider(ident)
        try:
            result = provider.respond(
                {
                    "goal": "Connection test: request the allowed verification tool.",
                    "operation": "go_test",
                    "allowed_tools": ["go_test"],
                    "tool_results": [],
                }
            )
            if result["type"] != "tool_call":
                raise MasaError("connection responded, but tool protocol test failed")
            outcome = {
                "ok": True,
                "message": "连接及 JSON 协议测试通过",
                "usage": provider.usage,
                "cost": None,
                "time": time.time(),
            }
        except MasaError as exc:
            outcome = {
                "ok": False,
                "message": str(exc),
                "usage": provider.usage,
                "cost": None,
                "time": time.time(),
            }
        with self.lock:
            if (
                ident in self.profiles
                and self.profiles[ident] == provider.config
                and self.keys.get(ident) == provider.key
            ):
                self.tests[ident] = outcome
        return outcome
