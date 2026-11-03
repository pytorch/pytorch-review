# review_data

> **Superseded.** This predates the design in "Dataset collection for maintainer reviews in PyTorch"
> (https://docs.google.com/document/d/1fM3A5WaBZMIDvtaB0A13sZaag2qngK_EoiKeJXLMrQI). Its cases are whole maintainer
> reviews, unlabeled, with no severity or generalized principle, so they don't match the design's unit of data.
> `select_reviews.py` is a copy of pytorch/ciforge's `scripts/pr_review_eval/select_reviews.py`, which is the
> maintained one. Kept for reference; don't build on it.

Datasets for evaluating automated code review on pytorch/pytorch pull requests, built from what PyTorch
maintainers actually said and what authors then changed, and the tools that build them. Paths below are relative
to `scratch/`; run the commands from there.

## Datasets

| Dataset | Cases | What it is |
|---|---|---|
| `review_data/datasets/recall_core` | 78 | First reviews by ezyang, albanD and janeyx99 on PRs created June-September 2026, where the reviewer requested changes or the author later changed the code under the reviewer's comments |
| `review_data/datasets/recall_rounds` | 132 | The same reviewers and window, every review round (`select_reviews.py --rounds all`): 105 first reviews and 27 later rounds from 109 PRs. Contains every `recall_core` case; it also skips bot accounts that `recall_core`'s selection counted as first reviewers. Split it by PR, since rounds of one PR are related |

Each dataset is a directory with:

- `dataset.json`: `name`, `scorer`, `description`, `source`, `created`.
- `cases.jsonl`: one case per line:
  - `case_id`: `<pr>-<reviewer>`, or `<pr>-<reviewer>-r<N>` for a later review round
  - `pr`
  - `head_sha`: the commit the reviewer saw
  - `merge_base`: where the reviewed diff starts (`git diff <merge_base> <head_sha>`)
  - `conversation`: path, relative to `review_data/datasets/`, of what a reviewer sees
  - `label`: `block`
  - `note`
  - `review`: the reviewer's login and the review's time
  - `tags` (when built by `select_reviews.py`): `ai_authored_pr` (the description discloses AI help) and
    `ai_assisted_review` (the review relays or was written by an AI reviewer)
  - for later rounds, `round` and `previous_review_sha` (the commit the previous round was on)
- `conversations/<case>.json`: the PR's title and description as of the review, and the comments posted before it,
  in the format of pytorch's `scripts/pr_review/fetch_pr_comments.py`. This is the input a reviewer gets, with the
  code at `head_sha`.
- `references/<case>.json`: the maintainer's review, the answer key: its body, state and inline comments (path,
  line, text, diff hunk), each with GitHub's `outdated` flag, set when the code under the comment changed later,
  so the author acted on it. Never give these to the reviewer under test.

All content comes from public pull requests on github.com/pytorch/pytorch.

## Building a dataset

`review_data/tools/select_reviews.py` builds a dataset end to end from named maintainers' reviews. For each reviewer it finds
the PRs they reviewed in a date window and keeps a PR when the reviewer was the first human to review it, with a
substantive review that requested changes or whose comments the author acted on. It then builds each case
(`review_data/tools/build_dataset.py --reviews`), fetches the references (`review_data/tools/fetch_references.py`) and tags the cases.
`--rounds all` also makes each later review round by the same reviewer a case.

It needs an authenticated `gh` and a pytorch/pytorch checkout at a recent main (for its
`scripts/pr_review/fetch_pr_comments.py`):

```
python3 -m review_data.tools.select_reviews --pytorch ../pytorch \
    --reviewers ezyang,albanD,janeyx99 --since 2026-06-01 --until 2026-09-30 \
    --out review_data/datasets/<name> [--rounds all]
```

`review_data/tools/build_dataset.py` alone builds `block` / `clean` cases from maintainer decisions in a date window.

## Human labeling

`datasets/recall_core/issues/<case>.json` holds the distinct issues each maintainer review raised, extracted once
by an LLM and frozen (`id`, `type`, `path`, `line`, `summary`; AI-written), so agent results and labels refer to the
same issues. `results/fanout-acc31ac6427/recall_core.json` records which of them one run of pytorch's PR review
agent with fan-out caught (86 of 167; two cases were not run). `tools/export_labeling.py` writes them as a CSV for
human labeling: the AI summary, what the maintainer wrote, whether the author acted on it, whether the agent caught
it, and empty label columns; `--show missed` keeps only the issues the agent did not catch:

```
python3 -m review_data.tools.export_labeling --dataset review_data/datasets/recall_core \
    --results review_data/results/fanout-acc31ac6427/recall_core.json --show missed --out labels.csv
```

## Tests

```
python3 -m unittest discover -s review_data/tools -t .
```
