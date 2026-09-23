"""Fixtures for genuine multi-connection concurrency tests.

tests/conftest.py's `db_session` fixture wraps every test in one shared
connection + outer transaction (rolled back at teardown) so ordinary tests
stay isolated cheaply -- but that means two "sessions" built from it share
one connection and nothing really commits, so they can never see each
other's writes the way two real concurrent clients would. Concurrency tests
need the opposite: independent sessions on independent connections that
actually commit, the same as real concurrent API callers would.
"""

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from tests.conftest import TEST_DATABASE_URL

# A separate engine from the one tests/conftest.py's `engine` fixture uses,
# sized explicitly for this suite's concurrency levels (test_driver_assignment.py
# parametrizes up to 50 simultaneous threads, each needing its own live
# connection for the duration of a match_ride call). The default pool (5 +
# 10 overflow = 15 connections) would silently serialize most threads behind
# pooled-connection waits, turning a "50-way race" into something much less
# concurrent without ever failing to reveal it. Postgres's own default
# max_connections (100, stock image) has headroom for this.
_concurrency_engine = create_engine(TEST_DATABASE_URL, pool_size=80, max_overflow=0)
# expire_on_commit=False: setup/check-phase code in these tests reads attributes
# (like .id) on objects after commit()/close() on a different session than the
# one that will read them -- with the default expire-on-commit behavior those
# reads would try to re-SELECT through an already-closed session and raise
# DetachedInstanceError. Worker threads each get their own fresh session, so
# this doesn't risk reading stale data within a single request the way
# DriverRepository.get_by_id's identity-map caching could (see its `fresh` param).
_ConcurrencySession = sessionmaker(
    bind=_concurrency_engine, autoflush=False, autocommit=False, expire_on_commit=False
)


@pytest.fixture
def committing_session_factory(engine):
    """Returns a zero-arg callable that makes a new, independent, real
    (committing) Session -- so each thread in a concurrency test gets its own
    connection, the way independent concurrent clients would.

    Depends on the parent `engine` fixture purely for its create_all/drop_all
    side effect (guaranteeing the schema exists) -- the sessions actually
    handed out here are bound to the separate, larger-pooled engine above,
    pointed at the same test database.
    """
    del engine
    return _ConcurrencySession


@pytest.fixture(autouse=True)
def _truncate_after_test(engine):
    """Concurrency tests commit for real, so db_session's rollback-based
    isolation doesn't apply here -- clear every table after each test."""
    del engine
    yield
    with _concurrency_engine.begin() as conn:
        conn.execute(text("TRUNCATE TABLE rides, drivers, riders RESTART IDENTITY CASCADE"))
