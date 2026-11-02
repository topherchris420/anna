"""Hybrid retrieval on PostgreSQL: Full-Text Search + pgvector kNN, fused.

Mirrors :class:`engine.search.SearchService` (same inputs, same
``SearchResults`` output) but runs entirely on Postgres:

- **Lexical**  — the query's terms (:mod:`engine.lexical`), each normalized by
  Postgres' own English configuration, matched against the ``search_vector``
  ``tsvector`` column with the same coverage rule Elasticsearch applies
  (``minimum_should_match``) and ranked by IDF-weighted ``ts_rank`` per term,
  times the phrase bonus.
- **Semantic** — cosine kNN via the pgvector ``<=>`` operator on ``embedding``.
- **Fusion**   — the same Reciprocal Rank Fusion the Elasticsearch backend uses.

Filters, facets, paging, highlights, and ``related`` are all supported. Queries
run on a single short-lived connection; a dropped connection surfaces as
:class:`~engine.search.SearchBackendError` (HTTP 503).
"""

from __future__ import annotations

import datetime
import itertools
import math
import re
import time
from collections import Counter
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

from engine import spelling
from engine.config import EngineConfig, get_config
from engine.embeddings import Embedder, get_embedder
from engine.lexical import QueryTerm, bm25_idf, parse_query, required_matches
from engine.retrieval import (
    explain_rankings,
    retrieval_report,
    vector_fusion_skipped,
)
from engine.search import (
    SearchBackendError,
    SearchFilters,
    SearchHit,
    SearchResults,
    fused_score_ceiling,
    reciprocal_rank_fusion,
)
from engine.pg.store import (
    _SELECT_COLUMNS,
    get_store,
    row_to_document,
    vector_literal,
)

# --------------------------------------------------------------------------- #
# The lexical plan: a query's terms as Postgres matches them
# --------------------------------------------------------------------------- #
# Each typed term is normalized by ``phraseto_tsquery`` — Postgres' English
# stemmer and stop words, the very ones that built ``search_vector`` — so
# "estimate" matches "estimation" and a term the index splits ("RISC-V" →
# 'risc-v' <-> 'risc' <-> 'v') still has to appear in one piece. The same
# round trip counts how many documents contain each term, for its IDF.
_ANALYZE_SQL = (
    "SELECT q::text, numnode(q), "
    "CASE WHEN numnode(q) = 0 THEN 0 "
    "ELSE (SELECT count(*) FROM {table} WHERE search_vector @@ q) END, "
    "(SELECT count(*) FROM {table}) "
    "FROM unnest(%(lx_texts)s::text[]) WITH ORDINALITY AS u(txt, ord) "
    "CROSS JOIN LATERAL phraseto_tsquery('english', u.txt) AS q "
    "ORDER BY u.ord"
)


# Largest "r of n terms" rule written out as an exact tsquery (the OR of
# every r-term AND) for the GIN index: 64 covers every query of up to eight
# terms under the default rule, and a tsquery of at most a few kilobytes.
_MAX_COVER_CLAUSES = 64


@dataclass(frozen=True)
class PlannedTerm:
    """A query term as Postgres will match it.

    ``tsquery`` is Postgres' normalized form ('kalman', 'estim', 'circular'
    <-> 'buffer'); ``document_frequency`` is how many documents contain it
    and ``weight`` the BM25 IDF that follows from that.
    """

    term: QueryTerm
    tsquery: str
    document_frequency: int = 0
    weight: float = 0.0


@dataclass(frozen=True)
class LexicalPlan:
    """What the lexical retriever will match for one query.

    ``included`` terms are counted against ``required``; ``excluded`` terms
    must be absent. ``dropped`` are typed terms the index cannot match at
    all (they normalize to nothing), kept so a report can say so.
    """

    included: Tuple[PlannedTerm, ...] = ()
    excluded: Tuple[PlannedTerm, ...] = ()
    required: int = 0
    corpus_size: int = 0
    dropped: Tuple[QueryTerm, ...] = ()

    @property
    def matchable(self) -> bool:
        return bool(self.included)

    def any_tsquery(self) -> str:
        return " | ".join(f"( {t.tsquery} )" for t in self.included)

    def all_tsquery(self) -> str:
        return " & ".join(f"( {t.tsquery} )" for t in self.included)

    def excluded_tsquery(self) -> str:
        return " | ".join(f"( {t.tsquery} )" for t in self.excluded)

    def report(self) -> Dict[str, Any]:
        """The coverage rule as the retrieval report states it."""
        return {
            "terms": [t.term.label for t in self.included],
            "excluded": [t.term.label for t in self.excluded],
            "required": self.required,
            "weights": {
                t.term.label: round(t.weight, 4) for t in self.included
            },
        }


