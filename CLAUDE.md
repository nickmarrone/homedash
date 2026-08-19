# HomeDash — Implementation Plan

> **Status: Phase 1 (Foundation) is complete.** Backend (FastAPI/SQLModel/Alembic/APScheduler),
> ICS + Open-Meteo adapters, SSE, the SvelteKit agenda view, and Docker packaging are all in
> place and verified end-to-end. Next up is Phase 2 (Family and views).

An open-source, self-hosted wall-mounted family calendar in the spirit of Skylight and Hearth. Runs as a Docker container; displayed on a wall-mounted Raspberry Pi with a touch screen, locked into the app.

---

## Stack decisions

| Layer | Choice | Why |
|---|---|---|
| Backend | Python 3.12 + FastAPI | `recurring-ical-events` is the best RRULE expansion library in any ecosystem, and recurrence is the hardest part of this app |
| Frontend | SvelteKit (`adapter-static`) | Compiles away most of its runtime; keeps the Pi comfortable |
| Database | SQLite | One file, one volume mount, trivial backup |
| Scheduler | APScheduler, in-process | No separate worker container needed at this scale |
| Live updates | Server-sent events (`sse-starlette`) | One long-lived page on the Pi that never reloads |
| Packaging | Single Docker image | SvelteKit builds to static files that FastAPI mounts |

### Python dependencies

```
fastapi, uvicorn[standard], sse-starlette
sqlmodel                      # SQLAlchemy + Pydantic
apscheduler
httpx
icalendar, recurring-ical-events, python-dateutil
pillow                        # photo resizing
tzdata
```

---

## Architecture

```
External sources          HomeDash container            Raspberry Pi
─────────────────         ──────────────────            ────────────
ICS / CalDAV      ──┐
Open-Meteo        ──┼──►  sync workers (APScheduler)
Local photo dir   ──┘         │
                              ▼
                          SQLite ──► FastAPI ──► SSE ──►  kiosk browser
                                          │
                                          └──────────────► screen agent
```

The Pi is a thin client. It runs a browser and a ~50-line screen agent, nothing else. This holds even if you use a Pi 4 that could host the container — keeping the split means the display can be replaced or duplicated without touching the server.

### Data model sketch

```
members            id, name, color, avatar, display_order
calendar_sources   id, kind (ics|caldav), name, color, display_order, url, credentials_ref, member_id, enabled
events             id, source_id, uid, raw_vevent, etag, updated_at
event_instances    id, event_id, member_id, starts_at, ends_at, all_day, title, location
devices            id, name, screen_schedule, visible_member_ids, last_seen
settings           key, value
photos             id, path, width, height, orientation, hash, added_at
```

**Key design call:** on each sync, expand recurrences into a materialized `event_instances` table covering a rolling window (roughly one month back, twelve months forward). Views then read a flat, indexed table. The Pi never triggers an RRULE expansion, and month view becomes a single indexed range query.

Store the raw VEVENT on the `events` row so you can re-expand the window without re-fetching from the source.

---

## Phase 1 — Foundation

**Goal:** real calendar data and weather rendering in a desktop browser. No Pi, no profiles, no styling polish.

### Deliverables

- FastAPI skeleton, SQLite via SQLModel, Alembic migrations from the start
- **ICS calendar adapter** — poll a secret iCal URL, parse with `icalendar`, expand with `recurring-ical-events`, write to `event_instances`
- **Weather adapter** — Open-Meteo, one provider covering everything:
  - `api.open-meteo.com/v1/forecast` — current conditions, 1-day and 10-day
  - `air-quality-api.open-meteo.com/v1/air-quality` — US AQI, European AQI, PM2.5
  - Daily fields `sunrise`, `sunset`, `daylight_duration` come from the same forecast call
  - No API key required
- **Agenda view** in SvelteKit — a flat chronological list, the easiest view and the one people actually read across a kitchen
- SSE endpoint pushing `events.updated` and `weather.updated`
- `docker-compose.yml` with a named volume for the database
- `/healthz` endpoint

### Notes

- Define the `CalendarSource` protocol now (`fetch() -> list[VEvent]`, plus etag/sync-token state) even though there's only one implementation. Phase 2 adds CalDAV behind the same interface.
- Google caches private ICS feeds aggressively — changes can take **hours** to appear. Fine for a wall calendar, but don't spend a day debugging a sync that isn't broken.
- Cache weather for 15–30 minutes. Never fetch on page load.
- Store everything in UTC, convert at render time using a configured home timezone.

### Done when

