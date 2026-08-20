import json
import logging
from datetime import date, time
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

logger = logging.getLogger(__name__)

BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent

CalendarKind = Literal["ics", "caldav", "google"]


def source_key(kind: str, url: str | None, calendar_id: str | None) -> str:
    """Stable identity for a calendar, used to reconcile config against rows.

    Matching on this rather than on the name is what lets a calendar be
    renamed or reordered without losing its events. `calendar_id` wins where
    both are present: a Google source stores the API endpoint in `url` for
    legibility, but its identity is the calendar address. ICS rows seeded
    before kinds existed still match, since their key is built the same way.
    """
    return f"{kind}:{calendar_id or url}"


class CalendarConfig(BaseModel):
    """One configured calendar.

    Colors are not settable here - they are auto-assigned from a fixed palette
    in configured order (see app.calendars.colors).

    Secrets are referenced by name rather than inlined: `credentials` names an
    entry in HOMEDASH_CALENDAR_CREDENTIALS. That keeps tokens and passwords out
    of the calendar list, which is the value most likely to be pasted into a
    chat window or a bug report.
    """

    name: str
    kind: CalendarKind = "ics"
    url: str | None = None
    calendar_id: str | None = None
    credentials: str | None = None

    @model_validator(mode="after")
    def _check_required_fields(self) -> "CalendarConfig":
        if self.kind in ("ics", "caldav") and not self.url:
            raise ValueError(f"calendar {self.name!r}: kind {self.kind!r} requires a \"url\"")
        if self.kind == "google" and not self.calendar_id:
            raise ValueError(
                f"calendar {self.name!r}: kind 'google' requires a \"calendar_id\" "
                "(the address shown in Google Calendar's settings, e.g. "
                "\"abc123@group.calendar.google.com\")"
            )
        if self.kind in ("caldav", "google") and not self.credentials:
            raise ValueError(
                f"calendar {self.name!r}: kind {self.kind!r} requires a \"credentials\" key "
                "naming an entry in HOMEDASH_CALENDAR_CREDENTIALS"
            )
        return self

    @property
    def key(self) -> str:
        """Stable identity for reconciling against calendar_sources rows."""
        return source_key(self.kind, self.url, self.calendar_id)


def _normalize_hh_mm(value: str) -> str:
    """Validate a 24-hour "HH:MM" wall-clock time and return it normalized."""
    try:
        hour, _, minute = str(value).partition(":")
        parsed = time(int(hour), int(minute))
    except ValueError:
        raise ValueError(
            f"{value!r} is not a time of day - expected 24-hour \"HH:MM\", e.g. \"06:30\""
        ) from None
    return parsed.strftime("%H:%M")


class ScreenWindow(BaseModel):
    """The hours the panel's screen is lit on one kind of day."""

    on: str = "06:30"
    off: str = "21:30"

    _check_times = field_validator("on", "off")(lambda cls, v: _normalize_hh_mm(v))

    @property
    def on_time(self) -> time:
        return time.fromisoformat(self.on)

    @property
    def off_time(self) -> time:
        return time.fromisoformat(self.off)

    def lit_at(self, moment: time) -> bool:
        """Whether the screen is lit at a wall-clock time on this kind of day.

        `on == off` is read as "always on" rather than as a zero-length window:
        a schedule that blanks the panel permanently is far more likely to be a
        typo than an intention, and a dark panel gives no clue why.
        """
        if self.on_time == self.off_time:
            return True
        if self.on_time < self.off_time:
            return self.on_time <= moment < self.off_time
        # Wraps past midnight, e.g. on 22:00 / off 06:00.
        return moment >= self.on_time or moment < self.off_time


