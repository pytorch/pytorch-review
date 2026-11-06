"""Synthetic fixtures only; no network calls and no writes to real dataset files."""

from __future__ import annotations

import argparse
import copy
import csv
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import collect_github as c

WHEN = "2026-01-02T00:00:00Z"


def user(login="reviewer", kind="User"):
    return {"login": login, "type": kind, "id": 123}


def snapshot(kind="review_comment"):
    return {"comment_kind": kind, "comment_id": 1, "pr_number": 2,
            "body": "Synthetic stored AI claim.", "snapshot_updated_at": "2026-01-01 00:00:00.123456789",
            "updated_at": "2026-01-01 00:00:00.123456789", "source_event_key": "synthetic-event-1"}


def comment():
    return {"id": 1, "body": "Synthetic current AI claim.\nSecond line, quoted: \"x\".\u2028Keep separator.",
            "user": user(c.REVIEW_AUTHOR, "Bot"), "pull_request_review_id": 10,
            "html_url": "https://github.com/pytorch/pytorch/pull/2#discussion_r1",
            "original_commit_id": "a" * 40, "commit_id": "b" * 40,
            "path": "example.py", "original_line": 7, "line": 8,
            "updated_at": "2026-01-01T00:00:00Z"}


def review():
    return {"id": 10, "user": user(c.REVIEW_AUTHOR, "Bot"), "body": c.REVIEW_MARKER + "\nSynthetic review."}


class FakeGH:
    def __init__(self):
        self.comment = comment()
        self.reviews = [review()]
        self.reviewers = {"reviewer": "requested"}
        self.reactions = [{"id": 3, "content": "-1", "user": user()}]
        self.fail = False

    def get(self, endpoint):
        if self.fail:
            raise c.CollectionError("synthetic API failure")
        return copy.deepcopy(self.comment)

    def pr_context(self, pr):
        return copy.deepcopy(self.reviews), dict(self.reviewers)

    def paged(self, endpoint):
        if self.fail:
            raise c.CollectionError("synthetic API failure")
        return copy.deepcopy(self.reactions)


class FakeCH:
    def __init__(self, pages):
        self.pages = copy.deepcopy(pages)
        self.sql = []

    def query(self, sql):
        self.sql.append(sql)
        page = self.pages.pop(0)
        if isinstance(page, Exception):
            raise page
        return page


def args(**changes):
    result = argparse.Namespace(since=datetime(2026, 1, 1, tzinfo=timezone.utc),
                                until=datetime(2026, 2, 1, tzinfo=timezone.utc),
                                page_size=100, max_pages=10, read_cap=1000, prs=[], kinds=["review_comment"])
    for key, value in changes.items():
        setattr(result, key, value)
    return result


class QueryTests(unittest.TestCase):
    def test_gcx_empty_and_named_columns(self):
        self.assertEqual(c.parse_gcx('{"columns":[{"name":"id"}],"rows":null}'), [])
        self.assertEqual(c.parse_gcx('{"columns":[{"name":"id"}],"rows":[]}'), [])
        self.assertEqual(c.parse_gcx("No data"), [])
        self.assertEqual(c.parse_gcx('{"columns":[{"name":"id"}],"rows":[[1]]}'), [{"id": 1}])

    def test_gcx_malformed_is_not_empty(self):
        for text in ("", "null", "{}", '{"columns":[{"name":"id"}],"rows":[[1,2]]}',
                     '{"columns":[{"name":"id"},{"name":"id"}],"rows":[]}'):
            with self.assertRaises(c.CollectionError):
                c.parse_gcx(text)

    def test_sql_is_bounded_and_uses_live_columns(self):
        a = args()
        sql = c.query_sql("review_comment", a.since, a.until, 100, None, [2, 4], 1000)
        self.assertIn("LIMIT 100", sql)
        self.assertIn("max_rows_to_read = 1000", sql)
        self.assertIn("tupleElement(comment.reactions, '-1') > 0", sql)
        self.assertIn("pull_request.number IN (2,4)", sql)
        self.assertIn("comment.line AS line", sql)
        self.assertNotIn("comment.original_line", sql)
        self.assertIn("AS snapshot_updated_at", sql)
        self.assertNotIn(" AS updated_at", sql)

    def test_cursor_preserves_nanos_and_quotes(self):
        a = args()
        sql = c.query_sql("review_comment", a.since, a.until, 100,
                          ("2026-01-01 00:00:00.123456789", "key'quoted"), [], 1000)
        self.assertIn("123456789", sql)
        self.assertIn("'key''quoted'", sql)
        with self.assertRaises(c.CollectionError):
            c.query_sql("review_comment", a.since, a.until, 100, ("not-a-time", "key"), [], 1000)

    def test_pages_stop_only_on_short_page_and_detect_cap(self):
        ch = FakeCH([[snapshot()], []])
        report = {"queries": 0, "snapshot_events": 0}
        self.assertEqual(len(list(c.snapshots(ch, "review_comment", args(page_size=1), report))), 1)
        self.assertEqual(report["queries"], 2)
        self.assertIn("123456789", ch.sql[1])
        with self.assertRaisesRegex(c.CollectionError, "page cap"):
            list(c.snapshots(FakeCH([[snapshot()]]), "review_comment", args(page_size=1, max_pages=1),
                             {"queries": 0, "snapshot_events": 0}))

    def test_date_bounds_require_timezone(self):
        self.assertEqual(c.utc_bound("2026-01-01").tzinfo, timezone.utc)
        self.assertEqual(c.utc_bound("2026-01-01T01:00:00-05:00").hour, 6)
        with self.assertRaises(argparse.ArgumentTypeError):
            c.utc_bound("2026-01-01T01:00:00")


