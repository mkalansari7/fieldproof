# Ping ingest and the trust boundary

Status: ready-for-agent
Blocked by: 01, 02

`POST /api/visits/{id}/pings`. INSERT-only: compute `distance_m` and
`classification` once at write and store them, so verification stays an aggregate
(ADR-0002).

Rules from `spec.md` §4, each with a test:

- `received_at` server-stamped; `reported_at` stored but never scored on.
- Not `ACTIVE` → 409. Always.
- `reported_at` older than `BACKFILL_GRACE_S` → rejected. Test **both sides** of
  the boundary, 59s and 61s.
- Updates `visit.last_ping_at` — this is what the sweeper reads.

The 409 is not an error path, it is the expected outcome of a phone locked past
15 minutes. Return something the client can act on (issue 07).
