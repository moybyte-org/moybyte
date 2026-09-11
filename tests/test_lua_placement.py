"""The placement API from a Lua cart (#214) -- the same calls, the same rows.

`scene()` used to arrive as nil: every binding marshalled scalars, and a scene
row is an object with `.tag`/`.tile`/`.x`/`.y`/`.flip`/`.flags`. So the whole
`#85`/`#109` family -- scene / load_scene / actors / touching / move_actor /
move_actor_to / remove_actor -- was Python-only in practice, against a document
that promises every call is valid verbatim in both languages.

It now rides `runtime/lua_ext.py`'s handle glue like the layer and image verbs
before it: a whole scene crosses as ONE encoded string, the prelude decodes it
into plain Lua tables, and `__id` is each row's handle back to its Actor.

What each check is really watching for:

  * the ROWS: every documented field reaches Lua with the value the Python row
    has, including a tag with the separator character in it and a `flags` table.
  * IDENTITY round-trips: move/remove/tag/flag reach the SAME Actor object the
    Python side holds, which is the only thing that makes `draw_scene` -- the
    one verb that reads the actors back Python-side -- draw what the cart did.
  * the SNAPSHOT contract: `actors(tag)` is a fresh sequence over shared rows,
    so a for-each may `remove_actor` mid-loop without skipping anyone.
  * the EDGES: an empty scene, a missing scene, a missing name in `load_scene`,
    and an actor removed twice.
  * the TWINS agree: the Python cart and the Lua cart, both through the real
    glue, leave the world identical after the same scripted frames.

Skipped without a C compiler, like the other host-lua suites.
"""

import contextlib

import pytest

from runtime import lua_binding as lb
from runtime.lua_ext import PRELUDE_HANDLES, install_handles
from runtime.widgets import Scenes

pytestmark = pytest.mark.skipif(
    not lb.HostLuaRun.available(),
    reason="no C compiler for the host lua binding")


MAIN = ('[{"tag": "player", "tile": 1, "x": 100, "y": 100, "flip": 0},'
        ' {"tag": "coin", "tile": 2, "x": 104, "y": 100, "flip": 1,'
        '  "flags": {"size": 200, "say": "hi", "hidden": false}},'
        ' {"tag": "coin", "tile": 2, "x": 200, "y": 8, "flip": 0}]')
LEVEL2 = '[{"tag": "boss", "tile": 9, "x": 1, "y": 2, "flip": 0}]'
# The twin cart walks its player along (2f, f); these three coins sit on it.
TWINS = ('[{"tag": "player", "tile": 1, "x": 0, "y": 0, "flip": 0},'
         ' {"tag": "coin", "tile": 2, "x": 10, "y": 5, "flip": 0},'
         ' {"tag": "coin", "tile": 2, "x": 30, "y": 15, "flip": 0},'
         ' {"tag": "coin", "tile": 2, "x": 60, "y": 30, "flip": 0}]')
# A tag carrying BOTH characters the blob escapes. Nothing stops the Scene
# editor's tag field holding a comma, and an unescaped one would silently shift
# every field of every row after it.
ODD = '[{"tag": "a,b\\\\c", "tile": 0, "x": 0, "y": 0, "flip": 0}]'


class Run:
    """One Lua cart over a real Scenes world, wired as the Player wires it."""

    def __init__(self, blobs, names):
        self.scenes = Scenes(blobs, names)
        self.world = self.scenes.world()
        self.drawn = []
        self.ns = {
            "scene": self.scenes.scene,
            "load_scene": self.scenes.load_scene,
            "actors": self.world.actors,
            "touching": self.world.touching,
            "move_actor": self.world.move,
            "move_actor_to": self.world.move_to,
            "remove_actor": self.world.remove,
            "draw_scene": self._draw_scene,
        }
        self.buf = bytearray(96 * 64 * 2)
        self.run = lb.HostLuaRun(self.buf, 96, 64)
        self.run.register("draw_scene", self.ns["draw_scene"])
        install_handles(self.ns, self.run.register)
        assert self.run.exec(PRELUDE_HANDLES, "prelude") is None

    def _draw_scene(self):
        self.drawn.append(self.state())

    def state(self):
        return [(a.tag, a.tile, a.x, a.y, a.flip, dict(a.flags))
                for a in self.world.actors()]

    def get(self, name):
        return self.run.get_global(name)


@contextlib.contextmanager
def run_cart(body, blobs=None, names=None):
    r = Run(blobs if blobs is not None else {"main": MAIN, "level2": LEVEL2,
                                             "odd": ODD},
            names if names is not None else ["main", "level2", "odd"])
    try:
        err = r.run.load([("function _init()\n%s\nend\n"
                         "function _update(dt) end\nfunction _draw() end\n"
                         % body, "@cart")])
        assert err is None, err
        yield r
    finally:
        r.run.close()


