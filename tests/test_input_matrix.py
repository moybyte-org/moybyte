"""The input conformance matrix (#242's I1; docs/refactor_2026-11.md, phase 4):
every widget kind under every input as its driver delivers it, on every
layer an app is written in.

  * ROWS, the widget kinds: a field, a list, a button, a popover, a dialog
    and a text view, each the one widget of a page, placed at the same rect
    on every layer (`ui.at`).
  * COLUMNS, the inputs, each through its driver's own entry point on the
    host and never by setting the input table or calling the toolkit:
    the T-Deck keyboard's matrix (native/moy_input/moy_kbd.c, over an I2C
    fake: typing, and its move keys), a BLE keyboard (moy_hid.c, over a fake
    stack), the desk keyboard, the touchscreen, the trackball and a gamepad
    (the host driver's entry points: runtime/run/host_api.py's
    ConsoleDriver), Esc as the host delivers it, and the T-Deck's Mic key (the
    matrix). Every column runs on a board's input table (moy_input's
    InputTable), the one the boards' drivers write.
  * LAYERS: a Python shell page (a system app's content layer), a Python
    cart app, a Lua app, a compiled app (a WASI program over the toolkit it
    links) and an ELF app built for the host (tools/elf_cart.py's host
    target), the last two the same C page.

A cell is a gesture: what that input does to that widget (type into a
field and cancel its edit, step a list, press a button, dismiss a popover,
choose a dialog's button, drag a text view), and its effect read back: the
app's own state on the Python and Lua layers, and on the compiled and ELF
layers the picture, which must be the Python app's after the same gesture,
the Python app's cell passing (tests/test_ui_linked.py holds the three to
the same pixels at rest).

A compiled or ELF app's toolkit is a copy linked into its module: the
console's input owner reaches it through the frame message's steps and asks
and the answers the module tells after each frame (sdk/src/moyui_link.c,
native/moy_kernel/moy_screen_slot.h's THE TOOLKIT'S ANSWERS), as it reaches a
Lua run's through the run face.

Every cell passes: a cell is never marked as an expected failure.
"""

import os
import shutil

import pytest

from runtime import moy_input as mi
from runtime.layers import AppLayer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DT = 1.0 / 30.0

ROWS = ("field", "list", "button", "popover", "dialog", "text_view")
COLUMNS = ("kbd_text", "kbd_move", "ble_kbd", "desk_kbd", "touch", "trackball", "gamepad",
           "esc", "mic")
LAYERS = ("py_shell", "py_app", "lua_app", "wasm_app", "elf_app")

# What each input does to each widget: the gesture a cell runs. A widget an
# input has no gesture for is no cell (a touchscreen types nothing).
GESTURES = {
    "field": {"kbd_text": "type", "kbd_move": "type_move", "ble_kbd": "type",
              "desk_kbd": "type", "esc": "cancel", "mic": "cancel"},
    "list": {"kbd_move": "next", "ble_kbd": "next", "desk_kbd": "next", "touch": "next",
             "trackball": "next", "gamepad": "next"},
    "button": {"kbd_text": "press", "kbd_move": "press", "ble_kbd": "press", "desk_kbd": "press",
               "touch": "press", "trackball": "press", "gamepad": "press"},
    "popover": {"ble_kbd": "dismiss", "desk_kbd": "dismiss", "touch": "dismiss",
                "gamepad": "dismiss", "esc": "dismiss", "mic": "dismiss"},
    "dialog": {"kbd_text": "choose", "kbd_move": "choose", "ble_kbd": "choose",
               "desk_kbd": "choose", "trackball": "choose", "gamepad": "choose"},
    "text_view": {"touch": "scroll"},
}
# Not cells, because the toolkit's contract (docs/ui_api_2026-10.md) defines
# no such gesture: a dialog's Esc, B or Mic (an app answers its own cancel),
# and a text view's keys, pad and trackball (a drag and the wheel scroll it).
# #242 asks for both under its convention rows, which the sprint leaves out.

CELLS = [(layer, row, col) for layer in LAYERS for row in ROWS for col in COLUMNS
         if col in GESTURES[row]]

# The page's widget rect, in the page's content rect: 8 in from its corner.
INSET = 8
TALL = {"field": 28, "list": 84, "button": 28, "popover": 28, "dialog": 0, "text_view": 96}
LINES = ["line %d" % i for i in range(40)]
TYPED = "h"


# -- the pages, one source a layer ------------------------------------------------

