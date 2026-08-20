# HomeDash — Architecture

A map of what exists, so a change can be planned without re-reading the whole codebase.

`CLAUDE.md` is the *plan*: numbered phases, design decisions, and a running "what actually
landed" narrative. This file is the *map*: module layout, data model, API surface, and the
conventions the code follows.

> **Keep this current.** Update it in the same commit as any structural change — a new
> table, endpoint, module, component, or setting. A map that lags the territory is worse
> than no map.

---

## Repo layout

```
backend/          FastAPI + SQLModel + APScheduler, a uv project (package `app`, src layout)
frontend/         SvelteKit 5, adapter-static, compiled to plain files FastAPI serves
deploy/pi/        Raspberry Pi kiosk provisioning: setup script, screen agent, systemd units
migrations/       (under backend/) Alembic revisions, run automatically at startup
Dockerfile        Two stages: node builds the frontend, python serves it alongside the API
docker-compose.yml
.env.example      Every HOMEDASH_* setting, with prose explaining why each exists
```

One image, one process, one SQLite file. The Pi is a thin client that runs a browser and a
~200-line screen agent, nothing else.

---

## Backend — `backend/src/app/`

| Module | Responsibility |
|---|---|
| `main.py` | App factory, lifespan ordering, static mount |
| `config.py` | `Settings` (pydantic-settings) and the config models it parses |
| `db.py` | Engine, `get_session` dependency, `run_migrations` |
| `models.py` | Every SQLModel table |
| `scheduler.py` | APScheduler jobs: calendar syncs, heartbeat, weather |
| `sse.py` | `SSEBroadcaster` fan-out to connected panels |
| `devices.py` | Screen-schedule arithmetic and the device row reconciler |
| `api/routes.py` | Every HTTP endpoint, on one `APIRouter` |
| `api/serializers.py` | `serialize_instance` — the shared event wire shape |
| `calendars/` | Calendar adapters and the sync/expansion pipeline |
| `photos/` | Photo sources, the index, and Pillow resizing for the screensaver |
| `weather/client.py` | Open-Meteo fetch and its in-process cache |
| `cli/` | One-off operator commands (`homedash-google-auth`, `homedash-inspect-calendars`) |

### `calendars/`

| Module | Responsibility |
|---|---|
| `base.py` | `CalendarSource` protocol: `fetch(force)`, `changed`, `sync_state` |
| `ics.py` | ICS over HTTP, ETag conditional GET |
| `caldav_source.py` | CalDAV, sync-token change detection |
| `google_source.py` | Google Calendar `events.list` with `syncToken` |
| `google_auth.py` | OAuth refresh-token credentials |
| `sync.py` | `seed_calendars_from_settings`, `build_adapter`, `sync_source`, `sync_window` |
| `grid.py` | Day/week/month bucketing, anchors, period titles |
| `localtime.py` | `to_local` / `as_utc`, all-day floating-datetime handling |
| `colors.py` | Fixed `PALETTE`, `color_for_index` |
| `providers.py` | Provider detection for the inspect CLI |

**Every adapter returns VEVENT masters, never expanded occurrences**, so all three kinds
flow through the single `recurring_ical_events` expansion in `sync.py`. Google is called
with `singleEvents=false` for exactly this reason.

**Detect cheaply, rebuild fully.** Sync tokens and ETags answer only "did anything
change?". When something did, that source's rows are deleted and rebuilt wholesale.
Incremental upserts would mean rewriting the materialization path, which is where sync
bugs live, to save rebuilding a few hundred rows.

### `photos/`

| Module | Responsibility |
|---|---|
| `base.py` | `PhotoSource` protocol and `SourcePhoto` — the Immich seam |
| `folder.py` | `FolderPhotoSource`: a recursive walk of `HOMEDASH_PHOTOS_DIR` |
| `derivatives.py` | The only module that touches Pillow. Sizes, slots, and rendering |
| `index.py` | `reindex(session)` — reconcile the table and the derivative cache |
| `observer.py` | Debounced filesystem watch, so a dropped-in photo appears in seconds |

