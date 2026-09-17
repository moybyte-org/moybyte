"""Brick Siege Python-vs-Lua parity harness (#67, host edition).

The sibling of host_parity.py (sakura), for the console's heaviest seed cart:
runs the REAL system_carts/brick_siege.moy/main.py and its line-faithful Lua port
brick_siege_lua.moy/main.lua side by side for N deterministic frames -- same
seeded PRNG, same scripted buttons, same manifest config, same tilemap -- and
after EVERY frame compares

  * the whole draw stream, call for call (cls / map / spr / rect / print / sfx),
  * the whole game state (tanks, enemies, bullets, booms, spawn_q, spawn_t,
    base_alive, score, state, state_t, t, shake), value for value AND type for
    type (an int that became a float is a real bug here: the HUD stringifies
    score and the spawn count), and
  * the TILEMAP, cell for cell -- the cart destroys brick cells with mset(), so
    the map is game state and a one-cell divergence in what a bullet crumbled
    would otherwise hide until it changed a collision.

THE LUA SIDE IS THE SHIPPED VM. main.lua runs under runtime/lua_host's
MoycoreHostRun -- libmoy's binding over the vendored Lua 5.4 both boards
compile, LUA_32BITS and all -- and the fake API reaches it the way the console's
does: the namespace is registered on top of libmoy's table, and this harness's
prelude rides the namespace's `_moy_prelude` hook. mget/mset and btn/btnp are
libmoy's own, over the cart's real TileMap cells and the real input snapshot;
the draw verbs are shadowed in Lua to RECORD rather than raster, and every
record crosses to Python as one string through the trampoline a cart's
extension verbs use. State is read back the same way (a Lua-side dump), because
the binding marshals numbers and strings and nothing else.

WHAT PARITY MEANS UNDER 32-BIT FLOATS. The Python cart computes in CPython
doubles; the Lua cart computes in float32, because that is what it computes in
on glass (and so does the Python cart there -- MicroPython's float is a C
float, which no CPython harness can reproduce). So the two sides cannot agree
bit-for-bit on every float, and the contract is what a faithful port CAN hold:

  * integers, booleans, strings and the tilemap: EXACT;
  * the draw stream: the same calls in the same order with EXACT arguments --
    the cart truncates every coordinate before it draws, so a draw argument is
    an integer, and one pixel of drift is a failure;
  * every float in the game state: within FLOAT_TOL of the double, scaled by
    the larger of 1 and its magnitude (parity_wire.py says why absolute). The
    only floats that differ at all are the rnd-derived timers -- every
    position and clock is exact at this dt -- and the summary line prints the
    largest deviation seen, so a widening is visible.

DT IS 1/32, NOT 1/30. At 1/30 an enemy tank advances 42/30 = 1.4 px a frame,
which neither width holds exactly and float32 holds worse: by frame 15 the two
sums truncate to x=37 and x=38, one pixel apart in the draw stream, and at
frame 32 the auto-pilot's nearest-axis test reads that pixel and turns the
player's tank a different way -- from there the two games draw different
randoms and nothing compares (measured; run this file with `30` to see it).
The timers make it worse, since 0.5/1.4/1.6/2.0 s are exact multiples of
1/30 and cross zero on a frame boundary where the two widths disagree about
the sign of the remainder. All of that is the float width, not the port.
1/32 is a dyadic rational: every timer and every constant-speed position is
then exact in BOTH widths, so a discrete decision flips only where the port
is wrong.

Two scenarios run, because the cart has two input paths:
  * autoplay -- config autoplay=1 and no buttons: the attract auto-pilot drives,
    so the run is input-free and entirely PRNG-driven (this is the long run).
  * manual -- config autoplay=0 plus a scripted button track (held directions +
    fire edges), exercising the btn()/btnp() branch and _move_tank.

The PRNG is one LCG written twice -- Python below, Lua in the prelude -- and the
Lua one is written for 32-bit integers (a mask, not a modulo: 2^31 is not an
integer literal there). `_check_prng_twins` proves both cursors walk the same
31-bit sequence before any cart runs; the FLOAT each side makes of a cursor is
where the widths differ, and that lands in the tolerance above.

Run:   .venv/bin/python experiments/lua_bridge/brick_parity.py
Also wired into pytest as tests/test_lua_brick_siege_parity.py (skips without
a C compiler for the host binding, like every other host Lua test).
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
from runtime.lua_host import MoycoreHostRun       # noqa: E402
from parity_wire import (decode, same, FloatStats, Lcg, PRNG_LUA,   # noqa: E402
                         check_prng_twins, ENC_LUA, FakeWs, FLOAT_TOL)

_CARTS = os.path.join(_ROOT, "system_carts")
PY_CART_DIR = os.path.join(_CARTS, "brick_siege.moy")        # the Python original
LUA_CART_DIR = os.path.join(_CARTS, "brick_siege_lua.moy")   # the #67 A/B twin
DT = 1.0 / 32.0
SEED = 0xC0FFEE % 2147483648

# The state globals both sides must agree on, in comparison order.
STATE_LISTS = ("tanks", "enemies", "bullets", "booms")
STATE_SCALARS = ("spawn_q", "spawn_t", "base_alive", "score", "state",
                 "state_t", "t", "shake")
STATE_NAMES = list(STATE_LISTS) + list(STATE_SCALARS) + ["tilemap"]


# The scripted button track for the "manual" scenario: a dict of
# frame -> (held-button tuple, pressed-button tuple). Built once in Python and
# handed to both sides through the same fake InputState, so no input arithmetic
# is duplicated across the language boundary. Directions are HELD in runs
# (that's how a kid plays); "a" arrives as a press EDGE only, which is btnp's
# contract.
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
        self.records = []              # the Lua side's STATE / unit dumps
        self.frame = -1
        self.lcg = Lcg(SEED)
        self.config = config
        self.script = script
        self.tilemap = None            # set by reset()
        self.bg = None                 # the DECLARED backdrop, if any

    def reset(self, cart_dir):
        del self.events[:]
        del self.records[:]
        self.frame = -1
        self.lcg.state = SEED
        self.tilemap = _load_map(cart_dir)
        self.bg = None

    def restore_bg(self):
        """Mirror host_api's _restore_bg: a DECLARED background is repainted as a
        cls() before the frame's first draw. The Python cart declares one with
        background(); the Lua twin is moy core 0.1 ONLY (no `layers` extension)
        so it calls cls() itself as _draw's first statement. That is exactly why
        the two draw streams stay identical."""
        if self.bg is not None:
            self.events.append(("cls", self.bg))

    # -- input (the fake InputState the Lua run snapshots, and the Python
    #    cart's btn/btnp) --------------------------------------------------------
    def _keys(self):
        return self.script.get(self.frame, ((), ()))

    def held(self, name):
        return name in self._keys()[0]

    def pressed(self, name):
        return name in self._keys()[1]

    def pointer(self):
        return None                    # a buttons-only cart: no touch, ever

    def btn(self, name, player=0):
        return self.held(name)

    def btnp(self, name, player=0):
        return self.pressed(name)

    # -- the verbs -----------------------------------------------------------
    def ns(self):
        ev = self.events

        def background(c=None):
            # NOT a draw event -- host_api's background() only DECLARES; the
            # pixels arrive via restore_bg()'s cls() at frame start.
            self.bg = c

        def rec(line):
            # The Lua side's one upcall: a draw event, or a STATE/unit dump.
            if line.startswith("STATE|") or line.startswith("unit|"):
                self.records.append(line)
            else:
                ev.append(tuple(decode(line)))

        return {
            "W": 320, "H": 240,
            "cfg": lambda k, d=None: self.config.get(k, d),
            "rnd": self.lcg.rnd,
            "col": palette.color,
            "btn": self.btn, "btnp": self.btnp,
            # The #65 roster verb: this is a single-player determinism check, so
            # one player is the right answer -- and the carts read it to decide
            # how many tanks to field. libmoy's own players() answers the same
            # 1 to the Lua twin (the snapshot's default roster).
            "players": lambda: 1,
            "mget": lambda x, y: self.tilemap.mget(x, y),
            "mset": lambda x, y, tile: self.tilemap.mset(x, y, tile),
            "background": background,
            "cls": lambda c=0: ev.append(("cls", c)),
            "map": lambda mx=0, my=0, w=None, h=None, sx=0, sy=0,
            colorkey=-1, scale=1:
                ev.append(("map", mx, my, w, h, sx, sy, colorkey, scale)),
            "spr": lambda n, x, y, colorkey=-1, scale=1, flip=0, w=1, h=1:
                ev.append(("spr", n, x, y, colorkey, scale)),
            "rect": lambda x, y, w, h, c: ev.append(("rect", x, y, w, h, c)),
            "print": lambda s, x, y, c, scale=1:
                ev.append(("print", s, x, y, c, scale)),
            "sfx": lambda n, chan=None: ev.append(("sfx", n, chan)),
            "__rec": rec,
        }

    def take_events(self):
        # Cleared IN PLACE, never rebound: ns() closes over this exact list, so
        # `self.events = []` would silently orphan the recorder and compare
        # nothing after frame 0 (it did, for one revision of this file).
        ev = list(self.events)
        del self.events[:]
        return ev

    def take_records(self):
        r = list(self.records)
        del self.records[:]
        return r

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

    def tick(self, dt):
        self.ns["_update"](dt)
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

# The harness prelude, run by MoycoreHostRun AFTER libmoy's table and the
# shared handle prelude, BEFORE the cart: the recording shadows of the draw
# verbs (each with the defaults the Python fake applies, so a call the cart
# makes with fewer arguments records the same tuple on both sides), the PRNG,
# cfg over the scenario's table, and the state dump.
PRELUDE = ENC_LUA + PRNG_LUA + """
do
  local rec, enc = __rec, __enc
  local function ev(name, ...)
    local parts = { name }
    for i = 1, select("#", ...) do
      parts[i + 1] = enc((select(i, ...)))
    end
    rec(table.concat(parts, "|"))
  end
  function cls(c) ev("cls", c or 0) end
  function map(mx, my, w, h, sx, sy, ck, sc)
    ev("map", mx or 0, my or 0, w, h, sx or 0, sy or 0, ck or -1, sc or 1)
  end
  function spr(n, x, y, ck, sc) ev("spr", n, x, y, ck or -1, sc or 1) end
  function rect(x, y, w, h, c) ev("rect", x, y, w, h, c) end
  function print(s, x, y, c, sc) ev("print", s, x, y, c, sc or 1) end
  function sfx(n, ch) ev("sfx", n, ch) end
  local CFG = __CFG
  function cfg(k, d)
    local v = CFG[k]
    if v == nil then return d end
    return v
  end
  function __dump()
    rec("STATE|" .. enc(tanks) .. "|" .. enc(enemies) .. "|" .. enc(bullets)
        .. "|" .. enc(booms) .. "|" .. enc(spawn_q) .. "|" .. enc(spawn_t)
        .. "|" .. enc(base_alive) .. "|" .. enc(score) .. "|" .. enc(state)
        .. "|" .. enc(state_t) .. "|" .. enc(t) .. "|" .. enc(shake))
  end
