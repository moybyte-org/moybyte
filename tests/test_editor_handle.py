"""The EDITOR HANDLE -- the console's text editor, drawn by a cart (step 3 of
docs/text_editing_2026-09.md).

Everything here goes through the REAL console: the handle is built by the real
factory (`Player._open_cart_editor`) over the real `Files` role and the real
clipboard, and the pixel checks read the canvas the cart draws on. What a test
asserts is the document on the card, the bytes on the screen, or where the
keyboard went -- never that a method was called.

The pixel statements are per-FEATURE and colour-addressed (a heading is accent
where a paragraph is ink; a ticked box paints where an empty one does not),
because that is what says the Markdown rendering happened rather than that
something drew. The whole-surface pin is `app_notes` in
`tests/test_shell_goldens.py`.
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import canvas_probe as probe                              # noqa: E402
from ws_helpers import build_ws                           # noqa: E402
from runtime import editor_handle, moy_carts, text_modes  # noqa: E402

DT = 1.0 / 30


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _handle(ws, name, kind="docs", mode=None):
    """A handle built the way a cart's `open_editor` builds one."""
    ctx = ws.app_context("probe", ("files", "clipboard"))
    return ws.player._open_cart_editor(ctx.files, kind, name, mode,
                                       ws.canvas, ctx.clipboard)


def _doc(tmp_path, name, body):
    carts = str(tmp_path / "carts")
    moy_carts.save_file("docs", name, body, carts)
    return carts


def _draw(ws, ed, rect=(0, 0, 320, 200)):
    ws.canvas.cls(0)
    ed.draw(*rect)
    return ws.canvas


def _row_y(ed, buffer_row, seg=0):
    """The y the last draw put one buffer line's visual row at."""
    for brow, si, y in ed._vis:
        if brow == buffer_row and si == seg:
            return y
    raise AssertionError("row %d/%d was not drawn" % (buffer_row, seg))


def _band(cv, y, height=8, x0=0, x1=None):
    """The pixel words present in one text row of the canvas.

    `x0` defaults past the caret column: the caret is drawn in the ACCENT and
    would otherwise answer every "is the accent on this row" question by
    itself."""
    px = probe.pixels(cv)
    stride = cv.w
    x1 = stride if x1 is None else min(stride, x1)
    out = set()
    for yy in range(y, y + height):
        row = yy * stride
        out.update(px[row + x0:row + x1])
    return out


CARET = 8          # the first cell, where a col-0 caret paints


# ---------------------------------------------------------------------------
# opening: identity is (kind, name), and the mode comes off the table
# ---------------------------------------------------------------------------

def test_a_handle_opens_the_document_its_kind_and_name_name(tmp_path):
    ws = build_ws(tmp_path.joinpath("a"))
    moy_carts.save_file("docs", "story", "once upon a time", ws.carts_root)
    ed = _handle(ws, "story")
    assert (ed.kind, ed.name()) == ("docs", "story")
    assert ed.mode() == text_modes.MD          # the docs kind IS markdown
    assert ed.text() == "once upon a time"
    assert ed.dirty() is False and ed.badge() == ""


def test_a_missing_document_opens_empty_rather_than_raising(tmp_path):
    ws = build_ws(tmp_path)
    ed = _handle(ws, "nothing_here")
    assert ed.text() == ""


def test_the_router_can_hand_the_handle_another_kind_and_its_mode(tmp_path):
    """A person choosing a file in Files is what widens the handle past the
    cart's own grant -- so the kind and the mode both come from the request."""
    ws = build_ws(tmp_path)
    moy_carts.save_file("music", "song", '{"bad": ', ws.carts_root)
    ed = _handle(ws, "song", kind="music", mode=text_modes.JSON)
    assert (ed.kind, ed.mode()) == ("music", text_modes.JSON)


# ---------------------------------------------------------------------------
# input: the SHELL feeds the focused handle, and the switch swallows its byte
# ---------------------------------------------------------------------------

