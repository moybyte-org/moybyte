"""The desk Editor's workspaces (docs/look_2026-10.md step 30;
docs/media/look_2026-10/EDITOR_LAYOUT.md and MAXIMISE_VARIANTS.md's Resize):
the starting arrangements at both desk sizes, the free dividers and their
minimums, what the board remembers, and the desk Editor driven through the
host's keys and pointer."""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from runtime import editor_panes as P      # noqa: E402
from runtime import host_app                # noqa: E402

FULL_1024 = (6, 46, 1012, 548)              # full screen: the row is the top 40 px
STRIP_1024 = (6, 90, 1012, 504)             # the pictures' title-strip frame
FULL_800 = (6, 46, 788, 428)
DT = 1 / 30


def _rects(key, area, **kw):
    return [(pane, r) for pane, r, _p in P.Arrangement(key, area, **kw).place()[0]]


# -- the starting arrangements --------------------------------------------------------

def test_the_workspaces_start_as_drawn_at_1024_full_screen():
    assert _rects("settings", FULL_1024) == [
        (("cards",), (6, 46, 360, 548)), (("game",), (372, 46, 646, 548))]
    assert _rects("code", FULL_1024) == [
        (("code",), (6, 46, 666, 548)), (("game",), (678, 46, 340, 282)),
        (("sprites", "sounds", "maps", "log"), (678, 334, 340, 260))]
    assert _rects("art", FULL_1024) == [
        (("paint",), (6, 46, 400, 548)), (("map", "game"), (412, 46, 606, 380)),
        (("sheet",), (412, 432, 606, 162))]
    assert _rects("world", FULL_1024)[1:] == [
        (("game",), (678, 46, 340, 282)), (("actor", "stamps"), (678, 334, 340, 260))]
    assert _rects("sound", FULL_1024) == [
        (("sounds",), (6, 46, 200, 548)), (("music",), (212, 46, 460, 548)),
        (("game",), (678, 46, 340, 282)), (("song",), (678, 334, 340, 260))]
    assert _rects("code", FULL_1024, blocks=True) == [
        (("palette",), (6, 46, 200, 548)), (("blocks",), (212, 46, 460, 548)),
        (("scene", "game"), (678, 46, 340, 282)), (("made",), (678, 334, 340, 260))]


def test_under_a_title_strip_the_scrolling_pane_gives_the_difference():
    """EDITOR_LAYOUT.md's pictures: Art's Map 336 tall over the Sheet's 162;
    the Reference 216 under the Run slot's 282."""
    assert _rects("art", STRIP_1024)[1:] == [
        (("map", "game"), (412, 90, 606, 336)), (("sheet",), (412, 432, 606, 162))]
    assert _rects("code", STRIP_1024)[2][1] == (678, 378, 340, 216)


def test_at_800_one_tabbed_pane_stands_at_the_right():
    assert _rects("code", FULL_800) == [
        (("code",), (6, 46, 442, 428)),
        (("game", "sprites", "sounds", "maps", "log"), (454, 46, 340, 428))]
    # full screen gives Sprite 330 (MAXIMISE_VARIANTS.md); a restored 800 its 320
    assert _rects("art", FULL_800)[0] == (("paint",), (6, 46, 330, 428))
    assert _rects("art", (6, 90, 788, 384))[0] == (("paint",), (6, 90, 320, 384))
    assert _rects("sound", FULL_800)[1][0] == ("game", "song")


# -- the dividers ---------------------------------------------------------------------

def test_a_divider_stops_at_its_neighbours_minimum_and_moves_only_its_two_panes():
    arr = P.Arrangement("code", FULL_1024)
    assert arr.drag((), 0, 2000) == 1           # stopped at the Run slot's minimum
    panes = _place(arr)
    assert panes[("code",)] == (6, 46, 686, 548)
    assert panes[("game",)][2] == P.GAME_MIN[0]          # the game at 1x, 320 wide
    # the right column's own split kept the Run slot's height
    assert panes[("game",)][3] == 282
    assert arr.drag((), 0, 0) == 0               # Code scrolls: its floor only
    assert _place(arr)[("code",)][2] == P.FLOOR[0]


def test_a_canvas_pane_keeps_the_t_decks_page():
    arr = P.Arrangement("art", FULL_1024)
    arr.drag((), 0, 0)
    assert _place(arr)[("paint",)][2] == P.CANVAS_MIN[0]
    arr.drag((1,), 0, 0)                         # the Map over the Sheet
    assert _place(arr)[("map", "game")][3] == P.GAME_MIN[1]


