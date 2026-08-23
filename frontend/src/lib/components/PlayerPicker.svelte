<script lang="ts">
	import type { MusicPlayer } from '$lib/api';

	let {
		players,
		selectedId,
		onSelect
	}: {
		players: MusicPlayer[];
		selectedId: number | null;
		onSelect: (id: number) => void;
	} = $props();
</script>

<!-- Renders nothing for a one-speaker household, the same way CalendarLegend
     hides itself when there is only one calendar: a picker with a single
     option is a control that cannot do anything. -->
{#if players.length > 1}
	<nav class="picker" aria-label="Speaker">
		{#each players as player (player.id)}
			<button
				type="button"
				class:selected={player.id === selectedId}
				class:unavailable={!player.available}
				aria-pressed={player.id === selectedId}
				onclick={() => onSelect(player.id)}
			>
				{player.name}
			</button>
		{/each}
	</nav>
{/if}

<style>
	.picker {
		display: flex;
		flex-wrap: wrap;
	}

	/* Underlined, like the view switcher and the music tabs: one selection mark
	   for the whole panel, so a chosen thing always looks chosen the same way. */
	button {
		min-height: var(--tap);
		padding: 0 0.7rem;
		border: none;
		border-bottom: 2px solid transparent;
		background: transparent;
		color: var(--ink-muted);
		font: inherit;
		font-size: 1.0625rem;
		cursor: pointer;
		touch-action: manipulation;
		-webkit-tap-highlight-color: transparent;
	}

	.selected {
		color: var(--ink);
		font-weight: 600;
		border-bottom-color: var(--ink);
	}

	/* Still selectable: a speaker often reports unavailable because it is
	   asleep, and choosing it is how you find out whether it wakes up. */
	.unavailable {
		color: var(--ink-trace);
	}

	button:active {
		transform: scale(0.97);
	}

	button:focus-visible {
		outline: 2px solid var(--ink);
		outline-offset: 2px;
	}
</style>
