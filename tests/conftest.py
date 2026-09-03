"""Database wiring for the tests that need one.

Postgres rather than an in-memory stand-in, because the constraints are the
thing under test: a partial unique index and a `timestamptz` round trip are
exactly what a substitute database would fail to reproduce, and they are what
issue 01 exists to get right.

`_schema` is deliberately *not* autouse. `test_geo`, `test_transitions` and
`test_verification` cover pure modules with no I/O (ADR-0002), and they stay
runnable on a machine with no Postgres on it.
"""

import asyncio
import os
from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from fieldproof.api import create_app
from fieldproof.database import (
    clear_tables,
    create_engine,
    create_schema,
    drop_schema,
    session_factory,
)
from fieldproof.events import EventBus

TEST_DATABASE_URL = os.environ.get(
    "FIELDPROOF_TEST_DATABASE_URL", "postgresql+asyncpg:///fieldproof_test"
)


@pytest.fixture(scope="session")
def _schema() -> None:
    """Drop and rebuild the test schema once per run.

    Runs its own loop rather than being an async fixture: the engine is created
    and disposed entirely inside it, so nothing crosses into the per-test loop.
    """

    async def reset() -> None:
        engine = create_engine(TEST_DATABASE_URL)
        try:
            await drop_schema(engine)
            await create_schema(engine)
        finally:
            await engine.dispose()

    asyncio.run(reset())


@pytest.fixture
async def db(_schema: None) -> AsyncIterator[AsyncSession]:
    """One database session per test, with every table emptied afterwards.

    Named `db` rather than `session`: CONTEXT.md lists "session" among the words
    to avoid, because it is the wrong name for a **visit**. SQLAlchemy's own noun
    is unrelated, and keeping it out of the fixture namespace means a test reading
    `session` never has to work out which of the two it means.

    Cleanup runs in a fresh session rather than rolling back an outer transaction,
    because several tests provoke an `IntegrityError` on purpose. That poisons the
    transaction they run in, and a teardown that depends on it is a teardown that
    stops working exactly where the interesting tests are.
    """
    engine = create_engine(TEST_DATABASE_URL)
    try:
        factory = session_factory(engine)
        async with factory() as session:
            yield session
        async with factory() as cleanup:
            await clear_tables(cleanup)
            await cleanup.commit()
    finally:
        await engine.dispose()


@pytest.fixture
async def factory(db: AsyncSession) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """A session factory on its own engine, for code that has to race `db`.

    The sweeper is given this rather than `db` for the same reason the `client`
    fixture gets its own engine: issue 04's row-lock obligation is a claim about
    what two transactions do to each other, and a sweep sharing the test's
    transaction cannot contend with it.

    Depends on `db` so pytest finalises this engine before `db` empties the
    tables.
    """
    engine = create_engine(TEST_DATABASE_URL)
    try:
        yield session_factory(engine)
    finally:
        await engine.dispose()


@pytest.fixture
def bus() -> EventBus:
    """A fresh bus per test. Nothing is subscribed until a test subscribes."""
    return EventBus()


@pytest.fixture
async def app(db: AsyncSession) -> AsyncIterator[FastAPI]:
    """The application, on its **own** engine, with no lifespan run.

    Its own engine and therefore its own connections, which is the entire point:
    a test that shared `db`'s session with the handler would be testing a
    handler that cannot race anything. Issue 04's boundary is about what two
    transactions do to each other — a ping arriving as a visit is sealed, two
    starts arriving at once — and neither is reproducible inside one.

    No lifespan, so no sweeper: three sweeps running against every test's
    fixtures would make the API tests race a background task none of them is
    about. `test_sweeper` drives the lifespan explicitly for the one test that
    is about it.

    Separate from `client` because a few tests need the app itself — its
    `state.bus` is the object the sweeper publishes to, and reaching it through
    a client's private transport is not a thing tests should be doing.

    It depends on `db` so that pytest finalises this fixture first: the engine
    is disposed, and only then does `db`'s teardown try to delete every row.
    """
    engine = create_engine(TEST_DATABASE_URL)
    try:
        yield create_app(engine=engine)
    finally:
        await engine.dispose()


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    """`app`, driven in-process over ASGI."""
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://fieldproof.test"
    ) as client:
        yield client
