import asyncio
import logging
import re
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from runo.core.url_safety import validate_outbound_url
from runo.exceptions import URLUnreachableError

logger = logging.getLogger("runo")

_TRACKING_DOMAINS = frozenset({
    "doubleclick.net", "google-analytics.com", "googletagmanager.com",
    "facebook.com", "fb.com", "twitter.com", "analytics.twitter.com",
    "scorecardresearch.com", "quantserve.com", "adnxs.com",
    "rubiconproject.com", "pubmatic.com", "amazon-adsystem.com",
})

_DECORATIVE_CLASSES = re.compile(
    r"\b(avatar|icon|logo|banner|bg|background|badge|emoji|spacer|pixel|"
    r"placeholder|spinner|loading|ad|advertisement|social|share|button|"
    r"thumbnail-xs|thumbnail-sm)\b",
    re.IGNORECASE,
)

_DECORATIVE_FILENAMES = re.compile(
    r"(pixel|spacer|blank|tracking|beacon|transparent|1x1|logo|favicon|"
    r"sprite|icon|arrow|chevron|close|menu|hamburger|star|rating-)\.",
    re.IGNORECASE,
)

_GEMINI_MIME_TYPES = frozenset({
    "image/jpeg", "image/png", "image/gif", "image/webp",
})

_MAX_IMAGE_BYTES = 5 * 1024 * 1024


@dataclass
class ImageCandidate:
    url: str
    alt: str
    score: float
    area: int


def _tokenize(text: str) -> set[str]:
    return set(re.findall(r"[a-z]{2,}", text.lower()))


def _score_image(
    alt: str,
    stem: str,
    null_field_names: list[str],
    same_origin: bool,
) -> float:
    img_tokens = _tokenize(alt + " " + stem)
    field_tokens = _tokenize(" ".join(null_field_names))
    overlap = len(img_tokens & field_tokens)
    score = float(overlap)
    if same_origin:
        score *= 1.5
    return score


def _parse_dims(tag) -> tuple[int, int]:
    try:
        w = int(tag.get("width", 0) or 0)
        h = int(tag.get("height", 0) or 0)
        return w, h
    except (ValueError, TypeError):
        return 0, 0


def _is_tracking_domain(url: str) -> bool:
    try:
        host = urlparse(url).netloc.lower()
        for domain in _TRACKING_DOMAINS:
            if host == domain or host.endswith("." + domain):
                return True
    except Exception:
        pass
    return False


def extract_image_candidates(
    html: str,
    base_url: str,
    null_field_names: list[str],
    max_images: int = 3,
) -> list[ImageCandidate]:
    """Score and rank <img> tags from raw HTML against null field names.

    Filters decorative/tracking images; prefers same-origin and larger images.
    Returns at most max_images candidates, sorted by relevance score desc.
    """
    if not null_field_names or not html:
        return []

    try:
        soup = BeautifulSoup(html, "html.parser")
    except Exception:
        return []

    base_host = urlparse(base_url).netloc.lower()
    seen_urls: set[str] = set()
    candidates: list[ImageCandidate] = []

    def _add_candidate(src: str, alt: str, w: int, h: int) -> None:
        if not src or src.startswith("data:"):
            return
        try:
            abs_url = urljoin(base_url, src)
        except Exception:
            return
        if abs_url in seen_urls:
            return
        if _is_tracking_domain(abs_url):
            return
        filename = urlparse(abs_url).path.rstrip("/").split("/")[-1]
        if _DECORATIVE_FILENAMES.search(filename):
            return
        if 0 < w < 150 and 0 < h < 150:
            return
        seen_urls.add(abs_url)
        stem = re.sub(r"\.[^.]+$", "", filename)
        same_origin = urlparse(abs_url).netloc.lower() == base_host
        score = _score_image(alt, stem, null_field_names, same_origin)
        area = (w or 300) * (h or 300)
        candidates.append(ImageCandidate(url=abs_url, alt=alt, score=score, area=area))

    for img in soup.find_all("img"):
        css_classes = " ".join(img.get("class", []))
        if _DECORATIVE_CLASSES.search(css_classes):
            continue
        src = (img.get("src") or img.get("data-src")
               or img.get("data-lazy-src") or "")
        if not src:
            srcset = img.get("srcset", "")
            if srcset:
                src = srcset.split(",")[0].strip().split()[0]
        alt = img.get("alt", "")
        parent = img.parent
        if parent and parent.name == "figure":
            cap = parent.find("figcaption")
            if cap:
                alt = alt + " " + cap.get_text(" ", strip=True)
        w, h = _parse_dims(img)
        _add_candidate(src, alt, w, h)

    for source in soup.find_all("source"):
        srcset = source.get("srcset", "")
        if not srcset:
            continue
        src = srcset.split(",")[0].strip().split()[0]
        _add_candidate(src, "", 0, 0)

    if not candidates:
        return []

    candidates.sort(key=lambda c: (c.score, c.area), reverse=True)
    return candidates[:max_images]


async def _ssrf_request_hook(request: "httpx.Request") -> None:
    """Validate every outbound URL — including redirect targets.
    Without this, an attacker-controlled <img src> could 302 to private IPs."""
    validate_outbound_url(str(request.url))


async def fetch_image_bytes(
    url: str,
    timeout_ms: int = 5000,
) -> tuple[bytes, str] | None:
    """Fetch a single image URL. Returns (bytes, mime_type) or None.

    Streams the response so a hostile origin can't OOM the worker by
    advertising small Content-Length and then sending gigabytes — we abort
    once the running byte count exceeds ``_MAX_IMAGE_BYTES``.
    """
    try:
        validate_outbound_url(url)
    except URLUnreachableError:
        return None
    try:
        async with httpx.AsyncClient(
            timeout=timeout_ms / 1000.0,
            follow_redirects=True,
            headers={"Accept": "image/*,*/*;q=0.8"},
            event_hooks={"request": [_ssrf_request_hook]},
        ) as client:
            async with client.stream("GET", url) as resp:
                if resp.status_code >= 400:
                    return None
                # Reject upfront if Content-Length advertises an oversize body.
                cl = resp.headers.get("content-length")
                if cl is not None:
                    try:
                        if int(cl) > _MAX_IMAGE_BYTES:
                            return None
                    except ValueError:
                        pass
                ct = (resp.headers.get("content-type") or "image/jpeg").split(";")[0].strip()
                if not ct.startswith("image/"):
                    ct = "image/jpeg"
                if ct not in _GEMINI_MIME_TYPES:
                    ct = "image/jpeg"
                buf = bytearray()
                async for chunk in resp.aiter_bytes():
                    buf.extend(chunk)
                    if len(buf) > _MAX_IMAGE_BYTES:
                        return None
                if not buf:
                    return None
                return bytes(buf), ct
    except Exception as exc:
        logger.debug("Image fetch failed %s: %s", url, exc)
        return None


async def fetch_candidates(
    candidates: list[ImageCandidate],
    timeout_ms: int = 5000,
) -> list[tuple[bytes, str]]:
    """Fetch all candidates concurrently; drop failures."""
    if not candidates:
        return []
    results = await asyncio.gather(
        *[fetch_image_bytes(c.url, timeout_ms) for c in candidates],
    )
    return [r for r in results if r is not None]
