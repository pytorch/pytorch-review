# Raw review data

First collection step for the maintainer review principles dataset. The CSV
contains **original human feedback**; principle distillation and severity
labeling come later. Questions, negotiation, praise, and duplicate concerns are
still present, so these rows are candidates for labeling.

## Schema

| Column             | Meaning                                                   |
| ------------------ | --------------------------------------------------------- |
| `pr_number`        | PR number in `pytorch/pytorch`.                           |
| `pr_summary`       | Title and full description as of the review's submission. |
| `current_commit`   | Commit SHA recorded on the submitted review.              |
| `review_principle` | Verbatim human review body or inline comment for now.     |
| `severity`         | Blank, for labelers to fill in.                           |
| `source_url`       | Link to the original GitHub review or inline comment.     |

There is no base or previous-review commit column. Multiple comments on the same
reviewed commit are separate rows; a nonempty review body is one row. Use a CSV
parser: summaries and feedback can contain quotes, commas, and newlines.

## First batch

The selected PR/reviewer pairs come from ciforge's `recall_core` and `pre_agent`
datasets. All submitted review cycles for those selected reviewers are included.

- 539 entries across 107 PRs: 454 inline comments and 85 review bodies.
- 128 PR/reviewer pairs were collected, covering 21 reviewers; 21 PRs had no
  nonempty feedback from their selected reviewer.
- Severity is blank and feedback has not been rewritten or moved between cycles.

## Provenance

`provenance.jsonl` has one record per CSV entry, containing the reviewer,
original source ID and URL, review timestamp, and original file/line location.
`csv_row` counts logical CSV records, starting at 2 after the header. The source
URL also appears directly in the CSV.

Feedback bodies are the versions available when collected. Titles and descriptions
are reconstructed from edit history. An inline comment's original commit can
differ from the parent review's commit; that original commit is in provenance.

`manifest.json` records the scope, CSV checksum, and exact ciforge export commit.
The collector and exporter are in that commit under `scripts/pr_review_eval`
in `pytorch/ciforge`. Losslessly compressed source archives, including code hunks
and surrounding replies, are under `scripts/pr_review_eval/datasets/maintainer_raw`.
