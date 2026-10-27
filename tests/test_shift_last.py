"""`shift_last` and `copy_last_into`, EXECUTED on every canvas
(docs/scroll_and_text_2026-10.md §3.2, §11, §13).

`shift_last(x, y, w, h, dx, dy)` moves the rect of the buffer the last
presented frame drew into by (dx, dy) inside the rect, into the buffer this
frame draws into; `copy_last_into` copies a rect of that buffer into a layer.
Every case below is held byte for byte to a per-pixel reference -- the rect
clipped to the buffer, a pixel kept only where its source is inside the rect
too, everything else (the exposed band included) what the destination held:

- the C kernels (moy_gfx's mg_shift_rect / mg_copy_block) through the host's
  ctypes binding, across buffers and in place, every sign of the move and
  every edge clip;
- a canvas on one, two and three buffers presented in turn (the host
  compositor's `last` ring), its layers (one persistent buffer, in place),
  and the viewport (buffer coordinates, never the camera's);
- the P4s' compositors with the 2D-DMA emulated two ways -- the copy landing
  at its submit, and only at the fence -- plus a transport without the engine
  and one that refuses the block: the host binding (`runtime/glass_binding.py`)
  and the boards' C binding (`native/moy_glass/modmoy_glass.c`, compiled into
  the desktop MicroPython) run the SAME scenario script, the S3s' banded
  compositor in the C binding beside them;
- the Guition's refusal while a rotate flies, and the shifted block noted as
  the frame's damage.
"""

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

from runtime import gfx_binding, glass_binding, host_canvas         # noqa: E402
from unix_mp import require_unix_mp                                # noqa: E402

W, H = 40, 24
RECTS = ((5, 4, 20, 12), (-3, -2, 15, 10), (30, 15, 20, 15), (0, 0, 40, 24),
         (45, 0, 5, 5), (5, 5, 0, 8), (0, 23, 40, 1))
MOVES = ((0, 0), (0, 1), (0, -1), (0, 7), (0, -7), (3, 0), (-3, 0), (2, -5),
         (-4, 6), (0, 11), (0, -11), (0, 12), (0, -30), (19, 0), (25, 3), (-1, 1))


def ref_shift(before, src, w_, h_, x, y, w, h, dx, dy):
    """The reference: `before` with the rect's pixels taken from `src` at
    (p - d) wherever that source lies inside the rect too."""
    out = bytearray(before)
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(w_, x + w), min(h_, y + h)
    for py in range(y0, y1):
        for px in range(x0, x1):
            sx, sy = px - dx, py - dy
            if x0 <= sx < x1 and y0 <= sy < y1:
                i, j = (py * w_ + px) * 2, (sy * w_ + sx) * 2
                out[i:i + 2] = src[j:j + 2]
    return out


def ref_copy(before, stride, src, sw, sh, x, y, w, h, lx, ly):
    out = bytearray(before)
    rows = len(before) // 2 // stride
    for r in range(h):
        for c in range(w):
            sx, sy, dx, dy = x + c, y + r, lx + c, ly + r
            if 0 <= sx < sw and 0 <= sy < sh and 0 <= dx < stride and 0 <= dy < rows:
                i, j = (dy * stride + dx) * 2, (sy * sw + sx) * 2
                out[i:i + 2] = src[j:j + 2]
    return out


def noise(n, seed):
    out = bytearray(n)
    s = seed * 2654435761 % 4294967296 or 1
    for i in range(n):
        s = (s * 1103515245 + 12345) % 2147483648
        out[i] = (s >> 16) & 255
    return out


# -- the kernels ------------------------------------------------------------------


@pytest.mark.parametrize("rect", RECTS)
def test_the_kernel_shifts_across_buffers_and_in_place(rect):
    for i, (dx, dy) in enumerate(MOVES):
        src = noise(W * H * 2, i + 1)
        dst = noise(W * H * 2, i + 100)
        want = ref_shift(dst, src, W, H, *rect, dx, dy)
        gfx_binding.shift_rect(dst, bytes(src), W, *rect, dx, dy)
        assert dst == want, (rect, dx, dy)
        one = noise(W * H * 2, i + 7)
        want = ref_shift(one, bytes(one), W, H, *rect, dx, dy)
        gfx_binding.shift_rect(one, one, W, *rect, dx, dy)
        assert one == want, ("in place", rect, dx, dy)
        two = noise(W * H * 2, i + 7)
        gfx_binding.scroll_rect(two, W, *rect, dx, dy)
        assert two == one


