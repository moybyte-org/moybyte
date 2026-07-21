"""Regression tests for #111 (owner decision: remove SAVE -- autosave is the only
model). SAVE the button and the concept are gone; every real exit path (a tab
switch, PLAY, PROJECTS, a window/context-X close, a workspace swap, going home)
must hard-commit whatever the kid was editing, so an edit immediately followed by
an exit -- with NO wait for the idle-typing debounce and NO explicit save call --
is never lost.

Driven through the SAME shared console the device runs (runtime.host_app +
ConsoleDriver), so these assert host == device behavior."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _ws(tmp_path, **kw):
    from runtime import host_app
    return host_app.build_workstation(str(tmp_path / "carts"), **kw)


def _open_in_editor_by_title(ws, title):
    for i, c in enumerate(ws.launcher.items):
        if c.get("title") == title:
            ws.launcher.sel = i
            break
    else:
        raise AssertionError("cart not found: " + title)
    ws.open_in_editor()


def _cart_path_by_title(ws, title):
    for c in ws.launcher.items:
        if c.get("title") == title:
            return c["path"]
    raise AssertionError("cart not found: " + title)


# -- fullscreen tier: context-X (go_home) is the immediate-exit gesture ------

def test_code_edit_survives_immediate_context_x_close(tmp_path):
    """Edit -> immediately context-X (no PLAY, no wait, no explicit save) ->
    reopen -> the edit survived. go_home() must hard-commit the Editor's active
    tab before it tears the workspace down."""
    from runtime import moy_carts
    ws = _ws(tmp_path)
    title = ws.launcher.items[ws.launcher.sel]["title"]
    while not ws.launcher.items[ws.launcher.sel].get("path"):
        ws.launcher.sel += 1
        title = ws.launcher.items[ws.launcher.sel]["title"]
    path = _cart_path_by_title(ws, title)
    ws.open_in_editor()
    ws.set_menu_view("code")
    ws.editor.set_text("def _draw():\n    cls(9)  # edited, never saved\n")
    ws.exit()                       # context-X: the ONLY thing that ran is exit()
    assert ws.screen == "launcher"
    reloaded = moy_carts.load(path)
    assert "cls(9)" in reloaded["src"], \
        "context-X must hard-commit the code tab before going home (#111)"


def test_paint_edit_survives_immediate_context_x_close(tmp_path):
    """Same contract for the sprite editor: paint a pixel, context-X immediately,
    reopen -- the pixel survived with no explicit save call."""
    from runtime import moy_carts
    from runtime.editors import SpriteSheet
    ws = _ws(tmp_path)
    while not ws.launcher.items[ws.launcher.sel].get("path"):
        ws.launcher.sel += 1
    title = ws.launcher.items[ws.launcher.sel]["title"]
    path = _cart_path_by_title(ws, title)
    ws.open_in_editor()
    ws._open_paint()
    pe = ws.paint
    pe.n = 0
    pe.color = 11
    pe.paint(0, 0)
    assert ws.sheet.dirty is True
    ws.exit()                       # context-X, no SAVE tap, no PLAY
    assert ws.screen == "launcher"
    reloaded = moy_carts.load(path)
    sheet = SpriteSheet.from_hex(reloaded["sprites"])
    assert sheet.tget(0, 0, 0) == 11, \
        "context-X must hard-commit the paint tab before going home (#111)"


# -- a workspace swap (PROJECTS -> a DIFFERENT project) is an exit path too --

def test_code_edit_survives_a_workspace_swap_via_projects(tmp_path):
    """Edit cart A's code, then PROJECTS -> open cart B (a workspace swap, never
    an explicit save) -- cart A's edit must already be on disk, because
    _open_workspace hard-commits the OUTGOING project before replacing it."""
    from runtime import moy_carts
    ws = _ws(tmp_path)
    real = [c for c in ws.launcher.items if c.get("path")]
    assert len(real) >= 2, "need at least two real carts to swap between"
    title_a, title_b = real[0]["title"], real[1]["title"]
    path_a = _cart_path_by_title(ws, title_a)

    _open_in_editor_by_title(ws, title_a)
    ws.set_menu_view("code")
    ws.editor.set_text("def _draw():\n    cls(4)  # cart A, never saved\n")

    _open_in_editor_by_title(ws, title_b)   # PROJECTS -> a DIFFERENT project

    reloaded = moy_carts.load(path_a)
    assert "cls(4)" in reloaded["src"], \
        "a workspace swap must hard-commit the outgoing project's tab (#111)"


# -- windowed WM: the title-strip X on the Make window is an exit path too --

def test_windowed_make_window_close_commits_code_edit(tmp_path):
    """#111 regression: the Make window's title-strip X used to route through
    wm_windowed.close_window_kind, which only flushed writer/storybook -- an
    Editor mid-idle-debounce closed by dragging the window shut (rather than
    using PROJECTS/PLAY) would silently lose the edit. close_window_kind must
    now hard-commit the Editor too."""
    from runtime import moy_carts
    ws = _ws(tmp_path, sys_size=(1024, 600), font_scale=2, windowed=True)
    while not ws.launcher.items[ws.launcher.sel].get("path"):
        ws.launcher.sel += 1
    title = ws.launcher.items[ws.launcher.sel]["title"]
    path = _cart_path_by_title(ws, title)
    ws.open_in_editor()
    ws.set_menu_view("code")
    ws.editor.set_text("def _draw():\n    cls(6)  # windowed close, never saved\n")
    ws.wm.close_window_kind("menu")     # the make window's own strip X
    reloaded = moy_carts.load(path)
    assert "cls(6)" in reloaded["src"], \
        "closing the Make window must hard-commit the Editor's active tab (#111)"


def test_windowed_sheets_window_close_commits_the_open_sheet(tmp_path):
    """Same #111 gap, the Sheets app: closing its window via the strip X must
    flush the open sheet (sheets_app.flush), matching writer/storybook."""
    ws = _ws(tmp_path, sys_size=(1024, 600), font_scale=2, windowed=True)
    ws.open_app(ws.sheets_app)
    app = ws.sheets_app
    app._new_sheet()
    app.sheet.set_cell(0, 0, "42")
    app._unsaved = True
    ws.wm.close_window_kind("sheets")
    assert app._unsaved is False, "closing the Sheets window must flush the open sheet"


def test_windowed_artwork_window_close_commits_the_drawing(tmp_path):
    """Same #111 gap, the Paint app (#108 user drawings): closing its window via
    the strip X must flush the open drawing (artwork_app._save)."""
    ws = _ws(tmp_path, sys_size=(1024, 600), font_scale=2, windowed=True)
    ws.open_app(ws.artwork_app)
    app = ws.artwork_app
    app.doc.put(0, 0, 5)
    app._mark_changed()
    assert app._unsaved is True
    ws.wm.close_window_kind("artwork")
    assert app._unsaved is False, "closing the Paint window must flush the open drawing"
