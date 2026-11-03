"""Build a recall dataset from the first reviews a set of maintainers gave, end to end.

For each listed reviewer, finds the pytorch/pytorch PRs they reviewed in a date window and keeps a PR when:
  - the reviewer is the first human to review or comment on it (pings and bot commands do not count), and
    that first event is a review with inline comments or a body of more than 40 characters, and
  - the reviewer requested changes, or the author later changed the code under one of the reviewer's inline
    comments (GitHub marks the thread outdated).
Each kept review becomes a block case at the commit it was on (build_dataset.py --reviews), its body and inline
comments the reference (fetch_references.py). Cases are tagged `ai_authored_pr` when the PR description as of
the review discloses AI help, and `ai_assisted_review` when the review relays or was written by an AI reviewer,
so results can be split by either.

With --rounds all, each later round by the same reviewer (their first review on a commit they had not reviewed
yet) is also a case, kept by the same rules applied to that round's own comments. Its id is <pr>-<login>-r<N>,
and it records `round` and `previous_review_sha`, the commit the previous round was on; the conversation it is
reviewed with holds the earlier rounds. Cases of one PR are related: split them by PR.

Writes <out>/reviews.txt (the selected `<pr> <login> <submittedAt>` lines) and the dataset format README.md
describes. Needs an authenticated gh, run outside any agent session:
    python3 -m review_data.tools.select_reviews --pytorch ../pytorch \\
        --reviewers ezyang,albanD,janeyx99 --since 2026-06-01 --until 2026-09-30 \\
        --out review_data/datasets/recall_core
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
from typing import Any

from review_data.tools import build_dataset, fetch_references
from review_data.tools.build_dataset import AUTOMATED_LOGINS, REPO, SEARCH_QUERY, graphql

BOTS = AUTOMATED_LOGINS | {"pytorch-bot", "pytorchmergebot", "facebook-github-bot", "pytorchbot"}
SUBSTANTIVE_BODY = 40
# CLAUDE.md is a file name, not a disclosure.
AI_PR = re.compile(r"\b(claude|codex)\b(?!\.md)|\bai[- ]assist|generated (with|by)|copilot|\bcursor\b|gemini|\bllm\b",
                   re.I)
AI_REVIEW = re.compile(r"on behalf of @|astra agent|ai assistan|prepared with ai|\b(claude|codex)\b(?!\.md)", re.I)

DETAIL_QUERY = """
query($number: Int!) {
  repository(owner: "pytorch", name: "pytorch") {
    pullRequest(number: $number) {
      number author { login __typename }
      reviews(first: 100) {
        nodes {
          author { login __typename } state body submittedAt commit { oid }
          comments(first: 100) { nodes { body } }
        }
      }
      comments(first: 100) { nodes { author { login __typename } body createdAt } }
      reviewThreads(first: 100) { nodes { isOutdated comments(first: 1) { nodes { author { login } body } } } }
    }
  }
}
"""


def human(actor: dict[str, Any] | None, pr_author: str | None) -> str | None:
    if not actor or actor.get("__typename", "User") != "User":
        return None
    login = actor.get("login")
    if not login or login == pr_author or login.endswith("[bot]") or login in BOTS:
        return None
    return login


def is_ping(body: str | None) -> bool:
    b = (body or "").strip().lower()
    return "@claude" in b or b.startswith(("@pytorchbot", "@pytorchmergebot", "/"))


def kept_reviews(pr: dict[str, Any], reviewers: set[str], all_rounds: bool = False) -> list[dict[str, Any]]:
    """The kept reviews of `pr`, each {login, submitted_at, why, round, previous_review_sha}.

    Only the first human reviewer's reviews count. A round is their first review on a commit they had not reviewed
    yet; round 1 is kept by the rules in the module docstring, and with `all_rounds` each later round is kept by the
    same rules applied to that round's own comments. `previous_review_sha` is the commit the previous round was on.
    """
    author = (pr.get("author") or {}).get("login")
    events = []
    for r in pr["reviews"]["nodes"]:
        login = human(r.get("author"), author)
        if not login or not r.get("submittedAt"):
            continue
        inline = [c for c in r["comments"]["nodes"] if not is_ping(c["body"])]
        if is_ping(r.get("body")) and not inline and r["state"] == "COMMENTED":
            continue
        events.append((r["submittedAt"], login, r))
    for c in pr["comments"]["nodes"]:
        login = human(c.get("author"), author)
        if login and not is_ping(c.get("body")):
            events.append((c["createdAt"], login, None))
    events.sort(key=lambda e: e[:2])
    if not events:
        return []
    _, login, first = events[0]
    if login not in reviewers or first is None or not first.get("commit"):
        return []
    threads = [t["comments"]["nodes"][0] | {"outdated": t["isOutdated"]} for t in pr["reviewThreads"]["nodes"]
               if t["comments"]["nodes"] and (t["comments"]["nodes"][0].get("author") or {}).get("login") == login
               and not is_ping(t["comments"]["nodes"][0]["body"])]
    rounds, seen = [], set()
    for when, who, r in events:
        if who == login and r is not None and r.get("commit") and r["commit"]["oid"] not in seen:
            seen.add(r["commit"]["oid"])
            rounds.append((when, r))
    kept, previous = [], ""
    for number, (when, r) in enumerate(rounds if all_rounds else rounds[:1], 1):
        if number == 1:
            mine = threads  # every thread this reviewer started, as recall_core was selected
        else:
            bodies = {" ".join(c["body"].split()) for c in r["comments"]["nodes"]}
            mine = [t for t in threads if " ".join(t["body"].split()) in bodies]
        substantive = r["comments"]["nodes"] or len((r.get("body") or "").strip()) > SUBSTANTIVE_BODY
        why = ("requested changes" if r["state"] == "CHANGES_REQUESTED"
               else "author changed the commented code" if any(t["outdated"] for t in mine) else "")
        if substantive and why:
            kept.append({"login": login, "submitted_at": when, "why": why, "round": number,
                         "previous_review_sha": previous})
        previous = r["commit"]["oid"]
    return kept


def search(reviewer: str, since: str, until: str, limit: int) -> list[int]:
    query = f"repo:{REPO} is:pr reviewed-by:{reviewer} created:{since}..{until}"
    numbers, cursor = [], None
    while len(numbers) < limit:
        page = graphql(SEARCH_QUERY, q=query, cursor=cursor)["search"]
        numbers += [n["number"] for n in page["nodes"] if n.get("number")]
        if not page["pageInfo"]["hasNextPage"]:
            break
        cursor = page["pageInfo"]["endCursor"]
    return numbers[:limit]


def annotate(dataset: Path, extra: dict[str, dict[str, Any]]) -> dict[str, int]:
    """Add `tags` to every case from its conversation and reference, and its `extra` fields; returns the count
    per tag."""
    path = dataset / "cases.jsonl"
    cases = [json.loads(line) for line in path.read_text().splitlines()]
    counts = {"ai_authored_pr": 0, "ai_assisted_review": 0}
    for c in cases:
        body = json.loads((dataset.parent / c["conversation"]).read_text()).get("body") or ""
        ref_path = dataset / "references" / f"{c['case_id']}.json"
        ref = json.loads(ref_path.read_text()) if ref_path.exists() else {}
        review = [(ref.get("decision") or {}).get("body") or ""] + [x["body"] for x in ref.get("inline_comments", [])]
        c["tags"] = [t for t, hit in (("ai_authored_pr", AI_PR.search(body)),
                                      ("ai_assisted_review", any(AI_REVIEW.search(t) for t in review))) if hit]
        c.update(extra.get(c["case_id"], {}))
        for t in c["tags"]:
            counts[t] += 1
    path.write_text("".join(json.dumps(c) + "\n" for c in cases))
    return counts


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pytorch", type=Path, required=True, help="pytorch checkout (for scripts/pr_review)")
    ap.add_argument("--reviewers", required=True, help="comma-separated GitHub logins")
    ap.add_argument("--since", required=True, help="earliest PR creation date (YYYY-MM-DD)")
    ap.add_argument("--until", required=True, help="latest PR creation date (YYYY-MM-DD)")
    ap.add_argument("--max-per-reviewer", type=int, default=1000,
                    help="PRs searched per reviewer; GitHub search returns at most 1000, and fewer miss reviews")
    ap.add_argument("--out", type=Path, required=True, help="dataset directory, e.g. review_data/datasets/recall_core")
    ap.add_argument("--workers", type=int, default=4, help="PRs fetched at once (keep <= 8)")
    ap.add_argument("--rounds", choices=("first", "all"), default="first",
                    help="first: each reviewer's first review; all: also their later rounds, each a case with "
                         "`round` and `previous_review_sha`")
    args = ap.parse_args(argv)
    reviewers = [r.strip() for r in args.reviewers.split(",") if r.strip()]

    numbers = sorted({n for r in reviewers for n in search(r, args.since, args.until, args.max_per_reviewer)})
    print(f"{len(numbers)} PRs reviewed by {', '.join(reviewers)}", flush=True)

    def fetch(number: int) -> dict[str, Any]:
        return graphql(DETAIL_QUERY, number=number)["repository"]["pullRequest"]

    all_rounds = args.rounds == "all"
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        picked = [(pr["number"], k) for pr in pool.map(fetch, numbers)
                  for k in kept_reviews(pr, set(reviewers), all_rounds)]
    args.out.mkdir(parents=True, exist_ok=True)
    reviews = args.out / "reviews.txt"
    lines, extra = [], {}
    for n, k in picked:
        # Round 1 keeps the <pr>-<login> id recall_core uses; later rounds are told apart by suffix.
        case_id = f"{n}-{k['login']}" + (f"-r{k['round']}" if k["round"] > 1 else "")
        lines.append(f"{n} {k['login']} {k['submitted_at']} {case_id}\n")
        if all_rounds:
            extra[case_id] = {"round": k["round"], "previous_review_sha": k["previous_review_sha"]}
    reviews.write_text("".join(lines))
    print(f"selected {len(picked)} reviews ({args.rounds} rounds) -> {reviews}", flush=True)

    build_dataset.main(["--pytorch", str(args.pytorch), "--out", str(args.out), "--reviews", str(reviews)])
    fetch_references.main(["--dataset", str(args.out)])
    counts = annotate(args.out, extra)
    n = len((args.out / "cases.jsonl").read_text().splitlines())
    (args.out / "dataset.json").write_text(json.dumps({
        "name": args.out.name,
        "scorer": "maintainer",
        "description": f"{n} {'first reviews' if not all_rounds else 'review rounds'} by {', '.join(reviewers)} on PRs created {args.since} to {args.until} "
                       "where the reviewer requested changes or the author later changed the code under the "
                       "reviewer's comments. Each case is the commit the reviewer saw, with the conversation and "
                       "description as of that review; references/ holds that review's body and inline comments, "
                       "each with GitHub's outdated flag (the author acted on it). Tags: "
                       f"{counts['ai_authored_pr']} ai_authored_pr, {counts['ai_assisted_review']} ai_assisted_review.",
        "source": "review_data/tools/select_reviews.py",
        "created": date.today().isoformat(),
    }, indent=2) + "\n")
    print(f"wrote {n} cases to {args.out} (tags: {counts})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
