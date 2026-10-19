"""#89 -- the CODE tab's status gaps: text selection + clipboard, find/search,
block indent/outdent, and the line-number gutter.

Two layers, mirroring the existing split:
  * CodeEditor core (runtime.editors) -- pure buffer logic, unit-tested directly.
  * CodeLayer surface (runtime.code_layer) -- the touch affordances (tool palette,
    SELECT mode drag, find bar, gutter), driven through the SAME shared console the
    device runs (host_app + ConsoleDriver / Workstation), so they assert host==device.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

import canvas_probe as probe  # noqa: E402  (pixel-width-agnostic "it drew" probes)
from ws_helpers import device_frames as _dframe  # noqa: E402
import pytest
from ws_helpers import boot_on_fixture

@pytest.fixture(autouse=True)
def _on_the_night_fixture(monkeypatch):
    """These pin the lookless pixel bar and its coordinates: every
    workstation here boots on the `night` fixture (ws_helpers)."""
    boot_on_fixture(monkeypatch)

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
    assert probe.distinct_pixels_in(buf, 3) > 4     # drew content, not a flat fill


# ---------------------------------------------------------------------------
# CodeEditor core: autocomplete completion source (#89)
# ---------------------------------------------------------------------------

def test_word_prefix():
    from runtime import CodeEditor
    ed = CodeEditor("draw cir")
    ed.row, ed.col = 0, 8                          # caret after "cir"
    assert ed.word_prefix() == "cir"
    ed.col = 5                                     # caret just after the space -> none
    assert ed.word_prefix() == ""
    ed.col = 2                                     # caret mid-word "dr|aw"
    assert ed.word_prefix() == "dr"
    ed.set_text("x2y")                             # digits are word chars mid-run
    ed.row, ed.col = 0, 3
    assert ed.word_prefix() == "x2y"
    ed.set_text("123")                             # a number is not an identifier prefix
    ed.row, ed.col = 0, 3
    assert ed.word_prefix() == ""


def test_buffer_words_prefix_and_dedup():
    from runtime import CodeEditor
    ed = CodeEditor("score = score + player\nplayer_x = 3")
    words = ed.buffer_words("")
    # first-appearance order, de-duplicated ("score" once), numbers excluded
    assert words == ["score", "player", "player_x"]
    assert ed.buffer_words("pl") == ["player", "player_x"]
    assert ed.buffer_words("player") == ["player_x"]   # exact prefix itself excluded


def test_completions_api_then_buffer():
    from runtime import CodeEditor
    names = ("circ", "circb", "cls", "def", "class")
    ed = CodeEditor("circular = 1\ncir")
    ed.row, ed.col = 1, 3                          # prefix "cir"
    out = ed.completions(names)
    # API/keyword matches first (in the pool's order), then the buffer word
    assert out == ["circ", "circb", "circular"]
    # no prefix -> no completions (autocomplete only fires mid-word)
    ed.col = 0
    assert ed.completions(names) == []
    # limit caps the list
    ed.set_text("aa\nab\nac\nad\na")
    ed.row, ed.col = 4, 1
    assert len(ed.completions((), limit=2)) == 2


def test_complete_replaces_prefix():
    from runtime import CodeEditor
    ed = CodeEditor("x = cir")
    ed.row, ed.col = 0, 7
    assert ed.complete("circ")
    assert ed.lines == ["x = circ"]
    assert ed.col == 8
    # completing mid-line keeps the tail after the caret
    ed.set_text("cir()")
    ed.row, ed.col = 0, 3
    ed.complete("circb")
    assert ed.lines == ["circb()"]
    assert ed.col == 5


# ---------------------------------------------------------------------------
# CodeEditor core: jump-to-symbol (#89)
# ---------------------------------------------------------------------------

def test_def_symbols_python():
    from runtime import CodeEditor
    src = "import x\n\ndef update():\n    pass\n\nclass Ball(Sprite):\n    def draw(self):\n        pass"
    ed = CodeEditor(src)
    syms = ed.def_symbols()
    assert syms == [("update", 2), ("Ball", 5), ("draw", 6)]


def test_def_symbols_lua_function():
    from runtime import CodeEditor
    src = "function TIC()\nend\nlocal function helper()\nend\nfunction obj.method()\nend"
    ed = CodeEditor(src)
    syms = ed.def_symbols()
    assert syms == [("TIC", 0), ("helper", 2), ("obj.method", 4)]


def test_goto_row_moves_and_clamps():
    from runtime import CodeEditor
    ed = CodeEditor("a\nbb\nccc")
    ed.goto_row(2, 1)
    assert (ed.row, ed.col) == (2, 1)
    ed.goto_row(99, 99)                            # clamped to the buffer
    assert (ed.row, ed.col) == (2, 3)
    ed.sel = (0, 0)
    ed.goto_row(0)                                 # jump collapses any selection
    assert ed.sel is None


# ---------------------------------------------------------------------------
# CodeLayer surface: autocomplete + jump popups through the console (#89)
# ---------------------------------------------------------------------------

def test_autocomplete_popup_pick(tmp_path):
    ws = _ws(tmp_path)
    drv = _enter_code(ws)
    ws.editor.set_text("cir")
    ws.editor.row, ws.editor.col = 0, 3
    cl = ws.code_layer
    _tap_tool(ws, drv, "auto")                     # open the completion popup
    assert cl._cmp_open and cl._cmp_items[0] == "circ"
    lay = ws.code_layout
    _panel, rects = cl._cmp_geom(lay, ws.editor)
    r = rects[0]
    _tap(drv, r[0] + r[2] // 2, r[1] + r[3] // 2)  # tap the first candidate
    assert not cl._cmp_open
    assert ws.editor.lines == ["circ"]


def test_autocomplete_empty_prefix_no_popup(tmp_path):
    ws = _ws(tmp_path)
    drv = _enter_code(ws)
    ws.editor.set_text("x = ")
    ws.editor.row, ws.editor.col = 0, 4            # caret after a space -> no prefix
    _tap_tool(ws, drv, "auto")
    assert not ws.code_layer._cmp_open             # nothing to offer, stays closed


def test_autocomplete_dismissed_by_typing(tmp_path):
    ws = _ws(tmp_path)
    drv = _enter_code(ws)
    ws.editor.set_text("cir")
    ws.editor.row, ws.editor.col = 0, 3
    cl = ws.code_layer
    _tap_tool(ws, drv, "auto")
    assert cl._cmp_open
    drv.type_char(ord("c"))                        # any key dismisses + edits normally
    drv.frame(DT)
    assert not cl._cmp_open
    assert ws.editor.text() == "circ"              # the 'c' was inserted, not swallowed


def test_jump_to_symbol_pick(tmp_path):
    ws = _ws(tmp_path)
    drv = _enter_code(ws)
    ws.editor.set_text("def a():\n    pass\ndef b():\n    pass\ndef c():\n    pass")
    ws.editor.row, ws.editor.col = 0, 0
    cl = ws.code_layer
    _tap_tool(ws, drv, "goto")
    assert cl._jump_open
    assert [n for n, _ in cl._jump_items] == ["a", "b", "c"]
    lay = ws.code_layout
    _panel, rects = cl._jump_geom(lay)
    r = rects[2]                                   # tap "c" (row 4)
    _tap(drv, r[0] + r[2] // 2, r[1] + r[3] // 2)
    assert not cl._jump_open
    assert ws.editor.row == 4


def test_jump_tap_outside_dismisses(tmp_path):
    ws = _ws(tmp_path)
    drv = _enter_code(ws)
    ws.editor.set_text("def a():\n    pass")
    cl = ws.code_layer
    _tap_tool(ws, drv, "goto")
    assert cl._jump_open
    _tap(drv, 1, 1)                                # a tap off the popup closes it
    assert not cl._jump_open


def test_completion_popup_renders_without_error(tmp_path):
    """The popups draw over the code body without touching the frozen baseline."""
    ws = _ws(tmp_path)
    drv = _enter_code(ws)
    ws.editor.set_text("cir")
    ws.editor.row, ws.editor.col = 0, 3
    ws.code_layer._open_completion(ws.editor)
    drv.frame(DT)
    buf = ws.sys_canvas.to_rgb888()
    assert probe.distinct_pixels_in(buf, 3) > 4


# ---------------------------------------------------------------------------
# Entering the Code tab must not type the key that entered it
# ---------------------------------------------------------------------------

def _device_ws(tmp_path):
    """The console with the BOARDS' InputState under it.

    There are two InputState classes and the boards use `device/moybyte/input.py`
    (the two are pinned separate on purpose -- see .claude/rules/shell.md). The
    key feed under test is that one's: a keyboard SOURCE writes `last_key`, and
    the merge is what the console reads."""
    import sys
    sys.path.insert(0, str(ROOT))
    from runtime import host_app
    from runtime.moy_input import InputTable as DeviceInputState

    ws = host_app.build_workstation(str(tmp_path / "carts"))
    inp = DeviceInputState()
    inp.cart_start_ms = 0
    ws.input = inp
    ws.launcher.sel = 0
    ws.open()
    return ws, inp.source("kbd")


def test_entering_the_code_tab_does_not_type_the_key_that_entered_it(tmp_path):
    """The owner on the T-Deck: "a bug in the code tab, it would seem to add a
    random letter when entering it."

    Taking the keyboard for a text surface is a screen change, and the key that
    made it happen is still in `last_key` when that surface's first frame runs.
    The code editor's edge tracker was RESET to 0 on entry -- so the byte
    already there read as a fresh keystroke and went into the buffer at the
    caret, whichever key it was. Every tab the kid can arrive from, and the
    re-entry that is already on the tab.
    """
    for entry, key in (("cards", ord("m")), ("paint", ord("l")),
                       ("map", ord(" ")), ("code", 0x0D)):
        ws, src = _device_ws(tmp_path)
        ws.screen = "menu"
        ws.set_menu_view(entry)
        _dframe(ws)
        ws.set_menu_view("code")            # build the editor before measuring
        _dframe(ws)
        ws.set_menu_view(entry)
        _dframe(ws)
        before = ws.editor.text()

        src.last_key = key                  # the key that enters the tab
        ws.set_menu_view("code")
        _dframe(ws, 2)
        src.last_key = 0
        _dframe(ws)
        assert ws.editor.text() == before, \
            "entering code from %r with %r down typed it" % (entry, key)


def test_the_swallowed_key_still_types_when_it_is_pressed_again(tmp_path):
    """The seed must be a seed, not a mute: an already-down byte looks like the
    PREVIOUS one, so releasing and pressing it again still types -- and so does
    any other key. Without this the fix would be a code editor that ignores its
    first keystroke."""
    ws, src = _device_ws(tmp_path)
    ws.screen = "menu"
    before = ws.editor.text() if ws.editor is not None else None
    src.last_key = ord("m")
    ws.set_menu_view("code")
    _dframe(ws)
    before = ws.editor.text()
    src.last_key = 0
    _dframe(ws)
    src.last_key = ord("m")                 # the SAME key, pressed again
    _dframe(ws)
    src.last_key = 0
    _dframe(ws)
    src.last_key = ord("z")                 # and a different one
    _dframe(ws)
    src.last_key = 0
    _dframe(ws)
    assert ws.editor.text() == "mz" + before


# ---------------------------------------------------------------------------
# The 2026-10 look (docs/look_2026-10.md step 23): the current line and the fade
# ---------------------------------------------------------------------------

def _w(cv, x, y):
    """The RGB565 word at (x, y): a theme's colours are block slots that
    `pix` does not read back."""
    i = (y * cv._stride + x) * 2
    return cv._buf[i] | (cv._buf[i + 1] << 8)


def _look_ws(tmp_path, theme, variant="light"):
    ws = _ws(tmp_path)
    ws.look.set_theme(theme, persist=False, variant=variant)
    drv = _enter_code(ws)
    lay = ws.code_layout
    ed = ws.editor
    ed.set_text("\n".join(["x = 1", "y" * (ed.COLS + 9), "z" * ed.COLS, "w = 2"]))
    ed.row, ed.col, ed.top, ed.left = 0, 2, 0, 0
    drv.frame(DT)
    return ws, drv, lay, ed


def test_the_current_line_wears_the_current_mark_and_the_value_bar(tmp_path):
    ws, drv, lay, ed = _look_ws(tmp_path, "moy")
    cv, th = ws.sys_canvas, ws.theme_colors
    value, surface = cv._col(th["value"]), cv._col(th["surface"])
    mid = lay.y0 + lay.lh // 2
    assert _w(cv, lay.cx, mid) == value and _w(cv, lay.cx + lay.fs, mid) == value
    far = lay.x0 + 10 * lay.cell                 # past the short line's text
    assert _w(cv, far, mid) not in (value, surface), "the current plate"
    assert _w(cv, far, mid + 3 * lay.lh) == surface, "another line is bare"
    assert _w(cv, lay.cx, mid + 3 * lay.lh) == surface
    ed.row = 3
    drv.frame(DT)
    assert _w(cv, lay.cx, mid) == surface and _w(cv, lay.cx, mid + 3 * lay.lh) == value


def test_a_theme_without_a_depth_keeps_its_code_tab(tmp_path):
    ws, _drv, lay, _ed = _look_ws(tmp_path, "night", "dark")
    cv = ws.sys_canvas
    mid = lay.y0 + lay.lh // 2
    bg = cv._col(ws.code_layer._t["bg"])
    assert _w(cv, lay.cx, mid) == _w(cv, lay.x0 + 10 * lay.cell, mid) == bg


def _band(cv, x0, x1, y, h):
    return [_w(cv, x, yy) for yy in range(y, y + h) for x in range(x0, x1)]


def test_a_line_longer_than_the_view_fades_over_its_last_12_pixels(tmp_path):
    from runtime.code_layer import _FADE
    ws, _drv, lay, ed = _look_ws(tmp_path, "moy")
    cv = ws.sys_canvas
    x1 = lay.x0 + ed.COLS * lay.cell
    x0 = x1 - _FADE * lay.fs
    long_y, fits_y = lay.y0 + lay.lh, lay.y0 + 2 * lay.lh
    # the same glyphs ("y" and "z" differ, so compare each line with its own
    # left: the fitting line's band holds its ink, the long line's has none)
    ink = cv._col(ws.look.content_colors()["code_text"])
    assert ink in _band(cv, x0, x1, fits_y, lay.lh)
    assert ink in _band(cv, lay.x0, lay.x0 + _FADE, long_y, lay.lh)
    assert ink not in _band(cv, x0 + _FADE * lay.fs // 3, x1, long_y, lay.lh)


def _edges(cv, lay, y, c):
    """The columns of the line at y whose every row is colour c: a mark's
    vertical edges (no glyph fills a line's whole height)."""
    return [x for x in range(lay.cx, lay.cx + lay.cw)
            if all(_w(cv, x, yy) == c for yy in range(y, y + lay.lh))]


def test_a_faded_current_line_wears_one_outline(tmp_path):
    """Contrast's current line is an outline: a line longer than the view,
    drawn in two parts around its fade, is still ONE box -- no second edge
    where the fade starts (design review R2-14)."""
    for variant in ("dark", "light"):
        ws, drv, lay, ed = _look_ws(tmp_path / variant, "contrast", variant)
        ed.row = 1                                   # the line longer than the view
        drv.frame(DT)
        cv = ws.sys_canvas
        y = lay.y0 + lay.lh
        x = lay.cx + lay.cw - 20                     # inside the fade
        ring = _w(cv, x, y)                          # the outline's top edge
        assert ring == _w(cv, x, y + lay.lh - 1), variant
        assert ring != cv._col(ws.code_layer._t["bg"]), variant
        caret = lay.x0 + (ed.col - ed.left) * lay.cell - lay.fs   # 2 * fs wide
        inner = [x for x in _edges(cv, lay, y, ring)
                 if lay.cx + 2 * lay.fs < x < lay.cx + lay.cw - 4 * lay.fs
                 and not caret <= x < caret + 2 * lay.fs]
        assert inner == [], (variant, inner)


def test_a_disabled_undo_draws_in_the_themes_disabled_ink(tmp_path):
    """Undo/Redo with nothing to walk take the look's `disabled` role (about
    3:1), not the bar's dim ink -- which in Contrast is near full ink (design
    review R2-09)."""
    from runtime import ui_draw
    for theme, variant in (("contrast", "dark"), ("contrast", "light"),
                           ("moy", "dark")):
        ws, _drv, _lay, _ed = _look_ws(tmp_path / (theme + variant), theme, variant)
        cv, th = ws.sys_canvas, ws.theme_colors
        cv.rect(0, 0, 24, 24, th["surface"])
        ws.editor_app._draw_history_icon(cv, "undo", 4, 4, 16, False)
        box = set(_band(cv, 0, 24, 0, 24))
        off = cv._col(ui_draw.disabled_ink(th))
        assert off != cv._col(th["chrome_ink_dim"]), (theme, variant)
        assert off in box, (theme, variant)
        assert cv._col(th["chrome_ink_dim"]) not in box, (theme, variant)


def test_the_status_band_is_one_height_and_starts_on_the_code_column(tmp_path):
    """Under every theme's own `ui` face the status band is one height, in
    both modes, and its text pad is the code's left inset, so the status
    line and the code start on one column (design review R2-22)."""
    from runtime import ui_draw
    bands = set()
    for theme, variant in (("moy", "light"), ("moy", "dark"), ("blocks", "light"),
                           ("contrast", "light"), ("contrast", "dark")):
        ws, _drv, _lay, _ed = _look_ws(tmp_path / (theme + variant), theme, variant)
        lay = ws.code_layout
        bx, by, bw, bh = lay.status_band
        bands.add(bh)
        pad = ui_draw.metrics("status")[ui_draw.ST_PAD] * lay.fs
        assert lay.x0 == bx + pad, (theme, variant)
        assert by + bh <= lay.sym_y and lay.y0 + lay.rows * lay.lh <= by, (theme, variant)
    assert len(bands) == 1, bands


@pytest.mark.parametrize("variant", ("light", "dark"))
@pytest.mark.parametrize("seed", ("moy", "blocks", "contrast"))
def test_the_caret_and_a_find_match_read_on_the_code_surface(tmp_path, seed, variant):
    """The caret and the find outline are the accent as an ink, at least
    3:1 on the code surface under every seed theme; Contrast Light's own
    accent is its yellow, 1.27:1 on white (design review R2-02)."""
    from runtime import ui_draw

    def lum(v):
        c = ui_draw._C.theme_rgb(v)[0]
        rgb = ((c >> 11) * 255 // 31, ((c >> 5) & 63) * 255 // 63, (c & 31) * 255 // 31)
        lin = [x / 255 / 12.92 if x / 255 <= 0.03928 else ((x / 255 + 0.055) / 1.055) ** 2.4
               for x in rgb]
        return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]

    _ws, _drv, _lay, _ed = _look_ws(tmp_path, seed, variant)
    t = _ws.code_layer._tones()
    bg = lum(t["bg"])
    for role in ("caret", "find"):
        fg = lum(t[role])
        ratio = (max(fg, bg) + 0.05) / (min(fg, bg) + 0.05)
        assert ratio >= 3.0, (seed, variant, role, round(ratio, 2))


def test_the_open_tabs_icon_takes_the_current_marks_ink(tmp_path):
    """On the labelled ladder the open tab's icon takes the ink its name takes
    on the current mark (Moy's `current.ink`), the other tabs the chrome's."""
    from runtime import ui_draw
    ws, _drv, _lay, _ed = _look_ws(tmp_path, "moy", "light")
    cv, th = ws.sys_canvas, ws.theme_colors
    app = ws.editor_app
    rect = (0, 0, cv.w, 20)
    assert not app._compact(rect), "the labelled ladder fits the strip"
    cur = ui_draw.current_ink(cv, th)
    assert cur is not None and cur != th["chrome_ink"]
    seen = {}
    real = ws.draw_icon
    ws.draw_icon = lambda kind, r, ink, c=None: (seen.setdefault(kind, ink),
                                                 real(kind, r, ink, c))[1]
    try:
        app.draw_zone(cv, rect)
    finally:
        ws.draw_icon = real
    assert seen["code"] == cur, seen
    assert seen["blocks"] == th["chrome_ink"], seen


def test_the_labelled_ladder_keeps_8px_from_a_pill_to_the_next_icon(tmp_path):
    """Each labelled chip ends at least 8px (at the zone's scale) before the
    next chip's icon, so the open tab's pill never crowds its neighbour; the
    draw and the tap share the rects (design review R2-08)."""
    from runtime import ui_draw
    ws, _drv, _lay, _ed = _look_ws(tmp_path, "moy", "light")
    app = ws.editor_app
    rect = (0, 0, 700, 20)                       # a desk window's lent zone
    tabs_area, _play = app._zone_parts(rect)
    rects = app._shelf_rects(tabs_area)
    fs = app._zone_scale()
    pad = ui_draw.metrics("tab")[ui_draw.TAB_PAD] * fs
    assert len(rects) == len(app._chips()) and rects[0][2], "the whole ladder, labelled"
    for (_a, r, _on), (_b, nxt, _on2) in zip(rects, rects[1:]):
        assert nxt[0] + pad - (r[0] + r[2]) >= 8 * fs, (r, nxt)
    for tid, r, _on in rects:
        assert app.zone_tap(r[0] + r[2] // 2, r[1] + r[3] // 2, rect) is not False, tid
