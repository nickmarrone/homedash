<script lang="ts">
	import { onMount } from 'svelte';
	import {
		fetchAgenda,
		fetchCalendarView,
		fetchCalendars,
		fetchPhotos,
		fetchWeather,
		subscribeToUpdates,
		type AgendaCalendar,
		type AgendaItem,
		type CalendarView,
		type CalendarViewName,
		type Heartbeat,
		type PhotoPlaylist,
		type Weather
	} from '$lib/api';
	import { createOrientation } from '$lib/orientation.svelte';
	import { createMusic } from '$lib/music.svelte';
	import { startIdleTimer, type IdleTimer } from '$lib/idle';
	import { startWatchdog } from '$lib/watchdog';
	import { startBackoff } from '$lib/retry';
	import { isVisible, loadHidden, pruneHidden, saveHidden } from '$lib/calendarVisibility';
	import { loadView, saveView } from '$lib/viewPreference';
	import { formatMasthead } from '$lib/format';
	import AgendaList from '$lib/components/AgendaList.svelte';
	import Almanac from '$lib/components/Almanac.svelte';
	import CalendarLegend from '$lib/components/CalendarLegend.svelte';
	import DayWeekView from '$lib/components/DayWeekView.svelte';
	import HourlyForecast from '$lib/components/HourlyForecast.svelte';
	import MonthGrid from '$lib/components/MonthGrid.svelte';
	import MusicOverlay from '$lib/components/MusicOverlay.svelte';
	import NowPlayingBar from '$lib/components/NowPlayingBar.svelte';
	import PeriodNav from '$lib/components/PeriodNav.svelte';
	import PanelBlank from '$lib/components/PanelBlank.svelte';
	import Screensaver from '$lib/components/Screensaver.svelte';
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

	// Everything about the speakers, including the commands that change them.
	// It never touches the calendar, so it lives in its own module rather than
	// adding a sixth state domain to this one - see lib/music.svelte.ts.
	const music = createMusic();

	let playlist = $state<PhotoPlaylist | null>(null);
	let idle = $state(false);
	// Starts lit so a panel that has not had its first heartbeat yet shows the
	// calendar. Assuming "off" would flash the screen black on every reload.
	let screenOn = $state(true);
	let idleTimer: IdleTimer | null = null;

	// Rotating the panel changes what needs fetching, not just how it looks:
	// portrait shows the agenda under the calendar, so it needs both. The photo
	// playlist changes too - which photos are the awkward ones inverts with the
	// panel, so the slots come back different.
	const orientation = createOrientation(() => {
		loadEvents();
		loadPhotos();
	});
	let isPortrait = $derived(orientation.isPortrait);

	// Bedtime wins over idleness: a screen that should be dark is not a screen
	// that should be showing photographs.
	let screensaverOn = $derived(
		idle && screenOn && (playlist?.photos.length ?? 0) > 0
	);

	// Null until the server has told us what day it is. The heartbeat updates
	// serverToday at midnight, so the masthead rolls over on its own without
	// the panel ever reading its own clock.
	let masthead = $derived(serverToday ? formatMasthead(serverToday) : null);

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
		guard(next === 'agenda' ? loadAgenda() : loadGrid());
	}

	function goTo(next: string | null) {
		anchor = next;
		guard(loadGrid());
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

	async function loadPhotos() {
		const next = await fetchPhotos(orientation.isPortrait ? 'portrait' : 'landscape');
		playlist = next;
		// The idle timeout is server configuration, so the timer cannot be
		// started until the first playlist has arrived with it.
		restartIdleTimer(next.idle_minutes);
	}

	// The timeout the running timer was built with, so an unchanged one can be
	// left alone. Restarting resets "last activity" to now, and this runs on
	// every reload - so flaky wifi reconnecting the stream every few minutes
	// against a five-minute threshold kept pushing idleness out of reach and
	// the screensaver simply never appeared, with nothing logged anywhere.
	let idleMinutesInUse: number | null = null;

	function restartIdleTimer(idleMinutes: number) {
		if (idleTimer !== null && idleMinutes === idleMinutesInUse) return;
		idleMinutesInUse = idleMinutes;
		idleTimer?.stop();
		idleTimer = startIdleTimer({
			idleAfterMs: Math.max(1, idleMinutes) * 60_000,
			onIdle: () => (idle = true),
			onActive: () => (idle = false)
		});
	}

	function dismissScreensaver() {
		// The overlay swallowed the tap, so the window listener never saw it.
		idle = false;
		idleTimer?.notify();
	}

	function loadEvents(): Promise<unknown> {
		// In portrait both are on screen at once, so both are fetched. The two
		// endpoints answer different questions - the grid covers the period
		// being navigated, the agenda is always "what is coming up next" - so
		// one cannot be derived from the other.
		if (view === 'agenda') return loadAgenda();
		return Promise.all([loadGrid(), orientation.isPortrait ? loadAgenda() : null]);
	}

	// Every fetch in this file goes through here or through `guard` below.
	// A loader that rejects with nobody watching is not just a lost update: on
	// a cold start it is the whole panel, permanently, because the SSE
	// watchdog only fires on a *quiet* stream and a backend that has since
	// come up keeps the stream perfectly healthy. See lib/retry.ts.
	async function reloadEverything(): Promise<void> {
		const results = await Promise.allSettled([
			loadEvents(),
			loadCalendars(),
			loadWeather(),
			loadPhotos(),
			music.load()
		]);
		const failed = results.filter((r) => r.status === 'rejected');
		if (failed.length === 0) {
			backoff.succeed();
			return;
		}
		console.warn(`HomeDash: ${failed.length} of ${results.length} loads failed; retrying`);
		backoff.fail();
	}

	/** For the one-off loads a tap triggers. A single failure arms the same
	 * retry as a failed round, which reloads everything - blunter than
	 * reissuing just this fetch, and correct for the reason that made it fail. */
	function guard(load: Promise<unknown>): void {
		load.catch(() => backoff.fail());
	}

	const backoff = startBackoff(() => {
		reloadEverything();
	});

	function onHeartbeat(heartbeat: Heartbeat) {
		// Every 30 seconds, which is the resolution at which an event stops
		// being current. Assigned before the early return below: the date not
		// having changed is the ordinary case, and it is exactly when the clock
		// still needs to advance.
		serverNow = heartbeat.now;

		// The schedule is the server's to decide, for the same reason the date
		// is: the panel's own clock is not trusted anywhere in this app. An
		// older backend omits the field, in which case the panel stays lit.
		const wasOn = screenOn;
		screenOn = heartbeat.screen !== 'off';
		if (!wasOn && screenOn) {
			// Coming back from bedtime. The idle timer kept running behind the
			// blank all night, so `idle` is long since true and the panel would
			// otherwise wake straight into the slideshow - the first thing on
			// the wall in the morning being photos, with the day's calendar one
			// tap away behind them. Start the day on the calendar.
			idle = false;
			idleTimer?.notify();
		}

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
		reloadEverything();

		const watchdog = startWatchdog();

		const unsubscribe = subscribeToUpdates({
			onMessage: () => watchdog.notify(),
			onHeartbeat,
			// The stream dropped and came back, so anything could have changed
			// while it was gone.
			onReconnect: reloadEverything,
			onError: (fatal) => {
				// A dropped stream reopens by itself, and the watchdog covers
				// it going quiet. A *closed* one never reopens, so the page has
				// to be replaced - and the watchdog's own reload throttle is
				// what stops this becoming a loop against a backend that is
				// simply down.
				if (fatal) watchdog.reloadNow();
			},
			onEvent: (eventType) => {
				// Calendars are reloaded too: a config change adds or removes a
				// source, and the legend must follow without a page reload.
				if (eventType === 'events.updated') {
					guard(loadEvents());
					guard(loadCalendars());
				}
				if (eventType === 'weather.updated') guard(loadWeather());
				if (eventType === 'photos.updated') guard(loadPhotos());
				if (eventType === 'music.updated') guard(music.load());
			}
		});

		return () => {
			watchdog.stop();
			backoff.stop();
			idleTimer?.stop();
			unsubscribe();
		};
	});
