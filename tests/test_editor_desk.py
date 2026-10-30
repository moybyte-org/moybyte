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
from runtime import ui                      # noqa: E402

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


def _painted(ws, drv, n=4):
    ws.pointer.visible = False
    ws._toast_until = 0
    drawn = ws._frames_drawn
    for _ in range(n):
        drv.frame(DT)
    return ws._frames_drawn - drawn


def test_a_clean_switch_paints_once_and_a_commit_paints_again(desk):
    """The owed commit rides the frame's tail and shows nothing: a switch
    away from a clean tab paints the destination once; a tab that wrote
    paints once more, since a commit may move what the Editor shows."""
    ws, drv = desk
    _painted(ws, drv)
    ws.editor_app.set_tab("code")
    assert _painted(ws, drv) == 1
    ws.editor_app.set_tab("paint")
    assert _painted(ws, drv) == 1
    ws.paint.sheet.pset(0, 0, 9)
    ws.paint.sheet.dirty = True
    ws.editor_app.set_tab("cards")
    assert _painted(ws, drv) >= 2


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


def _card_bodies_hidden(ws, drv, keys, monkeypatch, label):
    from runtime import editor_desk
    ws.pointer.visible = False
    d = ws.editor_desk

    def shot(key):
        d.switch(key)
        for _ in range(3):
            drv.frame(DT)
        ws.mark_dirty()
        drv.frame(DT)
        return bytes(ws.sys_canvas.to_rgb888())

    for key in keys:
        got = shot(key)
        monkeypatch.setattr(editor_desk, "_FILLS_BODY", ())
        want = shot(key)
        monkeypatch.undo()
        assert got == want, "%s %s: a card's body showed" % (key, label)


@pytest.mark.parametrize("theme,variant", [("moy", "light"), ("moy", "dark"),
                                           ("contrast", "dark"), ("blocks", "light")])
def test_a_pane_that_fills_its_body_hides_the_cards_body(desk, theme, variant, monkeypatch):
    """`_FILLS_BODY`'s panes draw their card down to the header only: the
    workspaces showing them paint the same pixels as with the whole card."""
    ws, drv = desk
    ws.look.set_theme(theme, persist=False, variant=variant)
    _card_bodies_hidden(ws, drv, ("settings", "code", "art", "world"), monkeypatch,
                        "%s/%s" % (theme, variant))


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


def test_an_idle_code_pane_reads_its_document_only_after_a_key(desk, monkeypatch):
    """A Code edit made in its pane owes the playtest a restart (E-02), and
    the check compares the whole source: a frame with no key asks nothing
    (it built the source's text every frame, ~5 KB of garbage a frame on
    the P4), a typed key still owes it."""
    ws, drv = desk
    _tap(drv, ord("2"), 1)                      # Code: the Code pane focused
    app = ws.editor_app
    asked = []
    real = app._tab_is_clean
    monkeypatch.setattr(app, "_tab_is_clean", lambda tab: asked.append(tab) or real(tab))
    for _ in range(10):
        drv.frame(DT)
    assert asked == []
    _tap(drv, ord("x"))
    assert "code" in asked and "code" in app.start_owed


