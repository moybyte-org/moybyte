"""A compiled cart's frame shown from its own memory: the shell's half.

libmoy's frame hand-off (moy-spec libmoy/include/moy_wasm.h, `frame`) lets a
blit leave its frame where the cart made it instead of writing the game
canvas, and a banded board's flush snapshots and resolves it band by band
(native/moy_flush/moy_fold.h, the frame fold; tests/test_flush_fold.py has
the C). What the shell owes that path is ORDER, and it is what this file
drives through the real frame walk on the host:

  * the composite point hands an owed frame to the system canvas
    (`present_frame`) and, when that takes it, composites nothing;
  * a frame the system canvas declines is SETTLED into the game canvas
    first, so the ordinary composite reads the pixels the blit would have
    written;
  * anything that draws on the game canvas over the frame -- the hold-to-exit
    toast, a crash panel, a windowed desk's player window -- settles it before
    it draws, so correctness never depends on the fast path;
  * the FPS chip and the perf HUD line, opaque rects, are declared instead
    (`patch_cart_frame`): the frame still goes to the glass from the cart's
    memory and the flush takes those rects from the game canvas; a frame
    settled after they drew keeps them;
  * the geometry handed over is the composite's own: the shared canvas at
    (0, 0) x1, a separate system canvas at the WM's viewport.

The CartFrame here is a recording double; the device one is
device/moycore_glue.CartFrame (tests/test_moycore_glue.py), and the canvas
half is DeviceCanvas.present_frame, driven below over a fake compositor.
"""

from array import array
from types import SimpleNamespace

from runtime import host_app
from runtime.console import Workstation
from runtime.wm_windowed import WindowedWM

DT = 1.0 / 30


class FakeCartFrame:
    """Owes a frame whenever `owed` is set -- which a test does before each
    loop frame, standing in for the cart's blit."""

    MAX_PATCHES = 4

    def __init__(self, w=320, h=240):
        self.w, self.h = w, h
        self.lut = bytearray(512)
        self.owed = False
        self.log = []
        self.rects = []
        self.nrects = 0

    def take(self):
        self.log.append("take")
        return memoryview(bytearray(self.w * self.h)) if self.owed else None

    def patch(self, x, y, w, h):
        if self.nrects >= self.MAX_PATCHES:
            return False
        self.log.append(("patch", x, y, w, h))
        self.rects.append((x, y, w, h))
        self.nrects += 1
        return True

    def settle(self, canvas=None):
        self.log.append(("settle", canvas is not None) if self.nrects else "settle")
        self.owed = False
        self.nrects = 0

    def presented(self, kept, off):
        self.log.append("presented")
        self.owed = False


def _playing(tmp_path, **kw):
    ws = host_app.build_workstation(str(tmp_path / "carts"), **kw)
    drv = host_app.ConsoleDriver(ws)
    ws.launcher.sel = 0
    ws.open()
    ws._splash_until = None
    ws._toast_until = 0
    ws.show_fps = False
    for _ in range(3):
        drv.frame(DT)
    ws._toast_until = 0
    return ws, drv


def _spy(ws, took=True):
    """Record present_frame's arguments and the ordinary composite."""
    calls = []
    sc = ws.sys_canvas

    def present(cf, view, gc, ox, oy, scale, src):
        assert gc is ws.canvas
        calls.append(("present", gc.w, gc.h, ox, oy, scale, src, len(view),
                      cf.nrects))
        if took:
            cf.presented(None, 0)
        return took

    sc.present_frame = present
    wm = ws.wm
    real = wm.composite_game

    def composite():
        calls.append(("composite",))
        return real()

    wm.composite_game = composite
    return calls


def _frame(ws, drv, cf):
    cf.owed = True
    cf.log.clear()
    drv.frame(DT)


