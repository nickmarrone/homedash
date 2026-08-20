# HomeDash

An open-source, self-hosted wall-mounted family calendar in the spirit of Skylight and Hearth.

See `CLAUDE.md` for the full phased implementation plan.

## Status

**Phases 1 to 4 are done.** Real calendar sync from ICS, CalDAV, and Google Calendar;
Open-Meteo weather; agenda, day, week, and month views; live updates over SSE — all served
from one Docker image — plus a locked-down Raspberry Pi wall panel with a screen schedule,
and a family-photo screensaver when nobody is using it.

HomeDash is **read-only by design**. It never writes to your calendars. Edit events on your
phone the way you already do, and they sync down to the panel.

## Repo layout

```
backend/    FastAPI + SQLModel + Alembic + APScheduler (Python 3.12, uv)
frontend/   SvelteKit (adapter-static) - compiles to static files the backend serves
deploy/pi/  Provisioning for the Raspberry Pi that hangs on the wall
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

**Either way up.** Mounted in portrait, the panel stacks the agenda underneath the calendar
and gives the week view one column per day instead of seven side by side. This follows the
physical rotation with no setting to change.

**Updating.** The panel never needs a manual refresh. The backend pushes changes over SSE as
soon as a sync notices them. A heartbeat every 30 seconds carries the server's date, so the
display rolls over at midnight even on a day when nothing else changes, and the page reloads
itself if that heartbeat stops — a silently frozen display is the number one kiosk failure
mode.

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

Every calendar is also fully re-fetched and re-expanded every
`HOMEDASH_FULL_RESYNC_INTERVAL_MINUTES` (60) regardless of what change detection says. That
does two jobs. The materialization window rolls forward, so a calendar nobody ever edits still
needs rebuilding or its far edge slowly empties out. And it is the only thing that corrects a
provider signal that missed a change — which is the bound on how long a deleted event can
survive on the wall if its calendar never reports the deletion.

A rebuild materializes what the calendar currently holds, so anything the source stops
serving disappears with it. The exception is an event the source keeps serving while
marking it `STATUS:CANCELLED` — how CalDAV servers and ICS exports report a meeting the
organizer called off, including a single occurrence dropped out of a series. Those are
filtered out at materialization time, since a rebuild would otherwise re-create them
faithfully every hour, forever.

---

## Weather

The header shows current conditions, today's high/low, sunrise/sunset, and AQI. Under it, a
strip covers the next 12 hours: temperature per hour, with rain probability as a bar under each
one and a percentage printed for hours at 20% or above. Everything comes from a single
Open-Meteo call, refreshed on the `HOMEDASH_WEATHER_CACHE_MINUTES` schedule and pushed to the
panel over SSE — the page never fetches weather on load.

Set `HOMEDASH_WEATHER_LATITUDE` / `HOMEDASH_WEATHER_LONGITUDE` for your home, and
`HOMEDASH_WEATHER_TEMPERATURE_UNIT` to `fahrenheit` or `celsius`. No API key is needed.

## The sky

Beside the forecast the header shows the **current moon phase**, drawn to its real illuminated
fraction rather than rounded to one of eight icons. Above the hourly strip is a line of
**upcoming sky events** for the next three weeks: new and full moons, meteor shower peaks, and
the next equinox or solstice. Anything happening tonight is picked out in bold.

All of it is computed from your configured latitude and longitude — there is no second API to
set up and nothing else to go down, which is also why the moon is still there when Open-Meteo
is not.

**Your coordinates do real work.** For each shower the panel walks that night at your location,
finds the darkest moment when the radiant is highest, and reports it:

```
Geminids       Dec 14   ~150/hr, best 2am, radiant 85° up
Ursids         Dec 22   ~10/hr, best 6:15am, radiant 49° up, bright moon
```

A shower whose radiant never climbs into a dark sky where you live is not listed at all — the
Perseids are simply absent in Sydney, and absent in Reykjavík in August because the sky never
gets dark. The Geminids show at 85° from California and 24° from Sydney, and say so. The
Draconids come out as an evening shower while everything else peaks before dawn, because that
is what their radiant actually does. A bright moon is flagged only when it is *above the
horizon* at the best moment, which is the difference between a good night out and a wasted one.
Seasons are named for your hemisphere and the moon is drawn lit on the correct side.

### Comets

A bright comet is the one thing here that cannot be computed from first principles. Meteor
showers are annual clockwork and the Moon keeps its own schedule, but a naked-eye comet is a
*discovery* — NEOWISE in 2020, Tsuchinshan-ATLAS in 2024. Neither existed in any table until it
did. So this is the only part of HomeDash that reaches the network: it fetches orbital elements
from the [Minor Planet Center](https://www.minorplanetcenter.net/) once a day, propagates each
orbit, and lists anything predicted brighter than `HOMEDASH_COMET_MAGNITUDE_LIMIT` (6.0) that
actually climbs into your dark sky:

```
C/2026 A1 (Testbright)  Tonight  mag 3.3, best 5:15am, 13° up
```

A comet takes the front of the strip regardless of date, because everything else there is a
diary entry and a comet is a thing in the sky tonight that may be gone next month.

**The magnitudes are the weak part, and deliberately so.** A comet's position is celestial
mechanics; its brightness depends on how much ice is left and how it behaves near the Sun, and
comets routinely miss their forecasts by magnitudes in both directions. The default cut-off is
conservative for that reason — treat a listing as "worth a look", never a promise.

Elements are cached on disk beside the database, so a failed refresh falls back to the last good
copy rather than emptying the strip, and a response that parses to no orbits is refused rather
than written over a good file. Set `HOMEDASH_COMETS_ENABLED=false` to keep the panel entirely
self-contained and offline.

### The showers

The thirteen showers are the IMO visual working list — the ones a person can actually watch.
The IAU catalogue holds hundreds more, but nearly all were found by radar or camera networks
and produce a meteor an hour or less: real, and invisible.

Accuracy is a couple of minutes for moon phases, under an hour for solstices, and a fraction of
a degree for positions — all far tighter than a date and a rounded altitude need. See
`backend/src/app/astro.py` for the algorithms and their sources.

---

## The photo screensaver

Leave the panel alone for a few minutes and it drifts into a slideshow of your own photos.
Touch it anywhere and the calendar comes straight back.

Point `HOMEDASH_PHOTOS_SOURCE` at a folder of photos and that is the whole setup. Compose
mounts it **read-only** at `/photos`, so HomeDash can never delete a family photo. Fill it
however you like — Syncthing, an SMB share, Nextcloud, or dragging files in once a quarter.
Subfolders are searched too.

There is no on/off switch. An empty or missing folder means the panel never drifts, and the
screensaver never starts while `HOMEDASH_SCREEN_SCHEDULE` says the screen should be off.

**How it handles orientation.** The panel is 1920x1080 and can be mounted either way up, so
a photo either agrees with the way it is turned or it does not. Ones that agree fill the
screen. Ones that do not are shown two at a time, side by side in landscape or stacked in
portrait, so the screen is always full — a black bar down the side of a wall panel reads as
a fault. Note that on a portrait panel it is the *landscape* photos that get paired.

Photos are resized on the server, once, to exactly the size the panel will show them at. The
Pi never decodes an original. JPEG, PNG, GIF, BMP, WebP and TIFF are indexed; HEIC is not,
because decoding it needs an extra library. Anything Pillow cannot read is logged, skipped,
and remembered so it is not retried on every scan. EXIF rotation is honoured, so photos
straight off a phone come out upright.

**How fast a new photo appears.** A filesystem watch normally picks one up within seconds.
That watch sees nothing when the folder is filled from *another machine* over SMB or NFS —
no local filesystem event is ever raised — so a full rescan also runs every
`HOMEDASH_PHOTO_INDEX_INTERVAL_MINUTES`. If your photos arrive over a network share, that
interval is the speed you will actually see.

The resized copies live in their own Docker volume, separate from the database, because they
are regenerable and a backup should not have to carry them. To force a clean re-render:

```bash
docker compose down
docker volume rm homedash_homedash-photos
docker compose up -d
```

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
| `HOMEDASH_WEEK_STARTS_ON` | `monday` | first column of the week and month views: `monday` or `sunday` |
| `HOMEDASH_ICS_POLL_INTERVAL_MINUTES` | `15` | ICS poll cadence |
| `HOMEDASH_FAST_POLL_INTERVAL_MINUTES` | `1` | CalDAV/Google poll cadence |
| `HOMEDASH_FULL_RESYNC_INTERVAL_MINUTES` | `60` | forced full re-fetch, whatever change detection says |
| `HOMEDASH_SYNC_WINDOW_PAST_DAYS` | `30` | how far back instances are materialized |
| `HOMEDASH_SYNC_WINDOW_FUTURE_DAYS` | `365` | how far forward |
| `HOMEDASH_WEATHER_LATITUDE` / `_LONGITUDE` | `0` | home coordinates |
| `HOMEDASH_WEATHER_TEMPERATURE_UNIT` | `fahrenheit` | `fahrenheit` or `celsius` |
| `HOMEDASH_WEATHER_CACHE_MINUTES` | `20` | weather refresh cadence |
| `HOMEDASH_COMETS_ENABLED` | `true` | fetch comet orbits from the Minor Planet Center |
| `HOMEDASH_COMET_REFRESH_HOURS` | `24` | how often to re-fetch those orbits |
| `HOMEDASH_COMET_MAGNITUDE_LIMIT` | `6.0` | faintest comet worth listing |
| `HOMEDASH_SCREEN_SCHEDULE` | `{"on": "06:30", "off": "21:30"}` | when the wall panel's screen is lit |
| `HOMEDASH_DEVICE_NAME` | `panel` | name stored on the panel's `devices` row |
| `HOMEDASH_PHOTOS_SOURCE` | `./photos` | host folder of photos, mounted read-only at `/photos` |
| `HOMEDASH_PHOTOS_DIR` | `/photos` | the same folder as seen inside the container |
| `HOMEDASH_PHOTO_CACHE_DIR` | `/app/backend/photo-cache` | where resized copies are cached |
| `HOMEDASH_PHOTO_INDEX_INTERVAL_MINUTES` | `15` | full rescan cadence (the backstop for network shares) |
| `HOMEDASH_PHOTO_MAX_COUNT` | `2000` | ceiling on photos handed to the panel |
| `HOMEDASH_SCREENSAVER_IDLE_MINUTES` | `5` | untouched time before the slideshow starts |
| `HOMEDASH_SCREENSAVER_DWELL_SECONDS` | `30` | how long each slide is held |

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

## Putting it on the wall

The Pi is a thin client: HomeDash keeps running wherever your containers run, and the Pi
runs only a browser and a small screen agent. Everything it needs is in
[`deploy/pi/`](deploy/pi/README.md):

```bash
# on a Raspberry Pi running Raspberry Pi OS Lite (64-bit)
git clone https://github.com/nickmarrone/homedash.git
cd homedash
sudo deploy/pi/setup.sh --server http://homedash.local:8000 --orientation portrait
sudo systemctl start homedash-kiosk homedash-screen
```

That gives you console autologin into labwc, Chromium in kiosk mode with an enterprise
policy that blocks every URL but HomeDash, and a systemd-managed agent applying
`HOMEDASH_SCREEN_SCHEDULE`.

**One step is left to you deliberately.** The screen agent starts in `--dry-run`, because on
a Pi 5 there is no way to know in advance how a given monitor blanks: `vcgencmd
display_power` was removed with the move to Wayland, `/sys/class/backlight` exists only for
the official DSI panel, and portable USB-C monitors disagree about whether they honour the
compositor's power management at all. Run `homedash-screen-agent probe` on the Pi, see which
mechanism actually darkens your display, and set it. The full walkthrough, including the
touch-rotation check and the read-only filesystem, is in
[`deploy/pi/README.md`](deploy/pi/README.md).

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
