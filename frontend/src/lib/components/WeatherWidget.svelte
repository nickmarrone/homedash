<script lang="ts">
	import type { Weather } from '$lib/api';
	import { weatherDescription } from '$lib/weatherCodes';

	let { weather }: { weather: Weather | null } = $props();

	let current = $derived(weather?.current);
	// `temperature_2m` is optional in the payload, and defaulting it to 0
	// printed a believable `0°F` in the largest type on the panel - a wrong
	// number nobody would question, where a missing one is obvious at a
	// glance. Almanac already treats its own fields this way.
	let temperature = $derived(current?.temperature_2m);
	// Open-Meteo returns "°F"/"°C" in *_units; strip the degree sign so the
	// markup keeps its own and we render "72°F" rather than "72°°F".
	let unit = $derived((weather?.current_units?.temperature_2m ?? '').replace('°', ''));
</script>

<!-- The right half of the masthead, and nothing else. Everything that used to
     stack underneath this - the day's range, the sun times, the AQI, the moon -
     moved into Almanac, which lays them along one line instead of down the
     right edge. -->
<div class="weather">
	{#if !current || temperature === undefined}
		<p class="empty">Weather unavailable.</p>
	{:else}
		<span class="temp">{Math.round(temperature)}°{unit}</span>
		<span class="caps desc">{weatherDescription(current.weather_code)}</span>
	{/if}
</div>

<style>
	.weather {
		text-align: right;
		flex: none;
	}

	.empty {
		margin: 0;
		color: var(--ink-muted);
	}

	/* The one number on the panel readable from the doorway. Newsreader at a
	   regular weight rather than a bold sans: at this size the letterforms are
	   doing the work and extra weight only makes it heavier than the date it
	   sits beside. */
	.temp {
		display: block;
		font-family: var(--font-display);
		font-size: 4.25rem;
		font-weight: 400;
		line-height: 0.98;
		letter-spacing: -0.015em;
	}

	.desc {
		display: block;
		margin-top: 0.3rem;
	}
</style>
