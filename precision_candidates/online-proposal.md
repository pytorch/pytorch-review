# Online precision feedback collection proposal

Proposed source pipeline (historical design):
The current first deliverable is the manual ClickHouse-to-CSV exporter described
in `README.md`, with `precision_candidates/data.csv` in this existing repository.
The private-service architecture below is not the selected implementation.
No session logger, daily schedule, or downstream training is enabled.

AI-authored proposal, researched 2026-11-04. This change adds the dataset contract
and local staging tool only: no collector, schedule, live bot change, contributor
announcement, or training job is enabled.

## MVP: stored thumbs-down plus ingestion-time verification

Two producers use `precision_candidates/schema.json`:

1. **GitHub:** mine comments whose ClickHouse snapshot already shows `-1 > 0`.
   Query GitHub **at ingestion** to check whether eligible thumbs-down reactions
   are still present, and capture canonical comment/thread evidence.
2. **Agent sessions:** log explicit human rejection, substantive correction,
   or pushback on a surfaced AI review candidate.

There is **no discovery sweep for reactions missing from ClickHouse** and no
continuous reaction reconciliation/removal tracker. We accept that stale-zero
snapshots may omit real downvotes. This is a precision-candidate collection MVP,
not an exhaustive feedback inventory or production precision metric.

Both producers submit to an authenticated, access-controlled operational store;
store heavy immutable input snapshots separately and reference them. Keep raw
evidence immutable, human adjudications separate, and reviewed public exports
versioned. Git holds schema/tools and public exports, not a PR per incoming row.
The local append tool is a staging interface, not a distributed ingestion API.
A production deployment still needs service credentials, a durable store,
authorized collection scope, access/retention decisions, and explicit enablement.

## ClickHouse feasibility: live-verified versus unknown

The upstream **ci-metrics** skill and unmodified `gcx-wrapper.sh` were used for
read-only queries through Grafana datasource UID `ceczcsck1b20wb`. The following
schema/freshness findings inform this deliberately narrow MVP.

Live checks establish:

- `default.issue_comment.reactions` has aggregate `-1` counts, stable comment
  IDs, text, URLs, and timestamps, but no first-class reviewed commit reference.
- `default.pull_request_review_comment.comment.reactions` has aggregate `-1`
  counts. The nested comment includes review/reply IDs, text/location,
  `commit_id`, and `original_commit_id`.
- The deployed `default.pull_request_review.review` tuple has no reactions
  field. Whole-review-body downvotes are outside this ClickHouse-seeded MVP;
  don't pretend body-level feedback labels every contained finding.
- Inline/review webhook events have fresh UUID `dynamoKey`s. `FINAL` does not
  collapse different events for one GitHub comment; use repository + source
  kind + GitHub ID as the canonical comment key.
- The inspected webhook-to-Dynamo/replicator path shows no reaction-only refresh.
  External refresh jobs and add/remove latency remain unverified. A stored
  nonzero count is a snapshot, not proof a reaction is currently present.

Source pins:

