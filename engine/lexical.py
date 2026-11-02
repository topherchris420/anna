"""What a query asks the lexical retrievers for: terms, coverage, weights.

Both backends match a query the same way, and this module is where that way
is written down. A query is split into **terms** — each typed word, or each
"quoted phrase" — plus **excluded** terms (a leading ``-``). A document is a
lexical match when it carries enough of the terms (``required_matches``) and
none of the excluded ones; it then ranks by the terms it carries, each
weighted by how rare it is in the corpus (``bm25_idf``), so the one word in
"kalman filter state estimation" that actually discriminates — "kalman" —
outweighs the three that half the corpus contains.

Why not require every term? Because people type questions. "how do I
estimate state with a kalman filter" asks for four content terms, and a
paper about Kalman filtering that never says "estimate" is still the answer.
Requiring all of them returned nothing for most natural-language questions;
requiring any of them returned everything that says "state". Coverage sits
between, and its threshold is one setting shared by both backends
(``ENGINE_LEXICAL_MIN_SHOULD_MATCH``), in Elasticsearch's
``minimum_should_match`` notation.

Pure and dependency-free: the backends turn these terms into SQL or query
DSL, and every rule here is unit-tested without either.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import List, Tuple

# PostgreSQL's English stop words (``tsearch_data/english.stop``, the Snowball
# list). Postgres drops these from the full-text index itself; dropping the
# same words before counting terms is what lets both backends agree that
# "how do I estimate state with a kalman filter" asks for four terms, not ten.
STOP_WORDS = frozenset(
    """
    i me my myself we our ours ourselves you your yours yourself yourselves
    he him his himself she her hers herself it its itself they them their
    theirs themselves what which who whom this that these those am is are was
    were be been being have has had having do does did doing a an the and but
    if or because as until while of at by for with about against between into
    through during before after above below to from up down in out on off
    over under again further then once here there when where why how all any
    both each few more most other some such no nor not only own same so than
    too very s t can will just don should now
    """.split()
)

_WORD_CHARS = re.compile(r"[^\W_]+", re.UNICODE)

# Every term costs the backends work — a document-frequency count and a rank
# term on Postgres, a clause per field on Elasticsearch — and a 2,000-character
# query can hold hundreds. No real question has more than a few dozen, so
# terms past this many are ignored rather than allowed to multiply the cost of
# a search (or exceed Elasticsearch's clause limit).
MAX_TERMS = 32


@dataclass(frozen=True)
class QueryTerm:
    """One unit of a query, as the user typed it.

    ``text`` is the word or the phrase without its quotes or minus sign.
    ``phrase`` marks text typed in double quotes, which must match as a
    phrase; a lone word that the index splits into several tokens ("RISC-V",
    "wi-fi") is matched the same way, so the distinction only records what
    was typed. ``negated`` marks a term the results must not contain.
    """

    text: str
    phrase: bool = False
    negated: bool = False

    @property
    def label(self) -> str:
        """The term the way an explanation should show it."""
        shown = f'"{self.text}"' if self.phrase else self.text
        return f"-{shown}" if self.negated else shown


def _is_content(text: str) -> bool:
    """Whether a typed word carries anything a full-text index keeps."""
    words = [w.lower() for w in _WORD_CHARS.findall(text)]
    return any(w not in STOP_WORDS for w in words)


def parse_query(query: str) -> List[QueryTerm]:
    """Split ``query`` into the terms the lexical retrievers match.

    The syntax is a small, forgiving subset of web-search conventions:

    - words are separated by whitespace; each content word is one term;
    - ``"a quoted phrase"`` is one term that must match as a phrase (an
      unclosed quote runs to the end of the query);
    - a leading ``-`` excludes the word or phrase it is attached to.

    Stop words ("how", "do", "the" …) and punctuation-only tokens are not
    terms — the index drops them, so counting them would make every question
    look less covered than it is. Inside quotes they are kept: they are part
    of the phrase. Repeated terms count once, and only the first
    :data:`MAX_TERMS` terms are kept.
    """
    terms: List[QueryTerm] = []
    seen = set()
    text = query or ""
    i, n = 0, len(text)
    while i < n and len(terms) < MAX_TERMS:
        if text[i].isspace():
            i += 1
            continue
        negated = False
        if text[i] == "-" and i + 1 < n and not text[i + 1].isspace():
            negated = True
            i += 1
        if text[i] == '"':
            close = text.find('"', i + 1)
            end = n if close == -1 else close
            body = " ".join(text[i + 1 : end].split())
            i = end + 1
            words = _WORD_CHARS.findall(body)
            if not words:
                continue
            # A quoted single word is just that word; a quoted phrase made
            # only of stop words carries nothing an index can match.
            phrase = len(words) > 1
            if not _is_content(body):
                continue
            term = QueryTerm(body, phrase=phrase, negated=negated)
        else:
            start = i
            while i < n and not text[i].isspace():
                i += 1
            word = text[start:i]
            if not _is_content(word):
                continue
            term = QueryTerm(word, negated=negated)
        key = (term.text.lower(), term.negated)
        if key not in seen:
            seen.add(key)
            terms.append(term)
    return terms


def split_terms(terms: List[QueryTerm]) -> Tuple[List[QueryTerm], List[QueryTerm]]:
    """``(included, excluded)`` terms, each in typed order."""
    return (
        [t for t in terms if not t.negated],
        [t for t in terms if t.negated],
    )


# --------------------------------------------------------------------------- #
# Coverage: Elasticsearch ``minimum_should_match`` semantics
# --------------------------------------------------------------------------- #
_CONDITIONAL = re.compile(r"^(\d+)<(.+)$")


def _apply_simple(spec: str, n: int) -> int:
    """One non-conditional ``minimum_should_match`` value for ``n`` terms."""
    spec = spec.strip()
    if spec.endswith("%"):
        percent = float(spec[:-1])
        # Elasticsearch rounds the computed share down, then — for a
        # negative percentage — subtracts it from the total.
        share = math.floor(n * abs(percent) / 100.0)
        return n - share if percent < 0 else share
    count = int(spec)
    return n + count if count < 0 else count


def required_matches(spec: str, n: int) -> int:
    """How many of ``n`` query terms a document must contain.

    Implements Elasticsearch's ``minimum_should_match`` notation so the
    Postgres backend can apply the very setting Elasticsearch does:

    - ``"3"`` / ``"-1"`` — a fixed count, or all but that many;
    - ``"75%"`` / ``"-25%"`` — a share of the terms, rounded down (negative:
      all but that share);
    - ``"2<70%"`` — conditional: with ``n`` at or below 2 *every* term is
      required, above it the right-hand value applies. Several conditions
      may be given, space-separated (``"2<-25% 9<-3"``); the one with the
      largest bound below ``n`` wins.

    Whatever the arithmetic says, the result is clamped to ``1..n``: a query
    with terms always requires at least one, never more than it has. An
    unparseable value falls back to requiring every term — the strict
    reading, which can lose recall but never returns unrelated documents.
    """
    if n <= 0:
        return 0
    try:
        value = _evaluate(spec, n)
    except (AttributeError, TypeError, ValueError):
        value = n
    return max(1, min(n, value))


def _evaluate(spec: str, n: int) -> int:
    parts = spec.split()
    if not parts:
        raise ValueError("empty minimum_should_match")
    if len(parts) == 1 and not _CONDITIONAL.match(parts[0]):
        return _apply_simple(parts[0], n)
    conditions = []
    for part in parts:
        match = _CONDITIONAL.match(part)
        if not match:
            raise ValueError(f"not a condition: {part!r}")
        conditions.append((int(match.group(1)), match.group(2)))
    applicable = [c for c in conditions if n > c[0]]
    if not applicable:
        return n
    return _apply_simple(max(applicable)[1], n)


def bm25_idf(document_frequency: int, corpus_size: int) -> float:
    """Inverse document frequency, as BM25 (and Elasticsearch) define it.

    ``ln(1 + (N - df + 0.5) / (df + 0.5))`` — positive for every term, large
    for a term only a few documents contain, close to zero for one most of
    them do.
    """
    n = max(int(corpus_size), 0)
    df = min(max(int(document_frequency), 0), n)
    return math.log(1.0 + (n - df + 0.5) / (df + 0.5))
