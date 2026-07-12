"""Unit tests for source normalizers (no network)."""

import xml.etree.ElementTree as ET

from engine.documents import DocumentKind
from engine.ingest.sources.arxiv import ArxivSource
from engine.ingest.sources.doe import DoeSource
from engine.ingest.sources.github import GithubSource


class TestArxivNormalize:
    ENTRY = """<entry xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
      <id>http://arxiv.org/abs/2401.12345v1</id>
      <title>Adaptive Control of Nonlinear Systems</title>
      <summary>We derive $u = -Kx$ feedback laws.</summary>
      <published>2024-01-01T00:00:00Z</published>
      <author><name>Jane Doe</name></author>
      <author><name>John Roe</name></author>
      <category term="eess.SY"/>
      <link title="pdf" href="http://arxiv.org/pdf/2401.12345v1"/>
    </entry>"""

    def test_normalize(self):
        doc = ArxivSource().normalize({"entry": ET.fromstring(self.ENTRY)})
        assert doc.title == "Adaptive Control of Nonlinear Systems"
        assert doc.authors == ["Jane Doe", "John Roe"]
        assert doc.categories == ["eess.SY"]
        assert doc.kind == DocumentKind.PAPER.value
        assert doc.has_equations is True
        assert doc.identifiers["arxiv_id"] == "2401.12345v1"
        assert doc.pdf_url.endswith("2401.12345v1")


class TestGithubNormalize:
    def test_repo(self):
        item = {
            "full_name": "torvalds/linux",
            "description": "Linux kernel source tree",
            "html_url": "https://github.com/torvalds/linux",
            "owner": {"login": "torvalds"},
            "language": "C",
            "topics": ["kernel", "linux"],
            "stargazers_count": 170000,
            "created_at": "2011-09-04T00:00:00Z",
        }
        doc = GithubSource().normalize({"item": item, "code": False})
        assert doc.kind == DocumentKind.REPOSITORY.value
        assert doc.has_code is True
        assert doc.popularity == 170000
        assert "kernel" in doc.tags
        assert doc.language == "C"

    def test_code_file(self):
        item = {
            "repository": {"full_name": "torvalds/linux"},
            "path": "kernel/sched/core.c",
            "html_url": "https://github.com/torvalds/linux/blob/master/kernel/sched/core.c",
        }
        doc = GithubSource().normalize({"item": item, "code": True})
        assert doc.kind == DocumentKind.CODE.value
        assert doc.has_code is True
        assert doc.identifiers["path"] == "kernel/sched/core.c"


class TestDoeNormalize:
    def test_normalize(self):
        rec = {
            "osti_id": "1888888",
            "title": "Grid-Scale Battery Storage Analysis",
            "description": "A techno-economic analysis of grid storage.",
            "authors": "Smith, A.; Jones, B.",
            "publication_date": "2022-06-15",
            "doi": "10.1000/xyz",
            "links": [{"rel": "fulltext", "href": "https://osti.gov/servlets/purl/1888888"}],
            "subjects": ["energy storage", "batteries"],
        }
        doc = DoeSource().normalize(rec)
        assert doc.kind == DocumentKind.REPORT.value
        assert doc.identifiers["doi"] == "10.1000/xyz"
        assert doc.identifiers["osti_id"] == "1888888"
        assert doc.authors == ["Smith, A.", "Jones, B."]
        assert doc.pdf_url.endswith("1888888")

    def test_skips_untitled(self):
        assert DoeSource().normalize({"osti_id": "1"}) is None
