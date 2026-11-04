#!/usr/bin/env python3
"""Export stored ClickHouse downvotes on verified public AI reviews to CSV.

Read-only against ClickHouse/GitHub. Writes only the explicitly selected local
CSV and report. Does not commit, schedule collection, or produce human labels.
"""

from __future__ import annotations

import argparse
from collections import Counter
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile
from typing import Any
from urllib.parse import urlparse

REPOSITORY = "pytorch/pytorch"
DATASOURCE = "ceczcsck1b20wb"
REVIEW_AUTHOR = "github-actions[bot]"
REVIEW_MARKER = "<!-- hardened-pr-review -->"
NON_HUMANS = {"facebook-github-bot", "pytorch-bot", "pytorchbot", "pytorchmergebot"}
# The explicit attribution emitted by the assisted-review skill, not a guess
# based on writing style or a comment merely discussing an AI review.
AI_ATTRIBUTION = re.compile(
    r"^\*?\(Posted by [^\n]{1,120}, an AI assistant, on behalf of @[A-Za-z0-9-]+\.\)\*?(?:\s|$)"
)
COLUMNS = [
    "candidate_id", "repository", "pr_number", "comment_kind", "comment_id",
    "review_id", "reviewed_commit", "path", "line", "ai_comment",
    "ai_comment_sha256", "comment_author", "ai_authorship", "authorship_evidence",
    "feedback_id", "feedback_kind", "reaction", "human_feedback", "reactor",
    "reactor_eligibility", "source_url", "observed_at", "comment_updated_at",
    "snapshot_updated_at", "snapshot_comment", "label_status",
]


class CollectionError(RuntimeError):
    pass


def integer(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CollectionError(f"expected positive numeric ID, got {value!r}")
    if isinstance(value, float) and not value.is_integer():
        raise CollectionError("ID must be an integer")
    number = int(value)
    if number <= 0:
        raise CollectionError("ID must be positive")
    return number


def sql_string(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "''") + "'"


def utc_bound(text: str) -> datetime:
    try:
        value = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("use an ISO date or timezone-aware datetime") from exc
    if value.tzinfo is None:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
            raise argparse.ArgumentTypeError("datetime bounds need a timezone")
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def parse_gcx(text: str) -> list[dict]:
    if text.strip() == "No data":
        return []
    try:
        data = json.loads(text)
        columns, values = data["columns"], data["rows"]
        values = [] if values is None else values
        names = [column["name"] for column in columns]
    except (ValueError, TypeError, KeyError) as exc:
        raise CollectionError("gcx did not return its columns/rows JSON contract") from exc
    if not isinstance(values, list) or len(names) != len(set(names)):
        raise CollectionError("invalid gcx table shape")
    if any(not isinstance(row, list) or len(row) != len(names) for row in values):
        raise CollectionError("gcx row width does not match its columns")
    return [dict(zip(names, row)) for row in values]


def query_sql(kind: str, since: datetime, until: datetime, page_size: int,
              cursor: tuple[str, str] | None, prs: list[int], read_cap: int) -> str:
    if kind == "review_comment":
        table, updated, key = "default.pull_request_review_comment", "comment.updated_at", "dynamoKey"
        fields = """comment.id AS comment_id, pull_request.number AS pr_number,
 comment.user.login AS author_login, comment.body AS body,
 comment.html_url AS source_url, comment.pull_request_review_id AS review_id,
 comment.original_commit_id AS reviewed_commit, comment.path AS path,
 comment.line AS line"""
        scope = "repository.full_name = 'pytorch/pytorch'"
        downvotes = "tupleElement(comment.reactions, '-1')"
        pr_expression = "pull_request.number"
    elif kind == "issue_comment":
        table, updated, key = "default.issue_comment", "updated_at", "dynamoKey"
        pr_expression = "toUInt64OrZero(arrayElement(splitByChar('/', issue_url), -1))"
        fields = f"""id AS comment_id, {pr_expression} AS pr_number,
 user.login AS author_login, body, html_url AS source_url,
 0 AS review_id, '' AS reviewed_commit, '' AS path, 0 AS line"""
        scope = "startsWith(issue_url, 'https://api.github.com/repos/pytorch/pytorch/issues/')"
        downvotes = "tupleElement(reactions, '-1')"
    else:
        raise ValueError("unknown comment kind")
    start = since.strftime("%Y-%m-%d %H:%M:%S.%f")
    end = until.strftime("%Y-%m-%d %H:%M:%S.%f")
    predicates = [scope, f"{updated} >= toDateTime64({sql_string(start)}, 9, 'UTC')",
                  f"{updated} < toDateTime64({sql_string(end)}, 9, 'UTC')", f"{downvotes} > 0"]
    if prs:
        predicates.append(f"{pr_expression} IN ({','.join(str(pr) for pr in prs)})")
    if cursor:
        timestamp, event_key = cursor
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:\.\d{1,9})?", timestamp):
            raise CollectionError("invalid ClickHouse cursor timestamp")
        predicates.append(f"({updated}, {key}) > (toDateTime64({sql_string(timestamp)}, 9, 'UTC'), {sql_string(event_key)})")
    # SQL LIMIT is mandatory: gcx's client --limit is unreliable with SETTINGS.
    # Project timestamps as strings so Grafana cannot truncate nanoseconds.
    return (f"SELECT {fields}, {downvotes} AS thumbs_down_snapshot, "
            f"toString({updated}, 'UTC') AS snapshot_updated_at, {key} AS source_event_key\n"
            f"FROM {table}\nWHERE " + "\n AND ".join(predicates)
            + f"\nORDER BY {updated} ASC, {key} ASC\nLIMIT {page_size}"
            + f"\nSETTINGS max_execution_time = 30, max_rows_to_read = {read_cap}")


