"""Auto-assigned calendar colors.

Colors are not user-configurable: each calendar gets the palette entry at its
configured position.

These are the deep tones the "Kitchen paper" panel design asks for. The
constraint they are chosen against changed with that design, in two ways worth
recording:

* The panel is now light-committed (`frontend/src/lib/theme.css` sets
  `color-scheme: light`), so the old two-sided requirement - 3:1 against both
  #ffffff and #1b1b1b, which is what forced mid-tones - is gone. There is one
  ground, the warm off-white #faf7f2.
* The design uses the calendar's colour as *text* for an all-day event, not
  only as an accent bar, so the bar has to clear the text threshold rather than
  the non-text one.

Every entry therefore clears 4.5:1 against #faf7f2. The measured ratios, in
order: 8.2, 6.1, 5.1, 4.7, 6.7, 5.7, 5.0, 4.7.

If the panel ever goes back to following the OS theme, this docstring is the
thing to re-derive: none of these clear 3:1 against a near-black ground.
"""

PALETTE: tuple[str, ...] = (
    "#1e40af",  # blue
    "#b91c1c",  # red
    "#047857",  # emerald
    "#b45309",  # amber
    "#6d28d9",  # violet
    "#be185d",  # pink
    "#0e7490",  # cyan
    "#4d7c0f",  # olive
)

# Warm rather than neutral grey, so a calendar with no colour still belongs to
# the paper. Mirrored as --accent-fallback in the frontend's theme.css for the
# case where an item arrives with no calendar at all.
FALLBACK_COLOR = "#7a7162"


def color_for_index(index: int) -> str:
    """Palette color for a calendar at `index` in configured order, wrapping
    around if there are more calendars than palette entries."""
    return PALETTE[index % len(PALETTE)]
