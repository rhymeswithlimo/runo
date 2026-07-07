"""T3 — per-host cookie persistence.

Many bot walls (Cloudflare, Datadome, Incapsula, Akamai) don't block on the
first request — they issue a challenge cookie (`cf_clearance`, `datadome`,
`_abck`, `incap_ses_*`, `__cf_bm`) after the client "proves" it's a browser
(solves JS challenge, waits out a timer, etc.). Once that cookie is set,
every subsequent request with the cookie breezes through.

This module stores those cookies per hostname in Redis so consecutive Runo
calls to the same origin *share* the trust the first call built up.

Cost: zero. Reuses the existing Redis connection.
"""

from __future__ import annotations

import json
from urllib.parse import urlsplit

from runo.config import settings
from runo.core.cache import get_store as get_redis


# Cookies we care about (case-insensitive prefix match). Everything else is
# noise — storing per-request session IDs would waste Redis and risk
# cross-contaminating unrelated sessions.
_TRUST_COOKIE_PREFIXES = (
    "cf_clearance",
    "__cf_bm",
    "datadome",
    "_abck",        # Akamai Bot Manager
    "bm_sv",        # Akamai
    "ak_bmsc",      # Akamai
    "incap_ses",    # Incapsula
    "visid_incap",  # Incapsula
    "_px",          # PerimeterX
)

# Default TTL if the cookie didn't ship a Max-Age. 30 minutes matches real
# `__cf_bm` behavior; `cf_clearance` usually lives ~30 min to a few hours.
_DEFAULT_TTL_S = 30 * 60


def _host(url: str) -> str:
    try:
        return urlsplit(url).netloc.lower()
    except Exception:
        return ""


def _is_trust_cookie(name: str) -> bool:
    low = name.lower()
    return any(low.startswith(p) for p in _TRUST_COOKIE_PREFIXES)


async def get_cookies_for(url: str) -> dict[str, str]:
    """Return saved trust cookies for this URL's host. Empty dict on miss or
    when session warming is disabled / Redis is down."""
    if not settings.session_warming_enabled:
        return {}
    host = _host(url)
    if not host:
        return {}
    r = await get_redis()
    if r is None:
        return {}
    try:
        raw = await r.get(f"runo:session:{host}")
    except Exception:
        return {}
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


async def store_cookies_from(url: str, cookies: dict[str, str]) -> None:
    """Merge-store the trust cookies from a response. Non-trust cookies are
    filtered out to keep the stored blob small."""
    if not settings.session_warming_enabled:
        return
    host = _host(url)
    if not host or not cookies:
        return
    trust = {k: v for k, v in cookies.items() if _is_trust_cookie(k)}
    if not trust:
        return
    r = await get_redis()
    if r is None:
        return
    key = f"runo:session:{host}"
    try:
        existing_raw = await r.get(key)
        existing = json.loads(existing_raw) if existing_raw else {}
        if not isinstance(existing, dict):
            existing = {}
        existing.update(trust)
        await r.set(key, json.dumps(existing), ex=_DEFAULT_TTL_S)
    except Exception:
        # Redis issues are non-fatal for the request path; graceful-degrade
        # the same way rate-limiting does.
        pass
