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
