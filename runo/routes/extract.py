import asyncio
import re

from fastapi import APIRouter

from runo.config import settings
from runo.core import extractor, fetcher
from runo.core.cleaned_text_cache import (
    get_cleaned as _get_cleaned_text,
    hash_html as _hash_html,
    set_cleaned as _set_cleaned_text,
)
from runo.core.field_cache import get_cached_fields, set_cached_fields
from runo.core.result_cache import get_cached, set_cached
from runo.core.schema import validate_schema
from runo.core.structured import (
    collect_structured,
    partial_structured_prefill,  # noqa: F401  (kept for monkeypatch compatibility)
    try_structured_extract,  # noqa: F401  (kept as a module attribute for test monkeypatch compatibility)
    try_structured_extract_async,  # noqa: F401  (kept for monkeypatch compatibility)
)
from runo.exceptions import RunoError
from runo.models.request import ExtractOptions, ExtractRequest, SchemaField
from runo.models.response import ErrorDetail, ExtractResult

router = APIRouter()


async def run_single_extract(
    url: str,
    schema_fields: list[SchemaField],
    options: ExtractOptions,
    use_cache: bool = True,
    static_key_id: int | None = None,
    telemetry: dict | None = None,
    paid_bypass_enabled: bool = False,
    process_images_enabled: bool = False,
    owner_id: int = 0,
    cancel_event: asyncio.Event | None = None,
) -> ExtractResult:
    def _note(**kv) -> None:
        if telemetry is not None:
            telemetry.update(kv)

    def _cancelled_result() -> ExtractResult:
        return ExtractResult(
            url=url, status="error",
            error=ErrorDetail(
                code="JOB_CANCELLED",
                message="Cancelled by DELETE /v1/jobs.",
                retryable=False,
            ),
        )

    try:
        if cancel_event is not None and cancel_event.is_set():
            return _cancelled_result()

        validate_schema(schema_fields)

        if use_cache:
            cached = await get_cached(url, schema_fields, owner_id=owner_id)
            if cached is not None:
                _note(fast_path="cache", render_mode=cached.get("render_mode"))
                return ExtractResult(
                    url=url,
                    status="success",
                    render_mode=cached.get("render_mode"),
                    data=cached["data"],
                )

        # Field-level cache lookup runs concurrently with the fetch+parse
        # pipeline so it adds zero wall-time on miss. Awaited after structured
        # so hits seed the prefilled dict alongside structured-data prefills.
        field_cache_task: asyncio.Task | None = (
            asyncio.create_task(get_cached_fields(url, schema_fields))
            if use_cache and settings.field_cache_enabled else None
        )

        has_numeric = any(
            f.type in ("integer", "float") or f.type.startswith("array<int")
            or f.type.startswith("array<float")
            for f in schema_fields
        )
        fetch_result = await fetcher.fetch_url(
            url=url,
            render_js=options.render_js,
            timeout_ms=options.timeout_ms,
            locale=options.locale,
            has_numeric=has_numeric,
            paid_bypass_enabled=paid_bypass_enabled,
        )
        _note(render_mode=fetch_result.render_mode)
        if fetch_result.fetch_source:
            _note(fetch_source=fetch_result.fetch_source)

        # Spawn all CPU/IO support tasks immediately in parallel. clean_html,
        # canonical extraction, and candidate descriptions all operate on the
        # raw HTML independently of each other and of structured parsing — no
        # reason to serialize them. Tasks that aren't needed (fast paths) are
        # cancelled before they contribute results.
        structured_task = asyncio.create_task(
            collect_structured(
                fetch_result.html, url, schema_fields,
                response_headers=fetch_result.response_headers,
            )
        )

        # Content-addressable cache for cleaned text. Hash is computed in the
        # same worker thread as clean_html so the happy path (miss) pays only
        # the hash; on a hit, the heavy trafilatura+strippers pass is skipped
        # entirely.
        async def _clean_with_cache() -> str:
            html_hash = _hash_html(fetch_result.html)
            cached_text = await _get_cleaned_text(html_hash) if html_hash else None
            if cached_text is not None:
                return cached_text
            cleaned = await asyncio.to_thread(
                fetcher.clean_html,
                fetch_result.html,
                url=url,
                schema_fields=schema_fields,
            )
            # Fire-and-forget write; failures here cost nothing to the caller.
            if html_hash and cleaned:
                asyncio.ensure_future(_set_cleaned_text(html_hash, cleaned))
            return cleaned

        clean_task: asyncio.Task = asyncio.create_task(_clean_with_cache())
        _want_canonical = settings.canonical_preamble_enabled
        _want_candidates = (
            settings.description_candidates_enabled
            and fetcher._schema_has_description_field(schema_fields)
        )
        canonical_task: asyncio.Task | None = (
            asyncio.create_task(
                asyncio.to_thread(fetcher.extract_canonical_signals, fetch_result.html, url)
            ) if _want_canonical else None
        )
        candidates_task: asyncio.Task | None = (
            asyncio.create_task(
                asyncio.to_thread(fetcher.extract_description_candidates, fetch_result.html)
            ) if _want_candidates else None
        )

        def _cancel_support_tasks() -> None:
            clean_task.cancel()
            if canonical_task is not None:
                canonical_task.cancel()
            if candidates_task is not None:
                candidates_task.cancel()

        # Wait for structured with a short guard. shield() keeps it alive so
        # its result is still usable after the timeout.
        try:
            full_match, prefilled = await asyncio.wait_for(
                asyncio.shield(structured_task), timeout=0.5
            )
        except asyncio.TimeoutError:
            full_match, prefilled = await structured_task

        # Resolve field-cache result now (it ran concurrently with fetch).
        # Merged into prefilled below, after the partial_structured_fallback
        # gate so the two cache layers can be toggled independently.
        if field_cache_task is not None:
            try:
                field_prefilled = await field_cache_task
            except Exception:
                field_prefilled = {}
        else:
            field_prefilled = {}

        # Structured fast path (JSON-LD, microdata, OG, Twitter Cards, oEmbed).
        # If all fields resolve, skip LLM entirely.
        if full_match is not None:
            _cancel_support_tasks()
            if use_cache:
                await set_cached(
                    url, schema_fields,
                    {"data": full_match, "render_mode": fetch_result.render_mode},
                    structured=full_match,
                    owner_id=owner_id,
                )
                if settings.field_cache_enabled:
                    asyncio.ensure_future(set_cached_fields(
                        url, schema_fields, full_match,
                        structured=full_match, source="structured",
                    ))
            _note(fast_path="structured")
            return ExtractResult(
                url=url,
                status="success",
                render_mode=fetch_result.render_mode,
                data=full_match,
            )

        # Partial structured: let the LLM fill only the missing fields.
        # ``partial_structured_fallback`` only gates the *structured* prefills;
        # field-cache prefills flow through independently so the two cache
        # layers stay toggleable on their own.
        if not settings.partial_structured_fallback:
            prefilled = {}

        if field_prefilled:
            prefilled = {**field_prefilled, **(prefilled or {})}
            _note(
                field_cache_hits=len(field_prefilled),
                field_cache_attempted=len(schema_fields),
            )

        # Partial-structured short-circuit: if structured markup already
        # covered ≥ threshold of the schema AND none of the remaining fields
        # are critical (price/stock/sku/id), skip the LLM call and null the
        # rest. Pure latency + cost win on the ~5–8% of pages where OG /
        # JSON-LD covers almost everything.
        if (prefilled and schema_fields
                and settings.structured_partial_skip_threshold > 0):
            coverage = len(prefilled) / len(schema_fields)
            remaining = [f for f in schema_fields if f.field not in prefilled]
            critical_re = re.compile(
                r"\b(price|stock|sku|availability|in_stock|inventory|id|identifier)\b",
                re.IGNORECASE,
            )
            has_critical_remaining = any(
                critical_re.search(f.field) for f in remaining
            )
            if (coverage >= settings.structured_partial_skip_threshold
                    and not has_critical_remaining):
                _cancel_support_tasks()
                data = dict(prefilled)
                for f in remaining:
                    data[f.field] = None
                if use_cache:
                    await set_cached(
                        url, schema_fields,
                        {"data": data, "render_mode": fetch_result.render_mode},
                        structured=prefilled,
                        owner_id=owner_id,
                    )
                    if settings.field_cache_enabled:
                        # Only the resolved fields; never write the nulled-out
                        # `remaining` keys.
                        asyncio.ensure_future(set_cached_fields(
                            url, schema_fields, dict(prefilled),
                            structured=prefilled, source="partial",
                        ))
                _note(fast_path="partial_skip",
                      prefilled_count=len(prefilled))
                return ExtractResult(
                    url=url,
                    status="success",
                    render_mode=fetch_result.render_mode,
                    data=data,
                )

        # LLM path: gather all pre-computed support results (already running).
        support_tasks = [t for t in [canonical_task, candidates_task] if t is not None]
        support_results = await asyncio.gather(clean_task, *support_tasks)
        clean_text: str = support_results[0]

        canonical_block: str | None = None
        candidates_block: str | None = None
        _idx = 1
        if canonical_task is not None:
            canonical_block = support_results[_idx] or None
            _idx += 1
        if candidates_task is not None:
            candidates_block = support_results[_idx] or None

        # Only forward optional kwargs when set — some tests monkeypatch
        # `extract` with an older signature.
        extract_kw: dict = {}
        if prefilled:
            extract_kw["prefilled"] = prefilled
        if canonical_block:
            extract_kw["canonical"] = canonical_block
        if candidates_block:
            extract_kw["candidates"] = candidates_block

        # Archive callable: when the null-rate Flash fallback fires, redirect
        # it to T6 archive content instead of retrying the same useless text.
        # Only built when archive is enabled; only invoked if Lite returns nulls.
        if settings.archive_fallback_enabled:
            _url = url
            _schema = schema_fields
            async def _get_archive_text() -> str | None:
                from runo.core.archive import fetch_from_archive
                archived = await fetch_from_archive(_url)
                if archived is None or not archived.html:
                    return None
                return await asyncio.to_thread(
                    fetcher.clean_html, archived.html, _url, _schema
                )
            extract_kw["get_fallback_text"] = _get_archive_text

        if len(clean_text) < 300:
            extract_kw["allow_fallback"] = False

        if cancel_event is not None and cancel_event.is_set():
            return _cancelled_result()

        extraction = await extractor.extract(
            clean_text, schema_fields,
            static_key_id=static_key_id,
            **extract_kw,
        )
        if prefilled:
            _note(fast_path="partial", prefilled_count=len(prefilled))
        else:
            _note(fast_path="llm")

        # Per-call cost telemetry — captured so stress/bench harnesses can
        # measure real spend instead of multiplying a flat rate by call
        # count. Surfaced via x-runo-* headers in middleware.
        _note(
            llm_model=extraction.model,
            llm_tokens_total=extraction.tokens_used,
            llm_tokens_input=extraction.tokens_input,
            llm_tokens_output=extraction.tokens_output,
            llm_tokens_thoughts=extraction.tokens_thoughts,
            llm_tokens_cached=extraction.tokens_cached,
            llm_fallback_fired=extraction.fallback_fired,
            llm_truncation_retries=extraction.truncation_retries,
        )

        # Image-augmentation pass: fill remaining nulls via vision (Scale only).
        images_processed: int | None = None
        if process_images_enabled and extraction.data:
            null_names = [
                f.field for f in schema_fields
                if extraction.data.get(f.field) is None
            ]
            if null_names:
                from runo.core.image_processor import (
                    extract_image_candidates,
                    fetch_candidates,
                )
                candidates = extract_image_candidates(
                    fetch_result.html, url, null_names
                )
                if candidates:
                    image_parts = await fetch_candidates(candidates)
                    if image_parts:
                        extraction = await extractor.augment_with_images(
                            clean_text, schema_fields, extraction,
                            image_parts, prefilled or {},
                        )
                        images_processed = len(image_parts)
                        _note(images_processed=images_processed)

        if use_cache:
            await set_cached(
                url, schema_fields,
                {"data": extraction.data, "render_mode": fetch_result.render_mode},
                owner_id=owner_id,
            )
            if settings.field_cache_enabled and extraction.data:
                any_null = any(v is None for v in extraction.data.values())
                asyncio.ensure_future(set_cached_fields(
                    url, schema_fields, extraction.data,
                    source="llm", halve_ttl=any_null,
                ))

        # Null ratio for observability.
        if extraction.data:
            nulls = sum(1 for v in extraction.data.values() if v is None)
            _note(null_ratio=round(nulls / max(1, len(extraction.data)), 3))

        return ExtractResult(
            url=url,
            status="success",
            render_mode=fetch_result.render_mode,
            data=extraction.data,
            warnings=(
                extraction.warnings
                if settings.llm_emit_warnings and extraction.warnings
                else None
            ),
            images_processed=images_processed,
        )
    except RunoError as e:
        _note(fast_path="error", error_code=e.code)
        # Surface Retry-After on LLM rate-limit terminal failures so clients
        # can honor the hint if Gemini returned one.
        retry_after = getattr(e, "retry_after_s", None)
        if retry_after is not None and telemetry is not None:
            telemetry["retry_after_s"] = retry_after
        return ExtractResult(
            url=url,
            status="error",
            error=ErrorDetail(
                code=e.code,
                message=e.message,
                retryable=e.retryable,
            ),
        )


@router.post("/extract", response_model=ExtractResult)
async def extract_endpoint(request: ExtractRequest) -> ExtractResult:
    return await run_single_extract(
        url=str(request.url),
        schema_fields=request.schema_ or [],
        options=request.options,
        use_cache=not request.options.no_cache,
        process_images_enabled=request.options.process_images,
    )
