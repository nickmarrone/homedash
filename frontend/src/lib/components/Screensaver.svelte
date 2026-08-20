<script lang="ts">
	import { onMount } from 'svelte';
	import type { PhotoPlaylist } from '$lib/api';
	import { createSlideshow, type Slide } from '$lib/slideshow';

	let {
		playlist,
		onDismiss
	}: {
		playlist: PhotoPlaylist;
		onDismiss: () => void;
	} = $props();

	// Slow enough to read as a dissolve rather than a cut, short enough not to
	// spend a noticeable share of a 30-second dwell in a half-faded state.
	const FADE_MS = 1200;

	// A slide whose photos will not decode is skipped, but only so many times:
	// if the whole cache is missing, every slide fails and an unbounded loop
	// would spin the CPU on a Pi rather than simply showing nothing.
	const MAX_SKIPS = 5;

	// Two layers that swap which one is opaque. Hand-rolled rather than a
	// Svelte transition because the old photo must stay painted underneath for
	// the whole fade - a transition that removes the node mid-fade shows the
	// black background through it, which reads as a flicker on a wall panel.
	let front: Slide | null = $state(null);
	let back: Slide | null = $state(null);
	let frontVisible = $state(false);

	let stopped = false;
	let timer: ReturnType<typeof setTimeout> | undefined;

	// Derived, not captured once: a photo dropped into the folder publishes
	// photos.updated, which refetches the playlist while the screensaver is
	// very likely already up. Reading the prop once would mean the new photo
	// never appeared until somebody touched the panel.
	let show = $derived.by(() => createSlideshow(playlist.photos));
	let dwellMs = $derived(Math.max(1, playlist.dwell_seconds) * 1000);

	/** Decode before showing. On a Pi this is the difference between a smooth
	 * dissolve and a flash of half-painted JPEG: the image would otherwise
	 * start decoding only once it is already on screen. */
	async function preload(slide: Slide): Promise<boolean> {
		try {
			await Promise.all(
				slide.photos.map(async (photo) => {
					const image = new Image();
					image.src = photo.url;
					await image.decode();
				})
			);
			return true;
		} catch {
			// A derivative that has not been rendered yet 404s. Skipping is the
			// right answer; the next scan will have it.
			return false;
		}
	}

	async function advance() {
		for (let attempt = 0; attempt < MAX_SKIPS; attempt++) {
			const slide = show.next();
			if (!slide) return;
			if (!(await preload(slide))) continue;
			if (stopped) return;

			// Paint into whichever layer is currently hidden, then flip. The
			// layer that was showing stays mounted and fades out underneath.
			if (frontVisible) {
				back = slide;
			} else {
				front = slide;
			}
			frontVisible = !frontVisible;
			return;
		}
	}

	function schedule() {
		timer = setTimeout(async () => {
			await advance();
			if (!stopped) schedule();
		}, dwellMs);
	}

	onMount(() => {
		// The first slide fades up from black rather than appearing, so the
		// screensaver arrives the same way it advances.
		advance();
		schedule();

		return () => {
			stopped = true;
			clearTimeout(timer);
		};
	});

	function classesFor(slide: Slide): string {
		// A lone half-panel photo happens when the library has an odd number of
		// them. It is centred at half size rather than stretched, which would
		// crop an already-cropped derivative a second time.
		return slide.photos.length === 1 && slide.photos[0].slot === 'half'
			? 'slide lonely'
			: 'slide';
	}
</script>

<!-- Fixed and on top, so the tap that dismisses it is swallowed here and
     cannot also press whatever calendar control sits underneath - the standard
     double-action bug in tap-to-dismiss overlays. -->
<div
	class="screensaver"
	role="button"
	tabindex="-1"
	aria-label="Dismiss the photo slideshow"
	style="--fade-ms: {FADE_MS}ms"
	onpointerdown={onDismiss}
>
	<div class="layer" class:visible={frontVisible}>
		{#if front}
			<div class={classesFor(front)}>
				{#each front.photos as photo (photo.id)}
					<img src={photo.url} alt="" width={photo.width} height={photo.height} />
				{/each}
			</div>
		{/if}
	</div>

	<div class="layer" class:visible={!frontVisible}>
		{#if back}
			<div class={classesFor(back)}>
				{#each back.photos as photo (photo.id)}
					<img src={photo.url} alt="" width={photo.width} height={photo.height} />
				{/each}
			</div>
		{/if}
	</div>
</div>

<style>
	.screensaver {
		position: fixed;
		inset: 0;
		z-index: 100;
		background: #000;
		/* Reset the button-ish defaults role="button" invites, and keep the
		   focus ring off a display nobody tabs through. */
		border: 0;
		padding: 0;
		outline: none;
		cursor: none;
	}

	.layer {
		position: absolute;
		inset: 0;
		opacity: 0;
		transition: opacity var(--fade-ms) ease-in-out;
	}

	.layer.visible {
		opacity: 1;
	}

	/* Row on a landscape panel: two disagreeing photos side by side. The
	   derivatives are already rendered at exactly half the panel, so this only
	   has to place them. */
	.slide {
		display: flex;
		justify-content: center;
		width: 100%;
		height: 100%;
	}

	img {
		flex: 1 1 0;
		min-width: 0;
		min-height: 0;
		/* Belt and braces: the derivative is already the right aspect, so this
		   only absorbs a rounding pixel. */
		object-fit: cover;
	}

	.lonely img {
		flex: 0 0 50%;
	}

	/* Portrait stacks the pair instead, because it is landscape photos that
	   disagree with a portrait panel - the mismatch is not always the same way
	   round, which is the trap in this whole phase. */
	@media (orientation: portrait) {
		.slide {
			flex-direction: column;
		}
	}
</style>
