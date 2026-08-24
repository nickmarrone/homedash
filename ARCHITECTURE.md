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
frontend/static/  Served at the site root; holds the vendored woff2 files
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
| `scheduler.py` | APScheduler jobs: calendar syncs, heartbeat, weather, comets, photo index |
| `sse.py` | `SSEBroadcaster` fan-out to connected panels |
| `devices.py` | Screen-schedule arithmetic and the device row reconciler |
| `api/routes/` | Every HTTP endpoint, one router per subject, assembled in `__init__` |
| `api/deps.py` | `settings` and `SessionDep` — the one config seam every router shares |
| `api/serializers.py` | `serialize_instance` and `serialize_now_playing` — the wire shapes |
| `calendars/` | Calendar adapters and the sync/expansion pipeline |
| `photos/` | Photo sources, the index, and Pillow resizing for the screensaver |
| `music/` | The HEOS connection and the speakers the panel controls |
| `weather/client.py` | Open-Meteo fetch and its in-process cache |
| `astro.py` | Moon phase, meteor showers, equinoxes — computed, no I/O at all |
| `comets.py` | MPC orbital elements: fetch, cache, propagate, filter to what is actually visible |
| `cli/` | One-off operator commands (`homedash-google-auth`, `homedash-inspect-calendars`, `homedash-heos-probe`) |

### `calendars/`

| Module | Responsibility |
|---|---|
| `base.py` | `CalendarSource` protocol: `fetch(force)`, `changed`, `sync_state` |
| `ics.py` | ICS over HTTP, ETag conditional GET |
| `caldav_source.py` | CalDAV, sync-token change detection |
| `google_source.py` | Google Calendar `events.list` with `syncToken` |
| `google_auth.py` | OAuth refresh-token credentials |
| `sync.py` | `seed_calendars_from_settings`, `build_adapter`, `sync_source`, `sync_window` |
| `grid.py` | Day/lookahead/week/month bucketing, anchors, period titles |
| `queries.py` | `instances_touching` — the one overlap predicate both views read through |
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

**…and rebuild anyway once an hour.** `needs_full_resync()` forces a fetch regardless of
what change detection says, every `full_resync_interval_minutes` (60). It has two jobs. The
materialization window rolls forward a day at a time, so a calendar nobody ever edits would
otherwise never be re-expanded and the far end of its window would quietly empty out. It is
also the backstop for change detection being *wrong*, which is not hypothetical: Google's
sync-token probe did not pass `showDeleted`, a deletion is reported as an ordinary event
whose status is `cancelled`, and so a cancelled appointment read as "nothing changed" and
stayed on the wall. That specific bug is fixed in `google_source.py`, but the class of it —
a provider signal that misses a change, with nothing to notice — is what the hourly rebuild
bounds.

**Deletions the rebuild cannot see for itself.** Re-fetching harder does not help with
these — the row survives precisely *because* the rebuild ran:

* **`STATUS:CANCELLED`.** Every other deletion is an *absence* — the event stops arriving,
  and a rebuild from what did arrive drops it. A cancelled event is the opposite: the
  source keeps serving it as a tombstone, so each rebuild re-materializes an appointment
  somebody already called off, and the hourly resync re-creates it rather than clearing
  it. `_is_cancelled()` filters both masters and expanded occurrences, which covers a
  cancelled single event, a whole cancelled series, and one occurrence dropped from a
  series by a `RECURRENCE-ID` override. `google_source.py` drops cancelled items before
  they reach here; this is what gives ICS and CalDAV the same guarantee. `TENTATIVE` is
  kept — an unconfirmed appointment is still an appointment.
* **Orphaned rows.** Every deletion in `sync.py` is scoped by `source_id`, so a row whose
  `calendar_sources` or `events` parent is gone is unreachable — the rebuild never looks at
  it and a forced full resync is still only a rebuild of one source's rows. It is *not*
  unreachable from the panel: `api/routes.py` joins outwards on purpose, so an instance
  with a missing source renders uncolored rather than vanishing, which turns an orphan into
  a permanent ghost. `sweep_orphaned_events()` runs from the reconciler at startup, which
  is the only moment a source row is ever deleted. `homedash-inspect-calendars --state`
  reports any that exist, and `--find TEXT` answers the question behind any stale
  appointment: it asks the calendar, with one forced fetch, whether the event on the
  wall is still being served — which separates "the calendar still has it" from "this
  row should have been rebuilt away".

**One `events` row per UID, and it holds the master.** A recurring series and every
`RECURRENCE-ID` override of it share a single UID. Storing a row per VEVENT and keying them
by UID meant the last component seen won — and that is an override, which carries no RRULE.
Moving one soccer practice therefore replaced the series' stored VEVENT with that single
Thursday and orphaned the row holding the actual rule. Nothing on the panel showed it, since
the expansion has already happened by then; what broke was everything downstream of
`raw_vevent` — re-expanding the window without re-fetching, which is the only reason the
column exists, and `homedash-inspect-calendars --find`, which reported `recurring=False` for
exactly the events somebody would be running it on. `_rows_to_store()` picks the master
regardless of feed order, and falls back to an override only when the feed offers no master
at all.

**One overlap predicate, read by both views.** `queries.instances_touching` answers "which
instances touch these local dates?" for the agenda and the grid alike. It was written twice
before and the two copies disagreed: the agenda filtered on `starts_at` alone, so an event
already running when the range opened was dropped from it while the grid still showed it —
a week-long holiday appeared on the wall on its first morning and then vanished for six
days. The predicate is subtle enough to be worth centralising because `event_instances`
holds two kinds of value in one column: a timed row carries a real UTC instant, an all-day
row carries a *floating* midnight (see `localtime.py`), so every range check is two range
checks.

`pad_days` is the one knob. The grid passes 1 because `build_days` re-buckets by exact local
date afterwards and only needs a superset — an instance whose local date is in range can
carry a UTC instant that is not. The agenda passes 0 and renders what it is given.

