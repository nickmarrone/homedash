// The view the panel was left on. A wall display is not reopened by a person
// choosing a view each morning - it reboots and has to come back the way it
// was, so this is remembered rather than reset to a default.

import type { CalendarViewName } from '$lib/api';

const STORAGE_KEY = 'homedash:view';
const VIEWS: CalendarViewName[] = ['agenda', 'day', 'week', 'month'];

export function loadView(fallback: CalendarViewName = 'month'): CalendarViewName {
	try {
		const raw = localStorage.getItem(STORAGE_KEY);
		return VIEWS.includes(raw as CalendarViewName) ? (raw as CalendarViewName) : fallback;
	} catch {
		// Unreadable or disabled storage must never blank the panel.
		return fallback;
	}
}

export function saveView(view: CalendarViewName): void {
	try {
		localStorage.setItem(STORAGE_KEY, view);
	} catch {
		// Quota or private-mode failures are not worth breaking rendering over.
	}
}
