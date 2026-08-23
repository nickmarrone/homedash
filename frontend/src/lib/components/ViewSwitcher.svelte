<script lang="ts">
	import type { CalendarViewName } from '$lib/api';

	let {
		view,
		onSelect
	}: {
		view: CalendarViewName;
		onSelect: (view: CalendarViewName) => void;
	} = $props();

	const views: { id: CalendarViewName; label: string }[] = [
		{ id: 'agenda', label: 'Agenda' },
		{ id: 'day', label: 'Day' },
		{ id: 'next3', label: '3 Day' },
		{ id: 'next5', label: '5 Day' },
		{ id: 'week', label: 'Week' },
		{ id: 'month', label: 'Month' }
	];
</script>

<nav class="switcher" aria-label="Calendar view">
	{#each views as option (option.id)}
		<button
			type="button"
			class:selected={view === option.id}
			aria-pressed={view === option.id}
			onclick={() => onSelect(option.id)}
		>
			{option.label}
		</button>
	{/each}
</nav>

<style>
	.switcher {
		display: flex;
		/* Six options no longer fit on one line beside the legend on a portrait
		   panel, which is 1080px wide. Wrapping keeps every option reachable
		   rather than letting the row overflow off-screen. */
		flex-wrap: wrap;
	}

	button {
		/* 48px of height is what makes a target reliable for a fingertip on a
		   wall panel, where you are reaching rather than aiming. */
		min-height: var(--tap);
		padding: 0 0.9rem;
		border: none;
		/* The selected mark is an underline, so every button carries a
		   transparent one and only the width of the row changes. */
		border-bottom: 2px solid transparent;
		background: transparent;
		color: var(--ink-muted);
		font: inherit;
		font-size: 1.0625rem;
		cursor: pointer;
		/* Skips the browser's 300ms double-tap-to-zoom wait. */
		touch-action: manipulation;
		-webkit-tap-highlight-color: transparent;
	}

	/* Underlined rather than a raised pill, the way a printed index marks the
	   page you are on. It sits on the controls row's own rule, so the mark is
	   the page's rule thickening under one word. */
	.selected {
		color: var(--ink);
		font-weight: 600;
		border-bottom-color: var(--ink);
	}

	/* Press feedback replaces hover, which does not exist on touch. */
	button:active {
		transform: scale(0.97);
	}

	button:focus-visible {
		outline: 2px solid var(--ink);
		outline-offset: 2px;
	}
</style>
