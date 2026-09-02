"""Subject export, backup, restore, and irreversible privacy deletion."""

import shutil
import sqlite3
from pathlib import Path
from typing import Any, Dict

from . import database


EXPORT_DATASETS = (
    "source_epoch",
    "source_record_version",
    "quality_issue",
    "normalized_fact",
    "context_event",
    "coverage_result",
    "trend_result",
    "association_result",
    "assessment_change_result",
    "derivation_receipt",
)


def _rows(conn: sqlite3.Connection, table: str, subject_id: str):
    return [dict(row) for row in conn.execute(f"SELECT * FROM {table} WHERE subject_id=?", (subject_id,))]


def export_subject(conn: sqlite3.Connection, subject_id: str) -> Dict[str, Any]:
    subject = conn.execute("SELECT * FROM subject WHERE subject_id=?", (subject_id,)).fetchone()
    if subject is None:
        raise KeyError("subject not found")
    return {
        "schema_version": database.schema_version(conn),
        "subject": dict(subject),
        "datasets": {name: _rows(conn, name, subject_id) for name in EXPORT_DATASETS},
    }


def purge_subject(conn: sqlite3.Connection, subject_id: str) -> Dict[str, int]:
    counts = {
        "subjects": conn.execute("SELECT COUNT(*) FROM subject WHERE subject_id=?", (subject_id,)).fetchone()[0],
        "raw_versions": conn.execute("SELECT COUNT(*) FROM source_record_version WHERE subject_id=?", (subject_id,)).fetchone()[0],
        "normalized_facts": conn.execute("SELECT COUNT(*) FROM normalized_fact WHERE subject_id=?", (subject_id,)).fetchone()[0],
        "trend_results": conn.execute("SELECT COUNT(*) FROM trend_result WHERE subject_id=?", (subject_id,)).fetchone()[0],
    }
    conn.execute("DELETE FROM subject WHERE subject_id=?", (subject_id,))
    conn.commit()
    return {key: int(value) for key, value in counts.items()}


def backup_database(source: Path, destination: Path) -> None:
    with database.connect(source) as source_conn, sqlite3.connect(str(destination)) as dest_conn:
        source_conn.backup(dest_conn)


def restore_database(backup: Path, destination: Path) -> None:
    if destination.exists():
        destination.unlink()
    shutil.copy2(backup, destination)
    with database.connect(destination) as conn:
        if database.schema_version(conn) < 1:
            raise ValueError("backup has no health-core schema")
