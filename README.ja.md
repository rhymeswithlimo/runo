<p align="center">
  <img src="public/Logo_SVG_v1.0__Runo.svg" width="72" height="72" alt="Runo ロゴ">
</p>

<h1 align="center">Runo</h1>

<p align="center">
  <a href="README.md">English</a> ·
  <a href="README.zh-CN.md">简体中文</a> ·
  <a href="README.es.md">Español</a> ·
  <a href="README.fr.md">Français</a> ·
  <a href="README.de.md">Deutsch</a> ·
  日本語
</p>

<p align="center">自分で定義したスキーマを使って、あらゆる URL から構造化された型付き JSON を抽出します。</p>

<p align="center">
  <img src="https://img.shields.io/badge/python-3.11%2B-2281f7?logo=python&logoColor=white" alt="Python 3.11+">
  <img src="https://img.shields.io/badge/LLM-Google%20Gemini-2281f7?logo=googlegemini&logoColor=white" alt="Google Gemini 搭載">
  <img src="https://img.shields.io/badge/runs-locally-2281f7" alt="ローカルで動作">
  <img src="https://img.shields.io/badge/license-Apache--2.0-2281f7" alt="Apache-2.0">
</p>

> [!NOTE]
> このプロジェクトは私が単独でメンテナンスしています。
> クローズドソースの SaaS ([scrapewithruno.com](https://scrapewithruno.com)) として始めましたが、オープンソース化することにしました :)。

---

欲しいもの（フィールド名、型、例の値）を記述すると、Runo がページを取得し、サイトが必要とする場合は JavaScript をレンダリングし、LLM でデータを抽出して、各値を指定した型に変換します。きれいでフラットな JSON が返ってきます。

セレクターも XPath も不要で、メンテナンスするものは何もありません。LLM は DOM の位置ではなく意味を読み取るため、誰かがサイトをリデザインしても次回スキーマが壊れることはありません。見つからないフィールドは、単に消えるのではなく `null` として返ります。

これは自分で動かすオープンソース版です。Google Gemini の API キーが必要で、それが唯一の主要な要件です。

- **型付き出力**: 文字列、整数、浮動小数点数、真偽値、ISO 8601 の日付、型付き配列を、すべて厳密に変換します。
- **シンプルなスキーマ**: 名前、型、例のみ。DSL は不要です。
- **意味ベースの抽出**: DOM の位置ではなく意味を読むため、リデザインで壊れません。
- **スマートなレンダリング**: まずプレーンな HTTP、ページが必要とする場合のみヘッドレスブラウザを使います。
- **高速パス**: LLM を呼び出す前に、JSON-LD、OpenGraph、Twitter Cards、oEmbed を確認します。
- **3 つのモード**: extract（単一 URL）、batch（1 つのスキーマを複数 URL に適用）、crawl（起点 URL からリンクをたどる）。
- **非同期を標準搭載**: 独自のイベントループ内で実行する人向けに `extract_async`、`batch_async`、`crawl_async` を用意しています。
- **3 つのインターフェース**: CLI、ローカルサーバー、Python ライブラリ。

## セットアップ

Python 3.11 以上が必要です（Python 3.14 を推奨）。

```bash
pip install -e ".[tls,patchright]"   # これらの extras はアンチボット対策下での取得を改善します
playwright install chromium           # JS ページ向けの一度きりのブラウザーダウンロード
```

> 重要！: Runo が機能するには Gemini API キーが必要です。次の場所で取得してください:
https://aistudio.google.com/apikey 。

```bash
cp .env.example .env
# .env を編集して GEMINI_API_KEY=... を設定します
```

（`.env` は自動的に読み込まれます。シェルで `GEMINI_API_KEY` をエクスポートするだけでも構いません。）

上記の `pip install -e .` は `runo` コマンドを PATH に登録するため、クローンに `cd` する必要なく**どの**ディレクトリからでも実行できます。クローンしたフォルダーはそのままの場所に置いておき（editable インストールがそこにリンクされます）、同じ Python 環境を有効にしておいてください。

**`.env` は現在いるディレクトリから読み込まれる**ため、`runo` をどこからでも実行するには、代わりにキーをグローバルにエクスポートしてください:

```bash
export GEMINI_API_KEY=your_key      # Unix/macOS
setx GEMINI_API_KEY your_key        # Windows（新しいターミナルに適用されます）
```

## 使い方

**コマンドライン**が Runo を最も手早く試す方法です。あるいは、自分のコードに組み込むときは **Python ライブラリ**を、言語に依存しない HTTP エンドポイントが欲しいときは**ローカルサーバー**を使ってください。

### オプション 1: ローカル HTTP サーバー

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

API キーも認証ヘッダーも不要です。あなたのローカルサーバーですから。エンドポイント: `/v1/extract`、`/v1/batch`、`/v1/crawl`。リクエストごとの設定は `options` オブジェクトで渡します（[オプション](#オプション) を参照）。

### オプション 2: Python ライブラリ

Runo は Python ライブラリとして動作します。

```python
from runo import extract

data = extract("https://example.com", [
    {"field": "title",     "type": "string",        "example": "Example Domain"},
    {"field": "paragraph", "type": "string",        "example": "This domain is..."},
])
print(data)   # {"title": "Example Domain", "paragraph": "..."}
```

`batch` は 1 つのスキーマを複数 URL に対して実行し、`crawl` は起点 URL からリンクをたどります。それぞれに `_async` バリアント（`extract_async`、`batch_async`、`crawl_async`）があり、独自のイベントループ内で使えます。

```python
from runo import batch, crawl

rows = batch(["https://a.com", "https://b.com"], schema, concurrency=5)
site = crawl("https://blog.com", "https://blog.com/posts/*", schema, max_pages=50)
```

### オプション 3: コマンドライン

Runo はコマンドラインからも実行できます。

```bash
# 単一 URL（ファイルからのスキーマ）
runo extract https://example.com --schema schema.json

# インラインスキーマ、ヘッドレスブラウザーを強制、結果をファイルに書き出す
runo extract https://example.com --schema '[{"field":"title","type":"string","example":"x"}]' \
  --render-js always -o out.json

# 1 つのスキーマで複数 URL（urls.txt は 1 行 1 URL）
runo batch --urls urls.txt --schema schema.json --concurrency 5

# 起点からリンクをたどる
runo crawl https://blog.com --pattern "https://blog.com/posts/*" --schema schema.json --max-pages 50

# ローカル HTTP サーバーを起動
runo serve --host 127.0.0.1 --port 8000
```

`--schema` は JSON ファイルへのパス、またはインラインの JSON 文字列を受け取ります。よく使うフラグ: `--render-js auto|always|never`、`--timeout-ms`、`--no-cache`、および出力を stdout ではなくファイルに書き出す `-o out.json`（batch には `--concurrency`、crawl には `--max-pages`、`--max-depth`、`--use-sitemap`、`--ignore-robots`）。

`schema.json` ファイルは、フィールドオブジェクトの JSON 配列にすぎません:

```json
[
  {"field": "title", "type": "string", "example": "Example Domain"},
  {"field": "price", "type": "float",  "example": 29.99, "hint": "Use the sale price if present."}
]
```

## スキーマ

各フィールドには `field` 名、`type`、`example` の値（LLM 向けのワンショットのアンカー）、任意の `hint` があります。良い例は形式の曖昧さを解消します。たとえば `35` と `"35 years old"`、`2024-01-31` と `January 31` の違いです。

| 型 | 変換 |
|---|---|
| `string` | 常に文字列 |
| `integer` | テキストから解析（`"35 years old"` -> `35`） |
| `float` | テキストから解析（`"$1.2M"` -> `1200000.0`） |
| `boolean` | 正規化（`"✓ Verified"` -> `true`） |
| `date` | ISO 8601（`YYYY-MM-DD`）。相対的な日付は解決されます |
| `array<string>` / `array<integer>` / `array<float>` | JSON 配列（一致がなければ空の `[]`） |

解決できないフィールドは `null` として返り、決して削除されません。そのため `data` は常にスキーマと同じキーを持ちます。

### ヒント（hints）

通常はデフォルトの挙動で十分です。ページが同じ概念に対して 2 つの値を示していて特定の一方が欲しいとき（`"Use sale price if present."`）、フィールド名が曖昧なとき（再掲載記事の `author`）、またはサイトが分かりにくい語を使っているとき（`likes` と `reactions`）に `hint` を使ってください。ヒントは短く保ち、必要なときだけ使いましょう。

### 実例

商品ページ:

```json
[
  { "field": "title",    "type": "string",        "example": "MacBook Pro 14\"" },
  { "field": "price",    "type": "float",         "example": 1999.00, "hint": "Use sale price if present." },
  { "field": "inStock",  "type": "boolean",       "example": true },
  { "field": "rating",   "type": "float",         "example": 4.6 },
  { "field": "tags",     "type": "array<string>", "example": ["laptop", "apple"] }
]
```

記事 / ブログ投稿:

```json
[
  { "field": "headline",    "type": "string", "example": "OpenAI ships o3" },
  { "field": "author",      "type": "string", "example": "Cade Metz" },
  { "field": "publishedAt", "type": "date",   "example": "2024-12-20" },
  { "field": "summary",     "type": "string", "example": "A short summary.", "hint": "1-3 sentences." }
]
```

### コツ

- **スキーマは絞り込む。** 4〜10 フィールドの方が 30 フィールドより正確に抽出できます。大きいものは 2 回の呼び出しに分けましょう。
- **区切り文字の文字列より `array<T>` を優先する。** `array<string>` を宣言し、連結された文字列を自分で分割するのではなく、Runo にリストを組み立てさせましょう。
- **名前が重要。** `firstName` と `givenName` では抽出結果が微妙に変わります。対象サイトが使う用語を使いましょう。camelCase はうまく機能します。

## オプション

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

上記のフラグは CLI 専用ではありません。同じオプションが Python ライブラリからも HTTP 経由でも、同じ名前で利用できます。

**Python ライブラリ。** オプションは snake_case のキーワード引数として渡します（CLI の `--render-js` は `render_js`、`--max-pages` は `max_pages`、といった具合です）。URL または URL リスト、スキーマ、crawl の追跡パターンは位置引数です:

```python
from runo import extract, crawl

# ヘッドレスを強制し、タイムアウトを長くし、画像から null を埋め、キャッシュをスキップ
extract("https://example.com", schema,
        render_js="always", timeout_ms=30000, process_images=True, no_cache=True)

# crawl 固有の設定もキーワード引数です
crawl("https://blog.com", "https://blog.com/posts/*", schema,
      max_pages=100, max_depth=3, use_sitemap=True)
```

各関数には `_async` の双子（`extract_async`、`batch_async`、`crawl_async`）があり、シグネチャは同一で、独自のイベントループ内で使えます。

**HTTP 経由。** オプションはリクエストボディに入れます。`extract` と `batch` は `options` オブジェクトを取り、`crawl` は crawl の設定を `crawl` オブジェクトに、共有の `options` オブジェクトと並べて保持します:

```json
{
  "seed_url": "https://blog.com",
  "schema": [{ "field": "title", "type": "string", "example": "Example post" }],
  "crawl": { "follow_pattern": "https://blog.com/posts/*", "max_pages": 100, "use_sitemap": true },
  "options": { "render_js": "always", "timeout_ms": 30000 }
}
```

**Batch API。** `batch` と `crawl` は `async_mode` も受け付けます。これは抽出を Gemini の Batch API 経由でルーティングします。より安価で、最大 24 時間のレイテンシーがあり、失敗時には同期モードへ透過的にフォールバックします。

## レスポンス

すべての抽出は同じ形を返します:

```json
{
  "url": "https://example.com",
  "status": "success",
  "render_mode": "plain",
  "data": { "title": "Example Domain" },
  "images_processed": null
}
```

| フィールド | 備考 |
|---|---|
| `status` | `"success"` または `"error"`。 |
| `render_mode` | `"plain"`（プレーンな HTTP 取得で十分だった）または `"headless"`（ブラウザーにエスカレーションした）。 |
| `data` | スキーマの `field` 名でキー付けされます。解決できないフィールドは `null`。 |
| `warnings` | 変換に関する注記の任意の配列（例: `"coerced 'price' from '$19.99' to 19.99"`）。空のときは省略されます。 |
| `images_processed` | ビジョンパスで読み取られた画像数。実行されなかった場合は `null`。 |

Python ライブラリの `extract()` は `data` 辞書のみを返し、失敗時に `RunoError` を送出します。`batch()`/`crawl()` は完全な結果オブジェクトを返し、1 ページの失敗では例外を送出しません（各エントリの `status` を確認してください）。

## 取得とレンダリング

Runo はまず最も安価なパスを試し、ページが必要とするときだけエスカレーションします。`render_js: "auto"`（デフォルト）では、プレーンな HTTP 取得から始め、問題の兆候を検知するとステルスのヘッドレスブラウザーに切り替えます:

- 既知のアンチボットのブロックシグネチャ（Cloudflare、Datadome、PerimeterX、Akamai、Incapsula）。
- 本文が約 500 文字未満、または大きな HTML ペイロードの背後に可視テキストがまばら（JS の外殻）。
- HTML 中の JavaScript フレームワークのマーカー。
- HTTP `402`、`403`、`406`、`429`、`503`。
- 数字がほとんどないページで数値を求めるスキーマ（天気/ダッシュボードのウィジェット）。

エスカレーションは透過的です。レスポンスの形は同一で、`render_mode` が `plain` から `headless` に変わるだけです。

ボット保護を突破するために、最も安価な手段から順に上げていき、最初に成功したところで止め、ホストごとにサイトが何を必要とするかを記憶するので、以降の呼び出しは効かない試行をスキップします:

- 静的 HTML 向けの**プレーンな HTTP 取得**。
- 実際のブラウザーの TLS フィンガープリントを模倣し、受動的な JA3/JA4 チェックを突破する **TLS なりすまし取得**（`[tls]` extra）。
- canvas/WebGL/audio のフィンガープリントバンドルを備え、CDP 検出とフィンガープリントの壁を突破する**堅牢なヘッドレスブラウザー**（`[patchright]` / camoufox）。
- **ホストごとの Cookie 永続化**。段階的な信頼のチャレンジを一度だけ通過すれば済むようにします。
- ライブサイトに到達できないときの最終手段としての**アーカイブフォールバック**（Wayback / リーダービュー）。

積極的に保護されたサイト（一部の Cloudflare/Datadome の構成、Amazon/Walmart のような大手小売）は、これらすべてを突破してもなお `FETCH_BLOCKED` で返ることがあります。

## batch と crawl モード

`batch` は、すでに手元にある URL のリストに対して 1 つのスキーマを実行します。`crawl` は起点 URL から始め、パターンに一致するリンクをたどり、ページを発見します。

```python
from runo import batch, crawl

rows = batch(["https://a.com", "https://b.com"], schema, concurrency=5)

site = crawl("https://blog.com", "https://blog.com/posts/*", schema,
             max_pages=50, max_depth=2, use_sitemap=False, ignore_robots=False)
```

クローラーは `robots.txt` を尊重し（`ignore_robots=True` の場合を除く）、`sitemap.xml` からシード可能で（`use_sitemap=True`）、ホストごとのジッターと適応的なバックオフを適用してサイトに過負荷をかけないようにします。`crawl` はページごとの結果に加えて `crawl_meta` ブロックを返します:

```json
{
  "results": [ { "url": "...", "status": "success", "data": { } } ],
  "crawl_meta": { "pages_visited": 17, "pages_skipped": 3, "pages_failed": 0, "cancelled": false }
}
```

URL のリストがある場合（`?page=1..N` として組み立てられるページ送りのフィードを含む）は `batch` を、1 つの URL があって関連ページを発見したい場合は `crawl` を使うのが最適です。

## 画像からのデータ読み取り

`process_images=true`（オプション / `--process-images` フラグ）を設定すると、テキストパスの後、まだ `null` のフィールドがビジョンパスを起動します。Runo はページの `<img>` タグを、欠けているフィールド名に対してスコア付けし、上位 3 件の候補までを取得して、それらのフィールドだけを対象とする単一のマルチモーダル呼び出しで Gemini に送ります。見つかったものをマージし、その件数を `images_processed` に報告します。画像パスが失敗した場合は、元のテキストのみの結果がそのまま返されます。画像に焼き込まれたデータ（価格のオーバーレイ、ポスター上の統計、マーケットプレイスのカード）に最適です。追加のトークンがかかるため、デフォルトではオフです。

## エラー

失敗は一貫したエンベロープを使います。単一の抽出ではトップレベルの `status` が `"error"` になります。`batch`/`crawl` の内部では、個々のエントリが同じ `error` オブジェクトを持ちますが、呼び出し全体は成功します。

```json
{ "status": "error", "error": { "code": "FETCH_BLOCKED", "message": "...", "retryable": true } }
```

| コード | 再試行可 | 意味 |
|---|---|---|
| `SCHEMA_INVALID` | 不可 | スキーマが不正（`field` の欠落、未知の型）。 |
| `TYPE_COERCION_FAILED` | 不可 | 値を宣言した型に変換できなかった。 |
| `URL_UNREACHABLE` | 可 | DNS/ネットワークの失敗、または SSRF ガードによるブロック。 |
| `TIMEOUT` | 可 | ページが `timeout_ms` を超過した。 |
| `FETCH_BLOCKED` | 可 | アンチボットが無料の取得戦略をすべて突破した。 |
| `LLM_UNAVAILABLE` / `LLM_RATE_LIMITED` / `LLM_TIMEOUT` / `LLM_EMPTY` / `LLM_ERROR` | 可 | Gemini が過負荷、レート制限、低速、または使用不能な応答を返した。 |
| `LLM_TRUNCATED` / `LLM_BLOCKED` / `LLM_BAD_REQUEST` | 不可 | 出力を解析できなかった、安全性/ポリシーによるブロック、または不正なリクエスト（プロンプトが長すぎる）。 |

`retryable: true` のコードは指数バックオフ（1s、2s、4s、8s、上限は約 4 回）で再試行し、それ以外は最終的なものとして扱ってください。呼び出しが成功したものの何かがおかしく見えた場合（例: 通貨記号が取り除かれた）、その修正は呼び出しを失敗させるのではなく、任意の `warnings` 配列で報告されます。

## 設定

すべては環境変数（または `.env`）で制御されます。必須なのは `GEMINI_API_KEY` だけです。複数キーでのラウンドロビン用の `GEMINI_API_KEYS`、`HEADLESS_ENGINE`、`TLS_IMPERSONATE`、`SSRF_GUARD_ENABLED` を含む、ドキュメント化された調整項目については [`.env.example`](.env.example) を参照してください。

## 制限事項

- **JS を多用するサイトにはブラウザーが必要です。** プレーンな HTML のページは `pip install` だけで動作しますが、JavaScript でコンテンツをレンダリングするサイトには `playwright install chromium` が必要です。これがないと、それらのページは空で返ります。
- **強固なアンチボットの壁は失敗することがあります。** 積極的に保護されたサイト（一部の Cloudflare/Datadome の構成、Amazon/Walmart のような大手小売）は、組み込みの取得戦略をすべて突破して `FETCH_BLOCKED` を返すことがあります。
- **キャッシュはメモリ内です。** 結果とフィールドごとの値は、LLM の繰り返し呼び出しを避けるために実行中のプロセス内でキャッシュされますが、キャッシュは再起動でリセットされます。
- **トークンには Google への支払いが発生します。** 抽出の品質とコストは、構成されている Gemini モデル（デフォルトは Flash-Lite）に左右されます。

## ライセンス

[Apache License 2.0](LICENSE)。
