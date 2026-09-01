# Verification: the pure function

Status: ready-for-agent
Blocked by: 01

**Build this first on Wednesday, while freshest.** It is the intellectual core and
it has no dependencies — no DB, no clock, no I/O (ADR-0002).

`verify(trail, target, config) -> Verdict` per `spec.md` §3: classify by
uncertainty interval, attribute time between agreeing conclusive pings within
`GAP_ATTRIBUTION_LIMIT_S`, judge in the order sufficiency → duration → dwell.

Return the full breakdown, not just the bucket: `inside_s`, `outside_s`,
`unattributed_s`, `dwell_ratio`, `conclusive_pings`, `total_pings`. The dashboard
renders it and the debrief depends on it.

Frozen dataclass for policy thresholds with a `version` string. `radius_m` and
`min_duration_s` arrive from the assignment, not the config (ADR-0002).

Table-driven tests, cases chosen to encode the spec (issue 04 covers the rest):

- All pings inside but spanning only 90s → `unverifiable`, not `verified`.
  Sufficiency beats percentage.
- Perfect-accuracy trail 2km away, 10 min → `suspicious`.
- 20-minute pocket gap mid-visit, good pings either side → the gap is
  unattributed; verdict driven by what remains, never punished.
- Every ping `accuracy: 3` but 500m away → `suspicious`. Fake precision earns
  nothing (ADR-0003).
- Every ping `accuracy: 800`, dead on target → `unverifiable`. Junk absorbed by
  the interval arithmetic, no cutoff needed.
- Zero pings (permission denied) → `unverifiable` with no special case.
- 200s session, 190s attributed inside → `suspicious` via `min_duration_s`,
  not `verified`.
