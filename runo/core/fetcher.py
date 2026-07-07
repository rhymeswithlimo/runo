import asyncio
import concurrent.futures
import random
import re
import threading
import time
from dataclasses import dataclass
from typing import Callable, Literal
from urllib.parse import urlsplit

import httpx
import trafilatura
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright as _pw_sync, Browser

from runo.config import settings
from runo.core.archive import fetch_from_archive
from runo.core.fingerprint import build_bundle as _build_fp_bundle, init_script as _fp_init_script
from runo.core.negative_cache import (
    is_host_blocked,
    mark_host_blocked,
)
from runo.core.session_cache import get_cookies_for, store_cookies_from
from runo.core.url_safety import is_ip_forbidden, validate_outbound_url
from runo.exceptions import (
    FetchBlockedError, FetchTimeoutError, URLUnreachableError,
)
from runo.models.request import SchemaField


# patchright is a drop-in Playwright fork with CDP-leak fixes. Optional dep.
try:
    from patchright.sync_api import sync_playwright as _patchright_sync  # type: ignore
    _PATCHRIGHT_AVAILABLE = True
except Exception:
    _patchright_sync = None  # type: ignore
    _PATCHRIGHT_AVAILABLE = False


def sync_playwright():  # back-compat shim for any test that imports this name
    return _pw_sync()


def _resolve_sync_playwright():
    """Return a (sync_playwright_factory, engine_label) based on HEADLESS_ENGINE.

    Falls back silently: patchright → playwright if the lib is missing;
    camoufox → playwright (Firefox path) if camoufox isn't installed (camoufox
    reuses the Playwright Firefox binary so the same factory works)."""
    engine = (settings.headless_engine or "patchright").strip().lower()
    if engine == "patchright" and _PATCHRIGHT_AVAILABLE:
        return _patchright_sync, "patchright"
    if engine == "camoufox":
        return _pw_sync, "camoufox"
    return _pw_sync, "playwright"

_thread_local = threading.local()
_browser_semaphore: asyncio.Semaphore | None = None

# Each worker thread owns its own playwright instance via _thread_local.browser.
# Thread-local storage preserves greenlet affinity without serialising calls —
# concurrent headless requests land on different threads, each with their own
# sync_playwright context and browser process.
_browser_executor: concurrent.futures.ThreadPoolExecutor | None = None


def _get_browser_executor() -> concurrent.futures.ThreadPoolExecutor:
    global _browser_executor
    if _browser_executor is None:
        _browser_executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=settings.headless_concurrency, thread_name_prefix="runo-playwright"
        )
    return _browser_executor


async def prewarm_browser_async() -> None:
    """Launch Chromium on each dedicated browser thread. Safe to call from
    the FastAPI lifespan startup hook — failures are swallowed so a missing
    Chromium install never blocks the rest of the server from coming up."""
    loop = asyncio.get_running_loop()
    executor = _get_browser_executor()
    tasks = [
        loop.run_in_executor(executor, _get_browser_sync)
        for _ in range(settings.headless_concurrency)
    ]
    await asyncio.gather(*tasks, return_exceptions=True)


# h2 is an optional dep; if it's not installed, httpx raises *on construction*
# rather than advertising capability. Probe once at import.
try:
    import h2 as _h2  # noqa: F401
    _HTTP2_AVAILABLE = True
except Exception:
    _HTTP2_AVAILABLE = False


# Persistent httpx client — reused across all plain-fetch requests so TCP
# connections and TLS sessions are pooled instead of created per-request.
# Initialized lazily on first use; cookies and timeouts are passed per-request.
_httpx_client: httpx.AsyncClient | None = None


def _cap_html(html: str) -> str:
    """Truncate raw HTML to ``settings.max_html_bytes``. DoS guard against
    pages that try to exhaust parser memory; downstream BM25 already trims
    further, so truncation here has no effect on extraction quality for
    legitimately-sized pages."""
    cap = getattr(settings, "max_html_bytes", 0)
    if cap and len(html) > cap:
        return html[:cap]
    return html


async def _ssrf_request_hook(request: "httpx.Request") -> None:
    """Re-validate every outbound request URL — including redirect targets."""
    validate_outbound_url(str(request.url))


def _peer_addr_from_response(response: "httpx.Response") -> str | None:
    """Best-effort extraction of the actual remote IP an httpx response was
    served from. ``response.extensions['network_stream']`` is documented but
    backend-specific (asyncio vs trio, sync vs async); fall back gracefully."""
    try:
        stream = response.extensions.get("network_stream")
        if stream is None:
            return None
        info = None
        for key in ("server_addr", "peer_addr"):
            try:
                info = stream.get_extra_info(key)
            except Exception:
                info = None
            if info:
                break
        if info is None:
            return None
        if isinstance(info, tuple):
            return str(info[0])
        return str(info)
    except Exception:
        return None


async def _ssrf_response_hook(response: "httpx.Response") -> None:
    """Detect DNS rebinding: even though the URL passed validate_outbound_url
    at request time, the resolver may have flipped a hostname's answer between
    validation and connect. Verify the actual peer IP before the caller
    iterates the body. Raising here propagates out of httpx and the
    surrounding ``client.stream`` context manager closes the connection."""
    peer = _peer_addr_from_response(response)
    if peer and is_ip_forbidden(peer):
        raise URLUnreachableError(
            f"Hostname resolved to forbidden address at connect: {peer}"
        )


def _get_httpx_client() -> httpx.AsyncClient:
    global _httpx_client
    if _httpx_client is None:
        _httpx_client = httpx.AsyncClient(
            http2=settings.http2_enabled and _HTTP2_AVAILABLE,
            follow_redirects=True,
            limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
            event_hooks={
                "request": [_ssrf_request_hook],
                "response": [_ssrf_response_hook],
            },
        )
    return _httpx_client


# curl_cffi provides TLS-level browser impersonation (JA3/JA4 + HTTP/2
# SETTINGS + header order). When available, it is the primary plain-fetch
# path (T1 in the bypass ladder). httpx stays as the fallback for networks
# that block curl_cffi fingerprints or when the library is absent.
try:
    from curl_cffi.requests import AsyncSession as _CurlAsyncSession  # type: ignore
    from curl_cffi.requests.errors import RequestsError as _CurlRequestsError  # type: ignore
    _CURL_CFFI_AVAILABLE = True
except Exception:
    _CurlAsyncSession = None  # type: ignore
    _CurlRequestsError = Exception  # type: ignore
    _CURL_CFFI_AVAILABLE = False


# curl_cffi accepts a specific set of impersonation targets. Validate once
# at import so a typo in TLS_IMPERSONATE doesn't fail every request.
_VALID_IMPERSONATE = {
    "chrome99", "chrome100", "chrome101", "chrome104", "chrome107",
    "chrome110", "chrome116", "chrome119", "chrome120", "chrome123",
    "chrome124", "chrome125",
    "safari15_3", "safari15_5", "safari17_0", "safari17_2_ios",
    "firefox120", "firefox125", "firefox133",
    "edge99", "edge101",
}


# ── Browser fingerprint pool ────────────────────────────────────────────
#
# A small pool of current stable UAs + matching Client Hints. Rotating across
# this pool flattens the "every Runo request is identical" signal that naive
# fingerprinters key on. Per-request jitter only; the headless launch pins
# one UA per browser context so navigator.userAgent stays consistent.

_UA_POOL: list[tuple[str, str, str, str]] = [
    # (User-Agent, Sec-CH-UA, platform, curl_cffi impersonate target)
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
        '"Windows"',
        "chrome124",
    ),
    (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
        '"macOS"',
        "chrome124",
    ),
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        '"Chromium";v="125", "Google Chrome";v="125", "Not-A.Brand";v="99"',
        '"Windows"',
        "chrome125",
    ),
    (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
        '"Linux"',
        "chrome124",
    ),
    (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:125.0) "
        "Gecko/20100101 Firefox/125.0",
        "",  # Firefox doesn't send Sec-CH-UA
        '"macOS"',
        "firefox125",
    ),
]


