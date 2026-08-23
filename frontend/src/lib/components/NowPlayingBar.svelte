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

	// For a HomeDash queue this is a proxied Jellyfin cover; for anything the
	// speaker is playing on its own it is whatever host that stream came from,
	// which may not answer. When it does not load, the browser paints its
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
			<span class="caps label">Now playing</span>
			<span class="line">
				<span class="title">{title}</span>
				<span class="subtitle">{subtitle}</span>
			</span>
		</span>
	</button>

	<TransportControls {player} compact {onAction} />
</div>

<style>
	/* A strip under a rule rather than a grey capsule. It is the last thing on
	   a page of ruled sections, so it is separated the same way every other
	   section is - by a line, not by a change of surface. */
	.bar {
		display: flex;
		align-items: center;
		gap: 1rem;
		padding-top: 0.9rem;
		border-top: 1px solid var(--rule);
	}

	.open {
		display: flex;
		align-items: center;
		gap: 1rem;
		flex: 1;
		/* The whole strip is the target, so it comfortably clears 48px without
		   a min-height fighting the bar's own padding. */
		min-height: var(--tap);
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
		width: 52px;
		height: 52px;
		border: 1px solid var(--rule);
		border-radius: var(--radius-sm);
		object-fit: cover;
		background: var(--wash);
		flex: none;
	}

	.text {
		display: flex;
		flex-direction: column;
		min-width: 0;
	}

	.label {
		font-size: 0.6875rem;
	}

	.line {
		display: flex;
		align-items: baseline;
		gap: 0.5rem;
		min-width: 0;
	}

	.title,
	.subtitle {
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	.title {
		flex: none;
		max-width: 60%;
		font-family: var(--font-display);
		font-size: 1.375rem;
		font-weight: 500;
	}

	/* Italic and set back, so the title and what it is from read as one line
	   the way a caption does, rather than as two competing labels. */
	.subtitle {
		font-family: var(--font-display);
		font-style: italic;
		font-size: 1.0625rem;
		color: var(--ink-muted);
	}

	.open:active {
		transform: scale(0.99);
	}

	.open:focus-visible {
		outline: 2px solid var(--ink);
		outline-offset: 2px;
	}
</style>
