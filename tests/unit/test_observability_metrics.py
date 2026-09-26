"""Slice 8: pure unit tests for MetricsTracker. A fake clock (`now_fn`) is
injected so throughput-window assertions are exact, not racing real time.
"""

from app.observability.metrics import MetricsTracker


class FakeClock:
    def __init__(self, start: float = 0.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def test_snapshot_of_an_empty_tracker_is_all_zero():
    tracker = MetricsTracker(window_size=10)

    snapshot = tracker.snapshot()

    assert snapshot.failed_matches == 0
    assert snapshot.lock_contention_count == 0
    assert snapshot.match_throughput_per_minute == 0
    assert snapshot.p50_latency_ms == 0.0
    assert snapshot.p95_latency_ms == 0.0
    assert snapshot.p99_latency_ms == 0.0


def test_record_lock_contention_increments_independently_of_record():
    tracker = MetricsTracker(window_size=10)

    tracker.record_lock_contention()
    tracker.record_lock_contention()
    tracker.record("matched", 0.01)  # a ride can succeed despite contention

    snapshot = tracker.snapshot()
    assert snapshot.lock_contention_count == 2
    assert snapshot.failed_matches == 0


def test_lock_contention_count_is_thread_safe_under_concurrent_writers():
    import threading

    tracker = MetricsTracker(window_size=10)

    def worker():
        for _ in range(500):
            tracker.record_lock_contention()

    threads = [threading.Thread(target=worker) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert tracker.snapshot().lock_contention_count == 5000


def test_unmatched_outcomes_increment_failed_matches_lifetime_counter():
    tracker = MetricsTracker(window_size=10)

    tracker.record("unmatched", 0.01)
    tracker.record("matched", 0.02)
    tracker.record("unmatched", 0.03)

    assert tracker.snapshot().failed_matches == 2


def test_error_outcomes_do_not_count_as_failed_matches():
    tracker = MetricsTracker(window_size=10)

    tracker.record("error", 0.01)

    assert tracker.snapshot().failed_matches == 0


def test_throughput_only_counts_matched_outcomes_within_the_trailing_60_seconds():
    clock = FakeClock()
    tracker = MetricsTracker(window_size=10, now_fn=clock)

    tracker.record("matched", 0.01)
    clock.advance(30)
    tracker.record("matched", 0.01)
    clock.advance(31)  # first match is now 61s old, outside the 60s window
    tracker.record("unmatched", 0.01)  # never counts toward throughput regardless

    snapshot = tracker.snapshot()

    assert snapshot.match_throughput_per_minute == 1


def test_latency_window_is_bounded_by_window_size():
    tracker = MetricsTracker(window_size=3)

    for latency in [0.001, 0.002, 0.003, 0.004]:
        tracker.record("matched", latency)

    # The oldest (0.001s = 1ms) must have been evicted.
    snapshot = tracker.snapshot()
    assert snapshot.p50_latency_ms >= 2.0


def test_percentiles_reflect_recorded_latencies_in_milliseconds():
    tracker = MetricsTracker(window_size=100)

    for i in range(1, 101):
        tracker.record("matched", i / 1000)  # 1ms .. 100ms

    snapshot = tracker.snapshot()

    assert 45 <= snapshot.p50_latency_ms <= 55
    assert 90 <= snapshot.p95_latency_ms <= 100
    assert 95 <= snapshot.p99_latency_ms <= 100


def test_record_is_thread_safe_under_concurrent_writers():
    import threading

    tracker = MetricsTracker(window_size=1000)

    def worker():
        for _ in range(200):
            tracker.record("matched", 0.001)

    threads = [threading.Thread(target=worker) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # No crash, no lost/corrupted state -- exactly 1000 entries retained
    # (window_size cap), nothing more, nothing torn.
    assert len(tracker._recent) == 1000
