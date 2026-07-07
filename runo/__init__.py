"""Runo — extract structured, typed JSON from any URL with a schema you define.

Quick start::

    from runo import extract

    data = extract("https://example.com", [
        {"field": "title", "type": "string", "example": "Example Domain"},
    ])

See ``runo.api`` for the async variants and ``batch`` / ``crawl``.
"""
from runo.api import (
    batch,
    batch_async,
    crawl,
    crawl_async,
    extract,
    extract_async,
)
from runo.exceptions import RunoError

__version__ = "1.0.0"

__all__ = [
    "extract",
    "extract_async",
    "batch",
    "batch_async",
    "crawl",
    "crawl_async",
    "RunoError",
    "__version__",
]
