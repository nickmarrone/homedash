"""track the last full re-expansion

Revision ID: e7c2a9f45d13
Revises: d5b8e0c14a92
Create Date: 2026-08-19 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e7c2a9f45d13'
down_revision: Union[str, Sequence[str], None] = 'd5b8e0c14a92'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Nullable, so existing rows read as "never fully synced" and get one
    # rebuild on first startup after the upgrade - which is what they need.
    op.add_column(
        'calendar_sources',
        sa.Column('last_full_sync_at', sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('calendar_sources', 'last_full_sync_at')
