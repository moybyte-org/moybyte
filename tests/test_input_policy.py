"""The input owner's guard (docs/refactor_2026-11.md, phase 5).

Four policies have one owner, runtime/shell/input_policy.py: text mode,
cancel, back and the trackball's steps. Every surface answers the input
protocol `layers.Layer` defaults; the owner asks the one holding the keys.
This file holds that shape in place:

- a table over every registered app, Settings, the Editor's tabs and a cart
  app: a focused field types, Mic gives a draft back and opens no menu, Esc,
  B and the bar's chevron end in one state, a menu over a field is game mode
  and typing comes back after it, a pending edit survives every exit;
- Ctrl+Z on every Editor tab is the bar's UNDO, past the tab's own steps;
- a scan: the mode's writers live in the owner and the input layer alone.
"""

import re
from pathlib import Path

import pytest

from ws_helpers import build_ws, build_desktop_ws, make_drv, open_cart, select_on_home
from runtime import app_decls
from runtime import layers
from runtime.editor_app import COMMIT_TABS

ROOT = Path(__file__).resolve().parent.parent
DT = 1 / 30


def _frames(drv, n=3):
    for _ in range(n):
        drv.frame(DT)


def _type(drv, text):
    for ch in text:
        drv.type_char(ord(ch))
        drv.frame(DT)
        drv.frame(DT)


# -- every surface answers the protocol -------------------------------------

def test_every_surface_is_a_layer(tmp_path):
    """No duck typing: each registered app, Settings, Tasks, the home and every
    Editor tab is a `layers.Layer`, so the owner asks it without `getattr`."""
    ws = build_ws(tmp_path)
    for d in app_decls.APPS:
        assert isinstance(ws.app(d["id"]), layers.AppLayer), d["id"]
    for kind in ("settings", "tasks", "launcher", "desktop", "update"):
        assert isinstance(ws.surface_of(kind), layers.Layer), kind
    for tab in COMMIT_TABS + ("paint",):
        assert isinstance(ws.layer_of(tab), layers.Layer), tab


# -- a field in focus types; Mic gives its draft back -------------------------

def _files_rename(ws):
    f = ws.app("files")
    f.rename_text = "note"
    f.dialog = "rename"
    return lambda: f.rename_text, "note"


def _appearance_name(ws):
    a = ws.app("appearance")
    a._open_name()
    return lambda: a.name_text, ""


def _music_add(ws):
    m = ws.app("jukebox")
    m._go("add")
    # Music's own cancel closes Add a station, its draft with it
    return lambda: m.url if m.page == "add" else "", ""


FIELDS = {"files": _files_rename, "appearance": _appearance_name,
          "jukebox": _music_add}


@pytest.mark.parametrize("app_id", sorted(FIELDS))
def test_a_shell_apps_field_types_and_mic_gives_it_back(tmp_path, app_id):
    ws = build_ws(tmp_path)
    drv = make_drv(ws)
    assert ws.spine.open_app(ws.app(app_id))
    _frames(drv)
    assert not ws.input.text_mode, "the app's rows are no text surface"
    read, was = FIELDS[app_id](ws)
    _frames(drv)
    assert ws.input.text_mode, "a focused field holds the keyboard in text mode"
    _type(drv, "xy")
    assert read() == was + "xy"
    assert ws.input_policy.cancel(), "Mic inside an edit takes it"
    _frames(drv)
    assert not ws.sysmenu.open, "and opens no menu"
    assert read() == was, "the draft is given back"
    assert not ws.input.text_mode, "focus left the field: game mode"


def _focus_name(ws, drv):
    ws.settings_layer._want = "name"
    ws.mark_dirty()                  # the next frame draws, and focuses it
    _frames(drv)


def test_settings_name_types_and_mic_gives_it_back(tmp_path):
    ws = build_ws(tmp_path)
    drv = make_drv(ws)
    sl = ws.settings_layer
    ws.open_settings()
    _frames(drv)
    _focus_name(ws, drv)
    assert ws.input.text_mode
    name = str(ws.system.get("name", "") or "")
    _type(drv, "zq")
    assert sl.rows.draft("name") == name + "zq"
    assert ws.input_policy.cancel()
    _frames(drv)
    assert sl.rows.draft("name") in (None, name) and not ws.sysmenu.open
    assert not ws.input.text_mode


def test_the_terminal_and_the_code_tab_type_always(tmp_path):
    ws = build_ws(tmp_path)
    assert ws.app("terminal").typing() and ws.code_layer.typing()


