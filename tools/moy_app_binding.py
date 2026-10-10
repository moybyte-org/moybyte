"""The app ABI's kernel half on the CPython host: the `moy_app` module
native/moy_app builds, as Python loads it by ctypes from the spine's host
library (tools/moy_index_spike.py's SPINE, which carries native/moy_app). The
runtime package registers it as `moy_app` (runtime/__init__.py), so the host's
roles are the C rows every image runs.

`binding()` returns a module-like object with modmoy_app.c's names -- App,
kernel, Damage, Surface, Theme, Prefs, Clipboard, policy, manifest_error,
id_for, roles, rows, perms, kinds, tokens and the constants -- or None where
there is no C compiler. Each
call into C is moy_app.h's ABI and nothing more; what a binding adds is the
mapping of the ABI's codes to what the Python roles answered, as modmoy_app.c
does on a VM. tests/test_moy_app.py holds the two bindings to the oracle,
tests/app_twin.py.
"""

import collections
import ctypes
import json
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import moy_index_spike  # noqa: E402

c = ctypes
_P = c.c_void_p
_U32 = c.c_uint32
_SIZE = c.c_size_t
_PU32 = c.POINTER(c.c_uint32)
_PSIZE = c.POINTER(c.c_size_t)
_PCHAR = c.c_char_p

OK, STALE, FULL, NOMEM, DENIED, NOSTORE, IO, BAD, ABSENT, NEEDS_VM = range(10)
ROLE_N, ROW_N, KINDS_N, TOKENS_N = 12, 23, 4, 28
NAME_MAX = 31
TOKEN_ABSENT = -(1 << 31)
CLIP_MAX = 4096
SLOTS = 32
ID_MAX = 63
_ENOSPC = 28


# surface.pointer()'s answer: the row's five numbers, by name as by index.
Pointer = collections.namedtuple("Pointer", ("x", "y", "down", "click", "visible"))


class _Surf(c.Structure):
    _fields_ = [("canvas", c.c_uint32), ("w", c.c_int32), ("h", c.c_int32),
                ("ox", c.c_int32), ("oy", c.c_int32), ("bar_h", c.c_int32),
                ("font_scale", c.c_uint8), ("chrome_scale", c.c_uint8),
                ("windowed", c.c_uint8)]


class _Policy(c.Structure):
    _fields_ = [("roles", c.c_uint32), ("nkinds", c.c_uint8),
                ("kinds", c.c_int8 * KINDS_N)]


