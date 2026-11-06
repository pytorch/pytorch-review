# evalset_3

Recall targets that maintainers confirmed. Each target is an issue a maintainer raised in review of a pytorch/pytorch
pull request and the author fixed; the same maintainer then answered "Do you think this issue should be captured by
the review agent as a blocking issue?" with **Yes**. 21 cases, 25 targets: albanD 19, janeyx99 5, ezyang 1; 18
design, 7 defect.

The maintainers were asked about the targets of `evalset_2/recall/`, one row per maintainer comment (68 rows). Of
the 45 rows answered, 25 were Yes, 16 Unsure and 4 No; only the Yes ones are here.

## Provenance

| Part | Written by |
|---|---|
| `conversations/`: PR title, description and comments as of the review | the PR's participants, verbatim from GitHub |
| `references/`: the maintainer's review | **human**: the maintainer, verbatim from GitHub |
| `targets/`: `maintainer_comment`, `maintainer_comment_url` | **human**: the maintainer, verbatim from GitHub |
| `targets/`: `human_label`, `labeler`, `labeler_note` | **human**: the maintainer who raised the issue |
| `targets/`: `summary`, `type`, `path`, `line` | **AI** |

A target's `summary` is a paraphrase; `maintainer_comment` is what the maintainer said.

## Scoring

A target is found when one of the reviewer's findings raises the same problem: the same root cause at the same place,
or a fix that would resolve it. Since the maintainers said these should block, also report how many were found as
blocking.

## Format

The dataset format of `review_data/`; `conversation` paths are relative to `evalset_3/`.