def build_plan(
    terms: Sequence[QueryTerm],
    rows: Sequence[Sequence[Any]],
    minimum_should_match: str,
) -> LexicalPlan:
    """Assemble a plan from the typed terms and ``_ANALYZE_SQL``'s rows.

    ``rows`` holds one ``(tsquery_text, node_count, document_frequency,
    corpus_size)`` per term, in order. Terms that normalize to the same
    tsquery ("filter" and "filters") count once.
    """
    corpus = int(rows[0][3]) if rows else 0
    included: List[PlannedTerm] = []
    excluded: List[PlannedTerm] = []
    dropped: List[QueryTerm] = []
    seen = set()
    for term, (tsquery, nodes, frequency, _) in zip(terms, rows):
        if not nodes:
            dropped.append(term)
            continue
        if (tsquery, term.negated) in seen:
            continue
        seen.add((tsquery, term.negated))
        if term.negated:
            excluded.append(PlannedTerm(term, tsquery))
        else:
            included.append(
                PlannedTerm(
                    term,
                    tsquery,
                    int(frequency),
                    bm25_idf(int(frequency), corpus),
                )
            )
    return LexicalPlan(
        included=tuple(included),
        excluded=tuple(excluded),
        required=required_matches(minimum_should_match, len(included)),
        corpus_size=corpus,
        dropped=tuple(dropped),
    )


def term_weights(
    words: Sequence[str], config: Optional[EngineConfig] = None
) -> Dict[str, float]:
    """BM25 IDF of each word over the table, in one round trip.

    The same analysis a search plan runs: each word as Postgres' English
    configuration normalizes it, its document frequency, the table size.
    Words that normalize to nothing (stop words to Postgres) are omitted.
    """
    config = config or get_config()
    with get_store(config).connect() as conn, conn.cursor() as cur:
        cur.execute(
            _ANALYZE_SQL.format(table=config.index_name),
            {"lx_texts": [_phrase_text(word) for word in words]},
        )
        rows = cur.fetchall()
    return {
        word: bm25_idf(int(frequency), int(corpus))
        for word, (_, nodes, frequency, corpus) in zip(words, rows)
        if nodes
    }


def lexical_match_sql(plan: LexicalPlan) -> Tuple[str, Dict[str, Any]]:
    """The ``WHERE`` condition for a lexical match, and its parameters.

    The rule is handed to the GIN index as one exact tsquery whenever it can
    be: every term (AND), any term (OR), or "at least r of n" written out as
    the OR of every r-term AND — ``(a & b) | (a & c) | (b & c)`` for two of
    three. Measured on 50,000 documents, that exact form ranked 5,760 rows
    where an any-term prefilter fetched and then discarded 25,920, and ran
    3× faster. Past :data:`_MAX_COVER_CLAUSES` combinations the index finds
    documents carrying any term and the count each one carries is checked
    against ``required``. Every term travels as a bound parameter.
    """
    if not plan.matchable:
        return "FALSE", {}
    params: Dict[str, Any] = {}
    n, required = len(plan.included), plan.required
    if required >= n:
        sql = "search_vector @@ %(lx_all)s::tsquery"
        params["lx_all"] = plan.all_tsquery()
    elif required <= 1:
        sql = "search_vector @@ %(lx_any)s::tsquery"
        params["lx_any"] = plan.any_tsquery()
    elif math.comb(n, required) <= _MAX_COVER_CLAUSES:
        sql = "search_vector @@ %(lx_cover)s::tsquery"
        params["lx_cover"] = " | ".join(
            "( " + " & ".join(f"( {plan.included[i].tsquery} )" for i in combo) + " )"
            for combo in itertools.combinations(range(n), required)
        )
    else:
        carried = " + ".join(
            f"(search_vector @@ %(lx_t{i})s::tsquery)::int"
            for i in range(len(plan.included))
        )
        sql = (
            f"search_vector @@ %(lx_any)s::tsquery "
            f"AND ({carried}) >= %(lx_required)s"
        )
        params["lx_any"] = plan.any_tsquery()
        params["lx_required"] = plan.required
        params.update(_term_params(plan))
    if plan.excluded:
        sql += " AND NOT search_vector @@ %(lx_not)s::tsquery"
        params["lx_not"] = plan.excluded_tsquery()
    return sql, params


def lexical_rank_sql(
    plan: LexicalPlan, phrase_boost: float
) -> Tuple[str, Dict[str, Any]]:
    """The lexical ranking expression, and its parameters.

    Each term contributes its IDF times ``ts_rank`` for that term alone —
    which saturates with repetition and weighs title over abstract over body
    by the A/B/C labels ``search_vector`` carries — so the score has BM25's
    shape: rare terms count for much, common ones for little, and a fifth
    mention adds less than the first. ``ts_rank_cd`` over the whole query,
    the previous ranking, knew nothing of rarity: a paper saying "state" and
    "estimation" could outrank the one paper that says "Kalman filter".

    When the query has several terms and appears in a document as a phrase,
    the score is multiplied by ``phrase_boost`` — the same bonus the
    Elasticsearch phrase clause gives. It re-ranks matches, never filters.
    """
    if not plan.matchable:
        return "0", {}
    params = _term_params(plan)
    parts = []
    for i, term in enumerate(plan.included):
        params[f"lx_w{i}"] = term.weight
        parts.append(f"%(lx_w{i})s * ts_rank(search_vector, %(lx_t{i})s::tsquery)")
    rank = "(" + " + ".join(parts) + ")"
    if phrase_boost != 1.0 and len(plan.included) > 1:
        rank = (
            f"{rank} * (CASE WHEN search_vector @@ "
            "phraseto_tsquery('english', %(lx_phrase)s) "
            "THEN %(phrase_boost)s ELSE 1 END)"
        )
        params["lx_phrase"] = " ".join(t.term.text for t in plan.included)
        params["phrase_boost"] = phrase_boost
    return rank, params


