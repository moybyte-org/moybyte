# Map (grep -n a name to jump there):
#   Pointer                      the screen-space cursor (moy_input_ptr_t)
#   InputSource                  one producer's held set, key and pointer sample
#   InputTable                   the merged table every surface reads
#   HostInputTable               the same table over the host's eight names
#   pointer_state                the pointer a cart sees
#   kernel                       the drivers' table; keyboard/touch/trackball/kick
#   Bus                          a machine.I2C-shaped fake as the drivers' C bus
#   KbdDriver                    the T-Deck keyboard's C driver over a Bus
#   TouchDriver                  a touch controller's C driver over a Bus
#   HeldPoint                    the no-news contract on its own
#   map_point                    a raw point onto the glass
#   HidMachine                   the BLE keyboard central's machine over a fake stack
"""The input table (native/moy_input) on CPython, by ctypes: the module
`moy_input` as the boards and the browser import it, name for name
(docs/kernel_survival_2026-10.md section 4.1). A VM's `import moy_input` is the
native module (native/moy_input/modmoy_input.c), which always wins over this
file; CPython has no such module, so this is what it finds.

  NAMES            the buttons, in libmoy's moy_button order with the console's
                   own names after it; HOST_NAMES is the host's prefix of it
  InputTable       the merged input every surface reads, and the masks: all
                   fifteen names. HostInputTable is the same table over
                   HOST_NAMES, the eight a keyboard, a mouse and the browser
                   can press; a name outside a table's names is refused
  InputSource      one producer's held set and key, a row of kind SRC
  Pointer          a screen-space cursor: trackball-relative, touch-absolute
  pointer_state    the pointer a CART sees, one answer for every tier

Every producer writes its own source and only that; begin_frame() is the
union's one author, so read held()/pressed()/button_masks() after it, the way
the frame loop does (poll every source, then begin_frame, then handle_input).
`release_all` on a source means "I hold nothing"; on the table it means
"everybody let go". moy_input.h has the rest of the contract.
"""

from __future__ import annotations

import ctypes
import os

try:                                    # the host's runtime package
    from . import native_build
    from .ticks import _ticks_ms
except ImportError:                     # loaded flat, runtime/ on sys.path
    import native_build
    from ticks import _ticks_ms

_INPUT = os.path.join(native_build.ROOT, "native", "moy_input")
_SPINE = os.path.join(native_build.ROOT, "native", "moy_spine")
_CACHE = os.path.join(native_build.ROOT, ".build", "host_input")
_SHIM = os.path.join(_INPUT, "moy_input.c")

# libmoy's moy_button enum (lua_ext.MOY_BUTTONS, pinned against the vendored
# header by tests/test_moy_button_order.py), then the console's own buttons.
NAMES = ("left", "right", "up", "down", "a", "b", "run", "home",
         "x", "y", "stop", "save", "share", "select", "start")
HOST_NAMES = NAMES[:8]
_BIT = {n: i for i, n in enumerate(NAMES)}

SOURCES = 8
PLAYERS = 8
UNION = 0xFF
_TICKS_MASK = (1 << 30) - 1     # the VM's ticks_ms period
OK, STALE, FULL, NOMEM, BAD = range(5)

CURSOR_IDLE_MS = 2000   # hide the trackball cursor after this long with no movement
# How long a touchscreen pointer OUTLIVES the finger that made it: a cart wants
# a mouse, and a released drag must not read as "no pointer" on the next frame.
POINTER_LINGER_MS = 1500

P_NONE = 0
P_LIVE = 1                     # there is a pointer at all
P_HELD = 2                     # the finger/button is down this frame
P_CLICK = 4                    # ...and it went down THIS frame (the press edge)

_U8 = ctypes.c_uint8
_U32 = ctypes.c_uint32
_I32 = ctypes.c_int32
_I = ctypes.c_int
_P = ctypes.c_void_p
_B = ctypes.c_bool
_PU32 = ctypes.POINTER(_U32)


class Pointer(ctypes.Structure):
    """A screen-space cursor (moy_input_ptr_t). The trackball drives it
    relatively and shows it; touch places it absolutely and keeps it hidden;
    it auto-hides after `idle_ms` without trackball movement. `hovers` is set
    by a source that reports a position with nothing held (a mouse): such a
    pointer never expires, a touched one lingers POINTER_LINGER_MS. `fresh` says
    this frame's sample came from the hardware rather than a repeat."""

    _fields_ = [("w", _I32), ("h", _I32), ("x", _I32), ("y", _I32),
                ("idle_ms", _I32), ("_sampled", _U32), ("_last_move", _U32),
                ("click", _B), ("down", _B), ("hovers", _B), ("fresh", _B),
                ("visible", _B)]

    def __init__(self, w, h, idle_ms=CURSOR_IDLE_MS):
        super().__init__()
        _lib().moy_input_ptr_init(ctypes.byref(self), w, h, idle_ms, _now())

    def move(self, dx, dy):
        _lib().moy_input_ptr_move(ctypes.byref(self), int(dx), int(dy), _now())

    def place(self, x, y):
        _lib().moy_input_ptr_place(ctypes.byref(self), int(x), int(y), _now())

    def live(self):
        """Held, hovered, or released less than POINTER_LINGER_MS ago."""
        return _lib().moy_input_ptr_live(ctypes.byref(self), _now())

    def tick(self, now):
        _lib().moy_input_ptr_tick(ctypes.byref(self), now & _TICKS_MASK)


