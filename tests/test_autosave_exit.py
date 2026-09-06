"""Regression tests for #111 (owner decision: remove SAVE -- autosave is the only
model). SAVE the button and the concept are gone; every real exit path (a tab
switch, PLAY, PROJECTS, a window/context-X close, a workspace swap, going home)
must hard-commit whatever the kid was editing, so an edit immediately followed by
an exit -- with NO wait for the idle-typing debounce and NO explicit save call --
is never lost.

Driven through the SAME shared console the device runs (runtime.host_app +
ConsoleDriver), so these assert host == device behavior."""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


from ws_helpers import build_ws as _ws


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


# -- the clean-tab guard (P4 tab-switch cost, on-glass 2026-07-25) -----------
#
# save_current() skips a tab that provably has nothing to persist. On the P4 a
# commit is ~800ms of flash write + ~175ms of journal, so an unguarded commit
# made merely WALKING the tab ladder cost 0.5-1.4s per switch. These pin both
# halves: an untouched tab writes nothing, a real edit still commits on exit.

def _writes_during(ws, fn):
    """Count store writes fn() performs (the verbs every commit_* routes to)."""
    store = ws.carts_store
    names = ("save_code", "save_sprites", "save_map", "save_config",
             "save_sounds", "save_scene")
    hits = []
    orig = {}
    for n in names:
        f = getattr(store, n, None)
        if f is None:
            continue
        orig[n] = f

        def mk(n=n, f=f):
            def w(*a, **k):
                hits.append(n)
                return f(*a, **k)
            return w
        setattr(store, n, mk())
    try:
        fn()
    finally:
        for n, f in orig.items():
            setattr(store, n, f)
    return hits


def test_untouched_tab_switch_writes_nothing(tmp_path):
    """Walking the tab ladder without editing must not touch the store."""
    ws = _ws(tmp_path)
    # A cart with real assets on every tab, named rather than indexed off the
    # shelf -- and NOT one whose `_init` authors its own map (Bench msets
    # there, so its map is legitimately dirty before the walk starts).
    _open_in_editor_by_title(ws, "Hop Quest")
    ws.editor_app.set_tab("code")
    hits = _writes_during(ws, lambda: [ws.editor_app.set_tab(t)
                                       for t in ("paint", "map", "scene",
                                                 "music", "cards", "code")])
    assert hits == [], "an untouched tab ladder walk still wrote: %s" % hits


def test_a_real_edit_still_commits_on_tab_switch(tmp_path):
    """The guard must never swallow an actual edit -- each tab's own mutation
    verb (not the set_text loader) marks it dirty, and the switch persists it."""
    from runtime import moy_carts
    ws = _ws(tmp_path)
    title = next(c["title"] for c in ws.launcher.items if c.get("path"))
    path = _cart_path_by_title(ws, title)
    _open_in_editor_by_title(ws, title)

    # code: type a character through the real edit verb
    ws.editor_app.set_tab("code")
    ws.editor.goto_row(0, 0)
    ws.editor.insert("#")
    hits = _writes_during(ws, lambda: ws.editor_app.set_tab("paint"))
    assert "save_code" in hits, hits
    assert moy_carts.load(path)["src"].startswith("#")

    # paint: one pset
    ws.sheet.pset(0, 0, 7)
    hits = _writes_during(ws, lambda: ws.editor_app.set_tab("map"))
    assert "save_sprites" in hits, hits

    # map: one tile
    ws.tilemap.mset(0, 0, 1)
    hits = _writes_during(ws, lambda: ws.editor_app.set_tab("scene"))
    assert "save_map" in hits, hits

    # scene: place one actor through the editor verb (#154: scene joined the
    # guard, so a real placement must still commit)
    ws.scene_ui.sceneedit.place(16, 16)
    hits = _writes_during(ws, lambda: ws.editor_app.set_tab("music"))
    assert "save_scene" in hits, hits

    # music: one real mutation on the current SFX step
    ws.music_ui.musicedit.toggle_rest()
    hits = _writes_during(ws, lambda: ws.editor_app.set_tab("code"))
    assert "save_sounds" in hits, hits


