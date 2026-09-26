"""Slice 9: per-statement database latency, for GET /stats's db_p50/p95/p99
fields -- the literal "measure database latency" requirement.

Nothing before this isolated database time from match_ride's combined
DB+Redis+application-logic latency (see app/observability/metrics.py).
SQLAlchemy's standard `before_cursor_execute`/`after_cursor_execute` engine
events are the idiomatic way to time individual statement execution without
touching any call site -- transparent instrumentation, not hidden logic,
consistent with CLAUDE.md's "don't hide important logic" (there's nothing
to hide; every query on this engine passes through here the same way).

Scope, stated plainly: this instruments the ENGINE, so it captures every
statement executed on it -- including GET /stats's own read queries during
a benchmark run, not just match_ride's writes. That's a simpler, more
honest interpretation of an unscoped spec bullet than trying to attribute
each statement back to its calling code path.

Thread safety: registered engine events fire on whatever thread executes the
query (Starlette's sync-route thread pool), so DbLatencyTracker needs the
same threading.Lock discipline as MetricsTracker -- not optional here either.
"""

from __future__ import annotations

import threading
import time
from collections import deque

from sqlalchemy import Engine, event

from app.observability.percentiles import percentile


class DbLatencyTracker:
    def __init__(self, window_size: int):
        self._lock = threading.Lock()
        self._recent_ms: deque[float] = deque(maxlen=window_size)

    def record(self, duration_seconds: float) -> None:
        with self._lock:
            self._recent_ms.append(duration_seconds * 1000)

    def percentiles(self) -> tuple[float, float, float]:
        with self._lock:
            latencies_ms = sorted(self._recent_ms)
        return (
            percentile(latencies_ms, 50),
            percentile(latencies_ms, 95),
            percentile(latencies_ms, 99),
        )


def register_db_latency_listeners(engine: Engine, tracker: DbLatencyTracker) -> None:
    """Call once, right after engine creation (see app/storage/database.py).
    conn.info is a plain per-connection dict SQLAlchemy provides specifically
    for this kind of bookkeeping; a list (not a single float) handles nested/
    re-entrant statements on the same connection correctly."""

    @event.listens_for(engine, "before_cursor_execute")
    def _before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
        conn.info.setdefault("query_start_times", []).append(time.perf_counter())

    @event.listens_for(engine, "after_cursor_execute")
    def _after_cursor_execute(conn, cursor, statement, parameters, context, executemany):
        start = conn.info["query_start_times"].pop()
        tracker.record(time.perf_counter() - start)
