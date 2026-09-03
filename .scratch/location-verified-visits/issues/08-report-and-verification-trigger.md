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

## Comments

**2026-09-04 — implemented.** `api.end_visit` (`POST /api/visits/{id}/end`)
and `api.submit_report` (`POST /api/visits/{id}/report`); decision A in
`sweeper.expire_overdue_assignments`; the verdict on the `COMPLETED` delta in
`events.VisitTransitioned`. 29 new test cases (267 total); ruff, ruff format,
mypy strict and pytest all green. No new dependencies.

**`/end` is a stamp and a lock.** `ended_at` is `received_now()`,
`report_deadline_at` is that plus the assignment's `report_deadline_s`, and the
handler takes `FOR UPDATE OF visit` — it is the sealer `ingest_ping` was
written to wait on, so a ping that read `ACTIVE` before the seal either
commits before the stamp or re-reads `PENDING_REPORT` and gets its 409.
Response carries the deadline, because `UNREPORTED` is unrecoverable and the
participant's page should say when rather than let them find out from a 409.

**`/report` is one transaction, four writes, two locks.** Report row, verdict
row with the full breakdown and `scoring_config_version`, `PENDING_REPORT ->
COMPLETED`, `ASSIGNED -> FULFILLED`; commit, then publish both events in that
order. Both rows are locked with a plain `with_for_update()` — the visit
against the unreported sweep, the assignment against the expiry sweep — and
the pre-paid interleaving tests from issue 05 pass unchanged. The response
carries no verdict: it is the business's to read (ADR-0004, ADR-0005) and the
participant's page ends at "done".

**The two caller invariants, upheld where `verify` says they must be.** The
trail query is `received_at <= ended_at`, and `visit_duration_s` is
`ended_at - started_at` from the server's own stamps. `test_the_verdict_row_is_
verify_over_the_sealed_trail` plants a 500-second visit with ten pings inside
the window and one 10 seconds past it, and asserts the stored breakdown
against hand-computed literals *and* against `verify` on the bounded trail;
the stray ping changes `inside_s`, `unattributed_s` and `total_pings`, so an
unbounded query fails on three numbers.

**Machine first, trail second.** A report at a visit that was never ended is
refused by `advance_visit` before any ping is read — not by inspecting
`ended_at`. Once the move is known legal, `ended_at` exists (the only way into
`PENDING_REPORT` stamps it); a null there is a 500 on purpose, because the
sealer is broken and not the participant.

**Double report.** Sequential: the machine refuses at `COMPLETED`, under the
visit lock, so two at once serialise into one 200 and one 409. The unique
constraints are named (`ux_report_one_per_visit`, `ux_verdict_one_per_visit`)
and read back out of the `IntegrityError` the way `open_visit` reads its
partial index, for the one row that exists without its transition:
`test_a_report_row_without_its_transition_is_409_not_500`.

**Decision A landed.** `expire_overdue_assignments` adds `NOT EXISTS (visit
with a non-terminal state)` to its `WHERE`; the guard is in the query and not
in `advance_assignment`, which sees one row and cannot ask about others.
`test_an_assignment_expires_beneath_a_live_visit` flipped to `..._stays_
assigned_beneath_a_live_visit`, parametrised over both non-terminal states;
its converse over the terminal states; and one for the order of the sweeps —
a visit abandoned in a pass lets its assignment expire in the same pass.
spec.md §5 and §7 say "start by"; issue 05's "Noted, not fixed" paragraph is
rewritten; `DeadlinePassed`'s docstring and the transition table's test
docstring say where the condition lives. One window the `NOT EXISTS` does not
close, recorded on the sweep: `open_visit` does not lock the assignment, so a
visit whose start commits after the sweep's `SELECT` began can still lose, in
the last instant before a pass.

**The 06 handoff: the verdict rides the delta.** Argued in issue 06's comments
and recorded for issue 09. In short: the breakdown is exactly what ADR-0005
lets the business see and is the same `Verification` the row is written from;
`COMPLETED` is a state only a report reaches, so the field is not an origin in
disguise and the one-renderer symmetry holds as a property of the type —
`VisitTransitioned.__post_init__` refuses a `COMPLETED` event without a verdict
and any other event with one. The re-snapshot alternative makes the client
treat one `to_state` differently from the other five, for a round trip that
buys nothing the delta cannot carry.

**Mutation pass, on the trigger wiring only, four mutations.** (1) Drop the
`received_at <= ended_at` bound — caught by the hand-computed trail test.
(2) Take `visit_duration_s` from the trail's span instead of the stamps —
caught by the same test. (3) Scope the report handler's lock to `of=Visit`,
leaving the assignment unlocked — **survived**: the pre-paid expiry-race test
proves the *sweep* waits on a handler that holds the assignment, by holding it
by hand, and nothing proved the handler holds it. Closed by `test_a_report_
racing_an_expiry_does_not_revive_the_assignment`, which stages the sweep's
side through the endpoint; without the lock it fails with a 200 and
`FULFILLED` written over `EXPIRED`, a lost update. Re-run: caught. (4) Drop
the assignment transition — caught by the happy path and the bus test. All
reverted.
