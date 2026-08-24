<script lang="ts">
	import { onDestroy } from 'svelte';
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

	/** At most one volume command per this long while a finger is moving.
	 *
	 * `oninput` fires on every value change, so a drag from 20 to 70 is around
	 * fifty of them. Sending each one meant fifty POSTs and fifty refetches in
	 * under a second, from a Pi over wifi, each forwarded to the speaker over
	 * its own TCP control connection - audible stepping at best, and a backed
	 * up HEOS connection at worst. The slider itself still tracks the finger
	 * at full rate; only the network is rationed. */
	const SEND_EVERY_MS = 120;

	/** How long the slider keeps showing the value the finger left it on.
	 *
	 * Long enough for the command and the refetch behind it to land. Clearing
	 * on release instead put the snap-back at the *end* of the gesture that
	 * `dragging` exists to prevent during it: `player.volume` is still the old
	 * value until the round trip completes, so the thumb jumped back to where
	 * the drag started and then forward again. */
	const SETTLE_MS = 1_000;

	/** The slider's own position while a finger is on it - and briefly after.
	 *
	 * Without this the value snaps back mid-drag: every volume change publishes
	 * a HEOS event, the panel refetches, and the incoming `player.volume` would
	 * overwrite where the finger actually is. */
	let dragging = $state<number | null>(null);
	const volume = $derived(dragging ?? player.volume);

	let sendTimer: ReturnType<typeof setTimeout> | null = null;
	let settleTimer: ReturnType<typeof setTimeout> | null = null;
	let unsent: number | null = null;

	function send(value: number) {
		unsent = null;
		onVolume(value);
	}

	function commit(value: number) {
		dragging = value;
		if (settleTimer !== null) {
			clearTimeout(settleTimer);
			settleTimer = null;
		}
		if (sendTimer !== null) {
			// Already sent one recently: remember this and let the timer send it.
			unsent = value;
			return;
		}
		send(value);
		sendTimer = setTimeout(() => {
			sendTimer = null;
			if (unsent !== null) commit(unsent);
		}, SEND_EVERY_MS);
	}

	function release(value: number) {
		// The last position always reaches the speaker, whatever the throttle
		// was in the middle of - letting a drag end on a value that was never
		// sent is the one outcome nobody would forgive.
		send(value);
		if (settleTimer !== null) clearTimeout(settleTimer);
		settleTimer = setTimeout(() => {
			settleTimer = null;
			dragging = null;
		}, SETTLE_MS);
	}

	onDestroy(() => {
		if (sendTimer !== null) clearTimeout(sendTimer);
		if (settleTimer !== null) clearTimeout(settleTimer);
	});
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
					class="tab"
					class:selected={tab === 'now'}
					aria-pressed={tab === 'now'}
					onclick={() => (chosenTab = 'now')}>Now Playing</button
				>
				<button
					type="button"
					class="tab"
					class:selected={tab === 'browse'}
					aria-pressed={tab === 'browse'}
					onclick={() => (chosenTab = 'browse')}>Library</button
				>
			</nav>
		{/if}
		{#if hasLibrary && players.length > 1}
			<span class="divider" aria-hidden="true"></span>
		{/if}
		<PlayerPicker {players} selectedId={player.id} onSelect={onSelectPlayer} />
		<button class="control-round close" type="button" onclick={onClose} aria-label="Close music">
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
			<p class="caps queue">Track {player.queue.position} of {player.queue.length}</p>
		{/if}

		<div class="controls">
			<TransportControls {player} {onAction} />

			<label class="volume">
				<span class="caps label">Volume</span>
				<input
					type="range"
					min="0"
					max="100"
					step="1"
					value={volume}
					style:--filled={`${volume}%`}
					oninput={(event) => commit(Number(event.currentTarget.value))}
					onchange={(event) => release(Number(event.currentTarget.value))}
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
		background: var(--paper);
		color: var(--ink);
	}

	header {
		display: flex;
		align-items: center;
		gap: 1.75rem;
		padding: 1.5rem 1.5rem 0;
		border-bottom: 1px solid var(--rule);
	}

	.divider {
		width: 1px;
		height: 1.6rem;
		background: var(--rule);
		flex: none;
	}

	.tabs {
		display: flex;
	}

	/* Underlined, like the view switcher: opening the music does not change how
	   a selected thing looks. */
	.browse {
		flex: 1;
		min-height: 0;
		padding: 1.25rem 1.5rem 1.5rem;
	}

	.queue {
		margin: 0;
		font-size: 0.75rem;
	}

	.close {
		/* Pushed right on its own rather than by justify-content, so it still
		   sits against the edge when the picker renders nothing at all - which
		   it does for a one-speaker household. */
		margin-left: auto;
	}

	.close svg {
		width: 22px;
		height: 22px;
	}

	.body {
		flex: 1;
		display: flex;
		flex-direction: column;
		align-items: center;
		justify-content: center;
		gap: 2rem;
		padding: 1.5rem;
		/* A long album title must not be able to push the transport buttons
		   off the bottom of a 1080-tall panel. */
		overflow-y: auto;
	}

	.controls {
		display: flex;
		flex-direction: column;
		align-items: center;
		gap: 1.5rem;
	}

	.volume {
		display: flex;
		align-items: center;
		gap: 1.1rem;
		/* Matched to NowPlaying's own text column, so the slider, the position
		   bar and the title all share one measure. */
		width: min(84vw, 520px);
	}

	.label {
		font-size: 0.6875rem;
	}

	.level {
		min-width: 3ch;
		text-align: right;
		font-size: 1.125rem;
	}

	/* Drawn rather than left native. accent-color alone paints the filled half
	   and the thumb, but leaves the rest of the track the browser's own cool
	   grey - the one cold thing on a warm page. Turning the appearance off
	   also turns off the filled half, so it is painted here from --filled,
	   which the markup sets from the same value the thumb sits at.
	   Chromium-only selectors are safe: the panel is Chromium in kiosk mode,
	   and a browser that ignores them still gets a working slider. */
	input[type='range'] {
		flex: 1;
		/* The thumb is the target here, and the default one is far too small
		   for a fingertip on a wall panel. */
		height: var(--tap);
		accent-color: var(--ink);
		touch-action: manipulation;
		-webkit-appearance: none;
		appearance: none;
		background: transparent;
	}

	input[type='range']::-webkit-slider-runnable-track {
		height: 3px;
		background: linear-gradient(
			to right,
			var(--ink) 0 var(--filled),
			var(--wash) var(--filled) 100%
		);
	}

	input[type='range']::-webkit-slider-thumb {
		-webkit-appearance: none;
		appearance: none;
		width: 28px;
		height: 28px;
		/* Half the thumb above the 3px track, so it sits centred on the line. */
		margin-top: -12.5px;
		border-radius: var(--radius-pill);
		background: var(--ink);
	}

	.close:active {
		transform: scale(0.97);
	}

	.close:focus-visible {
		outline: 2px solid var(--ink);
		outline-offset: 2px;
	}
</style>