def _pick_fingerprint() -> tuple[str, str, str, str]:
    if settings.rotate_user_agent:
        return random.choice(_UA_POOL)
    return _UA_POOL[0]


def _accept_language_for(locale: str | None) -> str:
    if not locale:
        return "en-US,en;q=0.9"
    primary = locale.replace("_", "-")
    base = primary.split("-")[0]
    if base == primary:
        return f"{primary},{primary};q=0.9"
    return f"{primary},{base};q=0.9,en;q=0.5"


def _accept_encoding() -> str:
    """Only advertise encodings we can actually decompress.

    httpx handles gzip+deflate via stdlib zlib. Brotli (``br``) needs the
    optional ``brotli`` / ``brotlicffi`` package; zstd needs ``zstandard``.
    Advertising an encoding we can't decode makes the server return bytes
    we serve up as garbage HTML (root cause of the books.toscrape all-null
    regression). Detect optional deps at import time and build the header.
    """
    encodings = ["gzip", "deflate"]
    try:
        import brotli  # noqa: F401
        encodings.append("br")
    except ImportError:
        try:
            import brotlicffi  # noqa: F401
            encodings.append("br")
        except ImportError:
            pass
    try:
        import zstandard  # noqa: F401
        encodings.append("zstd")
    except ImportError:
        pass
    return ", ".join(encodings)


_ACCEPT_ENCODING = _accept_encoding()


def _build_headers(locale: str | None) -> dict[str, str]:
    ua, ch_ua, platform, _impersonate = _pick_fingerprint()
    headers = {
        "User-Agent": ua,
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;q=0.9,"
            "image/avif,image/webp,*/*;q=0.8"
        ),
        "Accept-Language": _accept_language_for(locale),
        "Accept-Encoding": _ACCEPT_ENCODING,
        "Upgrade-Insecure-Requests": "1",
        "DNT": "1",
        "Connection": "keep-alive",
    }
    if settings.realistic_headers:
        headers.update({
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
        })
        if ch_ua:  # Chromium only
            headers.update({
                "Sec-CH-UA": ch_ua,
                "Sec-CH-UA-Mobile": "?0",
                "Sec-CH-UA-Platform": platform,
            })
    return headers


def _get_browser_sync() -> Browser:
    browser: Browser | None = getattr(_thread_local, "browser", None)
    if browser is None or not browser.is_connected():
        factory, engine = _resolve_sync_playwright()
        pw = factory().start()
        # Launch args that reduce the "automated Chrome" signal without
        # enabling anything privileged. Safe across Chromium versions.
        launch_args = [
            "--disable-blink-features=AutomationControlled",
            "--disable-features=IsolateOrigins,site-per-process",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-features=AutomationControlled",
        ]
        # camoufox swaps Chromium for a hardened Firefox. Its detection
        # surface is different (Firefox is rare among bots), which is why
        # we route the escalation here for PerimeterX/Datadome-class walls.
        if engine == "camoufox":
            browser = pw.firefox.launch(
                headless=settings.playwright_headless,
            )
        else:
            browser = pw.chromium.launch(
                headless=settings.playwright_headless,
                args=launch_args,
            )
        _thread_local.browser = browser
        _thread_local.browser_engine = engine
    return _thread_local.browser


def get_browser_engine() -> str:
    """Engine identifier for observability (`X-Runo-Headless-Engine`)."""
    return getattr(_thread_local, "browser_engine", "playwright")


async def _get_semaphore() -> asyncio.Semaphore:
    global _browser_semaphore
    if _browser_semaphore is None:
        _browser_semaphore = asyncio.Semaphore(settings.headless_concurrency)
    return _browser_semaphore


@dataclass
class FetchResult:
    html: str
    status_code: int
    render_mode: Literal["plain", "headless"]
    fetch_ms: int
    # T6 provenance: populated only when the response came from an archive
    # source (google cache / wayback / amp / reader). ``None`` means the
    # response came directly from the origin. Surfaced to callers via
    # ``X-Runo-Fetch-Source`` so stale data is never served silently.
    fetch_source: str | None = None
    # Response headers (lowercased) — used as a structured-data source so
    # date/language/mime-type fields can be filled without ever touching the
    # LLM. Only a small whitelist (last-modified, content-language,
    # content-type) is consumed downstream; the dict is otherwise opaque.
    response_headers: dict[str, str] | None = None


async def fetch_url(
    url: str,
    render_js: str = "auto",
    timeout_ms: int = 15000,
    locale: str | None = None,
    has_numeric: bool = False,
    paid_bypass_enabled: bool = False,
    schema_fingerprint: str | None = None,
) -> FetchResult:
    validate_outbound_url(url)

    # Negative cache: if this host just failed hard, fail fast instead of
    # re-hammering the origin (and re-paying headless cost).
    if settings.negative_cache_ttl_s > 0 and await is_host_blocked(url):
        raise FetchBlockedError(
            "Host recently returned a hard block; negative cache active."
        )

    if render_js == "always":
        return await _fetch_playwright(url, timeout_ms, locale=locale)

    if render_js == "never":
        return await _fetch_plain(url, timeout_ms, locale=locale)

    # auto: try plain first, escalate if needed
    result = await _fetch_plain(url, timeout_ms, locale=locale)
    reason = _escalation_reason(result.html, result.status_code, has_numeric)
    if reason != "none":
        # Soft-block retry: 429/503 without a block marker often clears on a
        # short retry — cheaper than spinning up headless.
        if (
            settings.retry_before_escalate
            and reason == "soft_status"
            and result.status_code in (429, 503)
        ):
            await asyncio.sleep(random.uniform(0.25, 0.75))
            retry_result = await _fetch_plain(url, timeout_ms, locale=locale)
            if _escalation_reason(
                retry_result.html, retry_result.status_code, has_numeric
            ) == "none":
                return retry_result
            result = retry_result

        try:
            headless_result = await _fetch_playwright(
                url, settings.headless_timeout_ms, locale=locale
            )
            return headless_result
        except FetchBlockedError:
            # The hosted build had paid tiers here: T4 (CAPTCHA solving via
            # CapSolver/2Captcha) and T5 (residential-proxy rotation). The
            # open-source build ships only the free tiers (T0-T3 fetch plus the
            # T6 archive fallback below), so a hard anti-bot block falls straight
            # through to the archive and, failing that, raises FetchBlockedError.

            # T6: last-resort archive fallback. If any public mirror has a
            # usable copy of this URL, return that — stale is better than a
            # hard 502. Only marks the host blocked if archives also fail.
            if settings.archive_fallback_enabled:
                archived = await fetch_from_archive(url)
                if archived is not None:
                    return FetchResult(
                        html=_cap_html(archived.html),
                        status_code=archived.status_code,
                        render_mode="plain",
                        fetch_ms=0,
                        fetch_source=f"archive-{archived.source}",
                    )
            if settings.negative_cache_ttl_s > 0:
                await mark_host_blocked(url, settings.negative_cache_ttl_s)
            raise
    return result


def _pick_impersonate_target() -> str:
    """Return a valid curl_cffi impersonation target or empty string if disabled.

    Priority: explicit setting → UA-pool rotation → empty. An invalid setting
    silently falls back to rotation rather than raising, so a typo in a single
    env var doesn't take the fleet offline."""
    if not _CURL_CFFI_AVAILABLE:
        return ""
    target = (settings.tls_impersonate or "").strip().lower()
    if target == "off":
        return ""
    if target and target in _VALID_IMPERSONATE:
        return target
    # Rotation path: use the impersonate slot from the UA-pool pick so TLS
    # fingerprint and UA string line up (mismatch is itself a detection tell).
    if settings.rotate_user_agent:
        return _pick_fingerprint()[3]
    return _UA_POOL[0][3]