PY_PAGE = {
    "field": 'S["t"], _ = ui.field("Name", S["t"], 16)',
    "list": 'S["c"] = ui.list("Pick", ["one", "two", "three"], S["c"])',
    "button": 'S["n"] += 1 if ui.button("Go") else 0\n    ui.label("x" * S["n"])',
    "popover": ('ui.button("Anchor")\n'
                '    if S["open"]:\n'
                '        if not ui.popover("pop", 120):\n'
                '            S["open"] = 0\n'
                '        ui.button("In")\n'
                '        ui.popover()'),
    "dialog": ('if S["open"]:\n'
               '        if ui.dialog("Sure?", ("No", "Yes")) >= 0:\n'
               '            S["open"] = 0\n'
               '        ui.label("Really?")\n'
               '        ui.dialog()'),
    "text_view": 'S["first"] = ui.text_view("tv", LINES)[0]',
}

PY_APP = '''LINES = %r
S = {"t": "", "c": 0, "n": 0, "open": 1, "first": 0}


def _draw():
    r = ui.page("Matrix")
    S["r"] = r
    if %r:
        ui.at(r[0] + %d, r[1] + %d, r[2] - %d, %d)
    %s
'''

LUA_PAGE = {
    "field": 'T = ui.field("Name", T, 16)',
    "list": 'C = ui.list("Pick", {"one", "two", "three"}, C)',
    "button": 'if ui.button("Go") then N = N + 1 end\n  ui.label(string.rep("x", N))',
    "popover": ('ui.button("Anchor")\n'
                '  if OPEN == 1 then\n'
                '    if not ui.popover("pop", 120) then OPEN = 0 end\n'
                '    ui.button("In")\n'
                '    ui.popover()\n'
                '  end'),
    "dialog": ('if OPEN == 1 then\n'
               '    if ui.dialog("Sure?", {"No", "Yes"}) > 0 then OPEN = 0 end\n'
               '    ui.label("Really?")\n'
               '    ui.dialog()\n'
               '  end'),
    "text_view": 'FIRST = ui.text_view("tv", LINES)',
}

LUA_APP = '''LINES = {%s}
T, C, N, OPEN, FIRST = "", 1, 0, 1, 1
function _draw()
  local x, y, w, h = ui.page("Matrix")
  if %s then ui.at(x + %d, y + %d, w - %d, %d) end
  %s
end
'''

C_PAGE = {
    "field": "tl = ui_field(\"Name\", 4, t, sizeof t, tl, NULL, 0) & 0xffff;",
    "list": "c = ui_list(\"Pick\", 4, \"one\\0two\\0three\", 13, c, NULL, 0);",
    "button": ("if (ui_button(\"Go\", 2, 0, NULL, 0) > 0) { n++; }\n"
               "        ui_label(\"xxxxxxxx\", n < 8 ? n : 8, NULL, 0);"),
    "popover": ("ui_button(\"Anchor\", 6, 0, NULL, 0);\n"
                "        if (open) {\n"
                "            if (!ui_popover(\"pop\", 3, 120, NULL, 0)) { open = 0; }\n"
                "            ui_button(\"In\", 2, 0, NULL, 0);\n"
                "            ui_popover(\"\", 0, 0, NULL, 0);\n"
                "        }"),
    "dialog": ("if (open) {\n"
               "            if (ui_dialog(\"Sure?\", 5, \"No\\0Yes\", 6, 1, NULL, 0) >= 0) { open = 0; }\n"
               "            ui_label(\"Really?\", 7, NULL, 0);\n"
               "            ui_dialog(\"\", 0, \"\", 0, 0, NULL, 0);\n"
               "        }"),
    "text_view": ("first = ui_text_view(\"tv\", 2, lines, sizeof lines - 1, \"\", 0, NULL, 0,\n"
                  "                             tv, sizeof tv);"),
}

C_APP = r'''#include <stdint.h>
#include <string.h>

#include "moybyte/ui.h"

static const char lines[] = "%s";

int main(int argc, char **argv) {
    (void)argc;
    (void)argv;
    static char t[16];
    int32_t tl = 0, c = 0, n = 0, open = 1, first = 0;
    int32_t page[4], tv[2];
    moy_frame_t f;
    (void)tl; (void)c; (void)n; (void)open; (void)first; (void)tv; (void)lines;
    while (moy_ui_link_frame(&f)) {
        ui_page("Matrix", 6, NULL, 0, page, sizeof page);
        if (%d) {
            ui_at(page[0] + %d, page[1] + %d, page[2] - %d, %d);
        }
        %s
        if (moy_ui_link_show() != 0) {
            return 3;
        }
    }
    return 0;
}
'''


