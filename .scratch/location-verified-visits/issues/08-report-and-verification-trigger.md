# Report submission and verification

Status: ready-for-agent
Blocked by: 01, 02, 03

`POST /api/visits/{id}/report`: persist the report, transition
`PENDING_REPORT -> COMPLETED`, run `verify` over the sealed trail, persist the
verdict with its full breakdown and `scoring_config_version`, move the assignment
to `FULFILLED`, publish to the bus.

One transaction. A visit that is `COMPLETED` without a verdict row is a state the
dashboard cannot render.

Fulfilment does not consult the verdict (ADR-0004).