def test_a_scene_row_carries_every_documented_field():
    with run_cart("""
      local s = scene()
      N = #s
      TAG = (s[1].tag == "player") and 1 or 0
      TILE, X, Y, FLIP = s[2].tile, s[2].x, s[2].y, s[2].flip
      SAY = (s[2].flags.say == "hi") and 1 or 0
      SIZE = s[2].flags.size
      HIDDEN = (s[2].flags.hidden == false) and 1 or 0
      EMPTYFLAGS = (next(s[1].flags) == nil) and 1 or 0
      TYPEOK = (type(s) == "table") and 1 or 0
      COUNTED = 0
      for _, a in ipairs(s) do COUNTED = COUNTED + 1 end
    """) as r:
        assert r.get("N") == 3 and r.get("COUNTED") == 3
        assert r.get("TYPEOK") == 1, "scene() must be a table ipairs can walk"
        assert r.get("TAG") == 1
        assert (r.get("TILE"), r.get("X"), r.get("Y"), r.get("FLIP")) == \
            (2, 104, 100, 1)
        # flags is a TABLE, with the three JSON value kinds intact.
        assert r.get("SAY") == 1 and r.get("SIZE") == 200
        assert r.get("HIDDEN") == 1, "a false flag must stay false, not vanish"
        assert r.get("EMPTYFLAGS") == 1, "a row with no flags gets an empty table"


def test_a_tag_holding_the_separator_survives_the_blob():
    with run_cart("""
      local s = scene("odd")
      OK = (s[1].tag == "a,b\\\\c") and 1 or 0
      N = #s
    """) as r:
        assert r.get("N") == 1
        assert r.get("OK") == 1, "the blob's escaping lost a comma or a backslash"


def test_scene_by_name_and_load_scene_move_the_active_one():
    with run_cart("""
      NAMED = #scene("level2")
      STILL = #scene()
      LOADED = #load_scene("level2")
      ACTIVE = #scene()
      BAD = #load_scene("nope")
      AFTERBAD = #scene()
      BACK = #load_scene("main")
    """) as r:
        assert r.get("NAMED") == 1
        assert r.get("STILL") == 3, "scene(name) must not switch the active scene"
        assert r.get("LOADED") == 1 and r.get("ACTIVE") == 1
        assert r.get("BAD") == 0, "an unknown scene reads as empty, never raises"
        assert r.get("AFTERBAD") == 1, "a failed load_scene leaves the active one"
        assert r.get("BACK") == 3


def test_a_cart_with_no_scenes_reads_an_empty_list():
    with run_cart("""
      N = #scene()
      M = #scene("whatever")
      A = #actors()
      T = touching(nil, "coin") and 1 or 0
      draw_scene()
    """, blobs={}, names=[]) as r:
        assert (r.get("N"), r.get("M"), r.get("A")) == (0, 0, 0)
        assert r.get("T") == 0
        assert r.drawn == [[]]


def test_actors_filters_by_tag_and_hands_out_a_fresh_snapshot():
    with run_cart("""
      A = #actors()
      C = #actors("coin")
      NONE = #actors("ghost")
      local one, two = actors("coin"), actors("coin")
      FRESH = (one ~= two) and 1 or 0
      SHARED = (one[1] == two[1]) and 1 or 0
      SEEN = 0
      for _, c in ipairs(actors("coin")) do
        SEEN = SEEN + 1
        remove_actor(c)
      end
      LEFT = #actors()
    """) as r:
        assert (r.get("A"), r.get("C"), r.get("NONE")) == (3, 2, 0)
        assert r.get("FRESH") == 1, "each call must be its own sequence"
        assert r.get("SHARED") == 1, "the ROWS are shared, only the list is fresh"
        # Removing mid-for-each must not skip the coin after it.
        assert r.get("SEEN") == 2 and r.get("LEFT") == 1


def test_touching_matches_the_python_rule():
    with run_cart("""
      local p = actors("player")[1]
      local near = actors("coin")[1]
      local far = actors("coin")[2]
      PAIR = touching(p, near) and 1 or 0
      PAIRFAR = touching(p, far) and 1 or 0
      BYTAG = touching(p, "coin") and 1 or 0
      SELFTAG = touching(p, "player") and 1 or 0
      NILA = touching(nil, "coin") and 1 or 0
      move_actor(p, 12, 0)
      APART = touching(p, near) and 1 or 0
    """) as r:
        w = r.world
        p = w.actors("player")[0]
        near, far = w.actors("coin")[0], w.actors("coin")[1]
        assert r.get("PAIR") == 1 and w.touching(p, near) is True
        assert r.get("PAIRFAR") == 0 and w.touching(p, far) is False
        assert r.get("BYTAG") == 1
        assert r.get("SELFTAG") == 0, "an actor never touches itself by tag"
        assert r.get("NILA") == 0
        assert r.get("APART") == 0, "12px apart ends the 8x8 box overlap"


