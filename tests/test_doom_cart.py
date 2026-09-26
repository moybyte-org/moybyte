"""Doom, built by the recipe, on the host: the frames a board is held to.

Opt-in by construction. experiments/wasm_aot/doom/build_cart.py builds the cart
on the developer's machine -- doomgeneric is GPL and the shareware WAD's
licence forbids redistribution, so neither is ever in this repository or in
CI -- and every test here skips, saying so, until it has. Once built, the cart
runs on the host twin (runtime/wasm_binding.py: the boards' import table over
WAMR) and these hold what the on-glass suites compare a board against: the
frame Doom renders at a named gametic does not depend on how often the host
draws, and every level of the shareware episode loads and plays at the zone
the cart was built with.
"""

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_spec = importlib.util.spec_from_file_location(
    "doom_frames", str(ROOT / "experiments" / "wasm_aot" / "doom" / "frames.py"))
frames = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(frames)


def _built():
    return os.path.isfile(os.path.join(frames.CART, "main.wasm"))


def _host():
    try:
        from runtime.wasm_binding import HostWasmRun, why_unavailable
    except ImportError:                              # pragma: no cover
        return "no runtime.wasm_binding"
    return None if HostWasmRun.available() else (why_unavailable() or "no binding")


pytestmark = [
    pytest.mark.skipif(not _built(), reason=(
        "the recipe's Doom cart is not built (python3 experiments/wasm_aot/doom/"
        "build_cart.py): doomgeneric and the WAD are never in the repository "
        "or CI")),
    pytest.mark.skipif(_built() and _host() is not None,
                       reason="no host wasm binding: %s" % _host()),
]

TICS = 6000


def test_the_frame_at_a_gametic_does_not_depend_on_the_draw_cadence():
    every, img = frames.host_crcs(tics=TICS, draw_every=1)
    third, _ = frames.host_crcs(tics=TICS, draw_every=3)
    assert len(every) >= TICS // frames.CRC_EVERY
    assert every == third
    assert not frames.error(img)


@pytest.mark.parametrize("level", range(1, 10))
def test_every_shareware_level_loads_and_plays_at_the_carts_zone(level):
    """Warped to E1M<level> and played for 600 tics at the zone the cart was
    built with -- turning the whole time and walking in bursts, so the view
    sweeps the level's textures and sprites -- the level loads, nothing
    traps, the zone never runs dry."""
    with open(os.path.join(frames.CART, "config.json")) as f:
        zone = json.load(f)["zone"]
    right, up = 1 << 1, 1 << 2
    _crcs, img = frames.host_crcs(
        tics=600, cfg={"args": "-warp 1 %d -skill 3" % level},
        buttons=lambda t: right | (up if (t // 45) % 2 else 0))
    assert img[frames.PM_MAP] == 10 + level
    assert img[frames.PM_ZONE_MB] == zone
    assert img[frames.PM_ZONE_LOW] > 0
    assert not frames.error(img)


def test_the_named_transitions_are_the_attract_loops_own():
    assert frames.excluded(9439 + 61) == "the title gives way to demo 3 (E1M7)"
    assert frames.excluded(9000) is None
    assert all(frames.excluded(t) for t in frames.TRANSITIONS)
