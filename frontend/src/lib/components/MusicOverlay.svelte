<script lang="ts">
	import type { MusicPlayer, TransportAction } from '$lib/api';
	import NowPlaying from './NowPlaying.svelte';
	import PlayerPicker from './PlayerPicker.svelte';
	import TransportControls from './TransportControls.svelte';

	let {
		players,
		player,
		onSelectPlayer,
		onAction,
		onVolume,
		onClose
	}: {
		players: MusicPlayer[];
		player: MusicPlayer;
		onSelectPlayer: (id: number) => void;
		onAction: (action: TransportAction) => void;
		onVolume: (level: number) => void;
		onClose: () => void;
	} = $props();

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

	<div class="body">
		<NowPlaying {player} />

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
		justify-content: space-between;
		gap: 1rem;
		padding: 0.75rem 1rem;
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
