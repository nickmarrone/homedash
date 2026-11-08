"""Watching the photo folder: the fast path, and everything that may refuse it."""

import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.photos import observer as observer_module
from app.photos.observer import _DebouncedHandler, start_folder_watch


def fs_event(src: str = "", dest: str = "", is_directory: bool = False):
    return SimpleNamespace(src_path=src, dest_path=dest, is_directory=is_directory)


class Recorder:
    """Counts scans, and lets a test wait for one without sleeping."""

    def __init__(self) -> None:
        self.count = 0
        self.fired = threading.Event()

    def __call__(self) -> None:
        self.count += 1
        self.fired.set()


class TestWhichEventsMatter:
    @pytest.mark.parametrize(
        "event",
        [
            fs_event(src="/p/beach.jpg"),
            fs_event(src="/p/BEACH.JPG"),
            fs_event(src="/p/.syncthing.tmp", dest="/p/beach.jpg"),
        ],
    )
    def test_a_photo_triggers_a_scan(self, event):
        scans = Recorder()
        handler = _DebouncedHandler(scans, 0.01)
        handler.on_any_event(event)
        assert scans.fired.wait(2)

    @pytest.mark.parametrize(
        "event",
        [
            fs_event(src="/p/notes.txt"),
            fs_event(src="/p/.syncthing.tmp"),
            fs_event(src="/p/album.jpg", is_directory=True),
            fs_event(),
        ],
    )
    def test_anything_else_does_not(self, event):
        scheduled = []
        handler = _DebouncedHandler(lambda: None, 0.01)
        handler._schedule = lambda: scheduled.append(True)
        handler.on_any_event(event)
        assert scheduled == []


class TestDebounce:
    def test_a_burst_is_one_scan(self):
        scans = Recorder()
        handler = _DebouncedHandler(scans, 0.05)
        for i in range(50):
            handler.on_any_event(fs_event(src=f"/p/{i}.jpg"))
        assert scans.fired.wait(2)
        assert scans.count == 1

    def test_stopping_cancels_a_pending_scan(self):
        scans = Recorder()
        handler = _DebouncedHandler(scans, 0.05)
        handler.on_any_event(fs_event(src="/p/a.jpg"))
        handler.stop()
        assert not scans.fired.wait(0.2)

    def test_a_failing_scan_does_not_escape_the_timer_thread(self, caplog):
        handler = _DebouncedHandler(lambda: 1 / 0, 0.01)
        handler._fire()
        assert "rescan failed" in caplog.text


class TestStartFolderWatch:
    def test_a_missing_folder_is_not_watched(self, tmp_path):
        assert start_folder_watch(tmp_path / "missing", lambda: None) is None

    def test_a_watch_that_cannot_start_falls_back_quietly(self, tmp_path, monkeypatch):
        """An exhausted inotify limit is a reason to rely on the rescan, and
        not a reason to refuse to start."""

        class Exhausted:
            def schedule(self, *args, **kwargs):
                raise OSError("inotify watch limit reached")

        monkeypatch.setattr(observer_module, "Observer", Exhausted)
        assert start_folder_watch(tmp_path, lambda: None) is None

    def test_a_real_file_written_into_a_subfolder_is_seen(self, tmp_path):
        album = tmp_path / "album"
        album.mkdir()
        scans = Recorder()
        watch = start_folder_watch(tmp_path, scans, debounce_seconds=0.01)
        assert watch is not None
        try:
            Path(album / "beach.jpg").write_bytes(b"x")
            assert scans.fired.wait(5)
        finally:
            watch.stop()

    def test_stop_ends_the_observer_thread(self, tmp_path):
        watch = start_folder_watch(tmp_path, lambda: None, debounce_seconds=0.01)
        assert watch is not None
        watch.stop()
        assert not watch._observer.is_alive()
