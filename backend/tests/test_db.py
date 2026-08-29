from app.db import check_db_connection, init_db


def test_init_db_and_connection() -> None:
    init_db()
    assert check_db_connection() is True
