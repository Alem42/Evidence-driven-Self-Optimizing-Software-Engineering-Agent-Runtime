"""多 API 配置与可选本地密钥。 Multiple API profiles and opt-in local credentials."""

import json
import os
import threading
import time
import uuid

from masa.infrastructure.llm import ChatProvider, validate_config
from masa.domain.models import MasaError
from masa.infrastructure import providers


class Settings:
    def __init__(self, root):
        """加载配置与用户选择保存的本地密钥。 Load profiles and credentials explicitly persisted by the user."""
        self.path = root / "provider.json"
        self.secret_path = root / 'provider-keys.local.json'
        self.routing_path = root / 'routing.json'
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
                    "ready": p['enabled'] and (p['model_type']=='local' or bool(self.keys.get(i))),
                    "last_test": self.tests.get(i),
                }
                for i, p in sorted(self.profiles.items(),key=lambda item:(item[1]['level'],item[1]['priority'],item[0]))
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

    # ───────────── 路由默认值（可在设置页编辑）/ routing defaults editable in Settings ─────────────
    def routing(self):
        """已保存的路由默认值（策略与预算）；文件缺失或损坏时回到内置默认。 Saved routing defaults, built-in defaults when absent."""
        from masa.application.routing import validate_budget, validate_policy
        raw = {}
        try:
            raw = json.loads(self.routing_path.read_text(encoding='utf-8')) if self.routing_path.exists() else {}
        except (ValueError, OSError):
            raw = {}
        try:
            return {'policy': validate_policy(raw.get('policy')), 'budget': validate_budget(raw.get('budget'))}
        except MasaError:
            return {'policy': validate_policy(None), 'budget': validate_budget(None)}

    def save_routing(self, body):
        """校验后原子保存。只保存与默认值不同的部分之外的完整规范化结果。 Validate, then save atomically."""
        from masa.application.routing import validate_budget, validate_policy
        policy = validate_policy(body.get('policy'))
        budget = validate_budget(body.get('budget'))
        with self.lock:
            temp = self.routing_path.with_suffix('.tmp')
            temp.write_text(json.dumps({'policy': policy, 'budget': budget}, ensure_ascii=False, indent=2), encoding='utf-8')
            os.replace(temp, self.routing_path)
        return {'policy': policy, 'budget': budget}

    # ───────────── 模型次序 / model order ─────────────
    def reorder(self, order, levels=None):
        """按给定顺序重写 (等级, 优先级)：同一等级内按出现顺序给 0,1,2…。levels 可同时改等级。
        等级沿列表非递减：路由是“从低等级起、逐级升级”，所以更靠前的模型不能比后面的等级更高。
        Rewrite (level, priority) from a list order; levels are forced non-decreasing along the list because routing climbs from low to high."""
        with self.lock:
            if not isinstance(order, list) or sorted(order) != sorted(self.profiles):
                raise MasaError('order must list every profile exactly once')
            levels = levels or {}
            if not isinstance(levels, dict) or any(k not in self.profiles or type(v) is not int or not 1 <= v <= 100 for k, v in levels.items()):
                raise MasaError('invalid levels')
            profiles = {i: dict(p) for i, p in self.profiles.items()}
            previous = 1
            counters = {}
            for ident in order:
                level = max(levels.get(ident, profiles[ident]['level']), previous)
                profiles[ident]['level'] = previous = level
                profiles[ident]['priority'] = counters.get(level, 0)
                counters[level] = counters.get(level, 0) + 1
            for p in profiles.values():
                validate_config(p)
            raw = {'version': 2, 'active_id': self.active_id, 'profiles': [dict(profiles[i], id=i) for i in order]}
            temp = self.path.with_suffix('.tmp')
            temp.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding='utf-8')
            os.replace(temp, self.path)
            self.profiles = profiles
            return self.public()

    # ───────────── API 账户 / API accounts ─────────────
    def accounts(self):
        """把云端配置按 (地址 + key) 分组。不返回 key。 Group cloud profiles by (base URL + key); keys are never returned."""
        with self.lock:
            groups = {}
            for ident, cfg in self.profiles.items():
                key = self.keys.get(ident)
                if cfg['model_type'] != 'cloud' or not key:
                    continue
                acc = providers.account_id(cfg['base_url'], key)
                groups.setdefault(acc, {'id': acc, 'base_url': cfg['base_url'], 'host': providers.host_of(cfg['base_url']), 'profiles': []})
                groups[acc]['profiles'].append({'id': ident, 'name': cfg.get('name') or cfg['model'], 'model': cfg['model'], 'level': cfg['level'],
                                                'enabled': cfg['enabled']})
            for g in groups.values():
                known = providers.KNOWN.get(g['host'])
                g['balance_supported'] = bool(known and known.get('balance_path'))
                g['pricing_source'] = known['source'] if known else None
            return list(groups.values())

    def _account(self, acc):
        """账户的 (地址, key, 模板配置)。 (base URL, key, template profile) of an account."""
        with self.lock:
            for ident, cfg in self.profiles.items():
                key = self.keys.get(ident)
                if cfg['model_type'] == 'cloud' and key and providers.account_id(cfg['base_url'], key) == acc:
                    return cfg['base_url'], key, ident, dict(cfg)
        raise MasaError('unknown API account')

    def account_models(self, acc):
        """向官方 /models 查询这把 key 能用的模型，并标出已添加的与建议配置。 Live model list with what is already added."""
        base_url, key, _, _ = self._account(acc)
        have = {cfg['model'] for ident, cfg in self.profiles.items() if cfg['base_url'] == base_url and self.keys.get(ident) == key}
        models = providers.fetch_models(base_url, key)
        for m in models:
            m['added'] = m['id'] in have
            m['suggestion'] = providers.suggestion(base_url, m['id'])
        return models

    def account_balance(self, acc):
        base_url, key, _, _ = self._account(acc)
        return providers.fetch_balance(base_url, key)

    def add_models(self, acc, model_ids):
        """把该 key 能用的新模型加成配置：复制地址与 key，按官方信息与已知建议填上下文、价格、等级。
        Add models the key can use as profiles; endpoints/keys are copied and limits/prices come from the official data."""
        base_url, key, template_id, template = self._account(acc)
        if not isinstance(model_ids, list) or not model_ids or len(model_ids) > 20:
            raise MasaError('choose 1..20 models to add')
        live = {m['id']: m for m in providers.fetch_models(base_url, key)}
        added = []
        for model_id in model_ids:
            if model_id not in live:
                raise MasaError(f'the API does not list model {model_id}')
            if any(c['base_url'] == base_url and c['model'] == model_id and self.keys.get(i) == key for i, c in self.profiles.items()):
                continue
            info, hint = live[model_id], providers.suggestion(base_url, model_id) or {}
            body = {'new': True, 'name': info['name'], 'base_url': base_url, 'model': model_id, 'api_key': key,
                    'model_type': 'cloud', 'protocol': template['protocol'], 'enabled': True,
                    'level': hint.get('level', template['level']), 'priority': 0,
                    'context_limit': max(512, min(info['context_window'] or template['context_limit'], 262144)),
                    'max_output_tokens': max(64, min(info['max_output_tokens'] or template['max_output_tokens'], 8192)),
                    'timeout_seconds': template['timeout_seconds'], 'token_parameter': template['token_parameter'],
                    'thinking': template['thinking'], 'roles': [], 'input_price_per_million': hint.get('price_in'),
                    'output_price_per_million': hint.get('price_out')}
            # save() 会把新配置设为默认；添加模型不应改变默认选择。 save() would make it the default; adding must not.
            previous_active = self.active_id
            result = self.save(body)
            if previous_active in self.profiles:
                result = self.save({'action': 'select', 'id': previous_active})
            added.append(model_id)
        return {'added': added, 'settings': self.public()}

    def select_models(self, acc, model_ids):
        """按勾选应用：勾选的模型启用（没有就先添加），没勾选的已有配置只停用、不删除（保留价格/等级等设置）。
        Apply a tick-list: ticked models are enabled (added first when missing); unticked existing profiles are disabled, never deleted.
        默认模型不变。 The default model never changes."""
        base_url, key, _, _ = self._account(acc)
        if not isinstance(model_ids, list) or len(model_ids) > 50 or any(not isinstance(m, str) for m in model_ids):
            raise MasaError('model_ids must be a list of model ids')
        wanted = set(model_ids)
        have = {cfg['model'] for i, cfg in self.profiles.items() if cfg['base_url'] == base_url and self.keys.get(i) == key}
        missing = [m for m in model_ids if m not in have]
        if missing:
            self.add_models(acc, missing)  # 校验它们确实在官方列表里 / validates against the live list
        with self.lock:
            profiles = {i: dict(p) for i, p in self.profiles.items()}
            for i, cfg in profiles.items():
                if cfg['base_url'] == base_url and self.keys.get(i) == key:
                    cfg['enabled'] = cfg['model'] in wanted
            raw = {'version': 2, 'active_id': self.active_id, 'profiles': [dict(p, id=i) for i, p in profiles.items()]}
            temp = self.path.with_suffix('.tmp')
            temp.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding='utf-8')
            os.replace(temp, self.path)
            self.profiles = profiles
            return self.public()

    def ready_profiles(self):
        """已启用且凭据就绪（本地无需密钥）的配置，按等级与优先级排序。 Enabled, credentialed profiles in routing order."""
        with self.lock:
            return [(i, dict(p)) for i, p in sorted(self.profiles.items(), key=lambda item: (item[1]['level'], item[1]['priority'], item[0]))
                    if p['enabled'] and (p['model_type'] == 'local' or self.keys.get(i))]

    def provider(self, ident=None, expected=None, snapshot=None):
        """绑定配置，恢复时匹配原运行身份。 Bind configuration and match frozen identity on resume."""
        with self.lock:
            if snapshot is not None:
                # 配置来自任务快照；凭据仍必须绑定同一个存活的目的地址。
                # Restore frozen options, but obtain credentials only from the same live destination.
                if (not isinstance(snapshot,dict) or type(snapshot.get('version')) is not int
                    or snapshot['version']!=1 or snapshot.get('mode')!='fixed'
                    or not isinstance(snapshot.get('profile_id'),str) or not isinstance(snapshot.get('config'),dict)):
                    raise MasaError('unsupported model snapshot')
                ident=snapshot.get('profile_id')
                config=validate_config(snapshot.get('config',{}))
                current=self.profiles.get(ident)
                if not current or not current['enabled'] or current['base_url']!=config['base_url'] or current['model_type']!=config['model_type']:
                    raise MasaError('snapshot destination unavailable or disabled; restore the original profile')
                provider=ChatProvider(config,self.keys.get(ident,''))
                if expected is not None and provider.profile!=expected:
                    raise MasaError('snapshot provider identity mismatch')
                provider.snapshot=snapshot
                return provider
            candidates = (
                [ident or self.active_id] if expected is None else list(self.profiles)
            )
            for ident in candidates:
                if ident not in self.profiles or not self.profiles[ident]['enabled'] or (self.profiles[ident]['model_type']=='cloud' and not self.keys.get(ident)):
                    continue
                provider = ChatProvider(self.profiles[ident], self.keys.get(ident,''))
                if expected is None or provider.profile == expected:
                    provider.snapshot={'version':1,'mode':'fixed','profile_id':ident,'config':dict(provider.config)}
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
                "metrics": provider.metrics,
                "cost": None,
                "time": time.time(),
            }
        except MasaError as exc:
            outcome = {
                "ok": False,
                "message": str(exc),
                "usage": provider.usage,
                "metrics": provider.metrics,
                "cost": None,
                "time": time.time(),
            }
        with self.lock:
            if (
                ident in self.profiles
                and self.profiles[ident] == provider.config
                and self.keys.get(ident,'') == provider.key
            ):
                self.tests[ident] = outcome
        return outcome
