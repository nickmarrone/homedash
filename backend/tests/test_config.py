"""Calendar configuration: kinds, required fields, and the deprecated alias.

A malformed calendar list should fail at startup with something actionable,
not a stack trace - and an existing HOMEDASH_ICS_CALENDARS deployment must
keep working across the rename.
"""

import logging

import pytest
from pydantic import ValidationError

from app.calendars import sync as sync_module
from app.config import CalendarConfig, Settings
from app.models import CalendarSource


def settings(**kwargs) -> Settings:
    return Settings(_env_file=None, **kwargs)


class TestCalendarConfig:
    def test_ics_defaults_and_key(self):
        cal = CalendarConfig(name="Family", url="https://example.com/a.ics")
        assert cal.kind == "ics"
        assert cal.key == "ics:https://example.com/a.ics"

    def test_ics_requires_a_url(self):
        with pytest.raises(ValidationError, match='requires a "url"'):
            CalendarConfig(name="Family")

    def test_google_requires_a_calendar_id(self):
        with pytest.raises(ValidationError, match='requires a "calendar_id"'):
            CalendarConfig(name="Family", kind="google", credentials="g")

    def test_google_requires_credentials(self):
        with pytest.raises(ValidationError, match="requires a \"credentials\" key"):
            CalendarConfig(
                name="Family", kind="google", calendar_id="abc@group.calendar.google.com"
            )

    def test_caldav_requires_credentials(self):
        with pytest.raises(ValidationError, match="requires a \"credentials\" key"):
            CalendarConfig(name="Nick", kind="caldav", url="https://caldav.example/x")

    def test_google_key_is_the_calendar_address(self):
        cal = CalendarConfig(
            name="Family",
            kind="google",
            calendar_id="abc@group.calendar.google.com",
            credentials="g",
        )
        assert cal.key == "google:abc@group.calendar.google.com"

    def test_same_url_under_different_kinds_are_distinct(self):
        ics = CalendarConfig(name="A", url="https://example.com/x")
        dav = CalendarConfig(name="A", kind="caldav", url="https://example.com/x", credentials="c")
        assert ics.key != dav.key

    def test_unknown_kind_is_rejected(self):
        with pytest.raises(ValidationError):
            CalendarConfig(name="A", kind="carrier-pigeon", url="https://example.com/x")


class TestDeprecatedAlias:
    def test_old_var_still_populates_calendars(self, caplog):
        with caplog.at_level(logging.WARNING):
            s = settings(ics_calendars='[{"name": "Family", "url": "https://example.com/a.ics"}]')
        assert [c.name for c in s.calendars] == ["Family"]
        assert "deprecated" in caplog.text

    def test_new_var_wins_when_both_are_set(self, caplog):
        with caplog.at_level(logging.WARNING):
            s = settings(
                calendars='[{"name": "New", "url": "https://example.com/new.ics"}]',
                ics_calendars='[{"name": "Old", "url": "https://example.com/old.ics"}]',
            )
        assert [c.name for c in s.calendars] == ["New"]
        assert "ignoring" in caplog.text.lower()

    def test_empty_string_is_no_calendars(self):
        assert settings(calendars="").calendars == []


class TestJsonErrors:
    def test_error_names_the_variable_that_is_wrong(self):
        with pytest.raises(ValidationError) as exc:
            settings(calendars='[{"name": "Family",}]')
        assert "HOMEDASH_CALENDARS is not valid JSON" in str(exc.value)

    def test_error_points_at_the_offending_character(self):
        with pytest.raises(ValidationError) as exc:
            settings(calendars='[{"name": "Fam\\ily", "url": "x"}]')
        message = str(exc.value)
        assert "^" in message
        assert "backslash" in message

    def test_credentials_error_names_its_own_variable(self):
        with pytest.raises(ValidationError) as exc:
            settings(calendar_credentials="{oops}")
        assert "HOMEDASH_CALENDAR_CREDENTIALS is not valid JSON" in str(exc.value)


class TestCredentialLookup:
    """Resolving a calendar's `credentials` key to the blob it names.

    Against `sync._credentials`, which is the implementation production
    actually reaches. `Settings.credentials_for` did the same job from the
    config object and no code path ever called it - these tests were the only
    thing keeping it alive, and testing the copy nothing runs is worse than not
    testing at all.
    """

    def source(self, monkeypatch, **overrides):
        monkeypatch.setattr(sync_module, "settings", settings(**overrides))
        return CalendarSource(
            kind="caldav", name="Nick", url="https://d/x", credentials_ref="fm"
        )

    def test_resolves_a_named_blob(self, monkeypatch):
        row = self.source(
            monkeypatch, calendar_credentials='{"fm": {"username": "me", "password": "pw"}}'
        )
        assert sync_module._credentials(row) == {"username": "me", "password": "pw"}

    def test_missing_blob_is_reported_with_both_names(self, monkeypatch):
        """Both names, because either one alone leaves the reader hunting: the
        calendar says which entry in the config is wrong, the reference says
        what to go and look for."""
        row = self.source(monkeypatch)
        with pytest.raises(ValueError, match="'Nick'.*'fm'"):
            sync_module._credentials(row)

    def test_a_source_with_no_reference_says_so(self, monkeypatch):
        monkeypatch.setattr(sync_module, "settings", settings())
        row = CalendarSource(kind="caldav", name="Nick", url="https://d/x")
        with pytest.raises(ValueError, match="no credentials"):
            sync_module._credentials(row)


class TestWeekStartSetting:
    """The week/month grids start on Monday unless told otherwise."""

    def test_defaults_to_monday(self, monkeypatch):
        monkeypatch.delenv("HOMEDASH_WEEK_STARTS_ON", raising=False)
        assert settings().week_starts_on == "monday"

    def test_sunday_is_accepted(self):
        assert settings(week_starts_on="sunday").week_starts_on == "sunday"

    def test_read_from_the_environment(self, monkeypatch):
        monkeypatch.setenv("HOMEDASH_WEEK_STARTS_ON", "sunday")
        assert settings().week_starts_on == "sunday"

    def test_anything_else_is_rejected_at_startup(self):
        # A typo here would otherwise silently pick a week start rather than
        # failing, and a wall panel off by a day is hard to spot.
        with pytest.raises(ValidationError):
            settings(week_starts_on="tuesday")
