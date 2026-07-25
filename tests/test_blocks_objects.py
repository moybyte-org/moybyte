"""Per-object scripts (#85/#93, Phase 2): each scene object/tag has its OWN block
scripts, compiled to a `for _self in actors("<tag>"): <body>` loop with `self`
bound to each live actor (Scratch's sprite-scripts model over the #109 SceneWorld).

Covers the compiler (emission, auto draw_scene, byte-identical when absent,
round-trip/graduation, prune) and the BlockEditor per-target root.
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from runtime import blocks as B                      # noqa: E402
from runtime import widgets                          # noqa: E402
from runtime.editors_block import BlockEditor        # noqa: E402

mb = B.make_block


def _prog_with_objects():
    return {
        "vars": ["score"],
        "scripts": [mb("on_start"), mb("on_update"),
                    mb("on_draw", children=[mb("cls", {"c": 0})])],
        "objects": [
            {"tag": "player", "scripts": [
                mb("on_start", children=[mb("set_var", {"var": "score", "value": 0})]),
                mb("on_update", children=[mb("move_actor_by", {"dx": 2, "dy": 0})]),
                mb("on_draw")]},
            {"tag": "coin", "scripts": [
                mb("on_update", children=[mb("remove_actor")])]},
        ],
    }


# -- compiler -----------------------------------------------------------------

def test_object_scripts_emit_actor_loops():
    src = B.compile_blocks(_prog_with_objects())
    assert 'for _self in actors("player"):' in src
    assert 'for _self in actors("coin"):' in src
    assert "move_actor(_self, 2, 0)" in src
    assert "remove_actor(_self)" in src


def test_object_var_assignment_hoists_global():
    src = B.compile_blocks(_prog_with_objects())
    # `score = 0` inside the player on_start loop -> _init must declare it global.
    init = src.split("def _init():", 1)[1].split("def ", 1)[0]
    assert "global score" in init
    assert 'for _self in actors("player"):' in init


def test_auto_draw_scene_when_objects_present_and_not_manual():
    src = B.compile_blocks(_prog_with_objects())
    assert "draw_scene()" in src
    # ...but never doubled when the kid already added a draw scene block.
    prog = _prog_with_objects()
    prog["scripts"][2]["c"].append(mb("draw_scene"))
    src2 = B.compile_blocks(prog)
    assert src2.count("draw_scene()") == 1


def test_no_objects_is_byte_identical():
    base = {"vars": [], "scripts": [mb("on_start"), mb("on_update"), mb("on_draw")]}
    with_empty = dict(base); with_empty["objects"] = []
    assert B.compile_blocks(base) == B.compile_blocks(with_empty)
    # and a legacy program (no "objects" key at all) is unaffected.
    legacy = {"scripts": [mb("on_update", children=[mb("cls", {"c": 0})])]}
    assert "actors(" not in B.compile_blocks(legacy)
    assert "draw_scene()" not in B.compile_blocks(legacy)


def test_object_program_round_trips_for_graduation():
    prog = _prog_with_objects()
    src = B.compile_blocks(prog)
    assert B.source_roundtrips(prog, src)          # cosmetic-identical -> stays blocks


def test_blank_and_duplicate_tags_are_dropped():
    prog = {"scripts": [], "objects": [
        {"tag": "", "scripts": [mb("on_update", children=[mb("remove_actor")])]},
        {"tag": "e", "scripts": [mb("on_update", children=[mb("move_actor_by", {"dx": 1, "dy": 1})])]},
        {"tag": "e", "scripts": [mb("on_update", children=[mb("remove_actor")])]},
    ]}
    objs = B.collect_objects(prog)
    assert [o["tag"] for o in objs] == ["e"]        # blank dropped, dup 'e' first-wins
    src = B.compile_blocks(prog)
    assert src.count('for _self in actors("e"):') == 1


def test_object_can_run_against_live_world():
    # Compile + exec against a real SceneWorld and confirm behavior per actor.
    src = B.compile_blocks(_prog_with_objects())
    scenes = widgets.Scenes({"main": json.dumps([
        {"tag": "player", "tile": 1, "x": 10, "y": 10},
        {"tag": "coin", "tile": 2, "x": 50, "y": 50},
        {"tag": "coin", "tile": 2, "x": 60, "y": 60}])}, ["main"])
    world = scenes.world()
    ns = {"actors": world.actors, "move_actor": world.move,
          "remove_actor": world.remove, "move_actor_to": world.move_to,
          "touching": world.touching, "col": lambda n: 0, "cls": lambda c: None,
          "draw_scene": lambda: None, "spr": lambda *a, **k: None}
    exec(compile(src, "<t>", "exec"), ns)
    ns["_init"]()
    ns["_update"](0.016)
    players = world.actors("player")
    assert (players[0].x, players[0].y) == (12, 10)     # moved by its own script
    assert world.actors("coin") == []                   # both coins removed themselves


# -- Scratch self-motion / sensing vocabulary (#85/#93) -----------------------

def test_self_motion_blocks_compile_to_none_safe_helpers():
    prog = {"scripts": [], "objects": [{"tag": "ball", "scripts": [
        mb("on_start", children=[mb("set_my_x", {"x": 100}), mb("set_my_y", {"y": 50})]),
        mb("on_update", children=[
            mb("change_my_x", {"dx": 2}),
            mb("if", {"cond": mb("touching_edge")}, children=[mb("set_my_x", {"x": 0})]),
        ])]}]}
    src = B.compile_blocks(prog)
    for helper in ("def _setax(", "def _chax(", "def _atedge("):
        assert helper in src                          # emitted only because used
    assert "_setax(_self, 100)" in src and "_chax(_self, 2)" in src
    assert "if _atedge(_self):" in src
    # helpers are gated: an unused one is never emitted
    assert "def _ay(" not in src


def test_self_motion_runs_and_is_none_safe():
    prog = {"scripts": [
        # a STAGE (no `self`) set_my_x must NOT crash -- it no-ops on None
        mb("on_start", children=[mb("set_my_x", {"x": 999})])],
        "objects": [{"tag": "ball", "scripts": [
            mb("on_start", children=[mb("set_my_x", {"x": 100}), mb("set_my_y", {"y": 20})]),
            mb("on_update", children=[mb("change_my_x", {"dx": 5})])]}]}
    src = B.compile_blocks(prog)
    scenes = widgets.Scenes({"main": json.dumps([
        {"tag": "ball", "tile": 1, "x": 0, "y": 0}])}, ["main"])
    world = scenes.world()
    ns = {"actors": world.actors, "move_actor": world.move, "remove_actor": world.remove,
          "move_actor_to": world.move_to, "touching": world.touching,
          "draw_scene": lambda: None, "col": lambda n: 0, "cls": lambda c: None}
    exec(compile(src, "<t>", "exec"), ns)
    ns["_init"]()                                     # the None set_my_x on the Stage no-ops
    ns["_update"](0.016)
    ball = world.actors("ball")[0]
    assert (ball.x, ball.y) == (105, 20)              # set to (100,20) then +5 x


# -- motion depth (direction / steps / bounce) --------------------------------

def test_motion_depth_compiles_and_runs():
    prog = {"scripts": [], "objects": [{"tag": "ball", "scripts": [
        mb("on_start", children=[mb("point_dir", {"d": 90})]),
        mb("on_update", children=[mb("move_steps", {"n": 5}), mb("bounce_edge")])]}]}
    src = B.compile_blocks(prog)
    assert "import math" in src                       # move steps needs trig
    assert "_movesteps(_self, 5)" in src and "_bounce(_self)" in src
    scenes = widgets.Scenes({"main": json.dumps([
        {"tag": "ball", "tile": 1, "x": 100, "y": 100}])}, ["main"])
    world = scenes.world()
    ns = {"actors": world.actors, "move_actor": world.move, "remove_actor": world.remove,
          "move_actor_to": world.move_to, "touching": world.touching,
          "draw_scene": lambda: None}
    exec(compile(src, "<t>", "exec"), ns)
    ns["_init"](); ns["_update"](0.016)
    ball = world.actors("ball")[0]
    assert ball.x == 105 and ball.y == 100           # dir 90 (right) -> +5 x


# -- looks (show/hide/size/say via draw_scene) --------------------------------

def _rec_api(scene_rows):
    from runtime import host_app, input as inp, canvas as canv
    calls = []

    class Rec(canv.Canvas):
        def spr_tile(self, sheet, n, x, y, ck, scale, flip):
            calls.append(("spr", n, scale, flip))

        def spr(self, img, x, y, scale=1, flip=0):
            calls.append(("rotspr", getattr(img, "w", 0), getattr(img, "h", 0)))

        def rect(self, *a):
            calls.append(("rect",) + a)

        def rectb(self, *a):
            calls.append(("rectb",) + a)

        def print(self, *a, **k):
            calls.append(("print",) + a)

    class Sheet:
        TILE = 8
        count = 16

        def tile_image(self, n, transparent=-1):
            return canv.Image(8, 8, [n] * 64, transparent)

        def tile_span_image(self, *a):
            return None

    scenes = widgets.Scenes({"main": json.dumps(scene_rows)}, ["main"])
    ns = host_app.make_api(Rec(320, 240), inp.InputState(), {}, sheet=Sheet(),
                           audio=None, scenes=scenes)
    return ns, scenes.world(), calls


def test_looks_flags_honoured_by_draw_scene():
    ns, world, calls = _rec_api([
        {"tag": "a", "tile": 1, "x": 50, "y": 50},
        {"tag": "b", "tile": 2, "x": 80, "y": 80},
        {"tag": "c", "tile": 3, "x": 100, "y": 100}])
    for act in world.actors():
        if act.tag == "b":
            act.flags["hidden"] = True
        if act.tag == "c":
            act.flags["size"] = 200
        if act.tag == "a":
            act.flags["say"] = "hi"
    ns["draw_scene"]()
    sprs = {c[1]: c for c in calls if c[0] == "spr"}   # tile -> ("spr", tile, scale, flip)
    assert 2 not in sprs                             # hidden 'b' not drawn
    assert sprs[3][2] == 2                            # 'c' size 200% -> scale 2
    assert sprs[1][2] == 1                            # 'a' normal -> scale 1
    assert any(c[0] == "print" and c[1] == "hi" for c in calls)   # say bubble


def test_direction_all_around_rotates_the_sprite():
    # Default rotation style is "all around": a directed sprite is drawn ROTATED
    # (an Image blit via canvas.spr), not as a plain sheet tile.
    ns, world, calls = _rec_api([
        {"tag": "P", "tile": 1, "x": 40, "y": 40},
        {"tag": "N", "tile": 2, "x": 60, "y": 60}])
    for a in world.actors():
        if a.tag == "P":
            a.flags["dir"] = 180            # point down -> 90 deg rotation
    ns["draw_scene"]()
    assert any(c[0] == "rotspr" for c in calls)          # P drew rotated
    assert any(c[0] == "spr" and c[1] == 2 for c in calls)   # N (no dir) plain tile


def test_left_right_rotation_style_flips():
    ns, world, calls = _rec_api([
        {"tag": "L", "tile": 1, "x": 10, "y": 10},
        {"tag": "R", "tile": 2, "x": 30, "y": 30}])
    for a in world.actors():
        a.flags["rot"] = "leftright"        # Scratch left-right style
        if a.tag == "L":
            a.flags["dir"] = -90            # point left -> mirror
        if a.tag == "R":
            a.flags["dir"] = 90             # point right
    ns["draw_scene"]()
    flip = {c[1]: c[3] for c in calls if c[0] == "spr"}
    assert flip[1] == 1                     # pointing left -> mirrored
    assert flip[2] == 0                     # pointing right -> not


# -- events (key / broadcast / receive) ---------------------------------------

def test_on_key_compiles_to_btnp_guard():
    prog = {"scripts": [], "objects": [{"tag": "hero", "scripts": [
        mb("on_key", {"key": "left"}, children=[mb("change_my_x", {"dx": -3})])]}]}
    src = B.compile_blocks(prog)
    assert 'if btnp("left"):' in src
    assert "_chax(_self, -3)" in src


def test_broadcast_and_receive_pump_and_deliver():
    prog = {"scripts": [
        mb("on_update", children=[mb("broadcast", {"msg": "win"})])],
        "objects": [{"tag": "flag", "scripts": [
            mb("on_receive", {"msg": "win"}, children=[mb("say", {"text": "yay"})])]}]}
    src = B.compile_blocks(prog)
    assert "_pump_msgs()" in src
    assert 'if _received("win"):' in src
    scenes = widgets.Scenes({"main": json.dumps([
        {"tag": "flag", "tile": 1, "x": 0, "y": 0}])}, ["main"])
    world = scenes.world()
    said = []
    ns = {"actors": world.actors, "move_actor": world.move, "remove_actor": world.remove,
          "move_actor_to": world.move_to, "touching": world.touching,
          "draw_scene": lambda: None}
    exec(compile(src, "<t>", "exec"), ns)
    ns["_update"](0.016)                              # frame 1: broadcast queued
    assert not world.actors("flag")[0].flags.get("say")
    ns["_update"](0.016)                              # frame 2: delivered
    assert world.actors("flag")[0].flags.get("say") == "yay"


# -- prune --------------------------------------------------------------------

def test_prune_drops_empty_objects_only():
    prog = {"scripts": [], "objects": [
        {"tag": "used", "scripts": [mb("on_update", children=[mb("remove_actor")])]},
        {"tag": "empty", "scripts": [mb("on_start"), mb("on_update"), mb("on_draw")]},
    ]}
    pruned = B.prune_empty_objects(prog)
    assert [o["tag"] for o in pruned["objects"]] == ["used"]
    # original untouched (the live editor keeps its entries)
    assert len(prog["objects"]) == 2
    # all-empty -> the key is removed entirely (byte-identical output)
    allempty = {"scripts": [], "objects": [{"tag": "x", "scripts": [mb("on_update")]}]}
    assert "objects" not in B.prune_empty_objects(allempty)


# -- BlockEditor per-target root ----------------------------------------------

def test_set_target_materializes_object_and_flattens_its_scripts():
    be = BlockEditor(B)
    be.set_target("enemy")
    assert be.target == "enemy"
    objs = be.program["objects"]
    assert objs[0]["tag"] == "enemy"
    # a sprite carries the standard event hats, incl. "when I'm tapped" (on_tap)
    assert [h["t"] for h in objs[0]["scripts"]] == \
        ["on_start", "on_update", "on_draw", "on_tap"]
    # the outline now flattens the enemy's hats, NOT the Stage scripts
    assert any(r.kind == "block" and r.block.get("t") == "on_update" for r in be.rows)


def test_older_sprite_gains_when_tapped_on_open():
    # a sprite authored before on_tap existed gets it (never losing existing scripts)
    be = BlockEditor(B)
    be.program = {"scripts": [], "objects": [{"tag": "coin", "scripts": [
        mb("on_update", children=[mb("remove_actor")])]}]}
    be.set_target("coin")
    tids = [h["t"] for h in be.program["objects"][0]["scripts"]]
    assert "on_tap" in tids
    assert tids.count("on_update") == 1              # existing script untouched


def test_on_tap_compiles_to_a_per_sprite_tap_guard():
    prog = {"scripts": [], "objects": [{"tag": "coin", "scripts": [
        mb("on_tap", children=[mb("remove_actor")])]}]}
    src = B.compile_blocks(prog)
    assert 'for _self in actors("coin"):' in src
    assert "if _taphit(_self):" in src and "def _taphit(" in src


def test_insert_lands_in_the_target_objects_scripts():
    be = BlockEditor(B)
    be.set_target("enemy")
    upd = be.program["objects"][0]["scripts"][1]      # on_update
    upd.setdefault("c", [])
    be.cur = next(i for i, r in enumerate(be.rows)
                  if r.kind == "insert" and r.parent is upd["c"])
    be.insert_block("move_actor_by", {"dx": 1, "dy": 0})
    assert [b["t"] for b in upd["c"]] == ["move_actor_by"]
    # the Stage program's scripts were NOT touched
    for s in be.program["scripts"]:
        assert not (s.get("c") or [])


def test_stage_switch_shows_global_scripts_and_procs():
    be = BlockEditor(B)
    be.set_target("enemy")
    be.set_target(None)
    assert be.target is None
    tids = [r.block.get("t") for r in be.rows if r.kind == "block"]
    assert "on_start" in tids and "on_update" in tids


def test_variable_rename_rewrites_inside_object_scripts():
    be = BlockEditor(B)
    be.program = _prog_with_objects()
    be.reflow()
    be.rename_var("score", "points")
    # the player on_start's set_var slot was rewritten inside the object entry
    player_start = be.program["objects"][0]["scripts"][0]
    assert player_start["c"][0]["p"]["var"] == "points"
