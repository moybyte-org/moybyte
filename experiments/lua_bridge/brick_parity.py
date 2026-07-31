"""Brick Siege Python-vs-Lua parity harness (#67, host edition).

The sibling of host_parity.py (sakura), for the console's heaviest seed cart:
runs the REAL system_carts/brick_siege.moy/main.py and its line-faithful Lua port
brick_siege_lua.moy/main.lua side by side for N deterministic frames -- same
seeded PRNG, same scripted buttons, same manifest config, same tilemap -- and
after EVERY frame compares

  * the whole draw stream, call for call (background / map / spr / rect / print /
    sfx), and
  * the whole game state (players, enemies, bullets, booms, spawn_q, spawn_t,
    base_alive, score, state, state_t, t, shake) float-for-float AND type for
    type (an int that became a float is a real bug here: the HUD stringifies
    score and the spawn count), and
  * the TILEMAP, cell for cell -- the cart destroys brick cells with mset(), so
    the map is game state and a one-cell divergence in what a bullet crumbled
    would otherwise hide until it changed a collision.

Both languages do IEEE double arithmetic, so a faithful port must match EXACTLY;
any epsilon is a porting bug, not noise.

Two scenarios run, because the cart has two input paths:
  * autoplay -- config autoplay=1 and no buttons: the attract auto-pilot drives,
    so the run is input-free and entirely PRNG-driven (this is the long run).
  * manual -- config autoplay=0 plus a scripted button track (held directions +
    fire edges), exercising the btn()/btnp() branch and _move_tank.

Unlike host_parity.py, `rnd` is the SAME Python LCG on both sides (registered as
a Lua global rather than reimplemented in Lua): brick siege's logic is riddled
with random draws, and a second PRNG implementation would put a possible
divergence in the HARNESS rather than in the port under test.

Like host_parity.py this deliberately FAKES the API instead of importing the
console -- it proves the PORT (the logic translation). Two shims are worth
naming:
  * spr_batch: the Lua bridge cannot marshal a table (lua_to_mp errors on one),
    so the Lua cart draws its sprites with a per-sprite spr() loop where the
    Python cart hands one list to spr_batch. The Python fake therefore EXPANDS
    spr_batch into the same per-sprite events the Lua side records -- i.e. the
    comparison is of PIXELS ISSUED, which is what parity means here. (The engine
    collapses either form into the same native blit_batch: canvas.spr_tile
    auto-batches a contiguous run, #63.)
  * mget/mset run against the real runtime.editors_sheet.TileMap parsed from each
    cart's own map.moymap, so cell semantics (tile+1 storage, -1 for empty/out of
    range) are the shipped ones, and a drifted map file fails the run.

Needs `lupa` (the #67 Phase 3 host-runner dependency): .venv/bin/pip install lupa
Run:   .venv/bin/python experiments/lua_bridge/brick_parity.py
Also wired into pytest as tests/test_lua_brick_siege_parity.py (skips without lupa).
"""

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.join(_HERE, "..", "..")
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from runtime import palette                      # noqa: E402
from runtime.editors_sheet import TileMap         # noqa: E402

_CARTS = os.path.join(_ROOT, "system_carts")
PY_CART_DIR = os.path.join(_CARTS, "brick_siege.moy")        # the Python original
LUA_CART_DIR = os.path.join(_CARTS, "brick_siege_lua.moy")   # the #67 A/B twin
DT = 1.0 / 30.0
SEED = 0xC0FFEE % 2147483648

# The state globals both sides must agree on, in comparison order.
STATE_LISTS = ("players", "enemies", "bullets", "booms")
STATE_SCALARS = ("spawn_q", "spawn_t", "base_alive", "score", "state",
                 "state_t", "t", "shake")

