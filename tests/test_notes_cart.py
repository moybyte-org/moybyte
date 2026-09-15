"""NOTES -- the console's one text app, and it is a CART (step 3 of
docs/text_editing_2026-09.md).

The skin is `system_carts/notes.moy/main.py`: a list, three buttons and a rect
for the editor handle. Everything below drives the REAL console the way a kid
does -- open it off the launcher, tap what it drew, arrive from Files -- and
asserts what is on the card or where the console ended up. The engine behind
the handle is pinned separately by `tests/test_editor_handle.py`; the surface
by `app_notes` in `tests/test_shell_goldens.py`.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ws_helpers import build_ws, device_frames as _dframe  # noqa: E402
from runtime import moy_carts, system_api       # noqa: E402

DT = 1.0 / 30


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _frames(ws, n=2):
    for _ in range(n):
        ws.input.begin_frame()
        ws.frame(DT)


def _open_notes(ws):
    for i, cart in enumerate(ws.launcher.items):
        if cart.get("title") == "Notes":
            ws.launcher.sel = i
            ws.open()
            _frames(ws)
            return ws.player.ns
    raise AssertionError("Notes is not on the launcher shelf")


def _open_files(ws):
    for i, cart in enumerate(ws.launcher.items):
        if cart.get("title") == "Files":
            ws.launcher.sel = i
            ws.open()
            break
    _frames(ws)
    return ws.files_app


def _hit(ns, verb, arg=None):
    for rect, v, a in ns["hits"]._items:
        if v == verb and a == arg:
            return rect
    raise AssertionError("Notes drew no %r hit: %r"
                         % (verb, [(v, a) for _r, v, a in ns["hits"]._items]))


def _tap(ws, rect):
    x, y, w, h = rect
    ws.pointer.place(x + w // 2, y + h // 2)
    ws.pointer.click = True
    ws.input.begin_frame()
    ws.frame(DT)
    ws.pointer.click = False
    _frames(ws, 1)


def _new(ws, ns, typed=""):
    """NEW, through the prompt a kid actually gets: the button opens it, the
    name is typed, MAKE commits."""
    _tap(ws, _hit(ns, "new"))
    assert ns["naming"] is True, "NEW asks for a name"
    for ch in typed:
        ns["typed"] = ns["typed"] + ch
    _tap(ws, _hit(ns, "make"))


def _tap_xy(ws, x, y):
    ws.pointer.place(x, y)
    ws.pointer.click = True
    ws.input.begin_frame()
    ws.frame(DT)
    ws.pointer.click = False
    _frames(ws, 1)


# ---------------------------------------------------------------------------
# identity
# ---------------------------------------------------------------------------

def test_notes_is_the_text_app_and_it_is_an_ordinary_cart(tmp_path):
    ws = build_ws(tmp_path)
    cart = next(c for c in ws.carts.all if c.get("title") == "Notes")
    assert cart.get("type") == "app"
    assert system_api.is_text_app(cart)
    assert ws.is_user_app(cart), "no shell app claims it -- it runs as a cart"
    # exactly one cart claims the door, or the router would pick by scan order
    assert sum(1 for c in ws.carts.all if system_api.is_text_app(c)) == 1


def test_the_engine_is_the_shells_and_the_skin_only_holds_a_handle(tmp_path):
    """The point of the handle: what the cart holds is a SHELL object it was
    handed, not an editor it built -- and a permission it did not ask for has
    no name at all."""
    from runtime.editor_handle import EditorHandle

    ws = build_ws(tmp_path)
    moy_carts.save_file("docs", "one", "text", ws.carts_root)
    ns = _open_notes(ws)
    assert callable(ns["open_editor"])
    assert "carts" not in ns and "shell" not in ns
    _tap(ws, _hit(ns, "open", 0))
    assert isinstance(ns["ed"], EditorHandle)
    assert ns["ed"] in ws.player._editors, "the run PINS what it opened"


# ---------------------------------------------------------------------------
# the vault
# ---------------------------------------------------------------------------

def test_the_shelf_lists_the_vault_newest_first(tmp_path):
    ws = build_ws(tmp_path)
    for name in ("older", "newer"):
        moy_carts.save_file("docs", name, name, ws.carts_root)
    ns = _open_notes(ws)
    assert ns["ed"] is None, "a fresh console lands on the shelf"
    assert ns["names"] == moy_carts.list_files("docs", ws.carts_root)[:7]


def test_new_makes_a_note_types_into_it_and_back_saves_it(tmp_path):
    ws = build_ws(tmp_path)
    ns = _open_notes(ws)
    _new(ws, ns)
    ed = ns["ed"]
    assert ed is not None and ed.focused()
    name = ed.name()
    for ch in "milk":
        ed.key(ord(ch))
    _tap(ws, _hit(ns, "back"))
    assert ns["ed"] is None
    assert moy_carts.load_file("docs", name, ws.carts_root) == "milk"
    assert name in ns["names"], "and it is on the shelf"


def test_a_shelf_row_opens_that_note(tmp_path):
    ws = build_ws(tmp_path)
    moy_carts.save_file("docs", "story", "once upon a time", ws.carts_root)
    ns = _open_notes(ws)
    _tap(ws, _hit(ns, "open", 0))
    assert ns["ed"].name() == "story"
    assert ns["ed"].text() == "once upon a time"


def test_undo_and_redo_run_off_the_skins_own_buttons(tmp_path):
    ws = build_ws(tmp_path)
    moy_carts.save_file("docs", "u", "start", ws.carts_root)
    ns = _open_notes(ws)
    _tap(ws, _hit(ns, "open", 0))
    ed = ns["ed"]
    for ch in "ab":
        ed.key(ord(ch))
    _frames(ws)
    _tap(ws, _hit(ns, "undo"))
    assert ed.text() == "start"
    _tap(ws, _hit(ns, "redo"))
    assert ed.text().startswith("ab")


def test_the_last_note_reopens_on_the_next_launch(tmp_path):
    ws = build_ws(tmp_path)
    moy_carts.save_file("docs", "kept", "still here", ws.carts_root)
    ns = _open_notes(ws)
    _tap(ws, _hit(ns, "open", 0))
    assert ws.system["notes_last"] == "kept"
    ws._exit_to_caller()
    ns = _open_notes(ws)
    assert ns["ed"] is not None and ns["ed"].name() == "kept"


# ---------------------------------------------------------------------------
# the markdown a note promises
# ---------------------------------------------------------------------------

def test_tapping_a_link_opens_that_note(tmp_path):
    ws = build_ws(tmp_path)
    moy_carts.save_file("docs", "other", "the other one", ws.carts_root)
    moy_carts.save_file("docs", "hub", "go [[other]] now", ws.carts_root)
    ns = _open_notes(ws)
    _tap(ws, _hit(ns, "open", 0 if ns["names"][0] == "hub" else 1))
    ed = ns["ed"]
    assert ed.name() == "hub"
    x0, y0, cell, _lh, _s = ed._geom
    row_y = next(y for brow, _si, y in ed._vis if brow == 0)
    _tap_xy(ws, x0 + 5 * cell, row_y + 3)       # inside [[other]]
    assert ns["ed"].name() == "other"
    assert ns["ed"].text() == "the other one"


def test_a_drawing_embedded_by_name_reaches_the_page(tmp_path):
    """Paint's own images, inline. The cart is granted `files:docs` and never
    sees the drawing -- the shell renders it behind the handle."""
    ws = build_ws(tmp_path)
    blob = moy_carts.encode_moyimg(8, 8, bytes((14,)) * 64)
    moy_carts.save_file("drawings", "dragon", blob, ws.carts_root)
    moy_carts.save_file("docs", "art", "look\n![[dragon]]", ws.carts_root)
    ns = _open_notes(ws)
    _tap(ws, _hit(ns, "open", 0))
    ed = ns["ed"]
    assert ed._image("dragon") is not None
    assert not ns["files"].load("dragon")[0], \
        "the CART's own files handle is scoped to docs and sees no drawing"


# ---------------------------------------------------------------------------
# the doors in and out
# ---------------------------------------------------------------------------

def test_files_opens_a_note_in_notes_and_x_returns_to_files(tmp_path):
    ws = build_ws(tmp_path)
    moy_carts.save_file("docs", "letter", "dear you", ws.carts_root)
    app = _open_files(ws)
    app._enter_kind("docs")
    app._act("OPEN", "letter")
    _frames(ws)
    assert ws.wm.top_is_player() and ws.cart.get("title") == "Notes"
    ed = ws.player.ns["ed"]
    assert (ed.name(), ed.text()) == ("letter", "dear you")
    ws._exit_to_caller()
    assert ws.wm.top_kind() == "files", "X returns to whoever asked"


def test_the_request_is_taken_once_and_a_re_run_lands_on_the_shelf(tmp_path):
    ws = build_ws(tmp_path)
    moy_carts.save_file("docs", "letter", "dear you", ws.carts_root)
    app = _open_files(ws)
    app._enter_kind("docs")
    app._act("OPEN", "letter")
    _frames(ws)
    assert ws._text_request is None
    ws.system.pop("notes_last", None)     # the OTHER way a note reopens
    ws.player.start(ws.project)
    _frames(ws)
    assert ws.player.ns["ed"] is None


def test_the_bar_x_exits_and_writes_the_open_note(tmp_path):
    ws = build_ws(tmp_path)
    ns = _open_notes(ws)
    _new(ws, ns)
    name = ns["ed"].name()
    for ch in "not lost":
        ns["ed"].key(ord(ch))
    xb = ws.layout.context_x_btn                # the OS bar's context-X
    ws.pointer.place(xb[0] + 2, xb[1] + 2)
    ws.pointer.click = True
    ws.handle_pointer()                         # the ROUTER owns the bar contract
    ws.pointer.click = False
    assert ws.wm.top_kind() == "launcher"
    assert moy_carts.load_file("docs", name, ws.carts_root) == "not lost"
    assert ws.input.text_mode is False


# ---------------------------------------------------------------------------
# the T-Deck: the shelf and the note under the BOARDS' input state
#
# `_device_ws` puts `device/moybyte/input.py` under the console -- the class the
# boards actually run, where a keyboard SOURCE writes `last_key` and the merge
# is what the shell reads. It is the lane the owner's findings came from, so the
# key paths are asserted through it and not through the host's.
# ---------------------------------------------------------------------------

def _device_ws(tmp_path):
    from device.moybyte.input import InputState as DeviceInputState

    ws = build_ws(tmp_path)
    inp = DeviceInputState()
    inp.cart_start_ms = 0
    inp.pointer = ws.pointer    # what `touch()` reads (wire_workstation_core's)
    ws.input = inp              # before the cart starts: make_api binds it once
    return ws, inp.source("kbd")


def _dtap(ws, rect):
    """A tap in the DEVICE lane. `_tap` above cannot serve it: the boards'
    InputState carries `game_pointer`, the published game-space tuple a cart's
    `touch()` prefers, and only `handle_pointer` refreshes it -- so a tap here
    has to go through the frame the board actually runs."""
    x, y, w, h = rect
    ws.pointer.place(x + w // 2, y + h // 2)
    ws.pointer.click = True
    _dframe(ws)
    ws.pointer.click = False
    _dframe(ws)


def _press(ws, src, name):
    src.set_button(name, True)
    _dframe(ws)
    src.set_button(name, False)
    _dframe(ws)


def _type(ws, src, code):
    src.last_key = code
    _dframe(ws)
    src.last_key = 0            # the 0 gap IS the press edge keyp() reads
    _dframe(ws)


def test_new_is_visible_on_the_shelf_and_a_key_reaches_it(tmp_path):
    """Finding 1. NEW is drawn (so a finger can reach it) AND it is the shelf
    cursor's first stop, so ENTER -- the launcher's own activate key -- opens
    it on a board being driven from the keyboard."""
    ws, src = _device_ws(tmp_path)
    moy_carts.save_file("docs", "one", "x", ws.carts_root)
    ns = _open_notes(ws)
    _dframe(ws, 2)
    assert _hit(ns, "new"), "NEW draws a tap rect"
    assert ns["sel"] == 0, "...and the cursor starts on it"
    _press(ws, src, "run")                      # ENTER
    assert ns["naming"] is True


def test_the_shelf_cursor_walks_the_vault_and_a_key_opens_a_note(tmp_path):
    ws, src = _device_ws(tmp_path)
    moy_carts.save_file("docs", "first", "hello", ws.carts_root)
    ns = _open_notes(ws)
    _dframe(ws, 2)
    _press(ws, src, "down")
    assert ns["sel"] == 1
    _press(ws, src, "a")                        # L / SPACE, the other one
    assert ns["ed"] is not None and ns["ed"].name() == "first"


def test_a_name_is_typed_on_the_board_keyboard_and_its_extension_sticks(tmp_path):
    """The prompt is the ONE place this cart types, so it asks for the text
    keyboard itself (`textmode`) and reads clean bytes off `keyp()` -- and the
    key that OPENED it must not land in the field."""
    ws, src = _device_ws(tmp_path)
    ns = _open_notes(ws)
    _dframe(ws, 2)
    _press(ws, src, "run")
    assert ws.input.text_mode is True
    for ch in "todo.txt":
        _type(ws, src, ord(ch))
    assert ns["typed"] == "todo.txt"
    _type(ws, src, 0x0D)                        # ENTER commits
    assert ns["naming"] is False
    assert (ns["ed"].name(), ns["ed"].mode()) == ("todo.txt", "text")


def test_new_keeps_the_extension_a_name_carries_and_the_shelf_lists_it(tmp_path):
    """Finding 3. Nothing on the console made a `.txt` or a `.json` before
    this: NEW auto-named, and the vault listed only `.md` and scripts."""
    ws = build_ws(tmp_path)
    ns = _open_notes(ws)
    for name, mode in (("todo.txt", "text"), ("data.json", "json"),
                       ("hi.py", "code"), ("plain", "md")):
        _new(ws, ns, name)
        ed = ns["ed"]
        assert (ed.name(), ed.mode()) == (name, mode), name
        ed.key(ord("x"))                        # something to save
        _tap(ws, _hit(ns, "back"))
    assert set(ns["names"]) == {"todo.txt", "data.json", "hi.py", "plain"}
    for name in ("todo.txt", "data.json", "hi.py"):
        assert moy_carts.file_path("docs", name, ws.carts_root).endswith(name)


def test_the_shelf_badges_what_each_file_is(tmp_path):
    ws = build_ws(tmp_path)
    for name, body in (("story", "prose"), ("todo.txt", "flat"),
                       ("data.json", "{}"), ("hi.py", "print(1)"),
                       ("hi.lua", "print(1)")):
        moy_carts.save_file("docs", name, body, ws.carts_root)
    ns = _open_notes(ws)
    assert set(ns["names"]) == {"story", "todo.txt", "data.json",
                                "hi.py", "hi.lua"}
    badge = ns["files"].badge
    assert [badge(n) for n in ("story", "todo.txt", "data.json", "hi.py",
                               "hi.lua")] == ["MD", "TXT", "JSON", "PY", "LUA"]


def test_the_note_toolbar_selects_copies_pastes_and_undoes(tmp_path):
    """Finding 2. The handle always had the verbs and the skin exposed none of
    them. Everything here goes through the drawn buttons -- the only path a
    T-Deck has, because that keyboard cannot make a Ctrl chord."""
    ws, _src = _device_ws(tmp_path)
    moy_carts.save_file("docs", "clip", "abcdef", ws.carts_root)
    ns = _open_notes(ws)
    _dframe(ws, 2)
    _dtap(ws, _hit(ns, "open", 0))
    ed = ns["ed"]
    _dtap(ws, _hit(ns, "sel"))
    assert ed.selecting() is True, "SEL turns the selection mode on"
    ed.nav(3, 0)                        # a roll of the ball EXTENDS it
    _dframe(ws)
    assert ed.has_selection()
    _dtap(ws, _hit(ns, "copy"))
    ed.select_mode(False)
    ed.nav(3, 0)                        # ...to the end of the line
    _dtap(ws, _hit(ns, "paste"))
    assert ed.text() == "abcdefabc"
    _dtap(ws, _hit(ns, "undo"))
    assert ed.text() == "abcdef"
    _dtap(ws, _hit(ns, "redo"))
    assert ed.text() == "abcdefabc"


def test_cut_takes_the_selection_out_and_a_dead_button_is_not_tappable(tmp_path):
    ws = build_ws(tmp_path)
    moy_carts.save_file("docs", "cutme", "keepdrop", ws.carts_root)
    ns = _open_notes(ws)
    _tap(ws, _hit(ns, "open", 0))
    ed = ns["ed"]
    # Nothing selected: CUT and COPY draw DISABLED and register no tap rect, so
    # a kid cannot press a button that would do nothing.
    assert ns["_tool_off"]("cut") and ns["_tool_off"]("copy")
    assert not any(v == "cut" for _r, v, _a in ns["hits"]._items)
    ed.select_mode(True)
    ed.nav(4, 0)
    _frames(ws)
    _tap(ws, _hit(ns, "cut"))
    assert ed.text() == "drop"


def test_the_trackball_reaches_the_caret_of_the_focused_note(tmp_path):
    """Findings 4 and 5, the input half. On the T-Deck the ball IS the arrow
    keys, so the shell routes it to a cart's focused handle exactly as it does
    to the Editor's Code tab -- `ws.nav` is the one place that decides, and it
    says whether it spent the roll."""
    ws, _src = _device_ws(tmp_path)
    moy_carts.save_file("docs", "roll", "one\ntwo\nthree", ws.carts_root)
    ns = _open_notes(ws)
    _dframe(ws, 2)
    _dtap(ws, _hit(ns, "open", 0))
    ed = ns["ed"]
    assert ws.focused_cart_editor() is ed
    assert ws.nav(0, 2) is True, "the roll is CONSUMED by the note"
    assert ed.caret()[0] == 2
    # ...and with no note open it is the cursor's again, so the shelf can still
    # be pointed at.
    _dtap(ws, _hit(ns, "back"))
    assert ws.nav(0, 1) is False


def test_the_name_prompt_is_modal_over_the_shelf(tmp_path):
    """A finger landing beside the box must not open a note out from under the
    prompt: while it is up the shelf's own tap rects are not registered."""
    ws = build_ws(tmp_path)
    moy_carts.save_file("docs", "behind", "x", ws.carts_root)
    ns = _open_notes(ws)
    row = _hit(ns, "open", 0)
    _tap(ws, _hit(ns, "new"))
    assert [v for _r, v, _a in ns["hits"]._items] == ["make", "cancel"]
    _tap(ws, row)                       # the note behind the box
    assert ns["ed"] is None and ns["naming"] is True
    _tap(ws, _hit(ns, "cancel"))
    assert ns["naming"] is False and ns["ed"] is None
    assert ws.input.text_mode is False, "the prompt hands the keyboard back"