**Two derivatives per photo, one per way the panel can be mounted.** For a given panel
orientation a photo either agrees with it (fills the screen, 1920x1080 or 1080x1920) or
disagrees (takes half, 960x1080 or 1080x960, and is paired with another). Pairing rather
than letterboxing, because a black bar down the side of a wall panel reads as a fault.

Derivative filenames are content-addressed (`{hash}-{w}x{h}.jpg`), which is what lets the
image endpoint mark them `immutable` — a photo rewritten in place changes its URL.

`ImageOps.exif_transpose` runs **before** anything else. Phones record orientation in EXIF
rather than rotating pixels, so skipping it shows a large fraction of any real library
sideways *and* crops along the wrong axis.

Orphaned derivatives are removed by a **sweep** at the end of each scan, not by unlinking
alongside each deleted row: two copies of one photo hash identically and share derivative
files, so per-row unlinking would blank the surviving copy.

### Lifespan ordering — `main.py`

```
run_migrations()
  → seed_calendars_from_settings(session)      reconcile config into calendar_sources
  → seed_device_from_settings(session)         reconcile config into the devices row
  → broadcaster.bind_loop(running loop)        SSE publishes are no-ops before this
  → refresh_weather() in a thread executor     blocking HTTP, kept off the event loop
  → start_scheduler()
  → start_folder_watch(photos_dir, …)         after the scheduler, so the first scan is queued
```

The frontend is mounted last, at `/`, with `html=True`. Starlette matches in registration
order, so **API routes must live on `router`** — anything registered after that mount is
unreachable.

### Data model — `models.py`

| Table | Columns | Notes |
|---|---|---|
| `members` | id, name, color, avatar, display_order | **Unused.** Kept because dropping `calendar_sources.member_id` would force a SQLite table rebuild |
| `calendar_sources` | id, kind, name, color, display_order, url, calendar_id, credentials_ref, member_id, enabled, last_synced_at, last_full_sync_at, sync_state | One row per configured calendar. `kind` is `ics`/`caldav`/`google` |
| `events` | id, source_id, uid, raw_vevent, etag, updated_at | `raw_vevent` is stored so the window can be re-expanded without re-fetching |
| `event_instances` | id, event_id, member_id, starts_at (indexed), ends_at, all_day, title, location | Materialized recurrence expansion over a rolling window |
| `settings` | key, value | Generic KV. **Currently unused**; available for runtime-mutable state |
| `devices` | id, name, screen_schedule, last_seen | One row, id 1. `screen_schedule` is JSON text, not columns |
| `photos` | id, path (unique), hash, width, height, orientation, size, mtime_ns, error, added_at | `size`/`mtime_ns` skip re-hashing an unchanged file; `error` keeps a broken file from being retried forever |

No SQLModel `Relationship` attributes anywhere — joins are written explicitly in queries.
No DB-level cascades either; child rows are deleted by hand in the reconcilers.

`event_instances` is the key design call: recurrences are expanded on sync into a flat,
indexed table covering roughly a month back and a year forward. The panel never triggers
an RRULE expansion, and month view is a single indexed range query.

### API surface — `api/routes.py`

| Endpoint | Returns |
|---|---|
| `GET /healthz` | Liveness |
| `GET /api/agenda` | Flat chronological events, each with its `calendar: {id, name, color}` |
| `GET /api/calendar?view=day\|week\|month&anchor=` | Server-bucketed grid, plus title and prev/next anchors |
| `GET /api/calendars` | The legend — every enabled source, so empty calendars still appear |
| `GET /api/devices/{id}/screen` | `{state, until, poll_after_seconds}` for the Pi's screen agent |
| `GET /api/photos?orientation=landscape\|portrait` | Screensaver playlist: each photo's slot, size, and hashed URL |
| `GET /api/photos/{id}/image?orientation=&v=` | One pre-rendered JPEG derivative, `immutable` |
| `GET /api/weather` | The weather cache, verbatim |
| `GET /api/events/stream` | SSE (`EventSourceResponse`) |