_SIGS = (
    ("moy_input_new", [_U8], _P),
    ("moy_input_free", [_P], None),
    ("moy_input_source", [_P, ctypes.c_char_p, _U8, _PU32], _I),
    ("moy_input_source_player", [_P, _U32, ctypes.POINTER(_U8)], _I),
    ("moy_input_set_player", [_P, _U32, _U8], _I),
    ("moy_input_set_held", [_P, _U32, _U8, _B], _I),
    ("moy_input_set_mask", [_P, _U32, _U32], _I),
    ("moy_input_release", [_P, _U32], _I),
    ("moy_input_set_key", [_P, _U32, _I32], _I),
    ("moy_input_key", [_P, _U32, _I32], _I),
    ("moy_input_source_key", [_P, _U32, ctypes.POINTER(_I32)], _I),
    ("moy_input_source_held", [_P, _U32], _U32),
    ("moy_input_begin_frame", [_P], None),
    ("moy_input_release_all", [_P], None),
    ("moy_input_clear_edges", [_P], None),
    ("moy_input_keep_edges", [_P], None),
    ("moy_input_tick_edges", [_P], None),
    ("moy_input_drop_edges", [_P], None),
    ("moy_input_masks", [_P, _U8, _PU32, _PU32], None),
    ("moy_input_released", [_P], _U32),
    ("moy_input_kept", [_P], _U32),
    ("moy_input_last_key", [_P], _I32),
    ("moy_input_set_last_key", [_P, _I32], None),
    ("moy_input_players", [_P, ctypes.POINTER(_U8)], _U8),
    ("moy_input_multi", [_P], _B),
    ("moy_input_text_mode", [_P], _B),
    ("moy_input_set_text_mode", [_P, _B], None),
    ("moy_input_ptr_init", [_P, _I32, _I32, _I32, _U32], None),
    ("moy_input_ptr_move", [_P, _I32, _I32, _U32], None),
    ("moy_input_ptr_place", [_P, _I32, _I32, _U32], None),
    ("moy_input_ptr_live", [_P, _U32], _B),
    ("moy_input_ptr_tick", [_P, _U32], None),
    ("moy_input_point", [_P, _U32, _I32, _I32, _B, _B, _B], _I),
    ("moy_input_ptr_apply", [_P, _P, _U32], _I),
)

_LIB = [None]


def build(verbose=False):
    return native_build.build("moy_input", _SHIM,
                              ("moy_input.h", "moy_htab.h", "moy_touch.h", "moy_drivers.h",
                               "moy_hid.h", "moy_touch.c", "moy_kbd.c", "moy_touchdev.c",
                               "moy_hid.c"),
                              _CACHE, libmoy_dir=(_INPUT, _SPINE), verbose=verbose)


def _lib():
    if _LIB[0] is None:
        path = build()
        if path is None:
            raise ImportError("moy_input: no C compiler for the host build")
        d = ctypes.CDLL(path)
        for name, args, res in _SIGS + _DRV_SIGS:
            fn = getattr(d, name)
            fn.argtypes = args
            fn.restype = res
        _LIB[0] = d
    return _LIB[0]


def _now():
    return _ticks_ms() & _TICKS_MASK


def _check(rc, what="input"):
    if rc == OK:
        return
    if rc == STALE:
        raise ValueError("stale %s handle" % what)
    if rc == FULL:
        raise OSError(28, "the input table's sources are full")
    if rc == BAD:
        raise ValueError("%s: refused" % what)
    raise MemoryError


class InputSource:
    """One producer's half of the input: its own held set and its own key.

    `release_all` here means "I hold nothing", which is NOT the table's
    "everybody let go": a driver clears ITS source; a modal that wants every
    button dropped calls the table's release_all()."""

    def __init__(self, state, name, h):
        self.state = state
        self.name = name
        self.h = h

    @property
    def player(self):
        p = _U8()
        _check(_lib().moy_input_source_player(self.state._t, self.h, ctypes.byref(p)))
        return p.value

    @player.setter
    def player(self, value):
        _check(_lib().moy_input_set_player(self.state._t, self.h, value), "player")

    def release_all(self):
        _lib().moy_input_release(self.state._t, self.h)

    def set_button(self, name, held):
        i = self.state._index(name)
        _lib().moy_input_set_held(self.state._t, self.h, i, bool(held))

    set_held = set_button

    def held_names(self):
        bits = _lib().moy_input_source_held(self.state._t, self.h)
        return {n for i, n in enumerate(NAMES) if bits & (1 << i)}

    @property
    def last_key(self):
        k = _I32()
        _lib().moy_input_source_key(self.state._t, self.h, ctypes.byref(k))
        return k.value

    @last_key.setter
    def last_key(self, value):
        """A source that did not type must not zero another source's key:
        ownership moves on a NEW nonzero value, and only the owner going quiet
        hands the slot to whoever else is still typing."""
        _lib().moy_input_set_key(self.state._t, self.h, value or 0)

    def point(self, x, y, down, edge=False, fresh=True):
        """This source's pointer sample, standing until it writes another."""
        _check(_lib().moy_input_point(self.state._t, self.h, int(x), int(y), bool(down),
                                      bool(edge), bool(fresh)), "point")

    def key(self, value):
        """A one-shot key: delivered as this source's key for exactly one frame."""
        _check(_lib().moy_input_key(self.state._t, self.h, value), "key")


