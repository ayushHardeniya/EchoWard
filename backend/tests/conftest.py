import os

import pytest


@pytest.fixture(autouse=True, scope="session")
def _isolated_test_database(tmp_path_factory: pytest.TempPathFactory):
    """Point the whole test session at a throwaway SQLite file instead of
    backend/data/echoward.db, so test runs never pollute (or depend on) real
    local dev data.
    """
    db_path = tmp_path_factory.mktemp("echoward-db") / "test.db"
    os.environ["DATABASE_PATH"] = str(db_path)

    from app.config import get_settings

    get_settings.cache_clear()

    from app.db import init_db
    from app.incident_db import init_incident_schema

    init_db()
    init_incident_schema()

    yield