class ScreenScheduleConfig(BaseModel):
    """When the wall panel's screen should be lit.

    One window applies to every day unless `weekend` overrides Saturday and
    Sunday. `on` and `off` are wall-clock times in HOMEDASH_HOME_TIMEZONE,
    never the panel's own clock - the Pi is a thin client whose OS timezone is
    deliberately not trusted anywhere in this app.

    An `on` later than `off` is a window crossing midnight, not an error:
    {"on": "22:00", "off": "06:00"} lights the screen overnight.
    """

    on: str = "06:30"
    off: str = "21:30"
    weekend: ScreenWindow | None = None

    _check_times = field_validator("on", "off")(lambda cls, v: _normalize_hh_mm(v))

    def window_for(self, day: date) -> ScreenWindow:
        """The window governing one calendar date. Monday is 0, Sunday is 6."""
        if self.weekend is not None and day.weekday() >= 5:
            return self.weekend
        return ScreenWindow(on=self.on, off=self.off)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="HOMEDASH_", env_file=".env", extra="ignore")

    home_timezone: str = "UTC"
    db_path: Path = BACKEND_ROOT / "data" / "homedash.db"

    # JSON list, e.g. HOMEDASH_CALENDARS='[{"name": "Family", "url": "https://..."}]'
    # NoDecode hands us the raw string so the validator below can report a
    # readable error; pydantic-settings' own decoder raises a bare SettingsError
    # with a stack trace and no sight of the offending text.
    calendars: Annotated[list[CalendarConfig], NoDecode] = []
    # Deprecated alias for `calendars`, kept so an existing deployment does not
    # break on upgrade. Merged in by _apply_deprecated_alias below.
    ics_calendars: Annotated[list[CalendarConfig], NoDecode] = []

    # JSON object of {name: {...}} credential blobs, referenced by a calendar's
    # "credentials" key. Kept separate from the calendar list so the list stays
    # safe to share.
    calendar_credentials: Annotated[dict[str, dict[str, Any]], NoDecode] = {}

    # ICS feeds are regenerated by the provider on its own schedule, so polling
    # them faster than this achieves nothing. CalDAV and Google report changes
    # as they happen, so those are polled on the fast interval.
    ics_poll_interval_minutes: int = 15
    fast_poll_interval_minutes: int = 1

    # How long a source may go without a full re-fetch and re-expansion,
    # regardless of what its change detection says.
    #
    # This is the backstop for a whole class of bug: every kind decides "did
    # anything change?" from a provider's own signal - an ETag, a sync token -
    # and a signal that misses a change leaves the panel wrong with no way to
    # notice. A deletion is the worst version, because the stale row is an
    # appointment somebody has already cancelled. It is also the one a
    # provider is most likely to under-report, since a deleted event is absent
    # rather than different.
    #
    # An hour costs one extra fetch per calendar per hour and bounds how long
    # any such miss can survive. It also keeps the rolling materialization
    # window moving on a calendar nobody ever edits.
    full_resync_interval_minutes: int = 60

    sync_window_past_days: int = 30
    sync_window_future_days: int = 365

    week_starts_on: Literal["sunday", "monday"] = "sunday"

    # The wall panel's screen schedule, seeded onto the devices row at
    # startup the same way calendars are. See ScreenScheduleConfig.
    screen_schedule: Annotated[ScreenScheduleConfig, NoDecode] = ScreenScheduleConfig()
    device_name: str = "panel"

    # Bright comets, from the Minor Planet Center. The one part of the sky
    # that cannot be computed from first principles - a naked-eye comet is a
    # discovery, not an annual event - so it is also the only thing here that
    # reaches the network. Set comets_enabled to false to keep the panel
    # entirely self-contained.
    comets_enabled: bool = True
    comet_refresh_hours: int = 24
    # Six is roughly the naked-eye limit under a dark sky. Deliberately
    # conservative: comet brightness forecasts routinely miss by magnitudes,
    # so a generous limit fills the strip with comets nobody can find.
    comet_magnitude_limit: float = 6.0

    weather_latitude: float = 0.0
    weather_longitude: float = 0.0
    weather_temperature_unit: Literal["fahrenheit", "celsius"] = "fahrenheit"
    weather_cache_minutes: int = 20

    frontend_dist: Path = BACKEND_ROOT.parent / "frontend" / "build"

    @field_validator("calendars", mode="before")
    @classmethod
    def _parse_calendars(cls, value: Any) -> Any:
        return _parse_json(value, "HOMEDASH_CALENDARS", _CALENDAR_EXAMPLE)

    @field_validator("ics_calendars", mode="before")
    @classmethod
    def _parse_ics_calendars(cls, value: Any) -> Any:
        return _parse_json(value, "HOMEDASH_ICS_CALENDARS", _CALENDAR_EXAMPLE)

    @field_validator("calendar_credentials", mode="before")
    @classmethod
    def _parse_calendar_credentials(cls, value: Any) -> Any:
        return _parse_json(
            value,
            "HOMEDASH_CALENDAR_CREDENTIALS",
            '{"fastmail": {"username": "me@fastmail.com", "password": "app-password"}}',
            empty={},
        )

    @field_validator("screen_schedule", mode="before")
    @classmethod
    def _parse_screen_schedule(cls, value: Any) -> Any:
        return _parse_json(
            value, "HOMEDASH_SCREEN_SCHEDULE", _SCREEN_SCHEDULE_EXAMPLE, empty={}
        )

    @model_validator(mode="after")
    def _apply_deprecated_alias(self) -> "Settings":
        if self.ics_calendars and not self.calendars:
            logger.warning(
                "HOMEDASH_ICS_CALENDARS is deprecated - rename it to HOMEDASH_CALENDARS. "
                "Entries are unchanged; each one defaults to kind \"ics\"."
            )
            self.calendars = self.ics_calendars
        elif self.ics_calendars and self.calendars:
            logger.warning(
                "Both HOMEDASH_CALENDARS and the deprecated HOMEDASH_ICS_CALENDARS are set; "
                "ignoring HOMEDASH_ICS_CALENDARS."
            )
        return self

    def credentials_for(self, config: CalendarConfig) -> dict[str, Any]:
        """The credential blob a calendar refers to, or an empty dict."""
        if not config.credentials:
            return {}
        blob = self.calendar_credentials.get(config.credentials)
        if blob is None:
            raise ValueError(
                f"calendar {config.name!r} references credentials "
                f"{config.credentials!r}, which is not defined in "
                "HOMEDASH_CALENDAR_CREDENTIALS"
            )
        return blob

    @property
    def database_url(self) -> str:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        return f"sqlite:///{self.db_path}"


