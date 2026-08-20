"""Reconciling the photos table against the folder on disk.

The expensive part of indexing is opening and hashing files, and the folder is
rescanned every fifteen minutes forever, so most of these are about what the
indexer manages *not* to do on an unchanged folder - and about the two ways a
photo can stop being showable: it is deleted, or it stops decoding.
"""

import pytest
from PIL import Image
from sqlmodel import select

from app.config import Settings
from app.models import Photo
from app.photos import index as index_module
from app.photos.folder import FolderPhotoSource
from app.photos.index import reindex


@pytest.fixture
def photos_dir(tmp_path):
    directory = tmp_path / "photos"
    directory.mkdir()
    return directory


@pytest.fixture
def cache_dir(tmp_path):
    return tmp_path / "cache"


@pytest.fixture(autouse=True)
def settings(monkeypatch, photos_dir, cache_dir):
    configured = Settings(
        _env_file=None,
        photos_dir=photos_dir,
        photo_cache_dir=cache_dir,
    )
    monkeypatch.setattr(index_module, "settings", configured)
    return configured


def add_photo(directory, name, width=1600, height=1200, color=(180, 40, 40)):
    path = directory / name
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (width, height), color).save(path)
    return path


def scan(session, photos_dir):
    return reindex(session, FolderPhotoSource(photos_dir))


def rows(session):
    return {photo.path: photo for photo in session.exec(select(Photo)).all()}


class TestIndexing:
    def test_a_new_photo_is_indexed_with_its_orientation(self, session, photos_dir):
        add_photo(photos_dir, "beach.jpg", 1600, 1200)

        assert scan(session, photos_dir) is True

        photo = rows(session)["beach.jpg"]
        assert photo.width == 1600
        assert photo.height == 1200
        assert photo.orientation == "landscape"
        assert photo.error is None
        assert photo.hash

    def test_photos_in_subfolders_are_found(self, session, photos_dir):
        add_photo(photos_dir, "2026/summer/lake.jpg")

        scan(session, photos_dir)

        assert "2026/summer/lake.jpg" in rows(session)

    def test_both_derivatives_are_rendered(self, session, photos_dir, cache_dir):
        """One per way the panel can be mounted. Rotating a panel on the wall
        should not mean re-rendering the library."""
        add_photo(photos_dir, "beach.jpg", 1600, 1200)

        scan(session, photos_dir)

        sizes = set()
        for path in cache_dir.glob("*.jpg"):
            with Image.open(path) as image:
                sizes.add(image.size)
        # Landscape photo: fills a landscape panel, shares a portrait one.
        assert sizes == {(1920, 1080), (1080, 960)}

    def test_a_non_image_is_never_offered_to_the_indexer(self, session, photos_dir):
        (photos_dir / "notes.txt").write_text("not a photo")

        assert scan(session, photos_dir) is False
        assert rows(session) == {}

    def test_a_second_scan_of_an_unchanged_folder_reports_no_change(
        self, session, photos_dir
    ):
        """The panel is told to refetch on a change. A folder that has not
        changed must not wake every panel in the house every fifteen minutes."""
        add_photo(photos_dir, "beach.jpg")
        scan(session, photos_dir)

        assert scan(session, photos_dir) is False

    def test_an_unchanged_photo_is_not_rehashed(self, session, photos_dir, monkeypatch):
        """Re-reading every byte of a photo library on a schedule, forever, to
        learn that nothing changed is the one part of this that would actually
        cost something."""
        add_photo(photos_dir, "beach.jpg")
        scan(session, photos_dir)

        calls = []
        real_hash = index_module.hash_file
        monkeypatch.setattr(
            index_module, "hash_file", lambda path: calls.append(path) or real_hash(path)
        )

        scan(session, photos_dir)

        assert calls == []


