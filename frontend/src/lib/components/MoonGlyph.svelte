<script lang="ts">
	import type { MoonPhase } from '$lib/api';

	let { moon, size = 20 }: { moon: MoonPhase; size?: number } = $props();

	// Drawn rather than lettered. The obvious implementation is the matching
	// emoji, but Raspberry Pi OS Lite ships no emoji font, so on the actual
	// wall panel that renders as a tofu box - and this is the one glyph the
	// widget exists for. Drawing it also gets the *real* illuminated fraction
	// instead of rounding to the nearest of eight.
	const R = 10;

	// The terminator is the edge of the disc seen at an angle, so it projects
	// to a half-ellipse whose x semi-axis runs from +R at new, through 0 at
	// quarter, to -R at full. The sign is what flips the curve from cutting
	// into the lit side (crescent) to bulging away from it (gibbous).
	let rx = $derived(R * (1 - 2 * moon.illumination));
	let sweep = $derived(rx > 0 ? 0 : 1);

	// Northern waxing is lit on the right. Waning mirrors it, and crossing the
	// equator mirrors it again - so the two flips cancel.
	let litOnRight = $derived(moon.waxing !== moon.southern);

	let lit = $derived(
		`M 0,${-R} A ${R},${R} 0 0 1 0,${R} A ${Math.abs(rx)},${R} 0 0 ${sweep} 0,${-R} Z`
	);
</script>

<svg
	class="moon"
	width={size}
	height={size}
	viewBox="-11 -11 22 22"
	role="img"
	aria-label="{moon.phase}, {Math.round(moon.illumination * 100)}% illuminated"
>
	<!-- The unlit disc, so a crescent still reads as a whole moon rather than a
	     detached sliver. Low-alpha grey for the same reason the rest of the app
	     uses it: it holds up in both light and dark without a palette. -->
	<circle r={R} fill="rgba(128, 128, 128, 0.25)" />
	<g transform={litOnRight ? undefined : 'scale(-1, 1)'}>
		<path d={lit} fill="currentColor" />
	</g>
	<circle r={R} fill="none" stroke="currentColor" stroke-width="0.75" opacity="0.35" />
</svg>

<style>
	.moon {
		display: inline-block;
		vertical-align: -0.15em;
	}
</style>
