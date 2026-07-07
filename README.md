<p align="center">
  <img src="public/Logo_SVG_v1.0__Runo.svg" width="72" height="72" alt="Runo logo">
</p>

<h1 align="center">Runo</h1>

<p align="center">Extract structured, typed JSON from any URL using a schema you define.</p>

<p align="center">
  <img src="https://img.shields.io/badge/python-3.11%2B-2281f7?logo=python&logoColor=white" alt="Python 3.11+">
  <img src="https://img.shields.io/badge/LLM-Google%20Gemini-2281f7?logo=googlegemini&logoColor=white" alt="Powered by Google Gemini">
  <img src="https://img.shields.io/badge/runs-locally-2281f7" alt="Runs locally">
</p>

> [!NOTE]
> I'm a sole maintainer on this project.
> It started as a closed-source SaaS ([scrapewithruno.com](https://scrapewithruno.com)), but I decided to open-source it :).

---

You describe what you want (a field name, a type, and an example value) and Runo
handles the rest: fetching the page, rendering JavaScript when needed, extracting
with an LLM, and coercing every value to its declared type. You get back clean,
flat JSON.

No CSS selectors. No XPath. No post-processing. The LLM extracts by semantic
meaning, so your schema keeps working even when a site redesigns its HTML.
Fields that can't be resolved come back as `null`, never silently dropped.

This is the open-source, run-it-locally build. All you need is a Google
Gemini API key.

- **Typed JSON output.** Every field is strictly coerced: strings, integers, floats, booleans, ISO 8601 dates, and typed arrays. No ambiguous values.
- **Plain JSON schema.** Just a field name, a type, and an example. No selectors, XPath, or DSL to learn or maintain.
- **Semantic extraction.** Runo extracts by meaning, not DOM position, so site redesigns don't break your pipeline.
- **Automatic JS rendering.** Plain HTTP first, transparently escalating to a stealth headless browser only when a page is JS-gated or bot-walled.
- **Structured fast paths.** Reads JSON-LD, OpenGraph, Twitter Cards, and oEmbed first. If they already satisfy your schema, no LLM call is made at all.
- **Three modes.** Single URL, batch (one schema across many URLs), and crawl (follow links from a seed).
- **Three interfaces.** Use it as a Python library, a command-line tool, or a local HTTP server.

## How it works

1. **Fetch** the page. Plain HTTP first, escalating to a stealth headless browser only when the page is JS-gated or bot-walled.
2. **Fast path.** Pull structured data (JSON-LD, OpenGraph, Twitter Cards, oEmbed). If it fully satisfies your schema, Runo returns immediately with no LLM call.
3. **Trim.** Reduce the cleaned page text to its most relevant chunks with a BM25 pre-filter, so long pages stay cheap.
4. **Extract.** Call Google Gemini against your schema.
5. **Coerce.** Cast every value to its declared type and return flat JSON.

## Getting started

Requires **Python 3.11+**.

```bash
pip install -e ".[tls,patchright]"   # extras sharpen anti-bot fetching
playwright install chromium          # one-time browser download for JS pages
```

Then set your Gemini API key (get one at
[aistudio.google.com/apikey](https://aistudio.google.com/apikey)):

```bash
cp .env.example .env
# edit .env and set GEMINI_API_KEY=...
```

`.env` is loaded automatically. You can also just export `GEMINI_API_KEY` in
your shell.

## Usage

Everything you can do over HTTP, you can also do in code or from the terminal.

### Local HTTP server

Start the server:

```bash
runo serve                    # http://127.0.0.1:8000
```

Then send requests to it from any language:

```bash
curl -X POST http://127.0.0.1:8000/v1/extract \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://example.com",
    "schema": [
      {"field": "title",     "type": "string", "example": "Example Domain"},
      {"field": "paragraph", "type": "string", "example": "This domain is..."}
    ]
  }'
```

```json
{
  "url": "https://example.com",
  "status": "success",
  "render_mode": "fetch",
  "data": {"title": "Example Domain", "paragraph": "This domain is..."}
}
```

No API key or auth header, it's your local server. Endpoints: `/v1/extract`,
`/v1/batch`, `/v1/crawl`. Pass per-request settings in an `options` object (see
[Options](#options)).

### Python library

```python
from runo import extract

data = extract("https://example.com", [
    {"field": "title",     "type": "string", "example": "Example Domain"},
    {"field": "paragraph", "type": "string", "example": "This domain is..."},
])
print(data)   # {"title": "Example Domain", "paragraph": "..."}
```

`batch` runs one schema across many URLs; `crawl` follows links from a seed URL.
Each has an `_async` variant (`extract_async`, `batch_async`, `crawl_async`) for
use inside your own event loop.

```python
from runo import batch, crawl

rows = batch(["https://a.com", "https://b.com"], schema, concurrency=5)
site = crawl("https://blog.com", "https://blog.com/posts/*", schema, max_pages=50)
```

### Command line

```bash
runo extract https://example.com --schema schema.json
runo batch --urls urls.txt --schema schema.json --concurrency 5
runo crawl https://blog.com --pattern "https://blog.com/posts/*" --schema schema.json --max-pages 50
```

`--schema` takes a path to a JSON file or an inline JSON string. Output goes to
stdout as JSON; add `-o out.json` to write a file instead.

A `schema.json` file is just a JSON array of field objects:

```json
[
  {"field": "title", "type": "string", "example": "Example Domain"},
  {"field": "price", "type": "float",  "example": 29.99, "hint": "Use the sale price if present."}
]
```

## Schema

Each field has a `field` name, a `type`, an `example` value (a one-shot anchor
for the LLM), and an optional `hint` for edge cases.

| Type | Coercion |
|---|---|
| `string` | Always a string |
| `integer` | Parsed from text (`"35 years old"` → `35`) |
| `float` | Parsed from text (`"$1.2M"` → `1200000.0`) |
| `boolean` | Normalised (`"✓ Verified"` → `true`) |
| `date` | ISO 8601 (`YYYY-MM-DD`); relative dates resolved |
| `array<string>` / `array<integer>` / `array<float>` | JSON array |

## Options

Pass these as keyword args to the library functions, flags to the CLI, or in the
server request body's `options` object:

| Option | Default | Description |
|---|---|---|
| `render_js` | `"auto"` | `"auto"`, `"always"`, or `"never"`. Controls headless escalation. |
| `timeout_ms` | `15000` | Per-page fetch timeout. |
| `locale` | `"en-US"` | Locale hint sent with the request. |
| `no_cache` | `false` | Bypass the in-memory result cache. |
| `process_images` | `false` | Run a Gemini vision pass to fill null fields from page images (extra tokens). |

## Configuration

Everything is driven by environment variables (or `.env`). Only `GEMINI_API_KEY`
is required; see [`.env.example`](.env.example) for the documented tunables,
including `GEMINI_API_KEYS` for round-robin across several keys, `HEADLESS_ENGINE`,
`TLS_IMPERSONATE`, and `SSRF_GUARD_ENABLED`.

## Limitations

- **JS-heavy sites need the browser.** Plain-HTML pages work with just `pip install`, but pages that render content with JavaScript need `playwright install chromium`. Without it, those pages come back empty.
- **Hard anti-bot walls may fail.** The hosted version of Runo adds paid CAPTCHA solving and residential proxies; those are not part of this build. Aggressively protected sites (some Cloudflare/Datadome setups, large retail like Amazon/Walmart) can return `FETCH_BLOCKED`.
- **Caching is in-memory.** Results and per-field values are cached within the running process to avoid repeat LLM calls, but the cache resets on restart.
- **You pay Google for tokens.** Extraction quality and cost track whatever Gemini model is configured (Flash-Lite by default).

## License

[Apache License 2.0](LICENSE).
