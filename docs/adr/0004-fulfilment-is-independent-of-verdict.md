# Fulfilment is independent of verdict

Any `COMPLETED` visit fulfils its assignment. The verdict rides alongside as
advice to the business and does not gate fulfilment.

## Considered options

- **`FULFILLED` requires a `verified` verdict.** Reads natural, and is what the
  brief's framing implies. Rejected: it quietly turns a confidence threshold into
  a payment gate, so a participant with a weak GPS chip or an indoor venue does
  not get paid, and a number chosen on a Tuesday evening decides someone's
  income. Whether to pay for a low-confidence visit is a business policy
  judgement that belongs to the business, with the evidence in front of them. It
  is not a formula's job.
- **A middle state — `fulfilled-pending-review` — for non-verified completions.**
  Rejected for the slice: it encodes a review workflow into the state machine
  when the verdict column already carries the same information, and every state
  added is another edge the transition table has to close.

## Consequences

This is a deliberate deviation from the brief as written, and is raised as such
rather than worked around silently. The dashboard therefore shows completion and
verdict as two independent columns, and a `suspicious` verdict is a prompt for a
human to look, not an automatic rejection.
