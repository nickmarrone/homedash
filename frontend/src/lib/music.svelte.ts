/** The panel's music state, and the commands that change it.
 *
 * Extracted from +page.svelte, which owned six unrelated state domains at
 * once. Music was around seventy lines of it that never touched the calendar,
 * so pulling it out is close to free and leaves the page reading as what it
 * is: a calendar, with a music bar in it.
 *
 * A factory returning a getter object, not a store and not a singleton - the
 * same shape `createOrientation` uses, which is this codebase's answer to
 * cross-component reactive state. `.svelte.ts` because it genuinely holds
 * runes; a rune in a plain `.ts` file type-checks cleanly and then fails at
 * runtime.
 */
import {
	fetchMusicPlayers,
	playAlbum,
	playTracks,
	sendTransport,
	setPlayerVolume,
	type MusicPlayer,
	type TransportAction
} from '$lib/api';
import { loadPlayerId, pickPlayer, savePlayerId } from '$lib/musicPreference';

export interface Music {
	/** Null means this panel has no music configured at all, which is different
	 * from having music whose speakers are asleep: the first hides the UI
	 * permanently, the second shows it with nothing playing. */
	readonly players: MusicPlayer[] | null;
	readonly hasLibrary: boolean;
	/** The speaker being controlled, or null. Falls back rather than showing
	 * nothing when the remembered one is gone - an unplugged or renamed player
	 * would otherwise leave the controls wired to an id the backend no longer
	 * knows. */
	readonly activePlayer: MusicPlayer | null;
	/** Whether the sticky bar under the calendar should show.
	 *
	 * Playing *or* paused. That is not a detail: keying it on 'play' alone made
	 * the bar vanish the instant you paused from it, taking the resume button
	 * with it. Only a stopped speaker has nothing to offer. */
	readonly barVisible: boolean;
	readonly overlayOpen: boolean;
	openOverlay: () => void;
	closeOverlay: () => void;
	selectPlayer: (id: number) => void;
	/** Refetch. Rejects if the request fails, so the page's retry can see it. */
	load: () => Promise<void>;
	transport: (action: TransportAction) => void;
	setVolume: (level: number) => void;
	playAlbum: (albumId: string) => void;
	playTracks: (trackIds: string[], albumId: string) => void;
}

export function createMusic(): Music {
	let players = $state<MusicPlayer[] | null>(null);
	let hasLibrary = $state(false);
	let selectedPlayerId = $state<number | null>(loadPlayerId());
	let overlayOpen = $state(false);

	const activePlayer = $derived(pickPlayer(players ?? [], selectedPlayerId));
	const barVisible = $derived(
		activePlayer !== null && (activePlayer.state === 'play' || activePlayer.state === 'pause')
	);

	async function load(): Promise<void> {
		const next = await fetchMusicPlayers();
		players = next === null ? null : next.players;
		hasLibrary = next?.library ?? false;
	}

	async function run(command: Promise<void>): Promise<void> {
		try {
			await command;
		} catch {
			// A speaker that has just dropped off wifi must not take the
			// calendar down with it. The next pushed event or reload corrects
			// whatever the panel is showing.
			return;
		}
		// HEOS pushes a change event for anything that actually happened, but
		// refetching immediately closes the window where a tapped button still
		// renders its old state.
		load().catch(() => {
			// Same reasoning: a failed refetch is a stale bar, not a dead panel.
		});
	}

	/** Every command needs a speaker, and there may not be one. */
	function onActivePlayer(command: (id: number) => Promise<void>): void {
		const player = activePlayer;
		if (player === null) return;
		run(command(player.id));
	}

	return {
		get players() {
			return players;
		},
		get hasLibrary() {
			return hasLibrary;
		},
		get activePlayer() {
			return activePlayer;
		},
		get barVisible() {
			return barVisible;
		},
		get overlayOpen() {
			return overlayOpen;
		},
		openOverlay: () => (overlayOpen = true),
		closeOverlay: () => (overlayOpen = false),
		selectPlayer(id: number) {
			selectedPlayerId = id;
			savePlayerId(id);
		},
		load,
		transport: (action) => onActivePlayer((id) => sendTransport(id, action)),
		setVolume: (level) => onActivePlayer((id) => setPlayerVolume(id, level)),
		playAlbum: (albumId) => onActivePlayer((id) => playAlbum(id, albumId)),
		playTracks: (trackIds, albumId) =>
			onActivePlayer((id) => playTracks(id, trackIds, albumId))
	};
}
