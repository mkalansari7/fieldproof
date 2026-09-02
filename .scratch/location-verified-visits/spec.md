# Location-Verified Visit Tracking — implementation spec

Vocabulary is `CONTEXT.md`. Rationale is `docs/adr/`. This file is the buildable
detail: constants, schema, algorithm, endpoints, and the cut line.

Deadline: **Friday 7pm**. Plan in `handoff.md`.

## 1. Constants

Judgement policy — a frozen dataclass in code, `version` stamped on every verdict
(ADR-0002). Changing any of these is a code change with a git history, not a row
edit.

| Constant | Value | Why |
| --- | --- | --- |
| `SUFFICIENCY_S` | `180` | Below ~3 min of accountable time, a ratio is noise. |
| `DWELL_RATIO_MIN` | `0.80` | Allows a fifth of accountable time outside — GPS drift, stepping out. |
| `GAP_ATTRIBUTION_LIMIT_S` | `60` | Longest gap over which continuity is assumed. Deliberately the same 60s as the ingest backfill grace: one concept, two uses. |
| `SCORING_CONFIG_VERSION` | `"v1"` | Bump on any change above. |

Per-Assignment columns — venue and task facts that legitimately vary (ADR-0002,
and the reason config is split rather than global):

| Column | Default | Why |
| --- | --- | --- |
| `radius_m` | `100` | **Must clear the indoor accuracy floor.** Conclusive-inside needs `d + a < R`; indoor accuracy runs 30–100m, so R=50 makes every visit unverifiable. A kiosk and a shopping mall genuinely differ. |
| `min_duration_s` | `300` | A coffee run and a bank branch audit are not the same task. |

**Invariant: `min_duration_s >= SUFFICIENCY_S`.** These two are set in different
places — one per assignment, one in global config — and the interaction is not
obvious. Attributed time is measured over the visit, so it can never exceed the
visit duration. A two-minute task against a 180s sufficiency threshold therefore
admits visits that clear the duration gate and *cannot* clear the sufficiency
one: a flawless visit, every ping conclusively inside, comes back `unverifiable`,
and the business sees an honest participant it can never verify. The default of
300 hides this. Enforced at assignment creation by `check_terms()`, which raises
`IncoherentTermsError`; deliberately **not** enforced inside `verify()`, because
raising there would turn a later `SUFFICIENCY_S` increase into an outage across
every stored visit whose assignment predates it, and replay under a newer config
is the property ADR-0002 exists to protect.

Operational timings:

| Constant | Value | Why |
| --- | --- | --- |
| `PING_INTERVAL_S` | `15` | Client cadence. |
| `SWEEP_TICK_S` | `10` | Sweeper loop. |
| `ABANDON_AFTER_S` | `900` | 15 min. Derived from measurement, not chosen — see `docs/experiments/` and `docs/design.md`. iOS Safari suspends JS *entirely* on screen lock; a 5-minute lock produced a single 297.8s gap. Any shorter timeout marks honest visits abandoned. |
| `REPORT_DEADLINE_S` | `86400` | 24h. `UNREPORTED` is unrecoverable — the participant has already left the site and cannot re-run a visit to attach prose — so it must be generous. |
| `BACKFILL_GRACE_S` | `60` | Pings whose client time is older than this are rejected (ADR: trust boundary, §4). |

## 2. Schema

```
assignment
  id, business_name, participant_name          -- seeded; no auth in this slice
  target_lat, target_lng, radius_m, min_duration_s
  deadline_at
  state          ASSIGNED | EXPIRED | FULFILLED
  created_at
  index (state, deadline_at)                   -- sweeper

visit
  id, assignment_id -> assignment
  state          ACTIVE | PENDING_REPORT | COMPLETED | ABANDONED | UNREPORTED
  started_at, ended_at, last_ping_at, report_deadline_at
  created_at
  index (state, last_ping_at)                  -- sweeper
  partial unique (assignment_id) where state in (ACTIVE, PENDING_REPORT)

ping
  id, visit_id -> visit
  lat, lng, accuracy_m
  reported_at        -- client clock. Tamper signal only. NEVER scored on.
  received_at        -- server clock. The basis for all ordering and scoring.
  distance_m         -- computed once at write (ADR-0002)
  classification     INSIDE | OUTSIDE | INCONCLUSIVE  -- computed once at write
  index (visit_id, received_at)

report
  id, visit_id -> visit UNIQUE, body, submitted_at

verdict
  id, visit_id -> visit UNIQUE
  verdict            verified | suspicious | unverifiable
  dwell_ratio, inside_s, outside_s, unattributed_s, attributed_total_s
  conclusive_pings, total_pings, visit_duration_s
  radius_m, min_duration_s              -- snapshotted from assignment
  scoring_config_version, computed_at
```

