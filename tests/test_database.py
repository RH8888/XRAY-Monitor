import asyncio
from pathlib import Path

from app.database.session import Database, ensure_sqlite_parent_directory


def test_database_creates_missing_sqlite_parent_directory(tmp_path: Path) -> None:
    database_file = tmp_path / "missing" / "nested" / "xray-monitor.db"
    database = Database(f"sqlite+aiosqlite:///{database_file}")

    try:
        assert database_file.parent.is_dir()
        asyncio.run(database.create_all())
        assert database_file.is_file()
    finally:
        asyncio.run(database.dispose())


def test_sqlite_parent_directory_helper_ignores_non_file_databases(tmp_path: Path) -> None:
    ensure_sqlite_parent_directory("sqlite+aiosqlite:///:memory:")
    ensure_sqlite_parent_directory("postgresql+asyncpg://user:pass@localhost/db")

    assert not (tmp_path / ":memory:").exists()
