"""the wall panel and its screen schedule

Revision ID: c96dd2280d33
Revises: e7c2a9f45d13
Create Date: 2026-08-19 15:02:28.626910

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


# revision identifiers, used by Alembic.
revision: str = 'c96dd2280d33'
down_revision: Union[str, Sequence[str], None] = 'e7c2a9f45d13'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # One row, seeded from HOMEDASH_SCREEN_SCHEDULE at startup, so no data
    # migration is needed: app.devices.seed_device_from_settings creates it.
    # screen_schedule holds a ScreenScheduleConfig as JSON rather than
    # columns, so the shape can gain per-weekday windows without a
    # migration on a table a future settings UI will be writing to.
    op.create_table('devices',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('screen_schedule', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('last_seen', sa.DateTime(), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('devices')
