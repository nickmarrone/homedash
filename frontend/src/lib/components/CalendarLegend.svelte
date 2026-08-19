<script lang="ts">
	import type { AgendaCalendar } from '$lib/api';

	let {
		calendars,
		hidden,
		onToggle
	}: {
		calendars: AgendaCalendar[];
		hidden: Set<number>;
		onToggle: (id: number) => void;
	} = $props();
</script>

<!-- A legend for a single calendar is noise - there is nothing to tell apart,
     and nothing worth filtering. -->
{#if calendars.length > 1}
	<ul class="legend">
		{#each calendars as calendar (calendar.id)}
			{@const isHidden = hidden.has(calendar.id)}
			<li>
				<!-- The whole chip is the tap target, not just the icon: 48px is the
				     minimum comfortable touch size, and a bare swatch is far below it. -->
				<button
					type="button"
					class="toggle"
					class:is-hidden={isHidden}
					aria-pressed={!isHidden}
					onclick={() => onToggle(calendar.id)}
				>
					<span
						class="swatch"
						style:border-color={calendar.color}
						style:background-color={isHidden ? 'transparent' : calendar.color}
						aria-hidden="true"
					>
						{#if !isHidden}
							<svg viewBox="0 0 16 16" class="check">
								<path
									d="M3.5 8.5l3 3 6-6.5"
									fill="none"
									stroke="currentColor"
									stroke-width="2.5"
									stroke-linecap="round"
									stroke-linejoin="round"
								/>
							</svg>
						{/if}
					</span>
					<span class="name">{calendar.name}</span>
				</button>
			</li>
		{/each}
	</ul>
{/if}

<style>
	.legend {
		display: flex;
		flex-wrap: wrap;
		gap: 0.5rem;
		list-style: none;
		margin: 1rem 0 0;
		padding: 0;
	}

	.toggle {
		display: flex;
		align-items: center;
		gap: 0.6rem;
		/* 48px: the smallest target that stays reliable for a fingertip on a
		   wall panel, where you are often reaching rather than aiming. */
		min-height: 48px;
		padding: 0.4rem 1rem 0.4rem 0.7rem;
		border: none;
		border-radius: 999px;
		background: rgba(128, 128, 128, 0.14);
		color: inherit;
		font: inherit;
		font-size: 1rem;
		cursor: pointer;
		/* Skips the browser's 300ms double-tap-to-zoom wait, so the toggle
		   feels immediate under a finger. */
		touch-action: manipulation;
		-webkit-tap-highlight-color: transparent;
		user-select: none;
	}

	/* Press feedback replaces hover, which does not exist on touch. */
	.toggle:active {
		transform: scale(0.97);
	}

	.toggle:focus-visible {
		outline: 2px solid currentColor;
		outline-offset: 2px;
	}

	.is-hidden .name {
		opacity: 0.5;
	}

	.swatch {
		width: 1.5rem;
		height: 1.5rem;
		border: 2px solid;
		border-radius: 6px;
		display: grid;
		place-items: center;
		flex-shrink: 0;
		box-sizing: border-box;
	}

	/* White reads on every palette entry: all eight clear 3:1 against white by
	   construction, which is what made them safe on a light background too. */
	.check {
		width: 1rem;
		height: 1rem;
		color: #fff;
	}

	.name {
		white-space: nowrap;
	}
</style>
