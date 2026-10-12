"""Citations for indexed documents: BibTeX entries and plain-text references.

"Engineering answers you can cite" ends with an actual citation. Every
document the engine returns can be rendered as a BibTeX entry or a one-line
reference, deterministically, from the fields the index already holds:
authors, title, date, source, version, identifiers and link. Nothing here
needs a backend, a model or a network, and the output never varies between
runs — the same document gives the same key and the same bytes, so a
bibliography can be regenerated and compared.

``frontend/citations.js`` is the byte-exact twin of this module (same keys,
same escaping, same field order), so a citation copied from the workbench —
Live or Demo — matches one fetched from ``GET /api/v1/document/<id>/citation``
or printed by ``flask engine cite``. ``test/engine/test_citations.py``
executes the JavaScript under Node to keep the two in step.

Keys follow the common ``<surname><year><first title word>`` convention
(``devlin2024zero``); entry types follow the document kind (paper → article,
report and standard → techreport, documentation and datasheet → manual,
everything else → misc). The ``annaid`` field carries the engine's document
id so a reference can be traced back to the index it came from.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Dict, Iterable, List, Optional, Tuple

from engine.evidence import STOP_WORDS

#: The sources' display names, as the plugin registry declares them. The
#: browser twin cannot import the registry, so the names live here too;
#: ``test_citations.py`` checks they agree with ``engine.ingest``.
SOURCE_LABELS: Dict[str, str] = {
    "arxiv": "arXiv",
    "github": "GitHub",
    "nasa": "NASA Technical Reports (NTRS)",
    "doe": "DOE OSTI",
    "ieee": "IEEE Open Access",
    "nist": "NIST Publications",
    "linux_kernel": "Linux Kernel Documentation",
    "stm32": "STM32 Documentation",
    "espressif": "Espressif ESP-IDF Documentation",
    "arm": "ARM Developer Documentation",
    "riscv": "RISC-V Specifications",
    "shadowlibraries": "Shadow Libraries",
}

#: BibTeX entry type by document kind; anything unlisted is ``misc``.
ENTRY_TYPES: Dict[str, str] = {
    "paper": "article",
    "report": "techreport",
    "standard": "techreport",
    "documentation": "manual",
    "datasheet": "manual",
}

# Whitespace as JavaScript's \s defines it, spelled out so both twins
# collapse exactly the same characters.
_WS = (
    "[ \t\n\r\f\v   -     　"
    "﻿]"
)
_WS_RE = re.compile(_WS + "+")
_YEAR_RE = re.compile(_WS + "*([0-9]{4})")
_SLUG_DROP_RE = re.compile(r"[^a-z0-9]")
_SLUG_TOKEN_RE = re.compile(r"[a-z0-9]+")
_AND_RE = re.compile(r"(?:^| )and(?: |$)", re.IGNORECASE)
_HTTP_RE = re.compile(r"^https?://", re.IGNORECASE)

_ESCAPES = {
    "\\": "\\textbackslash{}",
    "{": "\\{",
    "}": "\\}",
    "&": "\\&",
    "%": "\\%",
    "$": "\\$",
    "#": "\\#",
    "_": "\\_",
    "^": "\\^{}",
    "~": "\\~{}",
}


# --------------------------------------------------------------------------- #
# Field access (a Document, or a plain mapping with the same names)
# --------------------------------------------------------------------------- #
def _get(doc: Any, name: str, default: Any = None) -> Any:
    if isinstance(doc, dict):
        value = doc.get(name, default)
    else:
        value = getattr(doc, name, default)
    return default if value is None else value


def clean(text: Any) -> str:
    """Collapse runs of whitespace to one space and trim the ends."""
    if not isinstance(text, str):
        return ""
    return _WS_RE.sub(" ", text).strip(" ")


def _text(doc: Any, name: str) -> str:
    return clean(_get(doc, name, ""))


def _authors(doc: Any) -> List[str]:
    value = _get(doc, "authors", [])
    if not isinstance(value, (list, tuple)):
        return []
    return [clean(a) for a in value if isinstance(a, str) and clean(a)]


def _identifiers(doc: Any) -> Dict[str, str]:
    value = _get(doc, "identifiers", {})
    if not isinstance(value, dict):
        return {}
    return {
        str(k): clean(v)
        for k, v in value.items()
        if isinstance(v, str) and clean(v)
    }


# --------------------------------------------------------------------------- #
# Keys
# --------------------------------------------------------------------------- #
def slug(text: str) -> str:
    """ASCII letters and digits only: NFKD-decomposed, lowercased, the rest
    dropped (``"Müller-Lüdenscheidt"`` → ``"mullerludenscheidt"``)."""
    return _SLUG_DROP_RE.sub(
        "", unicodedata.normalize("NFKD", text or "").lower()
    )


def _slug_tokens(text: str) -> List[str]:
    return _SLUG_TOKEN_RE.findall(
        unicodedata.normalize("NFKD", text or "").lower()
    )


def surname(author: str) -> str:
    """The family name of one author string, as a key slug.

    ``"Smith, John"`` → ``smith``; ``"J. R. Smith"`` → ``smith``.
    """
    author = clean(author)
    if "," in author:
        head = author.split(",", 1)[0]
    else:
        parts = author.split(" ")
        head = parts[-1] if parts else ""
    return slug(head)


def year_of(published: Any) -> str:
    """The four-digit year a date string starts with, or ``""``."""
    match = _YEAR_RE.match(published if isinstance(published, str) else "")
    return match.group(1) if match else ""


def title_word(title: str) -> str:
    """The first content word of a title, for the key (``zero`` for
    "Zero-Copy Circular DMA…"); ``untitled`` when there is none."""
    tokens = _slug_tokens(title)
    for token in tokens:
        if len(token) >= 3 and token not in STOP_WORDS:
            return token
    return tokens[0] if tokens else "untitled"


def bibtex_key(doc: Any) -> str:
    """``<surname><year><word>``, from the first author (the source when
    there is none), the publication year (``nd`` when unknown) and the
    first content word of the title."""
    authors = _authors(doc)
    who = surname(authors[0]) if authors else ""
    if not who:
        who = slug(_text(doc, "source"))
    if not who:
        who = "anna"
    year = year_of(_get(doc, "published", "")) or "nd"
    return who[:32] + year + title_word(_text(doc, "title"))[:32]


# --------------------------------------------------------------------------- #
# BibTeX
# --------------------------------------------------------------------------- #
def escape(text: Any) -> str:
    """Escape BibTeX's special characters in a field value, one pass, so an
    escape never gets escaped again (``\\`` → ``\\textbackslash{}``)."""
    return "".join(_ESCAPES.get(ch, ch) for ch in clean(text))


def source_label(source: str) -> str:
    source = clean(source)
    return SOURCE_LABELS.get(source, source)


def _author_field(author: str) -> str:
    # BibTeX splits author lists on the word "and"; an organization whose
    # name contains it ("Research and Development Corp") stays one author
    # when braced.
    value = escape(author)
    return "{" + value + "}" if _AND_RE.search(author) else value


def link(doc: Any) -> str:
    """The document's http(s) link (its page, else its PDF), with whitespace
    removed and braces percent-encoded so it is safe inside ``url = {…}``;
    ``""`` for anything that is not an http(s) URL."""
    for name in ("url", "pdf_url"):
        value = _get(doc, name, "")
        if not isinstance(value, str):
            continue
        value = _WS_RE.sub("", value)
        if _HTTP_RE.match(value):
            return (
                value.replace("\\", "%5C")
                .replace("{", "%7B")
                .replace("}", "%7D")
            )
    return ""


def bibtex(doc: Any, key: Optional[str] = None) -> str:
    """One BibTeX entry for ``doc`` (key from :func:`bibtex_key` unless
    given). Field order is fixed; empty fields are left out."""
    kind = _text(doc, "kind")
    entry_type = ENTRY_TYPES.get(kind, "misc")
    source = _text(doc, "source")
    label = source_label(source)
    identifiers = _identifiers(doc)
    authors = _authors(doc)
    fields: List[Tuple[str, str]] = []
    if authors:
        fields.append(("author", " and ".join(map(_author_field, authors))))
    fields.append(("title", "{" + escape(_text(doc, "title") or "Untitled") + "}"))
    eprint = identifiers.get("arxiv_id", "")
    if entry_type == "article" and source == "arxiv":
        fields.append(
            (
                "journal",
                escape("arXiv preprint" + (" arXiv:" + eprint if eprint else "")),
            )
        )
    elif entry_type == "techreport" and label:
        fields.append(("institution", escape(label)))
    elif entry_type == "manual" and label:
        fields.append(("organization", escape(label)))
    elif entry_type == "misc" and label:
        fields.append(("howpublished", escape(label)))
    year = year_of(_get(doc, "published", ""))
    if year:
        fields.append(("year", year))
    version = _text(doc, "version")
    if version:
        fields.append(("version", escape(version)))
    if identifiers.get("doi"):
        fields.append(("doi", escape(identifiers["doi"])))
    if eprint:
        fields.append(("eprint", escape(eprint)))
        fields.append(("archivePrefix", "arXiv"))
    url = link(doc)
    if url:
        fields.append(("url", url))
    doc_id = _text(doc, "id")
    if doc_id:
        fields.append(("annaid", escape(doc_id)))
    lines = ["@" + entry_type + "{" + (key or bibtex_key(doc)) + ","]
    for index, (name, value) in enumerate(fields):
        comma = "," if index < len(fields) - 1 else ""
        lines.append("  " + name + " = {" + value + "}" + comma)
    lines.append("}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Plain text
# --------------------------------------------------------------------------- #
def _join_names(names: List[str]) -> str:
    if len(names) == 1:
        return names[0]
    if len(names) == 2:
        return names[0] + " and " + names[1]
    return ", ".join(names[:-1]) + ", and " + names[-1]


def plain(doc: Any) -> str:
    """A one-line reference: authors (year). Title. Source, version. Link.

    A readable reference, not a particular style guide; the BibTeX entry
    is what a reference manager should ingest.
    """
    authors = _authors(doc)
    title = _text(doc, "title") or "Untitled"
    year = year_of(_get(doc, "published", "")) or "n.d."
    if authors:
        head = _join_names(authors) + " (" + year + "). " + title
    else:
        head = title + " (" + year + ")"
    if head[-1] not in ".?!":
        head += "."
    parts = [head]
    venue = source_label(_text(doc, "source"))
    version = _text(doc, "version")
    if version:
        venue += (", " if venue else "") + "version " + version
    if venue:
        parts.append(venue if venue[-1] in ".?!" else venue + ".")
    url = link(doc)
    if url:
        parts.append(url)
    return " ".join(parts)


# --------------------------------------------------------------------------- #
# Several documents at once
# --------------------------------------------------------------------------- #
def _unique_key(base: str, occurrence: int) -> str:
    if occurrence == 1:
        return base
    if occurrence <= 26:
        return base + chr(ord("a") + occurrence - 1)
    return base + "-" + str(occurrence)


def citations(docs: Iterable[Any]) -> List[Dict[str, str]]:
    """``{id, key, bibtex, text}`` for each document, with keys made unique
    in order (``smith2024foo``, ``smith2024foob``, …)."""
    seen: Dict[str, int] = {}
    out: List[Dict[str, str]] = []
    for doc in docs:
        base = bibtex_key(doc)
        occurrence = seen.get(base, 0) + 1
        seen[base] = occurrence
        key = _unique_key(base, occurrence)
        out.append(
            {
                "id": _text(doc, "id"),
                "key": key,
                "bibtex": bibtex(doc, key),
                "text": plain(doc),
            }
        )
    return out


def citation(doc: Any) -> Dict[str, str]:
    """``{id, key, bibtex, text}`` for one document."""
    return citations([doc])[0]


def bibliography(docs: Iterable[Any]) -> str:
    """The documents' BibTeX entries, blank-line separated, as one file."""
    entries = [c["bibtex"] for c in citations(docs)]
    return "\n\n".join(entries) + "\n" if entries else ""
