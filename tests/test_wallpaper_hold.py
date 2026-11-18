"""A live wallpaper held still under a gesture, and wallpapers on every
cart runtime (owner, 2026-11-18).

A live wallpaper (a cart with `_update`) animates every frame, which turns
off every cheap path a still backdrop takes: the home's band frame and
retained stamp, the desk's drag cache. While a gesture is live -- the
home's cards dragged or flung, a window dragged or resized, a window's
content scrolled or flung, a verbs page coasting -- `Wallpaper.held`
holds it: its `_update` does not run, the frame stays a still picture,
and the cheap paths take the gesture's frames. It animates again the
frame the gesture ends. At rest it runs at the console's full rate.

A moving desk renders live every frame, so it takes no snapshot of
itself that the next frame would throw away (`wm._capture_backdrop`).

A wallpaper cart on another runtime -- Lua (moycore), a compiled cart
(wasm) -- runs through the same `ws.runtimes` factory the Player starts it
with, on the same per-frame contract and the same cover-scale.
"""

import os
import random

import pytest

from runtime import host_app, launcher_layer
from tools import wasm_cart
from ws_helpers import build_desktop_ws, no_unlock_banner, quiesce

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HELLO = os.path.join(ROOT, "tests", "fixtures", "wasm", "hello.moy")
DT = 1 / 30


class _Counted:
    """A wallpaper hook that counts its calls."""

    def __init__(self, fn):
        self.fn = fn
        self.n = 0

    def __call__(self, *a):
        self.n += 1
        return self.fn(*a)


def _live(ws, wp="moybyte.sakura"):
    """`wp` on the backdrop, its `_update` and `_draw` counted."""
    random.seed(7)
    ws.look.select_wallpaper(wp, persist=False)
    w = ws.wallpaper
    assert w._wp_update is not None, wp
    w._wp_update = up = _Counted(w._wp_update)
    w._wp_draw = dr = _Counted(w._wp_draw)
    return up, dr


def _handheld(tmp_path, size, bufs=1, ref=False, monkeypatch=None):
    if ref:
        L = launcher_layer.LauncherHomeLayer
        monkeypatch.setattr(L, "_try_band_frame", lambda self, cv, dt: False)
        monkeypatch.setattr(L, "_try_stamp_retained", lambda self, cv, dt: False)
    ws = host_app.build_workstation(str(tmp_path / "carts"), sys_size=size, font_scale=1)
    ws.home.go_home()
    ws.home.set_home_arrangement("carousel")
    ws.launcher.sel = 0
    ws.launcher.scroll = 0
    ws.bar_layer.clock_text = lambda: "10:30"
    no_unlock_banner(ws)
    quiesce(ws)
    ws.pointer.visible = False
    ws.sys_canvas.RETAINED_FRAMES = bufs
    return ws, host_app.ConsoleDriver(ws)


def _presenter(ws, drv, bufs):
    """frame(): one console frame on `bufs` buffers presented in turn, as a
    board's framebuffers are; the presented picture, or None when the frame
    drew nothing."""
    root = ws.sys_canvas
    held = [bytes(root._buf)]

    def frame():
        n0 = ws.frame_state.frames_drawn
        drv.frame(DT)
        quiesce(ws)
        ws.pointer.visible = False
        if ws.frame_state.frames_drawn == n0:
            return None
        held.append(bytes(root._buf))
        if bufs > 1:
            root._comp.last = held[-1]
            root._buf[:] = held[-bufs] if len(held) > bufs else held[0]
        return held[-1]
    return frame


def _band_drag(ws, drv, frame, out):
    """A finger drags the carousel's band across and lets go: each frame's
    (held, picture). `out` takes the wallpaper's counts per frame."""
    g = ws.launcher
    bx, by, bw, bh = g.band_rect()
    x, y = bx + bw - 30, by + bh // 2
    shots = []

    def run(touch=None):
        held = ws.wallpaper.held()
        if touch is not None:
            touch()
        shots.append(frame())
        out.append(held)

    drv.touch(x, y)
    run()
    for i in range(1, 9):
        drv.touch_drag(x - 22 * i, y)
        run()
    drv.touch_up()
    for _ in range(60):
        run()
    return shots


