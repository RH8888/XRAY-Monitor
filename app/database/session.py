from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import Settings, get_settings
from app.database.base import Base


class Database:
    """Async database facade kept independent from the concrete SQL backend."""

    def __init__(self, database_url: str, *, echo: bool = False) -> None:
        ensure_sqlite_parent_directory(database_url)
        self.engine: AsyncEngine = create_async_engine(
            database_url,
            echo=echo,
            pool_pre_ping=True,
        )
        self.session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
            self.engine,
            expire_on_commit=False,
            class_=AsyncSession,
        )

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        async with self.session_factory() as session:
            yield session

    async def create_all(self) -> None:
        """Create all database tables registered with the declarative base."""

        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    async def drop_all(self) -> None:
        """Drop all database tables registered with the declarative base."""

        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)

    async def dispose(self) -> None:
        await self.engine.dispose()


def create_database(settings: Settings | None = None) -> Database:
    """Build a database facade from application settings."""

    resolved_settings = settings or get_settings()
    return Database(resolved_settings.database_url)


def ensure_sqlite_parent_directory(database_url: str) -> None:
    """Create the parent directory for file-based SQLite database URLs."""

    url = make_url(database_url)
    if url.get_backend_name() != "sqlite" or not url.database:
        return

    database_path = url.database
    if database_path in {":memory:", ""} or database_path.startswith("file:"):
        return

    Path(database_path).expanduser().parent.mkdir(parents=True, exist_ok=True)
