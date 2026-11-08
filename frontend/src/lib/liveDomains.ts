/** Everything on the panel that can go stale, and what keeps it current.
 *
 * A live domain is something the panel shows and the server can change: the
 * calendar, the weather, the photos, the speakers. Each needs the same things -
 * a first load, a retry when that fails, a refetch when the stream says it
 * changed, and another when the stream comes back after a drop. Those used to
 * be spread across five places per domain, and two of the ways to miss one were
 * silent: a named SSE event with no listener never fires and nothing reports it,
 * and a load rejecting with nobody watching leaves the panel blank for good,
 * because the watchdog only fires on a *quiet* stream and a backend that has
 * since come up keeps the stream perfectly healthy. Here a domain is declared
 * once, and everything it is owed follows from that.
 *
 * Plain `.ts` and free of runes on purpose: the state being kept current lives
 * in the page, and a rune in a plain module type-checks cleanly and then fails
 * at runtime. The stream, timers and reload are injectable so this is testable
 * without a DOM, the same shape retry.ts and watchdog.ts use.
 */
import type { Heartbeat } from '$lib/api';
import { startBackoff, type BackoffOptions } from '$lib/retry';
import { startWatchdog, type Watchdog, type WatchdogOptions } from '$lib/watchdog';

const STREAM_URL = '/api/events/stream';

/** `EventSource.CLOSED`, spelled out so this module loads where there is no
 * `EventSource` to read it from. Fixed by the spec. */
const CLOSED = 2;

export interface LiveDomain {
	/** Refetch. Must reject on failure, so a failed load can be retried. */
	load: () => Promise<unknown>;
	/** The named SSE events that mean this domain has changed. */
	events: string[];
}

/** The part of `EventSource` this module uses. */
export interface LiveStream {
	readonly readyState: number;
	addEventListener: (type: string, listener: (event: MessageEvent) => void) => void;
	close: () => void;
}

export interface LiveDomainsOptions {
	domains: LiveDomain[];
	onHeartbeat?: (heartbeat: Heartbeat) => void;
	/** Injectable for tests; defaults to opening the backend's SSE stream. */
	openStream?: () => LiveStream;
	backoff?: BackoffOptions;
	watchdog?: WatchdogOptions;
}

export interface LiveDomains {
	/** Load every domain and start listening. */
	start: () => void;
	/** Load every domain, retrying with backoff until a round comes back clean. */
	reloadAll: () => Promise<void>;
	/** For a one-off load a tap triggers. A failure arms the same retry as a
	 * failed round, which reloads everything - blunter than reissuing just this
	 * fetch, and correct for the reason that made it fail. */
	guard: (load: Promise<unknown>) => void;
	stop: () => void;
}

export function createLiveDomains(options: LiveDomainsOptions): LiveDomains {
	const { domains, onHeartbeat } = options;
	const openStream = options.openStream ?? (() => new EventSource(STREAM_URL));

	const backoff = startBackoff(() => {
		reloadAll();
	}, options.backoff);

	let watchdog: Watchdog | null = null;
	let stream: LiveStream | null = null;

	async function reloadAll(): Promise<void> {
		const results = await Promise.allSettled(domains.map((domain) => domain.load()));
		const failed = results.filter((r) => r.status === 'rejected');
		if (failed.length === 0) {
			backoff.succeed();
			return;
		}
		console.warn(`HomeDash: ${failed.length} of ${results.length} loads failed; retrying`);
		backoff.fail();
	}

	function guard(load: Promise<unknown>): void {
		load.catch(() => backoff.fail());
	}

	function listen(source: LiveStream, dog: Watchdog): void {
		// The first `open` is the initial connection, not a reconnection; only
		// the ones after a drop should trigger a refetch.
		let hasConnected = false;

		for (const name of new Set(domains.flatMap((domain) => domain.events))) {
			source.addEventListener(name, () => {
				dog.notify();
				for (const domain of domains) {
					if (domain.events.includes(name)) guard(domain.load());
				}
			});
		}

		source.addEventListener('heartbeat', (event: MessageEvent) => {
			dog.notify();
			try {
				onHeartbeat?.(JSON.parse(event.data) as Heartbeat);
			} catch {
				// A malformed heartbeat still proves the stream is alive, which is
				// most of its job. Don't let it take the page down.
			}
		});

		source.addEventListener('open', () => {
			dog.notify();
			// EventSource retries on its own, but nothing re-syncs what went
			// stale while it was gone, so anything could have changed.
			if (hasConnected) reloadAll();
			hasConnected = true;
		});

		source.addEventListener('error', () => {
			// EventSource reports both of its failure modes through this one
			// event, and they need opposite responses. A dropped connection
			// leaves readyState CONNECTING and the browser retries by itself; the
			// watchdog covers it going quiet. A non-2xx status or a wrong
			// Content-Type - a proxy answering 502 through a redeploy, say - is
			// fatal per spec: readyState goes to CLOSED and nothing will ever
			// reopen it, so the page has to be replaced. The watchdog's reload
			// throttle is what stops that becoming a loop against a backend that
			// is simply down.
			if (source.readyState !== CLOSED) return;
			// Only the fatal case is worth saying out loud. An ordinary drop is
			// routine on a page that stays open for months - any proxy timeout
			// does it - so logging those would bury the one that matters.
			console.warn('HomeDash: SSE stream closed for good; reloading');
			dog.reloadNow();
		});
	}

	return {
		start() {
			reloadAll();
			watchdog = startWatchdog(options.watchdog);
			stream = openStream();
			listen(stream, watchdog);
		},
		reloadAll,
		guard,
		stop() {
			watchdog?.stop();
			backoff.stop();
			stream?.close();
		}
	};
}
