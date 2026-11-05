# evalset_2

The second evaluation set for automated code review on pytorch/pytorch pull requests, from the same four maintainers
on both sides: ezyang, albanD, janeyx99 and drisspg, on PRs created 2026-01-01 to 2026-09-30. `recall/` asks whether
a reviewer finds what these maintainers found; `precision/` asks whether it leaves alone the PRs they approved as they
were. It supersedes `evalset_1`, which is kept as the first cut.

**Status: agent-verified, human review pending.** Every row carries `human_review: "pending"`. Until humans have
confirmed the labels, treat them as an agent's judgement.

## Provenance

| Part | Written by |
|---|---|
| `*/conversations/`: PR title, description and comments as of the review | the PR's participants, verbatim from GitHub |
| `recall/references/`: the maintainer's review | **human**: the maintainer, verbatim from GitHub |
| precision labels' starting point: a maintainer approved the PR without substantive comments | **human** approval |
| `recall/targets/`: summary, type, `adopted` and `adoption_evidence`, `stage2`, `stage3`, `stage3_reason`, `status` | **AI** |
| `precision/cases.jsonl`: `stage1_flags`, `stage2*`, `stage3*`, `real_defect_claims`, `status` | **AI** |
| `recall/excluded.json` | **AI** |

"Stage 2" is one agent's reading of every item; "stage 3" is an independent agent that did not see stage 2's
decisions. A target's `summary` is a paraphrase; the reference is what the maintainer said.

## recall/

53 cases, 69 targets: issues the maintainer raised that the merged code adopted.

1. First reviews by the four maintainers that requested changes or whose comments the author acted on (284), kept
   when the PR merged, the automated PR review never reviewed it, and the review does not relay an AI reviewer (176).
2. Each review is split into issues (LLM), and each issue is judged against the diff from the reviewed commit to the
   merged commit (LLM): did the merged code do what was asked?
3. Candidates are the adopted `design` and `defect` issues (117). Stage 2 and stage 3 then decided, per candidate,
   whether it is a fair target: a real issue, adopted, and catchable from the code alone.
4. Rule applied to their disagreements: specific design feedback the author adopted is a target; vague remarks
   ("this is overly defensive"), questions and nits are not, even when adopted.

`status` is `agreed` (both stages kept it, 43) or `kept_by_rule` (stage 3 dropped it as a design preference, kept by
rule 4, 26). Each target links to the maintainer's comment (`maintainer_comment_url`). `excluded.json` lists the other
48 candidates and why, including 6 `borderline: pending decision`.

`split` is by PR, balanced per reviewer: dev 29 cases, test 24.

Score recall on targets only: a target is found when one of the reviewer's findings raises the same problem. Many
targets are conditional blockers: whether they block depends on a fact the maintainer left for the author to check
("is this function used?", "any perf impact?"). So score whether the point was raised, not whether it was graded
blocking.

## precision/

66 PRs the four maintainers approved as they were: the first human event was one of them approving the PR's final head
without substantive comments, nobody else requested changes or commented, the PR merged, its reviewed diff changed
50-1500 lines, no later commit refers to it or reverts it, and the automated PR review never reviewed it.
`approved_by` names the approver.

Then:

1. Stage 1 (scripts): flags a later commit on main within 30 days that rewrote lines the PR added, leftover markers
   ("TODO ... before merging"), and reverts by title.
2. Stage 2: an agent read the stage-1 flags and checked an automated reviewer's blocking findings against the code
   at the reviewed commit; a confirmed real defect the PR introduced makes it `not_clean`.
3. Stage 3: an independent agent decided clean / not_clean / unsure for each PR from the code, blind to stage 2.

`status`: `clean` when both stages say clean (56), `not_clean` when both say so (2), `disputed` otherwise (8). For
the disputed PRs the open question is mostly scope, not existence: whether a real but docs-only or niche-path problem
makes a PR not clean.

`split` is by PR, balanced per approver: dev 32, test 34.

Score precision as the share of `clean` PRs a reviewer blocks. Report `disputed` PRs separately until they are
resolved.

## Format

The dataset format of `review_data/`; `conversation` paths are relative to `evalset_2/`. Built with pytorch/ciforge's
`select_reviews.py` (including `--clean`).
