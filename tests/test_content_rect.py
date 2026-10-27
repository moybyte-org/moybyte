"""The content rect (docs/shell_layout_2026-10.md §7, §13 steps 3 and 3b).

`layout_base.place_bar` is where the host's bar sits on every surface, and
`content_of` / `BarLayer.bar_rects` are the one author of what that leaves:
under today's top bar the content rect is exactly the old geometry (the shell
goldens are the proof, byte for byte), so these tests move the bar to the
BOTTOM through the step's hook (`Workstation.place_bar`) and check that every
surface follows it -- every layout's rects inside its content rect, the bar's
pixels inside its own band and nowhere else, and taps on the bar routed to it
while taps where the top bar used to be reach the surface.
"""

import hashlib
import json
from pathlib import Path

import pytest

from runtime import layout_base
from runtime.layout_base import LayoutBase
from ws_helpers import build_ws, build_desktop_ws, select_on_home
from ws_helpers import boot_on_fixture

@pytest.fixture(autouse=True)
def _on_the_night_fixture(monkeypatch):
    """Every workstation here boots on the `night` fixture (ws_helpers): a
    palette theme with no look, drawn in the default's look flat."""
    boot_on_fixture(monkeypatch)

DT = 1.0 / 30.0

# The fullscreen tiers: the T-Deck, the Guition S3's tap floor (chrome scale 2
# over font scale 1) and a big font-scale-3 canvas.
SIZES = {
    "tdeck": {},
    "guition_s3": {"sys_size": (480, 320), "panel_diagonal_in": 3.5},
    "big_fs3": {"sys_size": (800, 480), "font_scale": 3},
}

# Fields of chrome.Layout that are BAR rects, in bar coordinates.
BAR_LOCAL = {"batt_btn", "wifi_btn", "sysmenu_btn", "context_x_btn",
             "zone_left"}

EDITOR_TABS = ("code", "blocks", "paint", "map", "music", "cards")


@pytest.fixture(autouse=True)
def _top_again():
    yield
    layout_base.place_bar("top")


def _rects(v):
    """Every (x, y, w, h) with an area held in `v`, nested lists included."""
    if (isinstance(v, tuple) and len(v) == 4
            and all(isinstance(n, int) and not isinstance(n, bool) for n in v)):
        return [v] if v[2] > 0 and v[3] > 0 else []
    if isinstance(v, (list, tuple)):
        out = []
        for item in v:
            out += _rects(item)
        return out
    return []


def _inside(r, box):
    x, y, w, h = r
    bx, by, bw, bh = box
    return x >= bx and y >= by and x + w <= bx + bw and y + h <= by + bh


def _rect_map(ws):
    """(layout, field, i) -> (rect, that layout's content rect)."""
    out = {}
    for name, lay in _layouts(ws):
        content = (lay.cx, lay.cy, lay.cw, lay.ch)
        for attr, v in vars(lay).items():
            if attr in BAR_LOCAL:
                continue
            for i, r in enumerate(_rects(v)):
                out[(name, attr, i)] = (r, content)
    return out


def _bottom(ws):
    ws.place_bar("bottom")
    assert layout_base.bar_edge() == "bottom"


# -- the bar and the content rect partition every surface ----------------------

@pytest.mark.parametrize("edge", ("top", "bottom"))
@pytest.mark.parametrize("size", sorted(SIZES))
def test_bar_and_content_partition_the_canvas(tmp_path, size, edge):
    ws = build_ws(tmp_path, **SIZES[size])
    ws.place_bar(edge)
    bl = ws.bar_layer
    # a tool on a game canvas of its own keeps the compact band over the
    # game's rows (docs/look_2026-10.md step 33), no partition of the glass
    tool = ("tool",) if bl.tool_band() is None else ()
    for where in ("home", "picker", "settings", "menu", "app") + tool:
        cv = bl._bar_canvas(where)
        (bx, by, bw, bh), = bl.bar_rects(where)
        cx, cy, cw, ch = bl.content_rect(where)
        assert (bx, bw, bh) == (0, cv.w, bl._bar_h(where)), where
        assert (cx, cw) == (0, cv.w), where
        assert ch + bh == cv.h, where
        if edge == "top":
            assert (by, cy) == (0, bh), where       # today's geometry
        else:
            assert (by, cy) == (cv.h - bh, 0), where
    assert ws.app_content_rect() == bl.content_rect("tool")
    assert ws.app_bar_h() == ws.app_content_rect()[1]


