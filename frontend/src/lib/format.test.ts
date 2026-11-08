import { afterEach, describe, expect, it, vi } from 'vitest';
import {
	addDays,
	dateKey,
	formatDayHeading,
	formatHour,
	formatMasthead,
	formatSkyDate,
	formatTime,
	hasPassed
} from './format';

describe('formatTime', () => {
	it.each([
		['2026-08-19T00:05:00-05:00', '12:05 AM'],
		['2026-08-19T09:30:00-05:00', '9:30 AM'],
		['2026-08-19T11:59:00+00:00', '11:59 AM'],
		['2026-08-19T12:00:00+00:00', '12:00 PM'],
		['2026-08-19T13:07:00Z', '1:07 PM'],
		['2026-08-19T23:59:00+14:00', '11:59 PM']
	])('%s reads as %s', (iso, expected) => {
		expect(formatTime(iso)).toBe(expected);
	});

	it('reads the wall clock digits whatever the panel timezone is', () => {
		vi.stubEnv('TZ', 'Pacific/Kiritimati');
		expect(formatTime('2026-08-19T09:30:00-05:00')).toBe('9:30 AM');
	});

	it('passes through what it cannot parse', () => {
		expect(formatTime('soon')).toBe('soon');
	});
});

describe('formatHour', () => {
	it.each([
		['2026-08-19T00:00', '12 AM'],
		['2026-08-19T11:00', '11 AM'],
		['2026-08-19T12:00', '12 PM'],
		['2026-08-19T14:00', '2 PM']
	])('%s reads as %s', (iso, expected) => {
		expect(formatHour(iso)).toBe(expected);
	});

	it('passes through what it cannot parse', () => {
		expect(formatHour('later')).toBe('later');
	});
});

describe('dateKey', () => {
	it('takes the date digits without converting', () => {
		expect(dateKey('2026-08-19T23:30:00-11:00')).toBe('2026-08-19');
	});
});

describe('addDays', () => {
	it.each([
		['2026-08-19', 1, '2026-08-20'],
		['2026-08-31', 1, '2026-09-01'],
		['2026-12-31', 1, '2027-01-01'],
		['2028-02-28', 1, '2028-02-29'],
		['2026-03-01', -1, '2026-02-28'],
		// The two US DST changes of 2026, which a local-time Date would mishandle.
		['2026-03-08', 1, '2026-03-09'],
		['2026-11-01', 1, '2026-11-02']
	])('%s plus %i is %s', (date, days, expected) => {
		expect(addDays(date, days)).toBe(expected);
	});
});

describe('formatDayHeading', () => {
	afterEach(() => vi.useRealTimers());

	it('names today and tomorrow from the server date', () => {
		expect(formatDayHeading('2026-08-19', '2026-08-19')).toBe('Today');
		expect(formatDayHeading('2026-08-20', '2026-08-19')).toBe('Tomorrow');
	});

	it('names tomorrow across a month end', () => {
		expect(formatDayHeading('2026-09-01', '2026-08-31')).toBe('Tomorrow');
	});

	it('trusts the server date over the panel clock', () => {
		vi.useFakeTimers();
		vi.setSystemTime(new Date(2030, 0, 1, 12));
		expect(formatDayHeading('2026-08-19', '2026-08-19')).toBe('Today');
	});

	it('spells out any other day, without saying today', () => {
		const heading = formatDayHeading('2026-08-25', '2026-08-19');
		expect(heading).not.toMatch(/today|tomorrow/i);
		expect(heading).toContain('25');
	});

	it.each(['Pacific/Midway', 'Pacific/Kiritimati'])('does not shift the date in %s', (tz) => {
		vi.stubEnv('TZ', tz);
		expect(formatDayHeading('2026-08-25', '2026-08-19')).toContain('25');
	});

	it('falls back to the panel clock before the server has said', () => {
		vi.useFakeTimers();
		vi.setSystemTime(new Date(2026, 7, 19, 23, 30));
		expect(formatDayHeading('2026-08-19')).toBe('Today');
		expect(formatDayHeading('2026-08-20')).toBe('Tomorrow');
		expect(formatDayHeading('2026-08-21')).toContain('21');
	});
});

