# pytorch-review

Data and tools for evaluating automated code review on pytorch/pytorch pull requests.

- [`review_principles/`](review_principles/): human-annotated ground truth for generalizable review rules.
  [`raw/`](review_principles/raw/) contains the initial human feedback, source links, and provenance,
  ready for principle distillation and labeling.
- [`scratch/review_data/`](scratch/review_data/): an earlier dataset of mined maintainer reviews and the tools that
  built it, from before the current design; kept for reference only.
