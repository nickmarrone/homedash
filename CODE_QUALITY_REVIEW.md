# HomeDash — Code Quality Review

*Reviewed 2026-08-24, at commit `c0e5527`. Backend and frontend, by two independent
reading passes plus verification of every finding below.*

This is a health check, not a bug report — nothing was known to be broken when it started.
It exists because the codebase has grown fast through four phases plus the music feature,
and is about to take on the Immich photo source.

---

## Verdict

**Well above average, and unusually well documented.** The layering is real rather than
aspirational, the comments overwhelmingly explain *why* rather than restating *what*, and
the backend test suite is genuinely adversarial — the HEOS queue concurrency tests and the
DST/all-day grid tests are the kind most projects never write.

The problems cluster in three places:

1. **`api/routes.py` has become a 749-line god-module**, where the music feature left
   HTTP-proxy work that belongs in `music/`.
2. **Four real bugs** — in sync, the agenda query, startup, and the scheduler. All four
   were reproduced, not merely suspected.
3. **The frontend has no error handling on its data path at all**, which combines with an
   SSE reconnect rule to produce a plausible permanently-blank panel.

### Baseline

| | Lines | Notes |
|---|---|---|
| `backend/src/app/` | 6,692 | 41 modules |
| `backend/tests/` | 5,852 | 25 files — **529 tests, all pass in 6.2s** |
| `frontend/src/` | 4,949 | 21 components + 12 lib modules |
| frontend tests | **0** | `svelte-check` is the only gate: 194 files, 0 errors, 0 warnings |
| `deploy/pi/` | 759 | `screen_agent.py` (339) + `setup.sh` (420), untested |

Largest files: `api/routes.py` (749), `astro.py` (614), `+page.svelte` (589),
`MusicBrowser.svelte` (529), `comets.py` (415), `api.ts` (404).

---

## What is already good

Listed first, and specifically, because a review that only lists problems misrepresents the
codebase — and because several of these are decisions that should be actively defended
against future "cleanup".

- **`ARCHITECTURE.md` (897 lines) is current and honest.** It covers `astro.py`/`comets.py`,
  the Playwright harness, and the theme tokens, and at line 846 it documents "no CI, no
  Makefile, and no linter or formatter configured" as a deliberate call rather than
  pretending otherwise. A map that admits its own gaps is worth more than one that does not.
- **The protocol seams are real, not decorative.** `calendars/base.py:6` stops at VEVENTs so
  all three adapters flow through one expansion. `photos/base.py:33` returns `SourcePhoto` so
  the indexer never learns what a folder is. `music/queue.py:66-67` takes `play_url`/`url_for`
  as injected callables — which is precisely why a concurrency state machine can be tested
  with no HEOS and no Jellyfin present.
- **`calendars/localtime.py` is the best file in the repo.** Forty-three lines, and the module
  docstring names both timezone traps — naive `.astimezone()` reading the host clock, and
  all-day placeholders converting backwards in negative-offset zones — before a single line
  of code.
- **Date arithmetic is out of the handlers.** `grid.py` and `devices.py` own it, and every
  function takes tz / week-start / today explicitly, which is what makes them testable.
- **The dependency direction is enforced and stated.** `comets.py` imports `astro.py`, never
  the reverse. `heos.py` and `jellyfin.py` never import each other.
- **Zero `any` in the frontend**, API types defined once in `api.ts` and shared, and
  `tsc --noUnusedLocals --noUnusedParameters` exits clean.
- **Every timer, listener and `EventSource` is torn down.** For a display that runs for months
  this is the thing that matters most, and it is right in all five places.
- **`DayWeekView.svelte:15`** derives its column count from `days.length` rather than the view
  name, collapsing day / 3-day / 5-day / week into one component with one set of styles. The
  best structural call in the frontend. **Do not undo this.**