class VerificationTests(unittest.TestCase):
    def test_positive_keeps_exact_versions_and_original_commit(self):
        gh = FakeGH()
        row = c.verify(snapshot(), gh, WHEN)[0]
        self.assertEqual(row["ai_comment"], gh.comment["body"])
        self.assertEqual(row["snapshot_comment"], snapshot()["body"])
        self.assertEqual(row["reviewed_commit"], "a" * 40)
        self.assertEqual(row["line"], 7)
        self.assertEqual(row["observed_at"], WHEN)
        self.assertEqual(row["label_status"], "pending")
        self.assertEqual(row["feedback_id"], "3")

    def test_absent_and_ineligible_reactions(self):
        for reaction in (None, {"id": 4, "content": "+1", "user": user()},
                         {"id": 4, "content": "-1", "user": user("unrelated")},
                         {"id": 4, "content": "-1", "user": user("maintainer")},
                         {"id": 4, "content": "-1", "user": user("reviewer", "Bot")},
                         {"id": 4, "content": "-1", "user": user("reviewer[bot]")}):
            gh = FakeGH()
            gh.reactions = [] if reaction is None else [reaction]
            self.assertEqual(c.verify(snapshot(), gh, WHEN), [])

    def test_hardened_marker_requires_parent_and_author(self):
        for edit in ("marker", "parent", "author", "reply"):
            gh = FakeGH()
            if edit == "marker":
                gh.reviews[0]["body"] = "Unmarked review"
            elif edit == "parent":
                gh.comment["pull_request_review_id"] = 11
            elif edit == "author":
                gh.comment["user"] = user("different-bot", "Bot")
            else:
                gh.comment["in_reply_to_id"] = 9
            self.assertEqual(c.verify(snapshot(), gh, WHEN), [])

    def test_explicit_attribution_is_not_style_inference(self):
        gh = FakeGH()
        gh.comment["user"] = user("poster")
        gh.comment["body"] = "*(Posted by Synthetic Model, an AI assistant, on behalf of @poster.)*\nSynthetic finding."
        self.assertEqual(c.verify(snapshot("issue_comment"), gh, WHEN)[0]["authorship_evidence"],
                         "explicit_assisted_review_attribution")
        for text in ("AI-generated reviews are frustrating.", "@pytorchbot review", "Looks like an AI wrote this."):
            gh.comment["body"] = text
            self.assertEqual(c.verify(snapshot("issue_comment"), gh, WHEN), [])

    def test_api_failure_is_unknown_not_absent(self):
        gh = FakeGH()
        gh.fail = True
        rows, report = c.export(FakeCH([[snapshot()]]), gh, args())
        self.assertEqual(rows, [])
        self.assertTrue(report["source_complete"])
        self.assertFalse(report["verification_complete"])
        self.assertEqual(len(report["unknown"]), 1)

    def test_identity_and_exact_pr_url(self):
        for field, value in (("id", 99), ("html_url", "https://github.com/pytorch/pytorch/pull/23#discussion_r1"),
                             ("html_url", "https://other.example/pytorch/pytorch/pull/2")):
            gh = FakeGH()
            gh.comment[field] = value
            with self.assertRaises(c.CollectionError):
                c.verify(snapshot(), gh, WHEN)

    def test_duplicate_snapshots_are_one_candidate_reaction(self):
        ch = FakeCH([[snapshot(), snapshot()]])
        rows, report = c.export(ch, FakeGH(), args())
        self.assertEqual(len(rows), 1)
        self.assertEqual(report["duplicates"], 1)
        self.assertEqual(report["unique_comments"], 1)

    def test_unmarked_snapshot_does_not_hide_later_attribution_across_pages(self):
        early = snapshot("issue_comment")
        later = copy.deepcopy(early)
        later["body"] = "*(Posted by Synthetic Model, an AI assistant, on behalf of @poster.)*\nSynthetic finding."
        later["snapshot_updated_at"] = "2026-01-02 00:00:00.123456789"
        later["source_event_key"] = "synthetic-event-2"
        gh = FakeGH()
        gh.comment["body"] = later["body"]
        gh.comment["user"] = user("poster")
        rows, report = c.export(FakeCH([[early], [later], []]), gh,
                                args(kinds=["issue_comment"], page_size=1))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["snapshot_comment"], later["body"])
        self.assertEqual(report["unique_comments"], 1)
        self.assertEqual(report["duplicates"], 1)
        self.assertTrue(report["source_complete"])

    def test_source_failure_keeps_coverage_incomplete(self):
        rows, report = c.export(FakeCH([c.CollectionError("synthetic cap failure")]), FakeGH(), args())
        self.assertEqual(rows, [])
        self.assertFalse(report["source_complete"])
        self.assertIn("cap failure", report["source_error"])

    def test_csv_round_trip_and_overwrite_protection(self):
        rows = c.verify(snapshot(), FakeGH(), WHEN)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "data.csv"
            c.write_csv(path, rows)
            with path.open(encoding="utf-8", newline="") as stream:
                read = list(csv.DictReader(stream))
            self.assertEqual(read[0]["ai_comment"], rows[0]["ai_comment"])
            self.assertEqual(read[0]["snapshot_comment"], rows[0]["snapshot_comment"])
            before = path.read_bytes()
            with self.assertRaises(c.CollectionError):
                c.write_csv(path, [])
            self.assertEqual(path.read_bytes(), before)

    def test_empty_output_has_header_not_a_fake_row(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "data.csv"
            c.write_csv(path, [])
            with path.open(encoding="utf-8", newline="") as stream:
                reader = csv.DictReader(stream)
                self.assertEqual(reader.fieldnames, c.COLUMNS)
                self.assertEqual(list(reader), [])

    def test_cli_does_not_replace_csv_on_incomplete_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "data.csv"
            path.write_text("existing dataset\n")
            with patch.object(c, "ClickHouse", return_value=FakeCH([c.CollectionError("synthetic cap")])):
                with patch.object(c, "GitHub", return_value=FakeGH()):
                    result = c.main(["--gcx-wrapper", "synthetic-wrapper", "--since", "2026-01-01",
                                     "--until", "2026-02-01", "--kinds", "review_comment",
                                     "--output", str(path), "--overwrite"])
            self.assertEqual(result, 2)
            self.assertEqual(path.read_text(), "existing dataset\n")
            report = json.loads(Path(str(path) + ".report.json").read_text())
            self.assertFalse(report["csv_written"])
            self.assertFalse(report["source_complete"])

    def test_cli_success_creates_csv_and_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "data.csv"
            with patch.object(c, "ClickHouse", return_value=FakeCH([[snapshot()]])):
                with patch.object(c, "GitHub", return_value=FakeGH()):
                    result = c.main(["--gcx-wrapper", "synthetic-wrapper", "--since", "2026-01-01",
                                     "--until", "2026-02-01", "--kinds", "review_comment", "--output", str(path)])
            self.assertEqual(result, 0)
            with path.open(encoding="utf-8", newline="") as stream:
                self.assertEqual(len(list(csv.DictReader(stream))), 1)
            report = json.loads(Path(str(path) + ".report.json").read_text())
            self.assertTrue(report["csv_written"])
            self.assertEqual(report["verification_attempts"], 1)

    def test_reviewers_are_requested_or_submitted_not_pending_or_bots(self):
        gh = c.GitHub()
        def get(endpoint):
            if endpoint.endswith("/pulls/2"):
                return {"base": {"repo": {"full_name": c.REPOSITORY, "private": False}}}
            if "requested_reviewers" in endpoint:
                return {"users": [user("requested")]}
            return [
                {"user": user("submitted"), "submitted_at": WHEN, "state": "COMMENTED"},
                {"user": user("pending"), "submitted_at": None, "state": "PENDING"},
                {"user": user("robot[bot]", "Bot"), "submitted_at": WHEN, "state": "COMMENTED"},
            ]
        with patch.object(gh, "get", side_effect=get):
            _, names = gh.pr_context(2)
        self.assertEqual(names, {"requested": "requested", "submitted": "submitted"})

    def test_pr_author_reaction_is_eligible(self):
        gh = FakeGH()
        gh.reviewers["author"] = "author"
        gh.reactions = [{"id": 4, "content": "-1", "user": user("author")}]
        rows = c.verify(snapshot(), gh, WHEN)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["reactor"], "author")
        self.assertEqual(rows[0]["reactor_eligibility"], "author")

    def test_pr_context_adds_human_author_but_not_bot_author(self):
        for author, eligible in ((user("author"), {"author": "author"}),
                                 (user("robot[bot]", "Bot"), {}),
                                 (user("facebook-github-bot"), {})):
            with self.subTest(author=author["login"]):
                gh = c.GitHub()
                def get(endpoint):
                    if endpoint.endswith("/pulls/2"):
                        return {"user": author, "base": {"repo": {"full_name": c.REPOSITORY, "private": False}}}
                    if "requested_reviewers" in endpoint:
                        return {"users": []}
                    return []
                with patch.object(gh, "get", side_effect=get):
                    _, names = gh.pr_context(2)
                self.assertEqual(names, eligible)

    def test_private_or_unknown_visibility_is_rejected(self):
        for private in (True, None):
            gh = c.GitHub()
            with patch.object(gh, "get", return_value={"base": {"repo": {"full_name": c.REPOSITORY, "private": private}}}):
                with self.assertRaises(c.CollectionError):
                    gh.pr_context(2)


if __name__ == "__main__":
    unittest.main()
