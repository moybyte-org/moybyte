"""The EDITOR HANDLE -- the console's text editor, drawn by a cart (step 3 of
docs/text_editing_2026-09.md).

Everything here goes through the REAL console: the handle is built by the real
factory (`Player._open_cart_editor`) over the real `Files` role and the real
clipboard, and the pixel checks read the canvas the cart draws on. What a test
asserts is the document on the card, the bytes on the screen, or where the
keyboard went -- never that a method was called.

The document draws as a text view (`ui.text_view`): its source in the
monospace face, which tests/test_text_view.py holds pixel by pixel. What is
the handle's own is held here through the view's hit map (`ui_draw.text_at`):
which line a point lands on, a checkbox's and a link's taps, the caret kept
in view. The whole-surface pin is `app_notes` in
`tests/test_shell_goldens.py`.
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import canvas_probe as probe                              # noqa: E402
from ws_helpers import build_ws, device_frames as _dframe, select_on_home  # noqa: E402
from runtime import editor_handle, moy_carts, text_modes, ui_draw  # noqa: E402
from ws_helpers import boot_on_fixture

@pytest.fixture(autouse=True)
def _on_the_night_fixture(monkeypatch):
    """Every workstation here boots on the `night` fixture (ws_helpers): a
    palette theme with no look, drawn in the default's look flat."""
    boot_on_fixture(monkeypatch)

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