def run(command: list[str], timeout: int) -> str:
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CollectionError(f"command unavailable or timed out: {command[0]}") from exc
    if result.returncode:
        raise CollectionError(f"{command[0]} failed: {result.stderr.strip()[:600]}")
    return result.stdout


class ClickHouse:
    def __init__(self, wrapper: Path, datasource: str = DATASOURCE, timeout: int = 90):
        self.wrapper, self.datasource, self.timeout = wrapper, datasource, timeout

    def query(self, sql: str) -> list[dict]:
        return parse_gcx(run(["bash", str(self.wrapper), "datasources", "clickhouse", "query",
                              "-d", self.datasource, sql, "-o", "json", "--limit", "0"], self.timeout))


def snapshots(client: ClickHouse, kind: str, args: argparse.Namespace, report: dict):
    cursor = None
    for page in range(args.max_pages):
        rows = client.query(query_sql(kind, args.since, args.until, args.page_size,
                                     cursor, args.prs, args.read_cap))
        report["queries"] += 1
        report["snapshot_events"] += len(rows)
        for row in rows:
            if not {"comment_id", "pr_number", "body", "snapshot_updated_at", "source_event_key"}.issubset(row):
                raise CollectionError("ClickHouse row is missing required source/cursor fields")
            if not isinstance(row["body"], str):
                raise CollectionError("ClickHouse comment text is not a string")
            row = {**row, "comment_kind": kind, "updated_at": row["snapshot_updated_at"]}
            yield row
        if len(rows) < args.page_size:
            return
        next_cursor = (str(rows[-1]["snapshot_updated_at"]), str(rows[-1]["source_event_key"]))
        if cursor is not None and next_cursor <= cursor:
            raise CollectionError("ClickHouse cursor did not advance")
        cursor = next_cursor
    raise CollectionError(f"{kind}: page cap reached; historical coverage is incomplete")


