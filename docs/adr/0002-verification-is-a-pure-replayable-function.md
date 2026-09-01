# Verification is a pure, replayable function over a retained trail

Verification takes `(ping trail, target location, scoring config)` and returns a
verdict plus its breakdown, with no I/O and no clock access. The raw trail is
retained rather than reduced at submission time, and every stored verdict records
the scoring config version that produced it.

## Considered options

- **Score once at submission, store the number.** The obvious reading of the
  brief, and less to persist. Rejected: a scoring bug is unfixable on historical
  data, and the question "what would this have been at 150m?" becomes
  unanswerable — including in a debrief, live, with the interviewer watching.
- **Score continuously and keep a running total.** Cheaper reads, but the score
  becomes an accumulator whose value depends on the order it was fed, which is
  exactly the property that makes a bug irreproducible.

## Consequences

Historical visits can be re-scored under a newer config. The core of the product
is a function with no dependencies, which is where essentially all of the test
effort goes. Ingest must stay cheap for this to hold: distance and conclusiveness
are computed once at write time and stored on the ping row, so verification is an
aggregate over indexed rows rather than a recomputation.

Re-scoring is bounded by retention. Once a trail is reduced to its verdict
summary (ADR-0005), that visit's verdict is frozen and cannot be recomputed under
a later config. The replay window and the privacy window are the same window:
widening one widens the other, and that trade-off is deliberate rather than an
oversight in either direction.