**The agenda says which day to file an item under.** Almost always the day it starts, but an
in-progress event starts in the past and a forward-looking list has no heading for a date
that has scrolled off it, so `agenda_date` is clamped to today. It is computed server-side
for the same reason every other date is: the panel must not consult its own clock. This is
also why `/api/agenda` and `/api/calendar` are *not* byte-identical in shape — they share a
core and each adds what only it knows (`agenda_date` here, `continues_before`/`_after`
there).

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

### `music/`

| Module | Responsibility |
|---|---|
| `base.py` | `MusicLibrary` protocol and the `Artist`/`Album`/`Track` shapes |
| `heos.py` | `HeosController`: the connection, the player snapshot, five transport verbs |
| `jellyfin.py` | `JellyfinLibrary`: browse three levels, and fetch the art and audio |
| `tokens.py` | Short opaque stream tokens, and the 255-character check |
| `queue.py` | `QueueManager`: the per-speaker track list HEOS cannot hold, and the three ends that give the speaker back |
| `service.py` | The process-wide singletons, and the switches that decide they exist |

`queue.py` is the only module that knows about both halves — `heos.py` and
`jellyfin.py` never import each other. The same one-way discipline
`comets.py`/`astro.py` follow.

**HEOS's own CLI protocol, not DLNA.** HEOS pushes change events down the same
socket the commands go up, so a speaker's state arrives unprompted and turns
straight into an SSE publish. The DLNA equivalent (GENA) would have HomeDash
hosting a NOTIFY callback endpoint and renewing subscriptions, or polling
`GetPositionInfo` forever. HEOS also has multiroom grouping, which DLNA has no
concept of, and every speaker here is HEOS — so a second generic path would buy
nothing and cost an inbound HTTP surface.

`pyheos` owns the protocol: the serialized command lock, the heartbeat
keepalive, reconnect with backoff, and demultiplexing unsolicited events from
command responses. Same trade as `recurring-ical-events` in Phase 1, and it has
no transitive dependencies.

**`connected` means connected now.** It used to be `self._heos is not None`, which only ever
meant "has connected at some point" — pyheos reconnects underneath the controller without
clearing the handle, so a system that dropped off wifi during the evening kept answering
True. The panel switches on that field to decide whether the music UI is healthy, so it went
on offering buttons whose commands could only fail. It now reads pyheos's own
`connection_state`, and RECONNECTING counts as not connected: it resolves itself in seconds,
but until it does a command sent now will not arrive.

Reads and writes still degrade differently and deliberately: `/api/music/players` answers 200
with `connected: false`, because a cold start is ordinary and the panel needs something to
render. A command answers 503 (nothing to send it down), 502 (the speaker refused it) or 404
(no such speaker) — never a 200 the speaker never heard, and no longer a 500, which is what
everything but `KeyError` used to produce.

**This is the first long-lived outbound connection in the app.** Everything
else reaches the network on an interval, fetches, and lets go. That is why it
is an asyncio task started in the lifespan rather than an APScheduler job, and
why `start_music()` returns the moment the task is created — the speakers are
usually asleep at boot, and the calendar must not wait on them.

**Nothing is persisted.** The speakers hold their own state and it is read back
from them, so there is no row to reconcile and no way for a stored volume to
disagree with the wall. This is the one subsystem with no seeder.

**Connecting is not enough to have players.** `pyheos.Heos.players` stays an
empty dict until `get_players()` is called, so `_run()` calls it once after
connecting. Skipping it is not a partial failure — it presents as a healthy
connection with no speakers, which the panel renders identically to a
deployment that has no music configured. One call is also all that is needed
for the lifetime of the process: pyheos re-loads players on reconnect only if
they were ever loaded, so this is what arms that too.

**An artist carries the name the library sorted it under.** Jellyfin files "The
Beatles" as "Beatles, The" and returns the list in that order, so `Artist` has a
`sort_name` alongside its display name and `/Artists` asks for `fields=SortName`
explicitly. The panel's A–Z rail jumps by position in the list; indexed on the
display name it would point its T at a row sitting between the As and the Cs.

**Progress events are dropped.** HEOS emits one per second for the playing
speaker. Each is a legitimate update, but forwarding them would put an SSE
message per second per speaker onto a panel that only needs to know the track
changed — so the position rides along on the next real update instead, and the
now-playing bar is deliberately not animated between them.

Four hardware behaviours shape everything above, none of them guessable:

- **A URL over 255 characters is not played,** and no error is reported. This
  is what forces HomeDash to be the stream origin rather than handing the
  speaker a Jellyfin URL, and `homedash-heos-probe` refuses to send one rather
  than letting it present as silence.
- **`.m3u` and `.pls` are not played** — direct stream links only. So an album
  cannot be handed over in one call, and `browse/add_to_queue` only accepts
  ids from HEOS's own browse tree. HomeDash must own the queue.
- **SSDP does not cross a Docker bridge network.** The host is configured, not
  discovered; any one speaker's address will do, since `player/get_players`
  returns the rest.
- **`browse/play_stream` appends the URL to the speaker's own queue.** It is
  not a fire-and-forget "play this" — it adds a queue entry and plays that
  entry. Driving an album one track at a time therefore fills the speaker with
  one dead HomeDash URL per track unless something takes them out again. See
  "Giving the speaker back".

**HomeDash is the stream origin, and all three limits force it.** The speaker
fetches `/api/music/s/{token}` from HomeDash, which proxies the bytes from
Jellyfin with a header-authenticated request: the URL stays short, no credential
travels in it, and nothing depends on Jellyfin's query-parameter auth — which is
deprecated in 10.11 and **removed in 10.13**.

**The queue is HomeDash's, and it is not gapless.** One track is sent, and the
next goes out when the speaker reports the first finished. There is roughly a
second of silence between tracks; that is inherent to driving it this way. The
escape hatch, if it ever matters, is routing playback through a DLNA server HEOS
browses natively — a real queue, at the cost of mapping two id spaces.

