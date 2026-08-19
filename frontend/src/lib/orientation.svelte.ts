// Whether the panel is taller than it is wide.
//
// The wall panel can be mounted either way up, and the two need different
// layouts rather than the same one squeezed: portrait puts the agenda under
// the calendar, where landscape has no room for both.
//
// This mirrors the `@media (orientation: portrait)` rules in the components.
// CSS drives the layout wherever it can, because it needs no JavaScript and
// follows a rotation instantly; this exists only for the part CSS cannot do,
// which is deciding whether to fetch the agenda at all.

import { onMount } from 'svelte';

const QUERY = '(orientation: portrait)';

/** A reactive `isPortrait`, kept in sync with the media query.
 *
 * Returns false during SSR and before mount. The app prerenders with
 * `ssr = false`, so the first real value arrives on mount, and the CSS has
 * already laid the page out correctly by then regardless.
 */
export function createOrientation(onChange?: (isPortrait: boolean) => void) {
	let isPortrait = $state(false);

	onMount(() => {
		const media = window.matchMedia(QUERY);
		isPortrait = media.matches;

		const update = (event: MediaQueryListEvent) => {
			isPortrait = event.matches;
			onChange?.(event.matches);
		};
		media.addEventListener('change', update);
		return () => media.removeEventListener('change', update);
	});

	return {
		get isPortrait() {
			return isPortrait;
		}
	};
}
