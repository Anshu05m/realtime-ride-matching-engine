import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, Float, ForeignKey, Index, Numeric, String, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class RideStatus(str, enum.Enum):
    REQUESTED = "requested"
    MATCHED = "matched"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class Ride(Base):
    __tablename__ = "rides"

    # Slice 3's durable correctness boundary: at most one row here may have a
    # given driver_id while status is active. Declared here (not just in the
    # Alembic migration) so tests/conftest.py's Base.metadata.create_all(),
    # which builds the test schema straight from these models rather than
    # running migrations, actually gets this constraint too -- otherwise the
    # test database and real deployments would silently disagree about what's
    # enforced. Must stay in sync with the partial index created by migration
    # 4400761b43ec.
    __table_args__ = (
        Index(
            "ux_rides_one_active_ride_per_driver",
            "driver_id",
            unique=True,
            postgresql_where=text(
                "driver_id IS NOT NULL AND status IN ('requested', 'matched', 'in_progress')"
            ),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    rider_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("riders.id"), nullable=False, index=True
    )
    # Nullable: a ride exists in "requested" status before any driver is matched.
    driver_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("drivers.id"), nullable=True, index=True
    )

    status: Mapped[RideStatus] = mapped_column(
        Enum(
            RideStatus,
            name="ride_status",
            native_enum=True,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
        default=RideStatus.REQUESTED,
        server_default=RideStatus.REQUESTED.value,
        index=True,
    )

    pickup_lat: Mapped[float] = mapped_column(Float, nullable=False)
    pickup_lng: Mapped[float] = mapped_column(Float, nullable=False)

    matched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Money and multipliers use Numeric, not Float, to avoid binary floating-point
    # rounding errors in fares. Both are unset until the matching/pricing slices
    # (2 and 5) compute them.
    fare: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)
    surge_multiplier: Mapped[float | None] = mapped_column(Numeric(4, 2), nullable=True)

    # H3 zone the pickup falls into, used by Slice 5's surge aggregation.
    zone_id: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
