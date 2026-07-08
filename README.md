<p align="center">
  <img src="public/Logo_SVG_v1.0__Runo.svg" width="72" height="72" alt="Runo logo">
</p>

<h1 align="center">Runo</h1>

<p align="center">Extract structured, typed JSON from any URL using a schema you define.</p>

<p align="center">
  <img src="https://img.shields.io/badge/python-3.11%2B-2281f7?logo=python&logoColor=white" alt="Python 3.11+">
  <img src="https://img.shields.io/badge/LLM-Google%20Gemini-2281f7?logo=googlegemini&logoColor=white" alt="Powered by Google Gemini">
  <img src="https://img.shields.io/badge/runs-locally-2281f7" alt="Runs locally">
  <img src="https://img.shields.io/badge/license-Apache--2.0-2281f7" alt="Apache-2.0">
</p>

> [!NOTE]
> I'm a sole maintainer on this project.
> I built it and open-sourced it so anyone can run it locally :).

---

You describe what you want (a field name, a type, an example value) and Runo fetches the page, renders JavaScript if the site needs it, extracts the data with an LLM, and coerces every value to the type you asked for. You get clean, flat JSON back.
 
No selectors, no XPath, nothing to maintain. Since the LLM reads for meaning instead of DOM position, your schema doesn't break the next time someone redesigns the site. A field it can't find comes back `null` instead of just vanishing.
 
This is the open-source build you run yourself. You'll need a Google Gemini API key, that's the only main requirement.
 
- **Typed output**: strings, ints, floats, booleans, ISO 8601 dates, typed arrays, all coerced strictly.
- **Plain schema**: name, type, example. No DSL.
- **Semantic extraction**: reads meaning, not DOM position, so redesigns don't break it.
- **Smart rendering**: plain HTTP first, headless browser only if the page needs it.
- **Fast paths**: checks JSON-LD, OpenGraph, Twitter Cards, oEmbed before ever calling an LLM.
- **Three modes**: extract (single URL), batch (one schema across many URLs), or crawl (follows links from a seed URL).
- **Async built in**: `extract_async`, `batch_async`, `crawl_async` for anyone running this inside their own event loop.
- **Three interfaces**: CLI, local server, or Python library.
 
## Setup

Requires Python 3.11+ (python 3.14 recommended).

```bash
pip install -e ".[tls,patchright]"   # the extras improve anti-bot fetching
playwright install chromium           # one-time browser download for JS pages
```

> IMPORTANT!: Runo requires a Gemini API key to function. Get one at
https://aistudio.google.com/apikey.

```bash
cp .env.example .env
# edit .env and set GEMINI_API_KEY=...
```

(`.env` is loaded automatically. You can also just export `GEMINI_API_KEY` in
your shell.)

The `pip install -e .` above registers a `runo` command on your PATH, so you can
run it from **any** directory, no need to `cd` into the clone. Just keep the
cloned folder in place (the editable install links back to it) and the same
Python environment active.

**`.env` is read from the current directory you're in**,
so to run `runo` from anywhere, export the key globally instead:

```bash
export GEMINI_API_KEY=your_key      # Unix/macOS
setx GEMINI_API_KEY your_key        # Windows (applies to new terminals)
```

## Usage

The **command line** is the quickest way to try Runo. Alternatively, reach for the
**Python library** when you're building it into your own code, or the **local
server** when you want a language-agnostic HTTP endpoint.

### Option 1: Local HTTP Server

```bash
runo serve                    # http://127.0.0.1:8000
```

```bash
curl -X POST http://127.0.0.1:8000/v1/extract \
  -H "Content-Type: application/json" \
  -d '{"url":"https://example.com","schema":[{"field":"title","type":"string","example":"x"}]}'
```

```json
{
  "url": "https://example.com",
  "status": "success",
  "render_mode": "plain",
  "data": {"title": "Example Domain"}
}
```

