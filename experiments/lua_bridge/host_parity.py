"""Sakura Python-vs-Lua parity harness (#67 Phase 4, host edition).

Runs the REAL system_carts/sakura.moy/main.py and its line-faithful Lua port
main.lua side by side for N deterministic frames -- same seeded PRNG, same
scripted touch, same manifest config, and the shed scene both carts ship,
parsed by the shared `widgets.Scenes` -- recording every draw call each side
makes (spr / make_layer / layer spr / draw_layer) through a minimal fake API,
then compares the two streams and the final petal state.

THE LUA SIDE IS THE SHIPPED VM: main.lua runs under runtime/lua_host's
MoycoreHostRun (libmoy's binding over the vendored Lua 5.4 both boards
compile, LUA_32BITS and all), with the fake API registered the way the
console's is. The object-valued verbs -- make_layer, image, scene -- reach the
cart through the SAME int-handle glue and Lua prelude the boards run
(runtime/lua_ext.py), so this proves that seam as well as the port; touch()
is libmoy's own, over the run's real input snapshot; spr is shadowed in Lua to
RECORD, and the record crosses as one string (parity_wire.py).

WHAT PARITY MEANS UNDER 32-BIT FLOATS. The petal physics is inexact
arithmetic on every axis -- a sine table, a per-petal fall speed drawn from
rnd, a breeze that is not a dyadic number -- so the two widths drift apart by
a few float32 ulps a frame, and after hundreds of frames a petal's truncated
pixel position differs by one wherever its double sits within that drift of
an integer. Once in the run that drift falls on the SHED LINE: a petal whose
float32 y crosses H+4 a frame before its double is re-shed a frame early, and
that frame's sprite lands hundreds of pixels from its twin. The contract is
what holds through both:

  * the draw stream: the same calls in the same order, the same layer
    handles, images and tiles, every sprite within PIXEL_TOL -- except a
    STRADDLE, a sprite pair with one side at or past the shed line, which is
    counted and capped (STRADDLES_MAX);
  * the PRNG cursors: EQUAL at the end of every frame that is not a straddle
    or the frame after one. Every _shed draws three randoms from the shared
    stream, so a straddle offsets the cursors for one frame and nothing else
    may; a second petal shedding in between would hand later petals different
    randoms, and every one of them would then fail the stream;
  * the final petal state: every float within DRIFT_TOL, `base` exact, and a
    straddled petal -- one frame behind on one side by construction -- checked
    to within STRADDLE_PX instead.

DT is 1/32 for the reason brick_parity.py gives (a dyadic step keeps every
clock exact in both widths); the run's summary line prints the largest pixel
and float deviation it saw, and how many straddles.

Run:   .venv/bin/python experiments/lua_bridge/host_parity.py
Also wired into pytest as tests/test_lua_sakura_parity.py (skips without a C
compiler for the host binding, like every other host Lua test).
"""

import json
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.join(_HERE, "..", "..")
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from runtime.widgets import Scenes                             # noqa: E402
from runtime.lua_host import MoycoreHostRun                     # noqa: E402
from parity_wire import (decode, same, FloatStats, Lcg, PRNG_LUA,   # noqa: E402
                         check_prng_twins, ENC_LUA, FakeWs, FLOAT_TOL)

_CARTS = os.path.join(_ROOT, "system_carts")
PY_CART_DIR = os.path.join(_CARTS, "sakura.moy")        # the Python original
LUA_CART_DIR = os.path.join(_CARTS, "sakura_lua.moy")   # the #67 A/B twin
DT = 1.0 / 32.0
SEED = 0xC0FFEE % 2147483648
PIXEL_TOL = 1          # a truncated float32 position may land one pixel over
SHED_LINE = 240 + 4.0  # the cart's `p[1] > H + 4.0`
STRADDLES_MAX = 3      # shed-line straddles tolerated in a run (1 seen in 600)
STRADDLE_PX = 3        # a straddled petal, one frame apart: within this
DRIFT_TOL = 1e-4       # 600 frames of inexact float32 steps: 2.6e-5 seen,
                       # under the linear bound (600 x ~5 ops x 6e-8), see FloatStats