You can point a laptop browser at the container and see this week's real appointments and today's weather, updating without a manual refresh.

### Implementation notes (what actually landed)

- `backend/` is a `uv`-managed Python 3.12 project, package name `app` (src layout:
  `backend/src/app/`). Run things with `uv run ...` from `backend/`.
- `CalendarSource` protocol lives in `app/calendars/base.py`; `app/calendars/ics.py` is the
  HTTP/ETag-aware ICS implementation; `app/calendars/sync.py` does fetch → expand (via
  `recurring_ical_events`) → materialize into `event_instances`, replacing that source's rows
  each sync (simple and correct at this scale).
- **Multiple calendars, each with its own color.** `HOMEDASH_ICS_CALENDARS` is a JSON list of
  `{"name", "url"}` objects (parsed by pydantic-settings into `config.CalendarConfig`); it
  replaced the old single-valued `HOMEDASH_ICS_URL`. `seed_ics_calendars_from_settings()` in
  `app/calendars/sync.py` reconciles that list into `calendar_sources` on every startup: rows are
  matched **by URL**, the list is de-duplicated by URL, and any ICS source no longer listed is
  deleted together with its `events`/`event_instances` — otherwise a removed calendar's
  appointments would sit on the panel forever. Colors are not user-settable: each calendar takes
  `PALETTE[index]` from `app/calendars/colors.py` by its position in the env var, so reordering
  the list recolors the calendars.
- `GET /api/agenda` joins `event_instances → events → calendar_sources` to attach a
  `calendar: {id, name, color}` to every item (no `source_id` was denormalized onto
  `event_instances`; at 200 rows the two-hop join costs nothing and cannot go stale).
  `GET /api/calendars` serves the legend separately so a calendar with nothing scheduled still
  appears and swatches don't reshuffle as events come and go. The `member` field is untouched and
  still always `null` — Phase 2's per-member colors layer on top of source colors rather than
  replacing them.
- `app/scheduler.py` runs the ICS sync and weather refresh on `APScheduler` intervals
  (`HOMEDASH_ICS_POLL_INTERVAL_MINUTES`, `HOMEDASH_WEATHER_CACHE_MINUTES`) and publishes
  `events.updated` / `weather.updated` over `app/sse.py`'s broadcaster.
- Weather is fetched proactively (on startup + on schedule) into an in-process cache;
  `GET /api/weather` only ever reads that cache, never fetches live.
- `GET /api/agenda` converts stored UTC instants to `HOMEDASH_HOME_TIMEZONE` at render time.
  The frontend then parses the wall-clock digits straight out of that ISO string (see
  `frontend/src/lib/format.ts`) rather than re-interpreting through the browser's own timezone,
  so the panel always shows home-local time regardless of the Pi's OS clock settings.
- Alembic migrations run automatically at app startup (`app/db.py:run_migrations`). One gotcha
  worth remembering: Alembic's `env.py` calls `fileConfig(...)`, which by default
  (`disable_existing_loggers=True`) silently disables every logger created before it runs -
  including the app's own. `migrations/env.py` passes `disable_existing_loggers=False` to avoid
  swallowing error logs.
- `frontend/` has no separate `svelte.config.js` — this SvelteKit version configures everything
  (including the adapter) through the `sveltekit()` Vite plugin options in `vite.config.ts`.
  `adapter-static` is configured for full prerendering (`export const prerender = true` in
  `+layout.ts`) with `ssr = false`, since there's no meaningful SSR data at build time - all data
  comes from client-side `fetch`/SSE against the running backend.
- Single `Dockerfile` at the repo root: a `node` stage builds the frontend to
  `frontend/build`, then a `python:3.12-slim` stage `uv sync`s the backend and copies the built
  frontend in alongside it, preserving the same `backend/` + `frontend/build` sibling layout the
  app expects locally (see `Settings.frontend_dist` in `app/config.py`).
- Verified end-to-end via `docker compose up --build`: `/healthz`, `/api/agenda`, and the static
  frontend all serve correctly from the container. Live weather could not be verified against the
  real Open-Meteo API from this sandbox (outbound network policy blocks it), but the failure path
  was confirmed to degrade gracefully — it logs and leaves the cache empty rather than crashing.

---

## Phase 2 — Family and views

**Goal:** the app is genuinely usable as a family calendar.

### Deliverables

