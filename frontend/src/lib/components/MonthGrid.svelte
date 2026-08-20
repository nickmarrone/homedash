<script lang="ts">
	import type { CalendarDay } from '$lib/api';
	import { formatTime, hasPassed } from '$lib/format';

	let {
		days,
		today = null,
		now = null
	}: { days: CalendarDay[]; today?: string | null; now?: string | null } = $props();

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
			<div
				class="cell"
				class:outside={!day.in_period}
				class:today={day.is_today}
				class:past={today !== null && day.date < today}
			>
				<span class="daynum">{day.day_of_month}</span>
				{#each day.items.slice(0, MAX_CHIPS) as item (item.id)}
					<div
						class="chip"
						class:allday={item.all_day}
						class:passed={hasPassed(day.date, item, today, now)}
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

	/* Today has to be findable in a 42-cell grid from across the room, so it
	   gets a heavier border and a lifted background rather than the hairline
	   outline it had. Inset, because the cells are 2px apart and a 3px outline
	   would otherwise read as belonging to the neighbouring day. */
	.today {
		outline: 3px solid currentColor;
		outline-offset: -2px;
		background: rgba(128, 128, 128, 0.2);
	}

	/* A day already gone dims its date only. The chips inside carry their own
	   finished treatment, and fading the whole cell as well would multiply the
	   two opacities into something barely legible. */
	.past .daynum {
		opacity: 0.45;
	}

	.daynum {
		font-size: 0.95rem;
		font-variant-numeric: tabular-nums;
		opacity: 0.75;
	}

	/* A filled pill, the way a phone calendar marks today. Grey rather than an
	   inverted swatch so it holds up in both themes without a palette. */
	.today .daynum {
		align-self: flex-start;
		padding: 0.05rem 0.45rem;
		border-radius: 999px;
		background: rgba(128, 128, 128, 0.45);
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

	/* An event that has already finished, on any day. Struck through as well as
	   faded, because a fade alone is easy to mistake for one of the
	   padding-day dims the grid is already full of. */
	.passed {
		opacity: 0.5;
		text-decoration: line-through;
		text-decoration-thickness: 1px;
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

	/* Portrait keeps all seven columns - a month grid is seven columns by
	   definition - but they are ~150px wide, so the cells give back the height
	   they were using to spread out and the chips lose their time prefix
	   rather than ellipsing every title away. */
	@media (orientation: portrait) {
		.cell {
			min-height: 4.5rem;
		}

		.chip {
			font-size: 0.75rem;
			padding: 0.1rem 0.2rem;
		}

		.chiptime {
			display: none;
		}
	}
</style>
