"""The placement API from a Lua cart (#214) -- the same calls, the same rows.

`scene`, `load_scene`, `actors`, `touching`, `move_actor`, `move_actor_to`,
`remove_actor` and `draw_scene` are, for a Lua cart, the handles prelude's Lua
(runtime/lua_ext.py) over the run's C (native/moycore/moycore_scene.h): the
scene texts are handed to the run before the load, parsed in C into plain row
tables, and the live world is those tables, drawn by C. Nothing crosses into
Python.

What each check is really watching for:

  * the ROWS: every documented field reaches Lua with the value
    widgets.Scenes' row has, including an awkward tag and a `flags` table.
  * the WORLD follows widgets.SceneWorld's rules: copies of the active scene's
    rows made at first use, a fresh snapshot each call, int() moves.
  * the SNAPSHOT contract: `actors(tag)` is a fresh sequence over shared rows,
    so a for-each may `remove_actor` mid-loop without skipping anyone.
  * the EDGES: an empty scene, a missing scene, a missing name in
    `load_scene`, an actor removed twice, and the 256-actor cap.
  * the PIXELS: the C draw_scene paints what cart_api's Python draw_scene
    paints, scale, rotation styles and say bubble included.
  * the TWINS agree: the Python cart and the Lua cart leave the world
    identical after the same scripted frames.

Skipped without a C compiler, like the other host-lua suites.
"""

import ast
import contextlib
import math
import struct

import pytest

from runtime import host_canvas, lua_binding as lb
from runtime.editors_sheet import SpriteSheet
from runtime.lua_ext import PRELUDE_HANDLES
from runtime.widgets import Scenes

host_canvas.install()

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
# A tag carrying a comma, a backslash and a JSON escape, and rows Python's
# parse turns into ints (a float, a bool) or drops (not an object).
ODD = ('[{"tag": "a,b\\\\c\\u00e9", "tile": 2.9, "x": true, "y": -3.5,'
       ' "flip": 0}, 7, "row", {"tile": 3}]')

# Each frame draw_scene draws is recorded as a Python literal, read back with
# ast.literal_eval: (tag, tile, x, y, flip, flags) per live actor.
RECORD = r"""
do
  local draw = __draw_scene
  local function lit(v)
    if type(v) == "boolean" then return v and "True" or "False" end
    if type(v) == "string" then return string.format("%q", v) end
    return tostring(v)
  end
  function __lit_rows(rows)
    local out = {}
    for i = 1, #rows do
      local r, fl = rows[i], {}
      for k, v in pairs(r.flags) do fl[#fl + 1] = lit(k) .. ": " .. lit(v) end
      out[i] = "(" .. lit(r.tag) .. ", " .. lit(r.tile) .. ", " .. lit(r.x) .. ", "
               .. lit(r.y) .. ", " .. lit(r.flip) .. ", {" .. table.concat(fl, ", ") .. "})"
    end
    return "[" .. table.concat(out, ", ") .. "]"
  end
  DRAWN = {}
  __draw_scene = function(rows)
    DRAWN[#DRAWN + 1] = __lit_rows(rows)
    return draw(rows)
  end
end
"""


class Run:
    """One Lua cart over the run's C scenes, wired as the glue wires it."""

    def __init__(self, blobs, names, canvas=None, sheet=None):
        self.scenes = Scenes(blobs, names)
        self.canvas = canvas or host_canvas.make_canvas(96, 64)
        self.run = lb.HostLuaRun(self.canvas._buf, self.canvas.w, self.canvas.h,
                                 sheet, wire=self.canvas._wire)
        for name in self.scenes.names:
            self.run.scene_put(name, self.scenes.raw(name))
        assert self.run.exec(RECORD, "record") is None
        assert self.run.exec(PRELUDE_HANDLES, "prelude") is None

    def text(self, expr):
        """A Lua string expression's value."""
        assert self.run.exec("local s = %s\n__T = {s:byte(1, -1)}" % expr,
                             "probe") is None
        n = self.run.get_global_len("__T")
        out = []
        for i in range(1, n + 1):
            assert self.run.exec("__B = __T[%d]" % i, "probe") is None
            out.append(self.run.get_global("__B"))
        return bytes(out).decode("utf-8")

    @property
    def drawn(self):
        n = self.run.get_global_len("DRAWN")
        return [ast.literal_eval(self.text("DRAWN[%d]" % (i + 1)))
                for i in range(n)]

    def state(self):
        return ast.literal_eval(self.text("__lit_rows(actors())"))

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


def test_awkward_rows_parse_as_the_python_scenes_parse_them():
    with run_cart("""
      ODD = scene("odd")
    """) as r:
        got = ast.literal_eval(r.text("__lit_rows(ODD)"))
        py = [(a.tag, a.tile, a.x, a.y, a.flip, dict(a.flags))
              for a in Scenes({"odd": ODD}, ["odd"]).scene()]
        assert got == py
        assert got[0][0] == "a,b\\c\u00e9"


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
        w = Scenes({"main": MAIN}, ["main"]).world()
        p = w.actors("player")[0]
        near, far = w.actors("coin")[0], w.actors("coin")[1]
        assert r.get("PAIR") == 1 and w.touching(p, near) is True
        assert r.get("PAIRFAR") == 0 and w.touching(p, far) is False
        assert r.get("BYTAG") == 1
        assert r.get("SELFTAG") == 0, "an actor never touches itself by tag"
        assert r.get("NILA") == 0
        assert r.get("APART") == 0, "12px apart ends the 8x8 box overlap"


def test_every_mutation_reaches_the_drawn_world():
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


def test_a_deleted_flag_is_gone_from_the_drawn_world():
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