**There are no response models.** Handlers are annotated `-> dict` / `-> list[dict]` and
build plain dicts; `serializers.py` exists only where two endpoints must emit an identical
shape. Request params use `Query(...)` with manual validation raising `HTTPException(400)`.
Routes stay thin — date arithmetic lives in `grid.py` and `devices.py`, not in handlers.

The image endpoint is the only non-JSON response in the app. It is a pure file read — the
resize happened at index time, the same discipline the weather cache states for itself. Its
URL carries the content hash as `v`, which is what makes `immutable` safe: a photo replaced
in place gets a new URL rather than a cache entry the panel would hold for a year. The `v`
value is deliberately *not* validated on the way in — it exists to change the URL, and
rejecting a stale one would only turn a slightly old playlist into visible gaps.

`GET /api/devices/{id}/screen` writes `last_seen` as a side effect: the poll *is* the
check-in, throttled to at most one write a minute. It is deliberately non-idempotent.

### Scheduler — `scheduler.py`

| Job id | Interval | Publishes |
|---|---|---|
| `ics_sync` | `ics_poll_interval_minutes` (15) | `events.updated` if changed |
| `fast_sync` | `fast_poll_interval_minutes` (1) | `events.updated` if changed |
| `heartbeat` | 30 seconds | `heartbeat` with `{today, now, screen}` |
| `photo_index` | `photo_index_interval_minutes` (15) | `photos.updated` if changed |
| `weather_refresh` | `weather_cache_minutes` (20) | `weather.updated` if changed |

Every job: `next_run_time=datetime.now()` so it fires at boot rather than after one full
interval; its own `with Session(engine)`; per-item `try/except` with `session.rollback()`
and `logger.exception`, never letting an error escape and kill the job; and it publishes
**only when something actually changed**.

ICS and the fast kinds are split because providers regenerate `.ics` files on their own
schedule — polling harder just re-fetches the same stale bytes.

### SSE — `sse.py`

Module-level singleton `broadcaster`. `publish(event_type, data)` is **synchronous and
safe to call from APScheduler threads** (`loop.call_soon_threadsafe`); it silently no-ops
before `bind_loop`. No per-subscriber filtering and no replay — every panel gets every
event.

Events: `events.updated`, `weather.updated`, `photos.updated`, `heartbeat`.

It also carries `screen` — whether the schedule says the display should be lit — which the
panel needs so the screensaver doesn't start at bedtime. It rides the heartbeat rather than
the browser polling `/api/devices/1/screen`, because that endpoint writes `last_seen`, which
means "the screen agent is alive"; a second client writing it would blur that. Both read the
same `screen_state()`, so the browser and the agent cannot disagree.

The heartbeat is a *named* event rather than sse-starlette's ping, because a ping is an SSE
comment and `EventSource` never surfaces comments to `addEventListener` — so it could not
serve as the liveness signal the panel's watchdog needs. It carries the server's date,
which is also how an always-on panel notices midnight: `events.updated` only fires when a
sync changes something, so on a quiet day nothing else would prompt the rollover.

### Settings — `config.py`

`env_prefix="HOMEDASH_"`, so field `home_timezone` reads `HOMEDASH_HOME_TIMEZONE`.
`get_settings()` is `lru_cache`d, and modules bind `settings = get_settings()` at import
time — **tests override with `monkeypatch.setattr(module, "settings", Settings(...))`**,
not by clearing the cache.

**The JSON-settings pattern**, used by `calendars`, `calendar_credentials`, and
`screen_schedule`:

1. A plain `BaseModel` for the shape, with `@model_validator(mode="after")` for cross-field
   rules.
2. `Annotated[T, NoDecode]` on the settings field, so pydantic-settings hands over the raw
   string instead of decoding it.
