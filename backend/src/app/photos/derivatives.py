"""Pre-resizing photos to the panel's exact resolution.

The only module that touches Pillow. The Pi never decodes an original: it is a
thin client with a modest GPU, and a slideshow that stutters on every transition
is worse than no slideshow.

The panel is 1920x1080, mounted either way up, so both orientations are
prepared - which way it ends up is a deployment choice, and rotating a panel on
the wall should not mean re-rendering a photo library.

For a given panel orientation a photo either agrees with it or does not:

    panel        agreeing photo          disagreeing photo
    landscape    full bleed 1920x1080    half width  960x1080, paired
    portrait     full bleed 1080x1920    half height 1080x960, paired

Two disagreeing photos side by side fill the panel between them. That is what
"pair or crop" means in the plan, and why letterboxing was rejected: a black bar
down the side of a wall panel reads as a fault, not as a design.

So exactly two derivatives per photo, one per panel orientation - never four,
because a photo cannot both agree and disagree with the same orientation.
"""

import logging
from pathlib import Path

from PIL import Image, ImageOps

logger = logging.getLogger(__name__)

PANEL_ORIENTATIONS = ("landscape", "portrait")

# What the panel is, each way up.
PANEL_SIZES: dict[str, tuple[int, int]] = {
    "landscape": (1920, 1080),
    "portrait": (1080, 1920),
}

# The half of the panel a disagreeing photo gets: split across the long edge,
# so two of them tile the full screen exactly.
HALF_SIZES: dict[str, tuple[int, int]] = {
    "landscape": (960, 1080),
    "portrait": (1080, 960),
}

JPEG_QUALITY = 85


def orientation_of(width: int, height: int) -> str:
    """Which way round an image is. Square counts as agreeing with anything."""
    if width > height:
        return "landscape"
    if height > width:
        return "portrait"
    return "square"


def slot_for(photo_orientation: str, panel_orientation: str) -> str:
    """Whether a photo fills the panel or shares it with another.

    A square photo is treated as agreeing with either orientation: cropping it
    to fill loses the same amount whichever way the panel is turned, and
    pairing squares would leave them more distorted than a full-bleed crop.
    """
    if photo_orientation in (panel_orientation, "square"):
        return "full"
    return "half"


def target_size(panel_orientation: str, slot: str) -> tuple[int, int]:
    sizes = PANEL_SIZES if slot == "full" else HALF_SIZES
    return sizes[panel_orientation]


def derivative_name(photo_hash: str, size: tuple[int, int]) -> str:
    """Content-addressed, so a photo rewritten in place never collides with the
    derivative of what used to be there."""
    width, height = size
    return f"{photo_hash}-{width}x{height}.jpg"


def _flatten(image: Image.Image) -> Image.Image:
    """Drop transparency onto white rather than onto JPEG's implicit black.

    A PNG logo or a transparent-cornered scan converted straight to RGB comes
    out with black where it should be blank, which on a wall panel looks like a
    rendering fault rather than a source file quirk.
    """
    has_alpha = image.mode in ("RGBA", "LA") or (
        image.mode == "P" and "transparency" in image.info
    )
    if not has_alpha:
        return image.convert("RGB")
    rgba = image.convert("RGBA")
    background = Image.new("RGB", rgba.size, (255, 255, 255))
    background.paste(rgba, mask=rgba.split()[-1])
    return background


def render(source: Path, destination: Path, size: tuple[int, int]) -> None:
    """Write one centre-cropped JPEG of `source` at exactly `size`."""
    with Image.open(source) as image:
        # Before anything else. Phones record orientation in EXIF rather than
        # rotating the pixels, so skipping this shows a good fraction of any
        # real library sideways - and it would be measured from the wrong axis
        # too, so the crop would compound the error.
        upright = ImageOps.exif_transpose(image)
        flattened = _flatten(upright)
        fitted = ImageOps.fit(flattened, size, method=Image.Resampling.LANCZOS)
        destination.parent.mkdir(parents=True, exist_ok=True)
        # optimize costs a little CPU once, on a server, and saves it on every
        # transfer to a panel that will fetch this image over wifi.
        fitted.save(destination, "JPEG", quality=JPEG_QUALITY, optimize=True)


def probe(source: Path) -> tuple[int, int]:
    """The image's dimensions as displayed, honouring EXIF rotation.

    Reading width and height off the raw file would disagree with what `render`
    produces for every rotated phone photo, and the disagreement would show up
    much later as a portrait photo being handed a landscape slot.
    """
    with Image.open(source) as image:
        upright = ImageOps.exif_transpose(image)
        return upright.width, upright.height


def render_all(source: Path, photo_hash: str, cache_dir: Path, photo_orientation: str) -> None:
    """Render this photo's derivative for each way the panel can be mounted."""
    for panel_orientation in PANEL_ORIENTATIONS:
        slot = slot_for(photo_orientation, panel_orientation)
        size = target_size(panel_orientation, slot)
        destination = cache_dir / derivative_name(photo_hash, size)
        if destination.exists():
            # Two orientations can want the same size - a square photo is
            # "full" for both, at different sizes, but a future panel size
            # could collide - and a re-index of an unchanged photo should not
            # re-encode what is already there.
            continue
        render(source, destination, size)


def derivative_path(cache_dir: Path, photo_hash: str, size: tuple[int, int]) -> Path:
    return cache_dir / derivative_name(photo_hash, size)
