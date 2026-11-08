# HomeDash: The panel lock, settings, bedtime listening, multi-room, and Apple Music

## Context

HomeDash is working as a wall calendar with a music feature (Jellyfin browsed on the panel,
played on HEOS speakers). The next real household use is bedtime: the children listen to
audiobooks and podcasts from a self-hosted Audiobookshelf (ABS), today through the plappa
iOS app. They should be able to pick a book or episode on the panel, send it to the speaker
in their room, and then not be able to touch the panel any more. Three rooms may be
listening to three different things at once. Parents need a passcode lock. The
configuration has grown to ~25 env vars, so a settings page is overdue and comes right
after the lock that gates it.
And after bedtime lands, the children should be able to start Apple Music playlists from
the panel without an iDevice, which an always-on Mac makes possible.

Six parts, in the order they should land:

1. **Panel lock** with a PIN and a bedtime window. Independent of everything else,
   smallest, and it owns the PIN: where it is stored, how it is entered, and the
   wrong-attempt gate.
2. **Settings page**: household preferences move to the database, editable behind the
   same PIN, with a status section that shows what is configured and healthy.
3. **Queue generalisation.** The existing per-speaker queue becomes source-agnostic
   (a stream token resolves to "Jellyfin track X" *or* "ABS file Y from second Z"). Pure
   refactor; the music suite stays green.
4. **Audiobookshelf backend**: adapter, offset streaming via ffmpeg, listening sessions
   (resume, progress sync, chapters, sleep timer), per-child listener profiles.
5. **Audiobookshelf UI** and the multi-room now-playing bar.
6. **Apple Music via an always-on Mac** (post-bedtime): Music.app on a Mac is the licensed
   player and AirPlay 2 sender; a small agent on it takes orders from HomeDash.

Decisions already made with the user: per-child ABS profiles (not one shared account);
ffmpeg in the image for exact resume; lock is manual plus a nightly auto-lock window;
lock first, then the settings page; Apple Music through a Mac rather than a captured
headless browser.

Facts verified while planning, which the design rests on:

- **HEOS has no seek.** The CLI spec 1.16 player commands (4.2.1 to 4.2.26) have no
  set-position command and pyheos 1.0.6 has none. Resuming mid-file therefore has to be done
  by serving audio from an offset. That is why ffmpeg is in the plan.
- **ABS auth** is `Authorization: Bearer <token>`. On 2.26+ create an API key per child
  under Settings > API Keys (acts as that user); older servers use the user's token from
  Settings > Users. Progress is stored per user, which is what makes per-child profiles work.
- **ABS book shape** (`GET /api/items/{id}?expanded=1`): `media.tracks[]` with `index`,
  `startOffset` (seconds into the book), `duration`, `title`, `contentUrl`
  (`/api/items/{itemId}/file/{ino}`), `mimeType`, `metadata.ino`; `media.chapters[]` with
  `id`, `start`, `end`, `title`; `media.duration`. A single-file M4B is one track.
- **ABS podcast shape**: `media.episodes[]` each with `id`, `title`, `publishedAt`,
  `season`, `episode`, `duration`, `audioTrack.contentUrl`.
- **Progress write**: `PATCH /api/me/progress/{itemId}` and
  `PATCH /api/me/progress/{itemId}/{episodeId}`, body keys `currentTime`, `duration`,
  `progress` (0..1), `isFinished`. Read: `GET /api/me/progress/{itemId}[/{episodeId}]`
  (404 when none). Continue-listening: `GET /api/me/items-in-progress`.
- File endpoint is Express `sendFile`, so `Range` works for the plain (offset 0) proxy.
- **Apple Music cannot be proxied.** Streams are FairPlay/Widevine encrypted and HEOS has
  no Apple Music service. Music.app on macOS is scriptable (AppleScript: play a playlist,
  transport, current track, and since Catalina `AirPlay devices` with a settable
  `selected`), and every HEOS speaker since ~2018 is an AirPlay 2 target. Part 6 lists
  the two `osascript` commands that confirm this on the actual Mac and speakers.

---

## Ground rules that carry over from the existing code

These are the conventions in `ARCHITECTURE.md` that every part below follows; read that
file's `music/` section first.

- New backend package mirrors `app/music/` and `app/photos/`: `base.py` (protocol +
  dataclasses), a client with an injectable `get` seam for tests, a `service.py` of
  process-wide singletons gated by `*_configured()` functions, and a router in
  `app/api/routes/`. Routers register on the shared `router` in `api/routes/__init__.py`.
- No response models; handlers return plain dicts. Request bodies are Pydantic models.
- Upstream failures are 502, "not configured" is 503, missing speaker is 404.
- Settings follow the JSON-settings pattern in `config.py` (`Annotated[..., NoDecode]` +
  `_parse_json` validator with an example string). Tests inject `Settings(_env_file=None, ...)`.
- Frontend: Svelte 5 runes, callback props, `.svelte.ts` factories for shared reactive
  state, `homedash:`-prefixed localStorage with try/catch, inline SVG only (no emoji, no
  icon fonts), 48px tap targets, every colour from `theme.css` tokens. New screens are
  **states inside the SPA**, never routes (the kiosk allowlist is one URL).
- Every new fetch on `+page.svelte` goes through `reloadEverything` or `guard`.
- Update `ARCHITECTURE.md` in the same commit as each structural change, and add a
  "Landed after v1" entry to `CLAUDE.md` per part.

---

## Part 1: Panel lock

### Behaviour

- **Locked** means: the calendar stays visible and live, but nothing on the panel responds
  to touch. Music keeps playing. Tapping anywhere brings up the PIN pad. Correct PIN unlocks.
- **Manual**: a lock button in the controls row (visible only when a PIN is set).
  Tap locks immediately, no PIN needed. Stays locked until unlocked.
- **Bedtime window** (`HOMEDASH_LOCK_SCHEDULE`, e.g. `{"from": "19:30", "to": "06:30"}`;
  editable on the settings page once Part 2 lands): inside the window the panel is locked
  unless recently unlocked. An unlock inside the window lasts `HOMEDASH_LOCK_UNLOCK_MINUTES`
  (default 10), then it re-locks by itself. Outside the window, only
  a manual lock applies.
