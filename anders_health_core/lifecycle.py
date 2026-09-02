"""Subject export, backup, restore, and irreversible privacy deletion."""

import os
import shutil
import sqlite3
import tempfile
from pathlib import Path
from typing import Any, Dict

from . import database


def _quote(identifier: str) -> str:
    return f'"{identifier.replace(chr(34), chr(34) * 2)}"'


def _subject_tables(conn: sqlite3.Connection) -> tuple[str, ...]:
    tables = [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
    return tuple(table for table in tables if any(
        column[1] == "subject_id" for column in conn.execute(f"PRAGMA table_info({_quote(table)})")
    ))


def _rows(conn: sqlite3.Connection, table: str, subject_id: str):
    return [dict(row) for row in conn.execute(
        f"SELECT * FROM {_quote(table)} WHERE subject_id=?", (subject_id,)
    )]


def export_subject(conn: sqlite3.Connection, subject_id: str) -> Dict[str, Any]:
    subject = conn.execute("SELECT * FROM subject WHERE subject_id=?", (subject_id,)).fetchone()
    if subject is None:
        raise KeyError("subject not found")
    return {
        "schema_version": database.schema_version(conn),
        "subject": dict(subject),
        "datasets": {
            name: _rows(conn, name, subject_id)
            for name in _subject_tables(conn) if name != "subject"
        },
    }


def purge_subject(conn: sqlite3.Connection, subject_id: str) -> Dict[str, int]:
    tables = _subject_tables(conn)
    counts = {
        table: int(conn.execute(
            f"SELECT COUNT(*) FROM {_quote(table)} WHERE subject_id=?", (subject_id,)
        ).fetchone()[0]) for table in tables
    }
    conn.execute("SAVEPOINT subject_purge")
    try:
        conn.execute("DELETE FROM subject WHERE subject_id=?", (subject_id,))
        remaining = {
            table: count for table in tables if (count := conn.execute(
                f"SELECT COUNT(*) FROM {_quote(table)} WHERE subject_id=?", (subject_id,)
            ).fetchone()[0])
        }
        if remaining:
            raise RuntimeError(f"subject purge incomplete: {remaining}")
        conn.execute("RELEASE SAVEPOINT subject_purge")
        conn.commit()
    except Exception:
        conn.execute("ROLLBACK TO SAVEPOINT subject_purge")
        conn.execute("RELEASE SAVEPOINT subject_purge")
        raise
    return counts


def backup_database(source: Path, destination: Path) -> None:
    with database.connect(source) as source_conn, sqlite3.connect(str(destination)) as dest_conn:
        source_conn.backup(dest_conn)


def _schema_inventory(conn: sqlite3.Connection):
    return tuple(tuple(row) for row in conn.execute(
        "SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
    ))


def _migration_ledger(conn: sqlite3.Connection):
    return tuple(tuple(row) for row in conn.execute("SELECT version,name,sha256 FROM schema_migration ORDER BY version"))


def _validate_backup(path: Path) -> None:
    with database.connect(":memory:") as expected:
        database._apply_migrations(expected)
        expected_inventory, expected_ledger = _schema_inventory(expected), _migration_ledger(expected)
    with database.connect(path) as conn:
        if _schema_inventory(conn) != expected_inventory or _migration_ledger(conn) != expected_ledger:
            raise ValueError("backup schema or migration ledger is not current")
        if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("backup integrity check failed")
        if list(conn.execute("PRAGMA foreign_key_check")):
            raise ValueError("backup foreign key check failed")


def restore_database(backup: Path, destination: Path) -> None:
    fd, temporary_name = tempfile.mkstemp(prefix=f".{destination.name}.", dir=destination.parent)
    os.close(fd)
    temporary = Path(temporary_name)
    try:
        shutil.copy2(backup, temporary)
        try:
            _validate_backup(temporary)
        except sqlite3.DatabaseError as error:
            raise ValueError("backup validation failed") from error
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
