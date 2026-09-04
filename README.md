# fieldproof

fieldproof verifies that a field participant physically attended an assigned
location, from the position evidence their own phone browser produces during
the visit. A participant opens an assignment link, consents, keeps the page open
while they do the task, ends the visit and writes a report; the server scores
the sealed ping trail into a verdict (`verified`, `suspicious` or
`unverifiable`) with the breakdown that produced it. The business watches
verdicts arrive live on a dashboard and never sees the trail itself.

Where things are:

| What | Where |
| --- | --- |
| Glossary, the vocabulary used in code | `CONTEXT.md` |
| The six decisions, each with rejected alternatives | `docs/adr/` |
| System writeup: architecture, algorithm, state machines, measurement, scale, limitations, tests | `docs/design.md` |
| AI process note and the agent configuration used | `docs/ai-process.md`, `docs/agent-config/` |
| Running log of the AI collaboration | `docs/ai-log.md` |
| Spec, tickets and handoff for the build | `.scratch/location-verified-visits/` |
| Backend (Python 3.12, FastAPI, SQLAlchemy async, Postgres) | `src/fieldproof/` |
| Frontend (Angular 21) | `frontend/` |

## Prerequisites

- **Python 3.12.**
- **Node 20.19 or later.** Angular is pinned to 21.x because the newer CLI
  refuses Node 20.19, which is what a stock machine is likely to have.
  `engines` in `frontend/package.json` records the accepted range
  (`^20.19.0 || ^22.12.0 || >=24.0.0`). Upgrading Angular means upgrading
  Node first.
- **Postgres**, by one of two paths: a local install reachable on the unix
  socket, or Docker with Compose v2.23 or later. Both are below; pick one.
- For the phone demo: an iPhone or Android phone on the same Wi-Fi as the
  machine running the servers.

## From clone to running

### 1. Clone and install the Python package

```bash
git clone git@github.com:mkalansari7/fieldproof.git
cd fieldproof
python3.12 -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

`python3.12` is whatever runs 3.12 on your machine; `CLAUDE.md` shows the
pyenv form. The install brings `uvicorn[standard]`, which serves the API in
step 4.

### 2. A database, by either path

**Path A, a local Postgres on the unix socket.** This is the default and needs
no configuration:

```bash
createdb fieldproof && createdb fieldproof_test
```

**Path B, `docker-compose.yml`.** For a machine with no Postgres on it. It binds
`127.0.0.1:5433`, so a local install on 5432 keeps working alongside it:

```bash
docker compose up -d --wait
export FIELDPROOF_DATABASE_URL=postgresql+asyncpg://fieldproof:fieldproof@localhost:5433/fieldproof
export FIELDPROOF_TEST_DATABASE_URL=postgresql+asyncpg://fieldproof:fieldproof@localhost:5433/fieldproof_test
```

The two exports are required on this path, not optional: the container is
reached over TCP, which the socket-shaped default URL cannot express. Set them
in every shell that runs the seed, the API or the tests. The compose file
creates both databases itself.

### 3. Build the schema and seed the demo data

```bash
.venv/bin/python -m fieldproof.seed
```

Creates any missing tables, then resets the data to three assignments for one
participant and prints their links. Re-run it before every demo: two of the
three assignments carry deadlines relative to the moment the seed ran, so that
`EXPIRED` and `UNREPORTED` can be watched happening inside three minutes.

There is no migration tool. If the schema changes under a database you already
have, drop and rebuild it (`dropdb fieldproof && createdb fieldproof` on path A,
`docker compose down -v && docker compose up -d --wait` on path B), then seed
again.

### 4. Run the API

```bash
.venv/bin/uvicorn fieldproof.api:app --timeout-graceful-shutdown 2
```

Listens on `http://127.0.0.1:8000`. The flag is not optional once a dashboard
is open: a Server-Sent Events stream is an in-flight response until the tab
closes, and uvicorn's default shutdown waits for in-flight responses without
bound. With the flag, Ctrl-C brings the server down in about two seconds and the
background sweeper stops cleanly after it. The phone never talks to this port
directly; the frontend dev server proxies to it.

### 5. Run the frontend

```bash
cd frontend
npm install
npm start
```

Serves `https://0.0.0.0:4200` with a self-signed certificate that Angular
generates fresh on every start, and proxies `/api` to the API from step 4, so
start the API first. HTTPS is not optional: iOS refuses `navigator.geolocation`
to a non-secure origin, and the proxy is what keeps the HTTPS page from making a
plain-HTTP request the browser would block as mixed content.

### 6. Get the phone past the certificate warning

1. Find the machine's LAN address: `ipconfig getifaddr en0` on macOS,
   `hostname -I` on Linux.
2. On the phone, open `https://<lan-ip>:4200/a/<assignment-id>` (the seed
   printed the ids; they are also listed below).
3. Safari shows **This Connection Is Not Private**. Tap **Show Details**, then
   **visit this website**, then **Visit Website** on the confirmation. Chrome on
   Android shows **Your connection is not private**: tap **Advanced**, then
   the **Proceed (unsafe)** link.
4. The landing page loads. Tap through to consent; the page asks for location
   permission before it offers **Start**, and tells you the visit will be
   unverifiable if you deny it.

The certificate is regenerated on every `npm start`, so the warning comes back
after every restart of the frontend. Accept it again.

The seeded target coordinates are three places in London
(`src/fieldproof/seed.py`). For a real-GPS test where you actually stand inside
the 100 m radius, edit them to where you are before seeding; otherwise a visit
scores `suspicious`, which is also a fine demo.

### 7. The two demo URLs

| Page | URL | Open it on |
| --- | --- | --- |
| Participant flow | `https://<lan-ip>:4200/a/<assignment-id>` | the phone |
| Business dashboard | `https://localhost:4200/dashboard` | the machine (accept the certificate warning once) |

The three assignment ids are fixed across reseeds so links survive:

| Assignment | Id | What it demonstrates |
| --- | --- | --- |
| Northwind Coffee | `00000000-0000-4000-8000-000000000001` | the normal path: start, ping, end, report, verdict |
| Halcyon Books | `00000000-0000-4000-8000-000000000002` | `EXPIRED`: the sweeper expires it 180 s after seeding, with no one touching it |
| Pelago Pharmacy | `00000000-0000-4000-8000-000000000003` | `UNREPORTED`: end a visit, leave it, and the sweeper lapses it 180 s later |

Open the dashboard first, then run the flow on the phone: every transition,
including the sweeper's, appears on the dashboard without a refresh. Locking
the phone for more than 15 minutes mid-visit is also a demo: the sweeper
abandons the visit and the phone's page shows the closed screen on its next
ping.

## Running the tests

Four gates, run in this order before committing Python. Nothing lands red.

```bash
.venv/bin/ruff check . --fix
.venv/bin/ruff format .
.venv/bin/mypy
.venv/bin/pytest
```

`ruff` lints and formats; `mypy` runs strict over `src` and `tests`; `pytest`
runs 269 tests. Only the tests that ask for the `db` fixture touch Postgres,
and they use `fieldproof_test` (created in step 2 on either path), never the
demo database. The pure modules, verification and the state machines, run with
no database at all.

The frontend has no test suite by design (see the tests section of
`docs/design.md`); `npm run build` in `frontend/` type-checks it.