def _device_ws(tmp_path):
    """The console with the BOARDS' InputState under it (the T-Deck lane).

    There are two InputState classes and the boards use `device/moybyte/input.py`
    -- a keyboard SOURCE writes `last_key` and the merge is what the console
    reads. Notes is opened the way a kid opens it: from the launcher."""
    from device.moybyte.input import InputState as DeviceInputState

    ws = build_ws(tmp_path)
    inp = DeviceInputState()
    inp.cart_start_ms = 0
    ws.input = inp
    for i, cart in enumerate(ws.launcher.items):
        if cart.get("title") == "Notes":
            ws.launcher.sel = i
            break
    ws.open()
    return ws, inp.source("kbd")


def _dframe(ws, n=1):
    for _ in range(n):
        ws.input.begin_frame()
        ws.handle_input()
        ws.handle_pointer()
        ws.frame(DT)


def test_the_t_deck_keyboard_types_into_the_focused_handle(tmp_path):
    ws, src = _device_ws(tmp_path)
    _dframe(ws, 2)
    ed = ws.player.ns["open_editor"]("typed")
    ed.focus()
    assert ws.input.text_mode is True          # the keyboard went to ASCII
    for ch in "hi":
        src.last_key = ord(ch)
        _dframe(ws)
        src.last_key = 0
        _dframe(ws)
    assert ed.text() == "hi"


def test_taking_the_keyboard_swallows_the_key_that_took_it(tmp_path):
    """`b2ff7de`, one rung down. Handing the keyboard to a text surface is a
    screen change and the byte that caused it is still down when the surface's
    first frame reads it -- so a note opened with a key would have typed it."""
    ws, src = _device_ws(tmp_path)
    _dframe(ws, 2)
    src.last_key = ord("k")                    # the key that opens the note
    ed = ws.player.ns["open_editor"]("seeded")
    ed.focus()
    _dframe(ws, 2)
    assert ed.text() == ""
    # A SEED, not a mute: release it and press it again and it types.
    src.last_key = 0
    _dframe(ws)
    src.last_key = ord("k")
    _dframe(ws)
    assert ed.text() == "k"


def test_a_focused_handle_owns_the_keyboard_and_the_cart_reads_nothing(tmp_path):
    ws, src = _device_ws(tmp_path)
    _dframe(ws, 2)
    ed = ws.player.ns["open_editor"]("owned")
    ed.focus()
    src.last_key = ord("q")
    _dframe(ws)
    assert ws.input.cart_key == 0 and ws.input.cart_keyp == 0
    assert ed.text() == "q"
    # ...and releasing focus hands it back, keyboard mode included.
    ed.focus(False)
    assert ws.input.text_mode is False
    src.last_key = 0
    _dframe(ws)
    src.last_key = ord("w")
    _dframe(ws)
    assert ws.input.cart_key == ord("w") and ed.text() == "q"


def test_a_blurred_handle_still_takes_a_key_the_skin_feeds_it(tmp_path):
    ws = build_ws(tmp_path)
    ed = _handle(ws, "manual")
    assert ed.focused() is False
    ed.key(ord("a"))
    ed.key(ord("b"))
    assert ed.text() == "ab"


# ---------------------------------------------------------------------------
# undo / clipboard
# ---------------------------------------------------------------------------

def test_a_typing_burst_undoes_and_redoes_as_one_step(tmp_path):
    ws = build_ws(tmp_path)
    ed = _handle(ws, "undone")
    assert ed.can_undo() is False
    for ch in "hello":
        ed.key(ord(ch))
    assert ed.can_undo() is True               # the open burst is undoable
    ed.undo()
    assert ed.text() == "" and ed.can_redo() is True
    ed.redo()
    assert ed.text() == "hello"


def test_undo_reaches_past_a_close_through_the_sidecar(tmp_path):
    ws = build_ws(tmp_path)
    ed = _handle(ws, "deep")
    for ch in "one.":
        ed.key(ord(ch))                        # the '.' closes the burst
    ed.save()
    ed.close()
    again = _handle(ws, "deep")
    assert again.text() == "one."
    assert again.can_undo() is True
    again.undo()
    assert again.text() == ""


