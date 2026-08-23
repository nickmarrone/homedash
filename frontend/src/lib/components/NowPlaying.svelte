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
		gap: 1.25rem;
		text-align: center;
	}

	.art {
		width: min(48vh, 420px);
		aspect-ratio: 1;
	}

	img,
	.placeholder {
		display: block;
		width: 100%;
		height: 100%;
		border-radius: 14px;
		object-fit: cover;
		background: rgba(128, 128, 128, 0.2);
	}

	.meta {
		width: min(90vw, 520px);
	}

	p {
		margin: 0 0 0.2rem;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	.title {
		font-size: 1.7rem;
		font-weight: 600;
	}

	.artist {
		font-size: 1.15rem;
		opacity: 0.85;
	}

	.album {
		font-size: 1rem;
		opacity: 0.6;
	}

	.progress {
		margin-top: 1rem;
	}

	.track {
		height: 6px;
		border-radius: 999px;
		background: rgba(128, 128, 128, 0.25);
		overflow: hidden;
	}

	.fill {
		height: 100%;
		background: currentColor;
		opacity: 0.7;
	}

	.times {
		display: flex;
		justify-content: space-between;
		margin-top: 0.35rem;
		font-size: 0.85rem;
		opacity: 0.6;
		font-variant-numeric: tabular-nums;
	}
</style>
