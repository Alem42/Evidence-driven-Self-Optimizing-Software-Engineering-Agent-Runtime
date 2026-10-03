"""Router：把纯函数 route() 接到存储、提供方和事件上。
Router: glue between the pure route() policy, the store, providers and events.

两种模式 / Two modes:
  fixed   —— 原有行为：整个任务只用一个模型，无预算、无升级。Original behaviour: one model, no budget, no escalation.
  ladder  —— 本地优先，有界升级，任务级预算。Local first, bounded escalation, task-level budget.
快照不含密钥；恢复时用快照里的候选集合，凭据仍取自存活的同一目的地址的配置。
Snapshots contain no secrets; on resume the frozen candidate set is used and credentials come from the live profile.
"""
from masa.application.routing import (Candidate, DEFAULT_BUDGET, DEFAULT_POLICY, FIXED_POLICY, STOP_TEXT, estimate_tokens, route,
                                      spend_from_report, unlimited_budget, validate_budget, validate_policy)
from masa.domain.models import MasaError

SNAPSHOT_VERSION = 1


class RoutingStop(MasaError):
    """路由决定停止（预算/等级/上下文）。不是故障，调用方应保留证据并结束任务。 A deliberate stop, not a fault."""

    def __init__(self, reason, detail=''):
        self.reason, self.detail = reason, detail
        super().__init__(f'routing stopped: {STOP_TEXT.get(reason, reason)}' + (f' ({detail})' if detail else ''))


def _candidate_from(entry) -> Candidate:
    return Candidate(id=entry['id'], level=entry['level'], priority=entry['priority'], model_type=entry['model_type'],
                     model=entry['model'], roles=tuple(entry.get('roles') or ()), context_limit=entry['context_limit'],
                     max_output=entry['max_output_tokens'], price_in=entry.get('price_in'), price_out=entry.get('price_out'),
                     digest=entry.get('digest'))


def build_snapshot(settings, *, mode='ladder', budget=None, policy=None, digests=None, profile_ids=None):
    """从当前可用配置冻结候选集合（不含密钥）。 Freeze the candidate set from live, ready profiles (no secrets)."""
    if mode not in {'ladder', 'fixed'}:
        raise MasaError('unknown routing mode')
    # 默认值来自设置页保存的内容；本任务的覆盖项逐层合并（嵌套的每链次数、起始等级也逐项合并）。
    # Defaults come from what the Settings page saved; per-task overrides merge on top, including nested fields.
    saved = settings.routing() if hasattr(settings, 'routing') else {'policy': DEFAULT_POLICY, 'budget': DEFAULT_BUDGET}
    merged_policy = {**saved['policy'], **(policy or {})}
    for nested in ('attempts_per_level', 'start_level_by_role'):
        merged_policy[nested] = {**saved['policy'].get(nested, {}), **((policy or {}).get(nested) or {})}
    merged_budget = {**saved['budget'], **(budget or {})}
    entries = []
    for ident, cfg in settings.ready_profiles():
        if profile_ids is not None and ident not in profile_ids:
            continue
        provider = settings.provider(ident)
        entries.append({'id': ident, 'level': cfg['level'], 'priority': cfg['priority'], 'model_type': cfg['model_type'],
                        'model': cfg['model'], 'roles': list(cfg.get('roles') or []), 'context_limit': cfg['context_limit'],
                        'max_output_tokens': cfg['max_output_tokens'], 'price_in': cfg.get('input_price_per_million'),
                        'price_out': cfg.get('output_price_per_million'),
                        'digest': (digests or {}).get(cfg['model']) if cfg['model_type'] == 'local' else None,
                        'snapshot': provider.snapshot})
    if not entries:
        raise MasaError('no usable model profile; configure and enable at least one model')
    return {'version': SNAPSHOT_VERSION, 'mode': mode, 'policy': validate_policy(merged_policy) if mode == 'ladder' else dict(FIXED_POLICY),
            'budget': validate_budget(merged_budget) if mode == 'ladder' else unlimited_budget(), 'candidates': entries}


