# scratch

Datasets built before the design in "Dataset collection for maintainer reviews in PyTorch"
(https://docs.google.com/document/d/1fM3A5WaBZMIDvtaB0A13sZaag2qngK_EoiKeJXLMrQI). They are whole maintainer reviews
mined by `review_data/tools/select_reviews.py`, unlabeled, with no severity or generalized principle, so they do not
match the design's unit of data. Kept for reference; don't build on them.

The format is described in [`review_data/README.md`](../review_data/README.md); `conversation` paths in
`cases.jsonl` are relative to this directory.
