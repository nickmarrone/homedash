<script lang="ts">
	import type { MusicPlayer, TransportAction } from '$lib/api';
	import MusicBrowser from './MusicBrowser.svelte';
	import NowPlaying from './NowPlaying.svelte';
	import PlayerPicker from './PlayerPicker.svelte';
	import TransportControls from './TransportControls.svelte';

	let {
		players,
		player,
		hasLibrary,
		onSelectPlayer,
		onAction,
		onVolume,
		onPlayAlbum,
		onPlayTracks,
		onClose
	}: {
		players: MusicPlayer[];
		player: MusicPlayer;
		/** False when no Jellyfin is configured. Speakers without a library is
		 * a coherent setup, so the browse tab goes rather than the whole UI. */
		hasLibrary: boolean;
		onSelectPlayer: (id: number) => void;
		onAction: (action: TransportAction) => void;
		onVolume: (level: number) => void;
		onPlayAlbum: (albumId: string) => void;
		onPlayTracks: (trackIds: string[], albumId: string) => void;
		onClose: () => void;
	} = $props();

	// Null until somebody picks one, so the default stays *derived* rather than
	// captured at construction. That is not tidiness: the overlay can be opened
	// before the first player snapshot arrives, and an initial value read then
	// would settle on the wrong tab and stay there.
	let chosenTab = $state<'now' | 'browse' | null>(null);

	// Opens on Now Playing when something is on, and on the library when
	// nothing is - which is what somebody walking up to a silent panel wants.
	const tab = $derived(
		chosenTab ?? (hasLibrary && player.state === 'stop' ? 'browse' : 'now')
	);

	function play(fn: () => void) {
		fn();
		// Jump to Now Playing so the tap visibly did something. The queue takes
		// a moment to reach the speaker, and staying in the list makes it look
		// like nothing happened.
		chosenTab = 'now';
	}

	/** The slider's own position while a finger is on it.
	 *
	 * Without this the value snaps back mid-drag: every volume change publishes
	 * a HEOS event, the panel refetches, and the incoming `player.volume` would
	 * overwrite where the finger actually is. Cleared on release, so the
	 * speaker becomes the source of truth again the moment the drag ends. */
	let dragging = $state<number | null>(null);
	const volume = $derived(dragging ?? player.volume);

	function commit(value: number) {
		dragging = value;
		onVolume(value);
	}
</script>

<!-- A state inside the SPA, never a route: in locked mode the Chromium
     enterprise policy allows exactly one URL, so a navigation would put a
     blank page on the wall with no way back. -->
<section class="overlay">
	<header>
		{#if hasLibrary}
			<nav class="tabs" aria-label="Music">
				<button
					type="button"
					class:selected={tab === 'now'}
					aria-pressed={tab === 'now'}
					onclick={() => (chosenTab = 'now')}>Now Playing</button
				>
				<button
					type="button"
					class:selected={tab === 'browse'}
					aria-pressed={tab === 'browse'}
					onclick={() => (chosenTab = 'browse')}>Library</button
				>
			</nav>
		{/if}
		<PlayerPicker {players} selectedId={player.id} onSelect={onSelectPlayer} />
		<button class="close" type="button" onclick={onClose} aria-label="Close music">
			<svg viewBox="0 0 24 24" aria-hidden="true">
				<path
					d="M6 6l12 12M18 6L6 18"
					stroke="currentColor"
					stroke-width="2.2"
					stroke-linecap="round"
				/>
			</svg>
		</button>
	</header>

	{#if tab === 'browse'}
		<div class="browse">
			<MusicBrowser
				onPlayAlbum={(albumId) => play(() => onPlayAlbum(albumId))}
				onPlayTracks={(ids, albumId) => play(() => onPlayTracks(ids, albumId))}
			/>
		</div>
	{:else}
	<div class="body">
		<NowPlaying {player} />

		{#if player.queue}
			<p class="queue">Track {player.queue.position} of {player.queue.length}</p>
		{/if}

		<div class="controls">
			<TransportControls {player} {onAction} />

			<label class="volume">
				<span class="label">Volume</span>
				<input
					type="range"
					min="0"
					max="100"
					step="1"
					value={volume}
					oninput={(event) => commit(Number(event.currentTarget.value))}
					onchange={() => (dragging = null)}
					onpointerup={() => (dragging = null)}
				/>
				<span class="level">{volume}</span>
			</label>
		</div>
	</div>
	{/if}
</section>

<style>
	.overlay {
		position: fixed;
		inset: 0;
		z-index: 40;
		display: flex;
		flex-direction: column;
		background: Canvas;
		color: inherit;
	}

	header {
		display: flex;
		align-items: center;
		gap: 1rem;
		padding: 0.75rem 1rem;
	}

	.tabs {
		display: flex;
		gap: 0.25rem;
		padding: 0.25rem;
		border-radius: 999px;
		background: rgba(128, 128, 128, 0.14);
	}

	.tabs button {
		min-height: 48px;
		padding: 0 1rem;
		border: none;
		border-radius: 999px;
		background: transparent;
		color: inherit;
		font: inherit;
		font-size: 1rem;
		cursor: pointer;
		touch-action: manipulation;
		-webkit-tap-highlight-color: transparent;
	}

	.tabs .selected {
		background: Canvas;
		font-weight: 600;
		box-shadow: 0 1px 3px rgba(0, 0, 0, 0.2);
	}

	.tabs button:active {
		transform: scale(0.97);
	}

	.browse {
		flex: 1;
		min-height: 0;
		padding: 0 1rem 1rem;
	}

	.queue {
		margin: 0;
		font-size: 0.9rem;
		opacity: 0.6;
	}

	.close {
		display: grid;
		place-items: center;
		min-width: 48px;
		min-height: 48px;
		/* Pushed right on its own rather than by justify-content, so it still
		   sits against the edge when the picker renders nothing at all - which
		   it does for a one-speaker household. */
		margin-left: auto;
		border: none;
		border-radius: 999px;
		background: rgba(128, 128, 128, 0.14);
		color: inherit;
		cursor: pointer;
		touch-action: manipulation;
		-webkit-tap-highlight-color: transparent;
	}

	.close svg {
		width: 24px;
		height: 24px;
	}

	.body {
		flex: 1;
		display: flex;
		flex-direction: column;
		align-items: center;
		justify-content: center;
		gap: 2rem;
		padding: 1rem;
		/* A long album title must not be able to push the transport buttons
		   off the bottom of a 1080-tall panel. */
		overflow-y: auto;
	}

	.controls {
		display: flex;
		flex-direction: column;
		align-items: center;
		gap: 1.25rem;
	}

	.volume {
		display: flex;
		align-items: center;
		gap: 0.75rem;
		width: min(90vw, 420px);
	}

	.label {
		font-size: 0.85rem;
		opacity: 0.6;
	}

	.level {
		min-width: 2.5ch;
		text-align: right;
		font-variant-numeric: tabular-nums;
	}

	input[type='range'] {
		flex: 1;
		/* The thumb is the target here, and the default one is far too small
		   for a fingertip on a wall panel. */
		height: 48px;
		accent-color: currentColor;
		touch-action: manipulation;
	}

	.close:active {
		transform: scale(0.97);
	}

	.close:focus-visible {
		outline: 2px solid currentColor;
		outline-offset: 2px;
	}
</style>