# -- a menu over a field is game mode, and the field types again after -------

def test_a_menu_over_a_field_is_game_mode_and_typing_comes_back(tmp_path):
    ws = build_ws(tmp_path)
    drv = make_drv(ws)
    ws.spine.open_app(ws.app("files"))
    _files_rename(ws)
    _frames(drv)
    assert ws.input.text_mode
    ws.system_key()                       # the console's key: the system menu
    _frames(drv)
    assert ws.sysmenu.open and not ws.input.text_mode
    ws.system_key()
    _frames(drv)
    assert not ws.sysmenu.open and ws.input.text_mode


# -- Esc, B and the bar's chevron are one cascade ---------------------------

def _state(ws):
    return (ws.wm.top_kind(), ws.app_has_page(ws.wm.top_kind()))


def _esc(ws, drv):
    drv.escape()
    _frames(drv)


def _b(ws, drv):
    drv.press("b")
    _frames(drv)


def _chevron(ws, drv):
    ws.bar_layer.go_back("settings" if ws.wm.top_is("settings") else "app")
    _frames(drv)


BACKS = {"esc": _esc, "b": _b, "chevron": _chevron}


@pytest.mark.parametrize("app_id", [d["id"] for d in app_decls.APPS] + ["settings"])
def test_esc_b_and_the_chevron_end_in_one_state(tmp_path, app_id):
    """From an app's (or Settings') first page each way back lands on the
    same surface, and on a page each steps it back alike."""
    ends = {}
    for how, back in BACKS.items():
        if how == "esc" and app_id == "terminal":
            continue                  # a text surface's Esc is its byte 27
        ws = build_ws(tmp_path / how)
        drv = make_drv(ws)
        if app_id == "settings":
            ws.open_settings()
        else:
            assert ws.spine.open_app(ws.app(app_id))
        _frames(drv)
        back(ws, drv)
        ends[how] = _state(ws)
    assert len(set(ends.values())) == 1, ends


def test_a_desk_windows_chevron_and_esc_step_its_page(tmp_path):
    ws = build_desktop_ws(tmp_path)
    drv = make_drv(ws)
    ws.home.open_desk()
    ws.spine.open_app(ws.app("storybook"))
    _frames(drv)
    app = ws.app("storybook")
    for how in ("esc", "window"):
        app.page = "deck"
        assert ws.app_has_page("storybook")
        if how == "esc":
            drv.escape()
        else:
            ws.wm._win_back(ws.wm.windows["storybook"])
        _frames(drv)
        assert app.page == "stories" and "storybook" in ws.wm.windows, how


# -- a pending edit survives every exit -----------------------------------
#
# A surface's edit not yet written -- a Name still typed, a stroke on the
# drawing, a story's change, a look still settling, an Editor tab's edit, a
# cart app's note -- is written, or still held, after every exit: home, a
# jump to another app, the window's X, and the hand-off to an update. Back is
# no exit for an edit in progress: its first step gives a field its draft
# back, as Mic does. Each recipe opens its surface on `ws`, makes the edit and
# answers the check that reads it back after the exit.

def _settings_name(ws, drv):
    ws.open_settings()
    _frames(drv)
    _focus_name(ws, drv)
    _type(drv, "kq")
    assert ws.settings_layer.rows.draft("name") is not None
    return lambda: str(ws.system.get("name", "")).endswith("kq")


def _paint_stroke(ws, drv):
    from runtime import moy_carts
    app = ws.app("artwork")
    ws.spine.open_app(app)
    _frames(drv)
    doc = app.doc
    doc.snapshot()
    doc.put(5, 5, 14)
    doc.finish()
    app._mark_changed()
    at = 5 * doc.W + 5

    def check():
        name = ws.artwork.doc_name()
        blob = name and moy_carts.load_file("drawings", name, ws.carts_root)
        return bool(blob) and moy_carts.decode_moyimg(blob)[2][at] == 14
    return check


def _story_edit(ws, drv):
    import json
    from runtime import moy_carts
    from runtime import story_doc as D
    moy_carts.save_file("stories", "brave/story.json", D.write(D.new_doc("Brave")),
                        ws.carts_root)
    app = ws.app("storybook")
    ws.spine.open_app(app)
    _frames(drv)
    app._open_story(next(s for s in app.stories if s["name"] == "brave"), "deck")
    st = app.story
    st.slides[0]["objects"].append(D.text_obj("kq words", 64, 64, 400, 72, 1))
    st.dirty = True
    return lambda: "kq words" in json.dumps(D.read(moy_carts.load_file(
        "stories", "brave/story.json", ws.carts_root)))