- **Lock after play**: the confirmation shown after starting an audiobook (Part 5) offers a
  "Lock the panel" button, so the bedtime flow is pick, send, lock, one tap each.
- Unlock is global. A parent opening the same URL on a phone sees the lock too and can
  unlock from there, which unlocks the wall as well. Per-client unlock is out of scope.
- The lock stops fingers, not curl. The write routes stay unauthenticated, same as today
  (open decision 3 in `CLAUDE.md`). Say so in the docs.

### Backend

**`app/kv.py`** — the first use of the `settings` table (`models.py:80`):
`get_json(session, key, default)`, `set_json(session, key, value)`, `delete(session, key)`.
Reads are tolerant the way `schedule_of` (`devices.py:58`) is: bad JSON logs and returns
the default. Part 2 builds on it.

**`app/config.py`** — new settings:

```python
class LockScheduleConfig(BaseModel):
    from_: str = Field(alias="from")   # "HH:MM", validated by _normalize_hh_mm
    to: str
    def active_at(self, moment: time) -> bool  # same overnight-wrap logic as ScreenWindow.lit_at

lock_pin: str = ""                       # empty = lock feature off
lock_schedule: Annotated[LockScheduleConfig | None, NoDecode] = None
lock_unlock_minutes: int = 10
```

Reuse `_normalize_hh_mm` (`config.py:71`) and copy the three-line wrap logic from
`ScreenWindow.lit_at` (`config.py:99`). Do not subclass `ScreenWindow`; its `on == off`
special case means the opposite thing here.

**`app/pin.py`** — `current_pin(session) -> str` (kv row `lock_pin`, falling back to
`settings.lock_pin`; Part 2 is what writes the row), `verify(session, candidate) -> bool`
using `secrets.compare_digest`, and `PinGate`: an in-process counter, 5 wrong attempts then
refuse for 30 seconds, `now` injectable; `check() -> retry_after_seconds | None`,
`record_failure()`, `record_success()`. The settings route reuses all of it in Part 2.

In this part `lock.py` reads the schedule and unlock minutes from `deps.settings`; Part 2
switches that one call to `preferences.load(session)`.

**`app/lock.py`** — all arithmetic server-side, `now` passed in:

```python
LOCK_KEY = "panel_lock"          # kv row
@dataclass
class LockRecord: manual: bool = False; unlocked_until: datetime | None = None

def load_record(session) -> LockRecord          # via kv.get_json, tolerant
def save_record(session, record) -> None
def lock_state(record, schedule, unlock_minutes, pin_set: bool, now: datetime, tz) -> dict:
    # {"enabled": pin_set, "locked": bool, "reason": "manual"|"schedule"|None,
    #  "relocks_at": iso|None}
    # locked if record.manual; else if schedule and it is active at local now
    # and (unlocked_until is None or now >= unlocked_until); else unlocked.
def lock(session, now) -> dict
def unlock(session, now, unlock_minutes) -> dict   # manual=False, unlocked_until=now+minutes
```

**`app/api/routes/lock.py`**:

| Endpoint | Body | Answers |
|---|---|---|
| `GET /api/lock` | | `lock_state` |
| `POST /api/lock/lock` | | `lock_state`; 404 if no PIN set |
| `POST /api/lock/unlock` | `{pin}` | `lock_state`; 403 wrong PIN; 429 `{retry_after_seconds}` while gated |

Both writes publish `lock.updated` (no payload; panel re-reads `/api/lock`).

**Heartbeat** (`scheduler.py:heartbeat_data`) gains `"locked": bool` computed the same way,
so a panel that missed the SSE event is right within 30 seconds and the scheduled re-lock
needs no timer anywhere: the state is *computed* from `unlocked_until` at read time.

### Frontend

- `lib/lock.svelte.ts` — `createLock()` factory: `enabled`, `locked`, `relocksAt`,
  `load()`, `lock()`, `unlock(pin) -> Promise<'ok'|'wrong'|{retryAfter}>`, plus
  `applyHeartbeat(locked)`. Same shape as `music.svelte.ts`.
- `components/PinPad.svelte` — shared with the settings page in Part 2. Four to eight
  dots, a 3x4 keypad with 64px keys, backspace and cancel as inline SVG, `onSubmit(pin)`,
  an `error` prop that drives a shake and clears, and a `retryAfter` prop that shows "Try
  again in 30 s" with a countdown.
- `components/PanelLock.svelte` — `position: fixed; inset: 0; z-index: 60`, transparent,
  `pointer-events: all`. First tap shows a centred card on `--paper-raised` with `PinPad`
  and, when the lock came from the schedule, one line: "Locked until 06:30". Card dismisses
  after 20 s without input.
- Masthead: a small lock glyph (inline SVG, `--ink-muted`) next to the date while locked.
- Controls row (`+page.svelte:330`): a `control-round` lock button after the view
  switcher, rendered only when `lock.enabled`. Part 2 adds the gear beside it.
- Heartbeat handler: `lock.applyHeartbeat(heartbeat.locked)`; SSE `lock.updated` →
  `guard(lock.load())`; add `'lock.updated'` to the listener list in `api.ts`.
- Z-order stays coherent: calendar < music slot (20) < music overlay (40) <
  **PanelLock (60)** < Screensaver (100) < PanelBlank (200). The lock covers an open music
  overlay (visible, inert); Part 2's settings overlay sits at 40 as well. The screensaver
  still starts when idle and its dismiss tap lands on the locked calendar. `idle.ts` listens on `window` with capture, so
  pokes at the lock layer count as activity, which is fine.

### Tests

- `test_lock.py`: window wrap (19:30 to 06:30 at 23:00 and at 03:00 and at 12:00), manual
  beats everything, unlock inside window expires after N minutes, unlock outside window is
  simply unlocked, no PIN means never locked, bad JSON in the KV row falls back to unlocked
  and logs.
- `test_pin.py`: `PinGate` refuses on the sixth attempt and recovers after 30 s (inject
  `now`); success resets the counter; `current_pin` falls back to env until a row exists.
- `test_lock_api.py`: 404 when no PIN set, 403 on wrong PIN, 429 with
  `retry_after_seconds`, heartbeat carries `locked`.
- Harness: `tools/panel/lock-shot.mjs` — stub `/api/lock` locked, screenshot both
  orientations, tap, screenshot the pad, verify a calendar button click does nothing.