class GitHub:
    def __init__(self, timeout: int = 60, max_pages: int = 50):
        self.timeout, self.max_pages = timeout, max_pages
        self.pr_cache: dict[int, tuple[list[dict], dict[str, str]]] = {}

    def get(self, endpoint: str):
        text = run(["gh", "api", "--hostname", "github.com", "--method", "GET", endpoint], self.timeout)
        try:
            return json.loads(text)
        except ValueError as exc:
            raise CollectionError("GitHub returned invalid JSON") from exc

    def paged(self, endpoint: str, object_field: str | None = None) -> list[dict]:
        result = []
        ids = set()
        for page in range(1, self.max_pages + 1):
            separator = "&" if "?" in endpoint else "?"
            response = self.get(f"{endpoint}{separator}per_page=100&page={page}")
            batch = response.get(object_field) if object_field else response
            if not isinstance(batch, list):
                raise CollectionError("GitHub listing did not return the expected array")
            for item in batch:
                if not isinstance(item, dict):
                    raise CollectionError("GitHub listing contains a non-object")
                identifier = item.get("id")
                if identifier is not None and identifier in ids:
                    raise CollectionError("GitHub pagination repeated an ID; result may have changed")
                ids.add(identifier)
            result.extend(batch)
            if len(batch) < 100:
                return result
        raise CollectionError("GitHub page cap reached; listing is incomplete")

    def pr_context(self, pr: int) -> tuple[list[dict], dict[str, str]]:
        if pr not in self.pr_cache:
            pull = self.get(f"repos/{REPOSITORY}/pulls/{pr}")
            base = pull.get("base", {}).get("repo", {})
            if base.get("full_name") != REPOSITORY or base.get("private") is not False:
                raise CollectionError("source is not a verified public pull request in the requested repository")
            reviews = self.paged(f"repos/{REPOSITORY}/pulls/{pr}/reviews")
            requested = self.paged(f"repos/{REPOSITORY}/pulls/{pr}/requested_reviewers", "users")
            names = {u["login"]: "requested" for u in requested if human_account(u)}
            for review in reviews:
                user = review.get("user") or {}
                if human_account(user) and review.get("submitted_at") and review.get("state") != "PENDING":
                    names[user["login"]] = "requested_and_submitted" if user["login"] in names else "submitted"
            self.pr_cache[pr] = reviews, names
        return self.pr_cache[pr]


def human_account(user: dict) -> bool:
    login = user.get("login") or ""
    return user.get("type") == "User" and bool(login) and login not in NON_HUMANS and not login.endswith("[bot]")


def provenance(comment: dict, kind: str, reviews: list[dict]) -> str | None:
    body, author = comment.get("body"), (comment.get("user") or {}).get("login")
    if not isinstance(body, str) or not body:
        return None
    if kind == "review_comment" and not comment.get("in_reply_to_id") and author == REVIEW_AUTHOR:
        review_id = comment.get("pull_request_review_id")
        if any(review.get("id") == review_id
               and (review.get("user") or {}).get("login") == REVIEW_AUTHOR
               and (review.get("body") or "").startswith(REVIEW_MARKER) for review in reviews):
            return "hardened_review_marker_and_author"
    if AI_ATTRIBUTION.match(body):
        return "explicit_assisted_review_attribution"
    return None


def verify(snapshot: dict, github: GitHub, observed_at: str | None = None) -> list[dict]:
    kind, comment_id, pr = snapshot["comment_kind"], integer(snapshot["comment_id"]), integer(snapshot["pr_number"])
    stem = "pulls" if kind == "review_comment" else "issues"
    endpoint = f"repos/{REPOSITORY}/{stem}/comments/{comment_id}"
    comment = github.get(endpoint)
    if not isinstance(comment, dict) or integer(comment.get("id")) != comment_id:
        raise CollectionError("GitHub comment identity mismatch")
    url = comment.get("html_url") or ""
    parsed = urlparse(url)
    if (parsed.scheme != "https" or parsed.netloc != "github.com"
            or parsed.path.strip("/").split("/")[:4] != ["pytorch", "pytorch", "pull", str(pr)]):
        raise CollectionError("comment URL does not identify the requested PR")
    reviews, reviewers = github.pr_context(pr)
    authored = provenance(comment, kind, reviews)
    if authored is None:
        return []
    reactions = github.paged(endpoint + "/reactions")
    observed_at = observed_at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    records = []
    for reaction in reactions:
        user = reaction.get("user") or {}
        if reaction.get("content") != "-1" or not human_account(user) or user["login"] not in reviewers:
            continue
        body = comment["body"]
        commit = comment.get("original_commit_id") or comment.get("commit_id") or ""
        if commit and not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", commit):
            raise CollectionError("GitHub supplied an invalid original commit")
        records.append(dict(zip(COLUMNS, [
            f"github:{REPOSITORY}:{kind}:{comment_id}", REPOSITORY, pr, kind, comment_id,
            comment.get("pull_request_review_id") or "", commit,
            comment.get("path") or "", comment.get("original_line") or comment.get("line") or "",
            body, hashlib.sha256(body.encode("utf-8")).hexdigest(), comment["user"]["login"],
            "agent", authored, str(integer(reaction.get("id"))), "thumbs_down", "-1", "",
            user["login"], reviewers[user["login"]], url, observed_at,
            comment.get("updated_at") or "", snapshot["updated_at"], snapshot["body"], "pending",
        ])))
    return records


