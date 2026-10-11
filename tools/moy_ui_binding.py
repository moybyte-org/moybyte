# Map (grep -n a name to jump there):
#   binding          the module: moy_ui's names over the glass's host library
"""The toolkit's C (native/moy_ui) on the CPython host, by ctypes: the module
`moy_ui` as the boards and the browser import it, name for name. Its C is
compiled into the glass's host library (runtime/glass_binding.py), because
its primitives draw into the canvas rows that library holds; runtime/__init__
registers this as `moy_ui`, so the host's toolkit is the C every image runs.
"""

import ctypes
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
if os.path.dirname(HERE) not in sys.path:
    sys.path.insert(0, os.path.dirname(HERE))

from runtime import glass_binding  # noqa: E402

c = ctypes
_I = c.c_int
_P = c.c_char_p
_Z = c.c_size_t
_I16x3 = c.c_int16 * 3
OK, BAD, STALE, NOMEM = range(4)

_SIGS = (
    ("moy_ui_kind", [_P, _Z], _I),
    ("moy_ui_kind_known", [_P, _Z], _I),
    ("moy_ui_button_kind", [_P, _Z], _I),
    ("moy_ui_state", [_P, _Z], _I),
    ("moy_ui_skin_use", [_P, _Z], _I),
    ("moy_ui_skin", [], _I),
    ("moy_ui_gen", [], c.c_uint32),
    ("moy_ui_look_set", [_I, c.c_void_p], _I),
    ("moy_ui_colors", [_I, _I, _I, c.POINTER(c.c_int16)], _I),
    ("moy_ui_metrics", [_I, c.POINTER(_I)], c.POINTER(c.c_int16)),
)


def _names(lib, sym, n):
    arr = (c.c_char_p * n).in_dll(lib, sym)
    return tuple(arr[i].decode() for i in range(n))


def _check(rc):
    if rc == NOMEM:
        raise MemoryError("moy_ui: no memory for the looks")
    if rc != OK:
        raise ValueError("moy_ui: bad argument")


def binding():
    """The module, or None where there is no C compiler."""
    try:
        lib = glass_binding._lib()
    except ImportError:
        return None
    for name, args, res in _SIGS:
        fn = getattr(lib, name)
        fn.argtypes = args
        fn.restype = res
    counts = {}
    for key in ("TOKENS", "STATES", "KINDS", "COLOR_KINDS", "SKINS"):
        counts[key] = c.c_int.in_dll(lib, "moy_ui_n_" + key.lower()).value
    tokens = _names(lib, "moy_ui_token_names", counts["TOKENS"])
    states = _names(lib, "moy_ui_state_names", counts["STATES"])
    kinds = _names(lib, "moy_ui_kind_names", counts["KINDS"])
    skins = _names(lib, "moy_ui_skin_names", counts["SKINS"])
    m = types.ModuleType("moy_ui")

    def _b(s):
        return s.encode("utf-8") if isinstance(s, str) else bytes(s)

    def kind(name):
        b = _b(name)
        return lib.moy_ui_kind(b, len(b))

    def button_kind(name):
        b = _b(name)
        return lib.moy_ui_button_kind(b, len(b))

    def skin(*name):
        if name:
            b = _b(name[0])
            return skins[lib.moy_ui_skin_use(b, len(b))]
        return skins[lib.moy_ui_skin()]

    def look(slot, row):
        if len(row) != len(tokens):
            raise ValueError("moy_ui: a token row is %d int32" % len(tokens))
        arr = (c.c_int32 * len(tokens))(*row)
        _check(lib.moy_ui_look_set(int(slot), arr))

    def colors(look_, kind_, state):
        out = _I16x3()
        _check(lib.moy_ui_colors(int(look_), int(kind_), int(state), out))
        return tuple(None if v < 0 else v for v in out)

    def metrics(name):
        b = _b(name)
        k = lib.moy_ui_kind_known(b, len(b))
        n = _I()
        p = lib.moy_ui_metrics(k, c.byref(n))
        if not p:
            return None
        return tuple(p[i] for i in range(n.value))

    m.tokens = lambda: tokens
    m.states = lambda: states
    m.kinds = lambda: kinds[:counts["COLOR_KINDS"]]
    m.metric_kinds = lambda: tuple(k for k in kinds if metrics(k) is not None)
    m.kind = kind
    m.button_kind = button_kind
    m.skins = lambda: skins
    m.skin = skin
    m.gen = lambda: lib.moy_ui_gen()
    m.look = look
    m.colors = colors
    m.metrics = metrics
    m.lib = lib
    return m
