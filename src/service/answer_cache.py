import hashlib
import json
from typing import Any


class AnswerCache:
    """메모리에만 두는 단순 캐시."""

    def __init__(
        self, versions: dict[str, Any], options: dict[str, Any], scope: str = "public"
    ) -> None:
        self.versions = dict(versions)
        self.options = dict(options)
        self.scope = scope
        self._store: dict[str, dict[str, Any]] = {}
        self.stats = {"miss": 0, "hit": 0, "put": 0}

    def key(self, query_normalized: str, mode: str) -> str:
        payload = {
            "q": " ".join(query_normalized.split()),
            "mode": mode,
            "scope": self.scope,
            "versions": self.versions,
            "options": self.options,
        }
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def get(self, key: str) -> dict[str, Any] | None:
        found = self._store.get(key)
        self.stats["hit" if found is not None else "miss"] += 1
        return dict(found) if found is not None else None

    def put(self, key: str, value: dict[str, Any]) -> None:
        self._store[key] = dict(value)
        self.stats["put"] += 1
