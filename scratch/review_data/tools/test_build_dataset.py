import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from review_data.tools import build_dataset

FETCHER = SimpleNamespace(NON_HUMAN_LOGINS={"pytorchmergebot"})
MAINTAINERS = {"maint1", "maint2"}


def review(login: str, state: str, oid: str, at: str = "2026-08-01T10:00:00Z", body: str = "",
           typename: str = "User") -> dict:
    return {"author": {"login": login, "__typename": typename}, "state": state, "body": body, "submittedAt": at,
            "commit": {"oid": oid, "committedDate": "2026-08-01T09:00:00Z", "parents": {"nodes": [{"oid": "p1"}]}}}


def pr(reviews: list[dict], head: str = "c3", merged: bool = True) -> dict:
    return {"headRefOid": head, "baseRefName": "main", "labels": {"nodes": [{"name": "Merged"}] if merged else []},
            "reviews": {"nodes": reviews}}


def classify(p: dict) -> dict | None:
    return build_dataset.classify(p, FETCHER, MAINTAINERS.__contains__)


class TestClassify(unittest.TestCase):
    def test_block_is_the_latest_maintainer_change_request(self):
        found = classify(pr([review("maint1", "CHANGES_REQUESTED", "c1", body="first"),
                             review("maint2", "CHANGES_REQUESTED", "c2", body="second ask")]))
        self.assertEqual(found["label"], "block")
        self.assertEqual(found["decision"]["commit"]["oid"], "c2")
        self.assertIn("maint2 requested changes at this head: second ask", found["note"])

    def test_block_wins_over_a_later_approval(self):
        found = classify(pr([review("maint1", "CHANGES_REQUESTED", "c1"), review("maint2", "APPROVED", "c3")]))
        self.assertEqual(found["label"], "block")

    def test_agent_bots_and_non_maintainers_never_decide(self):
        self.assertIsNone(classify(pr([review("github-actions", "CHANGES_REQUESTED", "c3"),
                                       review("pytorchmergebot", "CHANGES_REQUESTED", "c3"),
                                       review("some-bot", "CHANGES_REQUESTED", "c3", typename="Bot"),
                                       review("contributor", "CHANGES_REQUESTED", "c3")], merged=False)))

    def test_clean_needs_a_maintainer_approval_on_the_final_head(self):
        found = classify(pr([review("maint1", "APPROVED", "c3", body="LGTM")]))
        self.assertEqual(found["label"], "clean")
        self.assertIn("with a review comment", found["note"])
        self.assertIsNone(classify(pr([review("maint1", "APPROVED", "c2")])))  # more commits after it
        self.assertIsNone(classify(pr([review("contributor", "APPROVED", "c3")])))

    def test_clean_needs_merged(self):
        self.assertIsNone(classify(pr([review("maint1", "APPROVED", "c3")], merged=False)))


class TestDiffBase(unittest.TestCase):
    def test_ghstack_heads_diff_against_their_base_and_others_against_main(self):
        ghstack_head = {"parents": {"nodes": [{"oid": "previous-head"}, {"oid": "stack-base"}]}}
        self.assertEqual(build_dataset.diff_base({"baseRefName": "gh/dev/12/base"}, ghstack_head), "stack-base")
        self.assertEqual(build_dataset.diff_base({"baseRefName": "main"}, ghstack_head), "main")
        self.assertEqual(build_dataset.diff_base({"baseRefName": "gh/dev/12/base"},
                                                 {"parents": {"nodes": [{"oid": "only"}]}}), "main")


class TestConversationCut(unittest.TestCase):
    def test_keeps_only_items_before_the_cutoff(self):
        conversation = {"title": "t", "items": [{"created_at": "2026-08-01T08:00:00Z"},
                                                {"created_at": "2026-08-01T09:00:00Z"},
                                                {"created_at": ""}]}
        cut = build_dataset.cut_conversation(conversation, "2026-08-01T09:00:00Z")
        self.assertEqual(cut["items"], [{"created_at": "2026-08-01T08:00:00Z"}])
        self.assertEqual(cut["title"], "t")

    def test_iso_minus_second(self):
        self.assertEqual(build_dataset.iso_minus_second("2026-08-01T00:00:00Z"), "2026-07-31T23:59:59Z")