# The touch script: finger down for a sweep through the petal field (integer
# coords, so both sides see the exact same numbers), up otherwise. Built once
# in Python and read by both sides through the same fake console.
TOUCH_FRAMES = {f: (30 + ((f - 120) * 9) // 20, 40 + ((f - 120) * 7) // 25)
                for f in range(120, 480)}


def _load_config():
    """Both carts' manifest configs, drift-guarded: the twins must tune alike or
    the A/B (and this parity run) compares different scenes."""
    with open(os.path.join(PY_CART_DIR, "manifest.json")) as fh:
        py_cfg = json.load(fh)["config"]
    with open(os.path.join(LUA_CART_DIR, "manifest.json")) as fh:
        lua_cfg = json.load(fh)["config"]
    if py_cfg != lua_cfg:
        raise AssertionError("sakura.moy and sakura_lua.moy configs drifted: "
                             "%r != %r" % (py_cfg, lua_cfg))
    return py_cfg


def _load_scene():
    """The shed points, from the .moyscene both twins ship -- through the SHARED
    `widgets.Scenes`, which is the console's own parser, not a second one. The
    two files are compared byte-for-byte for the same reason the configs are:
    the twins must shed from the same canopy or nothing below means anything."""
    blobs = []
    for d in (PY_CART_DIR, LUA_CART_DIR):
        with open(os.path.join(d, "scenes", "blossoms.moyscene")) as fh:
            blobs.append(fh.read())
    if blobs[0] != blobs[1]:
        raise AssertionError("sakura.moy and sakura_lua.moy scenes drifted")
    return Scenes({"blossoms": blobs[0]}, ["blossoms"])


# --- the fake API both sides share -------------------------------------------

class _Pt:
    """The pointer the run's input snapshot reads (widgets.pointer_state)."""
    down = False
    click = False

    def __init__(self, x, y):
        self.x, self.y = x, y


class _PyLayer:
    def __init__(self, events, lid):
        self._events = events
        self._id = lid

    def spr(self, img, x, y, ck=-1, *a):
        self._events.append(("layer_spr", self._id, img, x, y))


class FakeConsole:
    """The recording namespace: draw events, the scripted touch, the PRNG."""

    def __init__(self, config, scenes):
        self.events = []
        self.records = []
        self.frame = -1
        self.lcg = Lcg(SEED)
        self.config = config
        self.scenes = scenes
        self._layers = 0

    # -- input (the fake InputState the Lua run snapshots, and the Python
    #    cart's touch()) ---------------------------------------------------
    def held(self, name):
        return False

    def pressed(self, name):
        return False

    def pointer(self):
        tp = TOUCH_FRAMES.get(self.frame)
        return None if tp is None else _Pt(tp[0], tp[1])

    def touch(self):
        tp = TOUCH_FRAMES.get(self.frame)
        return None if tp is None else (tp[0], tp[1], False, False)

    # -- the verbs -----------------------------------------------------------
    def make_layer(self, w, h):
        self.events.append(("make_layer", w, h))
        lay = _PyLayer(self.events, self._layers)
        self._layers += 1
        return lay

    def ns(self):
        ev = self.events

        def rec(line):
            if line.startswith("STATE|") or line.startswith("CUR|"):
                self.records.append(line)
            else:
                ev.append(tuple(decode(line)))

        return {
            "scene": self.scenes.scene,
            "W": 320, "H": 240,
            "cfg": lambda k, d=None: self.config.get(k, d),
            "rnd": self.lcg.rnd,
            "touch": self.touch,
            "image": lambda name: "img:" + name,
            "make_layer": self.make_layer,
            "draw_layer": lambda l, cx=0, cy=0:
                ev.append(("draw_layer", l._id, cx, cy)),
            "spr": lambda tile, x, y, ck=-1, *a:
                ev.append(("spr", tile, x, y, ck)),
            "__rec": rec,
        }

    def take_events(self):
        ev = list(self.events)
        del self.events[:]
        return ev

    def take_records(self):
        r = list(self.records)
        del self.records[:]
        return r


# --- Python side --------------------------------------------------------------

class PyCart:
    """Exec the real main.py under the fake recording API."""

    def __init__(self, console):
        self.console = console
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

    def tick(self, dt):
        self.ns["_update"](dt)
        self.ns["_draw"]()

    def petal_state(self):
        return ([[float(v) for v in p] for p in self.ns["petals"]],
                float(self.ns["t"]), int(self.ns["base"]))


# --- Lua side ------------------------------------------------------------------

PRELUDE = ENC_LUA + PRNG_LUA + """
do
  local rec, enc = __rec, __enc
  function spr(tile, x, y, ck)
    rec("spr|" .. enc(tile) .. "|" .. enc(x) .. "|" .. enc(y) .. "|"
        .. enc(ck or -1))
  end
  local CFG = __CFG
  function cfg(k, d)
    local v = CFG[k]
    if v == nil then return d end
    return v
  end
  function __dump()
    rec("STATE|" .. enc(petals) .. "|" .. enc(t) .. "|" .. enc(base))
  end
  function __cursor()
    rec("CUR|" .. enc(__rnd_state))
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
        self.run = None

    def set_frame(self, f):
        self.console.frame = f

    def init(self):
        ns = self.console.ns()
        ns["_moy_prelude"] = (_cfg_lua(self.console.config)
                              + "__SEED = %d\n" % SEED + PRELUDE)
        with open(os.path.join(LUA_CART_DIR, "main.lua")) as fh:
            src = fh.read()
        self.run = MoycoreHostRun(FakeWs(self.console), ns, src)

    def tick(self, dt):
        self.run.update(dt)            # _update then _draw, in the C loop

    def _probe(self, call):
        err = self.run.exec(call, "probe")
        if err:
            raise AssertionError("lua %s: %s" % (call, err))
        recs = self.console.take_records()
        if len(recs) != 1:
            raise AssertionError("lua %s: got %r" % (call, recs))
        return decode(recs[0])

    def petal_state(self):
        _tag, petals, t, base = self._probe("__dump()")
        return ([[float(v) for v in p] for p in petals], float(t), int(base))

    def cursor(self):
        return self._probe("__cursor()")[1]

    def close(self):
        if self.run is not None:
            self.run.close()
            self.run = None


# --- the run -------------------------------------------------------------------

class PixelStats:
    def __init__(self):
        self.n = 0
        self.max_off = 0
        self.off = 0
        self.straddles = []            # (frame, petal index)

    def note(self, a, b):
        self.n += 1
        d = max(abs(a[2] - b[2]), abs(a[3] - b[3]))
        if d:
            self.off += 1
        if d > self.max_off:
            self.max_off = d

    def straddled(self):
        return set(p for _f, p in self.straddles)

    def __str__(self):
        return ("%d sprites, %d off by up to %d px (tol %d), %d shed-line "
                "straddle(s) %s" % (self.n, self.off, self.max_off, PIXEL_TOL,
                                    len(self.straddles), self.straddles))


def _event_same(frame, i, a, b, px):
    """A draw event pair: exact, except a sprite's coordinates, which may sit
    PIXEL_TOL apart (same tile, same colorkey) -- or straddle the shed line,
    one side at/past it and the other re-shed, which is counted."""
    if a[0] == "spr" and b[0] == "spr" and len(a) == len(b) == 5:
        if a[1] != b[1] or a[4] != b[4]:
            return False
        if abs(a[2] - b[2]) <= PIXEL_TOL and abs(a[3] - b[3]) <= PIXEL_TOL:
            px.note(a, b)
            return True
        if max(a[3], b[3]) >= SHED_LINE - STRADDLE_PX:
            px.straddles.append((frame, i - 1))    # event 0 is draw_layer
            return True
        px.note(a, b)
        return False
    return same(a, b, 0.0)


def _compare(frame, ev_py, ev_lua, verbose, px):
    bad = 0
    if len(ev_py) != len(ev_lua):
        if verbose:
            print("frame %d: event count py=%d lua=%d"
                  % (frame, len(ev_py), len(ev_lua)))
        bad += 1
    for i, (a, b) in enumerate(zip(ev_py, ev_lua)):
        if not _event_same(frame, i, a, b, px):
            if verbose:
                print("frame %d event %d: py=%r lua=%r" % (frame, i, a, b))
            bad += 1
            if bad >= 5:
                break
    return bad


def _state_same(py_state, lua_state, straddled, stats, verbose):
    """Every petal walked (no short-circuit, so `stats` sees them all)."""
    ok = True
    for i, (a, b) in enumerate(zip(py_state[0], lua_state[0])):
        if i in straddled:
            near = (abs(a[0] - b[0]) <= STRADDLE_PX
                    and abs(a[1] - b[1]) <= STRADDLE_PX and a[5] == b[5])
            if not near:
                ok = False
                if verbose:
                    print("  straddled petal %d: py=%r lua=%r" % (i, a, b))
            continue
        if not same(a, b, DRIFT_TOL, stats):
            ok = False
            if verbose:
                print("  petal %d: py=%r lua=%r" % (i, a, b))
    if len(py_state[0]) != len(lua_state[0]):
        ok = False
        if verbose:
            print("  petal count: py=%d lua=%d"
                  % (len(py_state[0]), len(lua_state[0])))
    if not same(py_state[1], lua_state[1], FLOAT_TOL, stats):
        ok = False
        if verbose:
            print("  t: py=%r lua=%r" % (py_state[1], lua_state[1]))
    if py_state[2] != lua_state[2]:
        ok = False
        if verbose:
            print("  base: py=%r lua=%r" % (py_state[2], lua_state[2]))
    return ok


def run_parity(frames=600, verbose=True):
    check_prng_twins(SEED)
    config, scenes = _load_config(), _load_scene()
    pyc = PyCart(FakeConsole(config, scenes))
    luc = LuaCart(FakeConsole(config, scenes))
    px = PixelStats()

    pyc.init()
    luc.init()
    mismatches = _compare(-1, pyc.console.take_events(),
                          luc.console.take_events(), verbose, px)

    for f in range(frames):
        pyc.set_frame(f)
        luc.set_frame(f)
        pyc.tick(DT)
        luc.tick(DT)
        mismatches += _compare(f, pyc.console.take_events(),
                               luc.console.take_events(), verbose, px)
        straddle_frames = set(fr for fr, _p in px.straddles)
        if (pyc.console.lcg.state != luc.cursor()
                and f not in straddle_frames and f - 1 not in straddle_frames):
            if verbose:
                print("frame %d: PRNG cursors differ, py=%d lua=%r"
                      % (f, pyc.console.lcg.state, luc.cursor()))
            mismatches += 1
        if mismatches > 10:
            print("PARITY: too many mismatches, stopping early")
            luc.close()
            return False
    if len(px.straddles) > STRADDLES_MAX:
        if verbose:
            print("PARITY: %d shed-line straddles, more than %d"
                  % (len(px.straddles), STRADDLES_MAX))
        mismatches += 1
    if pyc.console.lcg.state != luc.cursor():
        if verbose:
            print("PARITY: PRNG cursors differ at the end")
        mismatches += 1

    stats = FloatStats()
    py_state = pyc.petal_state()
    lua_state = luc.petal_state()
    state_ok = _state_same(py_state, lua_state, px.straddled(), stats, verbose)
    ok = mismatches == 0 and state_ok
    if verbose:
        n_events = frames * (1 + len(py_state[0]))
        print("PARITY %s: %d frames, %d draw events compared, final petal "
              "state %s; %s; %s (drift tol %.0e)"
              % ("OK" if ok else "FAIL", frames, n_events,
                 "within tolerance" if state_ok else "DIVERGED", px, stats,
                 DRIFT_TOL))
    luc.close()
    return ok


def run_bench(frames=2000):
    """Host-only ballpark: CPython vs the boards' Lua on x86 says nothing about
    the S3 (that number comes from the device), but a Lua port that is NOT
    faster than Python on the host would flag a porting blunder. Update-only,
    touch inactive: pure physics on both sides."""
    config, scenes = _load_config(), _load_scene()

    pyc = PyCart(FakeConsole(config, scenes))
    pyc.set_frame(-1)
    pyc.init()
    upd = pyc.update
    t0 = time.perf_counter()
    for _ in range(frames):
        upd(DT)
    py_ms = (time.perf_counter() - t0) * 1000.0 / frames

    luc = LuaCart(FakeConsole(config, scenes))
    luc.set_frame(-1)
    luc.init()
    t0 = time.perf_counter()
    err = luc.run.exec("for f = 1, %d do _update(%r) end" % (frames, DT),
                       "bench")
    lua_ms = (time.perf_counter() - t0) * 1000.0 / frames
    luc.close()
    if err:
        raise AssertionError("bench: " + err)

    print("BENCH (host, update-only, %d frames): python=%.4f ms/frame  "
          "lua=%.4f ms/frame  (%.1fx)" % (frames, py_ms, lua_ms,
                                          py_ms / lua_ms if lua_ms else 0))


if __name__ == "__main__":
    ok = run_parity()
    run_bench()
    raise SystemExit(0 if ok else 1)
