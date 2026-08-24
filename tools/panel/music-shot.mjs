/**
 * Drives real Chrome at the music overlay with a canned library, so the A-Z
 * rail can actually be looked at. The API is stubbed in the browser rather
 * than served by the backend: what is under test here is the panel, and a
 * fake HEOS socket would be a great deal of machinery to prove a list scrolls.
 */
import { chromium } from 'playwright';
import { createServer } from 'node:http';
import { readFileSync, existsSync, statSync, mkdirSync } from 'node:fs';
import { join, extname, dirname } from 'node:path';

const BUILD = '/home/nmarrone/src/nickmarrone/homedash/frontend/build';
const OUT = process.argv[2] || '/tmp/music';
mkdirSync(OUT, { recursive: true });

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

// Deliberately lopsided: a big run of Bs and Ss, nothing at all under Q, X or
// Z, and three artists filed under a letter their display name does not start
// with. That last group is the whole reason `sort_name` exists.
const NAMES = [
  ['ABBA', 'ABBA'], ['Aphex Twin', 'Aphex Twin'], ['Arcade Fire', 'Arcade Fire'],
  ['The Beatles', 'Beatles, The'], ['Beach House', 'Beach House'],
  ['Beethoven', 'Beethoven'], ['Björk', 'Björk'], ['Blur', 'Blur'],
  ['Bonobo', 'Bonobo'], ['Boards of Canada', 'Boards of Canada'],
  ['Caribou', 'Caribou'], ['The Clash', 'Clash, The'], ['Cocteau Twins', 'Cocteau Twins'],
  ['Daft Punk', 'Daft Punk'], ['Debussy', 'Debussy'],
  ['Erik Satie', 'Erik Satie'], ['Explosions in the Sky', 'Explosions in the Sky'],
  ['Fleetwood Mac', 'Fleetwood Mac'], ['Four Tet', 'Four Tet'],
  ['Grimes', 'Grimes'], ['Godspeed You! Black Emperor', 'Godspeed You! Black Emperor'],
  ['Hania Rani', 'Hania Rani'], ['Interpol', 'Interpol'],
  ['Jon Hopkins', 'Jon Hopkins'], ['Kraftwerk', 'Kraftwerk'],
  ['LCD Soundsystem', 'LCD Soundsystem'], ['Låpsley', 'Låpsley'],
  ['Marconi Union', 'Marconi Union'], ['Massive Attack', 'Massive Attack'],
  ['Nils Frahm', 'Nils Frahm'], ['Ólafur Arnalds', 'Ólafur Arnalds'],
  ['Portishead', 'Portishead'], ['Radiohead', 'Radiohead'],
  ['Sigur Rós', 'Sigur Rós'], ['Slowdive', 'Slowdive'], ['Stereolab', 'Stereolab'],
  ['St. Vincent', 'St. Vincent'], ['Sufjan Stevens', 'Sufjan Stevens'],
  ['The Smiths', 'Smiths, The'], ['Talk Talk', 'Talk Talk'],
  ['Tycho', 'Tycho'], ['Underworld', 'Underworld'], ['Vangelis', 'Vangelis'],
  ['The War on Drugs', 'War on Drugs, The'], ['Yo La Tengo', 'Yo La Tengo'],
  ['808 State', '808 State'], ['65daysofstatic', '65daysofstatic'],
];
// Padded out to a library that genuinely does not fit on a screen, so a jump
// to S is a jump rather than a scroll that clamps at the bottom.
const PADDING = [];
for (const letter of 'ABCDEFGHIJKLMNOPRSTUVWY') {
  for (let i = 1; i <= 9; i++) PADDING.push([`${letter}rtist ${i}`, `${letter}rtist ${i}`]);
}
const ARTISTS = [...NAMES, ...PADDING]
  .map(([name, sort_name], i) => ({ id: `a${i}`, name, sort_name }))
  .sort((a, b) => a.sort_name.localeCompare(b.sort_name));

