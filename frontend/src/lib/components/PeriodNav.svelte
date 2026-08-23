<script lang="ts">
	let {
		title,
		onPrev,
		onNext,
		onToday
	}: {
		title: string;
		onPrev: () => void;
		onNext: () => void;
		onToday: () => void;
	} = $props();
</script>

<div class="nav">
	<h2>{title}</h2>
	<!-- Drawn chevrons rather than the ‹ and › characters this used to print.
	     They are ordinary Latin-1 punctuation and would very likely have
	     survived, but the app's rule is that no glyph on the wall depends on a
	     font being present - see TransportControls.svelte for the reasoning.
	     Drawing them also means the stroke weight matches the back arrow in the
	     music browser, which the character never could. -->
	<button type="button" class="arrow" aria-label="Previous" onclick={onPrev}>
		<svg viewBox="0 0 24 24" aria-hidden="true">
			<path
				d="M15 5l-7 7 7 7"
				fill="none"
				stroke="currentColor"
				stroke-width="2"
				stroke-linecap="round"
				stroke-linejoin="round"
			/>
		</svg>
	</button>
	<button type="button" class="today" onclick={onToday}>Today</button>
	<button type="button" class="arrow" aria-label="Next" onclick={onNext}>
		<svg viewBox="0 0 24 24" aria-hidden="true">
			<path
				d="M9 5l7 7-7 7"
				fill="none"
				stroke="currentColor"
				stroke-width="2"
				stroke-linecap="round"
				stroke-linejoin="round"
			/>
		</svg>
	</button>
</div>

<style>
	.nav {
		display: flex;
		align-items: center;
		gap: 0.6rem;
		margin: 1.35rem 0 0.75rem;
	}

	h2 {
		flex: 1;
		margin: 0;
		font-family: var(--font-display);
		font-size: 1.9rem;
		font-weight: 500;
		letter-spacing: -0.01em;
	}

	/* Outlined rather than filled. These are the only three controls on the
	   panel that are pressed while looking at the calendar rather than at them,
	   so they have to be findable - but a grey capsule each put three more
	   filled shapes next to a month heading. */
	button {
		display: grid;
		place-items: center;
		min-height: var(--tap);
		min-width: var(--tap);
		border: 1px solid var(--rule-strong);
		border-radius: var(--radius-pill);
		background: transparent;
		color: var(--ink-soft);
		font: inherit;
		cursor: pointer;
		touch-action: manipulation;
		-webkit-tap-highlight-color: transparent;
	}

	.arrow svg {
		width: 20px;
		height: 20px;
	}

	.today {
		padding: 0 1.25rem;
		font-size: 1rem;
	}

	button:active {
		transform: scale(0.97);
	}

	button:focus-visible {
		outline: 2px solid var(--ink);
		outline-offset: 2px;
	}
</style>
