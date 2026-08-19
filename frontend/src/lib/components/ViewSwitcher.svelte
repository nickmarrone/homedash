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
		gap: 0.25rem;
		padding: 0.25rem;
		border-radius: 999px;
		background: rgba(128, 128, 128, 0.14);
	}

	button {
		/* 48px: the smallest target that stays reliable for a fingertip on a
		   wall panel, where you are often reaching rather than aiming. */
		min-height: 48px;
		padding: 0 1.25rem;
		border: none;
		border-radius: 999px;
		background: transparent;
		color: inherit;
		font: inherit;
		font-size: 1rem;
		cursor: pointer;
		/* Skips the browser's 300ms double-tap-to-zoom wait. */
		touch-action: manipulation;
		-webkit-tap-highlight-color: transparent;
	}

	.selected {
		background: Canvas;
		font-weight: 600;
		box-shadow: 0 1px 3px rgba(0, 0, 0, 0.2);
	}

	/* Press feedback replaces hover, which does not exist on touch. */
	button:active {
		transform: scale(0.97);
	}

	button:focus-visible {
		outline: 2px solid currentColor;
		outline-offset: 2px;
	}
</style>
