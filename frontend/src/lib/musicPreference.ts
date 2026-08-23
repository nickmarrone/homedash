// Which speaker this panel controls. Panel-local, like every other preference
// here: the household may have speakers in several rooms, but the panel on the
// kitchen wall is almost always pointed at the kitchen one, and it has to come
// back that way after a reboot rather than defaulting to whichever speaker the
// backend happened to list first.

const STORAGE_KEY = 'homedash:player';

export function loadPlayerId(): number | null {
	try {
		const raw = localStorage.getItem(STORAGE_KEY);
		if (raw === null) return null;
		const id = Number(raw);
		return Number.isInteger(id) ? id : null;
	} catch {
		// Unreadable or disabled storage must never blank the panel.
		return null;
	}
}

export function savePlayerId(id: number): void {
	try {
		localStorage.setItem(STORAGE_KEY, String(id));
	} catch {
		// Quota or private-mode failures are not worth breaking rendering over.
	}
}

/** The speaker to control, given what the backend currently reports.
 *
 * Falls back rather than showing nothing: a remembered speaker that has been
 * unplugged, renamed onto a new id, or moved to another room would otherwise
 * leave the panel with a music bar wired to a player that no longer exists.
 */
export function pickPlayer<T extends { id: number; available: boolean }>(
	players: T[],
	rememberedId: number | null
): T | null {
	if (players.length === 0) return null;
	const remembered = players.find((p) => p.id === rememberedId);
	if (remembered) return remembered;
	return players.find((p) => p.available) ?? players[0];
}
