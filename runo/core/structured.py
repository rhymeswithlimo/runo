import re
from typing import Any, Callable, Optional

import extruct
import httpx
from bs4 import BeautifulSoup

from runo.config import settings
from runo.core.schema import coerce_extraction, coerce_value
from runo.exceptions import TypeCoercionError
from runo.models.request import SchemaField


# Substring hints that indicate a schema field is likely resolvable from
# oEmbed data. If no field matches, the oEmbed HTTP round-trip is skipped.
_OEMBED_SCHEMA_HINTS = frozenset({
    "title", "author", "thumbnail", "thumb", "embed", "video",
    "duration", "width", "height", "provider", "html", "version",
})


def _schema_needs_oembed(fields: list[SchemaField]) -> bool:
    for f in fields:
        name_lower = f.field.lower()
        for hint in _OEMBED_SCHEMA_HINTS:
            if hint in name_lower:
                return True
    return False


_ALIASES: dict[str, list[str]] = {
    "title": ["name", "headline", "title", "og:title", "twitter:title"],
    "name": ["name", "title", "og:title", "twitter:title"],
    "headline": ["headline", "name", "title", "og:title", "twitter:title"],
    "price": ["price", "offers.price", "lowPrice", "product:price:amount"],
    "lowprice": ["lowPrice", "offers.lowPrice", "price"],
    "highprice": ["highPrice", "offers.highPrice"],
    "author": [
        "author", "author.name", "creator", "creator.name", "byline",
        "twitter:creator", "article:author", "dc.creator", "dc:creator",
        "meta.author",
    ],
    "description": [
        "description", "og:description", "summary", "twitter:description",
        "meta.description",
    ],
    "image": [
        "image", "og:image", "thumbnailUrl", "image.url", "twitter:image",
        "twitter:image:src",
    ],
    "date": [
        "datePublished", "dateCreated", "uploadDate",
        "article:published_time", "meta.pubdate", "meta.date",
    ],
    "published": [
        "datePublished", "article:published_time", "uploadDate",
        "meta.pubdate",
    ],
    "publisheddate": [
        "datePublished", "article:published_time", "uploadDate",
        "header.last-modified",
    ],
    "modified": [
        "dateModified", "article:modified_time", "lastModified",
        "header.last-modified",
    ],
    "lastmodified": [
        "dateModified", "article:modified_time", "lastModified",
        "header.last-modified",
    ],
    "updated": [
        "dateModified", "article:modified_time", "lastModified",
        "header.last-modified",
    ],
    "category": [
        "category", "articleSection", "genre", "article:section",
    ],
    "brand": ["brand", "brand.name", "product:brand"],
    "sku": ["sku", "productID", "mpn"],
    "gtin": ["gtin", "gtin13", "gtin12", "gtin8", "gtin14"],
    "isbn": ["isbn"],
    "mpn": ["mpn", "productID"],
    "currency": [
        "priceCurrency", "offers.priceCurrency", "product:price:currency",
    ],
    "rating": ["aggregateRating.ratingValue", "ratingValue"],
    "ratingvalue": ["aggregateRating.ratingValue", "ratingValue"],
    "ratingcount": [
        "aggregateRating.ratingCount", "ratingCount",
        "aggregateRating.reviewCount",
    ],
    "reviewcount": [
        "aggregateRating.reviewCount", "reviewCount",
        "aggregateRating.ratingCount",
    ],
    "bestrating": ["aggregateRating.bestRating", "bestRating"],
    "worstrating": ["aggregateRating.worstRating", "worstRating"],
    "availability": [
        "offers.availability", "availability", "product:availability",
    ],
    "instock": [
        "offers.availability", "availability", "product:availability",
    ],
    "stock": [
        "offers.availability", "availability", "product:availability",
    ],
    "tags": ["keywords", "article:tag", "meta.keywords", "meta.news_keywords"],
    "keywords": ["keywords", "meta.keywords", "meta.news_keywords"],
    "breadcrumbs": ["breadcrumb.itemListElement", "breadcrumb"],
    "breadcrumb": ["breadcrumb.itemListElement", "breadcrumb"],
    "publisher": ["publisher.name", "og:site_name", "twitter:site"],
    "source": ["publisher.name", "og:site_name", "twitter:site"],
    "language": [
        "inLanguage", "og:locale", "meta.language",
        "header.content-language", "html.lang",
    ],
    "lang": [
        "inLanguage", "og:locale", "meta.language",
        "header.content-language", "html.lang",
    ],
    "locale": [
        "og:locale", "inLanguage", "header.content-language",
    ],
    "wordcount": ["wordCount"],
    "readingtime": ["timeRequired"],
    "email": [
        "email", "contactPoint.email", "publisher.email", "author.email",
    ],
    "phone": [
        "telephone", "contactPoint.telephone", "publisher.telephone",
    ],
    "address": [
        "address.streetAddress", "address", "publisher.address.streetAddress",
    ],
    "country": [
        "address.addressCountry", "publisher.address.addressCountry",
    ],
    "city": [
        "address.addressLocality", "publisher.address.addressLocality",
    ],
    "region": [
        "address.addressRegion", "publisher.address.addressRegion",
    ],
    "postal": [
        "address.postalCode", "publisher.address.postalCode",
    ],
    "postalcode": [
        "address.postalCode", "publisher.address.postalCode",
    ],
    "latitude": ["geo.latitude", "latitude"],
    "longitude": ["geo.longitude", "longitude"],
    "lat": ["geo.latitude", "latitude"],
    "lon": ["geo.longitude", "longitude"],
    "lng": ["geo.longitude", "longitude"],
    "url": ["url", "og:url", "twitter:url"],
    "canonical": ["url", "og:url"],
    "site": ["og:site_name", "twitter:site", "publisher.name"],
    "sitename": ["og:site_name", "twitter:site", "publisher.name"],
    "video": ["og:video", "og:video:url", "twitter:player"],
    "videourl": ["og:video", "og:video:url", "contentUrl", "embedUrl"],
    "embedurl": ["embedUrl", "og:video:url", "twitter:player"],
    "duration": ["duration", "video.duration"],
    "copyright": [
        "copyrightHolder.name", "copyrightNotice", "meta.copyright",
    ],
    # schema.org/Recipe
    "cookminutes": ["cookTime", "totalTime"],
    "cooktime": ["cookTime", "totalTime"],
    "prepminutes": ["prepTime"],
    "preptime": ["prepTime"],
    "totalminutes": ["totalTime", "cookTime"],
    "totaltime": ["totalTime", "cookTime"],
    "ingredients": ["recipeIngredient", "ingredients"],
    "recipeingredient": ["recipeIngredient", "ingredients"],
    "servings": ["recipeYield", "yield"],
    "recipeyield": ["recipeYield", "yield"],
    "yield": ["recipeYield", "yield"],
    "cuisine": ["recipeCuisine"],
    "recipecuisine": ["recipeCuisine"],
    "recipecategory": ["recipeCategory", "category"],
    "instructions": ["recipeInstructions", "instructions"],
    "recipeinstructions": ["recipeInstructions", "instructions"],
    "calories": ["nutrition.calories"],
}


