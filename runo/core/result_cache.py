import hashlib
import json
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from runo.models.request import SchemaField
from runo.core.cache import get_store as get_redis


# TTLs in seconds.
_TTL_NEWS = 6 * 3600           # 6 hours
_TTL_PRODUCT = 24 * 3600       # 24 hours — prices / stock move fast
_TTL_REFERENCE = 30 * 86400    # 30 days
_TTL_REFERENCE_LONG = 90 * 86400  # 90 days — stable encyclopedic / canonical docs hosts
_TTL_DEFAULT = 24 * 3600       # 24 hours

_NEWS_PATH_MARKERS = ("/news/", "/blog/", "/article/", "/articles/", "/posts/", "/post/")
_PRODUCT_PATH_MARKERS = ("/product/", "/products/", "/dp/", "/item/", "/p/")
_REFERENCE_PATH_MARKERS = ("/docs/", "/doc/", "/reference/", "/wiki/", "/manual/")
_REFERENCE_HOST_SUFFIXES = ("wikipedia.org", ".wiki", "readthedocs.io", "docs.python.org")
# Encyclopedic / canonical reference hosts where content turns over far slower
# than the generic /docs/ marker — safe to extend TTL to 90 days.
_REFERENCE_HOST_SUFFIXES_LONG = ("wikipedia.org", "readthedocs.io", "docs.python.org")

_NEWS_JSONLD_TYPES = {"NewsArticle", "BlogPosting", "Article", "Report"}
_PRODUCT_JSONLD_TYPES = {"Product", "Offer", "AggregateOffer"}

_VOLATILE_FIELD_RE = re.compile(
    r"price|stock|availability|inventory|in_stock|quantity",
    re.IGNORECASE,
)


def _has_volatile_field(fields: list[SchemaField]) -> bool:
    return any(_VOLATILE_FIELD_RE.search(f.field) for f in fields)


def _jsonld_type(structured: dict | None) -> str | None:
    if not structured:
        return None
    t = structured.get("@type") or structured.get("type")
    if isinstance(t, list) and t:
        t = t[0]
    return t if isinstance(t, str) else None


def _classify_ttl(
    url: str,
    fields: list[SchemaField],
    structured: dict | None = None,
) -> int:
    """Pick TTL based on content class. Price-like schema fields always cap at 24h."""
    volatile = _has_volatile_field(fields)
    p = urlsplit(url)
    host = p.netloc.lower()
    path = p.path.lower()

    jtype = _jsonld_type(structured)

    # Product detection — volatile fields force default (24h) regardless.
    if jtype in _PRODUCT_JSONLD_TYPES or any(m in path for m in _PRODUCT_PATH_MARKERS):
        return _TTL_PRODUCT  # already 24h

    # News/blog → 6h.
    if jtype in _NEWS_JSONLD_TYPES or any(m in path for m in _NEWS_PATH_MARKERS):
        return _TTL_NEWS

    # Reference — long TTL, but never if the schema asks for volatile fields.
    # Encyclopedic hosts (Wikipedia, ReadTheDocs, docs.python.org) get the
    # 90-day tier; generic /docs/ paths keep the 30-day default since vendor
    # changelogs can churn weekly.
    if not volatile:
        if any(host.endswith(suffix) for suffix in _REFERENCE_HOST_SUFFIXES_LONG):
            return _TTL_REFERENCE_LONG
        is_reference = (
            any(host.endswith(suffix) for suffix in _REFERENCE_HOST_SUFFIXES)
            or any(m in path for m in _REFERENCE_PATH_MARKERS)
        )
        if is_reference:
            return _TTL_REFERENCE

    return _TTL_DEFAULT


_TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "gclid", "fbclid", "mc_cid", "mc_eid", "_hsenc", "_hsmi", "yclid",
}


_DEFAULT_PORTS = {"http": "80", "https": "443"}


