# Business dashboard (Angular)

Status: ready-for-agent
Blocked by: 06, 08

Consumes the SSE stream: render the snapshot, apply deltas.

Shows per visit: assignment, state, verdict, and the breakdown — attributed time,
dwell ratio, conclusive ping count, visit duration. Plus attempt count per
assignment, which is signal in its own right (ADR-0001).

**No polyline, ever** (ADR-0005). The business sees the verdict and its
reasoning, not where the participant walked.

Completion and verdict are separate columns. A `suspicious` verdict is a prompt
for a human to look, not a rejection (ADR-0004).

Cut: the ticking "last seen Ns ago" counter and the map. The map may exist as an
internal audit route if time allows — demonstrate it as deliberately unexposed
rather than omitting it silently.

## Notes

**2026-09-04 — from issue 08: a `COMPLETED` delta carries its verdict.** No
re-snapshot on `COMPLETED`; apply every `visit` delta in place, the same way.
The `visit` event's `verdict` field is `null` for every `to_state` but
`COMPLETED`, where it is the full breakdown (`verdict`, `inside_s`,
`outside_s`, `unattributed_s`, `attributed_total_s`, `dwell_ratio`,
`conclusive_pings`, `total_pings`, `visit_duration_s`, `radius_m`,
`min_duration_s`, `scoring_config_version`) — the snapshot's `verdict` object
minus `computed_at`, which is the delta's `at`. Render a verdict from either
source with one function. The `assignment` delta carrying `FULFILLED` follows
the `COMPLETED` delta on the same bus, in that order. Argument recorded in
issue 06's comments.
