import { describe, expect, it } from 'vitest';
import type { Photo } from '$lib/api';
import { createSlideshow, type Slide } from './slideshow';

function photo(id: number, slot: Photo['slot']): Photo {
	return { id, slot, width: 1, height: 1, url: `/p/${id}` };
}
const full = (...ids: number[]) => ids.map((id) => photo(id, 'full'));
const half = (...ids: number[]) => ids.map((id) => photo(id, 'half'));

/** A small deterministic PRNG (mulberry32), so a failure can be replayed. */
function seeded(seed: number): () => number {
	return () => {
		seed = (seed + 0x6d2b79f5) | 0;
		let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
		t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
		return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
	};
}

function take(photos: Photo[], count: number, seed = 1): Slide[] {
	const show = createSlideshow(photos, seeded(seed));
	return Array.from({ length: count }, () => show.next()!);
}

const ids = (slide: Slide) => slide.photos.map((p) => p.id);
const SEEDS = Array.from({ length: 200 }, (_, i) => i + 1);

describe('createSlideshow', () => {
	it('has nothing to show for an empty playlist', () => {
		expect(createSlideshow([]).next()).toBeNull();
	});

	it('shows a full-slot photo on its own', () => {
		expect(take(full(1), 1).map(ids)).toEqual([[1]]);
	});

	it('keeps showing the only slide there is', () => {
		expect(take(full(1), 3).map(ids)).toEqual([[1], [1], [1]]);
	});

	it('pairs half-slot photos two to a screen', () => {
		const pass = take(half(1, 2, 3, 4), 2);
		expect(pass.every((slide) => slide.photos.length === 2)).toBe(true);
		expect(pass.flatMap(ids).sort()).toEqual([1, 2, 3, 4]);
	});

	it('shows an odd one out alone rather than dropping it', () => {
		const pass = take(half(1, 2, 3), 2);
		expect(pass.map((s) => s.photos.length).sort()).toEqual([1, 2]);
		expect(pass.flatMap(ids).sort()).toEqual([1, 2, 3]);
	});

	it('never pairs a full photo with a half one', () => {
		for (const seed of SEEDS) {
			for (const slide of take([...full(1, 2), ...half(3, 4)], 30, seed)) {
				const slots = new Set(slide.photos.map((p) => p.slot));
				expect(slots.size).toBe(1);
			}
		}
	});

	it('shows every photo once per pass', () => {
		for (const seed of SEEDS) {
			const pass = take(full(1, 2, 3, 4, 5), 5, seed);
			expect(pass.flatMap(ids).sort()).toEqual([1, 2, 3, 4, 5]);
		}
	});

	it('gives a slide a key stable across equal pairings', () => {
		expect(take(full(7), 2).map((s) => s.key)).toEqual(['7', '7']);
		expect(take(half(1, 2), 1)[0].key).toMatch(/^(1\+2|2\+1)$/);
	});

	it('never repeats a slide across the seam between passes', () => {
		for (const seed of SEEDS) {
			const slides = take(full(1, 2, 3), 300, seed);
			for (let i = 1; i < slides.length; i++) {
				expect(slides[i].key).not.toBe(slides[i - 1].key);
			}
		}
	});

	it('terminates on a random source stuck at one end', () => {
		for (const value of [0, 0.999999]) {
			const show = createSlideshow(full(1, 2, 3), () => value);
			for (let i = 0; i < 20; i++) expect(show.next()).not.toBeNull();
		}
	});
});
