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
  7  ui.list's two-line rows in a card with no caption: each row's lead (an
     icon, or a person's initials), title, tag or mark, time, second line
     and count badge (`o.subs`, `icons`, `values`, `tags`, `marks`,
     `counts`), then a link in no group.
  8  a settings page in a ui.scroll region under `snap`, run on past its
     foot: whole units only (a caption with its first row, each row, each
     of a list's rows) and the "More below" cue in the band the foot leaves.
  9  ui.note: a warning's and a paste note's card on the page; a link's
     and an info's tag (`o.tag`).
 10  fields: two in a group's rows at their `max` (the short name given five
     bytes keeps four), an off one (`o.disabled`) showing its hint, and a
     focused one whose text runs past it, its tail led by an ellipsis.
 11  a dialog with a stop in its body opening on its Cancel (`o.focus`).
 12  the console's name (`moybyte.name()`, a compiled app's moybyte_name
     row) as an info row's value.
 13  a focused field of `max` 8 holding "ab", before and after the
     console's Paste (Ctrl+V) brings it PASTE: the room takes "čaj" and
     leaves the cup, whose three bytes would run past it; pmem 1 the
     field's length in bytes.
 14  a chat's history in a ui.scroll region under `snap` and `follow`: it
     rests on its foot, whole bubbles only, and the "Older messages" cue
     in the gap its top leaves.

On the handheld consoles page 8 is dragged too: a drag let go settles its
scroll onto a unit's top.
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
PAGES = (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 14)
CONSOLES = ("tdeck", "guition", "desk")
SHOTS = os.environ.get("MOYBYTE_PARTS_SHOTS")     # a folder: each page as a PNG there
PASTE = "\u010daj\u2615x"                    # "čaj", a cup (three bytes), "x"
PASTED = 6                                      # "ab" and "čaj": the cup would run past 8


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
    from tools import pngwrite
    from pixel_goldens import rgb565_rows
    w, h = size
    pngwrite.write_png(os.path.join(SHOTS, name + ".png"),
                       rgb565_rows(px, w, h), w, h)


def _offset(ws, x, y):
    """The scroll of the page under (x, y), a Python app's (None elsewhere)."""
    ui = getattr(ws.run.player, "_ui", None)
    if getattr(ws.run.player, "_lua", None) is not None or not ui:
        return None
    from runtime import ui_draw
    return ui_draw._C.api_offset(ui[0], x, y)


