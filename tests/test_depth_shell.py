"""The theme's depth in the shell (docs/look_2026-10.md step 6): installed
with the theme and its mode, the WM's rounded windows with their foot, the
shadows and outline fringes the desk draws under the windows over pixels it
has just painted (so a drag, a release and a re-render agree to the byte), a
dragged window's smaller shadow, the carousel's hero shadow, cast only at
rest, and an app's page lying on its window's body. Counts are of the
toolkit calls that draw them.
"""

import json

import pytest

from runtime import ui_draw
from ws_helpers import build_desktop_ws, build_ws_with_shelf, use_theme

from runtime import host_app, wm_chrome, launcher_layer


def _desk(tmp_path, variant="light"):
    ws = build_desktop_ws(tmp_path)
    ws.look.set_theme("moy", persist=False, variant=variant)
    return ws, host_app.ConsoleDriver(ws)


def _quiet(ws):
    ws.pointer.visible = False
    ws._toast_until = 0


def _below_bar(ws):
    """The frame under the OS bar: the bar's clock redraws over a cache
    restore in its own way under this theme, which is not the depth's."""
    y = ws.wm._desk_rect()[1]
    return bytes(ws.sys_canvas._buf)[y * ws.sys_canvas.w * 2:]


def _spy(monkeypatch, mod, name):
    calls = []
    real = getattr(mod._ui_draw, name)

    def spy(*a, **k):
        calls.append(a)
        return real(*a, **k)

    monkeypatch.setattr(mod._ui_draw, name, spy)
    return calls


# -- installing it ------------------------------------------------------------------

