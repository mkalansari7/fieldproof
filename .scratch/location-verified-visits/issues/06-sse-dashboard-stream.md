# SSE: snapshot then stream

Status: ready-for-agent
Blocked by: 05

`GET /api/dashboard/stream`. First event is the complete dashboard snapshot —
byte-identical to `GET /api/dashboard` — then deltas (ADR-0006).

Reconnection needs no `Last-Event-ID`, no replay buffer, no gap detection: the
client re-snapshots. Keep it that way; the correctness argument is the whole
reason this transport was chosen over polling.

In-process fan-out: a registry of `asyncio.Queue`, one per connected client,
removed on disconnect. Watch for the leak — a client that vanishes without
closing cleanly must not hold a queue forever.

Not tested (issue 10 rationale): a broken stream is loud in the demo.
