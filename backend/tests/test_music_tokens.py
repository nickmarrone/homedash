"""Stream tokens, and the 255-character limit they exist to stay under.

The length test is the point of this file. HEOS silently declines a URL longer
than 255 characters - no error, no log, the speaker simply does not play - so
this is a limit that has to be caught here or not at all.
"""

import pytest

from app.music.tokens import MAX_URL_LENGTH, TokenStore, UrlTooLong, stream_url


def test_a_minted_token_resolves_to_its_track():
    store = TokenStore()
    token = store.mint("track-123")
    assert store.resolve(token) == "track-123"


def test_an_unknown_token_resolves_to_nothing():
    assert TokenStore().resolve("nope") is None


def test_a_token_stops_working_once_it_expires():
    clock = [1000.0]
    store = TokenStore(ttl_seconds=60, now=lambda: clock[0])
    token = store.mint("t1")
    clock[0] += 59
    assert store.resolve(token) == "t1"
    clock[0] += 2
    assert store.resolve(token) is None


def test_the_default_lifetime_outlives_any_plausible_track():
    """A token that expired mid-song would cut playback off partway through,
    which would look like a corrupt file rather than a timeout."""
    clock = [0.0]
    store = TokenStore(now=lambda: clock[0])
    token = store.mint("t1")
    clock[0] += 60 * 60  # an hour: far longer than any track
    assert store.resolve(token) == "t1"


def test_two_tokens_for_the_same_track_are_different():
    """Minted per play, so a stale playlist cannot resurrect a token that was
    supposed to have aged out."""
    store = TokenStore()
    assert store.mint("t1") != store.mint("t1")


def test_the_store_does_not_grow_without_bound():
    """A household leaving an album on repeat for a week mints a token per
    track per play, forever."""
    clock = [0.0]
    store = TokenStore(ttl_seconds=10_000, now=lambda: clock[0])
    for i in range(2500):
        store.mint(f"t{i}")
    assert len(store) <= 2000


def test_expired_tokens_are_dropped_as_new_ones_are_minted():
    clock = [0.0]
    store = TokenStore(ttl_seconds=10, now=lambda: clock[0])
    for i in range(5):
        store.mint(f"t{i}")
    clock[0] += 11
    store.mint("fresh")
    assert len(store) == 1


def test_a_normal_stream_url_is_comfortably_under_the_heos_limit():
    """The whole design - short tokens, a proxy rather than a Jellyfin URL -
    exists to make this true with room to spare."""
    url = stream_url("http://192.168.1.10:8000", TokenStore().mint("t1"))
    assert len(url) < 80
    assert url.startswith("http://192.168.1.10:8000/api/music/s/")


def test_an_over_long_url_raises_rather_than_being_handed_to_a_speaker():
    """HEOS accepts the command and plays nothing, which is close to
    undiagnosable from the kitchen. Failing here puts it in the log instead."""
    base = "http://" + "a" * 250
    with pytest.raises(UrlTooLong, match="HOMEDASH_PUBLIC_BASE_URL"):
        stream_url(base, "token123")


def test_the_boundary_itself_is_allowed():
    """Exactly at the limit is fine; the check is for what HEOS will not
    fetch, not for one character less than that."""
    token = "t"
    suffix = f"/api/music/s/{token}"
    base = "h" * (MAX_URL_LENGTH - len(suffix))
    assert len(stream_url(base, token)) == MAX_URL_LENGTH


def test_a_trailing_slash_on_the_base_url_does_not_double_up():
    url = stream_url("http://host:8000/", "abc")
    assert "//api" not in url
