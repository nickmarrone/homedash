"""calendar source name and color

Revision ID: b7e41c2d9f05
Revises: 9521e3fdaa89
Create Date: 2026-08-19 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


# revision identifiers, used by Alembic.
revision: str = 'b7e41c2d9f05'
down_revision: Union[str, Sequence[str], None] = '9521e3fdaa89'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # server_default is required: SQLite cannot add a NOT NULL column without
    # one, and existing rows need a value. The defaults are left in place -
    # dropping them on SQLite means a full batch_alter_table rebuild for no
    # benefit, since the seeder always writes all three columns explicitly.
    op.add_column(
        'calendar_sources',
        sa.Column(
            'name',
            sqlmodel.sql.sqltypes.AutoString(),
            nullable=False,
            server_default='',
        ),
    )
    op.add_column(
        'calendar_sources',
        sa.Column(
            'color',
            sqlmodel.sql.sqltypes.AutoString(),
            nullable=False,
            server_default='#888888',
        ),
    )
    op.add_column(
        'calendar_sources',
        sa.Column(
            'display_order',
            sa.Integer(),
            nullable=False,
            server_default='0',
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('calendar_sources', 'display_order')
    op.drop_column('calendar_sources', 'color')
    op.drop_column('calendar_sources', 'name')