`verdict` holds the full breakdown so the dashboard needs no recomputation and a
stored result is traceable to the rules that produced it.

## 3. Verification algorithm

Pure. No I/O, no clock. `(trail, target, visit_duration_s, config) -> Verdict`.

`visit_duration_s` is `ended_at - started_at`, server clock, **passed in — never
derived from the trail**. The trail is empty in exactly the case where this value
decides the verdict, so deriving it from first and last ping would collapse the
zero-ping case into a zero-duration one.

**Classify** (at ingest, stored on the row) — `d` = haversine distance, `a` =
reported accuracy, `R` = `radius_m`:

```
INSIDE        if d + a < R
OUTSIDE       if d - a > R
INCONCLUSIVE  otherwise
```

No accuracy cutoff: `accuracy: 800` against `R=100` is already inconclusive
(ADR-0003).

**Attribute** — walk consecutive *conclusive* pings in `received_at` order:

```
for each adjacent pair (p, q) of conclusive pings:
    gap = q.received_at - p.received_at
    if gap <= GAP_ATTRIBUTION_LIMIT_S and p.class == q.class:
        attribute gap seconds to p.class
    else:
        unattributed
```

Inconclusive pings are skipped, not treated as breaks. Everything unattributed —
pocketed phone, denied permission, disagreeing endpoints — counts for neither
side. Absence of evidence is not evidence.

**Judge** — order matters:

```
attributed_total = inside_s + outside_s
dwell_ratio      = inside_s / attributed_total   (0 if total == 0)

if visit_duration_s < min_duration_s:        suspicious
elif attributed_total < SUFFICIENCY_S:       unverifiable
elif dwell_ratio >= DWELL_RATIO_MIN:         verified
else:                                         suspicious
```

**Duration first**, and it is the only gate that does not read the trail.
`visit_duration_s` is server-clock evidence, outside the participant's reach and
unaffected by every mechanism that justifies `unverifiable`: a pocketed phone, a
denied permission and a 40m indoor accuracy each destroy pings, and none of them
shorten the visit. The two tests are therefore orthogonal, and putting duration
first reclassifies exactly one region — a visit both too short for the task *and*
too thin on location evidence.

That region is `suspicious`, deliberately. A 90-second all-inside sprint is
evidence, not an absence of it. Under sufficiency-first it never reaches the
duration check at all (90s attributed < `SUFFICIENCY_S` → `unverifiable`), which
makes brevity the cheapest way to launder a visit that was never performed: a
lazy participant scores strictly better by sprinting through than by staying
away. A rule this product exists to enforce cannot be gameable in that direction.

Counter-case, recorded rather than dismissed — permission denied on a 60-second
visit is `suspicious` here and would be `unverifiable` under sufficiency-first.
Accepted. There is no innocent account of performing a 300-second task inside a
60-second visit, and the verdict rests on the server clock rather than on the
missing pings. False starts cost nothing either: an abandoned visit never reaches
verification (§5), so a short visit is only ever judged when the participant
explicitly ended it *and* submitted a report against it.

Zero pings on a visit of adequate length still falls out as `unverifiable` with
no special case: duration passes, then `attributed_total == 0 < SUFFICIENCY_S`.

## 4. Trust boundary (ingest)

