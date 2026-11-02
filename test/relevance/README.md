# Relevance judgments

The measuring stick for Anna's ranking: 47 judged queries over a frozen,
312-document corpus, and `flask engine evaluate` to run them against any
backend. Every ranking default in the engine — how many of a query's terms a
document must carry, English stemming on Elasticsearch, leaving term-hashing
vectors out of hybrid search — was chosen by these numbers, and CI's
relevance lane holds both real backends to them
([`test/engine/test_relevance_live.py`](../engine/test_relevance_live.py)).

| File | Contents |
|---|---|
| `corpus.jsonl.gz` | The corpus: one `Document` per line (no embeddings — they are computed on load, by the configured embedder). |
| `judgments.json` | The topics (`anna-relevance-judgments/v1`): query, query type, the written information need, how the pool was built, and graded documents with a note for each relevant one. |
| `misspellings.json` | The answerable topics' queries misspelled by one typing slip and by two (`anna-misspellings/v1`), each scored against its topic's judgments. |
| `misspell.py` | Writes `misspellings.json` — deterministic, and checked by a test that reruns it. |

## Run it

```bash
# Load the corpus into a scratch index and score hybrid and lexical retrieval
./run flask engine evaluate --load-corpus --index anna_eval --mode hybrid --mode bm25

# The same against an index you already loaded, as a gate (exit 1 below it)
./run flask engine evaluate --index anna_eval --min-ndcg 0.72 --max-noise 1

# Full per-topic report as JSON
./run flask engine evaluate --index anna_eval --json > report.json

# Also score related-document recommendations
./run flask engine evaluate --index anna_eval --related

# Also score the misspelled queries, with spelling correction off and on
./run flask engine evaluate --index anna_eval --misspellings
```

`--load-corpus` recreates the index it loads and refuses the configured one,
so it cannot replace a deployment's real index. Any backend works:
`ENGINE_BACKEND=postgres` or `elasticsearch`, as configured.

The report scores each answerable topic by **nDCG@10** (graded gain
`2^grade − 1`), **MRR** (first relevant result), **P@5** and **Recall@10**,
and each unanswerable topic by **noise@10** — how many results the engine
showed for a question the corpus cannot answer (zero is honest). Documents a
ranking surfaces that were never judged are counted, as **unjudged**, and
scored as not relevant; a change that surfaces many of them needs its pool
re-judged before its score means much.

## Results

Hybrid is the default mode. The deployment measured is the free tier's:
term-hashing vectors, no sentence-embedding model.

| Backend · mode | nDCG@10 | MRR | P@5 | R@10 | noise@10 | topics with no relevant result |
|---|---|---|---|---|---|---|
| Postgres · hybrid, before | 0.667 | 0.928 | 0.500 | 0.576 | 3.33 | 1 |
| Postgres · full-text, before | 0.429 | 0.750 | 0.318 | 0.279 | 0.00 | 11 |
| **Postgres · hybrid, now** | **0.741** | **0.966** | **0.618** | **0.605** | **0.67** | 1 |
| Elasticsearch · hybrid, before | 0.685 | 0.915 | 0.514 | 0.587 | 3.33 | 1 |
| Elasticsearch · BM25, before | 0.599 | 0.841 | 0.477 | 0.461 | 0.67 | 7 |
| **Elasticsearch · hybrid, now** | **0.757** | **0.977** | **0.623** | **0.622** | **0.67** | 1 |

