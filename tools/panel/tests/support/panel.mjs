// The fixture every panel spec is built on: a page whose API and event stream
// are stubbed, and which fails the test on any error the panel raises.
import { test as base, expect } from '@playwright/test';
import { CALENDARS, NOW, PNG, TODAY, agenda, calendarView, household } from './data.mjs';

export { expect };

/** Replaces EventSource, because a stubbed HTTP response cannot stay open and
 * push. Runs in the page. The real thing is covered by real-backend.spec.mjs. */
function installFakeStream() {
  const sources = [];
  class FakeEventSource extends EventTarget {
    static CONNECTING = 0;
    static OPEN = 1;
    static CLOSED = 2;
    constructor(url) {
      super();
      this.url = url;
      this.readyState = FakeEventSource.OPEN;
      sources.push(this);
      queueMicrotask(() => this.dispatchEvent(new Event('open')));
    }
    close() {
      this.readyState = FakeEventSource.CLOSED;
    }
  }
  window.EventSource = FakeEventSource;
  const live = () => sources.filter((s) => s.readyState !== FakeEventSource.CLOSED);
  window.__stream = {
    emit(type, data = {}) {
      for (const s of live()) s.dispatchEvent(new MessageEvent(type, { data: JSON.stringify(data) }));
    },
    drop() {
      for (const s of live()) {
        s.readyState = FakeEventSource.CONNECTING;
        s.dispatchEvent(new Event('error'));
      }
    },
    reopen() {
      for (const s of live()) {
        s.readyState = FakeEventSource.OPEN;
        s.dispatchEvent(new Event('open'));
      }
    },
    kill() {
      for (const s of live()) {
        s.readyState = FakeEventSource.CLOSED;
        s.dispatchEvent(new Event('error'));
      }
    },
  };
}

class Api {
  today = TODAY;
  now = NOW;
  events = household();
  calendars = CALENDARS;
  weather = { current: null };
  photos = { dwell_seconds: 30, idle_minutes: 999, photos: [] };
  /** null answers 503, which is how the backend says "no speakers here". */
  music = null;
  library = { artists: [], albums: {}, tracks: {} };
  /** While true every /api request is refused, as when the container is down. */
  refusing = false;
  artFails = false;
  /** Every request seen, as `METHOD /path?query`. */
  requests = [];
  posts = [];
  #holds = [];

  count(prefix) {
    return this.requests.filter((r) => r.startsWith(prefix)).length;
  }

  /** Stalls requests matching `prefix` until the returned function is called. */
  hold(prefix) {
    let release;
    const gate = new Promise((resolve) => (release = resolve));
    this.#holds.push({ prefix, gate });
    return release;
  }

  #respond(route, url, method) {
    const path = url.pathname;
    if (/^\/api\/photos\/\d+/.test(path)) {
      return route.fulfill({ contentType: 'image/png', body: PNG });
    }
    if (path.startsWith('/api/music/art/')) {
      return this.artFails
        ? route.fulfill({ status: 404, body: '' })
        : route.fulfill({ contentType: 'image/png', body: PNG });
    }
    switch (path) {
      case '/api/calendars':
        return route.fulfill({ json: this.calendars });
      case '/api/agenda':
        return route.fulfill({ json: agenda(this.events) });
      case '/api/weather':
        return route.fulfill({ json: this.weather });
      case '/api/photos':
        return route.fulfill({ json: this.photos });
      case '/api/calendar': {
        const view = url.searchParams.get('view');
        const anchor = url.searchParams.get('anchor');
        return route.fulfill({
          json: calendarView(view, anchor, this.today, this.now, this.events),
        });
      }
      case '/api/music/players':
        return this.music === null
          ? route.fulfill({ status: 503, json: { detail: 'music is not configured' } })
          : route.fulfill({ json: this.music });
      case '/api/music/library': {
        const kind = url.searchParams.get('kind');
        const parent = url.searchParams.get('parent');
        const items =
          kind === 'artists' ? this.library.artists : (this.library[kind]?.[parent] ?? []);
        return route.fulfill({ json: { kind, parent, items } });
      }
    }
    const post = path.match(/^\/api\/music\/players\/(\d+)\/(transport|volume|play)$/);
    if (post && method === 'POST') {
      const body = route.request().postDataJSON();
      this.posts.push({ command: post[2], body });
      if (post[2] === 'transport' && this.music) {
        const states = { play: 'play', pause: 'pause', stop: 'stop' };
        if (states[body.action]) this.music.players[0].state = states[body.action];
      }
      return route.fulfill({ json: { ok: true } });
    }
    throw new Error(`The panel asked for ${method} ${path}, which this stub does not know`);
  }

  async handle(route) {
    const request = route.request();
    const url = new URL(request.url());
    const label = `${request.method()} ${url.pathname}${url.search}`;
    this.requests.push(label);
    if (this.refusing) return route.abort('connectionrefused');
    const hold = this.#holds.find((h) => label.includes(h.prefix));
    if (hold) {
      this.#holds.splice(this.#holds.indexOf(hold), 1);
      await hold.gate;
    }
    return this.#respond(route, url, request.method());
  }
}

