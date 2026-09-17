"""What the two parity harnesses share: the wire between the shipped Lua VM
and Python, the one PRNG, the tolerance, and the fake console a
`MoycoreHostRun` is opened over.

THE WIRE. `runtime/lua_binding` marshals numbers, strings and booleans across
its trampoline and nothing else, so a Lua-side record -- a draw call's
arguments, a table of game state -- crosses as ONE string that `ENC_LUA`
produces and `decode` reads back into Python values with their TYPES intact
(a Lua integer is an int, a Lua float a float). That type is part of the
contract: an int that became a float is a port bug, not a representation
detail.

THE TOLERANCE. The Lua side computes in float32 (LUA_32BITS, on every tier);
the Python side in doubles. `same` therefore compares ints, bools, strings
and list shapes EXACTLY, and a float to within FLOAT_TOL of the larger of 1
and its magnitude -- absolute below unit, relative above it. Absolute, because
the deviations are ROUNDING, not drift: a float32 result carries 24 bits, so a
timer of order 1 is off by at most ~1.2e-7 however small it has counted down
to, and a relative bound would fail it exactly there (a think timer at 6e-5
with the whole 1e-7 still on it). The bound is ~8 ulps at unit magnitude over
the largest deviation the twins' runs show (1.34e-7 on Brick Siege);
`FloatStats` prints that maximum in the summary line so a widening is visible
rather than absorbed.

THE PRNG. One 31-bit LCG, written in Python here and in Lua in PRNG_LUA. The
Lua one masks rather than reduces modulo 2^31, because under 32-bit integers
2^31 is a float literal and `state % 2^31` would round the cursor to 24 bits;
`(a * s + c) & 0x7FFFFFFF` wraps mod 2^32 first and takes the low 31 bits,
which is the same residue. `check_prng_twins` runs both cursors before any
cart does.
"""

import os
import sys

_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from runtime.lua_binding import HostLuaRun        # noqa: E402

# A float the two widths computed separately may differ by this much, times
# the larger of 1 and its magnitude.
FLOAT_TOL = 1e-6


# --- the shared PRNG -----------------------------------------------------------

class Lcg:
    def __init__(self, seed):
        self.state = seed

    def rnd(self, n=1.0):
        self.state = (1103515245 * self.state + 12345) % 2147483648
        return self.state / 2147483648.0 * n


PRNG_LUA = """
__rnd_state = __SEED
function rnd(n)
  __rnd_state = (1103515245 * __rnd_state + 12345) & 0x7FFFFFFF
  return __rnd_state / 2147483648.0 * (n or 1.0)
end
"""


def check_prng_twins(seed, draws=64):
    """Both LCG cursors walk the same 31-bit sequence from `seed`."""
    lua = HostLuaRun(bytearray(8 * 8 * 2), 8, 8)
    try:
        err = lua.exec("__SEED = %d\n" % seed + PRNG_LUA
                       + "for i = 1, %d do rnd() end\n" % draws, "prng")
        if err:
            raise AssertionError("prng twin: " + err)
        py = Lcg(seed)
        for _ in range(draws):
            py.rnd()
        if lua.get_global("__rnd_state") != py.state:
            raise AssertionError("PRNG twins diverge after %d draws: py=%d lua=%r"
                                 % (draws, py.state, lua.get_global("__rnd_state")))
    finally:
        lua.close()


# --- the wire ----------------------------------------------------------------

# `enc` in Lua: i<int>, f<%.9g>, T/F, N (nil), s<escaped text>, [a,b,...] for a
# sequence. %.9g round-trips a float32 exactly. Fields of a record are joined
# with `|` by the caller; the escape covers every delimiter.
ENC_LUA = r"""
do
  local fmt, mtype, type, concat = string.format, math.type, type, table.concat
  local function esc(s)
    return (s:gsub("[\\|,%[%]]", function(c) return "\\" .. c end))
  end
  local function enc(v)
    local t = type(v)
    if t == "number" then
      if mtype(v) == "integer" then return "i" .. fmt("%d", v) end
      return "f" .. fmt("%.9g", v)
    elseif t == "boolean" then
      return v and "T" or "F"
    elseif t == "nil" then
      return "N"
    elseif t == "string" then
      return "s" .. esc(v)
    elseif t == "table" then
      local parts = {}
      for i = 1, #v do parts[i] = enc(v[i]) end
      return "[" .. concat(parts, ",") .. "]"
    end
    return "?" .. t
  end
  __enc = enc
end
"""


