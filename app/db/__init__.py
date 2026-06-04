"""Database session and engine management for PostgreSQL (async SQLAlchemy)."""

from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import get_settings

settings = get_settings()


def _normalize_async_url(url: str) -> str:
    """Coerce a Postgres URL to the asyncpg driver and drop libpq-only params.

    The async engine requires an async driver. A plain ``postgres://`` /
    ``postgresql://`` URL (e.g. Railway's stock ``DATABASE_URL``) resolves to
    the sync psycopg2 dialect, which isn't installed — so we rewrite it to
    ``postgresql+asyncpg://``. We also strip query params asyncpg doesn't
    accept (``sslmode``/``channel_binding`` come from libpq-style URLs).
    A URL that already specifies a ``+driver`` is left untouched.
    """
    from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):  # no explicit driver → use asyncpg
        url = "postgresql+asyncpg://" + url[len("postgresql://"):]

    parts = urlsplit(url)
    if parts.query:
        kept = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
                if k not in ("sslmode", "channel_binding")]
        url = urlunsplit(parts._replace(query=urlencode(kept)))
    return url


engine = create_async_engine(
    _normalize_async_url(settings.database_url),
    pool_size=settings.database_pool_size,
    max_overflow=settings.database_max_overflow,
    echo=settings.debug,
    pool_pre_ping=True,
)

async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency that yields a database session."""
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