def _normalize_url(url: str) -> str:
    p = urlsplit(url)
    q = [
        (k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
        if k.lower() not in _TRACKING_PARAMS
    ]
    q.sort()
    scheme = p.scheme.lower()
    host = (p.hostname or "").lower()
    # Strip "www." so apex and www. variants share a cache entry.
    if host.startswith("www."):
        host = host[4:]
    # Drop the default port for the scheme; keep explicit non-default ports.
    port = p.port
    if port is not None and str(port) != _DEFAULT_PORTS.get(scheme, ""):
        netloc = f"{host}:{port}"
    else:
        netloc = host
    # Collapse trailing slash on non-root paths; "/foo/" and "/foo" hit
    # the same content on virtually every modern stack.
    path = p.path or "/"
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/") or "/"
    return urlunsplit((scheme, netloc, path, urlencode(q), ""))


def _schema_fingerprint(fields: list[SchemaField]) -> str:
    # Fingerprint is field-name + type only. example / hint are present in
    # the prompt to guide the LLM but they do NOT change which fields the
    # output dict carries, and coercion is deterministic per type. Excluding
    # them means two callers with identical schemas but cosmetic differences
    # (different example values) share the result cache.
    payload = [{"f": f.field, "t": f.type} for f in fields]
    payload.sort(key=lambda d: d["f"])
    blob = json.dumps(payload, sort_keys=True, default=str).encode()
    return hashlib.sha256(blob).hexdigest()[:16]


def cache_key(url: str, fields: list[SchemaField], owner_id: int = 0) -> str:
    # Cache is globally shared across tenants. The cached payload is purely
    # deterministic on (normalized URL, schema fields, types) — no user data,
    # no auth state, just the public-page extraction. Removing the owner_id
    # partition is the single biggest hit-rate multiplier: Wikipedia / docs /
    # GitHub READMEs extracted by user A now serve user B in O(ms) instead
    # of paying another LLM call. ``owner_id`` is kept in the signature for
    # backward compatibility with callers; it is ignored here.
    del owner_id  # intentionally unused — see docstring
    url_hash = hashlib.sha256(_normalize_url(url).encode()).hexdigest()[:24]
    return f"runo:ext:{_schema_fingerprint(fields)}:{url_hash}"


async def get_cached(
    url: str, fields: list[SchemaField], owner_id: int = 0,
) -> dict | None:
    try:
        r = await get_redis()
        if r is None:
            return None
        raw = await r.get(cache_key(url, fields, owner_id))
        return json.loads(raw) if raw else None
    except Exception:
        return None


async def set_cached(
    url: str,
    fields: list[SchemaField],
    payload: dict,
    ttl_seconds: int | None = None,
    structured: dict | None = None,
    owner_id: int = 0,
) -> None:
    data = payload.get("data") or {}
    any_null = False
    if data:
        null_count = sum(1 for v in data.values() if v is None)
        null_ratio = null_count / len(data)
        any_null = null_count > 0
        # Small-schema hygiene: never cache degraded results on <=4-field
        # schemas, since one null poisons future hits. Larger schemas
        # tolerate up to 40% null (paired with halved TTL below) so common
        # cases like a 5-field schema with one absent attribute still cache.
        # Field-level cache (api/core/field_cache.py) handles the per-field
        # case independently; null fields never write there.
        if len(data) <= 4 and null_count > 0:
            return
        if null_ratio > 0.40:
            return
    ttl = ttl_seconds if ttl_seconds is not None else _classify_ttl(url, fields, structured)
    # Halve TTL (capped at 1h) for results with any null so upstream fixes
    # (warm prompt cache, improved fallback) take effect on the next request
    # rather than waiting out the full content-type TTL.
    if any_null:
        ttl = min(ttl // 2, 3600)
    try:
        r = await get_redis()
        if r is None:
            return
        await r.set(
            cache_key(url, fields, owner_id),
            json.dumps(payload, default=str),
            ex=ttl,
        )
    except Exception:
        pass