class Panel {
  loads = 0;
  allowed = [];
  screen = 'on';

  constructor(page, api) {
    this.page = page;
    this.api = api;
  }

  /** Open the panel, optionally on a remembered view, and optionally at a URL
   * other than the root - the kiosk's query string is how a panel is told
   * about its frame. */
  async open({ view, path = '/' } = {}) {
    if (view) {
      await this.page.addInitScript((v) => localStorage.setItem('homedash:view', v), view);
    }
    await this.page.goto(path);
    if (view === 'agenda') await this.heartbeat();
    await expect(this.page.locator('h1')).toBeVisible();
  }

  emit(type, data) {
    return this.page.evaluate(([t, d]) => window.__stream.emit(t, d), [type, data]);
  }

  /** A screen state given here sticks, as it would on the real server. */
  heartbeat(overrides = {}) {
    if ('screen' in overrides) this.screen = overrides.screen;
    return this.emit('heartbeat', {
      today: this.api.today,
      now: this.api.now,
      ...overrides,
      screen: this.screen,
    });
  }

  /** Let time pass the way it does on the wall: with a heartbeat every 30
   * seconds. A bare fastForward past 100 seconds is a silent stream, and the
   * watchdog correctly reloads the page in the middle of the test. */
  async passTime(seconds) {
    for (let left = seconds; left > 0; left -= 30) {
      await this.page.clock.fastForward(Math.min(left, 30) * 1000);
      await this.heartbeat();
    }
  }

  tab(name) {
    return this.page.getByRole('navigation', { name: 'Calendar view' }).getByRole('button', {
      name,
      exact: true,
    });
  }

  async select(name) {
    await this.tab(name).click();
    await expect(this.tab(name)).toHaveAttribute('aria-pressed', 'true');
  }

  get periodTitle() {
    return this.page.locator('.nav h2');
  }

  /** Tolerate a console message the test is deliberately provoking. */
  allow(pattern) {
    this.allowed.push(pattern);
  }
}

export const test = base.extend({
  api: async ({}, use) => {
    await use(new Api());
  },

  panel: async ({ page, api }, use) => {
    const panel = new Panel(page, api);
    const issues = [];
    page.on('pageerror', (e) => issues.push(`pageerror: ${e.message}`));
    page.on('console', (m) => {
      if (m.type() !== 'error' && m.type() !== 'warning') return;
      // Chrome's own line for a non-2xx. It has no URL, and every case that
      // matters here is asserted on directly.
      if (m.text().startsWith('Failed to load resource')) return;
      issues.push(`${m.type()}: ${m.text()}`);
    });
    page.on('load', () => (panel.loads += 1));

    await page.addInitScript(installFakeStream);
    await page.route('**/api/**', (route) => api.handle(route));

    await use(panel);

    const unexpected = issues.filter((i) => !panel.allowed.some((p) => p.test(i)));
    expect(unexpected, 'the panel logged errors').toEqual([]);
  },
});
