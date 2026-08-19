<script lang="ts">
	import type { AgendaItem } from '$lib/api';
	import { dateKey, formatDayHeading, formatTime } from '$lib/format';

	let { items }: { items: AgendaItem[] } = $props();

	type Group = { key: string; heading: string; items: AgendaItem[] };

	let groups = $derived.by((): Group[] => {
		const byDay = new Map<string, AgendaItem[]>();
		for (const item of items) {
			const key = dateKey(item.starts_at);
			const list = byDay.get(key) ?? [];
			list.push(item);
			byDay.set(key, list);
		}
		return [...byDay.entries()].map(([key, dayItems]) => ({
			key,
			heading: formatDayHeading(key),
			items: dayItems
		}));
	});
</script>

<div class="agenda">
	{#if groups.length === 0}
		<p class="empty">Nothing on the calendar.</p>
	{/if}
	{#each groups as group (group.key)}
		<section>
			<h2>{group.heading}</h2>
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
