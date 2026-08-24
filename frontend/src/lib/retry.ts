/** Retrying a load that failed, with backoff.
 *
 * The panel and the container do not start together. On a Pi the browser is
 * usually up first, so the opening round of fetches can all be refused - and
 * every one of them was previously an unhandled rejection with nothing left
 * behind to try again. The panel then sat on a masthead and an empty page for
 * as long as the stream stayed healthy, because the SSE watchdog only reloads
 * when the stream goes *quiet*, and once the backend arrives the stream is
 * perfectly fine. Nothing else was ever going to notice.
 *
 * Backoff rather than a fixed interval because the two failures look the same
 * from here and want opposite things: a container still booting should be
 * retried in a second, and one that is down for the evening should not be
 * asked 3,600 times an hour.
 *
 * Plain `.ts` and free of runes on purpose - this holds no reactive state, and
 * a rune in a plain module type-checks cleanly and then fails at runtime. The
 * timer functions are injectable so this is testable without a DOM, the same
 * shape watchdog.ts uses.
 */

/** Roughly: soon, then a few seconds, then settle at half a minute. Long
 * enough to be polite to a backend that is genuinely down, short enough that
 * nobody standing at the wall watches it stay blank. */
const DEFAULT_DELAYS_MS = [1_000, 2_000, 5_000, 10_000, 30_000];

export interface BackoffOptions {
	/** Successive waits. The last is repeated for as long as it takes. */
	delaysMs?: number[];
	schedule?: (fn: () => void, ms: number) => number;
	cancel?: (handle: number) => void;
}

export interface Backoff {
	/** Something did not load. Ensures a retry is pending, and slows down if
	 * this is not the first failure in a row. */
	fail: () => void;
	/** Everything loaded. Cancels any pending retry and forgets the backoff,
	 * so the next bad patch starts from the short delay again. */
	succeed: () => void;
	stop: () => void;
	/** For tests and for reasoning: the wait the next failure would use. */
	readonly nextDelayMs: number;
}

export function startBackoff(retry: () => void, options: BackoffOptions = {}): Backoff {
	const delays = options.delaysMs ?? DEFAULT_DELAYS_MS;
	const schedule = options.schedule ?? ((fn, ms) => setTimeout(fn, ms) as unknown as number);
	const cancel = options.cancel ?? ((handle) => clearTimeout(handle));

	let attempt = 0;
	let pending: number | null = null;

	function delayFor(index: number): number {
		return delays[Math.min(index, delays.length - 1)];
	}

	return {
		fail() {
			// One retry in flight at a time. Several loaders failing in the
			// same round is the ordinary case, not five reasons to retry five
			// times over.
			if (pending !== null) return;
			const wait = delayFor(attempt);
			attempt += 1;
			pending = schedule(() => {
				pending = null;
				retry();
			}, wait);
		},
		succeed() {
			if (pending !== null) {
				cancel(pending);
				pending = null;
			}
			attempt = 0;
		},
		stop() {
			if (pending !== null) cancel(pending);
			pending = null;
		},
		get nextDelayMs() {
			return delayFor(attempt);
		}
	};
}
