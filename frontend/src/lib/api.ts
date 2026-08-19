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

export type CalendarViewName = 'agenda' | 'day' | 'week' | 'month';

export interface CalendarView {
	view: string;
	anchor: string;
	title: string;
	today: string;
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

export interface Weather {
	current?: WeatherCurrent;
	daily?: WeatherDaily;
	hourly?: WeatherHourly;
	air_quality?: WeatherAirQuality;
	current_units?: WeatherUnits;
	daily_units?: WeatherUnits;
	hourly_units?: WeatherUnits;
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

export interface Heartbeat {
	/** The server's current date in the home timezone. The panel must not read
	 * its own clock to decide the day has rolled over - see format.ts. */
	today: string;
	now: string;
}

export interface UpdateStreamHandlers {
	/** An "events.updated" or "weather.updated" name. */
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
