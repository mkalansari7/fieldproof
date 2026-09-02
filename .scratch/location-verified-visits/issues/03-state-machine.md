# State machine and transition tests

Status: ready-for-agent

Both machines from `spec.md` §5 as explicit transition functions, plus the
exhaustive `(state, event) -> state | rejected` test table.

The point of the table is the illegal moves: ping to a `PENDING_REPORT` visit,
report on an `ABANDONED` visit, ending an already-ended visit, starting a second
visit while one is `ACTIVE`, completing an `EXPIRED` assignment. Assert every one
is rejected — that is what proves the machine is closed, and it is cheap.

No `ABANDONED -> ACTIVE`. Resurrection is not in the machine (ADR-0001).

Assert fulfilment ignores verdict: a `suspicious` completed visit still moves its
assignment to `FULFILLED` (ADR-0004).
