"""A persistent background event loop for the synchronous API.

Runo's engine keeps loop-bound singletons (the httpx client, the Playwright
browser, the Gemini async client). If each synchronous call spun up its own
``asyncio.run`` loop, those singletons would be left bound to a closed loop and
the second call would fail. Instead we run one long-lived loop on a daemon
thread and marshal coroutines onto it. This also means the sync helpers work
even when called from inside an existing event loop.
"""
from __future__ import annotations

import asyncio
import threading
from typing import Any, Coroutine, TypeVar

_T = TypeVar("_T")

_loop: asyncio.AbstractEventLoop | None = None
_lock = threading.Lock()


def _ensure_loop() -> asyncio.AbstractEventLoop:
    global _loop
    if _loop is not None:
        return _loop
    with _lock:
        if _loop is None:
            loop = asyncio.new_event_loop()
            threading.Thread(
                target=loop.run_forever, name="runo-loop", daemon=True,
            ).start()
            _loop = loop
    return _loop


def run_sync(coro: Coroutine[Any, Any, _T]) -> _T:
    """Run *coro* on the persistent background loop and block for its result."""
    return asyncio.run_coroutine_threadsafe(coro, _ensure_loop()).result()
