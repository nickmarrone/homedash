<script lang="ts">
	import type { AgendaCalendar } from '$lib/api';

	let { calendars }: { calendars: AgendaCalendar[] } = $props();
</script>

<!-- A legend for a single calendar is noise - every row would be the same color. -->
{#if calendars.length > 1}
	<ul class="legend">
		{#each calendars as calendar (calendar.id)}
			<li>
				<span class="swatch" style:background-color={calendar.color} aria-hidden="true"></span>
				<span class="name">{calendar.name}</span>
			</li>
		{/each}
	</ul>
{/if}

<style>
	.legend {
		display: flex;
		flex-wrap: wrap;
		gap: 0.5rem 1.25rem;
		list-style: none;
		margin: 1rem 0 0;
		padding: 0;
	}

	li {
		display: flex;
		align-items: center;
		gap: 0.5rem;
	}

	.swatch {
		width: 0.9rem;
		height: 0.9rem;
		border-radius: 3px;
		flex-shrink: 0;
	}

	.name {
		font-size: 1rem;
		opacity: 0.8;
	}
</style>
