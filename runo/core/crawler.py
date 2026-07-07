import asyncio
import fnmatch
import random
import time
from urllib import robotparser
from urllib.parse import urljoin, urlparse, urlunparse
from xml.etree import ElementTree as ET

import httpx
from bs4 import BeautifulSoup

from runo.config import settings
from runo.core import extractor, fetcher
from runo.core.field_cache import set_cached_fields
from runo.core.result_cache import get_cached, set_cached
from runo.core.schema import validate_schema
from runo.core.structured import partial_structured_prefill, try_structured_extract_async
from runo.core.url_safety import validate_outbound_url
from runo.exceptions import RunoError, URLUnreachableError


async def _crawler_ssrf_hook(request: "httpx.Request") -> None:
    """Validate every URL the crawler's helper httpx clients touch
    (robots.txt, sitemap.xml, and any redirect targets they follow)."""
    validate_outbound_url(str(request.url))
from runo.models.request import CrawlConfig, ExtractOptions, SchemaField
from runo.models.response import CrawlMeta, CrawlResult, ErrorDetail, ExtractResult
from runo.routes.extract import run_single_extract


def normalize_url(url: str) -> str:
    parsed = urlparse(url)
    path = parsed.path.rstrip("/") or "/"
    normalized = urlunparse((
        parsed.scheme.lower(),
        parsed.netloc.lower(),
        path,
        parsed.params,
        parsed.query,
        "",
    ))
    return normalized


def extract_links(html: str, base_url: str) -> list[str]:
    soup = BeautifulSoup(html, "lxml")
    links: set[str] = set()
    for a_tag in soup.find_all("a", href=True):
        href = a_tag["href"]
        absolute = urljoin(base_url, href)
        normalized = normalize_url(absolute)
        if normalized.startswith(("http://", "https://")):
            links.add(normalized)
    return list(links)


# ── Politeness state (per-crawl, not global) ─────────────────────────────


class HostPolicy:
    """Per-crawl scratch space for host rate-limit state.

    - `last_hit`: most recent request timestamp per host → used to space out
      same-host traffic by 250–750ms jitter.
    - `soft_429s`: consecutive 429/503 counts → triggers adaptive concurrency
      back-off (halves max in-flight for that host after 2 soft hits).
    - `robots`: cached RobotFileParser per host so we only fetch /robots.txt
      once per crawl.
    """

    def __init__(self) -> None:
        self.last_hit: dict[str, float] = {}
        self.soft_429s: dict[str, int] = {}
        self.host_concurrency: dict[str, int] = {}
        self.robots: dict[str, robotparser.RobotFileParser | None] = {}

    def host_of(self, url: str) -> str:
        return urlparse(url).netloc.lower()

    async def pace(self, url: str) -> None:
        host = self.host_of(url)
        now = time.monotonic()
        last = self.last_hit.get(host)
        jitter_min = settings.crawler_host_jitter_ms_min / 1000
        jitter_max = settings.crawler_host_jitter_ms_max / 1000
        if last is not None:
            elapsed = now - last
            # Back off harder if this host is throwing 429s.
            if self.soft_429s.get(host, 0) >= 2:
                jitter_min *= 2
                jitter_max *= 2
            wait = random.uniform(jitter_min, jitter_max) - elapsed
            if wait > 0:
                await asyncio.sleep(wait)
        self.last_hit[host] = time.monotonic()

    def record_outcome(self, url: str, status_code: int | None) -> None:
        host = self.host_of(url)
        if status_code in (429, 503):
            self.soft_429s[host] = self.soft_429s.get(host, 0) + 1
        elif status_code and 200 <= status_code < 400:
            # Reset on any healthy response — the host might have briefly
            # flinched under a traffic spike that has now passed.
            self.soft_429s[host] = 0

    async def robots_allows(self, url: str) -> bool:
        if not settings.crawler_respect_robots:
            return True
        host = self.host_of(url)
        if host not in self.robots:
            self.robots[host] = await _fetch_robots(url)
        rp = self.robots[host]
        if rp is None:
            return True  # fail-open on robots.txt fetch errors
        try:
            ua = "RunoScraper"
            return rp.can_fetch(ua, url)
        except Exception:
            return True


