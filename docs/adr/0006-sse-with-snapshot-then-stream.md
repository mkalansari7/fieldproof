# SSE with snapshot-then-stream

The dashboard updates over Server-Sent Events. On every connect and reconnect the
server sends the complete current dashboard state as the first event, then deltas.

The motivating case is not participant activity, which is sparse. It is that three
of this system's transitions — `ABANDONED`, `UNREPORTED`, `EXPIRED` — are produced
by the sweeper and have **no HTTP request behind them**. Nothing a client does
causes them. A push channel is the direct expression of that: the server is the
only party that knows a visit just died.

## Considered options

- **Polling.** Sufficient for the brief as written, and about five lines. Rejected
  narrowly: the transitions above are server-originated, so polling makes the
  dashboard's own refresh timer the only way a death ever surfaces, which inverts
  where the knowledge lives. Reasonable people would pick polling here and the
  slice would work.
- **WebSockets.** A bidirectional channel where every message travels one way, and
  infrastructure to justify not using. The dashboard never writes.
- **SSE with `Last-Event-ID` replay.** The conventional way to survive a dropped
  connection. Rejected because it needs stored, ordered, retained events — real
  work — where re-snapshotting on connect is correct by construction.

## Consequences

Reconnection needs no event store, no replay buffer and no client-side gap
detection: the client re-snapshots. Fan-out is an in-process registry of `asyncio`
queues.

The live in-flight view — a ticking "last seen Ns ago" counter and a map — was
scoped in and then cut for time. The dashboard still lists visits in every state
and updates as events arrive; only the per-second liveness is gone. Had it been
kept, ping-level events would need coalescing to roughly one per visit per 3s,
since no dashboard usefully renders 67 events a second.

This shares one boundary with the sweeper in `docs/design.md`: both assume a
single process, so both break under multiple workers. That is one deliberate
limitation, recorded once, whose production answer is a shared bus — Postgres
`LISTEN/NOTIFY` or Redis — plus a leader lock or `SELECT … FOR UPDATE SKIP
LOCKED` for the sweep.
