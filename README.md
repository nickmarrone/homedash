# HomeDash

An open-source, self-hosted wall-mounted family calendar in the spirit of Skylight and Hearth.

See `CLAUDE.md` for the full phased implementation plan.

## Status

**Phase 1 (Foundation) is done.** Real ICS calendar sync, Open-Meteo weather, an
agenda view, and live updates over SSE, all served from one Docker image.

## Repo layout

```
backend/    FastAPI + SQLModel + Alembic + APScheduler (Python 3.12, uv)
frontend/   SvelteKit (adapter-static) - compiles to static files the backend serves
```

## Running with Docker (recommended)

```bash
cp .env.example .env   # fill in HOMEDASH_HOME_TIMEZONE, HOMEDASH_ICS_URL, lat/lon
docker compose up --build
```

Then open http://localhost:8000. `/healthz` returns 200 when the backend is ready.

## Local development

Backend:

```bash
cd backend
uv sync
uv run alembic upgrade head
uv run uvicorn app.main:app --reload --port 8000
```

Frontend (dev server proxies `/api` and `/healthz` to `127.0.0.1:8000`):

```bash
cd frontend
npm install
npm run dev
```

Configuration is via `HOMEDASH_*` environment variables read by
`backend/src/app/config.py` (or a `backend/.env` file) - see `.env.example` for
the full list.

## Migrations

Schema changes go through Alembic:

```bash
cd backend
uv run alembic revision --autogenerate -m "describe the change"
uv run alembic upgrade head
```

The app also runs `alembic upgrade head` automatically on startup.
