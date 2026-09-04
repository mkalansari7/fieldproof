# AI Process Log

## 2026-09-01 — Design grilling (grill-with-docs, Claude Code)

- Ran a grilling session on the full design before writing any code;
  transcript kept as evidence.
- AI self-correction (Q6): skill first framed live in-flight dashboard
  rows as implied by the brief, then corrected itself — brief step 6
  only requires completed visits to appear automatically. I adopted
  the in-flight view as my own deliberate scope addition, cuttable
  if time runs short.
- AI contradiction caught (Q4 vs Q9): Q4 recommended showing the
  raw ping trail on a map as the flagship demo. Q9 flagged that a
  business-facing trail over-discloses — conflicts with the brief's
  privacy requirement. Resolution: trail stays server-side, business
  sees verdict + breakdown only, map becomes an internal audit view.

- Override (Q18, schedule): the skill named Angular as my schedule risk and
  proposed compressing it. Rejected — Angular is my primary stack and my fastest
  layer. The real risk is the async FastAPI work (sweeper, SSE fan-out), so the
  buffer moved to Wednesday and the frontend was compressed instead.
- AI got a platform fact wrong (Q13): the skill recalled mobile browsers as
  _throttling_ background timers to roughly once a minute. I measured it instead
  of taking it (`docs/experiments/pingtest.html`): iOS Safari suspends JS
  **entirely** on screen lock and on app switch — a single unbounded gap, 297.8s
  for a ~5min lock. Categorically worse than throttling, and it is what set the
  15-minute abandonment timeout. The AI was right that the fact was load-bearing
  and right to push me to measure rather than assume; it was wrong about the fact.
- AI inconsistency caught late (ADR-0006): the skill justified SSE over polling
  partly on the live in-flight dashboard, which I then cut for time in Q18 —
  leaving the ADR resting on a feature that no longer exists. Flagged and
  rewritten to rest on the sweeper instead: `ABANDONED`, `UNREPORTED` and
  `EXPIRED` are server-originated with no request behind them, which motivates a
  push channel independently. Polling is recorded as a reasonable alternative
  rather than a strawman.

## 2026-09-01 — end of design session

Design closed with an empty question frontier. Six ADRs, a spec, ten ordered
issues and a handoff note, all generated from the grilling transcript before any
implementation code was written.

## 2026-09-02 — ADR revision pass

- I revised three ADRs after the design session; the skill reviewed them and
  disagreed on one. My addition to ADR-0002 restated ADR-0005's privacy/storage
  argument, which left the two ADRs softly contradicting each other: 0002 claimed
  historical visits can be re-scored, 0005 said trails are reduced after N days.
  Rewritten to state the trade-off instead — the replay window and the privacy
  window are the same window. The contradiction was mine; the catch was the AI's.
- Skill also flagged vocabulary drift in my own edit: I wrote "evidence
  sufficiency" in ADR-0005's breakdown list, which is not a `CONTEXT.md` term
  (sufficiency is a threshold test, not a displayed quantity). Reverted to "conclusive ping count";
  final wording keeps both — attributed time inside/outside and dwell ratio lead the
  breakdown (the quantities the verdict is computed from), with
  conclusive ping count as supporting detail. Matches the verdict
  table's stored columns.
- Kept over the skill's original: my "score client-side, never upload the trail"
  alternative in ADR-0005 — a _more_ privacy-protective option rejected on
  security grounds, which the skill had not considered.
- Gap the grilling missed, caught by me on review: 22 questions
  never asked how an assignment gets its participant — one
  pre-assigned person, or an open pool participants claim from.
  The brief's phrasing ("a participant is assigned a task")
  smuggled the answer in, and the AI inherited it unexamined.
  Decided: pre-assigned for the slice, marketplace named as the
  likely real-product model in the design writeup's pushback
  section.

## 2026-09-02 — spec generation reversed a settled decision

Caught by cross-reading `spec.md` against Tuesday's grilling, before any
implementation existed.

- **What happened.** The grilling settled the judgement order as duration first:
  minimum-duration failure is `suspicious`, because "a 90-second visit is
  evidence, not an absence of it". `spec.md` §3 shipped the opposite order —
  sufficiency → duration → dwell — and supplied its own rationale for it
  ("with too little accountable time there is nothing to judge"), so it read as
  reasoned rather than as a slip. Nothing flagged the change.
