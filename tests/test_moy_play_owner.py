"""A Lua run's pixels are the run's (docs/kernel_cartpath_2026-10.md §3.1),
on the desktop MicroPython: the kernel's Player mints an OWNER row at the
launch, and a layer's pixels and a decoded paint image are BUF rows on loan
to it, counted as cart memory, while the image itself is an IMAGE row whose
handle the cart holds. What each check watches for:

  * the loans exist while the run does, and are the cart's class;
  * the image handle is an IMAGE-kind handle, not an index;
  * the runtime's close gives every loan back, and the run's end leaves no
    cart memory behind -- a run that leaked one would show here first.
"""

import json
import os
import subprocess

from runtime.moyimg import encode_moyimg
from unix_mp import require_unix_mp

DRIVER = r'''
import moycore, moy_play, moy_glass
from array import array

moy_glass.stats()                       # the glass's tables, as a board has them
W, H = 32, 24
fb = bytearray(W * H * 2)
snap = array("i", bytearray(4 * moycore.SNAP_LEN))
aq = array("i", bytearray(4 * (1 + moycore.AQ_SLOTS * moycore.AQ_MAX)))

def cart_rows():
    return sorted((r[1], r[0]) for r in (moy_glass.row(h) for h in moy_glass.rows())
                  if r[5] == 1)

run = moy_play.launch("@CART@", True)
moycore.run_begin(fb, W, H, None, None, None, 0, 0, snap, aq, None, None, None, True)
moycore.image_put("bg", @TEXT@)
print("EXEC", moycore.exec("IMG = __image_handle('bg') LAY = __layer_new(8, 4)"
                           " MISS = __image_handle('nope')", "probe"))
print("HANDLES", moycore.get_global("IMG"), moycore.get_global("LAY"),
      moycore.get_global("MISS"))
print("LIVE", cart_rows())
moycore.close()
print("CLOSED", cart_rows())
moy_play.end(run)
print("ENDED", cart_rows(), moy_glass.stats()[8])
'''


def test_a_lua_runs_pixels_are_loans_to_the_run(tmp_path):
    exe = require_unix_mp(
        "moycore", "moy_play", "moy_glass",
        why="Without it nothing checks that a Lua run's layers and images are "
            "the run's own loans, given back with it.")
    cart = tmp_path / "own.moy"
    cart.mkdir()
    (cart / "manifest.json").write_text(json.dumps({
        "format": "moy-1", "title": "Own", "id": "test.own",
        "runtime": "lua", "main": "main.lua",
        "moybyte": {"type": "game", "permissions": ["graphics"]},
    }))
    (cart / "main.lua").write_text("function _draw() end\n")
    text = encode_moyimg(5, 3, bytes(i % 16 for i in range(15)))
    src = DRIVER.replace("@CART@", str(cart)).replace("@TEXT@", repr(text))
    p = subprocess.run([exe, "-c", src], capture_output=True, text=True, timeout=120)
    assert p.returncode == 0, p.stdout + p.stderr
    by = {l.split(" ", 1)[0]: l.split(" ", 1)[1] for l in p.stdout.splitlines()
          if " " in l}
    assert by["EXEC"] == "None", by
    img, lay, miss = (int(v) for v in by["HANDLES"].split())
    assert (img >> 8) & 0xF == 10, "the image handle is no IMAGE row: %#x" % img
    assert lay == 0 and miss == -1
    # BAKE (2) holds the decoded 5x3 indices, LAYER (1) the 8x4 RGB565 layer.
    assert eval(by["LIVE"]) == [(1, 8 * 4 * 2), (2, 5 * 3 + 1)], by["LIVE"]
    assert eval(by["CLOSED"]) == [], by["CLOSED"]
    rows, cart_bytes = by["ENDED"].rsplit(" ", 1)
    assert eval(rows) == [] and int(cart_bytes) == 0, by["ENDED"]
