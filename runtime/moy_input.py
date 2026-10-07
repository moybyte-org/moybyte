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

SOURCES = 12
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
)

_LIB = [None]


def build(verbose=False):
    return native_build.build("moy_input", _SHIM, ("moy_input.h", "moy_htab.h"),
                              _CACHE, libmoy_dir=(_INPUT, _SPINE), verbose=verbose)


def _lib():
    if _LIB[0] is None:
        path = build()
        if path is None:
            raise ImportError("moy_input: no C compiler for the host build")
        d = ctypes.CDLL(path)
        for name, args, res in _SIGS:
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
