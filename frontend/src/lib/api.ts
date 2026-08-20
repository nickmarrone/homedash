export interface AgendaCalendar {
	id: number;
	name: string;
	color: string;
}

export interface AgendaItem {
	id: number;
	title: string;
	location: string | null;
	all_day: boolean;
	starts_at: string;
	ends_at: string;
	calendar: AgendaCalendar | null;
}

/** One item as it appears inside a calendar grid: an agenda item plus where
 * it sits relative to the day it is being rendered on. */
export interface CalendarGridItem extends AgendaItem {
	continues_before: boolean;
	continues_after: boolean;
}

export interface CalendarDay {
	date: string;
	day_of_month: number;
	weekday_short: string;
	/** False for the padding days a month grid needs to stay rectangular. */
	in_period: boolean;
	is_today: boolean;
	items: CalendarGridItem[];
}

/** `next3` and `next5` are the rolling lookaheads: a fixed number of days
 * starting at the anchor, which with no anchor is today. Unlike `week` the
 * backend does not snap them to a week boundary. */
export type CalendarViewName = 'agenda' | 'day' | 'next3' | 'next5' | 'week' | 'month';

export interface CalendarView {
	view: string;
	anchor: string;
	title: string;
	today: string;
	/** The server's clock when it built this response. Carried so a freshly
	 * loaded panel can dim what has already finished immediately, instead of
	 * waiting up to 30 seconds for the first SSE heartbeat to tell it the
	 * time. */
	now: string;
	/** The backend does the date arithmetic, so navigation needs none here. */
	prev_anchor: string;
	next_anchor: string;
	days: CalendarDay[];
}

export interface WeatherCurrent {
	// Open-Meteo's own "now" stamp, in the coordinates' local timezone because
	// the backend calls it with timezone=auto. HourlyForecast anchors off this
	// rather than the browser clock - see lib/format.ts for why.
	time?: string;
	temperature_2m?: number;
	apparent_temperature?: number;
	relative_humidity_2m?: number;
	weather_code?: number;
	wind_speed_10m?: number;
}

export interface WeatherDaily {
	time?: string[];
	weather_code?: number[];
	temperature_2m_max?: number[];
	temperature_2m_min?: number[];
	sunrise?: string[];
	sunset?: string[];
	daylight_duration?: number[];
}

export interface WeatherHourly {
	time?: string[];
	temperature_2m?: number[];
	precipitation_probability?: number[];
}

export interface WeatherAirQuality {
	us_aqi?: number;
	european_aqi?: number;
	pm2_5?: number;
}

export interface WeatherUnits {
	temperature_2m?: string;
	temperature_2m_max?: string;
	temperature_2m_min?: string;
}

export interface MoonPhase {
	/** "Waxing Gibbous" and friends. */
	phase: string;
	/** Illuminated fraction of the disc, 0 to 1. */
	illumination: number;
	age_days: number;
	/** Which side is lit: waxing and waning are equally illuminated and are
	 * mirror images, and the whole picture flips again south of the equator. */
	waxing: boolean;
	southern: boolean;
}

export interface SkyEvent {
	kind: 'moon' | 'meteor_shower' | 'season' | 'comet';
	name: string;
	/** Local calendar date, YYYY-MM-DD. */
	date: string;
	detail: string | null;
	/** Comets only: predicted visual magnitude, lower being brighter. */
	magnitude?: number;
}

/** Computed on the server from the configured coordinates, not fetched from
 * Open-Meteo - which is why it is still here when the weather is not. */
export interface Astro {
	moon: MoonPhase;
	events: SkyEvent[];
}

export interface Weather {
	current?: WeatherCurrent;
	daily?: WeatherDaily;
	hourly?: WeatherHourly;
	air_quality?: WeatherAirQuality;
	current_units?: WeatherUnits;
	daily_units?: WeatherUnits;
	hourly_units?: WeatherUnits;
	astro?: Astro;
	fetched_at?: string;
}

