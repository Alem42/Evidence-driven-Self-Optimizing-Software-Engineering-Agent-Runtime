"""Small, serializable contracts shared by runtime and adapters."""

from dataclasses import asdict, dataclass
import hashlib
import json
from typing import Any


class MasaError(Exception):
    """An actionable configuration, policy, or integrity error."""


class ContextOverflow(MasaError):
    """输入几乎一定放不进模型的上下文窗口；在发出请求之前拒绝（不计费、不被服务端静默截断）。
    The input will almost certainly not fit the model's context window; rejected BEFORE sending (no cost, no silent truncation)."""

    def __init__(self, estimated_tokens, limit, model=''):
        self.estimated_tokens, self.limit, self.model = estimated_tokens, limit, model
        super().__init__(f'context overflow: input needs at least ~{estimated_tokens} tokens but {model or "the model"} has a {limit}-token window; '
                         'rejected before sending because local servers silently truncate instead of failing')


class TransportFailure(MasaError):
    """没有收到任何响应（连接被拒绝、被重置、服务被杀）。对免费的本地模型，这种失败可以在服务恢复后安全重试；
    对付费云调用，结果仍视为未知。 No response was received (refused, reset, server killed). Safe to retry for FREE local models
    once the server is back; for paid cloud calls the outcome stays unknown."""


def stage_error(exc, message):
    """阶段包装器用它构造要抛出的异常：上下文溢出必须保持类型（路由器据此升级），其余沿用带 run 编号的 MasaError。
    Stage wrappers raise this: a context overflow keeps its type so the router can act on it; everything else is the usual MasaError."""
    if isinstance(exc, ContextOverflow):
        return ContextOverflow(exc.estimated_tokens, exc.limit, exc.model)
    if isinstance(exc, TransportFailure):
        return TransportFailure(f'{message} ({exc})')
    return MasaError(message)


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


TERMINAL = frozenset({"succeeded", "failed", "cancelled", "skipped"})
OPERATIONS = frozenset({"go_test", "go_vet", "go_fmt_check"})


@dataclass(frozen=True)
class Node:
    id: str
    type: str
    dependencies: tuple[str, ...] = ()
    trigger: str = "all_succeeded"
    operation: str = "go_test"
    role: str = "verifier"


@dataclass(frozen=True)
class Graph:
    nodes: tuple[Node, ...]
    version: int = 1
    policy_version: str = "p0-verify-v1"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Graph":
        return cls(tuple(Node(**{**n, "dependencies": tuple(n["dependencies"])})
                         for n in data["nodes"]), data["version"], data["policy_version"])


@dataclass(frozen=True)
class Budget:
    model_calls: int = 4
    tool_calls: int = 3
    deadline_seconds: int = 300
    tool_timeout_ms: int = 120000
    max_output_bytes: int = 131072

    def validate(self) -> None:
        for key, value in asdict(self).items():
            if type(value) is not int or value <= 0:
                raise MasaError(f"{key} must be a positive integer")
        if self.tool_timeout_ms > 120000 or self.max_output_bytes > 1048576:
            raise MasaError("tool limits exceed runner policy")
