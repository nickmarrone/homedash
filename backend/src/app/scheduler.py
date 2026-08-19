import logging
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler
from sqlmodel import Session, select

from app.calendars.sync import sync_ics_source
from app.config import get_settings
from app.db import engine
from app.models import CalendarSource
from app.sse import broadcaster
from app.weather.client import refresh_weather

logger = logging.getLogger(__name__)
settings = get_settings()
scheduler = BackgroundScheduler(timezone="UTC")


def run_ics_sync() -> None:
    with Session(engine) as session:
        sources = session.exec(
            select(CalendarSource).where(
                CalendarSource.kind == "ics",
                CalendarSource.enabled == True,  # noqa: E712
            )
        ).all()
        changed = False
        for source in sources:
            try:
                if sync_ics_source(session, source):
                    changed = True
            except Exception:
                logger.exception("ICS sync failed for source %s (%s)", source.id, source.url)
        if changed:
            broadcaster.publish("events.updated")


def run_weather_refresh() -> None:
    try:
        if refresh_weather():
            broadcaster.publish("weather.updated")
    except Exception:
        logger.exception("Weather refresh failed")


def start_scheduler() -> None:
    scheduler.add_job(
        run_ics_sync,
        "interval",
        minutes=settings.ics_poll_interval_minutes,
        id="ics_sync",
        next_run_time=datetime.now(),
    )
    scheduler.add_job(
        run_weather_refresh,
        "interval",
        minutes=settings.weather_cache_minutes,
        id="weather_refresh",
        next_run_time=datetime.now(),
    )
    scheduler.start()


def stop_scheduler() -> None:
    scheduler.shutdown(wait=False)
