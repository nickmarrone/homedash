"""Reconciliation of calendar_sources against the configured calendar list.

The env var is the source of truth, so this is the code that decides whether
an edited config quietly deletes a family's appointments. Worth pinning.
"""

import pytest
from sqlmodel import Session, select

from app.calendars import sync as sync_module
from app.calendars.colors import PALETTE
from app.calendars.sync import seed_ics_calendars_from_settings
from app.config import CalendarConfig, Settings
from app.models import CalendarSource, Event, EventInstance


@pytest.fixture
def configure(monkeypatch):
    """Point the seeder at an explicit calendar list.

    `sync.py` binds `settings` at import time, so patching the module
    attribute is what takes effect - clearing get_settings()'s lru_cache
    would not rebind the name.
    """

    def _configure(*calendars: tuple[str, str]) -> None:
        monkeypatch.setattr(
            sync_module,
            "settings",
            Settings(
                _env_file=None,
                ics_calendars=[CalendarConfig(name=n, url=u) for n, u in calendars],
            ),
        )

    return _configure


def sources(session: Session) -> list[CalendarSource]:
    return list(
        session.exec(select(CalendarSource).order_by(CalendarSource.display_order)).all()
    )


def add_event(session: Session, source: CalendarSource) -> None:
    """Give a source one event and one materialized instance."""
    from datetime import datetime

    event = Event(source_id=source.id, uid="uid-1", raw_vevent="BEGIN:VEVENT\nEND:VEVENT")
    session.add(event)
    session.flush()
    session.add(
        EventInstance(
            event_id=event.id,
            starts_at=datetime(2026, 8, 19, 12, 0),
            ends_at=datetime(2026, 8, 19, 13, 0),
            title="Dentist",
        )
    )
    session.commit()


def test_seeds_calendars_with_palette_colors_in_order(session, configure):
    configure(("Family", "https://example.com/a.ics"), ("Nick", "https://example.com/b.ics"))
    seed_ics_calendars_from_settings(session)

    rows = sources(session)
    assert [r.name for r in rows] == ["Family", "Nick"]
    assert [r.color for r in rows] == [PALETTE[0], PALETTE[1]]
    assert [r.display_order for r in rows] == [0, 1]
    assert all(r.kind == "ics" and r.enabled for r in rows)


def test_rename_matches_by_url_and_keeps_events(session, configure):
    configure(("Family", "https://example.com/a.ics"))
    seed_ics_calendars_from_settings(session)
    original = sources(session)[0]
    add_event(session, original)

    configure(("Household", "https://example.com/a.ics"))
    seed_ics_calendars_from_settings(session)

    rows = sources(session)
    assert len(rows) == 1
    assert rows[0].id == original.id
    assert rows[0].name == "Household"
    # The rename must not have been treated as a remove-plus-add.
    assert session.exec(select(EventInstance)).all()


def test_reordering_recolors(session, configure):
    configure(("Family", "https://example.com/a.ics"), ("Nick", "https://example.com/b.ics"))
    seed_ics_calendars_from_settings(session)

    configure(("Nick", "https://example.com/b.ics"), ("Family", "https://example.com/a.ics"))
    seed_ics_calendars_from_settings(session)

    rows = sources(session)
    assert [r.name for r in rows] == ["Nick", "Family"]
    assert [r.color for r in rows] == [PALETTE[0], PALETTE[1]]


def test_duplicate_urls_collapse_to_one_source(session, configure):
    configure(
        ("Family", "https://example.com/a.ics"),
        ("Family copy", "https://example.com/a.ics"),
    )
    seed_ics_calendars_from_settings(session)

    rows = sources(session)
    assert len(rows) == 1
    assert rows[0].name == "Family"  # first occurrence wins


def test_removing_an_entry_deletes_its_events(session, configure):
    configure(("Family", "https://example.com/a.ics"), ("Nick", "https://example.com/b.ics"))
    seed_ics_calendars_from_settings(session)
    kept, removed = sources(session)
    add_event(session, kept)
    add_event(session, removed)

    configure(("Family", "https://example.com/a.ics"))
    seed_ics_calendars_from_settings(session)

    rows = sources(session)
    assert [r.name for r in rows] == ["Family"]
    # Only the surviving calendar's event and instance remain - a removed
    # calendar's appointments would otherwise sit on the panel forever.
    remaining = session.exec(select(Event)).all()
    assert [e.source_id for e in remaining] == [kept.id]
    assert len(session.exec(select(EventInstance)).all()) == 1


def test_empty_config_clears_everything(session, configure):
    configure(("Family", "https://example.com/a.ics"))
    seed_ics_calendars_from_settings(session)
    add_event(session, sources(session)[0])

    configure()
    seed_ics_calendars_from_settings(session)

    assert sources(session) == []
    assert session.exec(select(Event)).all() == []
    assert session.exec(select(EventInstance)).all() == []
