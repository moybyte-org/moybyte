"""The console APP API (docs/app_api_v1.md): register_app wires a cartridge
identity to a responsive system process -- launcher dispatch, router kind,
window minimums, text mode, relayout fan-out -- and Calc is the reference app
built only on the public seams (ui toolkit + Hits + the registry)."""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


from ws_helpers import build_ws as _ws


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
    assert kinds == ["artwork", "appearance", "writer", "storybook", "sheets",
                     "files", "calc"]
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
            return bool(cart) and cart.get("title") == "Brick Siege"

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
    _select(ws, "Brick Siege")
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


# -- the bar contract is a HOST GUARANTEE (ui_refactor_2026-08 Phase 2) --------
#
# Until 2026-08-19 every app hand-wrote both halves of this -- the
# `_draw_status_strip("tool")` last in draw() and the `handle_bar_tap("tool")`
# first in handle_pointer() -- and an app that forgot either became UNEXITABLE,
# silently, on device only. The router owns it now, so these are BEHAVIOURAL
# assertions, not a call-site count: a stub app that knows nothing about the bar
# must still show the strip and still exit on its context-X, and so must all
# seven shipped apps, through the same assertion.

import hashlib

import pytest

_DT = 1.0 / 30.0
_MAX_FRAMES = 6
_SHIPPED_APPS = ("artwork", "appearance", "writer", "storybook", "sheets",
                 "files", "calc")


class _NakedApp:
    """An app written by someone who never read the bar paragraph: no strip
    draw, no bar-tap route, no layout of its own (so the host also has to
    supply the band height). It paints one flat colour, so any pixel the
    router adds is the bar and nothing else."""

    id = "naked"
    domain = "system"
    TITLE = "NAKED"

    def __init__(self):
        self.taps = []

    def is_app(self, cart):
        return False                   # opened explicitly, never claimed

    def open(self):
        pass

    def relayout(self, w, h, fs):
        pass

    def draw(self, dt):
        pass                           # filled by the harness below

    def handle_input(self, i):
        return True

    def handle_pointer(self, px, py, click):
        self.taps.append((px, py, click))
        return True


def _quiesce_frame(ws):
    """Everything transient or clock-driven off the frame (the shell-golden
    recipe): two renders taken a moment apart must differ only by the bar."""
    if ws.pointer is not None:
        ws.pointer.visible = False
    ws._toast_until = 0
    ws._egg_until = 0
    ws._confetti_until = 0
    ws.show_achievements = False
    ws.show_fps = False
    ws.perf_hud = False
    ws.perf_capture = False
    ws.sysmenu.open = False
    ws._about = False
    ws._notice = None
    ws._notice_until = 0
    ws.bar_layer._clock_text = lambda: "00:00"


_SENTINEL = b"\x5a"      # a colour no surface paints, to prove nothing drew


def _settle(ws):
    """Wipe the canvas to a sentinel, then draw until two consecutive frames
    are byte-identical; return that frame.

    The wipe is load-bearing. Not every surface repaints the bar band -- the
    Appearance app leaves those rows alone -- so without it the STALE strip
    from the previous render survives into the muted one and the comparison
    silently reads as agreement. (It did, on exactly that app.)"""
    buf = ws.sys_canvas._buf
    buf[:] = _SENTINEL * len(buf)
    prev = None
    for _ in range(_MAX_FRAMES):
        _quiesce_frame(ws)
        ws._dirty = True
        ws.frame(_DT)
        cur = bytes(ws.sys_canvas._buf)
        if cur == prev:
            return cur
        prev = cur
    raise AssertionError("surface never settled in %d frames" % _MAX_FRAMES)


def _mute_tool_strip(ws):
    """Suppress ONLY the "tool" strip, leaving the other six strip kinds alone
    -- the desk, launcher, picker, editor-menu and Settings bars are not this
    phase's and must keep drawing, so a pixel difference here names the app bar
    and nothing else."""
    orig = ws.bar_layer._draw_status_strip

    def muted(where):
        if where == "tool":
            return
        return orig(where)

    ws.bar_layer._draw_status_strip = muted


def _band(ws, frame):
    """The bar band's rows and everything below it, as two hashes."""
    stride = ws.sys_canvas.w * 2
    rows = ws.bar_layer._bar_h("tool")
    top = frame[:stride * rows]
    rest = frame[stride * rows:]
    return hashlib.sha256(top).hexdigest(), hashlib.sha256(rest).hexdigest()


def _open_app_kind(ws, kind):
    if kind == "naked":
        app = _NakedApp()
        names = ws._NAMES if hasattr(ws, "_NAMES") else None
        colour = 7 if names is None else 7
        app.draw = lambda dt, cv=ws.sys_canvas, c=colour: cv.cls(c)
        ws.register_app(app)
        ws.open_app(app, cart=ws._all_carts[0])
        return app
    app = ws._apps_by_id[kind]
    assert ws.open_app(app), kind + " has no identity cart"
    return app


@pytest.mark.parametrize("kind", _SHIPPED_APPS + ("naked",))
def test_host_draws_the_app_bar_without_the_app_asking(tmp_path, kind):
    """The strip's PIXELS appear over a registered app on the fullscreen tier,
    including over an app whose draw() never mentions a bar."""
    ws = _ws(tmp_path)
    _open_app_kind(ws, kind)
    assert ws.screen == kind
    assert not ws.windowed_chrome
    with_bar = _settle(ws)
    _mute_tool_strip(ws)
    without_bar = _settle(ws)
    top_a, rest_a = _band(ws, with_bar)
    top_b, rest_b = _band(ws, without_bar)
    assert top_a != top_b, kind + ": nothing drew in the bar band"
    assert rest_a == rest_b, kind + ": the bar leaked outside its band"


@pytest.mark.parametrize("kind", _SHIPPED_APPS + ("naked",))
def test_host_routes_the_context_x_and_the_app_exits(tmp_path, kind):
    """A tap on the context-X exits, and the app never sees the tap -- the bar
    is routed BEFORE handle_pointer, not after it."""
    ws = _ws(tmp_path)
    app = _open_app_kind(ws, kind)
    assert ws.screen == kind
    x, y, w, h = ws.layout.context_x_btn
    ws.pointer.place(x + w // 2, y + h // 2)
    ws.pointer.click = True
    ws.handle_pointer()
    ws.pointer.click = False
    assert ws.wm.top_kind() == "launcher", kind + " did not exit on its bar X"
    if kind == "naked":
        assert app.taps == []          # the router consumed it first


def test_the_windowed_tier_still_suppresses_the_app_bar(tmp_path):
    """In the desk world the WM's title strip carries the close, so the host
    must draw NO tool strip -- muting it may not change a single pixel."""
    ws = _ws(tmp_path, sys_size=(1024, 600), windowed=True)
    ws.open_app(ws._apps_by_id["calc"])
    assert ws.windowed_chrome                     # the make world is open
    with_bar = _settle(ws)
    _mute_tool_strip(ws)
    without_bar = _settle(ws)
    assert with_bar == without_bar


def test_no_app_module_carries_the_bar_ritual():
    """The old road is closed: the strip/tap pair lives in the router, so no
    app module may reach the bar surface at all."""
    for name in ("calc_app", "artwork", "appearance_app", "files_app",
                 "storybook_app", "writer_app", "sheets_app"):
        src = (ROOT / "runtime" / (name + ".py")).read_text(encoding="utf-8")
        assert "bar_layer" not in src, name + " still reaches ws.bar_layer"
