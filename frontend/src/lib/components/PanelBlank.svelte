<script lang="ts">
	// Nothing to configure and nothing to dismiss - see the markup below.
</script>

<!-- The fallback deploy/pi/README.md describes for a monitor that ignores
     output power management: if the hardware will not go dark, at least the
     picture can. The backlight stays on, so this is worse than a real blank,
     but the kitchen goes dark and the panel stops lighting the room at 2am.

     Deliberately not tap-to-dismiss. The screen is meant to be off, and an
     elbow brushing it in the dark should not turn the calendar back on; it
     clears on its own when the schedule says the screen is lit again. -->
<div class="blank" aria-hidden="true"></div>

<style>
	.blank {
		position: fixed;
		inset: 0;
		/* Above the screensaver: bedtime wins over idleness. */
		z-index: 200;
		background: #000;
		cursor: none;
	}
</style>