**Telling three identical `stop` events apart** is where the queue's actual
logic lives. A speaker reports `stop` between two tracks, at the end of one, and
when somebody presses stop. `awaiting_start` distinguishes the first (set when a
track is sent, cleared only when the speaker reports `play` — a `pause` or an
`unknown` says nothing about whether the track we sent is the one it is on), and
clearing the queue on an explicit stop distinguishes the third. Getting the first
wrong consumes an entire album in a fraction of a second with only the last track
audible; getting the third wrong restarts music on somebody who just asked for
silence.

**Everything touching one speaker's queue is serialized behind a per-speaker
lock,** held across the command sent to the speaker rather than just the state
change. pyheos dispatches every pushed event as its own task, so a track ending
and a finger on a new album genuinely run at the same time: the ending track
sends its successor, the new album sends its first track, and whichever command
wins the race inside pyheos is what plays. Heard on the wall, that is an album
starting on the previous album's next song. The lock also restores the ordering
of the events themselves — `asyncio.Lock` is FIFO, so a `stop` and the `play`
that followed it can no longer be applied backwards — and it is why the transport
route calls `QueueManager.stop()` rather than `clear()`: dropping the queue while
the next track is still on its way would stop the music and let that command
start it again. The locks are per speaker so one stalled command cannot hold up
the album playing in the next room.

**Skips go through the queue, not through HEOS.** `play_next` has nothing to
move to and does nothing at all, because the entry `play_url` just appended is
always the *last* one in the speaker's queue. This was originally read as
"content sent as a URL never enters the speaker's queue", which is the opposite
of the truth and cost this feature a bug — see below. The conclusion was right
either way: the route asks `QueueManager` first and falls through to the speaker
only when there is no HomeDash queue, which is right for a speaker playing from
one of its own sources.

**Giving the speaker back.** Because `play_url` appends, HomeDash has to tidy
the speaker's queue or an album leaves it unusable — the speaker resumes into a
list of URLs that have already been served and whose tokens will stop resolving,
and nothing anybody plays afterwards, from the panel or from the HEOS app, gets
out from under them. Two mechanisms, and they are different on purpose:

- **Pruning, on `play`.** Once the speaker reports it is playing, it can say
  which queue entry it is on, and every other entry goes. That is the earliest
  such moment — pruning straight after `play_url` would be pruning to the entry
  the speaker has not adopted yet, and would delete the track about to start.
  It keeps the queue at one entry for the length of an album, and it sweeps up
  whatever an older build left behind, so a confused speaker fixes itself on the
  next thing HomeDash plays on it.
- **Clearing, at the end.** `QueueManager._end` is the one place an album stops,
  reached three ways: the last track finishing, a skip off the end, and the
  panel's stop button. They differ only in whether the speaker still needs
  telling — it has already stopped itself in the first case — and clearing is
  never *pruning* here because there is no entry left worth keeping. Clearing
  rather than pruning mid-album would be wrong the other way: `player/clear_queue`
  raises Player State Changed as well as Player Queue Changed, so it can stop
  the music.

Both are best-effort at the `music/service.py` seam. HEOS answers an error for
`clear_queue` on an empty queue and older firmware need not implement
`get_queue` at all, neither of which is a reason to fail a stop somebody asked
for. `awaiting_start` is cleared *before* the prune for the same reason: a
failure to tidy must never cost the queue its ability to tell a finished track
from the gap before one, which would stall the album for good.

`homedash-heos-probe` prints each speaker's queue depth, and `--clear-queue`
empties it — the queue is invisible from the panel and from the logs, so this is
the only way to see the fault or to clean up after a build that had it.

**HomeDash answers for the speaker about what is playing.** A speaker handed a
bare URL has no metadata for it and falls back to describing the stream, so the
panel showed a bitrate and a codec where the song title goes, and the speaker's
own art URL — pointing at nothing — where the cover goes. Whenever there is a
HomeDash queue, `GET /api/music/players` replaces `now_playing` with the
Jellyfin track it actually sent: title, artist, album, its duration, and a
cover at `/api/music/art/{album_id}`. Only `position_ms` is still the speaker's,
because it is the only party that knows it. A speaker playing one of its own
sources reports perfectly good metadata and keeps it.

The album a cover lives on rides along on `Track.album_id` rather than being
looked up again, because a queue can be started from an explicit list of tracks
with no album in the request.

The queue and the token store are in-process, like the weather cache. **A restart
therefore stops the music after the current track.**

### Astronomy — `astro.py`, `comets.py`

**Computed, not fetched.** Open-Meteo has no moon or meteor data, and every service that
does is one more thing that can be down, rate-limited, or want a key — for numbers that are
a few dozen lines of arithmetic and change on nobody's schedule but the solar system's.
`astro.py` does **no I/O whatsoever**, and every function in it takes `now` as an argument
rather than reading the clock.

Accuracy is chosen for something read across a kitchen, and the cheap version is not good
enough: moon phases come from Meeus ch. 49 rather than "days since a known new moon, modulo
29.53", because the Moon's orbit is an ellipse and the naive form drifts up to ~14 hours —
enough to print "Full moon" on the wrong evening about a third of the time. Equinoxes use
the mean expressions of ch. 27 without the periodic terms, worth ~20 minutes, which cannot
move the date unless the event falls that close to local midnight.

`comets.py` is the exception on both counts: a naked-eye comet is a *discovery*, not an
annual event, so there is nothing to hard-code and a feed is the only honest source. It is
therefore the one module here that reaches the network, and it is built to fail quietly —
elements are cached on disk beside the database (`CometEls.txt`, so the existing volume
already persists them), a failed refresh keeps the last good copy, a bad line is skipped
rather than failing the file, and every parsed value is range-checked so a format change
surfaces as "no comets" rather than a comet at an impossible distance.

