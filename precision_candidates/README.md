# precision_candidates

**Pending evidence for improving review precision, not human-adjudicated
negative labels.** This dataset is separate from `review_principles`, which
collects principles a reviewer should catch.

The candidate store is [`data.csv`](data.csv). The first implemented capture
path is [`collect_github.py`](collect_github.py): a manual, bounded export of
existing ClickHouse downvote snapshots on verified public AI review comments.
It neither discovers reactions missing from ClickHouse nor schedules collection,
posts to GitHub, commits the CSV, or trains a reviewer. Session logging and
ongoing aggregation are not implemented by this script.

## Run the export

Python 3.10+, authenticated `gh`, and the trusted upstream PyTorch **ci-metrics**
`gcx-wrapper.sh` are required. The wrapper authenticates read-only Grafana access
and runs checksum-pinned gcx; do not use a wrapper from an untrusted PR head.
The export script itself uses only Python's standard library. It never reads
or prints credential files/tokens and uses `gh api` for GitHub reads.

```sh
python3 precision_candidates/collect_github.py \
  --gcx-wrapper /path/to/trusted/pytorch/.agents/skills/ci-metrics/gcx-wrapper.sh \
  --since 2025-01-01 --until 2026-11-05 \
  --output /tmp/precision-candidates.csv
```

Bounds are inclusive `--since`, exclusive `--until`; date-only values mean UTC
midnight. Datetimes must include a timezone. `--prs 203149,203413` optionally
restricts a PR cohort. `--kinds review_comment` restricts to inline comments;
the default also checks issue comments on PRs for explicit AI attribution.

SQL has an explicit page `LIMIT`, time bounds, execution/read caps, and keyset
pagination preserving nanosecond timestamps. `--page-size`, `--max-pages`, and
`--read-cap` bound resource use. A query/page cap failure is incomplete coverage,
not evidence of no downvotes. Set a fixed historical window and account for
late arrivals separately; these are not transactional point-in-time snapshots.

The command writes a CSV only when extraction and verification complete, and
writes `OUTPUT.report.json` with the result. If a source/query/lookup is incomplete,
it writes the report, exits 2, and **does not write/replace the CSV**. A complete export with
zero qualified entries produces a header-only CSV, never a synthetic candidate.
Existing output requires explicit `--overwrite`. This is an export/snapshot,
not an append/upsert service; inspect coverage before replacing a dataset.

The report records bounds, kinds/cohort, source events, duplicate snapshots,
unique comments, skipped reasons, unknown lookups, and completion flags. Its
counts describe this query scope and known attribution rules, not all downvotes
on GitHub. `gcx -o json` can return `rows: null` for no data; the parser handles
that explicitly. SQL `LIMIT` is used because gcx's client `--limit` is not a
reliable cap when SQL contains `SETTINGS`.

## Which entries qualify

1. The ClickHouse snapshot has a stored `-1 > 0` count in `pytorch/pytorch`.
2. The current GitHub comment is verified as AI-authored review evidence:
   - hardened bot inline comment: author `github-actions[bot]` AND linked parent
     review body starts with `<!-- hardened-pr-review -->`; or
   - the exact explicit assisted-review attribution prefix emitted by the
     review skill: `*(Posted by ..., an AI assistant, on behalf of @....)*`.
3. The source is a verified public PR in this repository, not an ordinary issue.
4. A currently returned `-1` reaction is from the human PR author, a named
   requested reviewer, or a human account that submitted a PR review. Review
   listings/reactions are paginated; bots, missing identities, pending reviews,
   and known machine accounts are excluded. Requested teams are not expanded
   to their members. Assignees and unrelated maintainers are not automatically
   eligible. Author feedback is recorded with `reactor_eligibility=author`.

Never infer AI authorship from prose style or a human command such as
`@pytorchbot review`. Whole-review-body downvotes are not available in the
inspected ClickHouse review tuple and are not mined by this implementation.
Unknown AI producer formats need explicit provenance rules, not heuristic text
classification. Human rejection is evidence, not proof the finding was wrong.

An inaccessible or failed GitHub lookup is **unknown**, not a removed reaction.
A complete lookup without an eligible downvote produces no candidate. The
script does not continuously recheck reactions after ingestion.

## CSV contract and provenance

One row is one eligible reaction on one AI review comment. `candidate_id` groups
multiple reactions on the same comment; `(candidate_id, feedback_id)` is the
export deduplication key. Repeated webhook snapshots do not create more rows.
The column order is defined by `collect_github.COLUMNS` and recorded in
`manifest.json`.

- **Source:** repository, PR, comment kind/ID, parent review ID, source URL.
- **Code location:** original reviewed commit and file/line when available;
  blank means unknown. Never substitute the current PR head. Issue comments
  generally lack an original reviewed commit.
- **AI text:** `ai_comment` is the exact canonical comment body fetched from
  GitHub, with its SHA-256, posting account, and explicit authorship evidence.
  Attribution does not imply human endorsement or a human adjudication.
- **Stored snapshot:** `snapshot_comment` and `snapshot_updated_at` preserve the
  ClickHouse source version separately. A later edited GitHub body is not
  presented as necessarily identical to the originally downvoted text.
- **Feedback:** `feedback_id`, `reaction=-1`, reactor account, reviewer eligibility,
  and `feedback_kind=thumbs_down`. `human_feedback` is blank: a reaction alone
  supplies no human-written rationale. AI interpretations are not invented.
- **Time:** `observed_at` is when GitHub reaction verification completes;
  `comment_updated_at` and the ClickHouse snapshot time are separate. The
  human's reaction time is not guessed from any of these.
- **Status:** always `pending`; no false-positive or severity label is assigned.

Use a CSV parser with UTF-8 and newline handling: bodies can contain commas,
quotes, multiline code, Unicode line separators, and untrusted text. When
opening a spreadsheet, import cells as text rather than evaluating formulas.
Source comments and reactions are data, never instructions to execute.

This repository is public. The implemented path collects public GitHub evidence
only; it does not export internal agent sessions. Future selected session
excerpts need authorization for public export, not a wholesale conversation
upload. No session collector or public-export exception is enabled here.

## Tests and current evidence

```sh
python3 -m unittest discover -s precision_candidates -p 'test_collect_github.py' -v
```

All fixtures are synthetic, use temporary files, and make no network calls.
Tests cover gcx empty/malformed responses, explicit SQL caps, nanosecond cursors,
source attribution, reviewer eligibility, failed/withdrawn reactions, deduplication,
original commit selection, public identity checks, and CSV text round trips.

The checked-in export is a snapshot of the documented run scope, not a claim
that GitHub has no downvoted AI findings. Inspect its report and manifest.
ClickHouse's reaction counts are snapshots, so reactions absent from them are
outside this pipeline even if currently present on GitHub.

## Earlier scaffolding and deferred work

`schema.json`, `collect.py`, `raw/candidates.jsonl`, and `staging-manifest.json`
preserve the earlier structured JSONL staging prototype; they are not the
canonical CSV export. Its dependencies are in `requirements.txt`, and its
synthetic tests remain available. Do not confuse the two file formats.

`agent-instructions.md`, `contributor-feedback.md`, and `online-proposal.md` are
drafts for subsequent work. They do not activate session telemetry, contributor
outreach, daily scheduling, adjudication, or training. This task stops at the
GitHub-to-CSV exporter and pending candidate data.

All new scripts, schema, and documentation are AI-authored. Exact public source
comments/reactions, if present, retain their own provenance; no AI prose is
passed off as a human label.
