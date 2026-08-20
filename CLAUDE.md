# HomeDash — Implementation Plan

> **Status: Phases 1 to 4 are complete.** Backend (FastAPI/SQLModel/Alembic/APScheduler),
> ICS + CalDAV + Google Calendar adapters, Open-Meteo weather, SSE, agenda/day/week/month
> views, a backend test suite, and Docker packaging are all in place, as is the Raspberry Pi
> wall panel: `deploy/pi/` provisions the kiosk, the `devices` table carries the screen
> schedule, and the panel is laid out for both orientations. Phase 4 added the photo
> screensaver, and with it the black-page fallback for a screen that will not blank itself.
> One item is still open on hardware — which mechanism actually blanks the monitor (see
> Phase 3) — but it is no longer blocking: the panel goes dark either way.
>
> Everything below Phase 4 is post-v1; what has landed there is recorded under "Landed
> after v1". The obvious next step is the Immich photo source, which the `PhotoSource`
> protocol already has a seam for.

An open-source, self-hosted wall-mounted family calendar in the spirit of Skylight and Hearth. Runs as a Docker container; displayed on a wall-mounted Raspberry Pi with a touch screen, locked into the app.

> **Looking for how the code is laid out?** See [`ARCHITECTURE.md`](ARCHITECTURE.md) — module
> map, data model, API surface, and the conventions to follow. This file is the plan and the
> running "what actually landed" narrative; that one is the map, and is kept current commit
> by commit so a change can be planned without re-reading the codebase.

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
- **Multiple calendars, each with its own color.** (Phase 2 renamed this variable to
  `HOMEDASH_CALENDARS` and gave entries a `kind`; the old name still works as an alias. The
  colour and reconciliation behaviour described here is unchanged.) `HOMEDASH_ICS_CALENDARS`
  is a JSON list of
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
- Calendar visibility is a **frontend-only, per-device** filter: tapping a legend chip toggles
  that calendar's events, persisted in `localStorage` via `frontend/src/lib/calendarVisibility.ts`.
  The agenda API is unchanged and still returns everything - Phase 2 moves this onto the `devices`
  row, which is the same per-device scope, so no API contract has to change to get there.
- `app/scheduler.py` runs calendar syncs and the weather refresh on `APScheduler` intervals
  (`HOMEDASH_ICS_POLL_INTERVAL_MINUTES`, `HOMEDASH_WEATHER_CACHE_MINUTES`) and publishes
  `events.updated` / `weather.updated` over `app/sse.py`'s broadcaster.
- Weather is fetched proactively (on startup + on schedule) into an in-process cache;
  `GET /api/weather` only ever reads that cache, never fetches live.
- **Hourly strip.** The forecast call also requests
  `hourly=temperature_2m,precipitation_probability`, and
  `frontend/src/lib/components/HourlyForecast.svelte` renders the next 12 hours as a
  full-width row under the header: temperatures as plain text, rain probability as one
  SVG whose `viewBox` is in column units so all bars share a baseline and the strip
  scales to any panel width. `forecast_hours=48` is set explicitly - without it the
  hourly block inherits `forecast_days=10` and returns 240 timesteps that get
  re-serialized on every `/api/weather` read. 48 is deliberately generous so the window
  cannot run short late in the day whether Open-Meteo anchors the array at the current
  hour or at local midnight.
- **Finding "now" in the hourly array without the browser clock.** Open-Meteo is called
  with `timezone=auto`, so `current.time` (`2026-08-19T13:45`) and `hourly.time`
  (`2026-08-19T13:00`) are the same fixed-width format in the same timezone. Truncating
  both to 13 characters makes plain string comparison chronological, so the strip finds
  its start index with no `Date` and no timezone math - the same reasoning that keeps
  `format.ts` off `Intl`. The trade-off: `current.time` is frozen at fetch time, so the
  "Now" column can lag by up to `HOMEDASH_WEATHER_CACHE_MINUTES`, which is invisible at
  hour granularity.
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

## Phase 2 — Fast sync and views

**Goal:** the panel is current within a minute, and readable as a day, week, or month.

### What changed from the original plan

This phase was written around three assumptions that turned out to be wrong. They are
recorded here because they still shape Phases 3 and 4.

1. **One shared panel, not one per person.** Everyone looks at the same screen in the
   kitchen. Per-device filtering therefore collapses into "this panel's preference", which
   `frontend/src/lib/calendarVisibility.ts` already does in `localStorage`. No `devices` row
   is needed for filtering — that table's remaining job is Phase 3's screen schedule.