def _rows_of(ed, line):
    """The y of each of the view's rows that shows `line`, as it drew last."""
    x, y, w, h = ed._vr
    lh = ed._lh()
    return [yy for yy in range(y + 1, y + h, lh)
            if (ui_draw.text_at(ed._vh, ed._vid, x + w // 2, yy) or (-1,))[0] == line]


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


def _xy(ed, line, col):
    """The point over column `col` of `line`'s first row in the view."""
    s = getattr(ed._canvas, "font_scale", 1) or 1
    cell = ui_draw.font("mono")[1] * s
    return ed._vr[0] + 8 * s + col * cell + cell // 2, _rows_of(ed, line)[0] + 2


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
    from runtime.moy_input import InputTable as DeviceInputState

    ws = build_ws(tmp_path)
    inp = DeviceInputState()
    inp.cart_start_ms = 0
    ws.input = inp
    select_on_home(ws, "Notes")
    ws.open()
    return ws, inp.source("kbd")


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


def test_delete_takes_the_character_after_the_caret(tmp_path):
    """A handle's Delete (Notes) is forward delete; Backspace goes back."""
    ws = build_ws(tmp_path)
    ed = _handle(ws, "manual")
    for ch in "abc":
        ed.key(ord(ch))
    ed.ed.col = 1
    assert ed.key(0x7F) is not False and ed.text() == "ac"
    ed.key(0x08)
    assert ed.text() == "c"


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
    ws.store.bind()                            # the C rows read the store's state as bound
    ok, why = ed.save()
    assert ok is False and why
    assert ed.text() == "still here"           # editing is untouched


def test_leaving_the_cart_writes_a_dirty_handle(tmp_path):
    """The hard-exit ladder (#154): `release_world` is the one place every exit
    passes -- the X, the hold gesture, quit(), a crash -- so a kid who taps X
    never loses a note."""
    ws = build_ws(tmp_path)
    select_on_home(ws, "Notes")
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
# the source on the view: a program coloured, a note's taps read off its line
# ---------------------------------------------------------------------------

def test_a_program_is_coloured_and_a_note_is_its_plain_source(tmp_path):
    ws = build_ws(tmp_path)
    moy_carts.save_file("docs", "md", "def go(): return 1\n", ws.carts_root)
    moy_carts.save_file("music", "src", "def go(): return 1\n", ws.carts_root)
    note = _handle(ws, "md")
    code = _handle(ws, "src", kind="music", mode=text_modes.CODE)
    cv = _draw(ws, note)
    plain = _band(cv, _rows_of(note, 0)[0], x0=CARET + 8)
    cv = _draw(ws, code)
    tones = _band(cv, _rows_of(code, 0)[0], x0=CARET + 8)
    assert len(tones) > len(plain), "a program's words take their tones"


def test_a_checkbox_is_tappable_and_one_undo_step(tmp_path):
    ws = build_ws(tmp_path)
    moy_carts.save_file("docs", "todo", "- [ ] milk\n", ws.carts_root)
    ed = _handle(ws, "todo")
    _draw(ws, ed)
    assert ed.tap(*_xy(ed, 0, 3)) == ("check", 0)        # the box's mark
    assert ed.text() == "- [x] milk\n"
    assert ed.tap(*_xy(ed, 0, 2)) == ("check", 0)        # ...its bracket
    assert ed.text() == "- [ ] milk\n"
    ed.undo()
    assert ed.text() == "- [x] milk\n", "a tick is one undo step, not five keystrokes"
    assert ed.tap(*_xy(ed, 0, 7)) == ("caret", None)     # the words: a caret


def test_a_tap_on_a_link_names_the_note(tmp_path):
    ws = build_ws(tmp_path)
    moy_carts.save_file("docs", "hub", "go to [[other]] now ![[pic]]\n", ws.carts_root)
    ed = _handle(ws, "hub")
    _draw(ws, ed)
    assert ed.tap(*_xy(ed, 0, 6)) == ("link", "other")   # its brackets
    assert ed.tap(*_xy(ed, 0, 10)) == ("link", "other")
    # a tap OFF the link is an ordinary caret place; an embed is no link
    assert ed.tap(*_xy(ed, 0, 1)) == ("caret", None)
    assert ed.tap(*_xy(ed, 0, 23)) == ("caret", None)


def test_a_checkbox_line_can_still_carry_a_link(tmp_path):
    ws = build_ws(tmp_path)
    moy_carts.save_file("docs", "both", "- [ ] read [[other]]\n", ws.carts_root)
    ed = _handle(ws, "both")
    _draw(ws, ed)
    assert ed.tap(*_xy(ed, 0, 14)) == ("link", "other")
    assert ed.tap(*_xy(ed, 0, 3)) == ("check", 0)


# ---------------------------------------------------------------------------
# wrap, and the caret kept in view
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
    assert len(_rows_of(prose, 0)) > 1, "one prose line must occupy several rows"
    assert len(_rows_of(code, 0)) == 1, "a code line scrolls sideways, it does not wrap"


def test_a_narrower_view_wraps_into_more_rows(tmp_path):
    ws = build_ws(tmp_path)
    moy_carts.save_file("docs", "wide", "word " * 30, ws.carts_root)
    ed = _handle(ws, "wide")
    _draw(ws, ed, (0, 0, 320, 200))
    wide = len(_rows_of(ed, 0))
    _draw(ws, ed, (0, 0, 120, 200))
    assert len(_rows_of(ed, 0)) > wide, "a narrower page wraps into more rows"


def test_the_caret_stays_on_screen_in_a_wrapped_document(tmp_path):
    ws = build_ws(tmp_path)
    moy_carts.save_file("docs", "tall", ("sentence " * 8 + "\n") * 12,
                        ws.carts_root)
    ed = _handle(ws, "tall")
    _draw(ws, ed, (0, 0, 320, 100))
    ed.ed.row = len(ed.ed.lines) - 2
    ed.ed.col = len(ed.ed.lines[ed.ed.row])     # its paragraph's last row
    _draw(ws, ed, (0, 0, 320, 100))
    assert _rows_of(ed, ed.ed.row), "the caret's line is drawn"
    x, y, w, h = ed._vr
    got = ui_draw.text_at(ed._vh, ed._vid, x + w - 1, y + h - ed._lh() // 2)
    assert got is not None and got[0] == ed.ed.row, "it moved only as far as its row"


# ---------------------------------------------------------------------------
# JSON: read it, edit it, save what the kid saw
# ---------------------------------------------------------------------------

def test_a_json_document_opens_indented_and_saves_what_the_kid_sees(tmp_path):
    """A file a program wrote is one long line, which on 320px cannot be read
    or repaired. Opening one INDENTS it; saving writes exactly what is on the
    screen, and the loader parses either form."""
    ws = build_ws(tmp_path)
    moy_carts.save_file("docs", "data.json", '{"a":[1,2],"b":{"c":true}}',
                        ws.carts_root)
    ed = _handle(ws, "data.json")
    assert ed.mode() == text_modes.JSON
    assert ed.text() == ('{\n  "a": [\n    1,\n    2\n  ],\n'
                         '  "b": {\n    "c": true\n  }\n}')
    assert ed.dirty() is False, "opening is not an EDIT -- nothing is rewritten"
    ok, badge = ed.save()
    assert (ok, badge) == (True, "")
    on_card = moy_carts.load_file("docs", "data.json", ws.carts_root)
    assert on_card == ed.text(), "what was saved is what was shown"
    import json
    assert json.loads(on_card) == {"a": [1, 2], "b": {"c": True}}


def test_a_json_document_that_does_not_parse_opens_exactly_as_it_is(tmp_path):
    """The reason to open a broken document is to repair it, and re-flowing
    broken text would move the error away from where the badge points."""
    ws = build_ws(tmp_path)
    moy_carts.save_file("docs", "bad.json", '{"a": [1,2,}', ws.carts_root)
    ed = _handle(ws, "bad.json")
    assert ed.text() == '{"a": [1,2,}'
    assert ed.save(soft=True)[0] is False and ed.badge().startswith("INVALID")


def test_an_empty_container_prints_flat(tmp_path):
    ws = build_ws(tmp_path)
    moy_carts.save_file("docs", "e.json", '{"a":{},"b":[]}', ws.carts_root)
    assert _handle(ws, "e.json").text() == '{\n  "a": {},\n  "b": []\n}'


# ---------------------------------------------------------------------------
# scrolling: the caret sideways where the mode does not wrap, a drag down
# ---------------------------------------------------------------------------

def test_a_long_line_follows_the_caret_sideways_where_the_mode_does_not_wrap(tmp_path):
    ws = build_ws(tmp_path)
    moy_carts.save_file("docs", "wide.json", '{"k": "' + "x" * 200 + '"}',
                        ws.carts_root)
    ed = _handle(ws, "wide.json")
    _draw(ws, ed)
    assert ed.wraps() is False
    assert ed.ed.left == 0
    ed.nav(150, 0)
    _draw(ws, ed)
    assert ed.ed.left > 0
    _draw(ws, ed)
    assert ed.ed.left > 0, "it STAYS"


def test_a_wrapped_document_never_pans_sideways(tmp_path):
    ws = build_ws(tmp_path)
    moy_carts.save_file("docs", "prose", "word " * 200, ws.carts_root)
    ed = _handle(ws, "prose")
    _draw(ws, ed)
    assert ed.wraps() is True
    ed.nav(500, 0)
    _draw(ws, ed)
    assert ed.ed.left == 0


def test_a_long_note_scrolls_down_under_a_drag_and_stays_there(tmp_path):
    """Finding 5. `_keep_caret` used to PIN the caret on screen every draw, so
    a pan away from it was undone by the very next frame and a long note could
    not be read past its first screen."""
    ws = build_ws(tmp_path)
    moy_carts.save_file("docs", "long", "\n".join("line %d" % i
                                                  for i in range(80)),
                        ws.carts_root)
    ed = _handle(ws, "long")
    _draw(ws, ed)
    first = ed.ed.top
    ed.tap(100, 180)                        # press near the bottom...
    _draw(ws, ed)
    ed.drag(100, 20, True)                  # ...and drag up: later lines
    _draw(ws, ed)
    ed.drag(100, 20, False)
    _draw(ws, ed)
    assert ed.ed.top > first
    _draw(ws, ed)
    assert ed.ed.top > first, "and it holds across frames"
    # ...but an EDIT brings the caret back, wherever the reader had gone.
    ed.key(ord("!"))
    _draw(ws, ed)
    assert _rows_of(ed, ed.caret()[0])


def test_the_caret_is_reachable_at_the_bottom_of_a_long_note(tmp_path):
    """The 320x240 note page under the toolbar: the caret at line 79 has to
    be one of the rows the view put on the screen."""
    ws = build_ws(tmp_path)
    moy_carts.save_file("docs", "deep", "\n".join("l%d" % i for i in range(80)),
                        ws.carts_root)
    ed = _handle(ws, "deep")
    _draw(ws, ed, (4, 40, 312, 186))
    ed.nav(0, 200)                          # roll to the end
    _draw(ws, ed, (4, 40, 312, 186))
    assert ed.caret()[0] == 79
    assert _rows_of(ed, 79), "the last line is drawn"


# ---------------------------------------------------------------------------
# SELECT mode: the T-Deck's only range gesture
# ---------------------------------------------------------------------------

def test_select_mode_makes_a_drag_extend_the_selection(tmp_path):
    ws = build_ws(tmp_path)
    moy_carts.save_file("docs", "pick", "abcdefghij", ws.carts_root)
    ed = _handle(ws, "pick")
    _draw(ws, ed)
    assert ed.select_mode() is True
    ed.tap(*_xy(ed, 0, 0))                  # the press ANCHORS here
    ed.drag(*_xy(ed, 0, 5), True)
    assert ed.has_selection()
    assert ed.ed.selected_text() == "abcde"
    assert ed.copy() is True
    ed.select_mode(False)
    assert ed.has_selection() is False


def test_select_mode_makes_a_roll_of_the_ball_extend_the_selection(tmp_path):
    """The Code tab's `select_sticky` contract: with SELECT on, the same
    directional input that moves the caret grows the range instead."""
    ws = build_ws(tmp_path)
    moy_carts.save_file("docs", "roll", "abcdefghij", ws.carts_root)
    ed = _handle(ws, "roll")
    ed.select_mode(True)
    ed.nav(4, 0)
    assert ed.ed.selected_text() == "abcd"
    ed.select_mode(False)
    ed.nav(2, 0)
    assert ed.has_selection() is False, "off again, a roll just moves the caret"


def test_a_fresh_press_starts_a_fresh_selection(tmp_path):
    ws = build_ws(tmp_path)
    moy_carts.save_file("docs", "again", "abcdefghij", ws.carts_root)
    ed = _handle(ws, "again")
    _draw(ws, ed)
    ed.select_mode(True)
    ed.tap(*_xy(ed, 0, 0))
    ed.drag(*_xy(ed, 0, 6), True)
    assert ed.ed.selected_text() == "abcdef"
    ed.tap(*_xy(ed, 0, 2))                  # press again: collapse + re-anchor
    ed.drag(*_xy(ed, 0, 4), True)
    assert ed.ed.selected_text() == "cd"


def test_the_clipboard_state_the_toolbar_dims_on(tmp_path):
    ws = build_ws(tmp_path)
    moy_carts.save_file("docs", "state", "hello", ws.carts_root)
    ed = _handle(ws, "state")
    assert (ed.has_selection(), ed.can_paste()) == (False, False)
    ed.select_all()
    assert ed.has_selection() is True
    ed.copy()
    assert ed.can_paste() is True
