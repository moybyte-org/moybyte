"""The ui parts an app composes a chat or a settings page from
(docs/ui_api_2026-10.md section 1, docs/app_api_v1.md section 3), each a
page of one fixture app per tier -- tests/fixtures/app_ui/parts_py.moy,
parts_lua.moy and parts_wasm.moy (a compiled app) -- held to the same pixels
on the T-Deck's 320x240 console (Moy light), on the Guition S3's 480x320
under its tap floor (Moy dark: chrome scale 2 over font scale 1, so a list's
rows and a bubble take the floor's metrics and faces) and in a window on the
P4's desk as shipped (Moy dark). pmem 0 names the page each app draws:

  1  ui.empty: an empty page's head and its one verb.
  2  ui.bubble: an "in" bubble with its head and signal glyph, an "out"
     one sending, a failed one focused, and one at half the width cut at three
     lines, wearing the anchor ring; pmem 1 the CUT bit the last answered.
  3  the signal glyph in a details card: an info row's before its value
     (`o.signal`), and a link's.
  4  ui.popover hung from a failed bubble at the room's width (wider than
     the modal band leaves), its two buttons one right-aligned row inside
     the card, the primary focused.
  5  ui.list's secondary texts (`o.values`) and the check on its current
     row (`o.check`).
  6  ui.popover's `wash = "view"` hung from a failed bubble in a scroll
     region: the region washed, the row above it and the bubble with its
     status line and anchor ring not; the card clears the status line.
"""

import os
import shutil
import sys
from array import array

import pytest

from ws_helpers import build_desktop_ws, make_drv, quiesce

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIX = os.path.join(ROOT, "tests", "fixtures", "app_ui")
DT = 1.0 / 30.0
TIERS = ("python", "lua", "wasm")
_EXT = {"python": "py", "lua": "lua", "wasm": "wasm"}
_TITLE = {"python": "Parts Py", "lua": "Parts Lua", "wasm": "Parts Wasm"}
PAGES = (1, 2, 3, 4, 5, 6)
CONSOLES = ("tdeck", "guition", "desk")
SHOTS = os.environ.get("MOYBYTE_PARTS_SHOTS")     # a folder: each page as a PNG there


def _host_wasm():
    from runtime import wasm_host
    from tools import jet_cart
    if wasm_host.available() and jet_cart.wasi_sdk(fetch=False) is not None:
        return True
    if os.environ.get("CI") or os.environ.get("MOYBYTE_REQUIRE_HOST_WASM"):
        pytest.fail("the host wasm binding or wasi-sdk is missing")
    return False


def _install(tier, root):
    src = os.path.join(FIX, "parts_%s.moy" % _EXT[tier])
    dst = os.path.join(root, os.path.basename(src))
    if tier == "wasm":
        if not _host_wasm():
            return False
        from tools import wasm_cart
        wasm_cart.build(src, dst)
    else:
        shutil.copytree(src, dst)
    return True


def _open(tier, base, console):
    """The fixture app of `tier` running on a fresh console: the T-Deck, the
    Guition S3 under its tap floor, or a window on a desk; None where the
    host has no such tier."""
    from runtime import host_app
    root = str(base / "carts")
    host_app.moy_carts.ensure_dirs(root)
    if not _install(tier, root):
        return None
    # the P4 as shipped (font scale 1 on its 7-inch glass), in the dark
    # variant; the T-Deck in the light one
    desk = console == "desk"
    if desk:
        ws = build_desktop_ws(base, font_scale=1, panel_diagonal_in=7.0)
    elif console == "guition":
        ws = host_app.build_workstation(root, sys_size=(480, 320), font_scale=1,
                                        windowed=False, panel_diagonal_in=3.5)
    else:
        ws = host_app.build_workstation(root)
    ws.look.set_theme("moy", persist=False, variant="light" if console == "tdeck" else "dark")
    ws.pointer.visible = False
    ws.bar_layer._clock_text = lambda: "10:30"
    if desk:
        ws.open_desk()
    cart = next(c for c in ws.carts.all if c["title"] == _TITLE[tier])
    ws._open_workspace(cart)
    ws.play(ws.launcher_layer)
    if ws.confirm.open:             # a copied app's consent: Open
        act = ws.confirm._act
        ws.confirm.cancel()
        act()
    assert ws.run.player.cart_error is None, (tier, ws.run.player.cart_error)
    return ws


