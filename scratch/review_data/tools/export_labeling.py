"""Export a dataset's maintainer issues as a CSV for human labeling, all of them or only those an agent missed.

One row per issue in <dataset>/issues/<case>.json, with what the maintainer wrote, whether the author acted on it
(an inline comment near its line went outdated), whether the agent run in --results caught it, and empty label
columns for the labeler. `issue_ai_summary` is an LLM's paraphrase; `maintainer_wrote` is the maintainer's own text.

    python3 -m review_data.tools.export_labeling --dataset review_data/datasets/recall_core \\
        --results review_data/results/fanout-acc31ac6427/recall_core.json --show missed --out labels.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any

LABEL_COLUMNS = ["label_real_issue", "label_agent_should_catch", "label_notes"]
COLUMNS = ["case_id", "pr_url", "reviewer", "round", "issue_id", "type", "path", "line", "acted_on",
           "issue_ai_summary", "maintainer_wrote", "agent_caught"] + LABEL_COLUMNS


def near(comment: dict[str, Any], issue: dict[str, Any]) -> bool:
    return comment["path"] == issue.get("path") and issue.get("line") is not None and any(
        x is not None and abs(x - issue["line"]) <= 3 for x in (comment.get("line"), comment.get("original_line")))


def rows(dataset: Path, results: dict[str, Any], show: str) -> list[dict[str, Any]]:
    out = []
    for line in (dataset / "cases.jsonl").read_text().splitlines():
        case = json.loads(line)
        cid = case["case_id"]
        issues_path = dataset / "issues" / f"{cid}.json"
        if not issues_path.exists():
            print(f"  {cid}: no issues/ file, skipped", file=sys.stderr)
            continue
        ref = json.loads((dataset / "references" / f"{cid}.json").read_text())
        run = results["cases"].get(cid)
        for issue in json.loads(issues_path.read_text())["issues"]:
            caught = "not run" if run is None else "yes" if issue["id"] in run["caught"] else "no"
            if show == "missed" and caught != "no":
                continue
            inline = [c for c in ref["inline_comments"] if near(c, issue)]
            wrote = "\n---\n".join(c["body"] for c in inline) or (ref["decision"] or {}).get("body") or ""
            out.append({
                "case_id": cid,
                "pr_url": f"https://github.com/pytorch/pytorch/pull/{case['pr']}",
                "reviewer": case.get("review", {}).get("login", ""),
                "round": case.get("round", 1),
                "issue_id": issue["id"],
                "type": issue["type"],
                "path": issue.get("path") or "",
                "line": issue.get("line") or "",
                "acted_on": "yes" if any(c.get("outdated") for c in inline) else "no",
                "issue_ai_summary": issue["summary"],
                "maintainer_wrote": wrote,
                "agent_caught": caught,
                **dict.fromkeys(LABEL_COLUMNS, ""),
            })
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dataset", type=Path, required=True)
    ap.add_argument("--results", type=Path, required=True, help="an agent run's results file (results/<agent>/<dataset>.json)")
    ap.add_argument("--show", choices=("all", "missed"), default="all",
                    help="all maintainer issues, or only those the agent run did not catch")
    ap.add_argument("--out", type=Path, required=True, help="CSV to write")
    args = ap.parse_args(argv)
    out = rows(args.dataset, json.loads(args.results.read_text()), args.show)
    with args.out.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(out)
    print(f"wrote {len(out)} issues ({args.show}) to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
