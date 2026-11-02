"""Spelling correction: a word no document contains, read as the word meant.

A slip in the word that matters most costs a query most of its quality and
gives no sign of it: coverage matching (:mod:`engine.lexical`) answers from
the other words, so "reinforcment learning" returns papers about *learning*.
On the judged set (``test/relevance``), one slip in each topic's longest word
took nDCG@10 from 0.751 to 0.547 — and emptied none of the result pages that
would have told the user.

A typed word is corrected only when all of these hold:

- no indexed document contains it (its document frequency is 0);
- it is a plain word of :data:`MIN_LENGTH` or more letters, typed outside
  quotes and without a minus sign — not a number, a part name like
  "stm32h7", or an acronym short enough to be ambiguous;
- it is not an English word. In a small corpus most everyday words are
  absent, and without this check 36 of 121 of them were "corrected" into a
  technical neighbour ("lunch robot motion planning" into "launch …"). The
  check uses ENABLE, a public-domain word list (``engine/data``).

The correction is the indexed word the typed one most plausibly slipped
from, looked for in this order:

1. one slip away, among the words of the documents the rest of the query
   found — the vocabulary the query is about (the most frequent there);
2. one slip away, anywhere in the index (the backend's lookup: the most
   common such word);
3. two slips away, among the words of those documents (typed words of six
   or more letters).

A *slip* is a dropped, doubled or extra letter, two adjacent letters typed
in the wrong order, or a key replaced by a neighbouring key (or one vowel by
another); the first letter is taken as typed. The search then runs for the
corrected query, and the results say so — ``spelling`` names the typed query
and each corrected word, so a page can offer to search for what was typed.

Pure: the backends supply document frequencies, the words of the documents
a query found, and their index-wide lookup; every rule here is unit-tested
without either.
"""

from __future__ import annotations

import gzip
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import (
    Any,
    Callable,
    Dict,
    Iterable,
    List,
    Optional,
    Sequence,
    Set,
    Tuple,
)

from engine.lexical import STOP_WORDS, QueryTerm

# Shorter words are corrected too readily into other short words — and are
# where acronyms live.
MIN_LENGTH = 5

# How many of the documents a query found lend their words as context.
CONTEXT_DOCUMENTS = 20

WORDS_PATH = Path(__file__).with_name("data") / "english-words.txt.gz"

_ALPHABET = "abcdefghijklmnopqrstuvwxyz"
_VOWELS = frozenset("aeiouy")
_KEYBOARD = ("qwertyuiop", "asdfghjkl", "zxcvbnm")
_WORD = re.compile(r"[a-z]+")
_TYPED_WORD = re.compile(r"[A-Za-z]+")


def _neighbouring_keys() -> Dict[str, frozenset]:
    """Each letter's neighbours on a QWERTY keyboard (rows offset)."""
    where = {
        key: (row, col)
        for row, keys in enumerate(_KEYBOARD)
        for col, key in enumerate(keys)
    }
    near: Dict[str, frozenset] = {}
    for key, (row, col) in where.items():
        near[key] = frozenset(
            other
            for other, (r, c) in where.items()
            if other != key and abs(r - row) <= 1 and abs(c - col) <= 1
        )
    return near


NEIGHBOURS = _neighbouring_keys()


def likely_key(typed: str, meant: str) -> bool:
    """Whether ``typed`` is a plausible slip for the key ``meant``."""
    return meant in NEIGHBOURS.get(typed, ()) or (
        typed in _VOWELS and meant in _VOWELS
    )


def one_slip(word: str) -> Set[str]:
    """The words ``word`` may have been typed for, one slip away.

    Dropping a letter from ``word`` undoes an extra one; adding any letter
    undoes a dropped one; swapping two adjacent letters undoes a swap; and
    replacing a letter by a neighbouring key (or a vowel by a vowel) undoes
    a wrong key. The first letter stays as typed.
    """
    word = word.lower()
    if len(word) < 2:
        return set()
    head, tail = word[0], word[1:]
    meant: Set[str] = set()
    for i in range(len(tail) + 1):
        before, after = tail[:i], tail[i:]
        if after:
            meant.add(head + before + after[1:])
        if len(after) > 1:
            meant.add(head + before + after[1] + after[0] + after[2:])
        for letter in _ALPHABET:
            if after and likely_key(after[0], letter):
                meant.add(head + before + letter + after[1:])
            meant.add(head + before + letter + after)
    meant.discard(word)
    return meant


