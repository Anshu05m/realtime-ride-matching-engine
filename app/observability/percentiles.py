"""Slice 9: a single shared percentile helper.

Extracted here because this is now the third near-identical copy (app/
simulation/reporting.py's simulation-client-side percentiles, app/
observability/metrics.py's match-latency percentiles, and Slice 9's new
per-statement database-latency percentiles) -- a legitimate rule-of-three
refactor, not premature abstraction. app/simulation/reporting.py is left
as-is: it's a separate process/concern (a simulation client's own summary of
HTTP round-trips), and importing across the app/simulation <-> app/
observability boundary in either direction would be the wrong dependency
shape for two genuinely different contexts that just happen to both need a
percentile of a sorted list.
"""

from __future__ import annotations

import statistics


def percentile(sorted_values: list[float], pct: float) -> float:
    """pct in (0, 100]. Uses stdlib statistics.quantiles -- no numpy needed,
    simple and accurate enough for a summary/stats field, not claiming
    statistical rigor beyond that. `sorted_values` must already be sorted."""
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return sorted_values[0]
    quantiles = statistics.quantiles(sorted_values, n=100, method="inclusive")
    index = min(max(int(pct) - 1, 0), len(quantiles) - 1)
    return quantiles[index]