# -- every layout lays out inside it -------------------------------------------

def _open_every_surface(ws):
    """Build every responsive surface's layout: each editor tab and app."""
    cart = next(c for c in ws.carts.all if c.get("title") == "Star Catcher")
    ws.open_in_editor(cart)
    for tab in EDITOR_TABS:
        ws.set_menu_view(tab)
        ws.frame(DT)
    ws.go_home()


def _layouts(ws):
    out = [("Layout", ws.layout), ("CodeLayout", ws.code_layout),
           ("CardsLayout", ws.cards_layer.layout)]
    for attr in ("block_ui", "paint_layer", "map_ui", "scene_ui", "music_ui"):
        obj = ws.built(attr)
        if obj is None:
            continue
        lay = getattr(obj, "block_layout", None) or obj.layout
        out.append((type(lay).__name__, lay))
    for app, _t in ws._apps:
        lay = getattr(app, "layout", None)
        if lay is not None:
            out.append((type(lay).__name__, lay))
    return out


@pytest.mark.parametrize("size", sorted(SIZES))
def test_every_layout_lays_out_inside_the_content_rect(tmp_path, size):
    """Every rect a layout holds that sits inside its content rect under the
    top bar sits inside it under the bottom bar too. (A rect already outside
    under the top bar -- font scale 3's overflow on an 800x480 canvas -- is no
    bar's doing and is not this test's.)"""
    ws = build_ws(tmp_path, **SIZES[size])
    _open_every_surface(ws)
    names = {n for n, _l in _layouts(ws)}
    for want in ("Layout", "CodeLayout", "CardsLayout", "PaintLayout",
                 "MapLayout", "MusicLayout", "BlockLayout", "CalcLayout",
                 "FilesLayout", "StorybookLayout", "AppearanceLayout",
                 "PaintAppLayout", "GetCartsLayout", "MyArtLayout"):
        assert want in names, want + " was never built"
    top = _rect_map(ws)
    _bottom(ws)
    bar = ws.bar_layer
    for name, lay in _layouts(ws):
        where = "app" if not isinstance(lay, LayoutBase) else "menu"
        assert (lay.cx, lay.cy, lay.cw, lay.ch) == bar.content_rect(where), name
    bottom = _rect_map(ws)
    bad, checked = [], 0
    for key, (r, content) in sorted(top.items()):
        if not _inside(r, content) or key not in bottom:
            continue                    # a rect the relayout stood off the surface
        checked += 1
        rb, cb = bottom[key]
        if not _inside(rb, cb):
            bad.append("%s.%s[%d] %r in %r" % (key + (rb, cb)))
    assert checked > 80
    assert not bad, "outside the content rect under a bottom bar: %s" % bad
    lay = ws.layout
    assert lay.icon_y0 >= lay.cy and lay.grid_bottom <= lay.cy + lay.ch
    assert ws.code_layout.y0 == 0                     # nothing above the code


def test_a_window_has_no_bar_over_its_content(tmp_path):
    """The shipped apps' heads: a window's content is its whole buffer, the
    title strip being the WM's -- wherever the bar is."""
    from runtime.calc_app import CalcLayout
    from runtime.files_app import FilesLayout
    for edge in ("top", "bottom"):
        layout_base.place_bar(edge)
        for cls in (CalcLayout, FilesLayout):
            lay = cls(480, 300, 1, windowed=True)
            assert (lay.cx, lay.cy, lay.cw, lay.ch) == (0, 0, 480, 300)


# -- the bar's pixels land in its band and nowhere else --------------------------

def _settle(ws, n=6):
    prev = None
    for _ in range(n):
        ws.pointer.visible = False
        ws._toast_until = 0
        ws.bar_layer._clock_text = lambda: "12:34"
        ws._dirty = True
        ws.frame(DT)
        cur = bytes(ws.sys_canvas._buf)
        if cur == prev:
            return cur
        prev = cur
    raise AssertionError("never settled")


def _mute(ws, where):
    orig = ws.bar_layer._draw_status_strip

    def muted(w):
        if w != where:
            orig(w)

    ws.bar_layer._draw_status_strip = muted
    return orig


def _band_split(ws, frame, rect):
    stride = ws.sys_canvas.w * 2
    y, h = rect[1], rect[3]
    band = frame[stride * y:stride * (y + h)]
    rest = frame[:stride * y] + frame[stride * (y + h):]
    return (hashlib.sha256(band).hexdigest(), hashlib.sha256(rest).hexdigest())