async def _fetch_robots(url: str) -> robotparser.RobotFileParser | None:
    try:
        parsed = urlparse(url)
        robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
        try:
            validate_outbound_url(robots_url)
        except URLUnreachableError:
            return None
        async with httpx.AsyncClient(
            timeout=5.0, follow_redirects=True,
            event_hooks={"request": [_crawler_ssrf_hook]},
        ) as client:
            resp = await client.get(robots_url)
            if resp.status_code != 200:
                return None
            rp = robotparser.RobotFileParser()
            rp.parse(resp.text.splitlines())
            return rp
    except Exception:
        return None


async def _seed_from_sitemap(seed_url: str, follow_pattern: str) -> list[str]:
    """Best-effort sitemap.xml seed. Empty list on any failure."""
    try:
        parsed = urlparse(seed_url)
        sitemap_url = f"{parsed.scheme}://{parsed.netloc}/sitemap.xml"
        try:
            validate_outbound_url(sitemap_url)
        except URLUnreachableError:
            return []
        async with httpx.AsyncClient(
            timeout=10.0, follow_redirects=True,
            event_hooks={"request": [_crawler_ssrf_hook]},
        ) as client:
            resp = await client.get(sitemap_url)
            if resp.status_code != 200:
                return []
        # Parse; strip default XML namespaces for simpler XPath.
        root = ET.fromstring(resp.text)
        urls: list[str] = []
        # sitemap index
        for loc in root.iter():
            tag = loc.tag.split("}")[-1]
            if tag == "loc" and loc.text:
                urls.append(loc.text.strip())
        # Filter by follow pattern (normalized), keep order.
        filtered = []
        seen: set[str] = set()
        for u in urls:
            n = normalize_url(u)
            if n in seen:
                continue
            if not fnmatch.fnmatch(n, follow_pattern):
                continue
            seen.add(n)
            filtered.append(n)
        return filtered
    except Exception:
        return []


async def _sync_crawl(
    seed_url: str,
    follow_pattern: str,
    max_pages: int,
    max_depth: int,
    schema_fields: list[SchemaField],
    options: ExtractOptions,
    crawl_cfg: CrawlConfig,
    paid_bypass_enabled: bool = False,
    owner_id: int = 0,
    cancel_event: asyncio.Event | None = None,
    on_progress=None,
) -> CrawlResult:
    visited: set[str] = set()
    results: list[ExtractResult] = []
    pages_skipped = 0
    pages_failed = 0
    cancelled = False
    policy = HostPolicy()

    queue: asyncio.Queue[tuple[str, int]] = asyncio.Queue()
    await queue.put((normalize_url(seed_url), 0))
    visited.add(normalize_url(seed_url))

    # Optional sitemap seeding: append matching URLs at depth 1.
    if crawl_cfg.use_sitemap:
        for u in await _seed_from_sitemap(seed_url, follow_pattern):
            if u in visited:
                continue
            visited.add(u)
            await queue.put((u, 1))

    while not queue.empty() and len(results) < max_pages:
        if cancel_event is not None and cancel_event.is_set():
            cancelled = True
            break
        url, depth = await queue.get()

        if not crawl_cfg.ignore_robots and not await policy.robots_allows(url):
            pages_skipped += 1
            continue

        await policy.pace(url)
        try:
            fetch_result = await fetcher.fetch_url(
                url=url, render_js=options.render_js,
                timeout_ms=options.timeout_ms, locale=options.locale,
                paid_bypass_enabled=paid_bypass_enabled,
            )
            policy.record_outcome(url, fetch_result.status_code)
        except Exception:
            policy.record_outcome(url, None)
            pages_failed += 1
            continue

        result = await run_single_extract(
            url, schema_fields, options,
            paid_bypass_enabled=paid_bypass_enabled,
            owner_id=owner_id,
        )
        results.append(result)
        if on_progress is not None:
            on_progress(len(results))
        if result.status == "error":
            pages_failed += 1

        if depth < max_depth:
            for link in extract_links(fetch_result.html, url):
                norm_link = normalize_url(link)
                if norm_link in visited:
                    pages_skipped += 1
                    continue
                if not fnmatch.fnmatch(norm_link, follow_pattern):
                    continue
                if len(visited) >= max_pages:
                    break
                visited.add(norm_link)
                await queue.put((norm_link, depth + 1))

    return CrawlResult(
        results=results,
        crawl_meta=CrawlMeta(
            pages_visited=len(results),
            pages_skipped=pages_skipped,
            pages_failed=pages_failed,
            cancelled=cancelled,
        ),
    )