- **Why it mattered.** The reversal was self-defeating in a way the prose hid.
  Under sufficiency-first, a 90-second all-inside sprint has ~90s attributed,
  fails the sufficiency gate, and returns `unverifiable` — it never reaches the
  duration check. The grilling's position had become unreachable for exactly the
  visits it was decided about. The duration branch survived only for the narrow
  180–300s band.
- **The tell.** The spec kept the grilling's own justifying sentence — "a
  sprint-through _is_ evidence, so it is `suspicious`, not `unverifiable`" —
  directly beneath a code block that made it unreachable. Retained rationale
  sitting under reversed logic is the signature of a mechanical reordering, not
  a considered one. Worth generalising: prose and pseudocode in the same section
  are not cross-checked by the thing that writes them.
- **How close it came to being load-bearing.** Issue 02's first test case, "all
  pings inside spanning 90s → `unverifiable`", had already encoded the reversal
  as an assertion. The next step in the plan was to build issue 02 first, on
  Wednesday. One more step and the reversed decision would have been frozen as a
  passing test with a rationale attached, which is the hardest kind to reopen.
- **Decided.** Duration first. The argument that settles it is orthogonality:
  `visit_duration_s` is server-clock evidence, and every mechanism that justifies
  `unverifiable` — pocketed phone, denied permission, indoor accuracy — destroys
  pings without shortening the visit. So ADR-0003's protections survive intact,
  and the reorder touches only visits that are both too short for the task and
  too thin on pings. The decisive point is game-theoretic: under sufficiency-first
  a lazy participant scores _better_ by sprinting through (`unverifiable`) than by
  staying away (`suspicious`), which makes brevity the cheapest laundering
  strategy against the one rule the product exists to enforce.
- **Counter-case, accepted not dismissed.** Permission denied on a 60-second
  visit is now `suspicious` where it used to be `unverifiable`. Recorded in
  `spec.md` §3 rather than argued away: no innocent account exists of a 300-second
  task inside a 60-second visit, and false starts do not reach verification at all
  (an abandoned visit never gets a verdict), so a short visit is only judged when
  the participant explicitly ended it _and_ reported against it.
