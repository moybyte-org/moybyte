"""A compiled app draws the toolkit's widgets (docs/kernel_toolkit_2026-10.md
§3.5, §10 "a compiled app draws a button and a scrolling list"): the `ui`
rows of the role table, served by native/moy_ui/moy_ui_wasm.c through the
RUN sink, on the host's WAMR.

tests/fixtures/wasm/app_ui.moy imports every `ui` row, draws a button, a list
and a label with a palette remap, a camera and a fill pattern of its own set,
and pumps the button's hit rect and the list's drag from the pointer the
console delivers to its snapshot. Here:

  * the run makes no upcall of any class and is VM-free by the census;
  * its widgets' pixels are the Python face's, drawn by the ROW sink on a
    system canvas with the same theme: one body, two sinks;
  * the cart's draw state survives the rows, and does not reach them;
  * one pump: a scripted drag scrolls the list through the snapshot exactly
    as the Python face's DragTap scrolls a ScrollRegion under the same input,
    offset for offset, and a press holds the button pressed.
"""

import os

import pytest

from runtime import host_app, moy_loop, ui
from runtime.host_canvas import make_system_canvas
from tools import wasm_cart
from ws_helpers import open_cart

import moy_app

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURE = os.path.join(ROOT, "tests", "fixtures", "wasm", "app_ui.moy")
TITLE = "App UI Wasm"
_DT = 1.0 / 30.0
BUTTON = (20, 80, 80, 20)
LIST = (120, 80, 160, 96)
LABEL = (20, 110, 80, 10)
ITEMS = ["one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
         "ten", "eleven", "twelve"]
ROW_H = 12
P_FRAMES, P_OFFSET, P_PRESSED, P_HOVER, P_KEPT, P_BTN, P_LIST, P_TAPS, P_FAIL = range(9)


def _binding_or_skip():
    from runtime import wasm_binding, wasm_host
    if wasm_host.available():
        return
    why = wasm_binding.why_unavailable() or "unknown"
    if os.environ.get("CI") or os.environ.get("MOYBYTE_REQUIRE_HOST_WASM"):
        pytest.fail("the host wasm binding did not build: %s" % why)
    pytest.skip("no host wasm binding: %s" % why)


@pytest.fixture
def ws(tmp_path):
    _binding_or_skip()
    root = str(tmp_path / "carts")
    host_app.moy_carts.ensure_dirs(root)
    wasm_cart.build(FIXTURE, os.path.join(root, "app_ui.moy"))
    w = host_app.build_workstation(root)
    w.pointer.visible = False               # the host draws its cursor on the canvas
    open_cart(w, TITLE)
    assert w.player.cart_error is None, w.player.cart_error
    yield w
    w.exit()


def _pointer(ws, x=None, y=None, down=False, click=False):
    p = ws.pointer
    if x is not None:
        p.x, p.y = x, y
    p.down, p.click = down, click


def _frame(ws):
    ws._dirty = True
    ws.frame(_DT)


def _pmem(ws):
    return list(ws.player._lua._run.pmem()[1])


def _region(cv, rect):
    x, y, w, h = rect
    buf = cv._buf
    out = bytearray()
    for j in range(y, y + h):
        out += buf[2 * (j * cv.w + x):2 * (j * cv.w + x + w)]
    return bytes(out)


def test_the_rows_are_every_rows_and_the_run_is_vm_free(ws):
    from runtime import moy_play
    t0 = moy_loop.upcalls()[1]
    for _ in range(10):
        _frame(ws)
    assert ws.player.cart_error is None, ws.player.cart_error
    pm = _pmem(ws)
    assert pm[P_FAIL] == 0, "a ui row answered -%d" % pm[P_FAIL]
    assert pm[P_FRAMES] >= 8
    assert tuple(moy_play.info()[5]) == (0, 0, 0, 0, 0, 0), "the run crossed into Python"
    assert moy_loop.upcalls()[1][5] == t0[5], "a ui row went through the ROLE door"
    assert moy_play.census(os.path.join(ws.carts_root, "app_ui.moy")) == ("wasm", True, "free")
    # every ui row of the table is imported by the fixture: its coverage
    import json
    with open(os.path.join(ROOT, "native", "moy_app", "roles.json")) as f:
        rows = ["ui_" + r["verb"] for r in json.load(f)["rows"] if r["role"] == "ui"]
    with open(os.path.join(FIXTURE, "src", "main.wat")) as f:
        src = f.read()
    for name in rows:
        assert '"moybyte.app" "%s"' % name in src, name
    assert [n for n, _sig, _row in moy_app.wasm_rows() if n.startswith("ui_")] == rows


def test_the_widgets_are_the_python_faces_and_ignore_the_carts_state(ws):
    for _ in range(9):
        _frame(ws)
    pm = _pmem(ws)
    # the square drawn after the rows, under the cart's camera and pal(7, 8)
    assert pm[P_KEPT] == 8, "the cart's draw state was lost: %r" % pm[P_KEPT]
    run = ws.sys_canvas
    cv = make_system_canvas(run.w, run.h)
    cv._wire = run._wire
    th = ws.theme_colors
    ui.button(cv, th, BUTTON, "PRESS", kind="normal", glyph="gear")
    ui.list(cv, th, LIST, ITEMS, ui.ScrollRegion(), ROW_H)
    ui.label(cv, LABEL, "UI", 7, ui.ALIGN_CENTER)
    for rect in (BUTTON, LIST, LABEL):
        assert _region(run, rect) == _region(cv, rect), "the RUN sink drew %r otherwise" % (
            rect,)


def test_one_pump_drives_the_list_and_the_button_through_the_snapshot(ws):
    # The same script through the Python face's pump.
    region = ui.ScrollRegion()
    region.set(LIST, len(ITEMS) * ROW_H)
    tap = ui.DragTap(region)
    script = [(200, 150, True, True)] + [(200, 150 - 6 * i, True, False) for i in range(1, 8)]
    script += [(200, 108, False, False)] * 6
    want, got = [], []
    for x, y, down, click in script:
        _pointer(ws, x, y, down, click)
        _frame(ws)
        got.append(_pmem(ws)[P_OFFSET])
        tap.frame(x, y, click, down, 4, _DT * 1000)
        want.append(region.offset)
    assert max(got) > 0, "the drag did not scroll the list: %r" % got
    assert got == want, "the snapshot's pump %r, the face's %r" % (got, want)
    # A press on the button holds it pressed; the release lets it go.
    _pointer(ws, 40, 90, True, True)
    _frame(ws)
    _pointer(ws, 40, 90, True, False)
    _frame(ws)
    assert _pmem(ws)[P_PRESSED] == 1
    _pointer(ws, 40, 90, False, False)
    _frame(ws)
    assert _pmem(ws)[P_PRESSED] == -1
