<script lang="ts">
	import type { AgendaItem } from '$lib/api';
	import { dateKey, formatDayHeading, formatTime, hasPassed } from '$lib/format';

	let {
		items,
		today = null,
		now = null
	}: { items: AgendaItem[]; today?: string | null; now?: string | null } = $props();

	type Group = { key: string; heading: string; items: AgendaItem[] };

	// The server's date when it is known; the browser's own clock only as a
	// fallback before the first response lands. A panel whose OS timezone
	// differs from the configured home timezone would otherwise inject an
	// empty group for the wrong day and label it "Today".
	function todayKey(): string {
		if (today) return today;
		const now = new Date();
		const month = String(now.getMonth() + 1).padStart(2, '0');
		const day = String(now.getDate()).padStart(2, '0');
		return `${now.getFullYear()}-${month}-${day}`;
	}

	let groups = $derived.by((): Group[] => {
		const byDay = new Map<string, AgendaItem[]>();
		for (const item of items) {
			// The server's own answer to "which heading does this belong
			// under", which is not always the day it starts: an event already
			// in progress began on a date this list no longer shows, and would
			// otherwise open a group above today for a day that is gone.
			// Falls back to the start date so a response from an older backend
			// still renders.
			const key = item.agenda_date ?? dateKey(item.starts_at);
			const list = byDay.get(key) ?? [];
			list.push(item);
			byDay.set(key, list);
		}
		// Always include today, even with no events, so the panel reads as a
		// live calendar rather than going blank when nothing is scheduled.
		const key = todayKey();
		if (!byDay.has(key)) byDay.set(key, []);
		return [...byDay.entries()]
			.sort(([a], [b]) => a.localeCompare(b))
			.map(([key, dayItems]) => ({
				key,
				heading: formatDayHeading(key, today),
				items: dayItems
			}));
	});
</script>

<div class="agenda">
	{#each groups as group (group.key)}
		<section>
			<h2 class="caps">{group.heading}</h2>
			{#if group.items.length === 0}
				<p class="empty">Nothing scheduled.</p>
			{:else}
				<ul>
					{#each group.items as item (item.id)}
						{@const passed = hasPassed(group.key, item, today, now)}
						<li
							style:--item-color={item.calendar?.color ?? 'var(--accent-fallback)'}
							class:passed
						>
							<span class="bar" aria-hidden="true"></span>
							<span class="time">{item.all_day ? 'All day' : formatTime(item.starts_at)}</span>
							<!-- .struck is the shared "this is over" treatment; the time
							     beside it stays legible, which is why it goes here and not
							     on the row. -->
							<span class="title" class:struck={passed}>{item.title}</span>
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
	.empty {
		margin: 0.5rem 0 0;
		font-style: italic;
		color: var(--ink-trace);
	}

	/* .caps in theme.css supplies the letterspaced label treatment; only the
	   spacing around it belongs to this component. */
	h2 {
		margin: 1.5rem 0 0.1rem;
	}

	/* Enough air to sit under whatever precedes the list - the controls rule in
	   agenda view, the "Coming up" label in portrait - without opening the gap a
	   between-groups heading needs. */
	section:first-child h2 {
		margin-top: 0.9rem;
	}

	ul {
		list-style: none;
		margin: 0;
		padding: 0;
	}

	li {
		display: flex;
		align-items: baseline;
		gap: 1rem;
		padding: 0.7rem 0 0.7rem 0.85rem;
		border-bottom: 1px solid var(--rule-soft);
		position: relative;
	}

	/* Full-height accent rule in the owning calendar's colour - legible from
	   across a room in a way a small dot is not. */
	.bar {
		position: absolute;
		left: 0;
		top: 0.5rem;
		bottom: 0.5rem;
		width: 3px;
		background: var(--item-color);
	}

	/* An event that has already finished, marked the same way the grid views
	   mark it. In portrait the agenda sits directly under the calendar, so the
	   same appointment is on screen twice - showing it struck through in one
	   place and at full strength in the other reads as a bug. */
	.passed .time,
	.passed .location {
		color: var(--ink-trace);
	}

	.passed .bar {
		background: color-mix(in srgb, var(--item-color) 40%, var(--rule));
	}

	.time {
		min-width: 8ch;
		font-size: 1rem;
		font-weight: 500;
		color: var(--ink-muted);
	}

	/* The display face, because this is the one list on the panel that is read
	   as prose rather than scanned as a table. */
	.title {
		flex: 1;
		font-family: var(--font-display);
		font-size: 1.4rem;
		font-weight: 500;
		color: var(--ink);
	}

	.location {
		font-size: 0.9375rem;
		color: var(--ink-muted);
	}
</style>
