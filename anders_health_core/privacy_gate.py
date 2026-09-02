"""Fail-closed scan for common secrets and private local identifiers."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, Iterable, List


PATTERNS = (
    ("absolute_home_path", re.compile(r"/(?:Users|home)/[^/\s]+/")),
    ("jwt_like_token", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")),
    ("private_key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("generic_api_key", re.compile(r"(?i)(?:api[_-]?key|secret[_-]?key|access[_-]?token)\s*[:=]\s*['\"]?[A-Za-z0-9_\-]{20,}")),
    ("private_identifier", re.compile(r"(?i)\b(?:patient|medical_record|external_account|device_account)_id\b\s*[:=]\s*['\"]?(?!synthetic|YOUR_|0{8}-)[A-Za-z0-9@._-]{3,}")),
    ("health_record_marker", re.compile(r"(?i)\b(?:health_record|medical_record)\b\s*[:=]\s*['\"]?(?!synthetic)[A-Za-z0-9@._-]{3,}")),
)
SKIP_PARTS = {".git", "__pycache__", ".pytest_cache", ".mypy_cache"}
SYNTHETIC_BINARY_MAGIC = {
    ".db": (b"SQLite format 3\0",), ".sqlite": (b"SQLite format 3\0",),
    ".sqlite3": (b"SQLite format 3\0",), ".png": (b"\x89PNG\r\n\x1a\n",),
    ".jpg": (b"\xff\xd8\xff",), ".jpeg": (b"\xff\xd8\xff",), ".gif": (b"GIF8",),
    ".pdf": (b"%PDF-",), ".parquet": (b"PAR1",), ".xlsx": (b"PK\x03\x04",),
    ".zip": (b"PK\x03\x04",), ".gz": (b"\x1f\x8b",), ".bin": (),
}


def scan_text(text: str) -> List[Dict[str, Any]]:
    violations: List[Dict[str, Any]] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        for reason_code, pattern in PATTERNS:
            if pattern.search(line):
                violations.append({"reason_code": reason_code, "line": line_number})
    return violations


def scan_blob(path: str, data: bytes) -> List[Dict[str, Any]]:
    suffix = Path(path).suffix.lower()
    if suffix in SYNTHETIC_BINARY_MAGIC:
        stem = Path(path).stem.lower()
        if (stem == "synthetic" or stem.startswith("synthetic-")) and any(
            data.startswith(magic) for magic in SYNTHETIC_BINARY_MAGIC[suffix]
        ):
            return []
        return [{"reason_code": "private_health_artifact", "line": 0}]
    try:
        return scan_text(data.decode("utf-8"))
    except UnicodeDecodeError:
        return [{"reason_code": "unknown_binary_artifact", "line": 0}]


def _candidate_files(root: Path) -> Iterable[Path]:
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in SKIP_PARTS for part in path.parts):
            continue
        yield path


def scan_repository(root: Path) -> List[Dict[str, Any]]:
    violations: List[Dict[str, Any]] = []
    for path in _candidate_files(root):
        for item in scan_blob(path.relative_to(root).as_posix(), path.read_bytes()):
            violations.append(
                {**item, "file": path.relative_to(root).as_posix()}
            )
    return violations