_SCREEN_SCHEDULE_EXAMPLE = (
    '{"on": "06:30", "off": "21:30", "weekend": {"on": "08:00", "off": "22:00"}}'
)

_CALENDAR_EXAMPLE = '[{"name": "Family", "url": "https://example.com/family.ics"}]'


def _parse_json(value: Any, var_name: str, example: str, empty: Any = None) -> Any:
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text:
        return [] if empty is None else empty
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(_json_error_message(text, exc, var_name, example)) from None


def _json_error_message(
    text: str, exc: json.JSONDecodeError, var_name: str, example: str
) -> str:
    """Point at the exact character that broke the JSON, with the surrounding
    text, so a bad value is fixable without reading a stack trace."""
    start = max(0, exc.pos - 30)
    snippet = text[start : exc.pos + 30].replace("\n", "\\n")
    caret = " " * (exc.pos - start) + "^"
    hint = ""
    if "Invalid \\escape" in exc.msg:
        hint = (
            "\nA backslash inside a quoted string is being read as a JSON escape. "
            "URLs and names should contain no backslashes - remove any that were "
            "added to escape quotes or to continue the value onto the next line."
        )
    elif exc.msg.startswith("Expecting value") and "\\" in text[start : exc.pos + 5]:
        hint = (
            "\nThere is a backslash near the error - most likely one used to wrap the "
            "value across lines. Put the whole JSON value on a single line."
        )
    return (
        f"{var_name} is not valid JSON: {exc.msg} "
        f"(line {exc.lineno}, column {exc.colno}).\n"
        f"  {snippet}\n"
        f"  {caret}{hint}\n"
        f"Expected a single-line JSON value, e.g. {example}"
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
