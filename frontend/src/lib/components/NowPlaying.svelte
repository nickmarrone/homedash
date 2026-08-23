<script lang="ts">
	import type { MusicPlayer } from '$lib/api';

	let { player }: { player: MusicPlayer } = $props();

	const media = $derived(player.now_playing);

	// See NowPlayingBar: a broken-image glyph is worse than no art at all on a
	// display nobody is sitting in front of.
	let failedArt = $state<string | null>(null);
	const art = $derived(
		media?.image_url && media.image_url !== failedArt ? media.image_url : null
	);

	/** Position as a fraction, or null when there is nothing to measure.
	 *
	 * Deliberately not animated between updates. The position arrives on HEOS
	 * change events, and the once-a-second progress tick is filtered out on the
	 * server so a wall panel is not woken 3600 times an hour to move a bar a
	 * pixel. A stepped bar is the honest rendering of a stepped input. */
	const progress = $derived.by(() => {
		const duration = media?.duration_ms ?? 0;
		const position = media?.position_ms ?? 0;
		if (duration <= 0) return null;
		return Math.min(1, Math.max(0, position / duration));
	});

	function clock(ms: number | null | undefined): string {
		if (ms == null || ms < 0) return '';
		const total = Math.floor(ms / 1000);
		const minutes = Math.floor(total / 60);
		const seconds = total % 60;
		return `${minutes}:${String(seconds).padStart(2, '0')}`;
	}
</script>

<div class="now-playing">
	<!-- Matted like a print: the cover sits on a white border inside a hairline
	     frame, which is also what stops a dark sleeve bleeding into the paper. -->
	<div class="art">
		{#if art}
			<img src={art} alt="" onerror={() => (failedArt = art)} />
		{:else}
			<span class="placeholder" aria-hidden="true"></span>
		{/if}
	</div>

	<div class="meta">
		<p class="title">{media?.title ?? 'Nothing playing'}</p>
		{#if media?.artist}<p class="artist">{media.artist}</p>{/if}
		{#if media?.album}<p class="album">{media.album}</p>{/if}

		{#if progress !== null}
			<div class="progress">
				<div class="track"><div class="fill" style:width={`${progress * 100}%`}></div></div>
				<div class="times">
					<span>{clock(media?.position_ms)}</span>
					<span>{clock(media?.duration_ms)}</span>
				</div>
			</div>
		{/if}
	</div>
</div>

<style>
	.now-playing {
		display: flex;
		flex-direction: column;
		align-items: center;
		gap: 1.75rem;
		text-align: center;
	}

	/* Raised from the component's old min(48vh, 420px). On a 1920-tall portrait
	   panel that cap left the cover small from across a kitchen, which is the
	   distance this screen is actually read from. */
	.art {
		width: min(46vh, 560px);
		aspect-ratio: 1;
		padding: 7px;
		border: 1px solid var(--rule);
		border-radius: var(--radius-sm);
		background: var(--paper-raised);
		box-sizing: border-box;
	}

	img,
	.placeholder {
		display: block;
		width: 100%;
		height: 100%;
		object-fit: cover;
		background: var(--wash);
	}

	.meta {
		width: min(84vw, 520px);
	}

	p {
		margin: 0;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	.title {
		font-family: var(--font-display);
		font-size: 2.5rem;
		font-weight: 500;
		line-height: 1.06;
		letter-spacing: -0.015em;
	}

	.artist {
		margin-top: 0.4rem;
		font-size: 1.25rem;
		color: var(--ink-soft);
	}

	.album {
		margin-top: 0.15rem;
		font-family: var(--font-display);
		font-style: italic;
		font-size: 1.0625rem;
		color: var(--ink-muted);
	}

	.progress {
		margin-top: 1.6rem;
	}

	/* A hairline rather than a thick rounded track, and that is not only taste:
	   the once-a-second HEOS progress tick is filtered out on the server so the
	   panel is not woken 3600 times an hour, which means this steps rather than
	   sweeps. A hairline reads as a measurement; a fat capsule promises smooth
	   motion the input cannot deliver. */
	.track {
		height: 4px;
		background: var(--wash);
		overflow: hidden;
	}

	.fill {
		height: 100%;
		background: var(--ink);
	}

	.times {
		display: flex;
		justify-content: space-between;
		margin-top: 0.5rem;
		font-size: 0.9375rem;
		color: var(--ink-muted);
	}
</style>
