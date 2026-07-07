"""Command-line interface for Runo.

    runo extract <url> --schema schema.json
    runo batch --urls urls.txt --schema schema.json
    runo crawl <seed_url> --pattern "https://site.com/*" --schema schema.json
    runo serve --host 127.0.0.1 --port 8000

``--schema`` accepts a path to a JSON file or an inline JSON string. The schema
is a JSON array of field objects, e.g.::

    [{"field": "title", "type": "string", "example": "Example Domain"}]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from runo import batch as _batch
from runo import crawl as _crawl
from runo import extract as _extract
from runo.exceptions import RunoError


def _load_schema(value: str) -> list[dict[str, Any]]:
    """Load a schema from a file path or an inline JSON string."""
    text = value
    p = Path(value)
    if p.exists():
        text = p.read_text(encoding="utf-8")
    try:
        schema = json.loads(text)
    except json.JSONDecodeError as e:
        raise SystemExit(f"error: --schema is not valid JSON ({e})")
    if not isinstance(schema, list):
        raise SystemExit("error: schema must be a JSON array of field objects")
    return schema


def _load_urls(value: str) -> list[str]:
    """Load URLs from a file (one per line) or a comma-separated string."""
    p = Path(value)
    if p.exists():
        lines = p.read_text(encoding="utf-8").splitlines()
    else:
        lines = value.split(",")
    urls = [u.strip() for u in lines if u.strip() and not u.strip().startswith("#")]
    if not urls:
        raise SystemExit("error: no URLs found")
    return urls


def _emit(result: Any, output: str | None) -> None:
    text = json.dumps(result, indent=2, ensure_ascii=False, default=str)
    if output:
        Path(output).write_text(text, encoding="utf-8")
        print(f"wrote {output}")
    else:
        print(text)


def _add_common(sp: argparse.ArgumentParser) -> None:
    sp.add_argument("--schema", required=True, help="Path to a JSON schema file or inline JSON")
    sp.add_argument("--render-js", choices=["auto", "always", "never"], default="auto")
    sp.add_argument("--timeout-ms", type=int, default=15000)
    sp.add_argument("--locale", default="en-US")
    sp.add_argument("--no-cache", action="store_true", help="Bypass the in-memory result cache")
    sp.add_argument("-o", "--output", help="Write JSON output to this file instead of stdout")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="runo", description="Extract typed JSON from any URL.")
    sub = parser.add_subparsers(dest="command", required=True)

    p_extract = sub.add_parser("extract", help="Extract from a single URL")
    p_extract.add_argument("url")
    _add_common(p_extract)
    p_extract.add_argument("--process-images", action="store_true",
                           help="Run a vision pass to fill null fields from page images")

    p_batch = sub.add_parser("batch", help="Extract from many URLs with one schema")
    p_batch.add_argument("--urls", required=True, help="Path to a URL list (one per line) or comma-separated URLs")
    _add_common(p_batch)
    p_batch.add_argument("--concurrency", type=int, default=5)

    p_crawl = sub.add_parser("crawl", help="Crawl from a seed URL following a link pattern")
    p_crawl.add_argument("seed_url")
    p_crawl.add_argument("--pattern", required=True, help="Glob for links to follow, e.g. https://site.com/*")
    _add_common(p_crawl)
    p_crawl.add_argument("--max-pages", type=int, default=50)
    p_crawl.add_argument("--max-depth", type=int, default=2)
    p_crawl.add_argument("--use-sitemap", action="store_true")
    p_crawl.add_argument("--ignore-robots", action="store_true")

    p_serve = sub.add_parser("serve", help="Run the local HTTP server")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8000)
    p_serve.add_argument("--reload", action="store_true")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.command == "serve":
        import uvicorn
        uvicorn.run("runo.server:app", host=args.host, port=args.port, reload=args.reload)
        return 0

    common = dict(
        render_js=args.render_js,
        timeout_ms=args.timeout_ms,
        locale=args.locale,
        no_cache=args.no_cache,
    )
    schema = _load_schema(args.schema)

    try:
        if args.command == "extract":
            result = _extract(args.url, schema, process_images=args.process_images, **common)
        elif args.command == "batch":
            urls = _load_urls(args.urls)
            result = _batch(urls, schema, concurrency=args.concurrency, **common)
        elif args.command == "crawl":
            result = _crawl(
                args.seed_url, args.pattern, schema,
                max_pages=args.max_pages, max_depth=args.max_depth,
                use_sitemap=args.use_sitemap, ignore_robots=args.ignore_robots,
                **common,
            )
        else:  # pragma: no cover - argparse enforces choices
            raise SystemExit(f"unknown command: {args.command}")
    except RunoError as e:
        print(json.dumps({"status": "error", "error": {
            "code": e.code, "message": e.message, "retryable": e.retryable,
        }}, indent=2), file=sys.stderr)
        return 1

    _emit(result, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