_SIGS = (
    ("moy_app_new", [_P, _P], _P),
    ("moy_app_free", [_P], None),
    ("moy_app_kernel", [_P], _P),
    ("moy_app_fresh", [_P], None),
    ("moy_app_grant", [_P, _PCHAR, _SIZE, c.c_uint8, _U32, c.c_int, _PCHAR,
                       _SIZE, _U32, _PU32], c.c_int),
    ("moy_app_end", [_P, _U32], c.c_int),
    ("moy_app_grants", [_P], _U32),
    ("moy_app_perm_of", [c.c_int], _PCHAR),
    ("moy_app_kind_name", [c.c_int], _PCHAR),
    ("moy_app_kind_of", [_PCHAR, _SIZE], c.c_int),
    ("moy_app_policy_init", [c.POINTER(_Policy)], None),
    ("moy_app_policy_add", [c.POINTER(_Policy), _PCHAR, _SIZE], None),
    ("moy_app_policy_roles", [c.POINTER(_Policy), c.POINTER(c.c_int)], _U32),
    ("moy_app_policy_error", [c.POINTER(_Policy), _PCHAR, _SIZE], _SIZE),
    ("moy_app_id_for", [_PCHAR, _SIZE, _PCHAR, _SIZE, _PCHAR, _SIZE], _SIZE),
    ("moy_app_surface_write", [_P, c.POINTER(_Surf)], _U32),
    ("moy_app_pointer_bind", [_P, _P], None),
    ("moy_app_surface_canvas", [_P, _U32, _PU32], c.c_int),
    ("moy_app_surface_size", [_P, _U32, c.POINTER(c.c_int32), c.POINTER(c.c_int32)],
     c.c_int),
    ("moy_app_surface_font_scale", [_P, _U32], c.c_int32),
    ("moy_app_surface_chrome_scale", [_P, _U32], c.c_int32),
    ("moy_app_surface_windowed", [_P, _U32], c.c_int32),
    ("moy_app_surface_bar_h", [_P, _U32], c.c_int32),
    ("moy_app_surface_pointer", [_P, _U32, c.POINTER(c.c_int32)], c.c_int),
    ("moy_app_token_name", [c.c_int], _PCHAR),
    ("moy_app_token_flag", [c.c_int], c.c_int),
    ("moy_app_token_of", [_PCHAR, _SIZE], c.c_int),
    ("moy_app_theme_write", [_P, _PCHAR, _SIZE, _PCHAR, _SIZE, c.POINTER(c.c_int32)],
     c.c_int),
    ("moy_app_theme_write_skin", [_P, _PCHAR, _SIZE], c.c_int),
    ("moy_app_theme_read", [_P, c.POINTER(c.c_int32)], _U32),
    ("moy_app_theme_colors", [_P, _U32, _PU32], c.c_int),
    ("moy_app_theme_token", [_P, _U32, c.c_int, c.POINTER(c.c_int32)], c.c_int),
    ("moy_app_theme_gen", [_P, _U32], c.c_int32),
    ("moy_app_theme_light", [_P, _U32], c.c_int32),
    ("moy_app_theme_name", [_P, _U32, _P, _SIZE, _PSIZE], c.c_int),
    ("moy_app_theme_variant", [_P, _U32, _P, _SIZE, _PSIZE], c.c_int),
    ("moy_app_theme_skin", [_P, _U32, _P, _SIZE, _PSIZE], c.c_int),
    ("moy_app_damage_all", [_P, _U32], c.c_int),
    ("moy_app_damage_again", [_P, _U32], c.c_int),
    ("moy_app_damage_take", [_P], _U32),
    ("moy_app_damage_drop", [_P], None),
    ("moy_app_prefs_get", [_P, _U32, _PCHAR, _SIZE, _P, _SIZE, _PSIZE], c.c_int),
    ("moy_app_prefs_set", [_P, _U32, _PCHAR, _SIZE, _PCHAR, _SIZE], c.c_int),
    ("moy_app_prefs_clear", [_P, _U32, _PCHAR, _SIZE], c.c_int),
    ("moy_app_clip_put_text", [_P, _U32, _PCHAR, _SIZE], c.c_int),
    ("moy_app_clip_text", [_P, _U32, _P, _SIZE, _PSIZE], c.c_int),
    ("moy_app_clip_kind", [_P, _U32], c.c_int32),
    ("moy_app_clip_seq", [_P, _U32], c.c_int32),
    ("moy_app_count", [_P, c.c_int], _U32),
    ("moy_app_row_name", [c.c_int], _PCHAR),
    ("moy_app_role_name", [c.c_int], _PCHAR),
)


def _raise(rc):
    if rc == NOMEM:
        raise MemoryError("moy_app")
    if rc == FULL:
        raise OSError(_ENOSPC, "the grant table is full")
    if rc == STALE:
        raise ValueError("a grant that ended")
    if rc == DENIED:
        raise ValueError("a role the grant does not hold")
    raise ValueError("an argument the role refuses")


def _check(rc):
    if rc != OK:
        _raise(rc)


def _scalar(v):
    if v < 0:
        _raise(-v)
    return v


def _canvas_handle(cv):
    """The CANVAS row `cv` draws through (device_canvas.py's `_crow`), or 0."""
    row = getattr(cv, "_crow", None)
    h = getattr(row, "h", 0) if row is not None else 0
    return h if isinstance(h, int) else 0


def _str(o):
    if not isinstance(o, str):
        raise TypeError("a str")
    return o.encode("utf-8", "surrogateescape")


