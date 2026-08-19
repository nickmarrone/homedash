# HomeDash

An open-source, self-hosted wall-mounted family calendar in the spirit of Skylight and Hearth.

See `CLAUDE.md` for the full phased implementation plan.

## Status

**Phases 1 and 2 are done.** Real calendar sync from ICS, CalDAV, and Google Calendar;
Open-Meteo weather; agenda, day, week, and month views; live updates over SSE — all served
from one Docker image.

HomeDash is **read-only by design**. It never writes to your calendars. Edit events on your
phone the way you already do, and they sync down to the panel.

## Repo layout

```
backend/    FastAPI + SQLModel + Alembic + APScheduler (Python 3.12, uv)
frontend/   SvelteKit (adapter-static) - compiles to static files the backend serves
```

---

## Quick start

```bash
cp .env.example .env       # then edit it - see "Adding your calendars" below
docker compose up --build
```

Open <http://localhost:8000>. `/healthz` returns 200 when the backend is ready.

At minimum you need to set `HOMEDASH_HOME_TIMEZONE`, `HOMEDASH_CALENDARS`, and your
latitude/longitude. Everything else has a working default.

---

## Adding your calendars

`HOMEDASH_CALENDARS` is a **single-line JSON list**. Every entry needs a `name` and a `kind`;
what else it needs depends on the kind:

| kind | needs | how fast changes appear |
|---|---|---|
| `ics` (default) | `url` | **hours** — the provider regenerates the file on its own schedule |
| `caldav` | `url`, `credentials` | about a minute |
| `google` | `calendar_id`, `credentials` | about a minute |

Each calendar is automatically assigned a color from a fixed palette, in the order you list
them. Reordering the list recolors them.

### Step 1: find out what you have

If your calendars are currently ICS URLs and you don't know who actually hosts them, ask:

```bash
cd backend
uv run homedash-inspect-calendars          # add --probe to also test reachability
```

It reports, per calendar, which provider it recognises, whether a faster kind is available,
and exactly what credentials that would need. Run this before setting anything up.

### Step 2: ICS (the simplest, and the slowest)

Paste the secret iCal address from your provider:

```bash
HOMEDASH_CALENDARS=[{"name": "School", "url": "https://example.com/school.ics"}]
```

Nothing else is required. Be aware that Google in particular caches these feeds for hours —
this is not a bug in HomeDash, and polling more often cannot fix it. If a calendar needs to be
current, use one of the kinds below.

### Step 3: CalDAV — Apple iCloud, Fastmail, Nextcloud