- **The "Kitchen paper" migration actually completed** — no `rgba(128,…)`, no
  `color-scheme: light dark`, no system colours survive in live CSS. They appear only in
  `theme.css` comments explaining the history.
- **Repo hygiene is clean** — nothing tracked that shouldn't be, and `.gitignore` explains
  why `/photos/` is anchored to the root.

---

## Tier 1 — Real bugs

All four were reproduced or traced to a concrete failure, not inferred from a reading.

### B1 — A moved recurring occurrence corrupts the `events` table

**`backend/src/app/calendars/sync.py:326-341`**

Every VEVENT in the feed gets an `Event` row, and then `events_by_uid[uid] = event`
overwrites. A recurring series and its `RECURRENCE-ID` overrides all share one UID, so the
dict keeps only the **last** one — the override — and every instance in the series is
attached to it.

Reproduced by driving `sync_source` with a weekly series carrying one moved (non-cancelled)
occurrence:

```
Event rows for one UID: 2
  id=1 uid=soccer has_RRULE=True
  id=2 uid=soccer has_RRULE=False
Instances:
  event_id=2 'soccer'       2026-08-26
  event_id=2 'soccer moved' 2026-09-03
  event_id=2 'soccer'       2026-09-09

instances point at: {2}    row holding the RRULE: {1}    RRULE row orphaned: True
```

**Why it matters.** A parent moves this week's soccer practice to Thursday. From then on:

