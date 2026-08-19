from collections.abc import Generator

from alembic import command
from alembic.config import Config
from sqlmodel import Session, create_engine

from app.config import BACKEND_ROOT, get_settings

settings = get_settings()

engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False},
)


def run_migrations() -> None:
    cfg = Config(str(BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_ROOT / "migrations"))
    command.upgrade(cfg, "head")


def get_session() -> Generator[Session, None, None]:
    with Session(engine) as session:
        yield session