_INNER_HYPHEN = re.compile(r"(?<=[^\W_])-(?=[^\W_])")


def _phrase_text(text: str) -> str:
    """A term's text as ``phraseto_tsquery`` should read it.

    Postgres indexes a hyphenated word whole *and* in parts ("processing-
    in-memory" → 'processing-in-memori', 'process', 'memori'), but a query
    for "in-memory" would ask for the whole compound 'in-memori', which that
    document does not contain. Matching the parts as a phrase finds every
    spelling — "in-memory", "in memory", "compute-in-memory" — which is also
    how Elasticsearch's analyzer reads a hyphenated term.
    """
    return _INNER_HYPHEN.sub(" ", text)


def _term_params(plan: LexicalPlan) -> Dict[str, Any]:
    return {f"lx_t{i}": t.tsquery for i, t in enumerate(plan.included)}


def _with_terms(
    explanation: Dict[str, Any],
    plan: Optional[LexicalPlan],
    carried: Optional[Sequence[bool]],
) -> Dict[str, Any]:
    """A hit's explanation plus the query terms it does and does not carry.

    A lexical match no longer has to contain every term, so "why is this
    here?" has to say which ones it does contain.
    """
    if plan is None or not plan.matchable or carried is None:
        return explanation
    labels = [t.term.label for t in plan.included]
    return {
        **explanation,
        "matched_terms": [label for label, f in zip(labels, carried) if f],
        "missing_terms": [label for label, f in zip(labels, carried) if not f],
    }


_FACET_TERM_FIELDS = ("source", "kind", "language")

# --------------------------------------------------------------------------- #
# Highlighting (``ts_headline``)
# --------------------------------------------------------------------------- #
# Fragments carry the same contract as the Elasticsearch highlighter: raw
# document text with ``<em>``/``</em>`` around matched terms — consumers are
# responsible for escaping everything else. ``ts_headline`` is told to mark
# matches with control characters rather than a literal "<em>" a document
# could plainly contain; a document that somehow carries these bytes can at
# worst yield a stray (inert) ``<em>`` marker, never an arbitrary tag. The
# sentinels are swapped for ``<em>`` tags in :func:`headline_fragments`.
_HL_START = "\x02"
_HL_STOP = "\x03"
_HL_DELIM = "\x01"
_HL_OPTIONS = (
    f'StartSel="{_HL_START}", StopSel="{_HL_STOP}", '
    f'FragmentDelimiter="{_HL_DELIM}", MaxFragments=2, MaxWords=25, MinWords=8'
)
# ``ts_headline`` walks the whole input; cap it so a huge crawled body cannot
# stall the page-window fetch (matches beyond this depth are rare anyway).
_HL_MAX_CHARS = 50_000

# --------------------------------------------------------------------------- #
# "More like this" without a semantic model
# --------------------------------------------------------------------------- #
# A document is related to another by the words that make it distinctive:
# frequent in it, rare in the corpus. Elasticsearch's more_like_this picks
# terms the same way; this is the Postgres twin. Measured on the judged
# queries (test/relevance), documents related to an on-topic paper this way
# were on the same topic 0.429 of the time, against 0.280 for the shared-
# title-word ranking it replaces — and none came back empty.
_RELATED_TERMS = 12
# Title words say more than abstract words, which say more than body words.
_FIELD_WEIGHT = {"A": 3.0, "B": 2.0, "C": 1.0, "D": 1.0}
# Every lexeme of one document, with its field labels, occurrence count, the
# number of documents carrying it and the table size: one round trip, about
# 0.2 s for a full abstract at 50,000 documents. Lexemes with a backslash
# (which the English parser does not produce) are left out rather than quoted.
_DISTINCTIVE_SQL = (
    "SELECT u.lexeme, u.weights, cardinality(u.positions), "
    "(SELECT count(*) FROM {table} t "
    "WHERE t.search_vector @@ quote_literal(u.lexeme)::tsquery), "
    "(SELECT count(*) FROM {table}) "
    "FROM {table} d, unnest(d.search_vector) AS u "
    "WHERE d.id = %(id)s AND strpos(u.lexeme, chr(92)) = 0"
)


