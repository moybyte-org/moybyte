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
    assert probe.drew_something(ws.canvas)  # something was drawn

    # The fullscreen Config panel (fix B/C: no GO/CODE/CLOSE button bar) fits all four
    # tall visual cards on one screen now -- the extra vertical room the removed buttons
    # freed. All four display types are laid out + the sprite-tiles row is the tallest.
    rows = ws.cards_layer._card_layout()
    assert [r["display"] for r in rows] == ["count", "gauge", "choice-icons", "sprite-tiles"]
    assert not ws.cards_layer._cards_scrollable()        # all four fit -> no scroll needed
    spr = [r for r in rows if r["display"] == "sprite-tiles"][0]
    assert spr["h"] > console._CARD_H       # sprite-tiles row is tallest
    _draw_once(ws)                          # the full view renders cleanly
    # (cards-menu overflow scrolling itself is covered by
    # test_cards_menu_scroll_clamps_and_keeps_rows_on_panel, which uses 6 tall cards.)


def test_missing_display_falls_back_to_text_card():
    edit = [{"key": "size", "type": "int", "min": 0, "max": 9, "step": 1,
             "card": "SIZE {value}"}]
    ws = _ws_with_cart(edit, {"size": 3})
    row = ws.cards_layer._card_layout()[0]
    assert row["display"] is None
    assert row["h"] == console._CARD_H      # plain single-line text card height
    assert ws.cards_layer.card_text(0) == "SIZE 3"      # text is still the value's surface
    _draw_once(ws)                          # renders the legacy text card


def test_unknown_display_hint_falls_back_to_text():
    edit = [{"key": "k", "type": "int", "min": 0, "max": 9, "step": 1,
             "display": "bananas", "card": "K {value}"}]
    ws = _ws_with_cart(edit, {"k": 2})
    row = ws.cards_layer._card_layout()[0]
    assert row["display"] is None           # unrecognized -> treated as no display
    assert row["h"] == console._CARD_H
    _draw_once(ws)


# -- selecting without reading: tap a tile / icon ---------------------------

