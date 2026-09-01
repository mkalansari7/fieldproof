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
