"""the photo index behind the screensaver

Revision ID: f1a4b6e83c27
Revises: c96dd2280d33
Create Date: 2026-08-20 09:14:03.417220

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


# revision identifiers, used by Alembic.
revision: str = 'f1a4b6e83c27'
down_revision: Union[str, Sequence[str], None] = 'c96dd2280d33'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Populated by app.photos.index.reindex from HOMEDASH_PHOTOS_DIR, so there
    # is no data migration: the first scheduled scan fills it.
    #
    # size and mtime_ns are here so a rescan can skip a file without opening
    # it - re-hashing an unchanged folder every 15 minutes is the one part of
    # this that would actually cost something. error is here so an undecodable
    # file still gets a row: without one there is nothing to remember it by,
    # and it would be reopened and fail on every scan forever.
    #
    # mtime_ns is Integer rather than BigInteger although it holds ~1.7e18:
    # SQLite sizes an INTEGER column per value, up to 8 bytes, so it fits,
    # and matching what SQLModel emits keeps `alembic revision
    # --autogenerate` from reporting a type change on every future run.
    op.create_table('photos',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('path', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('hash', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('width', sa.Integer(), nullable=False),
    sa.Column('height', sa.Integer(), nullable=False),
    sa.Column('orientation', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('size', sa.Integer(), nullable=False),
    sa.Column('mtime_ns', sa.Integer(), nullable=False),
    sa.Column('error', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('added_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_photos_path'), 'photos', ['path'], unique=True)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_photos_path'), table_name='photos')
    op.drop_table('photos')
