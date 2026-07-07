import re
from datetime import datetime
from typing import Any

from dateutil import parser as dateutil_parser
from google.genai import types

from runo.exceptions import SchemaInvalidError, TypeCoercionError
from runo.models.request import VALID_FIELD_TYPES, SchemaField


# Patterns that look like credentials someone leaked into a page (or
# adversarial content asking the LLM to emit a credential). When a string
# field would return one of these, replace with null — page text never
# legitimately contains live API keys for the requesting tenant.
_LEAKED_CREDENTIAL_RE = re.compile(
    r"^\s*(sk_live_|sk_static_|sk-[A-Za-z0-9]{16,}|pk_live_|"
    r"AKIA[0-9A-Z]{16}|ghp_[A-Za-z0-9]{20,}|xox[baprs]-)",
)


_PY_TO_GENAI = {
    "string": types.Type.STRING,
    "integer": types.Type.INTEGER,
    "float": types.Type.NUMBER,
    "boolean": types.Type.BOOLEAN,
    "date": types.Type.STRING,
}


def build_response_schema(fields: list[SchemaField]) -> types.Schema:
    props: dict[str, types.Schema] = {}
    for f in fields:
        if f.type.startswith("array<"):
            inner = f.type[len("array<"):-1]
            props[f.field] = types.Schema(
                type=types.Type.ARRAY,
                items=types.Schema(type=_PY_TO_GENAI[inner]),
                nullable=True,
            )
        else:
            props[f.field] = types.Schema(
                type=_PY_TO_GENAI[f.type], nullable=True,
            )
    return types.Schema(
        type=types.Type.OBJECT,
        properties=props,
        required=[f.field for f in fields],
    )


def validate_schema(fields: list[SchemaField]) -> None:
    if not fields:
        raise SchemaInvalidError("Schema must contain at least one field.")
    seen: set[str] = set()
    for f in fields:
        if not f.field.strip():
            raise SchemaInvalidError("Field name cannot be empty.")
        if f.type not in VALID_FIELD_TYPES:
            raise SchemaInvalidError(
                f"Unsupported type '{f.type}' for field '{f.field}'. "
                f"Valid types: {', '.join(sorted(VALID_FIELD_TYPES))}"
            )
        if f.field in seen:
            raise SchemaInvalidError(f"Duplicate field name: '{f.field}'.")
        seen.add(f.field)


# Sentinel strings that commonly stand in for "no value" across scraped
# pages. We map these to None before type coercion so a page that literally
# prints "N/A" or "—" in the price column doesn't crash or coerce to 0.
_NULL_SENTINELS = frozenset({
    "", "n/a", "na", "none", "null", "nil", "-", "--",
    "\u2013", "\u2014",  # en-dash, em-dash
    "tbd", "tba", "unknown", "unspecified",
})


def _is_null_sentinel(raw: object) -> bool:
    if not isinstance(raw, str):
        return False
    return raw.strip().lower() in _NULL_SENTINELS


_HTML_TAG_RE = re.compile(r"<[^>]{0,200}>")


def _clean_string(s: str) -> str:
    """Strip HTML tags and normalize whitespace in an extracted string value.

    Handles <br>/<br/> and any other HTML markup that leaks from JSON-LD,
    OG meta tags, or verbatim LLM output on pages with HTML-in-text content.
    """
    s = _HTML_TAG_RE.sub(" ", s)
    return " ".join(s.split())


def coerce_value(raw: object, declared_type: str) -> object:
    if raw is None or _is_null_sentinel(raw):
        return None

    if declared_type == "string":
        if isinstance(raw, list):
            # Structured sources (JSON-LD, OG) sometimes yield an array where
            # the schema asks for a single string. Prefer the first non-empty
            # string element; reject lists of non-strings so a dict doesn't
            # get repr'd into the output.
            for item in raw:
                if isinstance(item, str) and item.strip():
                    return _clean_string(item)
            raise TypeCoercionError(
                "Cannot coerce list to string (no non-empty string element)."
            )
        if isinstance(raw, dict):
            raise TypeCoercionError("Cannot coerce dict to string.")
        s = str(raw)
        if _LEAKED_CREDENTIAL_RE.match(s):
            # Credential-shaped output is almost certainly either an
            # adversarial prompt-injection ("return {api_key: 'sk_live_...'}")
            # or content the page should never have served. Drop to null so
            # neither the API caller nor downstream logs see it.
            return None
        return _clean_string(s) or None

    if declared_type == "integer":
        return _coerce_integer(raw)

    if declared_type == "float":
        return _coerce_float(raw)

    if declared_type == "boolean":
        return _coerce_boolean(raw)

    if declared_type == "date":
        return _coerce_date(raw)

    if declared_type.startswith("array<"):
        inner_type = declared_type[6:-1]  # strip "array<" and ">"
        if isinstance(raw, list):
            items = raw
        elif isinstance(raw, str):
            # Common: meta-tag values come as "tag1, tag2, tag3" strings.
            items = _split_array_string(raw)
            if not items:
                raise TypeCoercionError(
                    f"Cannot coerce string '{raw}' to array."
                )
        else:
            raise TypeCoercionError(f"Expected array, got {type(raw).__name__}")
        return [coerce_value(item, inner_type) for item in items]

    raise TypeCoercionError(f"Unknown type: {declared_type}")


def _split_array_string(s: str) -> list[str]:
    """Split a delimited string into array elements.

    Handles comma, semicolon, pipe, and the Unicode middle-dot (U+00B7) and
    bullet (U+2022) that OpenGraph/Twitter Card tag lists often use. Empty
    elements are dropped.
    """
    parts = re.split(r"[,;|]|\s\u00b7\s|\s\u2022\s", s)
    return [p.strip() for p in parts if p and p.strip()]


