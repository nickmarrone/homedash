// The one run with nothing stubbed: an .ics file, the real sync, the real API,
// the real event stream, and the built panel. Everything else in this directory
// trusts support/data.mjs to describe the API; this is what keeps that honest.
import { expect, test } from '@playwright/test';
import { spawn } from 'node:child_process';
import { mkdtempSync, rmSync } from 'node:fs';
import { createServer } from 'node:http';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { CALENDARS, NOW, TODAY, agenda, calendarView, event, FAMILY } from './support/data.mjs';

const BACKEND = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..', '..', 'backend');
const PORT = 8097;
const ORIGIN = `http://127.0.0.1:${PORT}`;

test.describe.configure({ mode: 'serial' });

const pad = (n) => String(n).padStart(2, '0');
const stamp = (d) => `${d.getUTCFullYear()}${pad(d.getUTCMonth() + 1)}${pad(d.getUTCDate())}`;

function ics() {
  const today = new Date();
  const tomorrow = new Date(today.getTime() + 86_400_000);
  const dayAfter = new Date(today.getTime() + 2 * 86_400_000);
  return [
    'BEGIN:VCALENDAR',
    'VERSION:2.0',
    'PRODID:-//homedash//e2e//EN',
    'BEGIN:VEVENT',
    'UID:all-day@e2e',
    `DTSTAMP:${stamp(today)}T000000Z`,
    `DTSTART;VALUE=DATE:${stamp(today)}`,
    `DTEND;VALUE=DATE:${stamp(tomorrow)}`,
    'SUMMARY:Bin day',
    'END:VEVENT',
    'BEGIN:VEVENT',
    'UID:timed@e2e',
    `DTSTAMP:${stamp(today)}T000000Z`,
    `DTSTART:${stamp(dayAfter)}T090000Z`,
    `DTEND:${stamp(dayAfter)}T100000Z`,
    'SUMMARY:Dentist',
    'LOCATION:High Street',
    'END:VEVENT',
    'END:VCALENDAR',
    '',
  ].join('\r\n');
}

let backend;
let feed;
let dataDir;
const log = [];

