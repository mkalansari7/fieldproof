# Assignment and Visit are separate entities

The obvious model is one row with one status column carrying `ASSIGNED → ACTIVE →
COMPLETED`. That model cannot express a second attempt, so a participant whose
phone dies mid-visit kills an assignment the business paid for. We split the
lifecycle in two: an **Assignment** is the task a business issued (`ASSIGNED`,
`EXPIRED`, `FULFILLED`), a **Visit** is one attempt at it (`ACTIVE`,
`PENDING_REPORT`, `COMPLETED`, `ABANDONED`, `UNREPORTED`), and an assignment has
many visits with at most one non-terminal at a time.

## Considered options

- **One entity, one status enum.** Simpler schema, and the shape the brief's
  narration implies. Rejected: retries are impossible to express without
  resurrecting terminal states, and a resurrection edge (`ABANDONED → ACTIVE`
  inside a grace window) is a state machine that lies about what happened.
- **One entity plus an `attempt_number` column.** Retries expressible, but the
  ping trail of attempt 1 and attempt 2 share a row, so verification can no
  longer be scoped to a single attempt.

## Consequences

Reconnection stops being a state machine problem. There is no grace window and no
un-abandoning: a participant whose visit dies starts a new visit against the same
assignment. Attempt count becomes a queryable signal in its own right, and is
shown to the business — several abandoned attempts followed by one clean run is
a pattern worth seeing.
