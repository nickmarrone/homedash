import { TRACK, player } from './support/data.mjs';
import { expect, test } from './support/panel.mjs';

const connected = (players, library = false) => ({ connected: true, library, players });
const bar = (page) => page.locator('.music-slot .bar');
const overlay = (page) => page.locator('.overlay');
const button = (scope, name) => scope.getByRole('button', { name, exact: true });

const ARTIST = { id: 'a1', name: 'Radiohead', sort_name: 'Radiohead' };
const ALBUM = { id: 'kid-a', name: 'Kid A', artist: 'Radiohead', year: 2000 };
const tracks = ['Everything', 'Kid A', 'National Anthem'].map((title, i) => ({
  id: `t${i + 1}`,
  title,
  artist: 'Radiohead',
  album: 'Kid A',
  duration_ms: 125_000,
  track_number: i + 1,
}));

function withLibrary(api) {
  api.music = connected([player()], true);
  api.library = {
    artists: [ARTIST],
    albums: { a1: [ALBUM] },
    tracks: { 'kid-a': tracks },
  };
}

test('a panel with no speakers shows no music at all', async ({ page, panel }) => {
  await panel.open();
  await expect(bar(page)).toHaveCount(0);
  await expect(button(page, 'Music')).toHaveCount(0);
});

test('stopped speakers with a library get a way in, and no bar', async ({ page, api, panel }) => {
  withLibrary(api);
  await panel.open();
  await expect(bar(page)).toHaveCount(0);
  await expect(button(page, 'Music')).toBeVisible();
});

test('stopped speakers with no library show nothing', async ({ page, api, panel }) => {
  api.music = connected([player()]);
  await panel.open();
  await expect(bar(page)).toHaveCount(0);
  await expect(button(page, 'Music')).toHaveCount(0);
});

test('speakers still asleep at boot do not break the panel', async ({ page, api, panel }) => {
  api.music = { connected: false, library: false, players: [] };
  await panel.open();
  await expect(bar(page)).toHaveCount(0);
});

test('the bar names what is playing', async ({ page, api, panel }) => {
  api.music = connected([player({ state: 'play', now_playing: TRACK })]);
  await panel.open();
  await expect(bar(page).locator('.title')).toHaveText(TRACK.title);
  await expect(bar(page).locator('.subtitle')).toHaveText('Radiohead - Kid A');
});

test('pausing keeps the bar, and with it the way to resume', async ({ page, api, panel }) => {
  api.music = connected([player({ state: 'play', now_playing: TRACK })]);
  await panel.open();
  await button(bar(page), 'Pause').click();

  await expect(button(bar(page), 'Play')).toBeVisible();
  expect(api.posts).toEqual([{ command: 'transport', body: { action: 'pause' } }]);

  await button(bar(page), 'Play').click();
  await expect(button(bar(page), 'Pause')).toBeVisible();
});

test('skip buttons reach the speaker', async ({ page, api, panel }) => {
  api.music = connected([player({ state: 'play', now_playing: TRACK })]);
  await panel.open();
  await button(bar(page), 'Next track').click();
  await button(bar(page), 'Previous track').click();
  await expect
    .poll(() => api.posts.map((p) => p.body.action))
    .toEqual(['next', 'previous']);
});

test('a speaker refusing a command does not disturb the calendar', async ({
  page,
  api,
  panel,
}) => {
  api.music = connected([player({ state: 'play', now_playing: TRACK })]);
  await panel.open();
  await page.route('**/api/music/players/1/transport', (route) =>
    route.fulfill({ status: 502, json: {} }),
  );
  await button(bar(page), 'Pause').click();
  await page.waitForTimeout(100);
  await expect(page.locator('h1')).toBeVisible();
  await expect(button(bar(page), 'Pause')).toBeVisible();
});

test('cover art that will not load falls back to the placeholder', async ({
  page,
  api,
  panel,
}) => {
  api.artFails = true;
  api.music = connected([player({ state: 'play', now_playing: TRACK })]);
  await panel.open();
  await expect(bar(page).locator('.placeholder')).toBeVisible();
  await expect(bar(page).locator('img')).toHaveCount(0);
});