def test_the_depth_is_the_live_themes_in_its_mode(tmp_path):
    ws, _ = _desk(tmp_path)
    d = ui_draw.depth()
    assert d[0] and d[13]                          # on, and the frame is its
    assert (d[1], d[2], d[3]) == (12, 10, 10)      # the model's radii
    assert d[6] == 1 and (d[9], d[10], d[11]) == (16, 0, 8)
    assert d[8] == (18 * 255 + 50) // 100           # Day: 18%
    ws.look.set_theme("moy", persist=False, variant="dark")
    d = ui_draw.depth()
    assert (d[9], d[11], d[8]) == (14, 6, (55 * 255 + 50) // 100)
    use_theme(ws, "indigo")                     # a fixture with no look
    assert ui_draw.depth()[0] == 0                      # no look: the style's frame


def test_a_theme_naming_a_frame_style_keeps_it(tmp_path):
    ws, _ = _desk(tmp_path)
    pkg = {"format": 2, "name": "boxed", "inherits": "moy",
           "frame": {"style": "win9x"}}
    rc, _notes = ui_draw._C.theme_install(json.dumps(pkg).encode())
    assert rc == 0
    ws.look.set_theme("boxed", persist=False, variant="light")
    d = ui_draw.depth()
    assert d[0] and not d[13]
    assert ui_draw.frame_geom(400, 300, 36) == (0,) * 6


# -- the windows ----------------------------------------------------------------------

def test_a_window_has_a_foot_for_its_rounded_corners(tmp_path):
    ws, drv = _desk(tmp_path)
    ws.open_settings()
    drv.frame(0.0)
    win = ws.wm._wins["settings"]
    r, foot = ui_draw.frame_geom(win.w, win.h, win.title_h)[:2]
    assert r == 12 and foot == 11 == win.foot
    assert win.content_rect()[3] == win.h - 2 - win.title_h - foot
    assert win.buf.h == win.content_rect()[3]
    # a theme without depth gives the rows back (its bars re-pack: the window
    # is laid out again)
    use_theme(ws, "indigo")                     # a fixture with no look
    ws._dirty = True
    drv.frame(0.0)
    win = ws.wm._wins["settings"]
    assert win.foot == 0 and win.buf.h == win.h - 2 - win.title_h


def _drag(ws, drv):
    """Settings dragged over the desk, the home's window standing still
    beside it (its shadow is the one a careless restore would blend twice)."""
    ws.open_projects()
    drv.frame(0.0)
    ws.open_settings()
    drv.frame(0.0)
    _quiet(ws)
    win = ws.wm._wins["settings"]
    gx, gy = win.x + win.w // 2, win.y + 4
    drv.touch(gx, gy)
    drv.frame(0.0)
    drv.touch_drag(gx + 80, gy + 50)
    drv.frame(0.0)
    assert ws.wm._drag is not None
    return win, (gx + 80, gy + 50)


def test_a_drag_frame_is_its_live_render(tmp_path):
    """The cache holds the desk without shadows; each restore redraws them
    over what it restored, so a cached drag frame is byte for byte a live
    render of the same frame: nothing blends twice."""
    ws, drv = _desk(tmp_path)
    win, (hx, hy) = _drag(ws, drv)
    for _ in range(3):
        drv.touch_drag(hx, hy)
        drv.frame(0.0)
    assert ws.wm._backdrop_valid
    cached = _below_bar(ws)
    ws.wm._backdrop_valid = False
    ws._dirty = True
    drv.touch_drag(hx, hy)
    drv.frame(0.0)
    assert _below_bar(ws) == cached


def test_a_released_window_settles_to_its_full_shadow(tmp_path):
    ws, drv = _desk(tmp_path, variant="dark")
    win, (hx, hy) = _drag(ws, drv)
    for k in range(4):
        drv.touch_drag(hx + 7 * k, hy + 3 * k)
        drv.frame(0.0)
    drv.touch_up()
    for _ in range(4):
        ws._dirty = True
        drv.frame(0.0)
    settled = _below_bar(ws)
    for _ in range(3):                       # more frames: no darkening
        ws._dirty = True
        drv.frame(0.0)
    assert _below_bar(ws) == settled
    ws.wm._backdrop_valid = False            # and a live render agrees
    ws._dirty = True
    drv.frame(0.0)
    assert _below_bar(ws) == settled


def test_a_dragged_window_keeps_its_full_shadow_and_a_still_desk_casts_none(
        tmp_path, monkeypatch):
    """A moving window casts the shadow it casts at rest, so nothing pops
    when it is let go."""
    ws, drv = _desk(tmp_path)
    calls = _spy(monkeypatch, wm_chrome, "frame_under")
    win, (hx, hy) = _drag(ws, drv)
    for k in (1, 2):
        del calls[:]
        drv.touch_drag(hx + k, hy)
        drv.frame(0.0)
        mine = [c[5] if len(c) > 5 else ui_draw.UNDER_FULL
                for c in calls if c[2] == (win.x, win.y, win.w, win.h)]
        assert mine == [ui_draw.UNDER_FULL]
    drv.touch_up()
    for _ in range(4):
        drv.frame(0.0)
    del calls[:]
    for _ in range(5):                       # a still desk paints nothing
        drv.frame(0.0)
    assert calls == []


def test_the_desk_layer_bakes_no_shadow_per_frame(tmp_path):
    """A shadow's mask is baked once per (radius, blur) and read after: a
    long drag adds no bakes past its first frames."""
    from runtime import glass_binding
    lib = glass_binding._lib() if hasattr(glass_binding, "_lib") else None
    if lib is None or not hasattr(lib, "moy_shape_stats"):
        import pytest
        pytest.skip("the glass library carries no shape counters here")
    import ctypes
    st = (ctypes.c_uint32 * 5)()
    ws, drv = _desk(tmp_path)
    win, (hx, hy) = _drag(ws, drv)
    drv.touch_drag(hx + 1, hy)
    drv.frame(0.0)
    lib.moy_shape_stats(st)
    bakes = st[1]
    for k in range(8):
        drv.touch_drag(hx + 2 + k, hy + k)
        drv.frame(0.0)
    lib.moy_shape_stats(st)
    assert st[1] == bakes


# -- the hero card ----------------------------------------------------------------------

def _settle(drv, n=120):
    for _ in range(n):
        drv.frame(1 / 30)
        if not drv.ws.launcher.moving:
            drv.frame(1 / 30)
            return
    raise AssertionError("the carousel never came to rest")


def test_the_hero_keeps_its_shadow_on_a_moving_frame(tmp_path, monkeypatch):
    """Every frame of a glide casts the card nearest the centre at the
    theme's size and its neighbours smaller, as the settled row does, so no
    shadow pops when the row stops."""
    ws = build_ws_with_shelf(tmp_path, 8)
    ws.look.set_theme("moy", persist=False, variant="light")
    drv = host_app.ConsoleDriver(ws)
    _settle(drv)
    calls = _spy(monkeypatch, launcher_layer, "cast")

    def heroes():
        """The cards cast at the theme's size since the spy last emptied."""
        return [c[2] for c in calls if len(c) < 5 or c[4] == ui_draw.UNDER_FULL]

    drv.press("right")
    drv.frame(1 / 30)
    moving = 0
    for _ in range(60):
        if not ws.launcher.moving:
            break
        del calls[:]
        drv.frame(1 / 30)
        moving += 1
        if ws.launcher.moving:
            assert heroes() == [ws.launcher.tile_rect(ws.launcher.nearest())], \
                "a moving frame dropped the hero's shadow"
            assert len(calls) > 1, "a moving frame dropped the neighbours' shadows"
    assert moving >= 3
    _settle(drv)
    n = len(calls)
    for _ in range(5):
        drv.frame(1 / 30)
    assert len(calls) == n                   # a still home paints nothing


def test_the_settled_home_is_its_full_repaint(tmp_path):
    ws = build_ws_with_shelf(tmp_path, 8)
    ws.look.set_theme("moy", persist=False, variant="light")
    drv = host_app.ConsoleDriver(ws)
    _settle(drv)
    drv.press("right")
    drv.frame(1 / 30)
    _settle(drv)
    settled = bytes(ws.sys_canvas._buf)
    ws.launcher_layer._full_streak = 0
    ws.mark_dirty()
    drv.frame(1 / 30)
    row = ws.sys_canvas.w * ws.layout.status_h * 2
    assert bytes(ws.sys_canvas._buf)[row:] == settled[row:]


def test_a_window_shadow_falls_on_the_backdrop_it_covers(tmp_path):
    """The backdrop rasters into the wallpaper bake and the shadow blends over
    what the desk layer has just painted, so a window over Moy Day's disc
    darkens the disc, and moving it rasters nothing again."""
    ws = host_app.build_workstation(str(tmp_path / "carts"), sys_size=(1024, 600),
                                    windowed=True, panel_diagonal_in=7.0)
    drv = host_app.ConsoleDriver(ws)
    drv.frame(1 / 30)
    ws.look.set_theme("moy", persist=False, variant="light")
    for _ in range(6):
        drv.frame(1 / 30)
    _quiet(ws)
    w = ws.sys_canvas.w

    def at(buf, x, y):
        return tuple(buf[3 * (y * w + x):3 * (y * w + x) + 3])

    bare = bytes(drv.rgb888())
    rasters = ws.wallpaper.rasters
    ws.open_app(ws._apps_by_id["calc"])
    drv.frame(1 / 30)
    win = ws.wm._wins["calc"]
    win.x, win.y = 60, 560 - win.h
    ws.wm.dirty = True
    for _ in range(3):
        ws._dirty = True
        drv.frame(1 / 30)
    now = bytes(drv.rgb888())
    x, y = win.x + 40, win.y + win.h + 2
    assert at(bare, x, y) != at(bare, 900, y)        # the disc, not the desk
    assert all(s <= b for s, b in zip(at(now, x, y), at(bare, x, y)))
    assert at(now, x, y) != at(bare, x, y)
    assert ws.wallpaper.rasters == rasters


@pytest.mark.parametrize("variant", ["light", "dark"])
def test_an_apps_page_in_a_window_lies_on_the_windows_body(tmp_path, variant):
    """Settings, Files and Calc begin their page in the window they run in,
    so the page's ground is the colour the frame paints the window's body."""
    from runtime.host_canvas import make_system_canvas
    ws, drv = _desk(tmp_path, variant)
    _quiet(ws)
    scratch = make_system_canvas(320, 240)
    ui_draw.frame(scratch, ws.theme_colors, (10, 10, 300, 200), 20, "Window", True, False, 1)
    o = 2 * (208 * scratch.w + 160)
    body = scratch._buf[o] << 8 | scratch._buf[o + 1]
    cv = ws.sys_canvas

    def px(x, y):
        o = 2 * (y * cv.w + x)
        return cv._buf[o] << 8 | cv._buf[o + 1]
    for app in ("settings", "files", "calc"):
        if app == "settings":
            ws.open_settings()
        else:
            ws.open_app(ws._apps_by_id[app])
        for _ in range(3):
            ws._dirty = True
            drv.frame(1 / 30)
        cx, cy, cw, ch = ws.wm._wins[app].content_rect()
        # the page's ground: under its last row (Calc's keys reach its foot,
        # so right of its tape)
        x, y = ((cx + cw - 2, cy + 1) if app == "calc"
                else (cx + cw // 2, min(cy + ch, cv.h) - 2))
        assert px(x, y) == body, (app, x, y, hex(px(x, y)), hex(body))
