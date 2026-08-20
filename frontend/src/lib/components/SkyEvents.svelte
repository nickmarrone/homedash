<script lang="ts">
	import type { MoonPhase, SkyEvent } from '$lib/api';
	import { formatSkyDate } from '$lib/format';
	import MoonGlyph from './MoonGlyph.svelte';

	let {
		events = [],
		today = null,
		moon = null
	}: { events?: SkyEvent[]; today?: string | null; moon?: MoonPhase | null } = $props();

	// A wall panel has one line to spare here. Three keeps the strip to a
	// glance; the fourth event is always weeks out and will still be here
	// tomorrow.
	const MAX_EVENTS = 3;

	// A comet first, whatever the dates say. Everything else on this strip is
	// a date in the diary; a visible comet is a thing in the sky tonight that
	// may not be there next month, and it is the one item worth interrupting
	// the chronological order for.
	let ordered = $derived([
		...events.filter((event) => event.kind === 'comet'),
		...events.filter((event) => event.kind !== 'comet')
	]);

	let shown = $derived(ordered.slice(0, MAX_EVENTS));
</script>

<!-- Above the hourly strip rather than inside it: these are dated events, not
     a per-hour series, and threading them into a 12-column grid would either
     misplace them or force the strip's column geometry to bend around them. -->
{#if shown.length}
	<div class="sky">
		{#each shown as event (event.kind + event.date + event.name)}
			<span
				class="event"
				class:soon={today !== null && event.date <= today}
				class:comet={event.kind === 'comet'}
			>
				{#if event.kind === 'moon' && moon}
					<!-- The disc as it will actually look that night, not as it
					     looks tonight: a "Full Moon" line under a drawing of a
					     crescent is worse than no drawing. -->
					<MoonGlyph
						moon={{ ...moon, illumination: event.name === 'Full Moon' ? 1 : 0 }}
						size={14}
					/>
				{/if}
				<span class="name">{event.name}</span>
				<span class="when">{formatSkyDate(event.date, today)}</span>
				{#if event.detail}
					<span class="detail">{event.detail}</span>
				{/if}
			</span>
		{/each}
	</div>
{/if}

<style>
	.sky {
		display: flex;
		flex-wrap: wrap;
		gap: 0.4rem 1.25rem;
		margin-top: 1.25rem;
		font-size: 0.85rem;
		opacity: 0.75;
	}

	.event {
		display: flex;
		align-items: baseline;
		gap: 0.35rem;
		white-space: nowrap;
	}

	/* Something happening tonight earns full weight - the whole point of the
	   strip is that somebody looks up before it is over. */
	.soon,
	.comet {
		opacity: 1;
		font-weight: 600;
	}

	/* A comet is rare enough that its detail is the headline, not an aside. */
	.comet .detail {
		opacity: 0.85;
	}

	.when {
		opacity: 0.75;
	}

	.detail {
		opacity: 0.6;
	}

	/* Portrait is 1080px wide, so three events with their details do not fit on
	   one line. The detail is the first thing to go: the name and the date are
	   what get acted on. */
	@media (orientation: portrait) {
		.detail {
			display: none;
		}
	}
</style>