class InputTable:
    """The merged input every surface reads: the union of the sources, its
    edges, the per-player views and the masks. player=None is the shell's view
    (every source); player=n is a cart's btn(name, n)."""

    BUTTONS = NAMES

    def __init__(self):
        self.pointer = None         # the screen-space Pointer, once wired
        n = len(self.BUTTONS)
        assert self.BUTTONS == NAMES[:n]
        self._n = n
        self._t = _lib().moy_input_new(n)
        if not self._t:
            raise MemoryError("moy_input: no table")
        self._srcs = {}
        self._m = (_U32(), _U32())
        self._default = self.source("local")

    def __del__(self):
        t = getattr(self, "_t", None)
        if t and _LIB[0] is not None:
            _LIB[0].moy_input_free(t)
            self._t = None

    def _index(self, name):
        i = _BIT.get(name)
        if i is None or i >= self._n:
            raise ValueError("unknown button: " + str(name))
        return i

    # -- sources -------------------------------------------------------------
    def source(self, name, player=0):
        """The named source a producer writes through. Idempotent: the same
        name always returns the same object, so a driver may re-resolve it."""
        s = self._srcs.get(name)
        if s is None:
            h = _U32()
            _check(_lib().moy_input_source(self._t, name.encode(), player, ctypes.byref(h)),
                   "source")
            s = InputSource(self, name, h.value)
            self._srcs[name] = s
        return s

    # -- the frame -------------------------------------------------------------
    def begin_frame(self):
        _lib().moy_input_begin_frame(self._t)

    def keep_edges(self):
        _lib().moy_input_keep_edges(self._t)

    def tick_edges(self):
        _lib().moy_input_tick_edges(self._t)

    def drop_edges(self):
        _lib().moy_input_drop_edges(self._t)

    def clear_edges(self):
        """No edge this frame and none from the snapshot: the tap that opened a
        modal must not also act inside it."""
        _lib().moy_input_clear_edges(self._t)

    def release_all(self):
        """EVERYBODY let go: every source, and the merged set. A driver saying
        "I hold nothing" wants source.release_all()."""
        _lib().moy_input_release_all(self._t)

    def set_button(self, name, held):
        """Writes the table's own source, "local"."""
        self._default.set_button(name, held)

    set_held = set_button

    @property
    def last_key(self):
        return _lib().moy_input_last_key(self._t)

    @last_key.setter
    def last_key(self, value):
        _lib().moy_input_set_last_key(self._t, value or 0)

    @property
    def text_mode(self):
        return _lib().moy_input_text_mode(self._t)

    @text_mode.setter
    def text_mode(self, on):
        _lib().moy_input_set_text_mode(self._t, bool(on))

    # -- the reads -------------------------------------------------------------
    def _masks(self, player):
        h, p = self._m
        _lib().moy_input_masks(self._t, UNION if player is None else player,
                               ctypes.byref(h), ctypes.byref(p))
        return h.value, p.value

    def button_masks(self, order, player=None, out=None):
        """(held, pressed) as bitmasks over `order`, in ONE call. `order` is
        the protocol's (callers pass lua_ext.MOY_BUTTONS): a mask packed in the
        wrong order gave every Lua cart a d-pad rotated a quarter turn, with no
        crash, no test and no frame hash. `player` is the caller's for the same
        reason; None is the union. `out`, a caller-owned two-slot list, is
        filled and returned instead of a new tuple."""
        h, p = self._masks(player)
        n = len(order)
        if tuple(order) == NAMES[:n]:
            m = (1 << n) - 1
            h &= m
            p &= m
        else:
            hh = pp = 0
            for i, name in enumerate(order):
                b = _BIT.get(name)
                if b is not None:
                    if h & (1 << b):
                        hh |= 1 << i
                    if p & (1 << b):
                        pp |= 1 << i
            h, p = hh, pp
        if out is None:
            return h, p
        out[0] = h
        out[1] = p
        return out

    def held(self, name, player=None):
        b = _BIT.get(name)
        return b is not None and bool(self._masks(player)[0] & (1 << b))

    def pressed(self, name, player=None):
        b = _BIT.get(name)
        return b is not None and bool(self._masks(player)[1] & (1 << b))

    def released(self, name):
        b = _BIT.get(name)
        return b is not None and bool(_lib().moy_input_released(self._t) & (1 << b))

    def any_held(self):
        """Is any button held this frame, by any source?"""
        return bool(self._masks(None)[0])

    def any_pressed(self):
        """Did any button go down this frame, by any source?"""
        return bool(self._masks(None)[1])

    def held_names(self):
        h = self._masks(None)[0]
        return {n for i, n in enumerate(NAMES) if h & (1 << i)}

    def kept_names(self):
        """The press edges kept for a paced cart's next tick."""
        k = _lib().moy_input_kept(self._t)
        return {n for i, n in enumerate(NAMES) if k & (1 << i)}

    def source_players(self):
        """The distinct player slots the sources sit on (at least one)."""
        out = (_U8 * SOURCES)()
        n = _lib().moy_input_players(self._t, out)
        return tuple(out[i] for i in range(n))

    def apply_pointer(self, pointer):
        """The frame's pointer sample into `pointer`: P_HELD while down, with
        P_CLICK on its press edge; 0 when no source has a sample."""
        return _lib().moy_input_ptr_apply(self._t, ctypes.byref(pointer), _now())

    def multi(self):
        """Do the sources sit on more than one player slot?"""
        return _lib().moy_input_multi(self._t)

    def player_count(self):
        return len(self.source_players())


class HostInputTable(InputTable):
    BUTTONS = HOST_NAMES


# The pointer a CART sees, resolved once for every tier: cart_api's touch() and
# the Lua snapshot read this one answer. ONE integer of P_* flags, because the
# snapshot has one slot to say all three things in; `click` and `down` are
# independent (a scripted tap raises the edge with the finger already lifted).
def pointer_state(inp, out):
    """Fill `out` as `[x, y, state, ms]` and return it; `ms` is always 0.

    A linked match has no pointer (only buttons cross the radio). Liveness is
    the pointer's own; the game-space publication (`inp.game_pointer`, #39)
    wins the coordinates where the console makes one."""
    out[2] = P_NONE
    if getattr(inp, "netplay_live", False):
        return out
    p = getattr(inp, "pointer", None)
    if p is None:
        return out
    if hasattr(p, "live") and not p.live():
        return out
    gp = getattr(inp, "game_pointer", None)
    if gp is not None:
        out[0], out[1] = gp[0], gp[1]
        out[2] = (P_LIVE | (P_CLICK if bool(gp[2]) else 0)
                  | (P_HELD if (len(gp) > 3 and gp[3]) else 0))
        out[3] = 0
        return out
    out[0], out[1] = p.x, p.y
    out[2] = (P_LIVE | (P_HELD if getattr(p, "down", False) else 0)
              | (P_CLICK if getattr(p, "click", False) else 0))
    out[3] = 0
    return out


