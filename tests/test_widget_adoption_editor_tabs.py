"""The Editor tabs draw their widgets through `runtime/toolkit/ui.py` (Phase 3d).

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
"""

from pathlib import Path

import pytest

from ws_helpers import build_ws, open_cart, quiesce
from ws_helpers import boot_on_fixture
from tools.board_config import runtime_file

@pytest.fixture(autouse=True)
def _on_the_night_fixture(monkeypatch):
    """Every workstation here boots on the `night` fixture (ws_helpers): a
    palette theme with no look, drawn in the default's look flat."""
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
    ws.text.set_menu_view(tab)
    return ws


def _frame(ws):
    quiesce(ws)
    ws.mark_dirty()
    ws.frame(_DT)


# --------------------------------------------------------------- the code tab

def test_the_private_panel_button_copy_is_gone(tmp_path):
    """`_panel_btn` was one of the private button copies Phase 3a set out
    to absorb (the notebook app's history pair, this one). It is
    not a method any more, and its body is not hiding under another name: the
    module draws no filled+bordered button of its own."""
    from runtime.code_layer import CodeLayer
    assert not hasattr(CodeLayer, "_panel_btn")
    src = runtime_file("code_layer.py").read_text(encoding="utf-8")
    assert "def _panel_btn" not in src          # (the name survives in prose)
    assert "self._panel_btn(" not in src
    # The give-away pair of a hand-rolled button: a field fill immediately
    # followed by a border on the same rect.
    assert "cv.rect(r[0], r[1], r[2] - 1, r[3] - 1" not in src
    assert "cv.rectb(r[0], r[1], r[2] - 1, r[3] - 1" not in src


def test_code_chrome_is_drawn_by_the_toolkit(tmp_path, monkeypatch):
    """The Code tab is a page of the spec's kinds (`runtime/editors/code_ui.py`):
    every symbol key is a `ui_draw.button`, every tool a stop of the page, the
    find strip a row of a field, its count and Aa, and the status line one
    `status_row`. Counted with the tool row AND the find strip open -- the
    states no golden enters."""
    from runtime import code_layer, ui_draw
    ws = _editor(tmp_path, "code", sys_size=(480, 320))
    ui_draw.input_kind("touch")              # the symbol keys show after touch (D-17)
    cl = ws.code_layer
    _frame(ws)                               # the touch input reaches the page
    counter = _Counter(monkeypatch)
    k = ws.frame_state.frames_drawn
    _frame(ws)
    keys = len(cl._symbols())
    assert counter.n["button"] == keys * (ws.frame_state.frames_drawn - k)   # the symbol keys
    assert cl.box("tools") is not None        # the always-visible tools toggle
    assert all(cl.box(a) is None for a in code_layer._TOOL_ACTION.values() if a != "tools")

    cl.run("tools")
    cl.run("find")
    cl._find_q = "de"
    _frame(ws)
    counter.reset()
    k = ws.frame_state.frames_drawn
    _frame(ws)
    assert all(cl.box(a) is not None for a in code_layer._TOOL_ACTION.values()), \
        [a for a in code_layer._TOOL_ACTION.values() if cl.box(a) is None]
    assert counter.n["button"] == keys * (ws.frame_state.frames_drawn - k)
    assert cl.box("find_q") is not None and cl.box("case") is not None
    assert cl.page.rect_of("find_strip") is not None      # the row of its field, its count and Aa, placed this frame
    assert counter.n["dialog"] == 0 and not cl.page.dialog_shown()


def test_code_popups_are_menus_of_the_verbs(tmp_path, monkeypatch):
    """Completion and Go to are popovers of the page, a row per entry, the
    picked one focused (docs/history/look_2026-10.md step 33)."""
    ws = _editor(tmp_path, "code", sys_size=(480, 320))
    cl = ws.code_layer
    ws.editor.set_text("pr")
    ws.editor.row, ws.editor.col = 0, 2
    cl.run("complete_open")
    _frame(ws)
    _frame(ws)
    assert cl.page.popover_key() == "complete"
    words = [r.word for r in cl.s.cands]
    assert words and all(w.startswith("pr") for w in words)
    assert cl.row_box("cand", words[0]) is not None
    cl.run("overlay_close")
    ws.editor.set_text("def update():\n    pass\ndef draw():\n    pass")
    cl.run("goto_open")
    _frame(ws)
    _frame(ws)
    assert cl.page.popover_key() == "goto"
    assert [(r.name, r.line) for r in cl.s.defs] == [("update", 1), ("draw", 3)]
    assert cl.row_box("def_at", "1") is not None and cl.row_box("def_at", "3") is not None


def test_the_crash_popup_is_a_popover_of_the_verbs(tmp_path, monkeypatch):
    """See why's popover (docs/history/look_2026-10.md step 33): a popover of the
    page, hung under the stopped line, never the old dialog shell."""
    ws = _editor(tmp_path, "code", sys_size=(480, 320))
    counter = _Counter(monkeypatch)
    ws.crash_popup = "TypeError: unsupported types for __add__"
    _frame(ws)
    _frame(ws)
    cl = ws.code_layer
    assert counter.n["dialog"] == 0
    assert cl.page.popover_key() == "stopped"
    assert cl.s.stopped == ws.crash_popup
