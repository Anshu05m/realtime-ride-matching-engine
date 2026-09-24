"""add idempotency key to rides

Revision ID: c30a463d0562
Revises: 4400761b43ec
Create Date: 2026-09-23 17:43:58.001283

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c30a463d0562'
down_revision: Union[str, Sequence[str], None] = '4400761b43ec'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("rides", sa.Column("idempotency_key", sa.String(length=255), nullable=True))
    op.create_unique_constraint("uq_rides_idempotency_key", "rides", ["idempotency_key"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint("uq_rides_idempotency_key", "rides", type_="unique")
    op.drop_column("rides", "idempotency_key")
