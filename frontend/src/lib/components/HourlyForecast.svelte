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
		margin-top: 1.35rem;
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
		font-size: 1.0625rem;
		font-weight: 600;
		color: var(--ink-soft);
		margin-bottom: 0.35rem;
	}

	.bars {
		display: block;
		width: 100%;
		height: 2.75rem;
	}

	/* Rain is the one quantity on this panel that is neither text nor an
	   appointment, so it gets the only colour in the palette that is neither -
	   see --rain in theme.css. The track is the paper's wash, which is what the
	   direction uses wherever a filled block is unavoidable. */
	.track {
		fill: var(--wash);
	}

	.fill {
		fill: var(--rain);
	}

	.rain {
		font-size: 0.8125rem;
		color: var(--ink-muted);
		/* Reserved even when every hour is dry, so the strip does not change
		   height between refreshes. */
		min-height: 1.1rem;
		margin-top: 0.25rem;
	}

	.labels {
		font-size: 0.75rem;
		font-weight: 600;
		letter-spacing: 0.12em;
		text-transform: uppercase;
		color: var(--ink-muted);
	}
</style>
