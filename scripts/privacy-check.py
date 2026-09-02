#!/usr/bin/env python3
import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from anders_health_core import privacy_gate


def history(root):
    violations = []
    seen = set()
    commits = subprocess.run(["git", "-C", str(root), "rev-list", "--all"],
                             check=True, capture_output=True, text=True).stdout.splitlines()
    for commit in commits:
        tree = subprocess.run(["git", "-C", str(root), "ls-tree", "-rz", "--full-tree", commit],
                              check=True, capture_output=True).stdout
        for entry in tree.split(b"\0"):
            if not entry:
                continue
            metadata, path_bytes = entry.split(b"\t", 1)
            _, kind, oid_bytes = metadata.split()
            key = (oid_bytes, path_bytes)
            if kind != b"blob" or key in seen:
                continue
            seen.add(key)
            oid, path = oid_bytes.decode(), path_bytes.decode("utf-8", "replace")
            data = subprocess.run(["git", "-C", str(root), "cat-file", "blob", oid],
                                  check=True, capture_output=True).stdout
            violations.extend({**item, "file": path, "object": oid}
                              for item in privacy_gate.scan_blob(path, data))
    return violations


parser = argparse.ArgumentParser()
parser.add_argument("root", nargs="?", type=Path, default=Path.cwd())
parser.add_argument("--history", action="store_true")
args = parser.parse_args()
found = history(args.root) if args.history else privacy_gate.scan_repository(args.root)
print(json.dumps({"violation_count": len(found), "violations": found}, sort_keys=True))
raise SystemExit(bool(found))