async def _async_batch_crawl(
    seed_url: str,
    follow_pattern: str,
    max_pages: int,
    max_depth: int,
    schema_fields: list[SchemaField],
    options: ExtractOptions,
    crawl_cfg: CrawlConfig,
    paid_bypass_enabled: bool = False,
    owner_id: int = 0,
    cancel_event: asyncio.Event | None = None,
    on_progress=None,
) -> CrawlResult:
    """Two-pass crawl: (1) discover + fetch pages, (2) batch-LLM the remainder."""
    visited: set[str] = set()
    pages_skipped = 0
    pages_failed = 0
    cancelled = False
    use_cache = not options.no_cache
    policy = HostPolicy()

    # Ordered list of (url, early_result_or_None, (clean_text, render_mode) or None)
    discovered: list[tuple[str, ExtractResult | None, tuple[str, str] | None]] = []

    queue: asyncio.Queue[tuple[str, int]] = asyncio.Queue()
    await queue.put((normalize_url(seed_url), 0))
    visited.add(normalize_url(seed_url))

    if crawl_cfg.use_sitemap:
        for u in await _seed_from_sitemap(seed_url, follow_pattern):
            if u in visited:
                continue
            visited.add(u)
            await queue.put((u, 1))

    while not queue.empty() and len(discovered) < max_pages:
        if cancel_event is not None and cancel_event.is_set():
            cancelled = True
            break
        url, depth = await queue.get()

        if not crawl_cfg.ignore_robots and not await policy.robots_allows(url):
            pages_skipped += 1
            continue

        await policy.pace(url)

        try:
            validate_schema(schema_fields)
            if use_cache:
                cached = await get_cached(url, schema_fields, owner_id=owner_id)
                if cached is not None:
                    discovered.append((url, ExtractResult(
                        url=url, status="success",
                        render_mode=cached.get("render_mode"),
                        data=cached["data"],
                    ), None))
                    continue

            fetch_result = await fetcher.fetch_url(
                url=url, render_js=options.render_js,
                timeout_ms=options.timeout_ms, locale=options.locale,
                paid_bypass_enabled=paid_bypass_enabled,
            )
            policy.record_outcome(url, fetch_result.status_code)
        except RunoError as e:
            discovered.append((url, ExtractResult(
                url=url, status="error",
                error=ErrorDetail(code=e.code, message=e.message, retryable=e.retryable),
            ), None))
            pages_failed += 1
            policy.record_outcome(url, None)
            continue
        except Exception:
            pages_failed += 1
            policy.record_outcome(url, None)
            continue

        structured = await try_structured_extract_async(
            fetch_result.html, url, schema_fields
        )
        if structured is not None:
            if use_cache:
                await set_cached(
                    url, schema_fields,
                    {"data": structured, "render_mode": fetch_result.render_mode},
                    structured=structured,
                    owner_id=owner_id,
                )
                if settings.field_cache_enabled:
                    asyncio.ensure_future(set_cached_fields(
                        url, schema_fields, structured,
                        structured=structured, source="structured",
                    ))
            discovered.append((url, ExtractResult(
                url=url, status="success",
                render_mode=fetch_result.render_mode, data=structured,
            ), None))
        else:
            clean_text = fetcher.clean_html(
                fetch_result.html, url=url, schema_fields=schema_fields
            )
            discovered.append((url, None, (clean_text, fetch_result.render_mode)))

        if depth < max_depth:
            for link in extract_links(fetch_result.html, url):
                norm_link = normalize_url(link)
                if norm_link in visited:
                    pages_skipped += 1
                    continue
                if not fnmatch.fnmatch(norm_link, follow_pattern):
                    continue
                if len(visited) >= max_pages:
                    break
                visited.add(norm_link)
                await queue.put((norm_link, depth + 1))

    # Batch-extract the pages that need the LLM.
    llm_jobs = [(u, ct, schema_fields) for (u, done, pending) in discovered
                if done is None and pending is not None
                for (ct, _rm) in [pending]]
    llm_render_modes = [rm for (_u, done, pending) in discovered
                        if done is None and pending is not None
                        for (_ct, rm) in [pending]]
    llm_urls = [u for (u, done, pending) in discovered
                if done is None and pending is not None]

    extractions = []
    if llm_jobs:
        extractions = await extractor.extract_many_batched(llm_jobs)

    results: list[ExtractResult] = []
    ex_iter = iter(zip(llm_urls, llm_render_modes, extractions))
    for url, done, pending in discovered:
        if done is not None:
            results.append(done)
            if done.status == "error":
                pass
            continue
        u2, rm, extraction = next(ex_iter)
        if use_cache:
            await set_cached(
                u2, schema_fields,
                {"data": extraction.data, "render_mode": rm},
                owner_id=owner_id,
            )
            if settings.field_cache_enabled and extraction.data:
                any_null = any(v is None for v in extraction.data.values())
                asyncio.ensure_future(set_cached_fields(
                    u2, schema_fields, extraction.data,
                    source="llm", halve_ttl=any_null,
                ))
        results.append(ExtractResult(
            url=u2, status="success", render_mode=rm, data=extraction.data,
        ))

    pages_failed = sum(1 for r in results if r.status == "error")
    if on_progress is not None:
        on_progress(len(results))
    return CrawlResult(
        results=results,
        crawl_meta=CrawlMeta(
            pages_visited=len(results),
            pages_skipped=pages_skipped,
            pages_failed=pages_failed,
            cancelled=cancelled,
        ),
    )