# -- the board's drivers ---------------------------------------------------------
#
# A board's `keyboard()`, `touch(w, h)` and `trackball()` are its drivers in the
# kernel (native/moy_input/moy_input_task.c); the host has none, so each is
# None, and `kick()` -- one pass of them, once a frame -- does nothing. `kernel()`
# is the drivers' table, the one a board's console reads.

def keyboard():
    return None


def touch(w, h):
    return None


def trackball():
    return None


def kick():
    return None


def ble():
    return None


def task_stack_free():
    return 0


_KERNEL = [None]


def kernel():
    if _KERNEL[0] is None:
        _KERNEL[0] = InputTable()
    return _KERNEL[0]


# The drivers' protocols over a bus a test supplies (native/moy_input/
# moy_drivers.h): the same C a board runs, driven through machine.I2C's shape --
# readfrom(addr, n), writeto(addr, buf), readfrom_mem(addr, reg, n, addrsize)
# -- so a fake that answers a keyboard or a touch controller drives the real
# protocol. A bus may also give ms() and us() clocks and sleep_ms(); without
# them the host's ticks are used and sleeps are skipped.

_WRITE = ctypes.CFUNCTYPE(_I, _P, _U8, ctypes.POINTER(_U8), ctypes.c_size_t)
_READ = ctypes.CFUNCTYPE(_I, _P, _U8, ctypes.POINTER(_U8), ctypes.c_size_t)
_WREAD = ctypes.CFUNCTYPE(_I, _P, _U8, ctypes.POINTER(_U8), ctypes.c_size_t,
                          ctypes.POINTER(_U8), ctypes.c_size_t)
_CLOCK = ctypes.CFUNCTYPE(_U32, _P)
_SLEEP = ctypes.CFUNCTYPE(None, _P, _U32)


class _Ops(ctypes.Structure):
    _fields_ = [("write", _WRITE), ("read", _READ), ("write_read", _WREAD),
                ("ms", _CLOCK), ("us", _CLOCK), ("sleep_ms", _SLEEP), ("ctx", _P)]


class Bus:
    """A machine.I2C-shaped object, as the drivers' C bus. A transfer that
    raises is a failed transfer, as on the board."""

    def __init__(self, i2c):
        self.i2c = i2c

        def write(_ctx, addr, src, n):
            try:
                i2c.writeto(addr, bytes(src[:n]))
                return 0
            except Exception:       # noqa: BLE001 -- a failed transfer
                return -1

        def read(_ctx, addr, dst, n):
            try:
                data = i2c.readfrom(addr, n)
            except Exception:       # noqa: BLE001
                return -1
            for i, b in enumerate(bytes(data)[:n]):
                dst[i] = b
            return 0

        def write_read(_ctx, addr, src, ns, dst, nd):
            reg = 0
            for i in range(ns):
                reg = (reg << 8) | src[i]
            try:
                data = i2c.readfrom_mem(addr, reg, nd, addrsize=8 * ns)
            except Exception:       # noqa: BLE001
                return -1
            for i, b in enumerate(bytes(data)[:nd]):
                dst[i] = b
            return 0

        def ms(_ctx):
            f = getattr(i2c, "ms", None)
            return (f() if f is not None else _ticks_ms()) & _TICKS_MASK

        def us(_ctx):
            f = getattr(i2c, "us", None)
            return (f() if f is not None else int(_ticks_ms() * 1000)) & 0xFFFFFFFF

        def sleep(_ctx, ms_):
            f = getattr(i2c, "sleep_ms", None)
            if f is not None:
                f(ms_)

        self._fns = (_WRITE(write), _READ(read), _WREAD(write_read), _CLOCK(ms),
                     _CLOCK(us), _SLEEP(sleep))
        self.ops = _Ops(*self._fns, None)


class _Map(ctypes.Structure):
    _fields_ = [("w", _I32), ("h", _I32), ("raw_w", _I32), ("raw_h", _I32),
                ("raw_x0", _I32), ("raw_y0", _I32), ("swap", _B), ("flip_x", _B),
                ("flip_y", _B)]


class _Held(ctypes.Structure):
    _fields_ = [("down", _B), ("fresh", _B), ("has_last", _B), ("extrapolate", _B),
                ("gliding", _B), ("lx", _I32), ("ly", _I32), ("gx", _I32), ("gy", _I32),
                ("w", _I32), ("h", _I32), ("hold_ms", _I32), ("ms", _U32),
                ("vx", ctypes.c_float), ("vy", ctypes.c_float), ("damp", ctypes.c_float)]


class _Pt(ctypes.Structure):
    _fields_ = [("down", _B), ("edge", _B), ("x", _I32), ("y", _I32)]


class _Kbd(ctypes.Structure):
    _fields_ = [("table", _P), ("src", _U32), ("available", _B), ("raw_mode", _B),
                ("raw_unsupported", _B), ("raw_game", _B), ("want", ctypes.c_int8),
                ("err_run", _U8), ("held", _U32), ("held_until", _U32),
                ("raw_held", _U32), ("raw_key", _I32), ("raw_bytes", _U8 * 5),
                ("raw_valid", _B), ("stat_n", _U32), ("stat_max_us", _U32),
                ("stat_over5", _U32), ("stat_over20", _U32), ("stat_timeouts", _U32),
                ("stat_max_raw", _B)]


