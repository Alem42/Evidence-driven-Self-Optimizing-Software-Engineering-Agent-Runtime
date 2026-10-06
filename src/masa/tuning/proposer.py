"""提议者：给出“一个补丁”（至多 3 条操作）。LLM 提议者用最强模型；随机提议者不花 token，用作对照与离线测试。
Proposers: each yields ONE patch (at most three operations). The LLM proposer uses the strongest model; the random one costs no tokens and serves as a control and for offline tests.

提议者的输出一律是不可信输入：它只会被 space.apply() 校验后使用，不合法的整个丢弃。
A proposer's output is untrusted input: it is used only after space.apply() validates it, and an illegal patch is discarded whole.
"""
import json
import random

from masa.tuning import space

# 随机提议者能碰的“候选操作”。 The operations the random proposer samples from.
_CHOICES = {
    'policy.max_escalations': [0, 1, 2, 3], 'policy.stuck_after': [3, 4, 6, 8], 'policy.diagnose_max': [0, 1, 2, 4], 'policy.planner_retries': [1, 2, 3],
    'policy.attempts_per_level.fix': [1, 2, 3], 'policy.attempts_per_level.generation': [1, 2, 3], 'policy.diagnose': [True, False],
    'policy.conductor': [True, False], 'policy.conductor_max_calls': [2, 4, 6, 8], 'policy.conductor_cascade': [True, False],
}


class RandomProposer:
    """从空间里随机取 1–2 条合法操作。种子固定则结果可复现。 Samples one or two legal operations; a fixed seed makes it reproducible."""

    def __init__(self, seed=0):
        self.rng = random.Random(seed)

    def propose(self, incumbent, history):
        for _ in range(50):
            ops = []
            for _ in range(self.rng.choice([1, 1, 2])):
                kind = self.rng.choice(['scalar', 'scalar', 'top', 'start', 'edge'])
                if kind == 'scalar':
                    path = self.rng.choice(sorted(_CHOICES))
                    ops.append({'op': 'set', 'path': path, 'value': self.rng.choice(_CHOICES[path])})
                elif kind == 'top':
                    ops.append({'op': 'set', 'path': 'policy.prefer_highest.' + self.rng.choice(space.TOP_ROLES), 'value': self.rng.choice([True, False])})
                elif kind == 'start':
                    ops.append({'op': 'set', 'path': 'policy.start_level.' + self.rng.choice(space.START_ROLES), 'value': self.rng.choice([1, 2, 3])})
                else:
                    edges = sorted({(e['from'], e['to'], e['when']) for e in space.FIX_V1['edges'] if e.get('when') in space.DROPPABLE})
                    source, target, when = self.rng.choice(edges)
                    ops.append({'op': self.rng.choice(['drop_edge', 'keep_edge']), 'from': source, 'to': target, 'when': when})
            try:
                space.apply(incumbent, ops)
            except space.PatchError:
                continue
            return ops
        return None


class LLMProposer:
    """ask(context) -> {'patch': [...], 'rationale': '...'}（经 workflow_tuner 角色的 schema 校验）。ask 失败返回 None，不抛异常。
    ask(context) returns {'patch': [...], 'rationale': ...} (validated by the workflow_tuner role schema). A failing ask yields None instead of raising."""

    def __init__(self, ask):
        self.ask = ask
        self.last_rationale = None

    def propose(self, incumbent, history):
        from masa.tuning.score import summarize
        context = {
            'purpose': 'workflow_tuner',
            'space': space.catalog(),
            'current_changes': space.describe(incumbent),
            'current_result': history[-1]['summary'] if history else None,
            # 已经试过的，附上分数：不要重复，也不要在失败的方向上继续加码。 What was already tried, with scores: do not repeat, do not double down on a failing direction.
            'tried': [{'changes': h['changes'], 'score': h['score']} for h in history[-8:]],
            'goal': 'raise the score = weighted pass rate - cloud tokens per pass - false passes; propose ONE small patch (at most 3 operations).',
        }
        try:
            answer = self.ask(context)
        except Exception:  # 提议者崩溃 = 这一轮没有提议，搜索照常继续 / a crashed proposer means no proposal this round; the search goes on
            return None
        if not isinstance(answer, dict) or not isinstance(answer.get('patch'), list):
            return None
        self.last_rationale = str(answer.get('rationale', ''))[:300]
        return answer['patch']


def provider_ask(provider):
    """把一个模型提供方包成 ask：失败原样抛出（由 LLMProposer 吞掉）。 Wrap a model provider as `ask`; failures propagate and LLMProposer swallows them."""
    def ask(context):
        return provider.respond(json.loads(json.dumps(context)))
    return ask
