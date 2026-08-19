<script lang="ts">
	import type { Weather } from '$lib/api';
	import { formatTime } from '$lib/format';
	import { weatherDescription } from '$lib/weatherCodes';

	let { weather }: { weather: Weather | null } = $props();

	let current = $derived(weather?.current);
	let today = $derived.by(() => {
		const daily = weather?.daily;
		if (!daily?.time?.length) return null;
		return {
			high: daily.temperature_2m_max?.[0],
			low: daily.temperature_2m_min?.[0],
			sunrise: daily.sunrise?.[0],
			sunset: daily.sunset?.[0]
		};
	});
</script>

<div class="weather">
	{#if !current}
		<p class="empty">Weather unavailable.</p>
	{:else}
		<div class="now">
			<span class="temp">{Math.round(current.temperature_2m ?? 0)}°</span>
			<span class="desc">{weatherDescription(current.weather_code)}</span>
		</div>
		{#if today}
			<div class="details">
				<span>H {Math.round(today.high ?? 0)}° / L {Math.round(today.low ?? 0)}°</span>
				{#if today.sunrise && today.sunset}
					<span>☀ {formatTime(today.sunrise)} – {formatTime(today.sunset)}</span>
				{/if}
			</div>
		{/if}
		{#if weather?.air_quality?.us_aqi !== undefined}
			<div class="aqi">AQI {Math.round(weather.air_quality.us_aqi)}</div>
		{/if}
	{/if}
</div>

<style>
	.weather {
		text-align: right;
	}

	.empty {
		opacity: 0.6;
	}

	.now {
		display: flex;
		align-items: baseline;
		gap: 0.5rem;
		justify-content: flex-end;
	}

	.temp {
		font-size: 2.5rem;
		font-weight: 700;
	}

	.desc {
		opacity: 0.8;
	}

	.details {
		display: flex;
		gap: 1rem;
		justify-content: flex-end;
		opacity: 0.7;
		font-size: 0.9rem;
		margin-top: 0.25rem;
	}

	.aqi {
		opacity: 0.6;
		font-size: 0.85rem;
		margin-top: 0.25rem;
	}
</style>
