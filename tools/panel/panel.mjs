#!/usr/bin/env node
/**
 * Headless-browser harness for the HomeDash panel.
 *
 * The point of this script is to close the loop that unit tests can't: the panel
 * is a client-rendered SvelteKit app, so "the backend returns the right JSON" and
 * "the wall display looks right" are genuinely different claims. This drives real
 * Chrome, screenshots what it sees, and fails loudly on console errors - which is
 * what a broken panel actually looks like, since a Svelte render error leaves the
 * HTTP response a perfectly healthy 200.
 *
 *   node tools/panel/panel.mjs shot <url> [out.png]   one page, one screenshot
 *   node tools/panel/panel.mjs smoke [--url URL]      all four views, start-to-finish
 *
 * Run tools/panel/setup.sh once per sandbox first. See tools/panel/README.md.
 */
import { chromium } from 'playwright';
import { spawn, spawnSync } from 'node:child_process';
import { existsSync, mkdirSync, mkdtempSync, readdirSync, statSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const REPO = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..');
const OUT_DIR = process.env.PANEL_OUT_DIR || join(REPO, 'tools', 'panel', 'shots');

// Chrome runs as root-ish in a container with no user namespaces, so its own
// sandbox cannot initialise. --no-sandbox is the standard answer for CI images.
const LAUNCH = { channel: 'chrome', args: ['--no-sandbox', '--disable-dev-shm-usage'] };

/** Attach listeners that turn silent client-side breakage into reportable output. */
/** Non-2xx answers that mean the app is working, not failing.
 *
 * /api/music/players answers 503 when no HEOS host is configured, and the
 * panel is written to read that as "this household has no music" and hide the
 * UI - see fetchMusicPlayers in lib/api.ts. Treating it as a failure meant a
 * smoke run could never pass on a machine without speakers, which is every
 * machine this harness actually runs on.
 */
function isExpectedRefusal(response) {
  return response.status() === 503 && new URL(response.url()).pathname === '/api/music/players';
}

function watch(page) {
  const issues = [];
  page.on('response', (r) => {
    if (r.status() >= 400 && !isExpectedRefusal(r)) {
      issues.push(`[http ${r.status()}] ${r.url()}`);
    }
  });
  page.on('console', (m) => {
    if (m.type() !== 'error' && m.type() !== 'warning') return;
    // Chrome's bare "Failed to load resource" console line carries no URL and is
    // always a duplicate of something the response/requestfailed listeners above
    // already recorded *with* the URL - so dropping it costs no signal. What it
    // does drop is the one case those listeners never see: app.html declares no
    // favicon, so Chrome probes /favicon.ico itself, outside any request the page
    // made, and the SPA fallback answers 404. That is browser behaviour rather
    // than app breakage, and a kiosk panel has no tab to show an icon in anyway.
    if (m.text().startsWith('Failed to load resource')) return;
    issues.push(`[${m.type()}] ${m.text()}`);
  });
  page.on('pageerror', (e) => issues.push(`[pageerror] ${e.message}`));
  page.on('requestfailed', (r) =>
    issues.push(`[requestfailed] ${r.url()} - ${r.failure()?.errorText}`),
  );
  return issues;
}

/**
 * Wait until the rendered text stops changing.
 *
 * `waitForLoadState('networkidle')` is not enough on its own: switching views
 * fires its fetch from a Svelte effect, i.e. a microtask *after* the click, so
 * networkidle can be satisfied by the quiet moment before the request has even
 * started and return immediately. Screenshotting there captures the previous
 * view's content under the new view's highlighted tab - which looks like a real
 * rendering bug and is the exact kind of false alarm this harness must not raise.
 */
async function waitForStableText(page, { timeout = 10_000, quiet = 250 } = {}) {
  const deadline = Date.now() + timeout;
  let previous = null;
  while (Date.now() < deadline) {
    const current = await page.locator('body').innerText();
    if (current === previous) return current;
    previous = current;
    await page.waitForTimeout(quiet);
  }
  return previous;
}

async function capture(page, url, outPath) {
  await page.goto(url, { waitUntil: 'networkidle', timeout: 30_000 });
  // Client-rendered: the document being idle does not mean the panel has drawn.
  await waitForStableText(page).catch(() => {});
  mkdirSync(dirname(outPath), { recursive: true });
  await page.screenshot({ path: outPath, fullPage: true });
}

async function shot(url, out) {
  const browser = await chromium.launch(LAUNCH);
  const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
  const issues = watch(page);
  const outPath = resolve(out || join(OUT_DIR, 'shot.png'));
  try {
    await capture(page, url, outPath);
    console.log(`title: ${await page.title()}`);
    console.log(`screenshot: ${outPath}`);
    report(issues);
  } finally {
    await browser.close();
  }
  return issues.length;
}

function report(issues, indent = '') {
  if (!issues.length) {
    console.log(`${indent}no console errors or failed requests`);
    return;
  }
  console.log(`${indent}${issues.length} issue(s):`);
  for (const i of issues) console.log(`${indent}  ${i}`);
}

/** The newest mtime under a directory, ignoring node_modules. */
function newestMtime(dir) {
  let newest = 0;
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    if (entry.name === 'node_modules' || entry.name.startsWith('.')) continue;
    const path = join(dir, entry.name);
    const at = entry.isDirectory() ? newestMtime(path) : statSync(path).mtimeMs;
    if (at > newest) newest = at;
  }
  return newest;
}

