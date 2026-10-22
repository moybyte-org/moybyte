"""The Editor tabs draw their widgets through `runtime/ui.py` (Phase 3d).

`docs/history/ui_refactor_2026-08.md` Section 3, Phase 3b/3c/3d: the hand-rolled row /
cell / button draws are transcribed onto the toolkit kinds, and the ratchet is
that the hand-rolled idiom cannot come back. This module is that ratchet for the
three surfaces converted in 3d -- Code, Config (cards) and Music.

Why COUNTERS and not pixels: `tests/test_shell_goldens.py` already owns the
pixels, and it owns them for the states a freshly-opened tab is in. What it
cannot see is (a) whether those pixels came from the toolkit or from a private
copy that happens to agree today, and (b) the states it never enters -- the code
editor's tool palette, find bar and popups are all closed on a fresh open, and
the Config tab renders whatever the seed cart declares. So this file counts the
toolkit calls a draw makes, in exactly those states.

The `Hits` budget at the bottom is the measured P4 constraint, not a style
rule: `runtime/ui.py`'s module docstring records that registering one hit rect
per grid cell costs the map tab ~9.3ms (13% of its frame), so the Config tab's
choice/thumbnail grids must keep their arithmetic hit-test forever.
"""

from pathlib import Path

import pytest

from ws_helpers import build_ws, open_cart, quiesce
from ws_helpers import boot_on_fixture

@pytest.fixture(autouse=True)
def _on_the_night_fixture(monkeypatch):
    """These pin the lookless pixel bar and its coordinates: every
    workstation here boots on the `night` fixture (ws_helpers)."""
    boot_on_fixture(monkeypatch)

ROOT = Path(__file__).resolve().parent.parent
_DT = 1.0 / 30.0
_EDITOR_CART = "Star Catcher"


class _Counter:
    """Count calls to the ui widget verbs a draw makes, without changing one."""

    KINDS = ("row", "cell", "chip", "button", "status_row", "dialog")

    def __init__(self, monkeypatch):
        from runtime import ui_draw
        self.n = {}
        for kind in self.KINDS:
            self._wrap(monkeypatch, ui_draw, kind)

    def _wrap(self, monkeypatch, ui_draw, kind):
        real = getattr(ui_draw, kind)
        counts = self.n
        counts[kind] = 0

        def counted(*a, **kw):
            counts[kind] += 1
            return real(*a, **kw)

        monkeypatch.setattr(ui_draw, kind, counted)

    def reset(self):
        for k in self.n:
            self.n[k] = 0


def _editor(tmp_path, tab, **kw):
    ws = build_ws(tmp_path, **kw)
    open_cart(ws, _EDITOR_CART)
    ws.open_in_editor(next(c for c in ws.carts.all
                           if c.get("title") == _EDITOR_CART))
    ws.set_menu_view(tab)
    return ws


def _frame(ws):
    quiesce(ws)
    ws._dirty = True
    ws.frame(_DT)


# --------------------------------------------------------------- the code tab

def test_the_private_panel_button_copy_is_gone(tmp_path):
    """`_panel_btn` was one of the private button copies Phase 3a set out
    to absorb (the notebook app's history pair, this one). It is
    not a method any more, and its body is not hiding under another name: the
    module draws no filled+bordered button of its own."""
    from runtime.code_layer import CodeLayer
    assert not hasattr(CodeLayer, "_panel_btn")
    src = (ROOT / "runtime" / "code_layer.py").read_text(encoding="utf-8")
    assert "def _panel_btn" not in src          # (the name survives in prose)
    assert "self._panel_btn(" not in src
    # The give-away pair of a hand-rolled button: a field fill immediately
    # followed by a border on the same rect.
    assert "cv.rect(r[0], r[1], r[2] - 1, r[3] - 1" not in src
    assert "cv.rectb(r[0], r[1], r[2] - 1, r[3] - 1" not in src


def test_code_chrome_is_drawn_by_the_toolkit(tmp_path, monkeypatch):
    """Every chrome button on the code tab is a `ui_draw.chip`, and every symbol-
    palette key is a `ui_draw.cell`. Counted with the tool palette AND the find bar
    open -- the states no golden enters."""
    from runtime import ui_draw
    ws = _editor(tmp_path, "code", sys_size=(480, 320))
    ui_draw.input_kind("touch")              # the palette shows after touch (D-17)
    cl = ws.code_layer
    counter = _Counter(monkeypatch)

    cl._tools_open = False
    cl._find_open = False
    _frame(ws)
    keys = len(cl._symbols())
    assert counter.n["cell"] == keys          # the symbol palette
    assert counter.n["chip"] == 1             # the always-visible TLS toggle

    counter.reset()
    cl._tools_open = True
    cl._find_open = True
    cl._find_q = "de"
    _frame(ws)
    # TLS + one per tool + the find bar's prev/next/case/close.
    assert counter.n["chip"] == 1 + len(cl._TOOLS) + 4
    assert counter.n["cell"] == keys
    assert counter.n["row"] >= 1              # the find bar's query field
    # the responsive "LN n, COL n" band: its ground, then its text on the
    # code's column
    assert counter.n["status_row"] == 2


def test_code_popups_are_toolkit_rows(tmp_path, monkeypatch):
    """The autocomplete + jump-to-symbol popups are a panel row plus one row per
    entry -- the selected one carrying the only field."""
    ws = _editor(tmp_path, "code", sys_size=(480, 320))
    cl = ws.code_layer
    counter = _Counter(monkeypatch)

    cl._cmp_open = True
    cl._cmp_items = ["circfill", "rectfill", "print"]
    cl._cmp_sel = 1
    _frame(ws)
    # 1 shell + 3 entries (plus nothing else on this tab draws a row).
    assert counter.n["row"] == 1 + 3

    counter.reset()
    cl._cmp_open = False
    cl._jump_open = True
    cl._jump_items = [("update", 3), ("draw", 11)]
    cl._jump_sel = 0
    _frame(ws)
    assert counter.n["row"] == 1 + 2


def test_the_crash_popup_is_a_toolkit_dialog(tmp_path, monkeypatch):
    ws = _editor(tmp_path, "code", sys_size=(480, 320))
    counter = _Counter(monkeypatch)
    ws.crash_popup = "TypeError: unsupported types for __add__"
    _frame(ws)
    assert counter.n["dialog"] == 1


# ------------------------------------------------------------- the config tab

def _cards_ws(tmp_path, **kw):
    ws = _editor(tmp_path, "cards", **kw)
    ws.project.cart["edit"] = [
        {"key": "lives", "type": "int", "min": 1, "max": 9, "step": 1},
        {"key": "bg", "type": "choice", "display": "bg-thumbs",
         "choices": ["night", "stripes", "indigo"]},
        {"key": "mode", "type": "choice", "display": "choice-icons",
         "choices": [0, 1], "icons": ["close", "run"]},
    ]
    ws.cards_layer.mtop = 0
    ws.cards_layer.msel = 0
    return ws
