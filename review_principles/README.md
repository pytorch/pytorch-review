# review_principles

Human-annotated ground truth for generalizable code review rules in pytorch/pytorch, used to run evals on and
hill climb reviewer setups. It is not fed directly into a review agent.

Design: [Dataset collection for maintainer reviews in PyTorch](https://docs.google.com/document/d/1fM3A5WaBZMIDvtaB0A13sZaag2qngK_EoiKeJXLMrQI).

Each item is one review principle that should fire on a PR at a particular point in time:

- the codebase tree before and after (the commit of the previous review and the reviewed commit)
- the PR summary at the time of review
- the principle, with a suggested line number, labeled as human- or agent-written
- severity: P0 (must fix), P1 (better for the codebase), P2 (nits, preferences)

The same tree can show up in more than one item.

## Raw collection

[`raw/`](raw/) contains the initial collection of 539 human feedback entries,
with source URLs and provenance. These entries await principle distillation and
human labeling; severity is blank. See [the raw collection README](raw/README.md)
for the current CSV schema and collection scope.