def distinctive_terms(
    rows: Sequence[Sequence[Any]], count: int = _RELATED_TERMS
) -> LexicalPlan:
    """The ``count`` words that most distinguish a document, as a plan.

    ``rows`` holds ``(lexeme, field_labels, occurrences, document_frequency,
    corpus_size)`` per lexeme (``_DISTINCTIVE_SQL``). Each scores its field-
    weighted frequency in the document times its BM25 IDF; a word no other
    document carries cannot relate this one to anything and is skipped. Any
    one chosen word admits a candidate (``required`` 1), and candidates rank
    by the chosen words they carry, rare ones most — the search ranking.
    """
    scored = []
    corpus = 0
    for lexeme, labels, occurrences, frequency, corpus in rows:
        if int(frequency) < 2:
            continue
        weight = sum(_FIELD_WEIGHT.get(label, 1.0) for label in labels or ())
        idf = bm25_idf(int(frequency), int(corpus))
        scored.append(
            ((weight or float(occurrences or 1)) * idf, lexeme, frequency, idf)
        )
    scored.sort(key=lambda s: (-s[0], s[1]))
    chosen = tuple(
        PlannedTerm(
            QueryTerm(lexeme),
            "'" + lexeme.replace("'", "''") + "'",
            int(frequency),
            idf,
        )
        for _, lexeme, frequency, idf in scored[:count]
    )
    return LexicalPlan(
        included=chosen,
        required=1 if chosen else 0,
        corpus_size=int(corpus),
    )


_MARKED = re.compile(r"<em>(.*?)</em>", re.S)
_LEXEME = re.compile(r"'((?:[^']|'')*)'")


def rank_fragments(fragments: List[str], plan: Optional["LexicalPlan"]) -> List[str]:
    """Order highlight fragments by the weight of the terms they mark.

    ``ts_headline`` returns fragments in document order, and a page shows
    the first: for "kalman filter state estimation" that was a sentence
    about "the existing state of warehouse environment" while the one
    saying "Kalman Filter" came second. Each fragment scores the IDF weights
    of the query terms it marks — a marked word belongs to a term when it
    begins with one of the term's stemmed lexemes ("estimations" → 'estim')
    — and the most informative fragment comes first; ties keep their order.
    """
    if not plan or not plan.matchable or len(fragments) < 2:
        return fragments
    terms = [
        ([lx.replace("''", "'") for lx in _LEXEME.findall(t.tsquery)], t.weight)
        for t in plan.included
    ]

    def score(fragment: str) -> float:
        marked = [w.lower() for w in _MARKED.findall(fragment)]
        return sum(
            weight
            for lexemes, weight in terms
            if any(w.startswith(lx) for w in marked for lx in lexemes if lx)
        )

    return sorted(fragments, key=score, reverse=True)


def headline_fragments(raw: Optional[str]) -> List[str]:
    """Convert one ``ts_headline`` result into ES-style highlight fragments.

    Splits on the fragment delimiter and keeps only fragments that contain an
    actual match — ``ts_headline`` returns a matchless text prefix when the
    query terms are absent (e.g. a purely semantic kNN hit), and the ES
    backend returns no highlights for those either.
    """
    if not raw:
        return []
    fragments: List[str] = []
    for frag in raw.split(_HL_DELIM):
        frag = frag.strip()
        if _HL_START not in frag:
            continue
        fragments.append(
            frag.replace(_HL_START, "<em>").replace(_HL_STOP, "</em>")
        )
    return fragments


# --------------------------------------------------------------------------- #
# Spelling (engine.spelling): the indexed word a typed one slipped from
# --------------------------------------------------------------------------- #
# Each candidate word as the index normalizes it and the number of documents
# carrying it, the three most common first: one round trip for a few hundred
# candidates, 20–70 ms at 50,000 documents. Only a typed word that no
# document contains, and that is not English, ever gets here.
_SPELLING_SQL = (
    "SELECT lex FROM (SELECT lex, "
    "(SELECT count(*) FROM {table} WHERE search_vector @@ lex::tsquery) AS df "
    "FROM (SELECT DISTINCT plainto_tsquery('english', w)::text AS lex "
    "FROM unnest(%(sp_words)s::text[]) AS w) AS c WHERE lex <> '') AS s "
    "WHERE df > 0 ORDER BY df DESC, lex LIMIT 3"
)
# How an indexed stem is spelled: in the documents that carry it.
_SPELLED_SQL = (
    "SELECT concat_ws(' ', title, abstract, left(body, 2000)) FROM {table} "
    "WHERE search_vector @@ %(sp_lex)s::tsquery LIMIT %(sp_n)s"
)
# The words of the documents a query found: what the query is about.
_CONTEXT_SQL = (
    "SELECT concat_ws(' ', title, abstract) FROM {table} "
    "WHERE id = ANY(%(sp_ids)s)"
)


