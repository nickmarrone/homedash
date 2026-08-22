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
		gap: 0.25rem;
		padding: 0.25rem;
		border-radius: 999px;
		background: rgba(128, 128, 128, 0.14);
	}

	button {
		min-height: 48px;
		padding: 0 1rem;
		border: none;
		border-radius: 999px;
		background: transparent;
		color: inherit;
		font: inherit;
		font-size: 1rem;
		cursor: pointer;
		touch-action: manipulation;
		-webkit-tap-highlight-color: transparent;
	}

	.selected {
		background: Canvas;
		font-weight: 600;
		box-shadow: 0 1px 3px rgba(0, 0, 0, 0.2);
	}

	/* Still selectable: a speaker often reports unavailable because it is
	   asleep, and choosing it is how you find out whether it wakes up. */
	.unavailable {
		opacity: 0.45;
	}

	button:active {
		transform: scale(0.97);
	}

	button:focus-visible {
		outline: 2px solid currentColor;
		outline-offset: 2px;
	}
</style>
