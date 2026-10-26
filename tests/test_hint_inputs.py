"""The hints name ACTIONS and draw the keys of the input in use
(docs/ui_api_2026-10.md, "Hints"; native/moy_ui/moy_ui.h, MOY_UI_INPUT_*):

* each input draws its own keys for an action -- the desk keyboard's Esc, the
  T-Deck's K, a gamepad's B -- and a key spelled out draws as written on
  every input, so no caller spells out a key an action covers;
* under touch alone no hint draws and `ui.hints` takes no band, so the rows
  reach the page's foot;
* the input table names the source that brought news (a button going down, a
  new key, a press of its pointer) once, and the console switches the input
  in use to the kind the board declared for that source, ignoring a source it
  did not declare;
* every board's declaration names kinds the engine knows.
"""

import ast
import os
import re
from array import array
from pathlib import Path

import pytest

from runtime import moy_input, ui, ui_draw
from runtime.host_canvas import make_system_canvas
from ws_helpers import build_ws, make_drv

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BAND = (0, 220, 320, 20)


@pytest.fixture(autouse=True)
def _keys_after():
    yield
    ui_draw.input_kind("keys")


@pytest.fixture(scope="module")
def th(tmp_path_factory):
    ws = build_ws(tmp_path_factory.mktemp("hints"))
    ws.look.set_theme("moy", persist=False, variant="dark")
    return ws.theme_colors


def _strip(th, items, kind):
    ui_draw.input_kind(kind)
    cv = make_system_canvas(320, 240)
    spans = ui_draw.hints(cv, th, BAND, items)
    return bytes(cv._buf), spans


@pytest.mark.parametrize("action,spelled", [("back", "Esc"), ("confirm", "enter"),
                                            ("tabs", "Q E")])
def test_a_spelled_out_key_names_no_action(th, action, spelled):
    assert _strip(th, (spelled, "Go"), "pad") != _strip(th, (action, "Go"), "pad")
    assert _strip(th, (spelled, "Go"), "pad") == _strip(th, (spelled, "Go"), "keys")


# Every key an input does an action with (moy_ui_parts.c's ACT_KEYS), and the
# spellings the hints took before they named actions.
_ACTION_KEYS = {"enter", "Esc", "esc", "left right", "Q E", "K", "Super", "Mic",
                "A", "B", "L R", "Start", "/", "Sym G", "up down", "up down left right",
                "left right up down", "A D ball_lr", "WASD", "W S"}


def _py_callers():
    for d in ("runtime", "device", "system_carts", "tests/fixtures"):
        for p in sorted(Path(ROOT, d).rglob("*.py")):
            src = p.read_text()
            if "hints(" in src and p.name != "ui_draw.py":
                yield p, src


def test_no_caller_spells_out_a_key_an_action_covers():
    """A hint pair names `confirm`, `back`, `change`, `tabs`, `menu` or `cancel`, so
    every input draws its own keys: a caller that spells out `Esc` shows a
    keyboard's cap on a gamepad. Every string tuple or list in a module that
    calls the hints, every Lua `ui.hints{...}` and every compiled fixture's
    `ui_hints` items is held to it."""
    bad = []
    for p, src in _py_callers():
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, (ast.Tuple, ast.List)):
                # a pair's keys come first, its words second
                for e in node.elts[::2]:
                    if isinstance(e, ast.Constant) and e.value in _ACTION_KEYS:
                        bad.append("%s:%d %r" % (p.relative_to(ROOT), e.lineno, e.value))
    for p in sorted(Path(ROOT).glob("tests/fixtures/**/*.lua")) + sorted(
            Path(ROOT).glob("system_carts/*/*.lua")):
        for m in re.finditer(r"ui\.hints\s*\(?\s*\{([^}]*)\}", p.read_text()):
            for k in re.findall(r'"([^"]*)"', m.group(1))[::2]:
                if k in _ACTION_KEYS:
                    bad.append("%s %r" % (p.relative_to(ROOT), k))
    for p in sorted(Path(ROOT).glob("tests/fixtures/wasm/*/src/main.wat")):
        src = p.read_text()
        data = dict(re.findall(r'\(data \(i32\.const (\d+)\) "([^"]*)"\)', src))
        for at in re.findall(r"call \$ui_hints \(i32\.const (\d+)\)", src):
            for k in data[at].split("\\00")[::2]:
                if k in _ACTION_KEYS:
                    bad.append("%s %r" % (p.relative_to(ROOT), k))
    assert not bad, "name the action instead: %s" % bad


