"""The Editor's theme mode (docs/look_2026-10.md step 33, GAPS 56;
runtime/theme_editor.py).

* The door: Appearance's last row, Edit theme…, makes `custom` from a seed
  and opens it in the Editor on Config; Done (the bar's ‹, Esc, B) comes back.
* Config is custom's theme.json, its saves the look's (no journal).
* Sprites: a smooth slot painted turns to a pixel tile in one undo step, the
  state line says so, undo brings the smooth one back; a save writes the
  package's sheet and names the slot in `icons.pixel_slots`, which the C
  loader reads and the flattening keeps; the live look draws it.
* EDIT ICONS and the user's icon-sheet override are gone.
* The desk shows Config beside Sprites as panes, the tab following focus.
"""

import json
from pathlib import Path

import runtime  # noqa: F401  (registers the host's moy_ui)
import moy_ui
from runtime import iconsets, theme_editor, ui_draw
from ws_helpers import build_desktop_ws, build_ws

HOME = iconsets.SLOT["home"]


def _open(ws):
    assert ws.look.edit_file()
    ws.input.begin_frame()
    ws.frame(1 / 30)
    doc = ws.editor_app.theme
    assert doc is not None and ws.wm.top_is("menu")
    return doc


def _frames(ws, n=2):
    for _ in range(n):
        ws.input.begin_frame()
        ws.frame(1 / 30)


def _paint(ws, doc, x=6, y=3):
    layer = ws.theme_sprites_layer
    layer._press(doc, x, y)
    layer._release(doc)


def _package(ws):
    return Path(ws.carts_root) / "custom.moy"


def test_the_door_opens_a_copy_on_config_and_done_comes_back(tmp_path):
    ws = build_ws(tmp_path)
    ws.open_app(ws._apps_by_id["appearance"])
    _frames(ws)
    doc = _open(ws)
    assert ws.look.theme_name == "custom" and ws.menu_view == "theme_config"
    assert doc.note == "Moy is built in: this is your copy, custom"
    assert doc.status() == doc.note
    assert json.loads(doc.ed.text()) == json.loads(ws.look.file_text())
    assert ws.editor_app.project is None and ws.editor is doc.ed
    ws.editor_app.close_theme()
    assert ws.editor_app.theme is None and ws.editor is None
    assert ws.wm.top_kind() == "appearance"