SURFACES = {
    "home": lambda ws: ws.go_home(),
    "settings": lambda ws: ws.open_settings(),
    "menu": lambda ws: (ws.open_in_editor(next(
        c for c in ws.carts.all if c.get("title") == "Star Catcher")),
        ws.set_menu_view("code")),
    "app": lambda ws: ws.open_app(ws._apps_by_id["files"]),
}


@pytest.mark.parametrize("where", sorted(SURFACES))
@pytest.mark.parametrize("size", ("tdeck", "guition_s3"))
def test_the_bar_draws_in_its_band_at_the_bottom(tmp_path, size, where):
    """The band's rows are the bar's strip, byte for byte; and muting the bar
    changes nothing outside the band (the home keeps its last frame retained,
    bar included, so it takes the first half only)."""
    ws = build_ws(tmp_path, **SIZES[size])
    _bottom(ws)
    SURFACES[where](ws)
    with_bar = _settle(ws)
    rect = ws.bar_layer.bar_rect(0, where)
    assert rect[1] + rect[3] == ws.sys_canvas.h
    strip = ws.bar_layer._bar_strips[where][0][0][0]
    stride = ws.sys_canvas.w * 2
    band = with_bar[stride * rect[1]:stride * (rect[1] + rect[3])]
    assert band == bytes(strip._buf), where + ": the band is not the bar"
    if where == "home":
        return
    _mute(ws, where)
    without = _settle(ws)
    band_a, rest_a = _band_split(ws, with_bar, rect)
    band_b, rest_b = _band_split(ws, without, rect)
    assert band_a != band_b, where + ": nothing drew in the bottom band"
    assert rest_a == rest_b, where + ": the bar drew outside its band"


# -- taps: the bar's band is the bar's, the old top band is the surface's --------

def _tap(ws, x, y):
    ws.pointer.place(x, y)
    ws.pointer.click = True
    ws.handle_pointer()
    ws.pointer.click = False