- **Applied to:** `spec.md` §3, `CONTEXT.md` (`Unverifiable`/`Suspicious` — the
  old wording defined `suspicious` purely in terms of presence, which the reorder
  outgrows), ADR-0003 (the "mechanism that produces `unverifiable`" claim now
  states _why_ it survives duration-first), `docs/design.md` (the "silence feeds
  UNVERIFIABLE, never SUSPICIOUS" measurement note), issue 02's table.
  Issues 04 and 08 turned out not to encode the order at all, and the §9 seed
  scenarios demo `EXPIRED`/`UNREPORTED` rather than verdicts — no change needed
  in either.
- **Second finding from the same trace, unrelated to the order.** The field was
  named `session_duration_s`, and `session` is a `CONTEXT.md` banned word for
  `visit`. It had reached `spec.md` §2, §3 and issue 09 unnoticed. Renamed to
  `visit_duration_s` while it is still only markdown. Same class of drift the ADR
  revision pass caught with "evidence sufficiency" — the vocabulary list does not
  enforce itself, and generated artifacts are where it leaks.
- **Third finding.** §3 gave the signature as `(trail, target, config)`, with
  `session_duration_s` appearing in the judge block from nowhere. Harmless under
  sufficiency-first; fatal under duration-first, where an implementer deriving it
  from first-and-last ping would score every permission-denied visit as
  zero-duration and therefore `suspicious`. Signature now names it explicitly and
  issue 02 calls out the trap.

## 2026-09-02 — build, issue 02 (/tdd)

- Agent's first distance test was circular: it asserted a value
  computed by the same spherical method as the code under test — a
  test that could never fail. Caught by the agent itself, and all
  distance expectations rederived from an independent formula
  (Vincenty on WGS84), with tolerance (0.7%) set to what the
  spherical model honestly achieves.
- Agent deviated from the issue's stated signature deliberately:
  `AssignmentTerms` instead of `target`, because CONTEXT.md defines
  Target Location as coordinates + radius, and verify never sees
  coordinates. Flagged rather than silently applied; accepted — the
  glossary held against the ticket.
- Agent added one rule beyond the spec: verify sorts the trail by
  received_at rather than trusting caller order (unsorted input
  produced negative attributed time). Justified via ADR-0002:
  replay against stored rows makes query order not verify's to
  assume. Accepted.
- Honest red/green accounting kept: 6 of 13 cycles genuinely red
  and driving code; the rest green on arrival, recorded as spec
  pins rather than claimed as TDD theatre.

## 2026-09-02 — issue 02 implemented, then reviewed by me

Built TDD with the tdd skill. Seams were agreed before any test was written,
which is the step that made the review below cheap: all four findings are about
the _boundary_, and the boundary was the thing we had argued about first.

- **My finding, and the sharpest one: `min_duration_s` vs `SUFFICIENCY_S`.**
  `SUFFICIENCY_S` is global, `min_duration_s` is per-assignment, and nothing
  connected them. Set a two-minute task and `verified` becomes unreachable —
  attributed time cannot exceed the visit it is measured over, so a flawless
  120s visit scores `unverifiable` forever. The 300s default hid it completely.
  The AI's contribution was placing the guard: I had suggested validating on
  `AssignmentTerms` construction, it argued the check belongs at assignment
  creation and must **not** live in `verify`, because a later `SUFFICIENCY_S`
  bump would then raise on every historical visit whose assignment predates it —
  breaking exactly the replay property ADR-0002 exists for. Correct, and I had
  not seen it. Now `check_terms()` + `spec.md` §1 invariant.
- **AI declined to write a test it could not justify.** I flagged
  `asin(sqrt(h))` as a domain risk. It searched 3M near-antipodal pairs, found
  `h` peaks one ulp above 1.0 and `sqrt` rounds it back to exactly 1.0, and said
  it could not produce the crash. It added the `min(1.0, ...)` clamp anyway on
  the grounds that `sin`/`cos` are platform libm calls with no correct-rounding
  guarantee, but tested the antipodal _distance_ rather than asserting an
  exception that never fires. Right call — the test I half-asked for would have
  been theatre.
- **`haversine_m` moved to `geo.py`.** My point, and it turned out stronger than
  I made it: `verify` never calls it. Zero internal callers is the tell. Ingest
  now does geodesy without importing the pure judgement core.
- **Two invariants documented rather than enforced.** Negative `unattributed_s`
  (pings outside the visit window) is a caller precondition on `verify`'s
  docstring: the function cannot distinguish a late write from a wrong duration,
  and clamping would bury the discrepancy. The unattributed class-transition gap
  now carries its rationale in code — we do not split a crossing interval because
  we never observe the crossing, and an invented number would sit in a field the
  business reads as measured.
- Hand-worked verification of the uncertainty-interval rule (the `d + a` /
  `d - a` cases), done on paper before accepting the issue 02 design:
  `docs/experiments/interval-rule-notebook.pdf`.

## 2026-09-03 — build, issue 03 (/tdd)

- Seams agreed before any test, as with issue 02. Agent put three
  choices up with recommendations; all three were taken. The one
  that mattered: `start_visit` as a separate function rather than a
  `START` event with a `None` from-state. It means `ABANDONED ->
ACTIVE` is not in the type at all, so ADR-0001's "no resurrection"
  is a thing the machine cannot express rather than a rule it
  enforces.
- The other one worth noting: `VisitCompleted` carries the verdict
  that `advance_assignment` then refuses to read. The agent's
  argument for it over a bare enum member — which has the cleaner
  signature — was that a bare member makes ADR-0004 unfalsifiable:
  no input to vary, so the test asserts nothing. Carrying the verdict
  turns the ADR into a parametrized test over all three values.
- Agent caught its own overclaim mid-build: it wrote a docstring
  saying a third assignment event "cannot be added without failing
  mypy's exhaustiveness check", then noticed the `match` falls
  through to the raise so mypy would say nothing. Rewrote it to the
  true and weaker claim — closed by default. Small, but it is the
  class of comment that gets believed for years.
- Exhaustive table was green on arrival. Rather than bank it, the
  agent mutated the implementation — smuggled `(ABANDONED, PING) ->
ACTIVE` into the table — confirmed the test failed, and reverted.
  This is the right instinct for a test that costs nothing to write
  and could easily assert nothing.
- Honest accounting again: 7 of 10 cycles genuinely red and driving
  code; 3 green on arrival, recorded as spec pins rather than
  claimed as TDD.
- Flagged for review rather than decided silently: `transitions`
  imports `Verdict` from `verification`, which is the opposite
  direction to the `geo.py` split, where zero internal callers was
  the tell. Argument offered is that `Verdict` is verification's
  output type rather than an incidental utility, and ADR-0004 is
  _about_ the coupling between a verdict and fulfilment.

## 2026-09-03 — issue 03 reviewed by me

Two open questions, both accepted. Recorded because one of them
changed the code and the other should not be reopened by the next
reader.

- **Import direction, accepted.** `Verdict` is verification's output
  type, not a shared utility, and ADR-0004 is precisely a policy
  about fulfilment consuming that output — the arrow states the
  truth. No cycle, no smell. A shared types module only if a third
  consumer ever appears.
- **The permissive `(ASSIGNED, COMPLETED)` cell, accepted with one
  change.** The agent had the reasoning right and put it in the
  issue's comments and in the test. Not enough: an unexplained
  permissive cell in an otherwise-paranoid table reads as a bug to
  any reviewer, and the explanation must live where the code is
  read. Now a comment on the fall-through in `start_visit`. General
  rule for the rest of this build — a deliberate hole in a defensive
  structure is documented in the structure, not in the ticket that
  created it.
- Agent pushed back correctly on the instruction while carrying it
  out: I asked for a comment on "that table row in transitions.py",
  and there is no such row — `start_visit` is two guards and a
  fall-through, so the permissive cell is the absence of a check.
  It said so in one sentence, applied the intent, and noted where
  the literal row actually lives.

## 2026-09-03 — issue 01, schema and seed

- Seams put up before any test again, three choices with
  recommendations. I overrode one: the agent recommended SQLite on
  the strength of issue 10's fresh-clone test, I chose Postgres. It
  took the override and then found the argument _for_ my choice that
  it had missed — `timestamptz` is native, so the repo's
  timezone-aware rule holds at the driver boundary instead of needing
  a custom type to defend it against SQLite's naive strings. It also
  flagged the cost without being asked: the fresh-clone test now
  needs `createdb`, and it updated `CLAUDE.md` rather than leaving
  that for Friday to discover.
- **Zero genuinely red cycles, stated as such.** A declarative schema
  has almost no red step — the tests fail on import and then pass.
  Rather than dress that up as TDD, the agent mutation-tested
  instead: eleven mutations of the shipped code, each one confirmed
  to break a specific test, each reverted. Dropping the index
  predicate, widening it to every state, moving it to the wrong
  column, `timezone=False`, storing enum names instead of values,
  removing the `check_terms` call. This is the second time it has
  reached for mutation when a test was green on arrival, and the
  honest "0 of 11" line in the issue is worth more than the number.
- Both review axes found something real, and both were things the
  agent had written itself and believed. The `database.py` docstring
  claimed migrations were "named in spec.md §10 as unbuilt alongside
  auth and trail purge" — §10 names six things and migrations is not
  among them. Same failure mode as issue 03's exhaustiveness
  overclaim: a true-sounding sentence that cites a real document and
  is not in it. It now says the opposite plainly — §10 does _not_
  cover this, so the decision is recorded where it was made.
- The other one is the better catch and came from the spec axis, not
  the agent: the seeded `PENDING_REPORT` visit that makes `UNREPORTED`
  demoable is itself non-terminal, so it holds the partial unique
  index and 409s anyone starting a visit against that assignment for
  three minutes. Two correct decisions colliding, invisible until
  someone traced §6 through §5 into the index. Kept as built — it
  self-heals and it is the 409 issue 07 has to handle anyway — with
  the interaction now a comment in `seed.py` and a line in the seed
  CLI's own output telling the demoer to run that assignment last.
- Vocabulary drift caught by the standards axis: the test fixture was
  named `session`, which `CONTEXT.md` lists among the words to avoid
  for **visit**. SQLAlchemy's own noun, so arguably fine, and renamed
  to `db` anyway. Cheaper to keep the banned word out entirely than
  to have every future reader adjudicate which sense is meant.
- Declined one review finding and said why: a `TargetLocation` type
  for the `(lat, lng, radius_m)` clump that `CONTEXT.md` does name.
  No second caller until issue 04, and it cited this repo's own
  `geo.py` precedent — moved on a real caller, not an anticipated
  one. It removed an `Assignment.terms` property it had written for
  the same reason, which is the harder half of that argument to
  apply to your own code.
- **Reversed the seeded-visit decision the day it landed.** I told it
  the report window is a task fact — same family as `radius_m` and
  `min_duration_s` — so it belongs on the assignment, and the seed
  should plant no visit at all. It took the reversal cleanly, moved
  `REPORT_DEADLINE_S` out of the operational timings into
  `DEFAULT_REPORT_DEADLINE_S`, and updated `spec.md` in three places
  rather than leaving the spec disagreeing with the code. Worth
  noting what it did _not_ do: it didn't defend the previous version,
  and it didn't quietly keep the seeded visit "just in case". The old
  reasoning stays in the issue as the reasoning that was reversed,
  with §9's own wording noted as always having fitted the new shape
  better.
- Two things it volunteered that I hadn't asked for and wanted: that
  `visit.report_deadline_at` must be _stamped_ at end rather than
  derived from the assignment on read, or widening a window later
  would make an already-late visit on-time again; and that with no
  Alembic, `create_all` won't add the new column to a database that
  already exists, so anyone holding one has to drop and rebuild. The
  second is the cost of the no-migrations seam coming due for the
  first time, one day after we chose it.
- The replacement tests were green on arrival again, and it said so
  again, in the same paragraph as the original eleven mutations —
  including that one of those eleven no longer exists to run because
  the row it mutated is gone. The temptation with a reversal is to
  quietly re-bank the test count; it didn't, and it corrected the
  count in the issue header instead (33 → 35).

## 2026-09-03 — build, issue 04, ping ingest

- Four seam questions answered before code, two argued back
  correctly. Stale-ping status: I framed it as a conflict shaped
  like 409; the agent argued 422 from the client's own rule in
  spec §8 — 409 is terminal for the participant page, and iOS
  resuming a tab can serve one cached stale fix from inside the
  store, so a 409 there would end a live visit over one bad
  reading. 422 means "dropped this reading, keep going." The
  status code choice is downstream of the state machine, and it
  had the better read of our own spec.
- The row-lock question: I asked it to name the race before
  recommending. It rejected the two obvious candidates
  (last_ping_at last-writer-wins is harmless; start is refereed
  by the partial unique index) and found the real one — between
  advance_visit reading ACTIVE and the ping INSERT sits an await,
  and under READ COMMITTED the sweeper's abandon or /end's seal
  can commit invisibly in that gap, landing a ping on a sealed
  trail (the negative-unattributed_s corruption issue 02
  documented as a caller precondition). SELECT ... FOR UPDATE OF
  visit makes check-and-write one decision. It created an explicit
  obligation on issues 05 and 08 to hold the same discipline.
- Two validations I never asked for, both real: accuracy_m ge=0 —
  classify reads accuracy as a half-width, so a NEGATIVE accuracy
  shrinks the uncertainty interval and turns a ping 900m outside
  into conclusively inside, a spoofing vector hiding in a type
  constraint; and reported_at as AwareDatetime, refusing naive
  timestamps rather than guessing a zone.
- Response model withholds judgement deliberately: PingAccepted
  carries received_at and nothing about classification, so a
  spoofer cannot play "walk until it says inside" — the
  live-feedback calibration risk from the design session, enforced
  at the schema level.
- Mutation accounting stayed honest: 12 of 13 caught, and the miss
  is documented on the test itself (the rollback, not the
  assertion, is what holds it).

## 2026-09-03 — build, issue 05, sweeper and event bus

- Mutation campaign found two REAL design defects, not test gaps:
  transition_visit's self-loop guard was unobservable from its only
  caller (ingest_ping discarded the return; transition_visit now
  returns list[Event] and ingest publishes it unexamined), and
  stop_sweeper's bare await would hang forever on a task that
  swallowed cancellation — in production, a server that never
  exits. Now bounded (asyncio.wait with a timeout) with a logged
  give-up: the process is going down either way, and the only thing
  left to do about a sweeper that will not stop is to say so.
- Six of sixteen first-pass mutations were initially missed; each
  closure is recorded per-mutation in the issue. The pattern in the
  misses: every sweep test handed the sweeper a bus, so publishing
  into a void passed everything — closed by one test that runs the
  real lifespan, real task, real bus.
- The 04 lock obligation was paid on all three sweeps, each with a
  staged-interleaving test that fails without the lock. Two of the
  three are locked against writers that don't exist until issue 08
  — the obligation was carried forward, not just satisfied locally.
- One spec-level finding handed forward rather than decided by the
  agent: an assignment can expire beneath a live visit (started
  16:59, deadline 17:00), leaving a completed report unable to
  fulfil. Implemented as spec-written, pinned by a test, and routed
  to me. Decided: option A — the expiry sweep skips assignments
  with a non-terminal visit; deadline means "start by". Recorded in
  issue 08 for implementation there.
- Context management became part of the work: at ~180k tokens the
  session entered a polish-and-verify loop on already-proven code
  (each pass producing a genuinely good but unnecessary refinement,
  then a fresh mutation round to verify it). Stopped with a closed
  three-item checklist; the report was extracted cleanly. Directing
  the agent includes noticing when its context, not its judgement,
  is driving.

## 2026-09-03 — end-to-end phone smoke test (passed)

- Real iPhone → CORS → API → state machine → bus → SSE, all live.
  201 on start, 202 pings at 15s cadence, deltas on the stream.
- Accidental full rehearsal of abandonment: the first (CORS-mute)
  visit timed out at 15 min and its ABANDONED event arrived on the
  stream unprompted — sweeper + bus + SSE proven end-to-end by a
  real bug.
- Found for issue 07: iOS refuses geolocation to non-HTTPS origins
  — dev server must run --ssl. Smoke test completed with stubbed
  coords; real GPS was proven separately on 2026-09-01.
- CORS middleware added (was always needed for 07); GitHub SSH
  moved to 443 — home network blocks port 22.

## 2026-09-04 — build, issue 08, report and verification trigger

- Both endpoints landed with the lock discipline issue 05 pre-paid:
  `/end` takes the visit lock `ingest_ping` was written to wait on;
  `/report` locks visit and assignment and does its four writes in
  one transaction, commit then publish. The two pre-paid
  interleaving tests passed unchanged on first run.
- verify()'s two caller invariants are upheld at the trigger and
  pinned by one test with a hand-computed 500-second trail (ten
  pings inside the window, one past it): the stray ping moves
  three numbers, so an unbounded query cannot pass.
- Decision A implemented as a NOT EXISTS in the expiry sweep's
  query, not a branch in the pure machine. The pinning test
  flipped, spec §5/§7 now say "start by", issue 05's paragraph
  rewritten. One residual window recorded on the sweep, not fixed:
  open_visit does not lock the assignment.
- The 06 handoff decided: the verdict rides the COMPLETED delta.
  The argument (ADR-0005: the breakdown is exactly what the
  business may see; one-renderer: COMPLETED is reachable only by a
  report, so the field is not an origin in disguise) is in issue
  06's comments, the shape is in issue 09's notes, and the type
  enforces it — a COMPLETED event without a verdict is
  unconstructible.
- Mutation pass, four mutations on the trigger wiring, one
  survivor: scoping the report handler's lock to the visit alone
  passed everything, because the pre-paid expiry-race test holds
  the assignment lock by hand and so proves the sweep's side only.
  Closed by staging the race through the endpoint; without the
  lock it is a lost update, FULFILLED written over EXPIRED.

## 2026-09-04 — issue 07 scaffolding + secure-origin proof

- Verified on iPhone at 1:45am: Angular dev server with
  self-signed HTTPS gives Secure context: yes and real
  geolocation (granted, ±20m) after accepting the cert warning.
  The iOS geolocation wall from the smoke test is closed — no
  mkcert needed.
- Agent pinned Angular 21 after the newest CLI refused Node
  20.19; recorded in README. Mixed content flagged as the next
  wall; decision made: proxy /api through the dev server.
- Issue 08's mutation pass caught a fourth real defect class:
  report handler's lock scoped to visit alone survived sabotage
  (pre-paid test held the assignment lock by hand); closed with
  an endpoint-staged race test — without it, FULFILLED could
  overwrite EXPIRED.

## 2026-09-04 — full participant flow field-tested on iPhone

- Complete happy path on real hardware over the HTTPS dev server +
  proxy: landing → consent (permission checked before Start is
  offered) → active visit with Wake Lock and live ping counter →
  End → report → done. Real GPS throughout.
- First live verdict observed on the SSE stream: an 88s sofa visit
  scored `suspicious` — the duration-first gate (the reversed
  decision from 2026-09-02) firing on real data. Breakdown carried
  on the COMPLETED delta per the 08 decision: 6 conclusive pings,
  all outside (seeded coordinates ≠ my sofa), dwell 0.0, config v1.
- FULFILLED followed immediately despite the suspicious verdict —
  ADR-0004 observed on the wire: the verdict advises, fulfilment
  ignores it.
- Sweeper expired assignment #2 mid-test, unprompted, with no live
  visit to protect it — decision A's guard correctly not applying.
- Not exercised on-device this session: the 409-terminal screen
  and the 15-minute abandonment path (proven earlier via the
  smoke-test accident on 2026-09-03); permission-denied branch
  verified in UI logic only.
