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
		gap: 0 1.25rem;
		list-style: none;
		/* No margin of its own: it sits in a centre-aligned row beside the view
		   switcher, and a top margin there offsets the chips below the
		   switcher's centre line. Spacing is the row's to give. */
		margin: 0;
		padding: 0;
	}

	/* No pill behind it. The swatch already says which calendar this is, and a
	   grey capsule around every one of them was three more filled rectangles on
	   a panel this direction is trying to keep to paper and rules. The tap
	   target is unchanged - it is the height that makes a target reliable, not
	   the fill that shows where it is. */
	.toggle {
		display: flex;
		align-items: center;
		gap: 0.55rem;
		min-height: var(--tap);
		padding: 0 0.15rem;
		border: none;
		background: transparent;
		color: var(--ink);
		font: inherit;
		font-size: 1.0625rem;
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
		outline: 2px solid var(--ink);
		outline-offset: 2px;
	}

	.is-hidden .name {
		color: var(--ink-ghost);
	}

	.swatch {
		width: 1.1rem;
		height: 1.1rem;
		border: 2px solid;
		border-radius: var(--radius-sm);
		display: grid;
		place-items: center;
		flex-shrink: 0;
		box-sizing: border-box;
	}

	/* The paper, not white: the tick is punched out of the swatch, so it has to
	   be the colour the panel would show through. Every palette entry clears
	   4.5:1 against it by construction, which is the same property that lets an
	   all-day event be set in its calendar's colour. */
	.check {
		width: 0.7rem;
		height: 0.7rem;
		color: var(--paper);
	}

	.name {
		white-space: nowrap;
	}
</style>