test.beforeAll(async () => {
  test.setTimeout(120_000);
  feed = createServer((_, res) => {
    res.writeHead(200, { 'content-type': 'text/calendar' });
    res.end(ics());
  });
  await new Promise((r) => feed.listen(0, '127.0.0.1', r));
  dataDir = mkdtempSync(join(tmpdir(), 'homedash-e2e-'));

  // Run from the scratch directory so a developer's backend/.env is not read,
  // and aim outbound traffic at a closed port so the run is the same with or
  // without internet: weather has to degrade, not answer.
  backend = spawn(
    'uv',
    ['run', '--project', BACKEND, '--frozen', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', String(PORT)],
    {
      cwd: dataDir,
      env: {
        ...process.env,
        HOMEDASH_DB_PATH: join(dataDir, 'e2e.db'),
        HOMEDASH_HOME_TIMEZONE: 'UTC',
        HOMEDASH_COMETS_ENABLED: 'false',
        HOMEDASH_PHOTOS_DIR: join(dataDir, 'photos'),
        HOMEDASH_PHOTO_CACHE_DIR: join(dataDir, 'cache'),
        HOMEDASH_SCREEN_SCHEDULE: '{"on": "00:00", "off": "23:59"}',
        HOMEDASH_CALENDARS: JSON.stringify([
          { kind: 'ics', name: 'Family', url: `http://127.0.0.1:${feed.address().port}/family.ics` },
        ]),
        HTTP_PROXY: 'http://127.0.0.1:9',
        HTTPS_PROXY: 'http://127.0.0.1:9',
        NO_PROXY: '127.0.0.1',
      },
      stdio: ['ignore', 'pipe', 'pipe'],
    },
  );
  backend.stdout.on('data', (d) => log.push(d.toString()));
  backend.stderr.on('data', (d) => log.push(d.toString()));

  for (let i = 0; i < 400; i++) {
    if (backend.exitCode !== null) throw new Error(`backend exited early:\n${log.join('')}`);
    const up = await fetch(`${ORIGIN}/healthz`).then((r) => r.ok).catch(() => false);
    if (up) return;
    await new Promise((r) => setTimeout(r, 250));
  }
  throw new Error(`backend never became healthy:\n${log.join('')}`);
});

test.afterAll(async () => {
  backend?.kill('SIGTERM');
  await new Promise((r) => feed?.close(r) ?? r());
  if (dataDir) rmSync(dataDir, { recursive: true, force: true });
});

async function untilSynced() {
  await expect
    .poll(async () => (await (await fetch(`${ORIGIN}/api/agenda`)).json()).length, { timeout: 20_000 })
    .toBeGreaterThan(0);
}

test('an .ics event reaches the panel, through every layer', async ({ page }) => {
  const errors = [];
  page.on('pageerror', (e) => errors.push(e.message));
  await untilSynced();
  await page.goto(ORIGIN);
  await expect(page.locator('.month').getByText('Bin day')).toBeVisible();
  await expect(page.locator('.month').getByText('Dentist')).toBeVisible();
  expect(errors).toEqual([]);
});

test('the real event stream delivers the first heartbeat', async ({ page }) => {
  // In agenda view nothing but the stream can supply the masthead's date.
  await page.addInitScript(() => localStorage.setItem('homedash:view', 'agenda'));
  await page.goto(ORIGIN);
  await expect(page.locator('h1')).toBeVisible({ timeout: 10_000 });
  await expect(page.locator('.agenda').getByRole('heading', { name: 'Today' })).toBeVisible();
  await expect(page.locator('.agenda').getByText('Bin day')).toBeVisible();
});

test('every view renders from the real API without an error', async ({ page }) => {
  const errors = [];
  page.on('pageerror', (e) => errors.push(e.message));
  page.on('console', (m) => {
    if (m.type() === 'error' && !m.text().startsWith('Failed to load resource')) errors.push(m.text());
  });
  await page.goto(ORIGIN);
  for (const name of ['Agenda', 'Day', '3 Day', '5 Day', 'Week', 'Month']) {
    const tab = page.getByRole('navigation', { name: 'Calendar view' }).getByRole('button', { name, exact: true });
    await tab.click();
    await expect(tab).toHaveAttribute('aria-pressed', 'true');
    await expect(page.getByText('Bin day').first()).toBeVisible();
  }
  expect(errors).toEqual([]);
});

test('a panel with no weather says so rather than breaking', async ({ page }) => {
  await page.goto(ORIGIN);
  await expect(page.getByText('Weather unavailable.')).toBeVisible();
});

test.describe('the stub in support/data.mjs still describes this API', () => {
  const keys = (value) => Object.keys(value).sort();
  const sampleEvent = event(FAMILY, 'x', `${TODAY}T01:00:00`, `${TODAY}T02:00:00`);

  test('/api/agenda', async () => {
    await untilSynced();
    const real = await (await fetch(`${ORIGIN}/api/agenda`)).json();
    expect(keys(agenda([sampleEvent])[0])).toEqual(keys(real[0]));
  });

  test('/api/calendar', async () => {
    await untilSynced();
    const real = await (await fetch(`${ORIGIN}/api/calendar?view=week`)).json();
    const stub = calendarView('week', null, TODAY, NOW, [sampleEvent]);
    expect(keys(stub)).toEqual(keys(real));
    expect(keys(stub.days[0])).toEqual(keys(real.days[0]));
    const realItem = real.days.flatMap((d) => d.items)[0];
    const stubItem = stub.days.flatMap((d) => d.items)[0];
    if (realItem) expect(keys(stubItem)).toEqual(keys(realItem));
  });

  test('/api/calendars', async () => {
    const real = await (await fetch(`${ORIGIN}/api/calendars`)).json();
    expect(keys(CALENDARS[0])).toEqual(keys(real[0]));
  });

  test('/api/photos', async () => {
    const real = await (await fetch(`${ORIGIN}/api/photos?orientation=landscape`)).json();
    expect(keys(real)).toEqual(expect.arrayContaining(['dwell_seconds', 'idle_minutes', 'photos']));
  });
});