def test_the_clip_names_the_block_the_kernel_moves():
    for rect in RECTS:
        for dx, dy in MOVES:
            b = gfx_binding.shift_clip(W, H, *rect, dx, dy)
            src, dst = noise(W * H * 2, 3), bytearray(W * H * 2)
            gfx_binding.shift_rect(dst, src, W, *rect, dx, dy)
            want = bytearray(W * H * 2)
            if b is not None:
                x, y, sx, sy, w, h = b
                for r in range(h):
                    a, s = ((y + r) * W + x) * 2, ((sy + r) * W + sx) * 2
                    want[a:a + 2 * w] = src[s:s + 2 * w]
            assert dst == want, (rect, dx, dy, b)


def test_the_kernel_copies_a_block_clipped_to_both_pictures():
    src = noise(W * H * 2, 5)
    for x, y, w, h, lx, ly in ((0, 0, 30, 20, 0, 0), (10, 5, 30, 20, 0, 0),
                               (-5, -3, 30, 20, 2, 1), (35, 20, 30, 20, 0, 0),
                               (0, 0, 30, 20, -4, -6), (3, 3, 8, 6, 25, 15),
                               (0, 0, 0, 5, 0, 0), (50, 50, 4, 4, 0, 0)):
        lay = noise(30 * 20 * 2, 9)
        want = ref_copy(lay, 30, src, W, H, x, y, w, h, lx, ly)
        gfx_binding.copy_block(lay, 30, bytes(src), W, lx, ly, x, y, w, h)
        assert lay == want, (x, y, w, h, lx, ly)


# -- a canvas, on one, two and three buffers presented in turn ----------------------


@pytest.mark.parametrize("bufs", [1, 2, 3])
def test_a_canvas_shifts_out_of_the_last_presented_buffer(bufs):
    host_canvas.install()
    cv = host_canvas.make_system_canvas(W, H)
    held = []
    k = 0
    for rect in RECTS:
        for dx, dy in MOVES:
            k += 1
            cv._buf[:] = noise(W * H * 2, k)         # this frame's pixels
            held.append(bytes(cv._buf))              # ...presented
            if bufs > 1:
                cv._comp.last = held[-1]
                cv._buf[:] = held[-bufs] if len(held) >= bufs else held[0]
            before = bytes(cv._buf)
            assert cv.shift_last(*rect, dx, dy) is True
            cv.shift_fence()
            assert bytes(cv._buf) == ref_shift(before, held[-1], W, H, *rect, dx, dy)


def test_a_layer_shifts_in_place_and_ignores_the_viewport():
    host_canvas.install()
    cv = host_canvas.make_system_canvas(W, H)
    lay = cv.new_layer(W, H)
    for i, (dx, dy) in enumerate(MOVES):
        for c in (cv, lay):
            c._buf[:] = noise(W * H * 2, i + 30)
            before = bytes(c._buf)
            assert c.shift_last(4, 3, 25, 15, dx, dy)
            assert bytes(c._buf) == ref_shift(before, before, W, H, 4, 3, 25, 15, dx, dy)
    cv.set_viewport(10, 6, 20, 12)
    cv.camera(3, 2)
    cv._buf[:] = noise(W * H * 2, 99)
    before = bytes(cv._buf)
    assert cv.shift_last(0, 0, W, H, 0, 5)       # the buffer's coordinates
    assert bytes(cv._buf) == ref_shift(before, before, W, H, 0, 0, W, H, 0, 5)


def test_copy_last_into_brings_a_layer_up_from_the_last_frame():
    host_canvas.install()
    cv = host_canvas.make_system_canvas(W, H)
    lay = cv.new_layer(30, 20)
    for bufs in (1, 2):
        cv._buf[:] = noise(W * H * 2, 40 + bufs)
        last = bytes(cv._buf)
        if bufs > 1:
            cv._comp.last = last
            cv._buf[:] = noise(W * H * 2, 50)
        for args in ((5, 2, 30, 20), (5, 2, 30, 20, 0, 0), (0, 0, 10, 8, 20, 12),
                     (-4, -4, 30, 20, 3, 3), (30, 10, 30, 20)):
            lay._buf[:] = noise(30 * 20 * 2, 60)
            before = bytes(lay._buf)
            assert cv.copy_last_into(lay, *args)
            x, y, w, h = args[:4]
            lx, ly = (args[4], args[5]) if len(args) > 4 else (0, 0)
            assert bytes(lay._buf) == ref_copy(before, 30, last, W, H, x, y, w, h, lx, ly)


