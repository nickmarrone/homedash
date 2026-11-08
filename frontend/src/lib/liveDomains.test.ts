import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
	createLiveDomains,
	type LiveDomain,
	type LiveDomains,
	type LiveDomainsOptions,
	type LiveStream
} from './liveDomains';

const OPEN = 1;
const CONNECTING = 0;
const CLOSED = 2;

class FakeStream implements LiveStream {
	readyState = OPEN;
	closed = false;
	private listeners = new Map<string, ((event: MessageEvent) => void)[]>();

	addEventListener(type: string, listener: (event: MessageEvent) => void) {
		this.listeners.set(type, [...(this.listeners.get(type) ?? []), listener]);
	}

	close() {
		this.closed = true;
	}

	emit(type: string, data = '') {
		for (const listener of this.listeners.get(type) ?? []) {
			listener({ type, data } as MessageEvent);
		}
	}

	get names() {
		return [...this.listeners.keys()];
	}
}

/** A domain whose loader counts its calls and fails while `failing` is set. */
function domain(events: string[]) {
	const state = { failing: false };
	const load = vi.fn(async () => {
		if (state.failing) throw new Error('refused');
	});
	return { load, events, state } satisfies LiveDomain & { state: unknown };
}

const settle = () => new Promise((resolve) => setTimeout(resolve));

let stream: FakeStream;
let retries: (() => void)[];
let reload: ReturnType<typeof vi.fn<() => void>>;
let live: LiveDomains;

function start(domains: LiveDomain[], extra: Partial<LiveDomainsOptions> = {}) {
	live = createLiveDomains({
		domains,
		openStream: () => stream,
		backoff: {
			schedule: (fn) => retries.push(fn),
			cancel: (handle) => retries.splice(handle - 1, 1)
		},
		watchdog: { reload, readLastReloadAt: () => 0, writeLastReloadAt: () => {} },
		...extra
	});
	live.start();
	return live;
}

beforeEach(() => {
	stream = new FakeStream();
	retries = [];
	reload = vi.fn<() => void>();
	vi.spyOn(console, 'warn').mockImplementation(() => {});
});

afterEach(() => {
	live.stop();
	vi.useRealTimers();
});

describe('loading', () => {
	it('loads every domain when started', () => {
		const domains = [domain(['a']), domain(['b'])];
		start(domains);
		expect(domains[0].load).toHaveBeenCalledTimes(1);
		expect(domains[1].load).toHaveBeenCalledTimes(1);
	});

	it('retries the whole round after one failure, and stops once it is clean', async () => {
		const good = domain(['a']);
		const bad = domain(['b']);
		bad.state.failing = true;
		start([good, bad]);
		await settle();
		expect(retries).toHaveLength(1);

		bad.state.failing = false;
		retries.shift()?.();
		await settle();

		expect(good.load).toHaveBeenCalledTimes(2);
		expect(retries).toHaveLength(0);
	});

	it('does not retry a clean round', async () => {
		start([domain(['a'])]);
		await settle();
		expect(retries).toHaveLength(0);
	});

	it('a failed one-off load arms the retry', async () => {
		start([domain(['a'])]);
		await settle();
		live.guard(Promise.reject(new Error('refused')));
		await settle();
		expect(retries).toHaveLength(1);
	});
});

describe('pushed updates', () => {
	it('listens for exactly the events the domains declare', () => {
		start([domain(['a.updated']), domain(['a.updated', 'b.updated'])]);
		expect(stream.names.filter((name) => name.endsWith('.updated')).sort()).toEqual([
			'a.updated',
			'b.updated'
		]);
	});

	it('reloads only the domains that answer to the event', () => {
		const a = domain(['a.updated']);
		const b = domain(['b.updated']);
		start([a, b]);

		stream.emit('a.updated');

		expect(a.load).toHaveBeenCalledTimes(2);
		expect(b.load).toHaveBeenCalledTimes(1);
	});

	it('reloads every domain sharing an event', () => {
		const first = domain(['shared']);
		const second = domain(['shared']);
		start([first, second]);

		stream.emit('shared');

		expect(first.load).toHaveBeenCalledTimes(2);
		expect(second.load).toHaveBeenCalledTimes(2);
	});

	it('a load an event triggered is retried if it fails', async () => {
		const a = domain(['a.updated']);
		start([a]);
		await settle();

		a.state.failing = true;
		stream.emit('a.updated');
		await settle();

		expect(retries).toHaveLength(1);
	});
});

describe('the stream', () => {
	it('the first open is not a reconnection', () => {
		const a = domain(['a']);
		start([a]);
		stream.emit('open');
		expect(a.load).toHaveBeenCalledTimes(1);
	});

	it('reloads everything when the stream comes back', () => {
		const a = domain(['a']);
		start([a]);
		stream.emit('open');
		stream.emit('open');
		expect(a.load).toHaveBeenCalledTimes(2);
	});

	it('passes heartbeats on', () => {
		const onHeartbeat = vi.fn();
		start([domain(['a'])], { onHeartbeat });
		stream.emit('heartbeat', '{"today":"2026-11-08"}');
		expect(onHeartbeat).toHaveBeenCalledWith({ today: '2026-11-08' });
	});

	it('survives a malformed heartbeat', () => {
		const onHeartbeat = vi.fn();
		start([domain(['a'])], { onHeartbeat });
		expect(() => stream.emit('heartbeat', 'not json')).not.toThrow();
		expect(onHeartbeat).not.toHaveBeenCalled();
	});

	it('leaves a dropped connection to the browser', () => {
		start([domain(['a'])]);
		stream.readyState = CONNECTING;
		stream.emit('error');
		expect(reload).not.toHaveBeenCalled();
	});

	it('replaces the page when the stream is closed for good', () => {
		start([domain(['a'])]);
		stream.readyState = CLOSED;
		stream.emit('error');
		expect(reload).toHaveBeenCalledTimes(1);
	});

	it.each(['heartbeat', 'open', 'a.updated'])('%s keeps the watchdog away', (message) => {
		vi.useFakeTimers();
		start([domain(['a.updated'])]);
		for (let i = 0; i < 8; i++) {
			vi.advanceTimersByTime(30_000);
			stream.emit(message, '{}');
		}
		expect(reload).not.toHaveBeenCalled();
	});

	it('the watchdog reloads a stream gone quiet', () => {
		vi.useFakeTimers();
		start([domain(['a'])]);
		vi.advanceTimersByTime(120_000);
		expect(reload).toHaveBeenCalled();
	});
});

describe('stopping', () => {
	it('closes the stream and cancels a pending retry', async () => {
		const a = domain(['a']);
		a.state.failing = true;
		start([a]);
		await settle();
		expect(retries).toHaveLength(1);

		live.stop();

		expect(stream.closed).toBe(true);
		expect(retries).toHaveLength(0);
	});
});
