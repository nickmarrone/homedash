<script lang="ts">
	import { onMount } from 'svelte';
	import { fetchAgenda, fetchWeather, subscribeToUpdates, type AgendaItem, type Weather } from '$lib/api';
	import AgendaList from '$lib/components/AgendaList.svelte';
	import WeatherWidget from '$lib/components/WeatherWidget.svelte';

	let items: AgendaItem[] = $state([]);
	let weather: Weather | null = $state(null);

	async function loadAgenda() {
		items = await fetchAgenda();
	}

	async function loadWeather() {
		weather = await fetchWeather();
	}

	onMount(() => {
		loadAgenda();
		loadWeather();

		const unsubscribe = subscribeToUpdates((eventType) => {
			if (eventType === 'events.updated') loadAgenda();
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
