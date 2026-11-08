import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { startIdleTimer, type IdleTimer } from './idle';

const IDLE = 60_000;
const CHECK = 5_000;

describe('startIdleTimer', () => {
	let target: EventTarget;
	let timer: IdleTimer;
	const onIdle = vi.fn();
	const onActive = vi.fn();

	function start() {
		timer = startIdleTimer({ idleAfterMs: IDLE, checkEveryMs: CHECK, onIdle, onActive });
	}
	const fire = (name: string) => target.dispatchEvent(new Event(name));

	beforeEach(() => {
		vi.useFakeTimers();
		onIdle.mockReset();
		onActive.mockReset();
		target = new EventTarget();
		vi.stubGlobal('window', target);
	});

	afterEach(() => {
		timer?.stop();
		vi.useRealTimers();
	});

	it('does not go idle early', () => {
		start();
		vi.advanceTimersByTime(IDLE - 1);
		expect(onIdle).not.toHaveBeenCalled();
	});

	it('goes idle after the timeout', () => {
		start();
		vi.advanceTimersByTime(IDLE);
		expect(onIdle).toHaveBeenCalledOnce();
	});

	it('fires once, not on every check while idle', () => {
		start();
		vi.advanceTimersByTime(IDLE * 5);
		expect(onIdle).toHaveBeenCalledOnce();
	});

	it.each(['pointerdown', 'keydown', 'wheel'])('counts %s as activity', (name) => {
		start();
		vi.advanceTimersByTime(IDLE - CHECK);
		fire(name);
		vi.advanceTimersByTime(IDLE - CHECK);
		expect(onIdle).not.toHaveBeenCalled();
	});

	it('does not count mousemove, so a stray cursor cannot hold it off', () => {
		start();
		vi.advanceTimersByTime(IDLE - CHECK);
		fire('mousemove');
		vi.advanceTimersByTime(CHECK);
		expect(onIdle).toHaveBeenCalledOnce();
	});

	it('reports activity once on waking', () => {
		start();
		vi.advanceTimersByTime(IDLE);
		fire('pointerdown');
		fire('pointerdown');
		expect(onActive).toHaveBeenCalledOnce();
	});

	it('does not report activity while already active', () => {
		start();
		fire('pointerdown');
		expect(onActive).not.toHaveBeenCalled();
	});

	it('can go idle again after waking', () => {
		start();
		vi.advanceTimersByTime(IDLE);
		timer.notify();
		vi.advanceTimersByTime(IDLE);
		expect(onIdle).toHaveBeenCalledTimes(2);
	});

	it('counts activity that came from notify', () => {
		start();
		vi.advanceTimersByTime(IDLE - CHECK);
		timer.notify();
		vi.advanceTimersByTime(CHECK);
		expect(onIdle).not.toHaveBeenCalled();
	});

	it('stops checking and listening when stopped', () => {
		start();
		vi.advanceTimersByTime(IDLE);
		timer.stop();
		fire('pointerdown');
		expect(onActive).not.toHaveBeenCalled();
		onIdle.mockClear();
		vi.advanceTimersByTime(IDLE * 2);
		expect(onIdle).not.toHaveBeenCalled();
	});
});