def test_reset_puts_a_divider_back_and_ctrl_0_all_of_them():
    arr = P.Arrangement("art", FULL_1024)
    start = _place(arr)
    arr.drag((), 0, 500)
    arr.drag((1,), 0, 300)
    arr.reset((), 0)
    got = _place(arr)
    assert got[("paint",)] == start[("paint",)]
    assert got[("sheet",)] != start[("sheet",)]
    arr.reset()
    assert _place(arr) == start


def test_the_board_remembers_the_sizes_and_reads_a_foreign_state_as_absent():
    arr = P.Arrangement("sound", FULL_1024)
    arr.drag((), 0, 260)
    saved = arr.state()
    again = P.Arrangement("sound", FULL_1024)
    assert again.restore(saved)
    assert _place(again) == _place(arr)
    other = P.Arrangement("art", FULL_1024)
    assert not other.restore(saved)
    assert not other.restore([[[], ["x"]]])
    assert not other.restore("junk")


def test_ctrl_alt_arrows_reach_the_right_and_bottom_dividers():
    arr = P.Arrangement("code", FULL_1024)
    panes = {pane: path for pane, _r, path in arr.place()[0]}
    assert arr.edges(panes[("code",)]) == [((), 0, "h")]
    assert arr.edges(panes[("game",)]) == [((1,), 0, "v")]
    assert arr.edges(panes[("sprites", "sounds", "maps", "log")]) == []


def test_a_workspaces_minimum_is_its_panes_with_their_gutters():
    assert P.Arrangement("art", FULL_1024).minimum() == (320 + 6 + 320, 272 + 6 + 48)
    assert P.Arrangement("sound", FULL_1024).minimum()[0] == 64 + 6 + 320 + 6 + 320


def _place(arr):
    return {pane: r for pane, r, _p in arr.place()[0]}


# -- on the desk ----------------------------------------------------------------------

@pytest.fixture
def desk(tmp_path):
    ws = host_app.build_workstation(str(tmp_path / "carts"), sys_size=(1024, 600),
                                    panel_diagonal_in=7.0, font_scale=1, windowed=True)
    ws.open_desk()
    cart = next(c for c in ws.carts.all if c.get("title") == "Sky Run")
    ws.open_in_editor(cart)
    drv = host_app.ConsoleDriver(ws)
    for _ in range(3):
        drv.frame(DT)
    return ws, drv


def _tap(drv, code, mods=0):
    if mods:
        drv.set_mods(mods)
        drv.frame(DT)
    drv.key_down(code, mods)
    drv.frame(DT)
    drv.key_up(code, mods)
    drv.frame(DT)
    if mods:
        drv.set_mods(0)
        drv.frame(DT)


def test_the_editor_opens_full_screen_on_its_workspaces(desk):
    ws, _drv = desk
    d = ws.editor_desk
    assert ws.wm._wins["menu"].full()
    assert d.active() and ws.editor_layer() is d
    assert d.key == "settings" and ws.editor_app.tab == "cards"


def test_ctrl_digits_switch_workspaces_and_f6_moves_the_keys(desk):
    ws, drv = desk
    d = ws.editor_desk
    _tap(drv, ord("3"), 1)                       # Ctrl+3: Art
    assert d.key == "art" and ws.editor_app.tab == "paint"
    _tap(drv, 0x101 + 5)                         # F6: Map · Game
    assert ws.editor_app.tab == "map"
    _tap(drv, 0x101 + 5, 2)                      # Shift+F6: back to the Sprite pane
    assert ws.editor_app.tab == "paint"
    _tap(drv, ord("]"), 1)                       # the browser's Ctrl+Tab
    assert d.key == "world" and ws.editor_app.tab == "scene"
    _tap(drv, 9, 1)                              # Ctrl+Tab
    assert d.key == "sound"


def test_moving_focus_commits_nothing_and_a_switch_commits_only_dirty_panes(desk, monkeypatch):
    ws, drv = desk
    d = ws.editor_desk
    _tap(drv, ord("3"), 1)
    owed = []
    monkeypatch.setattr(ws.editor_app, "commit_later", lambda tab: owed.append(tab))
    _tap(drv, 0x101 + 5)
    assert owed == []
    ws.paint.sheet.pset(0, 0, 9)                 # the Sprite pane holds an edit
    ws.paint.dirty = True
    d.switch("code")
    assert owed == ["paint"]


