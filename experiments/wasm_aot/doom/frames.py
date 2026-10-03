"""Doom's frame CRCs: what the recipe's cart records, and the host run a board
is held to.

dg_cart.c renders the frame of every CRC_EVERY-th gametic at that tic and
writes its CRC (the 320 x 200 indices, then the 768-byte palette) into pmem,
so the frame at a gametic is the same on every host whatever the host's draw
cadence. `host_crcs` runs the cart on the host twin (runtime/wasm_binding.py,
the boards' own import table over WAMR) and returns those CRCs;
tests/test_doom_cart.py and the on-glass suites compare them.

The attract loop's melts are named here. A CRC whose gametic falls within
MELT_TICS after one is left out of every comparison: the #158 spike's one
mismatch in 23 was a level transition. This glue runs no game tic during a
melt and renders each CRC frame at its tic, so its host runs agree through
the transitions at every cadence; the exclusion stays, by name, so a board
that disagrees there says which transition it was rather than failing.
"""

import json
import os
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
CART = os.path.join(HERE, "out", "doom.moy")

# pmem, as dg_cart.c lays it out
PM_CRC_LAST, PM_CRC, CRC_EVERY = 0, 1, 500
PM_ERROR, PM_ERROR_SLOTS = 200, 32
PM_ZONE_MB, PM_ZONE_LOW, PM_HEAP_FREE, PM_GAMETIC, PM_MAP = 240, 241, 242, 243, 244

# The attract loop's melts, by the gametic whose state change starts them.
TRANSITIONS = {
    5199: "demo 1 (E1M5) ends into the credit page",
    5400: "the credit page gives way to demo 2 (E1M3)",
    9238: "demo 2 (E1M3) ends into the title",
    9439: "the title gives way to demo 3 (E1M7)",
    11573: "demo 3 (E1M7) ends into the credit page",
    11744: "the credit page gives way to demo 1 (E1M5)",
}
MELT_TICS = 70


def excluded(gametic):
    """The transition a CRC's gametic falls in, by name, or None."""
    for start, name in TRANSITIONS.items():
        if start <= gametic < start + MELT_TICS:
            return name
    return None


def crcs(img):
    """{gametic: crc} recorded in a pmem image (256 ints)."""
    n = img[PM_CRC_LAST]
    return {(k + 1) * CRC_EVERY: img[PM_CRC + k] & 0xFFFFFFFF for k in range(n)}


def error(img):
    """A fatal I_Error's text recorded in a pmem image, or ''."""
    raw = b"".join(int(v & 0xFFFFFFFF).to_bytes(4, "little")
                   for v in img[PM_ERROR:PM_ERROR + PM_ERROR_SLOTS])
    return raw.rstrip(b"\0").decode("latin-1")


def host_crcs(cart=CART, tics=6000, draw_every=1, cfg=None, buttons=None):
    """Run `cart` on the host twin until gametic `tics`: (crcs, pmem image).
    `draw_every` draws one tick in that many, as a slow host's tick model
    would; the CRCs must not care. `buttons(tick)` is the button bitmask
    (SPEC.md 7.3's order) held on that tick."""
    import sys
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)
    from runtime import wasm_binding as wb
    with open(os.path.join(cart, "manifest.json")) as f:
        man = json.load(f)
    with open(os.path.join(cart, "config.json")) as f:
        conf = json.load(f)
    conf.update(cfg or {})
    run = wb.HostWasmRun(bytearray(320 * 240 * 2), 320, 240, cart, "main.wasm",
                         man["memory"], cfg=conf)
    try:
        # The host's time() is the snapshot's clock plus the real milliseconds
        # spent inside the current call, and _init reads it. A first tick
        # whose clock is behind what _init read runs Doom's millisecond clock
        # backwards, which its tic counter takes for a wrap: no tic runs
        # again, and this loop never ends. So the clock starts past _init.
        began = time.monotonic()
        err = run.init()
        frame = 0
        clock = int((time.monotonic() - began) * 1000) + 1
        while not err:
            img = list(run.pmem()[1])
            if img[PM_GAMETIC] >= tics:
                break
            clock += 1000 // 30
            run.snap[wb.SNAP_TIME_MS] = clock
            run.snap[wb.SNAP_BTN] = buttons(frame) if buttons else 0
            err = run.tick(1 / 30, draw=(frame % draw_every == 0))
            frame += 1
        if err:
            raise RuntimeError("%s (pmem error %r)" % (err, error(list(run.pmem()[1]))))
        img = list(run.pmem()[1])
        return crcs(img), img
    finally:
        run.close()
