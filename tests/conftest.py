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
from sqlalchemy.ext.asyncio import AsyncSession

from fieldproof.database import (
    clear_tables,
    create_engine,
    create_schema,
    drop_schema,
    session_factory,
)

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