class TestChanges:
    def test_a_photo_replaced_in_place_gets_a_new_hash(self, session, photos_dir):
        """Same path, same name, different bytes. Without re-hashing, the panel
        would keep serving the old derivative from a URL it was told to cache
        for a year."""
        add_photo(photos_dir, "beach.jpg", 1600, 1200)
        scan(session, photos_dir)
        before = rows(session)["beach.jpg"].hash

        add_photo(photos_dir, "beach.jpg", 1200, 1600, color=(10, 10, 200))
        assert scan(session, photos_dir) is True

        after = rows(session)["beach.jpg"]
        assert after.hash != before
        assert after.orientation == "portrait"

    def test_a_deleted_photo_is_dropped_from_the_index(self, session, photos_dir):
        """The failure mode that actually matters: a photo pulled out of the
        folder must stop appearing on the kitchen wall."""
        path = add_photo(photos_dir, "beach.jpg")
        scan(session, photos_dir)

        path.unlink()

        assert scan(session, photos_dir) is True
        assert rows(session) == {}

    def test_a_deleted_photo_takes_its_derivatives_with_it(
        self, session, photos_dir, cache_dir
    ):
        path = add_photo(photos_dir, "beach.jpg")
        scan(session, photos_dir)
        assert list(cache_dir.glob("*.jpg"))

        path.unlink()
        scan(session, photos_dir)

        assert list(cache_dir.glob("*.jpg")) == []

    def test_a_rewritten_photo_leaves_no_orphaned_derivatives(
        self, session, photos_dir, cache_dir
    ):
        add_photo(photos_dir, "beach.jpg", 1600, 1200)
        scan(session, photos_dir)

        add_photo(photos_dir, "beach.jpg", 1200, 1600, color=(10, 10, 200))
        scan(session, photos_dir)

        assert len(list(cache_dir.glob("*.jpg"))) == 2

    def test_duplicate_photos_keep_their_shared_derivatives(
        self, session, photos_dir, cache_dir
    ):
        """Two copies of one photo hash identically and therefore share
        derivative files. Deleting one copy must not blank the other - which
        is exactly what unlinking per deleted row would do."""
        add_photo(photos_dir, "a.jpg", 1600, 1200)
        add_photo(photos_dir, "b.jpg", 1600, 1200)
        scan(session, photos_dir)
        assert rows(session)["a.jpg"].hash == rows(session)["b.jpg"].hash

        (photos_dir / "a.jpg").unlink()
        scan(session, photos_dir)

        assert set(rows(session)) == {"b.jpg"}
        assert len(list(cache_dir.glob("*.jpg"))) == 2

    def test_a_wiped_cache_is_rebuilt_without_touching_the_table(
        self, session, photos_dir, cache_dir
    ):
        """The cache is a separate volume precisely so it can be deleted to
        force a clean re-render. If the size/mtime fast path skipped that, the
        documented recovery would quietly do nothing."""
        add_photo(photos_dir, "beach.jpg")
        scan(session, photos_dir)
        for path in cache_dir.glob("*.jpg"):
            path.unlink()

        assert scan(session, photos_dir) is False
        assert len(list(cache_dir.glob("*.jpg"))) == 2


class TestUnreadablePhotos:
    def test_an_undecodable_file_is_recorded_rather_than_raised(
        self, session, photos_dir
    ):
        """One bad file in a folder of two thousand must not stop the other
        1999 from reaching the panel."""
        (photos_dir / "broken.jpg").write_text("this is not a photo")
        add_photo(photos_dir, "good.jpg")

        scan(session, photos_dir)

        indexed = rows(session)
        assert indexed["broken.jpg"].error is not None
        assert indexed["good.jpg"].error is None

    def test_a_broken_file_is_not_retried_on_every_scan(self, session, photos_dir, monkeypatch):
        """Without a row to remember it by, a corrupt file would be reopened
        and fail on every rescan for as long as it sits in the folder."""
        (photos_dir / "broken.jpg").write_text("this is not a photo")
        scan(session, photos_dir)

        calls = []
        monkeypatch.setattr(
            index_module, "hash_file", lambda path: calls.append(path) or "x"
        )

        assert scan(session, photos_dir) is False
        assert calls == []

    def test_a_repaired_file_recovers_on_the_next_scan(self, session, photos_dir):
        (photos_dir / "broken.jpg").write_text("this is not a photo")
        scan(session, photos_dir)

        add_photo(photos_dir, "broken.jpg")

        assert scan(session, photos_dir) is True
        assert rows(session)["broken.jpg"].error is None

    def test_a_file_that_breaks_reports_a_change(self, session, photos_dir):
        """It was on the panel a minute ago and now it cannot be; that is a
        playlist change even though nothing was added."""
        add_photo(photos_dir, "beach.jpg")
        scan(session, photos_dir)

        (photos_dir / "beach.jpg").write_text("corrupted")

        assert scan(session, photos_dir) is True
        assert rows(session)["beach.jpg"].error is not None


class TestFolderWalk:
    def test_thumbnail_directories_are_skipped(self, session, photos_dir):
        """Photo-syncing tools scatter these through a share. Indexing them
        shows the same picture twice, once at thumbnail quality."""
        add_photo(photos_dir, "@eaDir/beach.jpg")
        add_photo(photos_dir, ".stfolder/beach.jpg")
        add_photo(photos_dir, "beach.jpg")

        scan(session, photos_dir)

        assert set(rows(session)) == {"beach.jpg"}

    def test_macos_resource_forks_are_skipped(self, session, photos_dir):
        """`._IMG_1234.JPG` is a real file with a real .jpg suffix and no
        image inside it, so it would otherwise become an error row on every
        photo copied off a Mac."""
        add_photo(photos_dir, "IMG_1234.jpg")
        (photos_dir / "._IMG_1234.jpg").write_bytes(b"\x00\x05\x16\x07resource fork")

        scan(session, photos_dir)

        assert set(rows(session)) == {"IMG_1234.jpg"}

    def test_a_missing_folder_is_not_an_error(self, session, tmp_path):
        """An unmounted volume must not take the calendar down with it."""
        assert reindex(session, FolderPhotoSource(tmp_path / "nope")) is False