def _tall(row):
    return TALL[row]


def py_app(row):
    return PY_APP % (LINES, row != "dialog", INSET, INSET, 2 * INSET, _tall(row), PY_PAGE[row])


def lua_app(row):
    return LUA_APP % (", ".join('"%s"' % s for s in LINES), "true" if row != "dialog" else "false",
                      INSET, INSET, 2 * INSET, _tall(row),
                      LUA_PAGE[row])


def c_app(row):
    return C_APP % ("\\0".join(LINES), int(row != "dialog"), INSET, INSET, 2 * INSET,
                    _tall(row), C_PAGE[row])


# -- the shell page: a system app's content layer --------------------------------

class MatrixShellPage(AppLayer):
    """A system app drawing the row's page through its AppContext's toolkit
    host, as Calc does (runtime/apps/calc_app.py)."""

    id = "matrix"
    domain = "system"
    TITLE = "Matrix"
    NEEDS = ("surface", "theme", "damage")
    UI = True
    ROW = "field"

    def __init__(self, ctx, names=None):
        self.ctx = ctx
        self._ui = ctx.ui
        self.S = {"t": "", "c": 0, "n": 0, "open": 1, "first": 0}

    @staticmethod
    def is_app(cart):
        return bool(cart) and cart.get("title") == "Matrix Shell"

    def open(self):
        self.S = {"t": "", "c": 0, "n": 0, "open": 1, "first": 0}
        self.ctx.ui.reset()
        self.ctx.damage.all()

    def relayout(self, w, h, fs, cs=None):
        pass

    def handle_input(self, i):
        if self.ctx.ui.keys(i):
            self.ctx.damage.all()
        return True

    def handle_pointer(self, px, py, click):
        if self.ctx.ui.point(px, py, click):
            self.ctx.damage.all()
        return True

    def draw(self, dt):
        import ui
        cv = self.ctx.surface.canvas()
        self.ctx.ui.begin(cv, self.ctx.theme.colors(), (0, 0, cv.w, cv.h))
        ns = {"ui": ui, "S": self.S, "LINES": LINES}
        ui.page("Matrix")
        r = self.ctx.surface.content_rect()      # a system app's page is the surface
        self.S["r"] = r
        if self.ROW != "dialog":
            ui.at(r[0] + INSET, r[1] + INSET, r[2] - 2 * INSET, _tall(self.ROW))
        exec(compile("def _w():\n    " + PY_PAGE[self.ROW] + "\n", "matrix", "exec"), ns)
        ns["_w"]()


# -- one layer's run of one page ---------------------------------------------------

class Kbd:
    """The T-Deck keyboard's I2C answer: one matrix frame a read."""

    def __init__(self):
        self.t = 1000
        self.frames = []

    def ms(self):
        return self.t

    def us(self):
        return self.t * 1000

    def sleep_ms(self, n):
        self.t += n

    def readfrom(self, _addr, n):
        return self.frames.pop(0) if self.frames else bytes(n)

    def writeto(self, _addr, data):
        pass


def _matrix(*keys, mic=False):
    d = bytearray(5)
    for at, bit, code, _sym in mi.raw_keys():
        if chr(code) in keys:
            d[at] |= bit
    if mic:
        d[0] |= 0x40
    return bytes(d)


