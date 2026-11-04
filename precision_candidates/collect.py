#!/usr/bin/env python3
"""Validate and stage precision feedback. Never posts to GitHub or trains a model."""

from __future__ import annotations

import argparse
import copy
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sys

from jsonschema import Draft202012Validator, FormatChecker

DATASET_DIR = Path(__file__).resolve().parent
PUBLIC_DATA = DATASET_DIR / "raw" / "candidates.jsonl"
MANIFEST = DATASET_DIR / "staging-manifest.json"
SCHEMA = json.loads((DATASET_DIR / "schema.json").read_text(encoding="utf-8"))
Draft202012Validator.check_schema(SCHEMA)
VALIDATOR = Draft202012Validator(SCHEMA, format_checker=FormatChecker())
if "date-time" not in VALIDATOR.format_checker.checkers:
    raise RuntimeError("Install precision_candidates/requirements.txt to enable timestamp validation")


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def record_id(row: dict) -> str:
    """Stable identity for one version of one feedback event on one candidate."""
    version = row["source"]["event_version"]
    if isinstance(version, bool) or not isinstance(version, (int, float)) or (isinstance(version, float) and not version.is_integer()):
        raise ValueError("source.event_version must be an integer")
    # JSON Schema treats 1 and 1.0 as the same integer; hash them identically.
    key = [row["source"]["kind"], row["candidate_id"], row["source"]["feedback_id"], int(version)]
    return digest(json.dumps(key, separators=(",", ":"), ensure_ascii=False))


def prepare(row: dict) -> dict:
    """Fill derived fields only. Never edit source text or invent metadata."""
    row = copy.deepcopy(row)
    if not isinstance(row, dict):
        raise ValueError("record must be an object")
    row.setdefault("schema_version", 1)
    row.setdefault("label_status", "pending")
    row.setdefault("ai_assessment", None)
    if not isinstance(row.get("candidate"), dict):
        raise ValueError("candidate must be an object")
    if not isinstance(row["candidate"].get("text"), str):
        raise ValueError("candidate.text must be a string")
    row["candidate"].setdefault("sha256", digest(row["candidate"]["text"]))
    try:
        row.setdefault("record_id", record_id(row))
    except (KeyError, TypeError) as exc:
        raise ValueError("source kind/feedback_id/event_version and candidate_id are required") from exc
    validate(row)
    return row


def validate(row: dict) -> None:
    errors = sorted(VALIDATOR.iter_errors(row), key=lambda e: str(list(e.path)))
    if errors:
        err = errors[0]
        path = ".".join(str(p) for p in err.path) or "record"
        raise ValueError(f"{path}: {err.message}")
    if row["candidate"]["sha256"] != digest(row["candidate"]["text"]):
        raise ValueError("candidate.sha256 does not match the original text")
    if row["record_id"] != record_id(row):
        raise ValueError("record_id does not match the source event identity")


IDENTITY_FIELDS = {
    "candidate": ("text", "sha256", "scope", "authorship", "producer", "model", "instruction_version", "path", "line", "generated_at"),
    "context": ("repository", "pr_number", "base_commit", "reviewed_commit", "pr_summary", "snapshot_ref"),
}


def check_history(rows: list[dict]) -> None:
    """Preserve known candidate provenance and explicit feedback-version chains."""
    candidates: dict[str, dict] = {}
    records = {row["record_id"]: row for row in rows}
    for row in rows:
        old = candidates.setdefault(row["candidate_id"], copy.deepcopy(row))
        for section, fields in IDENTITY_FIELDS.items():
            for field in fields:
                before, after = old[section][field], row[section][field]
                if before is not None and after is not None and before != after:
                    if field == "text":
                        raise ValueError("candidate_id was reused for different original text")
                    raise ValueError(f"candidate_id has conflicting original {section}.{field}")
                if before is None and after is not None:
                    old[section][field] = after  # Only enrich previously unknown metadata.
        source = row["source"]
        if source["event_version"] == 1:
            continue
        predecessor = records.get(source["supersedes_record_id"])
        if predecessor is None:
            raise ValueError("feedback version's predecessor must be present in the export")
        previous = predecessor["source"]
        if (
            predecessor["candidate_id"] != row["candidate_id"]
            or previous["kind"] != source["kind"]
            or previous["feedback_id"] != source["feedback_id"]
            or previous["event_version"] != source["event_version"] - 1
        ):
            raise ValueError("feedback version must supersede the preceding version of the same feedback_id and candidate")
        if row["human_feedback"]["state"] == "removed":
            feedback = predecessor["human_feedback"]
            if feedback["state"] != "active" or feedback["kind"] != "thumbs_down" or feedback["reaction"] != "-1":
                raise ValueError("a removal must supersede an active thumbs-down reaction observation")


