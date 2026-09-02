"""SQLite bootstrap and migration ownership for the public core."""

from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path
from typing import Union


MIGRATIONS = Path(__file__).with_name("migrations")


def connect(path: Union[Path, str]) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _apply_migrations(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migration ("
        "version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL)"
    )
    applied = {
        row[0] for row in conn.execute("SELECT version FROM schema_migration")
    }
    for path in sorted(MIGRATIONS.glob("*.sql")):
        version_text, name = path.stem.split("_", 1)
        version = int(version_text)
        if version in applied:
            continue
        conn.executescript(path.read_text(encoding="utf-8"))
        conn.execute(
            "INSERT INTO schema_migration(version,name,applied_at) "
            "VALUES (?,?,strftime('%Y-%m-%dT%H:%M:%fZ','now'))",
            (version, name),
        )
        conn.commit()


def initialize(path: Union[Path, str]) -> str:
    db_path = Path(path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with connect(db_path) as conn:
        _apply_migrations(conn)
        existing = conn.execute(
            "SELECT subject_id FROM subject ORDER BY created_at LIMIT 1"
        ).fetchone()
        if existing:
            return str(existing[0])
        subject_id = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO subject(subject_id,created_at) "
            "VALUES (?,strftime('%Y-%m-%dT%H:%M:%fZ','now'))",
            (subject_id,),
        )
        conn.commit()
        return subject_id


def schema_version(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT COALESCE(MAX(version),0) FROM schema_migration").fetchone()
    return int(row[0])
