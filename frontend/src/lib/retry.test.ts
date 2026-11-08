import { describe, expect, it, vi } from 'vitest';
import { startBackoff } from './retry';

/** A scheduler the test fires by hand, so each wait can be read off. */
function manualClock() {
	const pending = new Map<number, { fn: () => void; ms: number }>();
	let next = 1;
	return {
		schedule: (fn: () => void, ms: number) => {
			pending.set(next, { fn, ms });
			return next++;
		},
		cancel: (handle: number) => {
			pending.delete(handle);
		},
		get waits() {
			return [...pending.values()].map((entry) => entry.ms);
		},
		fire() {
			const entries = [...pending.entries()];
			pending.clear();
			for (const [, entry] of entries) entry.fn();
		}
	};
}

function setup(delaysMs = [10, 20, 30]) {
	const clock = manualClock();
	const retry = vi.fn();
	const backoff = startBackoff(retry, {
		delaysMs,
		schedule: clock.schedule,
		cancel: clock.cancel
	});
	return { clock, retry, backoff };
}

describe('startBackoff', () => {
	it('retries after the first delay', () => {
		const { clock, retry, backoff } = setup();
		backoff.fail();
		expect(clock.waits).toEqual([10]);
		expect(retry).not.toHaveBeenCalled();
		clock.fire();
		expect(retry).toHaveBeenCalledOnce();
	});

	it('coalesces a round of failures into one retry', () => {
		// Five loaders failing together is one outage, not five.
		const { clock, retry, backoff } = setup();
		for (let i = 0; i < 5; i++) backoff.fail();
		expect(clock.waits).toEqual([10]);
		clock.fire();
		expect(retry).toHaveBeenCalledOnce();
	});

	it('does not count coalesced failures toward the backoff', () => {
		const { backoff } = setup();
		backoff.fail();
		backoff.fail();
		expect(backoff.nextDelayMs).toBe(20);
	});

	it('slows down across consecutive failed rounds', () => {
		const { clock, backoff } = setup();
		const seen: number[] = [];
		for (let i = 0; i < 3; i++) {
			backoff.fail();
			seen.push(...clock.waits);
			clock.fire();
		}
		expect(seen).toEqual([10, 20, 30]);
	});

	it('settles on the last delay for as long as it takes', () => {
		const { clock, backoff } = setup();
		for (let i = 0; i < 50; i++) {
			backoff.fail();
			clock.fire();
		}
		backoff.fail();
		expect(clock.waits).toEqual([30]);
	});

	it('starts over from the short delay after a success', () => {
		const { clock, backoff } = setup();
		backoff.fail();
		clock.fire();
		backoff.fail();
		clock.fire();
		backoff.succeed();
		expect(backoff.nextDelayMs).toBe(10);
		backoff.fail();
		expect(clock.waits).toEqual([10]);
	});

	it('cancels a pending retry on success', () => {
		const { clock, retry, backoff } = setup();
		backoff.fail();
		backoff.succeed();
		expect(clock.waits).toEqual([]);
		clock.fire();
		expect(retry).not.toHaveBeenCalled();
	});

	it('cancels a pending retry on stop', () => {
		const { clock, retry, backoff } = setup();
		backoff.fail();
		backoff.stop();
		clock.fire();
		expect(retry).not.toHaveBeenCalled();
	});

	it('can arm a new retry while the previous one is running', () => {
		// The retry clears `pending` before calling back, so a retry that fails
		// synchronously is not mistaken for one still waiting.
		const clock = manualClock();
		const backoff = startBackoff(() => backoff.fail(), {
			delaysMs: [10, 20],
			schedule: clock.schedule,
			cancel: clock.cancel
		});
		backoff.fail();
		clock.fire();
		expect(clock.waits).toEqual([20]);
	});

	it('uses real timers by default', () => {
		vi.useFakeTimers();
		try {
			const retry = vi.fn();
			startBackoff(retry).fail();
			vi.advanceTimersByTime(999);
			expect(retry).not.toHaveBeenCalled();
			vi.advanceTimersByTime(1);
			expect(retry).toHaveBeenCalledOnce();
		} finally {
			vi.useRealTimers();
		}
	});
});
