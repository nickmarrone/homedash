<script lang="ts">
	import { onMount } from 'svelte';
	import {
		fetchAgenda,
		fetchCalendarView,
		fetchCalendars,
		fetchWeather,
		subscribeToUpdates,
		type AgendaCalendar,
		type AgendaItem,
		type CalendarView,
		type CalendarViewName,
		type Weather
	} from '$lib/api';
	import { isVisible, loadHidden, pruneHidden, saveHidden } from '$lib/calendarVisibility';
	import { loadView, saveView } from '$lib/viewPreference';
	import AgendaList from '$lib/components/AgendaList.svelte';
	import CalendarLegend from '$lib/components/CalendarLegend.svelte';
	import DayWeekView from '$lib/components/DayWeekView.svelte';
	import HourlyForecast from '$lib/components/HourlyForecast.svelte';
	import MonthGrid from '$lib/components/MonthGrid.svelte';
	import PeriodNav from '$lib/components/PeriodNav.svelte';
	import ViewSwitcher from '$lib/components/ViewSwitcher.svelte';
	import WeatherWidget from '$lib/components/WeatherWidget.svelte';

	let items: AgendaItem[] = $state([]);
	let calendars: AgendaCalendar[] = $state([]);
	let weather: Weather | null = $state(null);
	let hiddenCalendars: Set<number> = $state(new Set());

	let view: CalendarViewName = $state('month');
	let grid = $state<CalendarView | null>(null);
	// Null means "wherever today is" - the backend resolves it, so no date
	// arithmetic happens here.
	let anchor = $state<string | null>(null);

	let visibleItems = $derived(items.filter((item) => isVisible(item, hiddenCalendars)));

	// The same filter applied to grid buckets, so a hidden calendar disappears
	// from every view rather than only the one it was hidden in.
	let visibleDays = $derived(
		(grid?.days ?? []).map((day) => ({
			...day,
			items: day.items.filter((item) => isVisible(item, hiddenCalendars))
		}))
	);

	function toggleCalendar(id: number) {
		// Reassign rather than mutate: $state tracks the binding, not Set writes.
		const next = new Set(hiddenCalendars);
		if (next.has(id)) next.delete(id);
		else next.add(id);
		hiddenCalendars = next;
		saveHidden(next);
	}

	function selectView(next: CalendarViewName) {
		view = next;
		anchor = null;
		saveView(next);
		if (next === 'agenda') loadAgenda();
		else loadGrid();
	}

	function goTo(next: string | null) {
		anchor = next;
		loadGrid();
	}

	async function loadAgenda() {
		items = await fetchAgenda();
	}

	async function loadGrid() {
		if (view === 'agenda') return;
		grid = await fetchCalendarView(view, anchor ?? undefined);
	}

	async function loadCalendars() {
		calendars = await fetchCalendars();
		const pruned = pruneHidden(
			hiddenCalendars,
			calendars.map((c) => c.id)
		);
		if (pruned.size !== hiddenCalendars.size) {
			hiddenCalendars = pruned;
			saveHidden(pruned);
		}
	}

	async function loadWeather() {
		weather = await fetchWeather();
	}

	function loadEvents() {
		if (view === 'agenda') loadAgenda();
		else loadGrid();
	}

	onMount(() => {
		hiddenCalendars = loadHidden();
		view = loadView();
		loadEvents();
		loadCalendars();
		loadWeather();

		const unsubscribe = subscribeToUpdates((eventType) => {
			// Calendars are reloaded too: a config change adds or removes a
			// source, and the legend must follow without a page reload.
			if (eventType === 'events.updated') {
				loadEvents();
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
	<HourlyForecast {weather} />
	<div class="controls">
		<ViewSwitcher {view} onSelect={selectView} />
		<CalendarLegend {calendars} hidden={hiddenCalendars} onToggle={toggleCalendar} />
	</div>

	{#if view === 'agenda'}
		<AgendaList items={visibleItems} />
	{:else if grid}
		<PeriodNav
			title={grid.title}
			onPrev={() => goTo(grid!.prev_anchor)}
			onNext={() => goTo(grid!.next_anchor)}
			onToday={() => goTo(null)}
		/>
		{#if view === 'month'}
			<MonthGrid days={visibleDays} />
		{:else}
			<DayWeekView days={visibleDays} />
		{/if}
	{/if}
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
		/* Deliberately full width: a month grid needs the whole panel. The
		   narrow column this used to have was an agenda-only choice. */
		margin: 0 auto;
		padding: 1.5rem 1.5rem 3rem;
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

	.controls {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 1rem;
		margin-top: 1rem;
	}
</style>
