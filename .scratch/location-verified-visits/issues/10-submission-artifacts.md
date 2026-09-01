# Submission artifacts

Status: ready-for-human
Blocked by: 07, 09

Friday daytime. These are what the brief actually scores.

- **Design writeup** + one diagram (state machine is the most useful here). Fold
  in `docs/design.md`'s measurement — a measured platform constraint is stronger
  than a cited one.
- **Decision log**, 3–5 entries, derived from `docs/adr/`: 0001, 0002+0003 as one
  "verification model" entry, 0004, 0005, 0006. Each already carries its
  considered alternatives.
- **Pushback section** — a named section, not a footnote. Geolocation is a
  laziness filter, not fraud prevention: honest and dishonest trails cost about
  the same to produce, and the fix is inverting that cost (QR/NFC at site, timed
  photo challenge). The suspension measurement is the evidence — a signal that
  vanishes when the user does the most natural thing with their phone.
- **AI process note** from `docs/ai-log.md`, plus `CLAUDE.md`, `docs/agents/`,
  the skill config and the grilling transcript, unedited.
- **Test rationale**: tested where bugs are invisible (scoring, state machine,
  trust boundary); skipped where they are loud (E2E, SSE, components, CRUD).
- **README**: setup and run, verified by a **fresh clone into a new directory**.
  The brief says someone should get it running without asking questions.
- **Scale answer**: ~67 writes/sec at a thousand sessions is nothing for
  Postgres; what matters is that ingest is INSERT-only and scoring is an
  aggregate. Real path — queued/batched ingest, time-partitioned ping table,
  shared event bus — named, not built.