async def _fetch_curlcffi(
    url: str, timeout_ms: int, locale: str | None = None,
    proxy_url: str | None = None,
) -> FetchResult:
    """TLS-impersonating plain fetch. Same response shape as _fetch_httpx.

    Raises the same exceptions so the caller's retry/escalate logic is
    identical. ImportError is surfaced as URLUnreachableError so the
    dispatch wrapper can fall back to httpx on broken installs."""
    if _CurlAsyncSession is None:
        raise URLUnreachableError("curl_cffi unavailable")

    impersonate = _pick_impersonate_target() or "chrome124"
    headers = _build_headers(locale)
    # curl_cffi sends header order matching the impersonation target; remove
    # fields it sets itself so we don't force a fingerprint-inconsistent order.
    # Accept-Encoding and Connection are managed by curl_cffi per-impersonate.
    for k in ("Accept-Encoding", "Connection"):
        headers.pop(k, None)

    # T3 session warming: inject any trust cookies we've accumulated for
    # this host on prior calls. Silent on miss — no cost when empty.
    cookies = await get_cookies_for(url)

    start = time.perf_counter()
    session_kwargs: dict = {
        "impersonate": impersonate,
        "timeout": timeout_ms / 1000,
    }
    if proxy_url:
        session_kwargs["proxies"] = {"http": proxy_url, "https": proxy_url}
    cap = max(getattr(settings, "max_response_bytes", 0) or 0, 0)
    try:
        async with _CurlAsyncSession(**session_kwargs) as session:
            resp = await session.get(
                url, headers=headers, allow_redirects=True,
                cookies=cookies or None,
            )
            elapsed = int((time.perf_counter() - start) * 1000)
            # Body-size guard. curl_cffi reads the full body up-front (no
            # streaming API), so cap on the materialised text after the fact;
            # this still limits memory growth on the *next* alloc (parsing).
            text = resp.text
            if cap and isinstance(text, str) and len(text.encode("utf-8", errors="ignore")) > cap:
                text = text[:cap]
            # Persist any new trust cookies for the next caller.
            try:
                resp_cookies = dict(resp.cookies) if resp.cookies else {}
                if resp_cookies:
                    await store_cookies_from(url, resp_cookies)
            except Exception:
                pass
            try:
                resp_headers = {
                    str(k).lower(): str(v) for k, v in (resp.headers or {}).items()
                }
            except Exception:
                resp_headers = None
            return FetchResult(
                html=_cap_html(text),
                status_code=resp.status_code,
                render_mode="plain",
                fetch_ms=elapsed,
                response_headers=resp_headers,
            )
    except _CurlRequestsError as e:
        msg = str(e).lower()
        if "timeout" in msg or "timed out" in msg:
            raise FetchTimeoutError(f"Timeout fetching {url} after {timeout_ms}ms")
        raise URLUnreachableError(str(e))
    except Exception as e:
        # Broad guard: curl_cffi can raise OSError / ValueError on malformed
        # hosts. Map all of them to URLUnreachableError so the caller's
        # fallback path triggers cleanly.
        raise URLUnreachableError(str(e))


async def _fetch_plain(
    url: str, timeout_ms: int, locale: str | None = None,
) -> FetchResult:
    """Plain-fetch dispatcher. Prefers curl_cffi (T1) when available +
    enabled; falls back to httpx on import failure, disabled setting, or any
    curl_cffi-level error that suggests the library itself is the problem
    (as opposed to a real network failure from the origin)."""
    use_curl = (
        _CURL_CFFI_AVAILABLE
        and (settings.tls_impersonate or "").strip().lower() != "off"
    )
    if not use_curl:
        return await _fetch_httpx(url, timeout_ms, locale=locale)

    try:
        return await _fetch_curlcffi(url, timeout_ms, locale=locale)
    except (FetchTimeoutError, FetchBlockedError):
        # Real network outcomes — don't mask by retrying with httpx.
        raise
    except URLUnreachableError:
        # curl_cffi couldn't reach the host; try httpx as a second opinion
        # in case the origin blocks curl_cffi's fingerprint specifically.
        return await _fetch_httpx(url, timeout_ms, locale=locale)


async def _fetch_httpx(
    url: str, timeout_ms: int, locale: str | None = None
) -> FetchResult:
    headers = _build_headers(locale)
    cookies = await get_cookies_for(url)
    start = time.perf_counter()
    cap = max(getattr(settings, "max_response_bytes", 0) or 0, 0)
    try:
        client = _get_httpx_client()
        # Stream so a server that advertises a small Content-Length but
        # streams gigabytes (decompression bomb) can't OOM the worker.
        async with client.stream(
            "GET", url,
            headers=headers,
            cookies=cookies or None,
            timeout=timeout_ms / 1000,
        ) as resp:
            # Pre-flight: refuse oversize bodies before reading any body bytes.
            if cap:
                cl = resp.headers.get("content-length")
                if cl is not None:
                    try:
                        if int(cl) > cap:
                            raise URLUnreachableError(
                                f"Response too large: Content-Length {cl} > cap {cap}"
                            )
                    except ValueError:
                        pass
            buf = bytearray()
            async for chunk in resp.aiter_bytes():
                buf.extend(chunk)
                if cap and len(buf) > cap:
                    # Truncate rather than raise — most pages with oversize bodies
                    # are still parseable from the leading bytes, and downstream
                    # _cap_html / BM25 already trim further.
                    buf = buf[:cap]
                    break
            # Decode using charset from headers when available; httpx exposes
            # this via resp.encoding on the streaming response.
            try:
                charset = resp.charset_encoding or "utf-8"
            except Exception:
                charset = "utf-8"
            try:
                text = bytes(buf).decode(charset, errors="replace")
            except (LookupError, TypeError):
                text = bytes(buf).decode("utf-8", errors="replace")
            elapsed = int((time.perf_counter() - start) * 1000)
            try:
                resp_cookies = {c.name: c.value for c in resp.cookies.jar}
                if resp_cookies:
                    await store_cookies_from(url, resp_cookies)
            except Exception:
                pass
            try:
                resp_headers = {
                    str(k).lower(): str(v) for k, v in (resp.headers or {}).items()
                }
            except Exception:
                resp_headers = None
            return FetchResult(
                html=_cap_html(text),
                status_code=resp.status_code,
                render_mode="plain",
                fetch_ms=elapsed,
                response_headers=resp_headers,
            )
    except URLUnreachableError:
        raise
    except httpx.TimeoutException:
        raise FetchTimeoutError(f"Timeout fetching {url} after {timeout_ms}ms")
    except httpx.ConnectError:
        raise URLUnreachableError(f"Cannot reach {url}")
    except httpx.HTTPError as e:
        raise URLUnreachableError(str(e))


def _apply_ssrf_route_sync(page) -> None:
    """Install a Playwright route handler that aborts any sub-resource or
    navigation request whose target URL fails ``validate_outbound_url``.

    Without this, JS executed inside a fetched page can pivot to internal
    addresses (cloud metadata, RFC1918) via ``fetch()`` / ``XHR`` /
    ``<iframe src>`` / ``window.location``, bypassing the request-time guard
    that only validated the seed URL. Cheap (one Python call per request);
    aborts produce a normal failed-resource error in the browser, which is
    already a no-op for our extraction flow.
    """
    if not settings.ssrf_guard_enabled:
        return

    def _handler(route, request):
        try:
            validate_outbound_url(request.url)
            route.continue_()
        except URLUnreachableError:
            try:
                route.abort("blockedbyclient")
            except Exception:
                pass
        except Exception:
            try:
                route.continue_()
            except Exception:
                pass

    try:
        page.route("**/*", _handler)
    except Exception:
        pass