- `received_at` is server-stamped and is the sole basis for ordering and scoring.
- `reported_at` is stored, never scored on; large skew is a tamper signal.
- A ping for a visit not in `ACTIVE` is **409**, always.
- A ping whose `reported_at` is more than `BACKFILL_GRACE_S` old is rejected.
- No client-side buffering and flush-on-resume. It is incompatible with the
  grace window, and gaps are legitimate signal rather than data to be recovered.

## 5. State transitions

The exhaustive table. Anything absent is rejected — tests assert this (issue 03).

**Assignment**

| From | Event | To |
| --- | --- | --- |
| ASSIGNED | visit completed | FULFILLED |
| ASSIGNED | deadline passed (sweep) | EXPIRED |

Fulfilment is independent of verdict (ADR-0004): a `suspicious` completed visit
still fulfils.

**Visit**

| From | Event | To |
| --- | --- | --- |
| — | start (assignment ASSIGNED, no non-terminal visit) | ACTIVE |
| ACTIVE | ping | ACTIVE, `last_ping_at` updated |
| ACTIVE | end | PENDING_REPORT (trail sealed) |
| ACTIVE | silence > `ABANDON_AFTER_S` (sweep) | ABANDONED |
| PENDING_REPORT | report submitted | COMPLETED + verification |
| PENDING_REPORT | past `report_deadline_at` (sweep) | UNREPORTED |

`ABANDONED`, `COMPLETED`, `UNREPORTED` are terminal. There is no resurrection: a
participant starts a new visit against the same assignment (ADR-0001).

## 6. Endpoints

```
GET  /api/assignments/{id}                 participant landing
POST /api/assignments/{id}/visits          start          409 if non-terminal visit exists
POST /api/visits/{id}/pings                202            409 if not ACTIVE
POST /api/visits/{id}/end                  -> PENDING_REPORT
POST /api/visits/{id}/report               -> COMPLETED, runs verification
GET  /api/dashboard                        snapshot (same payload as SSE event 1)
GET  /api/dashboard/stream                 SSE: snapshot, then deltas
```

## 7. Sweeper

One `asyncio` task, `SWEEP_TICK_S`. Each pass: `ACTIVE` with stale `last_ping_at`
→ `ABANDONED`; `PENDING_REPORT` past deadline → `UNREPORTED`; `ASSIGNED` past
deadline → `EXPIRED`. Every transition publishes to the same in-process bus the
API handlers use, so the dashboard cannot tell which path produced an event.

## 8. Client behaviour

- Consent screen before `ACTIVE`: what is collected, that it stops on end, that
  the page must stay open. Persistent in-session indicator.
- `setInterval` + `getCurrentPosition` at `PING_INTERVAL_S`. **Not**
  `watchPosition`, which fires on movement — a participant standing still in a
  shop may produce almost nothing.
- Screen Wake Lock requested on start, best-effort. Load-bearing, not polish: a
  pocketed phone produces no evidence at all.
- Permission denied: the visit starts anyway, and the participant is told
  **before** starting that it will be unverifiable.
- **409 on ping is terminal.** Stop the interval, release the Wake Lock, show
  *"This visit was closed after 15 minutes without a location update. You can
  start a new visit."* The measurement predicts this occurs naturally — including
  mid-demo.

## 9. Seed

Three assignments against one participant: a normal one; one with a short
`deadline_at` to demo `EXPIRED`; one whose visits get a short
`report_deadline_at` to demo `UNREPORTED` inside a debrief rather than in 24h.

## 10. Out of scope — stated, not hidden

- Auth. Participant and business are seeded.
- Trail purge / reduction to summary after N days. Specified in ADR-0005 as both
  the privacy answer and the storage bound; not implemented. Because it is not
  implemented, every visit in this slice stays re-scorable indefinitely — with
  purge on, re-scoring is possible only inside the retention window, and a
  reduced visit's verdict is frozen (ADR-0002). The replay window and the privacy
  window are the same window.
- Multi-worker. Sweeper and SSE fan-out are single-process by construction
  (ADR-0006).
- Live in-flight dashboard: ticking "last seen" counter and map. Cut for time.
- Android/Chrome suspension measurement. One device, one browser.
- Queued/batched ingest and time-partitioned ping table — the real answer at
  thousands of concurrent sessions. Named in the writeup, not built.
