import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from sqlmodel import Session

from app.api.routes import router
from app.calendars.sync import seed_calendars_from_settings
from app.config import get_settings
from app.db import engine, run_migrations
from app.devices import seed_device_from_settings
from app.music.service import start_music, stop_music
from app.photos.observer import start_folder_watch
from app.scheduler import run_photo_index, start_scheduler, stop_scheduler
from app.sse import broadcaster
from app.weather.client import refresh_weather

logging.basicConfig(level=logging.INFO)
settings = get_settings()


async def _warm_weather_cache() -> None:
    """Fill the weather cache before the first panel connects, without ever
    being able to stop the app from starting.

    `refresh_weather` returns False rather than raising for everything it
    anticipates, so this guard is for what it does not: nothing about the
    weather is worth trading the calendar, the photos and the bedtime schedule
    for, and a lifespan that raises takes all of them down. The scheduled
    refresh is already guarded this way - startup was the one path that was not.
    """
    try:
        await asyncio.get_running_loop().run_in_executor(None, refresh_weather)
    except Exception:
        logging.getLogger(__name__).exception("Weather fetch failed during startup")


@asynccontextmanager
async def lifespan(app: FastAPI):
    run_migrations()
    with Session(engine) as session:
        seed_calendars_from_settings(session)
        seed_device_from_settings(session)
    broadcaster.bind_loop(asyncio.get_running_loop())
    await _warm_weather_cache()
    start_scheduler()
    # After the scheduler, so the first full scan is already queued: the watch
    # only reports what changes from here on, and a folder that was filled while
    # the container was down would otherwise wait for the interval to be seen.
    watch = start_folder_watch(settings.photos_dir, run_photo_index)
    # After bind_loop, since its pushed events publish over SSE. Returns as
    # soon as the task is created - the speakers are usually asleep at boot and
    # the calendar must not wait on them.
    start_music()
    yield
    await stop_music()
    if watch is not None:
        watch.stop()
    stop_scheduler()


def create_app() -> FastAPI:
    fastapi_app = FastAPI(title="HomeDash", lifespan=lifespan)
    fastapi_app.include_router(router)

    if settings.frontend_dist.exists():
        fastapi_app.mount(
            "/", StaticFiles(directory=str(settings.frontend_dist), html=True), name="frontend"
        )

    return fastapi_app


app = create_app()


def main() -> None:
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=8000)
