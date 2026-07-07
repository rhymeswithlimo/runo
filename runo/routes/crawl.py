from fastapi import APIRouter, HTTPException

from runo.config import settings
from runo.core.crawler import crawl
from runo.core.schema import validate_schema
from runo.models.request import CrawlRequest
from runo.models.response import CrawlResult

router = APIRouter()


@router.post("/crawl", response_model=CrawlResult)
async def crawl_endpoint(body: CrawlRequest) -> CrawlResult:
    schema_fields = body.schema_ or []
    validate_schema(schema_fields)

    # Reject pathological follow_patterns. fnmatch is linear per match but
    # gets called against every discovered link in the crawl; a high wildcard
    # count combined with thousands of links is a quiet way to burn time.
    pattern = body.crawl.follow_pattern or ""
    if (
        len(pattern) > settings.follow_pattern_max_len
        or pattern.count("*") > settings.follow_pattern_max_wildcards
    ):
        raise HTTPException(
            status_code=400,
            detail={
                "status": "error",
                "error": {
                    "code": "SCHEMA_INVALID",
                    "message": "follow_pattern is too long or has too many wildcards.",
                    "retryable": False,
                },
            },
        )

    return await crawl(
        seed_url=str(body.seed_url),
        follow_pattern=body.crawl.follow_pattern,
        max_pages=body.crawl.max_pages,
        max_depth=body.crawl.max_depth,
        schema_fields=schema_fields,
        options=body.options,
        crawl_cfg=body.crawl,
    )
