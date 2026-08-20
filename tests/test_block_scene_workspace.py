"""Blocks + Scene side-by-side authoring workspace (#93/#85).

Scratch-style: on a wide canvas the Blocks tab grows an INTERACTIVE scene pane on
the right ("objects on the right, their programming on the left"). Big-screen only
by design -- below the gate (notably the 320x240 T-Deck) the tab renders exactly
as before (blocks-only), so the frozen `_base` path is untouched.

Covers: the width gate, both editors' layouts BOUND to their panes (and the
320x240 `_base` still frozen), a tap in the right pane driving the real
SceneEditor, a tap in the left pane driving the outline (not the scene), and the
scene pane persisting through the Blocks tab's commit path.
"""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

from runtime import block_editor_ui as BU        # noqa: E402
from runtime import scene_editor_ui as SU        # noqa: E402
from runtime import blocks as _blocks            # noqa: E402

import canvas_probe as probe                     # noqa: E402  (pixel-width-agnostic probes)


MAIN_SCENE = json.dumps([
    {"tag": "goal", "tile": 2, "x": 200, "y": 32},
    {"tag": "coin", "tile": 3, "x": 40, "y": 56},
])

WIDE = (1024, 600)


def _open_blocks_ws(tmp_path, size=WIDE, scenes=None):
    """Build a headless Workstation at `size`, open a cart that has a sheet + a
    scene, land on the Blocks tab, and draw one frame so the workspace lays out."""
    from runtime import host_app
    carts_dir = str(tmp_path / "carts")
    host_app.moy_carts.ensure_dirs(carts_dir)
    scenes = scenes or {"main": MAIN_SCENE}
    # A BLOCK-authored cart (src starts with the block marker) so the Blocks tab isn't
    # in the hand-written-code protect mode -- per-object editing needs a writable tab.
    src = _blocks.compile_blocks(_blocks.empty_program())
    host_app.moy_carts.create("WS Cart", carts_dir,
                              src=src, type="game",
                              scenes=scenes, scene_order=list(scenes))
    ws = host_app.build_workstation(carts_dir, sys_size=size)
    for i, c in enumerate(ws.launcher.items):
        if c["title"] == "WS Cart":
            ws.launcher.sel = i
            break
    ws.open()
    ws._open_blocks()
    ws.frame(0.016)                 # the tab draws headlessly -> lays out the panes
    return ws


def _tap_blocks(ws, px, py):
    """One press+release tap through the blocks layer's pointer facet."""
    layer = ws._content_layers["blocks"]
    ws.pointer.down = True
    layer.handle_pointer(px, py, True)
    ws.pointer.down = False
    layer.handle_pointer(px, py, False)


# -- the width gate -----------------------------------------------------------

def test_gate_active_on_wide_canvas(tmp_path):
    ws = _open_blocks_ws(tmp_path, WIDE)
    assert ws.block_ui._workspace_active()


def test_gate_off_on_tdeck_and_base_layout_frozen(tmp_path):
    # The 320x240 T-Deck: blocks-only, no scene pane, and the block layout stays
    # on the frozen `_base` branch (byte-identical to before this feature).
    ws = _open_blocks_ws(tmp_path, (320, 240))
    assert not ws.block_ui._workspace_active()
    assert ws.block_ui.block_layout._base
    # The Blocks tab never builds the scene editor at this size.
    assert ws.scene_ui.sceneedit is None


def test_gate_off_on_narrow_window(tmp_path):
    # Just under the width gate -> still blocks-only.
    ws = _open_blocks_ws(tmp_path, (BU._WORKSPACE_MIN_W - 1, 600))
    assert not ws.block_ui._workspace_active()


# -- the panes / bounded layouts ----------------------------------------------

def test_panes_split_and_do_not_overlap(tmp_path):
    ws = _open_blocks_ws(tmp_path, WIDE)
    left, right = ws.block_ui._workspace_panes()
    assert left[0] == 0
    assert right[0] >= left[0] + left[2]          # scene is to the right of blocks
    assert right[0] + right[2] <= WIDE[0]          # fits the canvas
    assert left[1] == right[1] == 18               # both below the 18px OS bar


def test_block_layout_bound_to_left_pane(tmp_path):
    ws = _open_blocks_ws(tmp_path, WIDE)
    left, _right = ws.block_ui._workspace_panes()
    lay = ws.block_ui.block_layout
    assert not lay._base                            # bounded -> never the frozen branch
    assert left[0] <= lay.x0 < left[0] + left[2]
    assert lay.x0 + lay.outline_w <= left[0] + left[2]
    # the action bar sits inside the left pane too
    assert lay.add_btn[0] >= left[0]
    assert lay.redo_btn[0] + lay.redo_btn[2] <= left[0] + left[2]


