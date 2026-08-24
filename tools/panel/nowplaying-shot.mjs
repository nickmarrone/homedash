/**
 * Drives real Chrome at the now-playing surfaces with the metadata shape the
 * server now returns for a HomeDash queue: the Jellyfin track's own title,
 * artist and album, and a cover served from HomeDash rather than the speaker.
 *
 * The new thing worth looking at is that `image_url` is a *relative* URL now,
 * so this serves /api/music/art/b1 as a real image and checks the browser both
 * resolves it and paints it - a broken cover is invisible to the backend suite
 * and extremely visible across a kitchen.
 */
import { chromium } from 'playwright';
import { createServer } from 'node:http';
import { readFileSync, existsSync, statSync, mkdirSync } from 'node:fs';
import { join, extname } from 'node:path';

const BUILD = '/home/nmarrone/src/nickmarrone/homedash/frontend/build';
const OUT = process.argv[2] || '/tmp/nowplaying';
mkdirSync(OUT, { recursive: true });

const TYPES = {
  '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css',
  '.json': 'application/json', '.svg': 'image/svg+xml', '.png': 'image/png',
  '.woff2': 'font/woff2', '.ico': 'image/x-icon',
};

// A 2x2 magenta PNG, so "did the cover paint" is answerable from a screenshot.
const COVER = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAYAAABytg0kAAAAFUlEQVR42mP8z8BQz0AEYBxVSF+FABJADveWkH6oAAAAAElFTkSuQmCC',
  'base64'
);

const server = createServer((req, res) => {
  const url = new URL(req.url, 'http://x');
  if (url.pathname.startsWith('/api/music/art/')) {
    res.writeHead(200, { 'content-type': 'image/png' });
    return res.end(COVER);
  }
  let path = join(BUILD, url.pathname);
  if (!existsSync(path) || statSync(path).isDirectory()) path = join(BUILD, 'index.html');
  res.writeHead(200, { 'content-type': TYPES[extname(path)] || 'application/octet-stream' });
  res.end(readFileSync(path));
});
await new Promise((r) => server.listen(0, '127.0.0.1', r));
const base = `http://127.0.0.1:${server.address().port}`;

// Exactly what /api/music/players returns once HomeDash answers for the
// speaker: not "160kbps MP3" and not a URL on the speaker's own host.
const PLAYER = {
  id: 1, name: 'Kitchen', model: 'HEOS 1', version: '1.0', available: true,
  state: 'play', volume: 30, muted: false, group_id: null,
  now_playing: {
    title: 'Weightless',
    artist: 'Marconi Union',
    album: 'Distance',
    image_url: '/api/music/art/b1',
    duration_ms: 480000,
    position_ms: 126000,
  },
  queue: { position: 3, length: 8, remaining: 5, track: { id: 't3', title: 'Weightless' } },
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
    if (url.pathname.startsWith('/api/music/art/')) return route.continue();
    if (url.pathname === '/api/music/players') {
      return route.fulfill({ json: { connected: true, library: true, players: [PLAYER] } });
    }
    if (url.pathname === '/api/music/library') {
      return route.fulfill({ json: { kind: url.searchParams.get('kind'), parent: null, items: [] } });
    }
    if (url.pathname === '/api/agenda' || url.pathname === '/api/calendars') {
      return route.fulfill({ json: [] });
    }
    if (url.pathname === '/api/calendar') {
      return route.fulfill({ json: { view: 'day', days: [], weekday_labels: [] } });
    }
    if (url.pathname === '/api/photos') {
      return route.fulfill({
        json: { orientation: 'landscape', idle_minutes: 999, dwell_seconds: 12, photos: [] },
      });
    }
    if (url.pathname === '/api/weather') {
      return route.fulfill({ json: { current: null, daily: [], hourly: [] } });
    }
    return route.fulfill({ json: {} });
  });
  await page.route('**/api/events', (route) => route.abort());

  await page.goto(base, { waitUntil: 'networkidle' });
  await page.waitForTimeout(800);
  await page.screenshot({ path: join(OUT, `${label}-bar.png`) });

  const open = page.getByRole('button', { name: /music/i }).first();
  await open.click();
  await page.waitForTimeout(600);
  await page.screenshot({ path: join(OUT, `${label}-overlay.png`) });

  // Did every cover actually decode, or is the browser painting its glyph?
  const art = await page.evaluate(() =>
    [...document.images].map((i) => ({ src: i.getAttribute('src'), w: i.naturalWidth }))
  );
  console.log(`${label}: images ${JSON.stringify(art)}`);
  const text = await page.evaluate(() => document.body.innerText.replace(/\n+/g, ' | '));
  console.log(`${label}: ${text.slice(0, 220)}`);
  console.log(`${label}: ${issues.length ? issues.join('\n  ') : 'no console issues'}`);
  await page.close();
}

await browser.close();
server.close();