def binding(sanitize=False):
    """The moy_app module over the spine's host library, or None with no C
    compiler. The library is the one tools/moy_spine_binding.py loads, so a
    Settings it made is a pointer this one writes."""
    path = moy_index_spike.host_library("c", sanitize, moy_index_spike.SPINE)
    if path is None:
        return None
    lib = c.CDLL(path)
    for name, args, res in _SIGS:
        fn = getattr(lib, name)
        fn.argtypes = args
        fn.restype = res
    mem = c.c_void_p(c.addressof(c.c_char.in_dll(lib, "moy_spine_host_mem")))

    role_names = tuple(lib.moy_app_role_name(i).decode() for i in range(ROLE_N))
    row_names = tuple(lib.moy_app_row_name(i).decode() for i in range(ROW_N))
    kind_names = tuple(lib.moy_app_kind_name(i).decode() for i in range(KINDS_N))
    token_names = tuple(lib.moy_app_token_name(i).decode() for i in range(TOKENS_N))
    token_flags = tuple(bool(lib.moy_app_token_flag(i)) for i in range(TOKENS_N))

    def mask_of(roles):
        mask = 0
        for r in roles:
            if r not in role_names:
                raise ValueError("unknown app context role: " + str(r))
            mask |= 1 << role_names.index(r)
        return mask

    class App:
        """A console's own state; prefs are written into `settings` (a
        moy_spine.Settings over this same library, or None)."""

        def __init__(self, settings):
            self._rows = settings
            p = None if settings is None else settings._p
            self._p = lib.moy_app_new(mem, p)
            if not self._p:
                raise MemoryError("moy_app")
            self._own = True
            self._init()

        def _init(self):
            self._canvases = [None] * SLOTS
            self._pointer = None
            self._servers = {}
            self._served = {}
            self._colors = None
            self._colors_gen = 0

        def __del__(self):
            if getattr(self, "_own", False) and self._p:
                lib.moy_app_free(self._p)
                self._p = None

        def grant(self, app_id, roles, kind=None, ns=None, run=False, owner=0):
            idb = _str(app_id)
            nsb = idb if ns is None else _str(ns)
            k = -1
            if kind is not None:
                kb = _str(kind)
                k = lib.moy_app_kind_of(kb, len(kb))
                if k < 0:
                    raise ValueError("unknown files kind")
            h = c.c_uint32()
            _check(lib.moy_app_grant(self._p, idb, len(idb), 1 if run else 0,
                                     mask_of(roles), k, nsb, len(nsb), owner,
                                     c.byref(h)))
            return h.value

        def end(self, h):
            return lib.moy_app_end(self._p, h) == OK

        def count(self):
            return lib.moy_app_grants(self._p)

        def damage_take(self):
            return lib.moy_app_damage_take(self._p)

        def damage_drop(self):
            lib.moy_app_damage_drop(self._p)

        def counts(self):
            return tuple(lib.moy_app_count(self._p, i) for i in range(ROW_N))

        # -- the shell's writes

        def surface_write(self, canvas, w, h, fs, cs, windowed, bar_h, ox=0, oy=0):
            s = _Surf(_canvas_handle(canvas), int(w), int(h), int(ox), int(oy),
                      int(bar_h), int(fs), int(cs), 1 if windowed else 0)
            mask = lib.moy_app_surface_write(self._p, c.byref(s))
            for slot in range(SLOTS):
                if mask & (1 << slot):
                    self._canvases[slot] = canvas
            return None

        def bind_pointer(self, p):
            ok = isinstance(p, c.Structure) and type(p).__name__ == "Pointer"
            self._pointer = p if ok else None
            lib.moy_app_pointer_bind(self._p, c.addressof(p) if ok else None)
            return ok

        def theme_write(self, name, variant, colors):
            tok = (c.c_int32 * TOKENS_N)(*([TOKEN_ABSENT] * TOKENS_N))
            for k, v in colors.items():
                if k not in token_names:
                    raise ValueError("a token outside the vocabulary")
                tok[token_names.index(k)] = int(v)
            nb, vb = _str(name), _str(variant)
            _check(lib.moy_app_theme_write(self._p, nb, len(nb), vb, len(vb), tok))

        def theme_write_skin(self, skin):
            b = _str(skin)
            _check(lib.moy_app_theme_write_skin(self._p, b, len(b)))

        def serve(self, role, server):
            self._servers[role] = server

        def served(self):
            return dict(self._served)

        def _serve(self, role, verb):
            key = role + "." + verb
            self._served[key] = self._served.get(key, 0) + 1
            return getattr(self._servers[role], verb)

        def _colors_at(self, gen):
            if self._colors is not None and self._colors_gen == gen:
                return self._colors
            tok = (c.c_int32 * TOKENS_N)()
            gen = lib.moy_app_theme_read(self._p, tok)
            d = {}
            for i, v in enumerate(tok):
                if v != TOKEN_ABSENT:
                    d[token_names[i]] = bool(v) if token_flags[i] else v
            self._colors, self._colors_gen = d, gen
            return d

    def kernel(fresh):
        p = lib.moy_app_kernel(mem)
        if not p:
            raise MemoryError("moy_app")
        if fresh:
            lib.moy_app_fresh(p)
        lib.moy_app_pointer_bind(p, None)
        o = App.__new__(App)
        o._p, o._own, o._rows = p, False, None
        o._init()
        return o

    class _Role:
        def __init__(self, app, g):
            if not isinstance(app, App):
                raise TypeError("an App")
            self._app, self._g = app, int(g)

    class Damage(_Role):
        def all(self):
            _check(lib.moy_app_damage_all(self._app._p, self._g))

        def again(self):
            _check(lib.moy_app_damage_again(self._app._p, self._g))

    class Surface(_Role):
        def canvas(self):
            h = c.c_uint32()
            _check(lib.moy_app_surface_canvas(self._app._p, self._g, c.byref(h)))
            return self._app._canvases[self._g & 0xFF]

        def size(self):
            w, h = c.c_int32(), c.c_int32()
            _check(lib.moy_app_surface_size(self._app._p, self._g, c.byref(w),
                                            c.byref(h)))
            return (w.value, h.value)

        def font_scale(self):
            return _scalar(lib.moy_app_surface_font_scale(self._app._p, self._g))

        def chrome_scale(self):
            return _scalar(lib.moy_app_surface_chrome_scale(self._app._p, self._g))

        def windowed(self):
            return bool(_scalar(lib.moy_app_surface_windowed(self._app._p, self._g)))

        def bar_h(self):
            return _scalar(lib.moy_app_surface_bar_h(self._app._p, self._g))

        def pointer(self):
            v = (c.c_int32 * 5)()
            rc = lib.moy_app_surface_pointer(self._app._p, self._g, v)
            if rc == ABSENT:
                return None
            _check(rc)
            return Pointer(v[0], v[1], bool(v[2]), bool(v[3]), bool(v[4]))

        def glyph(self, *a, **kw):
            return self._app._serve("surface", "glyph")(*a, **kw)

    class Theme(_Role):
        def colors(self):
            gen = c.c_uint32()
            _check(lib.moy_app_theme_colors(self._app._p, self._g, c.byref(gen)))
            return self._app._colors_at(gen.value)

        def token(self, role):
            v = c.c_int32()
            rc = lib.moy_app_theme_token(self._app._p, self._g, int(role), c.byref(v))
            if rc == ABSENT:
                return None
            _check(rc)
            return v.value

        def gen(self):
            return _scalar(lib.moy_app_theme_gen(self._app._p, self._g))

        def light(self):
            return bool(_scalar(lib.moy_app_theme_light(self._app._p, self._g)))

        def _name(self, fn):
            buf = c.create_string_buffer(NAME_MAX + 1)
            n = c.c_size_t()
            _check(fn(self._app._p, self._g, buf, NAME_MAX + 1, c.byref(n)))
            return buf.raw[:n.value].decode("utf-8", "surrogateescape")

        def name(self):
            return self._name(lib.moy_app_theme_name)

        def variant(self):
            return self._name(lib.moy_app_theme_variant)

        def skin(self):
            return self._name(lib.moy_app_theme_skin)

        def set(self, *a, **kw):
            return self._app._serve("theme", "set")(*a, **kw)

        def set_variant(self, *a, **kw):
            return self._app._serve("theme", "set_variant")(*a, **kw)

        def set_skin(self, *a, **kw):
            return self._app._serve("theme", "set_skin")(*a, **kw)

    class Prefs(_Role):
        def get(self, key, default=None):
            kb = _str(key)
            n = c.c_size_t()
            rc = lib.moy_app_prefs_get(self._app._p, self._g, kb, len(kb), None, 0,
                                       c.byref(n))
            if rc == OK:
                buf = c.create_string_buffer(n.value or 1)
                rc = lib.moy_app_prefs_get(self._app._p, self._g, kb, len(kb), buf,
                                           n.value, c.byref(n))
                if rc == OK:
                    return json.loads(buf.raw[:n.value].decode())
            if rc == ABSENT:
                return default
            _raise(rc)

        def _flushed(self, rc):
            rows = self._app._rows
            exc = None if rows is None else getattr(rows, "_exc", None)
            if exc is not None:
                rows._exc = None
                raise exc
            if rc not in (OK, ABSENT):
                _raise(rc)

        def set(self, key, value):
            kb, jb = _str(key), json.dumps(value).encode()
            self._flushed(lib.moy_app_prefs_set(self._app._p, self._g, kb, len(kb),
                                                jb, len(jb)))

        def clear(self, key):
            kb = _str(key)
            self._flushed(lib.moy_app_prefs_clear(self._app._p, self._g, kb, len(kb)))

    class Clipboard(_Role):
        def put_text(self, text):
            b = str(text).encode("utf-8", "surrogateescape")
            rc = lib.moy_app_clip_put_text(self._app._p, self._g, b, len(b))
            if rc != BAD:
                _check(rc)
            return rc == OK

        def text(self):
            n = c.c_size_t()
            _check(lib.moy_app_clip_text(self._app._p, self._g, None, 0, c.byref(n)))
            buf = c.create_string_buffer(n.value or 1)
            _check(lib.moy_app_clip_text(self._app._p, self._g, buf, n.value,
                                         c.byref(n)))
            return buf.raw[:n.value].decode("utf-8", "surrogateescape")

        def kind(self):
            k = lib.moy_app_clip_kind(self._app._p, self._g)
            if k < 0:
                _raise(-k)
            return "text" if k == 1 else None

        def seq(self):
            s = lib.moy_app_clip_seq(self._app._p, self._g)
            if s < 0:
                _raise(-s)
            return s

    def _policy(perms):
        p = _Policy()
        lib.moy_app_policy_init(c.byref(p))
        for perm in perms or ():
            b = _str(str(perm))
            lib.moy_app_policy_add(c.byref(p), b, len(b))
        return p

    def policy(perms):
        p = _policy(perms)
        k = c.c_int()
        mask = lib.moy_app_policy_roles(c.byref(p), c.byref(k))
        roles = tuple(r for i, r in enumerate(role_names) if mask & (1 << i))
        return roles, (kind_names[k.value] if k.value >= 0 else None)

    def manifest_error(perms):
        p = _policy(perms)
        buf = c.create_string_buffer(96)
        n = lib.moy_app_policy_error(c.byref(p), buf, 96)
        return buf.raw[:n].decode() if n else None

    def id_for(cart_id, title):
        i = cart_id.encode() if isinstance(cart_id, str) else None
        t = title.encode() if isinstance(title, str) else None
        buf = c.create_string_buffer(ID_MAX + 1)
        n = lib.moy_app_id_for(i, len(i or b""), t, len(t or b""), buf, ID_MAX + 1)
        return buf.raw[:n].decode()

    def perms():
        out = []
        for i, r in enumerate(role_names):
            p = lib.moy_app_perm_of(i).decode()
            if p:
                out.append((p, r))
        return tuple(out)

    m = types.ModuleType("moy_app")
    m.__dict__.update(
        App=App, kernel=kernel, Damage=Damage, Surface=Surface, Theme=Theme,
        Prefs=Prefs, Clipboard=Clipboard,
        policy=policy, manifest_error=manifest_error, id_for=id_for,
        roles=lambda: role_names, rows=lambda: row_names, perms=perms,
        kinds=lambda: kind_names, tokens=lambda: token_names,
        CLIP_MAX=CLIP_MAX, SLOTS=SLOTS)
    return m


def host_bindings():
    """{name: module} for the parity suite: the C over ctypes; sanitized
    under MOY_SPINE_SANITIZE=1."""
    mod = binding(sanitize=os.environ.get("MOY_SPINE_SANITIZE") == "1")
    return {} if mod is None else {"c": mod}
