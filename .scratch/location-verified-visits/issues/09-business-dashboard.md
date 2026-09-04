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

## Comments

**2026-09-04 — implemented.** `frontend/src/app/dashboard/`, one component on
`/dashboard`; `api.service.ts` gains the dashboard wire types and
`dashboardStream()`, the `EventSource` on `/api/dashboard/stream`. Nothing
server-side changed. No component tests, per issue 07's rule; the page was
driven in headless Chrome over the DevTools protocol against a seeded test
database: snapshot rendered, a start delta appended an attempt (count 0 → 1),
the end delta moved it to `PENDING_REPORT`, and the report's two deltas landed
as `COMPLETED` with `suspicious · 0 min · 0 min · 0% · 0 of 0 · v1` beside
`FULFILLED` (ADR-0004, on screen). Prettier and `ng build` green.

**One table, one template.** `<tbody>` per assignment, its header row a
`th[scope=rowgroup]` carrying business, participant, state, "start by", terms
and the attempt count (`visits.length`, no second field); one row per attempt
with state, started, "since", and for a verdict the bucket, inside, outside,
dwell, conclusive-of-total pings, duration and config version. Minutes to one
decimal. A `COMPLETED` row with no verdict says so rather than breaking the
table. Every state renders as itself — no rewording, no colour coding.

**The model is three pure functions.** `fromSnapshot`, `applyVisit`,
`applyAssignment`, each returning new rows. `applyVisit` is idempotent as
`dashboard.py` promises the deltas are: an unknown visit id is appended (a
start), a known one has its state set, and a delta that repeats the snapshot
lands on a row already in that state. The `COMPLETED` delta's breakdown
becomes the row's verdict with the delta's `at` as `computed_at`, so the
template never knows which source it came from — the one-renderer property
issue 06 and 08 argued for, held on the client.

**"Since".** A row has one time besides `started_at`: when it entered its
current state, as far as the page knows. From the snapshot that is
`computed_at`, else `ended_at` (the sweeper stamps it on `ABANDONED` too),
else `started_at`; from a delta it is `at`. `EXPIRED`, `ABANDONED` and
`UNREPORTED` render as themselves with that time and nothing else. The
assignment row gains "since <at>" only once a delta has said so; the snapshot
carries no transition time for an assignment and none is invented.

**Found and fixed on the dev proxy: an API restart left the page stuck
"Live".** The direct stream closes the instant the API exits (`curl -N`,
exit 18); through the Angular dev server's proxy the page's connection stayed
open until curl's own timeout, because the bundled http-proxy pipes the
upstream response into the page's and never ends it when the upstream closes
(Vite's error handler only acts before headers are sent). An `EventSource`
that never sees the close never reconnects, so the reconnect-and-re-snapshot
argument was silently void in development. `proxy.conf.json` became
`proxy.conf.mjs` with a `configure` hook that ends the page's response on
the upstream response's `close`. Re-measured: both sides close within the
API's graceful-shutdown timeout, and in the browser the status flips to
"reconnecting" and back to "Live" on a fresh snapshot. A real reverse proxy
does this on its own; issue 10's deployment notes should say the page and
the API share an origin behind one.

**Not built, as fenced.** No map, no trail, no coordinates (ADR-0005; the
wire carries none and the model has no field for one), no liveness counter,
no per-ping anything (ADR-0006). The connection line is the socket's state,
not a clock. The internal audit map route is not started.
