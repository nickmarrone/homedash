"""The audio proxy, which the speaker fetches and nobody ever looks at.

It exists so no Jellyfin credential travels in a URL and so the URL stays under
the 255 characters HEOS will fetch. Both of those make it invisible: it is not
on any screen, the panel never calls it, and when it goes wrong the symptom is
a speaker playing silence.

Driven through `JellyfinLibrary.open_stream` against a fake transport rather
than over HTTP, because what is worth pinning is the contract the route relies
on - what it opens, what it forwards, and that it closes both objects. The
route on top of it is six lines of turning that into a response.
"""

import asyncio

import httpx
import pytest

from app.music.jellyfin import JellyfinError, JellyfinLibrary

AUDIO = b"\xff\xfb" + b"sound" * 200


def streaming(status: int, body: bytes = b"", headers: dict | None = None) -> httpx.Response:
    """A response whose content is pulled rather than already in hand.

    MockTransport's `content=` is eagerly read, so a response built that way
    raises StreamConsumed the moment `aiter_raw` touches it - and streaming is
    the whole point of this endpoint. An async iterator gives httpx a real
    stream to hand back.
    """

    async def chunks():
        for i in range(0, len(body), 64):
            yield body[i : i + 64]

    return httpx.Response(status, content=chunks(), headers=headers)


@pytest.fixture
def patched(monkeypatch):
    """Point httpx.AsyncClient inside jellyfin.py at a fake transport."""

    def install(handler):
        original = httpx.AsyncClient

        def factory(*args, **kwargs):
            return original(*args, transport=httpx.MockTransport(handler), **kwargs)

        monkeypatch.setattr("app.music.jellyfin.httpx.AsyncClient", factory)
        return JellyfinLibrary("http://jelly:8096", "secret-key")

    return install


def run(coro):
    return asyncio.run(coro)


async def drain(lib, track_id, range_header=None):
    response, client = await lib.open_stream(track_id, range_header)
    try:
        chunks = [chunk async for chunk in response.aiter_raw()]
    finally:
        await response.aclose()
        await client.aclose()
    return response, b"".join(chunks)


class TestFetchingTheWholeTrack:
    def test_the_bytes_come_through(self, patched):
        lib = patched(lambda request: streaming(200, AUDIO))

        response, body = run(drain(lib, "t1"))

        assert response.status_code == 200
        assert body == AUDIO

    def test_it_asks_jellyfin_for_the_original_file(self, patched):
        """`static=true`. The speakers decode FLAC, ALAC, MP3 and AAC natively,
        so transcoding would burn server CPU to produce something worse."""
        seen = {}

        def handler(request):
            seen["url"] = str(request.url)
            return streaming(200, AUDIO)

        run(drain(patched(handler), "t1"))

        assert "/Audio/t1/stream" in seen["url"]
        assert "static=true" in seen["url"]


class TestTheApiKey:
    def test_it_travels_in_a_header(self, patched):
        seen = {}

        def handler(request):
            seen["auth"] = request.headers.get("Authorization")
            return streaming(200, AUDIO)

        run(drain(patched(handler), "t1"))

        assert "secret-key" in seen["auth"]

    def test_it_never_appears_in_the_url(self, patched):
        """The whole reason this proxy exists. Jellyfin removes
        query-parameter auth in 10.13, and a speaker's request log is not a
        place to leave a credential."""
        seen = {}

        def handler(request):
            seen["url"] = str(request.url)
            return streaming(200, AUDIO)

        run(drain(patched(handler), "t1"))

        assert "secret-key" not in seen["url"]


class TestRangeRequests:
    def test_the_range_header_is_forwarded(self, patched):
        """HEOS asks for ranges, and Jellyfin already implements them properly
        for the underlying file - so they are passed on rather than answered
        here."""
        seen = {}

        def handler(request):
            seen["range"] = request.headers.get("Range")
            return streaming(206, AUDIO[10:20])

        run(drain(patched(handler), "t1", "bytes=10-19"))

        assert seen["range"] == "bytes=10-19"

    def test_a_206_and_its_content_range_survive(self, patched):
        def handler(request):
            return streaming(
                206, AUDIO[10:20], {"Content-Range": f"bytes 10-19/{len(AUDIO)}"}
            )

        response, body = run(drain(patched(handler), "t1", "bytes=10-19"))

        assert response.status_code == 206
        assert response.headers["content-range"] == f"bytes 10-19/{len(AUDIO)}"
        assert body == AUDIO[10:20]

    def test_compression_is_declined(self, patched):
        """The proxy streams undecoded bytes and forwards no Content-Encoding,
        so a compressed response would reach the speaker as audio it cannot
        know is compressed. httpx asks for gzip by default, so this has to be
        turned off on purpose - and audio being already compressed is exactly
        why nobody would find it until some server decided to gzip anyway."""
        seen = {}

        def handler(request):
            seen["encoding"] = request.headers.get("Accept-Encoding", "")
            return streaming(200, AUDIO)

        run(drain(patched(handler), "t1"))

        assert seen["encoding"] == "identity"


class TestFailures:
    def test_an_upstream_error_is_a_jellyfin_error(self, patched):
        lib = patched(lambda request: httpx.Response(404))

        with pytest.raises(JellyfinError):
            run(lib.open_stream("gone"))

    def test_an_unreachable_server_is_a_jellyfin_error(self, patched):
        def refuse(request):
            raise httpx.ConnectError("no route to host")

        with pytest.raises(JellyfinError):
            run(patched(refuse).open_stream("t1"))

    def test_a_failure_does_not_leak_the_client(self, patched):
        """The client is created before the request and outlives the function
        on the happy path, so every failure branch has to close it by hand.
        A leak here is one dangling connection per failed track."""
        opened = []

        def handler(request):
            return httpx.Response(500)

        lib = patched(handler)

        import app.music.jellyfin as jf

        real_factory = jf.httpx.AsyncClient

        def tracking(*args, **kwargs):
            client = real_factory(*args, **kwargs)
            opened.append(client)
            return client

        jf.httpx.AsyncClient = tracking
        try:
            with pytest.raises(JellyfinError):
                run(lib.open_stream("t1"))
        finally:
            jf.httpx.AsyncClient = real_factory

        assert opened and all(c.is_closed for c in opened)
