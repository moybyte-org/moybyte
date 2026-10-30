"""Pre-literate UX (#15): the visual "Make it mine" cards.

A kid who can't read should be able to change how-fast / how-many / which-
character with icons only. These tests assert:
  * the per-field `display` hints ("gauge" | "count" | "choice-icons" |
    "sprite-tiles" | "bg-thumbs") render on the Settings page without error;
  * a field with NO `display` is an ordinary card (a slider for an int), and
    one that fails `_validate_field` stays off the page;
  * a sprite-tile / choice-icon card SELECTS by tapping a tile (no reading);
  * the showcase cart (Star Catcher, now sprite-tile catcher) still opens + runs
    headless, and the other seed carts still render their (text) cards.

Kept in its own file (per the issue) so it never collides with the existing
suites. Runs the same code path the device freezes (runtime/console.py).
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

from runtime import host_app  # noqa: E402  (registers the `editors` alias console needs)
from runtime import console  # noqa: E402  (import after host_app for the alias)
from runtime.editors import SpriteSheet  # noqa: E402
from runtime.host_canvas import make_system_canvas as Canvas  # noqa: E402
from runtime.input import InputState  # noqa: E402

import canvas_probe as probe  # noqa: E402  (pixel-width-agnostic "it drew" probes)


# -- a tiny in-memory Workstation fixture (no disk, no real cart) ------------

def _ws_with_cart(edit, cfg, sheet=None):
    """Build a host Workstation parked in the cards menu over an in-RAM cart with
    the given `edit` schema + `config`, ready to draw/hit-test cards."""
    canvas = Canvas(host_app.WIDTH, host_app.HEIGHT)
    inp = InputState()
    ws = console.Workstation(host_app._NullComp(), canvas, inp, [])
    ws.make_api = host_app.make_api
    ws.carts_store = host_app.moy_carts
    ws.set_pointer(console.Pointer(host_app.WIDTH, host_app.HEIGHT))
    inp.pointer = ws.pointer
    ws.cart = {"title": "T", "type": "app", "src": "", "cfg": dict(cfg),
               "edit": edit, "path": None}
    ws.config = dict(cfg)
    ws.sheet = sheet if sheet is not None else SpriteSheet()
    ws.screen = "menu"
    ws.menu_view = "cards"
    ws._draw = None
    ws._update = None
    return ws


def _draw_once(ws):
    ws.input.begin_frame()
    ws.frame(1 / 30)


def _painted_sheet():
    """A sheet with two recognizable, non-blank tiles (ids 0 and 1)."""
    sh = SpriteSheet()
    for i in range(sh.TILE):
        sh.tset(0, i, i, 11)        # green diagonal
        sh.tset(1, i, 0, 8)         # red top row
    return sh


# -- each display type renders without error --------------------------------

def _box(ws, key):
    """Card `key`'s row on the Settings page, as its last frame drew it."""
    return ws.cards_layer.page().host.box(key)


def test_all_display_types_render_without_error():
    sheet = _painted_sheet()
    edit = [
        {"key": "n", "type": "int", "min": 1, "max": 10, "step": 1,
         "display": "count", "icon": "star", "card": "DROP {value}"},
        {"key": "spd", "type": "int", "min": 0, "max": 100, "step": 10,
         "display": "gauge", "card": "FAST {value}"},
        {"key": "pick", "type": "choice", "choices": ["a", "b", "c"],
         "display": "choice-icons", "icons": ["star", "heart", "dot"],
         "card": "PICK {value}"},
        {"key": "who", "type": "choice", "choices": [0, 1], "tiles": [0, 1],
         "display": "sprite-tiles", "card": "WHO {value}"},
    ]
    cfg = {"n": 5, "spd": 50, "pick": "b", "who": 1}
    ws = _ws_with_cart(edit, cfg, sheet)
    _draw_once(ws)                          # must not raise
    assert ws.cart_error is None
    assert probe.drew_something(ws.canvas)  # something was drawn
    # every card is on the page: the meters as sliders, the pickers as tiles
    for key in ("n", "spd", "pick", "who"):
        assert _box(ws, key) is not None, key
    tiles = ws.cards_layer.page()._tiles
    assert len(tiles["pick"]) == 3 and len(tiles["who"]) == 2


