# Codebase Quality Tasks

This document captures four focused maintenance tasks identified while reviewing the codebase.

## 1) Typo fix task
- **Issue:** The `allthethings/up` package used a misspelled `__jnit__.py` module name.
- **Task:** Replace it with a proper `__init__.py` so package conventions and tooling expectations are met.
- **Impact:** Better package discoverability and fewer surprises for contributors and IDE/static tooling.

## 2) Bug fix task
- **Issue:** `/up/databases` executed raw SQL through `db.engine.execute("SELECT 1")`, which is brittle with SQLAlchemy 1.4+/2.x transitions and bypasses session-level behavior.
- **Task:** Execute the health-check query through `db.session.execute(text("SELECT 1"))`.
- **Impact:** More future-proof DB probing and cleaner SQLAlchemy usage.

## 3) Documentation/comment discrepancy task
- **Issue:** `test/conftest.py` said "Postgres" in the savepoint note, but this project is configured around MariaDB.
- **Task:** Update the comment to reference MariaDB.
- **Impact:** Reduces contributor confusion and aligns comments with actual infrastructure.

## 4) Test improvement task
- **Issue:** The `/up/databases` test depended on live external services and only asserted status code.
- **Task:** Improve the test by monkeypatching Redis/DB probes and asserting both probes are called exactly once.
- **Impact:** Makes the test deterministic and verifies intended behavior beyond HTTP status.
