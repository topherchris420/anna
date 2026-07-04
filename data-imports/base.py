"""
Base importer module for Anna's Archive data imports.

Provides a common base class for all data importers with:
- Progress tracking
- Error handling and retry logic
- Logging
- Database connection management
- Incremental import support
"""

import os
import sys
import time
import logging
import hashlib
from abc import ABC, abstractmethod
from typing import Optional, Iterator, Any, Dict, List
from dataclasses import dataclass, field
from pathlib import Path
from tqdm import tqdm

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session
from pymysql.constants import CLIENT

from config import settings


# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('imports.log')
    ]
)
logger = logging.getLogger(__name__)


@dataclass
class ImportProgress:
    """Track import progress for reporting and resume."""
    source: str
    total_records: int = 0
    processed: int = 0
    successful: int = 0
    failed: int = 0
    started_at: Optional[float] = None
    last_checkpoint: int = 0
    errors: List[Dict[str, Any]] = field(default_factory=list)

    def start(self):
        """Mark import as started."""
        self.started_at = time.time()
        logger.info(f"Starting import: {self.source}")

    def update(self, count: int = 1):
        """Update progress counter."""
        self.processed += count

    def success(self, count: int = 1):
        """Record successful imports."""
        self.successful += count
        self.update(count)

    def fail(self, count: int = 1):
        """Record failed imports."""
        self.failed += count
        self.update(count)

    def checkpoint(self, current_position: int):
        """Save checkpoint for resume capability."""
        self.last_checkpoint = current_position
        self._save_checkpoint()

    def _save_checkpoint(self):
        """Save checkpoint to file for recovery."""
        checkpoint_file = Path(f".checkpoint_{self.source}.json")
        import json
        data = {
            'source': self.source,
            'last_checkpoint': self.last_checkpoint,
            'processed': self.processed,
            'successful': self.successful,
            'failed': self.failed,
            'started_at': self.started_at
        }
        checkpoint_file.write_text(json.dumps(data))
        logger.debug(f"Checkpoint saved: {self.last_checkpoint}")

    def load_checkpoint(self) -> Optional[int]:
        """Load checkpoint if exists."""
        checkpoint_file = Path(f".checkpoint_{self.source}.json")
        if checkpoint_file.exists():
            import json
            data = json.loads(checkpoint_file.read_text())
            self.last_checkpoint = data.get('last_checkpoint', 0)
            self.processed = data.get('processed', 0)
            self.successful = data.get('successful', 0)
            self.failed = data.get('failed', 0)
            logger.info(f"Resuming from checkpoint: {self.last_checkpoint}")
            return self.last_checkpoint
        return None

    def clear_checkpoint(self):
        """Clear checkpoint after successful completion."""
        checkpoint_file = Path(f".checkpoint_{self.source}.json")
        if checkpoint_file.exists():
            checkpoint_file.unlink()

    def get_rate(self) -> float:
        """Calculate import rate (records/second)."""
        if self.started_at and self.processed > 0:
            elapsed = time.time() - self.started_at
            return self.processed / elapsed if elapsed > 0 else 0
        return 0

    def eta(self) -> float:
        """Estimate time to completion."""
        rate = self.get_rate()
        remaining = self.total_records - self.processed
        return remaining / rate if rate > 0 else 0

    def summary(self) -> str:
        """Get progress summary."""
        elapsed = time.time() - self.started_at if self.started_at else 0
        return (
            f"{self.source}: {self.processed}/{self.total_records} "
            f"({self.successful} ok, {self.failed} failed) "
            f"@ {self.get_rate():.0f}/s, ETA: {self.eta():0f}s"
        )


