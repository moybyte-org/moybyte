# #190 fold driver: the REAL moy_compositor + REAL moy_gfx kernels on unix
# MicroPython, with a fake bus (instant DMA) + fake moy_alloc/machine.
# For each geometry: arm a fold, flush, reassemble the tx_color bands, and
# byte-compare against the reference composite built the blit_game way.
import sys

sys.path.insert(0, sys.argv[1])      # the T-Deck modules dir


class _FakeTimer:
    PERIODIC = 1

    def __init__(self, *_a, **_k):
        pass

    def init(self, *_a, **_k):
        pass

    def deinit(self):
        pass


class _Mod:
    pass


machine = _Mod()
machine.Timer = _FakeTimer
sys.modules["machine"] = machine

moy_alloc = _Mod()
moy_alloc.malloc_dma = lambda n, caps=0: bytearray(n)
sys.modules["moy_alloc"] = moy_alloc

lcd_bus = _Mod()
lcd_bus.MEMORY_SPIRAM = 1
lcd_bus.MEMORY_DMA = 2
sys.modules["lcd_bus"] = lcd_bus

import moy_gfx  # noqa: E402  (real, from the usermod build)


class FakeBus:
    """Records tx_color payloads; completes 'DMA' instantly via the callback."""

    def __init__(self):
        self.cb = None
        self.bands = []              # (cmd, y0, y1, payload bytes)

    def register_callback(self, cb):
        self.cb = cb

    def tx_param(self, *_a, **_k):
        pass

    def tx_color(self, cmd, buf, x0, y0, x1, y1, rot, last):
        self.bands.append((cmd, y0, y1, bytes(buf)))
        if self.cb is not None:
            self.cb()


import moy_compositor  # noqa: E402

W, H = 320, 240


def reference(scr, vw, vh, ox, oy, scale):
    """The composite blit_game would have drawn: black + scaled game rect."""
    ref = bytearray(W * H * 2)
    moy_gfx.fill(ref, W * H, 0)
    moy_gfx.blit565_scale(ref, W, H, ox, oy, scr, vw, vh, scale)
    return bytes(ref)


def reassemble(bands):
    out = bytearray(W * H * 2)
    for _cmd, y0, y1, payload in bands:
        rb = W * 2
        out[y0 * rb:y0 * rb + len(payload)] = payload
    return bytes(out)


def scratch(vw, vh):
    s = bytearray(vw * vh * 2)
    for i in range(len(s)):
        s[i] = (i * 31 + (i >> 8) * 7) & 0xFF
    return s


CASES = [
    ("celeste_view", 128, 120, 32, 0, 2, 40),    # pillars only, 6 even bands
    ("small_1x", 128, 128, 96, 56, 1, 40),       # all four bezels, scale 1
    ("full_2x", 160, 120, 0, 0, 2, 40),          # no bezel at all
    ("short_band", 128, 120, 32, 0, 2, 36),      # 6x36 + 24-row final band
]

for name, vw, vh, ox, oy, scale, strip_h in CASES:
    import gc
    gc.collect()                     # each case allocates two 150KB buffers
    bus = FakeBus()
    comp = moy_compositor.Compositor(bus, W, H, strip_h=strip_h)
    assert comp.bounce_flush, name + ": bounce flush did not come up"
    assert comp.fold_supported, name + ": fold not supported"
    scr = scratch(vw, vh)
    ref = reference(scr, vw, vh, ox, oy, scale)

    # 1) folded flush: bands must reassemble into the reference EXACTLY.
    bus.bands = []
    comp.arm_scale_fold(scr, vw, vh, ox, oy, scale)
    comp.flush()
    got = reassemble(bus.bands)
    n_bands = len(bus.bands)
    if got != ref:
        diff = sum(1 for a, b in zip(got, ref) if a != b)
        print("FAIL", name, "fold diff bytes:", diff, "bands:", n_bands)
        sys.exit(1)
    assert comp.fold_count == 1, name

    # 2) disarm: the deferred composite must land in the BACK buffer, and the
    #    following (unarmed) flush must ship the same reference pixels.
    comp.arm_scale_fold(scr, vw, vh, ox, oy, scale)
    comp.disarm_scale_fold()
    assert bytes(comp._back) == ref, name + ": disarm composite wrong"
    bus.bands = []
    comp.flush()
    got = reassemble(bus.bands)
    if got != ref:
        print("FAIL", name, "post-disarm flush differs")
        sys.exit(1)

    # 3) an UNARMED flush after a folded one copies the root again (the fold
    #    is one-shot): draw a distinct root frame, flush, expect it verbatim.
    fb = comp.framebuffer()
    moy_gfx.fill(fb, W * H, 0x1234)
    bus.bands = []
    comp.flush()
    got = reassemble(bus.bands)
    exp = bytes(bytearray(comp._front))
    if got != exp:
        print("FAIL", name, "unarmed flush not a root copy")
        sys.exit(1)

    # 4) fold_fence is a no-op when the flush fully fed (instant DMA here).
    comp.fold_fence()
    print("OK", name, "bands=%d" % n_bands)

print("FOLD_DRIVER_DONE")
