"""#89 -- the CODE tab's status gaps: text selection + clipboard, find/search,
block indent/outdent, and the line-number gutter.

Two layers, mirroring the existing split:
  * CodeEditor core (runtime.editors) -- pure buffer logic, unit-tested directly.
  * CodeLayer surface (runtime.code_layer) -- the touch affordances (tool palette,
    SELECT mode drag, find bar, gutter), driven through the SAME shared console the
    device runs (host_app + ConsoleDriver / Workstation), so they assert host==device.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DT = 1 / 30


# ---------------------------------------------------------------------------
# CodeEditor core: selection + clipboard (#89)
# ---------------------------------------------------------------------------

def test_selection_bounds_and_selected_text():
    from runtime import CodeEditor
    ed = CodeEditor("hello world\nsecond line\nthird")
    ed.row, ed.col = 0, 0
    ed.move(0, 5, select=True)                    # select "hello"
    assert ed.has_selection()
    assert ed.selection_bounds() == (0, 0, 0, 5)
    assert ed.selected_text() == "hello"
    # anchor before caret normalizes the same way
    ed.row, ed.col = 0, 5
    ed.sel = (0, 0)
    assert ed.selection_bounds() == (0, 0, 0, 5)


def test_multiline_selection_text():
    from runtime import CodeEditor
    ed = CodeEditor("aaa\nbbb\nccc")
    ed.row, ed.col = 0, 1
    ed.move(1, 1, select=True)                    # (0,1) -> (1,2)
    assert ed.selection_bounds() == (0, 1, 1, 2)
    assert ed.selected_text() == "aa\nbb"


def test_copy_cut_paste_roundtrip():
    from runtime import CodeEditor
    ed = CodeEditor("hello world")
    ed.row, ed.col = 0, 0
    ed.move(0, 5, select=True)
    assert ed.copy() and ed.clipboard == "hello"
    assert ed.text() == "hello world"             # copy leaves the buffer intact
    # cut removes the selection and keeps the clipboard
    ed.row, ed.col = 0, 0
    ed.move(0, 5, select=True)
    assert ed.cut() and ed.clipboard == "hello"
    assert ed.lines == [" world"]
    # paste at the caret drops the clipboard back in
    ed.row, ed.col = 0, len(ed.lines[0])
    assert ed.paste()
    assert ed.lines == [" worldhello"]


def test_paste_multiline_and_over_selection():
    from runtime import CodeEditor
    ed = CodeEditor("abcd")
    ed.clipboard = "X\nY"
    ed.row, ed.col = 0, 2
    assert ed.paste()
    assert ed.lines == ["abX", "Ycd"]
    assert (ed.row, ed.col) == (1, 1)
    # paste over a selection replaces it
    ed.set_text("keepME")
    ed.clipboard = "gone"
    ed.row, ed.col = 0, 4
    ed.sel = (0, 6)                                # select "ME"
    assert ed.paste()
    assert ed.text() == "keepgone"


def test_typing_replaces_selection():
    from runtime import CodeEditor
    # insert over a selection
    ed = CodeEditor("aaa\nbbb\nccc")
    ed.row, ed.col = 0, 1
    ed.move(1, 1, select=True)                    # select "aa\nbb"
    ed.insert("X")
    assert ed.lines == ["aXb", "ccc"]
    # backspace over a selection deletes it (no extra char removed)
    ed.set_text("hello")
    ed.row, ed.col = 0, 1
    ed.move(0, 3, select=True)                    # select "ell"
    ed.backspace()
    assert ed.text() == "ho" and not ed.has_selection()
    # enter over a selection replaces it with a split
    ed.set_text("hello")
    ed.row, ed.col = 0, 1
    ed.move(0, 3, select=True)
    ed.newline()
    assert ed.lines == ["h", "o"]


def test_move_without_select_collapses():
    from runtime import CodeEditor
    ed = CodeEditor("hello world")
    ed.row, ed.col = 0, 0
    ed.move(0, 5, select=True)
    assert ed.has_selection()
    ed.move(0, 1)                                  # a plain move collapses the selection
    assert not ed.has_selection()


def test_select_sticky_extends_on_move():
    from runtime import CodeEditor
    ed = CodeEditor("hello world")
    ed.row, ed.col = 0, 0
    ed.select_sticky = True                       # SELECT mode: arrows/trackball extend
    ed.move(0, 5)
    assert ed.selected_text() == "hello"
    ed.select_sticky = False
    ed.move(0, 1)
    assert not ed.has_selection()


def test_clipboard_persists_across_set_text():
    from runtime import CodeEditor
    ed = CodeEditor("copyme")
    ed.row, ed.col = 0, 0
    ed.move(0, 6, select=True)
    ed.copy()
    ed.set_text("fresh buffer")                   # reload
    assert ed.clipboard == "copyme"
    ed.row, ed.col = 0, len(ed.lines[0])
    ed.paste()
    assert ed.text() == "fresh buffercopyme"


# ---------------------------------------------------------------------------
# CodeEditor core: block indent / outdent (#89)
# ---------------------------------------------------------------------------

def test_indent_outdent_selection():
    from runtime import CodeEditor
    ed = CodeEditor("one\ntwo\nthree")
    ed.row, ed.col = 0, 0
    ed.move(1, 1, select=True)                    # (0,0) -> (1,1): rows 0..1 both in the block
    ed.indent_selection()
    assert ed.lines == ["  one", "  two", "three"]
    assert ed.col == 3                            # caret (on row 1) rode the +2 shift
    ed.outdent_selection()
    assert ed.lines == ["one", "two", "three"]
    assert ed.col == 1


def test_block_range_excludes_trailing_col0_line():
    from runtime import CodeEditor
    # A selection ending at column 0 of a line does NOT include that trailing line
    # (the standard editor rule), so only row 0 indents here.
    ed = CodeEditor("one\ntwo")
    ed.row, ed.col = 0, 0
    ed.move(1, 0, select=True)                    # (0,0) -> (1,0)
    ed.indent_selection()
    assert ed.lines == ["  one", "two"]


def test_outdent_stops_at_zero_and_partial():
    from runtime import CodeEditor
    ed = CodeEditor(" x\n    y")                  # 1 space, 4 spaces
    ed.row, ed.col = 0, 0
    ed.move(1, 1, select=True)                    # into row 1 so both are in the block
    assert ed.outdent_selection()
    assert ed.lines == ["x", "  y"]              # strips up to one INDENT (2) per line
    # a line with no leading space is left alone; returns False when nothing changed
    ed.set_text("abc")
    ed.row, ed.col = 0, 0
    assert ed.outdent_selection() is False


def test_tab_indents_selection_else_inserts_spaces():
    from runtime import CodeEditor
    ed = CodeEditor("a\nb")
    ed.row, ed.col = 0, 0
    ed.move(1, 1, select=True)
    assert ed.key(0x09)                           # tab over a selection = indent
    assert ed.lines == ["  a", "  b"]
    # tab with no selection still inserts two spaces (unchanged behavior)
    ed.set_text("x")
    ed.row, ed.col = 0, 1
    ed.key(0x09)
    assert ed.lines == ["x  "]


# ---------------------------------------------------------------------------
# CodeEditor core: find / search (#89)
# ---------------------------------------------------------------------------

def test_find_forward_next_and_wrap():
    from runtime import CodeEditor
    ed = CodeEditor("foo bar foo\nbaz foo")
    ed.row, ed.col = 0, 0
    assert ed.find("foo") and (ed.row, ed.col) == (0, 8)   # 2nd on line 0
    assert ed.find("foo") and (ed.row, ed.col) == (1, 4)   # onto line 1
    assert ed.find("foo") and (ed.row, ed.col) == (0, 0)   # wrap to the top
    assert ed.selected_text() == "foo"                     # the match is selected


def test_find_backward_and_case_insensitive():
    from runtime import CodeEditor
    ed = CodeEditor("Foo bar foo")
    ed.row, ed.col = 0, 11
    assert ed.find("FOO", forward=False)                   # ci by default
    assert (ed.row, ed.col) == (0, 8)
    assert ed.find("FOO", forward=False)
    assert (ed.row, ed.col) == (0, 0)
    # case-sensitive skips the lowercase one
    ed.row, ed.col = 0, 0
    assert ed.find("Foo", ci=False) and (ed.row, ed.col) == (0, 0)
    assert ed.find("Foo", ci=False) and (ed.row, ed.col) == (0, 0)  # only one match -> stays


def test_find_missing_returns_false():
    from runtime import CodeEditor
    ed = CodeEditor("hello")
    ed.row, ed.col = 0, 0
    assert ed.find("zzz") is False
    assert (ed.row, ed.col) == (0, 0)             # caret unmoved
    assert ed.find("") is False


def test_find_include_current_accepts_match_at_caret():
    # Regression: the incremental path must NOT skip a match starting exactly at
    # the caret (find() used to always scan from pos+1, so with the caret sitting
    # on a 'def' the SECOND def highlighted). Explicit next keeps move-past.
    from runtime import CodeEditor
    ed = CodeEditor("def one\ndef two")
    ed.row, ed.col = 0, 0
    assert ed.find("def", include_current=True)
    assert (ed.row, ed.col) == (0, 0)             # the match AT the caret
    assert ed.selected_text() == "def"
    assert ed.find("def")                         # explicit next moves past it
    assert (ed.row, ed.col) == (1, 0)


def test_find_with_stale_caret_never_crashes():
    # Regression: the buffer can shrink under a caller holding an old (row, col)
    # (a cut while the find bar is open); find/_offset must clamp, not IndexError.
    from runtime import CodeEditor
    ed = CodeEditor("only\ntwo lines")
    ed.row, ed.col = 49, 7                        # stale caret from a 50-line buffer
    assert ed.find("only", include_current=True)  # no crash; wraps to the match
    assert (ed.row, ed.col) == (0, 0)
    ed.row, ed.col = 30, 2
    assert ed.find("zzz") is False                # miss path is clamp-safe too


def test_typing_after_bare_anchor_keeps_all_chars():
    # Regression: begin_select() with no extending move left a zero-width anchor;
    # insert() advanced the caret past it, so the SECOND keystroke saw a bogus
    # 1-char "selection" and delete_selection() ate the first char ('ab' -> 'b').
    from runtime import CodeEditor
    ed = CodeEditor("")
    ed.begin_select()                             # SEL tapped, no drag/move yet
    ed.insert("a")
    ed.insert("b")
    assert ed.text() == "ab"
    # newline + backspace drop a dangling anchor the same way
    ed = CodeEditor("x")
    ed.row, ed.col = 0, 1
    ed.begin_select()
    ed.newline()
    ed.insert("y")
    assert ed.text() == "x\ny"
    ed = CodeEditor("xy")
    ed.row, ed.col = 0, 2
    ed.begin_select()
    ed.backspace()
    ed.insert("z")
    assert ed.text() == "xz"


# ---------------------------------------------------------------------------
# CodeLayer surface: touch affordances driven through the console (#89)
# ---------------------------------------------------------------------------

def _ws(tmp_path, **kw):
    from runtime import host_app
    ws = host_app.build_workstation(str(tmp_path / "carts"), **kw)
    ws.launcher.sel = 0
    ws.open()
    return ws


def _enter_code(ws):
    from runtime import host_app
    ws.screen = "menu"
    ws.set_menu_view("code")
    return host_app.ConsoleDriver(ws)


def _tap(drv, x, y):
    drv.touch(int(x), int(y))
    drv.frame(DT)


def _tap_tool(ws, drv, name):
    """Open the tool palette (if needed) and tap a named tool button."""
    cl = ws.code_layer
    lay = ws.code_layout
    if not cl._tools_open:
        b = cl._tls_btn(lay)
        _tap(drv, b[0] + b[2] // 2, b[1] + b[3] // 2)
    r = cl._toolbar_rect(lay)
    n = len(cl._TOOLS)
    bw = r[2] // n
    i = cl._TOOLS.index(name)
    _tap(drv, r[0] + i * bw + bw // 2, r[1] + r[3] // 2)


def _cell_xy(ws, col, row):
    """Screen center of a (visible col, visible row) code cell, gutter-aware."""
    cl = ws.code_layer
    lay = ws.code_layout
    ed = ws.editor
    tx0 = cl._text_x0(lay, ed)
    return (tx0 + (col - ed.left) * lay.cell + 2,
            lay.y0 + (row - ed.top) * lay.lh + 2)


def test_tools_toggle_and_gutter_button(tmp_path):
    ws = _ws(tmp_path)
    drv = _enter_code(ws)
    cl = ws.code_layer
    lay = ws.code_layout
    assert not cl._tools_open
    b = cl._tls_btn(lay)                           # the always-visible TLS toggle
    _tap(drv, b[0] + b[2] // 2, b[1] + b[3] // 2)
    assert cl._tools_open
    ws.editor.set_text("\n".join("row %d" % i for i in range(30)))
    assert cl._gutter_cols(ws.editor) == 0
    base_cols = ws.editor.COLS
    _tap_tool(ws, drv, "gutter")
    assert cl._gutter
    drv.frame(DT)                                  # a draw applies the gutter reflow
    assert cl._gutter_cols(ws.editor) == 3         # 30 lines -> 2 digits + 1 margin
    assert cl._text_x0(lay, ws.editor) > lay.x0    # text shifted right of the gutter
    assert ws.editor.COLS == base_cols - 3         # visible columns narrowed to fit


def test_select_mode_drag_then_copy(tmp_path):
    ws = _ws(tmp_path)
    drv = _enter_code(ws)
    ws.editor.set_text("hello world")
    _tap_tool(ws, drv, "sel")                      # enable SELECT mode
    assert ws.code_layer._select_mode and ws.editor.select_sticky
    x0, y0 = _cell_xy(ws, 0, 0)
    x1, y1 = _cell_xy(ws, 5, 0)
    drv.touch(x0, y0)                              # press: drop the anchor
    drv.frame(DT)
    drv.touch_drag(x1, y1)                         # drag: extend to col 5
    drv.frame(DT)
    drv.touch_up()
    drv.frame(DT)
    assert ws.editor.selected_text() == "hello"
    _tap_tool(ws, drv, "copy")
    assert ws.editor.clipboard == "hello"


def test_find_bar_type_and_navigate(tmp_path):
    ws = _ws(tmp_path)
    drv = _enter_code(ws)
    ws.editor.set_text("alpha beta alpha\nbeta alpha")
    cl = ws.code_layer
    _tap_tool(ws, drv, "find")                     # open the find bar
    assert cl._find_open
    for ch in "alpha":                             # type the query into the focused field
        drv.type_char(ord(ch))
        drv.frame(DT)
    assert cl._find_q == "alpha"
    assert ws.editor.selected_text() == "alpha"    # incremental match landed
    r0 = (ws.editor.row, ws.editor.col)
    lay = ws.code_layout
    nxt = cl._find_btns(lay)["next"]
    _tap(drv, nxt[0] + nxt[2] // 2, nxt[1] + nxt[3] // 2)   # next match
    assert (ws.editor.row, ws.editor.col) != r0
    prv = cl._find_btns(lay)["prev"]
    _tap(drv, prv[0] + prv[2] // 2, prv[1] + prv[3] // 2)   # prev goes back
    assert (ws.editor.row, ws.editor.col) == r0
    cls_ = cl._find_btns(lay)["close"]
    _tap(drv, cls_[0] + cls_[2] // 2, cls_[1] + cls_[3] // 2)
    assert not cl._find_open


def test_tool_palette_indent_outdent(tmp_path):
    ws = _ws(tmp_path)
    drv = _enter_code(ws)
    ws.editor.set_text("one\ntwo")
    ws.editor.row, ws.editor.col = 0, 0
    ws.editor.move(1, 1, select=True)
    _tap_tool(ws, drv, "indent")
    assert ws.editor.lines == ["  one", "  two"]
    _tap_tool(ws, drv, "outdent")
    assert ws.editor.lines == ["one", "two"]


def test_sel_mode_then_typing_keeps_first_char(tmp_path):
    # Regression (layer repro): tap SEL then type 'ab' -- the eager zero-width
    # anchor must not turn the first char into a "selection" the second one eats.
    ws = _ws(tmp_path)
    drv = _enter_code(ws)
    ws.editor.set_text("")
    _tap_tool(ws, drv, "sel")
    for ch in "ab":
        drv.type_char(ord(ch))
        drv.frame(DT)
    assert ws.editor.text() == "ab"


def test_find_survives_buffer_shrink_via_cut(tmp_path):
    # Regression (layer repro, used to IndexError in the frame loop): open find on
    # a deep caret, CUT a large span behind the still-open find bar (the tool
    # palette stays tappable -- _find_tap only consumes taps on the bar's rect),
    # then type another query char. The incremental reset used to restore the
    # UNCLAMPED anchor row into the shrunken buffer.
    ws = _ws(tmp_path)
    drv = _enter_code(ws)
    ws.editor.set_text("\n".join("line %d" % i for i in range(50)))
    ws.editor.place(0, 0)
    ws.editor.row, ws.editor.col = 49, 3          # deep caret: the recorded anchor
    _tap_tool(ws, drv, "find")                    # opens find, anchor = (49, 3)
    drv.type_char(ord("l"))
    drv.frame(DT)
    # Select nearly everything and CUT it while the find bar stays open.
    ws.editor.sel = (0, 0)
    ws.editor.row, ws.editor.col = 48, 0
    _tap_tool(ws, drv, "cut")
    assert len(ws.editor.lines) < 49              # the buffer shrank under the anchor
    drv.type_char(ord("i"))                       # next incremental keystroke
    drv.frame(DT)                                 # must not raise
    assert ws.code_layer._find_q == "li"
    assert ws.editor.row < len(ws.editor.lines)   # caret restored clamped


def test_incremental_find_selects_match_at_anchor(tmp_path):
    # Regression (layer repro): with the caret sitting ON a match, typing the query
    # incrementally must select THAT occurrence, and enter (next) moves past it.
    ws = _ws(tmp_path)
    drv = _enter_code(ws)
    ws.editor.set_text("def one\ndef two")
    ws.editor.row, ws.editor.col = 0, 0
    _tap_tool(ws, drv, "find")
    for ch in "def":
        drv.type_char(ord(ch))
        drv.frame(DT)
    assert (ws.editor.row, ws.editor.col) == (0, 0)   # the FIRST def, at the caret
    assert ws.editor.selected_text() == "def"
    drv.type_char(0x0D)                               # enter = next match
    drv.frame(DT)
    assert (ws.editor.row, ws.editor.col) == (1, 0)


def test_gutter_renders_without_error(tmp_path):
    """Turning the gutter on at font 2x fills the canvas and writes valid pixels."""
    from runtime import host_app
    ws = _ws(tmp_path, sys_size=(960, 600), font_scale=2)
    drv = _enter_code(ws)
    ws.editor.set_text("\n".join("line %d" % i for i in range(50)))
    ws.code_layer._gutter = True
    ws.code_layer._tools_open = True
    ws.code_layer._find_open = True
    ws.code_layer._find_q = "line"
    drv.frame(DT)
    buf = ws.sys_canvas.to_rgb888()
    assert len(buf) == 960 * 600 * 3
    assert len(set(buf)) > 4                        # drew content, not a flat fill