2. **No event editing.** Parents edit on their phones and the change syncs down. HomeDash is
   read-only end to end, which is also why the Google adapter asks for `calendar.readonly`.
3. **No members.** With one calendar per person, `calendar_sources` *is* the person. Phase 1's
   per-calendar colors, legend, and tap-to-hide already delivered the family-filtering
   deliverable. The `members` table stays in the schema, unused; `EventInstance.member_id`
   stays null.

A fourth problem surfaced during planning and became the headline: **ICS is too slow.**
Google regenerates a private `.ics` feed on its own schedule, often hours behind, so the file
we fetch is already stale — polling harder just re-fetches the same stale bytes. That is what
pulled CalDAV forward. It was originally going to be deferred *because* dropping the write
path removed most of its value; latency alone justified it.

### Deliverables

- **Calendar kinds.** `HOMEDASH_CALENDARS` replaces `HOMEDASH_ICS_CALENDARS` (kept working as
  a deprecated alias). Each entry has a `kind`: `ics`, `caldav`, or `google`.
- **CalDAV adapter** — Apple, Fastmail, Nextcloud, with app-specific passwords.
- **Google Calendar adapter** — `events.list` with `syncToken`, behind an OAuth2 refresh token.
- **Two poll cadences** — ICS keeps 15 minutes, since faster changes nothing; CalDAV and Google
  poll every minute.
- **Day / week / month views**, precomputed server-side, plus the existing agenda.
- **A backend test suite** — there were none before this phase.
- Touch-friendly styling pass — large tap targets, no long-press or drag-select escapes

### Notes

- **Detect cheaply, rebuild fully.** Sync tokens are used only to answer "did anything
  change?". When something did, the existing wholesale rebuild runs. True incremental sync —
  upserting changed events, tombstoning cancelled ones — would mean rewriting the
  materialization path, which is where sync bugs live, to save a rebuild of a few hundred rows.
- **Every adapter returns VEVENT masters,** never expanded occurrences, so all three kinds flow
  through the one `recurring_ical_events` expansion. This is why Google is called with
  `singleEvents=false`.
- **Google's OAuth rules move.** The out-of-band flow is gone and non-loopback plaintext
  redirect URIs are rejected, so authorization is a one-time CLI on a machine with a browser,
  not something the panel does. Check the current docs before changing that flow.
- A consent screen left in **Testing** mode expires refresh tokens after seven days, which
  presents as the panel breaking a week after it started working. Publish it.

### Done when

An event edited on a phone appears on the panel within about a minute, and all four views
render correctly. *(Done.)*


## Phase 3 — The wall panel

**Goal:** a Pi on the wall running HomeDash and nothing else, on a schedule.

### Hardware

**Settled: a Raspberry Pi 5 as a thin client, driving an InnoView 15.6" 1080p 10-point touch
portable monitor** (2x USB-C, 1x HDMI). Mountable either way up, so the layout targets both
1920x1080 and 1080x1920.

This matters more than a hardware note usually would, because it invalidates most of the
display advice written below when the Pi was undecided — see the table under "Screen sleep and
wake". A Pi 5 runs labwc/Wayland, has no `vcgencmd display_power`, and a USB-C monitor has no
backlight sysfs node.

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

A small Python agent on the Pi polls `GET /api/devices/{id}/screen` every 30 seconds and applies the returned state. Schedule lives in the HomeDash settings UI, not in a cron file you'll forget about. This is the only thing the `devices` table is for — filtering was settled in Phase 2 as panel-local.

| Panel type | Mechanism | Applies here? |
|---|---|---|
| Official DSI touchscreen | `/sys/class/backlight/*/bl_power` (0 on, 1 off), `brightness` for dimming | **No** — that node exists only for the DSI panel |
| HDMI | `vcgencmd display_power 0\|1` | **No** — removed on Pi 5; answers "Command not registered" |
| X11 fallback | `xset dpms force off` | **No** — labwc is Wayland |
| Wayland / labwc | `wlopm --off '*'`, or `wlr-randr --output X --off` | **Yes** — the only path left |

The first three rows were written before the hardware was chosen and are all dead ends on a
Pi 5 driving a USB-C monitor. `vcgencmd display_power` in particular was retired in the move
to Wayland/labwc, which is easy to mistake for a broken install. `wlopm` is also not packaged
in every Debian release, so `deploy/pi/setup.sh` installs whichever of it and `wlr-randr`
exist rather than failing on the one that does not.

