"""A compiled cart's frame shown from its own memory: the shell's half.

libmoy's frame hand-off (moy-spec libmoy/include/moy_wasm.h, `frame`) lets a
blit leave its frame where the cart made it instead of writing the game
canvas: a banded board's flush snapshots and resolves it band by band
(native/moy_flush/moy_fold.h, the frame fold; tests/test_flush_fold.py has
the C), and a P4's PPA scales it from there (device/p4_canvas.py; its half is
tests/test_p4_display.py). What the shell owes that path is ORDER, and it is
what this file drives through the real frame walk on the host:

  * the composite point hands an owed frame to the system canvas
    (`present_frame`) and, when that takes it, composites nothing -- the
    fullscreen composite, the play world's over its bezel, and a windowed
    desk's player window;
  * a frame the system canvas declines is SETTLED into the game canvas
    first, so the ordinary composite reads the pixels the blit would have
    written;
  * a frame the cart did not replace is shown again from the kept copy;
  * anything that draws on the game canvas over the frame -- the
    hold-to-exit toast, a crash panel -- settles it before it draws, so
    correctness never depends on the fast path;
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
from ws_helpers import select_first_game  # noqa: E402

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
    select_first_game(ws)
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
        self.rect_views = [None] + [memoryview(self.rects)[:4 * n]
                                    for n in range(1, 5)]

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


# -- the windowed desk (the P4s): the player window shows the frame ------------
#
# On a windowed tier a game plays in the desk's player WINDOW, and the window
# composites the game itself (`_draw_player_window`), so the frame goes to the
# glass from there: the root canvas's `present_frame` takes it, with
# `_blit_game`'s defer, and the window composites the game canvas only when it
# declines -- after the frame is settled into it. A frame the cart did not
# replace is the kept copy, shown again the same way.


class KeptCartFrame(FakeCartFrame):
    """take() hands back the last frame shown while nothing replaced it."""

    def __init__(self):
        FakeCartFrame.__init__(self)
        self.kept = False

    def take(self):
        self.log.append("take")
        if self.owed or self.kept:
            return memoryview(bytearray(2 * self.w * self.h))
        return None

    def presented(self, kept, off):
        FakeCartFrame.presented(self, kept, off)
        self.kept = True
        self.nrects = 0

    def settle(self, canvas=None):
        FakeCartFrame.settle(self, canvas)
        self.kept = False


def _desk_playing(tmp_path):
    from ws_helpers import build_desktop_ws, open_cart
    ws = build_desktop_ws(tmp_path)
    drv = host_app.ConsoleDriver(ws)
    drv.frame(DT)
    ws.open_desk()
    open_cart(ws, "Star Catcher")
    ws._toast_until = 0
    ws.show_fps = False
    ws.pointer.visible = False
    for _ in range(4):
        drv.frame(DT)
    assert ws.wm._order == ["desktop"]
    return ws, drv


def _window_spy(ws, took=True):
    calls = []
    sc = ws.sys_canvas

    def present(cf, view, gc, ox, oy, scale, src, defer):
        assert gc is ws.canvas
        calls.append(("present", ox, oy, scale, src, defer, len(view),
                      cf.nrects))
        if took:
            cf.presented(None, 0)
        return took

    real = sc.blit_game

    def blit(*a, **k):
        calls.append(("blit_game",))
        return real(*a, **k)

    sc.present_frame = present
    sc.blit_game = blit
    ws.wm._pf_for = None
    return calls


def test_the_player_window_shows_the_frame_where_the_cart_made_it(tmp_path):
    ws, drv = _desk_playing(tmp_path)
    cf = ws.cart_frame = KeptCartFrame()
    calls = _window_spy(ws)
    _frame(ws, drv, cf)
    _frame(ws, drv, cf)
    ox, oy, scale = ws.wm._player_view(ws.wm._wins["desktop"])
    assert calls[-1] == ("present", ox, oy, scale, None, True, 2 * 320 * 240, 0)
    assert ("blit_game",) not in calls
    assert "settle" not in cf.log and cf.log[-2:] == ["take", "presented"]


def test_a_window_frame_it_declines_is_settled_then_composited(tmp_path):
    ws, drv = _desk_playing(tmp_path)
    cf = ws.cart_frame = KeptCartFrame()
    calls = _window_spy(ws, took=False)
    _frame(ws, drv, cf)
    assert [c[0] for c in calls] == ["present", "blit_game"]
    assert cf.log == ["take", "settle"]


def test_the_fps_chip_rides_the_window_frame_as_a_patch(tmp_path):
    ws, drv = _desk_playing(tmp_path)
    ws.show_fps = True
    drv.frame(DT)
    cf = ws.cart_frame = KeptCartFrame()
    calls = _window_spy(ws)
    _frame(ws, drv, cf)
    assert any(isinstance(e, tuple) and e[0] == "patch" for e in cf.log)
    assert "settle" not in cf.log
    assert calls[-1][0] == "present" and calls[-1][-1] == 1


def test_a_frame_the_cart_did_not_replace_is_shown_again_in_the_window(tmp_path):
    """No blit this frame (a logic-only tick, a cart that skipped it): the
    window shows the copy of the last frame shown rather than a canvas
    nothing wrote."""
    ws, drv = _desk_playing(tmp_path)
    cf = ws.cart_frame = KeptCartFrame()
    calls = _window_spy(ws)
    _frame(ws, drv, cf)
    del calls[:]
    cf.log.clear()
    drv.frame(DT)                                # nothing owed: the kept copy
    assert [c[0] for c in calls] == ["present"]
    assert cf.log == ["take", "presented"]


def test_a_window_with_nothing_shown_composites_the_canvas(tmp_path):
    ws, drv = _desk_playing(tmp_path)
    cf = ws.cart_frame = KeptCartFrame()
    calls = _window_spy(ws)
    drv.frame(DT)
    assert calls == [("blit_game",)] and cf.log == ["take"]


def test_the_play_world_shows_the_frame_over_its_bezel(tmp_path):
    """From the fullscreen Library the game is the play world's: the frame
    goes where composite_game would have composited the canvas, and the
    letterbox bezel is painted the same way first."""
    from ws_helpers import build_desktop_ws, open_cart
    ws = build_desktop_ws(tmp_path)
    drv = host_app.ConsoleDriver(ws)
    drv.frame(DT)
    ws.go_home()
    open_cart(ws, "Star Catcher")
    ws._toast_until = 0
    ws.show_fps = False
    for _ in range(3):
        drv.frame(DT)
    assert not ws.wm._order
    cf = ws.cart_frame = KeptCartFrame()
    calls = []
    sc = ws.sys_canvas

    def present(cf_, view, gc, ox, oy, scale, src):
        calls.append(("present", ox, oy, scale, src))
        cf_.presented(None, 0)
        return True

    clears = []
    real_cls = sc.cls
    sc.present_frame = present
    sc.cls = lambda c=0: (clears.append(c), real_cls(c))[1]
    ws.wm._pf_for = None
    ws.wm._bezel_key = None                       # a new geometry: repaint it
    _frame(ws, drv, cf)
    from runtime.wm import FullscreenStackWM
    ox, oy, scale = FullscreenStackWM.viewport(ws.wm)
    assert calls == [("present", ox, oy, scale, None)]
    assert clears, "the bezel is painted before the frame"
    assert cf.log == ["take", "presented"]
