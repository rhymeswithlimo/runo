"""Negative cache for hard-blocked hosts.

After a stealth-headless pass still fails with a block signal, we cache the
*host* (not URL) for a short TTL. The next caller that targets the same host
fails fast, saving us a second doomed fetch + headless escalation. Backed by
the shared in-memory store in ``runo.core.cache``.
"""
from urllib.parse import urlsplit

from runo.core.cache import get_store as get_redis


def _host_key(url: str) -> str:
    try:
        host = urlsplit(url).netloc.lower()
    except Exception:
        host = ""
    return f"runo:neg:{host}" if host else ""


async def is_host_blocked(url: str) -> bool:
    key = _host_key(url)
    if not key:
        return False
    try:
        r = await get_redis()
        if r is None:
            return False
        return bool(await r.exists(key))
    except Exception:
        return False


async def mark_host_blocked(url: str, ttl_s: int) -> None:
    key = _host_key(url)
    if not key or ttl_s <= 0:
        return
    try:
        r = await get_redis()
        if r is None:
            return
        await r.set(key, b"1", ex=ttl_s)
    except Exception:
        pass


async def clear_host_block(url: str) -> None:
    key = _host_key(url)
    if not key:
        return
    try:
        r = await get_redis()
        if r is None:
            return
        await r.delete(key)
    except Exception:
        pass