Whether a *portable* monitor honours any of this is a separate question — some sleep, some
show a floating "No Signal" logo, some ignore it. `screen_agent.py probe` exists to answer it
on the actual hardware, and the agent ships in `--dry-run` until it has been.

Ship the agent as a systemd unit with `Restart=always`. Same for the browser.

### Also in this phase

- Don't attach a keyboard — removes most of the attack surface for free
- Enable the read-only overlay filesystem via `raspi-config` — protects the SD card from power-yank corruption, and any mess resets on reboot
- Device registration: there is exactly one panel, so this is a configured device rather than a
  pairing flow. The `devices` row exists for the screen schedule and last-seen, not for filters —
  view and calendar visibility are panel-local `localStorage`, settled in Phase 2
- Write the SD card setup as a documented script in `deploy/pi/`, not as tribal knowledge

### What actually landed

- `devices` table, `HOMEDASH_SCREEN_SCHEDULE`, and `GET /api/devices/{id}/screen`. The schedule
  is seeded onto the row at startup the way calendars are, so a settings UI can later `PUT`
  the same row with no API change. All the date arithmetic is server-side, including the
  overnight-wrap case (`on` later than `off`) — the Pi does none.
- **A 30-second SSE `heartbeat`.** sse-starlette's own ping is an SSE *comment*, which
  EventSource never surfaces to an `addEventListener`, so it could not serve as the liveness
  signal a watchdog needs. The heartbeat also carries the server's date, which fixed a bug
  this plan never anticipated: `events.updated` only fires when a sync *changes* something,
  so on a quiet day an always-on panel kept yesterday's "Today" heading forever.
- **Portrait as well as landscape** (see Open decisions). Driven by a CSS media query, so it
  follows a physical rotation with nothing to configure.
- `deploy/pi/` — `setup.sh`, the screen agent, systemd units, the Chromium enterprise policy,
  and a README covering rotation, touch mapping, and the read-only overlay.

Layer 4 of the lockdown was already done: Phase 2's touch pass shipped the `contextmenu`
suppressor, `user-select`, `touch-action`, and 48px targets. Only the watchdog was missing.

### What the hardware then said about it (2026-08-20)

The panel went onto **Ubuntu 26.04 Desktop**, not Raspberry Pi OS Lite, and provisioning it
failed in three ways worth keeping:

- **The kiosk unit never autostarted, on any image.** It was `WantedBy=graphical.target`
  while `setup.sh` set the default target to `multi-user` — a target the script itself
  guaranteed would never be reached. A bug in the repo, not a distro mismatch, and invisible
  until a reboot.
- **A desktop image cannot be provisioned as a console one.** Starting labwc on tty1 while
  GDM holds the seat loses that fight every five seconds, and `set-default multi-user`
  means the Pi comes up as a text console. `setup.sh` now detects the session and installs
  either system units + labwc, or `graphical-session.target` user units on top of GNOME —
  and repairs a machine an earlier run left console-only.
- **Ubuntu's Chromium is a snap** whose `home` interface allows non-hidden paths under
  `$HOME` and nothing else, so the throwaway `/tmp` profile was refused and the browser
  exited at once. Behind a restart loop that is indistinguishable from a crash, which is why
  the loop now backs off and prints the command line it tried.

Lockdown became `--mode locked` rather than the only way in; **`--mode simple` is the
default** — fullscreen, escapable, no enterprise policy. The policy layer applies to every
Chromium on the machine, so leaving it on during setup makes a browser opened to check
something look broken for no reason. Blanking split the same way as the session: Mutter
implements none of the wlroots protocols, so GNOME gets `org.gnome.ScreenSaver` on the
session bus, which is also why the screen agent must run as a user unit there.

Still open: none of this has been run on the reinstalled Pi yet.

### Done when

The Pi boots straight into HomeDash, a curious eight-year-old can't get out of it, and the
screen turns itself off at bedtime. *(Done in the repo; unverified on the hardware. Both
remaining steps happen on the Pi: run `setup.sh` on the reinstalled image, then
`homedash-screen-agent probe` and drop `--dry-run`. `--mode locked` is the eight-year-old
half, and comes last.)*

---

## Phase 4 — Screensaver

**Goal:** the panel shows family photos when it's idle.

### Deliverables