def test_the_clipboard_carries_text_between_two_handles(tmp_path):
    ws = build_ws(tmp_path)
    moy_carts.save_file("docs", "src", "copy me", ws.carts_root)
    a = _handle(ws, "src")
    a.select_all()
    assert a.copy() is True
    b = _handle(ws, "dst")
    assert b.paste() is True
    assert b.text() == "copy me"


def test_without_the_clipboard_role_the_handle_keeps_its_own_buffer(tmp_path):
    """The `clipboard` grant is what puts a cart's copy on the SYSTEM lane; a
    cart without it still cuts and pastes inside its own document."""
    ws = build_ws(tmp_path)
    moy_carts.save_file("docs", "solo", "mine", ws.carts_root)
    ctx = ws.app_context("nolisp", ("files",))
    ed = ws.player._open_cart_editor(ctx.files, "docs", "solo", None,
                                     ws.canvas, None)
    ed.select_all()
    ed.copy()
    ed.key(ord("!"))
    ed.paste()
    assert ed.text() == "!mine"
    assert ws.clipboard.text() != "mine"       # nothing reached the system lane


# ---------------------------------------------------------------------------
# saving: the #154 gate, the dirty flag, and the hard-exit ladder
# ---------------------------------------------------------------------------

def test_a_soft_save_refuses_a_document_the_mode_cannot_parse(tmp_path):
    ws = build_ws(tmp_path)
    moy_carts.save_file("music", "cfg", '{"a": 1}', ws.carts_root)
    ed = _handle(ws, "cfg", kind="music", mode=text_modes.JSON)
    ed.set_text('{"a": ')
    ok, badge = ed.save(soft=True)
    assert ok is False and badge.startswith(editor_handle.INVALID)
    assert moy_carts.load_file("music", "cfg", ws.carts_root) == '{"a": 1}'
    # ...and the HARD save writes it anyway and keeps the badge.
    ok, badge = ed.save()
    assert ok is True and badge.startswith(editor_handle.INVALID)
    assert moy_carts.load_file("music", "cfg", ws.carts_root) == '{"a": '


def test_a_valid_document_clears_the_badge(tmp_path):
    ws = build_ws(tmp_path)
    ed = _handle(ws, "cfg", kind="music", mode=text_modes.JSON)
    ed.set_text("{")
    ed.save(soft=True)
    assert ed.badge()
    ed.set_text("{}")
    assert ed.save(soft=True)[0] is True
    assert ed.badge() == ""


def test_the_dirty_flag_tracks_the_edits_a_save_has_not_taken(tmp_path):
    ws = build_ws(tmp_path)
    ed = _handle(ws, "flagged")
    assert ed.dirty() is False
    ed.key(ord("x"))
    assert ed.dirty() is True
    ed.save()
    assert ed.dirty() is False


def test_a_read_only_store_keeps_the_text_and_reports_the_failure(tmp_path):
    ws = build_ws(tmp_path)
    ed = _handle(ws, "stuck")
    ed.set_text("still here")
    ws.can_manage = False
    ok, why = ed.save()
    assert ok is False and why
    assert ed.text() == "still here"           # editing is untouched


def test_leaving_the_cart_writes_a_dirty_handle(tmp_path):
    """The hard-exit ladder (#154): `release_world` is the one place every exit
    passes -- the X, the hold gesture, quit(), a crash -- so a kid who taps X
    never loses a note."""
    ws = build_ws(tmp_path)
    for i, cart in enumerate(ws.launcher.items):
        if cart.get("title") == "Notes":
            ws.launcher.sel = i
            break
    ws.open()
    ws.input.begin_frame()
    ws.frame(DT)
    ed = ws.player.ns["open_editor"]("unsaved")
    ed.set_text("do not lose me")
    assert ed.dirty() is True
    ws._exit_to_caller()
    assert moy_carts.load_file("docs", "unsaved", ws.carts_root) \
        == "do not lose me"
    assert ws.input.text_mode is False         # and the keyboard came back


# ---------------------------------------------------------------------------
# markdown, in pixels
# ---------------------------------------------------------------------------