class _Touch(ctypes.Structure):
    _fields_ = [("kind", _U8), ("addr", _U8), ("available", _B), ("yx", _B),
                ("clear_first", _B), ("gate", _B), ("int_count", _U32), ("int_last", _U32),
                ("int_seen", _B), ("touching", _B), ("last_read_ms", _U32), ("fw", _P),
                ("fw_len", ctypes.c_size_t), ("loaded", _U32), ("init_state", ctypes.c_int8),
                ("reprobe", ctypes.c_uint16), ("lock", _I), ("st_point", _B), ("st_up", _B),
                ("st_x", _I32), ("st_y", _I32), ("st_fingers", _U8), ("map", _Map),
                ("held", _Held), ("raw_x", _I32), ("raw_y", _I32), ("has_raw", _B),
                ("fingers", _U8), ("stat_n", _U32), ("stat_max_us", _U32),
                ("stat_over5", _U32), ("stat_over20", _U32), ("stat_skipped", _U32),
                ("fb_set", _B), ("fb_phase", _U8), ("fb_status", ctypes.c_int16),
                ("fb_ms", _U32), ("fb_n", _U32)]


_PKBD = ctypes.POINTER(_Kbd)
_PTOUCH = ctypes.POINTER(_Touch)
_POPS = ctypes.POINTER(_Ops)
_RESET = ctypes.CFUNCTYPE(None, _P, _B)

_DRV_SIGS = (
    ("moy_kbd_buttons_for_key", [_I32], _U32),
    ("moy_kbd_decode_raw", [ctypes.POINTER(_U8), _PU32, ctypes.POINTER(_I32)], None),
    ("moy_kbd_init", [_PKBD, _POPS, _P, _U32], None),
    ("moy_kbd_game_mode", [_PKBD, _B], None),
    ("moy_kbd_pass", [_PKBD, _POPS], None),
    ("moy_kbd_sizeof", [], ctypes.c_size_t),
    ("moy_kbd_raw_key", [ctypes.c_size_t, ctypes.POINTER(_U8), ctypes.POINTER(_U8),
                         ctypes.POINTER(_U8)], _I),
    ("moy_touchdev_init", [_PTOUCH, _U8, _U8, ctypes.POINTER(_Map), _B, ctypes.c_float, _I32],
     None),
    ("moy_touchdev_probe", [_PTOUCH, _POPS], None),
    ("moy_gsl_init", [_PTOUCH, _POPS, _RESET, _P], _B),
    ("moy_touchdev_pass", [_PTOUCH, _POPS], None),
    ("moy_touchdev_poll", [_PTOUCH, _U32, ctypes.POINTER(_Pt)], None),
    ("moy_touchdev_sizeof", [], ctypes.c_size_t),
    ("moy_touch_map", [ctypes.POINTER(_Map), _I32, _I32, ctypes.POINTER(_I32),
                       ctypes.POINTER(_I32)], None),
    ("moy_touch_held_init", [ctypes.POINTER(_Held), _B, _I32, _I32, ctypes.c_float, _I32],
     None),
    ("moy_touch_sample", [ctypes.POINTER(_Held), _I32, _I32, _U32, ctypes.POINTER(_Pt)], None),
    ("moy_touch_release", [ctypes.POINTER(_Held), ctypes.POINTER(_Pt)], None),
    ("moy_touch_hold", [ctypes.POINTER(_Held), _U32, ctypes.POINTER(_Pt)], None),
)

GT911, GSL3680, AXS = 1, 2, 3
KBD_ADDR = 0x55
KBD_HOLD_MS = 260
KBD_ERR_RUN = 10
TOUCH_HOLD_MS = 400
TOUCH_HOLD_MS_STREAM = 90


def buttons_for_key(key):
    """The button names a typed byte fires (an upper-case letter its lower's)."""
    bits = _lib().moy_kbd_buttons_for_key(key)
    return tuple(n for i, n in enumerate(NAMES) if bits & (1 << i))


def decode_raw(data):
    """Five matrix bytes -> (button names, key)."""
    d = (_U8 * 5)(*bytes(data)[:5])
    held, key = _U32(), _I32()
    _lib().moy_kbd_decode_raw(d, ctypes.byref(held), ctypes.byref(key))
    return tuple(n for i, n in enumerate(NAMES) if held.value & (1 << i)), key.value


def raw_keys():
    """The matrix table, (byte, bit, key) in the order the decoder tests them."""
    out = []
    a, b, c = _U8(), _U8(), _U8()
    while _lib().moy_kbd_raw_key(len(out), ctypes.byref(a), ctypes.byref(b), ctypes.byref(c)):
        out.append((a.value, b.value, c.value))
    return tuple(out)


class _Fields:
    """Attribute access onto a driver's C struct, by its field names."""

    def __getattr__(self, name):
        s = self.__dict__.get("_s")
        if s is not None and name in s._names:
            return getattr(s.contents if hasattr(s, "contents") else s, name)
        raise AttributeError(name)

    def __setattr__(self, name, value):
        s = self.__dict__.get("_s")
        if s is not None and name in s._names:
            setattr(s, name, value)
        else:
            object.__setattr__(self, name, value)