def _sheet():
    s = SpriteSheet(16, 32)
    v = 0
    for i in range(len(s.pix)):
        v = (v * 1103515245 + 12345) & 0x7FFFFFFF
        s.pix[i] = (v >> 16) & 15
    return s


# Every branch of cart_api's draw_scene: as placed, flipped, scaled, hidden by
# a truthy flag and drawn past a falsy one, the three rotation styles at
# several headings, and the say bubble cut to ten characters, above the actor
# and, near the top edge, below it.
LOOKS = ('['
         '{"tag": "a", "tile": 3, "x": 2, "y": 30, "flip": 0},'
         '{"tag": "a", "tile": 4, "x": 12, "y": 30, "flip": 1},'
         '{"tag": "a", "tile": 5, "x": 22, "y": 30, "flip": 0, "flags": {"size": 200}},'
         '{"tag": "a", "tile": 6, "x": 40, "y": 30, "flip": 0, "flags": {"size": 50}},'
         '{"tag": "a", "tile": 7, "x": 50, "y": 30, "flip": 0, "flags": {"hidden": true}},'
         '{"tag": "a", "tile": 8, "x": 60, "y": 30, "flip": 0, "flags": {"hidden": 0}},'
         '{"tag": "a", "tile": 9, "x": 2, "y": 44, "flip": 0, "flags": {"dir": 90}},'
         '{"tag": "a", "tile": 9, "x": 14, "y": 44, "flip": 0, "flags": {"dir": 135}},'
         '{"tag": "a", "tile": 9, "x": 28, "y": 44, "flip": 0, "flags": {"dir": 180}},'
         '{"tag": "a", "tile": 9, "x": 40, "y": 44, "flip": 0, "flags": {"dir": -30, "size": 200}},'
         '{"tag": "a", "tile": 10, "x": 62, "y": 44, "flip": 0,'
         ' "flags": {"dir": 270, "rot": "leftright"}},'
         '{"tag": "a", "tile": 10, "x": 72, "y": 44, "flip": 1,'
         ' "flags": {"dir": 270, "rot": "none"}},'
         '{"tag": "a", "tile": 11, "x": 4, "y": 2, "flip": 0, "flags": {"say": "hello there!"}},'
         '{"tag": "a", "tile": 11, "x": 60, "y": 16, "flip": 0, "flags": {"say": 42}}'
         ']')


def _f(x):
    return struct.unpack("f", struct.pack("f", x))[0]


def _rotate_f32(pix, w, h, deg, transparent):
    """widgets.rotate_indices as the boards' MicroPython runs it: every float
    single precision, which is what the C rotates in."""
    a = _f(_f(deg) * _f(math.pi / 180))
    ca, sa = _f(math.cos(a)), _f(math.sin(a))
    ow = int(_f(_f(abs(_f(w * ca)) + abs(_f(h * sa))) + 0.5)) or 1
    oh = int(_f(_f(abs(_f(w * sa)) + abs(_f(h * ca))) + 0.5)) or 1
    out = [-1] * (ow * oh)
    ocx, ocy = _f((ow - 1) * 0.5), _f((oh - 1) * 0.5)
    icx, icy = _f((w - 1) * 0.5), _f((h - 1) * 0.5)
    for oy in range(oh):
        ry = _f(oy - ocy)
        for ox in range(ow):
            rx = _f(ox - ocx)
            sx = _f(_f(_f(ca * rx) + _f(sa * ry)) + icx)
            sy = _f(_f(_f(-sa * rx) + _f(ca * ry)) + icy)
            ix, iy = int(_f(sx + 0.5)), int(_f(sy + 0.5))
            if 0 <= ix < w and 0 <= iy < h:
                out[oy * ow + ox] = pix[iy * w + ix]
    return out, ow, oh


@pytest.mark.parametrize("cam", [(0, 0), (3, -2)])
def test_the_c_draw_scene_paints_what_the_python_one_paints(cam, monkeypatch):
    from runtime import cart_api, widgets
    from ws_helpers import StubInput

    monkeypatch.setattr(widgets, "rotate_indices", _rotate_f32)

    sheet = _sheet()
    r = Run({"looks": LOOKS}, ["looks"], host_canvas.make_canvas(96, 64), sheet)
    try:
        err = r.run.load([("function _draw() cls(1) camera(%d, %d) draw_scene() end"
                           % cam, "@cart")])
        assert err is None, err
        assert r.run.tick(1 / 30.0) is None
        lua_px = bytes(r.canvas._buf)
    finally:
        r.run.close()
    ref = host_canvas.make_canvas(96, 64)
    ns = cart_api.make_api(ref, StubInput(), {}, sheet=sheet,
                           scenes=Scenes({"looks": LOOKS}, ["looks"]))
    ref.cls(1)
    ref.camera(*cam)
    ns["draw_scene"]()
    ref.flush_batch()
    py_px = bytes(ref._buf)
    diff = [i // 2 for i in range(0, len(py_px), 2) if py_px[i:i + 2] != lua_px[i:i + 2]]
    assert not diff, "%d pixels differ, first at %s" % (
        len(diff), [(i % 96, i // 96) for i in diff[:6]])
    assert len(set(lua_px[i:i + 2] for i in range(0, len(lua_px), 2))) > 4


def test_a_run_holds_at_most_256_live_actors():
    big = "[" + ",".join('{"tag": "t", "tile": 1, "x": %d, "y": 0}' % i
                         for i in range(257)) + "]"
    r = Run({"big": big}, ["big"])
    try:
        assert r.run.load([("N = #scene()", "@cart")]) is None
        assert r.get("N") == 257, "the read-only rows have no cap"
        err = r.run.exec("actors()", "probe")
        assert err and "256" in err
    finally:
        r.run.close()