def _apply_stealth_sync(page) -> None:
    """Apply stealth patches before any page navigation.

    Prefers `playwright-stealth` when installed. Falls back to an inline
    init script that covers the most-checked signals (webdriver flag,
    plugins, languages, chrome object, permissions API). Either path is
    free; neither path changes LLM or fetch cost.
    """
    if not settings.stealth_enabled:
        return
    try:  # optional dep
        from playwright_stealth import stealth_sync  # type: ignore
        stealth_sync(page)
        return
    except Exception:
        pass
    try:
        page.add_init_script(
            """
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            Object.defineProperty(navigator, 'plugins',
                {get: () => [1, 2, 3, 4, 5]});
            Object.defineProperty(navigator, 'languages',
                {get: () => ['en-US', 'en']});
            window.chrome = window.chrome || { runtime: {} };
            const origQuery = window.navigator.permissions && window.navigator.permissions.query;
            if (origQuery) {
                window.navigator.permissions.query = (p) => (
                    p.name === 'notifications'
                        ? Promise.resolve({ state: Notification.permission })
                        : origQuery(p)
                );
            }
            """
        )
    except Exception:
        pass


def _fetch_playwright_sync(
    url: str, timeout_ms: int, locale: str | None = None
) -> FetchResult:
    browser = _get_browser_sync()
    ua, _ch_ua, _platform, _impersonate = _pick_fingerprint()
    start = time.perf_counter()
    bundle = _build_fp_bundle(ua, locale) if settings.fingerprint_injection else None
    viewport_w = bundle.screen_width if bundle else 1920
    viewport_h = bundle.screen_height if bundle else 1080
    context_kwargs: dict = {
        "viewport": {"width": viewport_w, "height": viewport_h},
        "user_agent": ua,
        "locale": locale or "en-US",
    }
    if bundle:
        context_kwargs["timezone_id"] = bundle.timezone
    context = browser.new_context(**context_kwargs)
    page = context.new_page()
    if bundle:
        try:
            page.add_init_script(_fp_init_script(bundle))
        except Exception:
            pass
    _apply_ssrf_route_sync(page)
    _apply_stealth_sync(page)
    try:
        resp = page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        # networkidle is best-effort: domcontentloaded already gives us the
        # main DOM; this just lets late XHRs settle. Capped tightly so a
        # chatty analytics endpoint doesn't add seconds to every headless call.
        idle_ms = max(0, settings.headless_networkidle_ms)
        if idle_ms > 0:
            try:
                page.wait_for_load_state("networkidle", timeout=idle_ms)
            except Exception:
                pass
        html = page.content()
        status_code = resp.status if resp else 200
        elapsed = int((time.perf_counter() - start) * 1000)

        if _is_hard_block(html, status_code):
            raise FetchBlockedError(_hard_block_message(html, status_code))

        return FetchResult(
            html=_cap_html(html),
            status_code=status_code,
            render_mode="headless",
            fetch_ms=elapsed,
        )
    except FetchBlockedError:
        raise
    except Exception as e:
        if "Timeout" in str(type(e).__name__):
            raise FetchTimeoutError(f"Headless timeout for {url}")
        raise FetchBlockedError(str(e))
    finally:
        try:
            page.close()
        finally:
            context.close()


def _inject_captcha_token_sync(page, captcha_type: str, token: str) -> None:
    """Inject a solved CAPTCHA token into the active page and trigger submission.

    Each type has a different DOM interface:
      - Turnstile: hidden input + data-callback
      - hCaptcha:  textarea + hcaptcha.execute()
      - reCAPTCHA: #g-recaptcha-response + grecaptcha callback
    Falls back to form.submit() when the type-specific callback isn't found."""
    if captcha_type == "turnstile":
        script = f"""
        (function() {{
            document.querySelectorAll('input[name="cf-turnstile-response"]').forEach(
                function(el) {{ el.value = {repr(token)}; }}
            );
            var widget = document.querySelector('.cf-turnstile');
            if (widget) {{
                var cb = widget.getAttribute('data-callback');
                if (cb && typeof window[cb] === 'function') {{
                    window[cb]({repr(token)});
                    return;
                }}
            }}
            var form = document.querySelector('form');
            if (form) form.submit();
        }})();
        """
    elif captcha_type == "hcaptcha":
        script = f"""
        (function() {{
            document.querySelectorAll(
                'textarea[name="h-captcha-response"], textarea[name="g-recaptcha-response"]'
            ).forEach(function(el) {{ el.value = {repr(token)}; }});
            if (typeof hcaptcha !== 'undefined') {{
                hcaptcha.execute();
            }} else {{
                var form = document.querySelector('form');
                if (form) form.submit();
            }}
        }})();
        """
    elif captcha_type in ("recaptcha_v2", "recaptcha_v3"):
        script = f"""
        (function() {{
            var el = document.getElementById('g-recaptcha-response');
            if (el) el.innerHTML = {repr(token)};
            try {{
                var clients = typeof ___grecaptcha_cfg !== 'undefined'
                    ? ___grecaptcha_cfg.clients || {{}} : {{}};
                Object.keys(clients).forEach(function(k) {{
                    var c = clients[k];
                    var cb = (c && c.aa && c.aa.callback)
                          || (c && c.l && c.l.callback);
                    if (typeof cb === 'function') cb({repr(token)});
                }});
            }} catch(e) {{}}
            var form = document.querySelector('form');
            if (form) form.submit();
        }})();
        """
    else:
        return
    try:
        page.evaluate(script)
    except Exception:
        pass


async def _fetch_playwright_upstream(
    url: str, timeout_ms: int, locale: str | None = None
) -> FetchResult:
    """POST to a remote headless worker. Used when HEADLESS_UPSTREAM_URL is set."""
    upstream = settings.headless_upstream_url.rstrip("/")
    start = time.perf_counter()
    async with httpx.AsyncClient(timeout=(timeout_ms + 5000) / 1000) as client:
        resp = await client.post(
            f"{upstream}/internal/headless",
            json={"url": url, "timeout_ms": timeout_ms, "locale": locale},
        )
        resp.raise_for_status()
        payload = resp.json()
    elapsed = int((time.perf_counter() - start) * 1000)
    return FetchResult(
        html=_cap_html(payload["html"]),
        status_code=payload.get("status_code", 200),
        render_mode="headless",
        fetch_ms=elapsed,
    )


async def _fetch_playwright(
    url: str, timeout_ms: int, locale: str | None = None
) -> FetchResult:
    # If an upstream headless worker is configured, route there first. On any
    # failure (down, timeout, 5xx), fall back to the in-process browser so the
    # caller never sees a topology-specific error.
    if settings.headless_upstream_url:
        try:
            return await _fetch_playwright_upstream(url, timeout_ms, locale=locale)
        except Exception:
            pass
    sem = await _get_semaphore()
    async with sem:
        def _call() -> FetchResult:
            # Keep locale as a keyword so existing test doubles with a 2-arg
            # signature (url, timeout_ms) don't break.
            try:
                return _fetch_playwright_sync(url, timeout_ms, locale=locale)
            except TypeError:
                return _fetch_playwright_sync(url, timeout_ms)
        # Route every browser interaction through the single dedicated thread
        # so the playwright greenlet stays affine across requests.
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(_get_browser_executor(), _call)


def get_headless_pressure() -> dict:
    """Snapshot of in-process browser-pool pressure for /healthz/headless."""
    sem = _browser_semaphore
    max_c = settings.headless_concurrency
    if sem is None:
        return {"max_concurrency": max_c, "active": 0, "queue_depth": 0, "browser_connected": False}
    try:
        waiters = len(sem._waiters) if sem._waiters else 0  # type: ignore[attr-defined]
    except Exception:
        waiters = 0
    active = max_c - sem._value  # type: ignore[attr-defined]
    return {
        "max_concurrency": max_c,
        "active": max(0, active),
        "queue_depth": waiters,
        "browser_connected": _browser_executor is not None,
    }


# ── Block detection ──────────────────────────────────────────────────────
#
# Fast, body-level signals for the most common passive bot walls. We match
# cheaply (substring/regex) on the response HTML before deciding whether to
# escalate. True positives here *save* cost: we skip the doomed plain-fetch
# retry and go straight to stealth headless — or fail fast on a known block.

