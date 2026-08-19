"""generalize resource_etag to sync_state

Revision ID: d5b8e0c14a92
Revises: c3a1f7d20b48
Create Date: 2026-08-19 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'd5b8e0c14a92'
down_revision: Union[str, Sequence[str], None] = 'c3a1f7d20b48'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # The column now holds whatever opaque resume token an adapter uses - an
    # HTTP ETag for ICS, a sync-token for CalDAV and Google - so the old name
    # would be actively misleading. SQLite has supported ALTER TABLE RENAME
    # COLUMN since 3.25, so this is a plain rename, not a table rebuild.
    op.alter_column('calendar_sources', 'resource_etag', new_column_name='sync_state')


def downgrade() -> None:
    """Downgrade schema."""
    op.alter_column('calendar_sources', 'sync_state', new_column_name='resource_etag')