end
"""


def _cfg_lua(config):
    rows = []
    for k, v in config.items():
        if isinstance(v, bool):
            rows.append("[%r] = %s" % (k, "true" if v else "false"))
        elif isinstance(v, (int, float)):
            rows.append("[%r] = %r" % (k, v))
        else:
            rows.append("[%r] = %s" % (k, json.dumps(str(v))))
    return "__CFG = { %s }\n" % ", ".join(rows)


class LuaCart:
    """Run main.lua under MoycoreHostRun with the same fake API."""

    def __init__(self, console):
        self.console = console
        console.reset(LUA_CART_DIR)
        self.run = None

    def set_frame(self, f):
        self.console.frame = f

    def init(self):
        # The run is opened here rather than in __init__: load() runs _init,
        # and the Python side's _init is a separate call the scenario makes at
        # the same point.
        ns = self.console.ns()
        ns["_moy_prelude"] = (_cfg_lua(self.console.config)
                              + "__SEED = %d\n" % SEED + PRELUDE)
        with open(os.path.join(LUA_CART_DIR, "main.lua")) as fh:
            src = fh.read()
        self.run = MoycoreHostRun(FakeWs(self.console, self.console.tilemap),
                                  ns, src)

    def tick(self, dt):
        self.run.update(dt)            # _update then _draw, in the C loop

    def state(self):
        err = self.run.exec("__dump()", "dump")
        if err:
            raise AssertionError("lua state dump: " + err)
        recs = self.console.take_records()
        if len(recs) != 1 or not recs[0].startswith("STATE|"):
            raise AssertionError("lua state dump: got %r" % (recs,))
        out = decode(recs[0])[1:]
        out.append(self.console.cells())
        return out

    def exec(self, src, name):
        err = self.run.exec(src, name)
        if err:
            raise AssertionError("%s: %s" % (name, err))
        return self.console.take_records()

    def close(self):
        if self.run is not None:
            self.run.close()
            self.run = None


# --- comparison ----------------------------------------------------------------

def _report_events(frame, ev_py, ev_lua, verbose):
    bad = 0
    if len(ev_py) != len(ev_lua):
        if verbose:
            print("frame %d: event count py=%d lua=%d"
                  % (frame, len(ev_py), len(ev_lua)))
        bad += 1
    for i, (a, b) in enumerate(zip(ev_py, ev_lua)):
        # Draw arguments are EXACT: every coordinate the cart issues is an int.
        if not same(a, b, 0.0):
            if verbose:
                print("frame %d event %d: py=%r lua=%r" % (frame, i, a, b))
            bad += 1
            if bad >= 5:
                break
    return bad


def _report_state(frame, st_py, st_lua, verbose, stats):
    bad = 0
    for name, a, b in zip(STATE_NAMES, st_py, st_lua):
        if not same(a, b, FLOAT_TOL, stats):
            if verbose:
                print("frame %d state %s: py=%r lua=%r" % (frame, name, a, b))
            bad += 1
    return bad


# --- the run -------------------------------------------------------------------

def run_scenario(name, config, script, frames, verbose=True, units=False,
                 stats=None):
    _check_maps()
    stats = stats if stats is not None else FloatStats()
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
            luc.close()
            return False
        luc.close()
        pyc = PyCart(FakeConsole(config, script))
        luc = LuaCart(FakeConsole(config, script))
        pyc.init()
        luc.init()
    ev_py = pyc.console.take_events()
    n_events = len(ev_py)
    bad = _report_events(-1, ev_py, luc.console.take_events(), verbose)
    bad += _report_state(-1, pyc.state(), luc.state(), verbose, stats)
    rounds = 0                       # how many times the wave ended + restarted
    banners = set()

    for f in range(frames):
        pyc.set_frame(f)
        luc.set_frame(f)
        was = pyc.ns["state"]
        pyc.tick(DT)
        luc.tick(DT)
        if pyc.ns["state"] != was:
            banners.add(pyc.ns["state"])
            if pyc.ns["state"] == 0:
                rounds += 1
        ev_py = pyc.console.take_events()
        n_events += len(ev_py)
        bad += _report_events(f, ev_py, luc.console.take_events(), verbose)
        bad += _report_state(f, pyc.state(), luc.state(), verbose, stats)
        if bad > 10:
            print("PARITY[%s]: too many mismatches, stopping early" % name)
            luc.close()
            return False

    if verbose:
        st = dict(zip(STATE_NAMES, pyc.state()))
        print("PARITY[%s] %s: %d frames, %d draw events + %d state fields "
              "compared; saw score=%r banners=%s restarts=%d, "
              "%d/%d bricks left; %s"
              % (name, "OK" if bad == 0 else "FAIL", frames, n_events,
                 (frames + 1) * len(STATE_NAMES),
                 st["score"], sorted(banners), rounds,
                 sum(1 for c in st["tilemap"] if c == 9),
                 _BRICKS0, stats))
    luc.close()
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

UNITS_LUA = """
do
  local enc, rec = __enc, __rec
  local function unit(label, ret, obj)
    rec("unit|" .. enc(label) .. "|" .. enc(ret) .. "|" .. enc(obj) .. "|"
        .. enc(__rnd_state))
  end
  -- 1. _ai_player with nothing alive to shoot -> (0, 0, false).
  local saved = enemies
  enemies = {}
  local p = _make_player(4)
  local a, b, c = _ai_player(p, %(dt)r)
  unit("_ai_player(no target)", { a, b, c }, p)
  enemies = saved
  -- 2. _ai_retarget on an enemy boxed in on all four sides -- sat on the steel
  --    block at cell (1, 1), where every 2px probe still overlaps it, so the
  --    four-try loop falls through to the last-resort heading.
  local e = { TS, TS, 0, true, 0.0, 0.0 }
  _ai_retarget(e)
  unit("_ai_retarget(boxed in)", {}, e)
  -- 3. _move_tank's clamps: a tank parked outside the field on both axes, with
  --    speed 0 so the step itself is a no-op and only the clamps can move it.
  for _, pos in ipairs({ { -50, -50 }, { 999, 999 } }) do
    local tk = _make_player(4)
    tk[1], tk[2] = pos[1], pos[2]
    _move_tank(tk, 1, 1, 0.0, 0.0)
    unit("_move_tank(clamp " .. pos[1] .. ")", {}, tk)
  end
