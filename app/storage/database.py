from collections.abc import Generator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings
from app.observability.db_metrics import DbLatencyTracker, register_db_latency_listeners

engine = create_engine(settings.database_url, echo=settings.database_echo, pool_pre_ping=True)

# Slice 9: per-statement latency, feeding GET /stats's db_p50/p95/p99 fields.
# Registered here (once, right after engine creation) rather than at each
# call site -- see app/observability/db_metrics.py for why.
db_latency_tracker = DbLatencyTracker(window_size=settings.metrics_window_size)
register_db_latency_listeners(engine, db_latency_tracker)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency. Does NOT auto-commit: the caller (service/endpoint) owns
    the transaction boundary, which matters once Slice 3 adds locking around it."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def session_scope() -> Generator[Session, None, None]:
    """Transactional scope for use outside FastAPI's DI (scripts, simulation engine, tests)."""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