def history(body: str, edits: list[tuple[str, str]], renames: list[tuple[str, str]] = (), title: str = "now") -> dict:
    return {"repository": {"pullRequest": {
        "title": title, "body": body,
        "userContentEdits": {"nodes": [{"editedAt": at, "diff": text} for at, text in edits]},
        "timelineItems": {"nodes": [{"createdAt": at, "previousTitle": old} for at, old in renames]}}}}


class TestTextAt(unittest.TestCase):
    def text_at(self, data: dict, when: str) -> tuple[str, str]:
        original = build_dataset.graphql
        try:
            build_dataset.graphql = lambda query, **kw: data
            return build_dataset.text_at(7, when)
        finally:
            build_dataset.graphql = original

    def test_rolls_back_description_edits_and_renames_made_after_the_review(self):
        data = history("v3", [("2026-08-03T00:00:00Z", "v3"), ("2026-08-01T00:00:00Z", "v1"),
                              ("2026-08-02T00:00:00Z", "v2")],
                       renames=[("2026-08-02T12:00:00Z", "middle"), ("2026-08-01T12:00:00Z", "first")])
        self.assertEqual(self.text_at(data, "2026-08-02T06:00:00Z"), ("middle", "v2"))
        self.assertEqual(self.text_at(data, "2026-07-01T00:00:00Z"), ("first", "v1"))  # before any edit: original
        self.assertEqual(self.text_at(data, "2026-09-01T00:00:00Z"), ("now", "v3"))

    def test_never_edited_keeps_todays_text(self):
        self.assertEqual(self.text_at(history("b", []), "2026-08-01T00:00:00Z"), ("now", "b"))

    def test_history_that_does_not_end_at_todays_text_is_an_error(self):
        with self.assertRaises(RuntimeError):
            self.text_at(history("b", [("2026-08-01T00:00:00Z", "summary only")]), "2026-08-02T00:00:00Z")


class TestBuildCase(unittest.TestCase):
    def test_writes_a_cut_conversation_under_the_dataset_and_points_to_it_from_datasets(self):
        detail = {"repository": {"pullRequest": {
            "isDraft": False, "author": {"login": "dev", "__typename": "User"},
            "reviews": {"pageInfo": {"hasNextPage": False},
                        "nodes": [review("maint1", "CHANGES_REQUESTED", "c2", at="2026-08-02T10:00:00Z")]},
            **{k: v for k, v in pr([]).items() if k != "reviews"}}}}
        conversation = {"title": "t", "body": "b", "pr_author": "dev", "items": [
            {"created_at": "2026-08-01T08:00:00Z"}, {"created_at": "2026-08-02T10:00:00Z"}]}
        fetcher = SimpleNamespace(NON_HUMAN_LOGINS=set(), gh_api=None, dumps=json.dumps, MAX_PR_BODY_BYTES=100,
                                  cap_encoded=lambda text, limit: text[:limit],
                                  collect=lambda api, repo, number, head: conversation)
        # The description was rewritten after the deciding review (2026-08-02T10:00).
        texts = history("b, now with a test", [("2026-08-01T00:00:00Z", "b"),
                                               ("2026-08-03T00:00:00Z", "b, now with a test")])
        originals = build_dataset.graphql, build_dataset.gh
        try:
            build_dataset.graphql = lambda query, **kw: texts if "userContentEdits" in query else detail
            build_dataset.gh = lambda args: {"merge_base_commit": {"sha": "m" * 40}}
            with tempfile.TemporaryDirectory() as d:
                conversations = Path(d) / "mine" / "conversations"
                conversations.mkdir(parents=True)
                case = build_dataset.build_case(7, fetcher, MAINTAINERS.__contains__, conversations)
                self.assertEqual(case["conversation"], "mine/conversations/7.json")
                self.assertEqual((case["label"], case["head_sha"], case["merge_base"]), ("block", "c2", "m" * 40))
                written = json.loads((Path(d) / case["conversation"]).read_text())
                # Cut at the head commit's date (2026-08-01T09:00), before the deciding review.
                self.assertEqual(written["items"], [{"created_at": "2026-08-01T08:00:00Z"}])
                self.assertEqual(written["body"], "b")  # the description the reviewer saw
        finally:
            build_dataset.graphql, build_dataset.gh = originals


