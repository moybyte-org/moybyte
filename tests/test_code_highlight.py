"""#24 -- code-editor syntax highlighting + inline syntax-error markers.

The tokenizer is a pure module function (MicroPython-safe), so it's unit-tested
directly; the inline-error behavior is driven through the shared console exactly
like test_safe_edit drives it (host_app + a hand-authored cart).
"""

from runtime import host_app  # noqa: F401  -- registers the `editors` alias console.py needs
from runtime.console import (
    _highlight,
    _HL_TEXT, _HL_KEYWORD, _HL_STRING, _HL_NUMBER, _HL_COMMENT, _HL_BUILTIN,
)


# -- the tokenizer ----------------------------------------------------------

def test_highlight_returns_one_color_per_char():
    line = "def foo():"
    assert len(_highlight(line)) == len(line)


def test_keyword_is_colored():
    cols = _highlight("def foo():")
    assert cols[0:3] == [_HL_KEYWORD] * 3        # "def"
    assert cols[4:7] == [_HL_TEXT] * 3           # "foo" is a plain identifier


def test_builtin_verb_is_colored():
    cols = _highlight("cls(0)")
    assert cols[0:3] == [_HL_BUILTIN] * 3        # "cls"


def test_number_is_colored():
    cols = _highlight("x = 42")
    assert cols[4] == _HL_NUMBER and cols[5] == _HL_NUMBER


def test_string_is_colored_including_quotes():
    line = 's = "hi"'
    cols = _highlight(line)
    q0 = line.index('"')
    assert cols[q0] == _HL_STRING                # opening quote
    assert cols[q0 + 1] == _HL_STRING            # h
    assert cols[-1] == _HL_STRING                # closing quote


def test_comment_runs_to_end_of_line():
    line = "x = 1  # set x"
    cols = _highlight(line)
    h = line.index("#")
    assert all(c == _HL_COMMENT for c in cols[h:])


def test_hash_inside_string_is_not_a_comment():
    line = 's = "#notacomment"'
    cols = _highlight(line)
    assert all(c == _HL_STRING for c in cols[line.index('"'):])


def test_digits_in_identifier_are_not_a_number():
    cols = _highlight("x1 = 0")
    assert cols[0] == _HL_TEXT and cols[1] == _HL_TEXT   # x1 is one identifier


# -- inline syntax-error markers (driven through the console) ----------------

def _make_ws_with_cart(tmp_path, src, title="E"):
    from runtime import host_app
    carts_dir = str(tmp_path / "carts")
    host_app.kid_carts.ensure_dirs(carts_dir)
    host_app.kid_carts.create(title, carts_dir, src=src, type="app", edit=[])
    ws = host_app.build_workstation(carts_dir)
    for i, c in enumerate(ws.launcher.items):
        if c["title"] == title:
            ws.launcher.sel = i
            break
    ws.open()
    return ws


def test_syntax_error_marks_the_offending_line_and_jumps_caret(tmp_path):
    ws = _make_ws_with_cart(tmp_path, "def _draw():\n    cls(5)\n")
    ws.set_menu_view("code")
    ws.screen = "menu"
    ws.editor.set_text("a = 1\nb = 2\ndef _draw(:\n    cls(0)\n")   # broken line 3
    ws.run_code()

    assert ws.menu_view == "code"          # stayed in the editor
    assert ws.code_err_row == 2            # 0-based -> source line 3
    assert ws.code_err                     # a short reason for the inline note
    assert ws.editor.row == 2              # caret jumped onto the bad line


def test_code_view_renders_error_without_raising(tmp_path):
    ws = _make_ws_with_cart(tmp_path, "def _draw():\n    cls(5)\n")
    ws.set_menu_view("code")
    ws.screen = "menu"
    ws.editor.set_text("def _draw(:\n    cls(0)\n")
    ws.run_code()
    blank = list(ws.canvas.buf)
    ws.frame(1 / 30)                       # draws highlight + inline marker, must not raise
    assert ws.canvas.buf != blank


def test_fixing_and_saving_clears_the_marker(tmp_path):
    ws = _make_ws_with_cart(tmp_path, "def _draw():\n    cls(5)\n")
    ws.set_menu_view("code")
    ws.screen = "menu"
    ws.editor.set_text("def _draw(:\n    cls(0)\n")
    assert ws.save_code() is False
    assert ws.code_err_row is not None
    ws.editor.set_text("def _draw():\n    cls(0)\n")   # fixed
    assert ws.save_code() is True
    assert ws.code_err is None and ws.code_err_row is None


def test_runtime_crash_marks_offending_line_when_editor_opens(tmp_path):
    # A cart that raises at runtime (not a syntax error) should also drop the kid
    # on the offending line when they open the editor (#24).
    src = "def _draw():\n    cls(0)\n    boom()\n"   # boom() -> NameError on line 3
    ws = _make_ws_with_cart(tmp_path, src)
    ws.frame(1 / 30)                       # crash is captured, must not raise
    assert ws.cart_error and "boom" in ws.cart_error
    assert ws.crash_line == 3
    ws.set_menu_view("code")               # tap CODE -> editor opens on the bad line
    ws.screen = "menu"
    assert ws.code_err_row == 2            # 0-based -> source line 3
    assert ws.editor.row == 2


def test_clean_cart_has_no_crash_line(tmp_path):
    ws = _make_ws_with_cart(tmp_path, "def _draw():\n    cls(3)\n")
    ws.frame(1 / 30)
    assert ws.crash_line is None
    ws.set_menu_view("code")
    ws.screen = "menu"
    assert ws.code_err_row is None


def test_highlight_cache_reuses_result(tmp_path):
    ws = _make_ws_with_cart(tmp_path, "def _draw():\n    cls(5)\n")
    a = ws._hl("cls(7)")
    b = ws._hl("cls(7)")
    assert a is b                          # same line -> cached object
