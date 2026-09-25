"""Surge pricing (Slice 5): a deterministic, configurable multiplier derived
from demand/supply within an H3 zone.

Two things are kept deliberately separate here, mirroring how matching keeps
H3 candidate discovery apart from distance ranking:

- The formula (`surge_multiplier`) is pure -- no database access, so it's
  trivially unit-testable and its "deterministic" claim is actually checked,
  not just asserted.
- The orchestration (`zone_surge`) does the two database counts and calls
  the formula. It lives here, next to the formula, rather than in
  app/services/ -- the same shape app/matching/matcher.py already uses
  (business logic + repo calls together, not spread across layers).

Consistency stance: the two counts below are independent, unlocked snapshot
reads under ordinary READ COMMITTED isolation -- not atomic with each other
or with any concurrent write. That's deliberate. Unlike Slice 3's "a driver
can never have two active rides" invariant, there is no correctness
guarantee at stake here, just a best-effort recent signal. A small race
window (e.g. a driver going BUSY between the two counts) doesn't violate
anything this project claims.

Formula: multiplier = clamp(1.0 + surge_sensitivity * ratio, 1.0,
surge_max_multiplier), where ratio = demand / max(supply, 1). Continuous and
capped, not CLAUDE.md's illustrative step table (1.0x/1.2x/1.5x/2.0x at
named demand levels) -- a single formula is one sentence to explain and has
no arbitrary threshold cliffs to defend (see INTERVIEW_PREP.md).
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from app.config import settings
from app.storage.repositories.driver_repository import DriverRepository
from app.storage.repositories.ride_repository import RideRepository


@dataclass(frozen=True)
class SurgeResult:
    zone_id: str
    demand: int
    supply: int
    multiplier: float


def surge_multiplier(demand: int, supply: int) -> float:
    """Pure formula -- no I/O. `supply` is floored at 1 (not 0) so a
    momentarily driver-less zone produces a large-but-finite ratio instead of
    a division by zero."""
    ratio = demand / max(supply, 1)
    raw = 1.0 + settings.surge_sensitivity * ratio
    return min(max(raw, 1.0), settings.surge_max_multiplier)


def zone_surge(db: Session, zone_id: str, *, now: datetime | None = None) -> SurgeResult:
    """Computes the current surge multiplier for a zone from live Postgres
    counts. `now` is accepted (defaulting to the current UTC time) so tests
    can pin the rolling window's boundary precisely instead of racing the
    clock."""
    now = now or datetime.now(UTC)
    since = now - timedelta(seconds=settings.surge_demand_window_seconds)

    demand = RideRepository(db).count_recent_active_in_zone(zone_id, since=since)
    supply = DriverRepository(db).count_available_in_zone(zone_id)

    return SurgeResult(
        zone_id=zone_id,
        demand=demand,
        supply=supply,
        multiplier=surge_multiplier(demand, supply),
    )