async def crawl(
    seed_url: str,
    follow_pattern: str,
    max_pages: int,
    max_depth: int,
    schema_fields: list[SchemaField],
    options: ExtractOptions,
    crawl_cfg: CrawlConfig | None = None,
    paid_bypass_enabled: bool = False,
    owner_id: int = 0,
    cancel_event: asyncio.Event | None = None,
    on_progress=None,
) -> CrawlResult:
    cfg = crawl_cfg or CrawlConfig(
        follow_pattern=follow_pattern, max_pages=max_pages, max_depth=max_depth,
    )

    # Wall-clock cap: a malicious or pathological pattern (e.g. one that
    # never matches but discovers thousands of links) can otherwise hang the
    # connection until a client gives up. Caps are silent on healthy crawls.
    wall_clock = getattr(settings, "crawl_wall_clock_s", 600)

    async def _go() -> CrawlResult:
        if options.async_mode:
            try:
                return await _async_batch_crawl(
                    seed_url, follow_pattern, max_pages, max_depth,
                    schema_fields, options, cfg,
                    paid_bypass_enabled=paid_bypass_enabled,
                    owner_id=owner_id,
                    cancel_event=cancel_event,
                    on_progress=on_progress,
                )
            except Exception:
                pass
        return await _sync_crawl(
            seed_url, follow_pattern, max_pages, max_depth,
            schema_fields, options, cfg,
            paid_bypass_enabled=paid_bypass_enabled,
            owner_id=owner_id,
            cancel_event=cancel_event,
            on_progress=on_progress,
        )

    try:
        return await asyncio.wait_for(_go(), timeout=wall_clock)
    except asyncio.TimeoutError:
        return CrawlResult(
            results=[],
            crawl_meta=CrawlMeta(
                pages_visited=0,
                pages_skipped=0,
                pages_failed=0,
            ),
        )