_BLOCK_SIGNATURES = (
    # Cloudflare challenge / managed challenge
    "cf-chl-bypass",
    "__cf_chl_",
    "/cdn-cgi/challenge-platform/",
    "cf-turnstile",
    # Cloudflare "Just a moment..." interstitial
    "<title>Just a moment...",
    # Datadome
    "datadome",
    "dd-captcha",
    # PerimeterX
    "px-captcha",
    "_pxhd",
    "_px3",
    # Akamai Bot Manager / Incapsula
    "akam-sw.js",
    "_Incapsula_Resource",
)


# Phrases that appear in human-readable bot-block / access-denied pages that
# return HTTP 200 with no JS/script-level block markers. Lower-cased; matched
# against tag-stripped visible text on small pages only. Curated to avoid
# false positives on real articles by requiring the phrase to appear on a
# short page that contains little else.
_BLOCK_TEXT_PATTERNS = (
    # Adidas / Akamai-style polite blocks
    "unable to give you access",
    "unfortunately we are unable",
    # Akamai / generic access denied
    "access denied",
    "you don't have permission to access",
    "you do not have permission to access",
    # Distil Networks / Imperva soft walls
    "pardon our interruption",
    "as you were browsing",
    # Incapsula
    "incapsula incident",
    # Cloudflare error pages
    "sorry, you have been blocked",
    "checking your browser before accessing",
    "this website is using a security service to protect itself",
    # Generic bot challenge text
    "please verify you are a human",
    "complete the security check to access",
    "are you a robot",
    "enable javascript and cookies to continue",
    # Common phrasing on retail / sneaker drops
    "extra security in place to prevent bots",
    "automatically identified a security issue",
)


# Akamai reference error tokens: "Reference #18.a8c82c17.1779219285.13ff7a71"
# or "Reference Error: 18.a8c82c17.1779219285.13ff7a71". The 4-segment
# alternating hex/digit format is distinctive to Akamai's access-denied page
# and shows up verbatim in visible text when a request is rejected.
_AKAMAI_REFERENCE_RE = re.compile(
    r"reference\s*(?:error|#|number)?\s*[:#]?\s*"
    r"\d{1,3}\.[0-9a-f]{6,}\.\d{6,}\.[0-9a-f]{6,}",
    re.IGNORECASE,
)


def _has_block_signature(html: str) -> bool:
    if not html:
        return False
    low = html.lower()
    for sig in _BLOCK_SIGNATURES:
        if sig.lower() in low:
            return True
    return False


def _has_block_text(html: str) -> bool:
    """Detect HTTP-200 block pages by their visible-text fingerprint.

    These pages don't carry the JS/script markers in ``_BLOCK_SIGNATURES``,
    so the only signal is human-readable text explaining the block. Adidas
    (Akamai), Distil/Imperva soft walls, Cloudflare "Sorry, you have been
    blocked", and Incapsula incident pages all fall through here.

    Constrained to small pages (raw HTML < 60 KB **and** visible text < 4000
    chars) so a phrase like "access denied" inside a long article can't
    trigger a false positive.
    """
    if not html or len(html) > 60_000:
        return False
    visible = _visible_text(html)
    if not visible or len(visible) > 4000:
        return False
    low = visible.lower()
    for pat in _BLOCK_TEXT_PATTERNS:
        if pat in low:
            return True
    if _AKAMAI_REFERENCE_RE.search(low):
        return True
    return False


def _is_hard_block(html: str, status_code: int) -> bool:
    """After headless, if we still see a block page, it's a hard block."""
    if status_code in (403, 429, 503) and len(html.strip()) < 2000:
        return True
    if _has_block_signature(html) and len(html.strip()) < 20000:
        return True
    if _has_block_text(html):
        return True
    return False


def _hard_block_message(html: str, status_code: int) -> str:
    """Surface the actual block signal so the response message tells the
    caller *why* extraction failed, not just that it did. Pro/Scale add
    residential proxies + CAPTCHA solving for sites with stronger defenses
    — the message hints at the upgrade path without claiming it'll work."""
    upgrade_hint = (
        " Pro and Scale tiers add residential proxies and CAPTCHA solving "
        "for sites with stronger anti-bot defenses."
    )
    if _has_block_text(html):
        return (
            "Site returned a bot-detection page (HTTP 200 with block content). "
            "The page was reachable but its anti-bot system refused to serve "
            "the real content to this request." + upgrade_hint
        )
    if _has_block_signature(html):
        return (
            "Target site's anti-bot system (Cloudflare / Datadome / "
            "PerimeterX / Akamai-class) blocked the request after headless "
            "escalation." + upgrade_hint
        )
    return (
        f"Target URL returned {status_code} after headless escalation."
        + upgrade_hint
    )


def _visible_text(html: str) -> str:
    """Strip tags + script/style content, return the visible text."""
    try:
        soup = BeautifulSoup(html, "lxml")
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        return soup.get_text(separator=" ", strip=True)
    except Exception:
        return re.sub(r"<[^>]+>", " ", html).strip()


def _visible_text_length(html: str) -> int:
    """Kept as a thin wrapper for backwards compatibility with tests / callers."""
    return len(_visible_text(html))


def _digit_density(html: str) -> float:
    """Fraction of visible-text characters that are digits. Widget-shell
    pages (weather.com, dashboards) have near-zero density because the
    numeric payload is rendered client-side."""
    txt = _visible_text(html)
    if not txt:
        return 0.0
    digits = sum(1 for c in txt if c.isdigit())
    return digits / len(txt)


def _escalation_reason(
    html: str, status_code: int, has_numeric: bool = False
) -> str:
    """Classify why (or whether) to escalate to headless.

    Returns one of: 'block_signature' | 'soft_status' | 'empty_body' |
    'js_framework' | 'interstitial_like' | 'no_digits' | 'empty_text' | 'none'.
    'none' means no escalation.
    """
    if settings.block_detection_enabled and _has_block_signature(html):
        return "block_signature"
    # HTTP-200 block pages (Adidas/Akamai-style, Cloudflare "Sorry, you have
    # been blocked", Incapsula incident pages). Cheap text scan, only ever
    # fires on small pages — see _has_block_text for the bounds.
    if settings.block_detection_enabled and _has_block_text(html):
        return "block_content"
    if status_code in (402, 403, 406, 429, 503):
        return "soft_status"
    if len(html.strip()) < 500:
        return "empty_body"
    js_signals = ["__NEXT_DATA__", "ng-version", "data-reactroot", "window.__NUXT__"]
    if any(signal in html for signal in js_signals):
        return "js_framework"
    # Short-interstitial: medium body, mostly <script> shell with almost no
    # prose. Catches Reuters-class soft-walls that ship no explicit challenge
    # marker but still render empty to plain fetch.
    if 1000 < len(html) < 8000 and _visible_text_length(html) < 300:
        return "interstitial_like"
    # Numeric-field widget: schema wants integers/floats but page has zero
    # digits in visible text. Catches weather.com / accuweather / dashboards.
    if has_numeric and len(html) > 1000 and _digit_density(html) < 0.001:
        return "no_digits"
    # Final guard: allrecipes / timeanddate / similar JS-rendered pages serve
    # a multi-KB HTML skeleton with no prose — no framework marker, no block
    # signature, just empty after tag-strip. If the raw HTML is >2KB but
    # visible text is tiny, the content is client-rendered → escalate.
    if len(html) > 2000 and _visible_text_length(html) < 200:
        return "empty_text"
    return "none"


def _should_escalate(html: str, status_code: int, has_numeric: bool = False) -> bool:
    return _escalation_reason(html, status_code, has_numeric) != "none"


_BOILERPLATE_HEADINGS = {
    "references", "external links", "further reading", "see also",
    "notes", "bibliography", "citations", "footnotes", "sources",
    "works cited",
}

_BOILERPLATE_HEADING_IDS = {
    "References", "External_links", "Further_reading", "See_also",
    "Notes", "Bibliography", "Citations", "Footnotes", "Sources",
    "Works_cited",
}


