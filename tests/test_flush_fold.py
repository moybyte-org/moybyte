"""The #190 flush-bounce scale fold, driven through the REAL machinery.

A small-canvas game frame on the T-Deck skips the root-fb composite: blit_game
snapshots the (cropped) game frame into a flush-private scratch and ARMS the
fold, and the SRAM-bounce pump synthesizes each band (black + one dest-clipped
blit565_scale) instead of memcpy'ing root bands the composite would first have
had to write. tests/data_fold_driver.py runs the REAL moy_compositor + REAL
moy_gfx kernels on a unix-port MicroPython (fake bus with instant DMA, fake
moy_alloc/machine/lcd_bus) and byte-compares reassembled bands against the
reference composite across four geometries -- plus the disarm escape hatch
(the deferred composite an overlay frame forces) and the one-shot latch.

The unix build is `make unix-micropython`; its absence is loud, not silent
(tests/unix_mp.py)."""

import subprocess
import sys
from pathlib import Path

from unix_mp import require_unix_mp

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TDECK = ROOT / "firmware" / "lilygo_t_deck_plus_micropython"


def test_fold_bands_match_the_composite():
    exe = require_unix_mp(
        "moy_gfx",
        why="Without it the fold is checked only by the source-order pins "
            "below, which cannot see a band that reassembles into the wrong "
            "pixels.")
    out = subprocess.run(
        [exe, "-X", "heapsize=16m",
         str(ROOT / "tests" / "data_fold_driver.py"), str(TDECK / "modules")],
        capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr or out.stdout
    assert "FOLD_DRIVER_DONE" in out.stdout, out.stdout


def test_frame_walk_disarms_above_the_game():
    """Source-order pins for the shared console hooks: the composite arms
    (device-side), and the walk disarms before any layer that can paint the
    root above it -- including the deferred-transition loading toast."""
    src = (ROOT / "runtime" / "console.py").read_text()
    walk = src[src.index("_fold_live = False"):]
    assert walk.index("self._composite_game()") \
        < walk.index("disarm_scale_fold") \
        < walk.index("self._draw_loading_toast()")
    # blit_game arms only where the comp supports folding, after the fence.
    dc = (TDECK / "modules" / "device_canvas.py").read_text()
    body = dc[dc.index("def blit_game"):]
    assert body.index("fold_fence") < body.index("arm_scale_fold")
    assert '"fold_supported", False' in body