def _drag(ws, drv, x, y, dy):
    """A drag of `dy` px from (x, y), held still and let go, then frames
    enough to settle: the pixels, the scroll as the finger let go and
    after."""
    drv.touch(x, y)
    _frame(drv)
    for i in range(1, 7):
        drv.touch_drag(x, y + dy * i // 6)
        _frame(drv)
    for _ in range(6):
        drv.touch_drag(x, y + dy)       # held still: let go with no fling
        _frame(drv)
    held = _offset(ws, x, y)
    drv.touch_up()
    for _ in range(90):
        _frame(drv)
    return _pixels(ws), held, _offset(ws, x, y)


def _drags(ws, drv):
    """Page 8 dragged up 30 px, back to its top, then up 33 px."""
    _set_page(ws, 8)
    for _ in range(6):
        _frame(drv)
    x, y, w, h = ws.run.player.ns["content_rect"]()
    x, y = x + w // 2, y + h // 2
    first = _drag(ws, drv, x, y, -30)
    _drag(ws, drv, x, y - 60, 200)
    return first, _drag(ws, drv, x, y, -33)


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
                lent = ws.bar_layer._lent("tool")
                out[(console, tier, page, "bar")] = lent and lent[0]
                if SHOTS:
                    _shot("%s_%d_%s" % (console, page, tier), *out[(console, tier, page)])
                if page == 2:
                    assert _pmem1(ws) == 16, (console, tier, "the long bubble was not cut")
            _set_page(ws, 13)
            for _ in range(6):
                _frame(drv)
            out[(console, tier, "unpasted")] = _pixels(ws)
            ws.clipboard.put_text(PASTE)
            drv.type_char(0x16)             # Ctrl+V
            for _ in range(6):
                _frame(drv)
            assert ws.run.player.cart_error is None, (tier, ws.run.player.cart_error)
            out[(console, tier, "pasted")] = _pixels(ws), _pmem1(ws)
            if SHOTS:
                _shot("%s_paste_%s" % (console, tier), *out[(console, tier, "pasted")][0])
            if console != "desk":
                out[(console, tier, "drag")] = _drags(ws, drv)
                if SHOTS:
                    _shot("%s_drag_%s" % (console, tier), *out[(console, tier, "drag")][0][0])
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
@pytest.mark.parametrize("tier", TIERS)
def test_the_page_title_is_the_bars(pages, tier, console):
    """`ui.page("Parts")` draws no head in the page: its title is what the
    app lends the host's bar, from a Lua or compiled app's own context as
    from a Python app's."""
    if (console, tier, 1) not in pages:
        pytest.skip("no host %s tier" % tier)
    assert pages[(console, tier, 1, "bar")] == "Parts"


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


def _rules(pages, console, page):
    """The rows of `page` on `console` that are one colour across the middle
    of the room and differ from the rows either side: a group's rules."""
    (w, h), px = pages[(console, "python", page)]
    x = w // 2

    def at(xx, y):
        return px[2 * (y * w + xx):2 * (y * w + xx) + 2]
    return [y for y in range(1, h - 1)
            if all(at(xx, y) == at(x, y) for xx in range(x - 40, x + 40))
            and at(x, y) != at(x, y - 1) and at(x, y) != at(x, y + 1)]


@pytest.mark.parametrize("console,pitch", (("tdeck", 34), ("guition", 44), ("desk", 42)))
def test_two_line_rows_take_the_consoles_height(pages, console, pitch):
    """A two-line row is 34 px on the T-Deck, 44 under the Guition's tap
    floor and 42 in a desk window: the rules heading its rows lie that far
    apart (the desk's card rings its rows, its top a pixel over them)."""
    rules = _rules(pages, console, 7)
    top = 1 if console == "desk" else 0
    assert rules[:3] == [top + pitch, top + 2 * pitch, top + 3 * pitch], rules


@pytest.mark.parametrize("console", CONSOLES)
@pytest.mark.parametrize("tier", TIERS)
def test_the_consoles_paste_reaches_the_focused_field(pages, tier, console):
    """Ctrl+V brings the console's clipboard into the focused field on every
    tier, as many whole characters as its `max` holds, and draws the same
    pixels as a Python app's."""
    if (console, tier, "pasted") not in pages:
        pytest.skip("no host %s tier" % tier)
    (size, px), n = pages[(console, tier, "pasted")]
    assert n == PASTED, (tier, console, n)
    assert px != pages[(console, tier, "unpasted")][1], "the paste drew nothing"
    assert px == pages[(console, "python", "pasted")][0][1]


@pytest.mark.parametrize("console", ("tdeck", "guition"))
@pytest.mark.parametrize("tier", TIERS)
def test_a_drag_let_go_settles_onto_a_units_top(pages, tier, console):
    """A snapping region dragged 30 px and let go, and 33 px, rests at one
    scroll: the drag's own offset settles onto the unit's top nearest it,
    drawn alike on every tier."""
    if (console, tier, "drag") not in pages:
        pytest.skip("no host %s tier" % tier)
    (a, held_a, rest_a), (b, held_b, rest_b) = pages[(console, tier, "drag")]
    assert a == b, "two drags near one unit rested apart"
    assert a == pages[(console, "python", "drag")][0][0]
    if tier == "python":
        assert held_a != rest_a and held_b != rest_b, (held_a, rest_a, held_b, rest_b)
        assert rest_a == rest_b > 0, (rest_a, rest_b)


def test_the_desks_card_rings_its_rows(pages):
    """On the desk `ui.group("", {card = true})` is a card as the mocks draw
    it, not rules alone: a column at its left, beside its rows, is one
    colour (the rules') from its top to its foot, and not the ground."""
    (w, h), px = pages[("desk", "python", 7)]

    def at(x, y):
        return px[2 * (y * w + x):2 * (y * w + x) + 2]
    ground = at(w // 2, h - 4)
    rule = at(w // 2, _rules(pages, "desk", 7)[0])
    edge = [x for x in range(24) if all(at(x, y) == rule for y in range(12, 4 * 42 - 12))]
    assert edge and rule != ground, edge


@pytest.mark.parametrize("console,pitch", (("guition", 36), ("tdeck", None)))
def test_the_tap_floor_lifts_info_and_link_rows(pages, console, pitch):
    """Under the Guition's tap floor an info row is 36 px tall
    (MOY_UI_TAP_ROW), as a list's row is: the rules between page 3's info
    rows lie that far apart; the T-Deck's keep their own height."""
    rules = _rules(pages, console, 3)
    gaps = [b - a for a, b in zip(rules, rules[1:])][:2]     # the card's top, two rules
    if pitch is None:
        assert gaps and max(gaps) < 36, rules
    else:
        assert gaps == [pitch, pitch], rules
