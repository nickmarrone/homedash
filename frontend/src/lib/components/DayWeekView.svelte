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
							style:--item-color={item.calendar?.color ?? '#888'}
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
	.columns {
		display: grid;
		grid-template-columns: repeat(var(--columns), minmax(0, 1fr));
		gap: 0.5rem;
	}

	section {
		padding: 0.5rem;
		border-radius: 8px;
		background: rgba(128, 128, 128, 0.08);
		min-height: 12rem;
	}

	/* Today has to win at a glance from across the room, so it is marked three
	   ways rather than one: a heavier border, a lifted background, and the word
	   itself. The outline is inset so a thicker line cannot spill into the
	   2px column gap and look like it belongs to the neighbouring day. */
	.today {
		outline: 3px solid currentColor;
		outline-offset: -2px;
		background: rgba(128, 128, 128, 0.2);
	}

	/* A day that is over dims its heading only. The events inside carry their
	   own finished treatment, and fading the column as well would multiply the
	   two opacities into something barely legible. */
	.past h3 {
		opacity: 0.55;
	}

	h3 {
		display: flex;
		align-items: baseline;
		gap: 0.4rem;
		margin: 0 0 0.5rem;
		font-size: 1rem;
	}

	.weekday {
		text-transform: uppercase;
		letter-spacing: 0.05em;
		opacity: 0.6;
		font-size: 0.85rem;
	}

	.daynum {
		font-size: 1.2rem;
		font-variant-numeric: tabular-nums;
	}

	.today .daynum {
		font-weight: 700;
	}

	.flag {
		margin-left: auto;
		padding: 0.05rem 0.4rem;
		border-radius: 999px;
		background: rgba(128, 128, 128, 0.45);
		font-size: 0.7rem;
		font-weight: 700;
		text-transform: uppercase;
		letter-spacing: 0.05em;
	}

	/* An event that has already finished. Kept on screen rather than hidden -
	   "what happened today" is half of what a family reads off the wall in the
	   evening - but pushed behind everything still to come. The colour bar
	   fades with it, or a finished event would still carry the loudest mark in
	   the column.

	   Applied the same way on every day, not just today: an appointment that
	   happened last Tuesday is no less finished than one that ended an hour
	   ago, and treating the two differently makes the strike-through look like
	   it means something else. */
	.passed {
		opacity: 0.45;
	}

	.passed .title {
		font-weight: 500;
		text-decoration: line-through;
		text-decoration-thickness: 1px;
	}

	.empty {
		margin: 0;
		opacity: 0.5;
		font-size: 0.9rem;
	}

	ul {
		list-style: none;
		margin: 0;
		padding: 0;
		display: flex;
		flex-direction: column;
		gap: 0.4rem;
	}

	li {
		position: relative;
		display: flex;
		flex-direction: column;
		padding: 0.3rem 0.3rem 0.3rem 0.6rem;
		border-radius: 4px;
		background: color-mix(in srgb, var(--item-color) 14%, transparent);
	}

	/* Full-height accent bar in the owning calendar's color - legible from
	   across a room in a way a small dot is not. */
	.bar {
		position: absolute;
		left: 0;
		top: 0.2rem;
		bottom: 0.2rem;
		width: 3px;
		border-radius: 2px;
		background: var(--item-color);
	}

	.time {
		font-size: 0.8rem;
		opacity: 0.7;
		font-variant-numeric: tabular-nums;
	}

	.title {
		font-weight: 600;
		font-size: 0.95rem;
	}

	.location {
		font-size: 0.8rem;
		opacity: 0.6;
	}

	/* One column per day stops working long before a phone-sized screen, and
	   it never works in portrait: the wall panel is 1080px wide that way up,
	   which is wider than this breakpoint, so seven columns would survive at
	   ~150px each. Orientation is checked as well as width for that reason.

	   Only the wide views collapse here. A 3-day lookahead gets ~350px per
	   column at that width, which is comfortable, and stacking it would throw
	   away the side-by-side comparison the view exists for. */
	@media (max-width: 60rem), (orientation: portrait) {
		.columns.stacks {
			grid-template-columns: minmax(0, 1fr);
		}

		/* Stacked days only need to be as tall as their contents; the fixed
		   minimum exists to keep side-by-side columns even. */
		.columns.stacks section {
			min-height: 0;
		}
	}

	/* Phone width, where even three columns are unreadable. */
	@media (max-width: 40rem) {
		.columns {
			grid-template-columns: minmax(0, 1fr);
		}

		section {
			min-height: 0;
		}
	}
</style>
