"""The scene placement editor (#85 Stage 2): the WYSIWYG placed-actor tab.

Covers the SceneEditor core (editors_scene: place/select/move/z-order/props +
full-snapshot undo), the SceneLayout frozen-baseline contract (#39), the UI
gesture grammar driven headlessly through the real Workstation (tap = place/
select, drag = move an actor or pan the world, the props row), the LIVE-sync
semantics (a committed gesture reaches the running cart's scene() on the next
PLAY without an explicit SAVE -- the map tab's live-TileMap model), SAVE ->
Project.commit_scene persistence + the durable-journal walk reaching the live
workspace, the Editor tab-ladder wiring, and the firmware staging pins."""

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

from runtime.editors_scene import SceneEditor, parse_rows  # noqa: E402
from runtime import scene_editor_ui as SU                  # noqa: E402


MAIN_SCENE = json.dumps([
    {"tag": "goal", "tile": 2, "x": 200, "y": 32},
    {"tag": "coin", "tile": 3, "x": 40, "y": 56},
])


# -- SceneEditor core ---------------------------------------------------------

def test_parse_serialize_roundtrip_preserves_row_shape():
    se = SceneEditor(MAIN_SCENE)
    assert [r["tag"] for r in se.rows] == ["goal", "coin"]
    assert json.loads(se.serialize()) == json.loads(MAIN_SCENE)
    # flip/flags only serialize when set (the compact on-disk shape).
    assert "flip" not in se.rows[0]


def test_parse_normalizes_and_never_raises():
    assert parse_rows("not json {") == []
    assert parse_rows(None) == []
    rows = parse_rows(json.dumps([{"tag": 5, "x": "7", "y": 2.0}, "junk"]))
    assert rows == [{"tag": "5", "tile": 0, "x": 7, "y": 2}]


def test_place_snaps_to_grid_and_selects():
    se = SceneEditor("")
    se.n = 9
    se.place(43, 61)
    assert se.rows[-1] == {"tag": "actor", "tile": 9, "x": 40, "y": 56}
    assert se.sel == len(se.rows) - 1 and se.dirty
    se.snap = False
    se.place(43, 61)
    assert (se.rows[-1]["x"], se.rows[-1]["y"]) == (43, 61)   # free-pixel


def test_stamp_tag_follows_the_sprite_you_place():
    # "The sprite is the object type" (#85/#93): placing more of a sprite keeps its
    # tag instead of resetting to the generic default every time.
    se = SceneEditor(json.dumps([
        {"tag": "player", "tile": 1, "x": 10, "y": 10},
        {"tag": "coin", "tile": 2, "x": 40, "y": 40}]))
    # pick the coin sprite from the palette -> new placements are coins
    se.set_brush(2)
    assert se.stamp_tag == "coin"
    se.place(80, 80)
    assert se.rows[-1]["tag"] == "coin" and se.rows[-1]["tile"] == 2
    # a brand-new sprite starts generic, but naming it once is remembered
    se.set_brush(7)
    assert se.stamp_tag == "actor"
    se.place(100, 100)
    se.set_tag("enemy")
    assert se.stamp_tag == "enemy"
    se.place(120, 120)
    assert se.rows[-1]["tag"] == "enemy"          # remembered for the next stamp
    # selecting an actor loads it as the stamp (sprite + tag)
    se.select_at(10, 10)
    assert se.n == 1 and se.stamp_tag == "player"


def test_select_at_picks_topmost_and_move_snaps():
    se = SceneEditor(json.dumps([
        {"tag": "a", "tile": 0, "x": 40, "y": 40},
        {"tag": "b", "tile": 0, "x": 40, "y": 40},   # later row = drawn on top
    ]))
    assert se.select_at(44, 44) == 1                  # topmost wins
    se.begin_edit()
    se.move_sel(51, 53)
    assert (se.selected()["x"], se.selected()["y"]) == (48, 48)   # snapped
    assert se.end_edit()                              # a real move -> one step
    assert se.select_at(0, 0) is None                 # empty world -> deselect


