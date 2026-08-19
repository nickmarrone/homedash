<script lang="ts">
	import type { CalendarDay } from '$lib/api';
	import { formatTime } from '$lib/format';

	let { days }: { days: CalendarDay[] } = $props();
</script>

<!-- Day columns rather than a scrolling hour grid. From across a kitchen the
     question is "what is on today", not "where exactly does 2pm sit", and this
     keeps one rendering path for day, week, and month. -->
<div class="columns" class:single={days.length === 1}>
	{#each days as day (day.date)}
		<section class:today={day.is_today}>
			<h3>
				<span class="weekday">{day.weekday_short}</span>
				<span class="daynum">{day.day_of_month}</span>
			</h3>
			{#if day.items.length === 0}
				<p class="empty">Nothing scheduled.</p>
			{:else}
				<ul>
					{#each day.items as item (item.id)}
						<li style:--item-color={item.calendar?.color ?? '#888'}>
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
		grid-template-columns: repeat(7, minmax(0, 1fr));
		gap: 0.5rem;
	}

	.single {
		grid-template-columns: minmax(0, 1fr);
	}

	section {
		padding: 0.5rem;
		border-radius: 8px;
		background: rgba(128, 128, 128, 0.08);
		min-height: 12rem;
	}

	.today {
		outline: 2px solid currentColor;
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
	   ~150px each. Orientation is checked as well as width for that reason. */
	@media (max-width: 60rem), (orientation: portrait) {
		.columns {
			grid-template-columns: minmax(0, 1fr);
		}

		/* Stacked days only need to be as tall as their contents; the fixed
		   minimum exists to keep side-by-side columns even. */
		section {
			min-height: 0;
		}
	}
</style>