Create an **app-specific password** with your provider (for iCloud, at
[appleid.apple.com](https://appleid.apple.com)), then:

```bash
HOMEDASH_CALENDARS=[{"name": "Nick", "kind": "caldav", "url": "https://caldav.fastmail.com/dav/calendars/user/nick/personal", "credentials": "fastmail"}]
HOMEDASH_CALENDAR_CREDENTIALS={"fastmail": {"username": "nick@fastmail.com", "password": "app-password-here"}}
```

The `credentials` field is a *name* pointing into `HOMEDASH_CALENDAR_CREDENTIALS`, so the
calendar list itself stays free of secrets and safe to paste into a bug report.

### Step 4: Google Calendar

Google needs OAuth2. This is a one-time setup, done once per Google account.

**In the Google Cloud console:**

1. Create a project at [console.cloud.google.com](https://console.cloud.google.com)
2. Enable the **Google Calendar API**
3. Configure the **OAuth consent screen**, and add your own Google account as a test user
4. Create **Credentials → OAuth client ID → Desktop app**, and note the client ID and secret

**Then, on a machine with a browser** (your laptop, not the Pi):

```bash
cd backend
uv run homedash-google-auth --client-id YOUR_ID --client-secret YOUR_SECRET
```

It opens a consent page, catches the redirect, and prints the credential blob to paste into
your `.env`. Find your `calendar_id` in Google Calendar under
*Settings → your calendar → Integrate calendar → Calendar ID*; for your main calendar it is
just your email address.

```bash
HOMEDASH_CALENDARS=[{"name": "Family", "kind": "google", "calendar_id": "abc123@group.calendar.google.com", "credentials": "google"}]
HOMEDASH_CALENDAR_CREDENTIALS={"google": {"client_id": "...", "client_secret": "...", "refresh_token": "..."}}
```

> **Publish your consent screen when you are done testing.** While it is in *Testing* mode,
> Google expires refresh tokens after **seven days** — the panel will work perfectly and then
> stop syncing a week later.

HomeDash only ever requests the `calendar.readonly` scope, so it cannot modify your calendars
even if it wanted to.

### JSON formatting rules

Both variables must be valid JSON on a **single line**: no trailing backslashes to wrap them
across lines, and no backslashes inside strings. Leave the value blank or use `[]` for
"no calendars". A malformed value fails at startup with a message pointing at the exact
offending character.

---

## Using the panel

**Switching views.** The switcher at the top offers Agenda, Day, Week, and Month. The panel
remembers which one you left it on and comes back to it after a reboot.

- **Agenda** — a flat chronological list of everything coming up. Easiest to read at a glance.
- **Day / Week** — one column per day, events listed inside it.
- **Month** — the full grid. Each cell shows up to three events, then a "+N more" count.

**Navigating.** `‹` and `›` move one day, week, or month; **Today** jumps back to now. Today's
cell is outlined in every view.

**Hiding a calendar.** Tap its chip in the legend. Its events disappear from *all* views and
the chip dims with an empty checkbox, so it can always be tapped back on. This is stored per
panel, in browser local storage, and survives reboots.

**Reading the colors.** Every event carries an accent bar in its calendar's color. All-day
events render as solid banners and sort above timed events within a day. An event spanning
several days appears on each of them.

**Updating.** The panel never needs a manual refresh. The backend pushes changes over SSE as
soon as a sync notices them.

---

## How fast changes appear

Two poll cadences, because they are limited by different things:

| | interval | why |
|---|---|---|
| ICS | `HOMEDASH_ICS_POLL_INTERVAL_MINUTES` (15) | The provider's own caching is the floor. Lowering this will not make ICS faster. |
| CalDAV / Google | `HOMEDASH_FAST_POLL_INTERVAL_MINUTES` (1) | These report changes as they happen. |

An unchanged calendar is cheap to poll: CalDAV uses an RFC 6578 sync-collection request and
Google a `syncToken`, so the common "nothing changed" answer costs one small request. A full
re-fetch only happens when something actually moved.

Every calendar is also fully re-expanded once a day regardless. The materialization window
rolls forward, so a calendar nobody ever edits still needs rebuilding or its far edge slowly
empties out.

---

## Weather

The header shows current conditions, today's high/low, sunrise/sunset, and AQI. Under it, a
strip covers the next 12 hours: temperature per hour, with rain probability as a bar under each
one and a percentage printed for hours at 20% or above. Everything comes from a single
Open-Meteo call, refreshed on the `HOMEDASH_WEATHER_CACHE_MINUTES` schedule and pushed to the
panel over SSE — the page never fetches weather on load.

Set `HOMEDASH_WEATHER_LATITUDE` / `HOMEDASH_WEATHER_LONGITUDE` for your home, and
`HOMEDASH_WEATHER_TEMPERATURE_UNIT` to `fahrenheit` or `celsius`. No API key is needed.

---

## Configuration reference

All configuration is via `HOMEDASH_*` environment variables read by
`backend/src/app/config.py` (or a `backend/.env` file). See `.env.example` for the annotated
list.

| variable | default | meaning |
|---|---|---|
| `HOMEDASH_HOME_TIMEZONE` | `UTC` | IANA zone the panel displays times in |
| `HOMEDASH_CALENDARS` | `[]` | the calendar list (see above) |
| `HOMEDASH_CALENDAR_CREDENTIALS` | `{}` | secrets, keyed by name |
| `HOMEDASH_WEEK_STARTS_ON` | `sunday` | first column of the week and month views |
| `HOMEDASH_ICS_POLL_INTERVAL_MINUTES` | `15` | ICS poll cadence |
| `HOMEDASH_FAST_POLL_INTERVAL_MINUTES` | `1` | CalDAV/Google poll cadence |
| `HOMEDASH_SYNC_WINDOW_PAST_DAYS` | `30` | how far back instances are materialized |
| `HOMEDASH_SYNC_WINDOW_FUTURE_DAYS` | `365` | how far forward |
| `HOMEDASH_WEATHER_LATITUDE` / `_LONGITUDE` | `0` | home coordinates |
| `HOMEDASH_WEATHER_TEMPERATURE_UNIT` | `fahrenheit` | `fahrenheit` or `celsius` |
| `HOMEDASH_WEATHER_CACHE_MINUTES` | `20` | weather refresh cadence |

`HOMEDASH_ICS_CALENDARS` still works as a deprecated alias for `HOMEDASH_CALENDARS`; it logs a
warning at startup and its entries default to `kind: "ics"`.

### Changing the calendar list

The environment variable is the source of truth and is reconciled on every startup. Entries are
matched by URL (or calendar address, for Google), so **renaming or reordering a calendar keeps
its events**, while removing an entry deletes that calendar and its events. Changing a
calendar's `kind` is treated as a replacement, not a rename — its old events are removed with it.

---

## Troubleshooting

**A calendar shows nothing.**

```bash
cd backend
uv run homedash-inspect-calendars --state --probe
```

`--state` prints what each calendar has actually stored — event and instance counts, when it
last synced and last fully re-expanded, and the resume token it is holding — and flags the two
states that explain most empty calendars: every instance being in the past, and a source
holding a resume token while storing no events, which makes change detection keep answering
"nothing changed". `--probe` additionally fetches each ICS feed to confirm it is reachable.

**Changes take hours to appear.** That calendar is on `ics`. Run
`uv run homedash-inspect-calendars` to see whether a faster kind is available for its provider.

**Google stopped syncing after about a week.** The OAuth consent screen is still in *Testing*
mode, which expires refresh tokens after seven days. Publish it, then re-run
`homedash-google-auth`.

**`invalid_grant` from Google.** The refresh token was revoked, the account password changed,
or the seven-day trap above. Re-run `homedash-google-auth`.

**Times are wrong by a fixed number of hours.** Check `HOMEDASH_HOME_TIMEZONE`. The panel's own
OS clock is deliberately not used anywhere, so the host's timezone should make no difference.

**An all-day event shows on the wrong day.** This was a real bug fixed in Phase 2; make sure
you are not running an older image.

---

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

### Tests

```bash
cd backend && uv run pytest      # backend suite
cd frontend && npm run check     # svelte-check
```

### Migrations

Schema changes go through Alembic:

```bash
cd backend
uv run alembic revision --autogenerate -m "describe the change"
uv run alembic upgrade head
```

The app also runs `alembic upgrade head` automatically on startup.