**The dependency runs one way.** `comets.py` imports from `astro.py`, never the reverse.
`astro_summary(..., extra_events=...)` is the seam: comets are passed *in* as already-shaped
events, which is exactly what keeps the I/O-free module I/O-free.

Magnitudes are not trusted. Positions are celestial mechanics; brightness depends on how
much ice is left and how it behaves near the Sun, and comets routinely miss forecasts by
magnitudes in both directions. Hence a conservative `comet_magnitude_limit` (6.0) — a listed
comet is "worth a look", never a promise.

The sky is assembled in the `/api/weather` handler rather than folded into the weather cache
on refresh, deliberately: it needs no network, and Open-Meteo being unreachable must not also
take the moon off the panel. It is cheaper than serializing the forecast it travels with.

### Lifespan ordering — `main.py`

```
run_migrations()
  → seed_calendars_from_settings(session)      reconcile config into calendar_sources
  → seed_device_from_settings(session)         reconcile config into the devices row
  → broadcaster.bind_loop(running loop)        SSE publishes are no-ops before this
  → refresh_weather() in a thread executor     blocking HTTP, kept off the event loop
  → start_scheduler()
  → start_folder_watch(photos_dir, …)         after the scheduler, so the first scan is queued
  → start_music()                              after bind_loop; returns at once, connects in background
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

Not everything persistent is a row: the comet elements are a cached MPC text file beside
the database, and photo derivatives are files in the cache directory. Both are rebuildable
from their source, which is why neither earned a table.

No SQLModel `Relationship` attributes anywhere — joins are written explicitly in queries.
No DB-level cascades either; child rows are deleted by hand in the reconcilers.

`event_instances` is the key design call: recurrences are expanded on sync into a flat,
indexed table covering roughly a month back and a year forward. The panel never triggers
an RRULE expansion, and month view is a single indexed range query.

**Views are day buckets at different widths.** `day`, `next3`, `next5`, `week` and `month`
all return the same array shape, which is why the lookaheads cost a row in
`grid.LOOKAHEAD_DAYS` and three branches rather than a rendering path. The one thing that
separates them is where the window starts: `week` and `month` snap their anchor to a period
boundary, and `next3`/`next5` deliberately do not. A lookahead snapped to a week start would
be mostly in the past — on a Sunday, a snapped three-day view is two days already over — and
"the next few days" is the entire question those views answer.

### API surface — `api/routes.py`

| Endpoint | Returns |
|---|---|
| `GET /healthz` | Liveness |
| `GET /api/agenda` | Flat chronological events from today on, each with its `calendar: {id, name, color}` and an `agenda_date` |
| `GET /api/calendar?view=day\|next3\|next5\|week\|month&anchor=` | Server-bucketed grid, plus title and prev/next anchors |
| `GET /api/calendars` | The legend — every enabled source, so empty calendars still appear |
| `GET /api/devices/{id}/screen` | `{state, until, poll_after_seconds}` for the Pi's screen agent |
| `GET /api/photos?orientation=landscape\|portrait` | Screensaver playlist: each photo's slot, size, and hashed URL |
| `GET /api/photos/{id}/image?orientation=&v=` | One pre-rendered JPEG derivative, `immutable` |
| `GET /api/music/players` | The speakers, what each is playing, and its queue |
| `GET /api/music/library?kind=artists\|albums\|tracks&parent=` | One level of the library |
| `GET /api/music/art/{item_id}` | Proxied cover art, `max-age=3600` |
| `POST /api/music/players/{id}/play` | `{album_id}` or `{track_ids, parent_album_id}` |
| `POST /api/music/players/{id}/transport` | play / pause / stop / next / previous |
| `POST /api/music/players/{id}/volume` | `{level}`, 0-100 |
| `GET /api/music/s/{token}` | **Fetched by the speaker, not the panel.** Streaming audio proxy |
| `GET /api/weather` | The weather cache, plus `astro` — the moon and the next few weeks of sky |
| `GET /api/events/stream` | SSE (`EventSourceResponse`) |

**There are no response models.** Handlers are annotated `-> dict` / `-> list[dict]` and
build plain dicts; `serializers.py` exists only where two endpoints must emit an identical
shape. Request params use `Query(...)` with manual validation raising `HTTPException(400)`.
Routes stay thin — date arithmetic lives in `grid.py` and `devices.py`, not in handlers.

`GET /api/music/s/{token}` is the app's only streaming response, and the only
endpoint whose client is not a browser. It forwards the caller's `Range` header
and passes `Content-Range`/`Accept-Ranges` back, because HEOS asks for ranges and
Jellyfin already implements them correctly. The `httpx.AsyncClient` deliberately
outlives the handler — the body is pulled long after it returns — so it is closed
in the generator's `finally`, not a context manager. Browsing failures answer
**502, not 500**: the fault is upstream, and the status is the difference between
"check Jellyfin" and "check HomeDash" for whoever reads the log.

The image endpoint is the only other non-JSON response in the app. It is a pure file read — the
resize happened at index time, the same discipline the weather cache states for itself. Its
URL carries the content hash as `v`, which is what makes `immutable` safe: a photo replaced
in place gets a new URL rather than a cache entry the panel would hold for a year. The `v`
value is deliberately *not* validated on the way in — it exists to change the URL, and
rejecting a stale one would only turn a slightly old playlist into visible gaps.

**The music routes are the app's first write path, and there is no auth on
them.** The panel has none and the household has settled for a LAN-only
appliance; open decision 3 and the "kid lock" future feature are what would
change that. Reads and writes degrade differently on purpose: `GET
/api/music/players` answers 200 with `connected: false` while the speakers are
still asleep, because that is an ordinary cold start, but a command sent before
the connection is up is refused rather than reporting a success the speaker
never heard.

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
| `comet_refresh` | `comet_refresh_hours` (24) | `weather.updated` if the elements changed |

Every job: `next_run_time=datetime.now()` so it fires at boot rather than after one full
interval; its own `with Session(engine)`; per-item `try/except` with `session.rollback()`
and `logger.exception`, never letting an error escape and kill the job; and it publishes
**only when something actually changed**.

ICS and the fast kinds are split because providers regenerate `.ics` files on their own
schedule — polling harder just re-fetches the same stale bytes.

`comet_refresh` is the exception to "every job runs": it is registered only when
`comets_enabled`, so a panel configured to stay entirely self-contained makes no outbound
request at all. Once a day is generous for it — these are orbits, and a newly discovered
comet takes weeks to become worth looking at.

### SSE — `sse.py`

Module-level singleton `broadcaster`. `publish(event_type, data)` is **synchronous and
safe to call from APScheduler threads** (`loop.call_soon_threadsafe`); it silently no-ops
before `bind_loop`. No per-subscriber filtering and no replay — every panel gets every
event.

Events: `events.updated`, `weather.updated`, `photos.updated`, `music.updated`,
`heartbeat`. `music.updated` carries no payload — the panel re-reads
`/api/music/players`, which keeps one source of truth for that wire shape.

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

**One route, `/`.** The six views are a `view` state variable on `+page.svelte`, not
separate routes. `+layout.ts` is two lines: `prerender = true`, `ssr = false` — all data
comes from client-side fetch and SSE against the running backend, so there is nothing to
render at build time.

`+layout.svelte` imports `lib/theme.css` and holds the global touch lockdown: `contextmenu`
suppression via `<svelte:document>`, plus `touch-action: manipulation`,
`-webkit-touch-callout: none`, and `user-select: none` on everything except inputs.

`+page.svelte` owns all state and all data loading, and wires everything in a single
`onMount` that returns a cleanup closure. Its header is a masthead — today's date on the
left, the temperature on the right, under a thick-over-thin rule — then the almanac line,
the hourly forecast, and a `.controls` row holding the calendar legend on the left and the
view switcher on the right.

The date in the masthead is the server's, from the heartbeat or the grid payload, so the
header is empty until the first response lands and rolls over on its own at midnight. It is
the only place the panel states the date at all; the slot used to hold the product name. The switcher is pushed right by `margin-left: auto` on its own
wrapper rather than by `justify-content`, so it still sits against the right edge on a
single-calendar panel, where the legend renders nothing at all.

### `lib/`

| Module | Responsibility |
|---|---|
| `api.ts` | **All** types, all fetchers, and the SSE subscriber |
| `theme.css` | The whole design layer: `@font-face`, the colour tokens, the `.caps` label class. Imported once from `+layout.svelte` |
| `format.ts` | Wall-clock string parsing — `formatTime`, `dateKey`, `formatDayHeading`, `formatHour`, `formatMasthead`, `hasPassed`, `formatSkyDate`, `addDays` |
| `watchdog.ts` | Reloads the page if the SSE stream goes quiet, or on a fatal stream error |
| `retry.ts` | Backoff for a load that failed, so a panel that started before the backend heals itself |
| `idle.ts` | Notices when nobody has touched the panel; drives the screensaver |
| `slideshow.ts` | Pure shuffling and pairing of a photo playlist into slides |
| `orientation.svelte.ts` | Reactive `isPortrait` from `matchMedia` |
| `music.svelte.ts` | The speakers, the selected one, and the commands that change them |
| `calendarVisibility.ts` | localStorage set of hidden calendar ids |
| `musicPreference.ts` | localStorage of the controlled speaker, and the fallback when it is gone |
| `viewPreference.ts` | localStorage of the last-selected view |
| `weatherCodes.ts` | WMO code → description |

### `lib/components/`

| Component | Responsibility |
|---|---|
| `AgendaList.svelte` | Events grouped by day; always injects a "Today" group so the panel is never blank |
| `CalendarLegend.svelte` | Tap-to-hide calendar chips; renders only when there's more than one calendar |
| `DayWeekView.svelte` | Day, lookaheads and week as columns; column count comes from `days.length` |
| `MonthGrid.svelte` | 7-column grid, 3 chips per cell then "+N more" |
| `HourlyForecast.svelte` | 12-hour temperature and rain strip, one SVG in column units |
| `PeriodNav.svelte` | Title / ‹ / Today / › — the chevrons are inline SVG, not characters |
| `ViewSwitcher.svelte` | Agenda / Day / 3 Day / 5 Day / Week / Month segmented control |
| `WeatherWidget.svelte` | The masthead's right half: the temperature and the description, nothing else |
| `Almanac.svelte` | The line under the masthead rule: H/L, sunrise–sunset, AQI, the moon, then `SkyEvents` |
| `SkyEvents.svelte` | The next three sky events, comets first. `display: contents`, so they join Almanac's row rather than forming one of their own |
| `MoonGlyph.svelte` | The lunar disc as inline SVG, drawn from the real illuminated fraction |
| `Screensaver.svelte` | Full-screen photo slideshow with a two-layer crossfade |
| `PanelBlank.svelte` | Plain black, when the schedule says the screen should be off |
| `NowPlayingBar.svelte` | The sticky strip under the calendar, while there is a track to act on |
| `MusicOverlay.svelte` | Full-screen music: Now Playing and Library tabs |
| `MusicBrowser.svelte` | Artists → albums → tracks, one level at a time, with a history stack and an A–Z rail |
| `NowPlaying.svelte` | Art, title, artist, album, progress |
| `TransportControls.svelte` | Play/pause/skip/stop, inline SVG, compact and full |
| `PlayerPicker.svelte` | Which speaker; renders nothing for a one-speaker household |

### `api/routes/`

| Module | Endpoints |
|---|---|
| `system.py` | `/healthz`, the SSE stream, the device screen schedule |
| `calendar.py` | `/api/agenda`, `/api/calendar`, `/api/calendars` |
| `weather.py` | `/api/weather` |
| `photos.py` | The screensaver playlist and its derivatives |
| `music.py` | Speakers, the library, the art and audio proxies |

This was one 749-line module. The split is by what an endpoint is *about*; paths are
unchanged and `main.py` still imports a single `router`.

Two things left rather than moved sideways, because they were another layer's work:
the Jellyfin proxies are now `JellyfinLibrary.art()` and `.open_stream()` — a route should
turn a call into a status, not hold an httpx client open across a streaming response — and
the now-playing wire shape is in `api/serializers.py` beside the event one.

**Settings live in `api/deps.py`, not per router.** Five module-level `settings` globals
would mean a test had to know which router file an endpoint happened to land in. Reference it
as `deps.settings`; `from app.api.deps import settings` binds a copy and defeats the patch.

**Request bodies are Pydantic models**, not `dict` picked apart by hand. That moves a
malformed body to a 422 that names the field, and leaves 400 for what a schema genuinely
cannot answer — an action that is a string but not one this speaker has.

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

**One global stylesheet — `lib/theme.css` — and scoped `<style>` blocks for everything
else.** The design is called *Kitchen paper*: a warm off-white ground, a display serif for
dates and titles, hairline rules instead of grey fills, and the calendar's colour as a rule
beside the words rather than a tile behind them. `theme.css` is imported once, from
`+layout.svelte` rather than `+page.svelte`, because the music overlay, the screensaver and
the bedtime blank all render outside the page's markup and need the same tokens.

Anything that is a colour, a typeface, a radius or a tap target belongs in `theme.css`.
Components keep their layout and nothing else, so the panel can be re-skinned from one file.

| Token group | What it is |
|---|---|
| `--paper`, `--paper-raised`, `--paper-sunk`, `--wash` | Grounds. `raised` is white and marks today, and only today; `sunk` marks padding days |
| `--ink`, `--ink-soft`, `--ink-muted`, `--ink-ghost`, `--ink-trace` | Text, in five steps. Ratios against `--paper` are 17.1 / 11.1 / 4.8 / 3.2 / 2.1 |
| `--rule`, `--rule-soft`, `--rule-strong` | Hairlines — grid, list rows, outlined buttons |
| `--rain` | The precipitation bars, deliberately neither ink nor a calendar accent |
| `--accent-fallback` | An item with no calendar; mirrors `FALLBACK_COLOR` in `colors.py` |
| `--font-display`, `--font-body` | Newsreader and Figtree |
| `--tap`, `--radius-pill`, `--radius-sm` | 48px, and the two radii the direction keeps |

**Five global classes, and they are the whole of the shared design.** `.caps` and `.caps-sm`
are the section label at its two sizes; `.struck` is what a finished appointment looks like;
`.tab` is an underlined tab; `.control-round` is an outlined round control. Plus bare
`button` rules for the touch behaviour and the focus ring that every button needs.

They exist because the components had written them out privately — `.passed` three times,
the small caps run three times, the tab three times, the round control five times — and had
already begun to drift: two different custom properties carrying the calendar's colour, two
different paddings on the same tab, and a focus ring on eight button groups but not the other
two. That last one is the argument in miniature: a rule that lives in ten places is a rule
that will be missing from one of them.

Svelte scopes component styles, so a scoped selector outranks these and a component that
genuinely wants something different still can — `PlayerPicker` keeps tighter tab padding,
`.open-music` is a pill rather than a circle. The difference is that those are now one line
each, and visible as deliberate.

**`.caps` in particular.** Small, bold, letterspaced, upper, muted — the
direction's section label, used in the masthead, the agenda, both music screens and the
now-playing strip. Six private copies would drift, which is the thing a design layer exists
to prevent. It is global because Svelte scopes component styles to their own markup.

**The panel is light-committed.** `color-scheme: light`, not `light dark`. This palette has
no dark counterpart, and leaving the hint on has the browser paint form controls — the
volume slider above all — for a theme the page does not have. It is also why `PALETTE` in
`backend/src/app/calendars/colors.py` could move to deep tones: the old constraint was 3:1
against *both* `#ffffff` and `#1b1b1b`, which forced mid-tones. There is one ground now, and
every entry clears 4.5:1 against it — the text threshold rather than the non-text one,
because an all-day event is *set* in its calendar's colour rather than merely marked with it.