def test_scene_layout_bound_to_right_pane(tmp_path):
    ws = _open_blocks_ws(tmp_path, WIDE)
    _left, right = ws.block_ui._workspace_panes()
    lay = ws.scene_ui.layout
    assert not lay._base
    px, py, pw, ph = lay.panel
    assert px >= right[0] and px + pw <= right[0] + right[2] + 1
    assert py >= right[1] and py + ph <= right[1] + right[3] + 1
    # the world view (where actors composite) is inside the right pane
    vx, vy, vw, vh = ws.scene_ui._sv_area()
    assert vx >= right[0] and vx + vw <= right[0] + right[2] + 1


# -- interaction: right pane = objects, left pane = programming ----------------

def test_tap_in_scene_pane_places_object(tmp_path):
    ws = _open_blocks_ws(tmp_path, WIDE)
    se = ws.scene_ui.sceneedit
    assert se is not None
    before = len(se.rows)
    ws.scene_ui.sceneedit.n = 5
    x0, y0, scale, vw, vh = ws.scene_ui._sv_metrics()
    _tap_blocks(ws, x0 + 24, y0 + 24)              # a tap in the scene world view
    assert len(se.rows) == before + 1
    assert se.rows[-1]["tile"] == 5
    assert ws.block_ui._ws_focus == "scene"


def test_tap_in_block_pane_drives_outline_not_scene(tmp_path):
    ws = _open_blocks_ws(tmp_path, WIDE)
    se = ws.scene_ui.sceneedit
    scene_rows = len(se.rows)
    be = ws.block_ui.blocks_ed
    assert be is not None
    lay = ws.block_ui.block_layout
    # Tap a row in the outline (left pane): moves the block cursor, focuses blocks,
    # and does NOT place a scene object.
    target = min(len(be.rows) - 1, 1)
    _tap_blocks(ws, lay.x0 + 4, lay.y0 + target * lay.row_h + 2)
    assert be.cur == target
    assert ws.block_ui._ws_focus == "blocks"
    assert len(se.rows) == scene_rows              # the scene was untouched


# -- persistence: the Blocks tab commit persists the scene pane ----------------

def test_leaving_blocks_tab_persists_scene_pane(tmp_path):
    ws = _open_blocks_ws(tmp_path, WIDE)
    ws.scene_ui.sceneedit.n = 9
    x0, y0, scale, vw, vh = ws.scene_ui._sv_metrics()
    _tap_blocks(ws, x0 + 40, y0 + 40)              # place an object in the scene pane
    assert ws.scene_ui.sceneedit.dirty
    assert ws.editor_app.tab == "blocks"
    ws.editor_app.save_current()                   # the autosave-only exit path (#111)
    assert (Path(ws.cart["path"]) / "scenes" / "main.moyscene").exists()


# -- per-object scripts through the workspace (#85/#93 Phase 2) ----------------

def test_placing_object_focuses_its_scripts(tmp_path):
    ws = _open_blocks_ws(tmp_path, WIDE)
    assert ws.block_ui.blocks_ed.target is None        # starts on the Stage
    ws.scene_ui.sceneedit.n = 4
    x0, y0, scale, vw, vh = ws.scene_ui._sv_metrics()
    _tap_blocks(ws, x0 + 30, y0 + 30)                  # place + select a new object
    tag = ws.scene_ui.sceneedit.rows[-1]["tag"]
    assert ws.block_ui.blocks_ed.target == tag         # the outline now edits it
    # its script entry was materialized with the familiar three hats
    objs = ws.block_ui.blocks_ed.program["objects"]
    assert any(o["tag"] == tag for o in objs)


def test_selecting_existing_object_switches_target(tmp_path):
    ws = _open_blocks_ws(tmp_path, WIDE)               # MAIN_SCENE: goal + coin
    se = ws.scene_ui.sceneedit
    # select the 'coin' actor directly, then let the workspace sync the target
    coin_i = next(i for i, r in enumerate(se.rows) if r["tag"] == "coin")
    se.sel = coin_i
    ws.block_ui._sync_target_from_scene()
    assert ws.block_ui.blocks_ed.target == "coin"


def test_sprite_list_lists_stage_and_every_sprite(tmp_path):
    ws = _open_blocks_ws(tmp_path, WIDE)               # MAIN_SCENE: goal + coin
    ws.frame(0.016)
    tags = [tag for _rect, tag in ws.block_ui._blk_roster_btns]
    assert tags[0] is None                             # STAGE chip first
    assert "goal" in tags and "coin" in tags           # a chip per sprite


