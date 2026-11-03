# review_data

Datasets for evaluating automated code review on pytorch/pytorch pull requests, built from what PyTorch
maintainers actually said and what authors then changed, and the tools that build them. Paths below are relative
to the repository root.

## Datasets

These predate the dataset design and live in [`scratch/`](../scratch/README.md); don't build on them.

| Dataset | Cases | What it is |
|---|---|---|
| `scratch/recall_core` | 78 | First reviews by ezyang, albanD and janeyx99 on PRs created June-September 2026, where the reviewer requested changes or the author later changed the code under the reviewer's comments |
| `scratch/recall_rounds` | 132 | The same reviewers and window, every review round (`select_reviews.py --rounds all`): 105 first reviews and 27 later rounds from 109 PRs. Contains every `recall_core` case; it also skips bot accounts that `recall_core`'s selection counted as first reviewers. Split it by PR, since rounds of one PR are related |

Each dataset is a directory with:

- `dataset.json`: `name`, `scorer`, `description`, `source`, `created`.
- `cases.jsonl`: one case per line:
  - `case_id`: `<pr>-<reviewer>`, or `<pr>-<reviewer>-r<N>` for a later review round
  - `pr`
  - `head_sha`: the commit the reviewer saw
  - `merge_base`: where the reviewed diff starts (`git diff <merge_base> <head_sha>`)
  - `conversation`: path, relative to the directory holding the dataset, of what a reviewer sees
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
    --out <dataset dir> [--rounds all]
```

`review_data/tools/build_dataset.py` alone builds `block` / `clean` cases from maintainer decisions in a date window.

## Tests

```
python3 -m unittest discover -s review_data/tools -t .
```