- The series' `raw_vevent` on record is the *override*, which has no RRULE. The stated
  purpose of storing `raw_vevent` (`models.py:63`, and the plan's "re-expand the window
  without re-fetching") is silently defeated for that series.
- `homedash-inspect-calendars --find soccer` prints `recurring=False`
  (`inspect_calendars.py:304`) — the diagnostic tool built specifically for hunting stale
  appointments now lies about the event being hunted.
- Row 1 holds the RRULE, has zero instances, and is never referenced again. `--state` event
  counts inflate by one per override.

The panel renders correctly, because the expansion at `sync.py:322` happens before any of
this. That is why it went unnoticed.

**Fix.** Skip components carrying a `RECURRENCE-ID` when building `events_by_uid`, so the
master owns the row. Needs a regression test: no existing test moves an occurrence without
also cancelling it.

### B2 — `/api/agenda` drops events already in progress

**`backend/src/app/api/routes.py:82-84`**

The agenda filters `starts_at >= today_start` — a cutoff. `_instances_overlapping`
(`routes.py:180-189`), serving the day/week/month grid, uses a proper overlap. And its own
comment at `routes.py:161-164` explains why the cutoff form is wrong:

> The predicate is an overlap, not a start-time cutoff: an event already in progress when the
> period opens still belongs in every day it spans, and a `starts_at >=` filter would drop it
> from the view entirely.

**Why it matters.** A family adds an all-day "Vacation" for Aug 20–27. On the 20th it shows
in the agenda. On the 21st it **disappears from the agenda** — while still showing correctly
on the week and month grids. The same applies to any multi-hour timed event that started
before local midnight.

The agenda is a first-class user-selectable view and is *always* rendered in portrait
(`+page.svelte:257`), so this is the view the panel shows most.

**Fix.** Extract one `instances_touching(session, first, last, tz)` into the calendars
package and have both endpoints call it. This fixes the bug *and* removes the duplication
that caused it — see DRY #1 below.

### B3 — A bad weather response can stop the app from starting

**`backend/src/app/weather/client.py:70` and `backend/src/app/main.py:31`**

`refresh_weather` catches only `httpx.HTTPError`. `forecast_resp.json()` raises
`json.JSONDecodeError`, which is a `ValueError` — not caught. And `main.py:31` awaits
`refresh_weather` in the lifespan with no guard of its own.

**Why it matters.** A captive portal, a transparent proxy, or an Open-Meteo maintenance page
returns HTTP 200 with an HTML body. `.json()` raises, the exception propagates out of the
lifespan, and **the container fails to start** — no calendar, no photos, no screen schedule
— over a weather widget.

The scheduled path is already safe (`scheduler.py:163` catches everything), as are
`run_photo_index` and `run_comet_refresh`. Only the startup path is exposed.

**Fix.** One `try/except Exception` around the startup call. Smallest fix in this document,
worst consequence.

### B4 — Naive `datetime.now()` on a UTC scheduler

**`backend/src/app/scheduler.py:20` and `:173, 180, 187, 195, 202, 209`**

The scheduler is built with `BackgroundScheduler(timezone="UTC")`, and all six `add_job`
calls pass `next_run_time=datetime.now()` — a **naive local** datetime. APScheduler localizes
a naive value *into* the scheduler's timezone, so local wall-clock time is reinterpreted as
UTC.

**Why it matters.** Invisible today: neither the `Dockerfile` nor `docker-compose.yml` sets
`TZ`, so container-local time *is* UTC. The moment anyone adds `TZ: Europe/Berlin` — a
routine thing to do for readable logs — every boot job is scheduled 1–2 hours in the future.
After a restart the panel shows an empty calendar, no heartbeat and no weather for two hours,
and the reason is nowhere near the symptom.

**Fix.** `datetime.now(timezone.utc)`, six times.

### B5 — `connected` means "has ever connected"

**`backend/src/app/music/heos.py:135-136`, `routes.py:402, 509`**

`connected` is `self._heos is not None`, and `_heos` is never cleared after a successful
connect. `post_music_transport` catches only `KeyError`.

**Why it matters.** A speaker drops off wifi mid-evening. pyheos retries in the background,
but `GET /api/music/players` answers `connected: true`, so the panel keeps showing the music
UI as healthy. A finger on pause raises a pyheos `CommandError`, which is not a `KeyError`,
so it escapes as an unhandled 500 with a stack trace. The design note at `routes.py:441-443`
says reads and writes are meant to degrade differently and deliberately; this is the case
where the write path degrades badly.

### B6 — The Jellyfin proxies report our own bugs as 502

**`routes.py:612` and `routes.py:718`**

Both do `except Exception: raise HTTPException(502, "could not reach Jellyfin")`. Twenty-six
lines earlier, `routes.py:586-588` states the rule:

> 502, not 500: the failure is upstream, and saying so is the difference between "check
> Jellyfin" and "check HomeDash" for whoever is reading the log.

A `TypeError` introduced in `library.art_url()` would make every cover image report "could
not reach Jellyfin", and send somebody to spend an evening restarting a healthy server.
Should be `except httpx.HTTPError`.

### B7 — "Next" on the last track leaves the speaker playing

**`backend/src/app/music/queue.py:175-177`**

When `remaining == 0`, `next()` clears the queue and returns `True`. The route
(`routes.py:494-499`) sees `handled=True` and returns without touching the controller.
Nothing tells the speaker to stop.

Last track of an album, someone taps skip: the panel's queue display vanishes and now-playing
reverts to the speaker's own bitrate-and-codec metadata — while the track keeps playing to
the end. Compare `on_state` (`queue.py:228-230`), which hits the same branch correctly,
because there the track really has ended.

### B8 — `assert` used for invariants that ship

`service.py:140` and `heos.py:141` are stripped under `python -O`, turning a clear failure
into an `AttributeError` on `None`. Not currently run with `-O`; noted as a standing trap.

---

## Tier 2 — The blank-panel path

These are frontend findings, grouped because they compose into one failure the wall panel
hides very well.

### F1 — Cold start with the backend down can leave the panel permanently empty

**`frontend/src/routes/+page.svelte`**

There is exactly **one** `catch` in the file — line 194, inside `runMusicCommand`. The six
data loaders (`loadAgenda`, `loadGrid`, `loadCalendars`, `loadWeather`, `loadMusic`,
`loadPhotos`) all `await` a fetcher that throws on `!response.ok`, and all are called as bare
statements.

The Pi boots faster than the Docker container. So:

1. All five `onMount` fetches reject (`+page.svelte:300-304`). `grid` stays `null`, so the
   `{:else if grid}` at `:369` is false and **the entire calendar body is absent**.
2. `loadPhotos()` rejects *before* reaching `restartIdleTimer(next.idle_minutes)` at `:231`
   — so the idle timer is never created at all, and **the screensaver can never appear**.
3. The backend comes up. `EventSource` connects — but this is its **first** successful open,
   so `hasConnected` is false at `api.ts:399`, `onReconnect` is not called, and
   `reloadEverything` never runs.
4. Heartbeats now arrive, so `watchdog.notify()` fires every 30s and **the watchdog never
   triggers a reload** — from its point of view the stream is perfectly healthy.

The result is a wall panel showing today's date, a header rule, and nothing else,
indefinitely — until an `events.updated` happens to fire or midnight rolls over.

The comment at `api.ts:374-375` ("The first `open` is the initial connection, not a
reconnection") is correct in isolation, but assumes the initial fetches already succeeded.

**Fix.** Give the loaders `.catch` handlers that record the failure and retry.

### F2 — `EventSource` has no `error` handler

**`frontend/src/lib/api.ts:372-404`** — no `onerror`, no `error` listener.

`EventSource` distinguishes two failure modes. A *network* error triggers automatic retry.
But a **non-2xx status or a wrong `Content-Type` is fatal per spec**: the browser fires
`error`, sets `readyState = CLOSED`, and never retries.

A reverse proxy returning 502 for `/api/events/stream` during a redeploy kills the stream
permanently and silently. Recovery then depends entirely on the watchdog noticing 100 seconds
of quiet and blunt-reloading — which does work, but it is the *only* defence, it produces no
log line, and it cannot be told apart from a genuine outage.

### F3 — Volume drag hammers the speaker

**`MusicOverlay.svelte:131` → `+page.svelte:211-214` → `:191-204`**

`oninput` on a range input fires on every value change. Dragging the slider from 20 to 70 is
roughly 50 events, so ~50 `POST /api/music/players/N/volume` **plus ~50
`GET /api/music/players`** in under a second — from a Raspberry Pi over wifi, each POST
forwarded to a HEOS speaker over its own TCP control connection. No debounce, throttle, or
frame coalescing anywhere in the path.

Related: the slider **visibly snaps backwards on release** (`MusicOverlay.svelte:58-64`),
because `dragging` clears before the refetch lands — reintroducing at the end of the gesture
the exact snap the mechanism exists to prevent during it.

### F4 — Smaller correctness issues

- **`WeatherWidget.svelte:21`** renders a missing temperature as `Math.round(… ?? 0)` — a
  believable **`0°F`** in the largest type on the panel. `Almanac.svelte:19-25` handles the
  same case correctly by returning `null`.
- **`HourlyForecast.svelte:24-25`** collapses `findIndex`'s `-1` into `0`. When the weather
  job has been failing and every cached hour is in the past, the strip renders from the start
  of the stale window with the first column labelled **"Now"**.
- **`+page.svelte:231`** restarts the idle timer on *every* reload, resetting
  `lastActivityAt`. Marginal wifi dropping SSE every ~4 minutes against a 5-minute idle
  threshold means the screensaver never appears, with no error anywhere.
- **`+page.svelte:91-93`** — the panel wakes from bedtime **into the screensaver**, because
  `idle` went true overnight behind `PanelBlank`. The first thing on the wall at 7am is the
  slideshow, and somebody has to tap it to see the day.

### Also worth knowing

- **`Screensaver.svelte:117-124`** is a `role="button"` with only `onpointerdown` and
  `tabindex="-1"` — assistive tech is told there is a button that cannot be reached or
  activated. Keyboard dismissal happens to work, but by an unrelated route (`idle.ts:10`
  listens for `keydown` on `window`). Either drop the ARIA or make it operable.
- **Two button groups have no `:focus-visible`** (`+page.svelte:531-554`,
  `MusicOverlay:174-196`) where the other eight do — see S3, which is why.

---

## Tier 3 — Structure

### S1 — `api/routes.py` is a god-module

749 lines, 15 endpoints, six domains. The calendar and photo halves are exemplary: thin,
delegating, with the arithmetic in `grid.py` and the Pillow work in `derivatives.py`. The
music half is not.

- **`get_music_stream` (`:683-749`) is 67 lines of HTTP proxy** — it builds an
  `httpx.AsyncClient`, does `build_request` / `send(stream=True)`, forwards `Range`, filters
  passthrough headers, and defines a generator that closes the client in a `finally`. None of
  that is routing.

  The smoking gun is **`jellyfin.py:5`**, whose module docstring says *"the audio itself is
  opened as a stream rather than buffered — see `stream()`"*. **There is no `stream()` method
  on `JellyfinLibrary`.** The proxy was meant to live there and ended up in the route.
- **`get_music_art` (`:594-628`)** does the same on a smaller scale, including
  `import httpx` **inside the function body** (`:607`, and again at `:703`) despite httpx
  being a hard dependency imported at module scope in five other files.
- **`_now_playing_from_track` (`:409-433`)** is pure wire-shape mapping — the job
  `api/serializers.py` exists to do.
- **Three POST routes hand-roll validation** from `body: dict = Body(default={})` (`:471`,
  `:515`, `:632`), reimplementing with `isinstance` checks what a Pydantic request model
  would give for free, including real 422s.

`_instances_overlapping` (`:158-196`) is persistence logic in the route module. Defensible —
there is no repository layer anywhere — but it is the direct cause of B2.

### S2 — `+page.svelte` is a 589-line god component

It owns six unrelated state domains at once: calendar (`:46-55`), clock (`:61-62`), music
(`:67-70`), photos/idle (`:72-77`), screen schedule (`:76`), weather (`:48`).

The music slice alone is ~70 lines that never touch the calendar — state at `:67-70`,
derivations at `:98-109`, six command wrappers at `:180-224`. The codebase already has the
right pattern for extracting it: `createOrientation` in `orientation.svelte.ts` is a factory
returning a getter object, exactly what the ARCHITECTURE "Idioms" section prescribes.

Note also one fragile ordering dependency worth a comment: `createOrientation` is called at
`:83` during component initialization, so its internal `onMount` registers *before* the
page's own. That ordering is what makes `orientation.isPortrait` correct when `loadPhotos()`
reads it at `:227`. Move that call and the panel silently fetches the wrong photo orientation
on boot.

### S3 — The design layer stopped halfway

`theme.css:9-11` states the rule — colours, typefaces, radii and tap targets live here — and
`.caps` was extracted with an explicit argument that "six private copies of it would drift,
which is the thing a design layer is for". Then the extraction stopped:

| Duplicated | Copies | Locations |
|---|---|---|
| `.passed` finished-event treatment | 3 | `AgendaList:124-139`, `DayWeekView:200-215`, `MonthGrid:182-189` |
| The caps run at 0.75rem | 3 | `DayWeekView:118-124`, `:145-152`, `MonthGrid:78-85` |
| Underlined-tab button | 3 | `ViewSwitcher:44-70`, `MusicOverlay:174-192`, `PlayerPicker:42-60` |
| Outlined round control | 5 | `PeriodNav:71-84`, `TransportControls:74-89`, `MusicOverlay:209-225`, `MusicBrowser:323-334`, `+page.svelte:531-545` |

They are **already drifting**: `MonthGrid` uses `--chip-color` where the others use
`--item-color`; `PlayerPicker:44` has `0 0.7rem` padding where the other two tabs have
`0 0.9rem`. Two components carry a comment saying "like every other round control on the
panel", which is the tell.

**It has already caused a defect.** Eight button groups define
`:focus-visible { outline: 2px solid var(--ink) }`; two do not
(`+page.svelte:531-554`, `MusicOverlay:174-196`). A shared class makes that impossible to get
wrong.

---

## Tier 4 — Dead code, DRY, coverage

### Dead code

Every item verified unreferenced across `backend/`, `frontend/src/` and `deploy/`.

| Item | Location | Evidence |
|---|---|---|
| `Settings.credentials_for()` | `config.py:302-313` | Unreachable from production, which duplicates it at `sync.py:220`. **Dead code with three passing tests.** |
| `localtime.local_date()` | `localtime.py:41-43` | Zero references |
| `serialize_instance(**extra)` | `serializers.py:18,36` | No caller passes it; `build_days` merges itself at `grid.py:200-203` |
| `events.etag` column | `models.py:64` | Never written, never read |
| `start_is_date` | `google_source.py:250` | Assigned, never used |
| `QueueManager.has()` | `queue.py:95-96` | Tests only |
| `lib/index.ts` | frontend | Unmodified SvelteKit stub; nothing imports `$lib` directly |
| `daily_units` / `hourly_units` | `api.ts:130-131` | Declared, never read |
| `buildSlides` export | `slideshow.ts:44` | Exported for a test that was never written |

**Write-only** (populated, nothing reads them): `Photo.width`, `Photo.height`,
`Photo.added_at`, `Event.updated_at`.

**Intentionally unused — verified, leave them.** The `members` table and
`calendar_sources.member_id`; `EventInstance.member_id` (written at `sync.py:357`, always
`NULL`, never read); the `settings` table. All documented as deliberate, and the stated
reason for keeping the FK — dropping it forces a SQLite table rebuild of a table
`events.source_id` references — is correct.

**Checked and cleared** (not dead, despite appearances): `HOMEDASH_PHOTOS_SOURCE` is used by
`docker-compose.yml:11`; `heos_probe.MAX_URL_LENGTH` duplicates `tokens.MAX_URL_LENGTH`
deliberately, so the probe works when the app is misconfigured; `HOMEDASH_ICS_CALENDARS` is a
live, tested, documented deprecation alias.

### Stale documentation

- **`jellyfin.py:5`** promises a `stream()` method that does not exist (see S1).
- `ARCHITECTURE.md` calls `screen_agent.py` "~200-line"; `CLAUDE.md` says "~50-line". It is
  339.

### DRY — worth extracting

1. **The overlap predicate — this duplication *is* B2.** `routes.py:64-85` and `:171-194`
   both implement "which instances touch this local window", both with the all-day floating
   anchor rule, both with a paragraph of comment explaining it — and they disagree.
2. **`_clock()` is byte-identical** at `astro.py:528-532` and `comets.py:412-415`.
   `comets.py` already imports five names from `astro.py`; this should be the sixth.
3. **`sync._credentials` vs `Settings.credentials_for`** — same lookup, near-identical error
   text. Since the latter is dead, this is a delete rather than a refactor.
4. **`comets.DEFAULT_MAGNITUDE_LIMIT = 6.0`** duplicates `Settings.comet_magnitude_limit`,
   each with its own paragraph of justification and nothing keeping them in sync.
5. **Duration formatting, frontend, outside `format.ts`, with divergent rounding.**
   `NowPlaying.svelte:28-34` uses `Math.floor` and guards `< 0`; `MusicBrowser.svelte:188-192`
   uses `Math.round` and guards `<= 0`. A 90,600 ms track shows `1:30` in one place and
   `1:31` in the other. `format.ts` is the documented home for this and neither uses it.
6. **The `failedArt` broken-image guard** is byte-identical at `NowPlayingBar.svelte:24-27`
   and `NowPlaying.svelte:10-13`.

### DRY — explicitly NOT worth extracting

Recorded so nobody "cleans these up" later:

- **The three calendar adapters.** Their only shared surface is about six lines. Their
  `fetch()` bodies — conditional GET, sync-collection-or-digest, syncToken-with-401-retry —
  are three genuinely different algorithms. A base class would save ~12 lines and add an
  inheritance hop to the most correctness-sensitive code in the app. `base.py` being
  protocol-only is right.
- **The three "seed from settings" reconcilers.** They share a name pattern and nothing else:
  a positional upsert with a sweep, a six-line get-or-create, and a filesystem reconciler with
  an mtime fast path. A generic reconciler would be pure ceremony.
- **Route validation boilerplate.** Three lines each, different messages; a `_one_of()` helper
  would make the messages harder to tune.
- **`grid.py`'s five-view dispatch.** A table-driven rewrite would be less readable, and the
  module docstring already accounts for the cost.
- **`DayWeekView` vs `MonthGrid`.** Genuinely different components — variable columns versus a
  fixed 7-column grid with chip overflow and padding days.
- **The portrait media-query blocks.** Each does completely different work.
- **The three `now_playing` surfaces.** A strip, a full screen, and a photo caption. The
  decision to fix the metadata *server-side* so none of them learn that a queue exists was
  correct.

### Test coverage

**Backend: 529 tests across 25 files, all passing in 6.2s.** These are behavioural tests with
prose names stating the failure being prevented, not assertion soup. Specifically strong:

- **The HEOS queue lock** — `test_music_queue.py:237, 268, 298, 328` are four genuine
  interleaving tests (a new album racing a track-end, a late event about the old album,
  stop-waits-for-in-flight, two speakers not blocking each other). This is exemplary and rare.
- **Overnight wrap and DST** in `test_screen_schedule.py:90-111`, both directions.
- **Forced-resync recovery** and Google end-to-end deletion in `test_sync_materialization.py`.
- **The 255-character token boundary**, tested exactly, in `test_music_tokens.py:86`.
- **Dense arithmetic checked against something outside the implementation** — published
  ephemerides for the moon, facts-true-by-definition for comets.

**Untested — nothing imports these:** `main.py`, `weather/client.py`, `photos/observer.py`,
`music/service.py`, all three CLIs (632 lines), `deploy/pi/screen_agent.py` (339 lines),
`db.run_migrations`, and the migrations themselves.

**Highest-risk gaps, by consequence:**

1. **The Alembic chain is never exercised.** `conftest.py:3-7` builds the schema with
   `metadata.create_all`, not migrations — a deliberate and correct choice for application
   tests, but it means seven revisions including a column rename are only ever run when a real
   container boots against a family's live database. **One test that runs `upgrade head`
   against a temp SQLite file and diffs against `metadata` closes the highest-consequence gap
   in the suite.**
2. `weather/client.refresh_weather` — zero tests, and its failure mode is B3.
3. The stream proxy (`routes.py:683-749`) — only the unknown-token 404 is covered. Range
   forwarding, `Content-Range` passthrough, and the client-closing `finally` are not; that is
   60 lines containing the app's only resource-lifetime subtlety.
4. A non-cancelled `RECURRENCE-ID` override through `sync_source` — the gap that hides B1.
5. `photos/observer.py`, `get_music_art`, `screen_agent.py`.

**Frontend: no test infrastructure in the repository at all.** No vitest, no playwright, no
test script. The Playwright harness this project's own docs credit with catching every
client-render failure lives at
`.claude/worktrees/headless-browser-harness/tools/panel/` — 682 lines — and `git ls-files`
does not know about it, because `.gitignore` excludes `.claude/worktrees/`.

Given the documented argument that it is *the only thing* that catches a blank panel — the
backend suite stays green while the display is a blank rectangle — **leaving it untracked is
the largest testing risk in the project.** It will not survive a fresh clone.

Note also that the green frontend gate proves less than it appears: `svelte-check` cannot
catch runes in a plain `.ts` file, and it did not flag `Screensaver.svelte:117-124` because
Svelte's a11y rules key on `onclick`, not `onpointerdown`.

**Test hygiene:** `test_music_api.py:485-489` calls `service.start_music()` without
`stop_music()`, leaving a `_NullController` in module globals for four tests. Invisible today
because the route tests monkeypatch `get_controller` directly — but exactly the kind of leak
that makes a future test fail depending on ordering.

---

## Readability notes

**Exemplary, worth preserving as-is:** `localtime.py` (whole module); `grid.py:125-156`
(`local_dates_spanned`, two subtle conventions each stated with its consequence);
`queue.py:194-232` (`awaiting_start` explained by the failure it prevents in *both*
directions); `config.py:340-366` (points a caret at the offending character in a bad JSON
setting); `watchdog.ts:22-27`, `Screensaver.svelte:28-31` and `MusicOverlay.svelte:33-35`,
each documenting a real bug that was hit and fixed.

**Worst offenders:**

- **`google_source.py:276-288`** is the most fragile code in the repo. It serializes a VEVENT
  to text, does `text.replace("END:VEVENT", <rrule lines> + "END:VEVENT")`, wraps the result
  in a hand-written VCALENDAR string, and re-parses. `str.replace` replaces *every*
  occurrence, so any event whose SUMMARY, LOCATION or DESCRIPTION contains the literal
  `END:VEVENT` produces a malformed document. It degrades safely, but
  `event.add("RRULE", vRecur.from_ical(line))` does the same job without string surgery.
- **`astro.py:68-127`** — 60 lines of single-letter variables and 16 magic coefficients.
  Acceptable given the explicit Meeus citation and the published-ephemeris tests, but worth
  knowing it is not readable code.
- **`api/routes.py` docstring density.** `get_photos` has a 16-line docstring for 30 lines of
  code. The content is good; at 749 lines the file reads as prose with code embedded.

**Type-hint holes:** `queue.py:66-67` annotates `play_url`/`url_for` as `object` and then
calls them; `routes.py:158` and `:409` are missing annotations; four injection seams
(`caldav_source.py:53`, `jellyfin.py:60`, `google_source.py:42`, `comets.py:300`) are bare.

**Naming trap:** `AgendaList.svelte` has a prop `now: string | null` (line 8) and then
`const now = new Date()` inside `todayKey()` at line 19 — shadowing the prop with a different
type. Also `app.models.CalendarSource` (a table) and `app.calendars.base.CalendarSource` (a
protocol) share a name, papered over with an import alias at `sync.py:10`.

**Nits:** `config.py:315-318` is a property with a `mkdir` side effect — reading an attribute
creates a directory. `pyproject.toml:4` still says "Add your description here".
`screen_agent.py:288` is an f-string with no placeholders. `alembic.ini:89` retains scaffold
credentials (harmless; overridden at `migrations/env.py:24`).

---

## Recommended order of work

1. **B3**, then **B4** — smallest fixes, worst consequences.
2. **B2 together with DRY #1** — the fix and the de-duplication are the same change.
3. **B1**, plus the missing moved-occurrence test.
4. **F1 + F2** — the blank-panel path.
5. **F3, F4** — volume debounce; the four small correctness fixes.
6. **Track `tools/panel/`**, and add the Alembic migration test.
7. **B5, B6, B7** — small and contained.
8. **S1** — move the proxies into `jellyfin.py` (making its docstring true), then split the
   router.
9. **S2, S3** — extract `lib/music.svelte.ts`; finish the design layer.
10. **Delete the dead code**, and add a ruff config. `.gitignore` already lists `.ruff_cache/`
    and the source carries `# noqa` markers, so ruff was in use at some point; several
    findings here — the unused local, the f-string without placeholders, the bare excepts, the
    missing return annotations — are things a linter catches for free.
