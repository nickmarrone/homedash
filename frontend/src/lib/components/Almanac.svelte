<script lang="ts">
	import type { Weather } from '$lib/api';
	import { formatTime } from '$lib/format';
	import MoonGlyph from './MoonGlyph.svelte';
	import SkyEvents from './SkyEvents.svelte';

	let { weather, today = null }: { weather: Weather | null; today?: string | null } = $props();

	let moon = $derived(weather?.astro?.moon ?? null);

	let sun = $derived.by(() => {
		const daily = weather?.daily;
		const sunrise = daily?.sunrise?.[0];
		const sunset = daily?.sunset?.[0];
		if (!sunrise || !sunset) return null;
		return { sunrise, sunset };
	});

	let range = $derived.by(() => {
		const daily = weather?.daily;
		const high = daily?.temperature_2m_max?.[0];
		const low = daily?.temperature_2m_min?.[0];
		if (high === undefined || low === undefined) return null;
		return { high: Math.round(high), low: Math.round(low) };
	});
</script>

<!-- One line under the masthead rule, carrying everything about the day that is
     not the temperature: how far it swings, when the light starts and stops,
     what the moon is doing, and what is worth going outside for this week.

     It used to be two right-aligned rows - WeatherWidget's own details stacked
     under the temperature, and the sky strip below that - which put five
     unrelated facts in a column against the right edge and left the whole width
     beside them empty. -->
<div class="almanac">
	{#if range}
		<span>High {range.high}&deg; &middot; Low {range.low}&deg;</span>
	{/if}

	{#if sun}
		<span class="pair">
			<!-- Drawn, not the U+2600 character this line used to print. Every other
			     glyph in the app is inline SVG for one reason: Raspberry Pi OS Lite
			     ships no emoji font, and a missing glyph on a wall panel is a tofu
			     box the size of the text around it. See TransportControls.svelte. -->
			<svg viewBox="0 0 24 24" aria-hidden="true">
				<circle cx="12" cy="12" r="4.4" fill="none" stroke="currentColor" stroke-width="1.7" />
				<path
					d="M12 2.6v3M12 18.4v3M2.6 12h3M18.4 12h3M5.4 5.4l2.1 2.1M16.5 16.5l2.1 2.1M18.6 5.4l-2.1 2.1M7.5 16.5l-2.1 2.1"
					stroke="currentColor"
					stroke-width="1.7"
					stroke-linecap="round"
				/>
			</svg>
			{formatTime(sun.sunrise)} &ndash; {formatTime(sun.sunset)}
		</span>
	{/if}

	{#if weather?.air_quality?.us_aqi !== undefined}
		<span>AQI {Math.round(weather.air_quality.us_aqi)}</span>
	{/if}

	<!-- The moon and the sky events sit outside the weather branches on purpose:
	     both are computed from the configured coordinates, so they are still
	     known when Open-Meteo is not answering. A panel that loses the sky as
	     well as the forecast makes an outage look worse than it is. -->
	{#if moon}
		<span class="pair">
			<MoonGlyph {moon} size={15} />
			{moon.phase}
		</span>
	{/if}

	<SkyEvents events={weather?.astro?.events ?? []} {today} {moon} />
</div>

<style>
	.almanac {
		display: flex;
		flex-wrap: wrap;
		align-items: baseline;
		gap: 0.35rem 1.5rem;
		margin-top: 0.7rem;
		font-size: 0.9375rem;
		color: var(--ink-muted);
	}

	.pair {
		display: inline-flex;
		align-items: center;
		gap: 0.45rem;
		white-space: nowrap;
	}

	.pair svg {
		width: 15px;
		height: 15px;
		flex: none;
	}
</style>
