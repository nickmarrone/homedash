// Which calendars the user has hidden, persisted per *device* rather than per
// user - the wall panel has no login, and the choice has to survive the reboots
// and browser restarts a kitchen display goes through. Phase 2 moves this onto
// the `devices` row; localStorage is the same scope in the meantime.

const STORAGE_KEY = 'homedash:hidden-calendars';

export function loadHidden(): Set<number> {
	try {
		const raw = localStorage.getItem(STORAGE_KEY);
		if (!raw) return new Set();
		const parsed: unknown = JSON.parse(raw);
		if (!Array.isArray(parsed)) return new Set();
		return new Set(parsed.filter((id): id is number => typeof id === 'number'));
	} catch {
		// Unreadable or disabled storage must never blank the panel.
		return new Set();
	}
}

export function saveHidden(hidden: Set<number>): void {
	try {
		localStorage.setItem(STORAGE_KEY, JSON.stringify([...hidden]));
	} catch {
		// Quota or private-mode failures are not worth breaking rendering over.
	}
}

/** Drop ids for calendars that no longer exist. SQLite reuses rowids, so a
 * stale id could otherwise make a brand-new calendar show up already hidden. */
export function pruneHidden(hidden: Set<number>, knownIds: number[]): Set<number> {
	const known = new Set(knownIds);
	return new Set([...hidden].filter((id) => known.has(id)));
}
