"""Synthetic fixtures only: these are not actual human feedback or dataset rows."""

from __future__ import annotations

import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import collect


def observation() -> dict:
    return {
        "candidate_id": "synthetic:github:example/repo:comment:1",
        "source": {
            "kind": "github",
            "event_id": "synthetic:reaction:2:active:v1",
            "feedback_id": "synthetic:reaction:2",
            "event_version": 1,
            "supersedes_record_id": None,
            "occurred_at": None,
            "url": "https://github.com/example/repo/pull/1#discussion_r1",
            "visibility": "public",
            "observed_at": "2026-01-01T00:00:00Z",
            "collector_version": "synthetic-test-v1",
        },
        "context": {
            "repository": "example/repo",
            "pr_number": 1,
            "base_commit": None,
            "reviewed_commit": None,
            "pr_summary": None,
            "status": "partial",
            "missing": ["base_commit", "reviewed_commit", "pr_summary", "snapshot"],
            "snapshot_ref": None,
        },
        "candidate": {
            "text": "Synthetic candidate, not a real finding.\nPreserve this line.",
            "scope": "finding",
            "authorship": "agent",
            "producer": "synthetic-test",
            "model": None,
            "instruction_version": None,
            "path": "example.py",
            "line": 1,
            "generated_at": None,
        },
        "human_feedback": {
            "kind": "thumbs_down",
            "verbatim_text": None,
            "reaction": "-1",
            "corrected_text": None,
            "actor_role": "unknown",
            "state": "active",
            "authorship": "human",
        },
    }