def test_every_mutation_reaches_the_python_actor():
    with run_cart("""
      local p = actors("player")[1]
      move_actor(p, -3, 4)
      X1, Y1 = p.x, p.y
      move_actor_to(p, 12.9, -0.5)
      X2, Y2 = p.x, p.y
      p.tag = "hero"
      p.tile = 5
      p.flip = 1
      p.flags.say = "yo"
      p.x = p.x + 1
      draw_scene()
      remove_actor(actors("coin")[1])
      remove_actor(actors("hero")[1])
      draw_scene()
    """) as r:
        assert (r.get("X1"), r.get("Y1")) == (97, 104)
        # int() truncates toward zero on both sides of the seam.
        assert (r.get("X2"), r.get("Y2")) == (12, 0)
        assert r.drawn[0] == [
            ("hero", 5, 13, 0, 1, {"say": "yo"}),
            ("coin", 2, 104, 100, 1, {"size": 200, "say": "hi", "hidden": False}),
            ("coin", 2, 200, 8, 0, {}),
        ], r.drawn[0]
        assert r.drawn[1] == [("coin", 2, 200, 8, 0, {})], r.drawn[1]
        assert r.state() == [("coin", 2, 200, 8, 0, {})]


def test_a_deleted_flag_is_deleted_python_side_too():
    with run_cart("""
      local c = actors("coin")[1]
      c.flags.size = nil
      c.flags.hidden = true
      draw_scene()
    """) as r:
        assert r.drawn[0][1][5] == {"say": "hi", "hidden": True}, r.drawn[0]


def test_removing_an_actor_twice_is_a_no_op():
    with run_cart("""
      local c = actors("coin")[1]
      remove_actor(c)
      remove_actor(c)
      remove_actor(nil)
      LEFT = #actors()
      draw_scene()
    """) as r:
        assert r.get("LEFT") == 2
        assert len(r.drawn[0]) == 2


def test_draw_scene_without_ever_touching_the_world_still_draws_the_scene():
    with run_cart("""
      draw_scene()
      N = #actors()
    """) as r:
        assert len(r.drawn[0]) == 3, "the world projects from the active scene"
        assert r.get("N") == 3


# The coin collector out of docs/moy_cart_api.md, once in each language.
COIN_QUEST_PY = """
for _self in actors("player"):
    move_actor(_self, 2, 1)
for _self in actors("coin"):
    if touching(_self, "player"):
        remove_actor(_self)
        score[0] = score[0] + 1
draw_scene()
"""

COIN_QUEST_LUA = """
for _, s in ipairs(actors("player")) do move_actor(s, 2, 1) end
for _, s in ipairs(actors("coin")) do
  if touching(s, "player") then remove_actor(s) SCORE = SCORE + 1 end
end
draw_scene()
"""


def test_the_python_and_lua_halves_of_the_same_cart_agree_frame_for_frame():
    """The document's promise, executed: the same coin collector in each
    language, over the same scene, leaves the same world every frame."""
    py_scenes = Scenes({"main": TWINS}, ["main"])
    py_world = py_scenes.world()
    py_drawn = []
    score = [0]
    env = {"actors": py_world.actors, "touching": py_world.touching,
           "move_actor": py_world.move, "remove_actor": py_world.remove,
           "draw_scene": lambda: py_drawn.append(
               [(a.tag, a.tile, a.x, a.y, a.flip) for a in py_world.actors()]),
           "score": score}

    with run_cart("SCORE = 0", blobs={"main": TWINS}, names=["main"]) as r:
        assert r.run.exec("function _step() %s end" % COIN_QUEST_LUA,
                          "step") is None
        for _ in range(40):
            exec(COIN_QUEST_PY, env)                        # noqa: S102
            assert r.run.exec("_step()", "tick") is None
        assert len(r.drawn) == len(py_drawn) == 40
        for lua_frame, py_frame in zip(r.drawn, py_drawn):
            assert [row[:5] for row in lua_frame] == py_frame
        assert r.get("SCORE") == score[0] == 3


def test_the_on_glass_fixture_cart_runs_under_the_real_player(tmp_path):
    """`tests/fixtures/placement_lua.moy` is what a board is asked to run for
    #214, so it has to keep running here: every verb it calls goes through the
    shipped make_api and the shipped glue, not this file's harness."""
    import os
    import shutil

    from ws_helpers import build_ws, open_cart

    root = tmp_path / "carts"
    root.mkdir(parents=True)
    fixture = os.path.join(os.path.dirname(__file__), "fixtures",
                           "placement_lua.moy")
    shutil.copytree(fixture, str(root / "placement_lua.moy"))
    ws = build_ws(tmp_path)
    open_cart(ws, "Placement Lua")
    assert ws.player.cart_error is None
    run = ws.player._lua
    assert run is not None, "the fixture did not start on the Lua runtime"
    assert run.get_global("N") == 4                  # scene() saw four rows
    assert run.get_global("PLAYERS") == 1 and run.get_global("COINS") == 3
    # Walk right until the player reaches the coin at x=120: it is collected,
    # which only happens if touching() and remove_actor() reached the world.
    ws.input.set_held("right", True)
    for _ in range(60):
        ws.input.begin_frame()
        ws.frame(1 / 30)
    assert ws.player.cart_error is None
    assert run.get_global("score") == 1
