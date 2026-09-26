"""Provider metadata is persistent; credentials stay in this process only."""

import json
import os
from pathlib import Path
import threading
from urllib.parse import urlsplit

from masa.domain import MasaError


class Settings:
    def __init__(self, root: Path):
        self.path = root / "provider.json"
        self.lock = threading.Lock()
        self.key = ""
        self.metadata = {"name": "我的 API", "base_url": "", "model": ""}
        if self.path.exists():
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                if not isinstance(raw, dict):
                    raise ValueError("invalid metadata")
                self.metadata.update({k: str(raw[k]) for k in self.metadata if k in raw})
            except (ValueError, OSError):
                pass

    def public(self):
        with self.lock:
            return {**self.metadata, "key_configured": bool(self.key), "key_storage": "session_memory",
                    "execution_connected": False}

    def save(self, body):
        values = {}
        for name in ("name", "base_url", "model"):
            value = body.get(name, "")
            if not isinstance(value, str) or len(value) > 2048:
                raise MasaError("invalid provider metadata")
            values[name] = value.strip()
        url = urlsplit(values["base_url"])
        if values["base_url"] and (not url.hostname or url.username or url.password or url.query or url.fragment
                or not (url.scheme == "https" or (url.scheme == "http" and url.hostname in {"localhost", "127.0.0.1", "::1"}))):
            raise MasaError("Base URL must use HTTPS (or local HTTP), without credentials/query/fragment")
        key = body.get("api_key", "")
        if not isinstance(key, str) or len(key) > 8192 or "\n" in key or "\r" in key:
            raise MasaError("invalid API key")
        with self.lock:
            temp = self.path.with_suffix(".tmp")
            temp.write_text(json.dumps(values, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(temp, self.path)
            self.metadata = values
            if body.get("clear_key") is True:
                self.key = ""
            elif key.strip():
                self.key = key.strip()
        return self.public()
