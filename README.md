# pytorch-review

Data and tools for evaluating automated code review on pytorch/pytorch pull requests.

- [`review_principles/`](review_principles/): human-annotated ground truth for generalizable review rules.
  [`raw/`](review_principles/raw/) preserves the original feedback with source links and provenance,
  alongside AI-drafted principles awaiting human labeling.
- [`precision_candidates/`](precision_candidates/): unadjudicated human disapproval of AI review
  candidates, with a provenance-preserving schema, local staging tool, and online collection proposal.
  This is not a negative-label dataset and no production collector is enabled.
- [`scratch/review_data/`](scratch/review_data/): an earlier dataset of mined maintainer reviews and the tools that
  built it, from before the current design; kept for reference only.
