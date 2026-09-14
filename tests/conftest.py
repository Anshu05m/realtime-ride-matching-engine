import os

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.models import Base

# Integration/concurrency/failure tests require `docker compose up -d postgres redis`
# to be running. They point at a dedicated test database (provisioned automatically
# by scripts/init-test-db.sql on the postgres container's first startup) so runs
# never touch local dev data.
TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+psycopg://ride_matching:ride_matching@localhost:5432/ride_matching_test",
)


@pytest.fixture(scope="session")
def engine():
    eng = create_engine(TEST_DATABASE_URL)
    Base.metadata.create_all(eng)
    yield eng
    Base.metadata.drop_all(eng)
    eng.dispose()


@pytest.fixture()
def db_session(engine) -> Session:
    """Each test runs inside an outer transaction that is rolled back afterwards,
    so tests stay isolated from each other without recreating tables every time.

    join_transaction_mode="create_savepoint" makes the session's own commit()/
    rollback() calls (e.g. after a caught IntegrityError) operate on a SAVEPOINT
    nested inside the outer transaction, instead of ending it early — otherwise a
    test that deliberately triggers and catches a DB error leaves the connection's
    outer transaction deassociated before the fixture gets to close it.
    """
    connection = engine.connect()
    transaction = connection.begin()
    session_factory = sessionmaker(bind=connection, join_transaction_mode="create_savepoint")
    session = session_factory()

    yield session

    session.close()
    transaction.rollback()
    connection.close()
