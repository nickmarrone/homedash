<script lang="ts">
	import type { MusicPlayer, TransportAction } from '$lib/api';

	let {
		player,
		compact = false,
		onAction
	}: {
		player: MusicPlayer;
		/** The now-playing bar shows only play/pause and skip; the full screen
		 * shows everything. Same component either way, so the two can never
		 * disagree about what a button does. */
		compact?: boolean;
		onAction: (action: TransportAction) => void;
	} = $props();

	const playing = $derived(player.state === 'play');
</script>

<!-- Every icon is inline SVG. No emoji anywhere in this app: Raspberry Pi OS
     Lite ships no emoji font, so a play glyph would render as a tofu box on
     the actual wall panel. MoonGlyph.svelte is the precedent. -->
<div class="transport" class:compact>
	<button type="button" aria-label="Previous track" onclick={() => onAction('previous')}>
		<svg viewBox="0 0 24 24" aria-hidden="true">
			<path d="M18 5 8 12l10 7z" fill="currentColor" />
			<rect x="5" y="5" width="2.2" height="14" fill="currentColor" />
		</svg>
	</button>

	<button
		type="button"
		class="primary"
		aria-label={playing ? 'Pause' : 'Play'}
		onclick={() => onAction(playing ? 'pause' : 'play')}
	>
		{#if playing}
			<svg viewBox="0 0 24 24" aria-hidden="true">
				<rect x="6.5" y="5" width="3.8" height="14" fill="currentColor" />
				<rect x="13.7" y="5" width="3.8" height="14" fill="currentColor" />
			</svg>
		{:else}
			<svg viewBox="0 0 24 24" aria-hidden="true">
				<path d="M7 4.8 19 12 7 19.2z" fill="currentColor" />
			</svg>
		{/if}
	</button>

	<button type="button" aria-label="Next track" onclick={() => onAction('next')}>
		<svg viewBox="0 0 24 24" aria-hidden="true">
			<path d="M6 5l10 7-10 7z" fill="currentColor" />
			<rect x="16.8" y="5" width="2.2" height="14" fill="currentColor" />
		</svg>
	</button>

	{#if !compact}
		<button type="button" aria-label="Stop" onclick={() => onAction('stop')}>
			<svg viewBox="0 0 24 24" aria-hidden="true">
				<rect x="6" y="6" width="12" height="12" rx="1.5" fill="currentColor" />
			</svg>
		</button>
	{/if}
</div>

<style>
	.transport {
		display: flex;
		align-items: center;
		gap: 0.5rem;
	}

	button {
		display: grid;
		place-items: center;
		/* 48px, as everywhere: the smallest target that stays reliable for a
		   fingertip on a wall panel, where you are reaching rather than aiming. */
		min-width: 48px;
		min-height: 48px;
		padding: 0;
		border: none;
		border-radius: 999px;
		background: transparent;
		color: inherit;
		cursor: pointer;
		touch-action: manipulation;
		-webkit-tap-highlight-color: transparent;
	}

	.primary {
		background: rgba(128, 128, 128, 0.18);
	}

	svg {
		width: 26px;
		height: 26px;
	}

	.compact svg {
		width: 22px;
		height: 22px;
	}

	button:active {
		transform: scale(0.97);
	}

	button:focus-visible {
		outline: 2px solid currentColor;
		outline-offset: 2px;
	}
</style>