class CollectionTests(unittest.TestCase):
    def test_preparation_preserves_source(self):
        source = observation()
        before = copy.deepcopy(source)
        row = collect.prepare(source)
        self.assertEqual(source, before)
        self.assertEqual(row["candidate"]["text"], source["candidate"]["text"])
        self.assertEqual(row["human_feedback"], source["human_feedback"])
        self.assertEqual(row["candidate"]["sha256"], collect.digest(source["candidate"]["text"]))
        self.assertEqual(row["label_status"], "pending")
        self.assertIsNone(row["ai_assessment"])

    def test_non_object_input(self):
        with self.assertRaisesRegex(ValueError, "must be an object"):
            collect.prepare([])

    def test_human_label_is_not_raw_evidence(self):
        for key, value in [("label_status", "invalid"), ("human_label", "invalid")]:
            row = observation()
            row[key] = value
            with self.assertRaises(ValueError):
                collect.prepare(row)

    def test_candidate_authorship_cannot_be_human(self):
        row = observation()
        row["candidate"]["authorship"] = "human"
        with self.assertRaises(ValueError):
            collect.prepare(row)

    def test_text_feedback_requires_verbatim_evidence(self):
        row = observation()
        row["human_feedback"].update(kind="rejected", reaction=None)
        with self.assertRaises(ValueError):
            collect.prepare(row)
        row["human_feedback"]["verbatim_text"] = "Don't post this."
        collect.prepare(row)

    def test_revision_requires_human_correction(self):
        row = observation()
        row["human_feedback"].update(kind="revised", reaction=None, verbatim_text="Narrow the claim.")
        with self.assertRaises(ValueError):
            collect.prepare(row)
        row["human_feedback"]["corrected_text"] = "Synthetic corrected claim."
        collect.prepare(row)

    def test_exact_context_requires_snapshot_and_original_revisions(self):
        row = observation()
        row["context"].update(status="exact", missing=[])
        with self.assertRaises(ValueError):
            collect.prepare(row)
        row["context"].update(
            base_commit="a" * 40,
            reviewed_commit="b" * 40,
            pr_summary="Synthetic summary",
            snapshot_ref="synthetic-snapshot-id",
        )
        collect.prepare(row)

    def test_partial_context_explains_missing_data(self):
        row = observation()
        row["context"]["missing"] = []
        with self.assertRaises(ValueError):
            collect.prepare(row)

    def test_commit_and_timestamp_validation(self):
        for edit in ("commit", "time"):
            row = observation()
            if edit == "commit":
                row["context"]["reviewed_commit"] = "main"
            else:
                row["source"]["observed_at"] = "2026-01-01"
            with self.assertRaises(ValueError):
                collect.prepare(row)

    def test_checksums_and_ids_are_checked_not_overwritten(self):
        for edit in ("sha256", "record_id"):
            row = observation()
            if edit == "sha256":
                row["candidate"]["sha256"] = "0" * 64
            else:
                row["record_id"] = "0" * 64
            with self.assertRaises(ValueError):
                collect.prepare(row)

    def test_duplicate_reobservation_and_conflicting_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "candidates.jsonl"
            row = collect.prepare(observation())
            self.assertEqual(collect.append(row, path), "appended")
            again = copy.deepcopy(row)
            again["source"]["observed_at"] = "2026-01-02T00:00:00Z"
            self.assertEqual(collect.append(again, path), "already_present")
            self.assertEqual(len(collect.read_rows(path.read_text())), 1)
            again["human_feedback"]["verbatim_text"] = "Different evidence."
            with self.assertRaisesRegex(ValueError, "different evidence"):
                collect.append(again, path)

    def test_new_event_does_not_rewrite_original_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "candidates.jsonl"
            row = collect.prepare(observation())
            collect.append(row, path)
            changed = observation()
            changed["source"]["event_id"] = "synthetic:new-event"
            changed["source"]["feedback_id"] = "synthetic:new-feedback"
            changed["candidate"]["text"] = "A rewritten finding."
            with self.assertRaisesRegex(ValueError, "different original text"):
                collect.append(collect.prepare(changed), path)

    def test_reaction_removal_is_a_separate_event(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "candidates.jsonl"
            first = observation()
            first_row = collect.prepare(first)
            collect.append(first_row, path)
            removed = copy.deepcopy(first)
            removed["source"].update(
                event_id="synthetic:reaction:2:removed:v2",
                event_version=2,
                supersedes_record_id=first_row["record_id"],
            )
            removed["human_feedback"]["state"] = "removed"
            collect.append(collect.prepare(removed), path)
            rows = collect.read_rows(path.read_text())
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["candidate_id"], rows[1]["candidate_id"])
            self.assertEqual(rows[0]["human_feedback"]["state"], "active")

    def test_private_and_session_records_cannot_enter_public_checkout(self):
        for visibility, kind in [("private", "github"), ("space", "agent_session"), ("public", "agent_session")]:
            row = observation()
            row["source"].update(visibility=visibility, kind=kind)
            with self.assertRaisesRegex(ValueError, "outside this public repository"):
                collect.guard_public_export(collect.prepare(row), collect.PUBLIC_DATA)
        row = observation()
        row["source"].update(kind="agent_session", visibility="space")
        with tempfile.TemporaryDirectory() as tmp:
            collect.append(collect.prepare(row), Path(tmp) / "restricted.jsonl")

    def test_manifest_update_on_public_append_and_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "candidates.jsonl"
            manifest = Path(tmp) / "manifest.json"
            manifest.write_text('{"rows": 0}')
            with patch.object(collect, "PUBLIC_DATA", path), patch.object(collect, "MANIFEST", manifest):
                collect.append(collect.prepare(observation()), path)
                self.assertEqual(json.loads(manifest.read_text())["rows"], 1)
                self.assertEqual(collect.main(["validate", "--input", str(path)]), 0)

    def test_malformed_and_duplicate_jsonl_are_rejected(self):
        row = collect.prepare(observation())
        line = json.dumps(row) + "\n"
        for text in ("{broken\n", "\n", line + line):
            with self.assertRaises(ValueError):
                collect.read_rows(text)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "candidates.jsonl"
            path.write_text(json.dumps(row))
            other = observation()
            other["source"]["event_id"] = "synthetic:other"
            with self.assertRaisesRegex(ValueError, "end with a newline"):
                collect.append(collect.prepare(other), path)

    def test_concurrent_cli_writers_deduplicate(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "candidates.jsonl"
            input_path = Path(tmp) / "input.json"
            input_path.write_text(json.dumps(observation()))
            command = [sys.executable, str(Path(collect.__file__)), "append", "--input", str(input_path), "--output", str(path)]
            processes = [subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(4)]
            results = []
            for process in processes:
                stdout, stderr = process.communicate(timeout=30)
                self.assertEqual(process.returncode, 0, stderr)
                results.append(json.loads(stdout)["status"])
            self.assertEqual(results.count("appended"), 1)
            self.assertEqual(results.count("already_present"), 3)
            self.assertEqual(len(collect.read_rows(path.read_text())), 1)

    def test_unicode_separators_round_trip_verbatim(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "candidates.jsonl"
            original = observation()
            original["candidate"]["text"] = "Candidate\u2028line\u2029paragraph\u0085next"
            original["human_feedback"]["verbatim_text"] = "Feedback\u2028line\u2029paragraph\u0085next"
            collect.append(collect.prepare(original), path)
            again = observation()
            again["source"]["feedback_id"] = "synthetic:second"
            again["candidate"]["text"] = original["candidate"]["text"]
            collect.append(collect.prepare(again), path)
            rows = collect.read_rows(path.read_text())
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["candidate"]["text"], original["candidate"]["text"])
            self.assertEqual(rows[0]["human_feedback"]["verbatim_text"], original["human_feedback"]["verbatim_text"])

    def test_conflicting_candidate_provenance_is_rejected(self):
        changes = [
            ("context", "repository", "different/repo"),
            ("context", "pr_number", 2),
            ("context", "reviewed_commit", "c" * 40),
            ("candidate", "producer", "different-producer"),
            ("candidate", "scope", "review_body"),
            ("candidate", "path", "different.py"),
        ]
        for section, field, value in changes:
            with self.subTest(field=field), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "candidates.jsonl"
                first = observation()
                first["context"]["reviewed_commit"] = "b" * 40
                collect.append(collect.prepare(first), path)
                second = copy.deepcopy(first)
                second["source"]["feedback_id"] = "synthetic:other"
                second[section][field] = value
                with self.assertRaisesRegex(ValueError, "conflicting original"):
                    collect.append(collect.prepare(second), path)

    def test_known_metadata_can_enrich_unknowns_but_not_conflict(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "candidates.jsonl"
            first = observation()
            collect.append(collect.prepare(first), path)
            enriched = copy.deepcopy(first)
            enriched["source"]["feedback_id"] = "synthetic:enrichment"
            enriched["candidate"]["model"] = "synthetic-known-model"
            collect.append(collect.prepare(enriched), path)
            conflict = copy.deepcopy(first)
            conflict["source"]["feedback_id"] = "synthetic:conflict"
            conflict["candidate"]["model"] = "different-model"
            with self.assertRaisesRegex(ValueError, "conflicting original"):
                collect.append(collect.prepare(conflict), path)

    def test_feedback_versions_require_correct_predecessor(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "candidates.jsonl"
            first = collect.prepare(observation())
            collect.append(first, path)
            other = observation()
            other["source"]["feedback_id"] = "synthetic:another-human-reaction"
            other = collect.prepare(other)
            collect.append(other, path)
            removed = observation()
            removed["human_feedback"]["state"] = "removed"
            with self.assertRaises(ValueError):
                collect.prepare(removed)  # A removal is not a first observation.
            removed["source"].update(event_version=2, supersedes_record_id=other["record_id"])
            with self.assertRaisesRegex(ValueError, "same feedback_id"):
                collect.append(collect.prepare(removed), path)
            removed["source"]["supersedes_record_id"] = "0" * 64
            with self.assertRaisesRegex(ValueError, "predecessor must be present"):
                collect.append(collect.prepare(removed), path)
            removed["source"]["supersedes_record_id"] = first["record_id"]
            removed = collect.prepare(removed)
            collect.append(removed, path)
            rows = collect.read_rows(path.read_text())
            self.assertEqual(rows[-1]["source"]["supersedes_record_id"], first["record_id"])
            self.assertNotEqual(rows[-1]["record_id"], first["record_id"])

    def test_equivalent_integer_versions_have_one_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "candidates.jsonl"
            integer = collect.prepare(observation())
            collect.append(integer, path)
            equivalent = observation()
            equivalent["source"]["event_version"] = 1.0
            equivalent = collect.prepare(equivalent)
            self.assertEqual(equivalent["record_id"], integer["record_id"])
            self.assertEqual(collect.append(equivalent, path), "already_present")
            removed = observation()
            removed["source"].update(event_version=2.0, supersedes_record_id=equivalent["record_id"])
            removed["human_feedback"]["state"] = "removed"
            collect.append(collect.prepare(removed), path)
            self.assertEqual(len(collect.read_rows(path.read_text())), 2)
            for invalid in (1.5, True, float("inf"), "1"):
                bad = observation()
                bad["source"]["event_version"] = invalid
                with self.assertRaises(ValueError):
                    collect.prepare(bad)

    def test_removal_cannot_supersede_textual_rejection(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "candidates.jsonl"
            textual = observation()
            textual["human_feedback"].update(kind="rejected", reaction=None, verbatim_text="Don't post this.")
            textual = collect.prepare(textual)
            collect.append(textual, path)
            removed = observation()
            removed["source"].update(event_version=2, supersedes_record_id=textual["record_id"])
            removed["human_feedback"]["state"] = "removed"
            with self.assertRaisesRegex(ValueError, "active thumbs-down reaction"):
                collect.append(collect.prepare(removed), path)

    def test_current_export_matches_manifest(self):
        rows = collect.read_rows(collect.PUBLIC_DATA.read_text())
        self.assertEqual(json.loads(collect.MANIFEST.read_text())["rows"], len(rows))
        self.assertFalse(any(row["source"]["collector_version"].startswith("synthetic-test") for row in rows))
        self.assertEqual(collect.main(["validate", "--input", str(collect.PUBLIC_DATA)]), 0)


if __name__ == "__main__":
    unittest.main()
