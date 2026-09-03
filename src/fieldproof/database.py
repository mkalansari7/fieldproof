"""Engine, sessions, and building the schema.

Async throughout, on asyncpg. The sweeper and the SSE fan-out share one event
loop (ADR-0006, CLAUDE.md), so a synchronous driver here would stall every
connected dashboard on every query rather than only the request that made it.

There is no migration tool, and spec.md §10 does not cover the omission — this
is the one out-of-scope decision that is recorded here rather than there.
`create_schema` builds the schema from the model metadata. The argument is that
there is no history to migrate from yet and a one-revision Alembic tree would be
a second source of truth to keep in step with the models for the rest of the
build; the cost is that the first schema change against data anyone cares about
has to introduce the tool first. Issue 01's title says "migrations", so this is a
deliberate narrowing of it and not an oversight.
"""

import os

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from fieldproof.schema import Base

DEFAULT_DATABASE_URL = "postgresql+asyncpg:///fieldproof"
"""No host: asyncpg falls back to the local unix socket, so a fresh clone with a
running Postgres and `createdb fieldproof` needs no configuration at all."""

DATABASE_URL_ENV = "FIELDPROOF_DATABASE_URL"


def database_url() -> str:
    """The configured database, or the local default."""
    return os.environ.get(DATABASE_URL_ENV, DEFAULT_DATABASE_URL)


def create_engine(url: str | None = None) -> AsyncEngine:
    """An engine against `url`, or against `database_url()`.

    The caller owns it and must `dispose()` it: the connection pool holds sockets
    open, and a process that creates engines per request leaks them.
    """
    return create_async_engine(url if url is not None else database_url())


def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """A session factory bound to `engine`.

    `expire_on_commit=False` so that attributes read after a commit do not fire a
    fresh SELECT. Under async that lazy load would be an implicit await inside
    whatever coroutine happened to touch the attribute — the class of blocking
    surprise the `ASYNC` lint rules exist to keep out of this codebase.
    """
    return async_sessionmaker(engine, expire_on_commit=False)


async def create_schema(engine: AsyncEngine) -> None:
    """Create every table, index and enum type that does not already exist."""
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)


async def drop_schema(engine: AsyncEngine) -> None:
    """Drop the whole schema. Used by the tests and by a deliberate local reset."""
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)


async def clear_tables(session: AsyncSession) -> None:
    """Delete every row, leaving the schema in place. Does not commit.

    `sorted_tables` is in dependency order, so reversing it deletes children
    before parents and stays correct when a table is added. Lives here rather
    than in the two callers that want it — the seed's reset and the tests'
    teardown — because two walks of another module's metadata is two things to
    get wrong.
    """
    for table in reversed(Base.metadata.sorted_tables):
        await session.execute(delete(table))