def read_rows(text: str) -> list[dict]:
    rows = []
    ids: set[str] = set()
    lines = text.split("\n")  # JSONL delimits records by LF, not Unicode separators.
    for line_number, line in enumerate(lines, start=1):
        if not line and line_number == len(lines):
            continue  # Empty export or the final LF, not a logical blank row.
        if not line.strip():
            raise ValueError(f"line {line_number}: blank records are not allowed")
        try:
            row = json.loads(line)
            validate(row)
            if row["record_id"] in ids:
                raise ValueError("duplicate record_id")
            ids.add(row["record_id"])
        except (ValueError, TypeError) as exc:
            raise ValueError(f"line {line_number}: {exc}") from exc
        rows.append(row)
    check_history(rows)
    return rows


def comparable(row: dict) -> dict:
    row = copy.deepcopy(row)
    # Re-observing an unchanged event is not a new training example.
    row["source"].pop("observed_at")
    return row


def guard_public_export(row: dict, path: Path) -> None:
    try:
        path.resolve().relative_to(DATASET_DIR.parent.resolve())
    except ValueError:
        return  # Restricted local staging outside the public repository.
    if row["source"]["visibility"] != "public" or row["source"]["kind"] == "agent_session":
        raise ValueError("Space/private and agent-session feedback must be staged outside this public repository")


def update_manifest(count: int) -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    manifest["rows"] = count
    temp = MANIFEST.with_name(f"manifest.json.tmp.{os.getpid()}")
    try:
        temp.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        temp.replace(MANIFEST)
    finally:
        temp.unlink(missing_ok=True)


def append(row: dict, path: Path) -> str:
    validate(row)
    guard_public_export(row, path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+", encoding="utf-8", newline="\n") as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        stream.seek(0)
        text = stream.read()
        rows = read_rows(text)
        if text and not text.endswith("\n"):
            raise ValueError("existing JSONL must end with a newline before appending")
        for old in rows:
            if old["record_id"] == row["record_id"]:
                if comparable(old) != comparable(row):
                    raise ValueError("event already exists with different evidence; record a new event version")
                if path.resolve() == PUBLIC_DATA.resolve():
                    update_manifest(len(rows))
                return "already_present"
        check_history(rows + [row])
        stream.seek(0, os.SEEK_END)
        stream.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
        if path.resolve() == PUBLIC_DATA.resolve():
            update_manifest(len(rows) + 1)
    return "appended"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    add = sub.add_parser("append", help="Stage one observation; --input - reads stdin")
    add.add_argument("--input", required=True)
    add.add_argument("--output", required=True, type=Path)
    check = sub.add_parser("validate", help="Validate an entire JSONL export")
    check.add_argument("--input", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "append":
            text = sys.stdin.read() if args.input == "-" else Path(args.input).read_text(encoding="utf-8")
            row = prepare(json.loads(text))
            status = append(row, args.output)
            print(json.dumps({"status": status, "record_id": row["record_id"]}))
        else:
            rows = read_rows(args.input.read_text(encoding="utf-8"))
            if args.input.resolve() == PUBLIC_DATA.resolve():
                for row in rows:
                    guard_public_export(row, args.input)
                count = json.loads(MANIFEST.read_text(encoding="utf-8"))["rows"]
                if count != len(rows):
                    raise ValueError(f"manifest has {count} rows, data has {len(rows)}")
            print(json.dumps({"status": "valid", "rows": len(rows)}))
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
