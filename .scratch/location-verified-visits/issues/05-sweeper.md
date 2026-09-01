# Background sweeper

Status: ready-for-agent
Blocked by: 01, 03

One `asyncio` task started on app startup, ticking `SWEEP_TICK_S`. Per `spec.md`
§7: stale `ACTIVE` → `ABANDONED`, overdue `PENDING_REPORT` → `UNREPORTED`,
overdue `ASSIGNED` → `EXPIRED`.

Publishes to the **same** in-process bus the API handlers use, so a dashboard
consumer cannot tell whether an event came from a request or a sweep. That
symmetry is what makes issue 06 simple.

Single-process by construction. Do not add a leader lock or advisory lock — the
limitation is deliberate and recorded once (ADR-0006, `docs/design.md`). Note it
in the code where a reader would otherwise assume it was an oversight.

Highest-risk item on Wednesday alongside issue 06. Buffer sits here.
