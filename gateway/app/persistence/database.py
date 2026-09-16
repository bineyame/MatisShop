"""Async SQLAlchemy engine/session management.

The gateway owns exactly one database and it is not Odoo's (ADR-006).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import Settings, get_settings


class Base(DeclarativeBase):
    """Declarative base for every gateway table."""


_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def init_engine(settings: Settings | None = None, **engine_kwargs: Any) -> AsyncEngine:
    """Create (once) the process-wide engine and session factory."""
    global _engine, _sessionmaker
    if _engine is not None:
        return _engine

    settings = settings or get_settings()
    url = settings.gateway_database_url
    kwargs: dict[str, Any] = {"echo": settings.database_echo, "future": True}
    if url.startswith("sqlite"):
        # SQLite is used for tests only; keep a single shared connection so an
        # in-file database behaves consistently across sessions.
        kwargs["pool_pre_ping"] = False
    else:
        kwargs.update(pool_pre_ping=True, pool_size=10, max_overflow=5)
    kwargs.update(engine_kwargs)

    _engine = create_async_engine(url, **kwargs)
    _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False, class_=AsyncSession)
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    if _sessionmaker is None:
        init_engine()
    assert _sessionmaker is not None
    return _sessionmaker


def set_sessionmaker(factory: async_sessionmaker[AsyncSession], engine: AsyncEngine) -> None:
    """Used by the test harness to bind a temporary database."""
    global _engine, _sessionmaker
    _engine = engine
    _sessionmaker = factory


async def shutdown_engine() -> None:
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding a transactional session."""
    factory = get_sessionmaker()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
