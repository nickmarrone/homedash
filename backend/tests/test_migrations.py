"""The Alembic chain, run for real.

Every other test in this suite builds the schema with
`SQLModel.metadata.create_all` - deliberately, because paying the whole
migration history per test buys application logic nothing. The cost is that
the migrations themselves were never executed by anything except a container
booting against a family's live database. A broken revision, or one that has
drifted from the models, therefore had no earlier signal than the panel
failing to come up.

So this runs `upgrade head` against a throwaway file and asks Alembic whether
the result still matches the models. That is the same comparison
`--autogenerate` uses, which means it fails for the case that actually
happens: somebody adds a column to models.py and forgets the revision.
"""

import pathlib

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect
from sqlmodel import SQLModel

from app import models  # noqa: F401  (registers tables on SQLModel.metadata)
from app.config import BACKEND_ROOT, get_settings

MIGRATIONS = BACKEND_ROOT / "migrations"


def alembic_config(db_path: pathlib.Path) -> Config:
    cfg = Config(str(BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(MIGRATIONS))
    # Set for completeness, but env.py overwrites it with
    # `get_settings().database_url` - so what actually steers the migration at
    # a throwaway file is HOMEDASH_DB_PATH plus clearing the settings cache,
    # which the fixture below does. Worth knowing before wondering why an
    # obvious-looking override does nothing.
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    return cfg


@pytest.fixture
def migrated(tmp_path, monkeypatch) -> pathlib.Path:
    """A database with the whole chain applied to it."""
    db_path = tmp_path / "migrated.db"
    monkeypatch.setenv("HOMEDASH_DB_PATH", str(db_path))
    get_settings.cache_clear()
    try:
        command.upgrade(alembic_config(db_path), "head")
    finally:
        get_settings.cache_clear()
    return db_path


class TestTheChainRuns:
    def test_upgrade_head_succeeds_from_nothing(self, migrated):
        """A fresh install. This is the path a new container takes, and until
        now nothing ran it outside of a real deployment."""
        assert migrated.exists()

    def test_it_creates_the_tables_the_app_queries(self, migrated):
        tables = set(inspect(create_engine(f"sqlite:///{migrated}")).get_table_names())

        assert {"calendar_sources", "events", "event_instances", "devices", "photos"} <= tables

    def test_there_is_exactly_one_head(self):
        """Two heads is what a branched history looks like, and `upgrade head`
        refuses to run at all once it happens - at startup, in production."""
        scripts = ScriptDirectory.from_config(alembic_config(pathlib.Path("x.db")))
        assert len(scripts.get_heads()) == 1


class TestTheChainMatchesTheModels:
    def test_no_drift_between_migrations_and_models(self, migrated):
        """The failure this file exists for: a column added to models.py with
        no revision written for it. Every test that builds its schema with
        `create_all` passes happily, because it never looks at a migration -
        and the mismatch surfaces as a runtime error on the real database.

        This is the same comparison `alembic revision --autogenerate` makes.
        A non-empty diff means one of the two sides was changed alone.
        """
        engine = create_engine(f"sqlite:///{migrated}")
        with engine.connect() as connection:
            context = MigrationContext.configure(connection)
            diff = compare_metadata(context, SQLModel.metadata)

        assert diff == [], f"migrations and models disagree: {diff}"


class TestItGoesBackDown:
    def test_every_revision_has_a_working_downgrade(self, tmp_path, monkeypatch):
        """House style says a real `downgrade()`, not a `pass`. An untested
        one is a promise nobody has ever checked, and it is checked at exactly
        the wrong moment - halfway through backing a bad release out."""
        db_path = tmp_path / "roundtrip.db"
        monkeypatch.setenv("HOMEDASH_DB_PATH", str(db_path))
        get_settings.cache_clear()
        cfg = alembic_config(db_path)
        try:
            command.upgrade(cfg, "head")
            command.downgrade(cfg, "base")
            command.upgrade(cfg, "head")
        finally:
            get_settings.cache_clear()

        tables = set(inspect(create_engine(f"sqlite:///{db_path}")).get_table_names())
        assert "event_instances" in tables
