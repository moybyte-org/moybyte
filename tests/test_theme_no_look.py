"""A theme with no look (docs/look_2026-10.md step 33): a colours-only
package, Mint (tests/fixtures/themes/mint.moy), takes the default look's
geometry and marks flat -- no shadow, no backdrop -- and every colour the
look would set from its own roles; scenes `*fallback*` in
tools/look_shots.py draw it on the T-Deck and the desk."""

import json
import os
import sys

import runtime  # noqa: F401  (registers the host's moy_ui)
import moy_ui

from runtime import ui_draw
from ws_helpers import build_ws, install_theme, use_theme

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import look_shots as L                                  # noqa: E402

FALLBACK = ("mint-light_handheld_home-fallback", "mint-light_handheld_settings-fallback",
            "mint-light_desktop_fallback")
RGB, PAINT = 0x1000000, 0x2000000             # moy_theme.h's colour value ranges


def test_a_colours_only_package_takes_moys_geometry_flat(tmp_path):
    ws = build_ws(tmp_path)
    ws.look.set_theme("moy", persist=False, variant="light")
    day = ui_draw.depth()
    use_theme(ws, "mint", "light")
    mint = ui_draw.depth()
    assert mint[12], "the window frame is the depth's"
    # the radii, corners, steps, edges, the frame and the bars' heights
    for i in (0, 1, 2, 3, 4, 6, 12, 13, 14):
        assert mint[i] == day[i], i
    assert day[5] and not mint[5], "flat: no shadow"


def test_its_look_sets_no_colour_and_no_backdrop():
    """Every colour and paint of Mint's look is the model's own default, as
    under a look that sets geometry alone, and it has no backdrop; Moy's
    sets colours and a backdrop of its own."""
    day = moy_ui.theme_look("moy", True)[0]
    assert moy_ui.theme_backdrop("moy", True, "desk")
    install_theme("mint")
    mint, mint_paints = moy_ui.theme_look("mint", True)[:2]
    for surface in ("home", "desk"):
        assert moy_ui.theme_backdrop("mint", True, surface) == b"", surface
    assert moy_ui.theme_install(json.dumps(       # the one slot: Mint goes
        {"format": 2, "name": "bare", "look": {"win_r": 12}}).encode())[0] == 0
    bare = moy_ui.theme_look("bare", True)[0]
    assert not mint_paints
    lit = [i for i, v in enumerate(day) if RGB <= v < PAINT and v != bare[i]]
    assert lit, "Moy Day sets colours of its own"
    for i in lit:
        assert mint[i] == bare[i], i                  # each derives from Mint's roles


def test_the_fallback_scenes_are_built(tmp_path):
    rows = L.run(str(tmp_path), list(FALLBACK), quiet=True)
    assert sorted(r[0] for r in rows) == sorted(FALLBACK)
    for name, built, missing in rows:
        assert built, (name, missing)
