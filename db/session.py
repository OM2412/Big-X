# db/session.py
#
# Session factory + the FastAPI dependency every route handler uses to get
# a database session. Depends on database.py's engine — this file is
# purely about the unit-of-work layer on top of it.

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

try:
    from .database import engine
except ImportError:
    from database import engine

logger = logging.getLogger(__name__)

session_factory = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,  # objects stay usable after commit — avoids surprise lazy-load errors when code reads a field right after session.commit()
)


async def get_db_session() -> AsyncIterator[AsyncSession]:
    """FastAPI route dependency:

        @app.get("/agents/{id}")
        async def get_agent(id: str, session: AsyncSession = Depends(get_db_session)):
            ...

    Commits on clean exit, rolls back and re-raises on any exception —
    route handlers never have to remember to do either themselves."""
    async with session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


@asynccontextmanager
async def get_session() -> AsyncIterator[AsyncSession]:
    """Non-FastAPI equivalent for service classes constructed with a
    `db_session_factory` parameter (NFTTool, HumanApprovalService,
    PolicyCheckService, etc.) — those call this directly:

        async with self.db_session_factory() as session:
            session.add(entry)
            await session.commit()

    Pass `session.get_session` itself as the `db_session_factory` argument
    when wiring up those services, e.g.:
        NFTTool(..., db_session_factory=get_session)
    Same commit/rollback semantics as get_db_session, just usable outside
    a FastAPI request context."""
    async with session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise