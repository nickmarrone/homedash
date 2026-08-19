"""Reconciliation of calendar_sources against the configured calendar list.

The env var is the source of truth, so this is the code that decides whether
an edited config quietly deletes a family's appointments. Worth pinning.
"""

import pytest
from sqlmodel import Session, select

from app.calendars import sync as sync_module
from app.calendars.colors import PALETTE
from app.calendars.sync import seed_calendars_from_settings
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
                calendars=[CalendarConfig(name=n, url=u) for n, u in calendars],
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
    seed_calendars_from_settings(session)

    rows = sources(session)
    assert [r.name for r in rows] == ["Family", "Nick"]
    assert [r.color for r in rows] == [PALETTE[0], PALETTE[1]]
    assert [r.display_order for r in rows] == [0, 1]
    assert all(r.kind == "ics" and r.enabled for r in rows)


def test_rename_matches_by_url_and_keeps_events(session, configure):
    configure(("Family", "https://example.com/a.ics"))
    seed_calendars_from_settings(session)
    original = sources(session)[0]
    add_event(session, original)

    configure(("Household", "https://example.com/a.ics"))
    seed_calendars_from_settings(session)

    rows = sources(session)
    assert len(rows) == 1
    assert rows[0].id == original.id
    assert rows[0].name == "Household"
    # The rename must not have been treated as a remove-plus-add.
    assert session.exec(select(EventInstance)).all()


def test_reordering_recolors(session, configure):
    configure(("Family", "https://example.com/a.ics"), ("Nick", "https://example.com/b.ics"))
    seed_calendars_from_settings(session)

    configure(("Nick", "https://example.com/b.ics"), ("Family", "https://example.com/a.ics"))
    seed_calendars_from_settings(session)

    rows = sources(session)
    assert [r.name for r in rows] == ["Nick", "Family"]
    assert [r.color for r in rows] == [PALETTE[0], PALETTE[1]]


def test_duplicate_urls_collapse_to_one_source(session, configure):
    configure(
        ("Family", "https://example.com/a.ics"),
        ("Family copy", "https://example.com/a.ics"),
    )
    seed_calendars_from_settings(session)

    rows = sources(session)
    assert len(rows) == 1
    assert rows[0].name == "Family"  # first occurrence wins


def test_removing_an_entry_deletes_its_events(session, configure):
    configure(("Family", "https://example.com/a.ics"), ("Nick", "https://example.com/b.ics"))
    seed_calendars_from_settings(session)
    kept, removed = sources(session)
    add_event(session, kept)
    add_event(session, removed)

    configure(("Family", "https://example.com/a.ics"))
    seed_calendars_from_settings(session)

    rows = sources(session)
    assert [r.name for r in rows] == ["Family"]
    # Only the surviving calendar's event and instance remain - a removed
    # calendar's appointments would otherwise sit on the panel forever.
    remaining = session.exec(select(Event)).all()
    assert [e.source_id for e in remaining] == [kept.id]
    assert len(session.exec(select(EventInstance)).all()) == 1


def test_empty_config_clears_everything(session, configure):
    configure(("Family", "https://example.com/a.ics"))
    seed_calendars_from_settings(session)
    add_event(session, sources(session)[0])

    configure()
    seed_calendars_from_settings(session)

    assert sources(session) == []
    assert session.exec(select(Event)).all() == []
    assert session.exec(select(EventInstance)).all() == []


@pytest.fixture
def configure_kinds(monkeypatch):
    """Point the seeder at an explicit list of already-built configs."""

    def _configure(*calendars: CalendarConfig) -> None:
        monkeypatch.setattr(
            sync_module,
            "settings",
            Settings(_env_file=None, calendars=list(calendars)),
        )

    return _configure


GOOGLE = CalendarConfig(
    name="Family",
    kind="google",
    calendar_id="abc@group.calendar.google.com",
    credentials="g",
)
CALDAV = CalendarConfig(
    name="Nick", kind="caldav", url="https://caldav.example/dav/x", credentials="fm"
)


