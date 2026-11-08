import { addDays, event, FAMILY, TODAY } from './support/data.mjs';
import { expect, test } from './support/panel.mjs';

const VIEWS = [
  ['Day', 'day'],
  ['3 Day', 'next3'],
  ['5 Day', 'next5'],
  ['Week', 'week'],
  ['Month', 'month'],
];

test.describe('every view', () => {
  for (const [label, id] of VIEWS) {
    test(`${label} shows the appointments it was given`, async ({ page, panel }) => {
      await panel.open();
      await panel.select(label);
      await expect(panel.periodTitle).toHaveText(`${id} of ${TODAY}`);
      const calendar = page.locator(id === 'month' ? '.month' : '.columns');
      await expect(calendar.getByText('School run')).toBeVisible();
      await expect(calendar.getByText('Piano')).toBeVisible();
      await expect(calendar.getByText('Bin day')).toBeVisible();
    });

    test(`${label} does not scroll sideways`, async ({ page, panel }) => {
      await panel.open();
      await panel.select(label);
      await expect(panel.periodTitle).toHaveText(`${id} of ${TODAY}`);
      const overflow = await page.evaluate(
        () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
      );
      expect(overflow).toBeLessThanOrEqual(0);
    });
  }

  test('Agenda lists the coming days under their headings', async ({ page, panel }) => {
    await panel.open({ view: 'agenda' });
    const agenda = page.locator('.agenda');
    await expect(agenda.getByRole('heading', { name: 'Today' })).toBeVisible();
    await expect(agenda.getByRole('heading', { name: 'Tomorrow' })).toBeVisible();
    await expect(agenda.getByText('Swimming')).toBeVisible();
  });
});

test('a day with too many events says how many more there are', async ({ page, api, panel }) => {
  for (const title of ['One', 'Two', 'Three']) {
    api.events.push(event(FAMILY, title, `${TODAY}T20:00:00`, `${TODAY}T21:00:00`));
  }
  await panel.open();
  await expect(page.locator('.cell.today .more')).toHaveText('+3 more');
  await expect(page.locator('.cell.today .chip')).toHaveCount(3);
});

test('only finished events are struck through', async ({ page, panel }) => {
  await panel.open();
  await panel.select('Day');
  const row = (title) => page.locator('.columns li', { hasText: title });
  await expect(row('School run')).toHaveClass(/passed/);
  await expect(row('Piano')).not.toHaveClass(/passed/);
  await expect(row('Bin day')).not.toHaveClass(/passed/);
});

test('month chips are struck through too', async ({ page, panel }) => {
  await panel.open();
  const chip = (title) => page.locator('.cell.today .chip', { hasText: title });
  await expect(chip('School run')).toHaveClass(/passed/);
  await expect(chip('Piano')).not.toHaveClass(/passed/);
});

test('an event is struck through as soon as the heartbeat says it is over', async ({
  page,
  api,
  panel,
}) => {
  await panel.open();
  await panel.select('Day');
  const piano = page.locator('.columns li', { hasText: 'Piano' });
  await expect(piano).not.toHaveClass(/passed/);
  await panel.heartbeat({ now: `${api.today}T17:15:30` });
  await expect(piano).toHaveClass(/passed/);
});

test('today is the only highlighted day', async ({ page, panel }) => {
  await panel.open();
  await expect(page.locator('.cell.today')).toHaveCount(1);
  await expect(page.locator('.cell.today .daynum')).toHaveText('24');
});

test('hiding a calendar removes it from every view and survives a reboot', async ({
  page,
  panel,
}) => {
  await panel.open();
  await panel.select('Day');
  const columns = page.locator('.columns');
  await expect(columns.getByText('School run')).toBeVisible();

  await page.locator('.legend').getByRole('button', { name: 'Ada' }).click();
  await expect(columns.getByText('School run')).toBeHidden();
  await expect(columns.getByText('Piano')).toBeVisible();

  await panel.select('Week');
  await expect(columns.getByText('Swimming')).toBeHidden();

  await page.reload();
  await expect(columns.getByText('School run')).toBeHidden();
  await expect(
    page.locator('.legend').getByRole('button', { name: 'Ada' }),
  ).toHaveAttribute('aria-pressed', 'false');
});

test('a single calendar gets no legend', async ({ page, api, panel }) => {
  api.calendars = api.calendars.slice(0, 1);
  await panel.open();
  await expect(page.locator('.legend')).toHaveCount(0);
});

test('the view is remembered across a reboot', async ({ page, panel }) => {
  await panel.open();
  await panel.select('5 Day');
  await page.reload();
  await expect(panel.tab('5 Day')).toHaveAttribute('aria-pressed', 'true');
  await expect(panel.periodTitle).toHaveText(`next5 of ${TODAY}`);
});

test('paging walks whole periods and Today comes back', async ({ page, panel }) => {
  await panel.open();
  await panel.select('3 Day');
  await page.getByRole('button', { name: 'Next', exact: true }).click();
  await expect(panel.periodTitle).toHaveText(`next3 of ${addDays(TODAY, 3)}`);
  await page.getByRole('button', { name: 'Previous', exact: true }).click();
  await page.getByRole('button', { name: 'Previous', exact: true }).click();
  await expect(panel.periodTitle).toHaveText(`next3 of ${addDays(TODAY, -3)}`);
  await page.getByRole('button', { name: 'Today', exact: true }).click();
  await expect(panel.periodTitle).toHaveText(`next3 of ${TODAY}`);
});

test('changing view forgets where paging had got to', async ({ page, panel }) => {
  await panel.open();
  await panel.select('Week');
  await page.getByRole('button', { name: 'Next', exact: true }).click();
  await expect(panel.periodTitle).not.toHaveText(`week of ${TODAY}`);
  await panel.select('Day');
  await expect(panel.periodTitle).toHaveText(`day of ${TODAY}`);
});

test('tap targets are at least the 48px this project promises', async ({ page, panel }) => {
  await panel.open();
  await expect(panel.periodTitle).toBeVisible();
  const small = await page.evaluate(() =>
    [...document.querySelectorAll('main button')]
      .filter((b) => b.getBoundingClientRect().height < 48)
      .map((b) => b.textContent.trim() || b.getAttribute('aria-label')),
  );
  expect(small).toEqual([]);
});

test.describe('orientation', () => {
  test('portrait shows what is coming up under the calendar, landscape does not', async ({
    page,
    panel,
  }, testInfo) => {
    await panel.open();
    const upcoming = page.locator('.upcoming');
    if (testInfo.project.name === 'portrait') {
      await expect(upcoming.getByText('Swimming')).toBeVisible();
    } else {
      await expect(upcoming).toHaveCount(0);
    }
  });

  test('rotating refetches for the new shape', async ({ page, api, panel }) => {
    await page.setViewportSize({ width: 1920, height: 1080 });
    await panel.open();
    await expect(page.locator('.upcoming')).toHaveCount(0);

    await page.setViewportSize({ width: 1080, height: 1920 });
    await expect(page.locator('.upcoming').getByText('Swimming')).toBeVisible();
    await expect.poll(() => api.count('GET /api/photos?orientation=portrait')).toBeGreaterThan(0);
  });
});
