<script lang="ts">
	import { onMount } from 'svelte';
	import {
		fetchAgenda,
		fetchCalendars,
		fetchWeather,
		subscribeToUpdates,
		type AgendaCalendar,
		type AgendaItem,
		type Weather
	} from '$lib/api';
	import AgendaList from '$lib/components/AgendaList.svelte';
	import CalendarLegend from '$lib/components/CalendarLegend.svelte';
	import WeatherWidget from '$lib/components/WeatherWidget.svelte';

	let items: AgendaItem[] = $state([]);
	let calendars: AgendaCalendar[] = $state([]);
	let weather: Weather | null = $state(null);

	async function loadAgenda() {
		items = await fetchAgenda();
	}

	async function loadCalendars() {
		calendars = await fetchCalendars();
	}

	async function loadWeather() {
		weather = await fetchWeather();
	}

	onMount(() => {
		loadAgenda();
		loadCalendars();
		loadWeather();

		const unsubscribe = subscribeToUpdates((eventType) => {
			// Calendars are reloaded too: a config change adds or removes a
			// source, and the legend must follow without a page reload.
			if (eventType === 'events.updated') {
				loadAgenda();
				loadCalendars();
			}
			if (eventType === 'weather.updated') loadWeather();
		});

		return unsubscribe;
	});
</script>

<svelte:head>
	<title>HomeDash</title>
</svelte:head>

<main>
	<header>
		<h1>HomeDash</h1>
		<WeatherWidget {weather} />
	</header>
	<CalendarLegend {calendars} />
	<AgendaList {items} />
</main>

<style>
	:global(html) {
		color-scheme: light dark;
	}

	:global(body) {
		margin: 0;
		font-family:
			system-ui,
			-apple-system,
			'Segoe UI',
			sans-serif;
	}

	main {
		max-width: 48rem;
		margin: 0 auto;
		padding: 2rem 1.5rem 4rem;
	}

	header {
		display: flex;
		align-items: flex-start;
		justify-content: space-between;
		gap: 1rem;
	}

	h1 {
		font-size: 1.5rem;
		margin: 0;
	}
</style>
