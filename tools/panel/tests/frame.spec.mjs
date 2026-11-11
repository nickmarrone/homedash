// A panel behind a wooden frame loses strips of the screen at the top and the
// bottom, and is told how much on the kiosk URL (see frontend/src/lib/frame.ts).
// These check the things that sit against those edges actually move clear.

import { TRACK, player } from './support/data.mjs';
import { expect, test } from './support/panel.mjs';

const TOP = 40;
const BOTTOM = 30;
const FRAMED = `/?frame_top=${TOP}&frame_bottom=${BOTTOM}`;

const playing = { connected: true, library: false, players: [player({ state: 'play', now_playing: TRACK })] };

const box = async (locator) => {
  const b = await locator.boundingBox();
  if (!b) throw new Error('not rendered');
  return b;
};
const viewportHeight = (page) => page.viewportSize().height;

test('the masthead moves down by exactly the top of the frame', async ({ page, panel }) => {
  await panel.open();
  const bare = await box(page.locator('header'));

  await panel.open({ path: FRAMED });
  const framed = await box(page.locator('header'));

  expect(framed.y - bare.y).toBeCloseTo(TOP, 0);
});

test('a frame value that is not a number is ignored', async ({ page, panel }) => {
  await panel.open();
  const bare = await box(page.locator('header'));

  await panel.open({ path: '/?frame_top=lots' });
  expect((await box(page.locator('header'))).y).toBeCloseTo(bare.y, 0);
});

test('the now-playing bar sits clear of the bottom of the frame', async ({ page, api, panel }) => {
  api.music = playing;
  await panel.open({ path: FRAMED });
  const bar = await box(page.locator('.music-slot'));
  expect(bar.y + bar.height).toBeLessThanOrEqual(viewportHeight(page) - BOTTOM + 0.5);
});

test('the music overlay lays out inside the frame', async ({ page, api, panel }) => {
  api.music = playing;
  await panel.open({ path: FRAMED });
  await page.getByRole('button', { name: 'Open music' }).click();

  const overlay = page.locator('.overlay');
  // The overlay itself still covers the whole screen, so the calendar never
  // shows round it...
  const whole = await box(overlay);
  expect(whole.y).toBe(0);
  expect(whole.height).toBe(viewportHeight(page));

  // ...but what is in it starts below the frame and ends above it.
  const header = await box(overlay.locator('header'));
  expect(header.y).toBeGreaterThanOrEqual(TOP - 0.5);
  const volume = await box(overlay.locator('.volume'));
  expect(volume.y + volume.height).toBeLessThanOrEqual(viewportHeight(page) - BOTTOM + 0.5);
});