end
"""


def _unit_checks(pyc, luc, verbose):
    ns = pyc.ns
    lcg = pyc.console.lcg
    py = []

    def unit(label, ret, obj):
        py.append(("unit", label, list(ret), list(obj), lcg.state))

    saved = ns["enemies"]
    ns["enemies"] = []
    p = ns["_make_player"](4)
    unit("_ai_player(no target)", ns["_ai_player"](p, DT), p)
    ns["enemies"] = saved
    ts = ns["TS"]
    e = [ts, ts, 0, True, 0.0, 0.0]
    ns["_ai_retarget"](e)
    unit("_ai_retarget(boxed in)", (), e)
    for pos in ((-50, -50), (999, 999)):
        tk = ns["_make_player"](4)
        tk[0], tk[1] = pos
        ns["_move_tank"](tk, 1, 1, 0.0, 0.0)
        unit("_move_tank(clamp %d)" % pos[0], (), tk)

    lua = [tuple(decode(line)) for line in luc.exec(UNITS_LUA % {"dt": DT},
                                                    "units")]
    if luc.run.get_global("TS") != ts:
        raise AssertionError("TS drifted between the twins")
    bad = 0
    if len(lua) != len(py):
        if verbose:
            print("unit: %d checks py, %d lua" % (len(py), len(lua)))
        return 1
    for a, b in zip(py, lua):
        if not same(a, b, FLOAT_TOL):
            if verbose:
                print("unit %s: py=%r lua=%r" % (a[1], a[2:], b[2:]))
            bad += 1
    return bad


def run_parity(frames=600, verbose=True):
    check_prng_twins(SEED)
    config = _load_config()
    script = _button_script(frames)
    stats = FloatStats()

    auto = dict(config)
    auto["autoplay"] = 1
    ok_auto = run_scenario("autoplay", auto, {}, frames, verbose,
                           units=True, stats=stats)

    manual = dict(config)
    manual["autoplay"] = 0
    ok_manual = run_scenario("manual", manual, script, frames, verbose,
                             stats=stats)

    # The sitting duck: autoplay off and no buttons at all. This is the only run
    # that reaches the "all players dead, no lives left" game over (every other
    # scenario loses the BASE first), and the `if auto and not any_in` test with a
    # falsy auto -- Lua's truthy 0 trap.
    ok_idle = run_scenario("idle", manual, {}, frames, verbose, stats=stats)

    # The _wave_size clamps, which the shipped config (6, inside 1..16) can't reach.
    ok_lo = run_scenario("enemies=0", dict(auto, enemies=0), {}, 240, verbose,
                         stats=stats)
    ok_hi = run_scenario("enemies=99", dict(auto, enemies=99), {}, 240, verbose,
                         stats=stats)

    ok = ok_auto and ok_manual and ok_idle and ok_lo and ok_hi
    if verbose:
        print("PARITY %s (autoplay=%s manual=%s idle=%s clamp_lo=%s clamp_hi=%s) "
              "floats: %s"
              % ("OK" if ok else "FAIL", ok_auto, ok_manual, ok_idle, ok_lo,
                 ok_hi, stats))
    return ok


if __name__ == "__main__":
    if len(sys.argv) > 1:
        DT = 1.0 / float(sys.argv[1])
    raise SystemExit(0 if run_parity(frames=3000) else 1)