def slips(a: str, b: str, limit: int = 2) -> int:
    """Edit distance between ``a`` and ``b`` counting adjacent swaps as one.

    The optimal-string-alignment distance; anything beyond ``limit`` is
    reported as ``limit + 1`` without being computed exactly.
    """
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    previous: Optional[List[int]] = None
    row = list(range(len(b) + 1))
    for i in range(1, len(a) + 1):
        current = [i] + [0] * len(b)
        for j in range(1, len(b) + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            current[j] = min(
                row[j] + 1, current[j - 1] + 1, row[j - 1] + cost
            )
            if (
                previous is not None
                and i > 1
                and j > 1
                and a[i - 1] == b[j - 2]
                and a[i - 2] == b[j - 1]
            ):
                current[j] = min(current[j], previous[j - 2] + 1)
        if min(current) > limit:
            return limit + 1
        previous, row = row, current
    return min(row[len(b)], limit + 1)


def words(text: str) -> List[str]:
    """The lower-case words of ``text`` (letters only)."""
    return _WORD.findall((text or "").lower())


def vocabulary(texts: Iterable[str]) -> Counter:
    """How often each word occurs in ``texts``, stop words left out."""
    counts: Counter = Counter()
    for text in texts:
        counts.update(w for w in words(text) if w not in STOP_WORDS)
    return counts


def nearest(typed: str, texts: Iterable[str]) -> Optional[str]:
    """The word in ``texts`` closest to ``typed`` — at most two slips away.

    The way an index-wide lookup turns an indexed *stem* back into a word:
    the documents that contain the stem show how it is spelled.
    """
    typed = typed.lower()
    found = [
        (slips(word, typed), -count, word)
        for word, count in vocabulary(texts).items()
        if word[0] == typed[0] and word != typed
    ]
    found = [entry for entry in found if entry[0] <= 2]
    return min(found)[2] if found else None


# --------------------------------------------------------------------------- #
# English words: never corrected
# --------------------------------------------------------------------------- #
class EnglishWords:
    """ENABLE's words of :data:`MIN_LENGTH`+ letters, loaded on first use.

    Kept as one newline-separated string: a lookup is a substring search
    (about a millisecond), and the list costs 1.7 MB of memory instead of
    the ~15 MB a set of 168,000 strings would.
    """

    def __init__(self, path: Path = WORDS_PATH) -> None:
        self.path = path
        self._text: Optional[str] = None

    def __contains__(self, word: object) -> bool:
        if not isinstance(word, str) or not word:
            return False
        if self._text is None:
            with gzip.open(self.path, "rt", encoding="ascii") as handle:
                self._text = "\n" + handle.read()
        return f"\n{word.lower()}\n" in self._text


ENGLISH = EnglishWords()


# --------------------------------------------------------------------------- #
# The correction
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Correction:
    """A query as typed, the query searched instead, and what changed."""

    typed: str
    query: str
    words: Tuple[Tuple[str, str], ...]

    def report(self) -> Dict[str, Any]:
        """The ``spelling`` block of a search response."""
        return {
            "typed": self.typed,
            "query": self.query,
            "corrections": [
                {"typed": typed, "corrected": corrected}
                for typed, corrected in self.words
            ],
        }


def suspect(term: QueryTerm, english: Any = ENGLISH) -> Optional[str]:
    """The word ``term`` may misspell, when it is eligible for correction.

    A plain word of :data:`MIN_LENGTH`+ ASCII letters (punctuation around it
    is ignored), typed outside quotes and without a minus sign, and not an
    English word. Its document frequency is the caller's to check.
    """
    if term.phrase or term.negated:
        return None
    found = _TYPED_WORD.findall(term.text)
    if len(found) != 1 or len(found[0]) < MIN_LENGTH:
        return None
    word = found[0].lower()
    if word != term.text.strip(".,;:!?()[]{}'\"").lower():
        return None  # letters mixed with digits or symbols: a name, a part
    if word in english:
        return None
    return word


def correct(
    query: str,
    terms: Sequence[Tuple[QueryTerm, int]],
    context: Counter,
    lookup: Callable[[str], Optional[str]],
    english: Any = ENGLISH,
) -> Optional[Correction]:
    """The corrected query for ``query``, or ``None`` when nothing to fix.

    ``terms`` pairs each query term with its document frequency;
    ``context`` counts the words of the documents the query found (see
    :func:`vocabulary` and :data:`CONTEXT_DOCUMENTS`); ``lookup`` finds the
    most common indexed word one slip from a typed word, or ``None``.
    """
    fixes: Dict[str, str] = {}
    for term, frequency in terms:
        if frequency:
            continue
        typed = suspect(term, english)
        if typed is None or typed in fixes:
            continue
        meant = _choose(typed, context, lookup)
        if meant:
            fixes[typed] = meant
    if not fixes:
        return None
    return Correction(
        typed=query,
        query=_rewrite(query, fixes),
        words=tuple(fixes.items()),
    )


def _choose(
    typed: str, context: Counter, lookup: Callable[[str], Optional[str]]
) -> Optional[str]:
    near = one_slip(typed)
    in_context = [(-count, word) for word, count in context.items() if word in near]
    if in_context:
        return min(in_context)[1]
    found = lookup(typed)
    if found:
        return found
    if len(typed) >= 6:
        further = [
            (slips(word, typed), -count, word)
            for word, count in context.items()
            if word[0] == typed[0] and word != typed
        ]
        further = [entry for entry in further if entry[0] <= 2]
        if further:
            return min(further)[2]
    return None


def _rewrite(query: str, fixes: Dict[str, str]) -> str:
    """``query`` with each corrected word replaced, its capitals kept."""

    def replace(match: "re.Match[str]") -> str:
        word = match.group(0)
        fixed = fixes.get(word.lower())
        if fixed is None:
            return word
        if word.isupper():
            return fixed.upper()
        if word[0].isupper():
            return fixed[0].upper() + fixed[1:]
        return fixed

    return _TYPED_WORD.sub(replace, query)