**No opacity ladder.** Fading text over an accent bar changed its hue as well as its weight,
which is what made "finished" and "not this month" read as the same state. Both are now ink
steps, and `.passed` mixes its accent rule toward `--rule` with `color-mix` rather than
losing alpha. Per-item colour still arrives as an inline custom property
(`style:--item-color` / `--chip-color`).

**Typefaces are vendored, not linked.** `frontend/static/fonts/` holds six woff2 files —
Newsreader roman and italic, Figtree, each in latin and latin-ext. The container serves the
panel off the LAN, so a Google Fonts `<link>` would only work while the Pi happens to have
internet, and the failure mode is the whole panel falling back to a system serif at
different metrics. Both faces are SIL Open Font License. They are variable fonts, so one
file covers each weight range; the vietnamese subsets Google also offers are left out.

**No emoji, anywhere.** Raspberry Pi OS Lite ships no emoji font, so on that image every
one renders as a tofu box on the actual wall panel. A desktop image does ship one — the
rule stands anyway, because the panel has to survive either. This is why the moon is drawn as inline SVG
(`MoonGlyph.svelte`) and `PHASE_NAMES` in `astro.py` carries names rather than glyphs — and
it is a constraint on any future icon: text or inline SVG, never a character and never an
icon font. The restyle closed the three places that were still characters: the sunrise line's
`☀` (U+2600) and the period nav's `‹`/`›`.

