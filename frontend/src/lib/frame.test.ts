import { describe, expect, it } from 'vitest';
import { MAX_INSET_PX, NO_FRAME, applyFrame, parseFrame } from './frame';

describe('parseFrame', () => {
	it('is no frame at all when the URL says nothing', () => {
		expect(parseFrame('')).toEqual(NO_FRAME);
		expect(parseFrame('?view=month')).toEqual(NO_FRAME);
	});

	it('reads each edge independently', () => {
		expect(parseFrame('?frame_top=24&frame_bottom=18')).toEqual({ top: 24, bottom: 18 });
		expect(parseFrame('?frame_top=24')).toEqual({ top: 24, bottom: 0 });
		expect(parseFrame('?frame_bottom=18')).toEqual({ top: 0, bottom: 18 });
	});

	it('accepts fractional pixels, which a scaled display can need', () => {
		expect(parseFrame('?frame_top=12.5').top).toBe(12.5);
	});

	it('treats an unreadable edge as uncovered rather than failing the other', () => {
		expect(parseFrame('?frame_top=abc&frame_bottom=18')).toEqual({ top: 0, bottom: 18 });
		expect(parseFrame('?frame_top=-10')).toEqual(NO_FRAME);
		expect(parseFrame('?frame_top=')).toEqual(NO_FRAME);
		expect(parseFrame('?frame_top=24px')).toEqual(NO_FRAME);
		expect(parseFrame('?frame_top=Infinity')).toEqual(NO_FRAME);
	});

	it('caps a value no real frame could need', () => {
		expect(parseFrame('?frame_top=2400').top).toBe(MAX_INSET_PX);
	});
});

describe('applyFrame', () => {
	it('publishes both edges as pixel custom properties', () => {
		const set = new Map<string, string>();
		const root = {
			style: { setProperty: (name: string, value: string) => set.set(name, value) }
		} as unknown as HTMLElement;

		applyFrame(root, { top: 24, bottom: 0 });

		expect(Object.fromEntries(set)).toEqual({ '--frame-top': '24px', '--frame-bottom': '0px' });
	});
});
