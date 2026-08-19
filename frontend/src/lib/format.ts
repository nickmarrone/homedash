// starts_at/ends_at ISO strings from the backend already carry the home
// timezone's offset - parse the wall-clock digits directly rather than
// going through Date/Intl, so the display never drifts if the panel's own
// OS timezone differs from the configured home timezone.

export function formatTime(iso: string): string {
	const match = iso.match(/T(\d{2}):(\d{2})/);
	if (!match) return iso;
	let hours = Number(match[1]);
	const minutes = match[2];
	const suffix = hours >= 12 ? 'PM' : 'AM';
	hours = hours % 12 || 12;
	return `${hours}:${minutes} ${suffix}`;
}

export function dateKey(iso: string): string {
	return iso.slice(0, 10);
}

export function formatDayHeading(dateKeyValue: string): string {
	const [year, month, day] = dateKeyValue.split('-').map(Number);
	const date = new Date(year, month - 1, day);
	const today = new Date();
	today.setHours(0, 0, 0, 0);
	const diffDays = Math.round((date.getTime() - today.getTime()) / 86_400_000);

	if (diffDays === 0) return 'Today';
	if (diffDays === 1) return 'Tomorrow';
	return date.toLocaleDateString(undefined, { weekday: 'long', month: 'long', day: 'numeric' });
}

// Compact hour label for the hourly strip: "2 PM", "11 AM". Same wall-clock
// digit parsing as formatTime - Open-Meteo hourly timestamps carry no offset
// and are already local to the configured coordinates.
export function formatHour(iso: string): string {
	const match = iso.match(/T(\d{2}):/);
	if (!match) return iso;
	const hours = Number(match[1]);
	const suffix = hours >= 12 ? 'PM' : 'AM';
	return `${hours % 12 || 12} ${suffix}`;
}
