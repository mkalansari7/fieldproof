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

## Comments

**2026-09-03 — implemented (TDD).** `src/fieldproof/transitions.py`,
`tests/test_transitions.py`. 79 tests; ruff, ruff format, mypy strict and pytest
all green.

Seams confirmed with the user before any test was written:

- **`start_visit(*, assignment, latest_visit)` is its own function**, and
  `VisitEvent` has no `START` member. Start is the one row in `spec.md` §5 with
  no from-state, and its guard reads the *assignment* plus whatever visit came
  before. Splitting it makes `ABANDONED -> ACTIVE` unrepresentable rather than
  merely rejected, which is the stronger reading of ADR-0001: there is no edge to
  delete later.
- **`VisitCompleted` carries the verdict the assignment machine ignores.** A bare
  enum member would make ADR-0004 true by construction — no discriminating input,
  nothing to falsify. Carrying it lets
  `test_any_completed_visit_fulfils_its_assignment_whatever_the_verdict`
  parametrize over all three verdicts and assert `FULFILLED` for each.
- **Module is `transitions.py`**, matching §5's own heading.

Rejection is `IllegalTransitionError(ValueError)`, parallel to
`IncoherentTermsError`. The API layer turns it into the 409 (spec.md §4, §6).

Three things worth recording:

- **The visit machine is a dict, not branches.** `COMPLETED`, `ABANDONED` and
  `UNREPORTED` appear only as values, so the fifteen moves out of a terminal
  state are *absent* rather than guarded against. The assignment machine matches
  on a `VisitCompleted | DeadlinePassed` union instead, because one event carries
  data; a third variant added and not matched falls through to rejection.
- **The exhaustive table was green on arrival, so it was mutation-tested.** A
  smuggled `(ABANDONED, PING) -> ACTIVE` row was added to the implementation and
  the table caught it, then reverted. The table transcribes §5 by hand rather
  than reading `_VISIT_TABLE` back — importing the machine's own dict would make
  it agree by construction and assert nothing.
- **`(ASSIGNED, COMPLETED)` is deliberately startable.** A completed visit
  fulfils its assignment (ADR-0004), so the pair is unreachable; the guard lives
  in the assignment machine and is not duplicated in `start_visit`. Recorded
  because it looks like a hole in the start table until you know why.

## Review verdicts — 2026-09-03

Both open questions closed by the maintainer.

**Import direction: accepted.** `Verdict` is verification's output type, not a
shared utility, and ADR-0004 is precisely a policy about fulfilment *consuming*
that output — so `transitions -> verification` states the truth. No cycle, no
smell. Revisit with a shared types module only if a third consumer appears.

**`(ASSIGNED, COMPLETED)` startable: accepted, with one change, applied.** The
rationale now sits on the fall-through in `start_visit` itself, not only here: an
unexplained permissive cell in an otherwise-paranoid table reads as a bug to any
reviewer, and the explanation has to live where the code is read.

Worth noting for anyone chasing that comment: the cell is not a literal row in
`transitions.py`. `start_visit` is two guards and a fall-through, so the
permissive cell is the *absence* of a check; the literal row is `STARTABLE` in
`tests/test_transitions.py`, which already carried its reasoning inline. The new
comment marks the fall-through in the source.

**Callers to come.** Issue 04 (ping ingest) needs `advance_visit(..., PING)` and
the 409; issue 05 (sweeper) needs `SILENCE_ELAPSED`, `REPORT_DEADLINE_PASSED` and
`DeadlinePassed`; issue 07 needs `start_visit`; issue 08 needs
`REPORT_SUBMITTED` followed by `VisitCompleted(verdict)` against the assignment.
`NON_TERMINAL_VISIT_STATES` is exported for issue 01's partial unique index to
agree with.
