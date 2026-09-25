"""add zone_id to drivers for surge pricing

Revision ID: e90e1cf00ec5
Revises: c30a463d0562
Create Date: 2026-09-24 19:08:17.772959

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e90e1cf00ec5'
down_revision: Union[str, Sequence[str], None] = 'c30a463d0562'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("drivers", sa.Column("zone_id", sa.String(length=20), nullable=False))
    op.create_index(op.f("ix_drivers_zone_id"), "drivers", ["zone_id"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_drivers_zone_id"), table_name="drivers")
    op.drop_column("drivers", "zone_id")
