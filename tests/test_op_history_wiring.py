"""#111 phase 2 wiring: the Paint + Map editors run their in-RAM undo on the shared
op-history core, the #88 bar UNDO/REDO icons (ws.undo()/ws.redo()) route to the
ACTIVE tab's History before falling back to the durable journal walk, and a commit
embeds the fine-grained op batch in its journal.jsonl line.

Driven through the SAME shared console the device runs (runtime.host_app +
Workstation), so these assert host == device behavior. The boundary shipped is the
CLEAN one: in-RAM op steps until exhausted, THEN whole-commit journal steps (a
commit re-baselines the History, so the two never double-count a stroke)."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _make_ws_with_cart(tmp_path, src="def _draw():\n    cls(1)\n", title="Ops"):
    from runtime import host_app
    carts_dir = str(tmp_path / "carts")
    host_app.moy_carts.ensure_dirs(carts_dir)
    host_app.moy_carts.create(title, carts_dir, src=src, type="app")
    ws = host_app.build_workstation(carts_dir)
    for i, c in enumerate(ws.launcher.items):
        if c["title"] == title:
            ws.launcher.sel = i
            break
    ws.open_in_editor()
    return ws


def _entries(cart_path, file=None):
    from runtime import moy_carts
    ents = moy_carts._journal_load_entries(cart_path + "/journal/journal.jsonl")
    return [e for e in ents if file is None or e["file"] == file]


def _stroke(pe, x, y, color):
    """One brush stroke = one undo op (begin/paint/end)."""
    pe.color = color
    pe.begin_stroke()
    pe.paint(x, y)
    pe.end_stroke()


# -- paint: a stroke is ONE bar-undo step -----------------------------------

def test_paint_stroke_bar_undo_reverts_one_stroke(tmp_path):
    ws = _make_ws_with_cart(tmp_path)
    ws._open_paint()
    assert ws.menu_view == "paint"
    pe = ws.paint
    pe.n = 0
    ox, oy = ws.sheet.tile_origin(0)

    _stroke(pe, 0, 0, 5)                 # stroke A
    _stroke(pe, 1, 1, 7)                 # stroke B
    assert ws.sheet.pget(ox + 0, oy + 0) == 5
    assert ws.sheet.pget(ox + 1, oy + 1) == 7

    assert ws.undo() is True             # the bar UNDO reverts exactly ONE stroke (B)
    assert ws.sheet.pget(ox + 1, oy + 1) == 0
    assert ws.sheet.pget(ox + 0, oy + 0) == 5     # stroke A untouched

    assert ws.redo() is True             # ...and REDO re-lays B
    assert ws.sheet.pget(ox + 1, oy + 1) == 7


def test_paint_dimmed_state_is_truthful(tmp_path):
    # ws.can_undo()/can_redo() (the bar icons' enabled state) must track the active
    # paint History exactly -- a fresh sheet is dim both ways.
    ws = _make_ws_with_cart(tmp_path)
    ws._open_paint()
    pe = ws.paint
    pe.n = 0
    assert ws.can_undo() is False and ws.can_redo() is False

    _stroke(pe, 2, 2, 9)
    assert ws.can_undo() is True and ws.can_redo() is False

    assert ws.undo() is True
    # Only one local step existed and this cart has no earlier sprite commit, so undo
    # is now a floor; redo is armed.
    assert ws.can_undo() is False and ws.can_redo() is True


# -- ops land in the journal + round-trip -----------------------------------

def test_paint_commit_embeds_ops_in_journal(tmp_path):
    from runtime import moy_journal
    ws = _make_ws_with_cart(tmp_path)
    path = ws.cart["path"]
    ws._open_paint()
    pe = ws.paint
    pe.n = 0
    _stroke(pe, 0, 0, 5)
    _stroke(pe, 3, 4, 6)

    ws.save_sprites()                    # commit -> keyframe + the op batch

    ents = _entries(path, "sprites.moygfx")
    assert ents, "a sprite commit must journal"
    ops = moy_journal.journal_entry_ops(ents[-1])
    assert ops, "the commit line must carry the fine-grained op batch"
    # Each op is the JSON-able paint form ["p", n, size, spans]; round-trips as a list.
    for op in ops:
        assert op[0] == "p"
        assert isinstance(op[1], int) and isinstance(op[2], int)
        assert isinstance(op[3], list) and len(op[3]) % 3 == 0
    # And the batch drained: a re-commit with no new edit writes no ops key.
    assert pe._hist.peek() == []


def test_paint_commit_rebaselines_the_history(tmp_path):
    # The clean boundary: a commit clears the in-RAM stack (in-RAM undo covers edits
    # SINCE the last commit only), so the journal owns commit-to-commit.
    ws = _make_ws_with_cart(tmp_path)
    ws._open_paint()
    pe = ws.paint
    pe.n = 0
    _stroke(pe, 0, 0, 5)
    ws.save_sprites()
    assert pe.can_undo() is False        # re-baselined -- the stroke is now a commit


def test_paint_repeated_undo_walks_into_the_previous_commit(tmp_path):
    # Two committed strokes + a third uncommitted: local undo reverts the live
    # stroke, then the SAME bar UNDO crosses into the journal and walks whole commits.
    ws = _make_ws_with_cart(tmp_path)
    ws._open_paint()
    pe = ws.paint
    pe.n = 0
    ox, oy = ws.sheet.tile_origin(0)

    _stroke(pe, 0, 0, 5)
    ws.save_sprites()                    # commit V1: (0,0)=5
    _stroke(pe, 1, 1, 7)
    ws.save_sprites()                    # commit V2: (1,1)=7
    _stroke(pe, 2, 2, 9)                 # uncommitted live stroke: (2,2)=9

    assert ws.undo() is True             # local: revert the live stroke
    assert ws.sheet.pget(ox + 2, oy + 2) == 0
    assert ws.sheet.pget(ox + 1, oy + 1) == 7      # V2 content still present

    assert ws.undo() is True             # boundary crossed -> journal walks V2 -> V1
    # The reload rebuilt the editor over the restored (V1) sprite art.
    ox, oy = ws.sheet.tile_origin(0)
    assert ws.sheet.pget(ox + 1, oy + 1) == 0      # V2's stroke is gone
    assert ws.sheet.pget(ox + 0, oy + 0) == 5      # ...back to V1


# -- map: a paste is one undo step; ops land in the journal ------------------

def _open_map(ws):
    ws._open_map()
    assert ws.menu_view == "map"
    return ws.map_ui.mapedit


def test_map_paste_bar_undo_reverts_the_paste(tmp_path):
    ws = _make_ws_with_cart(tmp_path)
    me = _open_map(ws)
    tm = ws.tilemap
    for i in range(len(tm.cells)):       # blank slate
        tm.cells[i] = 0
    # Stamp a 2x2 block, copy it, paste elsewhere -- the paste is ONE undo step.
    me.n = 4
    me.begin_edit()
    me.place(0, 0)
    me.end_edit()
    me.set_selection(0, 0, 1, 1)
    me.copy_selection()
    assert me.paste(5, 5) is True
    assert tm.mget(5, 5) == 4

    assert ws.undo() is True             # the bar UNDO reverts the whole paste
    assert tm.mget(5, 5) == tm.EMPTY
    assert tm.mget(0, 0) == 4            # the original stamp survives
    assert ws.redo() is True
    assert tm.mget(5, 5) == 4


def test_map_commit_embeds_ops_in_journal(tmp_path):
    from runtime import moy_journal
    ws = _make_ws_with_cart(tmp_path)
    path = ws.cart["path"]
    me = _open_map(ws)
    tm = ws.tilemap
    me.n = 3
    me.begin_edit()
    me.place(2, 2)
    me.end_edit()

    ws.save_map()

    ents = _entries(path, "map.moymap")
    assert ents, "a map commit must journal"
    ops = moy_journal.journal_entry_ops(ents[-1])
    assert ops, "the map commit line must carry the op batch"
    # A small gesture stays the cheap delta form: a list of [idx, prev, new] triples
    # (op[0] != "snap" -- the whole-map snapshot form is only for a big flood/rect).
    op = ops[-1]
    assert isinstance(op, list) and op[0] != "snap"
    assert len(op[-1]) == 3              # a (idx, prev, new) triple


# ====================================================================# #111 phase 4: the CODE tab (a typing-burst codec) + the BLOCKS tab (structured
# whole-program ops) join the same unified bar undo -- local in-RAM steps first,
# then the durable journal, crossing the boundary transparently.
# ====================================================================
def _open_code(ws):
    ws.set_menu_view("code")
    assert ws.menu_view == "code"
    return ws.editor


def _type(ws, s, at_top=True):
    """Simulate a code typing BURST: open the burst, then insert text (left OPEN,
    exactly like mid-typing -- a bar UNDO closes it first). Inserts at the top by
    default so the buffer stays compilable (# comment lines)."""
    ed = ws.editor
    if at_top:
        ed.goto_row(0, 0)
    ws._code_burst_open()
    ed.insert_text(s)


# -- code: a typing burst is ONE bar-undo step ------------------------------

def test_code_burst_bar_undo_reverts_one_burst(tmp_path):
    ws = _make_ws_with_cart(tmp_path)
    ed = _open_code(ws)
    base = ed.text()

    _type(ws, "# hello\n")                # one live burst
    assert "# hello" in ed.text()

    assert ws.undo() is True             # the bar UNDO closes + reverts the whole burst
    assert ed.text() == base
    assert "# hello" not in ed.text()

    assert ws.redo() is True             # ...and REDO re-lays it
    assert "# hello" in ed.text()


def test_code_dimmed_state_is_truthful(tmp_path):
    ws = _make_ws_with_cart(tmp_path)
    ed = _open_code(ws)
    assert ws.can_undo() is False and ws.can_redo() is False

    _type(ws, "# x\n")                    # a LIVE (unrecorded) burst still dims-in undo
    assert ws.can_undo() is True and ws.can_redo() is False

    assert ws.undo() is True
    # Only one burst existed and this cart has no earlier code commit, so undo is a
    # floor now; redo is armed.
    assert ws.can_undo() is False and ws.can_redo() is True


def test_code_commit_embeds_ops_in_journal(tmp_path):
    from runtime import moy_journal
    ws = _make_ws_with_cart(tmp_path)
    path = ws.cart["path"]
    ed = _open_code(ws)

    _type(ws, "# note\n")
    ws.save_code()                       # commit -> closes burst, drains the op batch

    ents = _entries(path, "main.py")
    assert ents, "a code commit must journal"
    ops = moy_journal.journal_entry_ops(ents[-1])
    assert ops, "the code commit line must carry the fine-grained op batch"
    op = ops[-1]
    assert op[0] == "edit"               # ("edit", pos, deleted, inserted)
    assert isinstance(op[1], int)
    assert "# note" in op[3]             # the inserted text rode the journal line
    # And the batch drained + re-baselined: the in-RAM stack is clear after commit.
    assert ws._code_op_history().peek() == []
    assert ws._code_op_history().can_undo() is False   # in-RAM stack re-baselined


def test_code_undo_walks_into_previous_commit(tmp_path):
    ws = _make_ws_with_cart(tmp_path)
    ed = _open_code(ws)

    _type(ws, "# one\n")
    ws.save_code()                       # commit V1 (re-baselines the History)
    _type(ws, "# two\n")
    ws.save_code()                       # commit V2
    _type(ws, "# three\n")               # a live, uncommitted burst

    assert ws.undo() is True             # local: revert the live burst
    assert "# three" not in ws.editor.text()
    assert "# two" in ws.editor.text()

    assert ws.undo() is True             # boundary crossed -> journal walks V2 -> V1
    # The reload rebuilt the editor over the restored (V1) source.
    assert "# two" not in ws.editor.text()
    assert "# one" in ws.editor.text()


def test_code_ops_do_not_disturb_graduation(tmp_path):
    # A code commit that carries an op batch must journal graduation exactly as
    # before: a plain code-only cart never graduates, and its manifest stays put.
    ws = _make_ws_with_cart(tmp_path)
    ed = _open_code(ws)
    _type(ws, "# free edit\n")
    ws.save_code()
    assert bool(ws.cart.get("graduated")) is False


# -- blocks: structured whole-program ops on the same bar undo ---------------

def _open_blocks(ws):
    ws.set_menu_view("blocks")
    assert ws.menu_view == "blocks"
    return ws.block_ui.blocks_ed


def _go_to_insert(be, depth=1, which=-1):
    found = [i for i, r in enumerate(be.rows) if r.kind == "insert" and r.depth == depth]
    assert found, "no insert row at depth %d" % depth
    be.cur = found[which]


def _select_type(be, tid):
    for i, r in enumerate(be.rows):
        if (r.block or {}).get("t") == tid:
            be.cur = i
            return True
    return False


def test_blocks_add_delete_param_bar_undo(tmp_path):
    from runtime import blocks
    ws = _make_ws_with_cart(tmp_path)
    be = _open_blocks(ws)

    # ADD a block -> bar UNDO removes it.
    s0 = blocks_snapshot(be)
    _go_to_insert(be, 1)
    be.insert_block("cls", {"color": "red"})
    assert be.program != s0
    assert ws.undo() is True and be.program == s0
    assert ws.redo() is True and be.program != s0

    # PARAM edit -> bar UNDO reverts the slot.
    assert _select_type(be, "cls")
    s1 = blocks_snapshot(be)
    be.set_slot("color", "green")
    assert be.program != s1
    assert ws.undo() is True and be.program == s1

    # DELETE -> bar UNDO brings the block back.
    assert _select_type(be, "cls")
    s2 = blocks_snapshot(be)
    assert be.delete() is True and be.program != s2
    assert ws.undo() is True and be.program == s2


def test_blocks_dimmed_state_is_truthful(tmp_path):
    ws = _make_ws_with_cart(tmp_path)
    be = _open_blocks(ws)
    assert ws.can_undo() is False and ws.can_redo() is False

    _go_to_insert(be, 1)
    be.insert_block("cls", {"color": "red"})   # an un-sealed edit still dims-in undo
# -- scene: a placement is one undo step; undo re-syncs ws.scenes (#111 phase 4) --

def _open_scene(ws):
    ws._open_scene()
    assert ws.menu_view == "scene"
    return ws.scene_ui.sceneedit


def test_scene_place_bar_undo_removes_it_and_syncs_live(tmp_path):
    ws = _make_ws_with_cart(tmp_path)
    se = _open_scene(ws)
    name = ws.scene_ui.scene_name
    se.place(40, 40)
    ws.scene_ui._sync_live()             # the same call every committed gesture makes
    assert len(se.rows) == 1
    assert len(ws.scenes.scene(name)) == 1

    assert ws.undo() is True             # the bar UNDO reverts the placement...
    assert len(se.rows) == 0
    assert len(ws.scenes.scene(name)) == 0     # ...and the LIVE scene() sees it too
    # (undo() drives console._after_local_history -> scene_ui._sync_live(), the
    # scene-specific tail every other tab's generic "mutated in place" path doesn't need)

    assert ws.redo() is True
    assert len(se.rows) == 1
    assert len(ws.scenes.scene(name)) == 1


def test_scene_dimmed_state_is_truthful(tmp_path):
    ws = _make_ws_with_cart(tmp_path)
    se = _open_scene(ws)
    assert ws.can_undo() is False and ws.can_redo() is False

    se.place(8, 8)
    assert ws.can_undo() is True and ws.can_redo() is False

    assert ws.undo() is True
    assert ws.can_undo() is False and ws.can_redo() is True


def test_scene_commit_embeds_ops_in_journal(tmp_path):
    from runtime import moy_journal
    ws = _make_ws_with_cart(tmp_path)
    path = ws.cart["path"]
    se = _open_scene(ws)
    se.place(16, 16)
    se.place(32, 32)

    ws.save_scene()

    ents = _entries(path, "scenes/main.moyscene")
    assert ents, "a scene commit must journal"
    ops = moy_journal.journal_entry_ops(ents[-1])
    assert ops, "the scene commit line must carry the op batch"
    for op in ops:
        assert op["t"] == "place"
    assert se._hist.peek() == []         # the batch drained


# -- music: a step edit is one undo step; ops land in the journal (#111 phase 4) --

def _open_music(ws):
    ws._open_music()
    assert ws.menu_view == "music"
    return ws.music_ui.musicedit


def test_music_step_bar_undo_reverts_pitch(tmp_path):
    ws = _make_ws_with_cart(tmp_path)
    me = _open_music(ws)
    me.select_cursor(0)
    p0 = me.cur_step()[0]

    me.set_pitch(50)
    assert me.cur_step()[0] == 50

    assert ws.undo() is True             # the bar UNDO reverts the pitch edit
    assert me.cur_step()[0] == p0

    assert ws.redo() is True
    assert me.cur_step()[0] == 50


def test_music_dimmed_state_is_truthful(tmp_path):
    ws = _make_ws_with_cart(tmp_path)
    me = _open_music(ws)
    assert ws.can_undo() is False and ws.can_redo() is False

    me.nudge_vol(1)
    assert ws.can_undo() is True and ws.can_redo() is False

    assert ws.undo() is True
    assert ws.can_undo() is False and ws.can_redo() is True


def test_graduated_blocks_tab_has_no_history(tmp_path):
    ws = _make_ws_with_cart(tmp_path)
    be = _open_blocks(ws)
    _go_to_insert(be, 1)
    be.insert_block("cls", {"color": "red"})   # an edit that WOULD be undoable

    # Graduated: the Blocks tab is a frozen read-only render, so its History is
    # ABSENT -- the registry returns None and the bar never routes into it.
    ws.block_ui.blk_graduated = True
    assert ws.project.history_for("blocks") is None
    assert ws._active_history() is None
    # can_undo now consults only the journal (no in-RAM block history to report).
    assert ws.can_undo() is False


def blocks_snapshot(be):
    from runtime.editors import _clone_tree
    return _clone_tree(be.program)
def test_music_commit_embeds_ops_in_journal(tmp_path):
    from runtime import moy_journal
    ws = _make_ws_with_cart(tmp_path)
    path = ws.cart["path"]
    me = _open_music(ws)
    me.select_cursor(0)
    me.set_pitch(60)
    me.cycle_wave(1)

    ws.save_sounds()

    ents = _entries(path, "sounds.json")
    assert ents, "a sounds commit must journal"
    ops = moy_journal.journal_entry_ops(ents[-1])
    assert ops, "the sounds commit line must carry the op batch"
    # Each op is the JSON-able [old_snap, new_snap] pair (_MusicOps).
    for op in ops:
        assert isinstance(op, list) and len(op) == 2
    assert me._hist.peek() == []         # the batch drained


# -- config: a field adjust is one undo step; ops land in the journal (#111 phase 4)
#
# NOTE: the CART INFO modal's title/author edits (#94, Project.commit_manifest)
# deliberately stay OUT of this history -- they're manifest metadata (a small
# immediate-commit modal), not config.json content, and manifest.json already
# carries its own one-way-door graduation journal riders; folding plain
# metadata edits into the config op-history would blur the two (see
# commit_manifest's docstring in project.py).

def _make_ws_with_config_cart(tmp_path, title="Cfg"):
    from runtime import host_app
    carts_dir = str(tmp_path / "carts")
    host_app.moy_carts.ensure_dirs(carts_dir)
    host_app.moy_carts.create(
        title, carts_dir, src="def _draw():\n    cls(1)\n", type="game",
        edit=[{"key": "spd", "type": "int", "min": 0, "max": 10, "default": 3,
              "card": "SPD {value}"}])
    ws = host_app.build_workstation(carts_dir)
    for i, c in enumerate(ws.launcher.items):
        if c["title"] == title:
            ws.launcher.sel = i
            break
    ws.open_in_editor()
    assert ws.menu_view == "cards"
    return ws


def test_config_adjust_bar_undo_reverts_field(tmp_path):
    ws = _make_ws_with_config_cart(tmp_path)
    ws.cards_layer.msel = 0
    assert ws.config.get("spd", 3) == 3

    ws.adjust(1)
    assert ws.config["spd"] == 4

    assert ws.undo() is True             # the bar UNDO reverts the field
    assert ws.config["spd"] == 3

    assert ws.redo() is True
    assert ws.config["spd"] == 4


def test_config_dimmed_state_is_truthful(tmp_path):
    ws = _make_ws_with_config_cart(tmp_path)
    assert ws.can_undo() is False and ws.can_redo() is False

    ws.cards_layer.msel = 0
    ws.adjust(1)
    assert ws.can_undo() is True and ws.can_redo() is False

    assert ws.undo() is True
    assert ws.can_undo() is False and ws.can_redo() is True


def test_config_commit_embeds_ops_in_journal(tmp_path):
    from runtime import moy_journal
    ws = _make_ws_with_config_cart(tmp_path)
    path = ws.cart["path"]
    ws.cards_layer.msel = 0
    ws.adjust(1)
    ws.adjust(1)

    ws._save_config()

    ents = _entries(path, "config.json")
    assert ents, "a config commit must journal"
    ops = moy_journal.journal_entry_ops(ents[-1])
    assert ops, "the config commit line must carry the op batch"
    for op in ops:
        assert op["k"] == "spd"
    assert ws.project.config_hist.peek() == []   # the batch drained


# -- the bar's cached strip must un-dim UNDO on the very next frame ----------

def test_bar_cache_key_tracks_local_history(tmp_path):
    # The user-visible bug this pins: paint a stroke -> the dirty star appears but
    # the bar's UNDO icon stayed dimmed, because the cached strip's key had no
    # undo-state component (it only rebuilt on a clock tick / zone change). The
    # key must now change as soon as the local History gains a step -- and again
    # when undo empties it.
    ws = _make_ws_with_cart(tmp_path)
    ws._open_paint()
    pe = ws.paint
    pe.n = 0
    before = ws.bar_layer._cart_bar_key()
    _stroke(pe, 3, 3, 6)
    after = ws.bar_layer._cart_bar_key()
    assert before != after                      # stroke -> key changes -> strip re-renders
    assert ws.can_undo() is True
    ws.undo()
    assert ws.bar_layer._cart_bar_key() != after   # redo now possible -> changes again


def test_bar_undo_bits_are_ram_only_off_editor(tmp_path):
    # On the launcher (no active editor tab) the bits are constant False/False --
    # and crucially computing them never touches the SD-backed journal check.
    ws = _make_ws_with_cart(tmp_path)
    ws.go_home()
    assert ws._bar_undo_bits() == (False, False)


# ==================================================================#
# #111 owner decision: the journal fallback walk is scoped to the ACTIVE TAB's
# file(s), so a bar undo on one tab never reverts another tab's newest commit and
# REDO only lights on the tab that actually has something ahead.
# ==================================================================#

def test_active_tab_files_maps_each_tab(tmp_path):
    # The tab -> journal-file-set table the scoped walk routes through.
    ws = _make_ws_with_cart(tmp_path)
    ws.set_menu_view("code")
    assert ws._active_tab_files() == ("main.py",)
    ws.set_menu_view("paint");  assert ws._active_tab_files() == ("sprites.moygfx",)
    ws.set_menu_view("map");    assert ws._active_tab_files() == ("map.moymap",)
    ws.set_menu_view("music");  assert ws._active_tab_files() == ("sounds.json",)
    ws.set_menu_view("blocks"); assert ws._active_tab_files() == ("blocks.json", "main.py")
    ws._open_scene()
    assert ws._active_tab_files() == ("scenes/main.moyscene",)
    # off the Editor (launcher) there is no scoped set -> whole-project (None)
    ws.go_home()
    assert ws._active_tab_files() is None


def test_bar_undo_on_code_tab_never_reverts_the_map_commit(tmp_path):
    # The reported symptom, end to end: commit code TWICE and the map ONCE, then a bar
    # UNDO on the CODE tab must revert the code (never the map, though the map committed
    # with the higher seq), and the map tab's REDO must stay DIMMED.
    ws = _make_ws_with_cart(tmp_path)
    ed = _open_code(ws)
    _type(ws, "# c1\n"); ws.save_code()          # main.py commit 1
    _type(ws, "# c2\n"); ws.save_code()          # main.py commit 2

    me = _open_map(ws)                           # (tab switch hard-commits code -- deduped)
    tm = ws.tilemap
    me.n = 5
    me.begin_edit(); me.place(6, 6); me.end_edit()
    ws.save_map()                                # map.moymap commit (the NEWEST commit)
    assert tm.mget(6, 6) == 5

    # Back on the code tab: the bar UNDO walks main.py (its own timeline), not the map.
    ws.set_menu_view("code")
    assert ws.undo() is True
    assert "# c2" not in ws.editor.text()        # code stepped c2 -> c1
    assert "# c1" in ws.editor.text()
    assert ws.tilemap.mget(6, 6) == 5            # the MAP is untouched

    # Redo dim is per-tab: the code tab can redo (it was just rewound); after switching
    # to the map tab, REDO is dimmed (the map has nothing ahead).
    assert ws.can_redo() is True                 # (still on code)
    ws.set_menu_view("map")
    assert ws.can_redo() is False                # the map tab's REDO stays dark


def test_graduated_blocks_tab_undo_ungraduates_under_scoped_walk(tmp_path):
    # Blocks pair walks in step: blocks.json is not itself journaled (block saves write
    # it straight to disk), so the blocks filter ("blocks.json","main.py") reaches the
    # main.py graduating commit -- a single bar UNDO on the FROZEN Blocks tab restores
    # the block-generated baseline AND un-graduates, in one press.
    from runtime import host_app, moy_carts, blocks
    root = str(tmp_path / "carts")
    moy_carts.ensure_dirs(root)
    ws = host_app.build_workstation(root)
    cart = moy_carts.create("Grad", root, type="game")
    prog = {"vars": ["score"], "scripts": [
        blocks.make_block("on_draw", children=[
            blocks.make_block("cls", {"color": "black"}),
            blocks.make_block("set_var", {"var": "score", "value": 7})])]}
    assert moy_carts.save_blocks(cart, prog)[0] == moy_carts.SAVE_OK
    ws.launcher.items = moy_carts.scan(root)
    for i, c in enumerate(ws.launcher.items):
        if c["title"] == "Grad":
            ws.launcher.sel = i
    ws.open()
    path = ws.cart["path"]

    # Graduate via a diverging CODE commit.
    ws.set_menu_view("code"); ws.screen = "menu"
    ws.editor.set_text(ws.cart["src"].replace("score = 7", "score = 999"))
    assert ws.save_code() is True
    assert ws.cart["graduated"] is True

    # Open the (now frozen, History-less) Blocks tab and press the bar UNDO ONCE.
    ws._open_blocks()
    assert ws.menu_view == "blocks"
    assert ws.project.history_for("blocks") is None       # no in-RAM history: journal walk
    assert ws.undo() is True
    assert "score = 999" not in (Path(path) / "main.py").read_text()
    assert ws.cart["graduated"] is False                  # un-graduated in ONE press
    assert moy_carts.load(path)["graduated"] is False