def test_a_heading_draws_in_the_accent_where_a_paragraph_draws_in_ink(tmp_path):
    ws = build_ws(tmp_path)
    moy_carts.save_file("docs", "md", "# Title\nplain words\n", ws.carts_root)
    ed = _handle(ws, "md")
    cv = _draw(ws, ed)
    th = ws.theme_colors
    head = _band(cv, _row_y(ed, 0), x0=CARET)
    body = _band(cv, _row_y(ed, 1), x0=CARET)
    assert probe.word_of(th["accent"], cv) in head
    assert probe.word_of(th["accent"], cv) not in body
    assert probe.word_of(th["ink"], cv) in body
    assert probe.word_of(th["ink"], cv) not in head, "the # markers stay dim"


def test_a_checkbox_is_tappable_and_the_tick_paints(tmp_path):
    ws = build_ws(tmp_path)
    moy_carts.save_file("docs", "todo", "- [ ] milk\n", ws.carts_root)
    ed = _handle(ws, "todo")
    cv = _draw(ws, ed)
    y = _row_y(ed, 0)
    empty = probe.painted_pixels_rect(cv, 0, y, 8 * 3, 8)
    assert ed.tap(8 * 3 + 2, y + 3) == ("check", 0)
    assert ed.text() == "- [x] milk\n"
    cv = _draw(ws, ed)
    assert probe.painted_pixels_rect(cv, 0, _row_y(ed, 0), 8 * 3, 8) > empty
    # ...and it is one undo step, not five keystrokes.
    ed.undo()
    assert ed.text() == "- [ ] milk\n"


def test_a_link_is_underlined_and_a_tap_on_it_names_the_note(tmp_path):
    ws = build_ws(tmp_path)
    moy_carts.save_file("docs", "hub", "go to [[other]] now\n", ws.carts_root)
    ed = _handle(ws, "hub")
    cv = _draw(ws, ed)
    th = ws.theme_colors
    y = _row_y(ed, 0)
    # The RULE under the link, and nothing under the words either side of it.
    rule = y + 7
    assert probe.word_of(th["focus"], cv) in _band(cv, rule, 1, 6 * 8, 15 * 8)
    assert probe.word_of(th["focus"], cv) not in _band(cv, rule, 1, CARET, 6 * 8)
    assert probe.word_of(th["focus"], cv) not in _band(cv, rule, 1, 15 * 8)
    assert ed.tap(6 * 8 + 2, y + 3) == ("link", "other")
    # and a tap OFF the link is an ordinary caret place
    assert ed.tap(1, y + 3) == ("caret", None)


def test_an_embedded_drawing_paints_its_own_pixels(tmp_path):
    ws = build_ws(tmp_path)
    blob = moy_carts.encode_moyimg(8, 8, bytes((14,)) * 64)   # solid pink
    moy_carts.save_file("drawings", "dragon", blob, ws.carts_root)
    moy_carts.save_file("docs", "art", "look\n![[dragon]]\n", ws.carts_root)
    ed = _handle(ws, "art")
    cv = _draw(ws, ed)
    y = _row_y(ed, 1)
    assert probe.word_of(14, cv) in _band(cv, y)
    # a missing drawing degrades to its NAME in the danger colour, never a raise
    moy_carts.save_file("docs", "gone", "![[nope]]\n", ws.carts_root)
    ed2 = _handle(ws, "gone")
    cv = _draw(ws, ed2)
    assert probe.word_of(ws.theme_colors["danger"], cv) in \
        _band(cv, _row_y(ed2, 0))


# ---------------------------------------------------------------------------
# wrap, and the per-line memo
# ---------------------------------------------------------------------------

def test_wrap_is_the_modes_and_only_prose_wraps(tmp_path):
    ws = build_ws(tmp_path)
    body = "word " * 30
    moy_carts.save_file("docs", "long", body, ws.carts_root)
    moy_carts.save_file("music", "src", body, ws.carts_root)
    prose = _handle(ws, "long")
    code = _handle(ws, "src", kind="music", mode=text_modes.CODE)
    _draw(ws, prose)
    _draw(ws, code)
    assert text_modes.MODES[text_modes.MD].wrap is True
    assert text_modes.MODES[text_modes.CODE].wrap is False
    assert len(prose._vis) > 1, "one prose line must occupy several rows"
    assert len(code._vis) == 1, "a code line scrolls sideways, it does not wrap"


