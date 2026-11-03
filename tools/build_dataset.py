"""Build a `maintainer`-scored dataset from recent pytorch/pytorch PRs and their human review decisions.

Labels follow what a maintainer did at the reviewed head:
  block  a maintainer's CHANGES_REQUESTED review; the case is the commit that review was on
  clean  a maintainer approved the PR's final head and it was merged with no further commits
Maintainer means triage access or above, as pytorch's scripts/pr_review/fetch_pr_comments.py decides it.
The production review agent posts as github-actions and is never counted as a reviewer.

Each case's conversation is collected with that fetcher and cut before the reviewed head was pushed
(approximated by the head commit's committer date) and before the deciding review, so the review
never sees the decision. The description is the PR's current one, not the one at the cut.

Writes <out>/dataset.json, <out>/cases.jsonl and <out>/conversations/<case>.json, the format
README.md describes. Cases are appended as they are found, so a rerun resumes.

Needs an authenticated gh with read access to pytorch collaborator permissions, run outside any
agent session:
    python3 -m tools.build_dataset \\
        --pytorch ../pytorch --out datasets/recent --per-label 25 \\
        --since 2026-06-01 --until 2026-10-28
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

REPO = "pytorch/pytorch"
# Accounts that act like bots but are not marked as such. github-actions posts the review agent's
# own verdict, so counting it would grade the agent against itself.
AUTOMATED_LOGINS = {"github-actions", "pytorchgreenlight"}
ATTEMPTS = 3

SEARCH_QUERY = """
query($q: String!, $cursor: String) {
  search(query: $q, type: ISSUE, first: 50, after: $cursor) {
    pageInfo { hasNextPage endCursor }
    nodes { ... on PullRequest { number } }
  }
}
"""

DETAIL_QUERY = """
query($number: Int!) {
  repository(owner: "pytorch", name: "pytorch") {
    pullRequest(number: $number) {
      number title isDraft headRefOid baseRefName
      author { login __typename }
      labels(first: 50) { nodes { name } }
      reviews(first: 100) {
        pageInfo { hasNextPage }
        nodes {
          author { login __typename } state body submittedAt
          commit { oid committedDate parents(first: 2) { nodes { oid } } }
        }
      }
    }
  }
}
"""

# The title and description a reviewer saw: today's values, rolled back through their edit history.
# ghstack rewrites the description on every push, so today's can describe tests or fixes added after
# the reviewed head.
TEXT_HISTORY_QUERY = """
query($number: Int!) {
  repository(owner: "pytorch", name: "pytorch") {
    pullRequest(number: $number) {
      title body
      userContentEdits(first: 100) { nodes { editedAt diff } }
      timelineItems(itemTypes: [RENAMED_TITLE_EVENT], first: 100) {
        nodes { ... on RenamedTitleEvent { createdAt previousTitle } }
      }
    }
  }
}
"""


def gh(args: list[str], stdin: str | None = None) -> Any:
    for attempt in range(1, ATTEMPTS + 1):
        proc = subprocess.run(["gh", *args], input=stdin, capture_output=True, text=True, timeout=120)
        if proc.returncode == 0:
            return json.loads(proc.stdout)
        if attempt == ATTEMPTS:
            raise RuntimeError(f"gh {' '.join(args[:3])}: {proc.stderr.strip()}")
        time.sleep(5 * attempt)
    raise AssertionError("unreachable")


def graphql(query: str, **variables: Any) -> dict[str, Any]:
    cmd = ["api", "graphql", "-f", f"query={query}"]
    for key, value in variables.items():
        if value is not None:
            cmd += ["-F", f"{key}={value}"]
    return gh(cmd)["data"]


def load_fetcher(pytorch: Path) -> Any:
    path = pytorch / "scripts/pr_review/fetch_pr_comments.py"
    spec = importlib.util.spec_from_file_location("fetch_pr_comments", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def human(actor: dict[str, Any] | None, fetcher: Any) -> str | None:
    if not actor or actor.get("__typename") != "User":
        return None
    login = actor["login"]
    return None if login in fetcher.NON_HUMAN_LOGINS or login in AUTOMATED_LOGINS else login


def classify(pr: dict[str, Any], fetcher: Any, is_maintainer: Any) -> dict[str, Any] | None:
    """The case a PR yields, or None. Reviews come oldest first, so the last match is the latest."""
    reviews = []
    for r in pr["reviews"]["nodes"]:
        login = human(r["author"], fetcher)
        if login and r["commit"] and r["state"] in ("CHANGES_REQUESTED", "APPROVED"):
            reviews.append({**r, "login": login})
    blocks = [r for r in reviews if r["state"] == "CHANGES_REQUESTED" and is_maintainer(r["login"])]
    if blocks:
        decision = blocks[-1]
        excerpt = " ".join((decision["body"] or "").split())[:200]
        note = f"Maintainer {decision['login']} requested changes at this head" + (f": {excerpt}" if excerpt else "")
        return {"label": "block", "decision": decision, "note": note}
    labels = {lab["name"] for lab in pr["labels"]["nodes"]}
    final = [r for r in reviews if r["commit"]["oid"] == pr["headRefOid"]]
    approvals = [r for r in final if r["state"] == "APPROVED" and is_maintainer(r["login"])]
    if "Merged" in labels and approvals:
        decision = approvals[-1]
        body = "with a review comment" if (decision["body"] or "").strip() else "with an empty review body"
        note = f"Approved at this head by {decision['login']} ({body}) and merged with no further commits"
        return {"label": "clean", "decision": decision, "note": note}
    return None


def text_at(number: int, when: str) -> tuple[str, str]:
    """The PR's title and description as of `when` (ISO UTC). Each edit records the full new
    description, oldest being the original, so the last edit at or before `when` is what a reader saw."""
    pr = graphql(TEXT_HISTORY_QUERY, number=number)["repository"]["pullRequest"]
    edits = sorted(pr["userContentEdits"]["nodes"], key=lambda e: e["editedAt"])
    body = pr["body"] or ""
    if edits:
        if (edits[-1]["diff"] or "") != body:
            raise RuntimeError(f"#{number}: the latest description edit does not match the description")
        seen = [e for e in edits if e["editedAt"] <= when]
        body = (seen[-1] if seen else edits[0])["diff"] or ""
    title = pr["title"]
    for rename in sorted(pr["timelineItems"]["nodes"], key=lambda r: r["createdAt"], reverse=True):
        if rename["createdAt"] > when:
            title = rename["previousTitle"]
    return title, body


def diff_base(pr: dict[str, Any], commit: dict[str, Any]) -> str:
    """Where the reviewed diff starts, as production's compare against the PR's base. A ghstack PR's base
    branch holds the PRs below it, and each head ghstack pushes has that base as its second parent;
    diffing it against main would show the reviewer the lower PRs too."""
    parents = [p["oid"] for p in commit["parents"]["nodes"]]
    if pr["baseRefName"].startswith("gh/") and len(parents) == 2:
        return parents[1]
    return "main"


def cut_conversation(conversation: dict[str, Any], cutoff: str) -> dict[str, Any]:
    items = [i for i in conversation["items"] if i["created_at"] and i["created_at"] < cutoff]
    return {**conversation, "items": items, "omitted_items": conversation.get("omitted_items", 0)}


def iso_minus_second(timestamp: str) -> str:
    moment = datetime.strptime(timestamp, "%Y-%m-%dT%H:%M:%SZ") - timedelta(seconds=1)
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def build_case(number: int, fetcher: Any, is_maintainer: Any, conversations: Path) -> dict[str, Any] | None:
    pr = graphql(DETAIL_QUERY, number=number)["repository"]["pullRequest"]
    if pr["isDraft"] or human(pr["author"], fetcher) is None or pr["reviews"]["pageInfo"]["hasNextPage"]:
        return None
    found = classify(pr, fetcher, is_maintainer)
    if found is None:
        return None
    decision = found["decision"]
    head = decision["commit"]["oid"]
    base = diff_base(pr, decision["commit"])
    merge_base = gh(["api", f"repos/{REPO}/compare/{base}...{head}"])["merge_base_commit"]["sha"]
    cutoff = min(decision["commit"]["committedDate"], iso_minus_second(decision["submittedAt"]))
    conversation = cut_conversation(fetcher.collect(fetcher.gh_api, REPO, number, head), cutoff)
    title, body = text_at(number, decision["submittedAt"])
    conversation.update(title=title, body=fetcher.cap_encoded(body, fetcher.MAX_PR_BODY_BYTES))
    case_id = str(number)
    (conversations / f"{case_id}.json").write_text(fetcher.dumps(conversation))
    return {
        "case_id": case_id,
        "pr": number,
        "head_sha": head,
        "merge_base": merge_base,
        # Paths are relative to datasets/ (README.md); each dataset keeps its own conversations.
        "conversation": f"{conversations.parent.name}/conversations/{case_id}.json",
        "label": found["label"],
        "note": found["note"],
    }


def build_review_case(number: int, login: str, submitted_at: str, fetcher: Any,
                      conversations: Path, case_id: str = "") -> dict[str, Any] | None:
    """A block case at one given review: its head, the conversation before it and the text then shown.

    For reviews that blocked in effect without CHANGES_REQUESTED (the author changed the commented code)."""
    pr = graphql(DETAIL_QUERY, number=number)["repository"]["pullRequest"]
    found = [r for r in pr["reviews"]["nodes"]
             if (r["author"] or {}).get("login") == login and r["submittedAt"] == submitted_at and r["commit"]]
    if not found:
        return None
    review = found[0]
    head = review["commit"]["oid"]
    base = diff_base(pr, review["commit"])
    merge_base = gh(["api", f"repos/{REPO}/compare/{base}...{head}"])["merge_base_commit"]["sha"]
    cutoff = min(review["commit"]["committedDate"], iso_minus_second(submitted_at))
    conversation = cut_conversation(fetcher.collect(fetcher.gh_api, REPO, number, head), cutoff)
    title, body = text_at(number, submitted_at)
    conversation.update(title=title, body=fetcher.cap_encoded(body, fetcher.MAX_PR_BODY_BYTES))
    case_id = case_id or f"{number}-{login}"
    (conversations / f"{case_id}.json").write_text(fetcher.dumps(conversation))
    excerpt = " ".join((review["body"] or "").split())[:200]
    return {
        "case_id": case_id,
        "pr": number,
        "head_sha": head,
        "merge_base": merge_base,
        "conversation": f"{conversations.parent.name}/conversations/{case_id}.json",
        "label": "block",
        "note": f"Maintainer {login} reviewed at this head ({review['state']})" + (f": {excerpt}" if excerpt else ""),
        # The deciding review, for fetch_references.py: it may be COMMENTED, which the label alone can't find.
        "review": {"login": login, "submitted_at": submitted_at},
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pytorch", type=Path, required=True, help="pytorch checkout (for scripts/pr_review)")
    ap.add_argument("--out", type=Path, required=True, help="dataset directory, e.g. datasets/recent")
    ap.add_argument("--per-label", type=int, default=25)
    ap.add_argument("--since", default=(date.today() - timedelta(days=60)).isoformat(),
                    help="earliest PR creation date (YYYY-MM-DD)")
    ap.add_argument("--until", default=date.today().isoformat(), help="latest PR creation date (YYYY-MM-DD)")
    ap.add_argument("--workers", type=int, default=4, help="PRs checked at once (GitHub throttles bursts; keep <= 8)")
    ap.add_argument("--prs", type=Path, help="file of PR numbers to build cases for, instead of searching")
    ap.add_argument("--reviews", type=Path,
                    help="file of '<pr> <login> <submittedAt> [case_id]' lines: a block case at each of those "
                         "reviews (case_id defaults to <pr>-<login>)")
    args = ap.parse_args(argv)
    if args.since > args.until:
        ap.error("--since must not be after --until")

    fetcher = load_fetcher(args.pytorch)
    conversations = args.out / "conversations"
    conversations.mkdir(parents=True, exist_ok=True)
    cases_file = args.out / "cases.jsonl"
    cases = [json.loads(line) for line in cases_file.read_text().splitlines()] if cases_file.exists() else []
    seen = {c["pr"] for c in cases}
    counts = {"block": 0, "clean": 0}
    for c in cases:
        counts[c["label"]] += 1

    roles: dict[str, bool] = {}

    def is_maintainer(login: str) -> bool:
        # Threads may check the same login twice; the answer is the same, so no lock.
        if login not in roles:
            roles[login] = fetcher.is_maintainer(fetcher.gh_api, REPO, login)
        return roles[login]

    def try_build(number: int) -> tuple[int, dict[str, Any] | None]:
        try:
            return number, build_case(number, fetcher, is_maintainer, conversations)
        except (RuntimeError, ValueError, KeyError, TypeError) as e:
            print(f"  skipped #{number}: {e}", file=sys.stderr)
            return number, None

    if args.reviews:
        wanted = [line.split() for line in args.reviews.read_text().splitlines() if line.strip()]
        with cases_file.open("a") as out:
            for number, login, submitted_at, *case_id in wanted:
                try:
                    case = build_review_case(int(number), login, submitted_at, fetcher, conversations, *case_id)
                except (RuntimeError, ValueError, KeyError, TypeError) as e:
                    print(f"  skipped #{number} {login}: {e}", file=sys.stderr)
                    continue
                if case is None:
                    print(f"  no review by {login} at {submitted_at} on #{number}", file=sys.stderr)
                    continue
                out.write(json.dumps(case) + "\n")
                counts["block"] += 1
                print(f"  block: #{number} ({login})", flush=True)
        args.per_label = 0

    if args.prs:
        numbers = [n for n in map(int, args.prs.read_text().split()) if n not in seen]
        with cases_file.open("a") as out, ThreadPoolExecutor(max_workers=args.workers) as pool:
            for number, case in pool.map(try_build, numbers):
                if case is None:
                    print(f"  no case for #{number}", file=sys.stderr)
                    continue
                out.write(json.dumps(case) + "\n")
                out.flush()
                counts[case["label"]] += 1
                print(f"  {case['label']}: #{number}", flush=True)
        args.per_label = 0  # the list replaces the search below

    # GitHub search returns at most 1000 results per query; use narrower windows to reach further back.
    query = f"repo:{REPO} is:pr is:closed -is:draft created:{args.since}..{args.until} sort:created-desc"
    cursor = None
    checked = 0
    # Threads only fetch; counting and writing stay on this thread. A batch may build a few cases past a
    # full label; those are dropped (their conversation files are left unused).
    with cases_file.open("a") as out, ThreadPoolExecutor(max_workers=args.workers) as pool:
        while min(counts.values()) < args.per_label:
            page = graphql(SEARCH_QUERY, q=query, cursor=cursor)["search"]
            numbers = [n["number"] for n in page["nodes"] if n.get("number") and n["number"] not in seen]
            seen.update(numbers)
            for number, case in pool.map(try_build, numbers):
                checked += 1
                if checked % 25 == 0:
                    print(f"  ... checked {checked} PRs (now at #{number})", flush=True)
                if case is None or counts[case["label"]] >= args.per_label:
                    continue
                out.write(json.dumps(case) + "\n")
                out.flush()
                counts[case["label"]] += 1
                print(f"  {case['label']}: #{number}  (block {counts['block']}, clean {counts['clean']})", flush=True)
            if not page["pageInfo"]["hasNextPage"]:
                print("search exhausted; widen --since/--until for more cases", file=sys.stderr)
                break
            cursor = page["pageInfo"]["endCursor"]

    name = args.out.name
    (args.out / "dataset.json").write_text(json.dumps({
        "name": name,
        "scorer": "maintainer",
        "description": f"PRs labeled by maintainer decisions at the reviewed head "
                       f"({counts['block']} block, {counts['clean']} clean; "
                       + (f"reviews listed in {args.reviews.name})." if args.reviews else f"PRs listed in {args.prs.name})." if args.prs else f"created {args.since} to {args.until})."),
        "source": "tools/build_dataset.py",
        "created": date.today().isoformat(),
    }, indent=2) + "\n")
    print(f"wrote {counts['block']} block + {counts['clean']} clean cases to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
