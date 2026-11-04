# Draft: feedback on AI review comments

**Draft for contributor docs/announcement; not sent or posted.**

If an AI review comment is wrong, inapplicable, or not worth flagging, please
react with **👎 on that specific comment**. A short reply explaining why is
especially useful—for example, "this is already validated by the caller" or
"this bound makes the performance concern inapplicable."

Feedback helps us find review candidates to improve. A thumbs-down is **not**
an automatic false-positive label, approval, dismissal, or merge override.
If several findings share a review body, name the finding in your reply: a
reaction on the body does not identify which individual finding you reject.

For the hardened autoreview bot, a re-review already receives the PR author's
and maintainers' replies/reactions, subject to collection limits. You can ask
for another review with `@pytorchbot review`; a reaction alone does not promise
an immediate run or suppression of the finding. Explain your reasoning rather
than repeatedly pushing code solely to satisfy an incorrect comment.

**Collection status:** Durable ingestion into `precision_candidates` is
proposed, not enabled by this change. Before announcing collection as active,
update this notice with the actual start date, scope, destination, and retention
policy. Public GitHub feedback and internal assisted-review sessions have
separate access/export rules.

Suggested placement: contributor review-workflow docs and a compact pointer in
the bot's generated review footer. Publish an announcement only after its
collection claims match the deployed behavior and the audience is chosen.