def _look_change(ws, drv):
    import json
    from runtime import ui_draw
    ws.spine.open_app(ws.app("appearance"))
    _frames(drv)
    accent = ui_draw.theme_accents()[7][1]
    ws.look.change("accent", accent)
    ws.look.tick(0.0)
    man = Path(ws.carts_root) / "custom.moy" / "manifest.json"
    return lambda: man.is_file() and json.loads(
        man.read_bytes())["moybyte"]["look"].get("accent") == accent


def _tab_edit(tab):
    """An edit on Editor tab `tab` of a cart with blocks (Coin Quest), made by
    the tab's own mutation verb; the check: the cart's stored `key` moved."""
    def edit(ws, drv):
        from runtime import moy_carts
        cart = next(c for c in ws.carts.all if c.get("title") == "Coin Quest")
        ws.open_in_editor(cart)
        _frames(drv)
        ws.text.set_menu_view(tab)
        _frames(drv)
        key, make = TAB_EDITS[tab]
        before = moy_carts.load(cart["path"]).get(key)
        make(ws)
        return lambda: moy_carts.load(cart["path"]).get(key) != before
    return edit


def _code_edit(ws):
    ws.editor.goto_row(0, 0)
    ws.editor.insert("#")


def _cfg_edit(ws):
    ws.run.project.config["fps"] = 12


TAB_EDITS = {
    "cards": ("cfg", _cfg_edit),
    "blocks": ("blocks", lambda ws: ws.block_ui.blocks_ed.new_var()),
    "code": ("src", _code_edit),
    "paint": ("sprites", lambda ws: ws.sheet.pset(0, 0, 7)),
    "map": ("map", lambda ws: ws.run.project.tilemap.mset(0, 0, 1)),
    "scene": ("scenes", lambda ws: ws.scene_ui.sceneedit.place(16, 16)),
    "music": ("sounds", lambda ws: ws.music_ui.musicedit.toggle_rest()),
}


def _cart_note(ws, drv):
    from runtime import moy_carts
    assert select_on_home(ws, "Notes") is not None
    ws.open()
    _frames(drv)
    ed = ws.run.player.ns["open_editor"]("kept")
    ed.focus()
    _frames(drv)
    _type(drv, "kq")
    assert ed.text() == "kq"
    return lambda: moy_carts.load_file("docs", "kept", ws.carts_root) == "kq"


# surface: (its kind, the recipe)
PENDING = {"settings": ("settings", _settings_name),
           "artwork": ("artwork", _paint_stroke),
           "storybook": ("storybook", _story_edit),
           "appearance": ("appearance", _look_change),
           "cart_app": ("desktop", _cart_note)}
PENDING.update(("tab:" + t, ("menu", _tab_edit(t))) for t in COMMIT_TABS)

# The declared apps that hold no edit an exit could lose, and why.
NO_PENDING = {
    "myart": "a rename's draft is a dialog's: every exit gives it back, as Back does",
    "files": "a rename's draft is a dialog's: every exit gives it back, as Back does",
    "calc": "its entry and tape reset on a fresh open by design",
    "getcarts": "an install is no edit: its close lands one in flight",
    "jukebox": "a station is written as it is added; Add's draft is a dialog's",
    "terminal": "a shell session, not an edit: a command runs as it is entered",
}


def test_the_pending_table_covers_every_declared_app():
    held = {k for k in PENDING if not k.startswith(("tab:", "cart_app", "settings"))}
    assert held | set(NO_PENDING) == {d["id"] for d in app_decls.APPS}
    assert not held & set(NO_PENDING)


def _exit_home(ws, drv, kind):
    ws.home.go_home()


def _exit_jump(ws, drv, kind):
    ws.spine.open_app(ws.app("calc"))


def _exit_window_x(ws, drv, kind):
    ws.wm.close_window_kind(kind)


def _exit_update(ws, drv, kind):
    from runtime.ticks import _ticks_ms

    class _Updater:
        reset_called = False

        def reset(self):
            self.reset_called = True

    ws.updater = _Updater()
    ui = ws.update_ui
    ui._upd_phase = "done"
    ui._upd_at = _ticks_ms() - 5000          # past the "UPDATED!" pause
    ui._pump_update(DT)
    assert ws.updater.reset_called


EXITS = {"home": _exit_home, "jump": _exit_jump, "window_x": _exit_window_x,
         "update": _exit_update}


