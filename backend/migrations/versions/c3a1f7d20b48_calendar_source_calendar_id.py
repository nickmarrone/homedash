"""calendar source calendar_id

Revision ID: c3a1f7d20b48
Revises: b7e41c2d9f05
Create Date: 2026-08-19 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


# revision identifiers, used by Alembic.
revision: str = 'c3a1f7d20b48'
down_revision: Union[str, Sequence[str], None] = 'b7e41c2d9f05'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Nullable, so no server_default and no table rebuild: only Google sources
    # carry a calendar address. ICS and CalDAV keep identifying by URL, which
    # is why `url` stays NOT NULL - relaxing it on SQLite would mean a full
    # batch_alter_table rebuild of a table that events.source_id references.
    op.add_column(
        'calendar_sources',
        sa.Column('calendar_id', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('calendar_sources', 'calendar_id')
