import asyncio

from fastapi import APIRouter

from runo.config import settings
from runo.core import extractor, fetcher
from runo.core.field_cache import set_cached_fields
from runo.core.result_cache import get_cached, set_cached
from runo.core.schema import validate_schema
from runo.core.structured import try_structured_extract_async
from runo.exceptions import RunoError
from runo.models.request import BatchRequest, ExtractOptions, SchemaField
from runo.models.response import BatchResult, ErrorDetail, ExtractResult
from runo.routes.extract import run_single_extract

router = APIRouter()


async def _batch_prefetch(
    url: str,
    schema_fields: list[SchemaField],
    options: ExtractOptions,
    use_cache: bool,
    paid_bypass_enabled: bool = False,
    owner_id: int = 0,
) -> tuple[str, ExtractResult | None, str | None]:
    """Run cache + fetch + structured-data fast path for one URL.

    Returns (url, finished_result_or_None, clean_text_or_None).
    - finished_result set: cache hit or structured-data hit or error → don't LLM.
    - clean_text set: need LLM extraction on this text.
    """
    try:
        validate_schema(schema_fields)
        if use_cache:
            cached = await get_cached(url, schema_fields, owner_id=owner_id)
            if cached is not None:
                return url, ExtractResult(
                    url=url, status="success",
                    render_mode=cached.get("render_mode"),
                    data=cached["data"],
                ), None

        fetch_result = await fetcher.fetch_url(
            url=url, render_js=options.render_js, timeout_ms=options.timeout_ms,
            locale=options.locale,
            paid_bypass_enabled=paid_bypass_enabled,
        )
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
            return url, ExtractResult(
                url=url, status="success",
                render_mode=fetch_result.render_mode, data=structured,
            ), None

        clean_text = fetcher.clean_html(
            fetch_result.html, url=url, schema_fields=schema_fields
        )
        # Stash render_mode on the return tuple by packing into a tuple.
        return url, None, (clean_text, fetch_result.render_mode)  # type: ignore[return-value]

    except RunoError as e:
        return url, ExtractResult(
            url=url, status="error",
            error=ErrorDetail(code=e.code, message=e.message, retryable=e.retryable),
        ), None


async def _run_async_batch(
    urls: list[str],
    schema_fields: list[SchemaField],
    options: ExtractOptions,
    use_cache: bool,
    concurrency: int,
    paid_bypass_enabled: bool = False,
    owner_id: int = 0,
    cancel_event: asyncio.Event | None = None,
) -> list[ExtractResult]:
    """Batch-mode execution: fan out prefetch, submit LLM-bound URLs as one job."""
    semaphore = asyncio.Semaphore(concurrency)

    async def _bounded_prefetch(u: str):
        if cancel_event is not None and cancel_event.is_set():
            return (u, ExtractResult(
                url=u, status="error",
                error=ErrorDetail(
                    code="JOB_CANCELLED",
                    message="Cancelled by DELETE /v1/jobs.",
                    retryable=False,
                ),
            ), None)
        async with semaphore:
            if cancel_event is not None and cancel_event.is_set():
                return (u, ExtractResult(
                    url=u, status="error",
                    error=ErrorDetail(
                        code="JOB_CANCELLED",
                        message="Cancelled by DELETE /v1/jobs.",
                        retryable=False,
                    ),
                ), None)
            return await _batch_prefetch(
                u, schema_fields, options, use_cache,
                paid_bypass_enabled=paid_bypass_enabled,
                owner_id=owner_id,
            )

    prefetched = await asyncio.gather(*(_bounded_prefetch(u) for u in urls))

    # Split into done vs. needs-LLM in original order.
    results: list[ExtractResult | None] = [None] * len(urls)
    llm_indices: list[int] = []
    llm_inputs: list[tuple[str, str, list[SchemaField]]] = []
    llm_render_modes: list[str] = []

    for i, (url, done, pending) in enumerate(prefetched):
        if done is not None:
            results[i] = done
            continue
        clean_text, render_mode = pending  # type: ignore[misc]
        llm_indices.append(i)
        llm_inputs.append((url, clean_text, schema_fields))
        llm_render_modes.append(render_mode)

    if llm_inputs:
        extractions = await extractor.extract_many_batched(llm_inputs)
        for idx, render_mode, extraction, (url, _clean, _fields) in zip(
            llm_indices, llm_render_modes, extractions, llm_inputs
        ):
            if use_cache:
                await set_cached(
                    url, schema_fields,
                    {"data": extraction.data, "render_mode": render_mode},
                    owner_id=owner_id,
                )
                if settings.field_cache_enabled and extraction.data:
                    any_null = any(v is None for v in extraction.data.values())
                    asyncio.ensure_future(set_cached_fields(
                        url, schema_fields, extraction.data,
                        source="llm", halve_ttl=any_null,
                    ))
            results[idx] = ExtractResult(
                url=url, status="success",
                render_mode=render_mode, data=extraction.data,
                warnings=(
                    extraction.warnings
                    if settings.llm_emit_warnings and extraction.warnings
                    else None
                ),
            )

    return [r for r in results if r is not None]


@router.post("/batch", response_model=BatchResult)
async def batch_endpoint(request: BatchRequest) -> BatchResult:
    schema_fields = request.schema_ or []
    validate_schema(schema_fields)

    extract_options = ExtractOptions(
        render_js=request.options.render_js,
        locale=request.options.locale,
        timeout_ms=request.options.timeout_ms,
        no_cache=request.options.no_cache,
    )
    urls = [str(u) for u in request.urls]
    use_cache = not request.options.no_cache

    if request.options.async_mode:
        results = await _run_async_batch(
            urls, schema_fields, extract_options, use_cache,
            request.options.concurrency,
        )
    else:
        semaphore = asyncio.Semaphore(request.options.concurrency)
        failed_flag = asyncio.Event()

        async def _extract_one(url: str) -> ExtractResult:
            if request.options.fail_fast and failed_flag.is_set():
                return ExtractResult(
                    url=url, status="error",
                    error=ErrorDetail(
                        code="BATCH_CANCELLED",
                        message="Cancelled due to fail_fast after a prior error.",
                        retryable=False,
                    ),
                )
            async with semaphore:
                result = await run_single_extract(
                    url, schema_fields, extract_options, use_cache=use_cache,
                )
                if result.status == "error" and request.options.fail_fast:
                    failed_flag.set()
                return result

        results = await asyncio.gather(*(_extract_one(u) for u in urls))

    succeeded = sum(1 for r in results if r.status == "success")
    failed = len(results) - succeeded

    return BatchResult(
        results=list(results), total=len(results),
        succeeded=succeeded, failed=failed,
        cancelled=False,
    )
