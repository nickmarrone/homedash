// WMO weather interpretation codes, as used by Open-Meteo.
// https://open-meteo.com/en/docs#weathervariables

const DESCRIPTIONS: Record<number, string> = {
	0: 'Clear sky',
	1: 'Mainly clear',
	2: 'Partly cloudy',
	3: 'Overcast',
	45: 'Fog',
	48: 'Depositing rime fog',
	51: 'Light drizzle',
	53: 'Drizzle',
	55: 'Dense drizzle',
	56: 'Light freezing drizzle',
	57: 'Freezing drizzle',
	61: 'Light rain',
	63: 'Rain',
	65: 'Heavy rain',
	66: 'Light freezing rain',
	67: 'Freezing rain',
	71: 'Light snow',
	73: 'Snow',
	75: 'Heavy snow',
	77: 'Snow grains',
	80: 'Light rain showers',
	81: 'Rain showers',
	82: 'Violent rain showers',
	85: 'Light snow showers',
	86: 'Snow showers',
	95: 'Thunderstorm',
	96: 'Thunderstorm with light hail',
	99: 'Thunderstorm with heavy hail'
};

export function weatherDescription(code: number | undefined): string {
	if (code === undefined) return 'Unknown';
	return DESCRIPTIONS[code] ?? 'Unknown';
}
