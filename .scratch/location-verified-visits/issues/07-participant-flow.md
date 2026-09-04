# Participant flow (Angular)

Status: ready-for-agent
Blocked by: 04

Zero styling budget. Unstyled semantic HTML reads as deliberate; a half-finished
design system reads as unfinished.

Screens: assignment landing → consent → active visit → end → report → done.

Consent before `ACTIVE` (ADR-0005): what is collected, that it stops when the
visit ends, that the page must stay open. Persistent in-session indicator while
tracking.

- `setInterval` + `getCurrentPosition`, **not** `watchPosition` — it fires on
  movement, and a participant standing still in a shop may produce almost nothing.
- Screen Wake Lock on start, best-effort, released on end. Load-bearing: a
  pocketed phone produces no evidence at all.
- Permission denied → start anyway, having told them **first** that the visit
  will be unverifiable. Never block the start (ADR-0003 reasoning).
- **409 on ping is terminal**: stop the interval, release the lock, show
  *"This visit was closed after 15 minutes without a location update. You can
  start a new visit."* with a path to do so.

The 409 handler is the most likely bug in the demo and the one the measurement
predicts will fire naturally. Build it, do not defer it.

## Comments

**2026-09-04 — implemented.** `frontend/src/app/participant-flow/` holds the
whole flow in one component: landing → consent → active → (closed | report) →
done. Decisions worth knowing:

- `/api` is proxied through the dev server (`proxy.conf.json`), so the
  HTTPS page never issues a plain-HTTP request. `API_BASE_URL` defaults to
  the empty string.
- `GET /api/assignments/{id}` added (spec §6): the task's terms and `state`,
  no target coordinates, no verdict. The page uses `state` to refuse a Start
  button on an `EXPIRED`/`FULFILLED` assignment rather than let it 409.
- The consent screen owns the location call. Start is only offered after one
  `getCurrentPosition`, so a denied participant reads "unverifiable" before
  they tap it. Denied, timed out and unsupported are three different
  messages; none blocks Start.
- 409 on ping *or* on End goes to the same closed screen with the §8
  sentence: both mean the sweeper closed the visit under the page.
- The API's error body has two shapes — `{detail: {reason, message}}` from
  `HTTPException` (404, 422) and `{reason, message}` from the
  `IllegalTransitionError` handler (409). `apiError()` in `api.service.ts`
  reads both. Not changed on the server: the tests pin both.
- Wake Lock is re-requested on `visibilitychange`, because the browser drops
  it when the tab hides and a lock that is not re-taken is no lock at all.
