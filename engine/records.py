"""Portable research records: canonical JSON, fingerprints, verification.

A research record (``anna-research-record/v1``) is what the workbench
exports after a search: the request, the retrieval report, the result page
and the source excerpts behind the answer, each with exact offsets into a
named document field. The browser seals it with a SHA-256 fingerprint of
the record's canonical JSON (``EngineEvidence.canonical`` in
``frontend/evidence.js``).

This module is the engine-side twin of that contract, with no dependencies
beyond the standard library:

- :func:`canonical` produces byte-for-byte the string the browser hashes:
  object keys sorted in UTF-16 code-unit order, numbers formatted the way
  ECMAScript's ``Number::toString`` formats them, strings escaped the way
  ``JSON.stringify`` escapes them. :func:`fingerprint` therefore matches the
  ``content_sha256`` of an exported packet.
- :func:`verify_excerpts` re-reads every cited passage from the current
  index and reports whether it is still exactly where the record says it
  is. That is the closest thing to a reproducibility check a citation can
  get, and it is what the API endpoint, the CLI and the workbench run.

A fingerprint match means the record's content is unchanged since export.
A verified excerpt means the cited passage still sits at the recorded
offsets of the live document. Neither says the source is correct.
"""

from __future__ import annotations

import hashlib
import math
import re
from decimal import Decimal
from typing import Any, Callable, Dict, List, Optional, Tuple

RECORD_SCHEMA = "anna-research-record/v1"

#: Offset conventions a record may declare. Python slices count code points;
#: browser records count UTF-16 code units (``String.prototype.slice``).
OFFSET_UNITS = ("unicode-code-points", "utf-16")

#: Document fields an excerpt may cite. The evidence selectors only ever
#: quote abstracts and bodies; the allow-list also keeps a hostile record
#: from reading arbitrary attributes of a document object.
EXCERPT_FIELDS = ("abstract", "body", "title")

STATUS_VERIFIED = "verified"
STATUS_RELOCATED = "relocated"
STATUS_DRIFTED = "drifted"
STATUS_MISSING_DOCUMENT = "missing-document"
STATUS_MISSING_FIELD = "missing-field"
STATUS_INVALID = "invalid-excerpt"
STATUSES = (
    STATUS_VERIFIED,
    STATUS_RELOCATED,
    STATUS_DRIFTED,
    STATUS_MISSING_DOCUMENT,
    STATUS_MISSING_FIELD,
    STATUS_INVALID,
)

_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_MAX_SAFE_INTEGER = 2**53 - 1


# --------------------------------------------------------------------------- #
# Canonical JSON (byte-identical to the browser's EngineEvidence.canonical)
# --------------------------------------------------------------------------- #
def _shortest_digits(value: float) -> Tuple[str, int]:
    """Shortest round-trip decimal digits of a positive finite float.

    Returns ``(digits, exponent)`` with ``value == int(digits) * 10 **
    exponent`` and no trailing zeros in ``digits``. Python's ``repr`` and
    ECMAScript's ``Number::toString`` both pick the shortest digit string
    that round-trips (and the closest one when several do), so the digits
    agree; only the layout differs, which :func:`js_number` reproduces.
    """
    sign, digits, exponent = Decimal(repr(value)).as_tuple()
    digit_list = list(digits)
    while len(digit_list) > 1 and digit_list[-1] == 0:
        digit_list.pop()
        exponent += 1
    return "".join(str(d) for d in digit_list), int(exponent)


def js_number(value: float) -> str:
    """Format a float exactly as ``JSON.stringify`` would in JavaScript.

    Integral values below 1e21 print without a fraction (``1`` not ``1.0``),
    small magnitudes switch to exponent form below 1e-6 (``1e-7`` rather
    than Python's ``1e-07``), and NaN or infinities become ``null``.
    """
    if math.isnan(value) or math.isinf(value):
        return "null"
    if value == 0:
        return "0"
    if value.is_integer() and abs(value) < 1e21:
        return str(int(value))
    sign = "-" if value < 0 else ""
    digits, exponent = _shortest_digits(abs(value))
    k = len(digits)
    n = k + exponent  # position of the decimal point relative to the digits
    if k <= n <= 21:
        return sign + digits + "0" * (n - k)
    if 0 < n <= 21:
        return sign + digits[:n] + "." + digits[n:]
    if -6 < n <= 0:
        return sign + "0." + "0" * (-n) + digits
    e = n - 1
    mantissa = digits if k == 1 else digits[0] + "." + digits[1:]
    return sign + mantissa + "e" + ("+" if e >= 0 else "-") + str(abs(e))


_SHORT_ESCAPES = {
    '"': '\\"',
    "\\": "\\\\",
    "\b": "\\b",
    "\f": "\\f",
    "\n": "\\n",
    "\r": "\\r",
    "\t": "\\t",
}