def test_a_canvas_without_a_last_buffer_has_no_copy_last_into():
    host_canvas.install()
    from device_canvas import SystemCanvas
    lay = host_canvas.make_system_canvas(W, H).new_layer(8, 8)
    assert getattr(lay, "copy_last_into", None) is None
    web = SystemCanvas(_OneBuffer(W, H))
    assert getattr(web, "copy_last_into", None) is None
    web._buf[:] = noise(W * H * 2, 3)
    before = bytes(web._buf)
    assert web.shift_last(2, 2, 30, 18, -3, 4)
    assert bytes(web._buf) == ref_shift(before, before, W, H, 2, 2, 30, 18, -3, 4)


class _OneBuffer:
    """The browser's compositor shape (firmware/web_runner/web_canvas.py):
    one buffer, no last frame of its own."""

    def __init__(self, w, h):
        self._w, self._h = w, h
        self._buf = bytearray(w * h * 2)

    def size(self):
        return (self._w, self._h)

    def framebuffer(self):
        return self._buf

    back_buffer = framebuffer

    def gfx(self):
        return gfx_binding


# -- the compositors: one scenario script, the host binding and the boards' C --------

SCENARIOS = r'''
W, H = 40, 24
RECTS = %(rects)r
MOVES = %(moves)r


def noise(n, seed):
    out = bytearray(n)
    s = seed * 2654435761 %% 4294967296 or 1
    for i in range(n):
        s = (s * 1103515245 + 12345) %% 2147483648
        out[i] = (s >> 16) & 255
    return out


def ref_shift(before, src, w_, h_, x, y, w, h, dx, dy):
    out = bytearray(before)
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(w_, x + w), min(h_, y + h)
    for py in range(y0, y1):
        for px in range(x0, x1):
            sx, sy = px - dx, py - dy
            if x0 <= sx < x1 and y0 <= sy < y1:
                i, j = (py * w_ + px) * 2, (sy * w_ + sx) * 2
                out[i:i + 2] = src[j:j + 2]
    return out


def ref_copy(before, stride, src, sw, sh, x, y, w, h, lx, ly):
    out = bytearray(before)
    rows = len(before) // 2 // stride
    for r in range(h):
        for c in range(w):
            sx, sy, dx, dy = x + c, y + r, lx + c, ly + r
            if 0 <= sx < sw and 0 <= sy < sh and 0 <= dx < stride and 0 <= dy < rows:
                i, j = (dy * stride + dx) * 2, (sy * sw + sx) * 2
                out[i:i + 2] = src[j:j + 2]
    return out


def blk(dst, dw, dx, dy, src, sw, sx, sy, w, h):
    for r in range(h):
        a = ((dy + r) * dw + dx) * 2
        b = ((sy + r) * sw + sx) * 2
        dst[a:a + 2 * w] = src[b:b + 2 * w]


class Ppa:
    """moy_ppa's surface: the fences, the rotates (recorded) and the 2D-DMA
    block copy in one of its modes -- None: the transport has none; 'submit':
    it lands at its submit; 'fence': only at fbcpy_wait; 'refuse': it takes
    nothing."""

    def __init__(self, mode):
        self.mode = mode
        self.flag = True
        self.log = []
        self.queued = None
        self.rotates = 0
        if mode is not None:
            self.fbcpy = self._fbcpy
            self.fbcpy_wait = self._fbcpy_wait

    def init(self):
        return True

    def sync(self):
        pass

    def done(self):
        return self.flag

    def wait(self, keep):
        return True

    def snap_wait(self):
        pass

    def stats(self):
        return (0, 0, 0)

    def rotate(self, *a):
        self.rotates += 1
        self.log.append('rotate')

    def rotate_scale(self, *a):
        self.log.append('rotate')

    def _fbcpy(self, dst, dw, dh, dx, dy, src, sw, sh, sx, sy, w, h, nb=False):
        assert 0 <= dx and dx + w <= dw and 0 <= dy and dy + h <= dh
        assert 0 <= sx and sx + w <= sw and 0 <= sy and sy + h <= sh
        assert self.queued is None
        if self.mode == 'refuse':
            return False
        self.log.append('fbcpy')
        job = (dst, dw, dx, dy, src, sw, sx, sy, w, h)
        if self.mode == 'fence' and nb:
            self.queued = job
        else:
            blk(*job)
        return True

    def _fbcpy_wait(self):
        self.log.append('wait')
        if self.queued is not None:
            blk(*self.queued)
            self.queued = None


class Dsi:
    def __init__(self, n, w, h):
        self.WIDTH, self.HEIGHT = w, h
        self._fbs = [bytearray(w * h * 2) for _ in range(n)]
        self._n = n
        self.log = None

    def init(self):
        pass

    def backlight(self, on):
        pass

    def nfbs(self):
        return self._n

    def fb(self, i=0):
        return self._fbs[i]

    def show(self, i):
        if self.log is not None:
            self.log.append('show')
        self.shown = bytes(self._fbs[i])

    def flush(self):
        pass


class Panel:
    WIDTH, HEIGHT = W, H

    def __init__(self, n):
        self._fbs = [bytearray(W * H * 2) for _ in range(n)]

    def init(self, nfbs=2):
        pass

    def nfbs(self):
        return len(self._fbs)

    def fb(self, i):
        return self._fbs[i]

    def drain(self):
        return True

    def kick(self, n):
        pass

    def show(self, n):
        pass

    def backlight(self, on):
        pass


def fence(comp):
    f = getattr(comp, 'shift_fence', None)
    if f is not None:
        f()


def frame(comp):
    comp.flush()
    pp = getattr(comp, 'present_pending', None)
    if pp is not None:
        pp()


def shifts(make, label, w=W, h=H, fresh=True):
    """Every rect and move: a frame drawn and presented, then shift_last
    into the next one, against the reference; with the copy landing at the
    fence, the rect untouched until it."""
    out = []
    comp, ppa = make()
    k = 0
    for rect in RECTS:
        for dx, dy in MOVES:
            k += 1
            if k == 1 and fresh:
                assert not comp.shift_last(*rect, dx, dy), 'a shift before any frame'
            fb = comp.framebuffer()
            fb[:] = noise(w * h * 2, k)
            frame(comp)
            src = bytes(fb)
            now = comp.framebuffer()
            before = bytes(now)
            ok = comp.shift_last(*rect, dx, dy)
            if ppa is not None and ppa.queued is not None:
                landed = bytes(now) == before
                fence(comp)
                ok = ok and landed and ppa.queued is None
            else:
                fence(comp)
            ok = ok and bytes(now) == ref_shift(before, src, w, h, *rect, dx, dy)
            out.append((label, rect, (dx, dy), bool(ok)))
    return out


def presents_fence(make, label):
    """A shift still flying when the frame presents lands before the show."""
    comp, ppa = make()
    fb = comp.framebuffer()
    fb[:] = noise(W * H * 2, 1)
    frame(comp)
    src = bytes(fb)
    now = comp.framebuffer()
    before = bytes(now)
    assert comp.shift_last(2, 3, 30, 15, 0, -4)
    want = ref_shift(before, src, W, H, 2, 3, 30, 15, 0, -4)
    del ppa.log[:]
    frame(comp)
    seen = ppa.log[:]
    return [(label, 'present', seen[:1] == ['wait'] and ppa.queued is None
             and bytes(now) == want)]


def copies_into(make, label):
    out = []
    comp, ppa = make()
    fb = comp.framebuffer()
    fb[:] = noise(W * H * 2, 3)
    frame(comp)
    src = bytes(fb)
    lay = bytearray(30 * 20 * 2)
    for args in ((5, 2, 30, 20), (5, 2, 30, 20, 0, 0), (0, 0, 10, 8, 20, 12),
                 (-4, -4, 30, 20, 3, 3), (30, 10, 30, 20)):
        lay[:] = noise(len(lay), 4)
        before = bytes(lay)
        ok = comp.copy_last_into(lay, 30, *args)
        x, y, w, h = args[:4]
        lx, ly = (args[4], args[5]) if len(args) > 4 else (0, 0)
        out.append((label, 'into', args, bool(ok) and bytes(lay) ==
                    ref_copy(before, 30, src, W, H, x, y, w, h, lx, ly)))
    return out


def run(glass, rot_size):
    out = []
    for n in (1, 2, 3):
        for mode in (None, 'submit', 'fence', 'refuse'):
            def make(n=n, mode=mode):
                ppa = Ppa(mode)
                return glass.DsiCompositor(Dsi(n, W, H), ppa), ppa
            label = 'dsi%%d/%%s' %% (n, mode)
            out += shifts(make, label, fresh=n > 1)
            out += copies_into(make, label)
            if n > 1 and mode == 'fence':
                out += presents_fence(make, label)
    pw, ph = rot_size
    for mode in (None, 'submit', 'fence'):
        def make(mode=mode):
            ppa = Ppa(mode)
            return glass.RotatedCompositor(Dsi(3, pw, ph), ppa), ppa
        out += shifts(make, 'rot/%%s' %% mode, w=ph, h=pw)
        comp, ppa = make()
        fb = comp.framebuffer()
        fb[:] = noise(len(fb), 8)
        frame(comp)
        ppa.flag = False
        stats = comp.shift_stats()
        now = comp.framebuffer()
        before = bytes(now)
        refused = (comp.shift_last(0, 0, ph, pw, 0, 3) is False
                   and comp.shift_stats()[1] == stats[1] + 1 and bytes(now) == before)
        ppa.flag = True
        dmg = comp.damage_stats()[0]
        took = comp.shift_last(0, 32, 64, 32, 0, 3) is True
        comp.shift_fence()
        frame(comp)
        noted = comp.damage_stats()[0] == dmg + 1
        out.append(('rot/%%s' %% mode, 'refusal', refused and took and noted))
    banded = getattr(glass, 'BandedCompositor', None)
    if banded is not None:
        for n in (1, 2, 3):
            def make(n=n):
                return banded(Panel(n), nfbs=n), None
            out += shifts(make, 'banded%%d' %% n)
            out += copies_into(make, 'banded%%d' %% n)
    return out
''' % {"rects": RECTS, "moves": MOVES}

