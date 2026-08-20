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

/**
 * "Today", "Tomorrow", or "Wednesday, August 19".
 *
 * `today` is the server's date, from the SSE heartbeat or the grid response.
 * Pass it whenever it is known: the fallback reads the browser's own clock,
 * and a panel whose OS timezone differs from HOMEDASH_HOME_TIMEZONE then
 * labels the wrong day "Today" - visibly, right beside a grid the server
 * already dated correctly.
 */
export function formatDayHeading(dateKeyValue: string, today: string | null = null): string {
	if (today) {
		if (dateKeyValue === today) return 'Today';
		if (dateKeyValue === addDays(today, 1)) return 'Tomorrow';
	} else {
		const clockToday = new Date();
		clockToday.setHours(0, 0, 0, 0);
		const [y, m, d] = dateKeyValue.split('-').map(Number);
		const diffDays = Math.round((new Date(y, m - 1, d).getTime() - clockToday.getTime()) / 86_400_000);
		if (diffDays === 0) return 'Today';
		if (diffDays === 1) return 'Tomorrow';
	}
	const [year, month, day] = dateKeyValue.split('-').map(Number);
	return new Date(Date.UTC(year, month - 1, day)).toLocaleDateString(undefined, {
		timeZone: 'UTC',
		weekday: 'long',
		month: 'long',
		day: 'numeric'
	});
}

/**
 * Whether an item rendered on `dayDate` is already over.
 *
 * Asked per rendered day rather than per item, which is what keeps it simple:
 * the grid has already bucketed a multi-day event onto each day it touches, so
 * there is no span arithmetic to redo here - and no chance of it drifting from
 * `grid.local_dates_spanned`, which owns the awkward conventions (an all-day
 * DTEND is exclusive, a timed event ending at midnight belongs to the day it
 * started).
 *
 * `today` and `now` come from the SSE heartbeat, never from the panel's own
 * clock - the same rule the rest of this module follows. Before the first
 * heartbeat lands both are null and nothing is dimmed, because dimming a
 * future event is a worse error than briefly failing to dim a past one.
 *
 * Comparing the ISO strings directly works because the backend emits both
 * these timestamps and the heartbeat in the home timezone, so the wall-clock
 * digits sort chronologically - see the note at the top of this file. The one
 * seam is the hour either side of a DST change, where two instants on the same
 * day carry different offsets; being an hour out on when an event greys is not
 * worth a Date for.
 */
export function hasPassed(
	dayDate: string,
	item: { all_day: boolean; ends_at: string },
	today: string | null,
	now: string | null
): boolean {
	if (!today || !now) return false;
	if (dayDate < today) return true;
	if (dayDate > today) return false;
	// An all-day event is today's all day, whatever the time is. Its ends_at
	// cannot answer this: it is exclusive midnight when a DTEND was present and
	// equal to its start when there was none.
	if (item.all_day) return false;
	return item.ends_at.slice(0, 19) <= now.slice(0, 19);
}

/**
 * A dated sky event relative to the server's today: "Tonight", "Tomorrow", or
 * "Wed, Aug 26".
 *
 * Separate from `formatDayHeading` because the wording and the width differ:
 * "Tonight" is what makes somebody actually go outside and look, and the strip
 * has one line to spare, so the date is abbreviated.
 *
 * The `Date` below only ever does calendar arithmetic on date components, in
 * UTC, and never reads the clock.
 */
export function formatSkyDate(date: string, today: string | null): string {
	if (today) {
		if (date === today) return 'Tonight';
		if (date === addDays(today, 1)) return 'Tomorrow';
	}
	const [year, month, day] = date.split('-').map(Number);
	return new Date(Date.UTC(year, month - 1, day)).toLocaleDateString(undefined, {
		timeZone: 'UTC',
		weekday: 'short',
		month: 'short',
		day: 'numeric'
	});
}

/** `date` shifted by whole days, as another YYYY-MM-DD string. */
export function addDays(date: string, days: number): string {
	const [year, month, day] = date.split('-').map(Number);
	return new Date(Date.UTC(year, month - 1, day + days)).toISOString().slice(0, 10);
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
