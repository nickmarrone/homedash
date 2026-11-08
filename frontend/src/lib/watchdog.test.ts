import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { startWatchdog, type Watchdog, type WatchdogOptions } from './watchdog';

const STALE = 100_000;
const CHECK = 10_000;
const THROTTLE = 300_000;

describe('startWatchdog', () => {
	let reload: ReturnType<typeof vi.fn<() => void>>;
	let lastReloadAt: number;
	let watchdog: Watchdog;

	function start(overrides: WatchdogOptions = {}) {
		watchdog = startWatchdog({
			staleAfterMs: STALE,
			checkEveryMs: CHECK,
			minReloadIntervalMs: THROTTLE,
			reload,
			readLastReloadAt: () => lastReloadAt,
			writeLastReloadAt: (at) => {
				lastReloadAt = at;
			},
			...overrides
		});
	}

	beforeEach(() => {
		vi.useFakeTimers();
		vi.setSystemTime(1_000_000_000);
		reload = vi.fn<() => void>();
		lastReloadAt = 0;
	});

	afterEach(() => {
		watchdog?.stop();
		vi.useRealTimers();
	});

	it('does not reload a stream that is merely between heartbeats', () => {
		start();
		for (let elapsed = 0; elapsed < 10 * 60_000; elapsed += 30_000) {
			vi.advanceTimersByTime(30_000);
			watchdog.notify();
		}
		expect(reload).not.toHaveBeenCalled();
	});

	it('survives one missed heartbeat', () => {
		start();
		vi.advanceTimersByTime(STALE - 1);
		expect(reload).not.toHaveBeenCalled();
	});

	it('reloads once the stream has been quiet for the stale window', () => {
		start();
		vi.advanceTimersByTime(STALE);
		expect(reload).toHaveBeenCalledOnce();
	});

	it('treats the wait for the first message like any other gap', () => {
		// The clock is seeded at start, so a page cannot reload the instant it opens.
		start();
		vi.advanceTimersByTime(CHECK);
		expect(reload).not.toHaveBeenCalled();
	});

	it('measures quietness from the last message, not from start', () => {
		start();
		vi.advanceTimersByTime(STALE - CHECK);
		watchdog.notify();
		vi.advanceTimersByTime(STALE - CHECK);
		expect(reload).not.toHaveBeenCalled();
		vi.advanceTimersByTime(CHECK);
		expect(reload).toHaveBeenCalledOnce();
	});

	it('will not reload in a loop against a backend that stays down', () => {
		// A real reload resets module state and starts a fresh watchdog, so the
		// throttle has to come from storage. Simulated by restarting.
		start();
		vi.advanceTimersByTime(STALE);
		expect(reload).toHaveBeenCalledOnce();
		watchdog.stop();

		start();
		vi.advanceTimersByTime(STALE);
		expect(reload).toHaveBeenCalledOnce();
	});

	it('reloads again once the throttle has passed', () => {
		start();
		vi.advanceTimersByTime(STALE);
		expect(reload).toHaveBeenCalledOnce();
		vi.advanceTimersByTime(THROTTLE);
		expect(reload).toHaveBeenCalledTimes(2);
	});

	it('honours the boundary of the throttle exactly', () => {
		lastReloadAt = Date.now();
		start();
		vi.setSystemTime(Date.now() + THROTTLE - 1);
		watchdog.reloadNow();
		expect(reload).not.toHaveBeenCalled();
		vi.setSystemTime(Date.now() + 1);
		watchdog.reloadNow();
		expect(reload).toHaveBeenCalledOnce();
	});

	it('reloadNow does not wait for the stale window', () => {
		start();
		watchdog.reloadNow();
		expect(reload).toHaveBeenCalledOnce();
		expect(lastReloadAt).toBe(Date.now());
	});

	it('reloadNow shares the throttle with the staleness check', () => {
		start();
		watchdog.reloadNow();
		watchdog.reloadNow();
		expect(reload).toHaveBeenCalledOnce();
	});

	it('stops checking when stopped', () => {
		start();
		watchdog.stop();
		vi.advanceTimersByTime(STALE * 2);
		expect(reload).not.toHaveBeenCalled();
	});

	describe('default throttle storage', () => {
		const storage = (items: Record<string, string> = {}) => ({
			getItem: (key: string) => items[key] ?? null,
			setItem: (key: string, value: string) => {
				items[key] = value;
			}
		});

		function startWithRealStorage() {
			watchdog = startWatchdog({
				staleAfterMs: STALE,
				checkEveryMs: CHECK,
				minReloadIntervalMs: THROTTLE,
				reload
			});
		}

		it('survives the reload it is throttling', () => {
			vi.stubGlobal('sessionStorage', storage());
			startWithRealStorage();
			watchdog.reloadNow();
			watchdog.stop();
			startWithRealStorage();
			watchdog.reloadNow();
			expect(reload).toHaveBeenCalledOnce();
		});

		it('ignores a corrupt stored value', () => {
			vi.stubGlobal('sessionStorage', storage({ 'homedash:last-watchdog-reload': 'nope' }));
			startWithRealStorage();
			watchdog.reloadNow();
			expect(reload).toHaveBeenCalledOnce();
		});

		it('still reloads when storage is unavailable', () => {
			vi.stubGlobal('sessionStorage', {
				getItem() {
					throw new Error('denied');
				},
				setItem() {
					throw new Error('denied');
				}
			});
			startWithRealStorage();
			watchdog.reloadNow();
			expect(reload).toHaveBeenCalledOnce();
		});
	});
});
