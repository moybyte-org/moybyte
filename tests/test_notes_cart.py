"""NOTES -- the console's one text app, and it is a CART (step 3 of
docs/text_editing_2026-09.md), on the public UI API's verbs (docs/look_2026-10.md
step 31, docs/media/look_2026-10/APPS_RESTYLE.md's Notes).

The page is `system_carts/moybyte.notes.moy/main.py`: the vault as a table, a
dialog that names a new note, a row's menu, the note's tools and the rect the
editor handle draws in. Everything below drives the REAL console the way a kid
does -- keys, typed keys and taps on the boxes the engine drew -- and asserts
what is in the store or where the console ended up. The engine behind the
handle is pinned by `tests/test_editor_handle.py`.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ws_helpers import build_desktop_ws, build_ws, select_on_home  # noqa: E402
from runtime import host_app, iconsets, moy_carts, system_api, ui_draw  # noqa: E402

DT = 1.0 / 30
ESC, BS, DEL, DUP = 0x1B, 0x08, 0x7F, 0x04


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _frames(drv, n=3):
    for _ in range(n):
        drv.ws.mark_dirty()
        drv.frame(DT)


def _open(ws):
    """Notes opened on the home, its first frames drawn: (namespace, driver)."""
    assert select_on_home(ws, "Notes") is not None
    ws.open()
    drv = host_app.ConsoleDriver(ws)
    _frames(drv)
    return ws.player.ns, drv


def _press(drv, *keys):
    for k in keys:
        drv.press(k)
        _frames(drv)


def _type(drv, text):
    for ch in text:
        drv.type_char(ord(ch) if isinstance(ch, str) else ch)
        _frames(drv, 2)


def _box(ws, id):
    """Where stop `id` drew in the Notes page's last frame."""
    got = ui_draw._C.api_box(ws.player._ui[0], id)
    assert got is not None, "Notes drew no %r" % id
    return got


