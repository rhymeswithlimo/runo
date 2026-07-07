"""Redis cache for the output of ``fetcher.clean_html``.

Trafilatura + domain-specific strippers are CPU-bound (trafilatura runs in
~100–250 ms on typical HTML; on Wikipedia-sized pages it can hit 400+ ms).
When two requests hit the same URL with different schemas in close succession,
the cleaned-text computation re-runs even though the result cache misses
(different schema fingerprint).

Keying on a hash of the raw HTML bytes makes the cache content-addressable:
if the page changed since the previous request, the hash changes and we
skip the stale cleaned output automatically — no TTL gymnastics, no risk
of serving the wrong content.

Zero cost on the cache-miss path beyond the hash + a single Redis GET.
"""
from __future__ import annotations

import hashlib
import logging

from runo.core.cache import get_store as get_redis

logger = logging.getLogger("runo")

# Short TTL: this layer is a cross-request deduplication buffer, not a
# durable cache. The result cache covers durability for repeat
# extractions. Anything past ~30 min is unlikely to land a hit anyway
# (cache wash by other URLs in a busy cluster).
_TTL_SECONDS = 1800

# Skip writes for absurdly large cleaned text — Redis network overhead
# starts to dominate the savings past a few hundred KB.
_MAX_VALUE_BYTES = 250_000


def hash_html(html: str) -> str:
    """Content-addressable fingerprint for the raw HTML.

    ``blake2b(digest_size=16)`` is ~3× faster than sha256 and the hash
    space (128 bits) is far beyond what we need for collision safety
    inside a 30-minute TTL window.
    """
    if not html:
        return ""
    return hashlib.blake2b(html.encode("utf-8", errors="ignore"),
                           digest_size=16).hexdigest()


def _redis_key(html_hash: str) -> str:
    return f"runo:clean:{html_hash}"


async def get_cleaned(html_hash: str) -> str | None:
    if not html_hash:
        return None
    try:
        r = await get_redis()
        if r is None:
            return None
        return await r.get(_redis_key(html_hash))
    except Exception:
        return None


async def set_cleaned(html_hash: str, text: str) -> None:
    if not html_hash or not text:
        return
    if len(text.encode("utf-8", errors="ignore")) > _MAX_VALUE_BYTES:
        return
    try:
        r = await get_redis()
        if r is None:
            return
        await r.set(_redis_key(html_hash), text, ex=_TTL_SECONDS)
    except Exception:
        pass
