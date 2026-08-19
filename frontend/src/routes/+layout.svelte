<script lang="ts">
	import favicon from '$lib/assets/favicon.svg';

	let { children } = $props();

	// A wall panel has no keyboard and no way back if a long-press or a
	// text selection takes over the screen, so the browser's touch
	// affordances are turned off globally rather than per component.
	function suppressContextMenu(event: Event) {
		event.preventDefault();
	}
</script>

<svelte:head>
	<link rel="icon" href={favicon} />
</svelte:head>

<svelte:document oncontextmenu={suppressContextMenu} />

{@render children()}

<style>
	:global(*) {
		-webkit-touch-callout: none;
		touch-action: manipulation;
	}

	/* Text selection is left on for inputs, so a future settings form is
	   still usable; it is the accidental drag-select on the panel that
	   needs suppressing. */
	:global(*:not(input):not(textarea)) {
		user-select: none;
		-webkit-user-select: none;
	}
</style>
