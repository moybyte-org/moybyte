"""Sakura Python-vs-Lua parity harness (#67 Phase 4, host edition).

Runs the REAL system_carts/sakura.moy/main.py and its line-faithful Lua port
main.lua side by side for N deterministic frames -- same seeded PRNG, same
scripted touch, same manifest config -- recording every draw call each side
makes (spr / make_layer / layer spr / draw_layer) through a minimal fake API,
then compares the two streams tuple-for-tuple and the final petal state
float-for-float. Both languages do IEEE double arithmetic, so a faithful port
must match EXACTLY -- any epsilon is a porting bug, not noise.

This deliberately fakes the API instead of importing the console: it proves the
PORT (the logic translation), which is what the #67 A/B instrument needs; the
engine-facing bridge semantics are proven separately (main.c, checksum-verified
on the XIAO S3 perf lab).

Needs `lupa` (the #67 Phase 3 host-runner dependency): .venv/bin/pip install lupa
Run:   .venv/bin/python experiments/lua_bridge/host_parity.py
Also wired into pytest as tests/test_lua_sakura_parity.py (skips without lupa).
"""

import json
import os
import time

_CARTS = os.path.join(os.path.dirname(__file__), "..", "..", "system_carts")
PY_CART_DIR = os.path.join(_CARTS, "sakura.moy")        # the Python original
LUA_CART_DIR = os.path.join(_CARTS, "sakura_lua.moy")   # the #67 A/B twin
DT = 1.0 / 30.0
SEED = 0xC0FFEE % 2147483648

