"""Write misspellings.json: the judged queries, misspelled the way people type.

    python test/relevance/misspell.py        # from the repository root

In each answerable topic's query, the longest purely alphabetic word of five
or more letters (the first, on a tie) is misspelled by one typing slip,
chosen by a generator seeded with the topic id: a letter dropped, a letter
doubled, two adjacent letters swapped, or a key replaced by a neighbouring
one (QWERTY). The first letter is never touched. Words of eight or more
letters also get a second variant with two slips. A variant that is itself a
word — in the corpus, or in English — is a real-word error, which spelling
correction does not attempt; it is drawn again.

Deterministic: rerunning it reproduces the file byte for byte. Each variant
keeps its topic's judgments (the need is unchanged), so a ranking for the
misspelled query is scored against the same grades as the intended one.
"""

from __future__ import annotations

import json
import random
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from engine import evaluation  # noqa: E402
from engine.spelling import ENGLISH, NEIGHBOURS  # noqa: E402

HERE = Path(__file__).resolve().parent
OUT = HERE / "misspellings.json"


def _slip(word: str, rng: random.Random):
    kind = rng.choice(["drop", "double", "swap", "neighbour"])
    if kind == "swap":
        i = rng.randrange(1, len(word) - 1)
        if word[i] == word[i + 1]:
            return None
        return word[:i] + word[i + 1] + word[i] + word[i + 2:], kind
    i = rng.randrange(1, len(word))
    if kind == "drop":
        return word[:i] + word[i + 1:], kind
    if kind == "double":
        return word[:i] + word[i] + word[i:], kind
    keys = sorted(NEIGHBOURS.get(word[i], ()))
    if not keys:
        return None
    return word[:i] + rng.choice(keys) + word[i + 1:], kind


def misspell(word: str, slips: int, rng: random.Random, known) -> tuple:
    for _ in range(500):
        typo, kinds = word, []
        for _ in range(slips):
            made = _slip(typo, rng)
            if made is None:
                break
            typo, kind = made
            kinds.append(kind)
        else:
            if typo != word and typo not in known and typo not in ENGLISH:
                return typo, kinds
    raise RuntimeError(f"no misspelling found for {word!r}")


def main() -> None:
    judged = evaluation.load_judgments()
    known = set()
    for doc in evaluation.read_corpus(judged.corpus):
        text = " ".join(str(doc.get(f) or "") for f in ("title", "abstract", "body"))
        known.update(w.lower() for w in re.findall(r"[A-Za-z]+", text))
    queries = []
    for topic in judged.topics:
        if not topic.answerable:
            continue
        words = [
            w for w in re.findall(r"[A-Za-z][A-Za-z-]*", topic.query)
            if w.isalpha() and len(w) >= 5
        ]
        if not words:
            continue
        word = max(words, key=len)
        for slips in (1, 2) if len(word) >= 8 else (1,):
            rng = random.Random(f"anna-misspelling:{topic.id}:{slips}")
            typo, kinds = misspell(word.lower(), slips, rng, known)
            queries.append(
                {
                    "topic": topic.id,
                    "query": re.sub(rf"\b{re.escape(word)}\b", typo, topic.query, count=1),
                    "intended": topic.query,
                    "slips": slips,
                    "edits": kinds,
                }
            )
    OUT.write_text(
        json.dumps(
            {
                "format": "anna-misspellings/v1",
                "judgments": "judgments.json",
                "method": (
                    "Each answerable topic's longest alphabetic word of 5+ "
                    "letters, misspelled by one typing slip (drop, double, "
                    "swap, neighbouring key; first letter kept), and words "
                    "of 8+ letters also by two; real words excluded. See "
                    "misspell.py."
                ),
                "queries": queries,
            },
            indent=1,
        )
        + "\n"
    )
    print(f"wrote {len(queries)} misspelled queries to {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