class Router:
    def __init__(self, store, snapshot, provider_factory, *, live_digests=None, fixed_provider=None):
        """provider_factory(entry) → provider；live_digests 用于发现本地权重变化。 Factory builds providers lazily."""
        if snapshot.get('version') != SNAPSHOT_VERSION:
            raise MasaError('unsupported routing snapshot')
        self.store, self.snapshot, self.factory, self.fixed_provider = store, snapshot, provider_factory, fixed_provider
        self.mode = snapshot['mode']
        self.policy, self.budget = snapshot['policy'], snapshot['budget']
        self.entries = {e['id']: e for e in snapshot['candidates']}
        self.candidates = [_candidate_from(e) for e in snapshot['candidates']]
        self.prices = {c.model: (c.price_in, c.price_out) for c in self.candidates}
        self._providers = {}
        # 本地权重摘要变了：该候选不再可用，必须由人显式处理（重新开始任务）。
        # A changed local weight digest makes that candidate unavailable until a human decides.
        self.unavailable = frozenset(
            c.id for c in self.candidates
            if live_digests is not None and c.digest and live_digests.get(c.model) not in (None, c.digest))

    @classmethod
    def fixed(cls, store, provider):
        """原有固定模型行为：单候选、无预算、无上下文准入。 Legacy fixed-model behaviour."""
        cfg = getattr(provider, 'config', None) or {}
        entry = {'id': (getattr(provider, 'snapshot', None) or {}).get('profile_id', 'fixed'), 'level': 1, 'priority': 0,
                 'model_type': cfg.get('model_type', 'cloud'), 'model': (provider.profile or {}).get('model', 'unknown'),
                 'roles': [], 'context_limit': 10 ** 9, 'max_output_tokens': 0, 'price_in': None, 'price_out': None,
                 'digest': None, 'snapshot': getattr(provider, 'snapshot', None)}
        snapshot = {'version': SNAPSHOT_VERSION, 'mode': 'fixed', 'policy': dict(FIXED_POLICY), 'budget': unlimited_budget(), 'candidates': [entry]}
        return cls(store, snapshot, lambda _entry: provider, fixed_provider=provider)

    def provider(self, candidate_id):
        if candidate_id not in self._providers:
            self._providers[candidate_id] = self.factory(self.entries[candidate_id])
        return self._providers[candidate_id]

    def provider_matching(self, profile):
        """找到身份（profile 字典）与保存记录一致的提供方，用于恢复未完成的调用。 Find the provider whose identity matches a saved call."""
        for c in self.candidates:
            provider = self.provider(c.id)
            if provider.profile == profile:
                return provider
        raise MasaError('the model that made this saved call is no longer available; restore its profile')

    def entry_of(self, provider):
        """provider 对应的候选条目（用于记录谁做了某次修复）。 The candidate entry of a provider instance."""
        for cid, cached in self._providers.items():
            if cached is provider:
                return self.entries[cid]
        return None

    def spend(self, anchor_run):
        """已花费 + 保守预留；固定模式不跟踪。 Settled spend plus reservations; not tracked in fixed mode."""
        if self.mode == 'fixed' or not anchor_run:
            return {'calls': 0, 'cloud_tokens': 0, 'active_seconds': 0, 'cost': 0.0, 'cost_known': True, 'reserved_calls': 0}
        from masa.application.usage import task_report
        return spend_from_report(task_report(self.store, anchor_run), self.prices)

    def decide(self, role, chain, history, *, anchor_run=None, need=None):
        """返回 (Decision, provider|None, spend)。stop 时 provider 为 None。 Provider is None on a stop decision."""
        need_tokens = need if isinstance(need, int) else (estimate_tokens(need) if need is not None else 0)
        spend = self.spend(anchor_run)
        decision = route(role, chain, self.candidates, history, spend, self.budget, self.policy, need_tokens, self.unavailable)
        provider = self.provider(decision.candidate) if decision.action == 'use' else None
        return decision, provider, spend

    def describe(self):
        """写入 task_budget 事件的内容（无密钥）。 Content of the task_budget event (no secrets)."""
        return {'mode': self.mode, 'budget': self.budget, 'policy': self.policy,
                'candidates': [{'id': c.id, 'level': c.level, 'model': c.model, 'model_type': c.model_type, 'digest': c.digest,
                                'context_limit': c.context_limit} for c in self.candidates],
                'prices': {c.model: [c.price_in, c.price_out] for c in self.candidates if c.price_in is not None}}

    def event_for(self, decision, role, chain, stage, spend):
        """写入 route_decided 事件的内容。 Content of the route_decided event."""
        entry = self.entries.get(decision.candidate) if decision.candidate else None
        return {'action': decision.action, 'role': role, 'chain': chain, 'stage': stage, 'reason': decision.reason, 'detail': decision.detail,
                'escalated': decision.escalated, 'candidate': decision.candidate, 'level': decision.level,
                'model': entry['model'] if entry else None, 'model_type': entry['model_type'] if entry else None,
                'spend': {k: spend[k] for k in ('calls', 'cloud_tokens', 'active_seconds', 'cost')}}