class KbdDriver(_Fields):
    """The T-Deck keyboard's driver (moy_kbd.c) over `i2c`, writing `table`'s
    "kbd" source. pass_() is one input-task pass; set_game_mode queues the flip."""

    def __init__(self, table, i2c):
        assert _lib().moy_kbd_sizeof() == ctypes.sizeof(_Kbd)
        self.__dict__["_s"] = _Kbd()
        self._s._names = {f[0] for f in _Kbd._fields_}
        self.bus = Bus(i2c)
        self.state = table
        self.source = table.source("kbd")
        _lib().moy_kbd_init(ctypes.byref(self._s), ctypes.byref(self.bus.ops), table._t,
                            self.source.h)

    def pass_(self):
        _lib().moy_kbd_pass(ctypes.byref(self._s), ctypes.byref(self.bus.ops))

    def set_game_mode(self, on):
        _lib().moy_kbd_game_mode(ctypes.byref(self._s), bool(on))

    @property
    def RAW_GAME_MODE(self):
        return self._s.raw_game

    @RAW_GAME_MODE.setter
    def RAW_GAME_MODE(self, on):
        self._s.raw_game = bool(on)


class TouchDriver(_Fields):
    """A touch controller's driver (moy_touchdev.c) over `i2c`: probe() it,
    pass_() is one input-task pass, poll() the frame's sample, (x, y, edge) or
    None, with `fresh` beside it."""

    def __init__(self, i2c, kind, w, h, addr=0, swap_xy=False, flip_x=False, flip_y=False,
                 raw_w=0, raw_h=0, raw_x0=0, raw_y0=0, yx=False, clear_first=False,
                 extrapolate=False, damp=0.5, hold_ms=TOUCH_HOLD_MS, fw=None):
        assert _lib().moy_touchdev_sizeof() == ctypes.sizeof(_Touch)
        self.__dict__["_s"] = _Touch()
        self._s._names = {f[0] for f in _Touch._fields_}
        self.bus = Bus(i2c)
        m = _Map(w, h, raw_w, raw_h, raw_x0, raw_y0, swap_xy, flip_x, flip_y)
        _lib().moy_touchdev_init(ctypes.byref(self._s), kind, addr, ctypes.byref(m),
                                 extrapolate, damp, hold_ms)
        self._s.yx = yx
        self._s.clear_first = clear_first
        if fw is not None:
            self._fw = ctypes.create_string_buffer(bytes(fw), len(fw))
            self._s.fw = ctypes.cast(self._fw, _P)
            self._s.fw_len = len(fw)
        self._pt = _Pt()

    def probe(self):
        _lib().moy_touchdev_probe(ctypes.byref(self._s), ctypes.byref(self.bus.ops))

    def gsl_init(self, reset):
        """The GSL3680's bring-up; `reset(before)` pulses or releases the pins."""
        self._reset = _RESET(lambda _ctx, before: reset(bool(before)))
        return _lib().moy_gsl_init(ctypes.byref(self._s), ctypes.byref(self.bus.ops),
                                   self._reset, None)

    def pass_(self):
        _lib().moy_touchdev_pass(ctypes.byref(self._s), ctypes.byref(self.bus.ops))

    def poll(self, now=None):
        if now is None:
            now = self.bus._fns[3](None)
        _lib().moy_touchdev_poll(ctypes.byref(self._s), now & _TICKS_MASK,
                                 ctypes.byref(self._pt))
        p = self._pt
        return (p.x, p.y, p.edge) if p.down else None

    @property
    def fresh(self):
        return self._s.held.fresh

    @property
    def raw(self):
        return (self._s.raw_x, self._s.raw_y) if self._s.has_raw else None


class HeldPoint:
    """The no-news contract (moy_touch.h) on its own: sample, release, hold."""

    def __init__(self, extrapolate=False, w=0, h=0, damp=1.0, hold_ms=TOUCH_HOLD_MS):
        self._h = _Held()
        self._pt = _Pt()
        _lib().moy_touch_held_init(ctypes.byref(self._h), extrapolate, w, h, damp, hold_ms)

    def _out(self):
        p = self._pt
        return (p.x, p.y, p.edge) if p.down else None

    def sample(self, x, y, now=None):
        _lib().moy_touch_sample(ctypes.byref(self._h), x, y,
                                (_now() if now is None else now) & _TICKS_MASK,
                                ctypes.byref(self._pt))
        return self._out()

    def release(self):
        _lib().moy_touch_release(ctypes.byref(self._h), ctypes.byref(self._pt))
        return None

    def hold(self, now=None):
        _lib().moy_touch_hold(ctypes.byref(self._h),
                              (_now() if now is None else now) & _TICKS_MASK,
                              ctypes.byref(self._pt))
        return self._out()

    @property
    def fresh(self):
        return self._h.fresh

    @property
    def down(self):
        return self._h.down


def map_point(x, y, w, h, swap, flip_x, flip_y, raw_w=0, raw_h=0, raw_x0=0, raw_y0=0):
    """A controller's raw point onto the glass: swap, scale, flip, clamp."""
    m = _Map(w, h, raw_w or 0, raw_h or 0, raw_x0, raw_y0, swap, flip_x, flip_y)
    ox, oy = _I32(), _I32()
    _lib().moy_touch_map(ctypes.byref(m), x, y, ctypes.byref(ox), ctypes.byref(oy))
    return ox.value, oy.value


# -- the BLE keyboard central's machine (native/moy_input/moy_hid.c) ----------------
#
# The machine without a radio: `stack` is the test's fake of NimBLE -- scan(picker),
# scan_stop(), connect(addr), disconnect(conn), pair(conn), disc_svcs(conn),
# disc_chrs(conn, start, end), disc_dscs(conn, start, end), write(conn, handle,
# data, response), forget_bonds(), save(machine) and ms() -- each request
# returning 0 when it went out; the test feeds the stack's events back through
# the on_* methods, as NimBLE's host task does on a board.

HID_STATES = ("off", "disabled", "idle", "scanning", "found", "connecting", "pairing",
              "discovering", "subscribe-retry", "ready", "choose")
