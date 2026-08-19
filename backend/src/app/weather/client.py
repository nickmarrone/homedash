import logging
import threading
from datetime import datetime, timezone
from typing import Any

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
AIR_QUALITY_URL = "https://air-quality-api.open-meteo.com/v1/air-quality"

_lock = threading.Lock()
_cache: dict[str, Any] | None = None


def get_cached_weather() -> dict[str, Any] | None:
    with _lock:
        return _cache


def refresh_weather() -> bool:
    """Fetch current conditions, 10-day forecast, and air quality from
    Open-Meteo and update the in-process cache. Returns True on success.

    Callers (the scheduler, and once at startup) are responsible for
    calling this periodically - request handlers only ever read the
    cache, never fetch live."""
    global _cache

    forecast_params = {
        "latitude": settings.weather_latitude,
        "longitude": settings.weather_longitude,
        "current": (
            "temperature_2m,apparent_temperature,relative_humidity_2m,"
            "weather_code,wind_speed_10m"
        ),
        "daily": (
            "weather_code,temperature_2m_max,temperature_2m_min,"
            "sunrise,sunset,daylight_duration"
        ),
        "hourly": "temperature_2m,precipitation_probability",
        "temperature_unit": settings.weather_temperature_unit,
        "timezone": "auto",
        "forecast_days": 10,
        # Without this the hourly block inherits forecast_days and returns 240
        # timesteps, re-serialized on every /api/weather read, for a panel that
        # shows 12. 48 is safe whether Open-Meteo anchors the hourly array at
        # the current hour or at local midnight: even at 23:00, midnight + 48h
        # still covers now + 12, so the window can never run short.
        "forecast_hours": 48,
    }
    air_quality_params = {
        "latitude": settings.weather_latitude,
        "longitude": settings.weather_longitude,
        "current": "us_aqi,european_aqi,pm2_5",
    }

    try:
        forecast_resp = httpx.get(FORECAST_URL, params=forecast_params, timeout=15.0)
        forecast_resp.raise_for_status()
        forecast = forecast_resp.json()

        air_quality_resp = httpx.get(AIR_QUALITY_URL, params=air_quality_params, timeout=15.0)
        air_quality_resp.raise_for_status()
        air_quality = air_quality_resp.json()
    except httpx.HTTPError:
        logger.exception("Failed to fetch weather from Open-Meteo")
        return False

    with _lock:
        _cache = {
            "current": forecast.get("current", {}),
            "daily": forecast.get("daily", {}),
            # Pass Open-Meteo's own unit labels through rather than deriving
            # them from the setting, so the panel's degree label always matches
            # what the numbers actually are.
            "current_units": forecast.get("current_units", {}),
            "daily_units": forecast.get("daily_units", {}),
            "hourly": forecast.get("hourly", {}),
            "hourly_units": forecast.get("hourly_units", {}),
            "air_quality": air_quality.get("current", {}),
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }
    return True
