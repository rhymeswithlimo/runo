import re

from rank_bm25 import BM25Okapi

from runo.config import settings
from runo.models.request import SchemaField


_STRUCTURED_MARKERS = ("application/ld+json", "og:", "schema.org", "itemprop")

# Heuristic: "Word - blurb" / "Word: blurb" rows that dominate link-list
# blocks (e.g. README link tables, footer site indexes). Such chunks
# outranked hero copy on svelte.dev — penalize them so BM25 prefers the
# hero/lead text.
_LINKLIST_ROW_RE = re.compile(r"^[A-Z][\w./-]{2,40}( - | – |: ).{20,80}$")


# Minimal Porter-ish suffix stemmer. Full NLTK PorterStemmer is heavier than
# we want in the request path; this handles the common English plural/
# tense/adverb cases that caused field-name recall misses (e.g. "authors" vs
# "author", "published" vs "publish"). Pure function, no deps.
_SUFFIXES = ("ations", "ation", "ingly", "edly", "ing", "ies", "ied", "ied",
             "ies", "es", "ed", "ly", "s")


def _stem(word: str) -> str:
    if len(word) <= 3:
        return word
    for s in _SUFFIXES:
        if word.endswith(s) and len(word) - len(s) >= 3:
            return word[: -len(s)]
    return word


_CAMEL_SPLIT_RE = re.compile(r"(?<=[a-z])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")


def _split_identifier(token: str) -> list[str]:
    """Split camelCase and snake_case identifiers into constituent words.

    'firstName' → ['first', 'name']
    'net_worth' → ['net', 'worth']
    'isVerified' → ['is', 'verified']
    Plain words pass through unchanged.
    """
    parts = _CAMEL_SPLIT_RE.sub("_", token).split("_")
    return [p for p in parts if p]


def _tokenize(s: str) -> list[str]:
    tokens: list[str] = []
    for raw in re.findall(r"\w+", s):
        for part in _split_identifier(raw):
            stemmed = _stem(part.lower())
            if stemmed:
                tokens.append(stemmed)
    return tokens


def _split_paras(text: str) -> list[str]:
    """Split cleaned text into paragraph-like units.

    Falls back from double-newline → single-newline → fixed character windows
    so the chunker works regardless of which cleaner produced the text.
    """
    paras = [p.strip() for p in text.split("\n\n") if p.strip()]
    if len(paras) >= 3:
        return paras
    paras = [p.strip() for p in text.split("\n") if p.strip()]
    if len(paras) >= 3:
        return paras
    # Degenerate: no newline structure. Slice by character window (~400 tok).
    window = 1600
    return [text[i:i + window] for i in range(0, len(text), window) if text[i:i + window].strip()]


def _split_oversized(para: str, max_chars: int) -> list[str]:
    """Break a single over-budget paragraph into <= max_chars pieces.

    Splits on single newlines first (trafilatura joins article bodies with
    `\\n` but no blank-line breaks, so the whole body can arrive as one
    "paragraph"); any line still longer than max_chars is char-windowed.
    """
    pieces: list[str] = []
    buf: list[str] = []
    size = 0
    for ln in para.split("\n"):
        if not ln.strip():
            continue
        if size + len(ln) > max_chars and buf:
            pieces.append("\n".join(buf))
            buf, size = [ln], len(ln)
        else:
            buf.append(ln)
            size += len(ln) + 1
    if buf:
        pieces.append("\n".join(buf))
    out: list[str] = []
    for piece in pieces:
        if len(piece) > max_chars:
            out += [piece[i:i + max_chars] for i in range(0, len(piece), max_chars)]
        else:
            out.append(piece)
    return out