def _js_string(value: str) -> str:
    """Quote a string exactly as ``JSON.stringify`` does.

    Control characters and lone surrogates become lowercase ``\\uXXXX``
    escapes; everything else, including non-ASCII text and ``/``, is
    emitted verbatim.
    """
    out = ['"']
    for ch in value:
        escaped = _SHORT_ESCAPES.get(ch)
        if escaped is not None:
            out.append(escaped)
            continue
        code = ord(ch)
        if code < 0x20 or 0xD800 <= code <= 0xDFFF:
            out.append("\\u%04x" % code)
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def _utf16_sort_key(key: str) -> bytes:
    # JavaScript's default sort compares UTF-16 code units; big-endian
    # UTF-16 bytes compare the same way, lone surrogates included.
    return key.encode("utf-16-be", "surrogatepass")


def canonical(value: Any) -> str:
    """Serialize ``value`` like ``EngineEvidence.canonical`` in the browser."""
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, int):
        if abs(value) <= _MAX_SAFE_INTEGER:
            return str(value)
        return js_number(float(value))  # a JavaScript number would round too
    if isinstance(value, float):
        return js_number(value)
    if isinstance(value, str):
        return _js_string(value)
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(canonical(item) for item in value) + "]"
    if isinstance(value, dict):
        keys = sorted((str(k) for k in value), key=_utf16_sort_key)
        return (
            "{"
            + ",".join(_js_string(k) + ":" + canonical(value[k]) for k in keys)
            + "}"
        )
    raise TypeError(f"cannot canonicalize {type(value).__name__}")


