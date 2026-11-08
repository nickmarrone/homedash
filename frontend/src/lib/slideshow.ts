// Turns a playlist into an endless sequence of slides.
//
// Pure and DOM-free on purpose: everything here is orderings and pairings,
// which is the part worth reasoning about, and keeping it out of the component
// means it can be exercised without a browser. Not a rune module - it holds a
// queue, not reactive state, and runes only compile in .svelte / .svelte.ts.

import type { Photo } from '$lib/api';

/** One screenful: either a photo that fills the panel, or two that share it. */
export interface Slide {
	/** Stable across a rebuild of the same pairing, for keying the crossfade. */
	key: string;
	photos: Photo[];
}

export interface Slideshow {
	/** The next slide, or null when there are no photos at all. */
	next: () => Slide | null;
}

function shuffle<T>(items: T[], random: () => number): T[] {
	const shuffled = items.slice();
	for (let i = shuffled.length - 1; i > 0; i--) {
		const j = Math.floor(random() * (i + 1));
		[shuffled[i], shuffled[j]] = [shuffled[j], shuffled[i]];
	}
	return shuffled;
}

function slideOf(photos: Photo[]): Slide {
	return { key: photos.map((photo) => photo.id).join('+'), photos };
}

/** Every slide the playlist makes, in a fresh random order.
 *
 * Photos that fill the panel become slides on their own; the ones that
 * disagree with the panel's orientation are paired two to a screen, which is
 * what stops a portrait photo on a landscape panel from either being cropped
 * to a letterbox of its own middle or sitting in a black frame.
 *
 * The two groups are shuffled separately and only then merged, so pairing does
 * not depend on where a photo happened to land in one big shuffle. */
function buildSlides(photos: Photo[], random: () => number = Math.random): Slide[] {
	const full = shuffle(
		photos.filter((photo) => photo.slot === 'full'),
		random
	);
	const half = shuffle(
		photos.filter((photo) => photo.slot === 'half'),
		random
	);

	const slides = full.map((photo) => slideOf([photo]));
	for (let i = 0; i < half.length; i += 2) {
		// An odd one out is shown on its own rather than dropped or repeated
		// alongside itself. It leaves half the screen dark for one dwell, which
		// is a fair price for never silently hiding a family photo - and the
		// next shuffle picks a different photo to be the odd one.
		slides.push(slideOf(half.slice(i, i + 2)));
	}

	return shuffle(slides, random);
}

/** An endless shuffle that never shows the same slide twice in a row. */
export function createSlideshow(
	photos: Photo[],
	random: () => number = Math.random
): Slideshow {
	let queue: Slide[] = [];
	let last: Slide | null = null;

	const overlapsLast = (slide: Slide) =>
		slide.photos.some((photo) => last?.photos.some((shown) => shown.id === photo.id));

	const refill = () => {
		queue = buildSlides(photos, random);
		// The seam is the only place a plain reshuffle actually repeats: within
		// one pass every photo appears once, but the last slide of one pass can
		// easily share photos with the first of the next. Compared by photo
		// rather than by key, because pairs are re-dealt each pass: 2+3 and 3+2
		// are different keys and the same picture. Sometimes every slide
		// overlaps, and then a repeat is the only option.
		const clear = queue.findIndex((slide) => !overlapsLast(slide));
		if (clear > 0) [queue[0], queue[clear]] = [queue[clear], queue[0]];
	};

	return {
		next: () => {
			if (queue.length === 0) refill();
			const slide = queue.shift();
			if (!slide) return null;
			last = slide;
			return slide;
		}
	};
}