def test_a_frame_the_system_canvas_takes_is_not_composited(tmp_path):
    ws, drv = _playing(tmp_path)
    cf = ws.cart_frame = FakeCartFrame()
    calls = _spy(ws)
    _frame(ws, drv, cf)
    assert calls == [("present", 320, 240, 0, 0, 1, None, 320 * 240, 0)]
    assert cf.log == ["take", "presented"]


def test_a_separate_system_canvas_gets_the_composites_geometry(tmp_path):
    ws, drv = _playing(tmp_path, sys_size=(480, 320))
    assert ws.sys_canvas is not ws.canvas
    cf = ws.cart_frame = FakeCartFrame()
    calls = _spy(ws)
    _frame(ws, drv, cf)
    ox, oy, scale = ws.wm.viewport()
    assert calls == [("present", 320, 240, ox, oy, scale, None, 320 * 240, 0)]
    assert (ox, oy, scale) == (80, 40, 1)


def test_a_frame_it_declines_is_settled_then_composited(tmp_path):
    ws, drv = _playing(tmp_path, sys_size=(480, 320))
    cf = ws.cart_frame = FakeCartFrame()
    calls = _spy(ws, took=False)
    _frame(ws, drv, cf)
    assert [c[0] for c in calls] == ["present", "composite"]
    assert cf.log == ["take", "settle"]


def test_nothing_owed_composites_as_ever(tmp_path):
    ws, drv = _playing(tmp_path, sys_size=(480, 320))
    cf = ws.cart_frame = FakeCartFrame()
    calls = _spy(ws)
    cf.log.clear()
    drv.frame(DT)                       # no blit this frame
    assert calls == [("composite",)]
    assert cf.log == ["take"]


def test_the_fps_chip_rides_the_frame_as_a_patch(tmp_path):
    ws, drv = _playing(tmp_path)
    ws.show_fps = True
    ws.perf_hud = True
    drv.frame(DT)
    cf = ws.cart_frame = FakeCartFrame()
    calls = _spy(ws)
    _frame(ws, drv, cf)
    # The chip and the HUD line above it are opaque rects over the frame: they
    # are declared, drawn on the game canvas, and the frame is still shown
    # from the cart's memory with both taken from the canvas.
    patches = [e for e in cf.log if isinstance(e, tuple) and e[0] == "patch"]
    assert len(patches) == 2
    assert "settle" not in cf.log and cf.log[-2:] == ["take", "presented"]
    assert calls[-1][0] == "present" and calls[-1][-1] == 2
    for _p, x, y, w, h in patches:
        assert 0 <= x and x + w <= 320 and 0 <= y and y + h <= 240


def test_a_frame_settled_after_the_chip_keeps_it(tmp_path):
    ws, drv = _playing(tmp_path)
    ws.show_fps = True
    drv.frame(DT)
    cf = ws.cart_frame = FakeCartFrame()
    _spy(ws, took=False)
    _frame(ws, drv, cf)
    assert ("settle", True) in cf.log     # the canvas is handed over to keep


def test_the_hold_to_exit_toast_settles_the_frame_first(tmp_path):
    ws, drv = _playing(tmp_path)
    cf = ws.cart_frame = FakeCartFrame()
    _spy(ws)
    drawn = []
    real = ws.player._draw_hold_progress

    def hold():
        drawn.append(list(cf.log))
        return real()

    ws.player._draw_hold_progress = hold
    real_input = ws.player.handle_input

    def holding(i):
        out = real_input(i)
        ws.player._home_holding = True      # BACKSPACE held down
        return out

    ws.player.handle_input = holding
    _frame(ws, drv, cf)
    assert drawn and drawn[0][-1] == "settle"


def test_the_desk_world_leaves_the_frame_to_the_player_window():
    wm = SimpleNamespace(_order=["desktop"])
    assert WindowedWM.present_frame(wm, FakeCartFrame(), memoryview(b"")) is False


