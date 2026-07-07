"""In-memory TTL store backing Runo's caches.

The hosted build kept its caches in Redis. The local build has no external
services, so this module provides a tiny, async, TTL-aware, process-local store
that implements the exact command surface the cache modules use — ``get``,
``set(ex=)``, ``mget``, ``exists``, ``delete``, and a minimal ``pipeline`` — and
nothing more. It is deliberately not a general Redis emulator.

Everything lives in one process and resets on restart. For a local tool that is
the right tradeoff: caching still speeds up repeat extractions within a run (and
across a long-lived ``runo serve``) with zero infrastructure to manage.
"""
from __future__ import annotations

import time

# Soft ceiling on live keys. Expired entries are reclaimed lazily on access;
# this bound stops a long-running server from growing without limit when many
# distinct keys are written and never read again.
_MAX_KEYS = 50_000


class _InMemoryStore:
    def __init__(self) -> None:
        # key -> (value, expires_at_monotonic | None)
        self._data: dict[str, tuple[object, float | None]] = {}

    def _live(self, key: str):
        item = self._data.get(key)
        if item is None:
            return None
        value, expires = item
        if expires is not None and expires <= time.monotonic():
            self._data.pop(key, None)
            return None
        return value

    def _maybe_purge(self) -> None:
        if len(self._data) <= _MAX_KEYS:
            return
        now = time.monotonic()
        # Drop everything already expired.
        for k in [k for k, (_, e) in self._data.items() if e is not None and e <= now]:
            self._data.pop(k, None)
        # Still over budget: evict oldest-inserted keys (dict preserves order).
        while len(self._data) > _MAX_KEYS:
            self._data.pop(next(iter(self._data)), None)

    async def get(self, key: str):
        return self._live(key)

    async def mget(self, *keys: str):
        return [self._live(k) for k in keys]

    async def set(self, key: str, value, ex: int | None = None):
        expires = time.monotonic() + ex if ex else None
        self._data[key] = (value, expires)
        self._maybe_purge()

    async def delete(self, *keys: str):
        for k in keys:
            self._data.pop(k, None)

    async def exists(self, *keys: str):
        return sum(1 for k in keys if self._live(k) is not None)

    def pipeline(self) -> "_Pipeline":
        return _Pipeline(self)


class _Pipeline:
    """Minimal buffered pipeline: queue ``set`` calls, flush on ``execute``."""

    def __init__(self, store: _InMemoryStore) -> None:
        self._store = store
        self._ops: list[tuple[str, object, int | None]] = []

    def set(self, key: str, value, ex: int | None = None) -> "_Pipeline":
        self._ops.append((key, value, ex))
        return self

    async def execute(self) -> list[bool]:
        results = []
        for key, value, ex in self._ops:
            await self._store.set(key, value, ex=ex)
            results.append(True)
        self._ops.clear()
        return results


_store = _InMemoryStore()


async def get_store() -> _InMemoryStore:
    """Return the process-local store. Async to match the old ``get_redis``
    call shape (``r = await get_redis()``), so cache modules only swap the
    import and keep their existing ``if r is None`` guards (never true here)."""
    return _store
