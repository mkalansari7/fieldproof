# Verification: the pure function

Status: ready-for-agent

**Build this first on Wednesday, while freshest.** It is the intellectual core and
it has no dependencies — no DB, no clock, no I/O (ADR-0002).

`verify(trail, target, visit_duration_s, config) -> Verdict` per `spec.md` §3:
classify by uncertainty interval, attribute time between agreeing conclusive
pings within `GAP_ATTRIBUTION_LIMIT_S`, judge in the order
**duration → sufficiency → dwell**.

`visit_duration_s` is an argument, **not derived from the trail**. It is
`ended_at - started_at` on the server clock, and it is the only input the zero-ping
cases have. Deriving it from first-and-last ping silently turns every permission-
denied visit into a zero-duration one and makes the first gate meaningless.

Return the full breakdown, not just the bucket: `inside_s`, `outside_s`,
`unattributed_s`, `dwell_ratio`, `conclusive_pings`, `total_pings`. The dashboard
renders it and the debrief depends on it.

Frozen dataclass for policy thresholds with a `version` string. `radius_m` and
`min_duration_s` arrive from the assignment, not the config (ADR-0002).

Table-driven tests, cases chosen to encode the spec (issue 04 covers the rest).
All cases assume `radius_m` 100 and `min_duration_s` 300 unless stated.

Duration gate (runs first):

- All pings inside, visit spans 90s → `suspicious`, not `unverifiable`. A
  sprint-through is evidence, not an absence of it. **This case reversed on
  2026-09-02** — see `docs/ai-log.md`; it is the whole reason the order is what
  it is, so it does not get quietly relaxed.
- Zero pings (permission denied), 60s visit → `suspicious`. The counter-case,
  decided deliberately: server-clock evidence alone, no innocent account of a
  300s task inside a 60s visit.
- 200s visit, 190s attributed inside → `suspicious` via `min_duration_s`, not
  `verified`. Good evidence does not buy back missing time.
- Boundary pair: 299s and 300s visits, otherwise identical and clean.

Sufficiency gate (runs second, on visits long enough for the task):

- Zero pings (permission denied), 600s visit → `unverifiable`, no special case:
  duration passes, then `attributed_total == 0`.
- Every ping `accuracy: 800`, dead on target, 600s visit → `unverifiable`. Junk
  absorbed by the interval arithmetic, no cutoff needed (ADR-0003).
- 20-minute pocket gap mid-visit inside a 30-minute visit, good pings either side
  → the gap is unattributed; verdict driven by what remains, never punished.
- Boundary pair: 179s and 180s attributed on a 600s visit.

Dwell gate (runs last):

- Perfect-accuracy trail 2km away, 10 min → `suspicious`.
- Every ping `accuracy: 3` but 500m away, 10 min → `suspicious`. Fake precision
  earns nothing (ADR-0003).
- Boundary pair: `dwell_ratio` 0.79 and 0.80 on a 600s visit.

## Comments

**2026-09-02 — implemented (TDD).** `src/fieldproof/verification.py`,
`tests/test_verification.py`. 31 tests; ruff, ruff format, mypy strict and pytest
all green.

Seams confirmed with the user before any test was written: `verify()`,
`classify()` and `haversine_m()` are all public — ingest is a second caller of the
latter two (spec.md §2 computes `distance_m` and `classification` at write).

Two interface decisions worth recording:

- **Pings reach `verify` as `(received_at, distance_m, accuracy_m)`, not
  pre-classified.** `classification` depends on `radius_m`, so carrying it in
  would freeze a verdict to the radius in force at write time and make ADR-0002's
  "what would this have been at 150m?" unanswerable. `verify` classifies;
  `haversine_m` stays at ingest. Covered by
  `test_the_same_trail_rescores_differently_under_a_wider_radius`.
- **The second parameter is `terms: AssignmentTerms(radius_m, min_duration_s)`,
  not `target`.** `CONTEXT.md` defines Target Location as coordinates plus
  radius; `min_duration_s` is a task fact, not a location fact, and `verify`
  never needs the coordinates. Naming it `target` would have put a banned meaning
  on a glossary term.

One rule implemented beyond the issue's table: `verify` sorts by `received_at`
rather than trusting caller order. Unsorted input made the pairwise walk attribute
*negative* time, which no gate is written to survive.

**2026-09-02 — review round.** Four findings, all at the module boundary. 37
tests; four gates green.

- `min_duration_s < SUFFICIENCY_S` makes `verified` unreachable. Guard is
  `check_terms()`, raising `IncoherentTermsError`, called at **assignment
  creation** — see `spec.md` §1 for the invariant and why `verify` deliberately
  does not raise (issue 01 should call it; issue 08 must not).
- `haversine_m` and `EARTH_RADIUS_M` moved to `src/fieldproof/geo.py`. `verify`
  never called it, so ingest no longer imports the judgement core for geodesy.
  `asin(min(1.0, sqrt(h)))` guards the near-antipodal domain edge.
- Negative `unattributed_s` from out-of-window pings: documented as a caller
  precondition on `verify`'s docstring, not clamped. **The trail query in issue
  08 is where the window gets enforced.**
- Unattributed class-transition gaps now carry their rationale in code.