@pytest.mark.parametrize("size", [(320, 240), (480, 320)], ids=["tdeck", "guition_s3"])
def test_a_home_drag_holds_a_live_wallpaper_and_takes_band_frames(tmp_path, size):
    """The carousel dragged and flung over Sakura: no `_update` runs while
    the band moves, the band frame draws the moving frames, and the frame
    the band rests on runs `_update` again."""
    ws, drv = _handheld(tmp_path, size)
    up, dr = _live(ws)
    frame = _presenter(ws, drv, 1)
    for _ in range(5):
        frame()
    assert ws.wallpaper.is_animating(DT)
    assert up.n == 5, "a live wallpaper at rest runs every frame"
    band = []
    real = ws.launcher_layer._try_band_frame

    def counted(cv, dt):
        r = real(cv, dt)
        band.append(r)
        return r
    ws.launcher_layer._try_band_frame = counted
    g = ws.launcher
    bx, by, bw, bh = g.band_rect()
    x, y = bx + bw - 30, by + bh // 2
    drv.touch(x, y)
    frame()
    u0, d0 = up.n, dr.n
    moving = 0
    for i in range(1, 9):
        drv.touch_drag(x - 22 * i, y)
        frame()
        moving += g.moving
    drv.touch_up()
    while g.moving:
        assert ws.wallpaper.held() and not ws.wallpaper.is_animating(DT)
        frame()
        moving += 1
    assert moving > 8
    # (the loop's last frame is the one the fling rests on: it animates)
    assert up.n - u0 <= 1, "the wallpaper advanced under the gesture"
    assert dr.n - d0 <= 3, "the held wallpaper drew past the gesture's first frames"
    assert sum(1 for b in band if b) >= moving - 2, band
    n = up.n
    frame()
    assert up.n == n + 1, "a wallpaper at rest after the gesture animates"
    assert ws.wallpaper.is_animating(DT)


@pytest.mark.parametrize("bufs", [1, 2])
def test_a_held_wallpaper_s_band_frames_are_the_full_paint_s_pixels(tmp_path, monkeypatch, bufs):
    """Every picture a drag and its fling present, on one and two buffers
    presented in turn, is the picture the same session draws with the band
    frame and the retained stamp off: the held frame is one still picture
    in every buffer, never two moments of the wallpaper."""
    shots = []
    for ref in (False, True):
        with monkeypatch.context() as m:
            ws, drv = _handheld(tmp_path / str(ref), (480, 320), bufs, ref, m)
            _live(ws)
            frame = _presenter(ws, drv, bufs)
            for _ in range(4):
                frame()
            held = []
            shots.append((_band_drag(ws, drv, frame, held), held))
    (got, held), (want, _ref_held) = shots
    assert any(held) and not all(held)
    assert len(got) == len(want)
    for i, (a, b) in enumerate(zip(got, want)):
        assert a == b, "picture %d differs (held %s)" % (i, held[i])


def _desk(tmp_path, wp="moybyte.sakura"):
    ws = build_desktop_ws(tmp_path, font_scale=1, panel_diagonal_in=7.0)
    drv = host_app.ConsoleDriver(ws)
    ws.bar_layer.clock_text = lambda: "00:00"
    ws.home.open_desk()
    ws.open_settings()
    for _ in range(10):
        quiesce(ws)
        drv.frame(DT)
    up, dr = _live(ws, wp)
    for _ in range(3):
        quiesce(ws)
        drv.frame(DT)
    return ws, drv, up, dr


def test_a_live_desk_takes_no_snapshot_it_throws_away(tmp_path):
    """A desk under a live wallpaper renders every frame: no frame copies
    the screen into the drag cache that the next frame renders over."""
    ws, drv, up, _dr = _desk(tmp_path)
    wm = ws.wm
    caps = []
    real = wm._capture_backdrop
    wm._capture_backdrop = lambda: (caps.append(1), real())[1]
    u0 = up.n
    for _ in range(8):
        quiesce(ws)
        drv.frame(DT)
    assert up.n - u0 == 8
    assert caps == []


def test_a_window_drag_holds_the_wallpaper_over_the_desk_s_cache(tmp_path):
    """A window dragged by its title over a live wallpaper: the desk renders
    once at the drag's first frame and takes its cache, every later frame
    restores from it, the wallpaper's `_update` never runs, and the frame
    after the release animates it again."""
    ws, drv, up, dr = _desk(tmp_path)
    wm = ws.wm
    caps = []
    real = wm._capture_backdrop
    wm._capture_backdrop = lambda: (caps.append(1), real())[1]
    win = wm.windows[wm.z_order[-1]]
    x, y = win.x + 40, win.y + win.title_h // 2
    drv.touch(x, y)
    quiesce(ws)
    drv.frame(DT)
    u0, d0 = up.n, dr.n
    for k in range(1, 10):
        drv.touch_drag(x - 20 * k, y + 8 * k)
        quiesce(ws)
        drv.frame(DT)
        assert wm.gesture_live() and ws.wallpaper.held()
    assert up.n == u0
    assert dr.n - d0 <= 1
    assert len(caps) == 1
    drv.touch_up()
    quiesce(ws)
    drv.frame(DT)
    for _ in range(wm.retained_n() + 1):
        quiesce(ws)
        drv.frame(DT)
    assert not ws.wallpaper.held()
    assert up.n > u0, "the wallpaper stayed held after the release"


def test_a_coasting_page_and_a_content_gesture_hold_the_wallpaper(tmp_path):
    """A verbs page coasting (`ws.ui_moving`) and a finger in a window's
    content (`_content_gesture`) hold it; each let go, it animates."""
    ws, _drv, _up, _dr = _desk(tmp_path)
    w = ws.wallpaper
    assert w.is_animating(DT)
    ws.ui_moving = True
    assert w.held() and not w.is_animating(DT) and not ws.wm.desk_moves(DT)
    ws.ui_moving = False
    ws.wm._content_gesture = True
    assert w.held() and not w.is_animating(DT)
    ws.wm._content_gesture = False
    assert w.is_animating(DT)