**Touch targets are 48px minimum** — "the smallest target that stays reliable for a
fingertip on a wall panel, where you are often reaching rather than aiming." Press feedback
is `:active { transform: scale(0.97) }`, because hover does not exist on touch.

The one deliberate exception is the A–Z rail in `MusicBrowser.svelte`: 27 letters down a
1080px-tall panel are ~35px each and cannot be made bigger without ceasing to be an
alphabet. It is therefore built as a *drag* rather than a set of taps — `letterAt()` maps a
pointer's Y onto a slot against the rail's own box, so the gaps between letters are live
too, and the whole strip is one pointer-captured gesture. Letters with nothing behind them
stay in place, dimmed, and jump to the next letter that does have something: a rail whose
letters move as the library grows is one you have to read instead of aim at. It appears
only above 20 artists, and only on the artists level.

### When the backend is not there yet

The Pi boots faster than the container, so the panel's opening round of fetches
routinely happens against a backend that is still starting. Every one of them used
to be an unhandled rejection, and nothing was left behind to try again: the masthead
painted, `grid` stayed null so the entire calendar body was absent, and `loadPhotos`
rejected before it could start the idle timer, so the screensaver could not appear
either. Once the backend arrived the SSE stream connected perfectly happily — and
because the stream was *healthy*, the staleness watchdog had nothing to react to. The
panel sat on a date and an empty page indefinitely.

