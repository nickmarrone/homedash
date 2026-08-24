"""The weather cache, and the sky it travels with."""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter

from app.api import deps
from app.astro import astro_summary
from app.comets import load_comet_elements, visible_comets
from app.weather.client import get_cached_weather

router = APIRouter()


@router.get("/api/weather")
def get_weather() -> dict:
    """The weather cache, plus the sky.

    The astronomy is computed here rather than folded into the cache on
    refresh, precisely so it does not share the weather's fate: it needs no
    network, and Open-Meteo being unreachable should not also take the moon
    off the panel. It is a few dozen floating-point operations - cheaper than
    serializing the forecast it travels with.
    """
    now = datetime.now(timezone.utc)
    tz = ZoneInfo(deps.settings.home_timezone)
    comets = (
        visible_comets(
            load_comet_elements(),
            now,
            deps.settings.weather_latitude,
            deps.settings.weather_longitude,
            tz,
            deps.settings.comet_magnitude_limit,
        )
        if deps.settings.comets_enabled
        else []
    )
    return {
        **(get_cached_weather() or {}),
        "astro": astro_summary(
            now,
            deps.settings.weather_latitude,
            deps.settings.weather_longitude,
            tz,
            extra_events=comets,
        ),
    }
