# fieldproof

## Agent skills

### Issue tracker

Issues live as markdown files under `.scratch/<feature-slug>/` in this repo. See `docs/agents/issue-tracker.md`.

### Triage labels

The five canonical triage roles, used verbatim as `Status:` values. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: `CONTEXT.md` and `docs/adr/` at the repo root. See `docs/agents/domain.md`.

## Python

Python 3.12, `src/` layout, package at `src/fieldproof/`. Angular lives in
`frontend/`. Dependencies and all tool config are in `pyproject.toml`.

```bash
~/.pyenv/versions/3.12.2/bin/python -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

Then two databases, `fieldproof` and `fieldproof_test`, by either path.

**A local Postgres**, on the unix socket. The default, and needs no configuration:

```bash
createdb fieldproof && createdb fieldproof_test
```

**Or `docker-compose.yml`**, for a machine with no Postgres on it. It binds
`127.0.0.1:5433`, so a local install on 5432 keeps working alongside it:

```bash
docker compose up -d --wait
export FIELDPROOF_DATABASE_URL=postgresql+asyncpg://fieldproof:fieldproof@localhost:5433/fieldproof
export FIELDPROOF_TEST_DATABASE_URL=postgresql+asyncpg://fieldproof:fieldproof@localhost:5433/fieldproof_test
```

Either way, then:

```bash
.venv/bin/python -m fieldproof.seed          # builds the schema, then resets the demo data
```

`FIELDPROOF_DATABASE_URL` and `FIELDPROOF_TEST_DATABASE_URL` override the socket
default. The compose path needs them rather than merely allowing them: it is
reached over TCP, which the socket-shaped default URL cannot express.
`tests/conftest.py` only connects for tests that ask for the `db` fixture, so the
pure modules stay runnable with no database at all.

Run all four before committing Python. Nothing lands red.

```bash
.venv/bin/ruff check . --fix
.venv/bin/ruff format .
.venv/bin/mypy
.venv/bin/pytest
```

### Conventions

- **mypy runs `strict`.** No untyped defs, no implicit `Any`. If a third-party
  library forces `Any`, isolate it at the boundary rather than letting it spread
  inward.
- **`# type: ignore` must carry its code** — `# type: ignore[arg-type]`.
  Enforced by `ignore-without-code`; a bare ignore also silences the next bug.
- **Datetimes are always timezone-aware** (`DTZ`). This codebase is almost
  entirely about timestamps, and `received_at` and `reported_at` mean different
  things (`spec.md` §4). A naive datetime here is a correctness bug, not a style
  preference.
- **No blocking calls inside `async def`** (`ASYNC`). The sweeper and the SSE
  fan-out share one event loop; one blocking call stalls every connected
  dashboard.
- **`unused-awaitable` is on.** A forgotten `await` in the sweeper fails silently
  and looks exactly like a working sweeper.
- **Verification stays pure** (ADR-0002): the scoring module takes no clock, no
  database and no I/O. Callers pass values in. This is what makes the test table
  in issue 02 possible and what keeps re-scoring honest.
- **Use `CONTEXT.md`'s vocabulary in identifiers.** `visit`, `ping_trail`,
  `verdict`, `dwell_ratio`, `attributed_time` — not `session`, `track`,
  `status`, `score`. The glossary lists the words to avoid, and they are the ones
  that will otherwise creep in.
