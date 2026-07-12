# Ingestion sources & the plugin architecture

Every knowledge source is a **plugin** that registers itself with a decorator. Adding a new
source is a single file dropped into `engine/ingest/sources/` — no changes to the pipeline,
the API, or the UI.

## The contract

A plugin subclasses `SourcePlugin` (`engine/ingest/base.py`) and implements two methods:

```python
from engine.documents import DocumentKind
from engine.ingest.base import SourcePlugin, register

@register
class MySource(SourcePlugin):
    name = "mysource"                       # unique id and the document `source`
    display_name = "My Source"
    default_kind = DocumentKind.REPORT
    description = "One-line description shown in source listings."

    def fetch(self, *, query=None, limit=100, **kwargs):
        # Talk to the upstream API / crawl, yield raw source-native dicts.
        for record in call_upstream(query, limit):
            yield record

    def normalize(self, raw):
        # Map one raw record to a Document (or return None to skip it).
        return self.make_document(
            raw["id"],
            title=raw["title"],
            abstract=raw.get("summary", ""),
            url=raw["url"],
            authors=raw.get("authors", []),
            published=raw.get("date"),
        )
```

`@register` adds the class to the registry; importing `engine.ingest.sources` triggers
registration for all built-ins. The pipeline handles embedding, batching, and indexing — a
plugin never touches Elasticsearch or vectors.

`self.make_document(native_id, **fields)` stamps the source name and a stable, namespaced ID
(`Document.make_id`) so re-ingestion updates instead of duplicating.

## Documentation crawlers

Sources without an API (vendor/kernel docs, standards listings) subclass `DocsCrawler`
(`engine/ingest/crawler.py`), which discovers pages from `sitemap.xml` and a one-level crawl of
seed pages, then extracts a title + readable text with a **dependency-free** HTML→text
converter. Typically you only declare metadata:

```python
@register
class MyDocsSource(DocsCrawler):
    name = "mydocs"
    display_name = "My Docs"
    seeds = ["https://docs.example.com/index.html"]
    sitemaps = ["https://docs.example.com/sitemap.xml"]
    allowed_domains = ["docs.example.com"]
    path_includes = ["/guide/"]
    # Version-aware sources override detect_version(url).
```

## Built-in sources

| `name` | Source | Kind | Mechanism | Notes |
|---|---|---|---|---|
| `arxiv` | arXiv | paper | Atom API (stdlib XML) | `-q "cat:eess.SY"`, LaTeX auto-detected |
| `github` | GitHub | repository/code | REST API | `GITHUB_TOKEN` lifts limits; `--code` for files |
| `nasa` | NASA NTRS | report | JSON API | technical reports + PDF links |
| `doe` | DOE OSTI | report | JSON API | DOI, report numbers, full-text links |
| `ieee` | IEEE Open Access | paper | Xplore API | needs `IEEE_API_KEY` (free); skips otherwise |
| `nist` | NIST | standard | crawler | publications + CSRC (SP/FIPS) |
| `linux_kernel` | Linux kernel docs | documentation | crawler | version captured from URL |
| `stm32` | STM32 | documentation | crawler | ST community wiki; PDF seeds supported |
| `espressif` | ESP-IDF | documentation | crawler + sitemap | version-aware (`v5.1`, `latest`) |
| `arm` | ARM developer | documentation | crawler | architecture / TRM docs |
| `riscv` | RISC-V | standard | crawler | ratified specifications |

## Running ingestion

```bash
./run flask engine sources                       # list all registered sources
./run flask engine ingest arxiv -q "cat:cs.RO" -n 500
./run flask engine ingest github -q "topic:fpga stars:>200" -n 100 --code
./run flask engine ingest espressif -n 200
./run flask engine ingest doe -q "fusion energy" -n 300 --dry-run
```

Options: `-q/--query`, `-n/--limit`, `-b/--batch-size`, `--no-embed`, `--dry-run`,
`--code` (GitHub).

### Background ingestion

Enqueue on the existing Celery worker instead of blocking:

```python
from engine.tasks import ingest_source
ingest_source.delay("arxiv", query="cat:eess.SY", limit=1000)
```

## PDF indexing

`engine/ingest/pdf.py` extracts text from PDF URLs/bytes with `pypdf`. A crawler or normalizer
can call `extract_text_from_url(pdf_url)` and put the result in the document `body`. Extraction
failures (encrypted, scanned, missing dep) degrade to empty text — the document is still indexed
on its metadata.

## Design principles for new sources

1. **Be defensive.** Upstream payloads change; read fields with `.get()` and skip malformed
   records by returning `None` from `normalize`.
2. **Never require network at import time.** Do all I/O inside `fetch`.
3. **Respect rate limits.** Use `engine.ingest.http` (configured user-agent + retry/backoff);
   add `time.sleep` where the provider asks for it (see `arxiv`).
4. **Set `default_kind`** so faceting and rendering are correct.