test('music.updated redraws the bar', async ({ page, api, panel }) => {
  api.music = connected([player({ state: 'play', now_playing: TRACK })]);
  await panel.open();
  api.music.players[0].now_playing = { ...TRACK, title: 'Optimistic' };
  await panel.emit('music.updated');
  await expect(bar(page).locator('.title')).toHaveText('Optimistic');
});

test('bedtime covers the music overlay too', async ({ page, api, panel }) => {
  withLibrary(api);
  await panel.open();
  await button(page, 'Music').click();
  await expect(overlay(page)).toBeVisible();
  await panel.heartbeat({ screen: 'off' });
  await expect(page.locator('.blank')).toBeVisible();
  const top = await page.evaluate(
    () => document.elementFromPoint(innerWidth / 2, innerHeight / 2).className,
  );
  expect(top).toContain('blank');
});

test('the remembered speaker is the one controlled', async ({ page, api, panel }) => {
  await page.addInitScript(() => localStorage.setItem('homedash:player', '2'));
  api.music = connected([
    player({ id: 1, name: 'Lounge', state: 'play', now_playing: TRACK }),
    player({ id: 2, name: 'Kitchen', state: 'play', now_playing: { ...TRACK, title: 'Idioteque' } }),
  ]);
  await panel.open();
  await expect(bar(page).locator('.title')).toHaveText('Idioteque');
});