def _flat(d: Any, prefix: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {}
    if isinstance(d, dict):
        for k, v in d.items():
            key = f"{prefix}{k}"
            if isinstance(v, dict):
                out.update(_flat(v, key + "."))
            elif isinstance(v, list) and v:
                # Aggregate primitive lists; walk all dict entries under the
                # same dotted prefix so first-match aliases land on the same
                # field regardless of which entry holds the value. Earlier
                # entries win on collision (typical JSON-LD shape — primary
                # offer / first review first).
                prims: list[Any] = []
                for item in v:
                    if isinstance(item, dict):
                        for sub_k, sub_v in _flat(item, key + ".").items():
                            out.setdefault(sub_k, sub_v)
                    elif item is not None:
                        prims.append(item)
                if prims:
                    out.setdefault(key, prims)
            elif v is not None:
                out[key] = v
    return out


def _expand_jsonld_graph(items: list[Any]) -> list[Any]:
    """Hoist @graph nodes (and @id-referenced siblings) into a flat list of
    schema.org entities. Without this, ``_flat`` sees the wrapper as a single
    node and drops every entity it contains."""
    out: list[Any] = []
    for it in items or []:
        if isinstance(it, dict):
            graph = it.get("@graph")
            if isinstance(graph, list):
                # Keep both the wrapper (in case it carries data) and graph
                # nodes themselves.
                shell = {k: v for k, v in it.items() if k != "@graph"}
                if shell:
                    out.append(shell)
                out.extend(g for g in graph if isinstance(g, dict))
                continue
        out.append(it)
    return out


# Generic <meta name="..."> tags worth collecting in addition to og:/twitter:/
# article:. These cover blogs / sites without JSON-LD where the only canonical
# signal is the legacy meta tags.
_GENERIC_META_NAMES = frozenset({
    "description", "author", "keywords", "news_keywords",
    "copyright", "publisher", "robots", "language", "pubdate", "date",
})


def _generic_meta(html: str) -> dict[str, Any]:
    """Collect ``<meta name="...">`` and ``http-equiv`` tags not already
    covered by extruct/OpenGraph. Namespaced under ``meta.<name>`` so they
    can be referenced explicitly from the alias map without clobbering
    JSON-LD/OG keys with the same bare name."""
    out: dict[str, Any] = {}
    try:
        soup = BeautifulSoup(html, "lxml")
        for tag in soup.find_all("meta"):
            name = (tag.get("name") or "").lower()
            content = tag.get("content")
            if not name or content is None:
                continue
            if name in _GENERIC_META_NAMES:
                out.setdefault(f"meta.{name}", content)
        # <html lang="..."> — most reliable language signal on plain HTML.
        html_tag = soup.find("html")
        if html_tag is not None:
            lang = html_tag.get("lang")
            if lang:
                out.setdefault("html.lang", lang)
        # http-equiv variants
        for tag in soup.find_all("meta"):
            eq = (tag.get("http-equiv") or "").lower()
            content = tag.get("content")
            if not eq or content is None:
                continue
            if eq == "content-language":
                out.setdefault("header.content-language", content)
    except Exception:
        pass
    return out


def _twitter_card_meta(html: str) -> dict[str, Any]:
    """Parse `<meta name="twitter:*">` tags. Sites that emit only Twitter
    Cards (no OpenGraph) are common — notably Twitter/X itself — and extruct
    does not cover them. One BS4 pass, no network."""
    out: dict[str, Any] = {}
    try:
        soup = BeautifulSoup(html, "lxml")
        for tag in soup.find_all("meta"):
            name = (tag.get("name") or tag.get("property") or "").lower()
            content = tag.get("content")
            if not name or content is None:
                continue
            if name.startswith("twitter:") or name.startswith("og:") or name.startswith("article:"):
                out[name] = content
    except Exception:
        pass
    return out


def _oembed_discover(html: str) -> str | None:
    """Return an oEmbed JSON endpoint URL if the page advertises one."""
    try:
        soup = BeautifulSoup(html, "lxml")
        for link in soup.find_all("link", rel=True):
            rel = link.get("rel")
            if isinstance(rel, list):
                rel_s = " ".join(rel).lower()
            else:
                rel_s = str(rel).lower()
            if "alternate" not in rel_s:
                continue
            if (link.get("type") or "").lower() in (
                "application/json+oembed", "text/xml+oembed",
            ):
                href = link.get("href")
                if href and (link.get("type") or "").lower() == "application/json+oembed":
                    return href
    except Exception:
        return None
    return None


async def _oembed_fetch(endpoint: str, timeout_ms: int = 5000) -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(
            timeout=timeout_ms / 1000, follow_redirects=True
        ) as client:
            r = await client.get(endpoint, headers={"Accept": "application/json"})
            if r.status_code != 200:
                return {}
            data = r.json()
            if not isinstance(data, dict):
                return {}
            # Namespace under "oembed." so aliases can disambiguate if needed,
            # but also expose bare keys for cheap lookup.
            flat = _flat(data, "oembed.")
            flat.update({k: v for k, v in data.items() if not isinstance(v, (dict, list))})
            return flat
    except Exception:
        return {}


_INTERESTING_HEADERS = {
    "last-modified": "header.last-modified",
    "content-language": "header.content-language",
    "content-type": "header.content-type",
}


def _collect(
    html: str,
    url: str,
    response_headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    try:
        raw = extruct.extract(
            html, base_url=url,
            syntaxes=["json-ld", "microdata", "opengraph", "microformat", "rdfa"],
            uniform=True,
        )
    except Exception:
        raw = {}

    for key in ("json-ld", "microdata", "microformat", "rdfa"):
        items = _expand_jsonld_graph(raw.get(key) or []) if key == "json-ld" else (raw.get(key) or [])
        for item in items:
            for k, v in _flat(item).items():
                merged.setdefault(k, v)

    og_list = raw.get("opengraph") or []
    if og_list:
        og = og_list[0]
        props = og.get("properties") if isinstance(og, dict) else None
        if isinstance(props, list):
            for pair in props:
                if isinstance(pair, (list, tuple)) and len(pair) == 2:
                    merged.setdefault(pair[0], pair[1])
        elif isinstance(og, dict):
            for k, v in og.items():
                merged.setdefault(k if k.startswith("og:") else f"og:{k}", v)

    # Twitter Cards + any meta extruct missed.
    if settings.twitter_cards_enabled:
        for k, v in _twitter_card_meta(html).items():
            merged.setdefault(k, v)

    # Generic <meta name="..."> tags + <html lang> — fills fields like
    # description / author / keywords on sites without JSON-LD.
    for k, v in _generic_meta(html).items():
        merged.setdefault(k, v)

    # HTTP response headers as a structured source. Last-Modified is the
    # cheapest possible signal for date-shaped fields when the page has no
    # JSON-LD datePublished; Content-Language fills locale fields. These
    # were already on the fetch result — no extra network cost.
    if response_headers:
        for raw_name, value in response_headers.items():
            if not value:
                continue
            mapped = _INTERESTING_HEADERS.get(raw_name.lower())
            if mapped:
                merged.setdefault(mapped, value)

    return merged


_DOMAIN_LIKE_RE = re.compile(r"^[a-z0-9][a-z0-9-]*\.[a-z]{2,}(?:/.*)?$", re.IGNORECASE)
# Field-name fragments whose values must carry real content, not a site name.
_CONTENT_FIELD_FRAGMENTS = ("headline", "title", "name", "article")
# Description-shaped field fragments. Structured prefill is unreliable for
# these — og:description is often SEO boilerplate (e.g. just the page title
# repeated). The LLM does a better job picking the authoritative description
# from the ## Candidate descriptions block. Skip prefill; let the LLM decide.
_DESCRIPTION_FIELD_FRAGMENTS = (
    "description", "tagline", "summary", "bio", "about", "subtitle",
)


def _looks_like_site_name(value: Any) -> bool:
    """Reject obvious OG site-name leaks like 'reuters.com' or 'TechCrunch'
    when they show up as the value for a content field."""
    if not isinstance(value, str):
        return False
    s = value.strip()
    if len(s) < 6:
        # Too short for a real headline/title; likely a site/app name.
        return True
    if _DOMAIN_LIKE_RE.match(s):
        return True
    return False


def _lookup(
    data: dict[str, Any],
    candidates: list[str],
    reject: Optional[Callable[[Any], bool]] = None,
) -> Any:
    for c in candidates:
        if c in data and data[c] not in (None, "", []):
            v = data[c]
            if reject is not None and reject(v):
                continue
            return v
    return None


def _resolve_fields(
    data: dict[str, Any], fields: list[SchemaField]
) -> dict[str, Any]:
    """Return raw values per field (or None) from a merged structured blob."""
    out: dict[str, Any] = {}
    for f in fields:
        key = f.field
        key_lower = key.lower()
        # Skip structured prefill for description-shaped fields entirely —
        # OG/meta description is often SEO boilerplate. The ## Candidate
        # descriptions block + LLM picks the right answer.
        if f.type == "string" and any(
            frag in key_lower for frag in _DESCRIPTION_FIELD_FRAGMENTS
        ):
            out[key] = None
            continue
        candidates = _ALIASES.get(key_lower, [])
        all_candidates = [key] + [c for c in candidates if c != key]
        reject = None
        is_content_field = (
            f.type == "string"
            and any(frag in key_lower for frag in _CONTENT_FIELD_FRAGMENTS)
        )
        if is_content_field:
            reject = _looks_like_site_name
        value = _lookup(data, all_candidates, reject=reject)
        # Length-based prefill skip: when the user provides a short example
        # (like "National Geographic") but the structured value is much
        # longer (e.g. og:title = "National Geographic (@natgeo) • Instagram
        # photos and videos"), the example is signalling a cleaned shape.
        # Skip prefill so the LLM applies Rule 4 (strip site noise) instead
        # of being forced to echo the noisy value verbatim.
        if (
            is_content_field
            and isinstance(value, str)
            and f.example
            and isinstance(f.example, str)
            and len(f.example) >= 3
            and len(value) > 2 * len(f.example) + 8
        ):
            value = None
        out[key] = value
    return out


def try_structured_extract(
    html: str, url: str, fields: list[SchemaField],
    response_headers: dict[str, str] | None = None,
) -> Optional[dict[str, Any]]:
    """Attempt to satisfy every requested field from embedded structured data.

    Returns None when *any* field cannot be resolved — caller falls through
    to the partial-structured path (which keeps the resolved fields and
    asks the LLM only for the rest) or to a full LLM pass.
    """
    if not html:
        return None
    data = _collect(html, url, response_headers=response_headers)
    if not data:
        return None

    raw = _resolve_fields(data, fields)
    if any(v is None for v in raw.values()):
        return None

    try:
        return coerce_extraction(raw, fields)
    except TypeCoercionError:
        return None


async def try_structured_extract_async(
    html: str, url: str, fields: list[SchemaField],
    response_headers: dict[str, str] | None = None,
) -> Optional[dict[str, Any]]:
    """Async variant that additionally tries oEmbed if page advertises it.

    One extra HTTP GET at most — zero LLM cost. Result cache keys off the
    original URL + schema, so oEmbed adds no cache fragmentation.
    """
    if not html:
        return None
    data = _collect(html, url, response_headers=response_headers)
    if settings.oembed_enabled and _schema_needs_oembed(fields):
        endpoint = _oembed_discover(html)
        if endpoint:
            oembed = await _oembed_fetch(endpoint)
            if oembed:
                # oEmbed keys don't overwrite JSON-LD/OG by default.
                for k, v in oembed.items():
                    data.setdefault(k, v)
    if not data:
        return None

    raw = _resolve_fields(data, fields)
    if any(v is None for v in raw.values()):
        return None

    try:
        return coerce_extraction(raw, fields)
    except TypeCoercionError:
        return None


async def collect_structured(
    html: str, url: str, fields: list[SchemaField],
    response_headers: dict[str, str] | None = None,
) -> tuple[Optional[dict[str, Any]], dict[str, Any]]:
    """Single-pass structured extraction.

    Returns ``(full_match, prefilled)``:
    - ``full_match``: all-fields-resolved dict if every requested field
      could be satisfied from structured data (and coerced cleanly);
      otherwise ``None``.
    - ``prefilled``: subset of fields that resolved + coerced cleanly.
      Always populated (may be ``{}``); useful even when ``full_match``
      is ``None`` so the LLM call can pin known-good values.

    One HTML parse + at most one oEmbed HTTP GET — replaces the prior
    ``try_structured_extract_async`` + ``partial_structured_prefill`` pair
    that walked the same HTML twice.
    """
    if not html:
        return None, {}

    data = _collect(html, url, response_headers=response_headers)
    if settings.oembed_enabled and _schema_needs_oembed(fields):
        endpoint = _oembed_discover(html)
        if endpoint:
            oembed = await _oembed_fetch(endpoint)
            for k, v in oembed.items():
                data.setdefault(k, v)
    if not data:
        return None, {}

    raw = _resolve_fields(data, fields)
    prefilled: dict[str, Any] = {}
    any_missing = False
    for f in fields:
        v = raw.get(f.field)
        if v is None:
            any_missing = True
            continue
        try:
            prefilled[f.field] = coerce_value(v, f.type)
        except TypeCoercionError:
            any_missing = True

    if any_missing:
        return None, prefilled
    # All fields resolved + coerced — full match.
    return prefilled, prefilled


async def partial_structured_prefill(
    html: str, url: str, fields: list[SchemaField],
    response_headers: dict[str, str] | None = None,
) -> tuple[dict[str, Any], list[SchemaField]]:
    """Return (prefilled_dict, remaining_fields).

    - `prefilled_dict` is the subset of field values we could satisfy from
      structured data (including Twitter Cards / oEmbed).
    - `remaining_fields` are the schema fields that still need an LLM pass.

    Called when `try_structured_extract_async` returned None. The LLM will be
    asked only for the remaining fields, which tightens BM25 relevance and
    reduces null rate — without adding an LLM call.
    """
    if not html:
        return {}, fields

    data = _collect(html, url, response_headers=response_headers)
    if settings.oembed_enabled and _schema_needs_oembed(fields):
        endpoint = _oembed_discover(html)
        if endpoint:
            oembed = await _oembed_fetch(endpoint)
            for k, v in oembed.items():
                data.setdefault(k, v)
    if not data:
        return {}, fields

    raw = _resolve_fields(data, fields)
    prefilled: dict[str, Any] = {}
    remaining: list[SchemaField] = []
    for f in fields:
        v = raw.get(f.field)
        if v is None:
            remaining.append(f)
            continue
        try:
            prefilled[f.field] = coerce_value(v, f.type)
        except TypeCoercionError:
            # Couldn't coerce the structured value — let the LLM try.
            remaining.append(f)
    return prefilled, remaining
