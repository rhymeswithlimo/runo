"""Per-field result cache.

Stores each extracted field value independently so requests with overlapping
schemas on the same URL share work. Sits *below* the whole-result cache in
``result_cache.py``: a whole-result hit short-circuits before any field
lookup. On whole-result miss, ``get_cached_fields`` issues one Redis ``mget``
to seed the existing ``prefilled`` dict that flows into structured parsing,
the partial-skip threshold, and (last resort) the LLM prompt.

Coherence note: assembling fields from different past extractions loses the
joint consistency the LLM provides when extracting all fields in one shot.
For most factual fields this is fine. Volatile fields (price/stock/etc.) are
excluded by name. Callers needing strict joint consistency can pass
``use_cache=False``.
"""

import hashlib
import json
import time
from typing import Any

from runo.config import settings
from runo.core.result_cache import (
    _VOLATILE_FIELD_RE,
    _classify_ttl,
    _normalize_url,
)
from runo.models.request import SchemaField
from runo.core.cache import get_store as get_redis

_MAX_FIELD_VALUE_BYTES = 16 * 1024  # 16 KB cap per cached field value


def _type_token(field_type: str) -> str:
    """Stable, key-safe token for the field type.

    ``array<string>`` → ``array_string``; other unsafe chars collapsed.
    """
    return (
        field_type.strip().lower()
        .replace("<", "_").replace(">", "")
        .replace(" ", "")
    )


def _field_cache_key(url: str, field: SchemaField) -> str:
    url_hash = hashlib.sha256(_normalize_url(url).encode()).hexdigest()[:24]
    field_hash = hashlib.sha256(
        field.field.strip().lower().encode()
    ).hexdigest()[:16]
    return f"runo:ext_field:{url_hash}:{field_hash}:{_type_token(field.type)}"


def _is_volatile(field: SchemaField) -> bool:
    return bool(_VOLATILE_FIELD_RE.search(field.field))


def _is_field_cacheable(field: SchemaField, value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str) and not value.strip():
        return False
    if isinstance(value, (list, tuple, dict)) and len(value) == 0:
        return False
    if settings.field_cache_skip_volatile and _is_volatile(field):
        return False
    return True


def _field_ttl(
    url: str,
    field: SchemaField,
    structured: dict | None,
) -> int:
    if _is_volatile(field):
        # Only reached when skip_volatile is False (otherwise we never write).
        return min(
            _classify_ttl(url, [field], structured),
            settings.field_cache_volatile_ttl_s,
        )
    return _classify_ttl(url, [field], structured)


async def get_cached_fields(
    url: str,
    fields: list[SchemaField],
) -> dict[str, Any]:
    """One mget round-trip. Returns {field_name: value} for hits only.

    Volatile fields (per ``field_cache_skip_volatile``) are not queried.
    Silent-degrade on Redis errors.
    """
    if not fields:
        return {}
    queryable: list[SchemaField] = [
        f for f in fields
        if not (settings.field_cache_skip_volatile and _is_volatile(f))
    ]
    if not queryable:
        return {}
    try:
        r = await get_redis()
        if r is None:
            return {}
        keys = [_field_cache_key(url, f) for f in queryable]
        raw_values = await r.mget(*keys)
    except Exception:
        return {}

    out: dict[str, Any] = {}
    for f, raw in zip(queryable, raw_values):
        if raw is None:
            continue
        try:
            wrapped = json.loads(raw)
            v = wrapped.get("v") if isinstance(wrapped, dict) else None
        except Exception:
            continue
        if v is None:
            continue
        out[f.field] = v
    return out


async def set_cached_fields(
    url: str,
    fields: list[SchemaField],
    data: dict[str, Any],
    structured: dict | None = None,
    source: str = "llm",
    halve_ttl: bool = False,
) -> None:
    """Pipelined per-field SETEX. Skips nulls, volatiles, empty values,
    type-mismatched entries, and over-cap blobs. Silent-degrade on Redis.
    """
    if not data or not fields:
        return
    by_name = {f.field: f for f in fields}
    payloads: list[tuple[str, str, int]] = []
    now = int(time.time())
    for name, value in data.items():
        f = by_name.get(name)
        if f is None:
            continue
        if not _is_field_cacheable(f, value):
            continue
        try:
            blob = json.dumps(
                {"v": value, "src": source, "t": now}, default=str
            )
        except (TypeError, ValueError):
            continue
        if len(blob.encode("utf-8")) > _MAX_FIELD_VALUE_BYTES:
            continue
        ttl = _field_ttl(url, f, structured)
        if halve_ttl:
            ttl = min(ttl // 2, 3600)
        if ttl <= 0:
            continue
        payloads.append((_field_cache_key(url, f), blob, ttl))

    if not payloads:
        return

    try:
        r = await get_redis()
        if r is None:
            return
        pipe = r.pipeline()
        for key, blob, ttl in payloads:
            pipe.set(key, blob, ex=ttl)
        await pipe.execute()
    except Exception:
        return