def test_ctrl_alt_arrows_and_ctrl_0_move_and_reset_the_dividers(desk):
    ws, drv = desk
    d = ws.editor_desk
    _tap(drv, ord("2"), 1)                       # Code: the Code pane focused
    w0 = d.panes()[0][1][2]
    _tap(drv, 0x111, 1 | 4)                      # Ctrl+Alt+Right
    assert d.panes()[0][1][2] == w0 + 8
    assert ws.system.get("editor_panes", None)    # remembered on the board
    _tap(drv, ord("0"), 1)
    assert d.panes()[0][1][2] == w0


def test_a_divider_dragged_by_the_pointer_lays_out_on_release(desk):
    ws, drv = desk
    d = ws.editor_desk
    _tap(drv, ord("2"), 1)
    (_p, (x, y, w, h), _path), = [p for p in d.panes() if p[0] == ("code",)]
    gx, gy = x + w + 3, y + h // 2
    drv.touch(gx, gy)
    drv.frame(DT)
    drv.touch_drag(gx - 100, gy)
    drv.frame(DT)
    assert d.panes()[0][1][2] == w               # only its line moves while held
    drv.touch_up()
    drv.frame(DT)
    assert d.panes()[0][1][2] == w - 100


def test_a_grown_pane_takes_the_workspace_and_esc_gives_it_back(desk):
    ws, drv = desk
    d = ws.editor_desk
    _tap(drv, ord("3"), 1)
    _tap(drv, 13, 1 | 2)                         # Ctrl+Shift+Enter
    assert d.grown == ("paint",)
    drv.escape()
    drv.frame(DT)
    assert d.grown is None


def test_a_sheet_pick_sets_the_sprite_and_the_brush_and_a_slide_previews(desk):
    ws, drv = desk
    d = ws.editor_desk
    _tap(drv, ord("3"), 1)
    drv.frame(DT)
    (pg, (x, y, w, h)), _second = d._sheet_rects
    tw = w // 16
    drv.touch(x + 3 * tw + 1, y + 1)
    drv.frame(DT)
    assert ws.paint.n == 3
    drv.touch_drag(x + 5 * tw + 1, y + tw + 1)
    drv.frame(DT)
    assert ws.paint.n == 16 + 5                 # each sprite it passes shows
    drv.touch_up()
    drv.frame(DT)
    assert ws.paint.n == 21
    assert ws.map_ui.mapedit.n == 21


def test_a_settled_workspace_paints_nothing(desk):
    """With no input the redraw gate draws no frame on any workspace."""
    ws, drv = desk
    for key in P.KEYS:
        ws.editor_desk.switch(key)
        ws.pointer.visible = False
        ws._toast_until = 0
        for _ in range(4):
            drv.frame(DT)
        drawn = ws._frames_drawn
        for _ in range(8):
            drv.frame(DT)
        assert ws._frames_drawn == drawn, "an idle %s workspace painted" % key


def test_a_handheld_never_loads_the_desk_editor(tmp_path):
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    assert ws.editor_desk is None


def _pane(d, content):
    return next(r for pane, r, _p in d.panes() if content in pane)


