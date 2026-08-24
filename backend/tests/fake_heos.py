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

from pyheos import ConnectionState


@dataclass
class FakeMedia:
    song: str | None = "Weightless"
    artist: str | None = "Marconi Union"
    album: str | None = "Distance"
    image_url: str | None = "http://speaker/art.jpg"
    duration: int | None = 480000
    current_position: int | None = 12000
    # Which of the speaker's own queue entries is playing. None is what a
    # speaker on an input or a service with no queue reports, and the pruner
    # has to leave that alone rather than reading it as "everything is stale".
    queue_id: int | None = None


@dataclass
class FakeQueueItem:
    queue_id: int
    song: str = "Track"


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
    # The speaker's own queue, which is a real thing and not a mirror of
    # HomeDash's. `play_url` appends to it - that is the HEOS behaviour this
    # fake exists to reproduce, and believing otherwise is what left finished
    # albums sitting in the speaker as lists of dead URLs.
    queue: list[FakeQueueItem] = field(default_factory=list)
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
        item = FakeQueueItem(queue_id=len(self.queue) + 1, song=url)
        self.queue.append(item)
        if self.now_playing_media is not None:
            self.now_playing_media.queue_id = item.queue_id

    async def get_queue(self, range_start=None, range_end=None) -> list:
        self.calls.append(("get_queue",))
        return list(self.queue)

    async def remove_from_queue(self, queue_ids: list[int]) -> None:
        self.calls.append(("remove_from_queue", tuple(queue_ids)))
        self.queue = [item for item in self.queue if item.queue_id not in set(queue_ids)]

    async def clear_queue(self) -> None:
        self.calls.append(("clear_queue",))
        # HEOS answers an error for this when there is nothing to clear, which
        # is why every caller treats it as best-effort. Reproduced, because a
        # fake that always succeeds would let a regression through.
        if not self.queue:
            raise RuntimeError("queue is already empty")
        self.queue = []


class FakeDispatcher:
    def __init__(self) -> None:
        self.handlers: dict[str, list] = {}

    def connect(self, signal, target):
        self.handlers.setdefault(str(signal), []).append(target)
        return lambda: self.handlers[str(signal)].remove(target)


class FakeHeos:
    """Lazy about players, because the real one is.

    `pyheos.Heos.players` is an empty dict until `get_players()` is called;
    connecting does not populate it. An eager fake hid a real bug for the whole
    life of this feature - the controller read `.players` straight after
    connecting and got nothing on real hardware, while every test passed.
    """

    def __init__(self, players: list[FakePlayer] | None = None) -> None:
        self._available = {p.player_id: p for p in (players or [FakePlayer()])}
        self.players: dict[int, FakePlayer] = {}
        self.load_count = 0
        self.dispatcher = FakeDispatcher()
        self.disconnected = False
        # The real enum, not a string, so this fake cannot drift into agreeing
        # with a value pyheos does not use. Settable, because a speaker system
        # dropping off wifi mid-evening is a state worth being able to write a
        # test about - it used to be indistinguishable from a healthy one.
        self.connection_state = ConnectionState.CONNECTED

    async def get_players(self, *, refresh: bool = False) -> dict[int, FakePlayer]:
        self.load_count += 1
        self.players = dict(self._available)
        return self.players

    async def disconnect(self) -> None:
        self.disconnected = True
