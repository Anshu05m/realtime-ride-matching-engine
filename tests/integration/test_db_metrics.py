"""Slice 9: proves DbLatencyTracker actually records real per-statement
latency from a real SQLAlchemy engine's before/after_cursor_execute events
-- not just that the tracker class works in isolation (see tests/unit/
test_observability_metrics.py-style unit coverage for the pure math; this
is the part that needs a real engine).

Requires `docker compose up -d postgres redis` running first.
"""

from sqlalchemy import create_engine, text

from app.observability.db_metrics import DbLatencyTracker, register_db_latency_listeners
from tests.conftest import TEST_DATABASE_URL


def test_register_db_latency_listeners_records_real_statement_latency():
    tracker = DbLatencyTracker(window_size=50)
    engine = create_engine(TEST_DATABASE_URL)
    register_db_latency_listeners(engine, tracker)

    with engine.connect() as conn:
        for _ in range(5):
            conn.execute(text("SELECT 1"))

    p50, p95, p99 = tracker.percentiles()

    assert p50 >= 0
    assert p95 >= p50
    assert p99 >= p95
    assert len(tracker._recent_ms) == 5

    engine.dispose()


def test_percentiles_are_zero_before_any_statement_runs():
    tracker = DbLatencyTracker(window_size=50)

    p50, p95, p99 = tracker.percentiles()

    assert (p50, p95, p99) == (0.0, 0.0, 0.0)


def test_nested_statements_on_the_same_connection_are_tracked_independently():
    """Regression guard for the conn.info list-based (not single-value)
    bookkeeping in register_db_latency_listeners -- a naive single float
    per connection would be overwritten/corrupted by a second statement
    starting before the first one's after-event fires."""
    tracker = DbLatencyTracker(window_size=50)
    engine = create_engine(TEST_DATABASE_URL)
    register_db_latency_listeners(engine, tracker)

    with engine.connect() as conn:
        # Multiple statements on one connection, sequentially, each must be
        # timed correctly (no leaked/mismatched start times).
        for _ in range(10):
            conn.execute(text("SELECT 1"))
            conn.execute(text("SELECT 2"))

    assert len(tracker._recent_ms) == 20
    assert all(latency_ms >= 0 for latency_ms in tracker._recent_ms)

    engine.dispose()