Before: Postgres required every query term (so "how do I estimate state
with a kalman filter" matched nothing lexically) and the hybrid page was
filled from term-hashing vectors gated on any one shared word, which put an
eye-tracking paper first and the Kalman-filter paper outside the top six.
Elasticsearch had no stemming and counted stop words as terms.

What decided each default (all on this set, after the second judging round,
so every configuration was scored with no unjudged documents):

| Choice | Measured |
|---|---|
| Coverage `70%` (`ENGINE_LEXICAL_MIN_SHOULD_MATCH`) | Postgres lexical nDCG 0.741, vs 0.713 for `2<70%` (every term of a one- or two-term query required) and 0.631 for `2<-1`. `50%` and `1` (any term) reach 0.749 and 0.742 but return ten junk results for "RISC-V vector extension" (noise 3.33 vs 0.67). |
| Terms counted without stop words, IDF-weighted | The five plain-English questions: nDCG 0.417 → 0.641 in Postgres hybrid (0.015 → 0.641 lexically) and 0.370 → 0.705 in Elasticsearch hybrid. |
| English analysis on Elasticsearch prose | BM25 nDCG 0.716 → 0.757 at the same coverage. |
| Hybrid without term-hashing vectors | Fusing them, even gated to the lexical matches, lowered nDCG (0.741 → 0.726 Postgres, 0.757 → 0.738 Elasticsearch) and P@5 (0.618 → 0.573). They stay available as the explicit vector mode, which also improved (0.588 → 0.655) once gated. |

### Related documents

`--related` scores **Details ▸ related work** with the same judgments: for each
of the 133 documents judged highly relevant to a topic, the share of its top 5
related documents judged relevant to that topic. A proxy — a paper can be
related in ways a topic does not cover — but the same proxy for every ranking.

| Related documents (no semantic model) | on topic | papers with nothing related |
|---|---|---|
| Postgres: shared title terms → distinctive terms | 0.280 → **0.427** | 6 → **0** |
| Elasticsearch: default `more_like_this` → distinctive terms | 0.283 → **0.432** | 14 → **0** |

Both now pick a document's 12 most distinctive terms — frequency in it times
rarity in the index, title over abstract over body, carried by at least one
other document — and admit any candidate carrying one, ranked by the search
ranking. Five terms (0.337) missed vocabulary and 40 (0.392) diluted it; the
title alone reached 0.329. Elasticsearch's defaults required 30% of 25 terms
and ignored terms in fewer than five documents — in a small corpus, exactly
the rare words that relate two papers.

### Misspelled queries

Coverage matching hides a typo: a query with one word misspelled still
returns a full page, matched on its other words. `--misspellings` measures
how much that costs and how much spelling correction
([`engine/spelling.py`](../../engine/spelling.py)) gives back.

**The set.** In each answerable topic's query, the longest alphabetic word of
five or more letters — usually the one that matters — misspelled by a
typing slip drawn by a generator seeded with the topic id: a letter
dropped, a letter doubled, two neighbours swapped, or a neighbouring key
(QWERTY); the first letter kept. Words of eight or more letters get a second
variant with two slips. A variant that is a real word, in the corpus or in
English, is drawn again: a real-word error is a different problem, which
correction does not attempt. 75 queries ("kalmna", "satbility of power
systems", "reinforcemsnt learning for control" …), each keeping its topic's
judgments.

| Hybrid search, nDCG@10 | uncorrected | corrected | as intended | corrected · restored exactly |
|---|---|---|---|---|
| Postgres, one slip (40) | 0.545 | **0.739** | 0.739 | 37 · 37 |
| Postgres, two slips (35) | 0.535 | **0.708** | 0.742 | 29 · 28 |
| Elasticsearch, one slip (40) | 0.552 | **0.749** | 0.749 | 37 · 37 |
| Elasticsearch, two slips (35) | 0.546 | **0.719** | 0.754 | 29 · 29 |

*Restored* means the corrected ranking is the intended query's, top 10
exactly. The three one-slip queries left alone had nothing to restore:
"storag" stems like "storage" and already ranked as intended, and the
intended words of "autiscaling" and "selep" are in no document of this
corpus. The one two-slip correction that did not restore its query read
"processoe" as "process" rather than "processor": both are one slip from it,
and the more common word wins.

**What must not change.** None of the 47 judged queries is corrected as
written, and their scores are those of the tables above. The rule that keeps
it so — a word in an English word list is never corrected — was measured
too: of 121 everyday words absent from this corpus ("lunch", "pocket",
"cooking" …), each searched alone and with a judged query, correction
without the list rewrote 36 into a technical neighbour one key away
("launch", "packet", "cooling"); with it, none.

## How the judgments were made

**Corpus.** 288 arXiv metadata records — title, authors, abstract,
categories — as `flask engine seed-corpus` fetched them (categories eess.SY,
cs.RO, cs.AR, cs.OS, cs.DC and eess.SP, with their cross-listings), the two
offline sample documents `flask engine demo` still loads alongside, and the
22 entries of the bundled Shadow Libraries directory. Frozen on 2026-11-02.
arXiv metadata is distributed under CC0.

**Topics.** 47 queries written before any ranking change was measured: 34
keyword queries, 5 plain-English questions, 3 acronyms, and 5 topics the
corpus barely or never answers ("RISC-V vector extension", "sourdough bread
recipe", a nonsense string …). Each has a written **need** that defines what
earns grade 2 (highly relevant: the document's main subject) and grade 1
(partially relevant: a substantive secondary treatment), and the queries that
share a need (a question and its keyword form) are graded identically.

**Pools.** Each topic's documents to judge were the union of independent
candidate generators, so no single ranking — least of all the one being
measured — decided what could count as relevant: the engine's hybrid and
full-text top 10 before any change, a broad any-term full-text top 15, and a
hand-written concept pattern over titles and abstracts (stored as
`pool_pattern`). A second round judged every document any measured
configuration — both backends, eleven coverage rules and fusion variants, two
analyzers — placed in a top 10 without a judgment: 160 more documents, of
which 5 were partially relevant.

**Assessors.** The grades were assigned by AI assessors — four independent
Claude agents, one per group of topics, each round-two document going back to
the assessor who judged its topic — not by people. Each worked from the title
and abstract alone, saw the documents in id order with nothing to say which
system had retrieved them, and applied the need's grade definitions
literally; a note records the reason for every relevant grade. That makes the
set consistent and auditable, not infallible: read the notes, and if a grade
is wrong, fix it in `judgments.json` and say so in the commit.

In all: 1,253 judgments — 133 grade 2, 174 grade 1, 946 grade 0.

## Extending it

- **A new topic:** add it to `judgments.json` with a need, judge a pool built
  the same way (several generators, documents unlabelled), and keep the notes.
- **A changed ranking that surfaces unjudged documents:** the report counts
  them per topic (`unj`); judge those documents against the existing need
  before trusting a score difference.
- **Your own corpus:** the format is not specific to this one. Write a
  judgment file whose `corpus` names your own JSON Lines file and run
  `flask engine evaluate path/to/judgments.json --load-corpus --index …`.
