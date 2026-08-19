"""Shared test fixtures.

The schema is created with `SQLModel.metadata.create_all` rather than by
running Alembic: these tests exercise application logic, and paying the cost
of the full migration history for every test buys nothing. `run_migrations`
is still covered where it matters - by the app actually starting up.
"""

from collections.abc import Iterator

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

# Imported for its side effect: SQLModel table classes must be defined before
# `metadata.create_all` can see them.
import app.models  # noqa: F401


@pytest.fixture
def session() -> Iterator[Session]:
    # StaticPool keeps every connection pointed at the same in-memory
    # database; the default pool would hand out a fresh, empty one per
    # connection and the schema would vanish between statements.
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
