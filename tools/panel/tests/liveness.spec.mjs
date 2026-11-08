// The panel stays open for months with nobody at it. These are the ways it can
// go wrong without anyone noticing.
import { addDays, event, FAMILY, TODAY } from './support/data.mjs';
import { expect, test } from './support/panel.mjs';

test.describe('a backend that is not there yet', () => {
  test('the panel fills itself in once the backend arrives', async ({ page, api, panel }) => {
    panel.allow(/loads failed; retrying/);
    api.refusing = true;
    await page.goto('/');
    await expect(page.locator('.month')).toHaveCount(0);
    await expect.poll(() => api.count('GET /api/calendars')).toBeGreaterThan(1);

    api.refusing = false;
    await expect(page.locator('.month').getByText('School run')).toBeVisible({ timeout: 10_000 });
    await expect(page.locator('h1')).toBeVisible();
  });

  test('one failing endpoint does not take the others with it', async ({ page, api, panel }) => {
    panel.allow(/loads failed; retrying/);
    await page.route('**/api/weather', (route) => route.fulfill({ status: 500, json: {} }));
    await page.goto('/');
    await expect(page.locator('.month').getByText('School run')).toBeVisible();
    await expect(page.getByText('Weather unavailable.')).toBeVisible();
  });
});

test.describe('pushed updates', () => {
  test('events.updated shows a new event without a reload', async ({ page, api, panel }) => {
    await panel.open();
    const month = page.locator('.month');
    await expect(month.getByText('Dentist')).toBeHidden();

    api.events.unshift(event(FAMILY, 'Dentist', `${addDays(TODAY, 2)}T09:00:00`, `${addDays(TODAY, 2)}T10:00:00`));
    await panel.emit('events.updated');

    await expect(month.getByText('Dentist')).toBeVisible();
    expect(panel.loads).toBe(1);
  });

  test('events.updated also refreshes the legend', async ({ page, api, panel }) => {
    await panel.open();
    await expect(page.locator('.legend button')).toHaveCount(2);
    api.calendars = [...api.calendars, { id: 3, name: 'Sam', color: '#3b3f8f' }];
    await panel.emit('events.updated');
    await expect(page.locator('.legend button')).toHaveCount(3);
  });

  test('weather.updated redraws the weather', async ({ page, api, panel }) => {
    await panel.open();
    await expect(page.getByText('Weather unavailable.')).toBeVisible();
    api.weather = {
      current: { temperature_2m: 72.4, weather_code: 0 },
      current_units: { temperature_2m: '°F' },
    };
    await panel.emit('weather.updated');
    await expect(page.locator('.temp')).toHaveText('72°F');
  });

  test('a reconnected stream refetches what it may have missed', async ({ page, api, panel }) => {
    await panel.open();
    api.events.unshift(event(FAMILY, 'Dentist', `${TODAY}T09:00:00`, `${TODAY}T10:00:00`));
    await page.evaluate(() => {
      window.__stream.drop();
      window.__stream.reopen();
    });
    await expect(page.locator('.month').getByText('Dentist')).toBeVisible();
    expect(panel.loads).toBe(1);
  });

  test('a stream that merely dropped does not reload the page', async ({ page, panel }) => {
    await panel.open();
    await page.evaluate(() => window.__stream.drop());
    await page.waitForTimeout(200);
    expect(panel.loads).toBe(1);
  });
});

test.describe('midnight', () => {
  const tomorrow = addDays(TODAY, 1);

  test('the heartbeat rolls the panel over to the new day', async ({ page, api, panel }) => {
    await panel.open();
    await panel.select('Day');
    await expect(panel.periodTitle).toHaveText(`day of ${TODAY}`);

    api.today = tomorrow;
    api.now = `${tomorrow}T00:00:30`;
    await panel.heartbeat();

    await expect(panel.periodTitle).toHaveText(`day of ${tomorrow}`);
    await expect(page.locator('h1')).toContainText(String(Number(tomorrow.slice(8))));
  });

  test('rolling over snaps back to today from wherever paging had got to', async ({
    page,
    api,
    panel,
  }) => {
    await panel.open();
    await panel.select('Day');
    await page.getByRole('button', { name: 'Previous', exact: true }).click();
    await expect(panel.periodTitle).toHaveText(`day of ${addDays(TODAY, -1)}`);

    api.today = tomorrow;
    api.now = `${tomorrow}T00:00:30`;
    await panel.heartbeat();

    await expect(panel.periodTitle).toHaveText(`day of ${tomorrow}`);
  });

  test('an unchanged date does not refetch anything', async ({ api, panel }) => {
    await panel.open();
    const before = api.requests.length;
    await panel.heartbeat();
    await panel.heartbeat();
    expect(api.requests.length).toBe(before);
  });
});

test.describe('a page that has stopped updating', () => {
  test('a stream closed for good reloads the page, once', async ({ page, panel }) => {
    panel.allow(/SSE stream closed for good/);
    await panel.open();
    await Promise.all([
      page.waitForEvent('load'),
      page.evaluate(() => window.__stream.kill()),
    ]);
    await expect(page.locator('h1')).toBeVisible();
    expect(panel.loads).toBe(2);

    // The backend is still down: the throttle has to outlive the reload.
    await page.evaluate(() => window.__stream.kill());
    await page.waitForTimeout(300);
    expect(panel.loads).toBe(2);
  });

  test('a stream gone quiet is reloaded by the watchdog', async ({ page, panel }) => {
    await page.clock.install();
    await panel.open();
    await Promise.all([page.waitForEvent('load'), page.clock.fastForward('01:50')]);
    expect(panel.loads).toBe(2);
  });

  test('heartbeats keep the watchdog away', async ({ page, panel }) => {
    await page.clock.install();
    await panel.open();
    for (let i = 0; i < 8; i++) {
      await page.clock.fastForward('00:30');
      await panel.heartbeat();
    }
    expect(panel.loads).toBe(1);
  });
});

test('a malformed heartbeat cannot take the page down', async ({ page, panel }) => {
  await panel.open();
  await page.evaluate(() => {
    const original = JSON.parse;
    JSON.parse = () => {
      throw new SyntaxError('bad heartbeat');
    };
    window.__stream.emit('heartbeat');
    JSON.parse = original;
  });
  await expect(page.locator('h1')).toBeVisible();
});