ROT_SIZE = (128, 192)            # the portrait panel; the desk is 192 x 128


def _host_results():
    import types
    glass = types.SimpleNamespace(DsiCompositor=glass_binding.DsiCompositor,
                                  RotatedCompositor=glass_binding.RotatedCompositor)
    env = {}
    exec(SCENARIOS, env)                           # noqa: S102 -- fixed text
    saved = sys.modules.get("moy_gfx")
    sys.modules["moy_gfx"] = gfx_binding
    try:
        return env["run"](glass, ROT_SIZE)
    finally:
        if saved is None:
            sys.modules.pop("moy_gfx", None)
        else:
            sys.modules["moy_gfx"] = saved


def test_the_host_binding_shifts_on_every_compositor_and_dma():
    res = _host_results()
    bad = [r for r in res if not r[-1]]
    assert not bad, bad[:8]
    assert len(res) > 1000


def test_the_boards_c_binding_runs_the_same_scenarios(tmp_path):
    exe = require_unix_mp("moy_glass", "moy_gfx",
                          why="the boards' compositor binding (modmoy_glass.c)")
    script = tmp_path / "shift.py"
    script.write_text(SCENARIOS + "\nimport moy_glass\nfor r in run(moy_glass, %r):\n"
                      "    print(repr(r))\n" % (ROT_SIZE,))
    proc = subprocess.run([exe, "-X", "heapsize=64M", str(script)], capture_output=True,
                          text=True, timeout=300)
    assert proc.returncode == 0, proc.stdout[-2000:] + proc.stderr[-2000:]
    got = [eval(line) for line in proc.stdout.splitlines()]         # noqa: S307
    bad = [r for r in got if not r[-1]]
    assert not bad, bad[:8]
    host = _host_results()
    assert [r[:-1] for r in got if not r[0].startswith("banded")] == [r[:-1] for r in host]
    assert any(r[0].startswith("banded3") for r in got)
