"""SQLite bootstrap and migration ownership for the public core."""

from __future__ import annotations

import hashlib
import sqlite3
import uuid
from pathlib import Path
from typing import Union


MIGRATIONS = Path(__file__).with_name("migrations")


class _ClosingConnection(sqlite3.Connection):
    """A sqlite connection whose context manager also releases the handle."""

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


def connect(path: Union[Path, str]) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), factory=_ClosingConnection)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _apply_migrations(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migration ("
        "version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL)"
    )
    conn.commit()
    paths = sorted(MIGRATIONS.glob("*.sql"))
    parsed = []
    for path in paths:
        version_text, name = path.stem.split("_", 1)
        version = int(version_text)
        script = path.read_text(encoding="utf-8")
        parsed.append((version, name, script, hashlib.sha256(script.encode("utf-8")).hexdigest()))
    versions = [item[0] for item in parsed]
    if versions != list(range(1, len(versions) + 1)):
        raise RuntimeError("migration files are out of order")

    columns = {row[1] for row in conn.execute("PRAGMA table_info(schema_migration)")}
    has_sha = "sha256" in columns
    select = "SELECT version,name,sha256 FROM schema_migration ORDER BY version" if has_sha else (
        "SELECT version,name,NULL AS sha256 FROM schema_migration ORDER BY version"
    )
    applied_rows = list(conn.execute(select))
    applied_versions = [int(row[0]) for row in applied_rows]
    if applied_versions and applied_versions != list(range(1, max(applied_versions) + 1)):
        raise RuntimeError("out-of-order migration history")
    by_version = {item[0]: item for item in parsed}
    for row in applied_rows:
        version = int(row[0])
        if version not in by_version or row[1] != by_version[version][1]:
            raise RuntimeError("migration history changed")
        if has_sha and row[2] != by_version[version][3]:
            raise RuntimeError("migration history changed")

    applied = set(applied_versions)
    for version, name, script, digest in parsed:
        if version in applied:
            continue
        if version != len(applied) + 1:
            raise RuntimeError("out-of-order migration history")
        escaped_name = name.replace("'", "''")
        if version >= 4:
            backfill = "\n".join(
                "UPDATE schema_migration SET sha256='{}' WHERE version={};".format(
                    item[3], item[0]
                )
                for item in parsed
                if item[0] < version
            )
            receipt = (
                "INSERT INTO schema_migration(version,name,applied_at,sha256) "
                f"VALUES ({version},'{escaped_name}',strftime('%Y-%m-%dT%H:%M:%fZ','now'),'{digest}');"
            )
        else:
            backfill = ""
            receipt = (
                "INSERT INTO schema_migration(version,name,applied_at) "
                f"VALUES ({version},'{escaped_name}',strftime('%Y-%m-%dT%H:%M:%fZ','now'));"
            )
        conn.execute("PRAGMA foreign_keys = OFF")
        try:
            conn.executescript(f"BEGIN IMMEDIATE;\n{script}\n{backfill}\n{receipt}")
            violations = list(conn.execute("PRAGMA foreign_key_check"))
            if violations:
                raise sqlite3.IntegrityError("migration produced foreign key violations")
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.execute("PRAGMA foreign_keys = ON")
        applied.add(version)


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
