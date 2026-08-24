import { chromium } from 'playwright';
const browser = await chromium.launch({ channel: 'chrome', args: ['--no-sandbox', '--disable-dev-shm-usage'] });
const OUT = '/home/agent/.claude/jobs/877321b9/tmp/shots';

for (const [label, w, h] of [['landscape', 1920, 1080], ['portrait', 1080, 1920]]) {
  const page = await browser.newPage({ viewport: { width: w, height: h } });
  await page.goto('http://127.0.0.1:8000', { waitUntil: 'networkidle' });
  await page.waitForSelector('.screensaver', { timeout: 100_000 });
  // Poll fast and shoot the instant a paired slide is fully faded in.
  for (let i = 0; i < 200; i++) {
    const n = await page.locator('.screensaver .layer.visible .slide img').count();
    const opacity = await page.locator('.screensaver .layer.visible').evaluate(
      (el) => getComputedStyle(el).opacity
    );
    if (n === 2 && opacity === '1') {
      await page.screenshot({ path: `${OUT}/${label}-paired-real.png` });
      console.log(`${label}: captured a paired slide`);
      break;
    }
    await page.waitForTimeout(250);
  }
  await page.close();
}
await browser.close();