3. A `@field_validator(..., mode="before")` delegating to `_parse_json(value, VAR_NAME,
   example)`, which on failure points a caret at the offending character and prints a
   working example. A malformed env var should never surface as a stack trace.

### The reconcile-on-startup pattern

`seed_calendars_from_settings()` and `seed_device_from_settings()` both implement it: **the
env var is the source of truth**, applied at every startup.

1. De-duplicate the config by a stable key (warn, don't raise).
2. Index existing rows by that same key function.
3. Upsert in configured order, deriving positional attributes (color, display_order) from
   the index.
4. Delete rows no longer configured, cascading children by hand.
5. One `commit()`.

Rows are matched on identity (`source_key(kind, url, calendar_id)`), never on name, so a
calendar can be renamed or reordered without losing its events. Observed state — `last_seen`
— is deliberately excluded: rewriting it on boot would make a restart look like a check-in
from a panel that may be unplugged.

Reading config back out of a row is *tolerant*: `schedule_of()` catches a parse failure,
logs, and falls back to the configured value. A panel that goes dark because a row failed
to parse is a miserable way to find out.

### Timezones

Everything is stored in UTC and converted at render time to `HOMEDASH_HOME_TIMEZONE`.
**The panel's own clock and OS timezone are not trusted anywhere.** All date arithmetic —
grid bucketing, screen schedules, DST — happens on the server, for the same reason a thin
client shouldn't reimplement DST.

---

## Frontend — `frontend/src/`

**Svelte 5, runes forced on project-wide** via a compiler option in `vite.config.ts`.
There is no `svelte.config.js` — SvelteKit is configured inline in the `sveltekit()` Vite
plugin options.

> **Runes only compile in `.svelte` and `.svelte.ts` files.** `$state` in a plain `.ts`
> type-checks fine and then throws "$state is not defined" at runtime, blanking the panel.
> `npm run check` cannot catch this.

### Routes

**One route, `/`.** The four views are a `view` state variable on `+page.svelte`, not
separate routes. `+layout.ts` is two lines: `prerender = true`, `ssr = false` — all data
comes from client-side fetch and SSE against the running backend, so there is nothing to
render at build time.

`+layout.svelte` holds the global touch lockdown: `contextmenu` suppression via
`<svelte:document>`, plus `touch-action: manipulation`, `-webkit-touch-callout: none`, and
`user-select: none` on everything except inputs.

`+page.svelte` owns all state and all data loading, and wires everything in a single
`onMount` that returns a cleanup closure.

### `lib/`

| Module | Responsibility |
|---|---|
| `api.ts` | **All** types, all fetchers, and the SSE subscriber |
| `format.ts` | Wall-clock string parsing — `formatTime`, `dateKey`, `formatDayHeading`, `formatHour` |
| `watchdog.ts` | Reloads the page if the SSE stream goes quiet |
| `orientation.svelte.ts` | Reactive `isPortrait` from `matchMedia` |
| `calendarVisibility.ts` | localStorage set of hidden calendar ids |
| `viewPreference.ts` | localStorage of the last-selected view |
| `weatherCodes.ts` | WMO code → description |

### `lib/components/`

| Component | Responsibility |
|---|---|
| `AgendaList.svelte` | Events grouped by day; always injects a "Today" group so the panel is never blank |
| `CalendarLegend.svelte` | Tap-to-hide calendar chips; renders only when there's more than one calendar |
| `DayWeekView.svelte` | Day/week as columns; collapses to one column when narrow *or* portrait |
| `MonthGrid.svelte` | 7-column grid, 3 chips per cell then "+N more" |
| `HourlyForecast.svelte` | 12-hour temperature and rain strip, one SVG in column units |
| `PeriodNav.svelte` | ‹ / title / Today / › |
| `ViewSwitcher.svelte` | Agenda / Day / Week / Month segmented control |
| `WeatherWidget.svelte` | Current conditions, H/L, sunrise/sunset, AQI |

### Idioms

- **No Svelte stores anywhere.** `$state` / `$derived` / `$derived.by` in components;
  cross-component reactive state is a factory in a `.svelte.ts` file returning a getter
  object (`createOrientation`), never a singleton store.
- **Callback props, not events.** `onSelect`, `onToggle`, `onPrev` — no
  `createEventDispatcher` in the codebase.
- **Props are always destructured with an inline type**: `let { weather }: { weather:
  Weather | null } = $props()`.
- **Non-reactive timer modules** follow `start*(options) -> { …, stop }` with injectable
  `now` and side effects, so they're testable without a DOM (`watchdog.ts`).
- **localStorage modules** use a `homedash:`-prefixed key, `load*`/`save*` functions, and
  wrap every access in `try/catch` — unreadable or disabled storage must never blank the
  panel.
- **Fetchers are three lines**: fetch a relative `/api/...` path, throw on `!response.ok`,
  return `response.json()`.

### Styling

**No global stylesheet, no CSS variables, no Tailwind.** Scoped `<style>` blocks only, with
two `:global()` escapes (the touch rules in `+layout.svelte`, and `html`/`body` in
`+page.svelte`).

Theming is purely `color-scheme: light dark` — components use `currentColor`, `Canvas`,
`inherit`, and low-alpha greys (`rgba(128,128,128,0.08–0.3)`) that read in both. Per-item
color arrives as an inline custom property (`style:--item-color`) mixed with `color-mix`.

**Touch targets are 48px minimum** — "the smallest target that stays reliable for a
fingertip on a wall panel, where you are often reaching rather than aiming." Press feedback
is `:active { transform: scale(0.97) }`, because hover does not exist on touch.

### Orientation

The panel is wall-mounted either way up, so both 1920x1080 and 1080x1920 are targets.
**CSS drives the layout wherever it can** — `@media (orientation: portrait)` in
`+page.svelte`, `DayWeekView.svelte`, and `MonthGrid.svelte`. `orientation.svelte.ts`
exists only for the part CSS cannot do: deciding whether to *fetch* something at all.

Note the width trap: portrait is 1080px wide, which is wider than most "mobile"
breakpoints, so a width-only media query would leave seven day columns at ~150px each.
Orientation is checked as well as width.

### Reading the clock

`format.ts` deliberately avoids `Date` and `Intl` for times: it parses the wall-clock digits
straight out of the ISO string the backend already localized. The panel therefore shows home
time regardless of the Pi's OS timezone. `HourlyForecast.svelte` applies the same trick to
find "now" — because Open-Meteo is called with `timezone=auto`, truncating both timestamps
to 13 characters makes plain string comparison chronological, with no `Date` involved.

The one exception is `formatDayHeading`, which uses `Date` for "Today"/"Tomorrow" — and the
authoritative date for that comes from the SSE heartbeat, not the browser.

### The watchdog

A silently frozen display is the number one kiosk failure mode: the page still looks right,
so nobody notices until an appointment is missed. `watchdog.ts` reloads the page if the SSE
stream says nothing for 100 seconds (three missed heartbeats). The throttle timestamp lives
in `sessionStorage`, not a module variable — a reload resets module state, so a backend
that is simply down would otherwise put the panel in a reload loop.

---

## Deploy — `deploy/pi/`

| File | Role |
|---|---|
| `setup.sh` | Provisions a fresh Raspberry Pi OS Lite install; `@PLACEHOLDER@` tokens substituted by `sed` |
| `kiosk-start.sh` | labwc startup command: rotates the output, then Chromium in a restart loop |
| `screen_agent.py` | Polls `/api/devices/1/screen` and blanks/unblanks the display. Stdlib only |
| `homedash-kiosk.service` | labwc + Chromium, `Restart=always` |
| `homedash-screen.service` | The screen agent, `Restart=always` |
| `chromium-policy.json` | Enterprise policy: `URLBlocklist: ["*"]` plus a single allowlist entry |
| `README.md` | Rotation, touch mapping, the read-only overlay, blanking mechanisms |

**Four layers of lockdown:** no desktop (Lite + console autologin + labwc), Chromium kiosk
flags, the enterprise policy (the strongest layer, and the one most guides skip — even a
successful escape from kiosk mode can't load anything else), and the app-level touch CSS.

**The Pi holds no configuration of its own.** The screen agent takes only CLI flags baked
into its unit file; everything else comes from the server response, including how long to
wait before polling again. It applies state only on change, and on an unreachable server it
backs off to five minutes and **leaves the screen alone** — a network blip should not black
out the kitchen calendar, and should not light it at 3am either.

The kiosk loads the bare server root. **A new screen must be a state inside the SPA**, not a
route the browser navigates to — the Chromium allowlist is a single entry.

### Blanking, on a Pi 5 driving a USB-C monitor

Three mechanisms that look right and are dead ends here, each of which costs real time to
re-doubt:

- `vcgencmd display_power` — **does not exist on a Pi 5.** Answers "Command not
  registered"; retired in the move to Wayland/labwc. It is not a broken install.
- `/sys/class/backlight/*/bl_power` — exists **only for the official DSI panel**, not for
  HDMI or USB-C.
- `xset dpms force off` — X11 only; labwc is Wayland.

That leaves `wlopm --off '*'` or `wlr-randr --output X --off`, and `wlopm` is not packaged
in every Debian release, so `setup.sh` installs whichever exists. Whether a *portable*
monitor honours any of it is a separate question — `screen_agent.py probe` answers it on the
hardware, and the agent ships in `--dry-run` until it has been run.

---

## Working on this codebase

```bash
cd backend  && uv sync && uv run alembic upgrade head
cd backend  && uv run uvicorn app.main:app --reload --port 8000
cd frontend && npm run dev        # vite proxies /api and /healthz to :8000

cd backend  && uv run pytest      # the backend suite
cd frontend && npm run check      # svelte-check, the only frontend gate

docker compose up --build         # the real thing
```

There is no CI, no Makefile, and no linter or formatter configured. Line length is held to
roughly 95 characters by hand.

### Migrations

```bash
cd backend && uv run alembic revision --autogenerate -m "…"
```

House style: hand-written hex revision ids, `sqlmodel.sql.sqltypes.AutoString()` for
strings, the autogenerated `# ### commands auto generated ###` banner replaced with a prose
comment explaining the design call, and a real `downgrade()`. Migrations also run
automatically at startup via `run_migrations()`.

`migrations/env.py` passes `disable_existing_loggers=False` to `fileConfig`. Without it,
Alembic silently disables every logger created before it runs — including the app's own,
which swallows error logs.

### Tests

`backend/tests/`, flat, `test_*.py`. Conventions:

- One shared fixture: `session`, in-memory SQLite with `StaticPool` and
  `SQLModel.metadata.create_all` — **not** Alembic. These tests exercise application logic;
  paying the full migration history per test buys nothing.
- API tests build a **bare `FastAPI()`** and `include_router(router)` rather than importing
  `app.main.app`, then override `dependency_overrides[get_session]`. This deliberately skips
  the lifespan: no migrations, no scheduler, no weather fetch.
- Settings are injected with `monkeypatch.setattr(module, "settings", Settings(_env_file=
  None, …))`. `_env_file=None` is always passed so a developer's real `.env` can't leak in.
- **No respx, no freezegun, no pytest-asyncio.** External HTTP is faked by injecting the
  transport into the adapter's constructor (preferred) or monkeypatching the module's
  `httpx.get`. Time is passed in as an argument — production functions take `now`
  explicitly for exactly this reason — or a `FrozenDatetime` subclass is patched in.
- Test names read as sentences, and docstrings explain **why the bug being guarded against
  is plausible**, not what the code does.

### A Playwright panel harness

`.claude/worktrees/headless-browser-harness/tools/panel/panel.mjs` (an untracked worktree)
drives real Chrome against the running panel. It is the only thing that catches
client-render failures — the backend suite stays green while the display is blank. It is
what caught a rune used in a plain `.ts` file.
