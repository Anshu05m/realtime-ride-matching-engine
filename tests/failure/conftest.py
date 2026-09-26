"""Fixtures for chaos/failure tests (Slice 9) needing genuinely independent,
really-committing sessions -- same rationale as tests/concurrency/conftest.py
(the SAVEPOINT-rollback `db_session` fixture can't produce real cross-session
visibility). Self-contained rather than importing tests/concurrency/'s
engine: chaos tests use at most 2-3 real sessions at a time, not 50 threads,
so a small dedicated pool (not concurrency's 80-connection one) is
appropriate, and it keeps tests/failure/ independent of tests/concurrency/.
"""

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from tests.conftest import TEST_DATABASE_URL

_failure_engine = create_engine(TEST_DATABASE_URL, pool_size=10, max_overflow=0)
_FailureSession = sessionmaker(
    bind=_failure_engine, autoflush=False, autocommit=False, expire_on_commit=False
)


@pytest.fixture
def committing_session_factory(engine):
    """Returns a zero-arg callable that makes a new, independent, real
    (committing) Session -- see tests/concurrency/conftest.py's identical
    pattern for the full rationale."""
    del engine
    return _FailureSession


@pytest.fixture(autouse=True)
def _truncate_after_test(engine):
    del engine
    yield
    with _failure_engine.begin() as conn:
        conn.execute(text("TRUNCATE TABLE rides, drivers, riders RESTART IDENTITY CASCADE"))