def test_zero_move_drag_records_no_undo_step():
    se = SceneEditor(MAIN_SCENE)
    se.select_at(44, 60)
    se.begin_edit()
    se.move_sel(40, 56)                               # back where it started
    assert not se.end_edit()
    assert not se.can_undo()


def test_delete_front_back_and_undo_redo():
    se = SceneEditor(MAIN_SCENE)
    se.select_at(44, 60)                              # the coin (index 1)
    assert se.back_sel() and se.rows[0]["tag"] == "coin"
    assert se.front_sel() and se.rows[-1]["tag"] == "coin"
    assert se.delete_sel()
    assert [r["tag"] for r in se.rows] == ["goal"] and se.sel is None
    assert se.undo() and [r["tag"] for r in se.rows] == ["goal", "coin"]
    assert se.undo() and se.rows[0]["tag"] == "coin"  # front_sel reverted
    assert se.redo() and se.rows[-1]["tag"] == "coin"


def test_tag_flip_and_caps():
    se = SceneEditor(MAIN_SCENE)
    se.select_at(44, 60)
    assert se.set_tag("x" * 40)
    assert se.selected()["tag"] == "x" * SceneEditor.TAG_MAX
    assert not se.set_tag(se.selected()["tag"])       # same tag -> no step
    assert se.toggle_flip() and se.selected()["flip"] == 1
    assert se.toggle_flip() and "flip" not in se.selected()
    assert json.loads(se.serialize())[1].get("flip") is None


def test_abort_edit_restores_pre_gesture_rows():
    se = SceneEditor(MAIN_SCENE)
    se.select_at(44, 60)
    se.begin_edit()
    se.move_sel(120, 120)
    se.abort_edit()
    assert (se.rows[1]["x"], se.rows[1]["y"]) == (40, 56)
    assert not se.can_undo()


def test_world_size_covers_viewport_map_and_actors():
    se = SceneEditor(json.dumps([{"tag": "far", "tile": 0, "x": 900, "y": 10}]))
    w, h = se.world_size(None)
    assert w >= 900 + 8 + 320 and h >= 240           # actor extent + margin
    empty = SceneEditor("")
    assert empty.world_size(None) == (640, 480)      # viewport + one-viewport margin


# -- SceneLayout: the #39 frozen-baseline contract ----------------------------

def test_scene_layout_base_reproduces_frozen_constants():
    lay = SU.SceneLayout(320, 240, 1)
    assert lay._base
    assert (lay.sv_x0, lay.sv_y0) == (SU._SV_X0, SU._SV_Y0)
    assert (lay.sv_avail_w, lay.sv_avail_h) == (SU._SV_AVAIL_W, SU._SV_AVAIL_H)
    assert lay.tp_area == SU._TP_AREA
    assert lay.tp_prev == SU._TP_PREV and lay.tp_next == SU._TP_NEXT
    assert lay.zoom_btn == SU._SC_ZOOM and lay.flip_btn == SU._SC_FLIP
    assert (lay.pan_up, lay.pan_lf, lay.pan_rt, lay.pan_dn) == \
        (SU._PAN_UP, SU._PAN_LF, SU._PAN_RT, SU._PAN_DN)
    assert (lay.tag_btn, lay.del_btn, lay.snap_btn) == \
        (SU._SC_TAG, SU._SC_DEL, SU._SC_SNAP)
    assert (lay.undo_btn, lay.redo_btn, lay.front_btn, lay.back_btn) == \
        (SU._SC_UNDO, SU._SC_REDO, SU._SC_FRONT, SU._SC_BACK)
    assert lay.pan_thresh == SU._SC_PAN_THRESH
    assert lay.zooms == SU._SV_ZOOMS