---

## Part 2: Settings page

### The model: two tiers

- **Deployment config stays in env**: anything that is a secret, a network address, a
  path, the timezone, or needs a restart. Shown read-only on the page with the env var
  name as the hint, so the page doubles as the map of what is configured.
- **Household preferences move to the database**, editable live, with the env value as
  the *default*: screen schedule (on/off/weekend), lock schedule and unlock minutes, the
  PIN, screensaver idle and dwell, week start, temperature unit.

This changes the reconcile contract for one thing: `seed_device_from_settings`
(`devices.py:39`) currently overwrites `screen_schedule` from env on every boot. It must
become seed-if-empty. Document in `.env.example` and `CLAUDE.md`: once a panel has saved a
schedule, the env value only applies via "Reset to default" on the page.

### Backend

Builds on Part 1's `kv.py`, `pin.py`, and `PinPad.svelte`. Nothing new in `config.py`:
every preference already has an env default.

**`app/preferences.py`**:

```python
class Preferences(BaseModel):
    screen_schedule: ScreenScheduleConfig
    lock_schedule: LockScheduleConfig | None
    lock_unlock_minutes: int = Field(ge=1, le=240)
    screensaver_idle_minutes: int = Field(ge=1, le=120)
    screensaver_dwell_seconds: int = Field(ge=5, le=600)
    week_starts_on: Literal["sunday", "monday"]
    weather_temperature_unit: Literal["fahrenheit", "celsius"]

def defaults() -> Preferences                     # from Settings
def load(session) -> Preferences                  # defaults overlaid with settings-table rows (kv.get_json per key)
def overridden_keys(session) -> list[str]
def save(session, patch: dict) -> Preferences     # validates the merged model, writes only the patched keys
def reset(session, keys: list[str])
def set_pin(session, new_pin: str)                # 4 to 8 digits, validated; writes the kv row pin.py reads
```

The PIN is a household PIN; hashing adds nothing against a keypad, so it is stored plain
and the docstring says so.

Consumers switch from `deps.settings.x` to `preferences.load(session).x`: the calendar
router for `week_starts_on`, the photos router for idle/dwell, `lock.py` for the schedule
and unlock minutes, and `weather/client.py` for the unit (a unit change kicks
`refresh_weather()` in the executor and publishes `weather.updated`). The screen schedule keeps living on the `devices` row; `preferences.save`
writes it there and the heartbeat already reads it from there.

**`app/api/routes/settings.py`**:

| Endpoint | Body | Returns |
|---|---|---|
| `GET /api/settings` | | `{preferences, defaults, overridden, pin_set, deployment}` |
| `PUT /api/settings` | `{pin?, preferences?: partial, new_pin?, reset?: [keys]}` | the same shape; 403 wrong PIN when one is set; 429 `{retry_after_seconds}` while gated (Part 1's `PinGate`); 422 on invalid values |

`deployment` is read-only status: timezone; calendars with kind, `last_synced_at`,
`last_full_sync_at`; music `{enabled, heos_host, connected, player_count, jellyfin}`;
listen `{configured, listeners: [names], ffmpeg_found}` (503-shaped "not configured"
until Part 4 exists); photos `{dir, indexed, errors}` (counts from the `photos` table);
weather `{configured, fetched_at}`; device `last_seen`; comets enabled. Every value that is
a secret is reported as `set: true/false` only.

Writes publish `settings.updated`; the panel re-reads `/api/settings`, `/api/photos`
(new idle/dwell), and the grid (week start). A save that touches the lock schedule or
unlock minutes also publishes `lock.updated`.

### Frontend

- Gear button (`control-round`, inline SVG) in the controls row beside Part 1's lock
  button, always visible. Opening the overlay asks for the PIN via `PinPad`
  when `pin_set`; the PIN is held in component memory for the overlay's lifetime and sent
  with each `PUT`.
- `components/SettingsOverlay.svelte`, `z-index: 40` (exclusive with the music overlay;
  opening one closes the other). A two-column layout in landscape, one column in portrait,
  scrolling inside. Sections, each a ruled block with a `.caps` heading, fields, and a
  "Reset to default" text button when the section has overrides:
  1. **Screen** — on / off times (native `<input type="time">`, styled to the tap height;
     Chromium's picker works on touch), weekend override toggle with its own pair.
  2. **Lock** — schedule from / to with an "Enabled" toggle, unlock minutes as a stepper,
     "Change PIN" (asks current, then new twice), or "Set a PIN" when none is set. Setting
     a PIN here is what turns the lock button on.
  3. **Screensaver** — idle minutes and dwell seconds as steppers.
  4. **Calendar** — week starts on (segmented `tab` pair).
  5. **Weather** — unit (segmented pair).
  6. **Status** (read-only) — the `deployment` block as label/value rows, env var names in
     `--ink-muted` beneath each, green/red inline SVG dot for connected/configured.
- Save is per section (a filled pill), with a one-line "Saved" confirmation; failures show
  the server's `detail`.
- `lib/settings.svelte.ts` factory with `load()`, `save(sectionPatch)`, `reset(keys)`,
  `changePin`. Add `'settings.updated'` to the SSE listener list in `api.ts:398`.

### Tests

- `test_preferences.py`: defaults from env, overlay wins, bad row tolerated, `save` rejects
  out-of-range, `reset` removes rows, seed-if-empty for the device row, `set_pin` is what
  `pin.current_pin` reads next.
- `test_settings_api.py`: 403 without PIN when set, 429 with `retry_after_seconds`, PUT
  round-trips, secrets never appear, unit change triggers a weather refresh (monkeypatch
  `refresh_weather`), `settings.updated` published.
- Harness `tools/panel/settings-shot.mjs`: both orientations, the PIN prompt, all six
  sections rendered.

---

## Part 3: Queue generalisation (refactor, no behaviour change)

Today the chain is Jellyfin-shaped end to end: `Track` (`music/base.py`) →
`QueueManager.url_for(track)` → `TokenStore.mint(track_id)` → `/api/music/s/{token}` →
`JellyfinLibrary.open_stream(track_id)`. Part 4 needs the same queue to play "ABS item X,
file Y, from second Z". Three small changes:

1. **`StreamRef`** in `music/base.py`:
   ```python
   @dataclass(frozen=True)
   class StreamRef:
       source: Literal["jellyfin", "audiobookshelf"]
       item_id: str                 # Jellyfin track id, or ABS library item id
       file_id: str | None = None   # ABS: the audio file's ino
       start_seconds: float = 0.0   # serve from here (resume, chapter, seek)
       listener: str | None = None  # ABS: which configured account fetches it
   ```
   `Track` gains `stream: StreamRef | None = None`. `JellyfinLibrary.tracks()` sets
   `StreamRef("jellyfin", item["Id"])`. `service._url_for` falls back to
   `StreamRef("jellyfin", track.id)` when `stream` is None, so the 35 existing queue tests
   that build bare `Track`s keep passing.
2. **`TokenStore`** maps token → `StreamRef` (`mint(ref)`, `resolve(token) -> StreamRef |
   None`). The 255-character check is untouched. Update `test_music_tokens.py`.
3. **`GET /api/music/s/{token}`** dispatches on `ref.source`: `jellyfin` → the existing
   `library.open_stream`; `audiobookshelf` → `listen.service.open_stream(ref, range)`
   (Part 4). The streaming body generator is shared.

Two hooks the sessions need, both optional callables so nothing existing changes:

- `QueueManager.on_end: Callable[[int, str], Awaitable[None]] | None`, called inside
  `_end` after `clear()` with reason `"finished"` (speaker stopped itself) or `"stopped"`
  (skip past end, stop button). Exceptions logged, never raised. Test: both reasons fire,
  a raising callback does not prevent `clear_speaker_queue`.
- `HeosController.on_progress: Callable[[int, int], Any] | None`, awaited on
  `_PROGRESS_EVENT` with `now_playing_media.current_position` (ms). Still no SSE publish for
  progress; that decision stands. Test: progress event calls the hook and does not call
  `on_change`.

Also fix the stale docstring on `post_music_transport` (`routes/music.py:283`), which still
says URLs never enter the speaker's queue.

---

## Part 4: Audiobookshelf backend

### Config

```
HOMEDASH_AUDIOBOOKSHELF_URL=http://192.168.1.10:13378
HOMEDASH_AUDIOBOOKSHELF_LISTENERS='[{"name": "Ada", "api_key": "..."}, {"name": "Ben", "api_key": "..."}]'
HOMEDASH_AUDIOBOOKSHELF_LIBRARY_IDS=          # optional, comma-separated; empty = every book/podcast library
HOMEDASH_FFMPEG_PATH=ffmpeg                   # optional; resolved with shutil.which
HOMEDASH_LISTEN_FORCE_MP3=false               # escape hatch: transcode everything to MP3 if a speaker refuses ADTS AAC
```

`ListenerConfig(name, api_key)` via the JSON-settings pattern; names must be unique
(validator). `listen_configured()` = `music_configured()` and URL and at least one
listener. `.env.example` explains: one API key per child, created under Settings > API
Keys as that child's user, read-only if the server offers it; that is what makes each
child's "Continue listening" their own and keeps it in sync with plappa.

### Package `app/listen/`

| Module | Responsibility |
|---|---|
| `base.py` | Dataclasses: `Listener`, `Shelf`, `ListenItem` (id, kind `book`/`podcast`, title, author, series, duration, progress), `BookDetail` (tracks as `Segment(track_id=ino, start_offset, duration, mime, content_url)`, chapters), `Episode`, `Progress(current_time, is_finished)` |
| `client.py` | `AudiobookshelfClient(base_url, get=None)`; every call takes the listener's `api_key` (the client is per server, keys are per call, so one client serves every profile). Sync `get` seam for browse like Jellyfin; async httpx for progress writes and stream opening |
| `stream.py` | `open_stream(ref, api_key, range_header)`: offset 0 → plain httpx proxy exactly like `JellyfinLibrary.open_stream` (Range forwarded, `Accept-Encoding: identity`); offset > 0 → ffmpeg subprocess (below) |
| `sessions.py` | `ListeningSessions`: per-speaker book/episode position, progress sync, chapters, sleep timer. Knows nothing about HEOS or ABS beyond injected callables, like `QueueManager` |
| `service.py` | Singletons: client, sessions, wiring into `music.service` (`_url_for`, `on_end`, `on_progress`, `on_state`) |

Client calls (all under `base_url`, header `Authorization: Bearer {api_key}`):

- `libraries()`: `GET /api/libraries` → those with `mediaType` in (`book`, `podcast`),
  filtered by `LIBRARY_IDS` if set.
- `items(library_id)`: `GET /api/libraries/{id}/items?minified=1&sort=media.metadata.title&limit=500&page=N`
  paged until `total` reached. Map `media.metadata.title/authorName/seriesName`,
  `media.duration`, `media.numTracks` or `numEpisodes`.
- `in_progress(api_key)`: `GET /api/me/items-in-progress?limit=20` → items plus
  `recentEpisode` for podcasts.
- `item(id)`: `GET /api/items/{id}?expanded=1` → tracks (from `media.tracks`), chapters,
  episodes (sorted `publishedAt` desc).
- `progress(api_key, item_id, episode_id)`: `GET /api/me/progress/...`; 404 → None.
- `update_progress(api_key, item_id, episode_id, current_time, duration, is_finished)`:
  async `PATCH /api/me/progress/...` with `{currentTime, duration, progress, isFinished}`.
- `cover(item_id, width)`: `GET /api/items/{id}/cover?width=W` → `(bytes, content_type)`
  or None. Proxied at `/api/listen/cover/{item_id}?size=` with `max-age=3600`, same as
  Jellyfin art.
- Audio URL for a segment: `base_url + segment.content_url` (verbatim from ABS).

Errors: `AudiobookshelfError` → 502 at the route, with the 401 case saying "check the API
key for listener X".

### Offset streaming (`stream.py`)

When `ref.start_seconds > 0`:

```
ffmpeg -nostdin -hide_banner -loglevel error
       -headers "Authorization: Bearer {key}\r\n"
       -ss {start_seconds} -i {url}
       -vn -map 0:a:0 -c copy -f {container} pipe:1
```

`-ss` before `-i` is a demuxer seek: for M4B it reads the index and jumps, for MP3 it
estimates by bitrate (or Xing header). `-c copy` means no re-encode; CPU cost is nil.
Container and media type by source `mimeType`:

| Source mime | `-f` | Served as |
|---|---|---|
| `audio/mpeg` | `mp3` | `audio/mpeg` |
| `audio/mp4`, `audio/x-m4b`, `audio/x-m4a`, `audio/aac` | `adts` | `audio/aac` |
| `audio/flac` | `flac` | `audio/flac` |
| `audio/ogg`, `audio/opus` | `ogg` | `audio/ogg` |
| anything else, or `LISTEN_FORCE_MP3` | `-c:a libmp3lame -b:a 160k -f mp3` | `audio/mpeg` (the one path that burns CPU; log at INFO) |

The response is 200 with no `Content-Length` and no `Accept-Ranges`; a `Range` request in
offset mode is answered from the offset anyway (HEOS treats it like a radio stream). Kill
the process in the generator's `finally` (`proc.kill()` then `await proc.wait()`), read
stderr on non-zero exit into the log. `build_command(ref, url, key, mime)` is a pure
function so the mapping is unit-testable without ffmpeg.

If `shutil.which(ffmpeg_path)` is None: warn once at startup ("resume will land on track
boundaries"), and `sessions.start` rounds the resume point down to the containing
segment's start.

`-headers` puts the key in the container's process list. Acceptable for a LAN appliance
whose API is unauthenticated; state it in the module docstring.

**Dockerfile**: `RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg
&& rm -rf /var/lib/apt/lists/*` in the backend stage, before `uv sync`.

### Listening sessions (`sessions.py`)

```python
@dataclass
class ListeningSession:
    player_id: int; listener: str; kind: "book"|"podcast"
    item_id: str; episode_id: str | None
    title: str; author: str | None; series: str | None; cover_url: str
    duration: float                      # seconds, whole book or episode
    chapters: list[Chapter]              # may be empty
    segments: list[Segment]              # audio files in book order
    served_from: float = 0.0             # absolute seconds where the stream now playing began
    speaker_position: float = 0.0        # seconds the speaker reports into that stream
    current_track_id: str | None = None
    last_synced_at: float | None = None  # monotonic
    sleep_until: float | None = None; sleep_task: asyncio.Task | None = None
    def position(self) -> float: return served_from + speaker_position
    def chapter_at(self, t) -> (index, Chapter) | None
```

`ListeningSessions(queues: QueueManager, sync: Callable, now=time.monotonic)`; one
`asyncio.Lock` per player like the queue.

- **`start(player_id, listener, item_id, episode_id=None, from_seconds=None, chapter_index=None)`**:
  fetch detail and (unless `from_seconds` given) progress; `is_finished` → 0. Find the
  segment containing `t`; build `Track`s: the first with
  `StreamRef("audiobookshelf", item_id, file_id=ino, start_seconds=t - seg.start_offset,
  listener=name)`, the rest from 0. `Track.title` = chapter title or segment title,
  `Track.artist` = author, `Track.album` = book title, `Track.album_id` = item_id (so the
  Jellyfin-shaped `serialize_now_playing` fallback still renders something sensible).
  Then `queues.start(pid, tracks)`; `served_from = t`. Replaces any session or music queue
  on that speaker.
- **Track advance detection**: on `on_state(pid, "play")` if `queues.current(pid).id !=
  session.current_track_id`, set `served_from = seg.start_offset + track.stream.start_seconds`
  and `speaker_position = 0`.
- **`on_progress(pid, ms)`**: `speaker_position = ms / 1000`; sync if ≥ 30 s since last.
- **`on_state(pid, "pause"|"stop")`**: sync now (skip if the queue is `awaiting_start`,
  which is the gap before a track, not a position).
- **`on_end(pid, reason)`**: `is_finished = reason == "finished" and duration - position() < 60`;
  final sync; cancel sleep; drop the session.
- **`seek(pid, to_seconds)`** (also used for ±30 s and chapter jumps): clamp to
  `[0, duration - 1]`, rebuild tracks from there, `queues.start(pid, tracks)` (a replace,
  which the queue already serialises), sync. Roughly one second of silence, same as a
  track change.
- **`set_sleep(pid, seconds | None)`**: cancel the old task; new task sleeps, then syncs and
  calls `queues.stop_and_release(pid)`. "End of chapter" is computed by the route as
  `chapter.end - position() + 1`.
- **`snapshot(pid)`** → `{listener, kind, item_id, episode_id, title, author, series,
  cover_url, position_seconds, duration_seconds, chapter: {index, count, title} | null,
  sleep_ends_in_seconds | null}`.
- Sync failures log at WARNING and never raise; playback must not stop because ABS is
  down. A restart loses at most 30 s of progress (in-process state, like the music queue).

### Routes `app/api/routes/listen.py`

| Endpoint | Body / query | Returns |
|---|---|---|
| `GET /api/listen/listeners` | | `[{name}]` (never keys) |
| `GET /api/listen/home?listener=` | | `{continue: [ListenItem+progress], shelves: [{library_id, name, kind, items}]}` |
| `GET /api/listen/items/{id}?listener=` | | detail: chapters, progress, episodes with per-episode progress |
| `GET /api/listen/cover/{id}?size=` | | proxied image |
| `POST /api/listen/players/{pid}/play` | `{listener, item_id, episode_id?, from_seconds?, chapter_index?, sleep_minutes?}` | `{ok, resumed_from_seconds}` |
| `POST /api/listen/players/{pid}/seek` | `{delta_seconds?}` or `{to_seconds?}` or `{chapter_index?}` | `{ok, position_seconds}` |
| `POST /api/listen/players/{pid}/sleep` | `{minutes?}` or `{end_of_chapter: true}` or `{clear: true}` | `{ok, sleep_ends_in_seconds}` |

`GET /api/music/players` adds `"listening": sessions.snapshot(pid)` per player, and when a
session exists `now_playing` is built by a new `serialize_listening_now_playing` in
`api/serializers.py`: title = book (or episode) title, artist = author (or podcast name),
album = current chapter title, image = `/api/listen/cover/{item_id}`, `duration_ms` and
`position_ms` at **book level** so the existing `NowPlaying.svelte` progress bar shows
3:14:20 / 10:05:11 unchanged. A speaker with a listening session also answers the plain
transport route: `play`/`pause`/`stop` as today; `next`/`previous` mean chapter forward and
back when a session exists (route checks sessions before queues).

`lifespan`: `start_listen()` after `start_music()`; `stop_listen()` syncs every open
session best-effort before the controller disconnects.

### Tests

- `test_listen_client.py` (fake `get`): libraries filtered to book/podcast, paging, detail
  mapping (tracks with `startOffset`, chapters, episodes desc), progress 404 → None, 401
  names the listener, `update_progress` body shape (fake async transport via
  `httpx.MockTransport`, as `test_music_stream.py` does).
- `test_listen_stream.py`: `build_command` mapping per mime and `FORCE_MP3`; offset 0
  uses the plain proxy and forwards Range; ffmpeg missing → fallback flag set.
- `test_listen_sessions.py` (the important file, like `test_music_queue.py`): resume lands
  in the right segment with the right `start_seconds`; a 3-file book at 3700 s starts file 2
  at 100 s; position math across a track advance; sync throttled to 30 s and forced on
  pause; `is_finished` only within 60 s of the end; seek rebuilds from the new point;
  sleep timer stops and syncs; sync failure does not stop playback; `is_finished` progress
  resumes from 0.
- `test_listen_api.py`: 503 unconfigured, listeners never expose keys, play validates
  listener name, seek clamps, `next` on a session speaker is a chapter jump, `players`
  carries `listening` and the book-level now-playing.

### Hardware checks (cannot be done from here; list them in the commit message)

1. `homedash-heos-probe --url` against an offset stream: confirm the speaker plays ADTS
   AAC from an M4B. If it refuses, set `HOMEDASH_LISTEN_FORCE_MP3=true` and note it.
2. Play a book for two minutes, stop, open plappa as that child: position matches within
   30 s.
3. Three speakers, three sources at once; skip a chapter on one, confirm the others do
   not stutter.

---

## Part 5: Audiobookshelf UI and the multi-room bar

**Types and fetchers** in `api.ts`: `ListenItem`, `ListenDetail`, `Chapter`,
`ListenEpisode`, `Listening` (on `MusicPlayer.listening`), `fetchListeners`, `fetchListenHome`,
`fetchListenItem`, `coverUrl`, `playListen`, `seekListen`, `setSleep`.

**`lib/listen.svelte.ts`** — `createListen()`: `listeners`, `listener` (remembered in
localStorage `homedash:listener`, validated against the list the way `pickPlayer` does),
`selectListener`, `play(...)`, `seek`, `sleep`. Commands go through the same `run()`
pattern as music (swallow failures, refetch players).

**Overlay tabs** (`MusicOverlay.svelte`): `Now Playing | Music | Books`. "Books" only when
`listen` is configured. Default tab stays derived: Now Playing if the selected speaker is
playing, else Books if configured, else Music. Renaming the component `PlaybackOverlay` is
optional; keep the file if renaming churns tests.

**`components/ListenBrowser.svelte`** — the kids' screen, three levels with the same
history-stack model as `MusicBrowser`:

1. **Home**. Header: "Listening as *Ada*" with a change button that shows the profile
   picker (`ListenerPicker.svelte`: big name tiles, 96px tall, shown automatically on
   first use). Then a horizontal "Continue listening" row of covers with a thin progress
   rule under each (`--ink` on `--wash`, book-level fraction) and "2 h 10 m left". Then
   one section per shelf ("Audiobooks", "Podcasts"), covers in a grid
   (`minmax(170px, 1fr)` like the album grid), title and author beneath. Above ~40 items
   a section gets the A–Z rail; extract the rail from `MusicBrowser` into
   `AlphaRail.svelte` (`letterAt`, `jumpTo`, scrub) and use it in both.
2. **Book**. Cover left, title/author/series/duration right, the resume line ("You're at
   3:14 of 10:05, chapter 12"). Primary filled pill **Resume** (or **Start** when no
   progress); secondary **Start over**. Chapter list below with the current one marked,
   each row a tap target that plays from that chapter. Sleep chips above the buttons:
   `15 · 30 · 45 · 60 · End of chapter`; the chosen chip is sent with the play request
   and shown on Now Playing.
3. **Podcast**. Episode list newest first: title, date, duration, a progress rule, played
   episodes in `--ink-ghost` with the `.struck` treatment. Tap plays (resumes if partly
   played).

**Room chooser** (`RoomChooser.svelte`). When the household has more than one speaker, a
play tap opens a bottom sheet: "Play in…" with one 64px button per speaker (name, and
"playing X" in `--ink-muted` if busy). One speaker plays immediately. The chosen speaker
becomes the selected player. After the request succeeds, the sheet becomes a confirmation
for 4 s: cover, "Playing in Bedroom", and two buttons: **Done** and **Lock the panel**
(calls `lock.lock()`). Part 6 reuses this component.

**Now Playing** for a session (`MusicOverlay` body): under the existing `NowPlaying`,
replace the "Track n of m" line with "Chapter 12 of 30 · Sleep in 23 min" and add a row of
four `control-round` buttons: `−30 s`, previous chapter, next chapter, `+30 s` (inline
SVG, label text under each). Sleep chips repeat here so the timer can be changed.

**Multi-room bar.** `NowPlayingBar.svelte` currently shows the selected speaker only.
Change it to render **one row per speaker that is playing or paused**, ordered by name,
each: 52px cover, room name in `.caps`, title, subtitle (chapter or artist), and a compact
play/pause. Tapping the row selects that speaker and opens the overlay. `music.svelte.ts`
gains `activePlayers` (state play or pause) and `barVisible` becomes
`activePlayers.length > 0`. Three rows on a 1080-tall panel is 3 × ~64px; acceptable, and
the bar is sticky so month view is unaffected. The screensaver caption keeps showing the
*selected* speaker only.

**Harness** `tools/panel/listen-shot.mjs`: stubbed home/detail/players with two speakers,
both orientations; screenshots of home, book, room chooser, now playing, and a three-row
bar.

---

## Part 6 (post-bedtime): Apple Music through an always-on Mac

### Why a Mac

Apple Music cannot be proxied the way Jellyfin and ABS are: the streams are encrypted and
only Apple's players decrypt them. A Mac running Music.app is a licensed player and a
native AirPlay 2 sender, and every HEOS speaker here is an AirPlay 2 target. Music.app is
scriptable, including which AirPlay devices it plays to. So HomeDash does not touch audio
at all for this source: it tells the Mac what to play and where, and reads back what is
playing. No DRM workaround, no audio capture, no headless browser.

**Limits, stated up front:**

- **One stream per Mac.** Music.app plays one thing at a time. It can send that to several
  speakers, but they hear the same audio. Together with Parts 4 and 5 that gives Jellyfin
  and ABS in any rooms plus Apple Music in one room at a time.
- **The Mac stays logged in.** Auto-login, sleep disabled, Music signed in, the automation
  permission granted once. Screen lock is fine; playback continues. A macOS or Music
  update dialog will occasionally need a human.
- **AirPlay 2 adds about two seconds** before a change is heard. The panel responds at once
  because the Mac pauses locally.

**Confirm on the hardware before building anything:**

```
osascript -e 'tell application "Music" to get name of AirPlay devices'
osascript -e 'tell application "Music"
  set selected of AirPlay device "Bedroom" to true
  play playlist "Bedtime"
end tell'
```

The HEOS speakers must appear in the first list under the names HEOS uses (they are the
same devices, so they normally do), and the second must play in the right room.

### The agent: `deploy/mac/`

Mirrors `deploy/pi/screen_agent.py`: stdlib only, CLI flags baked into a launchd plist,
no configuration of its own beyond a bind address and a shared token.

| File | Role |
|---|---|
| `music_agent.py` | `http.server` on the LAN (default port 8765), wraps `osascript` |
| `com.homedash.music-agent.plist` | launchd **user** agent (`~/Library/LaunchAgents`), `KeepAlive`, so it runs in the logged-in session that owns Music.app and the automation permission |
| `setup.sh` | installs the plist, prints the permission prompt to expect, runs the two checks above |
| `README.md` | the always-on checklist (auto-login, no sleep, Music signed in), recovery, how the token is set |

Endpoints, all JSON, all requiring `X-HomeDash-Token` when a token is configured:

| Endpoint | Body | Returns |
|---|---|---|
| `GET /status` | | `{music_running, state: play\|pause\|stop, track: {id, name, artist, album, duration_s, position_s} \| null, playlist: {id, name} \| null, volume, airplay: [{name, selected, volume}]}` |
| `GET /playlists` | | `[{id, name, count}]` — user playlists by `persistent ID` |
| `GET /playlists/{id}/tracks` | | `[{id, name, artist, album, duration_s}]` |
| `GET /artwork` | | current track's artwork bytes with its content type, 404 if none |
| `POST /play` | `{playlist_id, airplay: [names], shuffle?}` | selects exactly those AirPlay devices, plays the playlist |
| `POST /transport` | `{action: play\|pause\|next\|previous\|stop}` | |
| `POST /airplay` | `{devices: [names]}` | reselects outputs without changing playback |
| `POST /volume` | `{level}` | Music.app's own volume; per-speaker volume stays on HEOS |

Every handler builds an AppleScript string in a pure function (`script_for_play(...)`) and
runs it with `subprocess.run(["osascript", "-e", ...])`, parsing a delimiter-separated
result into JSON. Music not running: the agent sends `launch` first. A permission error
(`-1743`) returns 500 with the exact System Settings path to fix it. Playlist ids are
`persistent ID`s so a rename does not orphan the panel's configured list.

### HomeDash side

Config:

```
HOMEDASH_APPLE_MUSIC_AGENT_URL=http://192.168.1.20:8765
HOMEDASH_APPLE_MUSIC_AGENT_TOKEN=
HOMEDASH_APPLE_MUSIC_PLAYLISTS='["Bedtime", "Car songs"]'      # names; empty = every playlist the agent lists
HOMEDASH_APPLE_MUSIC_SPEAKER_MAP='{"Kids room": "Kids Room HEOS"}' # optional, HEOS name -> AirPlay name when they differ
```

- `app/music/applemusic.py`: `AppleMusicClient(base_url, token, get=None, post=None)`,
  same seam pattern as Jellyfin, `AppleMusicError` → 502.
- `app/music/service.py`: `apple_configured()` (requires `music_configured()` and the URL),
  the client singleton, and an `AppleOwnership` record: the set of HEOS player ids the Mac
  currently plays to, set when HomeDash starts a playlist on them, cleared on stop, when
  the agent reports `stop`, or when `QueueManager.start` takes that speaker for Jellyfin
  or ABS (HomeDash then also tells the agent to deselect that device).
- **Polling, not push.** The agent has no events, so while the Mac owns at least one
  speaker HomeDash polls `/status` every 5 s from an asyncio task and publishes
  `music.updated` when anything changed. Nothing is polled when the Mac owns nothing.
- `GET /api/music/players`: for an owned speaker, `source: "apple"` and `now_playing`
  from the agent (title, artist, album, image `/api/music/apple/art`, duration and
  position in ms, from a new `serialize_apple_now_playing`). Other speakers are untouched.
- Routes:

| Endpoint | Body | Returns |
|---|---|---|
| `GET /api/music/apple/playlists` | | the configured subset of the agent's list, in configured order |
| `GET /api/music/apple/art` | | proxied current artwork, `max-age=30` |
| `POST /api/music/apple/players/{pid}/play` | `{playlist_id, shuffle?}` | `{ok}`; 409 `{playing_on: [names]}` if the Mac is already playing to a different speaker, unless `take_over: true` |
| existing transport route | | forwarded to the agent when the Mac owns that speaker; volume stays on HEOS |

- Panel: the Music tab gains a **Playlists** row above the artists (text tiles, 96px tall,
  playlist name in `--font-display`; Music.app playlist artwork is unreliable, so no
  covers). Tap → `RoomChooser` from Part 5, which marks the room the Mac is already
  playing to and says "will move Apple Music here" when another is chosen. Now Playing
  renders through the existing components with the agent's metadata; skip buttons go to
  the agent. The lock, the screen schedule, and the multi-room bar work unchanged.

### Tests

- `deploy/mac/test_music_agent.py` (stdlib `unittest`, run from that directory like the
  Pi agent's tests if any): the AppleScript builders are pure functions; assert exact
  script text for play-with-devices, transport, and status; result parsing for a track,
  for no track, and for the permission error.
- `test_applemusic_client.py` (fake `get`/`post`): status mapping, playlist filtering by
  configured names and order, token header sent, 502 on failure.
- `test_apple_ownership.py`: ownership set on play, cleared on stop and on agent-reported
  stop, cleared and agent told when a Jellyfin queue takes the speaker, poll task starts
  and stops with ownership.
- `test_apple_api.py`: 503 unconfigured, 409 with `playing_on` when busy, take-over,
  transport forwarding for owned speakers only, `players` carries `source: "apple"`.
- Harness `tools/panel/apple-shot.mjs`: playlists row and the room chooser in both
  orientations.

### Hardware checks

1. The two `osascript` commands above.
2. Start a playlist from the panel to a bedroom speaker; pause from the panel; confirm the
   room goes quiet within ~2 s and the bar shows paused.
3. Start Jellyfin on that same speaker from the panel; confirm Music.app deselects it and
   the room switches sources cleanly.

---

## Other improvements considered

Folded in because they are cheap and serve the bedtime case: sleep timer, "Lock the panel"
after play, ±30 s, the status section on the settings page, the A–Z rail extracted for
reuse.

Deliberately left out, with the reason:

- **HEOS multiroom grouping.** Different rooms want different audio here; grouping is the
  opposite need.
- **Auto-playing the next podcast episode.** Bedtime wants one episode and silence.
- **Per-client unlock and auth on the write routes.** Open decision 3; the lock answers the
  household's actual problem (fingers on the wall) without a session model.
- **ABS WebSocket events.** A 15-minute-old cover grid is fine; progress is HomeDash's own.
- **Gapless / seek without a gap.** HEOS has no seek; one second of silence per jump is
  inherent, stated in the docs.
- **Apple Music through a captured headless browser.** Real Chrome with Widevine, a
  PulseAudio null sink, ffmpeg encoding the monitor, and 2FA sign-in decay every few
  months. It works, it is ~600 MB of image and a supervised process tree, and the Mac route
  makes it unnecessary.
- **Apple Music through HomeDash's own MusicKit page.** Cleaner control than automating
  music.apple.com, but it has the same capture problem and costs a developer account.

---

## Sequencing and files

Six PRs, each leaving both gates green:

1. **Lock** — `kv.py`, `config.py`, `pin.py`, `lock.py`, `routes/lock.py`,
   `scheduler.py` (heartbeat), `api.ts`, `lock.svelte.ts`, `PinPad.svelte`,
   `PanelLock.svelte`, `+page.svelte`, tests, `lock-shot.mjs`, docs.
2. **Settings page** — `preferences.py`, `devices.py` (seed-if-empty),
   `routes/settings.py`, consumers (`routes/calendar.py`, `routes/photos.py`,
   `weather/client.py`, `lock.py`), `api.ts`, `settings.svelte.ts`,
   `SettingsOverlay.svelte`, `+page.svelte`, tests, `settings-shot.mjs`, docs.
3. **Queue generalisation** — `music/base.py`, `music/tokens.py`, `music/queue.py`
   (`on_end`), `music/heos.py` (`on_progress`), `music/service.py`, `music/jellyfin.py`,
   `routes/music.py`, `fake_heos.py` (progress event), existing tests updated, docs.
4. **Listen backend** — `config.py`, `app/listen/*`, `routes/listen.py`, `serializers.py`,
   `main.py` lifespan, `Dockerfile`, `.env.example`, tests.
5. **Listen UI + multi-room bar** — `api.ts`, `listen.svelte.ts`, `music.svelte.ts`,
   `ListenBrowser.svelte`, `ListenerPicker.svelte`, `AlphaRail.svelte` (extracted from
   `MusicBrowser.svelte`), `RoomChooser.svelte`, `MusicOverlay.svelte`, `NowPlayingBar.svelte`,
   `+page.svelte`, `listen-shot.mjs`, `theme.css` only if a new token is genuinely needed.
6. **Apple Music via Mac** — `deploy/mac/*`, `config.py`, `music/applemusic.py`,
   `music/service.py`, `routes/music.py` (or a new `routes/apple.py`), `serializers.py`,
   `api.ts`, `MusicBrowser.svelte` (playlists row), tests, `apple-shot.mjs`, docs. Do not
   start it until the two `osascript` checks have passed on the real Mac.

Part 2 depends on Part 1 (PIN pad, gate, kv). Parts 3 to 5 depend only on each other in
order and can proceed in parallel with Part 2. Part 6 depends on Part 5 for `RoomChooser`
and the multi-room bar.

Existing code to reuse rather than rewrite: `ScreenWindow.lit_at` wrap logic
(`config.py:99`), `_normalize_hh_mm` (`config.py:71`), `_parse_json` (`config.py:315`),
`schedule_of`'s tolerant read (`devices.py:58`) as the model for every KV read,
`JellyfinLibrary.open_stream` as the template for the plain ABS proxy
(`music/jellyfin.py:228`), the streaming body generator in `routes/music.py:254`,
`QueueManager` locking and `awaiting_start` (`music/queue.py`), `pickPlayer` fallback
pattern (`musicPreference.ts:35`) for the remembered listener, `createMusic`'s `run()`
(`music.svelte.ts:71`), the A–Z rail (`MusicBrowser.svelte:97-132`), and
`deploy/pi/screen_agent.py` as the template for the Mac agent.

## Verification

```bash
cd backend  && uv run pytest && uv run ruff check .
cd frontend && npm run check && npm run build
node tools/panel/panel.mjs smoke
node tools/panel/lock-shot.mjs /tmp/lock && node tools/panel/settings-shot.mjs /tmp/settings \
  && node tools/panel/listen-shot.mjs /tmp/listen     # read the PNGs at both orientations
docker compose up --build                             # then: docker compose exec homedash ffmpeg -version
```

End-to-end against real services (on the LAN, not from a sandbox): set the ABS env vars,
pick a book on the panel, send it to a bedroom speaker, lock the panel, wait past the
sleep timer, open plappa as that child and confirm the position; then unlock from a phone
and check the wall unlocks. For Part 6: the three hardware checks listed there.