HID_SERVICE, HID_BOOT_KBD, HID_REPORT, HID_PROTOCOL, HID_CCCD = (0x1812, 0x2A22, 0x2A4D,
                                                                 0x2A4E, 0x2902)
HID_BOOT_MOUSE = 0x2A33
HID_NOTIFY = 0x10


class _Addr(ctypes.Structure):
    _fields_ = [("type", _U8), ("a", _U8 * 6)]


def _addr(a):
    return _Addr(a[0], (_U8 * 6)(*bytes(a[1])))


_PADDR = ctypes.POINTER(_Addr)
_H_SCAN = ctypes.CFUNCTYPE(_I, _P, _B)
_H_CTX = ctypes.CFUNCTYPE(_I, _P)
_H_CONNECT = ctypes.CFUNCTYPE(_I, _P, _PADDR)
_H_CONN = ctypes.CFUNCTYPE(_I, _P, ctypes.c_uint16)
_H_RANGE = ctypes.CFUNCTYPE(_I, _P, ctypes.c_uint16, ctypes.c_uint16, ctypes.c_uint16)
_H_WRITE = ctypes.CFUNCTYPE(_I, _P, ctypes.c_uint16, ctypes.c_uint16, ctypes.POINTER(_U8),
                            ctypes.c_size_t, _B)
_H_VOID = ctypes.CFUNCTYPE(None, _P)
_H_SAVE = ctypes.CFUNCTYPE(None, _P, _P)


class _HidOps(ctypes.Structure):
    _fields_ = [("scan", _H_SCAN), ("scan_stop", _H_CTX), ("connect", _H_CONNECT),
                ("disconnect", _H_CONN), ("pair", _H_CONN), ("disc_svcs", _H_CONN),
                ("disc_chrs", _H_RANGE), ("disc_dscs", _H_RANGE), ("write", _H_WRITE),
                ("forget_bonds", _H_VOID), ("save", _H_SAVE), ("ms", _CLOCK), ("ctx", _P)]


_PHID = _P
_U16 = ctypes.c_uint16
_HID_SIGS = (
    ("moy_hid_sizeof", [], ctypes.c_size_t),
    ("moy_hid_init", [_PHID, ctypes.POINTER(_HidOps), _P, _U32], None),
    ("moy_hid_load", [_PHID, _B, _PADDR, ctypes.c_char_p], None),
    ("moy_hid_started", [_PHID, _B, ctypes.c_char_p], None),
    ("moy_hid_stopped", [_PHID], None),
    ("moy_hid_tick", [_PHID], None),
    ("moy_hid_set_enabled", [_PHID, _B], None),
    ("moy_hid_discover", [_PHID], None),
    ("moy_hid_pick", [_PHID, _PADDR], _B),
    ("moy_hid_forget", [_PHID], None),
    ("moy_hid_scan", [_PHID], _B),
    ("moy_hid_on_scan_result", [_PHID, _PADDR, ctypes.c_int8, ctypes.c_char_p, ctypes.c_size_t],
     None),
    ("moy_hid_on_scan_done", [_PHID], None),
    ("moy_hid_on_connect", [_PHID, _U16, _PADDR], None),
    ("moy_hid_on_connect_failed", [_PHID], None),
    ("moy_hid_on_disconnect", [_PHID, _U16], None),
    ("moy_hid_on_svc", [_PHID, _U16, _U16, _U16, _U16], None),
    ("moy_hid_on_svc_done", [_PHID, _U16, _I], None),
    ("moy_hid_on_chr", [_PHID, _U16, _U16, _U16, _U8, _U16], None),
    ("moy_hid_on_chr_done", [_PHID, _U16, _I], None),
    ("moy_hid_on_dsc", [_PHID, _U16, _U16, _U16], None),
    ("moy_hid_on_dsc_done", [_PHID, _U16, _I], None),
    ("moy_hid_on_write_done", [_PHID, _U16, _U16, _I], None),
    ("moy_hid_on_notify", [_PHID, _U16, _U16, ctypes.c_char_p, ctypes.c_size_t], None),
    ("moy_hid_on_conn_update", [_PHID, _U16, _U16, _I], None),
    ("moy_hid_on_enc_change", [_PHID, _U16, _B, _B], None),
    ("moy_hid_frame", [_PHID], None),
    ("moy_hid_set_player", [_PHID, ctypes.c_int8], None),
    ("moy_hid_state", [_PHID], _U8),
    ("moy_hid_text", [_PHID, _I], ctypes.c_char_p),
    ("moy_hid_int", [_PHID, _I], _I32),
    ("moy_hid_dev", [_PHID, _U8, _PADDR, ctypes.c_char_p, ctypes.POINTER(ctypes.c_int8)], _B),
    ("moy_hid_pref", [_PHID, _PADDR], _B),
    ("moy_hid_adv_has_hid", [ctypes.c_char_p, ctypes.c_size_t], _B),
    ("moy_hid_adv_name", [ctypes.c_char_p, ctypes.c_size_t, ctypes.c_char_p, ctypes.c_size_t],
     ctypes.c_size_t),
    ("moy_hid_decode", [ctypes.c_char_p, ctypes.c_size_t, ctypes.POINTER(_U8),
                        ctypes.POINTER(_U8), ctypes.POINTER(_U8)], _B),
    ("moy_hid_keycode", [_U8, _U8, _B], _I32),
    ("moy_hid_buttons_for_key", [_I32], _U32),
    ("moy_hid_take_mouse", [_PHID, ctypes.POINTER(_I32), ctypes.POINTER(_I32),
                            ctypes.POINTER(_U8)], _B),
)
_DRV_SIGS = _DRV_SIGS + _HID_SIGS


def adv_has_hid(adv):
    adv = bytes(adv)
    return _lib().moy_hid_adv_has_hid(adv, len(adv))


