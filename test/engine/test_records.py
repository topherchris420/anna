"""Research records: canonical JSON parity with the browser, fingerprints,
and excerpt verification against the current index.

The parity tests execute the real ``frontend/evidence.js`` under Node when it
is available (CI's smoke lane has it on Linux and Windows) and compare bytes;
everything else is pure Python.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from engine import records
from engine.documents import Document

REPO = Path(__file__).resolve().parents[2]
EVIDENCE_JS = REPO / "frontend" / "evidence.js"

try:
    from flask import Flask
except ImportError:  # pragma: no cover - exercised by the skip itself
    Flask = None

requires_flask = pytest.mark.skipif(Flask is None, reason="flask not installed")
requires_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not installed"
)


class TestJsNumber:
    @pytest.mark.parametrize(
        "value, expected",
        [
            (1.0, "1"),
            (100.0, "100"),
            (-0.0, "0"),
            (0.1, "0.1"),
            (-1.5, "-1.5"),
            (0.5, "0.5"),
            (123456789.123, "123456789.123"),
            (1e20, "100000000000000000000"),
            (1e21, "1e+21"),
            (1.5e22, "1.5e+22"),
            (0.000001, "0.000001"),
            (1e-7, "1e-7"),
            (5e-324, "5e-324"),
            (1.7976931348623157e308, "1.7976931348623157e+308"),
            (2 / 61, "0.03278688524590164"),
            (1 / 3, "0.3333333333333333"),
            (float("nan"), "null"),
            (float("inf"), "null"),
            (float("-inf"), "null"),
        ],
    )
    def test_matches_ecmascript_number_to_string(self, value, expected):
        # Python prints 1.0 and 1e-07; JavaScript prints 1 and 1e-7.
        assert records.js_number(value) == expected


class TestCanonical:
    def test_keys_sort_by_utf16_code_units_not_code_points(self):
        # U+FFFD < U+1F600 by code point, but the emoji's UTF-16 lead
        # surrogate (D83D) sorts before FFFD, which is what Array.sort does.
        assert records.canonical({"�": 2, "\U0001F600": 1}) == (
            '{"\U0001F600":1,"�":2}'
        )
        assert records.canonical({"b": 1, "a": 2, "B": 3}) == (
            '{"B":3,"a":2,"b":1}'
        )

    def test_strings_escape_like_json_stringify(self):
        value = 'x\u001f"\\\n\r\t\b\f/é\U0001F600\ud800'
        assert records.canonical(value) == (
            '"x\\u001f\\"\\\\\\n\\r\\t\\b\\f/é\U0001F600\\ud800"'
        )

    def test_literals_integers_and_floats(self):
        assert records.canonical({"a": [1, 1.0, 0.5, True, False, None]}) == (
            '{"a":[1,1,0.5,true,false,null]}'
        )
        assert records.canonical([]) == "[]"
        assert records.canonical({}) == "{}"

    def test_integers_beyond_double_precision_round_like_javascript(self):
        assert records.canonical(2**53 - 1) == "9007199254740991"
        assert records.canonical(2**53 + 1) == "9007199254740992"

    def test_unsupported_values_are_rejected(self):
        with pytest.raises(TypeError):
            records.canonical({"a": {1, 2}})


class TestFingerprint:
    def test_independent_of_key_order_and_sensitive_to_content(self):
        a = records.fingerprint({"z": 1, "a": [1, 2]})
        b = records.fingerprint({"a": [1, 2], "z": 1})
        assert a == b
        assert len(a) == 64 and a == a.lower()
        assert records.fingerprint({"a": [1, 2], "z": 2}) != a


# Values whose formatting differs between Python and JavaScript, plus the
# string escapes and key orders the browser contract depends on.
PARITY_FIXTURE = {
    "schema": records.RECORD_SCHEMA,
    "numbers": [
        1.0,
        0.1,
        -0.0,
        1e21,
        1e-7,
        0.000001,
        123456789.123,
        5e-324,
        1.7976931348623157e308,
        1.5e22,
        100.0,
        2 / 61,
        1 / 3,
        1e20,
        -1.5,
        0,
        7,
        2**53 - 1,
        -42,
    ],
    "strings": ['x\u001f"\\\n\r\t\b\f/é\U0001F600', "\ud800 lone", "  ", ""],
    "nested": {
        "z": {"b": [None, True, False, {}], "a": []},
        "\U0001F600": 1,
        "�": 2,
        "A": 3,
        "a": 4,
    },
}

NODE_SCRIPT = """
const { webcrypto } = require("node:crypto");
const evidence = require(process.argv[1]);
let input = "";
process.stdin.on("data", (chunk) => { input += chunk; });
process.stdin.on("end", async () => {
  const value = JSON.parse(input);
  const out = {
    canonical: evidence.canonical(value),
    fingerprint: await evidence.fingerprint(value, webcrypto),
  };
  process.stdout.write(JSON.stringify(out));
});
"""


def _node_canonical(value):
    completed = subprocess.run(
        ["node", "-e", NODE_SCRIPT, str(EVIDENCE_JS)],
        input=json.dumps(value),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    return json.loads(completed.stdout)


@requires_node
class TestBrowserParity:
    def test_canonical_bytes_and_fingerprint_match_evidence_js(self):
        browser = _node_canonical(PARITY_FIXTURE)
        assert records.canonical(PARITY_FIXTURE) == browser["canonical"]
        assert records.fingerprint(PARITY_FIXTURE) == browser["fingerprint"]

    def test_excerpt_statuses_agree_with_evidence_js(self):
        # One record, six excerpts covering every status, checked by both
        # implementations against the same plain-object documents.
        doc = _doc()
        documents = {
            doc.id: {
                "id": doc.id,
                "abstract": doc.abstract,
                "body": doc.body,
            }
        }
        n = len(BODY_QUOTE)
        excerpts = [
            _abstract_excerpt(doc),
            {
                **_abstract_excerpt(doc),
                "field": "body",
                "offset_unit": "utf-16",
                "start": 12,
                "end": 12 + n,
                "quote": BODY_QUOTE,
            },
            {
                **_abstract_excerpt(doc),
                "field": "body",
                "start": 12,
                "end": 12 + n,
                "quote": BODY_QUOTE,
            },
            {**_abstract_excerpt(doc), "quote": "ring buffers", "end": 12},
            {**_abstract_excerpt(doc), "document_id": "arxiv:gone"},
            {**_abstract_excerpt(doc), "field": "title"},
            {**_abstract_excerpt(doc), "offset_unit": "bytes"},
        ]
        record = _record(doc, *excerpts)
        script = NODE_SCRIPT.replace(
            "canonical: evidence.canonical(value),",
            "reports: evidence.verifyExcerpts(value.record, (id) => "
            "value.documents[id] || null),",
        )
        completed = subprocess.run(
            ["node", "-e", script, str(EVIDENCE_JS)],
            input=json.dumps({"record": record, "documents": documents}),
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=True,
        )
        browser = json.loads(completed.stdout)["reports"]
        python = records.verify_excerpts(
            record, lambda doc_id: documents.get(doc_id)
        )
        assert [r["status"] for r in python] == [
            "verified",
            "verified",
            "relocated",
            "drifted",
            "missing-document",
            "missing-field",
            "invalid-excerpt",
        ]
        assert browser == json.loads(json.dumps(python))

    def test_a_browser_packet_verifies_offline(self):
        record = _record(_doc(), _abstract_excerpt(_doc()))
        browser = _node_canonical(record)
        packet = {
            "captured_at": "2026-10-04T00:00:00.000Z",
            "content_sha256": browser["fingerprint"],
            "record": record,
        }
        report = records.verify_record(packet)
        assert report["fingerprint"]["matches"] is True
        assert report["ok"] is True


# --------------------------------------------------------------------------- #
# Excerpt verification
# --------------------------------------------------------------------------- #
BODY = "\U0001F600 Intro.\n  DMA transfers samples into circular buffers."
BODY_QUOTE = "DMA transfers samples into circular buffers."


def _doc(**overrides):
    fields = dict(
        id="espressif:1020b2afe9f87462",
        source="espressif",
        kind="documentation",
        title="ESP32 DMA and Circular Buffers",
        abstract="The ESP32 DMA engine supports circular buffers.",
        body=BODY,
        url="https://docs.espressif.com/projects/esp-idf/en/v5.1/esp32/",
    )
    fields.update(overrides)
    return Document(**fields)


def _abstract_excerpt(doc):
    return {
        "document_id": doc.id,
        "field": "abstract",
        "offset_unit": "unicode-code-points",
        "start": 0,
        "end": len(doc.abstract),
        "quote": doc.abstract,
        "matched_terms": ["dma"],
    }


def _record(doc, *excerpts):
    return {
        "schema": records.RECORD_SCHEMA,
        "provider": "live",
        "request": {
            "q": "DMA circular buffers",
            "mode": "hybrid",
            "page": 1,
            "per_page": 20,
            "filters": {},
        },
        "retrieval": {"executed": ["bm25", "knn"], "score_ceiling": 2 / 61},
        "result_count": 1,
        "scope": "current-page",
        "hits": [
            {
                "score": 2 / 61,
                "relevance": 1.0,
                "explanation": None,
                "document": {
                    "id": doc.id,
                    "title": doc.title,
                    "source": doc.source,
                    "url": doc.url,
                    "pdf_url": "",
                    "authors": [],
                    "published": "",
                    "version": "",
                    "abstract": doc.abstract,
                },
                "highlights": [],
            }
        ],
        "summary": {
            "query": "DMA circular buffers",
            "answer": " ".join(e["quote"] + " [1]" for e in excerpts),
            "generator": "extractive",
            "grounding": "source-extract",
            "fallback_reason": None,
            "citations": [
                {
                    "n": 1,
                    "id": doc.id,
                    "title": doc.title,
                    "url": doc.url,
                    "source": doc.source,
                    "excerpts": list(excerpts),
                }
            ],
        },
    }


def _packet(record):
    return {
        "captured_at": "2026-10-04T00:00:00.000Z",
        "content_sha256": records.fingerprint(record),
        "record": record,
    }


class TestSliceAndLocate:
    def test_utf16_offsets_from_the_browser_resolve_in_python(self):
        # The emoji is one code point but two UTF-16 units, so the browser's
        # record puts the quote one unit further along than Python would.
        assert records.locate(BODY, BODY_QUOTE, "unicode-code-points") == 11
        assert records.locate(BODY, BODY_QUOTE, "utf-16") == 12
        n = len(BODY_QUOTE)
        assert records.slice_field(BODY, 11, 11 + n, "unicode-code-points") == (
            BODY_QUOTE
        )
        assert records.slice_field(BODY, 12, 12 + n, "utf-16") == BODY_QUOTE
        assert records.slice_field(BODY, 11, 11 + n, "utf-16") != BODY_QUOTE

    def test_missing_passage_has_no_location(self):
        assert records.locate(BODY, "ring buffers", "utf-16") is None


class TestVerifyExcerpt:
    def test_exact_passage_at_recorded_offsets_is_verified(self):
        doc = _doc()
        report = records.verify_excerpt(_abstract_excerpt(doc), doc, citation=1)
        assert report["status"] == records.STATUS_VERIFIED
        assert report["citation"] == 1
        assert report["field"] == "abstract"

    def test_browser_utf16_offsets_verify_against_a_python_document(self):
        doc = _doc()
        excerpt = {
            "document_id": doc.id,
            "field": "body",
            "offset_unit": "utf-16",
            "start": 12,
            "end": 12 + len(BODY_QUOTE),
            "quote": BODY_QUOTE,
        }
        assert (
            records.verify_excerpt(excerpt, doc)["status"]
            == records.STATUS_VERIFIED
        )
        # The same numbers read as code points are one off: the passage is
        # still there, so the excerpt is relocated rather than drifted.
        excerpt["offset_unit"] = "unicode-code-points"
        report = records.verify_excerpt(excerpt, doc)
        assert report["status"] == records.STATUS_RELOCATED
        assert report["found_at"] == 11

    def test_passage_that_disappeared_is_drifted(self):
        doc = _doc(abstract="The ESP32 DMA engine supports ring buffers.")
        excerpt = _abstract_excerpt(_doc())
        assert (
            records.verify_excerpt(excerpt, doc)["status"]
            == records.STATUS_DRIFTED
        )

    def test_missing_document_and_field(self):
        excerpt = _abstract_excerpt(_doc())
        assert (
            records.verify_excerpt(excerpt, None)["status"]
            == records.STATUS_MISSING_DOCUMENT
        )
        excerpt = dict(excerpt, field="body")
        # Plain mappings are accepted as documents; a field they lack is
        # reported as missing rather than treated as empty text.
        assert (
            records.verify_excerpt(excerpt, {"abstract": "x"})["status"]
            == records.STATUS_MISSING_FIELD
        )

    @pytest.mark.parametrize(
        "mutation, detail",
        [
            ({"field": "embedding"}, "field"),
            ({"start": True}, "start"),
            ({"end": 0}, "offsets"),
            ({"start": -1}, "offsets"),
            ({"offset_unit": "bytes"}, "offset_unit"),
            ({"quote": ""}, "quote"),
            ({"document_id": " "}, "document_id"),
        ],
    )
    def test_malformed_excerpts_are_invalid_not_crashes(self, mutation, detail):
        doc = _doc()
        excerpt = dict(_abstract_excerpt(doc), **mutation)
        report = records.verify_excerpt(excerpt, doc)
        assert report["status"] == records.STATUS_INVALID
        assert detail in report["detail"]
        assert records.verify_excerpt("not an object", doc)["status"] == (
            records.STATUS_INVALID
        )


class TestVerifyRecord:
    def test_packet_with_matching_fingerprint_and_live_excerpts_is_ok(self):
        doc = _doc()
        packet = _packet(_record(doc, _abstract_excerpt(doc)))
        lookups = []

        def get_document(doc_id):
            lookups.append(doc_id)
            return doc

        report = records.verify_record(
            packet,
            get_document,
            checked_against={"backend": "postgres", "index": "t"},
        )
        assert report["schema"] == records.RECORD_SCHEMA
        assert report["schema_known"] is True
        assert report["fingerprint"]["matches"] is True
        assert report["fingerprint"]["declared"] == (
            report["fingerprint"]["computed"]
        )
        assert report["checked_against"] == {
            "backend": "postgres",
            "index": "t",
        }
        assert [r["status"] for r in report["excerpts"]] == ["verified"]
        assert report["counts"]["verified"] == 1
        assert set(report["counts"]) == set(records.STATUSES)
        assert report["ok"] is True
        assert lookups == [doc.id]

    def test_each_document_is_fetched_once(self):
        doc = _doc()
        first = _abstract_excerpt(doc)
        second = dict(first, start=4, end=14, quote=doc.abstract[4:14])
        lookups = []

        def get_document(doc_id):
            lookups.append(doc_id)
            return doc

        report = records.verify_record(
            _record(doc, first, second), get_document
        )
        assert [r["status"] for r in report["excerpts"]] == [
            "verified",
            "verified",
        ]
        assert lookups == [doc.id]

    def test_tampered_record_fails_the_fingerprint(self):
        doc = _doc()
        packet = _packet(_record(doc, _abstract_excerpt(doc)))
        packet["record"]["summary"]["answer"] = "A claim that was never there"
        report = records.verify_record(packet, lambda doc_id: doc)
        assert report["fingerprint"]["matches"] is False
        assert report["ok"] is False

    def test_uppercase_declared_hash_is_accepted(self):
        doc = _doc()
        packet = _packet(_record(doc, _abstract_excerpt(doc)))
        packet["content_sha256"] = packet["content_sha256"].upper()
        assert records.verify_record(packet)["fingerprint"]["matches"] is True

    def test_relocated_or_missing_excerpts_are_not_ok(self):
        doc = _doc()
        excerpt = _abstract_excerpt(doc)
        shifted = _doc(abstract="Note. " + doc.abstract)
        report = records.verify_record(
            _packet(_record(doc, excerpt)), lambda doc_id: shifted
        )
        assert report["excerpts"][0]["status"] == records.STATUS_RELOCATED
        assert report["excerpts"][0]["found_at"] == 6
        assert report["ok"] is False
        report = records.verify_record(
            _packet(_record(doc, excerpt)), lambda doc_id: None
        )
        assert report["counts"]["missing-document"] == 1
        assert report["ok"] is False

    def test_bare_record_and_offline_mode(self):
        doc = _doc()
        report = records.verify_record(_record(doc, _abstract_excerpt(doc)))
        assert report["fingerprint"]["matches"] is None
        assert report["fingerprint"]["declared"] is None
        assert report["excerpts"] == []
        assert report["checked_against"] is None
        assert report["ok"] is True  # nothing contradicted, nothing checked

    def test_backend_errors_propagate(self):
        doc = _doc()

        def broken(doc_id):
            raise RuntimeError("connection refused")

        with pytest.raises(RuntimeError):
            records.verify_record(
                _packet(_record(doc, _abstract_excerpt(doc))), broken
            )

    @pytest.mark.parametrize(
        "payload",
        [
            None,
            [],
            "record",
            {"unrelated": True},
            {"record": {"schema": "x"}, "content_sha256": "nope"},
            {"record": {"schema": "x"}, "content_sha256": 42},
        ],
    )
    def test_malformed_payloads_are_rejected(self, payload):
        with pytest.raises(ValueError):
            records.unwrap_packet(payload)

    def test_records_without_a_summary_have_nothing_to_check(self):
        record = {"schema": records.RECORD_SCHEMA, "hits": [], "summary": None}
        assert records.count_excerpts(record) == 0
        assert records.verify_record(record, lambda i: None)["ok"] is True


@requires_flask
class TestVerifyRecordCommand:
    def _runner(self):
        from allthethings.engine_cli.views import engine_cli

        app = Flask(__name__)
        app.register_blueprint(engine_cli)
        return app.test_cli_runner()

    def _write(self, tmp_path, payload):
        path = tmp_path / "anna-research.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return str(path)

    def test_offline_check_reports_the_fingerprint(self, tmp_path):
        doc = _doc()
        path = self._write(
            tmp_path, _packet(_record(doc, _abstract_excerpt(doc)))
        )
        result = self._runner().invoke(
            args=["engine", "verify-record", path, "--offline"]
        )
        assert result.exit_code == 0, result.output
        assert "fingerprint:" in result.output and "MATCH" in result.output
        assert "VERIFY OK" in result.output

    def test_tampered_record_exits_nonzero(self, tmp_path):
        doc = _doc()
        packet = _packet(_record(doc, _abstract_excerpt(doc)))
        packet["record"]["request"]["q"] = "something else"
        path = self._write(tmp_path, packet)
        result = self._runner().invoke(
            args=["engine", "verify-record", path, "--offline"]
        )
        assert result.exit_code == 1
        assert "MISMATCH" in result.output

    def test_live_check_reads_documents_from_the_index(
        self, tmp_path, monkeypatch
    ):
        import engine.backend

        doc = _doc()
        monkeypatch.setattr(engine.backend, "get_document", lambda doc_id: doc)
        path = self._write(
            tmp_path, _packet(_record(doc, _abstract_excerpt(doc)))
        )
        result = self._runner().invoke(
            args=["engine", "verify-record", path, "--json"]
        )
        assert result.exit_code == 0, result.output
        report = json.loads(result.output)
        assert report["ok"] is True
        assert report["excerpts"][0]["status"] == "verified"
        assert report["checked_against"]["index"]

    def test_missing_documents_fail_the_live_check(self, tmp_path, monkeypatch):
        import engine.backend

        doc = _doc()
        monkeypatch.setattr(engine.backend, "get_document", lambda doc_id: None)
        path = self._write(
            tmp_path, _packet(_record(doc, _abstract_excerpt(doc)))
        )
        result = self._runner().invoke(args=["engine", "verify-record", path])
        assert result.exit_code == 1
        assert "missing-document" in result.output

    def test_unreachable_index_is_reported_not_a_traceback(
        self, tmp_path, monkeypatch
    ):
        import engine.backend

        def broken(doc_id):
            raise RuntimeError("connection refused")

        doc = _doc()
        monkeypatch.setattr(engine.backend, "get_document", broken)
        path = self._write(
            tmp_path, _packet(_record(doc, _abstract_excerpt(doc)))
        )
        result = self._runner().invoke(args=["engine", "verify-record", path])
        assert result.exit_code == 2
        assert "connection refused" in result.output
