<script lang="ts">
	import type { CalendarDay } from '$lib/api';
	import { formatTime } from '$lib/format';

	let { days }: { days: CalendarDay[] } = $props();

	// How many chips fit in a cell before the rest become a count. A wall panel
	// is read at a glance, so an overflowing cell is worse than an honest
	// "+3 more".
	const MAX_CHIPS = 3;

	// Taken from the data rather than hardcoded, so it follows
	// HOMEDASH_WEEK_STARTS_ON without the frontend knowing the setting exists.
	let weekdays = $derived(days.slice(0, 7).map((day) => day.weekday_short));
</script>

<div class="month">
	<div class="weekdays" aria-hidden="true">
		{#each weekdays as label}
			<span>{label}</span>
		{/each}
	</div>
	<div class="grid">
		{#each days as day (day.date)}
			<div class="cell" class:outside={!day.in_period} class:today={day.is_today}>
				<span class="daynum">{day.day_of_month}</span>
				{#each day.items.slice(0, MAX_CHIPS) as item (item.id)}
					<div
						class="chip"
						class:allday={item.all_day}
						style:--chip-color={item.calendar?.color ?? '#888'}
					>
						{#if !item.all_day}
							<span class="chiptime">{formatTime(item.starts_at)}</span>
						{/if}
						<span class="chiptitle">{item.title}</span>
					</div>
				{/each}
				{#if day.items.length > MAX_CHIPS}
					<span class="more">+{day.items.length - MAX_CHIPS} more</span>
				{/if}
			</div>
		{/each}
	</div>
</div>

<style>
	.weekdays,
	.grid {
		display: grid;
		grid-template-columns: repeat(7, minmax(0, 1fr));
		gap: 2px;
	}

	.weekdays span {
		padding: 0.4rem 0.5rem;
		font-size: 0.85rem;
		text-transform: uppercase;
		letter-spacing: 0.05em;
		opacity: 0.6;
	}

	.cell {
		/* A fixed minimum keeps every week the same height, so the grid does
		   not jump around as events come and go. */
		min-height: 7.5rem;
		display: flex;
		flex-direction: column;
		gap: 2px;
		padding: 0.35rem;
		border-radius: 6px;
		background: rgba(128, 128, 128, 0.08);
		overflow: hidden;
	}

	/* Padding days are dimmed rather than blank: the grid stays rectangular
	   and the eye still reads the month boundary. */
	.outside {
		opacity: 0.35;
	}

	.today {
		outline: 2px solid currentColor;
	}

	.daynum {
		font-size: 0.95rem;
		font-variant-numeric: tabular-nums;
		opacity: 0.75;
	}

	.today .daynum {
		font-weight: 700;
		opacity: 1;
	}

	.chip {
		display: flex;
		align-items: baseline;
		gap: 0.3rem;
		padding: 0.1rem 0.3rem;
		border-left: 3px solid var(--chip-color);
		border-radius: 3px;
		background: color-mix(in srgb, var(--chip-color) 16%, transparent);
		font-size: 0.85rem;
		/* One line per chip: wrapping would make cell heights uneven and the
		   grid unreadable from across a room. */
		white-space: nowrap;
		overflow: hidden;
		text-overflow: ellipsis;
	}

	/* An all-day item reads as a banner rather than an appointment. */
	.allday {
		background: var(--chip-color);
		color: #fff;
		border-left-color: transparent;
	}

	.chiptime {
		opacity: 0.75;
		font-variant-numeric: tabular-nums;
	}

	.chiptitle {
		overflow: hidden;
		text-overflow: ellipsis;
	}

	.more {
		font-size: 0.8rem;
		opacity: 0.6;
		padding-left: 0.3rem;
	}
</style>
