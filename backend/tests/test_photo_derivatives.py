"""Resizing photos to the panel's exact resolution.

The panel is mounted either way up, so a photo either agrees with the
orientation it is being shown in or it does not, and that decides whether it
fills the screen or shares it. These pin that decision and the pixels that come
out of it.
"""

import pytest
from PIL import Image

from app.photos.derivatives import (
    HALF_SIZES,
    PANEL_SIZES,
    derivative_name,
    orientation_of,
    probe,
    render,
    slot_for,
    target_size,
)


def write_image(path, width, height, color=(200, 30, 30), mode="RGB", **save_kwargs):
    image = Image.new(mode, (width, height), color)
    image.save(path, **save_kwargs)
    return path


class TestOrientation:
    @pytest.mark.parametrize(
        "width,height,expected",
        [
            (1920, 1080, "landscape"),
            (1080, 1920, "portrait"),
            (1000, 1000, "square"),
            (1001, 1000, "landscape"),
        ],
    )
    def test_orientation_follows_the_longer_edge(self, width, height, expected):
        assert orientation_of(width, height) == expected


class TestSlots:
    @pytest.mark.parametrize("panel", ["landscape", "portrait"])
    def test_a_photo_agreeing_with_the_panel_fills_it(self, panel):
        assert slot_for(panel, panel) == "full"

    def test_a_disagreeing_photo_gets_half_the_panel(self):
        assert slot_for("portrait", "landscape") == "half"
        assert slot_for("landscape", "portrait") == "half"

    @pytest.mark.parametrize("panel", ["landscape", "portrait"])
    def test_a_square_photo_always_fills_the_panel(self, panel):
        """Pairing squares would crop them harder than filling does, and it
        would leave the pair looking like a mistake rather than a choice."""
        assert slot_for("square", panel) == "full"

    def test_two_halves_tile_the_whole_panel(self):
        """If they did not, a paired slide would show a seam of background -
        which on a wall panel reads as a fault, and is the whole reason
        pairing was chosen over letterboxing."""
        for panel in ("landscape", "portrait"):
            full_w, full_h = PANEL_SIZES[panel]
            half_w, half_h = HALF_SIZES[panel]
            if panel == "landscape":
                assert half_w * 2 == full_w and half_h == full_h
            else:
                assert half_h * 2 == full_h and half_w == full_w


class TestRender:
    @pytest.mark.parametrize("panel", ["landscape", "portrait"])
    @pytest.mark.parametrize("slot", ["full", "half"])
    def test_output_is_exactly_the_target_size(self, tmp_path, panel, slot):
        source = write_image(tmp_path / "in.jpg", 3000, 2000)
        size = target_size(panel, slot)
        destination = tmp_path / "out.jpg"

        render(source, destination, size)

        with Image.open(destination) as rendered:
            assert rendered.size == size

    def test_an_exif_rotated_photo_comes_out_upright(self, tmp_path):
        """Phones record orientation in EXIF instead of rotating the pixels.
        Ignoring the tag shows a large fraction of any real library sideways -
        and crops it along the wrong axis on the way, so the mistake compounds
        rather than merely looking odd."""
        # Orientation 6 means "rotate 90 CW to display", so a 400x200 file is
        # a 200x400 photo.
        image = Image.new("RGB", (400, 200), (10, 10, 200))
        exif = image.getexif()
        exif[274] = 6
        source = tmp_path / "rotated.jpg"
        image.save(source, exif=exif)

        assert probe(source) == (200, 400)

        destination = tmp_path / "out.jpg"
        render(source, destination, (1080, 1920))
        with Image.open(destination) as rendered:
            assert rendered.size == (1080, 1920)

    def test_probe_matches_what_render_would_crop(self, tmp_path):
        """probe and render must agree about which way round a photo is, or a
        portrait photo gets handed a landscape slot and is cropped to a
        letterbox of its own middle."""
        source = write_image(tmp_path / "in.jpg", 800, 600)
        assert probe(source) == (800, 600)

    def test_transparency_becomes_white_not_black(self, tmp_path):
        """JPEG has no alpha, so a straight convert("RGB") fills transparent
        pixels with black. On a wall panel that looks like a rendering fault
        rather than a quirk of the source file."""
        image = Image.new("RGBA", (400, 400), (255, 255, 255, 0))
        source = tmp_path / "clear.png"
        image.save(source)

        destination = tmp_path / "out.jpg"
        render(source, destination, (1920, 1080))

        with Image.open(destination) as rendered:
            assert rendered.mode == "RGB"
            assert rendered.getpixel((10, 10)) == (255, 255, 255)

    def test_a_palette_png_with_transparency_also_flattens(self, tmp_path):
        image = Image.new("P", (400, 400))
        source = tmp_path / "paletted.png"
        image.save(source, transparency=0)

        destination = tmp_path / "out.jpg"
        render(source, destination, (960, 1080))

        with Image.open(destination) as rendered:
            assert rendered.size == (960, 1080)
            assert rendered.mode == "RGB"

    def test_render_creates_the_cache_directory(self, tmp_path):
        source = write_image(tmp_path / "in.jpg", 500, 500)
        destination = tmp_path / "nested" / "deeper" / "out.jpg"

        render(source, destination, (1920, 1080))

        assert destination.exists()

    def test_a_non_image_raises_rather_than_writing_garbage(self, tmp_path):
        source = tmp_path / "notes.jpg"
        source.write_text("this is not a photo")

        with pytest.raises(Exception):
            render(source, tmp_path / "out.jpg", (1920, 1080))


class TestDerivativeNames:
    def test_the_name_carries_the_hash_and_the_size(self):
        assert derivative_name("abc123", (1920, 1080)) == "abc123-1920x1080.jpg"

    def test_a_rewritten_photo_cannot_reuse_a_stale_derivative(self):
        """The name is content-addressed for this reason: the URL a panel
        caches for a year must stop being valid the moment the bytes change."""
        assert derivative_name("aaa", (1920, 1080)) != derivative_name("bbb", (1920, 1080))
