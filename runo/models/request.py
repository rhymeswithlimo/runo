from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


VALID_FIELD_TYPES = {
    "string",
    "integer",
    "float",
    "boolean",
    "date",
    "array<string>",
    "array<integer>",
    "array<float>",
}


# Per-request structural caps. These guard against memory-inflation attacks
# where a Free-tier caller POSTs a multi-megabyte schema before any quota
# deduction. Limits are well above any legitimate use case the docs advertise
# (max field count published in the schema design notes is < 50).
MAX_SCHEMA_FIELDS = 200
MAX_FIELD_NAME_LEN = 200
MAX_HINT_LEN = 1000
MAX_BATCH_URLS = 1000
MAX_CRAWL_PAGES = 10_000


class SchemaField(BaseModel):
    field: str = Field(min_length=1, max_length=MAX_FIELD_NAME_LEN)
    type: str = Field(min_length=1, max_length=64)
    # ``example`` is unstructured and user-supplied — Pydantic accepts ``Any``,
    # but we serialise it later, so reject obvious memory-bombs at the type
    # boundary. ``model_dump`` later turns it into JSON, where the size is
    # bounded by the body-size cap configured at the ASGI layer.
    example: Any = None
    hint: str | None = Field(default=None, max_length=MAX_HINT_LEN)


class ExtractOptions(BaseModel):
    render_js: Literal["auto", "always", "never"] = "auto"
    locale: str = "en-US"
    timeout_ms: int = 15000
    no_cache: bool = False
    async_mode: bool = False  # Crawl only: Gemini Batch API for LLM extractions
    process_images: bool = False  # Scale tier only; vision pass fills null fields


class ExtractRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    url: HttpUrl
    schema_: list[SchemaField] | None = Field(
        default=None, alias="schema", max_length=MAX_SCHEMA_FIELDS,
    )
    options: ExtractOptions = ExtractOptions()


class BatchOptions(BaseModel):
    render_js: Literal["auto", "always", "never"] = "auto"
    locale: str = "en-US"
    timeout_ms: int = 15000
    concurrency: int = 5
    fail_fast: bool = False
    no_cache: bool = False
    async_mode: bool = False  # Gemini Batch API: 50% discount, up to 24h latency


class BatchRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    urls: list[HttpUrl] = Field(min_length=1, max_length=MAX_BATCH_URLS)
    schema_: list[SchemaField] | None = Field(
        default=None, alias="schema", max_length=MAX_SCHEMA_FIELDS,
    )
    options: BatchOptions = BatchOptions()


class CrawlConfig(BaseModel):
    follow_pattern: str = Field(max_length=512)
    max_pages: int = Field(default=50, ge=1, le=MAX_CRAWL_PAGES)
    max_depth: int = Field(default=2, ge=0, le=20)
    use_sitemap: bool = False      # seed from sitemap.xml when available
    ignore_robots: bool = False    # bypass robots.txt Disallow rules
    allow_large_crawl: bool = False  # bypass 25% quota ceiling


class CrawlRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    seed_url: HttpUrl
    schema_: list[SchemaField] | None = Field(
        default=None, alias="schema", max_length=MAX_SCHEMA_FIELDS,
    )
    crawl: CrawlConfig
    options: ExtractOptions = ExtractOptions()