export async function fetchAgenda(): Promise<AgendaItem[]> {
	const response = await fetch('/api/agenda');
	if (!response.ok) throw new Error(`agenda fetch failed: ${response.status}`);
	return response.json();
}

export async function fetchCalendars(): Promise<AgendaCalendar[]> {
	const response = await fetch('/api/calendars');
	if (!response.ok) throw new Error(`calendars fetch failed: ${response.status}`);
	return response.json();
}

export async function fetchCalendarView(
	view: Exclude<CalendarViewName, 'agenda'>,
	anchor?: string
): Promise<CalendarView> {
	const query = new URLSearchParams({ view });
	if (anchor) query.set('anchor', anchor);
	const response = await fetch(`/api/calendar?${query}`);
	if (!response.ok) throw new Error(`calendar fetch failed: ${response.status}`);
	return response.json();
}

export async function fetchWeather(): Promise<Weather> {
	const response = await fetch('/api/weather');
	if (!response.ok) throw new Error(`weather fetch failed: ${response.status}`);
	return response.json();
}

/** How much of the panel one photo takes: 'full' agrees with the orientation
 * the panel is mounted in, 'half' does not and is shown paired with another. */
export type PhotoSlot = 'full' | 'half';

export interface Photo {
	id: number;
	slot: PhotoSlot;
	/** The size the derivative was rendered at, so the img can be sized before
	 * it loads and the crossfade never reflows mid-transition. */
	width: number;
	height: number;
	/** Carries the content hash, so it is safe to cache forever and changes
	 * the moment the photo does. */
	url: string;
}

export interface PhotoPlaylist {
	dwell_seconds: number;
	idle_minutes: number;
	photos: Photo[];
}

export type PanelOrientation = 'landscape' | 'portrait';

export async function fetchPhotos(orientation: PanelOrientation): Promise<PhotoPlaylist> {
	const response = await fetch(`/api/photos?orientation=${orientation}`);
	if (!response.ok) throw new Error(`photos fetch failed: ${response.status}`);
	return response.json();
}

export interface Heartbeat {
	/** The server's current date in the home timezone. The panel must not read
	 * its own clock to decide the day has rolled over - see format.ts. */
	today: string;
	now: string;
	/** Whether the screen schedule says the display should be lit. The
	 * screensaver must not start when it says "off", and the panel renders
	 * black instead - see components/PanelBlank.svelte. */
	screen?: 'on' | 'off';
}

export interface UpdateStreamHandlers {
	/** An "events.updated", "weather.updated" or "photos.updated" name. */
	onEvent: (eventType: string) => void;
	onHeartbeat?: (heartbeat: Heartbeat) => void;
	/** Fired when the stream reopens after dropping. EventSource retries on its
	 * own, but nothing re-syncs the DOM that went stale meanwhile, so the page
	 * needs to refetch here or it silently serves whatever it had before. */
	onReconnect?: () => void;
	/** Called on every message of any kind, for the staleness watchdog. */
	onMessage?: () => void;
}

/** Subscribes to the backend's SSE stream. Returns an unsubscribe function. */
export function subscribeToUpdates(handlers: UpdateStreamHandlers): () => void {
	const source = new EventSource('/api/events/stream');
	// The first `open` is the initial connection, not a reconnection; only the
	// ones after a drop should trigger a refetch.
	let hasConnected = false;

	const forward = (event: MessageEvent) => {
		handlers.onMessage?.();
		handlers.onEvent(event.type);
	};
	source.addEventListener('events.updated', forward);
	source.addEventListener('weather.updated', forward);
	source.addEventListener('photos.updated', forward);

	source.addEventListener('heartbeat', (event: MessageEvent) => {
		handlers.onMessage?.();
		try {
			handlers.onHeartbeat?.(JSON.parse(event.data) as Heartbeat);
		} catch {
			// A malformed heartbeat still proves the stream is alive, which is
			// most of its job. Don't let it take the page down.
		}
	});

	source.addEventListener('open', () => {
		handlers.onMessage?.();
		if (hasConnected) handlers.onReconnect?.();
		hasConnected = true;
	});

	return () => source.close();
}
