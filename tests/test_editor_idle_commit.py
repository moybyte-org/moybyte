"""#154: the Editor's commit rides the idle debounce on EVERY tab, not just code.

A commit is the dearest thing the Editor does (serialize + store write + journal
append). On six of the seven tabs it used to fall on a TAB SWITCH or on PLAY --
inside the interaction, where the kid feels all of it. These pin that it now
lands in the gap the kid already left, that it does not land while they are
still working, and that the hard exit paths (test_autosave_exit) keep theirs.

Driven through the same shared console the device runs."""

from runtime.ticks import _ticks_ms
from ws_helpers import build_ws as _ws


def _editable(ws):
    while not ws.launcher.items[ws.launcher.sel].get("path"):
        ws.launcher.sel += 1
    cart = ws.launcher.items[ws.launcher.sel]
    ws.open_in_editor()
    return cart["path"]


def _go_idle(ws):
    """Age the debounce past its window and run the frame hook that reads it."""
    ws.history.edit_ms = _ticks_ms() - ws.history.edit_debounce_ms - 1
    ws.history.idle_tick()


def _still_working(ws):
    ws.history.edit_ms = _ticks_ms()
    ws.history.idle_tick()


def _paint_a_pixel(ws, color=11):
    ws._open_paint()
    pe = ws.paint
    pe.n = 0
    pe.color = color
    pe.paint(0, 0)


# -- the tab ladder ---------------------------------------------------------

def test_a_paint_edit_commits_on_the_debounce_without_leaving_the_tab(tmp_path):
    from runtime import moy_carts
    from runtime.editors import SpriteSheet
    ws = _ws(tmp_path)
    path = _editable(ws)
    _paint_a_pixel(ws)
    assert ws.sheet.dirty is True

    _go_idle(ws)

    assert ws.sheet.dirty is False               # the tab is clean now
    assert ws.menu_view == "paint"               # ...and nobody left it
    sheet = SpriteSheet.from_hex(moy_carts.load(path)["sprites"])
    assert sheet.tget(0, 0, 0) == 11


def test_the_debounce_does_not_fire_while_the_kid_is_still_painting(tmp_path):
    ws = _ws(tmp_path)
    _editable(ws)
    _paint_a_pixel(ws)

    _still_working(ws)

    assert ws.sheet.dirty is True                # nothing was written mid-stroke
    assert ws.history.edit_ms is not None        # ...and the debounce is still armed


def test_the_whole_ladder_commits_on_the_debounce(tmp_path):
    """Each editing tab in turn: make it dirty, go idle, and it is clean --
    reached through save_current, so there is one commit ladder, not two."""
    ws = _ws(tmp_path)
    _editable(ws)

    ws._open_map()
    ws.tilemap.mset(0, 0, 4)
    assert ws.tilemap.dirty is True
    _go_idle(ws)
    assert ws.tilemap.dirty is False

    ws._open_scene()
    se = ws.scene_ui.sceneedit
    se.place(16, 16)
    assert se.dirty is True
    _go_idle(ws)
    assert se.dirty is False

    ws._open_music()
    me = ws.music_ui.musicedit
    me.nudge_pitch(1)
    assert me.dirty is True
    _go_idle(ws)
    assert me.dirty is False


def test_a_config_edit_commits_on_the_debounce(tmp_path):
    from runtime import moy_carts
    ws = _ws(tmp_path)
    path = _editable(ws)
    ws.set_menu_view("cards")
    ws.project.config["fps"] = 12

    _go_idle(ws)

    assert moy_carts.load(path)["cfg"]["fps"] == 12


def test_a_code_edit_still_waits_for_source_that_parses(tmp_path):
    """Code keeps its own gate: a mid-edit syntax error waits (no nag, no
    half-written program journaled), and the save lands once it parses."""
    from runtime import moy_carts
    ws = _ws(tmp_path)
    path = _editable(ws)
    ws.set_menu_view("code")
    ws.editor.set_text("def _draw(:\n")
    ws.editor.dirty = True

    _go_idle(ws)
    assert "def _draw(:" not in moy_carts.load(path)["src"]

    ws.editor.set_text("def _draw():\n    cls(7)\n")
    ws.editor.dirty = True
    _go_idle(ws)
    assert "cls(7)" in moy_carts.load(path)["src"]


# -- what the debounce must NOT do ------------------------------------------

def test_a_clean_tab_writes_nothing_when_the_debounce_fires(tmp_path, monkeypatch):
    """The clean-tab guard is what keeps a stray debounce free: a tab nobody
    touched must not pay a store write for having been looked at."""
    from runtime import moy_carts
    ws = _ws(tmp_path)
    _editable(ws)
    ws._open_paint()

    writes = []
    monkeypatch.setattr(moy_carts, "save_sprites",
                        lambda *a, **k: writes.append(a))
    _go_idle(ws)

    assert writes == []


def test_the_debounce_does_not_commit_once_the_editor_is_gone(tmp_path, monkeypatch):
    """A debounce armed in the Editor and left armed by a fast exit must not fire
    into a torn-down workspace -- the exit path already hard-committed."""
    ws = _ws(tmp_path)
    _editable(ws)
    _paint_a_pixel(ws)
    ws.history.edit_ms = _ticks_ms() - ws.history.edit_debounce_ms - 1

    ws.exit()                                    # context-X: hard-commits, goes home
    assert ws.screen == "launcher"

    commits = []
    monkeypatch.setattr(ws.editor_app, "save_current",
                        lambda: commits.append(1))
    ws.history.idle_tick()                       # the stale timer fires here

    assert commits == []


# -- arming it --------------------------------------------------------------

def test_a_touch_in_an_editor_tab_arms_the_debounce(tmp_path):
    """paint/map/scene/music are drawn at, not typed at. Arming only on keys is
    what left them committing on the tab switch instead."""
    ws = _ws(tmp_path)
    _editable(ws)
    ws._open_paint()
    ws.history.edit_ms = None

    ws.pointer.x, ws.pointer.y = 40, 120
    ws.pointer.down = True
    ws.pointer.click = True
    ws.handle_pointer()

    assert ws.history.edit_ms is not None


def test_the_launcher_does_not_arm_the_debounce(tmp_path):
    ws = _ws(tmp_path)
    ws.history.edit_ms = None

    ws.pointer.x, ws.pointer.y = 40, 120
    ws.pointer.down = True
    ws.pointer.click = True
    ws.handle_pointer()

    assert ws.history.edit_ms is None


# -- the exit path the ladder did not have ----------------------------------

def test_the_ota_reboot_commits_an_open_editor_first(tmp_path):
    """Every other exit path hard-commits (test_autosave_exit). A reboot into a
    new image did not, and on the windowed tier an Editor window can be open
    beside the update screen with an edit still inside its debounce window."""
    from runtime import moy_carts
    from runtime.editors import SpriteSheet
    ws = _ws(tmp_path)
    path = _editable(ws)
    _paint_a_pixel(ws, color=13)

    class _Updater:
        def __init__(self):
            self.reset_called = False

        def reset(self):
            self.reset_called = True

    ws.updater = _Updater()
    layer = ws.update_ui
    layer._upd_phase = "done"
    layer._upd_at = _ticks_ms() - 5000        # past the "UPDATED!" pause
    layer._pump_update(1 / 30)

    assert ws.updater.reset_called is True
    sheet = SpriteSheet.from_hex(moy_carts.load(path)["sprites"])
    assert sheet.tget(0, 0, 0) == 13