def test_each_input_draws_its_own_keys_for_an_action(th):
    keys = {k: _strip(th, ("back", "Back", "tabs", "Tabs"), k) for k in ("keys", "tdeck", "pad")}
    assert len({px for px, _sp in keys.values()}) == 3, "Esc, K and B differ"
    # Esc is a word's cap; K and B are square, one glyph each
    assert keys["keys"][1][0][1] > keys["tdeck"][1][0][1] == keys["pad"][1][0][1]
    # spelled-out keys draw the same on every input
    assert len({_strip(th, ("C", "Change"), k)[0] for k in ("keys", "tdeck", "pad")}) == 1


def test_search_is_the_slash_a_keyboard_types(th):
    """The home's search is `/` on a keyboard and the Sym+G chord that types
    it on the T-Deck, never one `/` cap for a chord (design review R4-23)."""
    assert _strip(th, ("search", "Search"), "keys") == _strip(th, ("/", "Search"), "keys")
    assert _strip(th, ("search", "Search"), "tdeck") == _strip(th, ("Sym G", "Search"), "tdeck")
    assert _strip(th, ("search", "Search"), "tdeck") != _strip(th, ("/", "Search"), "tdeck")


def test_one_glyph_caps_are_square(th):
    assert _px_cap_widths(th, ("Q", "x")) == _px_cap_widths(th, ("E", "x")) \
        == _px_cap_widths(th, ("/", "x")), "Q, E and / take one cap size"
    assert _px_cap_widths(th, ("Esc", "x")) > _px_cap_widths(th, ("Q", "x"))


def _px_cap_widths(th, items):
    """The first pair's span less its label: the cap's width and the gap."""
    full = ui_draw.hints(make_system_canvas(320, 240), th, BAND, items, draw=False)
    word = ui_draw.hints(make_system_canvas(320, 240), th, BAND, ("", items[1]), draw=False)
    return (full[0][1] - full[0][0]) - (word[0][1] - word[0][0])


def test_touch_draws_no_hints(th):
    blank = bytes(make_system_canvas(320, 240)._buf)
    px, spans = _strip(th, ("confirm", "Open", "back", "Back"), "touch")
    assert spans == () and px == blank


def test_under_touch_the_rows_take_the_hints_band(tmp_path):
    ws = build_ws(Path(tmp_path))
    ws.look.set_theme("moy", persist=False, variant="dark")
    th = ws.theme_colors

    def foot(kind):
        """The colours in the hints' band, right of their caps."""
        ui_draw.input_kind(kind)
        cv = make_system_canvas(320, 240)
        ctx = ui_draw.ui_context()
        inp = array("i", [0] * ui_draw.UI_IN_LEN)
        for _ in range(2):
            ui_draw.ui_frame(ctx, cv, th, None, inp)
            ui.page("")
            ui.hints(["confirm", "Open", "back", "Back"])
            ui.group("G")
            for i in range(14):
                ui.info("Row %d" % i, "x")
            ui.group()

        b = cv._buf
        # the colours right of the caps, where a row's value would print
        return {bytes(b[2 * (y * 320 + x):2 * (y * 320 + x) + 2])
                for y in range(222, 238) for x in range(240, 320)}

    assert len(foot("keys")) == 1, "under keys no row draws in the hints' band"
    assert len(foot("touch")) > 1, "under touch a row's value prints in it"


