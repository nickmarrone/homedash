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
cp .env.example .env   # fill in HOMEDASH_HOME_TIMEZONE, HOMEDASH_ICS_CALENDARS, lat/lon
docker compose up --build
```

Then open http://localhost:8000. `/healthz` returns 200 when the backend is ready.

## Calendars

`HOMEDASH_ICS_CALENDARS` is a JSON list of `{"name", "url"}` entries. Each is
assigned a color automatically from a fixed palette, in the order you list them,
and the agenda shows a matching accent bar plus a legend naming the calendars.
Reordering the list recolors the calendars.

The env var is the source of truth and is reconciled on every startup: entries
are matched by URL, so renaming or reordering a calendar keeps its events, while
removing an entry deletes that calendar and its events. There is no calendars UI
or API - that lands in Phase 2.

Keep the JSON on a **single line** - no trailing backslashes to wrap it, and no
backslashes inside the strings, since JSON reads those as escapes. Leave the
value blank or use `[]` for "no calendars". A malformed value fails at startup
with a message pointing at the offending character.

Tap a calendar in the legend to hide or show its events. The choice is stored
per device (browser localStorage), so each panel keeps its own filter across
reboots. Hidden calendars stay in the legend, dimmed with an empty checkbox, so
they can always be tapped back on.

## Weather

The header shows current conditions, today's high/low, sunrise/sunset, and AQI.
Under it, a strip covers the next 12 hours: temperature per hour, with rain
probability as a bar under each one and a percentage printed for hours at 20% or
above. Everything comes from the single Open-Meteo forecast call the backend
already makes, refreshed on the `HOMEDASH_WEATHER_CACHE_MINUTES` schedule and
pushed to the panel over SSE - the page never fetches weather on load.

Set `HOMEDASH_WEATHER_LATITUDE` / `HOMEDASH_WEATHER_LONGITUDE` for your home, and
`HOMEDASH_WEATHER_TEMPERATURE_UNIT` to `fahrenheit` or `celsius`. Rain is reported
as a probability, so it reads the same either way. No API key is needed.

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