class TestMixedKinds:
    def test_google_source_records_its_address_and_endpoint(self, session, configure_kinds):
        configure_kinds(GOOGLE)
        seed_calendars_from_settings(session)

        row = sources(session)[0]
        assert row.kind == "google"
        assert row.calendar_id == "abc@group.calendar.google.com"
        # The endpoint keeps the row self-describing, and the address is
        # percent-encoded so the '@' cannot break the path.
        assert row.url.endswith("/calendars/abc%40group.calendar.google.com/events")
        assert row.credentials_ref == "g"

    def test_google_calendar_survives_a_rename(self, session, configure_kinds):
        configure_kinds(GOOGLE)
        seed_calendars_from_settings(session)
        original = sources(session)[0]
        add_event(session, original)

        configure_kinds(GOOGLE.model_copy(update={"name": "Household"}))
        seed_calendars_from_settings(session)

        rows = sources(session)
        assert len(rows) == 1 and rows[0].id == original.id
        assert rows[0].name == "Household"
        assert session.exec(select(EventInstance)).all()

    def test_kinds_coexist_and_keep_configured_order(self, session, configure_kinds):
        configure_kinds(
            GOOGLE,
            CALDAV,
            CalendarConfig(name="School", url="https://school.example/e.ics"),
        )
        seed_calendars_from_settings(session)

        rows = sources(session)
        assert [r.kind for r in rows] == ["google", "caldav", "ics"]
        assert [r.color for r in rows] == [PALETTE[0], PALETTE[1], PALETTE[2]]

    def test_switching_a_calendar_to_a_faster_kind_replaces_it(self, session, configure_kinds):
        """Moving a feed from ICS to CalDAV is a different source, not a rename.

        The old row and its stale events must go, or the panel would show
        every appointment twice."""
        configure_kinds(CalendarConfig(name="Nick", url="https://caldav.example/dav/x"))
        seed_calendars_from_settings(session)
        add_event(session, sources(session)[0])

        configure_kinds(CALDAV)
        seed_calendars_from_settings(session)

        rows = sources(session)
        assert len(rows) == 1
        assert rows[0].kind == "caldav"
        assert len(session.exec(select(Event)).all()) == 0

    def test_a_non_ics_source_is_deleted_when_dropped(self, session, configure_kinds):
        """The old seeder only ever deleted ICS rows; a dropped Google
        calendar would have sat on the panel forever."""
        configure_kinds(GOOGLE, CALDAV)
        seed_calendars_from_settings(session)
        add_event(session, sources(session)[0])

        configure_kinds(CALDAV)
        seed_calendars_from_settings(session)

        rows = sources(session)
        assert [r.kind for r in rows] == ["caldav"]
        assert session.exec(select(Event)).all() == []


class TestAdapterDispatch:
    """A misconfigured credential should say what is wrong, not fail deep
    inside an HTTP client."""

    def _source(self, **kwargs) -> CalendarSource:
        defaults = dict(
            id=1, kind="caldav", name="Nick", url="https://caldav.example/dav/x"
        )
        return CalendarSource(**{**defaults, **kwargs})

    def _with_credentials(self, monkeypatch, blob):
        monkeypatch.setattr(
            sync_module,
            "settings",
            Settings(_env_file=None, calendar_credentials=blob),
        )

    def test_ics_needs_no_credentials(self):
        adapter = sync_module.build_adapter(
            self._source(kind="ics", url="https://example.com/a.ics", sync_state="etag-1")
        )
        assert adapter.sync_state == "etag-1"

    def test_missing_credentials_key_is_named(self, monkeypatch):
        self._with_credentials(monkeypatch, {})
        with pytest.raises(ValueError, match="no credentials configured"):
            sync_module.build_adapter(self._source())

    def test_unknown_credentials_key_is_named(self, monkeypatch):
        self._with_credentials(monkeypatch, {"other": {"username": "u", "password": "p"}})
        with pytest.raises(ValueError, match="'fm'.*not defined"):
            sync_module.build_adapter(self._source(credentials_ref="fm"))

    def test_incomplete_credentials_say_which_field_is_missing(self, monkeypatch):
        self._with_credentials(monkeypatch, {"fm": {"username": "nick"}})
        with pytest.raises(ValueError, match="missing password"):
            sync_module.build_adapter(self._source(credentials_ref="fm"))

    def test_unknown_kind_is_rejected(self):
        with pytest.raises(ValueError, match="unsupported kind"):
            sync_module.build_adapter(self._source(kind="carrier-pigeon"))

    def test_google_needs_a_refresh_token(self, monkeypatch):
        self._with_credentials(monkeypatch, {"g": {"client_id": "a", "client_secret": "b"}})
        with pytest.raises(ValueError, match="refresh_token"):
            sync_module.build_adapter(
                self._source(kind="google", calendar_id="x@group.calendar.google.com", credentials_ref="g")
            )