class Rig:
    """A workstation on a board's input table with every driver attached,
    one page open on one layer."""

    def __init__(self, tmp_path, layer, row, monkeypatch, built):
        from runtime import host_app
        from ws_helpers import open_cart
        monkeypatch.setattr(host_app, "InputState", mi.InputTable)
        self.layer, self.row = layer, row
        self.tmp, self.monkeypatch = tmp_path, monkeypatch
        root = str(tmp_path / "carts")
        host_app.moy_carts.ensure_dirs(root)
        if layer == "py_app":
            _write(root, "matrix_py.moy", {"runtime": "python", "main": "main.py"},
                   {"main.py": py_app(row)})
        elif layer == "lua_app":
            _write(root, "matrix_lua.moy", {"runtime": "lua", "main": "main.lua"},
                   {"main.lua": lua_app(row)})
        elif layer in ("wasm_app", "elf_app"):
            shutil.copytree(built[(layer, row)], os.path.join(root, "matrix_c.moy"))
        else:
            _write(root, "matrix_shell.moy", {"runtime": "python", "main": "main.py"},
                   {"main.py": "def _draw():\n    pass\n"}, title="Matrix Shell")
        self.ws = ws = host_app.build_workstation(root)
        # A board's inputs as the hints know them, the T-Deck's and the
        # host's: the Mic key's tap is a field's Cancel where the T-Deck
        # keyboard is the input in use.
        ws.use_inputs((("kbd", "tdeck"), ("ball", "tdeck"), ("ble", "keys"),
                       ("local", "keys"), ("touch", "touch")))
        ws.look.set_theme("moy", persist=False)
        ws.pointer.visible = False
        ws.bar_layer.clock_text = lambda: "10:30"
        self.drv = host_app.ConsoleDriver(ws)
        self.kbd_bus = Kbd()
        self.kbd_bus.frames = [b"\x00"]
        self.kbd = mi.KbdDriver(ws.input, self.kbd_bus)
        self.kbd.raw_mode = True
        from test_ble_keyboard import FakeStack, _ready
        self.ble = mi.HidMachine(ws.input, FakeStack())
        _ready(self.ble)
        if layer == "py_shell":
            MatrixShellPage.ROW = row
            from runtime.ui_draw import AppHost
            ctx = ws.app_context("matrix", MatrixShellPage.NEEDS)
            ctx.ui = AppHost("matrix", ctx.surface)
            ws._app_hosts["matrix"] = ctx.ui
            app = MatrixShellPage(ctx)
            ws.spine.register_app(app)
            self.app = app
        open_cart(ws, "Matrix Shell" if layer == "py_shell" else "Matrix")
        if ws.confirm.open:
            act = ws.confirm._act
            ws.confirm.cancel()
            act()
        assert ws.run.player.cart_error is None if layer != "py_shell" else True
        self.frames(4)
        if layer == "py_app":
            PAGE_RECT[row] = self.state()["r"]

    # -- frames and reads

    def frames(self, n=1):
        from ws_helpers import quiesce
        for _ in range(n):
            self.kbd.pass_()
            self.ble.frame()
            quiesce(self.ws)
            self.ws.mark_dirty()
            self.drv.frame(DT)

    def state(self):
        p = self.ws.run.player
        if self.layer == "py_shell":
            return dict(self.app.S)
        if self.layer == "lua_app":
            g = p.lua.get_global
            return {"t": g("T"), "c": g("C"), "n": g("N"), "open": g("OPEN"), "first": g("FIRST"),
                    "r": PAGE_RECT.get(self.row)}
        if self.layer == "py_app":
            return dict(p.ns["S"])
        return None

    def shot(self):
        from test_ui_linked import _shot
        return _shot(self.drv)

    def screen_of(self, x, y):
        if self.layer == "py_shell":
            return x, y                 # a system app draws on the screen's canvas
        ox, oy, sc = self.ws._viewport()[:3]
        return ox + x * sc, oy + y * sc

    def widget_rect(self):
        """The widget's rect on the screen, from the page's content rect the
        Python page recorded (every layer lays the page out alike)."""
        r = (self.state() or {}).get("r") or PAGE_RECT.get(self.row)
        if r is None:
            ref = Rig(self.tmp / "rect", "py_app", self.row, self.monkeypatch, {})
            ref.ws.spine.exit()
            r = PAGE_RECT[self.row]
        x, y = self.screen_of(r[0] + INSET, r[1] + INSET)
        sc = 1 if self.layer == "py_shell" else self.ws._viewport()[2]
        return x, y, (r[2] - 2 * INSET) * sc, (_tall(self.row) or 24) * sc

    # -- the inputs, each through its driver

    def kbd_key(self, *keys, mic=False):
        self.kbd_bus.frames = [_matrix(*keys, mic=mic)]
        self.frames(1)
        self.kbd_bus.frames = [bytes(5)]
        self.frames(2)

    def ble_key(self, usage):
        self.ble.notify(3, 3, bytes((0, 0, usage, 0, 0, 0, 0, 0)))
        self.frames(1)
        self.ble.notify(3, 3, bytes(8))
        self.frames(2)

    def desk_key(self, code):
        self.drv.key_down(code)
        self.frames(1)
        self.drv.key_up(code)
        self.frames(2)

    def desk_type(self, ch):
        self.drv.type_char(ord(ch))
        self.frames(2)

    def ball(self, steps):
        """The trackball rolled down `steps` notches, each held until the
        surface takes a step."""
        for _ in range(steps):
            self.drv.pan(0, 1)
            self.frames(6)
            self.drv.pan(0, 0)
            self.frames(2)

    def pad(self, name):
        self.drv.press(name)
        self.frames(3)

    def tap(self, x, y):
        self.drv.click(int(x), int(y))
        self.frames(3)

    def drag(self, x, y, dy):
        self.drv.touch(int(x), int(y))
        self.frames(1)
        for i in range(1, 5):
            self.drv.touch_drag(int(x), int(y + dy * i / 4))
            self.frames(1)
        self.drv.touch_up()
        self.frames(3)