/**
 * The backend serves the panel out of frontend/build, which is gitignored - so
 * in a fresh clone or a git worktree it simply isn't there and every page would
 * 404. Build it rather than making that the caller's problem.
 *
 * Rebuilt when a source file is newer than the build, not merely when the
 * build is missing. Testing a change against a stale bundle is worse than not
 * testing it: the harness passes, or fails for the previous reason, and either
 * way it reports on code that is not the code being changed. That is a
 * expensive way to lose an hour, and it is the whole reason this tool exists.
 */
function ensureFrontendBuilt() {
  const fe = join(REPO, 'frontend');
  const built = join(fe, 'build', 'index.html');
  if (existsSync(built) && statSync(built).mtimeMs >= newestMtime(join(fe, 'src'))) return;
  if (!existsSync(join(fe, 'node_modules'))) {
    console.log('installing frontend deps...');
    run('npm', ['install', '--no-audit', '--no-fund'], fe);
  }
  console.log(existsSync(built) ? 'frontend is stale, rebuilding...' : 'building frontend...');
  run('npm', ['run', 'build'], fe);
}

function run(cmd, args, cwd) {
  const r = spawnSync(cmd, args, { cwd, stdio: 'inherit' });
  if (r.status !== 0) throw new Error(`${cmd} ${args.join(' ')} failed (${r.status})`);
}