class BaseImporter(ABC):
    """
    Abstract base class for data importers.

    Implementers must define:
    - source_name: str - identifier for this data source
    - table_name: str - target database table
    - _import_file() - method to import a single file
    - _validate_record() - method to validate a record before insert
    - _transform_record() - method to transform record to schema
    """

    source_name: str = "base"
    table_name: str = "base"
    batch_size: int = 1000
    max_retries: int = 3
    retry_delay: float = 1.0

    def __init__(self, resume: bool = False, dry_run: bool = False):
        """
        Initialize importer.

        Args:
            resume: Whether to resume from checkpoint
            dry_run: If True, don't actually write to database
        """
        self.resume = resume
        self.dry_run = dry_run
        self.progress = ImportProgress(source=self.source_name)
        self._setup_database()

    def _setup_database(self):
        """Setup database connection."""
        self.engine = create_engine(
            settings.SQLALCHEMY_DATABASE_URI,
            connect_args={"client_flag": CLIENT.MULTI_STATEMENTS}
        )
        self.Session = sessionmaker(bind=self.engine)

    @property
    @abstractmethod
    def required_files(self) -> List[str]:
        """List of required files for this importer."""
        pass

    @abstractmethod
    def _transform_record(self, raw_record: Dict) -> Optional[Dict]:
        """
        Transform raw record to database schema.

        Args:
            raw_record: Raw record from import source

        Returns:
            Transformed record or None if invalid
        """
        pass

    def _validate_record(self, record: Dict) -> bool:
        """
        Validate record before insert.

        Args:
            record: Transformed record

        Returns:
            True if valid, False otherwise
        """
        return bool(record)

    def _import_file(self, filepath: Path, **kwargs) -> Iterator[Dict]:
        """
        Import a single file.

        Override in subclass for custom file parsing.

        Args:
            filepath: Path to file to import

        Yields:
            Raw records from file
        """
        # Default implementation for JSONL files
        import json
        with open(filepath, 'r', encoding='utf-8') as f:
            for line_num, line in enumerate(f, 1):
                if line.strip():
                    try:
                        yield json.loads(line)
                    except json.JSONDecodeError as e:
                        logger.warning(f"JSON decode error at line {line_num}: {e}")
                        self.progress.errors.append({
                            'file': str(filepath),
                            'line': line_num,
                            'error': str(e)
                        })

    def import_files(self, file_paths: List[Path]) -> ImportProgress:
        """
        Import multiple files.

        Args:
            file_paths: List of file paths to import

        Returns:
            ImportProgress with final statistics
        """
        # Load checkpoint if resuming
        if self.resume:
            self.progress.load_checkpoint()

        self.progress.start()

        try:
            for filepath in file_paths:
                if not filepath.exists():
                    logger.warning(f"File not found: {filepath}")
                    continue

                logger.info(f"Importing: {filepath}")
                self._process_file(filepath)

            self.progress.clear_checkpoint()
            logger.info(f"Import complete: {self.progress.summary()}")

        except Exception as e:
            logger.error(f"Import failed: {e}")
            self.progress.checkpoint(self.progress.processed)
            raise

        return self.progress

    def _process_file(self, filepath: Path):
        """Process a single file with batching."""
        records = list(self._import_file(filepath))
        self.progress.total_records = len(records)

        # Create progress bar
        with tqdm(total=len(records), desc=self.source_name) as pbar:
            batch = []
            for raw_record in records:
                # Transform record
                record = self._transform_record(raw_record)
                if record and self._validate_record(record):
                    batch.append(record)
                else:
                    self.progress.fail()

                # Process batch when full
                if len(batch) >= self.batch_size:
                    self._write_batch(batch)
                    batch = []

                pbar.update(1)
                pbar.set_postfix({'ok': self.progress.successful, 'fail': self.progress.failed})

            # Write remaining batch
            if batch:
                self._write_batch(batch)

    def _write_batch(self, records: List[Dict]):
        """
        Write batch of records to database.

        Args:
            records: List of transformed records
        """
        if self.dry_run:
            logger.info(f"DRY RUN: Would insert {len(records)} records")
            self.progress.success(len(records))
            return

        max_attempts = self.max_retries
        for attempt in range(max_attempts):
            try:
                with self.Session() as session:
                    self._insert_batch(session, records)
                    session.commit()
                    self.progress.success(len(records))
                    return
            except Exception as e:
                logger.warning(f"Batch insert failed (attempt {attempt + 1}/{max_attempts}): {e}")
                if attempt < max_attempts - 1:
                    time.sleep(self.retry_delay * (attempt + 1))
                else:
                    self.progress.fail(len(records))
                    self.progress.errors.append({'error': str(e), 'batch_size': len(records)})

    def _insert_batch(self, session: Session, records: List[Dict]):
        """
        Insert batch of records. Override for custom insert logic.

        Args:
            session: Database session
            records: Records to insert
        """
        # Default: bulk insert
        table = self.table_name
        if records:
            columns = list(records[0].keys())
            placeholders = [f":{col}" for col in columns]
            sql = f"""
                INSERT INTO {table} ({', '.join(columns)})
                VALUES ({', '.join(placeholders)})
            """
            session.execute(text(sql), records)

    def get_checksum(self, filepath: Path) -> str:
        """Calculate MD5 checksum of file."""
        hash_md5 = hashlib.md5()
        with open(filepath, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                hash_md5.update(chunk)
        return hash_md5.hexdigest()

    def verify_file(self, filepath: Path, expected_checksum: Optional[str] = None) -> bool:
        """
        Verify file integrity.

        Args:
            filepath: Path to file
            expected_checksum: Expected MD5 checksum

        Returns:
            True if valid
        """
        if not filepath.exists():
            logger.error(f"File not found: {filepath}")
            return False

        if expected_checksum:
            actual = self.get_checksum(filepath)
            if actual != expected_checksum:
                logger.error(f"Checksum mismatch: {actual} != {expected_checksum}")
                return False

        return True


class ImportRegistry:
    """Registry for available importers."""

    _importers: Dict[str, type] = {}

    @classmethod
    def register(cls, name: str):
        """Decorator to register an importer."""
        def decorator(importer_class):
            cls._importers[name] = importer_class
            return importer_class
        return decorator

    @classmethod
    def get(cls, name: str) -> Optional[type]:
        """Get importer class by name."""
        return cls._importers.get(name)

    @classmethod
    def list_importers(cls) -> List[str]:
        """List all available importers."""
        return list(cls._importers.keys())

    @classmethod
    def create(cls, name: str, **kwargs) -> Optional[BaseImporter]:
        """Create an importer instance."""
        importer_class = cls.get(name)
        if importer_class:
            return importer_class(**kwargs)
        return None