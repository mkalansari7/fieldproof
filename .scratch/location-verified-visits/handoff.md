# Handoff — end of Tuesday 2026-09-01

Design is closed. Every decision has a recorded alternative. Nothing below is
waiting on a judgement call.

## Where things stand

- `CONTEXT.md` — glossary, canonical vocabulary. Use these words in code and tests.
- `docs/adr/0001`–`0006` — the six decisions, each with rejected alternatives.
- `.scratch/location-verified-visits/spec.md` — constants, schema, algorithm,
  transition tables, endpoints, cut line.
- `issues/01`–`10` — ordered, with `Blocked by` lines.
- `docs/design.md` — the iOS suspension measurement. `docs/experiments/pingtest.html`
  is the harness that produced it.
- `docs/ai-log.md` — running, appended as things happen.

## Plan

| When | Work |
| --- | --- |
| **Wed eve** | Issues 02 + 03 first (pure, no deps, done while freshest), then 01 → 04 → 05 → 06. End with a phone smoke test against the real device. |
| **Thu eve** | Issues 07 → 08 → 09. Unstyled. |
| **Fri day** | Issue 10. Fresh-clone test. Submit ~5pm. |

Buffer is Wednesday, on issues 05 and 06 — the async sweeper and SSE fan-out are
the identified risk, not Angular.

## Start here Wednesday

Issue 02, the verification function. Pure, no DB, no clock, table-driven tests.
It is the core of the product and it does not need issue 01 finished to be written
against in-memory fixtures — start it before the schema if the schema stalls.

## Three things not to lose

1. **The 409 handler** (issue 07). The measurement predicts a locked phone past
   15 minutes fires it naturally, including mid-demo. It is the most likely
   visible bug and the cheapest thing to look deliberate about.
2. **Fresh-clone test** before submitting. The brief asks for it explicitly.
3. **The pushback section** is scored (issue 10). It is a named section, not a
   footnote, and the suspension measurement is its evidence.

## Known open ends, all deliberate

Auth, trail purge, multi-worker, live in-flight view, Android measurement,
queued ingest. All in `spec.md` §10 — stated in the writeup, not hidden.
