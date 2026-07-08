<p align="center">
  <img src="public/Logo_SVG_v1.0__Runo.svg" width="72" height="72" alt="Logo Runo">
</p>

<h1 align="center">Runo</h1>

<p align="center">
  <a href="README.md">English</a> ·
  <a href="README.zh-CN.md">简体中文</a> ·
  <a href="README.es.md">Español</a> ·
  Français ·
  <a href="README.de.md">Deutsch</a> ·
  <a href="README.ja.md">日本語</a>
</p>

<p align="center">Extrayez du JSON structuré et typé depuis n'importe quelle URL à l'aide d'un schéma que vous définissez.</p>

<p align="center">
  <img src="https://img.shields.io/badge/python-3.11%2B-2281f7?logo=python&logoColor=white" alt="Python 3.11+">
  <img src="https://img.shields.io/badge/LLM-Google%20Gemini-2281f7?logo=googlegemini&logoColor=white" alt="Propulsé par Google Gemini">
  <img src="https://img.shields.io/badge/runs-locally-2281f7" alt="Fonctionne en local">
  <img src="https://img.shields.io/badge/license-Apache--2.0-2281f7" alt="Apache-2.0">
</p>

> [!NOTE]
> Je suis le seul mainteneur de ce projet.
> Il a commencé comme un SaaS propriétaire ([scrapewithruno.com](https://scrapewithruno.com)), mais j'ai décidé de le passer en open source :).

---

Vous décrivez ce que vous voulez (un nom de champ, un type, une valeur d'exemple) et Runo récupère la page, effectue le rendu JavaScript si le site en a besoin, extrait les données avec un LLM et convertit chaque valeur vers le type demandé. Vous récupérez du JSON propre et plat.

Pas de sélecteurs, pas de XPath, rien à maintenir. Comme le LLM lit selon le sens plutôt que selon la position dans le DOM, votre schéma ne casse pas la prochaine fois que quelqu'un refond le site. Un champ introuvable revient à `null` au lieu de simplement disparaître.

Ceci est la version open source que vous exécutez vous-même. Il vous faudra une clé d'API Google Gemini, c'est la seule vraie exigence.

- **Sortie typée** : chaînes, entiers, flottants, booléens, dates ISO 8601 et tableaux typés, tout converti strictement.
- **Schéma simple** : nom, type, exemple. Aucun DSL.
- **Extraction sémantique** : lit le sens, pas la position dans le DOM, donc les refontes ne le cassent pas.
- **Rendu intelligent** : d'abord du HTTP simple, un navigateur headless seulement si la page en a besoin.
- **Chemins rapides** : vérifie JSON-LD, OpenGraph, Twitter Cards et oEmbed avant même d'appeler un LLM.
- **Trois modes** : extract (une seule URL), batch (un schéma sur plusieurs URL) ou crawl (suit les liens depuis une URL de départ).
- **Asynchrone intégré** : `extract_async`, `batch_async`, `crawl_async` pour quiconque exécute ceci dans sa propre boucle d'événements.
- **Trois interfaces** : CLI, serveur local ou bibliothèque Python.

## Installation

Nécessite Python 3.11+ (Python 3.14 recommandé).

```bash
pip install -e ".[tls,patchright]"   # les extras améliorent la récupération face aux systèmes anti-bot
playwright install chromium           # téléchargement unique du navigateur pour les pages JS
```

> IMPORTANT ! : Runo nécessite une clé d'API Gemini pour fonctionner. Obtenez-en une sur
https://aistudio.google.com/apikey.

```bash
cp .env.example .env
# modifiez .env et définissez GEMINI_API_KEY=...
```

(`.env` est chargé automatiquement. Vous pouvez aussi simplement exporter `GEMINI_API_KEY` dans
votre shell.)

Le `pip install -e .` ci-dessus enregistre une commande `runo` dans votre PATH, ce qui vous permet
de l'exécuter depuis **n'importe quel** répertoire, sans avoir à faire `cd` dans le clone. Gardez simplement le
dossier cloné en place (l'installation éditable pointe vers lui) et le même
environnement Python actif.

**`.env` est lu depuis le répertoire courant dans lequel vous vous trouvez**,
donc pour exécuter `runo` depuis n'importe où, exportez plutôt la clé globalement :

```bash
export GEMINI_API_KEY=votre_cle      # Unix/macOS
setx GEMINI_API_KEY votre_cle        # Windows (s'applique aux nouveaux terminaux)
```

## Utilisation

La **ligne de commande** est le moyen le plus rapide d'essayer Runo. Sinon, tournez-vous vers la
**bibliothèque Python** quand vous l'intégrez à votre propre code, ou le **serveur
local** quand vous voulez un endpoint HTTP indépendant du langage.

### Option 1 : Serveur HTTP local

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

Pas de clé d'API ni d'en-tête d'authentification, c'est votre serveur local. Endpoints : `/v1/extract`,
`/v1/batch`, `/v1/crawl`. Passez les réglages propres à chaque requête dans un objet `options` (voir
[Options](#options)).

### Option 2 : Bibliothèque Python

Runo fonctionne comme une bibliothèque Python.

```python
from runo import extract

data = extract("https://example.com", [
    {"field": "title",     "type": "string",        "example": "Example Domain"},
    {"field": "paragraph", "type": "string",        "example": "This domain is..."},
])
print(data)   # {"title": "Example Domain", "paragraph": "..."}
```

`batch` exécute un schéma sur plusieurs URL ; `crawl` suit les liens depuis une URL de départ.
Chacun a une variante `_async` (`extract_async`, `batch_async`, `crawl_async`) à
utiliser dans votre propre boucle d'événements.

```python
from runo import batch, crawl

rows = batch(["https://a.com", "https://b.com"], schema, concurrency=5)
site = crawl("https://blog.com", "https://blog.com/posts/*", schema, max_pages=50)
```

### Option 3 : Ligne de commande

Vous pouvez aussi exécuter Runo depuis la ligne de commande.

```bash
# une seule URL (schéma depuis un fichier)
runo extract https://example.com --schema schema.json

# schéma en ligne, forcer le navigateur headless, écrire le résultat dans un fichier
runo extract https://example.com --schema '[{"field":"title","type":"string","example":"x"}]' \
  --render-js always -o out.json

# plusieurs URL avec un seul schéma (urls.txt contient une URL par ligne)
runo batch --urls urls.txt --schema schema.json --concurrency 5

# suivre les liens depuis une URL de départ
runo crawl https://blog.com --pattern "https://blog.com/posts/*" --schema schema.json --max-pages 50

# lancer le serveur HTTP local
runo serve --host 127.0.0.1 --port 8000
```

`--schema` accepte le chemin d'un fichier JSON ou une chaîne JSON en ligne. Flags courants :
`--render-js auto|always|never`, `--timeout-ms`, `--no-cache` et `-o out.json`
pour écrire la sortie dans un fichier au lieu de stdout (`--concurrency` pour batch ;
`--max-pages`, `--max-depth`, `--use-sitemap`, `--ignore-robots` pour crawl).

Un fichier `schema.json` n'est qu'un tableau JSON d'objets de champ :

```json
[
  {"field": "title", "type": "string", "example": "Example Domain"},
  {"field": "price", "type": "float",  "example": 29.99, "hint": "Use the sale price if present."}
]
```

## Schéma

Chaque champ a un nom `field`, un `type`, une valeur `example` (une amorce à un seul exemple
pour le LLM) et un `hint` optionnel. Un bon exemple lève l'ambiguïté du format, par
exemple `35` contre `"35 years old"`, ou `2024-01-31` contre `January 31`.

| Type | Conversion |
|---|---|
| `string` | Toujours une chaîne |
| `integer` | Analysé depuis le texte (`"35 years old"` -> `35`) |
| `float` | Analysé depuis le texte (`"$1.2M"` -> `1200000.0`) |
| `boolean` | Normalisé (`"✓ Verified"` -> `true`) |
| `date` | ISO 8601 (`YYYY-MM-DD`) ; les dates relatives sont résolues |
| `array<string>` / `array<integer>` / `array<float>` | Tableau JSON (vide `[]` si rien ne correspond) |

Les champs qui ne peuvent pas être résolus reviennent à `null`, jamais supprimés, donc `data` a toujours les
mêmes clés que votre schéma.

### Indices (hints)

Le comportement par défaut convient généralement. Recourez à `hint` quand une page affiche deux valeurs
pour le même concept et que vous en voulez une précise (`"Use sale price if present."`),
quand le nom du champ est ambigu (`author` sur un article republié), ou quand le
site emploie une formulation peu évidente (`likes` contre `reactions`). Gardez les indices courts et utilisez-les
seulement au besoin.

### Exemples concrets

Page produit :

```json
[
  { "field": "title",    "type": "string",        "example": "MacBook Pro 14\"" },
  { "field": "price",    "type": "float",         "example": 1999.00, "hint": "Use sale price if present." },
  { "field": "inStock",  "type": "boolean",       "example": true },
  { "field": "rating",   "type": "float",         "example": 4.6 },
  { "field": "tags",     "type": "array<string>", "example": ["laptop", "apple"] }
]
```

Article / billet de blog :

```json
[
  { "field": "headline",    "type": "string", "example": "OpenAI ships o3" },
  { "field": "author",      "type": "string", "example": "Cade Metz" },
  { "field": "publishedAt", "type": "date",   "example": "2024-12-20" },
  { "field": "summary",     "type": "string", "example": "A short summary.", "hint": "1-3 sentences." }
]
```

### Astuces

- **Gardez des schémas resserrés.** 4 à 10 champs s'extraient plus précisément que 30. Divisez les gros en deux appels.
- **Préférez `array<T>` aux chaînes à délimiteurs.** Déclarez `array<string>` et laissez Runo construire la liste plutôt que de découper vous-même une chaîne concaténée.
- **Les noms comptent.** `firstName` contre `givenName` produisent des extractions subtilement différentes. Utilisez le terme employé par vos sites cibles ; le camelCase fonctionne bien.

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

Les flags ci-dessus ne sont pas réservés à la CLI. Les mêmes options sont disponibles depuis la
bibliothèque Python et via HTTP, sous les mêmes noms.

**Bibliothèque Python.** Passez les options comme arguments nommés en snake_case (le
`--render-js` de la CLI devient `render_js`, `--max-pages` devient `max_pages`, et ainsi
de suite). L'URL ou la liste d'URL, le schéma et le motif de suivi du crawl sont des
arguments positionnels :

```python
from runo import extract, crawl

# forcer headless, timeout plus long, remplir les nulls depuis les images, ignorer le cache
extract("https://example.com", schema,
        render_js="always", timeout_ms=30000, process_images=True, no_cache=True)

# les réglages propres au crawl sont aussi des arguments nommés
crawl("https://blog.com", "https://blog.com/posts/*", schema,
      max_pages=100, max_depth=3, use_sitemap=True)
```

Chaque fonction a sa jumelle `_async` (`extract_async`, `batch_async`,
`crawl_async`) avec une signature identique, à utiliser dans votre propre boucle d'événements.

**Via HTTP.** Les options vont dans le corps de la requête. `extract` et `batch` prennent un
objet `options` ; `crawl` conserve ses réglages de crawl dans un objet `crawl` aux côtés
d'un objet `options` partagé :

```json
{
  "seed_url": "https://blog.com",
  "schema": [{ "field": "title", "type": "string", "example": "Example post" }],
  "crawl": { "follow_pattern": "https://blog.com/posts/*", "max_pages": 100, "use_sitemap": true },
  "options": { "render_js": "always", "timeout_ms": 30000 }
}
```

**Batch API.** `batch` et `crawl` acceptent aussi `async_mode`, qui achemine les
extractions via la Batch API de Gemini : moins chère, jusqu'à 24h de latence, avec un
repli transparent vers le mode synchrone en cas d'échec.

## Réponse

Chaque extraction renvoie la même forme :

```json
{
  "url": "https://example.com",
  "status": "success",
  "render_mode": "plain",
  "data": { "title": "Example Domain" },
  "images_processed": null
}
```

| Champ | Notes |
|---|---|
| `status` | `"success"` ou `"error"`. |
| `render_mode` | `"plain"` (une simple récupération HTTP a suffi) ou `"headless"` (escalade vers un navigateur). |
| `data` | Indexé par les noms `field` de votre schéma ; les champs non résolus valent `null`. |
| `warnings` | Tableau optionnel de notes de conversion (p. ex. `"coerced 'price' from '$19.99' to 19.99"`) ; omis quand vide. |
| `images_processed` | Nombre d'images lues par la passe de vision ; `null` quand elle ne s'est pas exécutée. |

Le `extract()` de la bibliothèque Python renvoie uniquement le dictionnaire `data` et lève
`RunoError` en cas d'échec ; `batch()`/`crawl()` renvoient les objets de résultat complets et
ne lèvent pas d'exception pour l'échec d'une seule page (vérifiez le `status` de chaque entrée).

## Récupération et rendu

Runo tente d'abord le chemin le moins coûteux et n'escalade que lorsqu'une page l'exige.
Sous `render_js: "auto"` (le défaut), il commence par une récupération HTTP simple et
bascule vers un navigateur headless furtif quand il repère des signes de difficulté :

- Une signature de blocage anti-bot connue (Cloudflare, Datadome, PerimeterX, Akamai, Incapsula).
- Un corps de moins de ~500 caractères, ou un texte visible clairsemé derrière une charge HTML volumineuse (une coquille JS).
- Des marqueurs de frameworks JavaScript dans le HTML.
- Un HTTP `402`, `403`, `406`, `429` ou `503`.
- Un schéma réclamant des nombres sur une page presque sans chiffres (widgets météo/tableaux de bord).

L'escalade est transparente : la forme de la réponse est identique, seul `render_mode`
passe de `plain` à `headless`.

Pour franchir la protection anti-bot, il remonte depuis l'option la moins coûteuse, s'arrêtant à la
première qui réussit, et retient par hôte ce dont un site a besoin afin que les appels ultérieurs sautent
les tentatives qui ne fonctionneront pas :

- Une **récupération HTTP simple** pour du HTML statique.
- Une **récupération avec usurpation TLS** (extra `[tls]`) qui imite l'empreinte TLS d'un vrai navigateur, déjouant les contrôles passifs JA3/JA4.
- Un **navigateur headless renforcé** (`[patchright]` / camoufox) avec un ensemble d'empreintes canvas/WebGL/audio, déjouant la détection CDP et les murs d'empreinte.
- **Persistance des cookies par hôte**, pour que les défis à confiance progressive n'aient à être franchis qu'une fois.
- Un **repli d'archive** (Wayback / vue lecture) en dernier recours quand le site en direct est injoignable.

Les sites protégés de manière agressive (certaines configurations Cloudflare/Datadome, gros commerces comme
Amazon/Walmart) peuvent tout de même déjouer tout cela et revenir en `FETCH_BLOCKED`.

## Modes batch et crawl

`batch` exécute un schéma sur une liste d'URL que vous avez déjà. `crawl` part
d'une URL de départ, suit les liens correspondant à un motif et découvre les pages pour vous.

```python
from runo import batch, crawl

rows = batch(["https://a.com", "https://b.com"], schema, concurrency=5)

site = crawl("https://blog.com", "https://blog.com/posts/*", schema,
             max_pages=50, max_depth=2, use_sitemap=False, ignore_robots=False)
```

Le crawler respecte `robots.txt` (sauf si `ignore_robots=True`), peut s'amorcer depuis
`sitemap.xml` (`use_sitemap=True`), et applique un jitter par hôte plus un back-off adaptatif
pour que vous ne martelez pas un site. `crawl` renvoie les résultats par page plus un
bloc `crawl_meta` :

```json
{
  "results": [ { "url": "...", "status": "success", "data": { } } ],
  "crawl_meta": { "pages_visited": 17, "pages_skipped": 3, "pages_failed": 0, "cancelled": false }
}
```

Le mieux est d'utiliser `batch` quand vous avez la liste d'URL (y compris les flux paginés
que vous pouvez construire en `?page=1..N`) et d'utiliser `crawl` quand vous avez une URL et voulez
découvrir des pages liées.

## Lire des données depuis des images

Activez `process_images=true` (option / flag `--process-images`) et, après la
passe de texte, tout champ encore à `null` déclenche une passe de vision : Runo note les
balises `<img>` de la page par rapport aux noms des champs manquants, récupère jusqu'à 3 des meilleurs
candidats et les envoie à Gemini en un seul appel multimodal ciblant uniquement
ces champs. Il fusionne ce qu'il trouve et rapporte le compte dans
`images_processed`. Si la passe d'images échoue, le résultat original texte seul est renvoyé sans changement.
Idéal pour les données incrustées dans les images (incrustations de prix, statistiques sur une
affiche, cartes de marketplace) ; cela coûte des tokens supplémentaires, donc c'est désactivé par défaut.

## Erreurs

Les échecs utilisent une enveloppe cohérente. Sur une extraction unique, le `status` de premier niveau
vaut `"error"` ; à l'intérieur d'un `batch`/`crawl`, chaque entrée porte le même
objet `error` tandis que l'appel global réussit tout de même.

```json
{ "status": "error", "error": { "code": "FETCH_BLOCKED", "message": "...", "retryable": true } }
```

| Code | Réessayable | Signification |
|---|---|---|
| `SCHEMA_INVALID` | non | Le schéma est mal formé (`field` manquant, type inconnu). |
| `TYPE_COERCION_FAILED` | non | Une valeur n'a pas pu être convertie vers son type déclaré. |
| `URL_UNREACHABLE` | oui | Échec DNS/réseau, ou bloqué par la protection SSRF. |
| `TIMEOUT` | oui | La page a dépassé `timeout_ms`. |
| `FETCH_BLOCKED` | oui | L'anti-bot a déjoué toutes les stratégies de récupération gratuites. |
| `LLM_UNAVAILABLE` / `LLM_RATE_LIMITED` / `LLM_TIMEOUT` / `LLM_EMPTY` / `LLM_ERROR` | oui | Gemini était surchargé, limité en débit, lent, ou a renvoyé une réponse inutilisable. |
| `LLM_TRUNCATED` / `LLM_BLOCKED` / `LLM_BAD_REQUEST` | non | La sortie n'a pas pu être analysée, un blocage de sécurité/politique, ou une requête invalide (prompt trop long). |

Réessayez les codes `retryable: true` avec un back-off exponentiel (1s, 2s, 4s, 8s, plafonné
à ~4 tentatives) ; traitez le reste comme terminal. Quand un appel réussit mais que quelque chose
semblait anormal (p. ex. un symbole monétaire retiré), la correction est rapportée dans le
tableau optionnel `warnings` plutôt que de faire échouer l'appel.

## Configuration

Tout est piloté par des variables d'environnement (ou `.env`). Seule `GEMINI_API_KEY`
est requise ; voir [`.env.example`](.env.example) pour les paramètres documentés,
dont `GEMINI_API_KEYS` pour un round-robin entre plusieurs clés, `HEADLESS_ENGINE`,
`TLS_IMPERSONATE` et `SSRF_GUARD_ENABLED`.

## Limitations

- **Les sites riches en JS nécessitent le navigateur.** Les pages en HTML simple fonctionnent avec un simple
  `pip install`, mais les sites qui rendent le contenu en JavaScript nécessitent
  `playwright install chromium`. Sans lui, ces pages reviennent vides.
- **Les murs anti-bot durs peuvent échouer.** Les sites protégés de manière agressive (certaines
  configurations Cloudflare/Datadome, gros commerces comme Amazon/Walmart) peuvent déjouer toutes les
  stratégies de récupération intégrées et renvoyer `FETCH_BLOCKED`.
- **Le cache est en mémoire.** Les résultats et les valeurs par champ sont mis en cache au sein d'un
  processus en cours pour éviter des appels LLM répétés, mais le cache se réinitialise au redémarrage.
- **Vous payez Google pour les tokens.** La qualité et le coût de l'extraction suivent le modèle
  Gemini configuré (Flash-Lite par défaut).

## Licence

[Licence Apache 2.0](LICENSE).