def test_no_cart_frame_is_one_attribute_read():
    ws = SimpleNamespace(cart_frame=None,
                         wm=SimpleNamespace(composite_game=lambda: "composited"))
    assert Workstation._composite_game(ws) == "composited"
    Workstation.settle_cart_frame(ws)


# -- the device canvas's half (device/device_canvas.py) ------------------------


class FakeFoldComp:
    frames_supported = True

    def __init__(self, refuse=False):
        self.refuse = refuse
        self.calls = []

    def fold_fence(self):
        self.calls.append(("fold_fence",))

    def frame_fold(self, *a):
        self.calls.append(("frame_fold",) + a)
        if self.refuse:
            raise ValueError("fold geometry")
        return 13


class ScratchFrame(FakeCartFrame):
    PATCH_BYTES = 8192

    def __init__(self, room=True):
        FakeCartFrame.__init__(self)
        self.room = room
        self.asked = []
        self.kept = None
        self.rects = array("h", bytes(32))

    def scratch(self, n):
        self.asked.append(n)
        return bytearray(n) if self.room else None

    def presented(self, kept, off):
        self.kept = (kept, off)


def _canvas(comp):
    from device.device_canvas import DeviceCanvas
    return DeviceCanvas, SimpleNamespace(_comp=comp, _snap_live=False)


GC = SimpleNamespace(w=320, h=240, _buf=bytearray(320 * 240 * 2))


def test_the_device_canvas_folds_a_palette_frame():
    comp = FakeFoldComp()
    DC, cv = _canvas(comp)
    cf = ScratchFrame()
    view = memoryview(bytearray(320 * 240))
    assert DC.present_frame(cv, cf, view, GC, 80, 40, 1, None) is True
    assert comp.calls[0] == ("fold_fence",)
    (_f, v, fmt, lut, scr, gw, gh, sx, sy, vw, vh, ox, oy, scale, rects,
     canvas) = comp.calls[1]
    assert v is view and fmt == 2 and lut is cf.lut
    assert (gw, gh, sx, sy, vw, vh, ox, oy, scale) == (320, 240, 0, 0, 320, 240,
                                                       80, 40, 1)
    assert rects is None and canvas is GC._buf
    # The scratch holds the frame's aligned span, its palette and the patches.
    assert cf.asked == [320 * 240 + 128 + 512 + 8192] and len(scr) == cf.asked[0]
    assert cf.kept == (scr, 13)
    assert cv._snap_live is True        # sync_back fences it before the next hook


def test_the_device_canvas_folds_a_565_frame_through_a_view():
    comp = FakeFoldComp()
    DC, cv = _canvas(comp)
    cf = ScratchFrame()
    cf.rects[0:4] = array("h", [1, 2, 3, 4])
    cf.nrects = 1
    view = memoryview(bytearray(2 * 320 * 240))
    assert DC.present_frame(cv, cf, view, GC, 16, 8, 2,
                            (8, 4, 144, 112)) is True
    call = comp.calls[1]
    assert call[2] == 1 and call[3] is None
    assert call[5:14] == (320, 240, 8, 4, 144, 112, 16, 8, 2)
    assert list(call[14]) == [1, 2, 3, 4]


def test_the_device_canvas_declines_without_room_or_a_shape():
    for comp, cf in ((FakeFoldComp(refuse=True), ScratchFrame()),
                     (FakeFoldComp(), ScratchFrame(room=False))):
        DC, cv = _canvas(comp)
        view = memoryview(bytearray(320 * 240))
        assert DC.present_frame(cv, cf, view, GC, 0, 0, 1, None) is False
        assert cv._snap_live is False and cf.kept is None


def test_only_a_compositor_with_the_frame_fold_presents_frames():
    DC, cv = _canvas(FakeFoldComp())
    assert DC.presents_frames.fget(cv) is True
    for comp in (SimpleNamespace(), SimpleNamespace(frames_supported=False)):
        assert DC.presents_frames.fget(SimpleNamespace(_comp=comp)) is False