const PLAYER = {
  id: 1, name: 'Kitchen', model: 'HEOS 1', version: '1.0', available: true,
  state: 'stop', volume: 30, muted: false, group_id: null, now_playing: null,
};

const ROUTES = {
  '/api/music/players': { connected: true, library: true, players: [PLAYER] },
  '/api/photos': { orientation: 'landscape', idle_minutes: 999, dwell_seconds: 12, photos: [] },
  '/api/calendars': { calendars: [] },
  '/api/weather': { current: null, daily: [], hourly: [] },
};

const browser = await chromium.launch({
  channel: 'chrome',
  args: ['--no-sandbox', '--disable-dev-shm-usage'],
});

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

  await page.route('**/api/**', async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname === '/api/events/stream') {
      // A real content type, even though this stub sends no events. Answering
      // the stream with application/json like everything else is fatal to an
      // EventSource - readyState goes to CLOSED and the panel correctly
      // decides its stream will never come back and reloads the page. That is
      // the panel working; it was this fixture that was wrong.
      return route.fulfill({ status: 200, contentType: 'text/event-stream', body: '' });
    }
    if (url.pathname === '/api/music/library') {
      const kind = url.searchParams.get('kind');
      const items = kind === 'artists' ? ARTISTS : [];
      return route.fulfill({ json: { kind, parent: url.searchParams.get('parent'), items } });
    }
    if (url.pathname === '/api/agenda' || url.pathname === '/api/calendars') {
      return route.fulfill({ json: [] });
    }
    if (url.pathname === '/api/calendar') {
      return route.fulfill({ json: { view: 'day', days: [], weekday_labels: [] } });
    }
    const body = ROUTES[url.pathname];
    if (body) return route.fulfill({ json: body });
    return route.fulfill({ json: {} });
  });

  await page.goto(base, { waitUntil: 'networkidle' });
  await page.waitForTimeout(800);

  const open = page.getByRole('button', { name: /music/i }).first();
  await open.click();
  await page.waitForTimeout(400);
  const browse = page.getByRole('button', { name: 'Library', exact: true });
  if (await browse.isVisible().catch(() => false)) await browse.click();
  await page.waitForTimeout(600);
  await page.screenshot({ path: join(OUT, `${label}-artists.png`) });

  // Drag the rail from A down to S, which is where the long run is.
  const rail = page.locator('nav[aria-label="Jump to letter"]');
  const box = await rail.boundingBox();
  if (!box) {
    console.log(`${label}: NO RAIL RENDERED`);
  } else {
    const letters = 27;
    const yFor = (i) => box.y + ((i + 0.5) / letters) * box.height;
    await page.mouse.move(box.x + box.width / 2, yFor(1));
    await page.mouse.down();
    for (let i = 2; i <= 19; i++) {
      await page.mouse.move(box.x + box.width / 2, yFor(i));
      await page.waitForTimeout(20);
    }
    await page.screenshot({ path: join(OUT, `${label}-scrubbing.png`) });
    await page.mouse.up();
    await page.waitForTimeout(200);
    const first = await page.locator('ul.rows li').first().innerText();
    const top = await page.evaluate(() => {
      const list = document.querySelector('ul.rows');
      const listTop = list.getBoundingClientRect().top;
      return [...list.children]
        .map((li) => [li.innerText.trim(), li.getBoundingClientRect().top])
        .filter(([, t]) => t >= listTop - 2)[0][0];
    });
    const ok = top.startsWith('S') ? 'OK' : 'WRONG';
    console.log(`${label}: dragged to S -> top row is "${top}" [${ok}] (list starts "${first.trim()}")`);
    await page.screenshot({ path: join(OUT, `${label}-after-drag.png`) });
  }
  console.log(`${label}: ${issues.length ? issues.join('\n  ') : 'no console issues'}`);
  await page.close();
}

await browser.close();
server.close();