def test_missing_display_is_an_ordinary_card():
    edit = [{"key": "size", "type": "int", "min": 0, "max": 9, "step": 1,
             "card": "SIZE {value}"}]
    ws = _ws_with_cart(edit, {"size": 3})
    _draw_once(ws)
    assert _box(ws, "size") is not None     # a slider, its value at its right
    assert "size" not in ws.cards_layer.page()._tiles


def test_an_unknown_display_hint_leaves_the_card_off_the_page():
    edit = [{"key": "k", "type": "int", "min": 0, "max": 9, "step": 1,
             "display": "bananas", "card": "K {value}"},
            {"key": "ok", "type": "int", "min": 0, "max": 9, "card": "OK {value}"}]
    ws = _ws_with_cart(edit, {"k": 2, "ok": 1})
    _draw_once(ws)
    assert ws.cart_error is None
    assert _box(ws, "k") is None and _box(ws, "ok") is not None


# -- selecting without reading: tap a tile / icon ---------------------------

def _tap_cell(ws, key, k):
    """A tap on choice `k`'s tile of card `key` on the Settings page, where
    its last frame drew it."""
    _draw_once(ws)
    (cx, cy, cw, ch), _k = ws.cards_layer.page()._tiles[key][k]
    ws.pointer.place(cx + cw // 2, cy + ch // 2)
    ws.pointer.click = True
    ws.handle_pointer()
    ws.pointer.click = False


def test_sprite_tile_card_selects_by_tapping_a_tile():
    edit = [{"key": "who", "type": "choice", "choices": [0, 1], "tiles": [0, 1],
             "display": "sprite-tiles", "card": "WHO {value}"}]
    ws = _ws_with_cart(edit, {"who": 0}, _painted_sheet())
    _draw_once(ws)
    assert len(ws.cards_layer.page()._tiles["who"]) == 2  # one tappable tile per choice

    _tap_cell(ws, "who", 1)                 # tap the second tile
    assert ws.config["who"] == 1
    _tap_cell(ws, "who", 0)                 # tap the first tile
    assert ws.config["who"] == 0


def test_choice_icons_card_selects_by_tapping_an_icon():
    edit = [{"key": "p", "type": "choice", "choices": ["x", "y", "z"],
             "display": "choice-icons", "icons": ["star", "heart", "dot"],
             "card": "P {value}"}]
    ws = _ws_with_cart(edit, {"p": "x"})
    _draw_once(ws)
    rx, ry, rw, rh = _box(ws, "p")
    for (cx, cy, cw, ch), _k in ws.cards_layer.page()._tiles["p"]:
        # every tile lies inside its card's row: no dead zone at its foot
        assert rx <= cx and cx + cw <= rx + rw and ry <= cy and cy + ch <= ry + rh
    _tap_cell(ws, "p", 2)                   # tap the third icon
    assert ws.config["p"] == "z"


def test_an_int_card_steps_by_its_step_with_left_and_right():
    # A gauge card is a slider on the Settings page: left and right step it
    # by the card's own step.
    edit = [{"key": "spd", "type": "int", "min": 0, "max": 100, "step": 10,
             "display": "gauge", "card": "F {value}"}]
    ws = _ws_with_cart(edit, {"spd": 50})
    drv = host_app.ConsoleDriver(ws)
    drv.frame(1 / 30)
    drv.press("right")
    drv.frame(1 / 30)
    assert ws.config["spd"] == 60
    drv.press("left")
    drv.frame(1 / 30)
    assert ws.config["spd"] == 50


# -- the showcase cart: Star Catcher with real sprite-tile catcher ----------

def _open(ws, title):
    for i, c in enumerate(ws.launcher.items):
        if c["title"] == title:
            ws.launcher.sel = i
            ws.open()
            return
    # Not in the launcher run-grid (a WALLPAPER leaves it, spec shell_ux_v1.md): still a
    # real editable cart in the store, so open it by reference (as ws.open() does).
    cart = next((c for c in ws.carts.all if c["title"] == title), None)
    if cart is None:
        raise AssertionError("cart not found: " + title)
    ws._open_workspace(cart)
    ws.run(ws.project, ws.launcher_layer)


def test_showcase_star_catcher_opens_and_runs_headless(tmp_path):
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    _open(ws, "Star Catcher")
    assert ws.screen == "desktop" and ws.cart_error is None

    # the catcher is now a REAL sprite tile (frog id 0, robot id 1) -- both painted.
    assert ws.sheet.tile_image(0).pix.count(0) < 64     # tile 0 not blank
    assert ws.sheet.tile_image(1).pix.count(0) < 64     # tile 1 not blank

    for _ in range(120):                                # attract mode, no crash
        ws.input.begin_frame()
        ws.frame(1 / 30)
    assert ws.cart_error is None

    # its CATCHER card is a sprite-tile picker.
    ws._open_menu()
    basket = [f for f in ws.cart["edit"] if f["key"] == "basket"][0]
    assert basket["display"] == "sprite-tiles"
    _tap_cell(ws, "basket", 1)                          # pick the robot tile
    assert ws.config["basket"] == 1
    ws.apply()                                          # re-run with the new pick
    assert ws.cart_error is None
    assert ws.ns["catcher"] == 1


def test_showcase_runs_with_a_stale_string_basket(tmp_path):
    # An older config may still carry basket="frog"; the cart must not crash.
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    _open(ws, "Star Catcher")
    ws.config["basket"] = "robot"
    ws.apply()
    assert ws.cart_error is None
    assert ws.ns["catcher"] == 1                        # mapped name -> tile id


# -- P3: background picker (tap a thumbnail) --------------------------------

def test_bg_thumbs_card_renders_and_selects_by_tapping_a_thumbnail():
    edit = [{"key": "bg", "type": "choice",
             "choices": ["dark_blue", "night", "stripes", "indigo"],
             "display": "bg-thumbs", "card": "SKY {value}"}]
    ws = _ws_with_cart(edit, {"bg": "dark_blue"})
    _draw_once(ws)                               # draws solid/starfield/stripes thumbs
    assert len(ws.cards_layer.page()._tiles["bg"]) == 4      # one thumbnail per preset
    _tap_cell(ws, "bg", 2)                       # tap the "stripes" thumbnail
    assert ws.config["bg"] == "stripes"


def test_space_desktop_bg_picker_applies_a_preset(tmp_path):
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    _open(ws, "Space Desktop")
    ws._open_menu()
    bg = [f for f in ws.cart["edit"] if f["key"] == "bg"][0]
    assert bg["display"] == "bg-thumbs"
    ws.cards_layer.page().host.focus("bg")       # the page scrolls it into view
    _draw_once(ws)
    _tap_cell(ws, "bg", 2)                       # "stripes"
    assert ws.config["bg"] == "stripes"
    ws.apply()                                   # the cart paints stripes, no crash
    assert ws.cart_error is None
    for _ in range(10):
        ws.input.begin_frame()
        ws.frame(1 / 30)
    assert ws.cart_error is None


# -- back-compat: existing seed carts still render their cards --------------

def test_existing_seed_carts_still_render_their_cards(tmp_path):
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    for title in ("Pixel Pet", "Tiny Runner", "Hop Quest", "Tap Only Red"):
        _open(ws, title)
        if not ws.cart.get("edit"):
            ws.go_home()
            continue
        ws._open_menu()
        assert ws.menu_view == "cards"
        ws.input.begin_frame()
        ws.frame(1 / 30)                                # draws cards, no error
        assert probe.drew_something(ws.canvas)
        ws.go_home()


# -- robustness: a malformed sprite-tiles card can't crash the cards menu (#1) --

def test_non_numeric_tiles_entry_does_not_crash_cards_menu():
    # A cart authored with a non-numeric `tiles` entry must not raise through
    # _resolve_tiles -> draw_choice (which would hang the device). The bad
    # entry resolves to tile 0 and the frame still draws.
    edit = [{"key": "who", "type": "choice", "choices": ["a", "b"],
             "tiles": ["oops", 1], "display": "sprite-tiles", "card": "WHO {value}"}]
    ws = _ws_with_cart(edit, {"who": "a"}, _painted_sheet())
    assert ws.cards_layer._resolve_tiles(edit[0]) == [0, 1]     # bad entry -> 0, good one kept
    _draw_once(ws)                                   # must not raise
    assert ws.cart_error is None                     # no crash captured


def test_draw_cards_is_wrapped_against_a_thrown_card():
    # Belt-and-braces: even if a card raises for some unforeseen reason, frame()
    # must capture it (not let it escape the loop) and paint the error panel.
    ws = _ws_with_cart([{"key": "k", "type": "int", "min": 0, "max": 9, "card": "K"}],
                       {"k": 1})

    def _boom():
        raise ValueError("boom")
    ws.cards_layer._draw_cards = _boom
    _draw_once(ws)                                   # must not raise out of frame()
    assert ws.cart_error is not None
    assert "boom" in ws.cart_error


# -- #94: a bad `edit` field def costs one card, never the tab --------------
#
# Before #94 a malformed field def only surfaced through draw()'s belt-and-
# braces try/except (test_draw_cards_is_wrapped_against_a_thrown_card, above):
# ONE bad card took the whole Config tab down to the error panel, and a
# left/right press on it could raise straight out of the input router. The
# Settings page leaves a field `_validate_field` refuses off the page.

def test_bad_edit_field_stays_off_the_page_others_still_work():
    edit = [
        {"key": "ok", "type": "int", "min": 0, "max": 9, "card": "OK {value}"},
        {"key": "bad", "type": "int", "min": 9, "max": 0, "card": "BAD"},  # min > max
    ]
    ws = _ws_with_cart(edit, {"ok": 3, "bad": 5})
    _draw_once(ws)                                    # must not raise / no error panel
    assert ws.cart_error is None
    assert _box(ws, "ok") is not None and _box(ws, "bad") is None
    assert probe.drew_something(ws.canvas)


def test_validate_field_catches_the_known_bad_shapes():
    v = _ws_with_cart([], {}).cards_layer._validate_field
    assert v({"key": "k", "type": "int", "min": 0, "max": 9}) is None       # fine
    assert v({"key": "k", "type": "choice", "choices": [1, 2]}) is None    # fine
    assert v("not a dict") is not None
    assert v({}) is not None                                                # no key
    assert v({"key": "k"}) is not None                                      # no/bad type
    assert v({"key": "k", "type": "int", "min": 5, "max": 1}) is not None   # min > max
    assert v({"key": "k", "type": "int", "step": 0}) is not None            # step 0
    assert v({"key": "k", "type": "choice", "choices": []}) is not None     # empty choices
    assert v({"key": "k", "type": "choice"}) is not None                    # missing choices
    assert v({"key": "k", "type": "int", "display": "choice-icons"}) is not None  # display/type mismatch
    assert v({"key": "k", "type": "int", "display": "nope"}) is not None    # unknown display


def test_left_right_input_on_malformed_card_does_not_crash():
    edit = [{"key": "bad", "type": "int", "min": 9, "max": 0, "card": "BAD"}]
    ws = _ws_with_cart(edit, {"bad": 5})
    drv = host_app.ConsoleDriver(ws)
    drv.press("left")
    drv.frame(1 / 30)                                   # must not raise
    drv.press("right")
    drv.frame(1 / 30)
    assert ws.cart_error is None
    assert ws.config["bad"] == 5


# -- many cards: the page scrolls (#3) --------------------------------------

def _many_tile_cards(n):
    return [{"key": "k%d" % i, "type": "choice", "choices": [0, 1], "tiles": [0, 1],
             "display": "sprite-tiles", "card": "K%d" % i} for i in range(n)]


def test_many_cards_scroll_the_focus_and_files_into_view():
    edit = _many_tile_cards(6)                       # more tile rows than one 240px page
    ws = _ws_with_cart(edit, {})
    drv = host_app.ConsoleDriver(ws)
    drv.frame(1 / 30)
    page = ws.cards_layer.page()
    lay = ws.cards_layer.layout
    for _ in range(3 * len(edit)):
        if page._focus == "Files":
            break
        drv.press("down")
        for _ in range(30):                          # the reveal glides (§7)
            drv.frame(1 / 30)
        x, y, w, h = page.host.box(page._focus)
        assert lay.cy <= y and y + h <= lay.cy + lay.ch, page._focus   # the focus shows
    assert page._focus == "Files"                    # the last door is reachable