def _strip_boilerplate_html(soup: BeautifulSoup) -> None:
    """Remove citation/reference containers and Wikipedia-style trailing sections."""
    # Direct citation containers used by MediaWiki + common wikis.
    for sel in (
        {"name": "ol", "attrs": {"class": "references"}},
        {"name": "div", "attrs": {"class": "reflist"}},
        {"name": "div", "attrs": {"class": "refbegin"}},
        {"name": "span", "attrs": {"class": "mw-editsection"}},
        {"name": "sup", "attrs": {"class": "reference"}},
        {"name": "table", "attrs": {"class": "navbox"}},
    ):
        for tag in soup.find_all(sel["name"], attrs=sel["attrs"]):
            tag.decompose()

    # IPA pronunciation spans — Wikipedia and wiki-style sites wrap phonetic
    # transcriptions in <span class="IPA"> (and variants). Strip them at the
    # HTML level before trafilatura so the text path never sees them.
    for tag in soup.find_all("span", class_=re.compile(r"\bIPA\b")):
        tag.decompose()

    # Drop every sibling that follows a heading whose id is in the blocklist
    # (MediaWiki puts the id on the <h2><span id="References">).
    for span in soup.find_all(attrs={"id": True}):
        if span.get("id") not in _BOILERPLATE_HEADING_IDS:
            continue
        heading = span.find_parent(["h1", "h2", "h3", "h4"])
        if not heading:
            continue
        sib = heading.find_next_sibling()
        while sib is not None:
            nxt = sib.find_next_sibling()
            sib.decompose()
            sib = nxt
        heading.decompose()


# ── Domain-specific strippers ────────────────────────────────────────────
#
# These remove HTML *containers* only (nav / sidebar / footnote elements).
# They never truncate cleaned text at trailing-section keywords — unlike
# Wikipedia, SEC filings and legal opinions legitimately carry material
# content in sections named "Signatures", "Exhibits", "Footnotes". If the
# caller's schema mentions any of those keywords, the domain stripper is
# skipped for that request (see _schema_mentions_sensitive_section).


def _strip_sec(soup: BeautifulSoup) -> None:
    for sel in (
        {"name": "table", "attrs": {"class": "tableFile"}},
        {"name": "table", "attrs": {"class": "tableFile2"}},
        {"name": "div", "attrs": {"class": "formGrouping"}},
        {"name": "div", "attrs": {"id": "secNav"}},
        {"name": "div", "attrs": {"id": "globalNav"}},
    ):
        for tag in soup.find_all(sel["name"], attrs=sel["attrs"]):
            tag.decompose()


def _strip_legal(soup: BeautifulSoup) -> None:
    for sel in (
        {"name": "div", "attrs": {"class": "footnote"}},
        {"name": "div", "attrs": {"class": "footnotes"}},
        {"name": "a", "attrs": {"class": "footnote-link"}},
        {"name": "sup", "attrs": {"class": "footnote"}},
        {"name": "nav", "attrs": {"class": "breadcrumb"}},
        {"name": "aside", "attrs": {"class": "sidebar"}},
    ):
        for tag in soup.find_all(sel["name"], attrs=sel["attrs"]):
            tag.decompose()


def _strip_docs(soup: BeautifulSoup) -> None:
    for sel in (
        {"name": "nav", "attrs": {"class": "sidebar"}},
        {"name": "aside", "attrs": {"class": "toc"}},
        {"name": "div", "attrs": {"class": "toc"}},
        {"name": "footer", "attrs": {"class": "docs-footer"}},
        {"name": "div", "attrs": {"class": "edit-this-page"}},
        {"name": "div", "attrs": {"class": "page-nav"}},
    ):
        for tag in soup.find_all(sel["name"], attrs=sel["attrs"]):
            tag.decompose()


def _strip_social(soup: BeautifulSoup) -> None:
    """Strip common social-platform chrome: login modals, suggested posts, etc.

    Kept conservative — we only remove containers whose sole purpose is
    interstitial UI. Real post content stays intact.
    """
    for sel in (
        {"name": "div", "attrs": {"role": "dialog"}},
        {"name": "div", "attrs": {"class": "login-overlay"}},
        {"name": "div", "attrs": {"data-testid": "login-prompt"}},
        {"name": "section", "attrs": {"aria-label": "Suggested"}},
    ):
        for tag in soup.find_all(sel["name"], attrs=sel["attrs"]):
            tag.decompose()


_DOMAIN_STRIPPERS: list[tuple[tuple[str, ...], Callable[[BeautifulSoup], None]]] = [
    (("sec.gov",), _strip_sec),
    (("courtlistener.com", "justia.com", "law.cornell.edu"), _strip_legal),
    (("readthedocs.io", "readthedocs.org", "gitbook.io", "docusaurus.io"), _strip_docs),
    (("twitter.com", "x.com", "instagram.com", "linkedin.com", "reddit.com"),
     _strip_social),
]


_SENSITIVE_SECTION_RE = re.compile(
    r"signatur|exhibit|footnote|\bnote\b|\btoc\b|table[\s_]of[\s_]contents",
    re.IGNORECASE,
)


def _schema_mentions_sensitive_section(fields: list[SchemaField] | None) -> bool:
    if not fields:
        return False
    for f in fields:
        if _SENSITIVE_SECTION_RE.search(f.field):
            return True
        if f.hint and _SENSITIVE_SECTION_RE.search(f.hint):
            return True
    return False


def _apply_domain_stripper(soup: BeautifulSoup, url: str | None) -> None:
    if not url:
        return
    try:
        host = urlsplit(url).netloc.lower()
    except Exception:
        return
    for suffixes, fn in _DOMAIN_STRIPPERS:
        if any(host == s or host.endswith("." + s) or host.endswith(s) for s in suffixes):
            fn(soup)
            return


