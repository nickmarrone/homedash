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
	import { loadHidden, pruneHidden, saveHidden } from '$lib/calendarVisibility';
	import AgendaList from '$lib/components/AgendaList.svelte';
	import CalendarLegend from '$lib/components/CalendarLegend.svelte';
	import WeatherWidget from '$lib/components/WeatherWidget.svelte';

	let items: AgendaItem[] = $state([]);
	let calendars: AgendaCalendar[] = $state([]);
	let weather: Weather | null = $state(null);
	let hiddenCalendars: Set<number> = $state(new Set());

	// Items with no calendar (an orphaned instance) always show - there is no
	// legend chip that could bring them back.
	let visibleItems = $derived(
		items.filter((item) => !item.calendar || !hiddenCalendars.has(item.calendar.id))
	);

	function toggleCalendar(id: number) {
		// Reassign rather than mutate: $state tracks the binding, not Set writes.
		const next = new Set(hiddenCalendars);
		if (next.has(id)) next.delete(id);
		else next.add(id);
		hiddenCalendars = next;
		saveHidden(next);
	}

	async function loadAgenda() {
		items = await fetchAgenda();
	}

	async function loadCalendars() {
		calendars = await fetchCalendars();
		const pruned = pruneHidden(hiddenCalendars, calendars.map((c) => c.id));
		if (pruned.size !== hiddenCalendars.size) {
			hiddenCalendars = pruned;
			saveHidden(pruned);
		}
	}

	async function loadWeather() {
		weather = await fetchWeather();
	}

	onMount(() => {
		hiddenCalendars = loadHidden();
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
	<CalendarLegend {calendars} hidden={hiddenCalendars} onToggle={toggleCalendar} />
	<AgendaList items={visibleItems} />
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
