from typing import Any, Literal

from pydantic import BaseModel


class ErrorDetail(BaseModel):
    code: str
    message: str
    retryable: bool


class ExtractResult(BaseModel):
    url: str
    status: Literal["success", "error"]
    render_mode: Literal["plain", "headless"] | None = None
    data: dict[str, Any] | None = None
    error: ErrorDetail | None = None
    # Optional coercion warnings — populated when a raw value was present on
    # the page but could not be coerced to the declared type (the field is
    # still nulled in ``data``, consistent with the public contract).
    # Happy-path responses serialize ``"warnings": null``; only populated
    # when there is something to report, so no extra serialization work per
    # successful call.
    warnings: list[str] | None = None
    images_processed: int | None = None  # vision pass image count; omitted when not used


class BatchResult(BaseModel):
    results: list[ExtractResult]
    total: int
    succeeded: int
    failed: int
    cancelled: bool = False


class CrawlMeta(BaseModel):
    pages_visited: int
    pages_skipped: int
    pages_failed: int
    # True when the crawl exited early because the caller cancelled it via
    # DELETE /v1/jobs/{id}. Unused reservation has already been refunded.
    cancelled: bool = False


class CrawlResult(BaseModel):
    results: list[ExtractResult]
    crawl_meta: CrawlMeta
