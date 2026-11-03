import csv
import json
import tempfile
import unittest
from pathlib import Path

from review_data.tools import export_labeling


def dataset(root: Path) -> Path:
    ds = root / "set"
    (ds / "issues").mkdir(parents=True)
    (ds / "references").mkdir()
    (ds / "cases.jsonl").write_text(
        "".join(json.dumps({"case_id": c, "pr": n, "review": {"login": "rev1"}}) + "\n"
                for c, n in (("1-rev1", 1), ("2-rev1", 2))))
    issues = [{"id": "h1", "type": "design", "path": "a.py", "line": 10, "summary": "too defensive"},
              {"id": "h2", "type": "question", "path": None, "line": None, "summary": "why this approach?"}]
    for cid in ("1-rev1", "2-rev1"):
        (ds / "issues" / f"{cid}.json").write_text(json.dumps({"issues": issues}))
        (ds / "references" / f"{cid}.json").write_text(json.dumps({
            "decision": {"body": "Why this approach?"},
            "inline_comments": [{"path": "a.py", "line": None, "original_line": 11, "body": "Overly defensive.",
                                 "outdated": True}]}))
    return ds


class TestExport(unittest.TestCase):
    def export(self, show: str) -> list[dict]:
        with tempfile.TemporaryDirectory() as d:
            ds = dataset(Path(d))
            results = Path(d) / "results.json"
            results.write_text(json.dumps({"cases": {"1-rev1": {"caught": ["h1"]}}}))  # 2-rev1 was not run
            out = Path(d) / "labels.csv"
            export_labeling.main(["--dataset", str(ds), "--results", str(results), "--show", show, "--out", str(out)])
            return list(csv.DictReader(out.open()))

    def test_all_lists_every_issue_with_what_the_maintainer_wrote_and_whether_it_was_caught(self):
        rows = self.export("all")
        self.assertEqual([(r["case_id"], r["issue_id"], r["agent_caught"]) for r in rows], [
            ("1-rev1", "h1", "yes"), ("1-rev1", "h2", "no"), ("2-rev1", "h1", "not run"), ("2-rev1", "h2", "not run")])
        self.assertEqual((rows[0]["maintainer_wrote"], rows[0]["acted_on"]), ("Overly defensive.", "yes"))
        # Not anchored to a line: the review body, and no acted-on signal.
        self.assertEqual((rows[1]["maintainer_wrote"], rows[1]["acted_on"]), ("Why this approach?", "no"))
        self.assertEqual(rows[0]["label_real_issue"], "")

    def test_missed_keeps_only_issues_the_run_did_not_catch(self):
        self.assertEqual([(r["case_id"], r["issue_id"]) for r in self.export("missed")], [("1-rev1", "h2")])


if __name__ == "__main__":
    unittest.main()
