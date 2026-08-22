"""A stand-in for a HEOS system, good enough to drive the controller.

Injected through `HeosController`'s `connect` argument rather than patched over
`pyheos`, which is the same discipline the calendar adapters use for HTTP: the
seam is a constructor parameter, so a test never has to know which module-level
name the real implementation happens to bind.

It records the commands it was sent, because most of what is worth asserting
here is "did the right verb reach the right speaker" - the speaker's own
response to that verb is the firmware's business, and `homedash-heos-probe` is
what asks it.
"""

from dataclasses import dataclass, field


@dataclass
class FakeMedia:
    song: str | None = "Weightless"
    artist: str | None = "Marconi Union"
    album: str | None = "Distance"
    image_url: str | None = "http://speaker/art.jpg"
    duration: int | None = 480000
    current_position: int | None = 12000


@dataclass
class FakePlayer:
    player_id: int = 1
    name: str = "Kitchen"
    model: str = "HEOS 1"
    version: str = "1.583.147"
    available: bool = True
    state: str = "stop"
    volume: int = 20
    is_muted: bool = False
    group_id: int | None = None
    now_playing_media: FakeMedia | None = field(default_factory=FakeMedia)
    calls: list[tuple] = field(default_factory=list)

    async def play(self) -> None:
        self.calls.append(("play",))
        self.state = "play"

    async def pause(self) -> None:
        self.calls.append(("pause",))
        self.state = "pause"

    async def stop(self) -> None:
        self.calls.append(("stop",))
        self.state = "stop"

    async def play_next(self) -> None:
        self.calls.append(("next",))

    async def play_previous(self) -> None:
        self.calls.append(("previous",))

    async def set_volume(self, level: int) -> None:
        self.calls.append(("volume", level))
        self.volume = level

    async def play_url(self, url: str) -> None:
        self.calls.append(("play_url", url))


class FakeDispatcher:
    def __init__(self) -> None:
        self.handlers: dict[str, list] = {}

    def connect(self, signal, target):
        self.handlers.setdefault(str(signal), []).append(target)
        return lambda: self.handlers[str(signal)].remove(target)


class FakeHeos:
    def __init__(self, players: list[FakePlayer] | None = None) -> None:
        self.players = {p.player_id: p for p in (players or [FakePlayer()])}
        self.dispatcher = FakeDispatcher()
        self.disconnected = False

    async def disconnect(self) -> None:
        self.disconnected = True
