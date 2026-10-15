"""The home's band surface (docs/theming_2026-09.md §4.9, phase 6 step 10).

The carousel's card band is the theme part `home.band`: its surface paints
with the wallpaper, before the band's backdrop is captured, so the capture
holds it and a moving frame restores it rather than painting it. A wallpaper
cart's band is at its source resolution (the game canvas, before the cover
composite); a picture's and the fill's are 1:1 on the system canvas.

Pins:
  * the captured band backdrop is exactly the surface painted on its own
    (the fixture's dither is opaque, so no wallpaper pixel shows through);
  * a drag frame and an idle frame paint no band;
  * a cart's band lands on the game canvas, the cells the composite places
    under the band, and a picture's on the system canvas at the band rect;
  * a seed (no `home.band` surface) paints nothing there.
"""

import shutil
from pathlib import Path

from ws_helpers import build_desktop_ws, make_drv, quiesce

from runtime import host_app, ui, wallpaper as wallpaper_mod

ROOT = Path(__file__).resolve().parent.parent
BAND = ROOT / "tests" / "fixtures" / "themes" / "band.moy"


def _take_band(ws):
    import moy_ui
    moy_ui.theme_uninstall()           # the install slot is the process's
    shutil.copytree(BAND, Path(ws.carts_root) / "band.moy")
    ws.carts.rescan()
    ws.look.set_theme("band", persist=False)
    assert ws.look.theme_name == "band"


def _home(ws, drv, frames=6):
    ws.bar_layer._clock_text = lambda: "00:00"
    for _ in range(frames):
        quiesce(ws)
        ws.mark_dirty()
        drv.frame(1 / 30)


def _record(monkeypatch):
    """Every home.band paint, as (canvas, rect)."""
    calls = []
    real = wallpaper_mod._ui_paint

    def spy(cv, th, rect, part, *a):
        if part == "home.band":
            calls.append((cv, tuple(rect)))
        return real(cv, th, rect, part, *a)
    monkeypatch.setattr(wallpaper_mod, "_ui_paint", spy)
    return calls


def _rows(cv, rect):
    x, y, w, h = rect
    buf = bytes(cv._buf)
    return [buf[((y + r) * cv.w + x) * 2:((y + r) * cv.w + x + w) * 2]
            for r in range(h)]


def test_the_band_capture_holds_the_surface(tmp_path):
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    _take_band(ws)
    assert ws.look.wallpaper_id == ws.look.PICTURE
    drv = make_drv(ws)
    ws.go_home()
    _home(ws, drv)
    layer = ws.launcher_layer
    band = ws.launcher.band_rect()
    cv = ws.sys_canvas
    assert layer._band_bg is not None and layer._band_key is not None
    ref = cv.new_layer(cv.w, cv.h)
    try:
        assert ui.paint(ref, ws.theme_colors, band, "home.band")
        assert _rows(layer._band_bg, (0, 0, band[2], band[3])) == _rows(ref, band)
    finally:
        ref.release()
    layer._band_bg.release()


def test_a_seed_gives_the_band_no_surface(tmp_path, monkeypatch):
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    calls = _record(monkeypatch)
    drv = make_drv(ws)
    ws.go_home()
    _home(ws, drv, 2)
    assert calls, "the carousel asks for its band"
    assert not ui.paint(ws.sys_canvas, ws.theme_colors, (0, 0, 8, 8), "home.band")


def test_moving_and_idle_frames_paint_no_band(tmp_path, monkeypatch):
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    while len(ws.launcher.items) < 14:
        ws.launcher.items.append(dict(ws.launcher.items[-1]))
    _take_band(ws)
    calls = _record(monkeypatch)
    drv = make_drv(ws)
    ws.go_home()
    for _ in range(80):
        drv.frame(1 / 30)
    _home(ws, drv, 3)
    assert calls, "a full paint paints the band"
    calls.clear()
    for _ in range(5):
        drv.frame(1 / 30)
    assert calls == [], "an idle home paints nothing"
    bx, by, bw, bh = ws.launcher.band_rect()
    cx, cy = bx + bw - 10, by + bh // 2
    drv.touch(cx, cy)
    drv.frame(1 / 30)
    ws.pointer.visible = False
    painted = []
    for step in range(1, 10):
        calls.clear()
        drv.touch_drag(cx - step * 9, cy)
        drv.frame(1 / 30)
        painted.append(len(calls))
    assert ws.launcher.dragging
    assert painted[-3:] == [0, 0, 0], painted


def test_a_cart_band_is_at_its_source_resolution(tmp_path, monkeypatch):
    ws = build_desktop_ws(tmp_path, font_scale=1)
    _take_band(ws)
    ws.look.select_wallpaper(ws.look._default_wallpaper(), persist=False)
    assert ws.wallpaper._wp_draw is not None
    ws.wm.enter_console()
    assert ws.screen == "launcher"
    calls = _record(monkeypatch)
    drv = make_drv(ws)
    _home(ws, drv, 2)
    sc, gc = ws.sys_canvas, ws.canvas
    assert sc is not gc
    s, ox, oy = wallpaper_mod._cover(sc.w, sc.h, gc.w, gc.h)
    bx, by, bw, bh = ws.launcher.band_rect()
    cv, (x, y, w, h) = calls[-1]
    assert cv is gc
    assert x * s + ox <= bx and (x + w) * s + ox >= bx + bw
    assert y * s + oy <= by and (y + h) * s + oy >= by + bh
    assert (x + 1) * s + ox > bx and (y + 1) * s + oy > by
    assert 0 <= x and x + w <= gc.w and 0 <= y and y + h <= gc.h


def test_a_picture_band_is_one_to_one(tmp_path, monkeypatch):
    ws = build_desktop_ws(tmp_path, font_scale=1)
    _take_band(ws)
    ws.wm.enter_console()
    calls = _record(monkeypatch)
    drv = make_drv(ws)
    _home(ws, drv, 2)
    assert calls[-1] == (ws.sys_canvas, tuple(ws.launcher.band_rect()))
