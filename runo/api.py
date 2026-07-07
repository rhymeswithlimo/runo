"""Public programmatic API for Runo.

    from runo import extract

    data = extract("https://example.com", [
        {"field": "title", "type": "string", "example": "Example Domain"},
    ])

Each schema entry is a plain dict with ``field``, ``type``, ``example`` and an
optional ``hint``. Supported types: ``string``, ``integer``, ``float``,
``boolean``, ``date``, ``array<string>``, ``array<integer>``, ``array<float>``.

``extract`` returns the extracted data dict and raises ``RunoError`` on failure.
``batch`` and ``crawl`` return per-URL result dicts and do not raise on a single
page's failure (inspect each result's ``status``/``error``). Every function has
an ``*_async`` counterpart for use inside your own event loop.
"""
from __future__ import annotations

import asyncio
from typing import Any

from runo._runtime import run_sync
from runo.core.crawler import crawl as _core_crawl
from runo.exceptions import RunoError, SchemaInvalidError
from runo.models.request import CrawlConfig, ExtractOptions, SchemaField
from runo.routes.extract import run_single_extract

Schema = list[dict[str, Any]]

_OPTION_KEYS = {
    "render_js", "locale", "timeout_ms", "no_cache", "async_mode", "process_images",
}


def _fields(schema: Schema) -> list[SchemaField]:
    if not schema:
        raise SchemaInvalidError("A non-empty schema is required.")
    try:
        return [f if isinstance(f, SchemaField) else SchemaField(**f) for f in schema]
    except SchemaInvalidError:
        raise
    except Exception as e:
        raise SchemaInvalidError(f"Invalid schema field: {e}") from e


def _options(opts: dict[str, Any]) -> ExtractOptions:
    return ExtractOptions(
        **{k: v for k, v in opts.items() if k in _OPTION_KEYS and v is not None}
    )


# ── extract ──────────────────────────────────────────────────────────────────

async def extract_async(url: str, schema: Schema, **options: Any) -> dict:
    fields = _fields(schema)
    opts = _options(options)
    result = await run_single_extract(
        url=url,
        schema_fields=fields,
        options=opts,
        use_cache=not opts.no_cache,
        process_images_enabled=opts.process_images,
    )
    if result.status == "error" and result.error:
        raise RunoError(
            result.error.code, result.error.message, result.error.retryable,
        )
    return result.data or {}


def extract(url: str, schema: Schema, **options: Any) -> dict:
    return run_sync(extract_async(url, schema, **options))


# ── batch ────────────────────────────────────────────────────────────────────

async def batch_async(
    urls: list[str], schema: Schema, *, concurrency: int = 5, **options: Any,
) -> list[dict]:
    fields = _fields(schema)
    opts = _options(options)
    sem = asyncio.Semaphore(max(1, concurrency))

    async def _one(url: str) -> dict:
        async with sem:
            result = await run_single_extract(
                url=url,
                schema_fields=fields,
                options=opts,
                use_cache=not opts.no_cache,
                process_images_enabled=opts.process_images,
            )
            return result.model_dump(exclude_none=True)

    return list(await asyncio.gather(*(_one(u) for u in urls)))


def batch(
    urls: list[str], schema: Schema, *, concurrency: int = 5, **options: Any,
) -> list[dict]:
    return run_sync(batch_async(urls, schema, concurrency=concurrency, **options))


# ── crawl ────────────────────────────────────────────────────────────────────

async def crawl_async(
    seed_url: str,
    follow_pattern: str,
    schema: Schema,
    *,
    max_pages: int = 50,
    max_depth: int = 2,
    use_sitemap: bool = False,
    ignore_robots: bool = False,
    **options: Any,
) -> dict:
    fields = _fields(schema)
    opts = _options(options)
    cfg = CrawlConfig(
        follow_pattern=follow_pattern,
        max_pages=max_pages,
        max_depth=max_depth,
        use_sitemap=use_sitemap,
        ignore_robots=ignore_robots,
    )
    result = await _core_crawl(
        seed_url=seed_url,
        follow_pattern=follow_pattern,
        max_pages=max_pages,
        max_depth=max_depth,
        schema_fields=fields,
        options=opts,
        crawl_cfg=cfg,
    )
    return result.model_dump(exclude_none=True)


def crawl(seed_url: str, follow_pattern: str, schema: Schema, **kwargs: Any) -> dict:
    return run_sync(crawl_async(seed_url, follow_pattern, schema, **kwargs))