</script>

<svelte:head>
	<title>HomeDash</title>
</svelte:head>

<main>
	<!-- The masthead states today's date, which the panel did nowhere at all
	     before: the product name was in the one place a family calendar has no
	     use for it. Empty until the first heartbeat or grid response lands,
	     because the date shown here is the server's - the panel's own clock is
	     not trusted anywhere in this app. -->
	<header>
		<div class="masthead">
			{#if masthead}
				<span class="caps">{masthead.weekday}</span>
				<h1>{masthead.date}</h1>
			{/if}
		</div>
		<WeatherWidget {weather} />
	</header>
	<div class="masthead-rule"></div>
	<Almanac {weather} today={serverToday} />
	<HourlyForecast {weather} />
	<div class="controls">
		<CalendarLegend {calendars} hidden={hiddenCalendars} onToggle={toggleCalendar} />
		<!-- Pushed right by its own margin rather than by justify-content, so it
		     still sits against the right edge on a single-calendar panel, where
		     the legend renders nothing at all. -->
		<div class="switcher-slot">
			<ViewSwitcher {view} onSelect={selectView} />
		</div>
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
				<h2 class="caps">Coming up</h2>
				<AgendaList items={visibleItems} today={serverToday} now={serverNow} />
			</section>
		{/if}
	{/if}

	{#if music.barVisible && music.activePlayer}
		<div class="music-slot">
			<NowPlayingBar
				player={music.activePlayer}
				onAction={music.transport}
				onOpen={music.openOverlay}
			/>
		</div>
	{:else if music.hasLibrary && music.activePlayer}
		<!-- With nothing playing there is no bar, so there has to be some other
		     way in. A single button rather than a permanent strip: the calendar
		     is what the panel is for, and this is the smallest thing that keeps
		     the library reachable. -->
		<div class="music-slot quiet">
			<button class="control-round open-music" type="button" onclick={music.openOverlay}>
				<svg viewBox="0 0 24 24" aria-hidden="true">
					<path
						d="M9 18V6l10-2v12"
						fill="none"
						stroke="currentColor"
						stroke-width="2"
						stroke-linecap="round"
						stroke-linejoin="round"
					/>
					<circle cx="6.5" cy="18" r="2.5" fill="currentColor" />
					<circle cx="16.5" cy="16" r="2.5" fill="currentColor" />
				</svg>
				Music
			</button>
		</div>
	{/if}
</main>

{#if music.overlayOpen && music.activePlayer}
	<MusicOverlay
		players={music.players ?? []}
		player={music.activePlayer}
		hasLibrary={music.hasLibrary}
		onSelectPlayer={music.selectPlayer}
		onAction={music.transport}
		onVolume={music.setVolume}
		onPlayAlbum={music.playAlbum}
		onPlayTracks={music.playTracks}
		onClose={music.closeOverlay}
	/>
{/if}

{#if screensaverOn && playlist}
	<!-- Photos still win on idle - that is what the screensaver is for - and
	     the track rides along on top, so the panel can still answer "what is
	     this song" without giving up the family photos. -->
	<Screensaver
		{playlist}
		nowPlaying={music.activePlayer?.state === 'play'
			? (music.activePlayer.now_playing ?? null)
			: null}
		onDismiss={dismissScreensaver}
	/>
{/if}

{#if !screenOn}
	<PanelBlank />
{/if}

<style>
	main {
		/* Deliberately full width: a month grid needs the whole panel. The
		   narrow column this used to have was an agenda-only choice. */
		margin: 0 auto;
		padding: 2rem 2.25rem 2.25rem;
	}

	header {
		display: flex;
		align-items: flex-end;
		justify-content: space-between;
		gap: 1.75rem;
	}

	.masthead {
		min-width: 0;
	}

	h1 {
		margin: 0.25rem 0 0;
		font-family: var(--font-display);
		font-size: 3.5rem;
		font-weight: 500;
		line-height: 1.02;
		letter-spacing: -0.015em;
	}

	/* Two rules, thick over thin. One line would read as a border; the pair
	   reads as the rule under a masthead, which is the whole point of the
	   direction - this is a printed page, not a dashboard. */
	.masthead-rule {
		margin-top: 0.875rem;
		border-top: 2px solid var(--ink);
	}

	.masthead-rule::after {
		content: '';
		display: block;
		margin-top: 3px;
		border-top: 1px solid var(--rule);
	}

	.controls {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 0 1.25rem;
		margin-top: 1.5rem;
		/* The controls row is a rule the selected view underlines through, so
		   the switcher's own 2px mark has something to sit on. */
		border-bottom: 1px solid var(--rule);
	}

	.switcher-slot {
		margin-left: auto;
	}

	/* Sticky rather than in flow at the end: month view fills a 1080-tall
	   panel, so a bar that simply followed the grid would sit below the fold
	   exactly when there is most to look at. */
	.music-slot {
		position: sticky;
		bottom: 0;
		z-index: 20;
		padding-top: 1rem;
		margin-top: 1.5rem;
		/* Opaque, or the calendar scrolls through the bar. It is the page's own
		   ground rather than a panel of its own: the strip is separated by its
		   rule, not by a change of surface. */
		background: var(--paper);
	}

	/* Nothing playing: a button, not a bar, so an idle panel gives the
	   calendar back the space. */
	.music-slot.quiet {
		display: flex;
		justify-content: flex-end;
	}

	/* .control-round, but a pill rather than a circle: this one carries a word
	   as well as a glyph, so it needs width and a gap between them. */
	.open-music {
		gap: 0.5rem;
		padding: 0 1.25rem;
		font: inherit;
	}

	.open-music svg {
		width: 22px;
		height: 22px;
	}


	.upcoming {
		margin-top: 1.75rem;
		border-top: 1px solid var(--rule);
		padding-top: 0.75rem;
	}

	/* "Coming up" labels the section; the day headings inside AgendaList label
	   the groups. Two identical caps lines in a row need the air to read as a
	   heading over a list rather than as one wrapped label. */
	.upcoming h2 {
		margin: 0;
	}

	/* Portrait is 1080px wide on the wall panel, so the header's two halves no
	   longer fit on one line and the generous padding costs real estate the
	   calendar needs. */
	@media (orientation: portrait) {
		main {
			padding: 1.5rem 1.5rem 1.75rem;
		}

		/* The header's two halves stay on one line here, which they could not
		   before: the date replaced a product name that was longer than it, and
		   the temperature lost the four detail rows that used to stack under
		   it. Stacking them would push the calendar down for nothing. */
		header {
			gap: 1.25rem;
		}

		h1 {
			font-size: 3rem;
		}
	}
</style>
