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
			class="tab"
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

</style>