def test_esc_and_the_bars_back_are_done(tmp_path):
    from runtime import host_app
    ws = build_ws(tmp_path)
    drv = host_app.ConsoleDriver(ws)
    ws.open_app(ws._apps_by_id["appearance"])
    drv.frame(1 / 30)
    _open(ws)
    drv.key_down(27)
    drv.frame(1 / 30)
    drv.key_up(27)
    drv.frame(1 / 30)
    assert ws.editor_app.theme is None and ws.wm.top_kind() == "appearance"
    _open(ws)
    ws.editor_app.theme_tab("theme_sprites")
    drv.frame(1 / 30)
    bl = ws.bar_layer
    bar, zone = bl.cell("menu", "zone")
    zx, zy = bar[1][0] + zone[0], bar[1][1] + zone[1]
    drv.click(zx + 6, zy + zone[3] // 2)
    drv.frame(1 / 30)
    drv.frame(1 / 30)
    assert ws.editor_app.theme is None


def test_a_painted_smooth_slot_turns_to_pixels_and_undo_brings_it_back(tmp_path):
    ws = build_ws(tmp_path)
    doc = _open(ws)
    ws.editor_app.theme_tab("theme_sprites")
    assert doc.slot == HOME and not doc.is_pixels(HOME)
    assert doc.state() == "Home · smooth"
    assert not ws.history.can_undo()
    _paint(ws, doc)
    assert doc.is_pixels(HOME)
    assert doc.state() == "Home · pixels · Undo brings it back"
    assert ws.look.pixel_image("home") is not None, "the live look draws it"
    assert ws.history.can_undo()
    assert ws.history.undo()
    assert not doc.is_pixels(HOME) and doc.state() == "Home · smooth"
    assert ws.look.pixel_image("home") is None
    assert ws.history.redo() and doc.is_pixels(HOME)


def test_a_save_writes_the_tiles_and_names_the_slot(tmp_path):
    ws = build_ws(tmp_path)
    doc = _open(ws)
    ws.editor_app.theme_tab("theme_sprites")
    _paint(ws, doc)
    ws.editor_app.close_theme()                 # Done hard-commits
    _frames(ws)
    d = json.loads((_package(ws) / "theme.json").read_text())
    assert d["icons"] == "icons.moygfx"
    assert d["look"]["icons"]["pixel_slots"] == ["home"]
    assert not (_package(ws) / "journal").exists()
    sheet = iconsets.pixel_sheet_of((_package(ws) / "icons.moygfx").read_bytes())
    assert sheet is not None and sheet.clear == 16
    ox, oy = sheet.tile_origin(HOME)
    assert sheet.pget(ox + 6, oy + 3) == theme_editor.RED
    # installed as written: the look draws the slot from the tiles
    assert ws.look.pixel_slots is not None and ws.look.pixel_slots[HOME]
    assert ws.look.pixel_image("home") is not None
    # a change in Appearance re-derives from the file as saved and keeps it
    assert ws.look.change("tint", "plum") == 0
    ws.look.tick(0.0)
    assert ws.look.commit()
    d = json.loads((_package(ws) / "theme.json").read_text())
    assert d["look"]["icons"]["pixel_slots"] == ["home"] and d["icons"] == "icons.moygfx"


def test_a_save_from_sprites_reloads_a_clean_config(tmp_path):
    ws = build_ws(tmp_path)
    doc = _open(ws)
    ws.editor_app.theme_tab("theme_sprites")
    _paint(ws, doc)
    assert doc.save_sprites()
    _frames(ws)
    doc.follow()
    assert json.loads(doc.ed.text())["look"]["icons"]["pixel_slots"] == ["home"]


def test_config_saves_through_the_look_and_refuses_softly(tmp_path):
    ws = build_ws(tmp_path)
    doc = _open(ws)
    d = json.loads(doc.ed.text())
    d["colors"]["dark"]["panel"] = 3
    doc.ed.set_text(json.dumps(d, indent=2))
    doc.ed.dirty = True
    assert doc.save_config(soft=True) == (True, "")
    assert json.loads((_package(ws) / "theme.json").read_text())["colors"]["dark"]["panel"] == 3
    doc.ed.set_text("{ not json")
    doc.ed.dirty = True
    ok, badge = doc.save_config(soft=True)
    assert not ok and badge.startswith("INVALID")
    assert "INVALID" in doc.status()


def test_the_loader_reads_and_writes_pixel_slots():
    src = json.dumps({"format": 2, "name": "px", "look": {"icons": {
        "pixel_slots": ["play", "home", "nope", "edit", "secret"]}}}).encode()
    got = moy_ui.theme_resolve(src)
    assert got[0] == 0 and got[1] & 4, "the name of no slot is dropped and noted"
    rc, out = ui_draw.theme_custom(src, None, None)
    assert rc == 0
    # in slot order, an alias as its slot
    assert json.loads(out)["look"]["icons"]["pixel_slots"] == ["home", "play", "pencil",
                                                               "secret"]
    assert moy_ui.theme_install(src)[0] == 0
    try:
        names = moy_ui.theme_look_fields()[0]
        look = moy_ui.theme_look("px", False)
        flags = iconsets.pixel_slots((dict(zip(names, look[0])),))
        assert [i for i in range(len(flags)) if flags[i]] == [
            iconsets.SLOT[n] for n in ("home", "play", "pencil", "secret")]
    finally:
        moy_ui.theme_uninstall()


def test_the_sheet_cap_holds_a_tile_for_every_slot():
    caps = ui_draw.sheet_caps()
    assert caps[2] >= 16 * iconsets.PIXEL_ROWS
    sheet = iconsets.blank_sheet()
    assert ui_draw.sheet_check(sheet.to_hex().encode(), caps[2])[0] == 0


def test_settings_has_no_edit_icons_and_the_store_no_override(tmp_path):
    from runtime import moy_carts, moy_sync
    ws = build_ws(tmp_path)
    assert "icons" not in [r[0] for r in ws.settings_layer._SETTINGS_ROWS]
    assert not hasattr(moy_carts, "load_system_icons")
    assert "system_icons.moygfx" not in moy_sync.SYSTEM_FILES


def test_the_tdeck_hints_name_its_own_keys(tmp_path):
    ui_draw.input_kind("tdeck")
    try:
        assert theme_editor._hints_for("theme_sprites") == (
            "flip", "Flip", "rotate", "Rotate", "ball", "Colour", "back", "Done")
        # Config's caret takes the ball and its letters type: Mic's menu,
        # opening on Home, is the way out, and the tabs are a touch's
        assert theme_editor._hints_for("theme_config") == ("menu", "Home")
    finally:
        ui_draw.input_kind("keys")


def test_the_desk_shows_config_beside_sprites(tmp_path):
    ws = build_desktop_ws(tmp_path, panel_diagonal_in=7.0, font_scale=1)
    ws.open_desk()
    ws.open_app(ws._apps_by_id["appearance"])
    _frames(ws)
    _open(ws)
    _frames(ws)
    desk = ws.editor_desk
    assert desk.active() and desk.key == "theme"
    tabs = [desk.tab_of(p) for p, _r, _path in desk.panes()]
    assert tabs == ["theme_config", "theme_sprites"]
    desk.focus_pane(1)
    assert ws.menu_view == "theme_sprites"
    assert not desk.switch("code"), "a theme has no cart workspace"
    # the window's X commits what Sprites holds
    _paint(ws, ws.editor_app.theme)
    ws.wm.close_window_kind("menu")
    d = json.loads((_package(ws) / "theme.json").read_text())
    assert d["look"]["icons"]["pixel_slots"] == ["home"]