def _tap(drv, box, dx=None, dy=None):
    x, y, w, h = box
    gx, gy = drv.ws.wm.viewport()[:2] if drv.ws.app_window_canvas else (0, 0)
    drv.click(gx + x + (w // 2 if dx is None else dx), gy + y + (h // 2 if dy is None else dy))
    _frames(drv)


def _seed(ws, *names):
    for i, n in enumerate(names):
        moy_carts.save_file("docs", n, "body of " + n, ws.carts_root)


def _row(ws, ns, i):
    """The box of the vault's row i (0: New note)."""
    y0, rh, view, _px, _side = ui_draw._C.api_table_rows(ws.player._ui[0])
    return (view[0], y0 + i * rh, view[2], rh)


# ---------------------------------------------------------------------------
# identity
# ---------------------------------------------------------------------------

def test_notes_is_the_text_app_and_it_is_an_ordinary_cart(tmp_path):
    ws = build_ws(tmp_path)
    cart = next(c for c in ws.carts.all if c.get("title") == "Notes")
    assert cart.get("type") == "app"
    assert system_api.is_text_app(cart)
    assert ws.is_user_app(cart), "no shell app claims it -- it runs as a cart"
    assert system_api.draws_in_look(cart), "a built-in: it draws in the look"
    assert sum(1 for c in ws.carts.all if system_api.is_text_app(c)) == 1


def test_a_copy_of_notes_draws_inside_the_cart_boundary(tmp_path):
    ws = build_ws(tmp_path)
    cart = dict(next(c for c in ws.carts.all if c.get("title") == "Notes"))
    cart["path"] = str(tmp_path / "my_notes.moy")
    assert not system_api.draws_in_look(cart)


def test_the_page_holds_only_a_handle_and_names_no_engine(tmp_path):
    """The point of the handle: what the cart holds is a SHELL object it was
    handed; a permission it did not ask for, and the engine layer under the
    verbs, have no name at all."""
    from runtime.editor_handle import EditorHandle

    ws = build_ws(tmp_path)
    _seed(ws, "one")
    ns, drv = _open(ws)
    assert callable(ns["open_editor"])
    for name in ("carts", "shell", "ui_draw"):
        assert name not in ns
    _press(drv, "down", "a")
    assert isinstance(ns["ed"], EditorHandle)
    assert ns["ed"] in ws.player._editors, "the run PINS what it opened"


# ---------------------------------------------------------------------------
# the vault
# ---------------------------------------------------------------------------

def test_the_list_is_new_note_then_the_vault_newest_first(tmp_path):
    ws = build_ws(tmp_path)
    _seed(ws, "older", "newer")
    ns, _drv = _open(ws)
    assert ns["ed"] is None, "a fresh console lands on the list"
    assert ns["names"] == moy_carts.list_files("docs", ws.carts_root)
    assert ui_draw._C.api_focus(ws.player._ui[0]) == "vault"
    assert ns["cur"] == 0, "New note is the first row"


def test_names_show_bare_for_notes_and_whole_for_other_files(tmp_path):
    ws = build_ws(tmp_path)
    ns, _drv = _open(ws)
    assert ns["shown"]("story_ideas") == "Story ideas"
    assert ns["shown"]("todo.txt") == "todo.txt"
    assert ns["shown"]("hello.py") == "hello.py"


def test_new_note_asks_a_name_and_makes_the_note(tmp_path):
    ws = build_ws(tmp_path)
    ns, drv = _open(ws)
    _type(drv, "n")
    assert ns["asking"] == "new"
    _type(drv, "milk")
    assert ns["typed"] == "milk"
    _type(drv, [0x0D])                         # Enter makes
    ed = ns["ed"]
    assert ns["asking"] is None and ed is not None and ed.name() == "milk"
    for ch in "eggs":
        ed.key(ord(ch))
    _type(drv, [ESC])                          # leaves the note, saving it
    assert ns["ed"] is None
    assert moy_carts.load_file("docs", "milk", ws.carts_root) == "eggs"
    assert "milk" in ns["names"]


def test_a_name_keeps_its_extension_and_picks_the_mode(tmp_path):
    ws = build_ws(tmp_path)
    ns, drv = _open(ws)
    for name, mode in (("todo.txt", "text"), ("data.json", "json"), ("hi.py", "code")):
        _type(drv, "n" + name)
        _type(drv, [0x0D])
        assert (ns["ed"].name(), ns["ed"].mode()) == (name, mode), name
        _type(drv, [ESC])


def test_the_dialog_cancels_by_esc_by_its_button_and_backspace_past_the_start(tmp_path):
    ws = build_ws(tmp_path)
    ns, drv = _open(ws)
    _type(drv, "n")
    _type(drv, [ESC])
    assert ns["asking"] is None
    _type(drv, "n")
    _tap(drv, ui_draw._C.api_box(ws.player._ui[0], "Cancel"))
    assert ns["asking"] is None and ns["ed"] is None
    _type(drv, "n")
    _type(drv, "a")
    _type(drv, [BS])
    assert ns["asking"] == "new", "a letter to take back first"
    _type(drv, [BS])
    assert ns["asking"] is None, "past the start it cancels"


def test_nothing_under_the_dialog_takes_a_tap(tmp_path):
    ws = build_ws(tmp_path)
    _seed(ws, "behind")
    ns, drv = _open(ws)
    row = _row(ws, ns, 1)
    _type(drv, "n")
    _tap(drv, row)
    assert ns["ed"] is None and ns["asking"] == "new"


def test_a_row_opens_its_note_by_enter_and_by_a_second_tap(tmp_path):
    ws = build_ws(tmp_path)
    _seed(ws, "story")
    ns, drv = _open(ws)
    _press(drv, "down", "a")
    assert ns["ed"].name() == "story"
    assert ns["ed"].text() == "body of story"
    _type(drv, [ESC])
    _tap(drv, _row(ws, ns, 1))
    _tap(drv, _row(ws, ns, 1))
    assert ns["ed"] is not None and ns["ed"].name() == "story"


def test_del_moves_the_focused_note_to_the_trash_without_asking(tmp_path):
    ws = build_ws(tmp_path)
    _seed(ws, "gone", "kept")
    ns, drv = _open(ws)
    i = ns["names"].index("gone") + 1
    _press(drv, *("down",) * i)
    _type(drv, [DEL])
    assert "gone" not in ns["names"] and "kept" in ns["names"]
    assert ("docs", "gone") in [tuple(t[:2]) for t in moy_carts.trash_list(ws.carts_root)]


def test_ctrl_d_duplicates_the_focused_note(tmp_path):
    ws = build_ws(tmp_path)
    _seed(ws, "twin")
    ns, drv = _open(ws)
    _press(drv, "down")
    _type(drv, [DUP])
    assert len(ns["names"]) == 2


def test_the_row_menu_renames_duplicates_and_trashes(tmp_path):
    ws = build_ws(tmp_path)
    _seed(ws, "draft")
    ns, drv = _open(ws)
    _press(drv, "down")
    _type(drv, "m")
    assert ns["menu"] is True
    _tap(drv, _box(ws, "Rename…"))
    assert ns["asking"] == "rename" and ns["typed"] == "Draft"
    _type(drv, [BS] * 5 + list("final"))
    _type(drv, [0x0D])
    assert "final" in ns["names"] and "draft" not in ns["names"]
    _type(drv, "m")
    _tap(drv, _box(ws, "Duplicate\tCtrl+D"))
    assert len(ns["names"]) == 2
    _type(drv, "m")
    _tap(drv, _box(ws, "Move to Trash\tDel"))
    assert len(ns["names"]) == 1


def test_an_empty_vault_is_an_empty_page_whose_verb_makes_a_note(tmp_path):
    ws = build_ws(tmp_path)
    ns, drv = _open(ws)
    if ns["names"]:
        for n in list(ns["names"]):
            moy_carts.delete_file("docs", n, ws.carts_root)
        ns["_refresh"]()
        _frames(drv)
    assert ui_draw._C.api_focus(ws.player._ui[0]) == "New note"
    _press(drv, "a")
    assert ns["asking"] == "new"


# ---------------------------------------------------------------------------
# a note open
# ---------------------------------------------------------------------------

def test_the_note_lends_the_bar_its_title_undo_and_redo(tmp_path):
    ws = build_ws(tmp_path)
    moy_carts.save_file("docs", "u", "start", ws.carts_root)
    ns, drv = _open(ws)
    _press(drv, "down", "a")
    ed = ns["ed"]
    lent = ui_draw.bar_lent(ws.player._ui[0])
    assert lent[0] == "U" and lent[3] is True, "the title, and the way back is the app's"
    assert [iconsets.SLOTS[s] for s in lent[1]] == ["undo", "redo"]
    assert lent[2] == 0b11, "nothing to undo or redo yet: both at half strength"
    assert ws.bar_layer._title("tool") == "U"
    for ch in "ab":
        ed.key(ord(ch))
    _frames(drv)
    ui_draw.bar_tap(ws.player._ui[0], 0)        # the bar's undo
    _frames(drv)
    assert ed.text() == "start"
    ui_draw.bar_tap(ws.player._ui[0], 1)        # ...and redo
    _frames(drv)
    assert ed.text().startswith("ab")
    ui_draw.bar_tap(ws.player._ui[0], -2)       # the bar's way back
    _frames(drv)
    assert ns["ed"] is None and ws.wm.top_is_player(), "back to the list, Notes open"


def test_the_bars_back_on_the_list_leaves_notes_and_writes_the_note(tmp_path):
    ws = build_ws(tmp_path)
    ns, drv = _open(ws)
    _type(drv, "nkept")
    _type(drv, [0x0D])
    for ch in "not lost":
        ns["ed"].key(ord(ch))
    _type(drv, [ESC])
    xb = ws.bar_layer.back_rect("tool")
    ws.pointer.place(xb[0] + 2, xb[1] + 2)
    ws.pointer.click = True
    ws.handle_pointer()
    ws.pointer.click = False
    assert ws.wm.top_kind() == "launcher"
    assert moy_carts.load_file("docs", "kept", ws.carts_root) == "not lost"
    assert ws.input.text_mode is False


def test_the_tools_select_copy_paste_and_cut(tmp_path):
    ws = build_ws(tmp_path)
    moy_carts.save_file("docs", "clip", "abcdef", ws.carts_root)
    ns, drv = _open(ws)
    _press(drv, "down", "a")
    ed = ns["ed"]
    _tap(drv, _box(ws, "Select"))
    assert ed.selecting() is True
    ed.nav(3, 0)
    _frames(drv)
    assert ns["_status"]() == "3 letters"
    _tap(drv, _box(ws, "Copy"))
    ed.select_mode(False)
    ed.nav(3, 0)
    _frames(drv)
    _tap(drv, _box(ws, "Paste"))
    assert ed.text() == "abcdefabc"
    ed.select_mode(True)
    ed.nav(-3, 0)
    _frames(drv)
    _tap(drv, _box(ws, "Cut"))
    assert ed.text() == "abcdef"


def test_a_tool_with_nothing_to_do_is_no_stop(tmp_path):
    ws = build_ws(tmp_path)
    moy_carts.save_file("docs", "plain", "x", ws.carts_root)
    ns, drv = _open(ws)
    _press(drv, "down", "a")
    assert ui_draw._C.api_box(ws.player._ui[0], "Copy") is None
    assert ui_draw._C.api_box(ws.player._ui[0], "Cut") is None
    assert ui_draw._C.api_box(ws.player._ui[0], "Select") is not None


def test_no_tool_holds_focus_while_the_note_has_the_keys(tmp_path):
    ws = build_ws(tmp_path)
    moy_carts.save_file("docs", "keys", "x", ws.carts_root)
    ns, drv = _open(ws)
    _press(drv, "down", "a")
    assert ws.focused_cart_editor() is ns["ed"]
    assert ui_draw._C.api_focus(ws.player._ui[0]) is None


def test_tapping_a_link_opens_the_note_it_names(tmp_path):
    ws = build_ws(tmp_path)
    moy_carts.save_file("docs", "other_one", "the other one", ws.carts_root)
    moy_carts.save_file("docs", "hub", "go [[Other one]] now", ws.carts_root)
    ns, drv = _open(ws)
    ns["_open"]("hub")
    _frames(drv)
    ed = ns["ed"]
    x0, _y0, cell, lh, _s = ed._geom
    row_y = next(y for brow, _si, y in ed._vis if brow == 0)
    drv.click(x0 + 5 * cell + 2, row_y + lh // 2)    # inside the link's words
    _frames(drv)
    assert ns["ed"].name() == "other_one"


def test_the_last_note_reopens_on_the_next_launch(tmp_path):
    ws = build_ws(tmp_path)
    moy_carts.save_file("docs", "kept", "still here", ws.carts_root)
    ns, drv = _open(ws)
    _press(drv, "down", "a")
    assert ws.system.get("moybyte.notes_last") == "kept"
    ws._exit_to_caller()
    ns, _drv = _open(ws)
    assert ns["ed"] is not None and ns["ed"].name() == "kept"


def test_a_drawing_embedded_by_name_reaches_the_page(tmp_path):
    """Paint's own images, inline. The cart is granted `files:docs` and never
    sees the drawing -- the shell renders it behind the handle."""
    ws = build_ws(tmp_path)
    blob = moy_carts.encode_moyimg(8, 8, bytes((14,)) * 64)
    moy_carts.save_file("drawings", "dragon", blob, ws.carts_root)
    moy_carts.save_file("docs", "art", "look\n![[dragon]]", ws.carts_root)
    ns, drv = _open(ws)
    _press(drv, "down", "a")
    ed = ns["ed"]
    assert ed._image("dragon") is not None
    assert not ns["files"].load("dragon")[0], \
        "the CART's own files handle is scoped to docs and sees no drawing"


# ---------------------------------------------------------------------------
# the doors in and out
# ---------------------------------------------------------------------------

def _open_files(ws):
    select_on_home(ws, "Files")
    ws.open()
    _frames(host_app.ConsoleDriver(ws))
    return ws.files_app


def test_files_opens_a_note_in_notes_and_x_returns_to_files(tmp_path):
    ws = build_ws(tmp_path)
    moy_carts.save_file("docs", "letter", "dear you", ws.carts_root)
    app = _open_files(ws)
    app.open_place("docs")
    app._act("OPEN", "letter")
    _frames(host_app.ConsoleDriver(ws))
    assert ws.wm.top_is_player() and ws.cart.get("title") == "Notes"
    ed = ws.player.ns["ed"]
    assert (ed.name(), ed.text()) == ("letter", "dear you")
    ws._exit_to_caller()
    assert ws.wm.top_kind() == "files", "X returns to whoever asked"


def test_the_request_is_taken_once_and_a_re_run_lands_on_the_list(tmp_path):
    ws = build_ws(tmp_path)
    moy_carts.save_file("docs", "letter", "dear you", ws.carts_root)
    app = _open_files(ws)
    app.open_place("docs")
    app._act("OPEN", "letter")
    _frames(host_app.ConsoleDriver(ws))
    assert ws._text_request is None
    ws.system.delete("moybyte.notes_last")
    ws.player.start(ws.project)
    _frames(host_app.ConsoleDriver(ws))
    assert ws.player.ns["ed"] is None


# ---------------------------------------------------------------------------
# the desk: a window is the app's surface
# ---------------------------------------------------------------------------

def _desk(tmp_path):
    ws = build_desktop_ws(tmp_path, font_scale=1, panel_diagonal_in=7.0)   # the P4: a desk
    _seed(ws, "story")
    ns, drv = _open(ws)
    return ws, ns, drv


def test_on_the_desk_the_window_is_the_page_with_no_bar_inside_it(tmp_path):
    ws, ns, drv = _desk(tmp_path)
    assert ws.app_window_canvas and ws.app_full_canvas
    win = ws.wm._wins["desktop"]
    assert (win.w, win.h) == (900, 536), "it opens at its designed size"
    cx, cy, cw, ch = win.content_rect()
    assert ns["content_rect"]() == (0, 0, cw, ch), "the page begins under the title strip"
    assert ws.wm.viewport() == (cx, cy, 1)
    assert ws.canvas.w >= cw and ws.canvas.h >= ch


def test_the_desk_shows_the_vault_beside_the_note(tmp_path):
    ws, ns, drv = _desk(tmp_path)
    vault = _box(ws, "vault")
    assert vault[0] == 0 and vault[2] < ns["SIDE"]
    ns["_open"]("story")
    _frames(drv)
    sel = _box(ws, "Select")
    assert sel[0] > ns["SIDE"], "the note's tools stand over the note"
    assert ws.wm._win_title(ws.wm._wins["desktop"]).endswith("Story")


def test_a_resized_window_moves_the_page_not_its_pixels(tmp_path):
    ws, ns, drv = _desk(tmp_path)
    cv = ws.canvas
    win = ws.wm._wins["desktop"]
    ws.wm._resize_window(win, win.w - 100, win.h - 60)
    _frames(drv)
    assert ws.canvas is cv, "the same canvas: a resize allocates nothing"
    assert ns["content_rect"]()[2:] == win.content_rect()[2:]


def test_the_canvas_goes_back_when_notes_closes(tmp_path):
    ws, ns, drv = _desk(tmp_path)
    ws._exit_to_caller()
    _frames(drv)
    assert not ws.app_window_canvas and not ws.app_full_canvas
