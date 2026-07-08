<p align="center">
  <img src="public/Logo_SVG_v1.0__Runo.svg" width="72" height="72" alt="Runo-Logo">
</p>

<h1 align="center">Runo</h1>

<p align="center">
  <a href="README.md">English</a> ·
  <a href="README.zh-CN.md">简体中文</a> ·
  <a href="README.es.md">Español</a> ·
  <a href="README.fr.md">Français</a> ·
  Deutsch ·
  <a href="README.ja.md">日本語</a>
</p>

<p align="center">Extrahiere strukturiertes, typisiertes JSON aus jeder URL mithilfe eines von dir definierten Schemas.</p>

<p align="center">
  <img src="https://img.shields.io/badge/python-3.11%2B-2281f7?logo=python&logoColor=white" alt="Python 3.11+">
  <img src="https://img.shields.io/badge/LLM-Google%20Gemini-2281f7?logo=googlegemini&logoColor=white" alt="Angetrieben von Google Gemini">
  <img src="https://img.shields.io/badge/runs-locally-2281f7" alt="Läuft lokal">
  <img src="https://img.shields.io/badge/license-Apache--2.0-2281f7" alt="Apache-2.0">
</p>

> [!NOTE]
> Ich bin der alleinige Betreuer dieses Projekts.
> Es begann als Closed-Source-SaaS ([scrapewithruno.com](https://scrapewithruno.com)), aber ich habe mich entschieden, es als Open Source freizugeben :).

---

Du beschreibst, was du willst (einen Feldnamen, einen Typ, einen Beispielwert), und Runo ruft die Seite ab, rendert JavaScript, falls die Website es benötigt, extrahiert die Daten mit einem LLM und wandelt jeden Wert in den von dir gewünschten Typ um. Du bekommst sauberes, flaches JSON zurück.

Keine Selektoren, kein XPath, nichts zu warten. Da das LLM nach Bedeutung statt nach DOM-Position liest, bricht dein Schema nicht, wenn jemand die Website neu gestaltet. Ein Feld, das nicht gefunden wird, kommt als `null` zurück, statt einfach zu verschwinden.

Dies ist die Open-Source-Version, die du selbst betreibst. Du brauchst einen Google-Gemini-API-Schlüssel, das ist die einzige wesentliche Voraussetzung.

- **Typisierte Ausgabe**: Strings, Ganzzahlen, Fließkommazahlen, Booleans, ISO-8601-Daten und typisierte Arrays, alles streng umgewandelt.
- **Einfaches Schema**: Name, Typ, Beispiel. Keine DSL.
- **Semantische Extraktion**: liest die Bedeutung, nicht die DOM-Position, sodass Neugestaltungen es nicht brechen.
- **Intelligentes Rendering**: zuerst einfaches HTTP, ein Headless-Browser nur, wenn die Seite es braucht.
- **Schnellpfade**: prüft JSON-LD, OpenGraph, Twitter Cards und oEmbed, bevor überhaupt ein LLM aufgerufen wird.
- **Drei Modi**: extract (eine einzelne URL), batch (ein Schema über viele URLs) oder crawl (folgt Links ab einer Ausgangs-URL).
- **Async eingebaut**: `extract_async`, `batch_async`, `crawl_async` für alle, die dies in ihrer eigenen Event-Loop ausführen.
- **Drei Schnittstellen**: CLI, lokaler Server oder Python-Bibliothek.

## Einrichtung

Erfordert Python 3.11+ (Python 3.14 empfohlen).

```bash
pip install -e ".[tls,patchright]"   # die Extras verbessern das Abrufen gegen Anti-Bot-Systeme
playwright install chromium           # einmaliger Browser-Download für JS-Seiten
```

> WICHTIG!: Runo benötigt einen Gemini-API-Schlüssel, um zu funktionieren. Hol dir einen unter
https://aistudio.google.com/apikey.

```bash
cp .env.example .env
# bearbeite .env und setze GEMINI_API_KEY=...
```

(`.env` wird automatisch geladen. Du kannst `GEMINI_API_KEY` auch einfach in
deiner Shell exportieren.)

Das obige `pip install -e .` registriert einen `runo`-Befehl in deinem PATH, sodass du ihn
aus **jedem** Verzeichnis ausführen kannst, ohne in den Klon zu `cd`-en. Lass einfach den
geklonten Ordner an Ort und Stelle (die editierbare Installation verweist darauf zurück) und dieselbe
Python-Umgebung aktiv.

**`.env` wird aus dem aktuellen Verzeichnis gelesen, in dem du dich befindest**,
um `runo` also von überall auszuführen, exportiere den Schlüssel stattdessen global:

```bash
export GEMINI_API_KEY=dein_schluessel      # Unix/macOS
setx GEMINI_API_KEY dein_schluessel        # Windows (gilt für neue Terminals)
```

## Verwendung

Die **Kommandozeile** ist der schnellste Weg, Runo auszuprobieren. Alternativ greifst du zur
**Python-Bibliothek**, wenn du es in deinen eigenen Code einbaust, oder zum **lokalen
Server**, wenn du einen sprachunabhängigen HTTP-Endpunkt willst.

### Option 1: Lokaler HTTP-Server

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

Kein API-Schlüssel, kein Auth-Header, es ist dein lokaler Server. Endpunkte: `/v1/extract`,
`/v1/batch`, `/v1/crawl`. Übergib Einstellungen pro Anfrage in einem `options`-Objekt (siehe
[Optionen](#optionen)).

### Option 2: Python-Bibliothek

Runo funktioniert als Python-Bibliothek.

```python
from runo import extract

data = extract("https://example.com", [
    {"field": "title",     "type": "string",        "example": "Example Domain"},
    {"field": "paragraph", "type": "string",        "example": "This domain is..."},
])
print(data)   # {"title": "Example Domain", "paragraph": "..."}
```

`batch` führt ein Schema über viele URLs aus; `crawl` folgt Links ab einer Ausgangs-URL.
Jedes hat eine `_async`-Variante (`extract_async`, `batch_async`, `crawl_async`) zur
Verwendung in deiner eigenen Event-Loop.

```python
from runo import batch, crawl

rows = batch(["https://a.com", "https://b.com"], schema, concurrency=5)
site = crawl("https://blog.com", "https://blog.com/posts/*", schema, max_pages=50)
```

### Option 3: Kommandozeile

Du kannst Runo auch über die Kommandozeile ausführen.

```bash
# eine einzelne URL (Schema aus einer Datei)
runo extract https://example.com --schema schema.json

# Inline-Schema, den Headless-Browser erzwingen, Ergebnis in eine Datei schreiben
runo extract https://example.com --schema '[{"field":"title","type":"string","example":"x"}]' \
  --render-js always -o out.json

# viele URLs mit einem Schema (urls.txt enthält eine URL pro Zeile)
runo batch --urls urls.txt --schema schema.json --concurrency 5

# Links ab einer Ausgangs-URL folgen
runo crawl https://blog.com --pattern "https://blog.com/posts/*" --schema schema.json --max-pages 50

# den lokalen HTTP-Server starten
runo serve --host 127.0.0.1 --port 8000
```

`--schema` akzeptiert einen Pfad zu einer JSON-Datei oder einen Inline-JSON-String. Gängige Flags:
`--render-js auto|always|never`, `--timeout-ms`, `--no-cache` und `-o out.json`,
um die Ausgabe in eine Datei statt nach stdout zu schreiben (`--concurrency` für batch;
`--max-pages`, `--max-depth`, `--use-sitemap`, `--ignore-robots` für crawl).

Eine `schema.json`-Datei ist einfach ein JSON-Array von Feldobjekten:

```json
[
  {"field": "title", "type": "string", "example": "Example Domain"},
  {"field": "price", "type": "float",  "example": 29.99, "hint": "Use the sale price if present."}
]
```

## Schema

Jedes Feld hat einen `field`-Namen, einen `type`, einen `example`-Wert (einen One-Shot-Anker
für das LLM) und einen optionalen `hint`. Ein gutes Beispiel macht das Format eindeutig, zum
Beispiel `35` gegenüber `"35 years old"`, oder `2024-01-31` gegenüber `January 31`.

| Typ | Umwandlung |
|---|---|
| `string` | Immer ein String |
| `integer` | Aus Text geparst (`"35 years old"` -> `35`) |
| `float` | Aus Text geparst (`"$1.2M"` -> `1200000.0`) |
| `boolean` | Normalisiert (`"✓ Verified"` -> `true`) |
| `date` | ISO 8601 (`YYYY-MM-DD`); relative Daten werden aufgelöst |
| `array<string>` / `array<integer>` / `array<float>` | JSON-Array (leer `[]`, wenn nichts passte) |

Nicht auflösbare Felder kommen als `null` zurück, werden nie weggelassen, sodass `data` immer die
gleichen Schlüssel wie dein Schema hat.

### Hinweise (Hints)

Das Standardverhalten reicht meist aus. Greif zu `hint`, wenn eine Seite zwei Werte
für dasselbe Konzept zeigt und du einen bestimmten willst (`"Use sale price if present."`),
wenn der Feldname mehrdeutig ist (`author` bei einem wiederveröffentlichten Artikel), oder wenn die
Website ungewöhnliche Formulierungen verwendet (`likes` gegenüber `reactions`). Halte Hinweise kurz und nutze sie
nur bei Bedarf.

### Ausgearbeitete Beispiele

Produktseite:

```json
[
  { "field": "title",    "type": "string",        "example": "MacBook Pro 14\"" },
  { "field": "price",    "type": "float",         "example": 1999.00, "hint": "Use sale price if present." },
  { "field": "inStock",  "type": "boolean",       "example": true },
  { "field": "rating",   "type": "float",         "example": 4.6 },
  { "field": "tags",     "type": "array<string>", "example": ["laptop", "apple"] }
]
```

Artikel / Blogbeitrag:

```json
[
  { "field": "headline",    "type": "string", "example": "OpenAI ships o3" },
  { "field": "author",      "type": "string", "example": "Cade Metz" },
  { "field": "publishedAt", "type": "date",   "example": "2024-12-20" },
  { "field": "summary",     "type": "string", "example": "A short summary.", "hint": "1-3 sentences." }
]
```

### Tipps

- **Halte Schemas knapp.** 4 bis 10 Felder werden genauer extrahiert als 30. Teile große Schemas in zwei Aufrufe.
- **Bevorzuge `array<T>` gegenüber Strings mit Trennzeichen.** Deklariere `array<string>` und lass Runo die Liste bauen, statt selbst einen zusammengefügten String zu zerteilen.
- **Namen sind wichtig.** `firstName` gegenüber `givenName` erzeugen subtil unterschiedliche Extraktionen. Verwende den Begriff, den deine Zielwebsites nutzen; camelCase funktioniert gut.

## Optionen

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

Die obigen Flags sind nicht nur für die CLI. Dieselben Optionen sind aus der Python-Bibliothek
und über HTTP verfügbar, unter denselben Namen.

**Python-Bibliothek.** Übergib Optionen als Schlüsselwortargumente in snake_case (das
`--render-js` der CLI wird zu `render_js`, `--max-pages` wird zu `max_pages`, und so
weiter). Die URL oder URL-Liste, das Schema und das Crawl-Folgemuster sind positionsbasierte
Argumente:

```python
from runo import extract, crawl

# Headless erzwingen, längeres Timeout, Nulls aus Bildern füllen, Cache überspringen
extract("https://example.com", schema,
        render_js="always", timeout_ms=30000, process_images=True, no_cache=True)

# crawl-spezifische Einstellungen sind ebenfalls Schlüsselwortargumente
crawl("https://blog.com", "https://blog.com/posts/*", schema,
      max_pages=100, max_depth=3, use_sitemap=True)
```

Jede Funktion hat einen `_async`-Zwilling (`extract_async`, `batch_async`,
`crawl_async`) mit identischer Signatur zur Verwendung in deiner eigenen Event-Loop.

**Über HTTP.** Optionen kommen in den Anfragekörper. `extract` und `batch` nehmen ein
`options`-Objekt; `crawl` behält seine Crawl-Einstellungen in einem `crawl`-Objekt neben
einem gemeinsamen `options`-Objekt:

```json
{
  "seed_url": "https://blog.com",
  "schema": [{ "field": "title", "type": "string", "example": "Example post" }],
  "crawl": { "follow_pattern": "https://blog.com/posts/*", "max_pages": 100, "use_sitemap": true },
  "options": { "render_js": "always", "timeout_ms": 30000 }
}
```

**Batch API.** `batch` und `crawl` akzeptieren außerdem `async_mode`, das die
Extraktionen über Geminis Batch API leitet: günstiger, bis zu 24h Latenz, mit
transparentem Rückfall auf den synchronen Modus bei Fehlschlag.

## Antwort

Jede Extraktion gibt dieselbe Form zurück:

```json
{
  "url": "https://example.com",
  "status": "success",
  "render_mode": "plain",
  "data": { "title": "Example Domain" },
  "images_processed": null
}
```

| Feld | Anmerkungen |
|---|---|
| `status` | `"success"` oder `"error"`. |
| `render_mode` | `"plain"` (ein einfacher HTTP-Abruf hat gereicht) oder `"headless"` (Eskalation zu einem Browser). |
| `data` | Nach den `field`-Namen deines Schemas indexiert; nicht auflösbare Felder sind `null`. |
| `warnings` | Optionales Array von Umwandlungshinweisen (z. B. `"coerced 'price' from '$19.99' to 19.99"`); entfällt, wenn leer. |
| `images_processed` | Anzahl der von der Vision-Passe gelesenen Bilder; `null`, wenn sie nicht lief. |

Das `extract()` der Python-Bibliothek gibt nur das `data`-Dict zurück und wirft
`RunoError` bei Fehlschlag; `batch()`/`crawl()` geben die vollständigen Ergebnisobjekte zurück und
werfen keine Ausnahme bei einem einzelnen Seitenfehler (prüfe den `status` jedes Eintrags).

## Abrufen & Rendern

Runo versucht zuerst den günstigsten Pfad und eskaliert nur, wenn eine Seite es erfordert.
Unter `render_js: "auto"` (dem Standard) beginnt es mit einem einfachen HTTP-Abruf und
wechselt zu einem Stealth-Headless-Browser, wenn es Anzeichen von Schwierigkeiten sieht:

- Eine bekannte Anti-Bot-Blocksignatur (Cloudflare, Datadome, PerimeterX, Akamai, Incapsula).
- Ein Körper unter ~500 Zeichen, oder spärlicher sichtbarer Text hinter einer großen HTML-Nutzlast (eine JS-Hülle).
- JavaScript-Framework-Marker im HTML.
- Ein HTTP `402`, `403`, `406`, `429` oder `503`.
- Ein Schema, das Zahlen auf einer Seite mit fast keinen Ziffern verlangt (Wetter-/Dashboard-Widgets).

Die Eskalation ist transparent: Die Antwortform ist identisch, nur `render_mode`
wechselt von `plain` zu `headless`.

Um Bot-Schutz zu umgehen, arbeitet es sich von der günstigsten Option nach oben, hält bei der
ersten, die gelingt, und merkt sich pro Host, was eine Website braucht, sodass spätere Aufrufe
die Versuche überspringen, die nicht funktionieren werden:

- Ein **einfacher HTTP-Abruf** für statisches HTML.
- Ein **TLS-imitierender Abruf** (Extra `[tls]`), der den TLS-Fingerabdruck eines echten Browsers nachahmt und passive JA3/JA4-Prüfungen aushebelt.
- Ein **gehärteter Headless-Browser** (`[patchright]` / camoufox) mit einem Canvas-/WebGL-/Audio-Fingerabdruck-Bundle, das CDP-Erkennung und Fingerabdruck-Mauern aushebelt.
- **Cookie-Persistenz pro Host**, sodass Challenges mit progressivem Vertrauen nur einmal bestanden werden müssen.
- Ein **Archiv-Rückfall** (Wayback / Leseansicht) als letztes Mittel, wenn die Live-Website nicht erreichbar ist.

Aggressiv geschützte Websites (manche Cloudflare-/Datadome-Setups, großer Einzelhandel wie
Amazon/Walmart) können all dies dennoch aushebeln und als `FETCH_BLOCKED` zurückkommen.

## Batch- und Crawl-Modi

`batch` führt ein Schema über eine Liste von URLs aus, die du bereits hast. `crawl` startet
von einer Ausgangs-URL, folgt Links, die zu einem Muster passen, und entdeckt Seiten für dich.

```python
from runo import batch, crawl

rows = batch(["https://a.com", "https://b.com"], schema, concurrency=5)

site = crawl("https://blog.com", "https://blog.com/posts/*", schema,
             max_pages=50, max_depth=2, use_sitemap=False, ignore_robots=False)
```

Der Crawler respektiert `robots.txt` (außer bei `ignore_robots=True`), kann aus
`sitemap.xml` starten (`use_sitemap=True`), und wendet Jitter pro Host plus adaptives
Back-off an, damit du eine Website nicht überlastest. `crawl` gibt Ergebnisse pro Seite plus einen
`crawl_meta`-Block zurück:

```json
{
  "results": [ { "url": "...", "status": "success", "data": { } } ],
  "crawl_meta": { "pages_visited": 17, "pages_skipped": 3, "pages_failed": 0, "cancelled": false }
}
```

Am besten nutzt du `batch`, wenn du die URL-Liste hast (einschließlich paginierter Feeds,
die du als `?page=1..N` bauen kannst), und `crawl`, wenn du eine URL hast und
verwandte Seiten entdecken willst.

## Daten aus Bildern lesen

Setze `process_images=true` (Option / Flag `--process-images`), und nach der
Textpasse löst jedes noch `null`-Feld eine Vision-Passe aus: Runo bewertet die
`<img>`-Tags der Seite anhand der fehlenden Feldnamen, ruft bis zu 3 der besten
Kandidaten ab und sendet sie in einem einzigen multimodalen Aufruf an Gemini, der nur
diese Felder anvisiert. Es führt zusammen, was es findet, und meldet die Anzahl in
`images_processed`. Wenn die Bildpasse fehlschlägt, wird das ursprüngliche reine Textergebnis unverändert zurückgegeben.
Am besten für Daten, die in Bildern eingebettet sind (Preis-Overlays, Statistiken auf einem
Poster, Marktplatz-Karten); es kostet zusätzliche Tokens, daher ist es standardmäßig aus.

## Fehler

Fehlschläge verwenden eine einheitliche Hülle. Bei einer einzelnen Extraktion ist der `status` auf oberster Ebene
`"error"`; innerhalb eines `batch`/`crawl` tragen einzelne Einträge dasselbe
`error`-Objekt, während der Gesamtaufruf dennoch gelingt.

```json
{ "status": "error", "error": { "code": "FETCH_BLOCKED", "message": "...", "retryable": true } }
```

| Code | Wiederholbar | Bedeutung |
|---|---|---|
| `SCHEMA_INVALID` | nein | Das Schema ist fehlerhaft (`field` fehlt, unbekannter Typ). |
| `TYPE_COERCION_FAILED` | nein | Ein Wert konnte nicht in seinen deklarierten Typ umgewandelt werden. |
| `URL_UNREACHABLE` | ja | DNS-/Netzwerkfehler, oder vom SSRF-Schutz blockiert. |
| `TIMEOUT` | ja | Die Seite überschritt `timeout_ms`. |
| `FETCH_BLOCKED` | ja | Der Anti-Bot hat jede kostenlose Abruf-Strategie ausgehebelt. |
| `LLM_UNAVAILABLE` / `LLM_RATE_LIMITED` / `LLM_TIMEOUT` / `LLM_EMPTY` / `LLM_ERROR` | ja | Gemini war überlastet, ratenbegrenzt, langsam, oder gab eine unbrauchbare Antwort zurück. |
| `LLM_TRUNCATED` / `LLM_BLOCKED` / `LLM_BAD_REQUEST` | nein | Die Ausgabe konnte nicht geparst werden, eine Sicherheits-/Richtlinienblockade, oder eine ungültige Anfrage (Prompt zu lang). |

Wiederhole die Codes mit `retryable: true` mit exponentiellem Back-off (1s, 2s, 4s, 8s, gedeckelt
bei ~4 Versuchen); behandle den Rest als endgültig. Wenn ein Aufruf gelingt, aber etwas
merkwürdig aussah (z. B. ein entferntes Währungssymbol), wird die Korrektur im
optionalen `warnings`-Array gemeldet, statt den Aufruf scheitern zu lassen.

## Konfiguration

Alles wird über Umgebungsvariablen (oder `.env`) gesteuert. Nur `GEMINI_API_KEY`
ist erforderlich; siehe [`.env.example`](.env.example) für die dokumentierten Stellschrauben,
darunter `GEMINI_API_KEYS` für Round-Robin über mehrere Schlüssel, `HEADLESS_ENGINE`,
`TLS_IMPERSONATE` und `SSRF_GUARD_ENABLED`.

## Einschränkungen

- **JS-lastige Websites brauchen den Browser.** Reine HTML-Seiten funktionieren allein mit
  `pip install`, aber Websites, die Inhalte mit JavaScript rendern, brauchen
  `playwright install chromium`. Ohne ihn kommen diese Seiten leer zurück.
- **Harte Anti-Bot-Mauern können scheitern.** Aggressiv geschützte Websites (manche
  Cloudflare-/Datadome-Setups, großer Einzelhandel wie Amazon/Walmart) können jede
  eingebaute Abruf-Strategie aushebeln und `FETCH_BLOCKED` zurückgeben.
- **Der Cache liegt im Arbeitsspeicher.** Ergebnisse und Werte pro Feld werden innerhalb eines
  laufenden Prozesses zwischengespeichert, um wiederholte LLM-Aufrufe zu vermeiden, aber der Cache wird beim Neustart zurückgesetzt.
- **Du zahlst Google für Tokens.** Extraktionsqualität und -kosten hängen vom jeweils konfigurierten
  Gemini-Modell ab (standardmäßig Flash-Lite).

## Lizenz

[Apache-Lizenz 2.0](LICENSE).