def test_the_home_s_cards_hold_it_only_while_the_home_is_on_top(tmp_path):
    """The home's cards count only while the home is on top: a glide left
    behind by a key that opened another surface never holds the wallpaper
    behind it."""
    import types
    ws, _drv = _handheld(tmp_path, (480, 320))
    _live(ws)
    grid = ws.launcher
    try:
        ws.launcher = types.SimpleNamespace(moving=True)
        assert ws.wallpaper.held()
        ws.open_settings()
        assert ws.wm.top_kind() != "launcher"
        assert not ws.wallpaper.held()
    finally:
        ws.launcher = grid


# -- every runtime --------------------------------------------------------------


def _hosted(ws, title):
    cart = next(c for c in ws.carts.all if c.get("title") == title)
    ws.carts.rehydrate(cart)
    ws.wallpaper.clear()
    ws.wallpaper.compile(cart)
    return cart


def test_a_lua_cart_is_a_wallpaper_through_the_lua_runtime(tmp_path):
    """Sakura Lua (a game: its type stays) compiled as the backdrop runs on
    `ws.runtimes["lua"]` -- moycore, the Player's -- animates frame to frame,
    holds still under a gesture and is cover-scaled onto a bigger glass
    as a Python wallpaper is."""
    if "lua" not in getattr(host_app.build_workstation(str(tmp_path / "probe")),
                            "runtimes", {}):
        pytest.skip("no host moycore binding")
    ws, drv = _handheld(tmp_path, (480, 320))
    _hosted(ws, "Sakura Lua")
    w = ws.wallpaper
    assert type(w._wp_run.h).__name__ == "MoycoreRun"
    assert w.is_animating(DT)
    sc, gc = ws.sys_canvas, ws.run.canvas
    assert sc is not gc
    frames = []
    for _ in range(4):
        ws.mark_dirty()
        drv.frame(DT)
        frames.append(bytes(gc._buf))
    assert len(set(frames)) > 1, "the Lua wallpaper does not animate"
    assert len(set(frames[-1][i:i + 2] for i in range(0, len(frames[-1]), 2))) > 8
    # the cover-scale: the backdrop alone is the game frame laid on the
    # glass at the smallest integer scale that covers it, centred
    ws.ui_moving = True               # (held: the game frame stays as it is)
    w.draw(DT, home=True)
    scale = max(1, -(-sc.w // gc.w), -(-sc.h // gc.h))
    ox, oy = (sc.w - gc.w * scale) // 2, (sc.h - gc.h * scale) // 2
    for sx, sy in ((0, 0), (sc.w // 2, sc.h // 2), (sc.w - 1, sc.h - 1), (37, 211)):
        gx, gy = (sx - ox) // scale, (sy - oy) // scale
        i, j = 2 * (sy * sc.w + sx), 2 * (gy * gc.w + gx)
        assert sc._buf[i:i + 2] == gc._buf[j:j + 2], (sx, sy)
    ws.ui_moving = False
    # held: the frame drawn again is the frame that was
    ws.ui_moving = True
    a = bytes(gc._buf)
    w.draw(DT, home=True)
    assert bytes(gc._buf) == a
    ws.ui_moving = False
    w.draw(DT, home=True)
    assert bytes(gc._buf) != a
    # a quit() it calls quits nothing; clear() closes its run
    ws.input.cart_quit = False
    h = w._wp_run
    w.clear()
    assert w._wp_run is None and h.h.update is None


def test_a_compiled_cart_is_a_wallpaper_through_the_wasm_runtime(tmp_path):
    """The hello compiled cart as the backdrop: started by
    `ws.runtimes["wasm"]` and drawn on the wallpaper's canvas, with the
    console's audio stream left where it was."""
    from runtime import wasm_host
    if not wasm_host.available():
        pytest.skip("no host wasm binding")
    carts = str(tmp_path / "carts")
    host_app.moy_carts.ensure_dirs(carts)
    wasm_cart.build(HELLO, os.path.join(carts, "hello.moy"))
    ws = host_app.build_workstation(carts, sys_size=(480, 320), font_scale=1)
    ws.home.go_home()
    out = getattr(ws, "audio_out", None)
    stream = getattr(out, "stream", None)
    gc = ws.run.canvas
    gc.cls(0)
    before = bytes(gc._buf)
    _hosted(ws, "Hello Wasm")
    w = ws.wallpaper
    assert type(w._wp_run.h).__name__ == "WasmHostRun"
    assert getattr(out, "stream", None) is stream
    drv = host_app.ConsoleDriver(ws)
    for _ in range(3):
        ws.mark_dirty()
        drv.frame(DT)
    assert bytes(gc._buf) != before, "the compiled wallpaper drew nothing"
    w.clear()
    assert w._wp_run is None