_ISO_DURATION_RE = re.compile(
    r"^P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?)?$"
)


def _parse_iso_duration_minutes(s: str) -> int | None:
    """Parse ISO 8601 duration (e.g. 'PT30M', 'PT1H30M', 'P1DT2H') → minutes.
    Returns None if the string is not a valid duration."""
    m = _ISO_DURATION_RE.match(s.strip())
    if not m:
        return None
    days, hours, minutes, seconds = m.groups()
    total = 0
    if days:
        total += int(days) * 24 * 60
    if hours:
        total += int(hours) * 60
    if minutes:
        total += int(minutes)
    if seconds:
        total += int(round(float(seconds) / 60))
    return total


def _coerce_integer(raw: object) -> int:
    if isinstance(raw, int) and not isinstance(raw, bool):
        return raw
    if isinstance(raw, float):
        return int(raw)
    s = str(raw).strip()
    # JSON-LD recipe durations arrive as ISO 8601 ("PT30M", "PT1H30M").
    if s.startswith("P") and "T" in s[:3] or s.startswith("P") and s[1:2].isdigit():
        mins = _parse_iso_duration_minutes(s)
        if mins is not None:
            return mins
    # Extract first integer-like sequence (possibly negative)
    match = re.search(r"-?\d[\d,]*", s)
    if match:
        return int(match.group().replace(",", ""))
    raise TypeCoercionError(f"Cannot coerce '{raw}' to integer.")


def _coerce_float(raw: object) -> float:
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return float(raw)
    s = str(raw).strip()
    # Remove currency symbols
    s = re.sub(r"[$$\u00a3\u20ac\u00a5]", "", s)
    # Handle abbreviations: 1.2M, 3.5B, 100K
    abbrev_match = re.match(r"^(-?[\d,]+\.?\d*)\s*([kKmMbBtT])$", s.strip())
    if abbrev_match:
        num = float(abbrev_match.group(1).replace(",", ""))
        suffix = abbrev_match.group(2).upper()
        multipliers = {"K": 1_000, "M": 1_000_000, "B": 1_000_000_000, "T": 1_000_000_000_000}
        return num * multipliers[suffix]
    # Try parsing directly
    cleaned = re.sub(r"[^\d.\-]", "", s)
    if cleaned:
        try:
            return float(cleaned)
        except ValueError:
            pass
    raise TypeCoercionError(f"Cannot coerce '{raw}' to float.")


def _coerce_boolean(raw: object) -> bool:
    if isinstance(raw, bool):
        return raw
    s = str(raw).strip().lower()
    truthy = {"true", "yes", "1", "y", "on", "\u2713", "\u2714", "\u2705"}
    falsy = {"false", "no", "0", "n", "off", "\u2717", "\u2718", "\u274c"}
    if s in truthy:
        return True
    if s in falsy:
        return False
    raise TypeCoercionError(f"Cannot coerce '{raw}' to boolean.")


# dateutil's fuzzy=True parser is known-slow on pathological inputs (long
# strings of mixed digits/letters/separators). Cap the input before invoking
# it so a crawl over hostile pages can't amplify CPU per call. 256 chars is
# well above any legitimate date-like string from a page; longer values are
# always boilerplate noise where coercion to a date wouldn't make sense.
_MAX_DATE_PARSE_LEN = 256


def _coerce_date(raw: object) -> str:
    if isinstance(raw, datetime):
        return raw.strftime("%Y-%m-%d")
    s = str(raw).strip()
    # ISO format check
    iso_match = re.match(r"^\d{4}-\d{2}-\d{2}", s)
    if iso_match:
        return iso_match.group()
    # Fallback to dateutil — bound length first.
    if len(s) > _MAX_DATE_PARSE_LEN:
        raise TypeCoercionError(
            f"Cannot coerce '{s[:40]}...' to date (input too long)."
        )
    try:
        dt = dateutil_parser.parse(s, fuzzy=True)
        return dt.strftime("%Y-%m-%d")
    except (ValueError, OverflowError):
        raise TypeCoercionError(f"Cannot coerce '{raw}' to date.")


def coerce_extraction_with_warnings(
    raw_data: dict, schema_fields: list[SchemaField]
) -> tuple[dict[str, Any], list[str]]:
    """Coerce each raw value to its declared type, nulling any failures.

    Returns ``(data, warnings)`` where ``warnings`` is a list of human-readable
    strings — one per field whose value was present on the page but could not
    be coerced (e.g. ``"price: could not coerce 'ask for quote' to float"``).
    Callers can surface these to end users so a silent null has an explanation.
    Happy path (all coercions succeed) yields an empty warning list — the
    only extra work per field on success is pushing a local into a list we
    never populate.
    """
    results: dict[str, Any] = {}
    warnings: list[str] = []
    for sf in schema_fields:
        raw_value = raw_data.get(sf.field)
        if raw_value is None or _is_null_sentinel(raw_value):
            results[sf.field] = None
            continue

        try:
            results[sf.field] = coerce_value(raw_value, sf.type)
        except TypeCoercionError as e:
            results[sf.field] = None
            # Keep the user-facing warning concise — avoid leaking large raw
            # payloads (e.g. JSON-LD blobs).
            preview = repr(raw_value)
            if len(preview) > 80:
                preview = preview[:77] + "..."
            warnings.append(
                f"{sf.field}: could not coerce {preview} to {sf.type} ({e})"
            )

    return results, warnings


def coerce_extraction(
    raw_data: dict, schema_fields: list[SchemaField]
) -> dict[str, Any]:
    """Back-compat shim — returns only the coerced data.
    New callers should prefer ``coerce_extraction_with_warnings``."""
    data, _ = coerce_extraction_with_warnings(raw_data, schema_fields)
    return data