def _set_page(ws, n):
    p = ws.run.player
    lua = getattr(p, "_lua", None)
    if lua is None:
        p.ns["pmem"](0, n)
        return
    run = getattr(lua, "_run", None)
    if run is not None and hasattr(run, "pmem_load"):
        run.pmem_load([n] + [0] * 255)
        return
    assert lua.exec("pmem(0, %d)" % n, "parts") is None


def _pmem1(ws):
    p = ws.run.player
    lua = getattr(p, "_lua", None)
    if lua is None:
        return p.ns["pmem"](1)
    run = getattr(lua, "_run", None)
    if run is not None and hasattr(run, "pmem"):
        return run.pmem()[1][1]
    img = array("i", bytearray(4 * 256))
    sys.modules[type(lua).__module__]._moycore.pmem_image(img)
    return img[1]


def _frame(drv):
    quiesce(drv.ws)
    drv.ws._dirty = True
    drv.frame(DT)


def _pixels(ws):
    """The run's canvas in its content rect, where every tier draws its page."""
    cv = ws.run.canvas
    x, y, w, h = ws.run.player.ns["content_rect"]()
    buf = cv._buf
    out = bytearray()
    for j in range(y, y + h):
        out += buf[2 * (j * cv.w + x):2 * (j * cv.w + x + w)]
    return (w, h), bytes(out)


def _shot(name, size, px):
    from tools import golden_png, pngwrite
    w, h = size
    pngwrite.write_png(os.path.join(SHOTS, name + ".png"),
                       golden_png.rgb565be_rows(px, w, h), w, h)


@pytest.fixture(scope="module")
def pages(tmp_path_factory):
    """{(console, tier, page): (size, pixels)}."""
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    out = {}
    for console in CONSOLES:
        for tier in TIERS:
            ws = _open(tier, tmp_path_factory.mktemp("%s_%s" % (console, tier)), console)
            if ws is None:
                continue
            drv = make_drv(ws)
            for page in PAGES:
                _set_page(ws, page)
                for _ in range(6):
                    _frame(drv)
                assert ws.run.player.cart_error is None, (tier, ws.run.player.cart_error)
                out[(console, tier, page)] = _pixels(ws)
                if SHOTS:
                    _shot("%s_%d_%s" % (console, page, tier), *out[(console, tier, page)])
                if page == 2:
                    assert _pmem1(ws) == 16, (console, tier, "the long bubble was not cut")
            ws.exit()
    return out


@pytest.mark.parametrize("page", PAGES)
@pytest.mark.parametrize("console", CONSOLES)
@pytest.mark.parametrize("tier", TIERS[1:])
def test_every_tier_draws_the_python_apps_page(pages, tier, console, page):
    if (console, tier, page) not in pages:
        pytest.skip("no host %s tier" % tier)
    size, px = pages[(console, tier, page)]
    rsize, rpx = pages[(console, "python", page)]
    assert size == rsize, (size, rsize)
    assert px == rpx, "%s on %s drew page %d other than python" % (tier, console, page)


@pytest.mark.parametrize("console", CONSOLES)
def test_the_pages_differ(pages, console):
    """Each page draws something of its own: the ground alone is no page."""
    blank = bytes(len(pages[(console, "python", PAGES[0])][1]))
    for page in PAGES:
        assert pages[(console, "python", page)][1] != blank


def test_the_tap_floor_lifts_a_lists_rows(pages):
    """Under the Guition's tap floor a list's current row is a 36 px band
    (MOY_UI_TAP_ROW); the T-Deck's keeps its own height."""
    def bands(console):
        (w, h), px = pages[(console, "python", 5)]
        x = w // 2
        col = [px[2 * (y * w + x):2 * (y * w + x) + 2] for y in range(h)]
        ground = max(set(col), key=col.count)
        runs, n = [], 0
        for i, c in enumerate(col):
            n = n + 1 if c != ground and i and c == col[i - 1] else (1 if c != ground else 0)
            if n and (i + 1 == h or col[i + 1] != c):
                runs.append(n)
        return runs
    assert max(bands("guition")) == 36, bands("guition")
    assert max(bands("tdeck")) < 36, bands("tdeck")
