"""Optional local HTTP server.

Exposes the same extraction engine over HTTP, minus all the hosted-SaaS
concerns (no API keys, no auth, no rate limiting, no billing). Launch it with::

    runo serve            # or: uvicorn runo.server:app

Then POST to ``/v1/extract``, ``/v1/batch``, ``/v1/crawl`` with a JSON body —
no ``X-API-Key`` header required.
"""
import logging
import traceback
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from runo.config import settings
from runo.exceptions import RunoError
from runo.routes import batch, crawl, extract

logger = logging.getLogger("runo")


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not (settings.gemini_api_key or settings.gemini_api_keys):
        logger.warning(
            "No Gemini API key configured. Set GEMINI_API_KEY (or GEMINI_API_KEYS "
            "for round-robin) in your environment or .env file, or extraction "
            "will fail."
        )
    # Pre-warm Chromium so the first headless escalation doesn't pay the
    # browser-launch cost inline in a request. Safe to fail — the lazy launch
    # still works as a fallback.
    try:
        from runo.core.fetcher import prewarm_browser_async

        if settings.prewarm_browser:
            await prewarm_browser_async()
    except Exception as e:
        logger.warning("Playwright pre-warm skipped: %s", e)
    yield


app = FastAPI(
    title="Runo (local)",
    version="1.0.0",
    description="Extract structured data from any URL.",
    lifespan=lifespan,
)

# Local tool: allow any origin so a browser-based playground on any port works.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(
    request: Request, exc: StarletteHTTPException,
) -> JSONResponse:
    detail = exc.detail
    if isinstance(detail, dict) and detail.get("status") == "error" and "error" in detail:
        return JSONResponse(status_code=exc.status_code, content=detail)
    return JSONResponse(status_code=exc.status_code, content={"detail": detail})


@app.exception_handler(RunoError)
async def runo_error_handler(request: Request, exc: RunoError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "status": "error",
            "error": {
                "code": exc.code,
                "message": exc.message,
                "retryable": exc.retryable,
            },
        },
    )


@app.exception_handler(Exception)
async def generic_error_handler(request: Request, exc: Exception) -> JSONResponse:
    tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    logger.error("Unhandled exception:\n%s", tb)
    return JSONResponse(
        status_code=500,
        content={
            "status": "error",
            "error": {
                "code": "INTERNAL_ERROR",
                "message": "An unexpected error occurred.",
                "retryable": False,
            },
        },
    )


app.include_router(extract.router, prefix="/v1", tags=["extract"])
app.include_router(batch.router, prefix="/v1", tags=["batch"])
app.include_router(crawl.router, prefix="/v1", tags=["crawl"])


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}