@pytest.mark.parametrize("how", sorted(EXITS))
@pytest.mark.parametrize("what", sorted(PENDING))
def test_a_pending_edit_survives_every_exit(tmp_path, what, how):
    kind, recipe = PENDING[what]
    if how == "window_x":
        ws = build_desktop_ws(tmp_path)
        ws.home.open_desk()
    else:
        ws = build_ws(tmp_path)
    drv = make_drv(ws)
    check = recipe(ws, drv)
    assert not check(), "the edit is pending before the exit"
    EXITS[how](ws, drv, kind)
    assert check(), (what, how)


# -- Ctrl+Z on every Editor tab is the bar's UNDO -------------------------------

@pytest.mark.parametrize("tab", COMMIT_TABS)
@pytest.mark.parametrize("key", (0x1A, 0x19))
def test_ctrl_z_and_y_on_every_editor_tab_are_the_bars(tmp_path, tab, key, monkeypatch):
    """Past a tab's own steps Ctrl+Z walks the journal, as the bar's UNDO
    does, because it IS the bar's UNDO (`history_router`): one route, every
    tab, the Code tab's typing included."""
    ws = build_ws(tmp_path)
    drv = make_drv(ws)
    open_cart(ws, "Star Catcher")
    ws.open_in_editor(ws.cart)
    _frames(drv)
    ws.text.set_menu_view(tab)
    _frames(drv)
    calls = []
    monkeypatch.setattr(ws.history, "undo", lambda: calls.append("undo"))
    monkeypatch.setattr(ws.history, "redo", lambda: calls.append("redo"))
    drv.type_char(key)
    _frames(drv)
    assert calls == ["undo" if key == 0x1A else "redo"], (tab, calls)


# -- the scan --------------------------------------------------------------------

OWNER = {"runtime/shell/input_policy.py"}
INPUT_LAYER = {"runtime/host/moy_input.py", "runtime/shell/web_input.py"}
# The board's own keyboard check, which drives the C3 by hand.
SMOKE = {"firmware/lilygo_t_deck_plus_mainline/modules/tdeck_smoke.py"}

PATTERNS = {
    "the mode setter": (re.compile(r"(?<!\w)_set_text_mode\b|nav\.text_mode\("), set()),
    "a write of text_mode": (re.compile(r"\.text_mode\s*=[^=]"), OWNER | INPUT_LAYER),
    "set_game_mode(": (re.compile(r"\bset_game_mode\("), OWNER | INPUT_LAYER | SMOKE),
    "KeyEdge(": (re.compile(r"\bKeyEdge\("), set()),
    # One typed-key press, the input table's `typed`: a surface spends it
    # (`spend_key`), it keeps no edge or seed of its own.
    "a typed-key seed": (re.compile(r"\bseed_(cart_)?key\(|_wifi_seed\b|\.key = seed\b"), set()),
}


def _sources():
    for top in ("runtime", "device", "firmware"):
        for p in (ROOT / top).rglob("*.py"):
            rel = p.relative_to(ROOT).as_posix()
            if "/.build/" in rel or "/dist/" in rel or rel.startswith("firmware/") \
                    and "/modules/" in rel and rel not in SMOKE:
                continue
            yield rel, p.read_text(encoding="utf-8", errors="replace")


@pytest.mark.parametrize("what", sorted(PATTERNS))
def test_the_policies_are_written_in_one_place(what):
    pat, allowed = PATTERNS[what]
    hits = []
    for rel, text in _sources():
        if rel in allowed:
            continue
        for n, line in enumerate(text.splitlines(), 1):
            code = line.split("#", 1)[0]
            if pat.search(code):
                hits.append("%s:%d: %s" % (rel, n, line.strip()))
    assert not hits, "%s outside the input owner:\n%s" % (what, "\n".join(hits))


def test_esc_on_the_code_tab_closes_its_find_strip_then_leaves(tmp_path):
    """The Code tab types always, so a keyboard's Esc arrives there as the
    byte 27: the first closes the find strip, the next leaves the Editor
    (the browser's handheld start option leaves to the launcher by it)."""
    ws = build_ws(tmp_path)
    drv = make_drv(ws)
    _frames(drv)
    ws.open_in_editor(select_on_home(ws, "Star Catcher"))
    _frames(drv)
    ws.editor_app.focus_tab("code")
    _frames(drv)
    ws.code_layer._find_open = True
    _esc(ws, drv)
    assert not ws.code_layer._find_open and ws.wm.top_kind() == "menu"
    _esc(ws, drv)
    assert ws.wm.top_kind() == "launcher"
