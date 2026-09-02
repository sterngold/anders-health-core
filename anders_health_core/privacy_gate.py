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
)
SKIP_PARTS = {".git", "__pycache__", ".pytest_cache", ".mypy_cache"}
SKIP_SUFFIXES = {".db", ".sqlite", ".sqlite3", ".pyc", ".png", ".jpg", ".jpeg", ".gif", ".pdf"}


def scan_text(text: str) -> List[Dict[str, Any]]:
    violations: List[Dict[str, Any]] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        for reason_code, pattern in PATTERNS:
            if pattern.search(line):
                violations.append({"reason_code": reason_code, "line": line_number})
    return violations


def _candidate_files(root: Path) -> Iterable[Path]:
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in SKIP_PARTS for part in path.parts):
            continue
        if path.suffix.lower() in SKIP_SUFFIXES:
            continue
        yield path


def scan_repository(root: Path) -> List[Dict[str, Any]]:
    violations: List[Dict[str, Any]] = []
    for path in _candidate_files(root):
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for item in scan_text(text):
            violations.append(
                {**item, "file": path.relative_to(root).as_posix()}
            )
    return violations
