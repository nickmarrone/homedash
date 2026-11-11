// How much of the screen a physical frame hides at the top and bottom.
//
// A panel mounted behind a wooden frame loses a strip of pixels along its top
// and bottom edges. The browser has no way to know that - the viewport is the
// whole screen - so without this the masthead date and the now-playing bar sit
// partly under the wood.
//
// Configured on the kiosk URL, e.g. `http://homedash.local:8000/?frame_top=24&frame_bottom=18`,
// in CSS pixels. The URL rather than the server's settings because the frame
// belongs to one physical panel, not to the household: a second panel without
// one should not inherit the margins. And the URL rather than localStorage
// because the kiosk runs a throwaway browser profile, so anything stored there
// is gone after the next restart. `deploy/pi/setup.sh --frame-top/--frame-bottom`
// writes it into the kiosk's launch command.
//
// This module only decides the two numbers and publishes them as custom
// properties on the root element. Every layout rule that touches a screen
// edge reads `--frame-top` / `--frame-bottom` (see theme.css), so nothing here
// knows about any component.
//
// Plain .ts, not .svelte.ts: there is no reactive state. The frame does not
// change while the page is up; changing it means changing the URL, which
// reloads.

export interface Frame {
	/** CSS pixels hidden along the top edge. */
	top: number;
	/** CSS pixels hidden along the bottom edge. */
	bottom: number;
}

export const NO_FRAME: Frame = { top: 0, bottom: 0 };

/** No real frame covers a quarter of a screen. A larger value is a typo -
 * `frame_top=240` for 24 - and honouring it would push the calendar off the
 * panel, which on a wall with no keyboard is hard to diagnose. */
export const MAX_INSET_PX = 400;

const PARAMS = { top: 'frame_top', bottom: 'frame_bottom' } as const;

function parseInset(raw: string | null): number {
	if (raw === null || raw.trim() === '') return 0;
	const value = Number(raw);
	if (!Number.isFinite(value) || value < 0) return 0;
	return Math.min(value, MAX_INSET_PX);
}

/** Read the frame from a query string (`location.search`). Anything missing or
 * unreadable counts as no inset on that edge, so a bad value costs at most a
 * strip of the panel rather than the panel. */
export function parseFrame(search: string): Frame {
	const params = new URLSearchParams(search);
	return {
		top: parseInset(params.get(PARAMS.top)),
		bottom: parseInset(params.get(PARAMS.bottom))
	};
}

/** Publish the frame as `--frame-top` / `--frame-bottom` on `root`. */
export function applyFrame(root: HTMLElement, frame: Frame): void {
	root.style.setProperty('--frame-top', `${frame.top}px`);
	root.style.setProperty('--frame-bottom', `${frame.bottom}px`);
}
