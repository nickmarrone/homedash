import { beforeEach, describe, expect, it, vi } from 'vitest';
import { isVisible, loadHidden, pruneHidden, saveHidden } from './calendarVisibility';
import { loadPlayerId, pickPlayer, savePlayerId } from './musicPreference';
import { loadView, saveView } from './viewPreference';

function workingStorage(items: Record<string, string> = {}) {
	return {
		getItem: (key: string) => items[key] ?? null,
		setItem: (key: string, value: string) => {
			items[key] = value;
		}
	};
}

const brokenStorage = {
	getItem() {
		throw new Error('denied');
	},
	setItem() {
		throw new Error('quota');
	}
};

describe('hidden calendars', () => {
	beforeEach(() => vi.stubGlobal('localStorage', workingStorage()));

	it('round-trips through storage', () => {
		saveHidden(new Set([1, 3]));
		expect(loadHidden()).toEqual(new Set([1, 3]));
	});

	it('starts with nothing hidden', () => {
		expect(loadHidden()).toEqual(new Set());
	});

	it.each(['{not json', '{"a":1}', '42', 'null'])('ignores a stored %s', (raw) => {
		vi.stubGlobal('localStorage', workingStorage({ 'homedash:hidden-calendars': raw }));
		expect(loadHidden()).toEqual(new Set());
	});

	it('keeps only the numbers from a mixed list', () => {
		vi.stubGlobal('localStorage', workingStorage({ 'homedash:hidden-calendars': '[1,"2",null,3]' }));
		expect(loadHidden()).toEqual(new Set([1, 3]));
	});

	it('survives storage being unavailable', () => {
		vi.stubGlobal('localStorage', brokenStorage);
		expect(() => saveHidden(new Set([1]))).not.toThrow();
		expect(loadHidden()).toEqual(new Set());
	});

	it('forgets calendars that no longer exist', () => {
		expect(pruneHidden(new Set([1, 2, 9]), [1, 2, 3])).toEqual(new Set([1, 2]));
	});

	it('filters by calendar id', () => {
		expect(isVisible({ calendar: { id: 1 } }, new Set([1]))).toBe(false);
		expect(isVisible({ calendar: { id: 2 } }, new Set([1]))).toBe(true);
	});

	it('always shows an item with no calendar, which no chip could bring back', () => {
		expect(isVisible({ calendar: null }, new Set([1]))).toBe(true);
	});
});

describe('remembered view', () => {
	beforeEach(() => vi.stubGlobal('localStorage', workingStorage()));

	it('round-trips every view name', () => {
		for (const view of ['agenda', 'day', 'next3', 'next5', 'week', 'month'] as const) {
			saveView(view);
			expect(loadView()).toBe(view);
		}
	});

	it('defaults to month', () => {
		expect(loadView()).toBe('month');
	});

	it('honours a caller fallback', () => {
		expect(loadView('week')).toBe('week');
	});

	it('rejects a view name it does not know', () => {
		vi.stubGlobal('localStorage', workingStorage({ 'homedash:view': 'fortnight' }));
		expect(loadView()).toBe('month');
	});

	it('survives storage being unavailable', () => {
		vi.stubGlobal('localStorage', brokenStorage);
		expect(() => saveView('day')).not.toThrow();
		expect(loadView()).toBe('month');
	});
});

describe('remembered speaker', () => {
	beforeEach(() => vi.stubGlobal('localStorage', workingStorage()));

	it('round-trips, including a negative HEOS id', () => {
		savePlayerId(-1428);
		expect(loadPlayerId()).toBe(-1428);
	});

	it('is null when nothing was saved', () => {
		expect(loadPlayerId()).toBeNull();
	});

	it('is null for a stored value that is not an integer', () => {
		vi.stubGlobal('localStorage', workingStorage({ 'homedash:player': 'kitchen' }));
		expect(loadPlayerId()).toBeNull();
	});

	it('survives storage being unavailable', () => {
		vi.stubGlobal('localStorage', brokenStorage);
		expect(() => savePlayerId(1)).not.toThrow();
		expect(loadPlayerId()).toBeNull();
	});
});

describe('pickPlayer', () => {
	const speaker = (id: number, available = true) => ({ id, available });

	it('has nothing to pick from an empty list', () => {
		expect(pickPlayer([], 1)).toBeNull();
	});

	it('prefers the remembered speaker', () => {
		expect(pickPlayer([speaker(1), speaker(2)], 2)?.id).toBe(2);
	});

	it('falls back to an available speaker when the remembered one is gone', () => {
		expect(pickPlayer([speaker(1, false), speaker(2)], 9)?.id).toBe(2);
	});

	it('falls back the same way when nothing is remembered', () => {
		expect(pickPlayer([speaker(1, false), speaker(2)], null)?.id).toBe(2);
	});

	it('shows something even when every speaker is asleep', () => {
		expect(pickPlayer([speaker(1, false), speaker(2, false)], null)?.id).toBe(1);
	});
});
