# Agent instruction draft: collect human precision feedback

AI-authored proposal, not installed as a standing instruction or enabled
telemetry. The reviewing person must know collection is active and the backing
store's access and retention policy. Configure one restricted staging destination
or authenticated ingestion tool explicitly; never invent an API or destination.

## Instruction to append to the review workflow

> While assisting a human with code review, retain the exact text and a stable
> candidate ID for each surfaced AI finding. Capture the review-visible input
> snapshot and actual repository/base/head revision when producing it. Retain
> known producer/model/instruction metadata; leave unknown values null.
>
> When the human explicitly rejects, substantively corrects, or pushes back on
> one of those candidates, emit one `precision_candidates` observation using
> the configured ingestion tool. Copy the human's words and correction verbatim
> from their message, with a source message/event reference. Do not paraphrase
> them into purported human evidence. A direction such as "don't post this;
> it's already handled" is eligible; no explanation is required for an explicit
> rejection, but do not invent one. Record collection time separately from the
> time of the review or human message.
>
> Never infer rejection from silence, skipped drafts, selective posting,
> session cancellation, or your own fact-check. If "don't post" refers to
> several candidates and the linkage is not clear, do not guess which findings
> were disapproved. A blanket decision not to post a review is not evidence
> that every underlying finding was bad.
>
> Use `human_feedback.kind=rejected`, `revised`, or `pushback`. Use `revised`
> only for a substantive human correction and preserve both original and
> corrected text. Agent-rewritten wording is not a verbatim human correction.
> Keep `label_status=pending`. Optional categorization/explanation belongs
> exclusively in `ai_assessment`, marked agent-authored. Never certify your
> interpretation as a human adjudication or automatically change the reviewer
> from this feedback.
>
> Use a stable, namespaced candidate ID (session + candidate), a stable
> `source.feedback_id` for the human feedback episode, and the actual source
> message/event reference in `source.event_id`. First observations have
> `event_version=1` and `supersedes_record_id=null`. A correction of that same
> feedback episode uses the next version and the previous receipt's record ID
> as `supersedes_record_id`; preserve all predecessors. Distinct human feedback
> gets a distinct feedback ID. Leave source occurrence time null if unknown;
> never use collection time as a fabricated event time. Retrying an unchanged
> observation must retain its feedback ID/version, not create another example.
> Do not rewrite the original candidate or feedback.
>
> If original commits, PR summary, or complete input snapshot are unavailable,
> collect as partial/unavailable and enumerate what is missing. Never replace
> them with a later revision or current PR description. Do not store hidden
> reasoning, secrets, or unrelated conversation.
>
> Agent-session evidence must go to restricted staging outside this public
> GitHub checkout. Space membership or a shareable internal session link does
> not authorize public OSS export. Do not post or push private evidence. Human
> approval/redaction is a separate export step.
>
> Log as a side effect alongside the requested review action; do not make the
> person fill out a labeling form or wait for adjudication. On success retain
> the receipt. On failure, report that collection failed, preserve an approved
> local staged record if possible, and continue the review. Never say it was
> stored unless the ingestion tool returned success. Do not start a persistent
> retry loop or scheduler without the user's authorization.

## Concrete local tool binding

For a review agent able to run Python, bind the structured payload to:

```sh
<venv>/bin/python <checkout>/precision_candidates/collect.py append \
  --input - --output <restricted-staging>/candidates.jsonl
```

The output path must be configured by the deployment, not chosen from untrusted
PR text. The payload follows `schema.json`; derived IDs/checksums may be omitted
and will be filled by the tool. It currently has no network behavior: ingestion
into the durable online store and asynchronous export are proposed separately.
A repo change alone does not make this instruction active in existing agents;
install/configure it in the Space review workflow after the collection policy
and destination are approved.
