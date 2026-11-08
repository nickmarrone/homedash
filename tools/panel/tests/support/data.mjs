// The canned household. A fixed Monday, with "now" mid-afternoon so there are
// events on both sides of it. Every date comes from the server in this app, so
// the browser's own clock never has to agree with any of this.
//
// The shapes are checked against the real API in real-backend.spec.mjs, which
// is what stops this file drifting into describing an API that no longer exists.

export const TODAY = '2026-08-24';
export const NOW = `${TODAY}T15:30:00`;

export const FAMILY = { id: 1, name: 'Family', color: '#1f5f4e' };
export const ADA = { id: 2, name: 'Ada', color: '#8a3a12' };
export const CALENDARS = [FAMILY, ADA];

export function addDays(date, days) {
  const [y, m, d] = date.split('-').map(Number);
  return new Date(Date.UTC(y, m - 1, d + days)).toISOString().slice(0, 10);
}

let nextId = 1;

export function event(calendar, title, start, end, extra = {}) {
  return {
    id: nextId++,
    title,
    location: extra.location ?? null,
    all_day: extra.all_day ?? false,
    starts_at: start,
    ends_at: end,
    calendar,
  };
}

/** One event per state a row can be in. Three, because month cells cap at three. */
export function household(today = TODAY) {
  return [
    event(ADA, 'School run', `${today}T08:10:00`, `${today}T08:40:00`),
    event(FAMILY, 'Piano', `${today}T16:30:00`, `${today}T17:15:00`, { location: 'High Street' }),
    event(FAMILY, 'Bin day', `${today}T00:00:00`, `${today}T00:00:00`, { all_day: true }),
    event(ADA, 'Swimming', `${addDays(today, 1)}T17:00:00`, `${addDays(today, 1)}T18:00:00`),
  ];
}

const DAYS = ['SUN', 'MON', 'TUE', 'WED', 'THU', 'FRI', 'SAT'];

function weekdayIndex(date) {
  return new Date(`${date}T00:00:00Z`).getUTCDay();
}

function mondayOf(date) {
  return addDays(date, -((weekdayIndex(date) + 6) % 7));
}

const SPANS = { day: 1, next3: 3, next5: 5, week: 7 };

function bounds(view, anchor) {
  if (view === 'month') {
    const first = `${anchor.slice(0, 7)}-01`;
    const start = mondayOf(first);
    const next = new Date(Date.UTC(+anchor.slice(0, 4), +anchor.slice(5, 7), 1));
    const prev = new Date(Date.UTC(+anchor.slice(0, 4), +anchor.slice(5, 7) - 2, 1));
    return {
      start,
      length: 42,
      prev: prev.toISOString().slice(0, 10),
      next: next.toISOString().slice(0, 10),
      inPeriod: (d) => d.slice(0, 7) === anchor.slice(0, 7),
    };
  }
  const start = view === 'week' ? mondayOf(anchor) : anchor;
  const length = SPANS[view];
  return {
    start,
    length,
    prev: addDays(start, -length),
    next: addDays(start, length),
    inPeriod: () => true,
  };
}

/** The /api/calendar response. The title names the view and anchor, so a test
 * can tell exactly which response is on screen. */
export function calendarView(view, anchor, today, now, events) {
  const resolved = anchor ?? today;
  const { start, length, prev, next, inPeriod } = bounds(view, resolved);
  const days = Array.from({ length }, (_, i) => {
    const date = addDays(start, i);
    return {
      date,
      day_of_month: Number(date.slice(8)),
      weekday_short: DAYS[weekdayIndex(date)],
      in_period: inPeriod(date),
      is_today: date === today,
      items: events
        .filter((e) => e.starts_at.slice(0, 10) === date)
        .map((e) => ({ ...e, continues_before: false, continues_after: false })),
    };
  });
  return {
    view,
    anchor: resolved,
    title: `${view} of ${resolved}`,
    today,
    now,
    prev_anchor: prev,
    next_anchor: next,
    days,
  };
}

export function agenda(events) {
  return events.map((e) => ({ ...e, agenda_date: e.starts_at.slice(0, 10) }));
}

export function player(overrides = {}) {
  return {
    id: 1,
    name: 'Kitchen',
    model: 'HEOS 1',
    version: '1.0',
    available: true,
    state: 'stop',
    volume: 30,
    muted: false,
    group_id: null,
    now_playing: null,
    queue: null,
    ...overrides,
  };
}

export const TRACK = {
  title: 'Everything In Its Right Place',
  artist: 'Radiohead',
  album: 'Kid A',
  image_url: '/api/music/art/kid-a?size=480',
  duration_ms: 250_000,
  position_ms: 60_000,
};

export const PNG = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==',
  'base64',
);
