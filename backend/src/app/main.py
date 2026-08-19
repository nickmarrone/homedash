import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.routes import router
from app.config import get_settings
from app.db import run_migrations
from app.scheduler import start_scheduler, stop_scheduler
from app.sse import broadcaster
from app.weather.client import refresh_weather

logging.basicConfig(level=logging.INFO)
settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    run_migrations()
    broadcaster.bind_loop(asyncio.get_running_loop())
    await asyncio.get_running_loop().run_in_executor(None, refresh_weather)
    start_scheduler()
    yield
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
