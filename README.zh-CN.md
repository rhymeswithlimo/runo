<p align="center">
  <img src="public/Logo_SVG_v1.0__Runo.svg" width="72" height="72" alt="Runo 徽标">
</p>

<h1 align="center">Runo</h1>

<p align="center">
  <a href="README.md">English</a> ·
  简体中文 ·
  <a href="README.es.md">Español</a> ·
  <a href="README.fr.md">Français</a> ·
  <a href="README.de.md">Deutsch</a> ·
  <a href="README.ja.md">日本語</a>
</p>

<p align="center">使用你自己定义的 schema，从任意 URL 中提取结构化的、带类型的 JSON。</p>

<p align="center">
  <img src="https://img.shields.io/badge/python-3.11%2B-2281f7?logo=python&logoColor=white" alt="Python 3.11+">
  <img src="https://img.shields.io/badge/LLM-Google%20Gemini-2281f7?logo=googlegemini&logoColor=white" alt="由 Google Gemini 提供支持">
  <img src="https://img.shields.io/badge/runs-locally-2281f7" alt="本地运行">
  <img src="https://img.shields.io/badge/license-Apache--2.0-2281f7" alt="Apache-2.0">
</p>

> [!NOTE]
> 我是这个项目的唯一维护者。
> 它起初是一个闭源 SaaS（[scrapewithruno.com](https://scrapewithruno.com)），但我决定把它开源 :)。

---

你描述想要什么（字段名、类型、示例值），Runo 就会抓取页面，在网站需要时渲染 JavaScript，用 LLM 提取数据，并把每个值强制转换为你要求的类型。你会得到干净、扁平的 JSON。

无需选择器，无需 XPath，没有任何需要维护的东西。由于 LLM 是按语义而非 DOM 位置来读取的，下次有人重新设计网站时，你的 schema 也不会失效。找不到的字段会返回 `null`，而不是干脆消失。

这是你自己运行的开源版本。你需要一个 Google Gemini API 密钥，这是唯一的主要要求。

- **带类型的输出**：字符串、整数、浮点数、布尔值、ISO 8601 日期以及带类型的数组，全部严格转换。
- **简单的 schema**：名称、类型、示例。没有任何 DSL。
- **语义提取**：读取的是含义而非 DOM 位置，所以重新设计不会让它失效。
- **智能渲染**：先用纯 HTTP，只有页面需要时才启用无头浏览器。
- **快速路径**：在调用 LLM 之前，先检查 JSON-LD、OpenGraph、Twitter Cards 和 oEmbed。
- **三种模式**：extract（单个 URL）、batch（一个 schema 应用到多个 URL）或 crawl（从种子 URL 跟踪链接）。
- **内置异步**：为在自己的事件循环中运行的用户提供 `extract_async`、`batch_async`、`crawl_async`。
- **三种接口**：CLI、本地服务器或 Python 库。

## 安装

需要 Python 3.11+（推荐 Python 3.14）。

```bash
pip install -e ".[tls,patchright]"   # 这些 extras 可改善面对反爬系统时的抓取
playwright install chromium           # 面向 JS 页面的一次性浏览器下载
```

> 重要！：Runo 需要一个 Gemini API 密钥才能工作。在这里获取:
https://aistudio.google.com/apikey 。

```bash
cp .env.example .env
# 编辑 .env 并设置 GEMINI_API_KEY=...
```

（`.env` 会自动加载。你也可以直接在 shell 中导出 `GEMINI_API_KEY`。）

上面的 `pip install -e .` 会在你的 PATH 中注册一个 `runo` 命令，因此你可以从**任意**目录运行它，无需 `cd` 到克隆目录。只要把克隆的文件夹保留在原处（editable 安装会链接回它），并保持同一个 Python 环境处于激活状态即可。

**`.env` 是从你当前所在的目录读取的**，因此要从任何地方运行 `runo`，请改为全局导出该密钥：

```bash
export GEMINI_API_KEY=你的密钥      # Unix/macOS
setx GEMINI_API_KEY 你的密钥        # Windows（对新终端生效）
```

## 用法

**命令行**是试用 Runo 最快的方式。此外，当你要把它集成进自己的代码时，可以使用 **Python 库**；当你想要一个与语言无关的 HTTP 端点时，可以使用**本地服务器**。

### 选项 1：本地 HTTP 服务器

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

无需 API 密钥或认证头，这是你自己的本地服务器。端点：`/v1/extract`、`/v1/batch`、`/v1/crawl`。在 `options` 对象中传递每次请求的设置（参见 [选项](#选项)）。

### 选项 2：Python 库

Runo 可作为 Python 库使用。

```python
from runo import extract

data = extract("https://example.com", [
    {"field": "title",     "type": "string",        "example": "Example Domain"},
    {"field": "paragraph", "type": "string",        "example": "This domain is..."},
])
print(data)   # {"title": "Example Domain", "paragraph": "..."}
```

`batch` 会把一个 schema 应用到多个 URL；`crawl` 会从种子 URL 跟踪链接。每个都有一个 `_async` 变体（`extract_async`、`batch_async`、`crawl_async`），可在你自己的事件循环中使用。

```python
from runo import batch, crawl

rows = batch(["https://a.com", "https://b.com"], schema, concurrency=5)
site = crawl("https://blog.com", "https://blog.com/posts/*", schema, max_pages=50)
```

### 选项 3：命令行

你也可以从命令行运行 Runo。

```bash
# 单个 URL（从文件读取 schema）
runo extract https://example.com --schema schema.json

# 内联 schema，强制使用无头浏览器，将结果写入文件
runo extract https://example.com --schema '[{"field":"title","type":"string","example":"x"}]' \
  --render-js always -o out.json

# 用一个 schema 处理多个 URL（urls.txt 每行一个 URL）
runo batch --urls urls.txt --schema schema.json --concurrency 5

# 从种子跟踪链接
runo crawl https://blog.com --pattern "https://blog.com/posts/*" --schema schema.json --max-pages 50

# 运行本地 HTTP 服务器
runo serve --host 127.0.0.1 --port 8000
```

`--schema` 接受一个 JSON 文件路径或一个内联 JSON 字符串。常用标志：`--render-js auto|always|never`、`--timeout-ms`、`--no-cache`，以及把输出写入文件而非 stdout 的 `-o out.json`（batch 用 `--concurrency`；crawl 用 `--max-pages`、`--max-depth`、`--use-sitemap`、`--ignore-robots`）。

一个 `schema.json` 文件就是一个由字段对象组成的 JSON 数组：

```json
[
  {"field": "title", "type": "string", "example": "Example Domain"},
  {"field": "price", "type": "float",  "example": 29.99, "hint": "Use the sale price if present."}
]
```

## Schema

每个字段都有一个 `field` 名称、一个 `type`、一个 `example` 值（作为 LLM 的一次性锚点），以及一个可选的 `hint`。好的示例可以消除格式歧义，例如 `35` 与 `"35 years old"`，或 `2024-01-31` 与 `January 31`。

| 类型 | 转换 |
|---|---|
| `string` | 始终为字符串 |
| `integer` | 从文本解析（`"35 years old"` -> `35`） |
| `float` | 从文本解析（`"$1.2M"` -> `1200000.0`） |
| `boolean` | 归一化（`"✓ Verified"` -> `true`） |
| `date` | ISO 8601（`YYYY-MM-DD`）；相对日期会被解析 |
| `array<string>` / `array<integer>` / `array<float>` | JSON 数组（若无匹配则为空 `[]`） |

无法解析的字段会返回 `null`，绝不会被丢弃，因此 `data` 始终具有与你的 schema 相同的键。

### 提示（hints）

默认行为通常足够。当页面对同一概念显示两个值而你想要特定的那个时（`"Use sale price if present."`），当字段名有歧义时（转载文章上的 `author`），或当网站使用不明显的措辞时（`likes` 与 `reactions`），可以使用 `hint`。提示要简短，仅在需要时使用。

### 实例

商品页面：

```json
[
  { "field": "title",    "type": "string",        "example": "MacBook Pro 14\"" },
  { "field": "price",    "type": "float",         "example": 1999.00, "hint": "Use sale price if present." },
  { "field": "inStock",  "type": "boolean",       "example": true },
  { "field": "rating",   "type": "float",         "example": 4.6 },
  { "field": "tags",     "type": "array<string>", "example": ["laptop", "apple"] }
]
```

文章 / 博客帖子：

```json
[
  { "field": "headline",    "type": "string", "example": "OpenAI ships o3" },
  { "field": "author",      "type": "string", "example": "Cade Metz" },
  { "field": "publishedAt", "type": "date",   "example": "2024-12-20" },
  { "field": "summary",     "type": "string", "example": "A short summary.", "hint": "1-3 sentences." }
]
```

### 小贴士

- **保持 schema 精简。** 4 到 10 个字段比 30 个字段提取得更准确。把大的拆成两次调用。
- **优先使用 `array<T>` 而非带分隔符的字符串。** 声明 `array<string>`，让 Runo 构建列表，而不是自己去切分一个拼接的字符串。
- **名称很重要。** `firstName` 与 `givenName` 会产生细微不同的提取结果。使用你目标网站所用的术语；camelCase 效果不错。

## 选项

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

上面的标志并非 CLI 专用。相同的选项也可从 Python 库以及通过 HTTP 使用，名称相同。

**Python 库。** 以 snake_case 的关键字参数传入选项（CLI 的 `--render-js` 变为 `render_js`，`--max-pages` 变为 `max_pages`，依此类推）。URL 或 URL 列表、schema，以及 crawl 的跟踪模式是位置参数：

```python
from runo import extract, crawl

# 强制无头、更长的超时、从图片填充 null、跳过缓存
extract("https://example.com", schema,
        render_js="always", timeout_ms=30000, process_images=True, no_cache=True)

# crawl 专属设置同样是关键字参数
crawl("https://blog.com", "https://blog.com/posts/*", schema,
      max_pages=100, max_depth=3, use_sitemap=True)
```

每个函数都有一个 `_async` 孪生函数（`extract_async`、`batch_async`、`crawl_async`），签名完全相同，可在你自己的事件循环中使用。

**通过 HTTP。** 选项放在请求体中。`extract` 和 `batch` 接受一个 `options` 对象；`crawl` 则把它的爬取设置放在一个 `crawl` 对象中，与共享的 `options` 对象并列：

```json
{
  "seed_url": "https://blog.com",
  "schema": [{ "field": "title", "type": "string", "example": "Example post" }],
  "crawl": { "follow_pattern": "https://blog.com/posts/*", "max_pages": 100, "use_sitemap": true },
  "options": { "render_js": "always", "timeout_ms": 30000 }
}
```

**Batch API。** `batch` 和 `crawl` 还接受 `async_mode`，它会把提取通过 Gemini 的 Batch API 路由：更便宜，延迟最长 24 小时，失败时会透明地回退到同步模式。

## 响应

每次提取都会返回相同的结构：

```json
{
  "url": "https://example.com",
  "status": "success",
  "render_mode": "plain",
  "data": { "title": "Example Domain" },
  "images_processed": null
}
```

| 字段 | 说明 |
|---|---|
| `status` | `"success"` 或 `"error"`。 |
| `render_mode` | `"plain"`（纯 HTTP 抓取已足够）或 `"headless"`（升级为浏览器）。 |
| `data` | 以你 schema 的 `field` 名称为键；无法解析的字段为 `null`。 |
| `warnings` | 可选的转换说明数组（例如 `"coerced 'price' from '$19.99' to 19.99"`）；为空时省略。 |
| `images_processed` | 视觉处理读取的图片数量；未运行时为 `null`。 |

Python 库的 `extract()` 只返回 `data` 字典，并在失败时抛出 `RunoError`；`batch()`/`crawl()` 返回完整的结果对象，且不会因单个页面失败而抛出异常（请检查每个条目的 `status`）。

## 抓取与渲染

Runo 会先尝试最便宜的路径，只有在页面需要时才升级。在 `render_js: "auto"`（默认）下，它从纯 HTTP 抓取开始，一旦发现麻烦的迹象，就切换到隐身无头浏览器：

- 已知的反爬拦截特征（Cloudflare、Datadome、PerimeterX、Akamai、Incapsula）。
- 正文少于约 500 个字符，或在庞大的 HTML 负载背后可见文本稀少（一个 JS 外壳）。
- HTML 中的 JavaScript 框架标记。
- HTTP `402`、`403`、`406`、`429` 或 `503`。
- 在几乎没有数字的页面上要求数字的 schema（天气/仪表盘小部件）。

升级是透明的：响应结构完全相同，只有 `render_mode` 从 `plain` 变为 `headless`。

为了突破反爬保护，它从最便宜的方案逐级向上，在第一个成功的方案处停止，并按主机记住每个网站所需的方案，以便后续调用跳过那些行不通的尝试：

- 面向静态 HTML 的**纯 HTTP 抓取**。
- 一种**模拟 TLS 的抓取**（`[tls]` extra），模仿真实浏览器的 TLS 指纹，击败被动的 JA3/JA4 检查。
- 一个**强化的无头浏览器**（`[patchright]` / camoufox），带有 canvas/WebGL/audio 指纹套件，击败 CDP 检测和指纹墙。
- **按主机的 Cookie 持久化**，让渐进式信任挑战只需通过一次。
- 一个**归档回退**（Wayback / 阅读视图），在实时网站无法访问时作为最后手段。

受到强力保护的网站（某些 Cloudflare/Datadome 配置，Amazon/Walmart 这类大型零售）仍可能击败上述全部方案，并以 `FETCH_BLOCKED` 返回。

## batch 与 crawl 模式

`batch` 会对你已经拥有的一组 URL 运行一个 schema。`crawl` 从一个种子 URL 开始，跟踪匹配某个模式的链接，并为你发现页面。

```python
from runo import batch, crawl

rows = batch(["https://a.com", "https://b.com"], schema, concurrency=5)

site = crawl("https://blog.com", "https://blog.com/posts/*", schema,
             max_pages=50, max_depth=2, use_sitemap=False, ignore_robots=False)
```

爬虫会尊重 `robots.txt`（除非 `ignore_robots=True`），可以从 `sitemap.xml` 播种（`use_sitemap=True`），并应用按主机的抖动以及自适应退避，以免你猛烈冲击某个网站。`crawl` 返回逐页结果以及一个 `crawl_meta` 块：

```json
{
  "results": [ { "url": "...", "status": "success", "data": { } } ],
  "crawl_meta": { "pages_visited": 17, "pages_skipped": 3, "pages_failed": 0, "cancelled": false }
}
```

当你已有 URL 列表时（包括可以构造成 `?page=1..N` 的分页信息流），最好用 `batch`；当你只有一个 URL 并想发现相关页面时，最好用 `crawl`。

## 从图片中读取数据

设置 `process_images=true`（选项 / `--process-images` 标志），在文本处理之后，任何仍为 `null` 的字段都会触发一次视觉处理：Runo 会将页面的 `<img>` 标签与缺失的字段名进行打分匹配，抓取最多 3 个最佳候选，并在一次仅针对这些字段的多模态调用中把它们发送给 Gemini。它会合并找到的内容，并在 `images_processed` 中报告数量。如果图片处理失败，则原样返回仅文本的原始结果。最适合嵌入在图片中的数据（价格叠层、海报上的统计数据、市场商品卡片）；它会消耗额外的 token，因此默认关闭。

## 错误

失败使用一致的信封。在单次提取中，顶层 `status` 为 `"error"`；在 `batch`/`crawl` 内部，各个条目携带相同的 `error` 对象，而整体调用仍然成功。

```json
{ "status": "error", "error": { "code": "FETCH_BLOCKED", "message": "...", "retryable": true } }
```

| 代码 | 可重试 | 含义 |
|---|---|---|
| `SCHEMA_INVALID` | 否 | schema 格式错误（缺少 `field`，未知类型）。 |
| `TYPE_COERCION_FAILED` | 否 | 某个值无法转换为其声明的类型。 |
| `URL_UNREACHABLE` | 是 | DNS/网络失败，或被 SSRF 防护拦截。 |
| `TIMEOUT` | 是 | 页面超过了 `timeout_ms`。 |
| `FETCH_BLOCKED` | 是 | 反爬击败了所有免费的抓取策略。 |
| `LLM_UNAVAILABLE` / `LLM_RATE_LIMITED` / `LLM_TIMEOUT` / `LLM_EMPTY` / `LLM_ERROR` | 是 | Gemini 过载、被限速、缓慢，或返回了无法使用的响应。 |
| `LLM_TRUNCATED` / `LLM_BLOCKED` / `LLM_BAD_REQUEST` | 否 | 输出无法解析、安全/策略拦截，或请求无效（提示过长）。 |

对 `retryable: true` 的代码使用指数退避重试（1s、2s、4s、8s，上限约 4 次）；其余视为终态。当调用成功但有些地方看起来不对劲时（例如去掉了货币符号），修正会在可选的 `warnings` 数组中报告，而不会让调用失败。

## 配置

一切都由环境变量（或 `.env`）驱动。只有 `GEMINI_API_KEY` 是必需的；有关文档化的可调项，请参见 [`.env.example`](.env.example)，包括用于在多个密钥间轮询的 `GEMINI_API_KEYS`、`HEADLESS_ENGINE`、`TLS_IMPERSONATE` 和 `SSRF_GUARD_ENABLED`。

## 限制

- **重度依赖 JS 的网站需要浏览器。** 纯 HTML 页面仅用 `pip install` 即可工作，但用 JavaScript 渲染内容的网站需要 `playwright install chromium`。没有它，这些页面会返回空。
- **强硬的反爬墙可能失败。** 受到强力保护的网站（某些 Cloudflare/Datadome 配置，Amazon/Walmart 这类大型零售）可能击败所有内置抓取策略并返回 `FETCH_BLOCKED`。
- **缓存在内存中。** 结果和逐字段的值会在运行中的进程内缓存，以避免重复的 LLM 调用，但缓存会在重启时重置。
- **你要为 token 向 Google 付费。** 提取的质量和成本取决于所配置的 Gemini 模型（默认 Flash-Lite）。

## 许可证

[Apache License 2.0](LICENSE)。