- **Local folder photo source.** The server watches a mounted directory (`/photos`), indexes new files, and pre-resizes them to the panel's exact resolution with Pillow. Populate the folder however you like: Syncthing, an SMB share, Nextcloud, or dragging files in once a quarter.
- Define the `PhotoSource` protocol here — `list_photos() -> list[Photo]` returning stable IDs plus local paths. Everything downstream depends on this interface, not on the folder.
- Idle detection: no touch interaction for *N* minutes, and the screen schedule says the display should be on
- Crossfade transitions, configurable dwell time, shuffle with no immediate repeats
- Orientation handling: pair or crop whichever way the photo disagrees with the panel. Letterboxing looks bad on a wall panel either way up, and since this panel can be mounted in portrait, the mismatched orientation is not always the portrait one — see Open decision 2.
- Tap anywhere to dismiss and return to the calendar

### Notes

- Never serve originals to the Pi. Resize once on the server, cache the derivative, serve that.
- Skip non-image files and anything Pillow can't decode; log and move on rather than crashing the indexer.
- Watch the folder with `watchdog` rather than polling, but re-scan on startup regardless.

### Done when

The panel drifts into a photo slideshow when nobody's using it, and a tap brings the calendar straight back. *(Done.)*

### What actually landed

- `app/photos/` mirrors `app/calendars/`: `base.py` holds the `PhotoSource` protocol,
  `folder.py` walks the directory, `derivatives.py` is the only module that touches Pillow,
  `index.py` reconciles, `observer.py` watches. The reconciler follows the same contract the
  calendar and device seeders do — the source is the truth, and anything it stops offering is
  deleted, because a photo pulled out of the folder must stop appearing on the wall.
- **Two derivatives per photo, not four.** For a given panel orientation a photo either
  agrees with it and fills the screen, or disagrees and takes half of it to be paired. It
  cannot be both, so 1920x1080 + 1080x960 for a landscape photo, and 1080x1920 + 960x1080 for
  a portrait one. The plan's warning about portrait panels held up in testing: on a portrait
  panel it really is the *landscape* photos that pair.
- **`exif_transpose` before anything else.** Phones store orientation as a tag rather than
  rotating pixels. Skipping it would not merely show photos sideways — it would measure them
  along the wrong axis, hand them the wrong slot, and crop them accordingly.
- **`size` and `mtime_ns` on the row**, so a rescan never reopens an unchanged file. This is
  the only part of indexing that would actually cost something, since it runs forever on a
  folder that changes a few times a year.
- **An undecodable file still gets a row**, holding the error. Without one there is nothing
  to remember it by and it would be reopened and fail on every scan. Rows with an error are
  excluded from the playlist.
- **Orphaned derivatives go in a sweep**, not by unlinking next to each deleted row: two
  copies of one photo hash identically and share their files, so per-row unlinking would
  blank the copy that is still there.
- **The `watchdog` observer is the fast path, not the correct one.** inotify sees nothing
  when the folder is filled from the far side of an SMB or NFS mount, which is a very common
  way to run this, so the scheduled rescan is what actually guarantees the photo appears.
  Events are debounced, because a sync tool fires hundreds of them for one batch.
- **The server owns the catalogue; the panel owns the slideshow.** `/api/photos` returns what
  exists and how big it is, and the browser shuffles, pairs, and times. That keeps the server
  stateless per panel and puts the state where every other panel-local preference already is.
- **Image URLs carry the content hash**, so the response can be `immutable`. On a slideshow
  that loops for months, the alternative is re-fetching the whole library on every pass. The
  hash is not validated on the way in — it exists to change the URL, and rejecting a stale one
  would turn a slightly old playlist into visible gaps.
- **Screen state rides the heartbeat** rather than the browser polling
  `/api/devices/1/screen`, which writes `last_seen` as a side effect — that field means "the
  screen agent is alive", and a second writer would blur it. Both paths call the same
  `screen_state()`, so the browser and the Pi cannot disagree about bedtime.
- **The black-page fallback shipped too** (`PanelBlank.svelte`), which the plan had left as a
  Phase 3 hardware contingency. It is the same overlay machinery, and it means the panel goes
  dark at bedtime whether or not `wlopm` turns out to work on this monitor.
- Two frontend modules are deliberately plain `.ts`, not `.svelte.ts`: `idle.ts` and
  `slideshow.ts` hold no reactive state, and a rune in a plain `.ts` file type-checks
  cleanly and then blanks the panel at runtime.
- Verified in real Chrome at both 1920x1080 and 1080x1920 via the headless panel harness —
  which is the only thing that catches a client-render failure, since the backend suite stays
  green while the display is blank.

---

## Landed after v1

