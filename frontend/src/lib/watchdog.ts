// Reloads the panel if the SSE stream goes quiet.
//
// A silently frozen display is the number one kiosk failure mode: the page
// still looks right, so nobody notices it stopped updating until an
// appointment is missed. The backend sends a heartbeat every 30 seconds, so a
// stream that has said nothing for several times that long is not quiet, it is
// broken.
//
// Reloading is a blunt recovery and deliberately so - it clears whatever state
// the page has gotten into, and there is nobody at the wall to press F5.

/** How long without a message before the page is stale. Three missed
 * heartbeats, so one dropped heartbeat is not enough to trigger a reload. */
const STALE_AFTER_MS = 100_000;

/** How often to check. Cheap, so it runs well under the stale threshold. */
const CHECK_EVERY_MS = 10_000;

/** At most one reload per this long. */
const MIN_RELOAD_INTERVAL_MS = 300_000;

// The throttle has to outlive the very thing it is throttling, so it is kept
// in sessionStorage rather than in a module variable: a reload resets module
// state, and a backend that is simply down would otherwise put the panel in a
// reload loop for as long as the outage lasts. sessionStorage is scoped to the
// tab and cleared when the kiosk browser restarts, which is the right lifetime
// - a fresh browser should get its first reload without waiting.
const LAST_RELOAD_KEY = 'homedash:last-watchdog-reload';

function readLastReload(): number {
	try {
		const raw = sessionStorage.getItem(LAST_RELOAD_KEY);
		const parsed = raw === null ? NaN : Number(raw);
		return Number.isFinite(parsed) ? parsed : 0;
	} catch {
		// Unavailable storage must not disable the watchdog; it only means the
		// throttle degrades to "reload every stale window".
		return 0;
	}
}

function writeLastReload(at: number): void {
	try {
		sessionStorage.setItem(LAST_RELOAD_KEY, String(at));
	} catch {
		// Nothing to do - see above.
	}
}

export interface Watchdog {
	/** Call on every message from the stream. */
	notify: () => void;
	stop: () => void;
}

export interface WatchdogOptions {
	staleAfterMs?: number;
	checkEveryMs?: number;
	minReloadIntervalMs?: number;
	/** Injectable for tests; defaults to reloading the page. */
	reload?: () => void;
	now?: () => number;
	readLastReloadAt?: () => number;
	writeLastReloadAt?: (at: number) => void;
}

export function startWatchdog(options: WatchdogOptions = {}): Watchdog {
	const staleAfterMs = options.staleAfterMs ?? STALE_AFTER_MS;
	const checkEveryMs = options.checkEveryMs ?? CHECK_EVERY_MS;
	const minReloadIntervalMs = options.minReloadIntervalMs ?? MIN_RELOAD_INTERVAL_MS;
	const now = options.now ?? (() => Date.now());
	const reload = options.reload ?? (() => location.reload());
	const readLastReloadAt = options.readLastReloadAt ?? readLastReload;
	const writeLastReloadAt = options.writeLastReloadAt ?? writeLastReload;

	// Seeded at start rather than at 0, so the wait for the first heartbeat is
	// treated like any other gap and the page cannot reload the instant it opens.
	let lastMessageAt = now();

	const timer = setInterval(() => {
		const at = now();
		if (at - lastMessageAt < staleAfterMs) return;

		const lastReloadAt = readLastReloadAt();
		if (lastReloadAt !== 0 && at - lastReloadAt < minReloadIntervalMs) return;

		writeLastReloadAt(at);
		reload();
	}, checkEveryMs);

	return {
		notify: () => {
			lastMessageAt = now();
		},
		stop: () => clearInterval(timer)
	};
}