def decode(line):
    """One record -> a list of Python values: the bare tag first (`spr`,
    `STATE`, `unit`), then its `|`-joined encoded fields."""
    tag, sep, line = line.partition("|")
    out = [tag]
    if not sep:
        return out
    pos = 0
    n = len(line)
    while pos <= n:
        v, pos = _decode_value(line, pos)
        out.append(v)
        if pos >= n:
            break
        if line[pos] != "|":
            raise ValueError("bad record at %d: %r" % (pos, line))
        pos += 1
    return out


def _decode_value(s, pos):
    c = s[pos] if pos < len(s) else ""
    if c == "[":
        pos += 1
        items = []
        if pos < len(s) and s[pos] == "]":
            return items, pos + 1
        while True:
            v, pos = _decode_value(s, pos)
            items.append(v)
            if s[pos] == ",":
                pos += 1
                continue
            if s[pos] == "]":
                return items, pos + 1
            raise ValueError("bad list at %d: %r" % (pos, s))
    if c == "T":
        return True, pos + 1
    if c == "F":
        return False, pos + 1
    if c == "N":
        return None, pos + 1
    if c == "s":
        pos += 1
        buf = []
        while pos < len(s) and s[pos] not in "|,]":
            if s[pos] == "\\":
                pos += 1
            buf.append(s[pos])
            pos += 1
        return "".join(buf), pos
    if c in "if":
        end = pos + 1
        while end < len(s) and s[end] not in "|,]":
            end += 1
        text = s[pos + 1:end]
        return (int(text) if c == "i" else float(text)), end
    raise ValueError("bad value at %d: %r" % (pos, s))


class FloatStats:
    """The largest float deviation a comparison saw: absolute, and scaled the
    way the tolerance is (by the larger of 1 and the magnitude)."""

    def __init__(self):
        self.n = 0
        self.max_abs = 0.0
        self.max_scaled = 0.0

    def note(self, a, b):
        self.n += 1
        d = abs(a - b)
        if d > self.max_abs:
            self.max_abs = d
        s = d / max(1.0, abs(a), abs(b))
        if s > self.max_scaled:
            self.max_scaled = s

    def __str__(self):
        return ("%d floats, max |d|=%.3g, max scaled=%.3g (tol %.0e)"
                % (self.n, self.max_abs, self.max_scaled, FLOAT_TOL))


def same(a, b, tol, stats=None):
    """Exact for ints/bools/strings/None/shape; a float within `tol` times the
    larger of 1 and its magnitude. A float against an int is a TYPE mismatch,
    never a near-miss."""
    if isinstance(a, bool) or isinstance(b, bool):
        return type(a) is type(b) and a == b
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        return len(a) == len(b) and all(same(x, y, tol, stats)
                                        for x, y in zip(a, b))
    if isinstance(a, float) and isinstance(b, float):
        if stats is not None:
            stats.note(a, b)
        if a == b:
            return True
        return abs(a - b) <= tol * max(1.0, abs(a), abs(b))
    return type(a) is type(b) and a == b


# --- the console a MoycoreHostRun is opened over -----------------------------

class _Canvas:
    def __init__(self, w=320, h=240):
        self.w, self.h = w, h
        self._buf = bytearray(w * h * 2)   # RGB565, what canvas_target reads
        self._wire = None


class _Project:
    def __init__(self, tilemap):
        self.tilemap = tilemap
        self.sheet = None
        self.flags = None


class _Input:
    """The fake InputState the run snapshots each tick: buttons from the
    scripted console, the pointer from it too (widgets.pointer_state reads
    `pointer.x/y`, so a frame with a touch publishes one, an idle frame None)."""

    def __init__(self, console):
        self._c = console

    def held(self, name):
        return self._c.held(name)

    def pressed(self, name):
        return self._c.pressed(name)

    @property
    def pointer(self):
        return self._c.pointer()


class FakeWs:
    def __init__(self, console, tilemap=None):
        self.canvas = _Canvas()
        self.project = _Project(tilemap)
        self.input = _Input(console)