/** Boot the backend on a throwaway DB and wait for /healthz. Returns a stop(). */
async function startBackend(port) {
  const dbDir = mkdtempSync(join(tmpdir(), 'homedash-smoke-'));
  const proc = spawn(
    'uv',
    ['run', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', String(port)],
    {
      cwd: join(REPO, 'backend'),
      env: {
        ...process.env,
        // A scratch DB keeps a smoke run from touching backend/data/homedash.db.
        HOMEDASH_DB_PATH: join(dbDir, 'smoke.db'),
        // Keep the screen up for the whole run. The default schedule is
        // 06:30-21:30, so without this a smoke run started in the evening
        // fails with every click intercepted by the bedtime blank - which is
        // the panel working correctly, reported as a test failure. Anything
        // actually testing the schedule should set its own.
        HOMEDASH_SCREEN_SCHEDULE: '{"on": "00:00", "off": "23:59"}',
      },
      stdio: ['ignore', 'pipe', 'pipe'],
    },
  );
  const log = [];
  proc.stdout.on('data', (d) => log.push(d.toString()));
  proc.stderr.on('data', (d) => log.push(d.toString()));

  const url = `http://127.0.0.1:${port}`;
  for (let i = 0; i < 90; i++) {
    if (proc.exitCode !== null) {
      throw new Error(`backend exited early (code ${proc.exitCode}):\n${log.join('')}`);
    }
    try {
      const r = await fetch(`${url}/healthz`);
      if (r.ok) return { url, stop: () => proc.kill('SIGTERM'), log };
    } catch {
      /* not listening yet */
    }
    await new Promise((r) => setTimeout(r, 1000));
  }
  proc.kill('SIGKILL');
  throw new Error(`backend never became healthy:\n${log.join('')}`);
}

async function smoke(explicitUrl) {
  let base = explicitUrl;
  let stop = () => {};
  if (!base) {
    ensureFrontendBuilt();
    console.log('starting backend...');
    const server = await startBackend(8099);
    base = server.url;
    stop = server.stop;
    console.log(`backend healthy at ${base}`);
  } else {
    console.log(`using running server at ${base}`);
  }

  const browser = await chromium.launch(LAUNCH);
  // 1280x800 is a stand-in for the wall panel; once the display is chosen
  // (Phase 3 open decision #2) this should become that panel's real resolution.
  const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
  const issues = watch(page);
  let failed = 0;

  try {
    // Count the initial load as its own step, otherwise a 404 or a boot-time
    // exception lands in `issues` before the first view's baseline is taken and
    // is silently never reported.
    await capture(page, base, join(OUT_DIR, 'load.png'));
    failed += issues.length;
    console.log(`\nload  -> ${join(OUT_DIR, 'load.png')}`);
    report(issues, '  ');

    // The view lives in localStorage, not the URL, so there is no query param to
    // deep-link with - drive the actual switcher the way a thumb would. Asserting
    // aria-pressed afterwards is what makes this a test and not just a screenshot:
    // a broken switcher would otherwise re-photograph the same view four times.
    for (const [id, label] of [
      ['agenda', 'Agenda'],
      ['day', 'Day'],
      ['next3', '3 Day'],
      ['next5', '5 Day'],
      ['week', 'Week'],
      ['month', 'Month'],
    ]) {
      const before = issues.length;
      const tab = page.getByRole('button', { name: label, exact: true });
      await tab.click();
      await page.waitForFunction(
        (t) =>
          [...document.querySelectorAll('nav[aria-label="Calendar view"] button')].some(
            (b) => b.textContent.trim() === t && b.getAttribute('aria-pressed') === 'true',
          ),
        label,
        { timeout: 10_000 },
      );
      await page.waitForLoadState('networkidle');
      // Settle before the screenshot, so the image and the text below agree.
      const settled = await waitForStableText(page);

      const out = join(OUT_DIR, `${id}.png`);
      mkdirSync(dirname(out), { recursive: true });
      await page.screenshot({ path: out, fullPage: true });

      const text = settled.slice(0, 120).replace(/\s+/g, ' ');
      const fresh = issues.slice(before);
      failed += fresh.length;
      console.log(`\n${id}  -> ${out}`);
      console.log(`  text: ${text}`);
      report(fresh, '  ');
    }
  } finally {
    await browser.close();
    stop();
  }

  console.log(`\n${failed ? `FAIL - ${failed} issue(s)` : 'PASS - every view rendered clean'}`);
  return failed;
}

const [cmd, ...rest] = process.argv.slice(2);
let code = 0;
if (cmd === 'shot') {
  if (!rest[0]) {
    console.error('usage: panel.mjs shot <url> [out.png]');
    process.exit(2);
  }
  code = await shot(rest[0], rest[1]);
} else if (cmd === 'smoke') {
  const i = rest.indexOf('--url');
  code = await smoke(i === -1 ? undefined : rest[i + 1]);
} else {
  console.error('usage: panel.mjs shot <url> [out.png] | panel.mjs smoke [--url URL]');
  process.exit(2);
}
process.exit(code ? 1 : 0);
