"""Fetch each case's deciding human review, the answer key for finding-level scoring.

For every case in <dataset>/cases.jsonl, finds the latest human review at the case's head_sha with the
state its label implies (CHANGES_REQUESTED for block, APPROVED for clean), or the review a case names
(build_dataset.py --reviews), and writes its body and inline comments to <dataset>/references/<case>.json.
Each inline comment keeps GitHub's `outdated` flag: the code under it changed later, so the author acted on
it. References are never given to the review; it sees only the
conversation.

Needs an authenticated gh, run outside any agent session:
    python3 -m review_data.tools.fetch_references \\
        --dataset review_data/datasets/recall_core
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from review_data.tools.build_dataset import AUTOMATED_LOGINS, graphql

QUERY = """
query($number: Int!) {
  repository(owner: "pytorch", name: "pytorch") {
    pullRequest(number: $number) {
      reviews(first: 100) {
        nodes {
          author { login __typename } state body submittedAt commit { oid }
          comments(first: 100) { nodes { path line originalLine body diffHunk outdated } }
        }
      }
    }
  }
}
"""

STATE = {"block": "CHANGES_REQUESTED", "clean": "APPROVED"}


def pick(reviews: list[dict[str, Any]], case: dict[str, Any]) -> dict[str, Any] | None:
    """The case's deciding review: the one it names (build_dataset.py --reviews), else the latest human
    review of the state its label implies on the reviewed head; reviews come oldest first."""
    if case.get("review"):
        named = [r for r in reviews if (r["author"] or {}).get("login") == case["review"]["login"]
                 and r["submittedAt"] == case["review"]["submitted_at"]]
        return named[0] if named else None
    head, label = case["head_sha"], case["label"]
    matches = [r for r in reviews
               if r["state"] == STATE[label] and r["commit"] and r["commit"]["oid"] == head
               and (r["author"] or {}).get("__typename") == "User"
               and r["author"]["login"] not in AUTOMATED_LOGINS]
    return matches[-1] if matches else None


def reference(case: dict[str, Any], review: dict[str, Any] | None) -> dict[str, Any]:
    if review is None:
        return {"pr": case["pr"], "head_sha": case["head_sha"], "label": case["label"], "decision": None,
                "inline_comments": []}
    return {
        "pr": case["pr"],
        "head_sha": case["head_sha"],
        "label": case["label"],
        "decision": {"author": review["author"]["login"], "state": review["state"], "body": review["body"] or "",
                     "submitted_at": review["submittedAt"]},
        "inline_comments": [
            {"path": c["path"], "line": c["line"], "original_line": c["originalLine"], "body": c["body"],
             "diff_hunk": c["diffHunk"], "outdated": c.get("outdated")}
            for c in review["comments"]["nodes"]
        ],
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dataset", type=Path, required=True)
    args = ap.parse_args(argv)

    refs = args.dataset / "references"
    refs.mkdir(exist_ok=True)
    cases = [json.loads(line) for line in (args.dataset / "cases.jsonl").read_text().splitlines()]
    missing = 0
    for case in cases:
        out = refs / f"{case['case_id']}.json"
        if out.exists():
            continue
        try:
            reviews = graphql(QUERY, number=case["pr"])["repository"]["pullRequest"]["reviews"]["nodes"]
        except (RuntimeError, KeyError, TypeError) as e:
            print(f"  skipped {case['case_id']}: {e}", file=sys.stderr)
            continue
        ref = reference(case, pick(reviews, case))
        missing += ref["decision"] is None
        out.write_text(json.dumps(ref, indent=2) + "\n")
        print(f"  {case['case_id']} ({case['label']}): {len(ref['inline_comments'])} inline comment(s)", flush=True)
    print(f"wrote references for {len(cases)} cases to {refs} ({missing} without a matching review)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
