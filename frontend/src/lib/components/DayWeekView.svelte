<script lang="ts">
	import type { CalendarDay } from '$lib/api';
	import { formatTime, hasPassed } from '$lib/format';

	let {
		days,
		today = null,
		now = null
	}: { days: CalendarDay[]; today?: string | null; now?: string | null } = $props();

	// One column per day, taken from the data rather than from the view name,
	// so day (1), the lookaheads (3 and 5) and week (7) all render through the
	// same path. Floored at 1: repeat(0, ...) is invalid CSS and would drop
	// the whole grid.
	let columns = $derived(Math.max(1, days.length));

	// Four or more columns stop being legible once the panel is portrait or
	// the window is narrow, so those stack. Three still fit across 1080px -
	// and a 3-day lookahead side by side is the whole point of the view.
	let stacksWhenNarrow = $derived(columns > 3);
</script>

<!-- Day columns rather than a scrolling hour grid. From across a kitchen the
     question is "what is on today", not "where exactly does 2pm sit", and this
     keeps one rendering path for day, the lookaheads, week, and month. -->
<div class="columns" class:stacks={stacksWhenNarrow} style:--columns={columns}>
	{#each days as day (day.date)}
		<section class:today={day.is_today} class:past={today !== null && day.date < today}>
			<h3>
				<span class="weekday">{day.weekday_short}</span>
				<span class="daynum">{day.day_of_month}</span>
				{#if day.is_today}
					<span class="flag">Today</span>
				{/if}
			</h3>
			{#if day.items.length === 0}
				<p class="empty">Nothing scheduled.</p>
			{:else}
				<ul>
					{#each day.items as item (item.id)}
						<li
							style:--item-color={item.calendar?.color ?? 'var(--accent-fallback)'}
							class:passed={hasPassed(day.date, item, today, now)}
						>
							<span class="bar" aria-hidden="true"></span>
							<span class="time">
								{#if item.all_day}
									All day
								{:else if item.continues_before}
									<!-- Started yesterday: a start time here would be a lie. -->
									cont.
								{:else}
									{formatTime(item.starts_at)}
								{/if}
							</span>
							<span class="title">{item.title}</span>
							{#if item.location}
								<span class="location">{item.location}</span>
							{/if}
						</li>
					{/each}
				</ul>
			{/if}
		</section>
	{/each}
</div>

<style>
	/* Ruled columns rather than tinted cards. Side by side the rule between two
	   days is a vertical one; stacked, it turns horizontal - same idea either
	   way, and no fill in sight. */
	.columns {
		display: grid;
		grid-template-columns: repeat(var(--columns), minmax(0, 1fr));
		border-top: 1px solid var(--rule);
	}

	section {
		padding: 0.75rem 0.85rem 1rem;
		border-right: 1px solid var(--rule);
		min-height: 12rem;
	}

	section:last-child {
		border-right: none;
	}

	/* Today is the one day on white paper, with an ink edge and the same disc
	   the month grid sets its date in. It used to carry three marks - outline,
	   lifted background, and the word - which on a ruled table is two too many. */
	.today {
		background: var(--paper-raised);
		box-shadow: inset 3px 0 0 var(--ink);
	}

	/* A day that is over dims its heading only. The events inside carry their
	   own finished treatment, and fading the column as well would put two
	   reductions on top of each other. */
	.past h3 .weekday,
	.past h3 .daynum {
		color: var(--ink-trace);
	}

	h3 {
		display: flex;
		align-items: center;
		gap: 0.6rem;
		margin: 0 0 0.7rem;
		font-size: 1rem;
		font-weight: 400;
	}

	.weekday {
		font-size: 0.75rem;
		font-weight: 600;
		letter-spacing: 0.18em;
		text-transform: uppercase;
		color: var(--ink-muted);
	}

	.daynum {
		font-family: var(--font-display);
		font-size: 1.6rem;
		font-weight: 500;
		line-height: 1;
		color: var(--ink);
	}

	.today .daynum {
		display: grid;
		place-items: center;
		width: 2.75rem;
		height: 2.75rem;
		border-radius: var(--radius-pill);
		background: var(--ink);
		color: var(--paper);
		font-weight: 600;
	}

	.flag {
		margin-left: auto;
		font-size: 0.75rem;
		font-weight: 600;
		letter-spacing: 0.18em;
		text-transform: uppercase;
		color: var(--ink);
	}

	.empty {
		margin: 0;
		padding-left: 0.8rem;
		font-size: 0.9rem;
		font-style: italic;
		color: var(--ink-trace);
	}

	ul {
		list-style: none;
		margin: 0;
		padding: 0;
		display: flex;
		flex-direction: column;
		gap: 0.5rem;
	}

	li {
		position: relative;
		display: flex;
		flex-direction: column;
		padding: 0.15rem 0.2rem 0.15rem 0.8rem;
	}

	/* Full-height accent rule in the owning calendar's colour - legible from
	   across a room in a way a small dot is not, and now the only thing on the
	   row that is coloured at all. */
	.bar {
		position: absolute;
		left: 0;
		top: 0.1rem;
		bottom: 0.1rem;
		width: 3px;
		background: var(--item-color);
	}

	/* An event that has already finished. Kept on screen rather than hidden -
	   "what happened today" is half of what a family reads off the wall in the
	   evening - but pushed behind everything still to come, with the strike
	   drawn in the calendar's own colour so it reads as finished rather than
	   merely faint.

	   Applied the same way on every day, not just today: an appointment that
	   happened last Tuesday is no less finished than one that ended an hour ago,
	   and treating the two differently makes the strike look like it means
	   something else. */
	.passed .title {
		color: var(--ink-ghost);
		font-weight: 400;
		text-decoration: line-through;
		text-decoration-color: var(--item-color);
		text-decoration-thickness: 1px;
	}

	.passed .time,
	.passed .location {
		color: var(--ink-trace);
	}

	.passed .bar {
		background: color-mix(in srgb, var(--item-color) 40%, var(--rule));
	}

	.time {
		font-size: 0.8rem;
		color: var(--ink-muted);
	}

	.title {
		font-size: 1rem;
		font-weight: 500;
		color: var(--ink);
	}

	.location {
		font-size: 0.8rem;
		color: var(--ink-muted);
	}

	/* One column per day stops working long before a phone-sized screen, and it
	   never works in portrait: the wall panel is 1080px wide that way up, which
	   is wider than this breakpoint, so seven columns would survive at ~150px
	   each. Orientation is checked as well as width for that reason.

	   Only the wide views collapse here. A 3-day lookahead gets ~350px per
	   column at that width, which is comfortable, and stacking it would throw
	   away the side-by-side comparison the view exists for.

	   Stacked, the rules turn horizontal and today's ink edge is bled into the
	   page's own margin so the white band reads as a strip across the panel
	   rather than a box floating in it. */
	@media (max-width: 60rem), (orientation: portrait) {
		.columns.stacks {
			grid-template-columns: minmax(0, 1fr);
			border-top: none;
		}

		.columns.stacks section {
			min-height: 0;
			border-right: none;
			border-bottom: 1px solid var(--rule-soft);
		}

		.columns.stacks section:last-child {
			border-bottom: none;
		}

		.columns.stacks .today {
			margin: 0 -0.85rem;
			padding-left: 1.7rem;
			padding-right: 1.7rem;
		}

		/* Stacked, a day gets the full width of the panel, so an event reads as
		   one line - time, title, where - the way the agenda below it does.
		   Narrow columns cannot do that and keep stacking instead. */
		.columns.stacks li {
			flex-direction: row;
			align-items: baseline;
			gap: 1rem;
			padding-top: 0.3rem;
			padding-bottom: 0.3rem;
		}

		.columns.stacks .time {
			min-width: 8ch;
			font-size: 0.9375rem;
		}

		.columns.stacks .title {
			font-size: 1.1875rem;
		}

		.columns.stacks .location {
			margin-left: auto;
			font-size: 0.9375rem;
		}
	}

	/* Phone width, where even three columns are unreadable. */
	@media (max-width: 40rem) {
		.columns {
			grid-template-columns: minmax(0, 1fr);
			border-top: none;
		}

		section {
			min-height: 0;
			border-right: none;
			border-bottom: 1px solid var(--rule-soft);
		}
	}
</style>
