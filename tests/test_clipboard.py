"""The system clipboard (#132): one typed holder (widgets.Clipboard) every
editor writes THROUGH while keeping its local behavior -- copy in the code tab
pastes into a note, and an editor with no
workstation attached behaves exactly as before (the local-only contract)."""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

from runtime import host_app
from runtime.widgets import Clipboard
from runtime.editors_code import CodeEditor


# -- the holder ---------------------------------------------------------------

def test_clipboard_holder_typed_and_versioned():
    c = Clipboard()
    assert c.text() == "" and c.kind is None
    c.put_text("hello")
    assert c.text() == "hello" and c.kind == "text"
    s0 = c.seq
    c.put_text("world")
    assert c.seq == s0 + 1
    # A future non-text kind never leaks out of text().
    c.kind, c.data = "pixels", {"w": 8, "h": 8}
    assert c.text() == ""


# -- the CodeEditor lane (code tab + editor handle + Storybook share it) -----

def test_copy_travels_between_editors_through_the_system_lane():
    clip = Clipboard()
    a = CodeEditor("steal me", clip=clip)
    b = CodeEditor("", clip=clip)
    a.select_all()
    assert a.copy()
    assert b.paste()
    assert b.text() == "steal me"
    # Cut writes through too.
    b.select_all()
    assert b.cut()
    assert b.text() == "" and clip.text() == "steal me"


def test_paste_prefers_the_system_lane_over_a_stale_local_copy():
    clip = Clipboard()
    a = CodeEditor("old", clip=clip)
    a.select_all()
    a.copy()                       # local "old", system "old"
    clip.put_text("newer")         # another app copied since
    a.set_text("")
    assert a.paste()
    assert a.text() == "newer"


def test_no_workstation_keeps_the_local_only_behavior():
    ed = CodeEditor("local only")
    ed.select_all()
    assert ed.copy()
    assert ed.clipboard == "local only"
    ed.set_text("")
    assert ed.paste()
    assert ed.text() == "local only"


def test_select_all_spans_the_whole_buffer():
    ed = CodeEditor("one\ntwo")
    ed.select_all()
    assert ed.selected_text() == "one\ntwo"


def test_code_tab_editor_rides_the_workstation_clipboard(tmp_path):
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    title = next(c["title"] for c in ws.launcher.items if c.get("path"))
    for i, cart in enumerate(ws.launcher.items):
        if cart.get("title") == title:
            ws.launcher.sel = i
            break
    ws.open_in_editor()
    ws.set_menu_view("code")
    assert ws.editor is not None
    assert ws.editor.clip is ws.clipboard


# -- the editor handle (Ctrl+A / C / X / V through the shared core) -----------

def _handle(ws, name):
    """A cart's editor handle, built the way `open_editor` builds one. This is
    the SECOND surface on the lane -- the code tab is the first, and the point
    of the lane is that text crosses between them."""
    ctx = ws.app_context("probe", ("files", "clipboard"))
    return ws.player._open_cart_editor(ctx.files, "docs", name, None,
                                       ws.canvas, ctx.clipboard)


def test_a_note_pastes_what_the_code_editor_copied(tmp_path):
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    ws.clipboard.put_text("def bounce():")     # what a code-tab copy left behind
    ed = _handle(ws, "note")
    assert ed.ed.clip is ws.clipboard
    assert ed.key(0x16)                        # Ctrl+V
    assert ed.text() == "def bounce():"
    assert ed.dirty()
    # Ctrl+A then Ctrl+C round-trips the note back out to the system lane.
    ed.key(0x01)
    ed.key(0x03)
    assert ws.clipboard.text() == "def bounce():"
    # Ctrl+X empties the note and keeps the text on the clipboard.
    ed.key(0x01)
    ed.key(0x18)
    assert ed.text() == ""
    assert ws.clipboard.text() == "def bounce():"