Small changes that are neither a phase nor a future feature. Recorded here so the
running narrative stays honest about what the panel actually does.

### The week starts on Monday (2026-08-20)

`HOMEDASH_WEEK_STARTS_ON` had shipped with Phase 2 and already ran end to end; only the
default moved, from `sunday` to `monday`. `MonthGrid.svelte` takes its column headers from
the API response rather than hardcoding them, so the frontend needed no change at all.

### 3- and 5-day lookaheads (2026-08-20)

Two views between Day and Week: `next3` and `next5`, labelled **3 Day** and **5 Day**.

- **They are not snapped to a week.** That is the whole feature. `week` normalises its
  anchor to the week start; a lookahead keeps whatever date it is pointed at, which with
  no anchor is today. A snapped 3-day view opened on a Sunday would be two-thirds history.
- **They cost almost nothing** because every view was already the same response shape — a
  list of day buckets. `grid.LOOKAHEAD_DAYS` plus three branches in `period_bounds`,
  `step_anchor` and `normalize_anchor` is the entire backend.
- **`DayWeekView` now takes its column count from `days.length`** rather than a hardcoded
  7 with a `.single` special case for day view. One expression covers 1, 3, 5 and 7.
- **Only the wide views stack in portrait.** Week and 5 Day collapse to one day per row on
  a 1080px-wide panel; Day and 3 Day keep their columns, since three fit at ~350px each and
  the side-by-side comparison is what the view is for. A third breakpoint at 40rem stacks
  everything, for a phone.
- Paging steps a whole window, so `›` never re-shows a day just seen, and the existing
  midnight rollover already resets the anchor — which is what keeps a lookahead left on the
  wall meaning "from now".

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

### Members, and event editing

Both were dropped from Phase 2 rather than deferred for scheduling reasons — see that phase's
notes for why. They come back only if the shape of the household changes:

- **Members** would earn their place if one person needed several calendars grouped under a
  single name and color, or if avatars turned out to help kids find their own events faster
  than a legend does. `members` and `EventInstance.member_id` are already in the schema.
- **Event editing** would mean a write path, and with it the CalDAV/Google write scopes,
  a permission model, and the recurring-edit trap ("this occurrence" / "this and future" /
  "all"). Currently unnecessary: phones already edit these calendars well.
- **Multiple panels with independent filters** would revive the per-device work Phase 2
  removed. Filters would move from `localStorage` onto a `devices` row.

### Kid lock

App-level, distinct from the device lockdown in Phase 3. PIN gate on editing, settings, and member filter changes. Design the permission model in Phase 2 even if the UI lands here.

### Jellyfin → DLNA / HEOS music

Browse a Jellyfin library and push playback to HEOS speakers over DLNA. Likely needs `upnpclient` or similar for device discovery, plus HEOS's own telnet-style CLI protocol on port 1255 for the parts DLNA doesn't cover. Investigate whether HEOS's native protocol is a better target than generic DLNA.

### Chores and rewards

Repeating chore definitions, per-member assignment, completion tracking, and a points/rewards system for kids. Reuses the recurrence machinery from Phase 1 — the RRULE expander should work for chores as-is.

### Other candidates

- Photo source: Google Drive folder (Google Photos is not viable — the Library API scopes for reading a user's own albums were removed in March 2025)
- Photo source: iCloud shared album via the undocumented `sharedstreams` endpoint
- Touch-to-wake and PIR motion sensor
- Home Assistant integration

---

## Open decisions

1. **Which Pi.** *(Settled — a Pi 5, kept as a thin client.)* It could have hosted the
   container too, but keeping the split means the display can be replaced or duplicated
   without touching the server, and it keeps 24/7 SQLite writes off the panel's SD card.
2. **Panel size and resolution.** *(Settled — 15.6", 1920x1080, and it must work rotated, so
   1080x1920 too.)* The photo resize target in Phase 4 is therefore both, and portrait
   inverts that phase's orientation note: on a portrait panel it is *landscape* photos that
   need pairing or cropping.
3. **Auth on the web UI.** The wall panel has no login, but the phone-based admin UI probably needs one. Simplest workable answer: a single household password, LAN-only, no accounts.
4. **Repo layout.** Monorepo with `backend/` and `frontend/` is the obvious call given the single-image build. *(Settled — this is what's in the repo.)*

---

## Suggested first commit

Docker Compose with FastAPI serving a static "hello" page, SQLite mounted, `/healthz` returning 200. Get the deployment loop working before writing any features — every phase above assumes you can rebuild and redeploy in under a minute.
