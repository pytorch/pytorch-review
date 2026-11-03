import json
import tempfile
import unittest
from pathlib import Path

from review_data.tools import select_reviews

WHO = {"rev1", "rev2"}


def review(login: str, at: str, state: str = "COMMENTED", body: str = "", inline: tuple[str, ...] = ("fix this",),
           typename: str = "User", oid: str = "c1") -> dict:
    return {"author": {"login": login, "__typename": typename}, "state": state, "body": body, "submittedAt": at,
            "commit": {"oid": oid}, "comments": {"nodes": [{"body": b} for b in inline]}}


def comment(login: str, at: str, body: str = "a comment") -> dict:
    return {"author": {"login": login, "__typename": "User"}, "body": body, "createdAt": at}


def thread(login: str, outdated: bool, body: str = "fix this") -> dict:
    return {"isOutdated": outdated, "comments": {"nodes": [{"author": {"login": login}, "body": body}]}}


def pr(reviews=(), comments=(), threads=(), author: str = "dev") -> dict:
    return {"number": 1, "author": {"login": author}, "reviews": {"nodes": list(reviews)},
            "comments": {"nodes": list(comments)}, "reviewThreads": {"nodes": list(threads)}}


def first(p: dict) -> dict | None:
    kept = select_reviews.kept_reviews(p, WHO)
    return {k: v for k, v in kept[0].items() if k in ("login", "submitted_at", "why")} if kept else None


class TestFirstReview(unittest.TestCase):
    def test_a_change_request_is_kept(self):
        found = first(pr([review("rev1", "2026-08-01T10:00:00Z", "CHANGES_REQUESTED")]))
        self.assertEqual(found, {"login": "rev1", "submitted_at": "2026-08-01T10:00:00Z", "why": "requested changes"})

    def test_a_comment_the_author_acted_on_is_kept_and_one_they_did_not_is_not(self):
        acted = first(pr([review("rev1", "2026-08-01T10:00:00Z")], threads=[thread("rev1", True)]))
        self.assertEqual(acted["why"], "author changed the commented code")
        self.assertIsNone(first(pr([review("rev1", "2026-08-01T10:00:00Z")], threads=[thread("rev1", False)])))

    def test_only_the_first_human_counts(self):
        p = pr([review("other", "2026-08-01T09:00:00Z"), review("rev1", "2026-08-01T10:00:00Z", "CHANGES_REQUESTED")])
        self.assertIsNone(first(p))
        # An issue comment by someone else first also means another human got there first.
        p = pr([review("rev1", "2026-08-01T10:00:00Z", "CHANGES_REQUESTED")], [comment("other", "2026-08-01T09:00:00Z")])
        self.assertIsNone(first(p))

    def test_bots_the_author_and_pings_are_not_the_first_human(self):
        p = pr([review("pytorchgreenlight", "2026-08-01T08:00:00Z"),
                review("rev2", "2026-08-01T08:30:00Z", body="@pytorchbot merge", inline=()),
                review("rev1", "2026-08-01T10:00:00Z", "CHANGES_REQUESTED")],
               [comment("pytorch-bot", "2026-08-01T07:00:00Z"), comment("dev", "2026-08-01T07:30:00Z"),
                comment("other", "2026-08-01T08:45:00Z", "@claude review this")])
        self.assertEqual(first(p)["login"], "rev1")

    def test_a_first_review_with_nothing_to_score_is_dropped(self):
        self.assertIsNone(first(pr([review("rev1", "2026-08-01T10:00:00Z", "CHANGES_REQUESTED", body="pls", inline=())])))
        found = first(pr([review("rev1", "2026-08-01T10:00:00Z", "CHANGES_REQUESTED", body="x" * 41, inline=())]))
        self.assertEqual(found["login"], "rev1")

    def test_a_first_comment_that_is_not_a_review_is_dropped(self):
        p = pr([review("rev1", "2026-08-01T10:00:00Z", "CHANGES_REQUESTED")], [comment("rev1", "2026-08-01T09:00:00Z")])
        self.assertIsNone(first(p))


