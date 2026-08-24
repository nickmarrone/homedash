import { chromium } from 'playwright';
import { mkdirSync } from 'node:fs';

const OUT = '/home/agent/.claude/jobs/877321b9/tmp/shots';
mkdirSync(OUT, { recursive: true });
const URL = 'http://127.0.0.1:8000';
const issues = [];

function watch(page, label) {
  page.on('response', (r) => {
    if (r.status() >= 400) issues.push(`[${label}] http ${r.status()} ${r.url()}`);
  });
  page.on('console', (m) => {
    if (m.type() === 'error') issues.push(`[${label}] console: ${m.text()}`);
  });
  page.on('pageerror', (e) => issues.push(`[${label}] pageerror: ${e.message}`));
}

const browser = await chromium.launch({
  channel: 'chrome',
  args: ['--no-sandbox', '--disable-dev-shm-usage']
});

const mode = process.argv[2] || 'screensaver';

async function blankCheck(label, width, height) {
  const page = await browser.newPage({ viewport: { width, height } });
  watch(page, label);
  await page.goto(URL, { waitUntil: 'networkidle' });
  // The blank arrives with the first heartbeat, which fires immediately on
  // connect, but allow a beat for it.
  await page.waitForSelector('.blank', { timeout: 40_000 });
  await page.screenshot({ path: `${OUT}/${label}-blank.png` });

  // It must not be dismissable: the screen is meant to be off.
  await page.mouse.click(width / 2, height / 2);
  await page.waitForTimeout(800);
  const stillBlank = await page.locator('.blank').count();
  const saver = await page.locator('.screensaver').count();
  console.log(`${label}: blank shown, survives a tap=${stillBlank === 1}, screensaver suppressed=${saver === 0}`);
  await page.close();
  return { stillBlank, saver };
}

async function saverCheck(label, width, height) {
  const page = await browser.newPage({ viewport: { width, height } });
  watch(page, label);
  await page.goto(URL, { waitUntil: 'networkidle' });
  await page.waitForTimeout(1500);

  const blank = await page.locator('.blank').count();
  await page.screenshot({ path: `${OUT}/${label}-1-calendar.png` });
  console.log(`${label}: calendar loaded, blank=${blank}`);

  console.log(`${label}: waiting for idle...`);
  await page.waitForSelector('.screensaver', { timeout: 100_000 });
  await page.waitForTimeout(2500);
  await page.screenshot({ path: `${OUT}/${label}-2-screensaver.png` });

  const shown = () =>
    page.locator('.screensaver .layer.visible img').evaluateAll((nodes) =>
      nodes.map((n) => ({
        src: n.getAttribute('src').split('?')[0].replace('/api/photos/', '').replace('/image', ''),
        natural: `${n.naturalWidth}x${n.naturalHeight}`,
        box: `${Math.round(n.getBoundingClientRect().width)}x${Math.round(n.getBoundingClientRect().height)}`
      }))
    );

  const first = await shown();
  console.log(`${label}: slide A ->`, JSON.stringify(first));

  await page.waitForTimeout(5000);
  const second = await shown();
  console.log(`${label}: slide B ->`, JSON.stringify(second));

  // Collect a few slides so both a full-bleed and a paired one are seen.
  const seen = [first, second];
  for (let i = 0; i < 4; i++) {
    await page.waitForTimeout(4000);
    seen.push(await shown());
  }
  const paired = seen.find((s) => s.length === 2);
  const full = seen.find((s) => s.length === 1);
  console.log(`${label}: saw a paired slide=${!!paired}`, paired ? JSON.stringify(paired) : '');
  console.log(`${label}: saw a full slide=${!!full}`, full ? JSON.stringify(full) : '');
  if (paired) await page.screenshot({ path: `${OUT}/${label}-3-paired.png` });

  await page.mouse.click(width / 2, height / 2);
  await page.waitForTimeout(600);
  const after = await page.locator('.screensaver').count();
  await page.screenshot({ path: `${OUT}/${label}-4-dismissed.png` });
  console.log(`${label}: dismissed=${after === 0}`);

  await page.close();
  return {
    advanced: JSON.stringify(first) !== JSON.stringify(second),
    sawPaired: !!paired,
    sawFull: !!full,
    dismissed: after === 0
  };
}

const results = {};
if (mode === 'blank') {
  results.landscape = await blankCheck('blank-landscape', 1920, 1080);
} else {
  results.landscape = await saverCheck('landscape', 1920, 1080);
  results.portrait = await saverCheck('portrait', 1080, 1920);
}

await browser.close();
console.log('\n=== results ===');
for (const [k, v] of Object.entries(results)) console.log(k + ':', JSON.stringify(v));
console.log(issues.length ? `\nISSUES:\n${issues.join('\n')}` : '\nno console errors or failed requests');
