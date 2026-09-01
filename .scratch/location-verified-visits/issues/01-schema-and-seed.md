# Schema, migrations and seed

Status: ready-for-agent

Tables per `spec.md` §2: `assignment`, `visit`, `ping`, `report`, `verdict`.

- Partial unique index on `visit(assignment_id)` where state in
  (`ACTIVE`, `PENDING_REPORT`) — enforces "at most one non-terminal visit"
  (ADR-0001) in the database, not in application code.
- `ping(visit_id, received_at)` index: verification is an ordered aggregate.
- `visit(state, last_ping_at)` and `assignment(state, deadline_at)`: sweeper scans.
- Seed per §9, including the short-deadline and short-report-deadline assignments
  so `EXPIRED` and `UNREPORTED` are demoable.

Store `reported_at` and `received_at` as separate columns from the start. Merging
them later is a migration and a rewrite of every scoring test.
