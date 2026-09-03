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

## Comments

**2026-09-03 — implemented.** `src/fieldproof/{config,database,schema,seed}.py`,
`tests/{conftest,test_schema,test_seed}.py`. 35 new tests (151 total); ruff, ruff
format, mypy strict and pytest all green.

Seams agreed with the user before any test was written. Three choices, all taken:

- **Postgres + SQLAlchemy 2.0 async + asyncpg.** My recommendation was SQLite on
  the fresh-clone argument; the user chose Postgres and it was the better call.
  `timestamptz` is native, so the `DTZ` rule holds at the driver boundary instead
  of needing a `TypeDecorator` to defend it, and the partial unique index is the
  real thing rather than a dialect approximation.
- **No Alembic.** Schema from model metadata via `create_schema()`. The issue
  title says "migrations", so this is a narrowing, recorded on `database.py`'s
  module docstring — spec.md §10 does **not** cover it, and an earlier draft of
  that docstring claimed it did. Caught in review.
- **Seed as `python -m fieldproof.seed`,** resetting rather than appending, with
  fixed UUIDs so participant links survive a reseed.

Consequence for issue 10: the fresh-clone test now needs a running Postgres and
`createdb fieldproof && createdb fieldproof_test`. `CLAUDE.md` updated.

**§9's third assignment: `assignment.report_deadline_s`.** Shipped first as a
pre-seeded `PENDING_REPORT` visit, on the reading that §1 lists exactly two
per-assignment columns and `REPORT_DEADLINE_S` is a global operational timing.
The spec review found the cost — `PENDING_REPORT` is non-terminal, so that row
held the partial unique index and 409'd anyone starting a visit on that
assignment until the sweeper lapsed it, which made that assignment the one to
demo last.

Reversed on the user's instruction, and the instruction is the better reading:
how long a write-up may take is a *task* fact, the same family as `radius_m` and
`min_duration_s`, not a property of the server. So `report_deadline_s` is a
column on `assignment`, default `86_400`; `REPORT_DEADLINE_S` moved out of
`config`'s operational timings and became `DEFAULT_REPORT_DEADLINE_S` beside the
other two per-assignment defaults; Pelago Pharmacy seeds with `DEMO_WINDOW_S` and
the seed now plants **no visits at all**. `UNREPORTED` is reached by ending a
visit and leaving it, in any demo order, and nothing holds the index.

`spec.md` is updated to match, in three places: §1 gains the column row and loses
the operational timing, §2's `assignment` block gains `report_deadline_s`, and §9
says a short `report_deadline_s` and states the no-seeded-visits rule with its
reason. §9's own wording ("whose visits get a short `report_deadline_at`") always
fitted this shape better than the one I built.

The consequence for anyone with an existing database: `create_schema` is
`create_all`, which does not alter existing tables (no Alembic, above), so a
database seeded before this change needs its schema dropped and rebuilt. Running
the seed against the old one fails on the missing column.

**Where the deadline is stamped.** `report_deadline_s` is a duration on the
assignment; `visit.report_deadline_at` is the instant, written as
`ended_at + assignment.report_deadline_s` when the visit is sealed (issue 08).
Stamped rather than derived on read, so widening the assignment's window later
cannot make an already-late visit on-time again. The sweeper reads only the
visit's column.

**`visit.last_ping_at` is NOT NULL,** initialised to `started_at`. A nullable
column makes the sweeper's predicate partial and the null branch has no honest
answer. The deliberate consequence — a permission-denied visit is `ABANDONED`
after 15 minutes if never ended — is documented on the column.

**Tests.** Not CRUD coverage. Every case is a constraint that is invisible when
wrong: the partial index (parametrized over states derived from
`NON_TERMINAL_VISIT_STATES`, so a new state extends the cases automatically),
the one-report/one-verdict uniqueness, `timestamptz` surviving the round trip,
and the verdict enum storing §2's lowercase spelling rather than Python member
names.

Both test files were **green on arrival**, so they were mutation-checked rather
than banked: eleven mutations — dropping the index predicate, widening it to all
states, moving it to the wrong column, `timezone=False`, dropping
`values_callable`, removing the `check_terms` call, removing each scan index,
removing the FK, making the seed append, and making `new_assignment` ignore its
`config` — and every one was caught. Honest accounting: **0 of these cycles were
genuinely red.** The schema is declarative, and I could not find a way to make
the red step meaningful beyond the import error.

The `report_deadline_s` tests that replaced the seeded-visit ones were green on
arrival too, and were checked the same way: dropping `report_deadline_s=` from
Pelago's seed row, and making `new_assignment` ignore the argument and always
write the default. Both caught, both reverted. One of the original eleven
no longer exists to run — "give the seeded visit the real 24h window" — because
the visit it mutated is gone.

**Review findings applied:** the false §10 claim; a comment saying the
`(state, last_ping_at)` index serves the `PENDING_REPORT` scan, which it does not
— it gets the `state` prefix only; a `Ping` docstring crediting `classification`
with ADR-0002's work when only `distance_m` does it; `Visit` having no creation
seam beside `new_assignment` with the hole undocumented; `print()` inside
`async def`; the "empty every table" walk duplicated between seed and conftest,
now `database.clear_tables`; `sessions` renamed `session_factory`; the `session`
fixture renamed `db` (CONTEXT.md lists "session" among the words to avoid); three
inline `Ping(...)` literals; and a hardcoded `180` now reading
`SCORING_CONFIG.sufficiency_s`.

**Declined, recorded:** a `TargetLocation` type for the `(lat, lng, radius_m)`
clump. CONTEXT.md names the concept, so it is a fair flag — but it has no second
caller until issue 04 computes haversine against it, and this repo's own
precedent (`geo.py`) was to move on a real caller, not an anticipated one. Issue
04 decides it. `Assignment.terms` was removed for the same reason: no caller in
this change.