def test_scene_layout_reflows_bigger_panel():
    lay = SU.SceneLayout(1024, 600, 1)
    assert not lay._base
    # The world view grows to fill the panel: a desk-size panel shows the whole
    # 320x240 game viewport at 1x with no panning.
    assert lay.sv_avail_w >= 320 and lay.sv_avail_h >= 240
    # The view never overlaps the palette column, the palette gains rows.
    assert lay.sv_x0 + lay.sv_avail_w <= lay.tp_x0 - 4
    assert lay.tp_rows > SU._TP_ROWS
    # The props row sits below the view.
    assert lay.tag_btn[1] >= lay.sv_y0 + lay.sv_avail_h


# -- the tab through the real Workstation (headless, host == device code) -----

def _open_ws(tmp_path, scenes=None, src="def _draw():\n    cls(0)\n"):
    from runtime import host_app
    carts_dir = str(tmp_path / "carts")
    host_app.moy_carts.ensure_dirs(carts_dir)
    host_app.moy_carts.create("Scene Cart", carts_dir, src=src, type="game",
                              scenes=scenes, scene_order=list(scenes) if scenes else None)
    ws = host_app.build_workstation(carts_dir)
    for i, c in enumerate(ws.launcher.items):
        if c["title"] == "Scene Cart":
            ws.launcher.sel = i
            break
    ws.open()
    return ws


def _tap(ws, px, py):
    """One full press+release tap through the scene layer's pointer facet."""
    layer = ws._content_layers["scene"]
    ws.pointer.down = True
    layer.handle_pointer(px, py, True)
    ws.pointer.down = False
    layer.handle_pointer(px, py, False)


def test_tab_ladder_and_router_carry_the_scene_tab(tmp_path):
    from runtime import editor_app as EA
    assert "scene" in [t for t, _g in EA._ZONE_TABS]
    assert "scene" in [t for t, _l, _i in EA._TAB_CHIPS]
    ws = _open_ws(tmp_path)
    ws._open_scene()
    assert ws.menu_view == "scene" and ws.editor_app.tab == "scene"
    assert ws._content_layer() is ws._content_layers["scene"]
    ws.frame(0.016)                                  # the tab draws headlessly
    # The bar SAVE routes to the scene persist verb on this tab.
    ws.scene_ui.sceneedit.place(10, 10)
    ws.editor_app.save_current()
    assert (Path(ws.cart["path"]) / "scenes" / "main.moyscene").exists()


def test_tap_places_selects_and_drag_moves(tmp_path):
    ws = _open_ws(tmp_path, scenes={"main": MAIN_SCENE})
    ws._open_scene()
    se = ws.scene_ui.sceneedit
    x0, y0, scale, vw, vh = ws.scene_ui._sv_metrics()
    # Tap empty world at view (100, 100) -> place a snapped actor there.
    ws.scene_ui.sceneedit.n = 7
    _tap(ws, x0 + 100, y0 + 100)
    placed = se.rows[-1]
    assert placed["tile"] == 7 and se.sel == len(se.rows) - 1
    assert placed["x"] % 8 == 0 and placed["y"] % 8 == 0
    # Tap the coin at world (40, 56) -> selects it (no new actor).
    count = len(se.rows)
    _tap(ws, x0 + 44, y0 + 60)
    assert len(se.rows) == count and se.selected()["tag"] == "coin"
    # Press it and drag past the threshold -> the actor follows, one undo step.
    layer = ws._content_layers["scene"]
    ws.pointer.down = True
    layer.handle_pointer(x0 + 44, y0 + 60, True)
    layer.handle_pointer(x0 + 60, y0 + 80, False)     # held drag
    ws.pointer.down = False
    layer.handle_pointer(x0 + 60, y0 + 80, False)     # release
    assert (se.selected()["x"], se.selected()["y"]) == (56, 72)
    assert se.can_undo()