def fingerprint(record: Any) -> str:
    """Lowercase hex SHA-256 of the canonical UTF-8 JSON of ``record``."""
    return hashlib.sha256(canonical(record).encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- #
# Packets
# --------------------------------------------------------------------------- #
def unwrap_packet(payload: Any) -> Tuple[Dict[str, Any], Optional[str]]:
    """Return ``(record, declared_sha256)`` from a packet or a bare record.

    The workbench exports ``{"captured_at", "content_sha256", "record"}``;
    a bare record (an object carrying ``schema``) is accepted too and has
    no declared fingerprint. Raises :class:`ValueError` for anything else,
    including a malformed ``content_sha256``.
    """
    if not isinstance(payload, dict):
        raise ValueError(
            "a JSON object is required: a research record or its exported "
            "packet"
        )
    if isinstance(payload.get("record"), dict):
        declared = payload.get("content_sha256")
        if declared is not None:
            if not isinstance(declared, str) or not _SHA256_RE.match(declared):
                raise ValueError(
                    "content_sha256 must be a 64-character hexadecimal "
                    "SHA-256 digest"
                )
            declared = declared.lower()
        return payload["record"], declared
    if "schema" in payload or "summary" in payload or "hits" in payload:
        return payload, None
    raise ValueError(
        "not a research record: expected a packet with a 'record' object or "
        "a record with 'schema', 'summary' and 'hits'"
    )


def iter_excerpts(record: Dict[str, Any]) -> List[Tuple[Any, Any]]:
    """``(citation_number, excerpt)`` pairs in record order; tolerant of
    missing or malformed sections (they simply contribute nothing)."""
    summary = record.get("summary") if isinstance(record, dict) else None
    citations = summary.get("citations") if isinstance(summary, dict) else None
    pairs: List[Tuple[Any, Any]] = []
    for citation in citations if isinstance(citations, list) else []:
        if not isinstance(citation, dict):
            continue
        excerpts = citation.get("excerpts")
        for excerpt in excerpts if isinstance(excerpts, list) else []:
            pairs.append((citation.get("n"), excerpt))
    return pairs


def count_excerpts(record: Dict[str, Any]) -> int:
    return len(iter_excerpts(record))


# --------------------------------------------------------------------------- #
# Excerpt verification
# --------------------------------------------------------------------------- #
def slice_field(text: str, start: int, end: int, offset_unit: str) -> str:
    """Slice ``text`` with the record's own offset convention.

    ``utf-16`` offsets come from ``String.prototype.slice`` in the browser,
    so the text is re-encoded as UTF-16 and cut on code-unit boundaries; a
    cut through a surrogate pair simply yields a non-matching slice.
    """
    if offset_unit == "utf-16":
        data = text.encode("utf-16-le", "surrogatepass")
        return data[2 * start : 2 * end].decode("utf-16-le", "surrogatepass")
    return text[start:end]


def locate(text: str, quote: str, offset_unit: str) -> Optional[int]:
    """Offset of the first occurrence of ``quote`` in ``text``, in the
    record's offset unit, or ``None`` when the passage is gone."""
    index = text.find(quote)
    if index < 0:
        return None
    if offset_unit == "utf-16":
        return len(text[:index].encode("utf-16-le", "surrogatepass")) // 2
    return index


def _field_text(document: Any, field: str) -> Optional[str]:
    if isinstance(document, dict):
        value = document.get(field)
    else:
        value = getattr(document, field, None)
    return value if isinstance(value, str) else None


def _excerpt_problem(excerpt: Any) -> Optional[str]:
    """Why an excerpt cannot be checked at all, or ``None`` when it can."""
    if not isinstance(excerpt, dict):
        return "excerpt must be an object"
    if (
        not isinstance(excerpt.get("document_id"), str)
        or not excerpt["document_id"].strip()
    ):
        return "document_id must be a nonempty string"
    if excerpt.get("field") not in EXCERPT_FIELDS:
        return "field must be one of " + ", ".join(EXCERPT_FIELDS)
    if not isinstance(excerpt.get("quote"), str) or not excerpt["quote"]:
        return "quote must be a nonempty string"
    unit = excerpt.get("offset_unit", "unicode-code-points")
    if unit not in OFFSET_UNITS:
        return "offset_unit must be one of " + ", ".join(OFFSET_UNITS)
    start, end = excerpt.get("start"), excerpt.get("end")
    for name, value in (("start", start), ("end", end)):
        if isinstance(value, bool) or not isinstance(value, int):
            return f"{name} must be an integer"
    if start < 0 or end <= start:
        return "offsets must satisfy 0 <= start < end"
    return None


def verify_excerpt(
    excerpt: Any, document: Any, *, citation: Any = None
) -> Dict[str, Any]:
    """Check one excerpt against the document it cites.

    ``document`` is a :class:`~engine.documents.Document`, a plain mapping
    with the same field names, or ``None`` when the index no longer has it.
    """
    report: Dict[str, Any] = {"citation": citation}
    if isinstance(excerpt, dict):
        for key in ("document_id", "field", "start", "end"):
            report[key] = excerpt.get(key)
        report["offset_unit"] = excerpt.get(
            "offset_unit", "unicode-code-points"
        )
    problem = _excerpt_problem(excerpt)
    if problem is not None:
        report.update(status=STATUS_INVALID, detail=problem)
        return report
    if document is None:
        report["status"] = STATUS_MISSING_DOCUMENT
        return report
    text = _field_text(document, excerpt["field"])
    if text is None:
        report["status"] = STATUS_MISSING_FIELD
        return report
    unit = report["offset_unit"]
    if (
        slice_field(text, excerpt["start"], excerpt["end"], unit)
        == excerpt["quote"]
    ):
        report["status"] = STATUS_VERIFIED
        return report
    found = locate(text, excerpt["quote"], unit)
    if found is None:
        report["status"] = STATUS_DRIFTED
    else:
        report.update(status=STATUS_RELOCATED, found_at=found)
    return report


def verify_excerpts(
    record: Dict[str, Any], get_document: Callable[[str], Any]
) -> List[Dict[str, Any]]:
    """Verify every excerpt in ``record``, fetching each document once.

    ``get_document(document_id)`` returns the current document or ``None``.
    Backend errors it raises propagate unchanged so callers can distinguish
    "the index does not have this document" from "the index is down".
    """
    cache: Dict[str, Any] = {}
    reports: List[Dict[str, Any]] = []
    for citation, excerpt in iter_excerpts(record):
        document = None
        if _excerpt_problem(excerpt) is None:
            doc_id = excerpt["document_id"]
            if doc_id not in cache:
                cache[doc_id] = get_document(doc_id)
            document = cache[doc_id]
        reports.append(verify_excerpt(excerpt, document, citation=citation))
    return reports


def count_statuses(reports: List[Dict[str, Any]]) -> Dict[str, int]:
    counts = {status: 0 for status in STATUSES}
    for report in reports:
        counts[report["status"]] = counts.get(report["status"], 0) + 1
    return counts


def verify_record(
    payload: Any,
    get_document: Optional[Callable[[str], Any]] = None,
    *,
    checked_against: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Full verification report for a packet or bare record.

    Without ``get_document`` only the fingerprint is checked (offline mode).
    ``checked_against`` is echoed so a reader knows which index answered
    (for example ``{"backend": "postgres", "index": "engineering_docs"}``).
    ``ok`` is true only when the fingerprint is not contradicted and every
    checked excerpt is ``verified``; a relocated passage still exists but
    the record's offsets are stale, so it does not count as ok.
    """
    record, declared = unwrap_packet(payload)
    computed = fingerprint(record)
    matches = None if declared is None else declared == computed
    reports = (
        verify_excerpts(record, get_document)
        if get_document is not None
        else []
    )
    schema = record.get("schema")
    return {
        "schema": schema if isinstance(schema, str) else None,
        "schema_known": schema == RECORD_SCHEMA,
        "fingerprint": {
            "declared": declared,
            "computed": computed,
            "matches": matches,
        },
        "checked_against": checked_against,
        "excerpts": reports,
        "counts": count_statuses(reports),
        "ok": matches is not False
        and all(r["status"] == STATUS_VERIFIED for r in reports),
    }