test.describe('the library', () => {
  async function openLibrary(page, api, panel) {
    withLibrary(api);
    await panel.open();
    await button(page, 'Music').click();
    await expect(overlay(page).getByRole('heading', { name: 'Artists' })).toBeVisible();
  }

  async function intoAlbum(page) {
    await button(overlay(page), 'Radiohead').click();
    await overlay(page).getByRole('button', { name: /Kid A/ }).click();
    await expect(button(overlay(page), 'Play album')).toBeVisible();
  }

  test('opens on the library when nothing is playing', async ({ page, api, panel }) => {
    await openLibrary(page, api, panel);
    await expect(button(overlay(page), 'Library')).toHaveAttribute('aria-pressed', 'true');
  });

  test('back returns through the artist, not to the top', async ({ page, api, panel }) => {
    await openLibrary(page, api, panel);
    await intoAlbum(page);

    await button(overlay(page), 'Back').click();
    await expect(overlay(page).getByRole('heading', { name: 'Radiohead' })).toBeVisible();

    await button(overlay(page), 'Back').click();
    await expect(overlay(page).getByRole('heading', { name: 'Artists' })).toBeVisible();
    await expect(button(overlay(page), 'Back')).toHaveCount(0);
  });

  test('a track plays from there to the end of the album', async ({ page, api, panel }) => {
    await openLibrary(page, api, panel);
    await intoAlbum(page);
    await overlay(page).getByRole('button', { name: /National Anthem/ }).click();
    await expect.poll(() => api.posts).toEqual([
      { command: 'play', body: { track_ids: ['t3'], parent_album_id: 'kid-a' } },
    ]);
  });

  test('play album sends the album and moves to Now Playing', async ({ page, api, panel }) => {
    await openLibrary(page, api, panel);
    await intoAlbum(page);
    await button(overlay(page), 'Play album').click();
    await expect
      .poll(() => api.posts)
      .toEqual([{ command: 'play', body: { album_id: 'kid-a' } }]);
    await expect(button(overlay(page), 'Now Playing')).toHaveAttribute('aria-pressed', 'true');
  });

  test('a slow answer for the level just left is ignored', async ({ page, api, panel }) => {
    await openLibrary(page, api, panel);
    const release = api.hold('kind=albums');
    await button(overlay(page), 'Radiohead').click();
    await expect(overlay(page).getByRole('heading', { name: 'Radiohead' })).toBeVisible();
    await button(overlay(page), 'Back').click();
    await expect(button(overlay(page), 'Radiohead')).toBeVisible();

    release();
    await page.waitForTimeout(200);
    await expect(overlay(page).getByRole('heading', { name: 'Artists' })).toBeVisible();
    await expect(button(overlay(page), 'Radiohead')).toBeVisible();
  });

  test('a library that cannot be reached says so', async ({ page, api, panel }) => {
    withLibrary(api);
    await page.route('**/api/music/library*', (route) => route.fulfill({ status: 502, json: {} }));
    await panel.open();
    await button(page, 'Music').click();
    await expect(overlay(page).getByText('Could not reach the music library.')).toBeVisible();
  });

  test('closes back to the calendar', async ({ page, api, panel }) => {
    await openLibrary(page, api, panel);
    await button(overlay(page), 'Close music').click();
    await expect(overlay(page)).toHaveCount(0);
  });

  test.describe('the A-Z rail', () => {
    // "The Beatles" sorted under B, among enough artists to need scrolling.
    const artists = [
      { id: 'x', name: '65daysofstatic', sort_name: '65daysofstatic' },
      ...'ABCDEFGHIJKLMNOPRSTUVWY'.split('').flatMap((letter) =>
        Array.from({ length: 14 }, (_, i) => ({
          id: `${letter}${i}`,
          name: `${letter}rtist ${i}`,
          sort_name: `${letter}rtist ${i}`,
        })),
      ),
      { id: 'b', name: 'The Beatles', sort_name: 'Beatles, The' },
    ].sort((a, b) => a.sort_name.localeCompare(b.sort_name));

    async function openRail(page, api, panel) {
      withLibrary(api);
      api.library.artists = artists;
      await panel.open();
      await button(page, 'Music').click();
      return overlay(page).getByRole('navigation', { name: 'Jump to letter' });
    }

    const topRow = (page) =>
      page.evaluate(() => {
        const list = document.querySelector('ul.rows');
        const top = list.getBoundingClientRect().top;
        return [...list.children]
          .find((li) => li.getBoundingClientRect().bottom > top + 2)
          .innerText.trim();
      });

    test('only appears for a library long enough to need it', async ({ page, api, panel }) => {
      await openLibrary(page, api, panel);
      await expect(overlay(page).getByRole('navigation', { name: 'Jump to letter' })).toHaveCount(0);
    });

    test('a tapped letter jumps to it', async ({ page, api, panel }) => {
      const rail = await openRail(page, api, panel);
      await button(rail, 'S').click();
      await expect.poll(() => topRow(page)).toBe('Srtist 0');
    });

    test('indexes on the sort name, so T is not The Beatles', async ({ page, api, panel }) => {
      const rail = await openRail(page, api, panel);
      await button(rail, 'T').click();
      await expect.poll(() => topRow(page)).toBe('Trtist 0');
    });

    test('an empty letter lands on the next one that has something', async ({
      page,
      api,
      panel,
    }) => {
      const rail = await openRail(page, api, panel);
      await button(rail, 'Q').click();
      await expect.poll(() => topRow(page)).toBe('Rrtist 0');
    });

    test('numbers file under #', async ({ page, api, panel }) => {
      const rail = await openRail(page, api, panel);
      await button(rail, 'S').click();
      await expect.poll(() => topRow(page)).toBe('Srtist 0');
      await button(rail, '#').click();
      await expect.poll(() => topRow(page)).toBe('65daysofstatic');
    });

    test('a drag scrubs through the letters', async ({ page, api, panel }) => {
      const rail = await openRail(page, api, panel);
      const box = await rail.boundingBox();
      const yFor = (i) => box.y + ((i + 0.5) / 27) * box.height;
      await page.mouse.move(box.x + box.width / 2, yFor(1));
      await page.mouse.down();
      for (let i = 2; i <= 19; i++) await page.mouse.move(box.x + box.width / 2, yFor(i));
      await page.mouse.up();
      await expect.poll(() => topRow(page)).toBe('Srtist 0');
    });
  });
});
