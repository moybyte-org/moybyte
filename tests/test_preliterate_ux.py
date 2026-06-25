"""Pre-literate UX (#15): the visual "Make it mine" cards.

A kid who can't read should be able to change how-fast / how-many / which-
character with icons only. These tests assert:
  * the new per-field `display` hints ("gauge" | "count" | "choice-icons" |
    "sprite-tiles") render through the SHARED console without error;
  * a field with NO `display` (and an unknown one) falls back to today's text
    card -- the back-compat guarantee for every existing cart;
  * a sprite-tile / choice-icon card SELECTS by tapping a tile (no reading);
  * the showcase cart (Star Catcher, now sprite-tile catcher) still opens + runs
    headless, and the other seed carts still render their (text) cards.

Kept in its own file (per the issue) so it never collides with the existing
suites. Runs the same code path the device freezes (runtime/console.py).
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from runtime import host_app  # noqa: E402  (registers the `editors` alias console needs)
from runtime import console  # noqa: E402  (import after host_app for the alias)
from runtime.canvas import Canvas, SpriteSheet  # noqa: E402
from runtime.input import InputState  # noqa: E402


# -- a tiny in-memory Workstation fixture (no disk, no real cart) ------------

def _ws_with_cart(edit, cfg, sheet=None):
    """Build a host Workstation parked in the cards menu over an in-RAM cart with
    the given `edit` schema + `config`, ready to draw/hit-test cards."""
    canvas = Canvas(host_app.WIDTH, host_app.HEIGHT)
    inp = InputState()
    ws = console.Workstation(host_app._NullComp(), canvas, inp, [])
    ws.make_api = host_app.make_api
    ws.carts_store = host_app.kid_carts
    ws.pointer = console.Pointer(host_app.WIDTH, host_app.HEIGHT)
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
    assert len(set(ws.canvas.buf)) > 1      # something was drawn

    # every card got a layout row; visual cards are taller than the text default.
    rows = ws._card_layout()
    assert [r["display"] for r in rows] == ["count", "gauge", "choice-icons", "sprite-tiles"]
    assert rows[3]["h"] > console._CARD_H   # sprite-tiles row is tallest


def test_missing_display_falls_back_to_text_card():
    edit = [{"key": "size", "type": "int", "min": 0, "max": 9, "step": 1,
             "card": "SIZE {value}"}]
    ws = _ws_with_cart(edit, {"size": 3})
    row = ws._card_layout()[0]
    assert row["display"] is None
    assert row["h"] == console._CARD_H      # plain single-line text card height
    assert ws.card_text(0) == "SIZE 3"      # text is still the value's surface
    _draw_once(ws)                          # renders the legacy text card


def test_unknown_display_hint_falls_back_to_text():
    edit = [{"key": "k", "type": "int", "min": 0, "max": 9, "step": 1,
             "display": "bananas", "card": "K {value}"}]
    ws = _ws_with_cart(edit, {"k": 2})
    row = ws._card_layout()[0]
    assert row["display"] is None           # unrecognized -> treated as no display
    assert row["h"] == console._CARD_H
    _draw_once(ws)


# -- selecting without reading: tap a tile / icon ---------------------------

def _tap_cell(ws, row, k):
    cells = ws._choice_cells(row)
    _, (cx, cy, cw, ch) = cells[k]
    ws.pointer.place(cx + cw // 2, cy + ch // 2)
    ws.pointer.click = True
    ws.handle_pointer()
    ws.pointer.click = False


def test_sprite_tile_card_selects_by_tapping_a_tile():
    edit = [{"key": "who", "type": "choice", "choices": [0, 1], "tiles": [0, 1],
             "display": "sprite-tiles", "card": "WHO {value}"}]
    ws = _ws_with_cart(edit, {"who": 0}, _painted_sheet())
    row = ws._card_layout()[0]
    assert len(ws._choice_cells(row)) == 2  # one tappable cell per choice

    _tap_cell(ws, row, 1)                   # tap the second tile
    assert ws.config["who"] == 1
    _tap_cell(ws, row, 0)                   # tap the first tile
    assert ws.config["who"] == 0


def test_choice_icons_card_selects_by_tapping_an_icon():
    edit = [{"key": "p", "type": "choice", "choices": ["x", "y", "z"],
             "display": "choice-icons", "icons": ["star", "heart", "dot"],
             "card": "P {value}"}]
    ws = _ws_with_cart(edit, {"p": "x"})
    row = ws._card_layout()[0]
    _tap_cell(ws, row, 2)                   # tap the third icon
    assert ws.config["p"] == "z"


def test_choice_card_still_cycles_with_left_right_taps():
    # The -/+ stepper contract is preserved for non-cell taps (e.g. a gauge), and
    # adjust() still cycles a choice via keyboard left/right.
    edit = [{"key": "spd", "type": "int", "min": 0, "max": 100, "step": 10,
             "display": "gauge", "card": "F {value}"}]
    ws = _ws_with_cart(edit, {"spd": 50})
    row = ws._card_layout()[0]
    ws.pointer.place(row["x"] + row["w"] - 4, row["y"] + 4)   # right half = +
    ws.pointer.click = True
    ws.handle_pointer()
    ws.pointer.click = False
    assert ws.config["spd"] == 60
    ws.pointer.place(row["x"] + 2, row["y"] + 4)              # left half = -
    ws.pointer.click = True
    ws.handle_pointer()
    assert ws.config["spd"] == 50


# -- the showcase cart: Star Catcher with real sprite-tile catcher ----------

def _open(ws, title):
    for i, c in enumerate(ws.launcher.items):
        if c["title"] == title:
            ws.launcher.sel = i
            ws.open()
            return
    raise AssertionError("cart not found: " + title)


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
    rows = ws._card_layout()
    basket = [r for r in rows if r["f"]["key"] == "basket"][0]
    assert basket["display"] == "sprite-tiles"
    _tap_cell(ws, basket, 1)                            # pick the robot tile
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
    row = ws._card_layout()[0]
    assert row["display"] == "bg-thumbs"
    assert len(ws._choice_cells(row)) == 4       # one thumbnail per preset
    _draw_once(ws)                               # draws solid/starfield/stripes thumbs
    _tap_cell(ws, row, 2)                        # tap the "stripes" thumbnail
    assert ws.config["bg"] == "stripes"


def test_space_desktop_bg_picker_applies_a_preset(tmp_path):
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    _open(ws, "Space Desktop")
    ws._open_menu()
    rows = ws._card_layout()
    bg = [r for r in rows if r["f"]["key"] == "bg"][0]
    assert bg["display"] == "bg-thumbs"
    _tap_cell(ws, bg, 2)                         # "stripes"
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
        assert len(set(ws.canvas.buf)) > 1
        ws.go_home()