def write_csv(path: Path, rows: list[dict], overwrite: bool = False) -> None:
    if path.exists() and not overwrite:
        raise CollectionError("output exists; choose a new path or explicitly use --overwrite")
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="", dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            writer = csv.DictWriter(stream, fieldnames=COLUMNS, lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)
            stream.flush()
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def export(clickhouse: ClickHouse, github: GitHub, args: argparse.Namespace) -> tuple[list[dict], dict]:
    report = {"repository": REPOSITORY, "since": args.since.isoformat(), "until": args.until.isoformat(),
              "prs": args.prs, "kinds": args.kinds, "snapshot_events": 0, "queries": 0,
              "unique_comments": 0, "duplicates": 0, "verification_attempts": 0,
              "csv_rows": 0, "unknown": [],
              "skipped": {}, "source_complete": False, "verification_complete": True}
    skipped = Counter()
    seen = set()
    discovered = set()
    records: dict[tuple[str, str], dict] = {}
    try:
        for kind in args.kinds:
            for snapshot in snapshots(clickhouse, kind, args, report):
                key = (kind, integer(snapshot["comment_id"]))
                if key in discovered:
                    report["duplicates"] += 1
                else:
                    discovered.add(key)
                    report["unique_comments"] += 1
                # An unmarked historical version must not suppress a later
                # attributed version of the same comment, including next page.
                if kind == "issue_comment" and not AI_ATTRIBUTION.match(snapshot["body"]):
                    skipped["no_explicit_ai_review_attribution"] += 1
                    continue
                if key in seen:
                    continue
                seen.add(key)
                report["verification_attempts"] += 1
                try:
                    rows = verify(snapshot, github)
                    if not rows:
                        skipped["not_ai_review_or_no_current_eligible_downvote"] += 1
                    for row in rows:
                        records[(row["candidate_id"], row["feedback_id"])] = row
                except CollectionError as exc:
                    report["verification_complete"] = False
                    report["unknown"].append({"kind": kind, "comment_id": key[1], "reason": str(exc)})
        report["source_complete"] = True
    except CollectionError as exc:
        report["source_error"] = str(exc)
    rows = [records[key] for key in sorted(records)]
    report["skipped"], report["csv_rows"] = dict(skipped), len(rows)
    return rows, report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gcx-wrapper", required=True, type=Path, help="trusted upstream ci-metrics wrapper")
    parser.add_argument("--since", required=True, type=utc_bound, help="inclusive ISO UTC bound")
    parser.add_argument("--until", required=True, type=utc_bound, help="exclusive ISO UTC bound")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--report", type=Path, help="defaults to OUTPUT.report.json")
    parser.add_argument("--prs", default="", help="optional comma-separated PR cohort")
    parser.add_argument("--kinds", default="review_comment,issue_comment")
    parser.add_argument("--page-size", type=int, default=100)
    parser.add_argument("--max-pages", type=int, default=100)
    parser.add_argument("--read-cap", type=int, default=1_000_000)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)
    if args.until <= args.since:
        parser.error("--until must be after --since")
    if not 1 <= args.page_size <= 1000 or args.max_pages < 1 or args.read_cap < 1:
        parser.error("invalid page/read bounds")
    try:
        args.prs = [integer(int(text)) for text in args.prs.split(",") if text]
    except (ValueError, CollectionError):
        parser.error("--prs needs positive integer PR numbers")
    args.kinds = args.kinds.split(",")
    if not args.kinds or any(kind not in {"review_comment", "issue_comment"} for kind in args.kinds):
        parser.error("unknown --kinds value")
    report_path = args.report or args.output.with_suffix(args.output.suffix + ".report.json")
    if args.output.resolve() == report_path.resolve():
        parser.error("CSV and report paths must differ")
    if args.output.exists() and not args.overwrite:
        parser.error("output exists; choose a fresh output or --overwrite")
    rows, report = export(ClickHouse(args.gcx_wrapper), GitHub(), args)
    complete = report["source_complete"] and report["verification_complete"]
    if complete:
        write_csv(args.output, rows, args.overwrite)
        report["csv_written"] = True
    else:
        report["csv_written"] = False  # Never replace the dataset with an incomplete export.
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: report[key] for key in ["csv_rows", "source_complete", "verification_complete", "csv_written"]}))
    return 0 if complete else 2


if __name__ == "__main__":
    raise SystemExit(main())