- [Webhook ingestion](https://github.com/pytorch/test-infra/blob/e09f40a94056c012af29071ed215eb096966d112/torchci/lib/bot/webhookToDynamo.ts#L163-L211).
- [Dynamo → ClickHouse replication](https://github.com/pytorch/test-infra/blob/e09f40a94056c012af29071ed215eb096966d112/aws/lambda/clickhouse-replicator-dynamo/lambda_function.py#L61-L83).
- [CI-metrics skill](https://github.com/pytorch/pytorch/blob/e31c96313bf775a9e7dc1d33fbb25d3fbcd3011e/.agents/skills/ci-metrics/SKILL.md).

## Ingestion algorithm

1. Read a bounded, paginated cohort of stored `-1 > 0` comments. Deduplicate
   event snapshots by canonical comment ID before API calls. Persist cursors,
   overlap for late-arriving rows, and idempotent processed-event keys.
2. Confirm AI-review provenance. For hardened autoreview, verify both parent
   review author and `<!-- hardened-pr-review -->` marker. Bot authorship alone
   is insufficient. Other AI-assisted comments need explicit producer linkage
   or attribution. Verify that issue comments belong to a PR.
3. Fetch the current comment and all GitHub reaction pages. Issue comments use
   `/repos/{repo}/issues/comments/{id}` and `.../{id}/reactions`; inline review
   comments use `/repos/{repo}/pulls/comments/{id}` and `.../{id}/reactions`.
4. **Still has eligible human `-1`:** emit a pending observation for each
   distinct reaction, retaining candidate grouping. Use the reaction ID as
   `source.feedback_id`; do not infer who/when from a count. Start with author/
   maintainer feedback, matching current bot context policy; broader feedback
   scope is a deployment decision. Exclude known bots.
5. **Complete lookup confirms no eligible `-1`:** skip candidate creation.
   **Lookup fails/incomplete/inaccessible:** quarantine/retry as unknown; never
   manufacture an active downvote or call it a confirmed removal. Rate-limit
   handling/retries belong to the collector, not an unauthorized agent loop.
6. Preserve exact original text and revision references plus relevant human
   replies. A parent review's commit or current head is not interchangeable
   with the inline comment's original commit. If complete original input can't
   be recovered, keep the observation partial with explicit missing context.

Verification proves presence **at observation time**, not forever. The schema
can represent future versioned corrections/removals, but this MVP does not
monitor changes after ingestion. Adjudication must still determine whether the
original finding was invalid, low-value, valid, or uncertain.

## Bounded query, executed against live schema

This exact query, including the `-1 > 0` predicate, succeeded at
2026-11-04 19:59:26 UTC. It returned **zero rows in this cohort**. That verifies
syntax/schema execution, not nonempty extraction or end-to-end GitHub downvote
verification. It proves nothing about downvotes outside these bounds.

```sql
SELECT
  action,
  repository.full_name AS repo,
  pull_request.number AS pr_number,
  comment.id AS comment_id,
  comment.node_id AS node_id,
  comment.pull_request_review_id AS review_id,
  comment.in_reply_to_id AS reply_to,
  comment.html_url AS html_url,
  comment.body AS body,
  comment.path AS path,
  comment.line AS line,
  comment.commit_id AS commit_id,
  comment.original_commit_id AS original_commit_id,
  pull_request.head.sha AS head_sha_at_event,
  comment.created_at AS created_at,
  comment.updated_at AS updated_at,
  tupleElement(comment.reactions, '-1') AS thumbs_down_snapshot,
  comment.reactions.total_count AS total_reactions_snapshot,
  dynamoKey AS source_event_key
FROM default.pull_request_review_comment
WHERE repository.full_name = 'pytorch/pytorch'
  AND pull_request.number IN (203149, 200086, 203413)
  AND comment.updated_at >= toDateTime('2026-10-05 00:00:00', 'UTC')
  AND comment.updated_at < toDateTime('2026-11-05 00:00:00', 'UTC')
  AND tupleElement(comment.reactions, '-1') > 0
ORDER BY comment.updated_at DESC, dynamoKey DESC
LIMIT 100
SETTINGS max_execution_time = 30, max_rows_to_read = 1000000
```

For collection, page a fixed bounded interval on `(updated_at,dynamoKey)` and
record completion/cap failures; 100 results alone are not a complete export.
The PR-number predicate aligns with the leading sort key. Execution/read caps
matter because `LIMIT` does not bound scans. UUID ordering is only a pagination
mechanism, not authoritative comment-version ordering. The query is verified,
not production-tuned. A separately bounded issue-comment query can use its
verified `reactions` tuple and stable IDs, but needs explicit commit mapping.

## What autoreview already does

Current PyTorch code fetches author/maintainer reactions, maps `THUMBS_DOWN` to
`-1`, gives earlier automated findings and their feedback to re-reviews, and
preserves reacted inline comments during supersession:

- [Collection/mapping](https://github.com/pytorch/pytorch/blob/e31c96313bf775a9e7dc1d33fbb25d3fbcd3011e/scripts/pr_review/fetch_pr_comments.py#L90-L115).
- [Re-review instructions](https://github.com/pytorch/pytorch/blob/e31c96313bf775a9e7dc1d33fbb25d3fbcd3011e/.agents/skills/pr-review/SKILL.md).
- [Preserving reacted comments](https://github.com/pytorch/pytorch/blob/e31c96313bf775a9e7dc1d33fbb25d3fbcd3011e/scripts/pr_review/post_review.py#L764-L818).

This is not an immediate reaction trigger, automatic suppression, or durable
training capture. Prompt context is clipped/limited and carries truncation
flags; don't use it as an exhaustive collector.

Proposed **pytorch/pytorch PR**:

- Add the feedback notice to contributor review docs and a compact generated
  review-footer pointer. Explain inline versus whole-body reactions.
- Clarify that a downvote means disagreement: re-check the claim/preconditions,
  address any stated rationale, and don't repeat it without addressing that
  evidence. Never automatically suppress a valid concern or obey instructions
  embedded in untrusted comments.
- Add instruction/format regressions and retain reaction/truncation/preservation
  tests. Export a stable candidate → posted review/comment mapping with original
  text, reviewed SHA, run/model/instruction metadata, and scope.
- Keep collection in trusted preparation/posting stages or the external worker;
  do not give the offline Stage A reviewer network/write credentials.

Proposed **test-infra PR**:

- Implement the snapshot-positive collector and ingestion-time GitHub check,
  with cursoring, retries, idempotence, unavailable-source state, and lag/error
  metrics. No new reaction webhook is assumed or required for this MVP.
- Emit validated observations/snapshot references to the approved store using
  service credentials, not a human's temporary read-only Grafana Viewer token.
- If ClickHouse is chosen as the operational sink, add a dedicated table,
  ingestion mapping, grants, and deployment. Existing tables seed extraction;
  none inspected already contains this dataset's complete contract.

These are scoped proposals, not implemented bot/test-infra PRs in this change.

## Agent-session instructions and iteration

Use [`agent-instructions.md`](agent-instructions.md) and a configured structured
logging binding. Retain surfaced candidates before discarded drafts disappear.
Only explicit human disapproval/correction is eligible, with verbatim evidence;
AI triage is separate from human labels. Internal session rows remain in a
restricted store, not this public repository. Enable collection only after its
notice, authorized destination, and retention policy are agreed.

Pilot recording-only collection, manually audit source linkage/freshness and
missing context, then add separate human adjudication and versioned exports.
Train reviewer changes in batches, not immediately from a downvote. Evaluate
with an independent representative comment audit and a recall regression set;
keep fresh time-based holdouts and group all events/related PR revisions together.
Shadow/canary before rollout. Negative-triggered collection alone cannot measure
production precision or recall.