class TestRounds(unittest.TestCase):
    def test_later_rounds_are_cases_that_name_the_previous_round_commit(self):
        p = pr([review("rev1", "2026-08-01T10:00:00Z", "CHANGES_REQUESTED", oid="c1"),
                review("rev1", "2026-08-01T10:01:00Z", inline=("and this",), oid="c1"),  # same commit: same round
                review("rev1", "2026-08-03T10:00:00Z", inline=("rename it",), oid="c2"),
                review("rev1", "2026-08-05T10:00:00Z", "APPROVED", body="lgtm", inline=(), oid="c3")],
               threads=[thread("rev1", True, "rename it")])
        kept = select_reviews.kept_reviews(p, WHO, all_rounds=True)
        self.assertEqual([(k["round"], k["submitted_at"], k["previous_review_sha"], k["why"]) for k in kept], [
            (1, "2026-08-01T10:00:00Z", "", "requested changes"),
            (2, "2026-08-03T10:00:00Z", "c1", "author changed the commented code"),
        ])  # round 3, a bare approval, has nothing to score
        self.assertEqual(len(select_reviews.kept_reviews(p, WHO)), 1)

    def test_a_later_round_is_kept_only_by_its_own_comments(self):
        # The outdated thread belongs to round 1, so round 2 (nobody acted on it) is not kept.
        p = pr([review("rev1", "2026-08-01T10:00:00Z", inline=("fix this",), oid="c1"),
                review("rev1", "2026-08-03T10:00:00Z", inline=("one more",), oid="c2")],
               threads=[thread("rev1", True, "fix this"), thread("rev1", False, "one more")])
        kept = select_reviews.kept_reviews(p, WHO, all_rounds=True)
        self.assertEqual([k["round"] for k in kept], [1])

    def test_a_skipped_round_still_is_the_previous_feedback(self):
        p = pr([review("rev1", "2026-08-01T10:00:00Z", body="pls", inline=(), oid="c1"),
                review("rev1", "2026-08-03T10:00:00Z", "CHANGES_REQUESTED", oid="c2")])
        kept = select_reviews.kept_reviews(p, WHO, all_rounds=True)
        self.assertEqual([(k["round"], k["previous_review_sha"]) for k in kept], [(2, "c1")])


class TestTag(unittest.TestCase):
    def test_ai_authored_prs_and_ai_assisted_reviews_are_tagged(self):
        with tempfile.TemporaryDirectory() as d:
            ds = Path(d) / "set"
            (ds / "conversations").mkdir(parents=True)
            (ds / "references").mkdir()
            rows = {"1-rev1": ("Fix X.\n\nAuthored with Claude Code.", "Claude claims this leaks."),
                    "2-rev2": ("Fix Y.", "This is overly defensive.")}
            cases = []
            for cid, (body, review_body) in rows.items():
                (ds / "conversations" / f"{cid}.json").write_text(json.dumps({"body": body}))
                (ds / "references" / f"{cid}.json").write_text(
                    json.dumps({"decision": {"body": ""}, "inline_comments": [{"body": review_body}]}))
                cases.append({"case_id": cid, "conversation": f"set/conversations/{cid}.json"})
            (ds / "cases.jsonl").write_text("".join(json.dumps(c) + "\n" for c in cases))
            counts = select_reviews.annotate(ds, {"2-rev2": {"round": 2, "previous_review_sha": "c1"}})
            tags = {c["case_id"]: c["tags"] for c in map(json.loads, (ds / "cases.jsonl").read_text().splitlines())}
            rounds = {c["case_id"]: c.get("round") for c in map(json.loads, (ds / "cases.jsonl").read_text().splitlines())}
        self.assertEqual(tags, {"1-rev1": ["ai_authored_pr", "ai_assisted_review"], "2-rev2": []})
        self.assertEqual(rounds, {"1-rev1": None, "2-rev2": 2})
        self.assertEqual(counts, {"ai_authored_pr": 1, "ai_assisted_review": 1})


if __name__ == "__main__":
    unittest.main()
