from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict

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
    ics_calendars: list[CalendarConfig] = []
    ics_poll_interval_minutes: int = 15

    sync_window_past_days: int = 30
    sync_window_future_days: int = 365

    weather_latitude: float = 0.0
    weather_longitude: float = 0.0
    weather_temperature_unit: Literal["fahrenheit", "celsius"] = "fahrenheit"
    weather_cache_minutes: int = 20

    frontend_dist: Path = BACKEND_ROOT.parent / "frontend" / "build"

    @property
    def database_url(self) -> str:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        return f"sqlite:///{self.db_path}"


@lru_cache
def get_settings() -> Settings:
    return Settings()
