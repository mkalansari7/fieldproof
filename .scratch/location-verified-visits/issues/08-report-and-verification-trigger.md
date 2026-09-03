# Report submission and verification

Status: ready-for-agent
Blocked by: 01, 02, 03

`POST /api/visits/{id}/report`: persist the report, transition
`PENDING_REPORT -> COMPLETED`, run `verify` over the sealed trail, persist the
verdict with its full breakdown and `scoring_config_version`, move the assignment
to `FULFILLED`, publish to the bus.

One transaction. A visit that is `COMPLETED` without a verdict row is a state the
dashboard cannot render.

Fulfilment does not consult the verdict (ADR-0004).

## Notes

**2026-09-03 — decided, not yet acted on: expiry beneath a live visit is A.**
Issue 05 found that spec.md §5/§7 as written let an assignment expire under a
visit that started in time (started 16:59, deadline 17:00), leaving that visit's
report unable to fulfil. Decision: the expiry sweep **skips assignments with a
non-terminal visit**. `deadline_at` means "start by", and a participant who
started in time keeps the ability to fulfil. To do when this issue lands:
change `sweeper.expire_overdue_assignments` to exclude assignments holding an
`ACTIVE` or `PENDING_REPORT` visit (`transitions.NON_TERMINAL_VISIT_STATES`);
flip `test_an_assignment_expires_beneath_a_live_visit`, which pins the old
behaviour and will fail loudly; rewrite that sweep's docstring and issue 05's
"Noted, not fixed" paragraph; update spec.md §5 and §7 to say "start by".

**Also for this issue, from issue 06:** the dashboard's `visit` delta is the bus
event and carries no verdict. When the report handler publishes
`PENDING_REPORT -> COMPLETED`, decide with issue 09 how the dashboard gets the
breakdown — carry it on the event, or have the client re-snapshot on
`COMPLETED`. Issue 06's comment has the trade-off.
