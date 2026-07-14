"""The console APP API (docs/app_api_v1.md): register_app wires a cartridge
identity to a responsive system process -- launcher dispatch, router kind,
window minimums, text mode, relayout fan-out -- and Calc is the reference app
built only on the public seams (ui toolkit + Hits + the registry)."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _ws(tmp_path, **kw):
    from runtime import host_app
    return host_app.build_workstation(str(tmp_path / "carts"), **kw)


def _select(ws, title):
    for i, it in enumerate(ws.launcher.items):
        if it.get("title") == title:
            ws.launcher.sel = i
            return it
    raise AssertionError(title + " not on the launcher")


# -- the registry --------------------------------------------------------------

def test_builtin_apps_are_registered(tmp_path):
    ws = _ws(tmp_path)
    kinds = [app.id for app, _t in ws._apps]
    assert kinds == ["artwork", "appearance", "writer", "storybook", "calc"]
    for kind in kinds:
        assert ws._content_layers[kind] is not None       # router wired
    assert ws.app_min_size("artwork") == (310, 230)       # registered minimum
    assert ws.app_min_size("calc") == (170, 190)          # inferred from CalcLayout


def test_register_app_dispatches_a_custom_app(tmp_path):
    """A third-party app: one register_app call makes a launcher tap open it."""
    ws = _ws(tmp_path)

    class Demo:
        id = "demo"
        domain = "system"
        TITLE = "BATTLE DEMO"
        opened = 0

        def is_app(self, cart):
            return bool(cart) and cart.get("title") == "Battle City"

        def open(self):
            self.opened += 1

        def draw(self, dt):
            pass

        def handle_input(self, i):
            return True

        def handle_pointer(self, px, py, click):
            return True

    demo = Demo()
    ws.register_app(demo, min_size=(200, 100))
    assert ws._content_layers["demo"] is demo
    assert ws.app_min_size("demo") == (200, 100)
    assert ws.app_title("demo") == "BATTLE DEMO"
    _select(ws, "Battle City")
    ws.launch_selected()
    assert demo.opened == 1
    assert ws.screen == "demo"                            # back-stack kind


def test_late_app_metadata_reaches_windowed_wm(tmp_path):
    """TITLE and layout minimums are registry metadata, not WM id ladders."""
    ws = _ws(tmp_path, sys_size=(1024, 600), windowed=True)

    class DemoLayout:
        MIN_W = 210
        MIN_H = 130

    class Demo:
        id = "story_editor"
        domain = "system"
        TITLE = "Story Editor"

        def __init__(self):
            self.layout = DemoLayout()

    demo = Demo()
    ws.register_app(demo)
    assert ws.app_min_size(demo.id) == (210, 130)
    assert ws.wm._root_ctx.app_layouts[demo.id] is demo.layout
    win = type("Win", (), {"kind": demo.id})()
    assert ws.wm._win_title(win) == "Story Editor"


def test_writer_registration_keeps_text_mode(tmp_path):
    ws = _ws(tmp_path)
    _select(ws, "Writer")
    ws.launch_selected()
    assert ws.screen == "writer"
    assert ws.input.text_mode                             # typing app


# -- Calc, the reference app ----------------------------------------------------

def test_calc_opens_from_its_identity_cart(tmp_path):
    ws = _ws(tmp_path)
    _select(ws, "Calc")
    ws.launch_selected()
    assert ws.screen == "calc"
    ws.frame(1 / 30)                                      # draws without error
    # Exit returns home (the tool-bar context X path uses ws.exit).
    ws.exit()
    assert ws.screen == "launcher"


def test_calc_math_via_hits(tmp_path):
    """Drive 7 x 8 = 56 entirely through pointer taps resolved by ui.Hits --
    the draw pass builds the hit map, exactly the toolkit contract."""
    ws = _ws(tmp_path)
    _select(ws, "Calc")
    ws.launch_selected()
    calc = ws._content_layers["calc"]
    ws.frame(1 / 30)                                      # populate hits

    def tap(label):
        for rect, verb, arg in calc.hits._items:
            if arg == label:
                calc.handle_pointer(rect[0] + 2, rect[1] + 2, True)
                return
        raise AssertionError("no key " + label)

    for k in ("7", "*", "8", "="):
        ws.frame(1 / 30)
        tap(k)
    assert calc.entry == "56"
    tap("C")
    assert calc.entry == "0" and calc.op is None
    # Division by zero stays kid-friendly.
    for k in ("9", "/", "0", "="):
        ws.frame(1 / 30)
        tap(k)
    assert calc.entry == "OOPS"


def test_calc_layout_reflows_and_declares_minimum(tmp_path):
    from runtime.calc_app import CalcLayout
    assert CalcLayout.MIN_W > 0 and CalcLayout.MIN_H > 0
    small = CalcLayout(320, 240, 1)
    big = CalcLayout(1024, 600, 1)
    assert big.keys[3][3][2] > small.keys[3][3][2]        # keys grow with the window
    # Every key stays inside the canvas.
    for row in big.keys:
        for x, y, w, h in row:
            assert x >= 0 and y >= 0 and x + w <= 1024 and y + h <= 600


def test_calc_is_app_rejects_lookalikes(tmp_path):
    from runtime.calc_app import CalcAppLayer
    assert not CalcAppLayer.is_app(None)
    assert not CalcAppLayer.is_app({"title": "Calc", "permissions": ("graphics",),
                                    "path": "/x/calc.moy"})
    assert not CalcAppLayer.is_app({"title": "My Calc", "permissions": ("calc",),
                                    "path": "/x/calc.moy"})
    assert CalcAppLayer.is_app({"title": "Calc", "permissions": ("calc",),
                                "path": "/carts/calc.moy"})