@pytest.mark.parametrize("config", ["tdeck_320x240_fs1_dark", "p4_1024x600_cs2_fs1_console"])
def test_under_touch_the_homes_keep_no_strip_band(tmp_path, config):
    """Under touch a home has no hint strip and keeps none of its room at
    its foot (R3-07): the handheld's band reaches the content's foot, and
    the desk's shelf stands at most the side margin off the glass's foot,
    its caption taking the strip's room."""
    import moy_ui
    import test_shell_goldens as g
    moy_ui.theme_uninstall()
    try:
        ws = g._build(dict(g.CONFIGS[config], variant="dark", theme="moy"),
                      Path(tmp_path) / "carts")
        ws.go_home()
        if getattr(ws.wm, "mode", None) is not None:
            ws.open_library()
        grid, lay = ws.launcher, ws.layout
        foot = lay.cy + lay.ch
        ui_draw.input_kind("keys")
        assert grid.foot()[5][3] > 0, "under keys the strip has its band"
        ui_draw.input_kind("touch")
        assert grid.foot()[5][3] == 0, "under touch no strip"
        dk = grid._geom()[9]
        if dk is None:
            # the cards and caption centre in the whole content rect
            # (tests/test_launcher_scroll.py holds their layout)
            assert grid.band_rect()[1] + grid.band_rect()[3] == foot
        else:
            shelf_y, pad, side = dk[3], dk[4], grid.card_side()
            assert foot - (shelf_y + side) <= pad, \
                "the desk's shelf stands %d px off the foot, past its %d px margin" % (
                    foot - (shelf_y + side), pad)
    finally:
        moy_ui.theme_uninstall()


# -- the input in use: news and the board's declaration ---------------------------------


def test_the_table_names_the_source_with_news_once():
    t = moy_input.InputTable()
    kbd, touch = t.source("kbd"), t.source("touch")
    t.begin_frame()
    assert t.news() is None
    kbd.set_button("a", True)
    t.begin_frame()
    assert t.news() == "kbd"
    assert t.news() is None, "news is told once"
    t.begin_frame()
    assert t.news() is None, "a held button is no news"
    touch.point(10, 10, True, edge=True)
    t.begin_frame()
    assert t.news() == "touch"
    kbd.last_key = ord("q")
    t.begin_frame()
    assert t.news() == "kbd", "a new key is news"


def test_the_console_follows_the_last_input_used(tmp_path):
    ws = build_ws(Path(tmp_path))
    drv = make_drv(ws)
    assert ui_draw.input_kind() == "keys"
    ws.use_inputs((("local", "tdeck"), ("touch", "touch")))
    assert ui_draw.input_kind() == "tdeck", "the first declared input is in use"
    drv.click(160, 120)
    assert ui_draw.input_kind() == "touch"
    drv.press("down")
    drv.frame(1 / 30)
    assert ui_draw.input_kind() == "tdeck", "a key brings the keys back"


def test_an_undeclared_source_changes_nothing(tmp_path):
    ws = build_ws(Path(tmp_path))
    drv = make_drv(ws)
    drv.click(160, 120)
    assert ui_draw.input_kind() == "keys", "the sim's mouse is not a finger"
    ws.input_used("ble")
    assert ui_draw.input_kind() == "keys"


def _declared(path, name):
    tree = ast.parse(Path(ROOT, path).read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
                getattr(t, "id", None) == name for t in node.targets):
            return ast.literal_eval(node.value)
    raise AssertionError("%s declares no %s" % (path, name))


@pytest.mark.parametrize("path,name", [
    ("firmware/lilygo_t_deck_plus_mainline/modules/moy_runtime.py", "INPUT_KINDS"),
    ("device/wire_input.py", "TOUCH_BLE_INPUTS"),
    ("runtime/host_app.py", "HOST_INPUTS"),
    ("firmware/web_runner/web_boot.py", "WEB_INPUTS"),
    ("firmware/web_runner/web_boot.py", "WEB_TOUCH_INPUTS"),
])
def test_every_declaration_names_known_kinds(path, name):
    kinds = _declared(path, name)
    assert kinds and all(k in ui_draw.INPUTS for _src, k in kinds)
    assert len({s for s, _k in kinds}) == len(kinds)

