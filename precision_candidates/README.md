# precision_candidates

**Collection-stage evidence for improving review precision, not a negative-label
or ground-truth dataset.** This dataset is separate from `review_principles`,
which collects principles a reviewer should catch. Downstream training must
adjudicate these observations before using them.

This initial version contains **zero observations**. It defines the contract,
provides a local staging tool, and proposes the two online collectors. No
collector, schedule, production service, or training job is enabled.

## Unit of data

One row is **one version of a human feedback event on one AI candidate**.
`candidate_id` groups multiple events about the same original finding. Repeated
collection of an unchanged event does not create another row. Different humans,
a correction, or a reaction removal can create separate events, but training
must group them by candidate rather than counting each vote as another example.

A candidate may be an individual `finding` or a `review_body`. A thumbs-down on
a body containing several findings is review-level evidence, not a negative
label for every finding. Do not split it without explicit identifying feedback.

The full machine-readable contract is [`schema.json`](schema.json):

- **Source:** collector kind, stable underlying `feedback_id`, explicit
  `event_version`, `supersedes_record_id`, source event reference, evidence URL,
  visibility, source occurrence time when known, observation time, and collector
  version. First observations use version 1 and no predecessor; later versions
  reference the prior record for the same candidate/feedback ID. This links a
  removal to one reaction without parsing opaque IDs or guessing event order.
- **Context:** repository, PR number, original base/reviewed commits, PR summary,
  immutable snapshot reference, completeness, and explicitly missing context.
- **Candidate:** verbatim AI text and checksum, scope, file/line, producer,
  actual model/instruction version when known, and generation timestamp.
- **Human feedback:** exact human words, a `-1` reaction, or a verbatim human
  correction; role and active/removed reaction state. Never an AI paraphrase
  attributed to a human. Do not infer that an account is human just from a
  username; collectors must exclude known bots or mark the evidence unresolved.
- **AI assessment:** optional, explicitly AI-authored category/notes. These
  never replace original text or become a human adjudication.
- **Label status:** always `pending` in this raw dataset.

Use `null` for unknown metadata. `context.status=exact` requires original base
and reviewed commits, original PR summary, and a snapshot reference preserving
all review-visible input (including supplied conversation). When any of that
cannot be recovered, retain the event as `partial`/`unavailable` and list the
missing fields. A source commit alone does not prove the full input is exact.
Never substitute the latest PR head for the commit the comment reviewed.
A labeler later needs code at the original revision to decide what was valid.

## Collection eligibility

Collect explicit thumbs-down, rejection, substantive human correction, or
pushback directed at a specific surfaced AI candidate. Do **not** collect mere
silence, unexplained non-posting, a cancelled review, an agent's self-critique,
or an unlinked conversation as a human negative.

Examples of useful evidence are "already handled by the caller" or "don't post
this; the input is bounded." "Won't fix," resolved threads, and disagreement
are evidence to adjudicate, not proof of a false positive. A valid comment may
have been rejected for scope, expression, or priority.

## Local staging tool

Python 3.10+ on Linux/macOS; install the validation dependency in a virtualenv:

```sh
python3 -m venv /tmp/precision-candidates-venv
/tmp/precision-candidates-venv/bin/pip install -r precision_candidates/requirements.txt
/tmp/precision-candidates-venv/bin/python precision_candidates/collect.py append \
  --input /path/to/observation.json --output /restricted/staging/candidates.jsonl
/tmp/precision-candidates-venv/bin/python precision_candidates/collect.py validate \
  --input /restricted/staging/candidates.jsonl
```

`--input -` accepts one JSON object on stdin. The tool fills only the schema
version, pending status, null AI assessment, candidate checksum, and stable
record ID. All source metadata/evidence must be supplied by the collector.
It validates before writing, serializes concurrent local appenders with a file
lock, returns an `appended`/`already_present` receipt, and rejects conflicts in
original candidate text, scope, producer, and known context/provenance. Unknown
metadata can be enriched, but known values cannot conflict across events.
The stable record ID derives from source kind, candidate ID, feedback ID, and
explicit version. A changed observation needs the next `event_version`, its
source event reference, and the preceding `supersedes_record_id`; the complete
version chain must be retained. Re-observing an identical event at a later time
retains the first observation. Removals must supersede an active reaction, and
a failed/truncated lookup must never manufacture one. Unicode line/paragraph
separators in original text are preserved verbatim inside LF-delimited JSONL.

Public GitHub evidence can be appended to `raw/candidates.jsonl`; the tool
updates its manifest count. Export validation checks the count. This is a
**local file writer**, not a production ingestion API or a multi-host database.
Publishers must still review/commit exports; the online design avoids a PR per
row by keeping the operational store outside Git.

## Public repository versus session evidence

This GitHub repository is public. **Agent-session rows always stay in restricted
staging in this MVP**, even if somebody marks their visibility public. The
writer also refuses Space/private records anywhere inside this checkout.
Do not commit entire internal conversations, internal links, credentials,
personal data, or undisclosed human feedback. A Space-visible session is not
public OSS data. A future export flow needs a destination-appropriate access
check and explicit human approval/redaction; do not just relabel a private row
as public to bypass the guard.

Staging must itself be access-controlled and have an agreed retention policy;
`/tmp` is useful for tests, not a durable online collection backend. Only log
necessary evidence, not hidden model reasoning or unrelated conversation.

## Adjudication and downstream training

Keep human adjudications separate and versioned, keyed to `record_id` and
`candidate_id`, with the human's verbatim rationale and authorship. Suggested
outcomes: `invalid`, `valid_but_not_worth_posting`, `valid`, `uncertain`, or
`excluded`. An AI may propose an outcome, but must not author a human label.
A rejection correction is a training preference only after adjudication.

Training exports must select active, sufficiently contextualized, adjudicated
examples and preserve candidate grouping. A later reaction removal/correction
requires revisiting the candidate's label. Split all events and related PR
revisions together. Keep evaluation holdouts and collection/adjudication
versions separate from training.

For production precision measurement, independently judge a random sample of
emitted comments, including those without feedback. This negative-triggered
dataset alone cannot estimate precision; keep a separate representative audit
sample and a recall regression set.

## Integration drafts

- [`agent-instructions.md`](agent-instructions.md): collection instructions for
  assisted review sessions, with safe failure handling and staging.
- [`online-proposal.md`](online-proposal.md): GitHub/ClickHouse collection,
  autoreview behavior, operational ingestion, and rollout proposal.
- [`contributor-feedback.md`](contributor-feedback.md): announcement/documentation
  draft; not posted or sent to contributors.

All new schema, tooling, proposal, and instruction text in this change is
AI-authored. No synthetic test fixture is an actual feedback observation.
