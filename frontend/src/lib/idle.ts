// Notices when nobody has touched the panel for a while.
//
// Shaped like watchdog.ts - start it, it returns a handle with stop() - and
// deliberately not a rune module: this has no reactive state of its own, it
// just calls back, and runes only compile in .svelte and .svelte.ts files.

/** Activity that counts. Deliberately not mousemove: there is no mouse on the
 * wall, and on a desktop browser a stray cursor twitch would make the
 * screensaver almost impossible to reach while testing it. */
const ACTIVITY_EVENTS = ['pointerdown', 'keydown', 'wheel'] as const;

/** How often idleness is checked. The screensaver appears within this long of
 * the timeout, which at minute-scale timeouts nobody can perceive. */
const CHECK_EVERY_MS = 5_000;

export interface IdleTimer {
	/** Count activity that did not come from a DOM event - dismissing the
	 * screensaver, for instance, which happens on an overlay that swallows the
	 * tap before it reaches the window. */
	notify: () => void;
	stop: () => void;
}

export interface IdleTimerOptions {
	idleAfterMs: number;
	/** Fired once when the panel goes idle, not repeatedly while it stays so. */
	onIdle: () => void;
	/** Fired once when activity resumes after idling. */
	onActive?: () => void;
	checkEveryMs?: number;
	now?: () => number;
}

export function startIdleTimer(options: IdleTimerOptions): IdleTimer {
	const checkEveryMs = options.checkEveryMs ?? CHECK_EVERY_MS;
	const now = options.now ?? (() => Date.now());

	let lastActivityAt = now();
	let idle = false;

	const notify = () => {
		lastActivityAt = now();
		if (idle) {
			idle = false;
			options.onActive?.();
		}
	};

	// Capture, so a tap that lands on a button still counts as activity: the
	// button's handler would otherwise be the only thing that sees it.
	// Passive, because none of these ever need preventDefault and a passive
	// listener cannot delay a scroll.
	const listenerOptions = { passive: true, capture: true } as const;
	for (const name of ACTIVITY_EVENTS) {
		window.addEventListener(name, notify, listenerOptions);
	}

	const timer = setInterval(() => {
		if (idle) return;
		if (now() - lastActivityAt < options.idleAfterMs) return;
		idle = true;
		options.onIdle();
	}, checkEveryMs);

	return {
		notify,
		stop: () => {
			clearInterval(timer);
			for (const name of ACTIVITY_EVENTS) {
				window.removeEventListener(name, notify, listenerOptions);
			}
		}
	};
}
