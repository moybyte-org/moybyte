"""Tests for issue #109 -- actor-aware blocks over scene():

  for each {tag} actor / actor touching {tag}? / move actor by/to / remove actor /
  draw scene, plus their cart-API mirrors (actors / touching / move_actor /
  move_actor_to / remove_actor / draw_scene) and the live widgets.SceneWorld they
  bind to.

Each new block is asserted to compile to correct, MicroPython-safe Python; the
compiled coin-collector EXECUTES with the right behaviour against a real SceneWorld;
and the shipped coin_quest.moy seed cart's blocks.json compiles bit-stably to its
main.py (the #109 "pin a golden"). Mirrors tests/test_blocks.py's run harness."""

import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

from runtime import blocks       # noqa: E402
from runtime import moy_carts    # noqa: E402
from runtime import widgets      # noqa: E402
from runtime import palette      # noqa: E402

mk = blocks.make_block


def _program(vars_=None, scripts=None):
    return {"vars": list(vars_ or []), "scripts": scripts or []}


def _assert_micropython_safe(src):
    """No f-strings / eval / exec / getattr / open / import in generated carts."""
    ast.parse(src)
    for banned in ("f'", 'f"', "eval(", "exec(", "getattr(", "open(", "import "):
        assert banned not in src, "generated cart uses banned construct: " + banned


# ----------------------------------------------------------------------------
# A cart namespace wired to a REAL SceneWorld, so the compiled actor code runs
# against the exact shared implementation both backends bind (host == device).
# ----------------------------------------------------------------------------

