# Raw feedback and draft principles

The CSV preserves **original GitHub feedback** alongside **AI-drafted review
principles**. These are candidates for human labeling,
not accepted ground truth. Original messages are never rewritten by distillation.
Some source feedback explicitly discloses AI authorship or assistance; provenance
records those disclosures separately from the authorship of the new drafts.

## Schema

| Column               | Meaning                                                                   |
| -------------------- | ------------------------------------------------------------------------- |
| `pr_number`          | PR number in `pytorch/pytorch`.                                           |
| `pr_summary`         | Title and full description as of the review's submission.                 |
| `current_commit`     | Commit SHA recorded on the submitted review.                              |
| `reviewer`           | GitHub account that posted the original feedback, copied from provenance. |
| `reviewer_comment`   | Verbatim original review body or inline comment.                          |
| `reviewer_principle` | AI-drafted reusable rule, or blank when unsupported.                      |
| `source_url`         | Link to the original GitHub review or inline comment.                     |

There is no base or previous-review commit column. Multiple comments on the same
reviewed commit are separate rows; a nonempty review body is one row. Use a CSV
parser: summaries and feedback can contain quotes, commas, and newlines.

The existing CSV record order is preserved, and provenance row numbers follow
the CSV.

## First batch

The selected PR/reviewer pairs come from ciforge's `recall_core` and `pre_agent`
datasets. All submitted review cycles for those selected reviewers are included.

- 539 entries across 107 PRs: 454 inline comments and 85 review bodies.
- 128 PR/reviewer pairs were collected, covering 21 reviewers; 21 PRs had no
  nonempty feedback from their selected reviewer.
- 336 rows have draft principles. Human labels
  have not been collected. Feedback has not been moved between cycles.

## Drafting and labeling

Principles should be reusable within PyTorch while retaining the concrete
trigger and mechanism, such as `@parametrize`, `DeviceGuard`, or a specific
ownership or calling contract. A draft can combine several concerns from a
single review body; rows are not split during this pass.

Pure questions, open negotiation, approvals, and unsupported inferences have
blank principles. Some terse questions contain concrete requested corrections;
those are drafted when code context or the review thread establishes the issue.
The original messages remain available in every row, including excluded entries.
Retractions are recorded in provenance rather than turned into standing rules.

All nonempty `reviewer_principle` values in this revision are AI drafts.
Labelers should compare them with `reviewer_comment` and
the source before accepting or editing them. Explicitly AI-authored source
findings without a disclosed human endorsement require human adjudication.

## Provenance

`provenance.jsonl` has one record per CSV entry, containing the reviewer,
original source ID and URL, review timestamp, and original file/line location.
`csv_row` counts logical CSV records, starting at 2 after the header. The source
URL and reviewer also appear directly in the CSV. The `reviewer` account identifies
who posted the message; source authorship disclosures are recorded separately,
including when that account posts feedback written by an AI agent.

Provenance additionally records each original comment's checksum, disclosed
source authorship, draft authorship, principle status and reasoning, and whether
a human has reviewed the row. `not_disclosed` source authorship means the message
does not identify its writer beyond the recorded GitHub account. The draft
statuses are `draft`, `needs_human_adjudication`, `needs_context`, `resolved`, and
`excluded`; none of these is a human label.

Feedback bodies are the versions available when collected. Titles and descriptions
are reconstructed from edit history. An inline comment's original commit can
differ from the parent review's commit; that original commit is in provenance.

`manifest.json` records the scope, CSV checksum, draft counts,
and exact original export commit. The raw export's older schema is preserved in
that historical commit; the current sheet separates comments from drafted rules.
The collector and exporter are in that commit under `scripts/pr_review_eval`
in `pytorch/ciforge`. Losslessly compressed source archives, including code hunks
and surrounding replies, are under `scripts/pr_review_eval/datasets/maintainer_raw`.