No API key or auth header, it's your local server. Endpoints: `/v1/extract`,
`/v1/batch`, `/v1/crawl`. Pass per-request settings in an `options` object (see
[Options](#options)).

### Option 2: Python Library

Runo works as a python library.

```python
from runo import extract

data = extract("https://example.com", [
    {"field": "title",     "type": "string",        "example": "Example Domain"},
    {"field": "paragraph", "type": "string",        "example": "This domain is..."},
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

### Option 3: Command Line

You can also run Runo from the command line.

```bash
# single URL (schema from a file)
runo extract https://example.com --schema schema.json

# inline schema, force the headless browser, write result to a file
runo extract https://example.com --schema '[{"field":"title","type":"string","example":"x"}]' \
  --render-js always -o out.json

# many URLs with one schema (urls.txt is one URL per line)
runo batch --urls urls.txt --schema schema.json --concurrency 5

# follow links from a seed
runo crawl https://blog.com --pattern "https://blog.com/posts/*" --schema schema.json --max-pages 50

# run the local HTTP server
runo serve --host 127.0.0.1 --port 8000
```

`--schema` takes a path to a JSON file or an inline JSON string. Common flags:
`--render-js auto|always|never`, `--timeout-ms`, `--no-cache`, and `-o out.json`
to write output to a file instead of stdout (`--concurrency` for batch;
`--max-pages`, `--max-depth`, `--use-sitemap`, `--ignore-robots` for crawl).

A `schema.json` file is just a JSON array of field objects:

```json
[
  {"field": "title", "type": "string", "example": "Example Domain"},
  {"field": "price", "type": "float",  "example": 29.99, "hint": "Use the sale price if present."}
]
```

## Schema

Each field has a `field` name, a `type`, an `example` value (a one-shot anchor
for the LLM), and an optional `hint`. A good example disambiguates format, for
instance `35` vs `"35 years old"`, or `2024-01-31` vs `January 31`.

| Type | Coercion |
|---|---|
| `string` | Always a string |
| `integer` | Parsed from text (`"35 years old"` -> `35`) |
| `float` | Parsed from text (`"$1.2M"` -> `1200000.0`) |
| `boolean` | Normalised (`"✓ Verified"` -> `true`) |
| `date` | ISO 8601 (`YYYY-MM-DD`); relative dates resolved |
| `array<string>` / `array<integer>` / `array<float>` | JSON array (empty `[]` if nothing matched) |

Unresolvable fields come back as `null`, never dropped, so `data` always has the
same keys as your schema.

### Hints

Default behaviour is usually fine. Reach for `hint` when a page shows two values
for the same concept and you want a specific one (`"Use sale price if present."`),
when the field name is ambiguous (`author` on a republished article), or when the
site uses non-obvious wording (`likes` vs `reactions`). Keep hints short and use
them only when needed.

### Worked Examples

Product page:

```json
[
  { "field": "title",    "type": "string",        "example": "MacBook Pro 14\"" },
  { "field": "price",    "type": "float",         "example": 1999.00, "hint": "Use sale price if present." },
  { "field": "inStock",  "type": "boolean",       "example": true },
  { "field": "rating",   "type": "float",         "example": 4.6 },
  { "field": "tags",     "type": "array<string>", "example": ["laptop", "apple"] }
]
```

Article / blog post:

```json
[
  { "field": "headline",    "type": "string", "example": "OpenAI ships o3" },
  { "field": "author",      "type": "string", "example": "Cade Metz" },
  { "field": "publishedAt", "type": "date",   "example": "2024-12-20" },
  { "field": "summary",     "type": "string", "example": "A short summary.", "hint": "1-3 sentences." }
]
```

### Tips

- **Keep schemas tight.** 4-10 fields extract more accurately than 30. Split large ones into two calls.
- **Prefer `array<T>` over delimiter strings.** Declare `array<string>` and let Runo build the list instead of splitting a joined string yourself.
- **Names matter.** `firstName` vs `givenName` produce subtly different extractions. Use the term your target sites use; camelCase works well.

## Options

```text
runo <command> [flags]

COMMANDS:
   extract <url>            extract from a single URL
   batch                    extract from many URLs with one schema
   crawl <seed_url>         crawl from a seed URL, following a link pattern
   serve                    run the local HTTP server

COMMON (extract, batch, crawl):
   --schema string          JSON schema: a .json file path or inline JSON (required)
   --render-js string       JS render mode: auto, always, never (default "auto")
   --timeout-ms int         per-page timeout in milliseconds (default 15000)
   --locale string          BCP-47 locale for the browser context (default "en-US")
   --no-cache               bypass the in-memory result cache
   -o, --output string      write JSON output to a file instead of stdout

EXTRACT:
   --process-images         vision pass to fill null fields from page images (extra tokens)

BATCH:
   --urls string            URL list: a file (one per line) or comma-separated (required)
   --concurrency int        URLs fetched in parallel (default 5)

CRAWL:
   --pattern string         glob for links to follow, e.g. https://site.com/* (required)
   --max-pages int          hard ceiling on pages visited (default 50)
   --max-depth int          link hops from the seed URL (default 2)
   --use-sitemap            also seed URLs from the site's sitemap.xml
   --ignore-robots          ignore robots.txt disallow rules

SERVE:
   --host string            bind address (default "127.0.0.1")
   --port int               port to listen on (default 8000)
   --reload                 auto-reload on code changes (development)
```

The flags above aren't CLI-only. The same options are available from the Python
library and over HTTP, under the same names.

**Python library.** Pass options as keyword arguments in snake_case (the CLI's
`--render-js` becomes `render_js`, `--max-pages` becomes `max_pages`, and so on).
The URL or URL list, the schema, and the crawl follow-pattern are positional
arguments:

```python
from runo import extract, crawl

# force headless, longer timeout, fill nulls from images, skip the cache
extract("https://example.com", schema,
        render_js="always", timeout_ms=30000, process_images=True, no_cache=True)

# crawl-specific settings are keyword arguments too
crawl("https://blog.com", "https://blog.com/posts/*", schema,
      max_pages=100, max_depth=3, use_sitemap=True)
```

Each function has an `_async` twin (`extract_async`, `batch_async`,
`crawl_async`) with an identical signature for use inside your own event loop.

**Over HTTP.** Options go in the request body. `extract` and `batch` take an
`options` object; `crawl` keeps its crawl settings in a `crawl` object alongside
a shared `options` object:

```json
{
  "seed_url": "https://blog.com",
  "schema": [{ "field": "title", "type": "string", "example": "Example post" }],
  "crawl": { "follow_pattern": "https://blog.com/posts/*", "max_pages": 100, "use_sitemap": true },
  "options": { "render_js": "always", "timeout_ms": 30000 }
}
```

**Batch API.** `batch` and `crawl` also accept `async_mode`, which routes
extractions through Gemini's Batch API: cheaper, up to 24h latency, with a
transparent fallback to sync on failure.

## Response

Every extract returns the same shape:

```json
{
  "url": "https://example.com",
  "status": "success",
  "render_mode": "plain",
  "data": { "title": "Example Domain" },
  "images_processed": null
}
```

| Field | Notes |
|---|---|
| `status` | `"success"` or `"error"`. |
| `render_mode` | `"plain"` (plain HTTP fetch was enough) or `"headless"` (escalated to a browser). |
| `data` | Keyed by your schema's `field` names; unresolvable fields are `null`. |
| `warnings` | Optional array of coercion notes (e.g. `"coerced 'price' from '$19.99' to 19.99"`); omitted when empty. |
| `images_processed` | Number of images read by the vision pass; `null` when it didn't run. |

The Python library's `extract()` returns just the `data` dict and raises
`RunoError` on failure; `batch()`/`crawl()` return the full result objects and
don't raise on a single page's failure (check each entry's `status`).

## Fetching & Rendering

Runo tries the cheapest path first and escalates only when a page needs it.
Under `render_js: "auto"` (the default), it starts with a plain HTTP fetch and
switches to a stealth headless browser when it sees signs of trouble:

- A known anti-bot block signature (Cloudflare, Datadome, PerimeterX, Akamai, Incapsula).
- A body under ~500 characters, or sparse visible text behind a large HTML payload (a JS shell).
- JavaScript-framework markers in the HTML.
- An HTTP `402`, `403`, `406`, `429`, or `503`.
- A schema asking for numbers on a page with almost no digits (weather/dashboard widgets).

Escalation is transparent: the response shape is identical, only `render_mode`
changes from `plain` to `headless`.

To get past bot protection it works up from the cheapest option, stopping at the
first that succeeds, and remembers per host what a site needs so later calls skip
the attempts that won't work:

- A **plain HTTP fetch** for static HTML.
- A **TLS-impersonating fetch** (`[tls]` extra) that mimics a real browser's TLS fingerprint, defeating passive JA3/JA4 checks.
- A **hardened headless browser** (`[patchright]` / camoufox) with a canvas/WebGL/audio fingerprint bundle, defeating CDP detection and fingerprint walls.
- **Per-host cookie persistence**, so progressive-trust challenges only have to be cleared once.
- An **archive fallback** (Wayback / reader view) as a last resort when the live site is unreachable.

Aggressively protected sites (some Cloudflare/Datadome setups, large retail like
Amazon/Walmart) can still defeat all of these and come back as `FETCH_BLOCKED`.

## Batch & Crawl Modes

`batch` runs one schema across a list of URLs you already have. `crawl` starts
from a seed URL, follows links matching a pattern, and discovers pages for you.

```python
from runo import batch, crawl

rows = batch(["https://a.com", "https://b.com"], schema, concurrency=5)

site = crawl("https://blog.com", "https://blog.com/posts/*", schema,
             max_pages=50, max_depth=2, use_sitemap=False, ignore_robots=False)
```

The crawler respects `robots.txt` (unless `ignore_robots=True`), can seed from
`sitemap.xml` (`use_sitemap=True`), and applies per-host jitter plus adaptive
back-off so you don't hammer a site. `crawl` returns per-page results plus a
`crawl_meta` block:

```json
{
  "results": [ { "url": "...", "status": "success", "data": { } } ],
  "crawl_meta": { "pages_visited": 17, "pages_skipped": 3, "pages_failed": 0, "cancelled": false }
}
```

It's best to use `batch` when you have the URL list (including paginated feeds
you can build as `?page=1..N`) and use `crawl` when you have one URL and want to
discover related pages.

## Reading Data From Images

Set `process_images=true` (option / `--process-images` flag) and, after the text
pass, any fields still `null` trigger a vision pass: Runo scores the page's
`<img>` tags against the missing field names, fetches up to 3 of the best
candidates, and sends them to Gemini in a single multimodal call targeting only
those fields. It merges anything it finds and reports the count in
`images_processed`. If the image pass fails, the original text-only result is
returned unchanged. Best for data baked into images (price overlays, stats on a
poster, marketplace cards); it costs extra tokens, so it's off by default.

## Errors

Failures use a consistent envelope. On a single extract the top-level `status`
is `"error"`; inside a `batch`/`crawl`, individual entries carry the same
`error` object while the overall call still succeeds.

```json
{ "status": "error", "error": { "code": "FETCH_BLOCKED", "message": "...", "retryable": true } }
```

| Code | Retryable | Meaning |
|---|---|---|
| `SCHEMA_INVALID` | no | Schema is malformed (missing `field`, unknown type). |
| `TYPE_COERCION_FAILED` | no | A value couldn't be coerced to its declared type. |
| `URL_UNREACHABLE` | yes | DNS/network failure, or blocked by the SSRF guard. |
| `TIMEOUT` | yes | Page exceeded `timeout_ms`. |
| `FETCH_BLOCKED` | yes | Anti-bot defeated every free fetch strategy. |
| `LLM_UNAVAILABLE` / `LLM_RATE_LIMITED` / `LLM_TIMEOUT` / `LLM_EMPTY` / `LLM_ERROR` | yes | Gemini was overloaded, rate-limited, slow, or returned an unusable response. |
| `LLM_TRUNCATED` / `LLM_BLOCKED` / `LLM_BAD_REQUEST` | no | Output couldn't be parsed, a safety/policy block, or a bad request (prompt too long). |

Retry the `retryable: true` codes with exponential back-off (1s, 2s, 4s, 8s, cap
at ~4 attempts); treat the rest as terminal. When a call succeeds but something
looked off (e.g. a currency symbol stripped), the fix is reported in the
optional `warnings` array rather than failing the call.

## Configuration

Everything is driven by environment variables (or `.env`). Only `GEMINI_API_KEY`
is required; see [`.env.example`](.env.example) for the documented tunables,
including `GEMINI_API_KEYS` for round-robin across several keys, `HEADLESS_ENGINE`,
`TLS_IMPERSONATE`, and `SSRF_GUARD_ENABLED`.

## Limitations

- **JS-heavy sites need the browser.** Plain-HTML pages work with just
  `pip install`, but sites that render content with JavaScript need
  `playwright install chromium`. Without it, those pages come back empty.
- **Hard anti-bot walls may fail.** Aggressively protected sites (some
  Cloudflare/Datadome setups, large retail like Amazon/Walmart) can defeat every
  built-in fetch strategy and return `FETCH_BLOCKED`.
- **Caching is in-memory.** Results and per-field values are cached within a
  running process to avoid repeat LLM calls, but the cache resets on restart.
- **You pay Google for tokens.** Extraction quality and cost track whatever
  Gemini model is configured (Flash-Lite by default).

## License

[Apache License 2.0](LICENSE).
