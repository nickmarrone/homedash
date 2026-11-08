import { TRACK, player } from './support/data.mjs';
import { expect, test } from './support/panel.mjs';

const photo = (id, slot = 'full') => ({
  id,
  slot,
  width: 1,
  height: 1,
  url: `/api/photos/${id}/image`,
});

function withPhotos(api, photos = [photo(1), photo(2)]) {
  api.photos = { dwell_seconds: 30, idle_minutes: 1, photos };
}

const saver = (page) => page.locator('.screensaver');
const blank = (page) => page.locator('.blank');

async function goIdle(page, panel) {
  await panel.passTime(70);
  await expect(saver(page)).toBeVisible();
}

test.beforeEach(async ({ page, api }) => {
  await page.clock.install();
  withPhotos(api);
});

test('stays on the calendar while somebody is using it', async ({ page, panel }) => {
  await panel.open();
  await panel.passTime(50);
  await page.mouse.click(5, 5);
  await panel.passTime(50);
  await expect(saver(page)).toHaveCount(0);
});

test('takes over once nobody has touched the panel', async ({ page, panel }) => {
  await panel.open();
  await goIdle(page, panel);
  await expect(saver(page).locator('img').first()).toBeVisible();
});

test('never starts with no photos to show', async ({ page, api, panel }) => {
  withPhotos(api, []);
  await panel.open();
  await panel.passTime(600);
  await expect(saver(page)).toHaveCount(0);
});

test('a tap brings the calendar back and restarts the wait', async ({ page, panel }) => {
  await panel.open();
  await goIdle(page, panel);
  await saver(page).click();
  await expect(saver(page)).toHaveCount(0);
  await expect(page.locator('h1')).toBeVisible();

  await panel.passTime(30);
  await expect(saver(page)).toHaveCount(0);
  await panel.passTime(50);
  await expect(saver(page)).toBeVisible();
});

test.describe('the tap that dismisses it', () => {
  test.use({ hasTouch: true });

  async function dismissOverControl(page, panel, tap) {
    await panel.open();
    await panel.select('Month');
    await goIdle(page, panel);
    const box = await panel.tab('Week').boundingBox();
    await tap(box.x + box.width / 2, box.y + box.height / 2);
    await expect(saver(page)).toHaveCount(0);
    await page.waitForTimeout(400);
  }

  test('does not also press the control underneath, with a finger', async ({ page, panel }) => {
    await dismissOverControl(page, panel, (x, y) => page.touchscreen.tap(x, y));
    await expect(panel.tab('Month')).toHaveAttribute('aria-pressed', 'true');
  });

  test('the next tap after dismissing it works normally', async ({ page, panel }) => {
    await dismissOverControl(page, panel, (x, y) => page.touchscreen.tap(x, y));
    await panel.tab('Week').tap();
    await expect(panel.tab('Week')).toHaveAttribute('aria-pressed', 'true');
  });

  test('does not also press the control underneath, with a mouse', async ({ page, panel }) => {
    await dismissOverControl(page, panel, (x, y) => page.mouse.click(x, y));
    await expect(panel.tab('Month')).toHaveAttribute('aria-pressed', 'true');
  });
});

test('photos that disagree with the panel share the screen, two at a time', async ({
  page,
  api,
  panel,
}) => {
  withPhotos(api, [photo(1, 'half'), photo(2, 'half')]);
  await panel.open();
  await goIdle(page, panel);
  await expect(saver(page).locator('.layer.visible img')).toHaveCount(2);
});

test('a lone photo that disagrees with the panel is centred, not stretched', async ({
  page,
  api,
  panel,
}) => {
  withPhotos(api, [photo(1, 'half')]);
  await panel.open();
  await goIdle(page, panel);
  await expect(saver(page).locator('.layer.visible .slide')).toHaveClass(/lonely/);
});

test('captions the photos with the track that is playing', async ({ page, api, panel }) => {
  api.music = { connected: true, library: false, players: [player({ state: 'play', now_playing: TRACK })] };
  await panel.open();
  await goIdle(page, panel);
  await expect(saver(page).locator('.song')).toHaveText(TRACK.title);
  await expect(saver(page).locator('.artist')).toHaveText(TRACK.artist);
});

test('leaves the caption off while paused', async ({ page, api, panel }) => {
  api.music = { connected: true, library: false, players: [player({ state: 'pause', now_playing: TRACK })] };
  await panel.open();
  await goIdle(page, panel);
  await expect(saver(page).locator('.song')).toHaveCount(0);
});

test('a photo that will not load is skipped', async ({ page, api, panel }) => {
  panel.allow(/./);
  withPhotos(api, [photo(1), { ...photo(2), url: '/not-an-image' }]);
  await panel.open();
  await goIdle(page, panel);
  await expect(saver(page).locator('.layer.visible img')).toHaveAttribute('src', /\/api\/photos\/1/);
});

test.describe('bedtime', () => {
  test('blanks the panel', async ({ page, panel }) => {
    await panel.open();
    await panel.heartbeat({ screen: 'off' });
    await expect(blank(page)).toBeVisible();
  });

  test('wins over the screensaver', async ({ page, panel }) => {
    await panel.open();
    await goIdle(page, panel);
    await panel.heartbeat({ screen: 'off' });
    await expect(blank(page)).toBeVisible();
    await expect(saver(page)).toHaveCount(0);
  });

  test('cannot be dismissed by an elbow in the dark', async ({ page, panel }) => {
    await panel.open();
    await panel.heartbeat({ screen: 'off' });
    await blank(page).click();
    await expect(blank(page)).toBeVisible();
  });

  test('wakes on the calendar, not in the slideshow', async ({ page, panel }) => {
    await panel.open();
    await panel.heartbeat({ screen: 'off' });
    await panel.passTime(600);
    await expect(blank(page)).toBeVisible();

    await panel.heartbeat({ screen: 'on' });
    await expect(blank(page)).toHaveCount(0);
    await expect(saver(page)).toHaveCount(0);
    await expect(page.locator('h1')).toBeVisible();
  });

  test('an older backend with no screen field leaves the panel lit', async ({ page, panel }) => {
    await panel.open();
    await panel.heartbeat({ screen: undefined });
    await expect(blank(page)).toHaveCount(0);
  });
});
