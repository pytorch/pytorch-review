# evalset_1

An evaluation set for automated code review on pytorch/pytorch pull requests: does a reviewer find the problems
maintainers found (`recall/`), and does it leave alone the PRs maintainers approved as they were (`precision/`).
Each case is the PR as the maintainer saw it: the code at `head_sha` and the conversation up to that review.

## Provenance

| Part | Written by |
|---|---|
| `*/conversations/`: PR title, description and comments as of the review | the PR's participants, verbatim from GitHub |
| `recall/references/`: the maintainer's review (body, inline comments) | **human**: the maintainer, verbatim from GitHub |
| `precision/` labels: a maintainer approved the PR without substantive comments | **human** approval; "clean" is then decided by fixed rules (below) |
| `recall/targets/`: each issue's summary, type, `adopted` verdict and `evidence` | **AI**: an LLM, not checked by a human |
| `recall/excluded.json`: issues removed from the targets, with a reason | **AI**: an agent's reading of every target, not a human's |

The targets are an LLM's reading of human reviews. Treat a target's `summary` as a paraphrase and the reference as
what the maintainer actually said.

## recall/

64 cases from first reviews by ezyang, albanD, janeyx99 and drisspg on PRs created 2026-01-01 to 2026-09-30, with
88 targets: issues the maintainer raised that the merged code adopted.

How it was built:

1. Select reviews: the reviewer is the first human to review the PR, the review is substantive, and the reviewer
   requested changes or the author later changed the code under one of their comments (284 cases).
2. Keep a case when the PR merged, the automated PR review never reviewed it, and the review does not relay an AI
   reviewer (`ai_assisted_review`, which also catches "AI-assisted" drafts in inline comments): 176 cases.
3. Split each review into distinct issues with type, file and line (LLM).
4. Judge each issue against the diff from the reviewed commit to the merged commit: did the merged code do what the
   maintainer asked? (LLM; `adopted` is yes / partly / no / unclear, with `evidence`.)
5. Targets are the `design` and `defect` issues judged `yes`. Style, question and missing-test issues are not
   targets.
6. Remove targets that relay AI text, need knowledge outside the code (release schedules, people, policy), are naming
   nits, or whose evidence does not show the fix (`excluded.json`, 29 issues).

`cases.jsonl` adds two fields to the usual case format: `split` (`dev` or `test`, by PR, balanced per reviewer: dev
31 cases / 47 targets, test 33 / 41) and `targets` (the target ids, defined in `targets/<case>.json`).

Score recall on targets only: a target is found when one of the reviewer's findings raises the same problem (same
root cause at the same place, or a fix that would resolve it).

## precision/

92 PRs landed June-September 2026, balanced across code areas (each case's `area`; 8 docs/typo-only PRs are tagged
`docs/typo`, the rest 8-15 per area; 29 approvers, none above 14%), 50-1500 lines changed each. A maintainer's first
human review was an approval without substantive comments; nobody requested changes or left substantive comments;
the PR did not change after that approval, landed, was never reverted, and no later commit refers to it. None was
reviewed by the automated PR review.

`split`: `dev` is the 30 PRs (3-4 per code area, docs/typo excluded) used for the first comparisons, `test` the other
62.

Score precision as the share of these PRs a reviewer blocks: any block is a false block.

## Format

Both directories use the dataset format of `review_data/` (`dataset.json`, `cases.jsonl`, `conversations/`, and for
recall `references/`); `conversation` paths are relative to `evalset_1/`. The datasets are copies of pytorch/ciforge's
`scripts/pr_review_eval/datasets/recall_strong` and `clean_approved`, which hold the build scripts.
