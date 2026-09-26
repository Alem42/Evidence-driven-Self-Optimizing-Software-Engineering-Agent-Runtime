"""Small, serializable contracts shared by runtime and adapters."""

from dataclasses import asdict, dataclass
import hashlib
import json
from typing import Any


class MasaError(Exception):
    """An actionable configuration, policy, or integrity error."""


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