def _truncate_trailing_sections(text: str) -> str:
    """Cut cleaned text at the first line matching a boilerplate heading."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        stripped = line.strip().rstrip(":").lower()
        if stripped in _BOILERPLATE_HEADINGS:
            return "\n".join(lines[:i]).rstrip()
    return text


_PRODUCT_FIELD_RE = re.compile(
    r"\b(price|stock|sku|availability|in_stock|inventory|product)\b",
    re.IGNORECASE,
)
_PRODUCT_SELECTORS = (
    ".product_main", ".product_page", ".product", ".product-main",
    ".product-detail", "[itemtype*='Product']", "[data-product]",
)


def _schema_is_product_like(fields: list[SchemaField] | None) -> bool:
    if not fields:
        return False
    for f in fields:
        if _PRODUCT_FIELD_RE.search(f.field):
            return True
    return False


def _extract_product_text(soup: BeautifulSoup) -> str:
    """Return text from any element matching a product-detail selector.

    Trafilatura treats `<table>` blocks as boilerplate on pages like
    books.toscrape and drops the price/availability row entirely. This
    targeted pass runs only when the schema mentions product fields AND
    the HTML contains table/list markup — so prose pages are unaffected.
    """
    chunks: list[str] = []
    seen: set[int] = set()
    for sel in _PRODUCT_SELECTORS:
        try:
            matches = soup.select(sel)
        except Exception:
            continue
        for m in matches:
            if id(m) in seen:
                continue
            seen.add(id(m))
            text = m.get_text(separator="\n", strip=True)
            if text and len(text) > 20:
                chunks.append(text)
    return "\n\n".join(chunks)


_LIST_SELECTORS = (
    ".quote", "article", "[itemscope]",
    "[class*='item']", "[class*='card']", "[class*='entry']",
    "[class*='post']", "li[class]",
)


def _schema_has_array(fields: list[SchemaField] | None) -> bool:
    if not fields:
        return False
    return any(f.type.startswith("array<") for f in fields)


def _extract_list_text(soup: BeautifulSoup, limit: int = 80) -> str:
    """Pull repeated-structure blocks (cards/list items) into a preamble.

    Trafilatura aggressively strips repeated card/list markup as boilerplate.
    For array<> schemas (quotes, product lists, article feeds) this drops the
    answer. We walk known list selectors, capture short text per match, dedup
    by content hash, and cap at `limit` blocks so a pathological page can't
    blow up the prompt.
    """
    chunks: list[str] = []
    seen_text: set[str] = set()
    seen_ids: set[int] = set()
    for sel in _LIST_SELECTORS:
        try:
            matches = soup.select(sel)
        except Exception:
            continue
        for m in matches:
            if id(m) in seen_ids:
                continue
            seen_ids.add(id(m))
            text = m.get_text(separator=" ", strip=True)
            if not text or len(text) < 15 or len(text) > 2000:
                continue
            key = text[:200]
            if key in seen_text:
                continue
            seen_text.add(key)
            chunks.append(text)
            if len(chunks) >= limit:
                return "\n\n".join(chunks)
    return "\n\n".join(chunks)


_DESCRIPTION_FIELD_HINTS = (
    "description", "summary", "tagline", "bio", "about", "headline", "subtitle",
)

# Matches inline citation/footnote markers: [1], [ 2 ], [14], [a], [iv], etc.
# These appear when trafilatura or BS4 includes superscript text (e.g. Wikipedia's
# <sup class="reference">[1]</sup>) that wasn't stripped at the HTML level.
_CITATION_MARKER_RE = re.compile(r"\[\s*(?:\d{1,3}|[a-z]{1,4})\s*\]", re.IGNORECASE)

# Matches parenthetical phrases that contain IPA characters. Unicode ranges:
#   U+0250–U+02AF  IPA Extensions  (ɪ ʊ ə ʃ ʒ θ ð ŋ ɒ æ ɑ ɔ ɜ ʌ ɐ ɾ ɻ ɡ …)
#   U+02B0–U+02FF  Spacing Modifier Letters  (ˈ ˌ ː ʰ ʲ ʷ …)
# These characters appear in phonetic transcriptions but essentially never in
# ordinary prose, so any parenthetical containing them is safe to strip.
# {0,400} on each side bounds backtracking; non-nested only (no inner parens).
_IPA_PAREN_RE = re.compile(
    r"\s*\([^()]{0,400}[ɐ-˿][^()]{0,400}\)",
    re.UNICODE,
)


def _strip_citation_markers(text: str) -> str:
    """Remove inline footnote/citation markers and IPA pronunciation guides."""
    text = _CITATION_MARKER_RE.sub("", text)
    text = _IPA_PAREN_RE.sub("", text)
    return re.sub(r"  +", " ", text).strip()


def _truncate(s: str, n: int = 400) -> str:
    s = s.strip()
    return s[:n].rstrip() + "…" if len(s) > n else s


def _meta_content(soup: BeautifulSoup, *names: str) -> str | None:
    """Find first <meta name=...|property=...> with non-empty content."""
    for name in names:
        for attr in ("name", "property", "itemprop"):
            tag = soup.find("meta", attrs={attr: name})
            if tag and tag.get("content", "").strip():
                return tag["content"].strip()
    return None


def _jsonld_author(soup: BeautifulSoup) -> str | None:
    """Extract author name from JSON-LD, handling both string and object forms.

    Handles: "author": "John Doe"
             "author": {"name": "John Doe"}
             "author": [{"name": "Jane"}, ...]
    """
    import json as _json
    for tag in soup.find_all("script", attrs={"type": "application/ld+json"}):
        body = tag.string or tag.get_text() or ""
        if not body.strip():
            continue
        try:
            data = _json.loads(body)
        except Exception:
            continue
        items = data if isinstance(data, list) else [data]
        for item in items:
            if not isinstance(item, dict):
                continue
            graphs = [item] + (item.get("@graph") or [])
            for node in graphs:
                if not isinstance(node, dict):
                    continue
                for key in ("author", "creator"):
                    a = node.get(key)
                    if isinstance(a, str) and a.strip():
                        return a.strip()
                    if isinstance(a, dict):
                        n = a.get("name")
                        if isinstance(n, str) and n.strip():
                            return n.strip()
                    if isinstance(a, list) and a:
                        first = a[0]
                        if isinstance(first, str) and first.strip():
                            return first.strip()
                        if isinstance(first, dict):
                            n = first.get("name")
                            if isinstance(n, str) and n.strip():
                                return n.strip()
    return None


def _jsonld_field(soup: BeautifulSoup, *keys: str) -> str | None:
    """Pull a top-level JSON-LD field (name/headline/description) if present.

    Walks every <script type=application/ld+json> block and returns the first
    string value matching any of the requested keys at the top level (or
    inside @graph entries).
    """
    import json as _json
    for tag in soup.find_all("script", attrs={"type": "application/ld+json"}):
        body = tag.string or tag.get_text() or ""
        if not body.strip():
            continue
        try:
            data = _json.loads(body)
        except Exception:
            continue
        candidates = data if isinstance(data, list) else [data]
        for c in candidates:
            if not isinstance(c, dict):
                continue
            for k in keys:
                v = c.get(k)
                if isinstance(v, str) and v.strip():
                    return v.strip()
            graph = c.get("@graph")
            if isinstance(graph, list):
                for g in graph:
                    if not isinstance(g, dict):
                        continue
                    for k in keys:
                        v = g.get(k)
                        if isinstance(v, str) and v.strip():
                            return v.strip()
    return None


def extract_canonical_signals(html: str, final_url: str | None = None) -> str:
    """Build the ## Page Identifiers preamble from raw HTML.

    Pulls page identity signals — title, first H1, OG/Twitter card metadata,
    meta description, JSON-LD name/headline/description, host — and emits a
    compact preamble that's prepended to the LLM input verbatim. This guards
    against trafilatura/BM25 stripping the brand or hero tagline (the Vue.js
    failure mode).

    Returns "" if nothing useful is found. Each field is capped at 400 chars.
    """
    if not html:
        return ""
    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        return ""

    lines: list[str] = []
    if final_url:
        try:
            host = urlsplit(final_url).hostname
            if host:
                lines.append(f"URL host: {host}")
        except Exception:
            pass

    title_tag = soup.find("title")
    if title_tag and title_tag.get_text(strip=True):
        lines.append(f"Title: {_truncate(title_tag.get_text(strip=True))}")

    h1 = soup.find("h1")
    if h1 and h1.get_text(strip=True):
        lines.append(f"H1: {_truncate(h1.get_text(strip=True), 200)}")

    og_site = _meta_content(soup, "og:site_name")
    if og_site:
        lines.append(f"Site name: {_truncate(og_site, 200)}")

    og_title = _meta_content(soup, "og:title", "twitter:title")
    if og_title:
        lines.append(f"OG title: {_truncate(og_title)}")

    og_desc = _meta_content(soup, "og:description", "twitter:description")
    if og_desc:
        lines.append(f"OG description: {_truncate(og_desc)}")

    meta_desc = _meta_content(soup, "description")
    if meta_desc and meta_desc != og_desc:
        lines.append(f"Meta description: {_truncate(meta_desc)}")

    jsonld_name = _jsonld_field(soup, "name", "headline")
    if jsonld_name and jsonld_name not in (og_title or "", og_site or ""):
        lines.append(f"Schema.org name: {_truncate(jsonld_name, 200)}")

    jsonld_desc = _jsonld_field(soup, "description")
    if jsonld_desc and jsonld_desc not in (og_desc or "", meta_desc or ""):
        lines.append(f"Schema.org description: {_truncate(jsonld_desc)}")

    # Author/byline — trafilatura routinely drops short metadata rows (e.g.
    # HN's "by [author] 3 hours ago" subtext) and BM25 can't rescue them
    # because the word "author" never appears in the text. Capturing here
    # guarantees the author reaches the LLM regardless of page structure.
    # Four-tier fallback, each only runs if the previous found nothing.
    author: str | None = _meta_content(soup, "author", "article:author")
    if not author:
        # twitter:creator is widely used on blogs/news even without full OG.
        # Strip leading @ to get the name rather than the handle where possible.
        tc = _meta_content(soup, "twitter:creator")
        if tc:
            cleaned = tc.lstrip("@").strip()
            if cleaned and len(cleaned) > 2:
                author = cleaned
    if not author:
        author = _jsonld_author(soup)
    if not author:
        rel_tag = soup.find("a", rel="author")
        if rel_tag:
            txt = rel_tag.get_text(strip=True)
            if txt and len(txt) < 100:
                author = txt
    if not author:
        # Class-based byline heuristic: catches HN's hnuser, most blog/news
        # author spans, and other sites where the author is styled but not
        # semantically marked up. Length cap prevents full-paragraph matches.
        byline_el = soup.find(
            class_=re.compile(
                r"\b(author|byline|hnuser|creator|byline-name|post-author"
                r"|entry-author|article-author|author-name|author-link"
                r"|post-meta)\b",
                re.I,
            )
        )
        if byline_el:
            txt = byline_el.get_text(strip=True)
            if txt and len(txt) < 100:
                author = txt
    if not author:
        # <address> is the HTML5 semantic element for authorship contact info.
        addr = soup.find("address")
        if addr:
            txt = addr.get_text(strip=True)
            if txt and len(txt) < 100:
                author = txt
    if author:
        lines.append(f"Author: {_truncate(author, 200)}")

    # Numeric stats (score, votes, points, views, likes, etc.) — trafilatura
    # drops short metadata spans so these values never reach the LLM.
    # Scanning here captures them unconditionally for any site that uses
    # standard class names: HN score, Reddit votes, SO vote counts, etc.
    _STAT_CLS = re.compile(
        r"\b(score|points?|votes?|upvotes?|downvotes?|likes?|views?|"
        r"karma|reactions?|shares?|favorites?)\b",
        re.I,
    )
    stat_texts: list[str] = []
    seen_stats: set[str] = set()
    for el in soup.find_all(class_=_STAT_CLS)[:8]:
        txt = el.get_text(strip=True)
        # Only keep short, digit-bearing strings — filters headings like
        # "Votes" or decorative spans with no actual count.
        if txt and 1 < len(txt) < 80 and re.search(r"\d", txt) and txt not in seen_stats:
            seen_stats.add(txt)
            stat_texts.append(txt)
    if stat_texts:
        lines.append(f"Stats: {' | '.join(stat_texts)}")

    if not lines:
        return ""
    return "## Page Identifiers\n" + "\n".join(lines)


def _schema_has_description_field(fields: list[SchemaField] | None) -> bool:
    if not fields:
        return False
    for f in fields:
        name = f.field.lower()
        if any(h in name for h in _DESCRIPTION_FIELD_HINTS):
            return True
    return False


def extract_description_candidates(html: str) -> str:
    """Build the ## Candidate descriptions block.

    Surfaces 2–4 distinct description-shaped paragraphs so the LLM doesn't
    have to guess which paragraph is the page's authoritative description.
    Sources, in priority order: og:description, meta description, first
    paragraph of <main>/[role=main]/<article>, first paragraph after <h1>.
    Deduplicated, longest first, each capped at 400 chars.

    Returns "" if no candidates found. Only invoked when the schema asks
    for a description-shaped field — keeps prompt lean otherwise.
    """
    if not html:
        return ""
    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        return ""

    candidates: list[str] = []

    og_desc = _meta_content(soup, "og:description", "twitter:description")
    if og_desc:
        candidates.append(og_desc.strip())

    meta_desc = _meta_content(soup, "description")
    if meta_desc:
        candidates.append(meta_desc.strip())

    jsonld_desc = _jsonld_field(soup, "description")
    if jsonld_desc:
        candidates.append(jsonld_desc.strip())

    # First substantial paragraph in <main> / [role=main] / <article>.
    for sel in ("main", "[role=main]", "article"):
        try:
            container = soup.select_one(sel)
        except Exception:
            container = None
        if container:
            for p in container.find_all("p"):
                # Strip superscript/subscript annotation elements before text
                # extraction — catches Wikipedia [1] references and any site
                # that uses <sup> for inline footnotes.
                for ann in p.find_all(["sup", "sub"]):
                    ann.decompose()
                txt = p.get_text(" ", strip=True)
                txt = _strip_citation_markers(txt)
                if txt and len(txt) >= 40:
                    candidates.append(txt)
                    break
            break  # one container is enough

    # First paragraph after the first <h1>.
    h1 = soup.find("h1")
    if h1:
        nxt = h1.find_next("p")
        if nxt:
            for ann in nxt.find_all(["sup", "sub"]):
                ann.decompose()
            txt = nxt.get_text(" ", strip=True)
            txt = _strip_citation_markers(txt)
            if txt and len(txt) >= 40:
                candidates.append(txt)

    # Dedupe (case-insensitive prefix), keep longest for each near-duplicate.
    # Apply citation-marker cleanup to all sources (og/meta/jsonld text can
    # also carry over encoded references on some sites).
    deduped: list[str] = []
    seen_prefixes: set[str] = set()
    for c in sorted(candidates, key=len, reverse=True):
        c = _strip_citation_markers(c)
        prefix = c[:80].lower()
        if prefix in seen_prefixes:
            continue
        seen_prefixes.add(prefix)
        deduped.append(_truncate(c, 1500))
        if len(deduped) >= 4:
            break

    if not deduped:
        return ""
    return "## Candidate descriptions\n" + "\n".join(f"- {c}" for c in deduped)


def clean_html(
    html: str,
    url: str | None = None,
    schema_fields: list[SchemaField] | None = None,
) -> str:
    # HTML-level strip of reference/citation/navigation boilerplate first.
    try:
        soup_pre = BeautifulSoup(html, "lxml")
        _strip_boilerplate_html(soup_pre)
        if not _schema_mentions_sensitive_section(schema_fields):
            _apply_domain_stripper(soup_pre, url)
        for tag in soup_pre(["script", "style", "noscript"]):
            tag.decompose()
        stripped_html = str(soup_pre)
    except Exception:
        stripped_html = html

    # Product-aware preamble: when the schema asks for product-ish fields,
    # capture the product-detail region *before* trafilatura strips it as
    # boilerplate. Fires on any product-like schema — CSS-grid / flexbox
    # product pages don't carry `<table>` markup but still expose .product /
    # [itemtype=Product] selectors.
    product_preamble = ""
    if _schema_is_product_like(schema_fields):
        try:
            soup_p = BeautifulSoup(stripped_html, "lxml")
            product_preamble = _extract_product_text(soup_p)
        except Exception:
            product_preamble = ""

    # List-aware preamble: for array<> schemas, trafilatura often strips the
    # repeated card/list markup that holds the answer (quotes.toscrape-class).
    list_preamble = ""
    if settings.list_preamble_enabled and _schema_has_array(schema_fields):
        try:
            soup_l = BeautifulSoup(stripped_html, "lxml")
            list_preamble = _extract_list_text(soup_l)
        except Exception:
            list_preamble = ""

    def _combine(*parts: str) -> str:
        return "\n\n".join(p for p in parts if p)

    # Try trafilatura first. favor_recall=True pulls more content on pages
    # where the default balanced mode drops summary paragraphs (MDN-style).
    # BM25 filters excess boilerplate downstream.
    text = trafilatura.extract(
        stripped_html, include_tables=True, include_links=False,
        favor_recall=True,
    )
    if text and len(text.strip()) > 50:
        combined = _combine(product_preamble, list_preamble, text.strip())
        return _strip_citation_markers(_truncate_trailing_sections(combined))

    # Fallback to BeautifulSoup
    soup = BeautifulSoup(stripped_html, "lxml")
    for tag in soup(["script", "style", "nav", "footer", "header", "aside", "noscript"]):
        tag.decompose()
    text = soup.get_text(separator="\n", strip=True)
    combined = _combine(product_preamble, list_preamble, text)
    return _strip_citation_markers(_truncate_trailing_sections(combined))
