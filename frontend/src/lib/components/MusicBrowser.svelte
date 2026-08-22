<script lang="ts">
	import {
		artUrl,
		fetchLibrary,
		type LibraryAlbum,
		type LibraryArtist,
		type LibraryTrack
	} from '$lib/api';

	let {
		onPlayAlbum,
		onPlayTracks
	}: {
		onPlayAlbum: (albumId: string) => void;
		/** Tapping a track plays from there to the end of the album, which is
		 * what it means in every other music player. The whole tail is sent
		 * rather than one track, because the queue lives on the server. */
		onPlayTracks: (trackIds: string[], albumId: string) => void;
	} = $props();

	// One level at a time, not a tree. A whole music library is far too much to
	// hand a panel in one response, and the screen only ever shows one level.
	type Level =
		| { kind: 'artists' }
		| { kind: 'albums'; artist: LibraryArtist }
		| { kind: 'tracks'; album: LibraryAlbum };

	let level = $state<Level>({ kind: 'artists' });
	// A stack rather than a rule for "what is one level up". Tracks can be
	// reached from an artist's albums, so deriving the parent would send the
	// back button to the full artist list and lose the artist you were in.
	let history = $state<Level[]>([]);

	let items = $state<(LibraryArtist | LibraryAlbum | LibraryTrack)[]>([]);
	let loading = $state(true);
	let error = $state<string | null>(null);

	function descend(next: Level) {
		history = [...history, level];
		level = next;
	}

	// Re-runs whenever the level changes, which is the whole navigation model.
	$effect(() => {
		const current = level;
		loading = true;
		error = null;
		const parent =
			current.kind === 'albums'
				? current.artist.id
				: current.kind === 'tracks'
					? current.album.id
					: undefined;
		fetchLibrary(current.kind, parent)
			.then((next) => {
				// Ignore a response that arrived after somebody moved on, or a
				// slow artist list would overwrite the album list they opened.
				if (level !== current) return;
				items = next;
			})
			.catch(() => {
				if (level !== current) return;
				error = 'Could not reach the music library.';
				items = [];
			})
			.finally(() => {
				if (level === current) loading = false;
			});
	});

	const heading = $derived(
		level.kind === 'artists'
			? 'Artists'
			: level.kind === 'albums'
				? level.artist.name
				: level.album.name
	);

	function back() {
		const previous = history.at(-1);
		history = history.slice(0, -1);
		level = previous ?? { kind: 'artists' };
	}

	/** Play from the tapped track to the end of the album. */
	function playFrom(index: number) {
		if (level.kind !== 'tracks') return;
		const ids = items.slice(index).map((item) => (item as LibraryTrack).id);
		onPlayTracks(ids, level.album.id);
	}

	function duration(ms: number | null): string {
		if (ms == null || ms <= 0) return '';
		const total = Math.round(ms / 1000);
		return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, '0')}`;
	}
</script>

<div class="browser">
	<header>
		{#if history.length > 0}
			<button class="back" type="button" onclick={back} aria-label="Back">
				<svg viewBox="0 0 24 24" aria-hidden="true">
					<path
						d="M15 5l-7 7 7 7"
						fill="none"
						stroke="currentColor"
						stroke-width="2.2"
						stroke-linecap="round"
						stroke-linejoin="round"
					/>
				</svg>
			</button>
		{/if}
		<h2>{heading}</h2>
		{#if level.kind === 'tracks'}
			{@const albumId = level.album.id}
			<button class="play-all" type="button" onclick={() => onPlayAlbum(albumId)}>
				Play album
			</button>
		{/if}
	</header>

	{#if error}
		<p class="note">{error}</p>
	{:else if loading}
		<p class="note">Loading…</p>
	{:else if items.length === 0}
		<p class="note">Nothing here.</p>
	{:else if level.kind === 'artists'}
		<ul class="rows">
			{#each items as artist (artist.id)}
				<li>
					<button
						type="button"
						onclick={() => descend({ kind: 'albums', artist: artist as LibraryArtist })}
					>
						{(artist as LibraryArtist).name}
					</button>
				</li>
			{/each}
		</ul>
	{:else if level.kind === 'albums'}
		<ul class="grid">
			{#each items as album (album.id)}
				{@const entry = album as LibraryAlbum}
				<li>
					<button
						type="button"
						onclick={() => descend({ kind: 'tracks', album: entry })}
					>
						<img src={artUrl(entry.id, 300)} alt="" loading="lazy" />
						<span class="name">{entry.name}</span>
						{#if entry.year}<span class="sub">{entry.year}</span>{/if}
					</button>
				</li>
			{/each}
		</ul>
	{:else}
		<ul class="rows">
			{#each items as track, index (track.id)}
				{@const entry = track as LibraryTrack}
				<li>
					<button type="button" onclick={() => playFrom(index)}>
						<span class="index">{entry.track_number ?? ''}</span>
						<span class="name">{entry.title}</span>
						<span class="sub">{duration(entry.duration_ms)}</span>
					</button>
				</li>
			{/each}
		</ul>
	{/if}
</div>

<style>
	.browser {
		display: flex;
		flex-direction: column;
		min-height: 0;
		height: 100%;
	}

	header {
		display: flex;
		align-items: center;
		gap: 0.75rem;
		padding-bottom: 0.75rem;
	}

	h2 {
		margin: 0;
		font-size: 1.3rem;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	.back {
		display: grid;
		place-items: center;
		min-width: 48px;
		min-height: 48px;
		flex: none;
		border: none;
		border-radius: 999px;
		background: rgba(128, 128, 128, 0.14);
		color: inherit;
		cursor: pointer;
	}

	.back svg {
		width: 24px;
		height: 24px;
	}

	.play-all {
		margin-left: auto;
		flex: none;
		min-height: 48px;
		padding: 0 1.1rem;
		border: none;
		border-radius: 999px;
		background: rgba(128, 128, 128, 0.18);
		color: inherit;
		font: inherit;
		font-weight: 600;
		cursor: pointer;
	}

	ul {
		list-style: none;
		margin: 0;
		padding: 0;
		overflow-y: auto;
		flex: 1;
		min-height: 0;
		/* Momentum scrolling without the rubber-band navigation gestures that
		   would otherwise be an escape route out of the kiosk. */
		overscroll-behavior: contain;
	}

	/* Capped and centred. Left to fill a 1920px panel, a track row puts its
	   duration most of a metre from its title and the two stop reading as one
	   line. The album grid has no such problem - it fills by tiling. */
	.rows {
		max-width: 900px;
		margin: 0 auto;
		width: 100%;
	}

	.rows li button {
		display: flex;
		align-items: center;
		gap: 0.9rem;
		width: 100%;
		min-height: 48px;
		padding: 0.5rem 0.75rem;
		border: none;
		border-radius: 10px;
		background: transparent;
		color: inherit;
		font: inherit;
		text-align: left;
		cursor: pointer;
	}

	.rows li + li button {
		border-top: 1px solid rgba(128, 128, 128, 0.18);
		border-radius: 0;
	}

	.grid {
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
		gap: 1rem;
	}

	.grid li button {
		display: flex;
		flex-direction: column;
		gap: 0.35rem;
		width: 100%;
		padding: 0;
		border: none;
		background: transparent;
		color: inherit;
		font: inherit;
		text-align: left;
		cursor: pointer;
	}

	.grid img {
		width: 100%;
		aspect-ratio: 1;
		object-fit: cover;
		border-radius: 10px;
		background: rgba(128, 128, 128, 0.2);
	}

	.index {
		min-width: 2ch;
		text-align: right;
		opacity: 0.5;
		font-variant-numeric: tabular-nums;
	}

	.name {
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
		flex: 1;
	}

	.sub {
		opacity: 0.6;
		font-size: 0.85rem;
		flex: none;
	}

	.note {
		opacity: 0.6;
		padding: 1rem 0.75rem;
	}

	button {
		touch-action: manipulation;
		-webkit-tap-highlight-color: transparent;
	}

	button:active {
		transform: scale(0.98);
	}

	button:focus-visible {
		outline: 2px solid currentColor;
		outline-offset: 2px;
	}
</style>