Two things close it, and they are deliberately independent:

- **Every fetch goes through `reloadEverything` or `guard`** in `+page.svelte`. A round
  with any rejection arms `retry.ts`'s backoff — 1s, 2s, 5s, 10s, then every 30s —
  which re-runs the whole round until one comes back clean. Backoff rather than a fixed
  interval because "still booting" and "down for the evening" look identical from the
  browser and want opposite things.
- **`subscribeToUpdates` now has an `error` listener.** `EventSource` reports both of its
  failure modes through that one event and they need opposite responses: a dropped
  connection leaves `readyState` at CONNECTING and the browser retries by itself, while a
  non-2xx status or wrong `Content-Type` — a proxy answering 502 through a redeploy — is
  fatal per spec and nothing will ever reopen it. The fatal case calls
  `watchdog.reloadNow()`, which shares the staleness check's own reload throttle so a
  backend that is simply down cannot put the panel in a reload loop.

Verified by driving real Chrome with `/api/**` refused, then releasing it: the panel
recovers on its own, with no reload and nobody touching it.

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

`hasPassed()` follows the same rule for dimming events that are over: it compares the ISO
strings directly, because the backend emits both the event timestamps and the heartbeat in
the home timezone, so the wall-clock digits sort chronologically. It returns `false` while
either heartbeat field is still null — dimming a *future* event is a worse error than briefly
failing to dim a past one. Its one seam is the hour either side of a DST change, which is not
worth a `Date` for.

The exceptions are `formatDayHeading`, `formatSkyDate`, and `addDays`, which do use `Date` —
but only for calendar arithmetic on date components, in UTC, and never to read the clock. The
authoritative "today" they work from comes from the SSE heartbeat.

### What is over, and what is now

Three treatments, all driven by the heartbeat rather than the browser clock:

- **Finished events are struck through** (`.passed`) in all four views, via `hasPassed()`.
  The text drops to `--ink-ghost`, the strike is drawn in the calendar's own colour, and the
  accent rule mixes toward `--rule`. It used to be opacity, which on a ruled table stopped
  reading as "finished" and started reading as "faint".
- **Today is marked two ways** — the one surface that goes whiter than the page, and its
  date set in an ink disc. `DayWeekView` adds an inset ink edge and the word itself. It used
  to carry three marks including a 3px outline, which a gapless grid has nowhere to put.
- **A day that is over marks its heading only.** The events inside already carry the
  finished treatment, and reducing the whole cell as well would put two reductions on top of
  each other.

`is_today` comes off the server's grid payload; the `.past` comparison uses the heartbeat's
date. Neither asks the Pi what day it is.

### The three panel states

They are mutually exclusive and resolve in this order, all driven by the heartbeat's
`screen` field and the idle timer:

| Condition | Shows |
|---|---|
| `screen === 'off'` | `PanelBlank` — plain black, not dismissable |
| screen on, idle ≥ `screensaver_idle_minutes`, photos exist | `Screensaver` |
| otherwise | the calendar |

`PanelBlank` is the fallback `deploy/pi/README.md` describes for a monitor that ignores
output power management: the backlight stays on, so it is worse than a real blank, but the
kitchen goes dark. It is deliberately not tap-to-dismiss — the screen is meant to be off.

`screenOn` starts `true`, so a panel that has not had its first heartbeat shows the calendar
rather than flashing black on every reload.

**Music does not add a fourth state.** `NowPlayingBar` renders *inside* the calendar state,
and `MusicOverlay` sits above the calendar at `z-index: 40` but below the screensaver at
`100` and `PanelBlank` at `200` — so the order above is unchanged and bedtime still wins over
anything playing.

On idle the photos still take over, because that is what the screensaver is for; the track
rides on top as a caption instead. The panel therefore answers "what is this song" without
giving up the family photos exactly when people are in the kitchen. `idle.ts` listens on
`window` with `capture`, so taps inside the music overlay already count as activity and it
is never yanked away mid-browse — no extra wiring, unlike the screensaver, which swallows
its own dismissing tap.

With nothing playing there is no bar, so a **Music button** takes its place — the
smallest thing that keeps the library reachable without giving the calendar's
space to a permanent strip. The overlay opens on the Library tab when nothing is
playing and on Now Playing when something is, and that default is **derived, not
captured at construction**: the overlay can be opened before the first player
snapshot arrives, and a value read then settles on the wrong tab and stays there.