def test_drag_on_empty_world_pans_and_never_places(tmp_path):
    ws = _open_ws(tmp_path, scenes={"main": MAIN_SCENE})
    ws._open_scene()
    se = ws.scene_ui.sceneedit
    count = len(se.rows)
    layer = ws._content_layers["scene"]
    x0, y0, scale, vw, vh = ws.scene_ui._sv_metrics()
    ws.pointer.down = True
    layer.handle_pointer(x0 + 150, y0 + 100, True)
    layer.handle_pointer(x0 + 100, y0 + 60, False)    # drag left/up -> pan right/down
    ws.pointer.down = False
    layer.handle_pointer(x0 + 100, y0 + 60, False)
    assert len(se.rows) == count                      # a pan is not a place
    assert (se.cam_x, se.cam_y) == (50, 40)           # content followed the finger
    assert not se.can_undo()


def test_committed_gestures_reach_the_next_play_and_persist(tmp_path):
    # The live-sync contract: place an actor in the editor, hit PLAY -- the
    # cart's _init counts it via scene() immediately (the map tab's live-TileMap
    # semantics), with NO explicit SAVE tap (#111: there is no SAVE button
    # anymore). PLAY is ALSO a hard-commit trigger now (EditorApp.leave calls
    # save_current() before running), so the placement lands on disk too --
    # unlike the old explicit-SAVE model, a kid never has to remember to persist.
    ws = _open_ws(tmp_path, scenes={"main": MAIN_SCENE}, src=(
        "def _init():\n"
        "    global n\n"
        "    n = len(scene())\n"
        "def _draw():\n    cls(0)\n"))
    assert ws.ns["n"] == 2
    ws._open_scene()
    x0, y0, scale, vw, vh = ws.scene_ui._sv_metrics()
    _tap(ws, x0 + 120, y0 + 40)                       # place actor #3
    ws._leave_menu()                                  # PLAY re-runs _init AND commits
    assert ws.ns["n"] == 3
    # The placement is durably persisted too -- PLAY hard-commits (#111).
    live = Path(ws.cart["path"]) / "scenes" / "main.moyscene"
    assert len(json.loads(live.read_text())) == 3


def test_save_persists_and_journal_undo_reaches_live_workspace(tmp_path):
    ws = _open_ws(tmp_path, scenes={"main": MAIN_SCENE})
    ws._open_scene()
    se = ws.scene_ui.sceneedit
    se.place(80, 80)
    ws.save_scene()
    assert ws.save_status is None and not se.dirty   # invisible save: no failure text
    live = Path(ws.cart["path"]) / "scenes" / "main.moyscene"
    assert len(json.loads(live.read_text())) == 3
    ws.scene_ui.sceneedit.place(120, 80)
    ws.save_scene()                                   # a second durable step
    assert len(json.loads(live.read_text())) == 4
    # The durable journal walk restores the file AND the open workspace's live
    # Scenes (the _reload_after_walk rebuild), so the next run spawns 3 again.
    assert ws._journal_walk(redo=False)
    assert len(json.loads(live.read_text())) == 3
    assert len(ws.scenes.scene("main")) == 3
    # The rebuilt scene TAB reopens on the restored rows.
    assert len(ws.scene_ui.sceneedit.rows) == 3