# The touch script: finger down for a sweep through the petal field (integer
# coords, so both sides parse the exact same doubles), up otherwise. Built once
# in Python; the Lua side gets it as a generated table -- no arithmetic is
# duplicated across the language boundary.
TOUCH_FRAMES = {f: (30 + ((f - 120) * 9) // 20, 40 + ((f - 120) * 7) // 25)
                for f in range(120, 480)}


# --- the shared deterministic PRNG (LCG, 31-bit) -----------------------------
# Implemented twice (Python below, Lua in GLUE) because the Lua timing loop must
# not cross into Python per call; _check_prng_twins() proves the twins identical
# before any cart runs.

class Lcg:
    def __init__(self, seed):
        self.state = seed

    def rnd(self, n=1.0):
        self.state = (1103515245 * self.state + 12345) % 2147483648
        return self.state / 2147483648.0 * n


GLUE = """
__rnd_state = 0
function __seed(s) __rnd_state = s end
function rnd(n)
  if n == nil then n = 1.0 end
  __rnd_state = (1103515245 * __rnd_state + 12345) % 2147483648
  return __rnd_state / 2147483648.0 * n
end

function cfg(key, default)
  local v = __CFG[key]
  if v == nil then return default end
  return v
end

__frame = -1
function touch()
  local tp = __TOUCH[__frame]
  if tp == nil then return nil end
  return tp[1], tp[2], false, false
end

function image(name)
  return "img:" .. name
end

function make_layer(w, h)
  local l = { __id = __py_make_layer(w, h) }
  l.spr = function(self, img, x, y, ck)
    __py_layer_spr(self.__id, img, x, y)
  end
  return l
end

function draw_layer(l, cx, cy)
  __py_draw_layer(l.__id, cx, cy)
end

function spr(tile, x, y, ck)
  __py_spr(tile, x, y, ck)
end

function __bench_update(frames, dt)
  local t0 = os.clock()
  for f = 1, frames do
    _update(dt)
  end
  return (os.clock() - t0) * 1000.0 / frames
end
"""


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


# --- Python side --------------------------------------------------------------

class _PyLayer:
    def __init__(self, events, lid):
        self._events = events
        self._id = lid

    def spr(self, img, x, y, ck=-1, *a):
        self._events.append(("layer_spr", self._id, img, x, y))


class PyCart:
    """Exec the real main.py under the fake recording API."""

    def __init__(self, config):
        self.events = []
        self.frame = -1
        self.lcg = Lcg(SEED)
        self._layers = 0
        ns = {
            "W": 320, "H": 240,
            "cfg": lambda k, d=None: config.get(k, d),
            "rnd": self.lcg.rnd,
            "touch": self._touch,
            "image": lambda name: "img:" + name,
            "make_layer": self._make_layer,
            "draw_layer": lambda l, cx=0, cy=0:
                self.events.append(("draw_layer", l._id, cx, cy)),
            "spr": lambda tile, x, y, ck=-1, *a:
                self.events.append(("spr", tile, x, y, ck)),
        }
        with open(os.path.join(PY_CART_DIR, "main.py")) as fh:
            exec(fh.read(), ns)
        self.ns = ns

    def _touch(self):
        tp = TOUCH_FRAMES.get(self.frame)
        if tp is None:
            return None
        return (tp[0], tp[1], False, False)

    def _make_layer(self, w, h):
        self.events.append(("make_layer", w, h))
        lay = _PyLayer(self.events, self._layers)
        self._layers += 1
        return lay

    def take_events(self):
        ev, self.events = self.events, []
        return ev

    def petal_state(self):
        return ([[float(v) for v in p] for p in self.ns["petals"]],
                float(self.ns["t"]), int(self.ns["base"]))


# --- Lua side ------------------------------------------------------------------

class LuaCart:
    """Run main.lua under lupa with the same fake API, via the GLUE shims."""

    def __init__(self, config):
        # Lua 5.4 explicitly -- the version #67 vendors on the device; lupa's
        # default runtime tracks the newest release instead.
        try:
            from lupa import lua54
            self.lua = lua54.LuaRuntime()
        except ImportError:  # pragma: no cover - older lupa wheels
            import lupa
            self.lua = lupa.LuaRuntime()
        self.events = []
        self._layers = 0
        g = self.lua.globals()
        # NB: item syntax, not attributes -- g.__name inside a class would hit
        # Python name mangling and silently set _LuaCart__name.
        g["__py_make_layer"] = self._make_layer
        g["__py_layer_spr"] = lambda lid, img, x, y: \
            self.events.append(("layer_spr", lid, img, x, y))
        g["__py_draw_layer"] = lambda lid, cx, cy: \
            self.events.append(("draw_layer", lid, cx, cy))
        g["__py_spr"] = lambda tile, x, y, ck: \
            self.events.append(("spr", tile, x, y, ck))
        cfg_t = self.lua.table()
        for k, v in config.items():
            cfg_t[k] = v
        g["__CFG"] = cfg_t
        touch_t = self.lua.table()
        for f, (x, y) in TOUCH_FRAMES.items():
            touch_t[f] = self.lua.table(x, y)
        g["__TOUCH"] = touch_t
        g["W"] = 320
        g["H"] = 240
        self.lua.execute(GLUE)
        with open(os.path.join(LUA_CART_DIR, "main.lua")) as fh:
            self.lua.execute(fh.read())
        self.g = g

    def _make_layer(self, w, h):
        self.events.append(("make_layer", w, h))
        lid = self._layers
        self._layers += 1
        return lid

    def set_frame(self, f):
        self.g["__frame"] = f

    def take_events(self):
        ev, self.events = self.events, []
        return ev

    def petal_state(self):
        petals = []
        lp = self.g.petals
        for i in range(1, len(lp) + 1):
            petals.append([float(lp[i][j]) for j in range(1, 7)])
        return petals, float(self.g.t), int(self.g.base)


# --- the run -------------------------------------------------------------------

def _check_prng_twins(lua):
    py = Lcg(SEED)
    lua.execute("__seed(%d)" % SEED)
    lua_rnd = lua.globals().rnd
    for i in range(8):
        a, b = py.rnd(1.0), lua_rnd(1.0)
        if a != b:
            raise AssertionError("PRNG twins diverge at draw %d: %r != %r"
                                 % (i, a, b))


def run_parity(frames=600, verbose=True):
    config = _load_config()
    pyc = PyCart(config)
    luc = LuaCart(config)
    _check_prng_twins(luc.lua)

    # identical seed on both sides, then _init
    pyc.lcg.state = SEED
    luc.lua.execute("__seed(%d)" % SEED)
    pyc.ns["_init"]()
    luc.lua.eval("_init()")
    mismatches = _compare(-1, pyc.take_events(), luc.take_events(), verbose)

    for f in range(frames):
        pyc.frame = f
        luc.set_frame(f)
        pyc.ns["_update"](DT)
        luc.g._update(DT)
        pyc.ns["_draw"]()
        luc.g._draw()
        mismatches += _compare(f, pyc.take_events(), luc.take_events(), verbose)
        if mismatches > 10:
            print("PARITY: too many mismatches, stopping early")
            return False

    py_state = pyc.petal_state()
    lua_state = luc.petal_state()
    state_ok = py_state == lua_state
    if not state_ok and verbose:
        print("PARITY: final state differs")
        for i, (a, b) in enumerate(zip(py_state[0], lua_state[0])):
            if a != b:
                print("  petal %d: py=%r lua=%r" % (i, a, b))
                break
    ok = mismatches == 0 and state_ok
    if verbose:
        n_events = frames * (1 + len(py_state[0]))
        print("PARITY %s: %d frames, %d draw events compared, "
              "final petal state %s" % ("OK" if ok else "FAIL", frames,
                                        n_events, "identical" if state_ok
                                        else "DIVERGED"))
    return ok


def _compare(frame, ev_py, ev_lua, verbose):
    if ev_py == ev_lua:
        return 0
    bad = 0
    if len(ev_py) != len(ev_lua):
        if verbose:
            print("frame %d: event count py=%d lua=%d"
                  % (frame, len(ev_py), len(ev_lua)))
        bad += 1
    for i, (a, b) in enumerate(zip(ev_py, ev_lua)):
        if a != b:
            if verbose:
                print("frame %d event %d: py=%r lua=%r" % (frame, i, a, b))
            bad += 1
            if bad >= 5:
                break
    return bad


def run_bench(frames=2000):
    """Host-only ballpark: CPython vs liblua on x86 says nothing about the S3
    (that number comes from the main.c spike / the device bridge), but a Lua
    port that is NOT faster than Python on the host would flag a porting blunder."""
    config = _load_config()

    pyc = PyCart(config)
    pyc.lcg.state = SEED
    pyc.frame = -1              # touch inactive: pure physics
    pyc.ns["_init"]()
    upd = pyc.ns["_update"]
    t0 = time.perf_counter()
    for _ in range(frames):
        upd(DT)
    py_ms = (time.perf_counter() - t0) * 1000.0 / frames

    luc = LuaCart(config)
    luc.lua.execute("__seed(%d)" % SEED)
    luc.set_frame(-1)
    luc.lua.eval("_init()")
    lua_ms = luc.g["__bench_update"](frames, DT)

    print("BENCH (host, update-only, %d frames): python=%.4f ms/frame  "
          "lua=%.4f ms/frame  (%.1fx)" % (frames, py_ms, lua_ms,
                                          py_ms / lua_ms if lua_ms else 0))


if __name__ == "__main__":
    ok = run_parity()
    run_bench()
    raise SystemExit(0 if ok else 1)
