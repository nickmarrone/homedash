"""The screensaver's two endpoints, over a real database.

Constructed without the lifespan, so no migrations run, no scheduler starts and
nothing is indexed - the session fixture supplies the schema and each test
inserts the rows it needs.

The pairing decision is what most of this is about: which slot a photo gets
depends on the orientation being asked for, and getting it backwards would show
every portrait photo cropped to a letterbox of its own middle.
"""

import io

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from app.api import routes as routes_module
from app.api.routes import router
from app.config import Settings
from app.db import get_session
from app.models import Photo


@pytest.fixture
def cache_dir(tmp_path):
    directory = tmp_path / "cache"
    directory.mkdir()
    return directory


@pytest.fixture
def client(session, monkeypatch, cache_dir):
    monkeypatch.setattr(
        routes_module,
        "settings",
        Settings(
            _env_file=None,
            photo_cache_dir=cache_dir,
            screensaver_dwell_seconds=25,
            screensaver_idle_minutes=7,
        ),
    )
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = lambda: session
    return TestClient(app)


def add_photo(session, path="beach.jpg", orientation="landscape", photo_hash="abc123", error=None):
    photo = Photo(
        path=path,
        hash=photo_hash,
        width=1600 if orientation == "landscape" else 1200,
        height=1200 if orientation == "landscape" else 1600,
        orientation=orientation,
        size=1024,
        mtime_ns=1,
        error=error,
    )
    session.add(photo)
    session.commit()
    session.refresh(photo)
    return photo


def render_derivative(cache_dir, photo_hash, size):
    path = cache_dir / f"{photo_hash}-{size[0]}x{size[1]}.jpg"
    Image.new("RGB", size, (12, 34, 56)).save(path)
    return path


class TestPlaylist:
    def test_a_landscape_photo_fills_a_landscape_panel(self, client, session):
        add_photo(session, orientation="landscape")

        body = client.get("/api/photos", params={"orientation": "landscape"}).json()

        assert [p["slot"] for p in body["photos"]] == ["full"]
        assert body["photos"][0]["width"] == 1920
        assert body["photos"][0]["height"] == 1080

    def test_the_same_photo_shares_a_portrait_panel(self, client, session):
        """The panel is mounted either way up, so which photos are the awkward
        ones inverts with it. Assuming portrait photos are always the odd ones
        out is the bug this guards."""
        add_photo(session, orientation="landscape")

        body = client.get("/api/photos", params={"orientation": "portrait"}).json()

        assert body["photos"][0]["slot"] == "half"
        assert body["photos"][0]["width"] == 1080
        assert body["photos"][0]["height"] == 960

    def test_a_square_photo_fills_either_orientation(self, client, session):
        add_photo(session, orientation="square")

        for orientation in ("landscape", "portrait"):
            body = client.get("/api/photos", params={"orientation": orientation}).json()
            assert body["photos"][0]["slot"] == "full"

    def test_the_url_carries_the_content_hash(self, client, session):
        """Without it the panel would cache one image at that URL for a year
        and never see the photo change."""
        add_photo(session, photo_hash="deadbeef")

        body = client.get("/api/photos").json()

        assert "v=deadbeef" in body["photos"][0]["url"]

    def test_undecodable_photos_are_left_out(self, client, session):
        add_photo(session, path="good.jpg", photo_hash="aaa")
        add_photo(session, path="broken.jpg", photo_hash="bbb", error="cannot identify")

        body = client.get("/api/photos").json()

        assert len(body["photos"]) == 1

    def test_the_playlist_carries_its_own_timings(self, client, session):
        """The panel holds no configuration of its own; these arrive with the
        data, the same contract the Pi's screen agent has."""
        body = client.get("/api/photos").json()

        assert body["dwell_seconds"] == 25
        assert body["idle_minutes"] == 7

    def test_an_empty_folder_is_an_empty_playlist_not_an_error(self, client):
        """This is also the screensaver's off switch: no photos, no drifting."""
        response = client.get("/api/photos")

        assert response.status_code == 200
        assert response.json()["photos"] == []

    def test_the_playlist_is_capped(self, client, session, monkeypatch):
        for i in range(5):
            add_photo(session, path=f"{i}.jpg", photo_hash=f"hash{i}")
        monkeypatch.setattr(
            routes_module, "settings", Settings(_env_file=None, photo_max_count=3)
        )

        body = client.get("/api/photos").json()

        assert len(body["photos"]) == 3

    def test_an_unknown_orientation_is_rejected(self, client):
        response = client.get("/api/photos", params={"orientation": "sideways"})

        assert response.status_code == 400


class TestImage:
    def test_a_rendered_derivative_is_served_as_jpeg(self, client, session, cache_dir):
        photo = add_photo(session, orientation="landscape", photo_hash="abc123")
        render_derivative(cache_dir, "abc123", (1920, 1080))

        response = client.get(
            f"/api/photos/{photo.id}/image", params={"orientation": "landscape"}
        )

        assert response.status_code == 200
        assert response.headers["content-type"] == "image/jpeg"
        with Image.open(io.BytesIO(response.content)) as image:
            assert image.size == (1920, 1080)

    def test_the_response_is_cacheable_forever(self, client, session, cache_dir):
        """The slideshow loops for months. Without this the panel re-fetches
        the entire library on every pass."""
        photo = add_photo(session, photo_hash="abc123")
        render_derivative(cache_dir, "abc123", (1920, 1080))

        response = client.get(f"/api/photos/{photo.id}/image")

        assert "immutable" in response.headers["cache-control"]

    def test_the_orientation_selects_which_derivative_is_served(
        self, client, session, cache_dir
    ):
        photo = add_photo(session, orientation="landscape", photo_hash="abc123")
        render_derivative(cache_dir, "abc123", (1920, 1080))
        render_derivative(cache_dir, "abc123", (1080, 960))

        portrait = client.get(
            f"/api/photos/{photo.id}/image", params={"orientation": "portrait"}
        )

        with Image.open(io.BytesIO(portrait.content)) as image:
            assert image.size == (1080, 960)

    def test_an_unknown_photo_is_a_404(self, client):
        assert client.get("/api/photos/999/image").status_code == 404

    def test_an_unrendered_derivative_is_a_404_not_a_resize(self, client, session):
        """Rendering here would put a multi-second Pillow call on a request the
        panel makes every few seconds. The panel skips the slide instead."""
        photo = add_photo(session, photo_hash="notrendered")

        assert client.get(f"/api/photos/{photo.id}/image").status_code == 404

    def test_a_stale_v_parameter_still_serves(self, client, session, cache_dir):
        """`v` exists to change the URL, not to be checked. Rejecting a stale
        one would turn a panel holding a slightly old playlist into a panel
        showing gaps."""
        photo = add_photo(session, photo_hash="abc123")
        render_derivative(cache_dir, "abc123", (1920, 1080))

        response = client.get(
            f"/api/photos/{photo.id}/image", params={"v": "something-old"}
        )

        assert response.status_code == 200