- **Members** — CRUD for name, color, avatar. Ship a set of built-in avatars; allow image upload later.
- **Source-to-member mapping** — a calendar source belongs to a member; support a shared "household" calendar with no member.
- **Member filtering** — select which members are visible. Persist per *device*, not per user, since there's no login on the wall panel. Store on the `devices` row keyed by a device ID so it survives reboots.
- **Day / week / month views** — month is the most layout-intensive; precompute the grid server-side rather than doing it in JS on every navigation.
- **CalDAV adapter** — real bidirectional sync with etag/sync-token deltas via the `caldav` library. Apple, Fastmail, and Nextcloud work with app-specific passwords; Google requires an OAuth2 consent flow.
- Basic event creation and editing (for CalDAV sources only; ICS is read-only)
- Touch-friendly styling pass — large tap targets, high contrast, readable from across a room

### Notes

- The permission model matters here even though the kid-lock UI is post-v1. Decide now whether "edit" is a device-level capability or a per-member one; retrofitting it is painful.
- Recurring event edits are the classic trap: "this occurrence", "this and future", "all occurrences". Support only "this occurrence" in v1 and say so in the UI.

### Done when

Each family member has a color, the kids' tablet view shows only their items, and all four views render correctly.

---

## Phase 3 — The wall panel

**Goal:** a Pi on the wall running HomeDash and nothing else, on a schedule.

### Hardware

Undecided — see Open decisions. Everything below works on a Pi 2, 3, or 4; only the display stack differs.

### Kiosk lockdown, four layers

**1. No desktop.** Raspberry Pi OS Lite, autologin to console. On Pi 2/3, `startx` into a bare window manager (matchbox or openbox), no panel or menu. On Pi 4, labwc is the better-supported path. Add `unclutter -idle 0` to hide the cursor.

**2. Browser flags.**

```
chromium --kiosk --incognito --noerrdialogs --disable-infobars \
  --disable-session-crashed-bubble --disable-pinch \
  --overscroll-history-navigation=0 \
  --check-for-update-interval=31536000 \
  http://homedash.local:8000
```

If Chromium is too heavy at 1 GB, evaluate Cog on WPE WebKit — the embedded browser used in set-top boxes, far smaller footprint, and no UI chrome to escape into.

**3. Chromium enterprise policy.** The strongest layer, and the one most guides skip. Drop JSON in `/etc/chromium/policies/managed/homedash.json`:

```json
{
  "URLBlocklist": ["*"],
  "URLAllowlist": ["http://homedash.local:8000"],
  "DeveloperToolsAvailability": 2,
  "IncognitoModeAvailability": 1,
  "BookmarkBarEnabled": false
}
```

Now even a successful escape from kiosk mode can't load anything else.

**4. App-level.** On touch, the escape routes are gestures, not keys:

```css
* { -webkit-touch-callout: none; user-select: none; touch-action: manipulation; }
```

Plus `preventDefault` on `contextmenu`, and a client-side watchdog that reloads the page if the SSE stream goes stale for more than a few minutes. Silently frozen displays are the number one kiosk failure mode.

### Screen sleep and wake

A small Python agent on the Pi polls `GET /api/devices/{id}/screen` every 30 seconds and applies the returned state. Schedule lives in the HomeDash settings UI, not in a cron file you'll forget about.

| Panel type | Mechanism |
|---|---|
| Official DSI touchscreen | `/sys/class/backlight/*/bl_power` (0 on, 1 off), `brightness` for dimming |
| HDMI | `vcgencmd display_power 0\|1` |
| X11 fallback | `xset dpms force off` |
| Wayland / labwc | `wlopm --off '*'` |

Ship the agent as a systemd unit with `Restart=always`. Same for the browser.

### Also in this phase

- Don't attach a keyboard — removes most of the attack surface for free
- Enable the read-only overlay filesystem via `raspi-config` — protects the SD card from power-yank corruption, and any mess resets on reboot
- Device registration flow: first boot shows a pairing code, you name the device from the settings UI
- Write the SD card setup as a documented script in `deploy/pi/`, not as tribal knowledge

### Done when

The Pi boots straight into HomeDash, a curious eight-year-old can't get out of it, and the screen turns itself off at bedtime.

---

## Phase 4 — Screensaver

**Goal:** the panel shows family photos when it's idle.

### Deliverables

- **Local folder photo source.** The server watches a mounted directory (`/photos`), indexes new files, and pre-resizes them to the panel's exact resolution with Pillow. Populate the folder however you like: Syncthing, an SMB share, Nextcloud, or dragging files in once a quarter.
- Define the `PhotoSource` protocol here — `list_photos() -> list[Photo]` returning stable IDs plus local paths. Everything downstream depends on this interface, not on the folder.
- Idle detection: no touch interaction for *N* minutes, and the screen schedule says the display should be on
- Crossfade transitions, configurable dwell time, shuffle with no immediate repeats
- Orientation handling: filter to landscape, or pair two portrait photos side by side. Pillarboxed portraits look bad on a wall panel.
- Tap anywhere to dismiss and return to the calendar