def test_the_wheel_scrolls_the_code_pane_past_its_caret_and_a_key_brings_it_back(desk):
    """The wheel moves the view, not the caret, and nothing pulls the view
    back to the caret: the pane's layout is set every frame, and adopting
    it keeps the view where the wheel put it. A typed key shows the caret."""
    ws, drv = desk
    _tap(drv, ord("2"), 1)                      # Code: the Code pane focused
    d = ws.editor_desk
    ed = ws.editor
    ed.set_text("\n".join("v%d = 1" % i for i in range(300)))
    x, y, w, h = _pane(d, "code")
    ox, oy = d._origin()
    drv.hover(ox + x + w // 2, oy + y + h // 2)
    drv.frame(DT)
    for _ in range(110):
        drv.wheel(0, 1)
        drv.frame(DT)
    assert ed.top + ed.ROWS == len(ed.lines), "the last line is shown"
    assert (ed.row, ed.col) == (0, 0), "the caret stays"
    _tap(drv, ord("x"))
    for _ in range(30):                         # the view glides back to the caret
        drv.frame(DT)
    assert ed.top <= ed.row < ed.top + ed.ROWS and ed.lines[0].startswith("x")


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


def test_a_held_arrow_in_the_sound_pane_steps_at_the_repeat_rate(desk):
    """A held arrow moves the note once on the press and then at the
    repeat rate, as a held arrow does on every surface; the desk's own
    verbs never see its repeats (their focus stays where it was)."""
    from runtime import moy_input as mi
    ws, drv, d, me, S = _sound(desk)
    me.select_cursor(0)
    p = me.cur_step()[0]
    focus = _desk_focus(d)
    drv.key_down(mi.KEY_DOWN)
    for _ in range(30):                          # a second held
        drv.frame(DT)
    drv.key_up(mi.KEY_DOWN)
    drv.frame(DT)
    assert me.cur_step()[0] < p - 2
    assert _desk_focus(d) == focus


def _desk_focus(d):
    from runtime import ui_draw
    return ui_draw._C.api_focus(d._host.ctx)


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


# -- the Blocks workspace's palette and made code --------------------------------------

@pytest.fixture
def blocks_desk(tmp_path):
    ws = host_app.build_workstation(str(tmp_path / "carts"), sys_size=(1024, 600),
                                    panel_diagonal_in=7.0, font_scale=1, windowed=True)
    ws.open_desk()
    cart = next(c for c in ws.carts.all if c.get("title") == "Coin Quest")
    ws.open_in_editor(cart)
    drv = host_app.ConsoleDriver(ws)
    for _ in range(3):
        drv.frame(DT)
    _tap(drv, ord("2"), 1)                       # Code: a blocks cart's is the Blocks one
    return ws, drv


def test_a_blocks_carts_code_workspace_shows_the_palette_and_the_made_code(blocks_desk):
    ws, drv = blocks_desk
    d = ws.editor_desk
    assert [p for p, _r, _p in d.panes()][0] == ("palette",)
    from runtime import blocks as B, editor_parts as Parts
    be = ws.block_ui.blocks_ed
    assert Parts.made_lines(be) == B.compile_blocks(be.program).rstrip("\n").split("\n")
    assert d._host.box("palette blocks") is not None


def test_the_made_code_is_a_text_view_the_wheel_scrolls(blocks_desk):
    """The made code draws through `ui.text_view` on the desk's verbs: its
    rows answer a hit test, and the wheel over it scrolls it by pixels."""
    from runtime import ui_draw, editor_parts as Parts
    ws, drv = blocks_desk
    d = ws.editor_desk
    lines = len(Parts.made_lines(ws.block_ui.blocks_ed))
    pane, r = next((p, r) for p, r, _p in d.panes() if p == ("made",))
    x, y, w, h = d._body(r, d._unit(), pane)
    px, py = x + 60, y + 20
    first = ui_draw.text_at(d._host, "made", px, py)
    assert first is not None and first[0] == 0
    ox, oy = d._origin()
    drv.hover(ox + px, oy + py)
    drv.wheel(0, 3)
    for _ in range(20):
        drv.frame(DT)
    got = ui_draw.text_at(d._host, "made", px, py)
    assert lines > h // ws.code_layout.lh, "the made code fits its pane: nothing to scroll"
    assert got[0] > 0, "the wheel did not scroll the made code"


def test_a_palette_pick_puts_its_block_at_the_cursor_and_the_made_code_follows(blocks_desk):
    ws, drv = blocks_desk
    d = ws.editor_desk
    from runtime import editor_parts as Parts
    be = ws.block_ui.blocks_ed
    before = list(Parts.made_lines(be))
    d.part_state["block cat"] = "looks"
    ws._dirty = True
    drv.frame(DT)
    items = Parts.palette_items(be, "looks")
    bx, by, bw, bh = d._host.box("palette blocks")
    _click(drv, bx + 20, by + 14)                # the category's first block
    assert items[0][0] in repr(be.program)
    assert Parts.made_lines(be) != before
    assert not ws.editor_app._tab_is_clean("blocks")


def test_the_palettes_categories_pick_their_blocks(blocks_desk):
    ws, drv = blocks_desk
    d = ws.editor_desk
    bx, by, bw, bh = d._host.box("block categories 0")
    _click(drv, bx + bw // 2, by + 28 + 14)      # the second row: Draw
    assert d.part_state["block cat"] == "draw"
    from runtime import editor_parts as Parts
    assert Parts.palette_items(None, "events") == []


def test_a_blocks_carts_panes_hide_the_cards_body_too(blocks_desk, monkeypatch):
    ws, drv = blocks_desk
    _card_bodies_hidden(ws, drv, ("code", "world"), monkeypatch, "blocks cart")


def test_a_settled_blocks_workspace_paints_nothing(blocks_desk):
    ws, drv = blocks_desk
    ws.pointer.visible = False
    ws._toast_until = 0
    for _ in range(4):
        drv.frame(DT)
    drawn = ws._frames_drawn
    for _ in range(8):
        drv.frame(DT)
    assert ws._frames_drawn == drawn


def test_an_actor_picked_in_the_scene_pane_opens_its_scripts(blocks_desk):
    """A blocks cart's Scene pane: a tap on an actor points the Blocks pane
    at its scripts (EDITOR_LAYOUT.md's Scene · Game), and the scene edited
    beside the blocks is saved with them."""
    ws, drv = blocks_desk
    d = ws.editor_desk
    se = ws.scene_ui.sceneedit
    be = ws.block_ui.blocks_ed
    assert be.target is None                         # the Stage
    pane, r, _p = next(p for p in d.panes() if "scene" in p[0])
    body = d._body(r, d._unit(), pane)
    x0, y0, scale, _vw, _vh = d._in_pane("scene", body, ws.scene_ui._sv_metrics)
    actor = next(a for a in se.rows if a.get("tag"))
    ox, oy = d._origin()
    _click(drv, ox + body[0] + x0 + (actor["x"] - se.cam_x + 2) * scale,
           oy + body[1] + y0 + (actor["y"] - se.cam_y + 2) * scale)
    assert be.target == actor["tag"]
    se.dirty = True
    d.focus_pane(next(i for i, p in enumerate(d.panes()) if p[0] == ("blocks",)))
    assert ws.editor_app.tab == "blocks"
    ws.editor_app.save_current()
    assert not se.dirty


# -- the Reference's Sounds, Map and Log ---------------------------------------------

def _reference(desk, tab):
    ws, drv = desk
    d = ws.editor_desk
    _tap(drv, ord("2"), 1)                       # Code
    pane = ("sprites", "sounds", "maps", "log")
    d._tabs[pane] = pane.index(tab)
    ws.editor.row, ws.editor.col = 0, 0
    ws._dirty = True
    drv.frame(DT)
    return ws, drv, d


def _press(drv, d, id):
    bx, by, bw, bh = d._host.box(id)
    _click(drv, bx + bw // 2, by + bh // 2)


def test_a_reference_sound_pick_inserts_its_sfx_call(desk):
    ws, drv, d = _reference(desk, "sounds")
    bx, by, bw, bh = d._host.box("ref sounds")
    _click(drv, bx + 20, by + 28 + 14)           # sound 1
    assert d.part_state["ref sound"] == 1
    ws.editor.row, ws.editor.col = 0, 0
    _press(drv, d, "insert sfx")
    assert ws.editor.text().startswith("sfx(1)")


def test_a_reference_map_pick_inserts_its_mget_call(desk):
    ws, drv, d = _reference(desk, "maps")
    from runtime import editor_parts as Parts
    body = d._body(_pane(d, "maps"), 1)
    (mx, my, _mw, _mh), _c, _r, tw = Parts._map_rect(d, body, 1)
    vx, vy = d.part_state["ref view"]
    _click(drv, mx + 3 * tw + 1, my + 2 * tw + 1)
    assert d.part_state["ref cell"] == (vx + 3, vy + 2)
    ws.editor.row, ws.editor.col = 0, 0
    _press(drv, d, "insert mget")
    assert ws.editor.text().startswith("mget(%d, %d)" % (vx + 3, vy + 2))


def test_the_log_names_an_error_and_takes_the_caret_to_its_line(desk):
    ws, drv, d = _reference(desk, "log")
    ws.player.cart_error = "NameError: foo"
    ws.player.crash_line = 12
    ws._dirty = True
    drv.frame(DT)
    bx, by, bw, bh = d._host.box("log")
    _click(drv, bx + 20, by + 14)
    assert ws.editor.row == 11
    assert d.tab_of(d.focused()[0]) == "code"


def test_a_settled_reference_paints_nothing_on_any_tab(desk):
    ws, drv = desk
    for tab in ("sounds", "maps", "log"):
        _ws, _drv, d = _reference(desk, tab)
        ws.pointer.visible = False
        ws._toast_until = 0
        for _ in range(4):
            drv.frame(DT)
        drawn = ws._frames_drawn
        for _ in range(8):
            drv.frame(DT)
        assert ws._frames_drawn == drawn, tab


# -- the Code pane's header ------------------------------------------------------------

def test_the_code_panes_header_finds_jumps_and_opens_its_more_menu(desk):
    ws, drv = desk
    d = ws.editor_desk
    _tap(drv, ord("2"), 1)
    cl = ws.code_layer
    assert d._host.box("pane 0 files") is not None
    _press(drv, d, "pane 0 code find")
    assert cl._find_open
    _press(drv, d, "pane 0 code find")
    assert not cl._find_open
    _press(drv, d, "pane 0 code goto")
    assert cl._jump_open
    cl._jump_open = False
    gutter = cl._gutter
    _press(drv, d, "pane 0 code more")
    assert d.part_state["code more"] is not None
    drv.frame(DT)
    _press(drv, d, "Line numbers")
    assert cl._gutter != gutter
    assert d.part_state["code more"] is None
    _press(drv, d, "pane 0 code more")
    drv.frame(DT)
    _click(drv, 900, 500)                        # outside the card: dismissed
    assert d.part_state["code more"] is None


def test_the_code_panes_file_tabs_switch_the_script(desk, monkeypatch):
    ws, drv = desk
    d = ws.editor_desk
    _tap(drv, ord("2"), 1)
    monkeypatch.setattr(ws, "code_sources", lambda: ["main.py", "enemies.py"])
    opened = []
    monkeypatch.setattr(ws, "open_code_file", lambda name: opened.append(name) or True)
    ws._dirty = True
    drv.frame(DT)
    bx, by, bw, bh = d._host.box("pane 0 files")
    _click(drv, bx + 110, by + bh // 2)          # the second tab
    assert opened == ["enemies.py"]


def test_a_settled_code_pane_paints_nothing_with_its_more_menu_open(desk):
    ws, drv = desk
    d = ws.editor_desk
    _tap(drv, ord("2"), 1)
    _press(drv, d, "pane 0 code more")
    ws.pointer.visible = False
    ws._toast_until = 0
    for _ in range(4):
        drv.frame(DT)
    drawn = ws._frames_drawn
    for _ in range(8):
        drv.frame(DT)
    assert ws._frames_drawn == drawn


def _code_ready(ws, drv):
    _tap(drv, ord("2"), 1)                      # Code: the Code pane focused
    ws.pointer.visible = False
    ws._toast_until = 0
    for _ in range(4):
        drv.frame(DT)
    d = ws.editor_desk
    b = d._own_pane()[4]
    return d, (b[0], b[1], b[0] + b[2], b[1] + b[3])


def test_a_drag_in_the_code_pane_draws_the_pane_alone(desk):
    """A press and its drag inside the focused Code pane reach its text
    alone: every frame of the drag draws that pane and names it as what
    changed, as a key's does."""
    ws, drv = desk
    d, pane = _code_ready(ws, drv)
    x, y = (pane[0] + pane[2]) // 2, (pane[1] + pane[3]) // 2
    drv.touch(x, y)
    drv.frame(DT)
    got = []
    for i in range(1, 8):
        drv.touch_drag(x, y - 12 * i)
        drv.frame(DT)
        got.append(d.changed())
    drv.touch_up()
    drv.frame(DT)
    assert got == [list(pane)] * len(got)


def test_a_key_in_the_code_pane_draws_straight_to_the_glass(desk):
    """A key frame of the Code pane draws into the glass, not the window's
    buffer and a stamp, and the glass then holds what a whole draw of the
    Editor makes: its full-screen row painted once, not over itself."""
    ws, drv = desk
    d, pane = _code_ready(ws, drv)
    win = ws.wm._wins["menu"]
    _tap(drv, ord("a"))                         # the first edit marks the bar's file
    for code in (ord("b"), 0x0A, ord("c"), ord("d")):
        drv.key_down(code)
        drv.frame(DT)
        assert d.changed() == list(pane)
        assert win._buf_stale                   # the buffer lags: no stamp ran
        drv.key_up(code)
        drv.frame(DT)
    shown = bytes(ws.glass_canvas._buf)
    d._surround = None                          # the next frame draws whole
    ws._dirty = True
    drv.frame(DT)
    assert d.changed() == [0, 0, ws.sys_canvas.w, ws.sys_canvas.h]
    assert bytes(ws.glass_canvas._buf) == shown


def _desk_at(path):
    ws = host_app.build_workstation(str(path / "carts"), sys_size=(1024, 600),
                                    panel_diagonal_in=7.0, font_scale=1, windowed=True)
    ws.open_desk()
    ws.open_in_editor(next(c for c in ws.carts.all if c.get("title") == "Sky Run"))
    ws.bar_layer._clock_text = lambda: "10:30"
    drv = host_app.ConsoleDriver(ws)
    for _ in range(3):
        drv.frame(DT)
    return ws, drv


def test_typing_on_a_root_of_two_buffers_draws_the_pane_alone_after_a_whole_frame(tmp_path):
    """On a root that presents two buffers in turn the buffer a frame
    paints lags the frame before: after a whole frame the Code pane's key
    frames come back to the pane alone within a frame or two (a lag beyond
    the pane goes to the window's buffer, not into the next whole draw),
    its text moves out of the last buffer only the rows the frames since
    drew, and every presented frame is the one a single buffer presents."""
    a, da = _desk_at(tmp_path / "a")
    b, db = _desk_at(tmp_path / "b")
    d, pane = _code_ready(a, da)
    _code_ready(b, db)
    for ws, drv in ((a, da), (b, db)):
        _tap(drv, ord("a"))
    root = a.wm._root_canvas
    root.RETAINED_FRAMES = 2
    held = [bytes(root._buf)]
    moved = []
    real = root.shift_last

    def shift_last(x, y, w, h, dx, dy):
        moved.append(h)
        return real(x, y, w, h, dx, dy)
    root.shift_last = shift_last

    def frame():
        n = a._frames_drawn
        da.frame(DT)
        db.frame(DT)
        if a._frames_drawn != n:
            shot = bytes(root._buf)
            assert shot == bytes(b.wm._root_canvas._buf), "frame %d" % a._frames_drawn
            held.append(shot)
            root._comp.last = shot
            root._buf[:] = held[-2]
    for ws in (a, b):
        ws.editor_desk._surround = None         # a whole frame
        ws._dirty = True
    frame()
    got = []
    for code in b"keys typed\nand more":
        for drv in (da, db):
            drv.key_down(code)
        frame()
        got.append(d.changed())
        for drv in (da, db):
            drv.key_up(code)
    assert got[2:] == [list(pane)] * (len(got) - 2), got
    assert moved and max(moved[-4:]) <= 2 * a.code_layout.lh, moved


def test_at_800_the_sound_panes_menu_picks_a_sound(tmp_path):
    ws = host_app.build_workstation(str(tmp_path / "carts"), sys_size=(800, 480),
                                    panel_diagonal_in=7.0, font_scale=1, windowed=True)
    ws.open_desk()
    ws.open_in_editor(next(c for c in ws.carts.all if c.get("title") == "Sky Run"))
    drv = host_app.ConsoleDriver(ws)
    for _ in range(3):
        drv.frame(DT)
    _tap(drv, ord("5"), 1)
    d = ws.editor_desk
    me = ws.music_ui.musicedit
    _press(drv, d, "pane 0 sound menu")
    drv.frame(DT)
    _press(drv, d, "1")
    assert me.sfx_idx == 1 and not d.part_state["menu"]


def test_the_waves_swatches_are_their_moy64_inks():
    from runtime import editor_sound as S
    from runtime.palette import MOY64
    for light, inks, rgbs in ((True, S.WAVE_INK_DAY, S._WAVE_RGB_DAY),
                              (False, S.WAVE_INK_NIGHT, S._WAVE_RGB_NIGHT)):
        for wv in range(8):
            r, g, b = MOY64[inks[wv]]
            assert rgbs[wv] == (r << 16) | (g << 8) | b
            assert S.wave_rgb({"surface_light": light}, wv) == rgbs[wv]


# -- E-02: the playtest's own map and scenes ------------------------------------------

def _play(drv):
    _tap(drv, 13, 1)                             # Ctrl+Enter: Play, or Restart
    for _ in range(4):
        drv.frame(DT)


def test_the_playtest_runs_on_its_own_map_and_scenes(desk):
    ws, drv = desk
    _tap(drv, ord("4"), 1)                       # World: the Scene beside the Run slot
    _play(drv)
    pr = ws.project
    assert ws.editor_app.playing()
    assert pr.play_map is not pr.tilemap and pr.play_map.cells == pr.tilemap.cells
    assert pr.play_scenes is not pr.scenes
    ns = ws.player.ns
    assert ns["_moy_scenes"] is pr.play_scenes
    was, mine = pr.play_map.mget(0, 0), pr.tilemap.mget(1, 0)
    pr.tilemap.mset(0, 0, was + 1)               # an edit in the Editor's map
    pr.play_map.mset(1, 0, mine + 1)             # the game's own mset
    drv.frame(DT)
    assert pr.play_map.mget(0, 0) == was         # waits for the next Play
    assert pr.tilemap.mget(1, 0) == mine         # never reaches the Editor


def test_a_scene_edit_during_the_playtest_waits_for_restart_and_wears_the_dot(desk):
    ws, drv = desk
    d = ws.editor_desk
    app = ws.editor_app
    _tap(drv, ord("4"), 1)
    _play(drv)
    pr = ws.project
    name = ws.scene_ui.scene_name
    se = ws.scene_ui.sceneedit
    se.sel = 0
    before = pr.play_scenes.raw(name)
    d._head_tool("scene:flip")                   # a scene edit while it runs
    assert pr.scenes.raw(name) != before and pr.play_scenes.raw(name) == before
    assert app.restart_owed and "scene" in app.start_owed
    _play(drv)                                   # Restart
    assert not app.restart_owed and app.start_owed == ()
    assert pr.play_scenes.raw(name) == pr.scenes.raw(name)


def test_play_takes_an_edited_map_in_place_and_keeps_an_unedited_runs_msets(desk):
    ws, _drv = desk
    pr = ws.project
    pr.play_world(True)
    pm = pr.play_map
    pm.mset(2, 0, 9)                             # the run's own change
    assert not pr.play_world() and pm.mget(2, 0) == 9 and pr.play_map is pm
    pr.tilemap.mset(3, 0, 4)                     # an Editor edit
    assert not pr.play_world() and pr.play_map is pm
    assert pm.cells == pr.tilemap.cells
    pr.tilemap.resize(pr.tilemap.w + 1, pr.tilemap.h)
    assert pr.play_world()                       # a new size: the run restarts


# -- the Run slot's pause ---------------------------------------------------------------

def test_the_run_slots_pause_holds_the_run_and_shows_its_still(desk):
    ws, drv = desk
    d = ws.editor_desk
    _tap(drv, ord("2"), 1)                       # Code: the Run slot
    _play(drv)
    win = ws.wm._wins["desktop"]
    _press(drv, d, "pane 1 pause")
    drv.frame(DT)
    assert d.paused and ws.player.paused and win.minimized
    assert d._still is not None                  # the still, dimmed under Play
    ws.pointer.visible = False
    ws._toast_until = 0
    for _ in range(3):
        drv.frame(DT)
    drawn = ws._frames_drawn
    for _ in range(8):
        drv.frame(DT)
    assert ws._frames_drawn == drawn, "a paused Run slot painted"
    gx, gy, gw, gh = d._still
    _click(drv, gx + gw // 2, gy + gh // 2)      # the still's Play goes on
    drv.frame(DT)
    assert not d.paused and not ws.player.paused and not win.minimized
    assert win.docked and ws.wm._order[-1] == "desktop"


def test_restart_and_stop_end_the_pause(desk):
    ws, drv = desk
    d = ws.editor_desk
    _tap(drv, ord("2"), 1)
    _play(drv)
    d.pause(True)
    drv.frame(DT)
    _play(drv)                                   # Ctrl+Enter: Restart
    assert not d.paused and not ws.player.paused and ws.wm._wins["desktop"].docked
    d.pause(True)
    drv.frame(DT)
    _tap(drv, ord("."), 1)                       # Stop
    drv.frame(DT)
    assert not d.paused and "desktop" not in ws.wm._wins


# -- the Sheet's loupe and the palette's chip -------------------------------------------

def test_a_press_held_on_the_sheet_shows_its_tile_in_a_3x_loupe(desk):
    ws, drv = desk
    d = ws.editor_desk
    _tap(drv, ord("3"), 1)                       # Art: the Sheet under the Map
    drv.frame(DT)
    (_pg, (x, y, w, h)) = d._sheet_rects[0]
    tw = w // 16
    drv.touch(x + 3 * tw + 2, y + 2)
    drv.frame(DT)
    assert d._loupe is not None and d._loupe[0] == 3
    ly = d._loupe[2]
    calls = []
    real = d._draw_loupe

    def spy(cv, th, u):
        real_sspr = cv.sspr

        def sspr(sheet, sx, sy, sw, sh, dx, dy, dw, dh, *a, **k):
            calls.append((dy, dw // sw))
            return real_sspr(sheet, sx, sy, sw, sh, dx, dy, dw, dh, *a, **k)
        cv.sspr = sspr
        try:
            real(cv, th, u)
        finally:
            del cv.sspr
    d._draw_loupe = spy
    ws._dirty = True
    drv.frame(DT)
    del d._draw_loupe
    assert [y for y, s in calls if s == 6 and y + 48 < ly], "no 3x loupe over the finger"
    drv.touch_up()
    drv.frame(DT)
    drv.frame(DT)
    assert d._loupe is None and ws.paint.n == 3


def test_the_sprite_panes_palette_wears_its_64_chip(desk):
    ws, drv = desk
    _tap(drv, ord("3"), 1)
    lay = ws.editor_desk._pane_layout("paint", ws.editor_desk._body(
        _pane(ws.editor_desk, "paint"), 1))
    x, y, w, h = lay.pal_chip
    assert (x, w) == (lay.sw_x0, 2 * lay.sw) and h >= 32
    assert y > lay.sw_y0 + 8 * lay.sw            # under the palette, beside the steppers
    assert y == lay.spr_prev[1]


# -- a header's tools that do not fit -----------------------------------------------------

def _scene_game(d):
    return next(n for n, (pane, _r, _p) in enumerate(d.panes()) if pane == ("scene", "game"))


def test_a_narrow_headers_tools_fold_into_its_more_menu(blocks_desk):
    ws, drv = blocks_desk
    d = ws.editor_desk
    n = _scene_game(d)
    pane, (x, y, w, h), _p = d.panes()[n]
    assert w == 340
    shown, folded = d._head_fit(pane, "scene", x, w, 1)
    assert folded and len(shown) + len(folded) == 6
    tx = d._tools_x[n]
    tabs_right = x + 4 + sum(ui.text_size(word)[0] + 28 for word in ("Scene", "Game"))
    assert tx >= tabs_right                      # no tool over the tabs
    se = ws.scene_ui.sceneedit
    se.sel = 0
    flip = bool(se.selected().get("flip"))
    _press(drv, d, "pane %d head more" % n)
    drv.frame(DT)
    assert "Flip" in [f[1] for f in folded]
    _press(drv, d, "Flip")
    assert bool(se.rows[0].get("flip")) != flip
    assert d.part_state.get("head more") is None
    for _n, (pane, r, _p) in enumerate(d.panes()):    # a wide header folds nothing
        if pane != ("scene", "game"):
            for c in pane:
                assert not d._head_fit(pane, c, r[0], r[2], 1)[1]


def test_a_settled_header_more_menu_paints_nothing(blocks_desk):
    ws, drv = blocks_desk
    d = ws.editor_desk
    _press(drv, d, "pane %d head more" % _scene_game(d))
    assert d.part_state.get("head more") is not None
    ws.pointer.visible = False
    ws._toast_until = 0
    for _ in range(4):
        drv.frame(DT)
    drawn = ws._frames_drawn
    for _ in range(8):
        drv.frame(DT)
    assert ws._frames_drawn == drawn


# -- the pad's rows -------------------------------------------------------------------------

class _Pad:
    """A pad's frame: `held` buttons down, `pressed` this frame's edges."""
    key_event = 0
    wheel_x = wheel_y = pointer_buttons = mods = 0

    def __init__(self, held, pressed):
        self._held, self._pressed = set(held) | set(pressed), set(pressed)

    def held(self, name):
        return name in self._held

    def steps(self, n):
        return int(bool(self.pressed(n)))

    def pressed(self, name):
        return name in self._pressed

    def clear_edges(self):
        self._pressed = set()


def test_select_and_the_d_pad_move_the_keys_by_direction_and_select_a_grows(desk):
    ws, drv = desk
    d = ws.editor_desk
    _tap(drv, ord("2"), 1)                       # Code | Run slot over the Reference
    names = [pane for pane, _r, _p in d.panes()]
    at = lambda: names[d._focus.get(d.key, 0)]   # noqa: E731
    d.focus_pane(names.index(("code",)))
    assert d.handle_input(_Pad({"select"}, {"right"}))
    assert at() == ("game",)
    assert d.handle_input(_Pad({"select"}, {"down"}))
    assert "log" in at()
    assert d.handle_input(_Pad({"select"}, {"left"}))
    assert at() == ("code",)
    d.handle_input(_Pad({"select"}, {"left"}))   # nothing that way
    assert at() == ("code",)
    assert d.handle_input(_Pad({"select"}, {"a"}))
    assert d.grown == ("code",)
    d.handle_input(_Pad({"select"}, {"a"}))
    assert d.grown is None


def test_select_start_plays_and_restarts_and_start_alone_is_the_console_key(desk, monkeypatch):
    ws, drv = desk
    d = ws.editor_desk
    _tap(drv, ord("2"), 1)
    inp = ws.input
    pad = {"held": {"select"}, "pressed": {"start"}}
    monkeypatch.setattr(inp, "held", lambda n, p=None: n in pad["held"] | pad["pressed"],
                        raising=False)
    monkeypatch.setattr(inp, "pressed", lambda n, p=None: n in pad["pressed"], raising=False)
    drv.frame(DT)
    pad["pressed"] = set()
    for _ in range(4):
        drv.frame(DT)
    assert ws.editor_app.playing()
    calls = []
    monkeypatch.setattr(ws.editor_app, "restart", lambda: calls.append(1))
    ws.wm._move_focus("menu")
    pad["pressed"] = {"start"}
    drv.frame(DT)
    pad["pressed"] = set()
    drv.frame(DT)
    assert calls == [1]
    opened = []
    monkeypatch.setattr(ws, "system_key", lambda: opened.append(1))
    pad["held"], pad["pressed"] = set(), {"start"}
    drv.frame(DT)
    assert opened == [1]


def test_quiet_frames_warm_the_other_workspaces_and_a_switch_draws_the_same(tmp_path, monkeypatch):
    """`EditorDesk.warm_step` (the console's quiet frames): every workspace
    not on show gets its arrangement placed and its tab panes laid out ahead,
    one a frame, so its first switch builds none of it -- and draws the same
    panes it draws unwarmed."""
    made = []
    real = P.Arrangement

    class Counted(real):
        def __init__(self, *a, **k):
            made.append(a[0])
            real.__init__(self, *a, **k)

    shots = {}
    for warm in (True, False):
        ws = host_app.build_workstation(str(tmp_path / str(warm)), sys_size=(1024, 600),
                                        panel_diagonal_in=7.0, font_scale=1, windowed=True)
        ws.open_desk()
        ws.open_in_editor(next(c for c in ws.carts.all if c.get("title") == "Sky Run"))
        drv = host_app.ConsoleDriver(ws)
        d = ws.editor_desk
        if not warm:
            d.warm_step = lambda: None
        _painted(ws, drv, 12)
        if warm:
            assert sorted(sig[0] for sig in d._warm) == ["art", "code", "sound", "world"]
        monkeypatch.setattr(P, "Arrangement", Counted)
        del made[:]
        for key in ("code", "art", "world", "sound"):
            d.switch(key)
            drv.frame(DT)
            buf = ws.wm._wins["menu"].buf
            shots[(warm, key)] = bytes(buf._buf[2 * buf.w * 46:])
        assert made == ([] if warm else ["code", "art", "world", "sound"])
        monkeypatch.setattr(P, "Arrangement", real)
    for key in ("code", "art", "world", "sound"):
        assert shots[(True, key)] == shots[(False, key)], key