class PgSearchService:
    def __init__(
        self,
        config: Optional[EngineConfig] = None,
        embedder: Optional[Embedder] = None,
    ) -> None:
        self.config = config or get_config()
        self.store = get_store(self.config)
        self.embedder = embedder or get_embedder()
        self.table = self.config.index_name

    # ------------------------------------------------------------------ #
    def search(
        self,
        query: str,
        *,
        filters: Optional[SearchFilters] = None,
        mode: str = "hybrid",
        page: int = 1,
        per_page: int = 20,
        include_facets: bool = True,
        spell: bool = True,
    ) -> SearchResults:
        """Search; with ``spell``, a typed word no document contains is
        corrected (:mod:`engine.spelling`) and the results are the corrected
        query's, saying so in ``spelling``."""
        query = (query or "").strip()
        filters = filters or SearchFilters()
        page = max(1, page)
        per_page = max(1, min(per_page, 100))
        where_sql, params = self._where(filters)

        want = page * per_page
        cand = max(self.config.bm25_candidates, want)

        # pgvector is optional. When it is unavailable, only lexical retrieval
        # is possible, so ``semantic`` mode degrades to full-text search.
        vector_ok = self.store.has_vector()
        # Hybrid fuses lexical matching with *semantic* vectors; without a
        # semantic model there is nothing semantic to fuse (see
        # vector_fusion_skipped). Asked for by name, term vectors still run.
        skipped = vector_fusion_skipped(mode, query, vector_ok, self.embedder)
        run_knn = (
            mode in ("hybrid", "semantic")
            and bool(query)
            and vector_ok
            and not skipped
        )
        run_fts = bool(query) and (
            mode in ("hybrid", "bm25") or (mode == "semantic" and not vector_ok)
        )
        run_browse = not query
        # Paths the request asked for that cannot run. A missing pgvector
        # column is known up front; an encoder failure is discovered below.
        unavailable: List[str] = (
            ["knn"]
            if query and mode in ("hybrid", "semantic") and not vector_ok
            else []
        )

        started = time.time()
        try:
            from psycopg2.extras import RealDictCursor

            with self.store.connect() as conn:
                rankings: List[List[str]] = []
                named_rankings: Dict[str, List[str]] = {}
                # One plan serves the lexical retriever, the hashing gate,
                # the highlighter and each hit's matched-terms explanation.
                plan = self._plan(conn, query) if query else None
                if run_fts:
                    named_rankings["fts"] = self._fts_ids(
                        conn, plan, where_sql, params, cand
                    )
                if run_knn:
                    # Same contract as the Elasticsearch service: a model
                    # failure must not discard a healthy lexical result. It
                    # is reported as an unavailable path instead.
                    encode_error: Optional[Exception] = None
                    try:
                        qvec = vector_literal(self.embedder.encode(query))
                    except Exception as exc:  # noqa: BLE001 - reported
                        qvec, encode_error = None, exc
                        unavailable.append("knn")
                    if qvec is not None:
                        named_rankings["knn"] = self._knn_ids(
                            conn,
                            qvec,
                            where_sql,
                            params,
                            max(self.config.knn_candidates, want),
                            # Hashing vectors are not semantic: let them
                            # re-rank documents the lexical rule accepts,
                            # never put any other one on screen — a shared
                            # "the" or "state" is not a reason to show it.
                            gate=None if self.embedder.using_model else plan,
                        )
                    elif not named_rankings and not run_browse:
                        # Every requested retriever failed: surface it as a
                        # backend error (HTTP 503) rather than an empty page.
                        raise SearchBackendError(
                            f"query embedding failed: {encode_error}"
                        ) from encode_error
                if run_browse:
                    named_rankings["browse"] = self._browse_ids(
                        conn, where_sql, params, want
                    )
                rankings = list(named_rankings.values())
                explanations = explain_rankings(
                    named_rankings, self.config.rrf_k
                )

                if len(rankings) > 1:
                    fused = reciprocal_rank_fusion(
                        rankings, k=self.config.rrf_k
                    )
                elif rankings:
                    fused = [
                        (doc_id, 1.0 / (i + 1))
                        for i, doc_id in enumerate(rankings[0])
                    ]
                else:
                    fused = []

                start = (page - 1) * per_page
                window = fused[start : start + per_page]
                docs, highlights, carried = self._fetch(
                    conn,
                    [doc_id for doc_id, _ in window],
                    RealDictCursor,
                    plan=plan,
                )
                hits = [
                    SearchHit(
                        document=docs[doc_id],
                        score=round(score, 6),
                        highlights=highlights.get(doc_id, []),
                        explanation=_with_terms(
                            explanations.get(doc_id, {}),
                            plan,
                            carried.get(doc_id),
                        ),
                    )
                    for doc_id, score in window
                    if doc_id in docs
                ]

                facets: Dict[str, List[Dict[str, Any]]] = {}
                total = len(fused)
                if include_facets:
                    try:
                        facets, agg_total = self._facets(
                            conn,
                            where_sql,
                            params,
                            # Count what the page draws from: the fused
                            # candidates, not only the strict lexical
                            # matches (which undercounted hybrid results).
                            ids=None
                            if run_browse
                            else [doc_id for doc_id, _ in fused],
                        )
                        if run_browse:
                            total = agg_total
                    except Exception:
                        facets = {}
                correction = (
                    self._correction(
                        conn,
                        query,
                        plan,
                        [
                            doc_id
                            for doc_id, _ in fused[: spelling.CONTEXT_DOCUMENTS]
                        ],
                    )
                    if spell and run_fts and plan is not None
                    else None
                )
        except Exception as exc:  # connection drop / SQL error
            raise SearchBackendError(f"Postgres search failed: {exc}") from exc

        if correction is not None:
            corrected = self.search(
                correction.query,
                filters=filters,
                mode=mode,
                page=page,
                per_page=per_page,
                include_facets=include_facets,
                spell=False,
            )
            corrected.spelling = correction.report()
            corrected.took_ms = int((time.time() - started) * 1000)
            return corrected

        ceiling = fused_score_ceiling(len(rankings), self.config.rrf_k)
        return SearchResults(
            query=query,
            mode=mode,
            total=total,
            hits=hits,
            facets=facets,
            took_ms=int((time.time() - started) * 1000),
            page=page,
            per_page=per_page,
            score_ceiling=ceiling,
            retrieval=retrieval_report(
                mode,
                named_rankings,
                backend="postgres",
                candidate_count=len(fused),
                unavailable=unavailable,
                embedding=(
                    "sentence-transformer"
                    if self.embedder.using_model
                    else "hashing"
                )
                if "knn" in named_rankings or skipped
                else None,
                browse=run_browse and include_facets and bool(facets),
                score_ceiling=ceiling,
                lexical=plan.report() if plan is not None else None,
                skipped=skipped,
            ),
        )

    # ------------------------------------------------------------------ #
    # Query analysis
    # ------------------------------------------------------------------ #
    def _correction(
        self, conn, query: str, plan: LexicalPlan, context_ids: List[str]
    ) -> Optional[spelling.Correction]:
        """The corrected query when a typed word matches no document.

        Best effort, in a savepoint: a failure here costs the correction,
        never the search it would have improved.
        """
        terms = [(t.term, t.document_frequency) for t in plan.included]
        if all(frequency for _, frequency in terms):
            return None
        table = self.config.index_name
        try:
            with conn.cursor() as cur:
                cur.execute("SAVEPOINT spelling")
                try:
                    context: Counter = Counter()
                    if context_ids:
                        cur.execute(
                            _CONTEXT_SQL.format(table=table),
                            {"sp_ids": list(context_ids)},
                        )
                        context = spelling.vocabulary(
                            row[0] for row in cur.fetchall()
                        )

                    def lookup(word: str) -> Optional[str]:
                        cur.execute(
                            _SPELLING_SQL.format(table=table),
                            {"sp_words": sorted(spelling.one_slip(word))},
                        )
                        for (lexeme,) in cur.fetchall():
                            cur.execute(
                                _SPELLED_SQL.format(table=table),
                                {
                                    "sp_lex": lexeme,
                                    "sp_n": spelling.CONTEXT_DOCUMENTS,
                                },
                            )
                            found = spelling.nearest(
                                word, (row[0] for row in cur.fetchall())
                            )
                            if found:
                                return found
                        return None

                    correction = spelling.correct(query, terms, context, lookup)
                except Exception:
                    cur.execute("ROLLBACK TO SAVEPOINT spelling")
                    raise
                cur.execute("RELEASE SAVEPOINT spelling")
                return correction
        except Exception:  # noqa: BLE001 - an enrichment, never an outage
            return None

    def _plan(self, conn, query: str) -> LexicalPlan:
        """Analyze ``query`` against this table in one round trip."""
        terms = parse_query(query)
        if not terms:
            return LexicalPlan()
        with conn.cursor() as cur:
            cur.execute(
                _ANALYZE_SQL.format(table=self.table),
                {"lx_texts": [_phrase_text(t.text) for t in terms]},
            )
            rows = cur.fetchall()
        return build_plan(
            terms, rows, self.config.lexical_minimum_should_match
        )

    # ------------------------------------------------------------------ #
    # Filters -> SQL
    # ------------------------------------------------------------------ #
    def _where(self, filters: SearchFilters) -> Tuple[str, Dict[str, Any]]:
        conds: List[str] = []
        params: Dict[str, Any] = {}
        if filters.sources:
            conds.append("source = ANY(%(f_sources)s::text[])")
            params["f_sources"] = filters.sources
        if filters.kinds:
            conds.append("kind = ANY(%(f_kinds)s::text[])")
            params["f_kinds"] = filters.kinds
        if filters.categories:
            conds.append("categories && %(f_cats)s::text[]")
            params["f_cats"] = filters.categories
        if filters.language:
            conds.append("language = ANY(%(f_langs)s::text[])")
            params["f_langs"] = filters.language
        if filters.version:
            conds.append("version = %(f_version)s")
            params["f_version"] = filters.version
        if filters.has_code is not None:
            conds.append("has_code = %(f_has_code)s")
            params["f_has_code"] = filters.has_code
        if filters.has_equations is not None:
            conds.append("has_equations = %(f_has_eq)s")
            params["f_has_eq"] = filters.has_equations
        if filters.year_from is not None:
            conds.append("published_date >= %(f_yfrom)s")
            params["f_yfrom"] = datetime.date(filters.year_from, 1, 1)
        if filters.year_to is not None:
            conds.append("published_date <= %(f_yto)s")
            params["f_yto"] = datetime.date(filters.year_to, 12, 31)
        return " AND ".join(conds), params

    @staticmethod
    def _and(where_sql: str) -> str:
        return f" AND {where_sql}" if where_sql else ""

    # ------------------------------------------------------------------ #
    # Retrieval primitives
    # ------------------------------------------------------------------ #
    def _fts_ids(
        self, conn, plan: Optional[LexicalPlan], where_sql, params, limit
    ) -> List[str]:
        """Lexical matches for ``plan``, best first (ties by id, so pages
        never reshuffle between requests)."""
        if plan is None or not plan.matchable:
            return []
        match_sql, match_params = lexical_match_sql(plan)
        rank_sql, rank_params = lexical_rank_sql(
            plan, self.config.lexical_phrase_boost
        )
        sql = (
            f"SELECT id FROM {self.table} "
            f"WHERE {match_sql}{self._and(where_sql)} "
            f"ORDER BY {rank_sql} DESC, id LIMIT %(limit)s"
        )
        with conn.cursor() as cur:
            cur.execute(
                sql, {**params, **match_params, **rank_params, "limit": limit}
            )
            return [r[0] for r in cur.fetchall()]

    def _knn_ids(
        self,
        conn,
        qvec,
        where_sql,
        params,
        limit,
        gate: Optional[LexicalPlan] = None,
    ) -> List[str]:
        """Nearest neighbours by cosine distance.

        ``gate`` restricts candidates to the documents its lexical rule
        accepts. It is set when the vectors come from the hashing fallback,
        whose neighbours are otherwise dominated by bucket collisions:
        without a gate every query, even gibberish, returned the whole corpus
        as "results", and with only a shared-word gate a four-term question
        filled the page with documents that said "state".
        """
        gate_sql, gate_params = "", {}
        if gate is not None:
            if not gate.matchable:
                return []
            match_sql, gate_params = lexical_match_sql(gate)
            gate_sql = f" AND {match_sql}"
        sql = (
            f"SELECT id FROM {self.table} "
            f"WHERE embedding IS NOT NULL{self._and(where_sql)}{gate_sql} "
            f"ORDER BY embedding <=> %(qvec)s::vector LIMIT %(limit)s"
        )
        bound = {**params, **gate_params, "qvec": qvec, "limit": limit}
        with conn.cursor() as cur:
            cur.execute(sql, bound)
            return [r[0] for r in cur.fetchall()]

    def _browse_ids(self, conn, where_sql, params, limit) -> List[str]:
        where = f" WHERE {where_sql}" if where_sql else ""
        sql = (
            f"SELECT id FROM {self.table}{where} "
            f"ORDER BY published_date DESC NULLS LAST LIMIT %(limit)s"
        )
        with conn.cursor() as cur:
            cur.execute(sql, {**params, "limit": limit})
            return [r[0] for r in cur.fetchall()]

    def _fetch(
        self,
        conn,
        ids: List[str],
        dict_cursor,
        plan: Optional[LexicalPlan] = None,
    ) -> Tuple[Dict[str, Any], Dict[str, List[str]], Dict[str, List[bool]]]:
        """Fetch the page-window documents, plus what they matched.

        Returns ``(documents_by_id, highlight_fragments_by_id,
        carried_terms_by_id)``. Both extras are computed here — for the final
        page window only, never for every candidate: highlights via
        ``ts_headline`` over the abstract and body (any matched term is
        marked, so a document carrying three of four terms still shows why
        it is here), and for each document one flag per planned term saying
        whether it contains that term.
        """
        if not ids:
            return {}, {}, {}
        cols = ", ".join(_SELECT_COLUMNS)
        params: Dict[str, Any] = {"ids": ids}
        extra_cols = ""
        if plan is not None and plan.matchable:
            flags = ", ".join(
                f"search_vector @@ %(lx_t{i})s::tsquery"
                for i in range(len(plan.included))
            )
            extra_cols = (
                ", ts_headline('english', "
                "left(coalesce(abstract, '') || ' ' || coalesce(body, ''), "
                "%(hl_chars)s), "
                "%(hl_q)s::tsquery, %(hl_opts)s) AS hl"
                f", ARRAY[{flags}] AS lx_carried"
            )
            params.update(
                _term_params(plan),
                hl_q=plan.any_tsquery(),
                hl_opts=_HL_OPTIONS,
                hl_chars=_HL_MAX_CHARS,
            )
        with conn.cursor(cursor_factory=dict_cursor) as cur:
            cur.execute(
                f"SELECT {cols}{extra_cols} FROM {self.table} "
                f"WHERE id = ANY(%(ids)s::text[])",
                params,
            )
            docs: Dict[str, Any] = {}
            highlights: Dict[str, List[str]] = {}
            carried: Dict[str, List[bool]] = {}
            for row in cur.fetchall():
                frags = rank_fragments(
                    headline_fragments(row.pop("hl", None)), plan
                )
                flags_row = row.pop("lx_carried", None)
                doc = row_to_document(row)
                docs[doc.id] = doc
                if frags:
                    highlights[doc.id] = frags
                if flags_row is not None:
                    carried[doc.id] = [bool(f) for f in flags_row]
            return docs, highlights, carried

    # ------------------------------------------------------------------ #
    def _facets(
        self,
        conn,
        where_sql: str,
        params: Dict[str, Any],
        ids: Optional[List[str]] = None,
    ) -> Tuple[Dict[str, List[Dict[str, Any]]], int]:
        """Facet buckets and the population count.

        With ``ids`` the population is exactly those documents (the fused
        candidates of a search, already filtered); without, it is every
        document matching the filters — the corpus being browsed.
        """
        base_conds: List[str] = []
        p = dict(params)
        if ids is not None:
            base_conds.append("id = ANY(%(facet_ids)s::text[])")
            p = {"facet_ids": list(ids)}
        elif where_sql:
            base_conds.append(where_sql)
        base = (" WHERE " + " AND ".join(base_conds)) if base_conds else ""

        facets: Dict[str, List[Dict[str, Any]]] = {}
        with conn.cursor() as cur:
            cur.execute(f"SELECT count(*) FROM {self.table}{base}", p)
            total = int(cur.fetchone()[0])

            for field in _FACET_TERM_FIELDS:
                cur.execute(
                    f"SELECT {field} AS v, count(*) AS c FROM {self.table}{base} "
                    f"{'AND' if base else 'WHERE'} {field} IS NOT NULL "
                    f"GROUP BY {field} ORDER BY c DESC LIMIT 20",
                    p,
                )
                facets[field] = [
                    {"value": v, "count": c} for v, c in cur.fetchall()
                ]

            cur.execute(
                f"SELECT v, count(*) AS c FROM {self.table}, "
                f"unnest(categories) AS v{base} GROUP BY v ORDER BY c DESC LIMIT 20",
                p,
            )
            facets["categories"] = [
                {"value": v, "count": c} for v, c in cur.fetchall()
            ]

            for field in ("has_code", "has_equations"):
                cur.execute(
                    f"SELECT {field} AS v, count(*) AS c FROM {self.table}{base} "
                    f"GROUP BY {field}",
                    p,
                )
                facets[field] = [
                    {"value": v, "count": c} for v, c in cur.fetchall()
                ]
        return facets, total

    # ------------------------------------------------------------------ #
    def related(self, doc_id: str, size: int = 8) -> List[SearchHit]:
        from psycopg2.extras import RealDictCursor

        cols = ", ".join(_SELECT_COLUMNS)
        # Vector neighbours are only "related" when the vectors carry meaning;
        # hashing-fallback neighbours are bucket collisions (see Embedder.
        # semantic), so those deployments use the lexical path below.
        has_vec = self.store.has_vector() and self.embedder.semantic
        base_sel = "embedding" if has_vec else "id"
        try:
            with self.store.connect() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute(
                        f"SELECT {base_sel} FROM {self.table} WHERE id = %s",
                        (doc_id,),
                    )
                    base = cur.fetchone()
                    if not base:
                        return []
                    if has_vec and base["embedding"] is not None:
                        cur.execute(
                            f"SELECT {cols} FROM {self.table} "
                            f"WHERE id != %s AND embedding IS NOT NULL "
                            f"ORDER BY embedding <=> %s::vector LIMIT %s",
                            (doc_id, base["embedding"], size),
                        )
                        rows = cur.fetchall()
                    else:
                        rows = self._more_like_this(conn, cur, doc_id, size)
            return [
                SearchHit(document=row_to_document(r), score=0.0) for r in rows
            ]
        except Exception as exc:  # connection drop / SQL error
            # An outage must read as an outage (HTTP 503), not as "this
            # document has no related work".
            raise SearchBackendError(f"Postgres related failed: {exc}") from exc

    def _more_like_this(self, conn, dict_cursor, doc_id: str, size: int):
        """Documents sharing this one's most distinctive words, best first.

        See :func:`distinctive_terms`: the words are chosen by frequency in
        the document times rarity in the corpus, and candidates rank by the
        IDF-weighted search ranking over the words they carry.
        """
        with conn.cursor() as plain:
            plain.execute(
                _DISTINCTIVE_SQL.format(table=self.table), {"id": doc_id}
            )
            plan = distinctive_terms(plain.fetchall())
        if not plan.matchable:
            return []
        rank_sql, rank_params = lexical_rank_sql(plan, 1.0)
        cols = ", ".join(_SELECT_COLUMNS)
        dict_cursor.execute(
            f"SELECT {cols} FROM {self.table} "
            f"WHERE id != %(id)s AND search_vector @@ %(lx_any)s::tsquery "
            f"ORDER BY {rank_sql} DESC, id LIMIT %(size)s",
            {
                **rank_params,
                "id": doc_id,
                "lx_any": plan.any_tsquery(),
                "size": size,
            },
        )
        return dict_cursor.fetchall()
