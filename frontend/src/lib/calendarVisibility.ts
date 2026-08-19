// Which calendars have been hidden on this panel. There is no login and only
// one shared screen, so this is a property of the panel rather than of a
// person, and it has to survive the reboots and browser restarts a kitchen
// display goes through.

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

/** Whether an item should be shown, given the hidden set.
 *
 * Items with no calendar - an orphaned instance whose source has gone - always
 * show: there is no legend chip that could ever bring them back. Shared by the
 * agenda and every grid view so one filter cannot disagree with another. */
export function isVisible(item: { calendar: { id: number } | null }, hidden: Set<number>): boolean {
	return !item.calendar || !hidden.has(item.calendar.id);
}
