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
		type Heartbeat,
		type Weather
	} from '$lib/api';
	import { createOrientation } from '$lib/orientation.svelte';
	import { startWatchdog } from '$lib/watchdog';
	import { isVisible, loadHidden, pruneHidden, saveHidden } from '$lib/calendarVisibility';
	import { loadView, saveView } from '$lib/viewPreference';
	import AgendaList from '$lib/components/AgendaList.svelte';
	import CalendarLegend from '$lib/components/CalendarLegend.svelte';
	import DayWeekView from '$lib/components/DayWeekView.svelte';
	import HourlyForecast from '$lib/components/HourlyForecast.svelte';
	import MonthGrid from '$lib/components/MonthGrid.svelte';
	import SkyEvents from '$lib/components/SkyEvents.svelte';
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

	// The server's date and instant, from the heartbeat. The panel must never
	// read its own clock for either - see format.ts for the same reasoning
	// about times. Both are $state because they are rendered from now: they
	// decide which events are shown as already over.
	let serverToday = $state<string | null>(null);
	let serverNow = $state<string | null>(null);

	// Rotating the panel changes what needs fetching, not just how it looks:
	// portrait shows the agenda under the calendar, so it needs both.
	const orientation = createOrientation(() => loadEvents());
	let isPortrait = $derived(orientation.isPortrait);

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
		// Set the clock from the same response that carried the events, so the
		// first render already knows which of them are over. Waiting for the
		// heartbeat leaves a freshly loaded panel showing a whole morning of
		// finished appointments at full strength until one arrives.
		serverToday = grid.today;
		serverNow = grid.now;
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
		// In portrait both are on screen at once, so both are fetched. The two
		// endpoints answer different questions - the grid covers the period
		// being navigated, the agenda is always "what is coming up next" - so
		// one cannot be derived from the other.
		if (view === 'agenda') loadAgenda();
		else {
			loadGrid();
			if (orientation.isPortrait) loadAgenda();
		}
	}

	function reloadEverything() {
		loadEvents();
		loadCalendars();
		loadWeather();
	}

	function onHeartbeat(heartbeat: Heartbeat) {
		// Every 30 seconds, which is the resolution at which an event stops
		// being current. Assigned before the early return below: the date not
		// having changed is the ordinary case, and it is exactly when the clock
		// still needs to advance.
		serverNow = heartbeat.now;

		if (serverToday === heartbeat.today) return;
		const rolledOver = serverToday !== null;
		serverToday = heartbeat.today;
		// A day boundary changes which cell is outlined and which heading reads
		// "Today", and nothing else would prompt it: events.updated only fires
		// when a sync actually changes something, so a quiet day would leave an
		// always-on panel showing yesterday indefinitely. Snap back to today
		// rather than holding whatever period was last navigated to - nobody is
		// at the wall at midnight, and the panel should be showing now.
		if (rolledOver) {
			anchor = null;
			reloadEverything();
		}
	}

	onMount(() => {
		hiddenCalendars = loadHidden();
		view = loadView();
		loadEvents();
		loadCalendars();
		loadWeather();

		const watchdog = startWatchdog();

		const unsubscribe = subscribeToUpdates({
			onMessage: () => watchdog.notify(),
			onHeartbeat,
			// The stream dropped and came back, so anything could have changed
			// while it was gone.
			onReconnect: reloadEverything,
			onEvent: (eventType) => {
				// Calendars are reloaded too: a config change adds or removes a
				// source, and the legend must follow without a page reload.
				if (eventType === 'events.updated') {
					loadEvents();
					loadCalendars();
				}
				if (eventType === 'weather.updated') loadWeather();
			}
		});

		return () => {
			watchdog.stop();
			unsubscribe();
		};
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
	<SkyEvents
		events={weather?.astro?.events ?? []}
		today={serverToday}
		moon={weather?.astro?.moon ?? null}
	/>
	<HourlyForecast {weather} />
	<div class="controls">
		<ViewSwitcher {view} onSelect={selectView} />
		<CalendarLegend {calendars} hidden={hiddenCalendars} onToggle={toggleCalendar} />
	</div>

	{#if view === 'agenda'}
		<AgendaList items={visibleItems} today={serverToday} now={serverNow} />
	{:else if grid}
		<PeriodNav
			title={grid.title}
			onPrev={() => goTo(grid!.prev_anchor)}
			onNext={() => goTo(grid!.next_anchor)}
			onToday={() => goTo(null)}
		/>
		{#if view === 'month'}
			<MonthGrid days={visibleDays} today={serverToday} now={serverNow} />
		{:else}
			<DayWeekView days={visibleDays} today={serverToday} now={serverNow} />
		{/if}
		{#if isPortrait}
			<section class="upcoming">
				<h2>Coming up</h2>
				<AgendaList items={visibleItems} today={serverToday} now={serverNow} />
			</section>
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

	.upcoming {
		margin-top: 1.5rem;
		border-top: 1px solid rgba(128, 128, 128, 0.3);
		padding-top: 0.5rem;
	}

	.upcoming h2 {
		font-size: 1rem;
		text-transform: uppercase;
		letter-spacing: 0.05em;
		opacity: 0.6;
		margin: 0.5rem 0 0;
	}

	/* Portrait is 1080px wide on the wall panel, so the header's two halves no
	   longer fit on one line and the generous padding costs real estate the
	   calendar needs. */
	@media (orientation: portrait) {
		main {
			padding: 1rem 0.75rem 2rem;
		}

		header {
			flex-direction: column;
			align-items: stretch;
			gap: 0.5rem;
		}
	}
</style>
