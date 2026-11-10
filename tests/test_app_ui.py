"""What a responsive app's `ui` verbs do the same on every tier
(docs/ui_api_2026-10.md, docs/app_api_v1.md section 3), each held by one
fixture app per tier in tests/fixtures/app_ui/ running on the T-Deck's
320x240 console:

  * clip_*.moy -- ui verbs an app places in a table's parts draw inside
    the table's view: a row cut at the view's top or foot paints nothing
    over what lies above or below the table.
"""

import os
import shutil
import sys
from array import array

import pytest

from runtime import host_app
from ws_helpers import make_drv, open_cart, quiesce

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIX = os.path.join(ROOT, "tests", "fixtures", "app_ui")
DT = 1.0 / 30.0
TIERS = ("python", "lua")
_EXT = {"python": "py", "lua": "lua"}
_TITLE = {"python": "Py", "lua": "Lua"}


def _frame(drv):
    quiesce(drv.ws)
    drv.ws._dirty = True
    drv.frame(DT)


def _pmem(ws, n):
    p = ws.run.player
    lua = getattr(p, "_lua", None)
    if not lua:
        return [p.ns["pmem"](i) for i in range(n)]
    run = getattr(lua, "_run", None)
    if run is not None and hasattr(run, "pmem"):
        return list(run.pmem()[1])[:n]
    img = array("i", bytearray(4 * 256))
    sys.modules[type(lua).__module__]._moycore.pmem_image(img)
    return list(img[:n])


def _px(ws):
    """The run's canvas as a pixel reader, and its content rect."""
    cv = ws.run.canvas
    buf = cv._buf
    w = cv.w

    def at(x, y):
        i = 2 * (y * w + x)
        return buf[i] | buf[i + 1] << 8
    return at, ws.run.player.ns["content_rect"]()


def _open(app, tier, base):
    """The fixture `app` of `tier` running on a fresh T-Deck console."""
    root = str(base / "carts")
    host_app.moy_carts.ensure_dirs(root)
    name = "%s_%s.moy" % (app, _EXT[tier])
    shutil.copytree(os.path.join(FIX, name), os.path.join(root, name))
    ws = host_app.build_workstation(root)
    ws.look.set_theme("moy", persist=False)
    ws.pointer.visible = False
    ws.bar_layer._clock_text = lambda: "10:30"
    open_cart(ws, "%s %s" % (app.capitalize(), _TITLE[tier]))
    if ws.confirm.open:             # a copied app's consent: Open
        act = ws.confirm._act
        ws.confirm.cancel()
        act()
    assert ws.run.player.cart_error is None, (tier, ws.run.player.cart_error)
    return ws


def _content(ws):
    at, (x, y, w, h) = _px(ws)
    return bytes(b for j in range(y, y + h) for i in range(x, x + w)
                 for b in at(i, j).to_bytes(2, "little"))


# -- placed verbs in a table's parts draw inside its view ------------------------


@pytest.fixture(scope="module")
def clip(tmp_path_factory):
    """Each tier's (content pixels, the views, the ground's colour, a reader)."""
    out = {}
    for tier in TIERS:
        ws = _open("clip", tier, tmp_path_factory.mktemp("clip_" + tier))
        drv = make_drv(ws)
        for _ in range(6):
            _frame(drv)
        assert ws.run.player.cart_error is None, (tier, ws.run.player.cart_error)
        m = _pmem(ws, 8)
        at, (x, y, w, h) = _px(ws)
        views = [tuple(m[0:4]), tuple(m[4:8])]
        bands = {}
        for vx, vy, vw, vh in views:
            for top in (vy - 12, vy + vh):
                bands[(vx, top)] = [at(i, j) for j in range(top, top + 12)
                                    for i in range(vx, vx + vw)]
        out[tier] = (_content(ws), views, at(x, y + h - 1), bands)
        ws.exit()
    return out


@pytest.mark.parametrize("tier", TIERS)
def test_verbs_placed_in_a_part_draw_inside_the_tables_view(clip, tier):
    """A label and a button the app places in each row's part: the rows the
    view cuts paint nothing in the 12 pixels above or below it."""
    _px_, views, ground, bands = clip[tier]
    for (vx, top), band in sorted(bands.items()):
        stray = sum(1 for c in band if c != ground)
        assert stray == 0, "%s: %d pixels drawn past the view at x=%d y=%d" % (
            tier, stray, vx, top)


def test_the_parts_draw_the_same_pixels_on_every_tier(clip):
    assert clip["lua"][1] == clip["python"][1]
    assert clip["lua"][0] == clip["python"][0], "lua drew other pixels than python"
