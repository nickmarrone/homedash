/**
 * Drives real Chrome at a calendar with events in it, so the states that only
 * exist when something is scheduled can actually be looked at.
 *
 * `panel.mjs smoke` boots an empty database, which proves every view renders
 * but says nothing about an appointment - and the treatments that matter most
 * on a wall are the ones that distinguish two appointments from each other. A
 * finished event is struck in its calendar's colour; a multi-day one continues
 * across columns; an all-day one is set in the colour rather than marked with
 * it. None of that has any representation in the backend suite.
 *
 * The API is stubbed in the browser rather than served, for the same reason
 * music-shot does it: what is under test is the panel.
 */
import { chromium } from 'playwright';
import { createServer } from 'node:http';
import { readFileSync, existsSync, statSync, mkdirSync } from 'node:fs';
import { join, extname, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const REPO = resolveRepo();
const BUILD = join(REPO, 'frontend', 'build');
const OUT = process.argv[2] || join(REPO, 'tools', 'panel', 'shots');
mkdirSync(OUT, { recursive: true });

function resolveRepo() {
  return join(dirname(fileURLToPath(import.meta.url)), '..', '..');
}

const TYPES = {
  '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css',
  '.json': 'application/json', '.svg': 'image/svg+xml', '.png': 'image/png',
  '.woff2': 'font/woff2', '.ico': 'image/x-icon',
};

const server = createServer((req, res) => {
  const url = new URL(req.url, 'http://x');
  let path = join(BUILD, url.pathname);
  if (!existsSync(path) || statSync(path).isDirectory()) path = join(BUILD, 'index.html');
  res.writeHead(200, { 'content-type': TYPES[extname(path)] || 'application/octet-stream' });
  res.end(readFileSync(path));
});
await new Promise((r) => server.listen(0, '127.0.0.1', r));
const base = `http://127.0.0.1:${server.address().port}`;

// A fixed day, so the screenshots are comparable between runs. "Now" is
// mid-afternoon, which puts real events on both sides of it.
const TODAY = '2026-08-24';
const NOW = `${TODAY}T15:30:00`;

const CALENDARS = [
  { id: 1, name: 'Family', color: '#1f5f4e' },
  { id: 2, name: 'Ada', color: '#8a3a12' },
  { id: 3, name: 'Sam', color: '#3b3f8f' },
];

let nextId = 1;
function event(calendarId, title, start, end, extra = {}) {
  const calendar = CALENDARS.find((c) => c.id === calendarId);
  return {
    id: nextId++,
    title,
    location: extra.location ?? null,
    all_day: extra.all_day ?? false,
    starts_at: start,
    ends_at: end,
    calendar,
    agenda_date: extra.agenda_date ?? start.slice(0, 10),
    continues_before: extra.continues_before ?? false,
    continues_after: extra.continues_after ?? false,
  };
}

// Deliberately spread across "over", "now" and "later", because the whole
// point of this fixture is the difference between them.
function todaysItems() {
  return [
    event(2, 'School run', `${TODAY}T08:10:00`, `${TODAY}T08:40:00`),
    event(1, 'Dentist', `${TODAY}T09:30:00`, `${TODAY}T10:15:00`, { location: 'High Street' }),
    event(3, 'Standup', `${TODAY}T11:00:00`, `${TODAY}T11:15:00`),
    event(1, 'Holiday', '2026-08-22T00:00:00', '2026-08-29T00:00:00', {
      all_day: true,
      agenda_date: TODAY,
      continues_before: true,
      continues_after: true,
    }),
    event(2, 'Piano', `${TODAY}T16:30:00`, `${TODAY}T17:15:00`),
    event(3, 'Dinner with Sam', `${TODAY}T19:00:00`, `${TODAY}T21:00:00`, { location: 'Rosetta' }),
  ];
}

function day(date, items, inPeriod = true) {
  return {
    date,
    day_of_month: Number(date.slice(8)),
    weekday_short: ['MON', 'TUE', 'WED', 'THU', 'FRI', 'SAT', 'SUN'][
      (new Date(`${date}T00:00:00Z`).getUTCDay() + 6) % 7
    ],
    in_period: inPeriod,
    is_today: date === TODAY,
    items,
  };
}

function weekPayload() {
  const dates = ['24', '25', '26', '27', '28', '29', '30'].map((d) => `2026-08-${d}`);
  return {
    view: 'week',
    anchor: TODAY,
    title: 'Aug 24 - 30, 2026',
    today: TODAY,
    now: NOW,
    prev_anchor: '2026-08-17',
    next_anchor: '2026-08-31',
    weekday_labels: ['MON', 'TUE', 'WED', 'THU', 'FRI', 'SAT', 'SUN'],
    days: dates.map((date, i) => {
      if (date === TODAY) return day(date, todaysItems());
      if (i === 1) return day(date, [event(3, 'Swimming', `${date}T17:00:00`, `${date}T18:00:00`)]);
      if (i === 3) return day(date, [event(1, 'Bin day', `${date}T00:00:00`, `${date}T00:00:00`, { all_day: true })]);
      return day(date, []);
    }),
  };
}

const shots = [];

const browser = await chromium.launch({ channel: 'chrome', args: ['--no-sandbox'] });

for (const [label, viewport] of [
  ['landscape', { width: 1920, height: 1080 }],
  ['portrait', { width: 1080, height: 1920 }],
]) {
  const page = await browser.newPage({ viewport });
  const issues = [];
  page.on('console', (m) => {
    if (m.type() === 'error' || m.type() === 'warning') issues.push(`[${m.type()}] ${m.text()}`);
  });
  page.on('pageerror', (e) => issues.push(`[pageerror] ${e.message}`));

  await page.route('**/api/**', (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/api/events/stream') {
      return route.fulfill({ status: 200, contentType: 'text/event-stream', body: '' });
    }
    if (path === '/api/calendars') return route.fulfill({ json: CALENDARS });
    if (path === '/api/agenda') return route.fulfill({ json: todaysItems() });
    if (path === '/api/calendar') return route.fulfill({ json: weekPayload() });
    if (path === '/api/weather') return route.fulfill({ json: { current: null } });
    if (path === '/api/photos') {
      return route.fulfill({ json: { idle_minutes: 999, dwell_seconds: 12, photos: [] } });
    }
    return route.fulfill({ json: {} });
  });

  await page.goto(base, { waitUntil: 'networkidle' });

  for (const view of ['Week', 'Agenda']) {
    await page.getByRole('button', { name: view, exact: true }).click();
    await page.waitForTimeout(400);
    const file = join(OUT, `calendar-${label}-${view.toLowerCase()}.png`);
    await page.screenshot({ path: file });
    shots.push(file);
    console.log(`${label} ${view.toLowerCase()} -> ${file}`);
  }

  console.log(issues.length ? `${label}: ${issues.join('; ')}` : `${label}: no console issues`);
  await page.close();
}

await browser.close();
server.close();
