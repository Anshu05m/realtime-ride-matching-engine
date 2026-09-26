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

failed_matches is a lifetime, in-process counter -- unlike its sibling stats
fields (completed_rides, cancelled_rides), it has no corresponding database
row anywhere (a failed match attempt changes nothing durable), so it can't
be recovered from Postgres and resets to zero on every server restart. This
asymmetry is real and is documented on StatsResponse, not hidden.

Latency percentiles use a BOUNDED rolling window (settings.metrics_window_size
recent match_ride calls), consistent with Slice 5's own precedent that surge
uses a rolling window rather than an all-time count -- necessary to keep
memory bounded on a long-running server, at the cost of percentiles being
"over the last N calls," not "ever."
"""

from __future__ import annotations

import statistics
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable, Literal

from app.config import settings

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
    match_throughput_per_minute: int
    p50_latency_ms: float
    p95_latency_ms: float
    p99_latency_ms: float


class MetricsTracker:
    def __init__(self, window_size: int, *, now_fn: Callable[[], float] = time.monotonic):
        self._lock = threading.Lock()
        self._recent: deque[MatchAttempt] = deque(maxlen=window_size)
        self._failed_matches_total = 0
        self._now = now_fn

    def record(self, outcome: Outcome, latency_seconds: float) -> None:
        with self._lock:
            self._recent.append(
                MatchAttempt(outcome=outcome, latency_seconds=latency_seconds, at=self._now())
            )
            if outcome == "unmatched":
                self._failed_matches_total += 1

    def snapshot(self) -> MetricsSnapshot:
        with self._lock:
            recent = list(self._recent)
            failed_total = self._failed_matches_total
            now = self._now()

        throughput = sum(
            1
            for attempt in recent
            if attempt.outcome == "matched" and now - attempt.at <= _THROUGHPUT_WINDOW_SECONDS
        )
        latencies_ms = sorted(attempt.latency_seconds * 1000 for attempt in recent)

        return MetricsSnapshot(
            failed_matches=failed_total,
            match_throughput_per_minute=throughput,
            p50_latency_ms=_percentile(latencies_ms, 50),
            p95_latency_ms=_percentile(latencies_ms, 95),
            p99_latency_ms=_percentile(latencies_ms, 99),
        )


def _percentile(sorted_values: list[float], pct: float) -> float:
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return sorted_values[0]
    quantiles = statistics.quantiles(sorted_values, n=100, method="inclusive")
    index = min(max(int(pct) - 1, 0), len(quantiles) - 1)
    return quantiles[index]


metrics_tracker = MetricsTracker(window_size=settings.metrics_window_size)