def test_code_undo_is_not_mistaken_for_clean(tmp_path):
    """CodeEditor.set_text (the LOADER, which clears dirty) is what op_history
    replays undo/redo through -- so a `dirty`-based guard would drop an undone
    edit. The code tab compares content instead."""
    ws = _ws(tmp_path)
    title = next(c["title"] for c in ws.launcher.items if c.get("path"))
    _open_in_editor_by_title(ws, title)
    ws.editor_app.set_tab("code")
    ws.editor.set_text("# undone-into-place\n")     # loader: leaves dirty False
    assert ws.editor.dirty is False
    assert not ws.editor_app._tab_is_clean("code"), \
        "content differs from the persisted source -- must NOT read as clean"
    hits = _writes_during(ws, lambda: ws.editor_app.set_tab("cards"))
    assert "save_code" in hits, hits


# -- a HALF-TYPED line survives a fast quit (#154, owner 2026-09-06) ---------
#
# "save isn't persisted if I quit too fast". The compile gate refused the hard
# commit too, so quitting mid-line -- go home, the context X, a tab switch, the
# power button -- dropped whatever did not parse yet. The gate is SPLIT now: the
# soft paths still refuse, the hard paths write and keep the badge.

_HALF = "def _draw():\n    cls(3)\n    x = (\n"      # a line the kid is still typing


def _open_code(ws):
    title = next(c["title"] for c in ws.launcher.items if c.get("path"))
    path = _cart_path_by_title(ws, title)
    _open_in_editor_by_title(ws, title)
    ws.set_menu_view("code")
    return path


def test_a_half_typed_line_survives_going_home(tmp_path):
    """The kid's text is theirs even when Python cannot parse it yet. Broken code
    is caught at the next RUN (crash-to-code), never by silently dropping it."""
    from runtime import moy_carts
    ws = _ws(tmp_path)
    path = _open_code(ws)
    ws.editor.set_text(_HALF)

    ws.exit()                       # context-X -> go_home, no wait, no save call

    assert ws.screen == "launcher"
    assert "x = (" in moy_carts.load(path)["src"], \
        "a hard exit must persist the code tab even when it does not compile"


def test_a_half_typed_line_survives_a_tab_switch_and_keeps_its_badge(tmp_path):
    """...and the syntax status survives with it: the line is safe on disk AND
    still broken, so the badge must say so. The caret stays where the kid left
    it -- leaving a tab is not a request to be taken to the error."""
    from runtime import moy_carts
    ws = _ws(tmp_path)
    path = _open_code(ws)
    ed = ws.editor
    ed.set_text(_HALF)
    ed.row, ed.col = 1, 4
    where = (ed.row, ed.col)

    ws.set_menu_view("paint")

    assert "x = (" in moy_carts.load(path)["src"]
    assert (ws.save_status or "").startswith("SYNTAX"), ws.save_status
    assert ws.code_err_row == 2                  # the inline marker still points at it
    assert (ed.row, ed.col) == where, "the hard commit must not yank the caret"
    assert ws.cart_error is None, "nothing crashed -- the text was saved"


def test_a_forced_commit_does_not_forgive_a_struck_out_app(tmp_path):
    """commit_code clears the #160 strikes because a code fix is what makes the
    panel's "EDIT it" true. A KEPT write fixed nothing, so re-arming the guard
    would only let the same cart strike out again."""
    ws = _ws(tmp_path)
    _open_code(ws)
    forgiven = []
    ws.forgive_app = lambda cart: forgiven.append(cart)

    ws.editor.set_text(_HALF)
    ws.editor_app.save_current()                 # the hard path: writes anyway
    assert forgiven == []

    ws.editor.set_text("def _draw():\n    cls(3)\n")
    ws.editor_app.save_current()                 # ...and a real fix does forgive
    assert len(forgiven) == 1


def test_play_still_refuses_to_run_source_that_will_not_parse(tmp_path):
    """The other half of the split: the RUN gate is unchanged. PLAY keeps the kid
    in the editor with the error shown and the caret ON the bad line."""
    ws = _ws(tmp_path)
    _open_code(ws)
    ws.editor.set_text(_HALF)

    ws.run_code()

    assert ws.screen == "menu", "a cart that cannot compile must not be run"
    assert (ws.cart_error or "").startswith("Syntax error")
    assert ws.editor.row == 2, "the run gate DOES take the kid to the error"