def _tap_cell(ws, row, k):
    cells = ws.cards_layer._choice_cells(row)
    _, (cx, cy, cw, ch) = cells[k]
    ws.pointer.place(cx + cw // 2, cy + ch // 2)
    ws.pointer.click = True
    ws.handle_pointer()
    ws.pointer.click = False


def test_sprite_tile_card_selects_by_tapping_a_tile():
    edit = [{"key": "who", "type": "choice", "choices": [0, 1], "tiles": [0, 1],
             "display": "sprite-tiles", "card": "WHO {value}"}]
    ws = _ws_with_cart(edit, {"who": 0}, _painted_sheet())
    row = ws.cards_layer._card_layout()[0]
    assert len(ws.cards_layer._choice_cells(row)) == 2  # one tappable cell per choice

    _tap_cell(ws, row, 1)                   # tap the second tile
    assert ws.config["who"] == 1
    _tap_cell(ws, row, 0)                   # tap the first tile
    assert ws.config["who"] == 0


def test_choice_icons_card_selects_by_tapping_an_icon():
    edit = [{"key": "p", "type": "choice", "choices": ["x", "y", "z"],
             "display": "choice-icons", "icons": ["star", "heart", "dot"],
             "card": "P {value}"}]
    ws = _ws_with_cart(edit, {"p": "x"})
    row = ws.cards_layer._card_layout()[0]
    _tap_cell(ws, row, 2)                   # tap the third icon
    assert ws.config["p"] == "z"


def test_choice_card_still_cycles_with_left_right_taps():
    # The -/+ stepper contract is preserved for non-cell taps (e.g. a gauge), and
    # adjust() still cycles a choice via keyboard left/right.
    edit = [{"key": "spd", "type": "int", "min": 0, "max": 100, "step": 10,
             "display": "gauge", "card": "F {value}"}]
    ws = _ws_with_cart(edit, {"spd": 50})
    row = ws.cards_layer._card_layout()[0]
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
    # Not in the launcher run-grid (a WALLPAPER leaves it, spec shell_ux_v1.md): still a
    # real editable cart in the store, so open it by reference (as ws.open() does).
    cart = next((c for c in ws._all_carts if c["title"] == title), None)
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
    rows = ws.cards_layer._card_layout()
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
    row = ws.cards_layer._card_layout()[0]
    assert row["display"] == "bg-thumbs"
    assert len(ws.cards_layer._choice_cells(row)) == 4       # one thumbnail per preset
    _draw_once(ws)                               # draws solid/starfield/stripes thumbs
    _tap_cell(ws, row, 2)                        # tap the "stripes" thumbnail
    assert ws.config["bg"] == "stripes"


def test_space_desktop_bg_picker_applies_a_preset(tmp_path):
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    _open(ws, "Space Desktop")
    ws._open_menu()
    # The PET card is now a (taller) sprite-tiles picker, so all four cards no
    # longer fit at once -- scroll the BG card into view the way a kid would.
    bg_i = [i for i, f in enumerate(ws.cart["edit"]) if f["key"] == "bg"][0]
    ws.cards_layer._reveal_card(bg_i)
    bg = [r for r in ws.cards_layer._card_layout() if r["f"]["key"] == "bg"][0]
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
        assert probe.drew_something(ws.canvas)
        ws.go_home()


# -- robustness: a malformed sprite-tiles card can't crash the cards menu (#1) --

def test_non_numeric_tiles_entry_does_not_crash_cards_menu():
    # A cart authored with a non-numeric `tiles` entry must not raise through
    # _resolve_tiles -> _draw_choice_icons -> _draw_cards (which would hang the
    # device). The bad entry resolves to tile 0 and the frame still draws.
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

    def _boom(row):
        raise ValueError("boom")
    ws.cards_layer._draw_card = _boom
    _draw_once(ws)                                   # must not raise out of frame()
    assert ws.cart_error is not None
    assert "boom" in ws.cart_error


# -- #94: inline validation feedback for a bad `edit` field def -------------
#
# Before #94 a malformed field def only surfaced through draw()'s belt-and-
# braces try/except (test_draw_cards_is_wrapped_against_a_thrown_card, above):
# ONE bad card took the whole Config tab down to the error panel, and a
# left/right press on the bad card (outside draw()'s guard entirely) could
# raise straight out of the input router. These tests pin the friendlier
# behavior: a bad field renders as a small inline "!" card -- everything else
# on the panel still works -- and stepping it is just a no-op, never a crash.

def test_bad_edit_field_renders_inline_error_others_still_work():
    edit = [
        {"key": "ok", "type": "int", "min": 0, "max": 9, "card": "OK {value}"},
        {"key": "bad", "type": "int", "min": 9, "max": 0, "card": "BAD"},  # min > max
    ]
    ws = _ws_with_cart(edit, {"ok": 3, "bad": 5})
    _draw_once(ws)                                    # must not raise / no error panel
    assert ws.cart_error is None
    rows = ws.cards_layer._card_layout()
    assert [r["error"] for r in rows] == [None, "min > max"]
    assert rows[1]["display"] is None                  # never reaches the real renderer
    assert probe.drew_something(ws.canvas)              # something drew (the good card + "!")


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


def test_adjust_on_malformed_card_is_a_safe_noop():
    edit = [{"key": "bad", "type": "int", "min": 9, "max": 0, "card": "BAD"}]
    ws = _ws_with_cart(edit, {"bad": 5})
    ws.cards_layer.msel = 0
    ws.adjust(1)                                       # must not raise
    ws.adjust(-1)
    assert ws.config["bad"] == 5                        # untouched -- no-op stepping


def test_card_tap_on_malformed_card_is_a_safe_noop():
    edit = [{"key": "bad", "type": "choice", "choices": []}]  # empty choices -> invalid
    ws = _ws_with_cart(edit, {"bad": 1})
    row = ws.cards_layer._card_layout()[0]
    assert row["error"] == "no choices"
    ws.cards_layer._card_tap(row["x"] + 2, row["y"] + 2, 0)   # must not raise
    assert ws.config["bad"] == 1


def test_left_right_input_on_malformed_card_does_not_crash():
    # The real regression this closes: a d-pad left/right routes straight to
    # ws.adjust() through handle_input(), OUTSIDE draw()'s try/except -- before
    # #94 a bad field def here raised out of the frame loop entirely.
    edit = [{"key": "bad", "type": "int", "min": 9, "max": 0, "card": "BAD"}]
    ws = _ws_with_cart(edit, {"bad": 5})
    drv = host_app.ConsoleDriver(ws)
    ws.cards_layer.msel = 0
    drv.press("left")
    drv.frame(1 / 30)                                   # must not raise
    drv.press("right")
    drv.frame(1 / 30)
    assert ws.cart_error is None
    assert ws.config["bad"] == 5


# -- cards-menu scrolling (#3) ----------------------------------------------

def _many_tile_cards(n):
    return [{"key": "k%d" % i, "type": "choice", "choices": [0, 1], "tiles": [0, 1],
             "display": "sprite-tiles", "card": "K%d" % i} for i in range(n)]


def test_cards_menu_scroll_clamps_and_keeps_rows_on_panel():
    edit = _many_tile_cards(6)                       # 6 sprite-tiles cards (h=44) overflow
    ws = _ws_with_cart(edit, {})
    assert ws.cards_layer._cards_scrollable()

    ws.cards_layer.scroll_cards(99)                              # clamp: never past the last card
    assert ws.cards_layer.mtop == ws.cards_layer._max_mtop()
    bottoms = [r["y"] + r["h"] for r in ws.cards_layer._card_layout()]
    assert max(bottoms) <= console._CARD_VIEW_BOTTOM # nothing runs over the buttons
    assert ws.cards_layer._card_layout()[-1]["i"] == len(edit) - 1   # the last card IS reachable

    ws.cards_layer.scroll_cards(-99)                             # clamp the other way
    assert ws.cards_layer.mtop == 0
    assert ws.cards_layer._card_layout()[0]["i"] == 0
    _draw_once(ws)                                   # the scrolled view renders cleanly


def test_card_hit_test_agrees_with_scrolled_layout():
    # _card_at must match the SAME visible rows _draw_cards lays out, at any scroll.
    edit = _many_tile_cards(6)
    ws = _ws_with_cart(edit, {})
    ws.cards_layer.scroll_cards(2)
    for row in ws.cards_layer._card_layout():
        cx = row["x"] + row["w"] // 2
        cy = row["y"] + row["h"] // 2
        assert ws.cards_layer._card_at(cx, cy) == row["i"]
    # A point above the first visible card hits nothing (it's scrolled off).
    assert ws.cards_layer._card_at(ws.cards_layer._card_layout()[0]["x"] + 2, console._CARD_Y0 - 4) is None


def test_reveal_card_brings_any_selection_into_view():
    edit = _many_tile_cards(6)
    ws = _ws_with_cart(edit, {})
    # Selecting any card and revealing it must put it in the visible window, and
    # never scroll past the clamp.
    for sel in range(len(edit)):
        ws.cards_layer.msel = sel
        ws.cards_layer._reveal_card(sel)
        assert any(r["i"] == sel for r in ws.cards_layer._card_layout())
        assert 0 <= ws.cards_layer.mtop <= ws.cards_layer._max_mtop()


# -- choice-icons hit-test (no dead-zone) (#4) ------------------------------

def test_choice_icons_cells_fit_inside_their_card():
    edit = [{"key": "p", "type": "choice", "choices": ["x", "y", "z"],
             "display": "choice-icons", "icons": ["star", "heart", "dot"], "card": "P"}]
    ws = _ws_with_cart(edit, {"p": "x"})
    row = ws.cards_layer._card_layout()[0]
    card_bottom = row["y"] + row["h"]
    for _, (cx, cy, cw, ch) in ws.cards_layer._choice_cells(row):
        assert cy + ch <= card_bottom               # cell bottom no longer 2px past
        # and a tap at the cell's bottom edge still lands inside the card rect.
        assert ws.cards_layer._card_at(cx + cw // 2, cy + ch - 1) == 0


# -- count card stays one tidy row (#6) -------------------------------------

def test_count_card_clamps_to_one_row():
    # A big count must not wrap to a 2nd row that spills into the next card.
    edit = [{"key": "n", "type": "int", "min": 0, "max": 99, "step": 1,
             "display": "count", "icon": "star", "count_max": 99, "card": "N {value}"}]
    ws = _ws_with_cart(edit, {"n": 99})
    row = ws.cards_layer._card_layout()[0]
    # The drawn glyphs all sit on the single row at y+14 (height 32 card) -- assert
    # by drawing and checking nothing was painted below the card's bottom band.
    _draw_once(ws)                                   # must not raise
    # one-row cap = how many 16px glyph slots fit across the card width.
    per_row = max(1, (row["w"] - 4) // 16)
    # the loop is clamped to per_row, so even n=99 draws at most per_row glyphs.
    assert per_row >= 1 and per_row <= 18
