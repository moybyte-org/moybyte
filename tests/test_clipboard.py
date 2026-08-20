"""The system clipboard (#132): one typed holder (widgets.Clipboard) every
editor writes THROUGH while keeping its local behavior -- copy in the code tab
pastes in Writer, a Writer line lands in a Sheets cell, and an editor with no
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


# -- the CodeEditor lane (code tab + Writer + Storybook share this core) ------

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


# -- Writer (Ctrl+A / C / X / V through the shared core) ----------------------

def _open_writer(ws):
    for i, cart in enumerate(ws.launcher.items):
        if cart.get("title") == "Writer":
            ws.launcher.sel = i
            break
    ws.open()
    ws.input.begin_frame()
    ws.frame(1 / 30)
    return ws.writer_app


def _press(app, inp, key):
    inp.last_key = key
    app._typed_keys(inp)
    inp.last_key = 0
    app._typed_keys(inp)


def test_writer_pastes_what_the_code_editor_copied(tmp_path):
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    ws.clipboard.put_text("def bounce():")     # what a code-tab copy left behind
    app = _open_writer(ws)
    app._new_doc()
    assert app.editor is not None and app.editor.clip is ws.clipboard
    _press(app, ws.input, 0x16)                # Ctrl+V
    assert app.editor.text() == "def bounce():"
    assert app._unsaved
    # Ctrl+A then Ctrl+C round-trips the doc back out to the system lane.
    _press(app, ws.input, 0x01)
    _press(app, ws.input, 0x03)
    assert ws.clipboard.text() == "def bounce():"
    # Ctrl+X empties the doc and keeps the text on the clipboard.
    _press(app, ws.input, 0x01)
    _press(app, ws.input, 0x18)
    assert app.editor.text() == ""
    assert ws.clipboard.text() == "def bounce():"


# -- Sheets (cell lane, naive one-cell v1) ------------------------------------

def _open_sheets(ws):
    for i, cart in enumerate(ws.launcher.items):
        if cart.get("title") == "Sheets":
            ws.launcher.sel = i
            break
    ws.open()
    ws.input.begin_frame()
    ws.frame(1 / 30)
    return ws.sheets_app


def _type(app, inp, text):
    for ch in text:
        inp.last_key = 10 if ch == "\n" else ord(ch)
        app._typed_keys(inp)
        inp.last_key = 0
        app._typed_keys(inp)


def test_sheets_cell_copy_paste_and_undo(tmp_path):
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    app = _open_sheets(ws)
    app._new_sheet()
    _type(app, ws.input, "42\n")               # A1 = 42, cursor now on A2
    assert app.sheet.raw_at(0, 0) == "42"
    app.cur_col, app.cur_row = 0, 0
    _press(app, ws.input, 0x03)                # Ctrl+C: A1 -> clipboard
    assert ws.clipboard.text() == "42"
    app.cur_col, app.cur_row = 1, 1
    _press(app, ws.input, 0x16)                # Ctrl+V into B2
    assert app.sheet.raw_at(1, 1) == "42"
    app._undo()                                # the paste is one undoable cell op
    assert app.sheet.raw_at(1, 1) == ""
    # Multi-line text (a Writer copy) lands as its FIRST line (naive v1).
    ws.clipboard.put_text("first\nsecond")
    app.cur_col, app.cur_row = 2, 0
    _press(app, ws.input, 0x16)
    assert app.sheet.raw_at(2, 0) == "first"
    # Ctrl+X copies then clears.
    app.cur_col, app.cur_row = 0, 0
    _press(app, ws.input, 0x18)
    assert ws.clipboard.text() == "42"
    assert app.sheet.raw_at(0, 0) == ""