The bar shows while a track is **playing or paused**, not only while playing. Keying it on
`play` alone made the bar vanish the instant you paused from it, taking the resume button
with it; only a stopped speaker has nothing to offer. The screensaver caption is stricter
and keys on `play`, since a paused track is not what the room is listening to.

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
| `setup.sh` | Provisions the Pi; detects the session, takes a mode; `@PLACEHOLDER@` tokens substituted by `sed` |
| `kiosk-start.sh` | Browser in a restart loop, plus whatever the session needs first. Both sessions run it |
| `screen_agent.py` | Polls `/api/devices/1/screen` and blanks/unblanks the display. Stdlib only |
| `homedash-kiosk.user.service` | GNOME: bound to `graphical-session.target` |
| `homedash-screen.user.service` | GNOME: the agent, in-session so it can reach the session bus |
| `homedash-kiosk.service` | Console: labwc + Chromium, `Restart=always` |
| `homedash-screen.service` | Console: the screen agent, `Restart=always` |
| `chromium-policy.json` | `locked` mode only: `URLBlocklist: ["*"]` plus a single allowlist entry |
| `README.md` | The two axes, recovery, rotation, touch mapping, the overlay, blanking |

**Two axes, and neither is cosmetic.**

*Session* is detected, not configured: `gnome` where a display manager already drives the
screen, `console` where nothing does. Installing the console path onto a desktop image
starts a second compositor that loses the seat to GDM every five seconds, and sets the
default target to `multi-user` so the machine boots to a text console. Both units are
`WantedBy` the target their own path actually reaches — `graphical-session.target` for the
user units, `multi-user.target` for the system ones, which is what the console path boots
into.

*Mode* is `simple` (default) or `locked`. Lockdown is four layers when it is on: no desktop
(console image + autologin + labwc), Chromium kiosk flags, the enterprise policy (the
strongest layer, and the one most guides skip — even a successful escape from kiosk mode
can't load anything else), and the app-level touch CSS. `simple` keeps only the last of
those. It is the default because the policy layer applies to *every* Chromium on the
machine, which makes a browser opened to debug something look broken for no visible reason.

**A confined browser gets no profile directory.** Ubuntu's Chromium is a snap whose `home`
interface allows non-hidden paths under `$HOME` and nothing else, so any `--user-data-dir`
is refused and the process exits in milliseconds — which, on a restart loop, reads as a
crash rather than a rejected flag. `setup.sh` resolves the browser once and records whether
it is confined; `kiosk-start.sh` also backs off after five failures in a minute and logs the
command line it tried, so the next one of these is diagnosable from the journal alone.

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
- `xset dpms force off` — X11 only; both sessions here are Wayland.
- `wlopm --off '*'` / `wlr-randr --output X --off` — speak wlr-output-power-management,
  which **labwc implements and Mutter does not.** On a desktop image these are dead ends
  however they are installed, which the `available()` check cannot see: a binary on `PATH`
  says nothing about the protocol behind it.

So the mechanism follows the session: the wlroots pair under labwc, and
`org.gnome.ScreenSaver` on the session bus under GNOME. That last one is the only mechanism
whose availability check is exact — it asks the bus whether anything is listening — so it
sorts first, and it is also why the screen agent runs as a *user* unit on a desktop image:
outside the session there is no bus to ask. Whether a *portable* monitor honours any of it
is a separate question — `screen_agent.py probe` answers it on the hardware, and the agent
ships in `--dry-run` until it has been run.

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

There is no CI and no Makefile. Ruff is configured (`cd backend && uv run ruff check .`) and
is expected to be clean; it is not a formatter and nothing reformats this code.

**What it checks is deliberately narrow.** `E`, `F`, `I`, `B` — unused imports and locals,
undefined names, import order, and bugbear's real-defect rules. Pyupgrade (`UP`) is left out
on purpose: it wanted eighty-nine changes, every one a rewrite of correct code into
differently-spelled correct code. A linter that opens with two hundred stylistic opinions on
an existing codebase gets switched off within the week, and the rules that find actual
defects go with it. Line length is 100 rather than the ~95 the code was held to by hand,
because picking the exact number would have meant reflowing seventy readable lines.

`E711`/`E712` are ignored repo-wide: SQLAlchemy filters are *expressions*, so
`Model.flag == False` builds SQL where `not Model.flag` evaluates in Python and silently
matches everything. Alembic's own files are exempt from import sorting — it writes them in
its own order, and a rule that fails every `revision --autogenerate` teaches people to ignore
the linter.

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

`test_migrations.py` is the one place the chain is actually executed. It runs `upgrade head`
against a throwaway file, checks there is a single head, round-trips down to `base` and back,
and — the point of it — runs Alembic's own `compare_metadata` against `SQLModel.metadata`. A
column added to `models.py` with no revision written for it passes every other test in the
suite, because they all build their schema with `create_all` and never look at a migration;
it fails here. Note that `env.py` overwrites `sqlalchemy.url` from the settings singleton, so
steering it at a temp file means setting `HOMEDASH_DB_PATH` *and* clearing
`get_settings.cache_clear()`.

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
- **Dense arithmetic is checked against something outside the implementation.** `test_astro.py`
  asserts against published ephemerides with minute-wide tolerances, because a transcription
  slip in Meeus's coefficients is wrong silently or not at all, and an hours-wide test passes
  for the naive moon formula that this code exists to avoid. `test_comets.py` uses facts true
  by definition instead — a comet sits at its perihelion distance at perihelion; feeding
  Earth's own orbit in as a comet must come out at zero geocentric distance — because copying
  an ephemeris table only proves two numbers were transcribed alike. Fixture lines for the MPC
  parser are assembled from the documented column positions rather than copied from a real
  file, so they test the format and not one sample of it.
- Test names read as sentences, and docstrings explain **why the bug being guarded against
  is plausible**, not what the code does.

### A Playwright panel harness

`.claude/worktrees/headless-browser-harness/tools/panel/panel.mjs` (an untracked worktree)
drives real Chrome against the running panel. It is the only thing that catches
client-render failures — the backend suite stays green while the display is blank. It is
what caught a rune used in a plain `.ts` file.