class _ActorAPI(dict):
    def __init__(self, scene_rows):
        super().__init__()
        self["W"] = 320
        self["H"] = 240
        self.spr_calls = []
        self._btn = {}
        self._sfx = []
        self["col"] = palette.color
        self["btn"] = lambda d=None: self._btn.get(d, False)
        self["btnp"] = lambda d=None: False
        self["rnd"] = lambda n=1.0: 0.0
        self["flr"] = lambda x: int(x // 1)
        for name in ("cls", "print", "rect", "circ", "line"):
            self[name] = (lambda *a, **k: None)
        self["sfx"] = lambda n=0, chan=None: self._sfx.append(n)

        def spr(n, x, y, colorkey=-1, scale=1, flip=0, w=1, h=1):
            self.spr_calls.append((n, x, y, flip))
        self["spr"] = spr

        # the exact make_api wiring for scenes (host_app.make_api / device_api.make_api)
        scenes = widgets.Scenes({"main": json.dumps(scene_rows)}, ["main"])
        scenes.reset()
        self.scenes = scenes
        world = scenes.world()
        self.world = world
        self["scene"] = scenes.scene
        self["load_scene"] = scenes.load_scene
        self["actors"] = world.actors
        self["touching"] = world.touching
        self["move_actor"] = world.move
        self["move_actor_to"] = world.move_to
        self["remove_actor"] = world.remove

        def draw_scene():
            for a in world.actors():
                spr(a.tile, a.x, a.y, -1, 1, a.flip)
        self["draw_scene"] = draw_scene


def _run(src, api, frames=1):
    exec(compile(src, "<cart>", "exec"), api)
    if api.get("_init"):
        api["_init"]()
    for _ in range(frames):
        if api.get("_update"):
            api["_update"](1.0 / 60)
        if api.get("_draw"):
            api["_draw"]()
    return api


# ----------------------------------------------------------------------------
# Compile shape (the deterministic/normalized emission)
# ----------------------------------------------------------------------------

def test_for_each_actor_emits_snapshot_loop():
    prog = _program(scripts=[mk("on_update", children=[
        mk("for_each_actor", {"tag": "coin"}, children=[mk("remove_actor")])])])
    src = blocks.compile_blocks(prog)
    _assert_micropython_safe(src)
    assert 'for _actor1 in actors("coin"):' in src
    assert "remove_actor(_actor1)" in src


def test_touching_move_render_against_current_actor():
    prog = _program(scripts=[mk("on_update", children=[
        mk("for_each_actor", {"tag": "coin"}, children=[
            mk("if", {"cond": mk("actor_touching", {"tag": "player"})},
               children=[mk("move_actor_by", {"dx": 1, "dy": -1}),
                         mk("move_actor_to", {"x": 5, "y": 6})])])])])
    src = blocks.compile_blocks(prog)
    _assert_micropython_safe(src)
    assert 'touching(_actor1, "player")' in src
    assert "move_actor(_actor1, 1, -1)" in src
    assert "move_actor_to(_actor1, 5, 6)" in src


def test_nested_for_each_actor_distinct_loop_vars():
    prog = _program(scripts=[mk("on_update", children=[
        mk("for_each_actor", {"tag": "a"}, children=[
            mk("for_each_actor", {"tag": "b"}, children=[
                mk("if", {"cond": mk("actor_touching", {"tag": "a"})},
                   children=[mk("remove_actor")])])])])])
    src = blocks.compile_blocks(prog)
    _assert_micropython_safe(src)
    # outer/inner loop vars differ (namespaced by indent); the inner remove/touching
    # bind to the INNER actor
    assert 'for _actor1 in actors("a"):' in src
    assert 'for _actor2 in actors("b"):' in src
    assert "touching(_actor2, \"a\")" in src
    assert "remove_actor(_actor2)" in src


def test_draw_scene_emits_bare_call():
    prog = _program(scripts=[mk("on_draw", children=[mk("draw_scene")])])
    src = blocks.compile_blocks(prog)
    assert "draw_scene()" in src


def test_actor_blocks_outside_for_each_are_safe_noops():
    # move/remove/touching with no enclosing for_each_actor compile against None,
    # which every verb treats as a safe no-op -- the cart still parses and runs.
    prog = _program(vars_=["hit"], scripts=[mk("on_update", children=[
        mk("move_actor_by", {"dx": 1, "dy": 1}),
        mk("remove_actor"),
        mk("set_var", {"var": "hit",
                       "value": mk("actor_touching", {"tag": "coin"})})])])
    src = blocks.compile_blocks(prog)
    _assert_micropython_safe(src)
    assert "move_actor(None, 1, 1)" in src
    assert "remove_actor(None)" in src
    assert 'touching(None, "coin")' in src
    api = _ActorAPI([{"tag": "coin", "tile": 2, "x": 0, "y": 0}])
    _run(src, api)
    assert api["hit"] is False        # touching(None, ...) -> False, no crash


# ----------------------------------------------------------------------------
# Behaviour against the real SceneWorld
# ----------------------------------------------------------------------------

def test_coin_collector_removes_and_scores():
    rows = [
        {"tag": "player", "tile": 1, "x": 40, "y": 40, "flip": 0},
        {"tag": "coin", "tile": 2, "x": 44, "y": 44, "flip": 0},   # overlaps player
        {"tag": "coin", "tile": 2, "x": 200, "y": 200, "flip": 0},  # far away
    ]
    prog = _program(vars_=["score"], scripts=[
        mk("on_update", children=[
            mk("for_each_actor", {"tag": "coin"}, children=[
                mk("if", {"cond": mk("actor_touching", {"tag": "player"})}, children=[
                    mk("remove_actor"),
                    mk("change_var", {"var": "score", "value": 1})])])]),
        mk("on_draw", children=[mk("draw_scene")])])
    src = blocks.compile_blocks(prog)
    api = _ActorAPI(rows)
    _run(src, api, frames=1)
    assert api["score"] == 1                            # exactly the touching coin
    coins = [a for a in api.world.actors() if a.tag == "coin"]
    assert len(coins) == 1                              # the near coin is gone
    # draw_scene drew every remaining actor (player + 1 coin), flip respected
    assert (1, 40, 40, 0) in api.spr_calls
    assert len([c for c in api.spr_calls]) == 2


def test_move_player_with_buttons():
    rows = [{"tag": "player", "tile": 1, "x": 100, "y": 100, "flip": 0}]
    prog = _program(scripts=[mk("on_update", children=[
        mk("for_each_actor", {"tag": "player"}, children=[
            mk("if", {"cond": mk("btn", {"dir": "right"})},
               children=[mk("move_actor_by", {"dx": 2, "dy": 0})])])])])
    src = blocks.compile_blocks(prog)
    api = _ActorAPI(rows)
    api._btn = {"right": True}
    _run(src, api, frames=3)
    p = api.world.actors("player")[0]
    assert p.x == 106 and p.y == 100                   # +2 x three frames


def test_remove_while_iterating_visits_every_actor():
    # the snapshot guarantees a for-each that removes the current item never skips
    rows = [{"tag": "coin", "tile": 2, "x": i * 4, "y": 0} for i in range(5)]
    prog = _program(scripts=[mk("on_update", children=[
        mk("for_each_actor", {"tag": "coin"}, children=[mk("remove_actor")])])])
    src = blocks.compile_blocks(prog)
    api = _ActorAPI(rows)
    _run(src, api, frames=1)
    assert api.world.actors("coin") == []              # all five removed, none skipped


# ----------------------------------------------------------------------------
# SceneWorld unit behaviour (the shared, per-backend-free implementation)
# ----------------------------------------------------------------------------

def test_world_is_independent_of_scene_and_resets():
    scenes = widgets.Scenes(
        {"main": json.dumps([{"tag": "coin", "tile": 2, "x": 10, "y": 10}])}, ["main"])
    scenes.reset()
    w = scenes.world()
    w.actors()[0]  # build it
    a = w.actors("coin")[0]
    w.move(a, 5, 5)
    assert w.actors("coin")[0].x == 15
    # scene() is unchanged authored data
    assert scenes.scene()[0].x == 10
    # reset drops the world; a fresh one projects from the scene again
    scenes.reset()
    w2 = scenes.world()
    assert w2 is not w
    assert w2.actors("coin")[0].x == 10


def test_touching_tag_skips_self_and_needs_overlap():
    scenes = widgets.Scenes({"main": json.dumps([
        {"tag": "coin", "tile": 2, "x": 0, "y": 0},
        {"tag": "coin", "tile": 2, "x": 4, "y": 0},     # overlaps the first
        {"tag": "coin", "tile": 2, "x": 100, "y": 0}])}, ["main"])
    scenes.reset()
    w = scenes.world()
    first, second, far = w.actors("coin")
    assert w.touching(first, "coin") is True            # overlaps `second`, not itself
    assert w.touching(far, "coin") is False             # nothing within 8px
    assert w.touching(first, second) is True            # actor-vs-actor form
    assert w.touching(None, "coin") is False            # None guard


def test_empty_scene_world_is_harmless():
    scenes = widgets.Scenes({}, [])
    scenes.reset()
    w = scenes.world()
    assert w.actors() == []
    assert w.actors("coin") == []
    w.remove(None)                                       # no crash


# ----------------------------------------------------------------------------
# Reserved names + graduation round-trip
# ----------------------------------------------------------------------------

def test_new_verbs_are_reserved():
    for name in ("actors", "touching", "move_actor", "move_actor_to",
                 "remove_actor", "draw_scene"):
        assert blocks.is_reserved_name(name)


def test_actor_program_round_trips_for_graduation():
    prog = _program(vars_=["score"], scripts=[
        mk("on_update", children=[
            mk("for_each_actor", {"tag": "coin"}, children=[
                mk("if", {"cond": mk("actor_touching", {"tag": "player"})},
                   children=[mk("remove_actor"),
                             mk("change_var", {"var": "score", "value": 1})])])]),
        mk("on_draw", children=[mk("draw_scene")])])
    src = blocks.compile_blocks(prog)
    assert blocks.source_roundtrips(prog, src)           # no false graduation
    # a genuine hand-edit past the vocabulary DOES graduate
    assert not blocks.source_roundtrips(prog, src + "\nfoo = 1\n")


# ----------------------------------------------------------------------------
# The shipped coin_quest.moy seed cart: its blocks.json is the source of truth
# ----------------------------------------------------------------------------

def _coin_quest_dir():
    return str(ROOT / "system_carts" / "coin_quest.moy")


def test_coin_quest_blocks_json_compiles_to_shipped_main():
    base = _coin_quest_dir()
    with open(base + "/blocks.json") as f:
        prog = json.loads(f.read())
    with open(base + "/main.py") as f:
        shipped = f.read()
    assert blocks.compile_blocks(prog) == shipped        # pinned golden (#109)
    assert blocks.is_block_authored_source(shipped)
    _assert_micropython_safe(shipped)


def test_coin_quest_cart_loads_with_scene_and_runs():
    cart = moy_carts.load(_coin_quest_dir())
    assert "main" in (cart.get("scenes") or {})
    rows = json.loads(cart["scenes"]["main"])
    assert any(r["tag"] == "player" for r in rows)
    assert sum(1 for r in rows if r["tag"] == "coin") >= 1
    # the shipped source runs against a real world built from its own scene
    api = _ActorAPI(rows)
    api._btn = {"right": True}
    _run(cart["src"], api, frames=2)
    # player moved right; draw_scene drew the remaining actors
    assert api.world.actors("player")[0].x > [r for r in rows if r["tag"] == "player"][0]["x"]
    assert len(api.spr_calls) >= 1