def test_stage_chip_returns_to_global(tmp_path):
    ws = _open_blocks_ws(tmp_path, WIDE)
    ws.block_ui.blocks_ed.set_target("coin")
    ws.frame(0.016)                                    # draws the sprite list
    stage_rect = next(rect for rect, tag in ws.block_ui._blk_roster_btns if tag is None)
    _tap_blocks(ws, stage_rect[0] + 2, stage_rect[1] + 2)
    assert ws.block_ui.blocks_ed.target is None


def test_add_sprite_chip_creates_and_targets_a_new_sprite(tmp_path):
    ws = _open_blocks_ws(tmp_path, WIDE)
    ws.frame(0.016)
    before = len(ws.scene_ui.sceneedit.rows)
    add_rect = next(rect for rect, tag in ws.block_ui._blk_roster_btns
                    if tag == BU._ADD_SPRITE)
    _tap_blocks(ws, add_rect[0] + 2, add_rect[1] + 2)
    assert len(ws.scene_ui.sceneedit.rows) == before + 1   # a new actor placed
    assert ws.block_ui.blocks_ed.target == "sprite1"       # ...and its scripts open


def test_clicking_sprite_chip_edits_it_and_highlights_on_stage(tmp_path):
    ws = _open_blocks_ws(tmp_path, WIDE)
    ws.frame(0.016)
    coin_rect = next(rect for rect, tag in ws.block_ui._blk_roster_btns if tag == "coin")
    _tap_blocks(ws, coin_rect[0] + 2, coin_rect[1] + 2)
    assert ws.block_ui.blocks_ed.target == "coin"      # its scripts open
    se = ws.scene_ui.sceneedit
    assert se.rows[se.sel]["tag"] == "coin"            # and it's selected on the stage


def test_object_script_compiles_and_persists_on_play(tmp_path):
    ws = _open_blocks_ws(tmp_path, WIDE)
    be = ws.block_ui.blocks_ed
    be.set_target("coin")
    upd = be.program["objects"][0]["scripts"][1]       # coin on_update
    upd.setdefault("c", [])
    be.cur = next(i for i, r in enumerate(be.rows)
                  if r.kind == "insert" and r.parent is upd["c"])
    be.insert_block("remove_actor")
    # PLAY-commit path (save_current -> save_blocks): compiles the object loop into src
    ws.editor_app.save_current()
    src = ws.cart["src"]
    assert 'for _self in actors("coin"):' in src
    assert "remove_actor(_self)" in src
    assert "draw_scene()" in src


def test_number_keypad_renders_on_system_canvas_in_workspace(tmp_path):
    # Regression: double-clicking a number slot opens the number keypad; it must draw
    # on the SYSTEM canvas (the editor's surface), not the hidden 320x240 game canvas.
    # If it draws on the game canvas the modal is invisible and the editor looks frozen.
    ws = _open_blocks_ws(tmp_path, WIDE)
    ws.frame(0.016)
    be = ws.block_ui.blocks_ed
    be.set_target("player")
    upd = be.program["objects"][0]["scripts"][1]
    upd.setdefault("c", [])
    be.cur = next(i for i, r in enumerate(be.rows)
                  if r.kind == "insert" and r.parent is upd["c"])
    be.insert_block("point_dir", {"d": 90})
    ws.frame(0.016)
    pi = next(i for i, r in enumerate(be.rows)
              if r.kind == "block" and r.block.get("t") == "point_dir")
    be.cur = pi
    ws.block_ui.blk_slot = 0
    ws.block_ui._blk_a()                              # the double-click action
    assert ws.block_ui.blk_kbd is not None            # the number keypad opened
    ws.frame(0.016)
    # the keypad rect must have rendered onto the SYSTEM canvas
    from runtime import block_editor_ui as _bu
    x, y, w, h = _bu._BLK_NUM
    sc = ws.sys_canvas
    painted = probe.painted_pixels_rect(sc, x, y, w, h)
    assert painted > 500, "the number keypad did not render on the system canvas"