def _chunk(text: str, approx_tok: int = 400) -> list[str]:
    paras = _split_paras(text)
    max_chars = approx_tok * 4
    chunks: list[str] = []
    buf: list[str] = []
    size = 0
    for p in paras:
        # A single paragraph larger than the chunk budget would otherwise
        # become one oversized chunk. When that chunk exceeds the relevance
        # budget it gets dropped wholesale, leaving only small nav/TOC
        # fragments — so hard-split it into deliverable pieces first.
        sub_paras = _split_oversized(p, max_chars) if len(p) > max_chars else [p]
        for sp in sub_paras:
            ptok = max(1, len(sp) // 4)
            if size + ptok > approx_tok and buf:
                chunks.append("\n".join(buf))
                buf, size = [sp], ptok
            else:
                buf.append(sp)
                size += ptok
    if buf:
        chunks.append("\n".join(buf))
    return chunks


def _looks_like_linklist(chunk: str) -> bool:
    lines = [ln.strip() for ln in chunk.splitlines() if ln.strip()]
    if len(lines) < 4:
        return False
    hits = sum(1 for ln in lines if _LINKLIST_ROW_RE.match(ln))
    return hits / len(lines) > 0.40


def filter_by_relevance(
    text: str,
    fields: list[SchemaField],
    budget_tokens: int = 6000,
    pinned_prefix: str = "",
) -> str:
    """Trim long cleaned text to the chunks most relevant to the requested schema.

    `pinned_prefix` (e.g. the canonical Page Identifiers block) is prepended
    to the output verbatim and never counted against the budget — the LLM
    must always see the page's identity signals.

    Returns the original text object identity when no filtering happens —
    callers can use `is` comparison to detect whether filtering occurred.
    """
    pinned = (pinned_prefix.rstrip() + "\n\n") if pinned_prefix else ""

    if not text or len(text) // 4 <= budget_tokens:
        return pinned + text if pinned else text
    chunks = _chunk(text)
    if len(chunks) <= 1:
        return pinned + text if pinned else text

    query_terms: list[str] = []
    for f in fields:
        query_terms += _tokenize(f.field)
        if f.hint:
            query_terms += _tokenize(f.hint)
        query_terms += _tokenize(str(f.example))
    if not query_terms:
        return pinned + text if pinned else text

    tokenized = [_tokenize(c) for c in chunks]
    raw_scores = BM25Okapi(tokenized).get_scores(query_terms)

    # Hero boost: chunks within the first 1500 chars of the body get an
    # additive bump so hero/lead copy outranks late-page link-list rows.
    # Link-list penalty: chunks that look like nav/sitemap/README link
    # tables get a deduction. Both knobs configurable.
    hero_boost = float(settings.bm25_hero_boost)
    linklist_penalty = float(settings.bm25_linklist_penalty)
    cum_chars = 0
    scores = list(raw_scores)
    for i, c in enumerate(chunks):
        if cum_chars < 1500:
            scores[i] += hero_boost
        cum_chars += len(c)
        if _looks_like_linklist(c):
            scores[i] -= linklist_penalty

    kept_idx: set[int] = {0}
    for i, c in enumerate(chunks):
        if any(m in c for m in _STRUCTURED_MARKERS):
            kept_idx.add(i)

    ranked = sorted(range(len(chunks)), key=lambda i: -scores[i])
    approx_budget_chars = budget_tokens * 4
    total = sum(len(chunks[i]) for i in kept_idx)
    for i in ranked:
        if i in kept_idx:
            continue
        if total + len(chunks[i]) > approx_budget_chars:
            continue
        kept_idx.add(i)
        total += len(chunks[i])

    # Order kept chunks by descending BM25 score so the most relevant content
    # sits near the top of the LLM context where attention is strongest.
    # Chunk 0 (the document intro) is pinned first unconditionally — a
    # later chunk can outscore it on query terms (e.g. Stonehenge: a quarry
    # paragraph scoring high on "name/country/population") but the intro
    # paragraph is always the canonical subject of the page.
    ordered = sorted(kept_idx, key=lambda i: (i != 0, -scores[i]))
    body = "\n".join(chunks[i] for i in ordered)
    return pinned + body if pinned else body
