"""Auto-assigned calendar colors.

Colors are not user-configurable: each calendar gets the palette entry at its
configured position. These are mid-tone hues chosen to stay legible as a thin
accent bar against both a white and a near-black background, since the frontend
sets `color-scheme: light dark` and has no theming layer to swap values. Every
entry clears a 3:1 contrast ratio (the WCAG non-text threshold) against both
#ffffff and #1b1b1b - that two-sided constraint is why these are mid-tones
rather than the brighter hues a light-only palette could use.
"""

PALETTE: tuple[str, ...] = (
    "#2563eb",  # blue
    "#dc2626",  # red
    "#059669",  # emerald
    "#d97706",  # amber
    "#8b5cf6",  # violet
    "#db2777",  # pink
    "#0891b2",  # cyan
    "#65a30d",  # olive
)

FALLBACK_COLOR = "#888888"


def color_for_index(index: int) -> str:
    """Palette color for a calendar at `index` in configured order, wrapping
    around if there are more calendars than palette entries."""
    return PALETTE[index % len(PALETTE)]
