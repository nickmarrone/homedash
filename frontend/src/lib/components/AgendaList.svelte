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
			const key = dateKey(item.starts_at);
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
			<h2>{group.heading}</h2>
			{#if group.items.length === 0}
				<p class="empty">Nothing scheduled.</p>
			{:else}
				<ul>
					{#each group.items as item (item.id)}
						<li class:passed={hasPassed(group.key, item, today, now)}>
							<span
								class="bar"
								style:background-color={item.calendar?.color ?? '#888'}
								aria-hidden="true"
							></span>
							<span class="time">{item.all_day ? 'All day' : formatTime(item.starts_at)}</span>
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
	.agenda {
		font-size: 1.25rem;
	}

	.empty {
		opacity: 0.6;
	}

	h2 {
		font-size: 1.1rem;
		text-transform: uppercase;
		letter-spacing: 0.05em;
		opacity: 0.7;
		margin: 1.5rem 0 0.5rem;
	}

	ul {
		list-style: none;
		margin: 0;
		padding: 0;
	}

	li {
		display: flex;
		align-items: baseline;
		gap: 0.75rem;
		padding: 0.6rem 0 0.6rem 0.75rem;
		border-bottom: 1px solid rgba(128, 128, 128, 0.2);
		position: relative;
	}

	/* An event that has already finished, marked the same way the grid views
	   mark it. In portrait the agenda sits directly under the calendar, so the
	   same appointment is on screen twice - showing it struck through in one
	   place and at full strength in the other reads as a bug. */
	.passed {
		opacity: 0.45;
	}

	.passed .title {
		font-weight: 500;
		text-decoration: line-through;
		text-decoration-thickness: 1px;
	}

	/* Full-height accent bar in the owning calendar's color - legible from
	   across a room in a way a small dot is not. */
	.bar {
		position: absolute;
		left: 0;
		top: 0.35rem;
		bottom: 0.35rem;
		width: 4px;
		border-radius: 2px;
	}

	.time {
		min-width: 6ch;
		opacity: 0.7;
		font-variant-numeric: tabular-nums;
	}

	.title {
		flex: 1;
		font-weight: 600;
	}

	.location {
		opacity: 0.6;
		font-size: 0.9em;
	}
</style>
