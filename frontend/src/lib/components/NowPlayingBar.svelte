<script lang="ts">
	import type { MusicPlayer, TransportAction } from '$lib/api';
	import TransportControls from './TransportControls.svelte';

	let {
		player,
		onAction,
		onOpen
	}: {
		player: MusicPlayer;
		onAction: (action: TransportAction) => void;
		onOpen: () => void;
	} = $props();

	const media = $derived(player.now_playing);

	// Art comes off the speaker's own metadata, which points at whatever host
	// the stream came from. When that does not load, the browser paints its
	// broken-image glyph - the same failure mode as an emoji on a Pi, and just
	// as visible across a kitchen. Remember the URL that failed and fall back
	// to the plain placeholder instead; a new track clears it by having a
	// different URL.
	let failedArt = $state<string | null>(null);
	const art = $derived(
		media?.image_url && media.image_url !== failedArt ? media.image_url : null
	);

	const title = $derived(media?.title ?? 'Nothing playing');
	const subtitle = $derived(
		[media?.artist, media?.album].filter(Boolean).join(' - ') || player.name
	);
</script>

<!-- Music is a strip under the calendar rather than a seventh view, because the
     calendar is what the panel is for. It renders only while something is
     playing, so a household that never uses this never sees it. -->
<div class="bar">
	<button class="open" type="button" onclick={onOpen} aria-label="Open music">
		{#if art}
			<img src={art} alt="" onerror={() => (failedArt = art)} />
		{:else}
			<span class="placeholder" aria-hidden="true"></span>
		{/if}
		<span class="text">
			<span class="title">{title}</span>
			<span class="subtitle">{subtitle}</span>
		</span>
	</button>

	<TransportControls {player} compact {onAction} />
</div>

<style>
	.bar {
		display: flex;
		align-items: center;
		gap: 0.75rem;
		padding: 0.4rem 0.75rem;
		border-radius: 12px;
		background: rgba(128, 128, 128, 0.12);
	}

	.open {
		display: flex;
		align-items: center;
		gap: 0.75rem;
		flex: 1;
		/* The whole strip is the target, so it comfortably clears 48px without
		   a min-height fighting the bar's own padding. */
		min-height: 48px;
		padding: 0;
		border: none;
		background: transparent;
		color: inherit;
		font: inherit;
		text-align: left;
		cursor: pointer;
		touch-action: manipulation;
		-webkit-tap-highlight-color: transparent;
		/* Long track names must not push the transport buttons off the panel. */
		min-width: 0;
	}

	img,
	.placeholder {
		width: 44px;
		height: 44px;
		border-radius: 6px;
		object-fit: cover;
		background: rgba(128, 128, 128, 0.25);
		flex: none;
	}

	.text {
		display: flex;
		flex-direction: column;
		min-width: 0;
	}

	.title,
	.subtitle {
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	.title {
		font-weight: 600;
	}

	.subtitle {
		font-size: 0.85rem;
		opacity: 0.7;
	}

	.open:active {
		transform: scale(0.99);
	}

	.open:focus-visible {
		outline: 2px solid currentColor;
		outline-offset: 2px;
	}
</style>
