"""Slice 7: thread-safe collection and summarization of request outcomes.

Kept separate from runner.py so orchestration loops don't also own report
formatting/percentile math -- the same separation this codebase already draws
between "do the thing" (app/matching/matcher.py) and "summarize the thing"
(app/api/stats.py).
"""

from __future__ import annotations

import statistics
import threading
from dataclasses import dataclass
from typing import Literal

Outcome = Literal["matched", "unmatched", "error"]


@dataclass
class RequestOutcome:
    outcome: Outcome
    latency_seconds: float
    status_code: int | None = None
    ride_id: str | None = None
    driver_id: str | None = None


class ResultsCollector:
    """Thread-safe: runner.py's burst-firing workers each call record() from
    their own thread, synchronized on a shared threading.Barrier."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._outcomes: list[RequestOutcome] = []

    def record(self, outcome: RequestOutcome) -> None:
        with self._lock:
            self._outcomes.append(outcome)

    def snapshot(self) -> list[RequestOutcome]:
        with self._lock:
            return list(self._outcomes)


@dataclass
class SimulationSummary:
    total_requests: int
    matched: int
    unmatched: int
    errors: int
    p50_latency_ms: float
    p95_latency_ms: float
    p99_latency_ms: float

    @property
    def match_rate(self) -> float:
        return self.matched / self.total_requests if self.total_requests else 0.0


def _percentile(sorted_values: list[float], pct: float) -> float:
    """pct in (0, 100]. Uses stdlib statistics.quantiles (no numpy needed) --
    simple and accurate enough for a summary print, not claiming statistical
    rigor beyond that."""
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return sorted_values[0]
    quantiles = statistics.quantiles(sorted_values, n=100, method="inclusive")
    index = min(max(int(pct) - 1, 0), len(quantiles) - 1)
    return quantiles[index]


def summarize(outcomes: list[RequestOutcome]) -> SimulationSummary:
    latencies_ms = sorted(o.latency_seconds * 1000 for o in outcomes)
    return SimulationSummary(
        total_requests=len(outcomes),
        matched=sum(1 for o in outcomes if o.outcome == "matched"),
        unmatched=sum(1 for o in outcomes if o.outcome == "unmatched"),
        errors=sum(1 for o in outcomes if o.outcome == "error"),
        p50_latency_ms=_percentile(latencies_ms, 50),
        p95_latency_ms=_percentile(latencies_ms, 95),
        p99_latency_ms=_percentile(latencies_ms, 99),
    )


def format_summary(summary: SimulationSummary) -> str:
    lines = [
        "=== Simulation summary ===",
        f"Total ride requests fired: {summary.total_requests}",
        f"  Matched:   {summary.matched}",
        f"  Unmatched: {summary.unmatched}",
        f"  Errors:    {summary.errors}",
        f"  Match rate: {summary.match_rate:.1%}",
        "Latency (client-measured, ms): "
        f"p50={summary.p50_latency_ms:.1f} "
        f"p95={summary.p95_latency_ms:.1f} "
        f"p99={summary.p99_latency_ms:.1f}",
    ]
    return "\n".join(lines)