def test_the_layout_memo_holds_a_line_and_an_edit_invalidates_it(tmp_path):
    ws = build_ws(tmp_path)
    moy_carts.save_file("docs", "memo", "# one\ntwo\n", ws.carts_root)
    ed = _handle(ws, "memo")
    _draw(ws, ed)
    first = ed._row("# one", ed._view[0])
    _draw(ws, ed)
    assert ed._row("# one", ed._view[0]) is first, "an unchanged line re-parsed"
    ed.ed.row, ed.ed.col = 0, 0
    ed.key(ord("x"))                            # "x# one" -- no longer a heading
    _draw(ws, ed)
    fresh = ed._row(ed.ed.lines[0], ed._view[0])
    assert fresh is not first and fresh.head == 0


def test_a_code_lines_highlight_is_memoized_with_its_layout(tmp_path):
    """The per-frame re-highlight is the thing the memo exists to prevent, so
    the colours ride the SAME cached row the wrap does."""
    ws = build_ws(tmp_path)
    moy_carts.save_file("music", "src", "def go():\n    return 1\n",
                        ws.carts_root)
    ed = _handle(ws, "src", kind="music", mode=text_modes.CODE)
    row = ed._row("def go():", ed._view[0] or 38)
    assert row.hl is None, "nothing is highlighted before a draw"
    _draw(ws, ed)
    row = ed._row("def go():", ed._view[0])
    assert row.hl is not None and len(row.hl) == len("def go():")
    colours = row.hl
    _draw(ws, ed)
    assert ed._row("def go():", ed._view[0]).hl is colours


def test_a_checkbox_line_can_still_carry_a_link(tmp_path):
    ws = build_ws(tmp_path)
    moy_carts.save_file("docs", "both", "- [ ] read [[other]]\n",
                        ws.carts_root)
    ed = _handle(ws, "both")
    cv = _draw(ws, ed)
    y = _row_y(ed, 0)
    assert probe.word_of(ws.theme_colors["focus"], cv) in \
        _band(cv, y + 7, 1, 11 * 8, 20 * 8)
    assert ed.tap(13 * 8, y + 3) == ("link", "other")
    assert ed.tap(3 * 8 + 2, y + 3) == ("check", 0)


def test_a_resize_drops_the_memo_because_the_wrap_moved(tmp_path):
    ws = build_ws(tmp_path)
    moy_carts.save_file("docs", "wide", "word " * 30, ws.carts_root)
    ed = _handle(ws, "wide")
    _draw(ws, ed, (0, 0, 320, 200))
    wide = len(ed._vis)
    _draw(ws, ed, (0, 0, 120, 200))
    assert len(ed._vis) > wide, "a narrower page wraps into more rows"


def test_the_caret_stays_on_screen_in_a_wrapped_document(tmp_path):
    ws = build_ws(tmp_path)
    moy_carts.save_file("docs", "tall", ("sentence " * 8 + "\n") * 12,
                        ws.carts_root)
    ed = _handle(ws, "tall")
    _draw(ws, ed, (0, 0, 320, 100))
    ed.ed.row = len(ed.ed.lines) - 1
    _draw(ws, ed, (0, 0, 320, 100))
    assert any(brow == ed.ed.row for brow, _si, _y in ed._vis)


@pytest.mark.parametrize("cols,expected", [
    (10, [("one two", 0), ("three", 8)]),
    (40, [("one two three", 0)]),
])
def test_wrapping_breaks_at_spaces_and_keeps_the_column_map(cols, expected):
    assert list(editor_handle._wrap("one two three", cols, True)) == expected


def test_an_unbroken_word_is_cut_rather_than_dropped():
    got = editor_handle._wrap("aaaaaaaaaa", 4, True)
    assert "".join(t for t, _c in got) == "aaaaaaaaaa"
