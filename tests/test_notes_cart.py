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

from ws_helpers import build_ws                 # noqa: E402
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
    _tap(ws, _hit(ns, "new"))
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
    _tap(ws, _hit(ns, "new"))
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