# The scripted button track for the "manual" scenario: a dict of
# frame -> (held-button tuple, pressed-button tuple). Built once in Python and
# handed to both sides, so no input arithmetic is duplicated across the language
# boundary. Directions are HELD in runs (that's how a kid plays); "a" arrives as
# a press EDGE only, which is btnp's contract.
#
# The IDLE phase matters: with no button at all the cart evaluates
# `if auto and not any_in` with a falsy `auto`, which is the one place Lua's
# truthy 0 can silently flip the whole control path to the auto-pilot. A script
# that always holds a direction never reaches that test (verified: it lets the
# `auto ~= 0` -> `auto` mutation escape).
def _button_script(frames):
    script = {}
    for f in range(frames):
        held = []
        phase = (f // 24) % 5
        if phase == 0:
            held.append("up")
        elif phase == 1:
            held.append("right")
        elif phase == 2:
            pass                      # idle: hands off the pad
        elif phase == 3:
            held.append("down")
        else:
            held.append("left")
        pressed = ("a",) if f % 17 == 3 else ()
        script[f] = (tuple(held), pressed)
    return script


# --- the shared deterministic PRNG (LCG, 31-bit) -----------------------------
# ONE implementation, used by BOTH sides (see the module docstring).

class Lcg:
    def __init__(self, seed):
        self.state = seed

    def rnd(self, n=1.0):
        self.state = (1103515245 * self.state + 12345) % 2147483648
        return self.state / 2147483648.0 * n


def _load_config():
    """Both carts' manifest configs, drift-guarded: the twins must tune alike or
    the A/B (and this parity run) compares different waves."""
    with open(os.path.join(PY_CART_DIR, "manifest.json")) as fh:
        py_cfg = json.load(fh)["config"]
    with open(os.path.join(LUA_CART_DIR, "manifest.json")) as fh:
        lua_cfg = json.load(fh)["config"]
    if py_cfg != lua_cfg:
        raise AssertionError("brick_siege.moy and brick_siege_lua.moy configs "
                             "drifted: %r != %r" % (py_cfg, lua_cfg))
    return py_cfg


def _load_map(cart_dir):
    with open(os.path.join(cart_dir, "map.moymap")) as fh:
        return TileMap.from_hex(fh.read())


# Brick cells in the pristine field (a cell byte is tile + 1, so BRICK 8 -> 9):
# the "bricks left" line only means something against this.
_BRICKS0 = sum(1 for c in _load_map(PY_CART_DIR).cells if c == 9)


def _check_maps():
    """The twins must ship the same battlefield, byte for byte."""
    with open(os.path.join(PY_CART_DIR, "map.moymap")) as fh:
        a = fh.read()
    with open(os.path.join(LUA_CART_DIR, "map.moymap")) as fh:
        b = fh.read()
    if a != b:
        raise AssertionError("brick_siege.moy and brick_siege_lua.moy "
                             "map.moymap drifted")


# --- the fake API both sides share -------------------------------------------

class FakeConsole:
    """The recording namespace: draw events + a real TileMap + scripted input."""

    def __init__(self, config, script):
        self.events = []
        self.frame = -1
        self.lcg = Lcg(SEED)
        self.config = config
        self.script = script
        self.tilemap = None            # set by reset()
        self.bg = None                 # the DECLARED backdrop, if any

    def reset(self, cart_dir):
        self.events = []
        self.frame = -1
        self.lcg.state = SEED
        self.tilemap = _load_map(cart_dir)
        self.bg = None

    def restore_bg(self):
        """Mirror host_api's _restore_bg: a DECLARED background is repainted as a
        cls() before the frame's first draw. The Python cart declares one with
        background(); the Lua twin is moy core 0.1 ONLY (no `layers` extension)
        so it calls cls() itself as _draw's first statement, and this no-ops
        there. That is exactly why the two draw streams stay identical."""
        if self.bg is not None:
            self.events.append(("cls", self.bg))

    # -- input ---------------------------------------------------------------
    def _keys(self):
        return self.script.get(self.frame, ((), ()))

    def btn(self, name, player=0):
        return name in self._keys()[0]

    def btnp(self, name, player=0):
        return name in self._keys()[1]

    # -- the verbs -----------------------------------------------------------
    def ns(self):
        ev = self.events

        def spr(n, x, y, colorkey=-1, scale=1, flip=0, w=1, h=1):
            ev.append(("spr", n, x, y, colorkey, scale))

        def spr_batch(items, colorkey=-1, scale=1):
            # Expanded per item: the Lua twin issues one spr() per sprite (it
            # cannot pass a table over the bridge), and the engine collapses both
            # forms into the same native batch -- so PIXELS ISSUED is the parity
            # unit. See the module docstring.
            for it in items:
                ev.append(("spr", it[0], it[1], it[2], colorkey, scale))

        def background(c=None):
            # NOT a draw event -- host_api's background() only DECLARES; the
            # pixels arrive via restore_bg()'s cls() at frame start.
            self.bg = c

        return {
            "W": 320, "H": 240,
            "cfg": lambda k, d=None: self.config.get(k, d),
            "rnd": self.lcg.rnd,
            "col": palette.color,
            "btn": self.btn, "btnp": self.btnp,
            "mget": lambda x, y: self.tilemap.mget(x, y),
            "mset": lambda x, y, tile: self.tilemap.mset(x, y, tile),
            "background": background,
            "cls": lambda c=0: ev.append(("cls", c)),
            "map": lambda mx=0, my=0, w=None, h=None, sx=0, sy=0,
            colorkey=-1, scale=1:
                ev.append(("map", mx, my, w, h, sx, sy, colorkey, scale)),
            "spr": spr, "spr_batch": spr_batch,
            "rect": lambda x, y, w, h, c: ev.append(("rect", x, y, w, h, c)),
            "print": lambda s, x, y, c, scale=1:
                ev.append(("print", s, x, y, c, scale)),
            "sfx": lambda n, chan=None: ev.append(("sfx", n, chan)),
        }

    def take_events(self):
        # Cleared IN PLACE, never rebound: ns() closes over this exact list, so
        # `self.events = []` would silently orphan the recorder and compare
        # nothing after frame 0 (it did, for one revision of this file).
        ev = list(self.events)
        del self.events[:]
        return ev

    def cells(self):
        return bytes(self.tilemap.cells)


# --- Python side --------------------------------------------------------------

class PyCart:
    """Exec the real main.py under the fake recording API."""

    def __init__(self, console):
        self.console = console
        console.reset(PY_CART_DIR)
        ns = console.ns()
        with open(os.path.join(PY_CART_DIR, "main.py")) as fh:
            exec(fh.read(), ns)
        self.ns = ns

    def set_frame(self, f):
        self.console.frame = f

    def init(self):
        self.ns["_init"]()

    def update(self, dt):
        self.ns["_update"](dt)

    def draw(self):
        self.console.restore_bg()      # the Player's pre-draw backdrop hook
        self.ns["_draw"]()

    def state(self):
        out = []
        for name in STATE_LISTS:
            out.append([list(row) for row in self.ns[name]])
        for name in STATE_SCALARS:
            out.append(self.ns[name])
        out.append(self.console.cells())
        return out


# --- Lua side ------------------------------------------------------------------

class LuaCart:
    """Run main.lua under lupa with the same fake API (verbs registered as Lua
    globals -- lupa makes a Python callable directly callable from Lua, which is
    exactly the shape the device bridge gives a cart)."""

    def __init__(self, console):
        try:
            # Lua 5.4 explicitly -- the version #67 vendors on the device.
            from lupa import lua54
            self.lua = lua54.LuaRuntime(register_eval=False,
                                        register_builtins=False)
        except ImportError:  # pragma: no cover - older lupa wheels
            import lupa
            self.lua = lupa.LuaRuntime(register_eval=False,
                                       register_builtins=False)
        self.console = console
        console.reset(LUA_CART_DIR)
        g = self.lua.globals()
        for k, v in console.ns().items():
            g[k] = v
        with open(os.path.join(LUA_CART_DIR, "main.lua")) as fh:
            self.lua.execute(fh.read())
        self.g = g

    def set_frame(self, f):
        self.console.frame = f

    def init(self):
        self.g._init()

    def update(self, dt):
        self.g._update(dt)

    def draw(self):
        self.console.restore_bg()      # no-ops: the core-0.1 twin cls()es itself
        self.g._draw()

    def state(self):
        out = []
        for name in STATE_LISTS:
            rows = []
            lt = self.g[name]
            for i in range(1, len(lt) + 1):
                row = lt[i]
                rows.append([row[j] for j in range(1, len(row) + 1)])
            out.append(rows)
        for name in STATE_SCALARS:
            out.append(self.g[name])
        out.append(self.console.cells())
        return out


# --- comparison ----------------------------------------------------------------

def _same(a, b):
    """Exact equality, and for numbers the same TYPE too: a Lua integer that
    became a float (or vice versa) changes tostring()/str() in the HUD and is a
    real divergence, not a representation detail."""
    if isinstance(a, bool) != isinstance(b, bool):
        return False
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        return len(a) == len(b) and all(_same(x, y) for x, y in zip(a, b))
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return type(a) is type(b) and a == b
    return type(a) is type(b) and a == b


def _report_events(frame, ev_py, ev_lua, verbose):
    if len(ev_py) == len(ev_lua) and all(_same(a, b)
                                        for a, b in zip(ev_py, ev_lua)):
        return 0
    bad = 0
    if len(ev_py) != len(ev_lua):
        if verbose:
            print("frame %d: event count py=%d lua=%d"
                  % (frame, len(ev_py), len(ev_lua)))
        bad += 1
    for i, (a, b) in enumerate(zip(ev_py, ev_lua)):
        if not _same(a, b):
            if verbose:
                print("frame %d event %d: py=%r lua=%r" % (frame, i, a, b))
            bad += 1
            if bad >= 5:
                break
    return bad


def _report_state(frame, st_py, st_lua, verbose):
    names = list(STATE_LISTS) + list(STATE_SCALARS) + ["tilemap"]
    bad = 0
    for name, a, b in zip(names, st_py, st_lua):
        if not _same(a, b):
            if verbose:
                print("frame %d state %s: py=%r lua=%r" % (frame, name, a, b))
            bad += 1
    return bad


# --- the run -------------------------------------------------------------------

def run_scenario(name, config, script, frames, verbose=True, units=False):
    _check_maps()
    pyc = PyCart(FakeConsole(config, script))
    luc = LuaCart(FakeConsole(config, script))

    pyc.init()
    luc.init()
    if units:
        # After _init (so the tilemap/_FIELD0 exist) and before the frame loop
        # (the crafted calls consume randoms, which would shift the run).
        bad = _unit_checks(pyc, luc, verbose)
        if bad:
            print("PARITY[%s]: %d unit-check mismatch(es)" % (name, bad))
            return False
        pyc = PyCart(FakeConsole(config, script))
        luc = LuaCart(FakeConsole(config, script))
        pyc.init()
        luc.init()
    ev_py = pyc.console.take_events()
    n_events = len(ev_py)
    bad = _report_events(-1, ev_py, luc.console.take_events(), verbose)
    bad += _report_state(-1, pyc.state(), luc.state(), verbose)
    rounds = 0                       # how many times the wave ended + restarted
    banners = set()

    for f in range(frames):
        pyc.set_frame(f)
        luc.set_frame(f)
        was = pyc.ns["state"]
        pyc.update(DT)
        luc.update(DT)
        pyc.draw()
        luc.draw()
        if pyc.ns["state"] != was:
            banners.add(pyc.ns["state"])
            if pyc.ns["state"] == 0:
                rounds += 1
        ev_py = pyc.console.take_events()
        n_events += len(ev_py)
        bad += _report_events(f, ev_py, luc.console.take_events(), verbose)
        bad += _report_state(f, pyc.state(), luc.state(), verbose)
        if bad > 10:
            print("PARITY[%s]: too many mismatches, stopping early" % name)
            return False

    if verbose:
        st = dict(zip(list(STATE_LISTS) + list(STATE_SCALARS) + ["tilemap"],
                      pyc.state()))
        print("PARITY[%s] %s: %d frames, %d draw events + %d state fields "
              "compared; saw score=%r banners=%s restarts=%d, "
              "%d/%d bricks left"
              % (name, "OK" if bad == 0 else "FAIL", frames, n_events,
                 (frames + 1) * (len(STATE_LISTS) + len(STATE_SCALARS) + 1),
                 st["score"], sorted(banners), rounds,
                 sum(1 for c in st["tilemap"] if c == 9),
                 _BRICKS0))
    return bad == 0


# --- the branches gameplay cannot reach -----------------------------------------
# Six statements of main.py are defensive and unreachable in play (verified by
# tracing 120k frames over 30 seed/config combinations): _move_tank's four
# out-of-bounds clamps (a step off the field is already rejected by the wall
# check, which reads outside the field as STEEL), _ai_retarget's boxed-in
# fallthrough, and _ai_player's no-live-target return. They still have to match,
# so they are compared by calling the helpers DIRECTLY on both sides with
# crafted state -- return values, the mutated struct, AND the PRNG cursor (equal
# cursors prove both sides drew the same number of randoms).

def _unit_checks(pyc, luc, verbose):
    lua = luc.lua
    bad = 0

    def check(label, py_ret, lua_ret, py_obj, lua_obj):
        nonlocal bad
        py_cursor = pyc.console.lcg.state
        lua_cursor = luc.console.lcg.state
        if not _same(list(py_ret), list(lua_ret)):
            if verbose:
                print("unit %s: return py=%r lua=%r" % (label, py_ret, lua_ret))
            bad += 1
        if not _same(py_obj, lua_obj):
            if verbose:
                print("unit %s: struct py=%r lua=%r" % (label, py_obj, lua_obj))
            bad += 1
        if py_cursor != lua_cursor:
            if verbose:
                print("unit %s: prng cursor py=%d lua=%d"
                      % (label, py_cursor, lua_cursor))
            bad += 1

    def lua_list(t):
        return [t[i] for i in range(1, len(t) + 1)]

    # 1. _ai_player with nothing alive to shoot -> (0, 0, False).
    py_players = pyc.ns["players"]
    py_enemies = pyc.ns["enemies"]
    pyc.ns["enemies"] = []
    lua_enemies = luc.g["enemies"]
    luc.g["enemies"] = lua.table()
    p_py = pyc.ns["_make_player"](4)
    p_lua = luc.g._make_player(4)
    check("_ai_player(no target)",
          pyc.ns["_ai_player"](p_py, DT), luc.g._ai_player(p_lua, DT),
          list(p_py), lua_list(p_lua))
    pyc.ns["enemies"] = py_enemies
    luc.g["enemies"] = lua_enemies

    # 2. _ai_retarget on an enemy boxed in on all four sides -- sat on the steel
    #    block at cell (1, 1), where every 2px probe still overlaps it, so the
    #    four-try loop falls through to the last-resort heading.
    ts = pyc.ns["TS"]
    if ts != luc.g["TS"]:
        raise AssertionError("TS drifted between the twins")
    e_py = [ts, ts, 0, True, 0.0, 0.0]
    e_lua = lua.table(ts, ts, 0, True, 0.0, 0.0)
    pyc.ns["_ai_retarget"](e_py)
    luc.g._ai_retarget(e_lua)
    check("_ai_retarget(boxed in)", (), (), e_py, lua_list(e_lua))

    # 3. _move_tank's clamps: a tank parked outside the field on both axes, with
    #    speed 0 so the step itself is a no-op and only the clamps can move it.
    for pos in ((-50, -50), (999, 999)):
        t_py = pyc.ns["_make_player"](4)
        t_lua = luc.g._make_player(4)
        t_py[0], t_py[1] = pos
        t_lua[1], t_lua[2] = pos
        pyc.ns["_move_tank"](t_py, 1, 1, 0.0, 0.0)
        luc.g._move_tank(t_lua, 1, 1, 0.0, 0.0)
        check("_move_tank(clamp %r)" % (pos,), (), (), list(t_py),
              lua_list(t_lua))
    pyc.ns["players"] = py_players
    return bad


def run_parity(frames=600, verbose=True):
    config = _load_config()
    script = _button_script(frames)

    auto = dict(config)
    auto["autoplay"] = 1
    ok_auto = run_scenario("autoplay", auto, {}, frames, verbose,
                           units=True)

    manual = dict(config)
    manual["autoplay"] = 0
    ok_manual = run_scenario("manual", manual, script, frames, verbose)

    # The sitting duck: autoplay off and no buttons at all. This is the only run
    # that reaches the "all players dead, no lives left" game over (every other
    # scenario loses the BASE first), and the `if auto and not any_in` test with a
    # falsy auto -- Lua's truthy 0 trap.
    ok_idle = run_scenario("idle", manual, {}, frames, verbose)

    # The _wave_size clamps, which the shipped config (6, inside 1..16) can't reach.
    ok_lo = run_scenario("enemies=0", dict(auto, enemies=0), {}, 240, verbose)
    ok_hi = run_scenario("enemies=99", dict(auto, enemies=99), {}, 240, verbose)

    ok = ok_auto and ok_manual and ok_idle and ok_lo and ok_hi
    if verbose:
        print("PARITY %s (autoplay=%s manual=%s idle=%s clamp_lo=%s clamp_hi=%s)"
              % ("OK" if ok else "FAIL", ok_auto, ok_manual, ok_idle, ok_lo,
                 ok_hi))
    return ok


if __name__ == "__main__":
    raise SystemExit(0 if run_parity() else 1)
