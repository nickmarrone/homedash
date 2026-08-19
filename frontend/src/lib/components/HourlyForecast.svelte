<script lang="ts">
	import type { Weather } from '$lib/api';
	import { formatHour } from '$lib/format';

	let { weather }: { weather: Weather | null } = $props();

	const HOURS = 12;
	// Below this, a probability is noise on a wall panel - the bar still shows
	// it, but printing "8%" under every dry hour buries the ones that matter.
	const RAIN_LABEL_THRESHOLD = 20;

	let hours = $derived.by(() => {
		const hourly = weather?.hourly;
		const times = hourly?.time;
		if (!times?.length) return [];

		// The backend calls Open-Meteo with timezone=auto, so current.time
		// ("2026-08-19T13:45") and hourly.time ("2026-08-19T13:00") are the same
		// fixed-width format in the same timezone. Truncating both to the hour
		// makes plain string comparison chronological, which keeps the panel's
		// own clock and OS timezone out of it entirely - same reasoning as the
		// wall-clock parsing in lib/format.ts.
		const nowHour = weather?.current?.time?.slice(0, 13);
		const found = nowHour ? times.findIndex((time) => time.slice(0, 13) >= nowHour) : 0;
		const start = found > 0 ? found : 0;

		return times.slice(start, start + HOURS).map((time, i) => {
			const temp = hourly?.temperature_2m?.[start + i];
			const rain = hourly?.precipitation_probability?.[start + i] ?? 0;
			return {
				time,
				label: i === 0 ? 'Now' : formatHour(time),
				temp: temp === undefined ? '' : `${Math.round(temp)}°`,
				rain: Math.min(100, Math.max(0, rain))
			};
		});
	});
</script>

<!-- Nothing at all before the first successful fetch: WeatherWidget already
     says "Weather unavailable.", and two notices for one outage is noise. -->
{#if hours.length}
	<div class="hourly">
		<div class="row temps">
			{#each hours as hour (hour.time)}
				<span>{hour.temp}</span>
			{/each}
		</div>

		<!-- One SVG across the whole strip rather than one per column, so every
		     bar shares a baseline. The viewBox is in column units, which makes
		     the geometry a single expression and lets the strip scale to any
		     panel width. preserveAspectRatio="none" is safe here only because
		     these are axis-aligned, fill-only rects - do not add rx or a stroke,
		     which the non-uniform scale would distort. -->
		<svg
			class="bars"
			viewBox="0 0 {hours.length} 100"
			preserveAspectRatio="none"
			aria-hidden="true"
		>
			{#each hours as hour, i (hour.time)}
				<rect class="track" x={i + 0.15} y="0" width="0.7" height="100" />
				<rect class="fill" x={i + 0.15} y={100 - hour.rain} width="0.7" height={hour.rain} />
			{/each}
		</svg>

		<div class="row rain">
			{#each hours as hour (hour.time)}
				<span>{hour.rain >= RAIN_LABEL_THRESHOLD ? `${Math.round(hour.rain)}%` : ''}</span>
			{/each}
		</div>

		<div class="row labels">
			{#each hours as hour (hour.time)}
				<span>{hour.label}</span>
			{/each}
		</div>
	</div>
{/if}

<style>
	.hourly {
		margin-top: 1.5rem;
	}

	/* Equal columns that line up with the SVG's column-unit viewBox above.
	   min-width: 0 stops a wide label from pushing its column out of step. */
	.row {
		display: flex;
	}

	.row span {
		flex: 1;
		min-width: 0;
		text-align: center;
		white-space: nowrap;
	}

	.temps {
		font-size: 1rem;
		font-weight: 600;
		margin-bottom: 0.25rem;
	}

	.bars {
		display: block;
		width: 100%;
		height: 2.5rem;
	}

	/* No color tokens in this app - it themes purely via color-scheme, so the
	   bar borrows the inherited text color and the track is the same low-alpha
	   grey the legend chips use. Both stay legible in light and dark, and
	   neither can be mistaken for a calendar accent from the source palette. */
	.track {
		fill: rgba(128, 128, 128, 0.14);
	}

	.fill {
		fill: currentColor;
		opacity: 0.35;
	}

	.rain {
		font-size: 0.8rem;
		opacity: 0.7;
		/* Reserved even when every hour is dry, so the strip does not change
		   height between refreshes. */
		min-height: 1.1rem;
		margin-top: 0.2rem;
	}

	.labels {
		font-size: 0.8rem;
		opacity: 0.7;
	}
</style>
