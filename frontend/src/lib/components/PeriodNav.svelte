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
	<button type="button" class="arrow" aria-label="Previous" onclick={onPrev}>‹</button>
	<h2>{title}</h2>
	<button type="button" class="today" onclick={onToday}>Today</button>
	<button type="button" class="arrow" aria-label="Next" onclick={onNext}>›</button>
</div>

<style>
	.nav {
		display: flex;
		align-items: center;
		gap: 0.5rem;
		margin: 1rem 0 0.5rem;
	}

	h2 {
		flex: 1;
		margin: 0;
		font-size: 1.4rem;
	}

	button {
		min-height: 48px;
		min-width: 48px;
		border: none;
		border-radius: 999px;
		background: rgba(128, 128, 128, 0.14);
		color: inherit;
		font: inherit;
		cursor: pointer;
		touch-action: manipulation;
		-webkit-tap-highlight-color: transparent;
	}

	.arrow {
		/* The glyph is a chevron, so it needs to be large to be aimable. */
		font-size: 1.75rem;
		line-height: 1;
	}

	.today {
		padding: 0 1.25rem;
		font-size: 1rem;
	}

	button:active {
		transform: scale(0.97);
	}

	button:focus-visible {
		outline: 2px solid currentColor;
		outline-offset: 2px;
	}
</style>
