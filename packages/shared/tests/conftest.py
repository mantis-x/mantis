"""
Shared pytest fixtures for packages/shared tests.

Requires a real, DISPOSABLE Postgres reachable at TEST_DATABASE_URL —
defaults to localhost:5433, deliberately NOT the docker-compose default of
5432, since this suite creates and drops every table in the target
database. Point TEST_DATABASE_URL at a throwaway instance only, e.g.:
    docker run -d -p 5433:5432 -e POSTGRES_USER=mantis \\
      -e POSTGRES_PASSWORD=password -e POSTGRES_DB=mantis postgres:16-alpine

As a safety net, the port-5432 default is refused outright — running this
suite against the real dev/prod database would wipe its schema.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

_TEST_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL", "postgresql://mantis:password@localhost:5433/mantis"
)
if ":5432/" in _TEST_DATABASE_URL and "ALLOW_TEST_DB_ON_5432" not in os.environ:
    raise RuntimeError(
        "Refusing to run: TEST_DATABASE_URL points at port 5432 (the default "
        "docker-compose Postgres port). This test suite creates and drops "
        "every table — point it at a disposable instance instead (see this "
        "file's docstring), or set ALLOW_TEST_DB_ON_5432=1 if you are certain "
        "5432 is itself disposable in this environment."
    )
os.environ.setdefault("DATABASE_URL", _TEST_DATABASE_URL)

from src.db.connection import get_engine, get_session, reset_engine_for_tests
from src.db.models import Base


@pytest.fixture(scope="session", autouse=True)
def _db_schema():
    reset_engine_for_tests()
    engine = get_engine()
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine)


@pytest.fixture(autouse=True)
def _clean_tables():
    """Truncate every table between tests so each test starts from empty."""
    with get_session() as session:
        for table in reversed(Base.metadata.sorted_tables):
            session.execute(table.delete())
    yield


@pytest.fixture
def db_session():
    with get_session() as session:
        yield session
