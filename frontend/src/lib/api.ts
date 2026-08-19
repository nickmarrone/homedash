export interface AgendaMember {
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
	member: AgendaMember | null;
}

export interface WeatherCurrent {
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

export interface WeatherAirQuality {
	us_aqi?: number;
	european_aqi?: number;
	pm2_5?: number;
}

export interface Weather {
	current?: WeatherCurrent;
	daily?: WeatherDaily;
	air_quality?: WeatherAirQuality;
	fetched_at?: string;
}

export async function fetchAgenda(): Promise<AgendaItem[]> {
	const response = await fetch('/api/agenda');
	if (!response.ok) throw new Error(`agenda fetch failed: ${response.status}`);
	return response.json();
}

export async function fetchWeather(): Promise<Weather> {
	const response = await fetch('/api/weather');
	if (!response.ok) throw new Error(`weather fetch failed: ${response.status}`);
	return response.json();
}

/** Subscribes to the backend's SSE stream and calls `onEvent` with the
 * event name ("events.updated" | "weather.updated") whenever one arrives.
 * Returns an unsubscribe function. */
export function subscribeToUpdates(onEvent: (eventType: string) => void): () => void {
	const source = new EventSource('/api/events/stream');
	const forward = (event: MessageEvent) => onEvent(event.type);
	source.addEventListener('events.updated', forward);
	source.addEventListener('weather.updated', forward);
	return () => source.close();
}
