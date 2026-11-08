"""The audio proxy, which the speaker fetches and nobody ever looks at.

It exists so no Jellyfin credential travels in a URL and so the URL stays under
the 255 characters HEOS will fetch. Both of those make it invisible: it is not
on any screen, the panel never calls it, and when it goes wrong the symptom is
a speaker playing silence.

Driven through `JellyfinLibrary.open_stream` against a fake transport rather
than over HTTP, because what is worth pinning is what a source hands the route:
what it opens, what it forwards, and that it lets go afterwards. The route's
side of it is tested over HTTP in `test_music_api.py`.
"""

import asyncio

import anyio
import httpx
import pytest

from app.music.base import AudioStream
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
def opened_clients():
    """Every AsyncClient `patched` builds, so a test can ask whether it closed."""
    return []


@pytest.fixture
def patched(monkeypatch, opened_clients):
    """Point httpx.AsyncClient inside jellyfin.py at a fake transport."""

    def install(handler):
        original = httpx.AsyncClient

        def factory(*args, **kwargs):
            client = original(*args, transport=httpx.MockTransport(handler), **kwargs)
            opened_clients.append(client)
            return client

        monkeypatch.setattr("app.music.jellyfin.httpx.AsyncClient", factory)
        return JellyfinLibrary("http://jelly:8096", "secret-key")

    return install


def run(coro):
    return asyncio.run(coro)


async def drain(lib, track_id, range_header=None):
    stream = await lib.open_stream(track_id, range_header)
    return stream, b"".join([chunk async for chunk in stream.body()])


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

    def test_a_failure_does_not_leak_the_client(self, patched, opened_clients):
        """The client is created before the request and outlives the function
        on the happy path, so every failure branch has to close it by hand.
        A leak here is one dangling connection per failed track."""
        lib = patched(lambda request: httpx.Response(500))

        with pytest.raises(JellyfinError):
            run(lib.open_stream("t1"))

        assert opened_clients and all(c.is_closed for c in opened_clients)


class TestWhatGoesToTheSpeaker:
    def test_only_the_headers_needed_to_seek_are_forwarded(self, patched):
        """Content-Encoding above all: the bytes are raw, so forwarding one
        would be claiming something about them that the proxy cannot vouch for."""
        lib = patched(
            lambda request: streaming(
                200,
                AUDIO,
                {
                    "Content-Length": str(len(AUDIO)),
                    "Accept-Ranges": "bytes",
                    "Content-Encoding": "gzip",
                    "Server": "Kestrel",
                },
            )
        )

        stream, _ = run(drain(lib, "t1"))

        assert set(stream.headers) == {"content-length", "accept-ranges"}

    def test_the_upstream_content_type_is_kept(self, patched):
        lib = patched(lambda request: streaming(200, AUDIO, {"Content-Type": "audio/flac"}))

        stream, _ = run(drain(lib, "t1"))

        assert stream.media_type == "audio/flac"

    def test_a_missing_content_type_is_assumed_to_be_audio(self, patched):
        lib = patched(lambda request: streaming(200, AUDIO))

        stream, _ = run(drain(lib, "t1"))

        assert stream.media_type == "audio/mpeg"


class TestLettingGo:
    def test_the_client_is_closed_once_the_track_has_been_read(self, patched, opened_clients):
        run(drain(patched(lambda request: streaming(200, AUDIO)), "t1"))

        assert opened_clients and all(c.is_closed for c in opened_clients)

    def test_the_client_is_closed_when_the_speaker_hangs_up_early(self, patched, opened_clients):
        """Routine rather than exceptional: a skip abandons the fetch partway."""
        lib = patched(lambda request: streaming(200, AUDIO))

        async def read_one_chunk():
            stream = await lib.open_stream("t1")
            body = stream.body()
            await anext(body)
            await body.aclose()

        run(read_one_chunk())

        assert opened_clients and all(c.is_closed for c in opened_clients)


def test_cleanup_finishes_even_when_the_fetch_is_cancelled():
    """How a disconnect actually arrives under Starlette: the task group's scope
    is cancelled while the body is waiting on its next chunk. An unshielded
    cleanup would die on its own first await, and the ones after it never run."""
    closed = []

    async def chunks():
        yield b"first"
        await anyio.sleep_forever()

    async def close():
        await anyio.sleep(0)
        closed.append(True)

    async def scenario():
        stream = AudioStream(200, "audio/mpeg", {}, chunks(), close)

        async def consume():
            async for _ in stream.body():
                pass

        async with anyio.create_task_group() as group:
            group.start_soon(consume)
            await anyio.sleep(0.01)
            group.cancel_scope.cancel()

    run(scenario())

    assert closed == [True]
