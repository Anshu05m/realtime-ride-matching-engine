"""add partial unique index for one active ride per driver

Revision ID: 4400761b43ec
Revises: 3f0e070721ea
Create Date: 2026-09-23 16:54:14.439740

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '4400761b43ec'
down_revision: Union[str, Sequence[str], None] = '3f0e070721ea'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # The durable correctness boundary for Slice 3: at most one row in `rides`
    # may have a given driver_id while status is active (requested/matched/
    # in_progress). This holds regardless of whether the Redis lock around
    # assignment (app/matching/matcher.py) worked correctly -- see
    # INTERVIEW_PREP.md's Slice 3 section for why both exist.
    op.create_index(
        "ux_rides_one_active_ride_per_driver",
        "rides",
        ["driver_id"],
        unique=True,
        postgresql_where=sa.text(
            "driver_id IS NOT NULL AND status IN ('requested', 'matched', 'in_progress')"
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ux_rides_one_active_ride_per_driver", table_name="rides")