class TestMain(unittest.TestCase):
    def test_fills_both_labels_resumes_and_writes_the_dataset_format(self):
        pages = [{"pageInfo": {"hasNextPage": True, "endCursor": "p1"}, "nodes": [{"number": n} for n in range(1, 20)]},
                 {"pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": [{"number": n} for n in range(20, 40)]}]

        def fake_build(number, fetcher, is_maintainer, conversations):
            label = "block" if number % 5 == 0 else "clean" if number % 2 == 0 else None
            return label and {"case_id": str(number), "pr": number, "head_sha": "h", "merge_base": "m",
                              "conversation": f"conversations/{number}.json", "label": label, "note": "n"}

        originals = build_dataset.graphql, build_dataset.load_fetcher, build_dataset.build_case
        try:
            build_dataset.load_fetcher = lambda path: SimpleNamespace(is_maintainer=lambda *a: True, gh_api=None)
            build_dataset.build_case = fake_build
            with tempfile.TemporaryDirectory() as d:
                out = Path(d) / "mine"
                (out).mkdir()
                (out / "cases.jsonl").write_text(json.dumps({"case_id": "5", "pr": 5, "label": "block"}) + "\n")
                calls = iter(pages)
                build_dataset.graphql = lambda query, **kw: {"search": next(calls)}
                build_dataset.main(["--pytorch", d, "--out", str(out), "--per-label", "3", "--workers", "4",
                                    "--since", "2026-06-01", "--until", "2026-08-31"])
                cases = [json.loads(line) for line in (out / "cases.jsonl").read_text().splitlines()]
                labels = [c["label"] for c in cases]
                self.assertEqual((labels.count("block"), labels.count("clean")), (3, 3))
                self.assertEqual(len({c["pr"] for c in cases}), 6)  # the resumed #5 is not fetched again
                meta = json.loads((out / "dataset.json").read_text())
                self.assertEqual((meta["name"], meta["scorer"]), ("mine", "maintainer"))
        finally:
            build_dataset.graphql, build_dataset.load_fetcher, build_dataset.build_case = originals

    def test_a_reviews_line_may_name_its_case_id(self):
        calls = []

        def fake_build(number, login, submitted_at, fetcher, conversations, case_id=""):
            calls.append((number, login, case_id))
            return {"case_id": case_id or f"{number}-{login}", "pr": number, "label": "block"}

        originals = build_dataset.load_fetcher, build_dataset.build_review_case
        try:
            build_dataset.load_fetcher = lambda path: SimpleNamespace(is_maintainer=lambda *a: True, gh_api=None)
            build_dataset.build_review_case = fake_build
            with tempfile.TemporaryDirectory() as d:
                out, reviews = Path(d) / "mine", Path(d) / "reviews.txt"
                reviews.write_text("7 rev1 2026-08-01T10:00:00Z\n7 rev1 2026-08-03T10:00:00Z 7-rev1-r2\n")
                build_dataset.main(["--pytorch", d, "--out", str(out), "--reviews", str(reviews)])
                ids = [json.loads(line)["case_id"] for line in (out / "cases.jsonl").read_text().splitlines()]
        finally:
            build_dataset.load_fetcher, build_dataset.build_review_case = originals
        self.assertEqual(calls, [(7, "rev1", ""), (7, "rev1", "7-rev1-r2")])
        self.assertEqual(ids, ["7-rev1", "7-rev1-r2"])

    def test_rejects_an_inverted_window(self):
        with self.assertRaises(SystemExit):
            build_dataset.main(["--pytorch", "/x", "--out", "/tmp/x", "--since", "2026-09-01", "--until", "2026-08-01"])


if __name__ == "__main__":
    unittest.main()