def adv_name(adv):
    adv = bytes(adv)
    out = ctypes.create_string_buffer(32)
    n = _lib().moy_hid_adv_name(adv, len(adv), out, 32)
    return out.value.decode() if n else None


def decode_report(report):
    """A boot-shaped report -> (modifiers, usages), or None for another layout."""
    r = bytes(report)
    mods, keys, n = _U8(), (_U8 * 6)(), _U8()
    if not _lib().moy_hid_decode(r, len(r), ctypes.byref(mods), keys, ctypes.byref(n)):
        return None
    return mods.value, tuple(keys[i] for i in range(n.value))


def usage_to_keycode(usage, modifiers=0, caps=False):
    return _lib().moy_hid_keycode(usage, modifiers, caps)


def hid_buttons_for_key(key):
    bits = _lib().moy_hid_buttons_for_key(key)
    return tuple(n for i, n in enumerate(NAMES) if bits & (1 << i))


class HidMachine:
    """The central's machine over `stack`, writing `table`'s "ble" source."""

    def __init__(self, table, stack, enabled=True, preferred=None, name=""):
        lib = _lib()
        self._buf = ctypes.create_string_buffer(lib.moy_hid_sizeof())
        self.h = ctypes.cast(self._buf, _P)
        self.stack = stack
        self.table = table
        self.src = table.source("ble")

        def call(name, *a):
            r = getattr(stack, name)(*a)
            return 0 if r is None else int(r)

        self._fns = (
            _H_SCAN(lambda _c, picker: call("scan", bool(picker))),
            _H_CTX(lambda _c: call("scan_stop")),
            _H_CONNECT(lambda _c, a: call("connect", (a.contents.type, bytes(a.contents.a)))),
            _H_CONN(lambda _c, conn: call("disconnect", conn)),
            _H_CONN(lambda _c, conn: call("pair", conn)),
            _H_CONN(lambda _c, conn: call("disc_svcs", conn)),
            _H_RANGE(lambda _c, conn, s, e: call("disc_chrs", conn, s, e)),
            _H_RANGE(lambda _c, conn, s, e: call("disc_dscs", conn, s, e)),
            _H_WRITE(lambda _c, conn, h, src, n, rsp: call("write", conn, h, bytes(src[:n]),
                                                           bool(rsp))),
            _H_VOID(lambda _c: call("forget_bonds")),
            _H_SAVE(lambda _c, _h: call("save", self)),
            _CLOCK(lambda _c: int(stack.ms()) & _TICKS_MASK),
        )
        self.ops = _HidOps(*self._fns, None)
        lib.moy_hid_init(self.h, ctypes.byref(self.ops), table._t, self.src.h)
        pref = _addr(preferred) if preferred is not None else None
        lib.moy_hid_load(self.h, enabled, ctypes.byref(pref) if pref else None,
                         name.encode())

    def __getattr__(self, name):
        if name.startswith("on_") or name in ("tick", "discover", "forget", "frame",
                                               "stopped"):
            fn = getattr(_lib(), "moy_hid_" + name)
            return lambda *a: fn(self.h, *a)
        raise AttributeError(name)

    def started(self, ok=True, why=None):
        _lib().moy_hid_started(self.h, ok, (why or "").encode())

    def scan(self):
        return _lib().moy_hid_scan(self.h)

    def set_enabled(self, on):
        _lib().moy_hid_set_enabled(self.h, bool(on))

    def pick(self, addr):
        a = _addr(addr)
        return _lib().moy_hid_pick(self.h, ctypes.byref(a))

    def set_player(self, slot):
        _lib().moy_hid_set_player(self.h, slot)

    def scan_result(self, addr, rssi, adv):
        a = _addr(addr)
        adv = bytes(adv)
        _lib().moy_hid_on_scan_result(self.h, ctypes.byref(a), rssi, adv, len(adv))

    def connected(self, conn, addr):
        a = _addr(addr)
        _lib().moy_hid_on_connect(self.h, conn, ctypes.byref(a))

    def notify(self, conn, handle, report):
        r = bytes(report)
        _lib().moy_hid_on_notify(self.h, conn, handle, r, len(r))

    @property
    def state(self):
        return HID_STATES[_lib().moy_hid_state(self.h)]

    def _int(self, which):
        return _lib().moy_hid_int(self.h, which)

    enabled = property(lambda self: bool(self._int(0)))
    protocol = property(lambda self: (None, "boot", "report")[self._int(1)])
    conn = property(lambda self: self._int(2))
    notify_count = property(lambda self: self._int(3))
    available = property(lambda self: bool(self._int(5)))
    caps = property(lambda self: bool(self._int(6)))

    @property
    def name(self):
        return _lib().moy_hid_text(self.h, 0).decode() or None

    @property
    def error(self):
        return _lib().moy_hid_text(self.h, 1).decode() or None

    @property
    def preferred(self):
        a = _Addr()
        return (a.type, bytes(a.a)) if _lib().moy_hid_pref(self.h, ctypes.byref(a)) else None

    def take_mouse(self):
        """(dx, dy, buttons) since the last take, or None with no mouse."""
        dx, dy, b = _I32(), _I32(), _U8()
        if not _lib().moy_hid_take_mouse(self.h, ctypes.byref(dx), ctypes.byref(dy),
                                         ctypes.byref(b)):
            return None
        return dx.value, dy.value, b.value

    def devices(self):
        out = []
        a, name, rssi = _Addr(), ctypes.create_string_buffer(32), ctypes.c_int8()
        for i in range(self._int(4)):
            _lib().moy_hid_dev(self.h, i, ctypes.byref(a), name, ctypes.byref(rssi))
            out.append(((a.type, bytes(a.a)), name.value.decode(), rssi.value))
        return out