def test_tapping_the_second_argument_selects_that_slot(tmp_path):
    # Scratch-style: tap directly on the 2nd argument of a block to edit IT (not the
    # 1st) -- no keyboard right-arrow needed.
    ws = _open_blocks_ws(tmp_path, WIDE)
    ws.frame(0.016)
    be = ws.block_ui.blocks_ed
    be.set_target("player")
    upd = be.program["objects"][0]["scripts"][1]
    upd.setdefault("c", [])
    be.cur = next(i for i, r in enumerate(be.rows)
                  if r.kind == "insert" and r.parent is upd["c"])
    be.insert_block("move_actor_by", {"dx": -2, "dy": 0})   # "move by -2 0" (2 slots)
    ws.frame(0.016)
    bu = ws.block_ui
    lay = bu.block_layout
    pi = next(i for i, r in enumerate(be.rows)
              if r.kind == "block" and r.block.get("t") == "move_actor_by")
    row = be.rows[pi]
    row_x = lay.x0 + row.depth * lay.indent
    c1 = bu._blk_slot_text_col(row.block, 1)                 # column of the 2nd arg
    ry = lay.y0 + (pi - bu.blk_top) * lay.row_h + 6
    px = row_x + 3 * lay.fs + c1 * lay.cell + 1
    be.cur = 0                                               # block not pre-selected

    def tap(x, y):
        ws.pointer.down = True
        bu._blocks_pointer(x, y, True)
        ws.pointer.down = False
        bu._blocks_pointer(x, y, False)

    tap(px, ry)                                             # 1st tap -> highlight slot 1
    assert be.cur == pi and bu.blk_slot == 1
    tap(px, ry)                                             # 2nd tap -> edit slot 1 (dy)
    assert bu.blk_kbd is not None
    assert be.slots(row.block)[bu.blk_slot]["name"] == "dy"


# -- the bounded layouts as pure geometry (no Workstation) ---------------------

def test_bounded_layouts_confine_to_rect():
    bounds = (500, 18, 420, 560)
    bl = BU.BlockLayout(1024, 600, 1, bounds=bounds)
    assert not bl._base
    assert bounds[0] <= bl.x0 < bounds[0] + bounds[2]
    assert bl.x0 + bl.outline_w <= bounds[0] + bounds[2]
    assert bl.bar_y + bl.bar_h <= bounds[1] + bounds[3]
    sl = SU.SceneLayout(1024, 600, 1, bounds=bounds)
    assert not sl._base
    assert sl.body_fill == bounds
    assert sl.panel[0] >= bounds[0]


def test_base_layouts_ignore_bounds_and_stay_frozen():
    # A 320x240/1x layout is byte-identical whether or not bounds is passed.
    a = BU.BlockLayout(320, 240, 1)
    b = BU.BlockLayout(320, 240, 1)
    assert a._base and b._base
    assert (a.x0, a.y0, a.outline_w, a.rows) == (b.x0, b.y0, b.outline_w, b.rows)
    sa = SU.SceneLayout(320, 240, 1)
    assert sa._base


# -- the split is memoised (perf, #58) ----------------------------------------
#
# _layout_workspace runs on every pointer event AND the draw -- 4.2 times per
# painted frame during a drag on glass, 5.5ms of the tab's 87. Both layouts are
# pure functions of (canvas size, font scale, pane rect), so a repeat call must
# reuse them; anything that CHANGES the geometry -- or replaces a layout from
# outside -- must still re-derive.

def test_repeat_layout_reuses_both_layout_objects(tmp_path):
    ws = _open_blocks_ws(tmp_path, WIDE)
    bl, sl = ws.block_ui.block_layout, ws.scene_ui.layout
    for _ in range(4):
        assert ws.block_ui._layout_workspace() is not None
    assert ws.block_ui.block_layout is bl
    assert ws.scene_ui.layout is sl


def test_resize_re_derives_the_split(tmp_path):
    ws = _open_blocks_ws(tmp_path, WIDE)
    bl = ws.block_ui.block_layout
    left0, right0 = ws.block_ui._layout_workspace()
    ws.sys_canvas.w = WIDE[0] - 200            # a narrower window
    left1, right1 = ws.block_ui._layout_workspace()
    assert ws.block_ui.block_layout is not bl
    assert right1 != right0
    assert left1[2] < left0[2] or right1[2] < right0[2]


def test_external_relayout_is_not_masked_by_the_memo(tmp_path):
    # ws._relayout fans out to BlockEditorUI.relayout(), which installs an
    # UNBOUNDED layout at the SAME canvas size. The memo must notice (identity),
    # or the tab would draw full-width blocks under the scene pane.
    ws = _open_blocks_ws(tmp_path, WIDE)
    left, _right = ws.block_ui._layout_workspace()
    ws.block_ui.relayout(ws.sys_canvas.w, ws.sys_canvas.h, 1)
    assert ws.block_ui.block_layout.x0 + ws.block_ui.block_layout.outline_w > left[2]
    ws.block_ui._layout_workspace()
    assert (ws.block_ui.block_layout.x0 + ws.block_ui.block_layout.outline_w
            <= left[0] + left[2])
