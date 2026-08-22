"""The Jellyfin adapter: the requests it builds and what it makes of the answers.

HTTP is faked by injecting `get` into the constructor, the same seam the
calendar adapters use. Most of what matters here is in the request rather than
the response - a browse that asks the wrong question returns a perfectly valid
list of the wrong things, and nothing downstream can tell.
"""

import pytest

from app.music.jellyfin import JellyfinError, JellyfinLibrary


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


def build(payload=None, status_code=200, library_id=""):
    """A library that records the calls made through it."""
    calls = []

    def get(url, params, headers):
        calls.append({"url": url, "params": params, "headers": headers})
        return FakeResponse(payload or {"Items": []}, status_code)

    library = JellyfinLibrary(
        "http://jf.local:8096/", "SECRET", library_id=library_id, get=get
    )
    return library, calls


def test_the_api_key_travels_in_a_header_and_never_in_a_url():
    """Jellyfin deprecated query-parameter auth in 10.11 and removes it in
    10.13, and a key in a URL ends up in logs regardless. This is the test that
    fails if anyone reaches for the convenient version."""
    library, calls = build()
    library.artists()

    (call,) = calls
    assert 'Token="SECRET"' in call["headers"]["Authorization"]
    assert "SECRET" not in call["url"]
    assert "SECRET" not in str(call["params"])


def test_artists_are_parsed_into_ids_and_names():
    library, _ = build({"Items": [{"Id": "a1", "Name": "Marconi Union"}]})
    assert [(a.id, a.name) for a in library.artists()] == [("a1", "Marconi Union")]


def test_an_item_with_no_id_is_skipped_rather_than_crashing_the_browse():
    """Jellyfin has returned odd rows for damaged library entries. One of them
    should cost that row, not the whole screen."""
    library, _ = build({"Items": [{"Name": "Nameless"}, {"Id": "a1", "Name": "Real"}]})
    assert [a.id for a in library.artists()] == ["a1"]


def test_albums_are_narrowed_by_album_artist_not_by_appearance():
    """albumArtistIds, not artistIds: the latter also matches albums the artist
    merely appears on, so one guest track on a compilation would file the whole
    compilation under their name."""
    library, calls = build()
    library.albums("a1")
    assert calls[0]["params"]["albumArtistIds"] == "a1"
    assert "artistIds" not in calls[0]["params"]


def test_album_fields_are_parsed_including_a_missing_year():
    library, _ = build(
        {
            "Items": [
                {"Id": "b1", "Name": "Distance", "AlbumArtist": "Marconi Union",
                 "ProductionYear": 2005},
                {"Id": "b2", "Name": "Untitled"},
            ]
        }
    )
    albums = library.albums()
    assert (albums[0].name, albums[0].artist, albums[0].year) == (
        "Distance",
        "Marconi Union",
        2005,
    )
    assert (albums[1].artist, albums[1].year) == (None, None)


def test_tracks_are_scoped_to_the_album_even_when_a_library_is_configured():
    """The library-wide parentId must not overwrite the album's, or asking for
    one album's tracks would quietly return the entire music library - which
    still renders, still plays, and is entirely wrong."""
    library, calls = build(library_id="lib9")
    library.tracks("b1")
    assert calls[0]["params"]["parentId"] == "b1"


def test_a_configured_library_scopes_browses_that_are_not_already_scoped():
    library, calls = build(library_id="lib9")
    library.artists()
    assert calls[0]["params"]["parentId"] == "lib9"


def test_no_configured_library_means_no_parent_filter_at_all():
    library, calls = build()
    library.artists()
    assert "parentId" not in calls[0]["params"]


def test_track_durations_are_converted_from_jellyfin_ticks():
    """Jellyfin measures in 100-nanosecond ticks. Treating them as milliseconds
    would report a four-minute song as eleven hours."""
    library, _ = build({"Items": [{"Id": "t1", "Name": "Weightless",
                                   "RunTimeTicks": 4_800_000_000}]})
    (track,) = library.tracks("b1")
    assert track.duration_ms == 480_000


@pytest.mark.parametrize("ticks", [None, 0, -5, "1000"])
def test_an_unusable_duration_becomes_none_rather_than_a_wrong_number(ticks):
    library, _ = build({"Items": [{"Id": "t1", "Name": "X", "RunTimeTicks": ticks}]})
    (track,) = library.tracks("b1")
    assert track.duration_ms is None


def test_a_rejected_api_key_says_so_and_says_where_to_fix_it():
    """This is the most likely setup failure, and "Jellyfin returned 401" sends
    somebody to the wrong place."""
    library, _ = build(status_code=401)
    with pytest.raises(JellyfinError, match="API Keys"):
        library.artists()


def test_an_unreachable_server_is_a_jellyfin_error_not_a_raw_transport_error():
    """So the route can turn every upstream failure into one 502 rather than
    letting an httpx exception become a 500 that reads like a HomeDash bug."""

    def get(url, params, headers):
        raise OSError("connection refused")

    library = JellyfinLibrary("http://jf.local:8096", "k", get=get)
    with pytest.raises(JellyfinError, match="could not reach"):
        library.artists()


def test_the_stream_url_asks_for_the_original_file():
    """static=true. The speakers decode FLAC, ALAC, MP3 and AAC natively, so
    transcoding would spend server CPU to produce something worse."""
    library, _ = build()
    assert library.stream_url("t1") == "http://jf.local:8096/Audio/t1/stream?static=true"


def test_art_urls_are_bounded_in_size():
    library, _ = build()
    assert "maxWidth=480" in library.art_url("b1")
    assert "maxWidth=96" in library.art_url("b1", 96)


def test_a_trailing_slash_on_the_configured_url_does_not_double_up():
    """The .env example ends without one, but somebody will paste one in."""
    library, _ = build()
    assert "//Audio" not in library.stream_url("t1")
