"""T6 — archive fallback.

When T0–T5 all fail with a block signature, we try to recover the target
URL from public archives and mirror endpoints. Returns *stale but real*
HTML, which is strictly better than the current `FETCH_BLOCKED` cliff for
callers that just want the data.

Sources, in parallel (first non-empty wins):

  1. Google Cache       — https://webcache.googleusercontent.com/search?q=cache:{url}
  2. Wayback Machine    — https://web.archive.org/web/2/{url}  (auto-redirects to latest snapshot)
  3. AMP variant        — https://{host}/amp/{path}  (best-effort guess)
  4. Reader view        — trafilatura's URL fetch; thin wrapper over httpx but uses
                          its own user-agent + heuristics, occasionally bypasses
                          the blocks we just hit.

Every archive hit marks the response with ``X-Runo-Fetch-Source:
archive-{source}`` so callers know the data is stale.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Literal
from urllib.parse import quote, urlsplit, urlunsplit

import httpx

from runo.core.url_safety import validate_outbound_url
from runo.exceptions import URLUnreachableError


ArchiveSource = Literal["google", "wayback", "amp", "reader"]


@dataclass
class ArchiveResult:
    html: str
    source: ArchiveSource
    status_code: int = 200


def _google_cache_url(url: str) -> str:
    return f"https://webcache.googleusercontent.com/search?q=cache:{quote(url, safe='')}"


def _wayback_url(url: str) -> str:
    # The /web/2/ prefix redirects to the latest available snapshot — no need
    # to hit the availability-API endpoint first.
    return f"https://web.archive.org/web/2/{url}"


def _amp_url(url: str) -> str | None:
    """Guess an AMP variant. Site-specific heuristics beat a single rule, but
    a leading /amp/ segment covers the long tail of WordPress/news sites."""
    parts = urlsplit(url)
    if not parts.netloc:
        return None
    path = parts.path or "/"
    if path.startswith("/amp/") or path.endswith("/amp") or path.endswith(".amp"):
        return None  # already an AMP URL; archive call would be a no-op
    new_path = f"/amp{path}" if path.startswith("/") else f"/amp/{path}"
    return urlunsplit((parts.scheme, parts.netloc, new_path, parts.query, ""))


async def _try_archive(
    client: httpx.AsyncClient, archive_url: str, source: ArchiveSource,
) -> ArchiveResult | None:
    # SSRF guard: archive URLs are constructed from user-supplied URL inputs,
    # so a tampered or unexpected scheme/host could redirect into private
    # space. Validate before issuing the request — the host is also covered
    # by the client's request hook on every redirect.
    try:
        validate_outbound_url(archive_url)
    except URLUnreachableError:
        return None
    try:
        resp = await client.get(archive_url, follow_redirects=True, timeout=8.0)
    except Exception:
        return None
    if resp.status_code >= 400:
        return None
    body = resp.text or ""
    # Archive endpoints return a skeleton even on "not archived" — anything
    # under 800 chars is almost certainly a "not found" page.
    if len(body.strip()) < 800:
        return None
    return ArchiveResult(html=body, source=source, status_code=resp.status_code)


async def _try_reader(url: str) -> ArchiveResult | None:
    """Use trafilatura's fetch_url as a last-resort reader-view source.

    trafilatura uses its own urllib-based fetcher independent of our httpx
    client, so the validate-then-fetch SSRF guard here is the only barrier;
    skip the call outright if the URL fails validation (rare — would only
    fire if the seed URL itself was tampered after the fetcher's own check)."""
    try:
        validate_outbound_url(url)
    except URLUnreachableError:
        return None
    try:
        import trafilatura
    except Exception:
        return None
    loop = asyncio.get_running_loop()

    def _sync_fetch() -> str | None:
        try:
            return trafilatura.fetch_url(url)  # type: ignore[attr-defined]
        except Exception:
            return None

    body = await loop.run_in_executor(None, _sync_fetch)
    if not body or len(body.strip()) < 800:
        return None
    return ArchiveResult(html=body, source="reader", status_code=200)


async def fetch_from_archive(url: str) -> ArchiveResult | None:
    """Try every archive source in parallel; return the first non-empty hit.

    Returns None when every source comes up dry. The caller is expected to
    raise FetchBlockedError in that case (same behavior as before T6)."""
    tasks: list[asyncio.Task[ArchiveResult | None]] = []

    # Re-validate every redirect target via request hook; archive endpoints
    # often issue 302s and we want SSRF protection on each hop.
    async def _ssrf_hook(req: "httpx.Request") -> None:
        validate_outbound_url(str(req.url))

    async with httpx.AsyncClient(
        event_hooks={"request": [_ssrf_hook]},
    ) as client:
        tasks.append(asyncio.create_task(
            _try_archive(client, _google_cache_url(url), "google")
        ))
        tasks.append(asyncio.create_task(
            _try_archive(client, _wayback_url(url), "wayback")
        ))
        amp = _amp_url(url)
        if amp:
            tasks.append(asyncio.create_task(
                _try_archive(client, amp, "amp")
            ))
        tasks.append(asyncio.create_task(_try_reader(url)))

        # Return the first successful result; cancel the rest.
        try:
            for fut in asyncio.as_completed(tasks, timeout=12.0):
                try:
                    result = await fut
                except Exception:
                    continue
                if result is not None:
                    for t in tasks:
                        if not t.done():
                            t.cancel()
                    return result
        except asyncio.TimeoutError:
            pass
        finally:
            for t in tasks:
                if not t.done():
                    t.cancel()
    return None