ENTER, ESC, DOWN = 0x28, 0x29, 0x51
PAGE_RECT = {}
USAGE = {"h": 0x0B}


def gesture(rig, col, what):
    """Run `what` (the row's gesture) by input `col`."""
    if what == "choose":
        what = "press"              # the dialog's focused button, its first
    if col == "kbd_text":
        rig.kbd_key(TYPED if what == "type" else "\r")
    elif col == "kbd_move":
        if what == "type_move":
            rig.kbd_key("s")
        elif what == "press":
            rig.kbd_key("\r")
        elif what == "next":
            rig.kbd_key("s")
            rig.kbd_key("\r")
        else:
            for _ in range(3):
                rig.kbd_key("s")
    elif col == "ble_kbd":
        if what == "type":
            rig.ble_key(USAGE[TYPED])
        elif what == "press":
            rig.ble_key(ENTER)
        elif what == "next":
            rig.ble_key(DOWN)
            rig.ble_key(ENTER)
        elif what == "dismiss":
            rig.ble_key(ESC)
        else:
            for _ in range(3):
                rig.ble_key(DOWN)
    elif col == "desk_kbd":
        if what == "type":
            rig.desk_type(TYPED)
        elif what == "press":
            rig.desk_key(13)
        elif what == "next":
            rig.desk_key(mi.KEY_DOWN)
            rig.desk_key(13)
        elif what == "dismiss":
            rig.desk_key(27)
        else:
            for _ in range(3):
                rig.desk_key(mi.KEY_DOWN)
    elif col == "touch":
        x, y, w, h = rig.widget_rect()
        if what == "press":
            rig.tap(x + w // 2, y + h // 2)
        elif what == "next":
            rig.tap(x + w // 2, y + 30)          # the second row, under the caption
        elif what == "dismiss":
            sx, sy, sw, sh = rig.ws.app_content_rect()
            rig.tap(sx + sw - 4, sy + sh - 4)
        else:
            rig.drag(x + w // 2, y + h - 8, -60)
    elif col == "trackball":
        if what == "press":
            rig.drv.ball_click()
            rig.frames(3)
        elif what == "next":
            rig.ball(1)
            rig.drv.ball_click()
            rig.frames(3)
        else:
            rig.ball(3)
    elif col == "gamepad":
        if what == "press":
            rig.pad("a")
        elif what == "next":
            rig.pad("down")
            rig.pad("a")
        elif what == "dismiss":
            rig.pad("b")
        else:
            for _ in range(3):
                rig.pad("down")
    elif col == "esc":
        if what == "cancel":
            rig.desk_type(TYPED)
        rig.drv.escape()
        rig.frames(3)
    elif col == "mic":
        if what == "cancel":
            rig.desk_type(TYPED)
        rig.kbd_key(mic=True)


def effect(what, before, after):
    """Whether `after` is the gesture's effect on `before`."""
    if what == "type":
        return after["t"] == TYPED
    if what == "type_move":
        return after["t"] == "s"
    if what == "cancel":
        return after["t"] == ""
    if what == "next":
        return after["c"] != before["c"]
    if what == "press":
        return after["n"] == before["n"] + 1
    if what in ("dismiss", "choose"):
        return after["open"] == 0
    if what == "scroll":
        return after["first"] > before["first"]
    raise AssertionError(what)


# -- the compiled and ELF pages, built once ----------------------------------------

def _write(root, name, man, files, title="Matrix"):
    d = os.path.join(root, name)
    os.makedirs(d, exist_ok=True)
    full = {"format": "moy-1", "title": title, "id": "test." + name.split(".")[0],
            "canvas": "320x240", "moybyte": {"type": "app", "permissions": []}}
    full.update(man)
    import json
    with open(os.path.join(d, "manifest.json"), "w") as f:
        json.dump(full, f)
    for n, text in files.items():
        with open(os.path.join(d, n), "w") as f:
            f.write(text)
    with open(os.path.join(d, "config.json"), "w") as f:
        f.write("{}")


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    """{(layer, row): cart folder} for the compiled and ELF pages this host
    can build."""
    out = {}
    base = tmp_path_factory.mktemp("matrix_c")
    for row in ROWS:
        src = base / ("src_" + row)
        _write(str(src.parent), src.name, {}, {})
        os.makedirs(src / "src", exist_ok=True)
        (src / "src" / "page.c").write_text(c_app(row))
        try:
            from tools import elf_cart
            man = src / "manifest.json"
            import json
            m = json.loads(man.read_text())
            m["moybyte"]["permissions"] = ["full_access"]
            man.write_text(json.dumps(m))
            dst = str(base / ("elf_" + row + ".moy"))
            elf_cart.build(str(src), dst, ["host"], signed=False)
            out[("elf_app", row)] = dst
        except Exception as exc:  # noqa: BLE001 -- a host with no compiler
            out[("elf_app", row)] = exc
        try:
            from tools import wasm_cart
            wsrc = base / ("wsrc_" + row)
            shutil.copytree(str(src), str(wsrc))
            m = json.loads((wsrc / "manifest.json").read_text())
            m.update({"runtime": "wasm", "main": "main.wasm"})
            m["moybyte"]["wasi"] = True
            m["moybyte"]["permissions"] = []
            (wsrc / "manifest.json").write_text(json.dumps(m))
            os.rename(str(wsrc / "src" / "page.c"), str(wsrc / "src" / "main.c"))
            dst = str(base / ("wasm_" + row + ".moy"))
            wasm_cart.build(str(wsrc), dst)
            out[("wasm_app", row)] = dst
        except Exception as exc:  # noqa: BLE001 -- a host with no wasi-sdk
            out[("wasm_app", row)] = exc
    return out


_REF = {}


def _reference(tmp_path, row, col, monkeypatch, built):
    """The Python app's pictures before and after the gesture, and whether
    its state took the gesture's effect."""
    key = (row, col)
    if key not in _REF:
        rig = Rig(tmp_path / "ref", "py_app", row, monkeypatch, built)
        a, s0 = rig.shot(), rig.state()
        what = GESTURES[row][col]
        gesture(rig, col, what)
        _REF[key] = (a, rig.shot(), effect(what, s0, rig.state()))
        rig.ws.spine.exit()
    return _REF[key]


@pytest.mark.parametrize("layer,row,col", CELLS, ids=["-".join(c) for c in CELLS])
def test_cell(tmp_path, monkeypatch, built, layer, row, col):
    what = GESTURES[row][col]
    if layer in ("wasm_app", "elf_app"):
        got = built[(layer, row)]
        if isinstance(got, Exception):
            if os.environ.get("CI"):
                raise got
            pytest.skip("no %s build here: %s" % (layer, got))
    if layer in ("wasm_app", "elf_app"):
        ref = _reference(tmp_path, row, col, monkeypatch, built)
    rig = Rig(tmp_path, layer, row, monkeypatch, built)
    try:
        if layer in ("wasm_app", "elf_app"):
            before = rig.shot()
            if what == "cancel":
                # A cancel draws the page as it was before the typing, so the
                # typing has to show first: a cancel that did nothing would
                # draw the same.
                rig.desk_type(TYPED)
                typed = rig.shot() != before
                assert typed, "%s: typing into the field draws nothing" % layer
                gesture(rig, col, "dismiss")
            else:
                gesture(rig, col, what)
            after = rig.shot()
            assert ref[2], "the Python app's own cell fails: so does this layer's"
            # Booleans, not the shots: a failed == over two frames' bytes
            # makes pytest diff them, which runs for hours.
            drew = (ref[1] != ref[0] or what == "cancel") and after == ref[1]
            assert drew, ("%s: the %s's %s by %s does not draw what the Python app's does"
                          % (layer, row, what, col))
            rests = before == ref[0]
            assert rests, "the page at rest differs from the Python app's"
            return
        before = rig.state()
        gesture(rig, col, what)
        after = rig.state()
        assert effect(what, before, after), (
            "%s: %s by %s: %r -> %r" % (layer, what, col, before, after))
    finally:
        rig.ws.spine.exit()
