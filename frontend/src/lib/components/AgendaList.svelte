<script lang="ts">
	import type { AgendaItem } from '$lib/api';
	import { dateKey, formatDayHeading, formatTime } from '$lib/format';

	let { items }: { items: AgendaItem[] } = $props();

	type Group = { key: string; heading: string; items: AgendaItem[] };

	function todayKey(): string {
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
				heading: formatDayHeading(key),
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
						<li>
							<span
								class="dot"
								style:background-color={item.member?.color ?? '#888'}
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
		padding: 0.6rem 0;
		border-bottom: 1px solid rgba(128, 128, 128, 0.2);
	}

	.dot {
		width: 0.75rem;
		height: 0.75rem;
		border-radius: 50%;
		flex-shrink: 0;
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