def test_props_row_tag_typing_and_flip(tmp_path):
    ws = _open_ws(tmp_path, scenes={"main": MAIN_SCENE})
    ws._open_scene()
    ui = ws.scene_ui
    se = ui.sceneedit
    lay = ui.layout
    x0, y0, scale, vw, vh = ui._sv_metrics()
    _tap(ws, x0 + 44, y0 + 60)                        # select the coin
    # Tap the TAG field -> capture, pre-filled with the current tag; erase it
    # and type "gem" + ENTER -> committed, one undo step. Keys are edge-fired
    # (the wifi-password pattern), so distinct bytes need a 0 between repeats.
    _tap(ws, lay.tag_btn[0] + 2, lay.tag_btn[1] + 2)
    assert ui.tag_edit and ws.input.text_mode
    assert ui.tag_buf == "coin"
    for k in (8, 0, 8, 0, 8, 0, 8):                   # four BACKSPACEs
        ws.input.last_key = k
        ui._scene_input()
    for ch in "gem":
        ws.input.last_key = ord(ch)
        ui._scene_input()
    ws.input.last_key = 13
    ui._scene_input()
    ws.input.last_key = 0
    assert not ui.tag_edit and not ws.input.text_mode
    assert se.selected()["tag"] == "gem"
    assert se.undo() and se.selected()["tag"] == "coin"
    # FLIP via its button; DEL removes the actor.
    _tap(ws, lay.flip_btn[0] + 2, lay.flip_btn[1] + 2)
    assert se.selected().get("flip") == 1
    count = len(se.rows)
    _tap(ws, lay.del_btn[0] + 2, lay.del_btn[1] + 2)
    assert len(se.rows) == count - 1 and se.sel is None


def test_sceneless_cart_editor_creates_main_on_save(tmp_path):
    ws = _open_ws(tmp_path)                           # no scenes at all
    ws._open_scene()
    ui = ws.scene_ui
    assert ui.scene_name == "main" and ui.sceneedit.rows == []
    ui.sceneedit.place(24, 24)
    ui._sync_live()
    ws.save_scene()
    man = json.loads((Path(ws.cart["path"]) / "manifest.json").read_text())
    assert man["assets"]["scenes"] == ["main"]
    assert len(ws.scenes.scene("main")) == 1          # live object gained it too


def test_zoom_cycles_and_camera_clamps(tmp_path):
    ws = _open_ws(tmp_path, scenes={"main": MAIN_SCENE})
    ws._open_scene()
    ui = ws.scene_ui
    assert ui._sv_metrics()[2] == 1
    ui._cycle_zoom()
    assert ui._sv_metrics()[2] == 2
    ui._pan(10000, 10000)                             # clamped to the world extents
    se = ui.sceneedit
    ww, wh = se.world_size(ws.tilemap)
    x0, y0, scale, vw, vh = ui._sv_metrics()
    assert se.cam_x == ww - vw and se.cam_y == wh - vh
    ui._cycle_zoom()
    ui._cycle_zoom()                                  # wraps back to 1x, re-clamps
    assert ui._sv_metrics()[2] == 1


def test_editor_resets_between_carts(tmp_path):
    ws = _open_ws(tmp_path, scenes={"main": MAIN_SCENE})
    ws._open_scene()
    assert ws.scene_ui.sceneedit is not None
    ws.go_home()
    assert ws.scene_ui.sceneedit is None              # no stale editor leaks


def test_icon_and_glyph_vocabulary_carry_scene():
    from runtime.chrome import _GLYPHS, _ICON, _ICON_ART, _ICON_VERSION
    assert "scene" in _GLYPHS                          # the 12x12 fallback
    assert "scene" in _ICON and "scene" in _ICON_ART   # the themeable 16x16 slot
    assert _ICON_VERSION >= 4                          # stale saved themes re-seed


def test_build_scripts_stage_the_new_modules():
    # Host == device: both boards freeze the new shared modules (the #85 Stage 2
    # staging pins, mirroring the music-editor build asserts).
    #
    # Asked of the STAGED SET rather than of a `cp` line in build.sh: since #161
    # Phase 3 the boards stage every runtime/*.py minus their board.toml
    # denylist, so there is no per-module line to grep for -- and the question
    # was always "is this module frozen onto that board", never "does the shell
    # script contain this string".
    from tools.board_config import staged_modules

    for board in ("lilygo_t_deck_plus_mainline",
                  "esp32_p4_wifi6_touch_lcd_7b"):
        staged = staged_modules(ROOT / "firmware" / board, ROOT)
        assert "scene_editor_ui.py" in staged, board
        assert "editors_scene.py" in staged, board