@pytest.mark.parametrize("size", sorted(SIZES))
def test_the_system_menu_opens_from_the_bottom_bar_upward(tmp_path, size):
    ws = build_ws(tmp_path, **SIZES[size])
    _bottom(ws)
    ws.go_home()
    x, y, w, h = ws.layout.sysmenu_btn
    _tap(ws, x + w // 2, y + h // 2)              # where a TOP bar's ≡ is
    assert not ws.sysmenu.open
    by = ws.bar_layer.bar_rect(0, "home")[1]
    _tap(ws, x + w // 2, by + y + h // 2)
    assert ws.sysmenu.open
    px, py, pw, ph = ws.sysmenu.panel_rect()
    assert py + ph == by and py >= 0               # hangs upward from the bar
    first = next(i for i, it in enumerate(ws.sysmenu.items) if it[0] == "item")
    assert ws.sysmenu.row_at(px + 2, py + 1) is not None
    assert ws.sysmenu.row_at(px + 2, py - 1) is None
    assert first is not None


@pytest.mark.parametrize("size", sorted(SIZES))
def test_an_app_exits_on_the_bottom_bars_x(tmp_path, size):
    ws = build_ws(tmp_path, **SIZES[size])
    _bottom(ws)
    app = ws._apps_by_id["calc"]
    assert ws.open_app(app)
    x, y, w, h = ws.layout.context_x_btn
    _tap(ws, x + w // 2, y + h // 2)              # the app's own top rows now
    assert ws.screen == "calc"
    assert ws._app_bar_route(app, x + w // 2, y + h // 2) is None
    by = ws.bar_layer.bar_rect(0, "app")[1]
    _tap(ws, x + w // 2, by + y + h // 2)
    assert ws.wm.top_kind() == "launcher"


def test_an_editor_tab_routes_its_bottom_bar(tmp_path):
    """The lent zone (the Editor's tab ladder) is handed taps in bar
    coordinates: the bottom band's row 0 is the zone's row 0."""
    ws = build_ws(tmp_path)
    _bottom(ws)
    SURFACES["menu"](ws)
    got = []
    ws.editor_app.zone_tap = lambda px, py, rect: got.append((px, py, rect))
    zone = ws.bar_layer._zone_rect("menu")
    zx, zy, zw, zh = zone
    by = ws.bar_layer.bar_rect(0, "menu")[1]
    _tap(ws, zx + 3, by + zy + 2)
    assert got[-1] == (zx + 3, zy + 2, zone)
    n = len(got)
    assert ws.bar_layer.handle_bar_tap("menu", zx + 3, zy + 2) is False
    assert len(got) == n                          # the old top band: not a bar


# -- a running app cart reads the content rect --------------------------------

APP_PY = """
RECT = ""


def _init():
    global RECT
    x, y, w, h = content_rect()
    RECT = "%d,%d,%d,%d,%d" % (x, y, w, h, bar_h())


def _update(dt):
    pass


def _draw():
    cls(0)
"""

APP_LUA = """
RECT = ""

function _init()
  local x, y, w, h = content_rect()
  RECT = x .. "," .. y .. "," .. w .. "," .. h .. "," .. bar_h()
end

function _update(dt)
end

function _draw()
  cls(0)
end
"""


def _write_app(carts_dir, name, src, lua=False):
    d = Path(carts_dir) / (name + ".moy")
    d.mkdir(parents=True, exist_ok=True)
    man = {"title": name, "canvas": "320x240",
           "moybyte": {"type": "app", "permissions": []}}
    if lua:
        man.update({"runtime": "lua", "main": "main.lua"})
    (d / "manifest.json").write_text(json.dumps(man))
    (d / ("main.lua" if lua else "main.py")).write_text(src)
    (d / "config.json").write_text("{}")


@pytest.mark.parametrize("lua", (False, True), ids=("python", "lua"))
@pytest.mark.parametrize("edge", ("top", "bottom"))
def test_an_app_cart_reads_the_content_rect(tmp_path, edge, lua):
    layout_base.place_bar(edge)
    _write_app(tmp_path / "carts", "Rect", APP_LUA if lua else APP_PY, lua)
    ws = build_ws(tmp_path)
    ws.place_bar(edge)
    assert select_on_home(ws, "Rect") is not None
    ws.open()
    for _ in range(3):
        ws.input.begin_frame()
        ws.frame(DT)
    assert ws.player.cart_error is None, ws.player.cart_error
    got = (ws.player._lua.get_global("RECT") if lua
           else ws.player.ns["RECT"])
    x, y, w, h = ws.app_content_rect()
    assert got == "%d,%d,%d,%d,%d" % (x, y, w, h, y)     # Lua answers four values
    assert (y == 0) == (edge == "bottom") and h == 240 - ws.bar_layer._bar_h("tool")


def test_a_tool_cart_exits_on_the_bottom_bars_x(tmp_path):
    _write_app(tmp_path / "carts", "Rect", APP_PY)
    ws = build_ws(tmp_path)
    _bottom(ws)
    select_on_home(ws, "Rect")
    ws.open()
    ws.frame(DT)
    bar, (x, y, w, h) = ws.bar_layer.cell("tool", "close")      # the layout's X
    by = ws.bar_layer.bar_rect(0, "tool")[1]
    assert by == bar[1][1] == 240 - ws.bar_layer._bar_h("tool")
    assert not ws._tool_bar_tap(x + w // 2, y + h // 2)   # the app's top rows
    assert ws._tool_bar_tap(x + w // 2, by + y + h // 2)
    assert ws.wm.top_kind() == "launcher"


# -- the desk: windows, the icon column and the bar's taps ----------------------

def test_the_desk_keeps_windows_and_icons_off_the_bottom_bar(tmp_path):
    ws = build_desktop_ws(tmp_path)
    ws.open_desk()
    _bottom(ws)
    wm = ws.wm
    dx, dy, dw, dh = wm._desk_rect()
    (bx, by, bw, bh), = ws.bar_layer.bar_rects("desk")
    assert (dx, dy, dw, dh) == (0, 0, 1024, 600 - bh) and by == dh
    for _key, box, pill, _label, _cart in wm._backdrop_layer._icon_rects():
        assert _inside(box, (dx, dy, dw, dh)) and _inside(pill, (dx, dy, dw, dh))
    ws.open_app(ws._apps_by_id["calc"])
    ws.frame(DT)
    win = wm._wins[wm._order[-1]]
    assert dy <= win.y and win.y + win.h <= dy + dh
    wm._move_window(win, win.x, 10_000)
    assert win.y + 24 * wm._fs() <= dy + dh
    for _k, rect, _label in wm._chip_rects():
        assert _inside(rect, (bx, by, bw, bh))