describe('hasPassed', () => {
	const TODAY = '2026-08-19';
	const NOW = `${TODAY}T15:30:00-05:00`;
	const timed = (ends_at: string) => ({ all_day: false, ends_at });
	const allDay = { all_day: true, ends_at: `${TODAY}T00:00:00-05:00` };

	it('dims nothing before the server has said what time it is', () => {
		const old = timed('2020-01-01T00:00:00-05:00');
		expect(hasPassed('2020-01-01', old, null, NOW)).toBe(false);
		expect(hasPassed('2020-01-01', old, TODAY, null)).toBe(false);
	});

	it('treats an earlier day as over, even for an all-day event', () => {
		expect(hasPassed('2026-08-18', allDay, TODAY, NOW)).toBe(true);
	});

	it('treats a later day as not over, even if the event has ended', () => {
		expect(hasPassed('2026-08-20', timed('2020-01-01T00:00:00-05:00'), TODAY, NOW)).toBe(false);
	});

	it('keeps an all-day event lit for all of today', () => {
		expect(hasPassed(TODAY, allDay, TODAY, `${TODAY}T23:59:00-05:00`)).toBe(false);
	});

	it('compares a timed event against now', () => {
		expect(hasPassed(TODAY, timed(`${TODAY}T15:29:59-05:00`), TODAY, NOW)).toBe(true);
		expect(hasPassed(TODAY, timed(`${TODAY}T15:30:01-05:00`), TODAY, NOW)).toBe(false);
	});

	it('counts an event ending this very second as over', () => {
		expect(hasPassed(TODAY, timed(`${TODAY}T15:30:00-05:00`), TODAY, NOW)).toBe(true);
	});

	it('ignores sub-second digits on the heartbeat', () => {
		const precise = `${TODAY}T15:30:00.123456-05:00`;
		expect(hasPassed(TODAY, timed(`${TODAY}T15:30:00-05:00`), TODAY, precise)).toBe(true);
	});

	it('leaves running an event that continues past midnight', () => {
		expect(hasPassed(TODAY, timed('2026-08-20T01:00:00-05:00'), TODAY, NOW)).toBe(false);
	});
});

describe('formatSkyDate', () => {
	it('says Tonight and Tomorrow from the server date', () => {
		expect(formatSkyDate('2026-08-19', '2026-08-19')).toBe('Tonight');
		expect(formatSkyDate('2026-08-20', '2026-08-19')).toBe('Tomorrow');
	});

	it('gives an abbreviated date otherwise', () => {
		const text = formatSkyDate('2026-08-26', '2026-08-19');
		expect(text).not.toMatch(/tonight|tomorrow/i);
		expect(text).toContain('26');
	});

	it('gives a date when today is not known', () => {
		expect(formatSkyDate('2026-08-19', null)).toContain('19');
	});
});

describe('formatMasthead', () => {
	it.each(['Pacific/Midway', 'UTC', 'Pacific/Kiritimati'])('holds the date steady in %s', (tz) => {
		vi.stubEnv('TZ', tz);
		const masthead = formatMasthead('2026-08-23');
		expect(masthead.date).toContain('23');
		expect(masthead.date).toContain('2026');
		expect(masthead.weekday).toMatch(/\p{L}+/u);
	});

	it('names a Sunday as the same weekday in every zone', () => {
		vi.stubEnv('TZ', 'Pacific/Midway');
		const west = formatMasthead('2026-08-23').weekday;
		vi.stubEnv('TZ', 'Pacific/Kiritimati');
		expect(formatMasthead('2026-08-23').weekday).toBe(west);
	});
});
