import json
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent


class CalendarConfig(BaseModel):
    """One configured ICS calendar. Colors are not settable here - they are
    auto-assigned from a fixed palette in configured order (see
    app.calendars.colors)."""

    name: str
    url: str


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="HOMEDASH_", env_file=".env", extra="ignore")

    home_timezone: str = "UTC"
    db_path: Path = BACKEND_ROOT / "data" / "homedash.db"

    # JSON list, e.g. HOMEDASH_ICS_CALENDARS='[{"name": "Family", "url": "https://..."}]'
    # NoDecode hands us the raw string so the validator below can report a
    # readable error; pydantic-settings' own decoder raises a bare SettingsError
    # with a stack trace and no sight of the offending text.
    ics_calendars: Annotated[list[CalendarConfig], NoDecode] = []
    ics_poll_interval_minutes: int = 15

    sync_window_past_days: int = 30
    sync_window_future_days: int = 365

    weather_latitude: float = 0.0
    weather_longitude: float = 0.0
    weather_temperature_unit: Literal["fahrenheit", "celsius"] = "fahrenheit"
    weather_cache_minutes: int = 20

    frontend_dist: Path = BACKEND_ROOT.parent / "frontend" / "build"

    @field_validator("ics_calendars", mode="before")
    @classmethod
    def _parse_ics_calendars(cls, value: Any) -> Any:
        if not isinstance(value, str):
            return value
        text = value.strip()
        if not text:
            return []
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(_json_error_message(text, exc)) from None

    @property
    def database_url(self) -> str:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        return f"sqlite:///{self.db_path}"


def _json_error_message(text: str, exc: json.JSONDecodeError) -> str:
    """Point at the exact character that broke the JSON, with the surrounding
    text, so a bad HOMEDASH_ICS_CALENDARS is fixable without reading a stack
    trace."""
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
            "value across lines. Put the whole JSON list on a single line."
        )
    return (
        f"HOMEDASH_ICS_CALENDARS is not valid JSON: {exc.msg} "
        f"(line {exc.lineno}, column {exc.colno}).\n"
        f"  {snippet}\n"
        f"  {caret}{hint}\n"
        "Expected a single-line JSON list, e.g. "
        '[{"name": "Family", "url": "https://example.com/family.ics"}]'
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
