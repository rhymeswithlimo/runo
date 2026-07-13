<p align="center">
  <img src="public/Logo_SVG_v1.0__Runo.svg" width="72" height="72" alt="Logo de Runo">
</p>

<h1 align="center">Runo</h1>

<p align="center">
  <a href="README.md">English</a> ·
  <a href="README.zh-CN.md">简体中文</a> ·
  Español ·
  <a href="README.fr.md">Français</a> ·
  <a href="README.de.md">Deutsch</a> ·
  <a href="README.ja.md">日本語</a>
</p>

<p align="center">Extrae JSON estructurado y tipado desde cualquier URL usando un esquema que tú defines.</p>

<p align="center">
  <img src="https://img.shields.io/badge/python-3.11%2B-2281f7?logo=python&logoColor=white" alt="Python 3.11+">
  <img src="https://img.shields.io/badge/LLM-Google%20Gemini-2281f7?logo=googlegemini&logoColor=white" alt="Con tecnología de Google Gemini">
  <img src="https://img.shields.io/badge/runs-locally-2281f7" alt="Se ejecuta localmente">
  <img src="https://img.shields.io/badge/license-Apache--2.0-2281f7" alt="Apache-2.0">
</p>

> [!NOTE]
> Soy el único responsable de mantenimiento de este proyecto.
> Empezó como un SaaS de código cerrado ([scrapewithruno.com](https://scrapewithruno.com)), pero decidí liberarlo como código abierto :).

---

Describes lo que quieres (un nombre de campo, un tipo, un valor de ejemplo) y Runo descarga la página, renderiza JavaScript si el sitio lo necesita, extrae los datos con un LLM y convierte cada valor al tipo que pediste. Recibes JSON limpio y plano de vuelta.

Sin selectores, sin XPath, nada que mantener. Como el LLM lee por significado en lugar de por posición en el DOM, tu esquema no se rompe la próxima vez que alguien rediseñe el sitio. Un campo que no se encuentre vuelve como `null` en lugar de simplemente desaparecer.

Esta es una versión de código abierto que ejecutas tú mismo. Necesitarás una clave de API de Google Gemini, ese es el único requisito principal.

- **Salida tipada**: cadenas, enteros, decimales, booleanos, fechas ISO 8601 y arreglos tipados, todo convertido estrictamente.
- **Esquema sencillo**: nombre, tipo, ejemplo. Sin ningún DSL.
- **Extracción semántica**: lee el significado, no la posición en el DOM, así que los rediseños no lo rompen.
- **Renderizado inteligente**: primero HTTP simple, navegador headless solo si la página lo necesita.
- **Rutas rápidas**: consulta JSON-LD, OpenGraph, Twitter Cards y oEmbed antes de invocar siquiera a un LLM.
- **Tres modos**: extract (una sola URL), batch (un esquema para muchas URL) o crawl (sigue enlaces desde una URL semilla).
- **Asíncrono integrado**: `extract_async`, `batch_async`, `crawl_async` para quien ejecute esto dentro de su propio bucle de eventos.
- **Tres interfaces**: CLI, servidor local o biblioteca de Python.

## Instalación

Requiere Python 3.11+ (se recomienda Python 3.14).

```bash
pip install -e ".[tls,patchright]"   # los extras mejoran la descarga frente a sistemas anti-bot
playwright install chromium           # descarga única del navegador para páginas con JS
```

> ¡IMPORTANTE!: Runo necesita una clave de API de Gemini para funcionar. Consigue una en
https://aistudio.google.com/apikey.

```bash
cp .env.example .env
# edita .env y asigna GEMINI_API_KEY=...
```

(`.env` se carga automáticamente. También puedes simplemente exportar `GEMINI_API_KEY` en
tu shell.)

El `pip install -e .` de arriba registra un comando `runo` en tu PATH, así que puedes
ejecutarlo desde **cualquier** directorio, sin necesidad de hacer `cd` al clon. Solo mantén la
carpeta clonada en su sitio (la instalación editable enlaza de vuelta a ella) y el mismo
entorno de Python activo.

**`.env` se lee desde el directorio actual en el que te encuentres**,
así que para ejecutar `runo` desde cualquier lugar, exporta la clave globalmente:

```bash
export GEMINI_API_KEY=tu_clave      # Unix/macOS
setx GEMINI_API_KEY tu_clave        # Windows (se aplica a las terminales nuevas)
```

## Uso

La **línea de comandos** es la forma más rápida de probar Runo. Como alternativa, usa la
**biblioteca de Python** cuando lo estés integrando en tu propio código, o el **servidor
local** cuando quieras un endpoint HTTP independiente del lenguaje.

### Opción 1: Servidor HTTP local

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

Sin clave de API ni cabecera de autenticación, es tu servidor local. Endpoints: `/v1/extract`,
`/v1/batch`, `/v1/crawl`. Pasa la configuración de cada petición en un objeto `options` (consulta
[Opciones](#opciones)).

### Opción 2: Biblioteca de Python

Runo funciona como una biblioteca de Python.

```python
from runo import extract

data = extract("https://example.com", [
    {"field": "title",     "type": "string",        "example": "Example Domain"},
    {"field": "paragraph", "type": "string",        "example": "This domain is..."},
])
print(data)   # {"title": "Example Domain", "paragraph": "..."}
```

`batch` ejecuta un esquema sobre muchas URL; `crawl` sigue enlaces desde una URL semilla.
Cada uno tiene una variante `_async` (`extract_async`, `batch_async`, `crawl_async`) para
usar dentro de tu propio bucle de eventos.

```python
from runo import batch, crawl

rows = batch(["https://a.com", "https://b.com"], schema, concurrency=5)
site = crawl("https://blog.com", "https://blog.com/posts/*", schema, max_pages=50)
```

### Opción 3: Línea de comandos

También puedes ejecutar Runo desde la línea de comandos.

```bash
# una sola URL (esquema desde un archivo)
runo extract https://example.com --schema schema.json

# esquema en línea, forzar el navegador headless, escribir el resultado en un archivo
runo extract https://example.com --schema '[{"field":"title","type":"string","example":"x"}]' \
  --render-js always -o out.json

# muchas URL con un esquema (urls.txt es una URL por línea)
runo batch --urls urls.txt --schema schema.json --concurrency 5

# seguir enlaces desde una semilla
runo crawl https://blog.com --pattern "https://blog.com/posts/*" --schema schema.json --max-pages 50

# ejecutar el servidor HTTP local
runo serve --host 127.0.0.1 --port 8000
```

`--schema` acepta la ruta a un archivo JSON o una cadena JSON en línea. Flags comunes:
`--render-js auto|always|never`, `--timeout-ms`, `--no-cache` y `-o out.json`
para escribir la salida en un archivo en lugar de stdout (`--concurrency` para batch;
`--max-pages`, `--max-depth`, `--use-sitemap`, `--ignore-robots` para crawl).

Un archivo `schema.json` no es más que un arreglo JSON de objetos de campo:

```json
[
  {"field": "title", "type": "string", "example": "Example Domain"},
  {"field": "price", "type": "float",  "example": 29.99, "hint": "Use the sale price if present."}
]
```

## Esquema

Cada campo tiene un nombre `field`, un `type`, un valor `example` (un ancla de un solo disparo
para el LLM) y un `hint` opcional. Un buen ejemplo desambigua el formato, por
ejemplo `35` frente a `"35 years old"`, o `2024-01-31` frente a `January 31`.

| Tipo | Conversión |
|---|---|
| `string` | Siempre una cadena |
| `integer` | Se extrae del texto (`"35 years old"` -> `35`) |
| `float` | Se extrae del texto (`"$1.2M"` -> `1200000.0`) |
| `boolean` | Se normaliza (`"✓ Verified"` -> `true`) |
| `date` | ISO 8601 (`YYYY-MM-DD`); las fechas relativas se resuelven |
| `array<string>` / `array<integer>` / `array<float>` | Arreglo JSON (vacío `[]` si no coincidió nada) |

Los campos que no se pueden resolver vuelven como `null`, nunca se descartan, así que `data` siempre tiene las
mismas claves que tu esquema.

### Pistas (hints)

El comportamiento por defecto suele bastar. Recurre a `hint` cuando una página muestre dos valores
para el mismo concepto y quieras uno concreto (`"Use sale price if present."`),
cuando el nombre del campo sea ambiguo (`author` en un artículo republicado), o cuando el
sitio use una redacción poco evidente (`likes` frente a `reactions`). Mantén las pistas cortas y úsalas
solo cuando haga falta.

### Ejemplos prácticos

Página de producto:

```json
[
  { "field": "title",    "type": "string",        "example": "MacBook Pro 14\"" },
  { "field": "price",    "type": "float",         "example": 1999.00, "hint": "Use sale price if present." },
  { "field": "inStock",  "type": "boolean",       "example": true },
  { "field": "rating",   "type": "float",         "example": 4.6 },
  { "field": "tags",     "type": "array<string>", "example": ["laptop", "apple"] }
]
```

Artículo / entrada de blog:

```json
[
  { "field": "headline",    "type": "string", "example": "OpenAI ships o3" },
  { "field": "author",      "type": "string", "example": "Cade Metz" },
  { "field": "publishedAt", "type": "date",   "example": "2024-12-20" },
  { "field": "summary",     "type": "string", "example": "A short summary.", "hint": "1-3 sentences." }
]
```

### Consejos

- **Mantén los esquemas ajustados.** De 4 a 10 campos se extraen con más precisión que 30. Divide los grandes en dos llamadas.
- **Prefiere `array<T>` a cadenas con delimitadores.** Declara `array<string>` y deja que Runo construya la lista en lugar de partir tú mismo una cadena unida.
- **Los nombres importan.** `firstName` frente a `givenName` producen extracciones sutilmente distintas. Usa el término que usan tus sitios objetivo; camelCase funciona bien.

## Opciones

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

Los flags de arriba no son exclusivos de la CLI. Las mismas opciones están disponibles desde la
biblioteca de Python y por HTTP, con los mismos nombres.

**Biblioteca de Python.** Pasa las opciones como argumentos con nombre en snake_case (el
`--render-js` de la CLI pasa a ser `render_js`, `--max-pages` pasa a ser `max_pages`, y así
sucesivamente). La URL o la lista de URL, el esquema y el patrón de enlaces del crawl son
argumentos posicionales:

```python
from runo import extract, crawl

# forzar headless, timeout más largo, rellenar nulls desde imágenes, omitir la caché
extract("https://example.com", schema,
        render_js="always", timeout_ms=30000, process_images=True, no_cache=True)

# la configuración específica del crawl también son argumentos con nombre
crawl("https://blog.com", "https://blog.com/posts/*", schema,
      max_pages=100, max_depth=3, use_sitemap=True)
```

Cada función tiene su gemela `_async` (`extract_async`, `batch_async`,
`crawl_async`) con una firma idéntica para usar dentro de tu propio bucle de eventos.

**Por HTTP.** Las opciones van en el cuerpo de la petición. `extract` y `batch` toman un
objeto `options`; `crawl` mantiene su configuración de rastreo en un objeto `crawl` junto a
un objeto `options` compartido:

```json
{
  "seed_url": "https://blog.com",
  "schema": [{ "field": "title", "type": "string", "example": "Example post" }],
  "crawl": { "follow_pattern": "https://blog.com/posts/*", "max_pages": 100, "use_sitemap": true },
  "options": { "render_js": "always", "timeout_ms": 30000 }
}
```

**Batch API.** `batch` y `crawl` también aceptan `async_mode`, que enruta las
extracciones por la Batch API de Gemini: más barata, hasta 24h de latencia, con una
reserva transparente a modo síncrono en caso de fallo.

## Respuesta

Cada extracción devuelve la misma forma:

```json
{
  "url": "https://example.com",
  "status": "success",
  "render_mode": "plain",
  "data": { "title": "Example Domain" },
  "images_processed": null
}
```

| Campo | Notas |
|---|---|
| `status` | `"success"` o `"error"`. |
| `render_mode` | `"plain"` (bastó con una descarga HTTP simple) o `"headless"` (se escaló a un navegador). |
| `data` | Con las claves de los nombres `field` de tu esquema; los campos irresolubles son `null`. |
| `warnings` | Arreglo opcional de notas de conversión (p. ej. `"coerced 'price' from '$19.99' to 19.99"`); se omite cuando está vacío. |
| `images_processed` | Número de imágenes leídas por la pasada de visión; `null` cuando no se ejecutó. |

El `extract()` de la biblioteca de Python devuelve solo el diccionario `data` y lanza
`RunoError` en caso de fallo; `batch()`/`crawl()` devuelven los objetos de resultado completos y
no lanzan excepción por el fallo de una sola página (revisa el `status` de cada entrada).

## Descarga y renderizado

Runo prueba primero la ruta más barata y escala solo cuando una página lo requiere.
Con `render_js: "auto"` (el valor por defecto), empieza con una descarga HTTP simple y
cambia a un navegador headless sigiloso cuando detecta señales de problemas:

- Una firma conocida de bloqueo anti-bot (Cloudflare, Datadome, PerimeterX, Akamai, Incapsula).
- Un cuerpo de menos de ~500 caracteres, o texto visible escaso tras una carga HTML grande (una cáscara de JS).
- Marcadores de frameworks de JavaScript en el HTML.
- Un HTTP `402`, `403`, `406`, `429` o `503`.
- Un esquema que pide números en una página casi sin dígitos (widgets de clima/tableros).

El escalado es transparente: la forma de la respuesta es idéntica, solo cambia `render_mode`
de `plain` a `headless`.

Para sortear la protección anti-bot va subiendo desde la opción más barata, deteniéndose en la
primera que funcione, y recuerda por host lo que necesita cada sitio para que las llamadas posteriores omitan
los intentos que no van a servir:

- Una **descarga HTTP simple** para HTML estático.
- Una **descarga con suplantación de TLS** (extra `[tls]`) que imita la huella TLS de un navegador real, venciendo las comprobaciones pasivas JA3/JA4.
- Un **navegador headless reforzado** (`[patchright]` / camoufox) con un paquete de huellas de canvas/WebGL/audio, venciendo la detección CDP y los muros de huellas.
- **Persistencia de cookies por host**, para que los retos de confianza progresiva solo haya que superarlos una vez.
- Una **reserva de archivo** (Wayback / vista de lectura) como último recurso cuando el sitio en vivo es inalcanzable.

Los sitios protegidos de forma agresiva (algunas configuraciones de Cloudflare/Datadome, grandes minoristas como
Amazon/Walmart) aún pueden vencer todo esto y volver como `FETCH_BLOCKED`.

## Modos batch y crawl

`batch` ejecuta un esquema sobre una lista de URL que ya tienes. `crawl` parte
de una URL semilla, sigue los enlaces que coinciden con un patrón y descubre páginas por ti.

```python
from runo import batch, crawl

rows = batch(["https://a.com", "https://b.com"], schema, concurrency=5)

site = crawl("https://blog.com", "https://blog.com/posts/*", schema,
             max_pages=50, max_depth=2, use_sitemap=False, ignore_robots=False)
```

El crawler respeta `robots.txt` (salvo que `ignore_robots=True`), puede sembrar desde
`sitemap.xml` (`use_sitemap=True`), y aplica jitter por host más un retroceso adaptativo
para que no satures un sitio. `crawl` devuelve resultados por página más un
bloque `crawl_meta`:

```json
{
  "results": [ { "url": "...", "status": "success", "data": { } } ],
  "crawl_meta": { "pages_visited": 17, "pages_skipped": 3, "pages_failed": 0, "cancelled": false }
}
```

Lo mejor es usar `batch` cuando ya tienes la lista de URL (incluidos feeds paginados
que puedes construir como `?page=1..N`) y usar `crawl` cuando tienes una URL y quieres
descubrir páginas relacionadas.

## Leer datos desde imágenes

Activa `process_images=true` (opción / flag `--process-images`) y, tras la
pasada de texto, cualquier campo que siga en `null` dispara una pasada de visión: Runo puntúa las
etiquetas `<img>` de la página frente a los nombres de los campos que faltan, descarga hasta 3 de los mejores
candidatos y los envía a Gemini en una única llamada multimodal dirigida solo a
esos campos. Fusiona lo que encuentre e informa el conteo en
`images_processed`. Si la pasada de imágenes falla, se devuelve sin cambios el resultado original de solo texto.
Ideal para datos incrustados en imágenes (superposiciones de precio, estadísticas en un
cartel, tarjetas de marketplace); cuesta tokens adicionales, así que está desactivada por defecto.

## Errores

Los fallos usan una envoltura consistente. En una extracción individual, el `status` de nivel superior
es `"error"`; dentro de un `batch`/`crawl`, cada entrada individual lleva el mismo
objeto `error` mientras que la llamada global sigue teniendo éxito.

```json
{ "status": "error", "error": { "code": "FETCH_BLOCKED", "message": "...", "retryable": true } }
```

| Código | ¿Reintentable? | Significado |
|---|---|---|
| `SCHEMA_INVALID` | no | El esquema está mal formado (falta `field`, tipo desconocido). |
| `TYPE_COERCION_FAILED` | no | Un valor no se pudo convertir a su tipo declarado. |
| `URL_UNREACHABLE` | sí | Fallo de DNS/red, o bloqueado por la protección SSRF. |
| `TIMEOUT` | sí | La página superó `timeout_ms`. |
| `FETCH_BLOCKED` | sí | El anti-bot venció todas las estrategias de descarga gratuitas. |
| `LLM_UNAVAILABLE` / `LLM_RATE_LIMITED` / `LLM_TIMEOUT` / `LLM_EMPTY` / `LLM_ERROR` | sí | Gemini estaba sobrecargado, limitado por tasa, lento, o devolvió una respuesta inutilizable. |
| `LLM_TRUNCATED` / `LLM_BLOCKED` / `LLM_BAD_REQUEST` | no | La salida no se pudo parsear, un bloqueo de seguridad/política, o una petición inválida (prompt demasiado largo). |

Reintenta los códigos con `retryable: true` con retroceso exponencial (1s, 2s, 4s, 8s, con tope
en ~4 intentos); trata el resto como terminales. Cuando una llamada tiene éxito pero algo
parecía raro (p. ej. se eliminó un símbolo de moneda), la corrección se informa en el
arreglo opcional `warnings` en lugar de hacer fallar la llamada.

## Configuración

Todo se controla mediante variables de entorno (o `.env`). Solo `GEMINI_API_KEY`
es obligatoria; consulta [`.env.example`](.env.example) para los parámetros documentados,
incluidos `GEMINI_API_KEYS` para round-robin entre varias claves, `HEADLESS_ENGINE`,
`TLS_IMPERSONATE` y `SSRF_GUARD_ENABLED`.

## Limitaciones

- **Los sitios con mucho JS necesitan el navegador.** Las páginas de HTML plano funcionan solo con
  `pip install`, pero los sitios que renderizan contenido con JavaScript necesitan
  `playwright install chromium`. Sin él, esas páginas vuelven vacías.
- **Los muros anti-bot duros pueden fallar.** Los sitios protegidos de forma agresiva (algunas
  configuraciones de Cloudflare/Datadome, grandes minoristas como Amazon/Walmart) pueden vencer todas las
  estrategias de descarga integradas y devolver `FETCH_BLOCKED`.
- **La caché es en memoria.** Los resultados y los valores por campo se cachean dentro de un
  proceso en ejecución para evitar llamadas repetidas al LLM, pero la caché se reinicia al reiniciar.
- **Le pagas a Google por los tokens.** La calidad y el coste de la extracción dependen del modelo
  Gemini que esté configurado (Flash-Lite por defecto).

## Licencia

[Licencia Apache 2.0](LICENSE).