### Notes

- Never serve originals to the Pi. Resize once on the server, cache the derivative, serve that.
- Skip non-image files and anything Pillow can't decode; log and move on rather than crashing the indexer.
- Watch the folder with `watchdog` rather than polling, but re-scan on startup regardless.

### Done when

The panel drifts into a photo slideshow when nobody's using it, and a tap brings the calendar straight back.

---

## Future features (post-v1)

### Immich photo source

You already run Immich, so this is the intended second `PhotoSource` implementation. The API is documented and mostly stable — details worth keeping:

- Auth is an `x-api-key` header. **Create a dedicated read-only key** with only `album.read` and `asset.read` permissions, so a bug in HomeDash can never delete a family photo.
- `GET /api/albums/{albumId}` returns the album with an `assets` array. That single call is the entire sync. Get the album ID from the URL when you open the album in the Immich web UI, or `GET /api/albums` to list them for a settings-page picker.
- `GET /api/assets/{id}/thumbnail` and `GET /api/assets/{id}/original` are both marked Stable. **Use `/thumbnail` with a size parameter,** not `/original` — Immich already generated preview derivatives at import time. Check the exact size parameter against your version's docs at `api.immich.app`.
- **Never let the API key reach the browser.** Proxy through HomeDash: the Pi requests `/api/photos/{id}` from you, you fetch from Immich.
- Cache locally anyway, so the screensaver survives an Immich restart.
- Filter to `type === "IMAGE"` — videos will otherwise render as broken frames.
- Use `exifInfo` width and height for the orientation logic from Phase 4.
- Immich ships breaking API changes (the `/api/asset` → `/api/assets` rename broke a lot of scripts). Stick to endpoints marked `x-immich-state: "Stable"` and check the `x-immich-history` field before depending on anything.
- Optional polish: Immich emits WebSocket events when assets are uploaded or albums are modified. Subscribe and a photo taken at dinner appears on the kitchen screen minutes later. A 15-minute poll is fine to start.
- Deployment: put HomeDash on the same Docker network as Immich and use the internal service hostname. No TLS hop, no external exposure.

### Kid lock

App-level, distinct from the device lockdown in Phase 3. PIN gate on editing, settings, and member filter changes. Design the permission model in Phase 2 even if the UI lands here.

### Jellyfin → DLNA / HEOS music

Browse a Jellyfin library and push playback to HEOS speakers over DLNA. Likely needs `upnpclient` or similar for device discovery, plus HEOS's own telnet-style CLI protocol on port 1255 for the parts DLNA doesn't cover. Investigate whether HEOS's native protocol is a better target than generic DLNA.

### Chores and rewards

Repeating chore definitions, per-member assignment, completion tracking, and a points/rewards system for kids. Reuses the recurrence machinery from Phase 1 — the RRULE expander should work for chores as-is.

### Other candidates

- Photo source: Google Drive folder (Google Photos is not viable — the Library API scopes for reading a user's own albums were removed in March 2025)
- Photo source: iCloud shared album via the undocumented `sharedstreams` endpoint
- Multiple panels with independent filters
- Touch-to-wake and PIR motion sensor
- Home Assistant integration

---

## Open decisions

1. **Which Pi.** Pi 2 and 3 are both 1 GB and must stay thin clients. Pi 4 (4 GB) could host the container and the display on one box — simpler product, but a single point of failure, and SQLite on an SD card 24/7 will eventually kill the card. If you go Pi 4 all-in-one, boot from a USB SSD.
2. **Panel size and resolution.** Drives the entire layout, the photo resize target, and the backlight control mechanism. Worth settling before Phase 2 styling.
3. **Auth on the web UI.** The wall panel has no login, but the phone-based admin UI probably needs one. Simplest workable answer: a single household password, LAN-only, no accounts.
4. **Repo layout.** Monorepo with `backend/` and `frontend/` is the obvious call given the single-image build. *(Settled — this is what's in the repo.)*

---

## Suggested first commit

Docker Compose with FastAPI serving a static "hello" page, SQLite mounted, `/healthz` returning 200. Get the deployment loop working before writing any features — every phase above assumes you can rebuild and redeploy in under a minute.
