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
			<span class="caps-sm">{label}</span>
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
					{@const passed = hasPassed(day.date, item, today, now)}
					<!-- A chip is only a title, so the strike goes on the chip itself.
					     The list views strike their title span and leave the time
					     beside it legible. Same treatment, two shapes. -->
					<div
						class="chip"
						class:allday={item.all_day}
						class:passed
						class:struck={passed}
						style:--item-color={item.calendar?.color ?? 'var(--accent-fallback)'}
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
	/* A ruled table, not a field of boxes: no gaps, no radii, no fills. The
	   lines do the separating the way a printed calendar does it, which is what
	   gives 42 cells a structure you can read across a kitchen. The grid carries
	   its top and left rules and every cell carries its right and bottom, so
	   there is exactly one line between any two cells. */
	.weekdays,
	.grid {
		display: grid;
		grid-template-columns: repeat(7, minmax(0, 1fr));
	}

	.grid {
		border-top: 1px solid var(--rule);
		border-left: 1px solid var(--rule);
	}

	.weekdays {
		padding-bottom: 0.4rem;
	}

	/* Layout only; the type is .caps-sm in theme.css. */
	.weekdays span {
		padding-left: 0.55rem;
	}

	.cell {
		/* A fixed minimum keeps every week the same height, so the grid does
		   not jump around as events come and go. */
		min-height: 7.5rem;
		display: flex;
		flex-direction: column;
		gap: 0.3rem;
		padding: 0.45rem 0.5rem 0.55rem;
		border-right: 1px solid var(--rule);
		border-bottom: 1px solid var(--rule);
		overflow: hidden;
	}

	/* Padding days are sunk rather than faded: opacity on the cell would take
	   the chips inside down with it, and a half-strength accent bar reads as a
	   finished event rather than as another month. */
	.outside {
		background: var(--paper-sunk);
	}

	.outside .daynum {
		color: var(--ink-trace);
	}

	/* Today is the sheet the rest is printed on - the one cell that goes whiter
	   than the page - and its date is set in an ink disc. Two marks instead of
	   the three it used to carry, and no outline, which on a gapless grid would
	   have had nowhere to sit. */
	.today {
		background: var(--paper-raised);
	}

	.daynum {
		font-family: var(--font-display);
		font-size: 1.3rem;
		font-weight: 500;
		line-height: 1.05;
		color: var(--ink-muted);
	}

	/* A day already gone marks its date only. The chips inside carry their own
	   finished treatment. */
	.past .daynum {
		color: var(--ink-trace);
	}

	.today .daynum {
		align-self: flex-start;
		display: grid;
		place-items: center;
		width: 2.125rem;
		height: 2.125rem;
		margin: -0.2rem 0 0.05rem -0.2rem;
		border-radius: var(--radius-pill);
		background: var(--ink);
		color: var(--paper);
		font-weight: 600;
	}

	/* Colour as a rule beside the words, never as a tile behind them. A tinted
	   fill per chip put 40 competing rectangles on the panel; the rule says the
	   same thing and leaves the paper alone. */
	.chip {
		display: flex;
		align-items: baseline;
		gap: 0.3rem;
		padding-left: 0.5rem;
		border-left: 3px solid var(--item-color);
		font-size: 0.85rem;
		font-weight: 500;
		line-height: 1.25;
		color: var(--ink);
		/* One line per chip: wrapping would make cell heights uneven and the
		   grid unreadable from across a room. */
		white-space: nowrap;
		overflow: hidden;
		text-overflow: ellipsis;
	}

	/* An all-day item reads as a banner rather than an appointment - underlined
	   in its calendar's colour and set in it, which is why every palette entry
	   now has to clear the *text* contrast threshold. */
	.allday {
		border-left: none;
		padding-left: 0;
		padding-bottom: 0.2rem;
		border-bottom: 1.5px solid var(--item-color);
		color: var(--item-color);
		font-weight: 700;
		font-size: 0.8rem;
	}

	/* An event that has already finished, on any day. The strike is drawn in the
	   calendar's own colour: with no grey fill left to lean on, fading the text
	   alone stopped reading as "finished" and started reading as "faint". */
	/* The text treatment is .struck in theme.css; this is the accent rule
	   beside it, which mixes toward the paper's own rule rather than losing
	   alpha - fading it would change its hue as well as its weight. */
	.passed {
		border-left-color: color-mix(in srgb, var(--item-color) 40%, var(--rule));
	}

	.chiptime {
		color: var(--ink-muted);
	}

	.chiptitle {
		overflow: hidden;
		text-overflow: ellipsis;
	}

	.more {
		font-size: 0.75rem;
		font-style: italic;
		color: var(--ink-ghost);
		padding-left: 0.5rem;
	}

	/* Portrait keeps all seven columns - a month grid is seven columns by
	   definition - but they are ~140px wide, so the cells give back the height
	   they were using to spread out and the chips lose their time prefix
	   rather than ellipsing every title away. */
	@media (orientation: portrait) {
		.cell {
			min-height: 5rem;
			padding: 0.35rem 0.4rem 0.45rem;
		}

		.daynum {
			font-size: 1.15rem;
		}

		.today .daynum {
			width: 1.85rem;
			height: 1.85rem;
		}

		.chip {
			font-size: 0.75rem;
			padding-left: 0.4rem;
		}

		.chiptime {
			display: none;
		}
	}
</style>
