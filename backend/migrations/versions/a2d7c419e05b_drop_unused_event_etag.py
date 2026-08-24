"""drop the events.etag column, which nothing ever wrote

Revision ID: a2d7c419e05b
Revises: f1a4b6e83c27
Create Date: 2026-08-24 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'a2d7c419e05b'
down_revision: Union[str, Sequence[str], None] = 'f1a4b6e83c27'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Shipped in the initial schema on the assumption that an ETag belonged
    # with the event it came from. It does not: change detection is per
    # *source*, and the value lives on `calendar_sources.sync_state` where the
    # adapters can reach it. Nothing has ever written this column, so there is
    # no data to lose - `sync_source` builds every Event row without it.
    #
    # batch_alter_table because SQLite cannot drop a column in place; Alembic
    # rebuilds the table and copies the rows.
    with op.batch_alter_table('events') as batch:
        batch.drop_column('etag')


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('events') as batch:
        batch.add_column(
            sa.Column('etag', sqlmodel.sql.sqltypes.AutoString(), nullable=True)
        )
