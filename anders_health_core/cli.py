"""One-shot local commands. This is not a scheduler, sync service, or backend."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

from . import contracts, database, demo, lifecycle, privacy_gate, records


def _print(payload: Dict[str, Any]) -> None:
    print(json.dumps(payload, sort_keys=True, separators=(",", ":")))


def _verify(db_path: Path) -> Dict[str, Any]:
    with database.connect(db_path) as conn:
        return {
            "schema_version": database.schema_version(conn),
            "subjects": conn.execute("SELECT COUNT(*) FROM subject").fetchone()[0],
            "sources": conn.execute("SELECT COUNT(*) FROM source_registry").fetchone()[0],
            "metrics": conn.execute("SELECT COUNT(*) FROM metric_definition").fetchone()[0],
            "raw_versions": conn.execute("SELECT COUNT(*) FROM source_record_version").fetchone()[0],
            "current_records": conn.execute("SELECT COUNT(*) FROM current_source_record").fetchone()[0],
            "normalized_facts": conn.execute("SELECT COUNT(*) FROM normalized_fact").fetchone()[0],
            "quality_issues": conn.execute("SELECT COUNT(*) FROM quality_issue").fetchone()[0],
            "context_events": conn.execute("SELECT COUNT(*) FROM context_event").fetchone()[0],
        }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="anders-health-core")
    subparsers = parser.add_subparsers(dest="command", required=True)

    for name in ("init", "demo", "verify"):
        command = subparsers.add_parser(name)
        command.add_argument("--db", required=True, type=Path)

    for name in ("import-jsonl", "import-csv"):
        command = subparsers.add_parser(name)
        command.add_argument("--db", required=True, type=Path)
        command.add_argument("--input", required=True, type=Path)
        command.add_argument("--subject", required=True)

    for name in ("register-source", "register-metric"):
        command = subparsers.add_parser(name)
        command.add_argument("--db", required=True, type=Path)
        command.add_argument("--input", required=True, type=Path)

    export = subparsers.add_parser("export")
    export.add_argument("--db", required=True, type=Path)
    export.add_argument("--subject", required=True)
    export.add_argument("--output", required=True, type=Path)

    purge = subparsers.add_parser("purge")
    purge.add_argument("--db", required=True, type=Path)
    purge.add_argument("--subject", required=True)
    purge.add_argument("--confirm-subject", required=True)

    scan = subparsers.add_parser("privacy-scan")
    scan.add_argument("--root", default=Path.cwd(), type=Path)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "init":
        subject_id = database.initialize(args.db)
        with database.connect(args.db) as conn:
            version = database.schema_version(conn)
        _print({"schema_version": version, "subject_id": subject_id})
        return 0
    if args.command == "demo":
        _print(demo.build_demo(args.db))
        return 0
    if args.command == "verify":
        result = _verify(args.db)
        _print(result)
        return 0 if result["schema_version"] == 5 else 1
    if args.command in {"register-source", "register-metric"}:
        payload = json.loads(args.input.read_text(encoding="utf-8"))
        contract_name = "SourceManifest" if args.command == "register-source" else "MetricDefinition"
        if contract_name == "SourceManifest":
            errors = contracts.validate_contract(contract_name, payload)
            if errors:
                _print({"errors": errors, "status": "rejected"})
                return 1
        with database.connect(args.db) as conn:
            if args.command == "register-source":
                records.register_source(conn, payload)
            else:
                records.register_metric(conn, payload)
        _print({"status": "registered", "contract": contract_name})
        return 0
    if args.command in {"import-jsonl", "import-csv"}:
        with database.connect(args.db) as conn:
            receipt = (
                records.import_jsonl(conn, args.input, args.subject)
                if args.command == "import-jsonl"
                else records.import_csv(conn, args.input, args.subject)
            )
        _print(receipt)
        return 0 if receipt["rejected"] == 0 else 1
    if args.command == "export":
        with database.connect(args.db) as conn:
            payload = lifecycle.export_subject(conn, args.subject)
        args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        _print({"datasets": len(payload["datasets"]), "output": str(args.output)})
        return 0
    if args.command == "purge":
        if args.subject != args.confirm_subject:
            raise SystemExit("confirmation subject does not match")
        with database.connect(args.db) as conn:
            deleted = lifecycle.purge_subject(conn, args.subject)
            remaining = conn.execute("SELECT COUNT(*) FROM subject").fetchone()[0]
        _print({**deleted, "remaining_subjects": remaining})
        return 0
    if args.command == "privacy-scan":
        violations = privacy_gate.scan_repository(args.root)
        _print({"violations": violations, "violation_count": len(violations)})
        return 1 if violations else 0
    raise AssertionError("unreachable")


if __name__ == "__main__":
    raise SystemExit(main())
