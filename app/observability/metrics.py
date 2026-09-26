"""Slice 8: in-process match-latency/throughput tracking for GET /stats.

This is this codebase's first mutable shared state accessed from real
concurrent OS threads outside the Redis/Postgres layer -- Starlette runs
sync routes on a thread pool, and every POST /rides call records into the
same MetricsTracker instance. Given how carefully this project treats
concurrency everywhere else (Redis locks, Postgres constraints, atomic
conditional UPDATEs), an unguarded counter here would be a real,
findable inconsistency, not a minor omission -- so every mutation is under
a plain threading.Lock, and percentile computation snapshots the window
under the lock, then computes outside it.

Two deliberately different definitions, not one blurry one:
- Latency (p50/p95/p99) is measured around match_ride ALONE, not
  create_ride+match_ride together -- create_ride's idempotency-check/commit
  time is a different cost than the matching algorithm's, and conflating
  them would make "match latency" impossible to defend precisely in an
  interview. Includes NoAvailableDriverError outcomes too: a failed match
  still does real candidate-ranking/lock work, so it's part of the
  distribution, not excluded from it.
- Throughput is an exact count of successful matches in the trailing 60
  real seconds, not an extrapolation from a smaller sample.

failed_matches and lock_contention_count are lifetime, in-process counters
-- unlike their sibling stats fields (completed_rides, cancelled_rides),
neither has a corresponding database row anywhere (a failed match attempt,
or a single candidate losing the Redis lock race, changes nothing durable),
so they can't be recovered from Postgres and both reset to zero on every
server restart. This asymmetry is real and is documented on StatsResponse,
not hidden.

lock_contention_count (Slice 9) is incremented in app/matching/matcher.py at
the exact point a LOCK_FAILED dashboard event is already emitted -- one
per-candidate lock-acquire loss, not one per failed ride (a ride can lose
the lock race on several candidates before ultimately succeeding on a later
one, or failing outright). Deliberately NOT derived by counting LOCK_FAILED
events over the Slice 8 dashboard's WebSocket stream instead: that queue is
bounded and silently drops events under backpressure (correct for a live
dashboard, wrong for an authoritative benchmark number) -- under exactly
the high-concurrency conditions a load test cares about most, event-stream
counting would systematically undercount. Being lifetime-scoped, a
benchmark must snapshot this before and after a run and diff it, not read
one mid-run value.

Latency percentiles use a BOUNDED rolling window (settings.metrics_window_size
recent match_ride calls), consistent with Slice 5's own precedent that surge
uses a rolling window rather than an all-time count -- necessary to keep
memory bounded on a long-running server, at the cost of percentiles being
"over the last N calls," not "ever."
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable, Literal

from app.config import settings
from app.observability.percentiles import percentile

Outcome = Literal["matched", "unmatched", "error"]

_THROUGHPUT_WINDOW_SECONDS = 60.0


@dataclass(frozen=True)
class MatchAttempt:
    outcome: Outcome
    latency_seconds: float
    at: float  # a monotonic clock reading, for throughput windowing


@dataclass(frozen=True)
class MetricsSnapshot:
    failed_matches: int
    lock_contention_count: int
    match_throughput_per_minute: int
    p50_latency_ms: float
    p95_latency_ms: float
    p99_latency_ms: float


class MetricsTracker:
    def __init__(self, window_size: int, *, now_fn: Callable[[], float] = time.monotonic):
        self._lock = threading.Lock()
        self._recent: deque[MatchAttempt] = deque(maxlen=window_size)
        self._failed_matches_total = 0
        self._lock_contention_total = 0
        self._now = now_fn

    def record(self, outcome: Outcome, latency_seconds: float) -> None:
        with self._lock:
            self._recent.append(
                MatchAttempt(outcome=outcome, latency_seconds=latency_seconds, at=self._now())
            )
            if outcome == "unmatched":
                self._failed_matches_total += 1

    def record_lock_contention(self) -> None:
        """Called once per candidate that loses the Redis lock race (see
        app/matching/matcher.py's LOCK_FAILED emit site) -- independent of
        record()'s per-ride outcome, since one ride can generate several of
        these before it ultimately succeeds or fails."""
        with self._lock:
            self._lock_contention_total += 1

    def snapshot(self) -> MetricsSnapshot:
        with self._lock:
            recent = list(self._recent)
            failed_total = self._failed_matches_total
            lock_contention_total = self._lock_contention_total
            now = self._now()

        throughput = sum(
            1
            for attempt in recent
            if attempt.outcome == "matched" and now - attempt.at <= _THROUGHPUT_WINDOW_SECONDS
        )
        latencies_ms = sorted(attempt.latency_seconds * 1000 for attempt in recent)

        return MetricsSnapshot(
            failed_matches=failed_total,
            lock_contention_count=lock_contention_total,
            match_throughput_per_minute=throughput,
            p50_latency_ms=percentile(latencies_ms, 50),
            p95_latency_ms=percentile(latencies_ms, 95),
            p99_latency_ms=percentile(latencies_ms, 99),
        )


metrics_tracker = MetricsTracker(window_size=settings.metrics_window_size)
