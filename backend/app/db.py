import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager

from app.config import get_settings


def init_db() -> None:
    """Ensure the SQLite database file and its directory exist."""
    settings = get_settings()
    db_path = settings.database_full_path
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with get_connection() as conn:
        conn.execute("PRAGMA journal_mode=WAL;")


@contextmanager
def get_connection() -> Iterator[sqlite3.Connection]:
    settings = get_settings()
    conn = sqlite3.connect(settings.database_full_path)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def check_db_connection() -> bool:
    try:
        with get_connection() as conn:
            conn.execute("SELECT 1;")
        return True
    except sqlite3.Error:
        return False