def test_the_wheel_zooms_the_map_in_whole_steps_and_shift_wheel_pans(desk):
    ws, drv = desk
    d = ws.editor_desk
    _tap(drv, ord("3"), 1)                       # Art: the Map beside the Sprite
    x, y, w, h = _pane(d, "map")
    drv.hover(x + w // 2, y + h // 2)
    drv.frame(DT)
    mu = ws.map_ui
    z = mu.map_zoom
    drv.wheel(0, -1)                             # a notch up: in
    drv.frame(DT)
    assert mu.map_zoom == z + 1
    drv.wheel(0, 1)
    drv.frame(DT)
    assert mu.map_zoom == z
    cam = mu.mapedit.cam_x
    drv.wheel(0, 3, 2)                           # Shift+wheel: across
    drv.frame(DT)
    drv.set_mods(0)
    assert mu.mapedit.cam_x > cam


def test_a_middle_drag_pans_the_scene(desk):
    ws, drv = desk
    d = ws.editor_desk
    _tap(drv, ord("4"), 1)                       # World
    x, y, w, h = _pane(d, "scene")
    su = ws.scene_ui
    su.scene_zoom = len(su.layout.zooms) - 1     # zoomed in: room to pan
    se = su.sceneedit
    se.cam_x = se.cam_y = 0
    drv.middle(x + w // 2, y + h // 2, True)
    drv.frame(DT)
    drv.hover(x + w // 2 - 120, y + h // 2)
    drv.frame(DT)
    drv.middle(x + w // 2 - 120, y + h // 2, False)
    drv.frame(DT)
    assert se.cam_x > 0


def test_play_runs_the_game_in_the_run_slot_and_stop_ends_it(desk):
    ws, drv = desk
    d = ws.editor_desk
    _tap(drv, ord("2"), 1)                       # Code: the Run slot over the Reference
    _tap(drv, 13, 1)                             # Ctrl+Enter: Play
    for _ in range(4):
        drv.frame(DT)
    win = ws.wm._wins["desktop"]
    x, y, w, h = _pane(d, "game")
    gx, gy, gw, gh = win.docked
    assert (gw, gh) == (320, 240)                # the game at 1x, in the slot
    assert x <= gx and gx + gw <= x + w and y + P.HEADER <= gy and gy + gh <= y + h
    assert ws.wm._order[-1] == "desktop"
    _tap(drv, ord("."), 1)                       # Ctrl+.: Stop
    for _ in range(3):
        drv.frame(DT)
    assert "desktop" not in ws.wm._wins


def test_play_selects_the_game_tab_and_stop_gives_the_map_back(desk):
    ws, drv = desk
    d = ws.editor_desk
    _tap(drv, ord("3"), 1)                       # Art: Map · Game
    pane = ("map", "game")
    assert d.tab_of(pane) == "map"
    _tap(drv, 13, 1)
    for _ in range(4):
        drv.frame(DT)
    assert d.tab_of(pane) == "game" and ws.wm._wins["desktop"].docked
    _tap(drv, ord("."), 1)
    for _ in range(3):
        drv.frame(DT)
    assert d.tab_of(pane) == "map"


def _click(drv, x, y):
    drv.touch(x, y)
    drv.frame(DT)
    drv.touch_up()
    drv.frame(DT)
    drv.frame(DT)


def test_the_actor_pane_flips_and_removes_the_picked_actor(desk):
    ws, drv = desk
    d = ws.editor_desk
    _tap(drv, ord("4"), 1)                       # World
    se = ws.scene_ui.sceneedit
    se.sel = 0
    a = se.selected()
    flip = bool(a.get("flip"))
    n = len(se.rows)
    ws._dirty = True
    drv.frame(DT)
    box = d._host.box("actor flip")
    _click(drv, box[0] + box[2] - 8, box[1] + box[3] // 2)
    assert bool(se.rows[0].get("flip")) != flip
    box = d._host.box("actor remove")
    _click(drv, box[0] + box[2] // 2, box[1] + box[3] // 2)
    assert len(se.rows) == n - 1


def test_the_sounds_list_picks_a_sound_and_the_song(desk):
    ws, drv = desk
    d = ws.editor_desk
    _tap(drv, ord("5"), 1)                       # Sound
    drv.frame(DT)
    me = ws.music_ui.musicedit
    assert len(me.bank.sfx) >= 2
    x, y, w, h = _pane(d, "sounds")
    body_y = y + P.HEADER
    rows = [b for b in (d._host.box("Sound 1"),) if b]
    if rows:
        bx, by, bw, bh = rows[0]
    else:                                        # the list's rows from its top
        bx, by, bw, bh = x + 10, body_y + 10 + 2 * 28, w - 20, 28
    _click(drv, bx + bw // 2, by + bh // 2)
    assert me.view == me.SFX_VIEW and me.sfx_idx == 1


def test_a_stamp_pick_sets_the_scenes_brush(desk):
    ws, drv = desk
    d = ws.editor_desk
    _tap(drv, ord("4"), 1)
    pane = ("actor", "stamps")
    d._tabs[pane] = 1
    ws._dirty = True
    drv.frame(DT)
    (_pg, (x, y, w, h)) = d._sheet_rects[0]
    tw = w // 16
    _click(drv, x + 6 * tw + 1, y + 1)
    assert ws.scene_ui.sceneedit.n == 6


def test_the_map_and_scene_panes_open_at_2x_and_their_header_tools_act(desk):
    ws, drv = desk
    d = ws.editor_desk
    _tap(drv, ord("3"), 1)
    assert ws.map_ui.map_zoom == 1               # 16 px a cell: 2x
    d._head_tool("map:flood")
    assert ws.map_ui.map_tool == "flood" and d._tool_on("map:flood")
    d._head_tool("map:erase")
    assert ws.map_ui.map_erase and not d._tool_on("map:flood")
    _tap(drv, ord("4"), 1)
    assert ws.scene_ui.scene_zoom == 1
    se = ws.scene_ui.sceneedit
    snap = se.snap
    d._head_tool("scene:snap")
    assert se.snap != snap


def test_a_reference_pick_inserts_its_call_at_the_caret(desk):
    ws, drv = desk
    d = ws.editor_desk
    _tap(drv, ord("2"), 1)                       # Code: the Reference's Sprites
    ws._dirty = True
    drv.frame(DT)
    (_pg, (x, y, w, h)) = d._sheet_rects[0]
    tw = w // 16
    _click(drv, x + 6 * tw + 1, y + 1)
    assert d._cur_sprite() == 6
    ws.editor.row, ws.editor.col = 0, 0
    ws._dirty = True
    drv.frame(DT)
    bx, by, bw, bh = d._host.box("insert call")
    _click(drv, bx + bw // 2, by + bh // 2)
    assert ws.editor.text().startswith("spr(6, x, y)")


# -- the Sound pane (on the verbs over the MusicEditor core) ------------------------

def _sound(desk):
    ws, drv = desk
    d = ws.editor_desk
    _tap(drv, ord("5"), 1)                       # Sound
    me = ws.music_ui.musicedit
    d.focus_pane(1)                              # the Sound pane
    ws._dirty = True
    drv.frame(DT)
    from runtime import editor_sound as S
    return ws, drv, d, me, S


def test_the_sound_pane_is_drawn_on_the_verbs_not_the_music_tab(desk, monkeypatch):
    ws, drv, d, me, S = _sound(desk)
    drew = []
    monkeypatch.setattr(ws.layer_of("music"), "draw", lambda dt: drew.append(dt))
    ws._dirty = True
    drv.frame(DT)
    assert drew == []
    assert ws.editor_app.tab == "music"
    assert d._host.box("wave brush") is not None
    assert d._host.box("sound add") is not None


def test_a_tap_on_the_graph_picks_the_step_and_sets_its_note(desk):
    ws, drv, d, me, S = _sound(desk)
    body = d._body(_pane(d, "music"), 1)
    g = S.geometry(body, 1)
    gx, gy, gw, gh = g["graph"]
    lo = S.low(d, me)
    _click(drv, gx + g["cw"] + 4, gy + gh - 3 * g["semi"] - 2)
    assert me.step == 1
    assert me.cur_step()[0] == lo + 3
    lx, ly, _lw, _lh = g["ladder"]
    _click(drv, lx + g["cw"] + 4, ly + 1)
    assert me.cur_step()[2] == S.LEVELS
    me.undo()
    assert me.cur_step()[2] != S.LEVELS


def test_the_sound_panes_keys_move_the_step_and_its_note(desk):
    ws, drv, d, me, S = _sound(desk)
    me.select_cursor(0)
    p = me.cur_step()[0]
    drv.press("right")
    drv.frame(DT)
    assert me.step == 1
    me.select_cursor(0)
    drv.press("up")
    drv.frame(DT)
    assert me.cur_step()[0] == p + 1
    w = me.cur_step()[1]
    _tap(drv, ord("w"))
    assert me.cur_step()[1] != w
    assert not ws.editor_app._tab_is_clean("music")


def test_a_sound_is_renamed_and_its_name_saved_with_the_bank(desk):
    ws, drv, d, me, S = _sound(desk)
    _tap(drv, 0x101 + 1)                         # F2
    assert d.part_state.get("rename") == ""
    drv.frame(DT)
    for ch in "boom":
        _tap(drv, ord(ch))
    _tap(drv, 13)
    assert me.bank.sfx[0].name == "boom"
    assert me.bank.to_dict()["sfx"][0]["name"] == "boom"
    assert d.part_state.get("rename") is None
    assert S.title(d) == "Sound 0 · boom"
    assert S.row_label(me, 0) == "0  boom"
    me.undo()
    assert me.bank.sfx[0].name is None
    me.redo()
    ws.save_sounds()
    import json
    with open(ws.cart["path"] + "/sounds.json") as f:
        assert json.load(f)["sfx"][0]["name"] == "boom"


def test_the_sounds_list_adds_a_sound_and_plays_one(desk):
    ws, drv, d, me, S = _sound(desk)
    n = len(me.bank.sfx)
    bx, by, bw, bh = d._host.box("pane 0 new sound")
    _click(drv, bx + bw // 2, by + bh // 2)
    assert len(me.bank.sfx) == n + 1 and me.sfx_idx == n


def test_a_settled_sound_pane_paints_nothing_with_its_name_popover_open(desk):
    ws, drv, d, me, S = _sound(desk)
    S.open_rename(d)
    ws.pointer.visible = False
    ws._toast_until = 0
    for _ in range(4):
        drv.frame(DT)
    drawn = ws._frames_drawn
    for _ in range(8):
        drv.frame(DT)
    assert ws._frames_drawn == drawn
