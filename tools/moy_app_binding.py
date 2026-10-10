# Map (grep -n a name to jump there):
#   _Buf             moy_fs.h's buffer: an answer the user-files layer allocated
#   _raise           the ABI's codes as the exceptions the roles raise
#   binding          the module: App, kernel, the role types, the policy
#   host_bindings    the parity suite's bindings
"""The app ABI's kernel half on the CPython host: the `moy_app` module
native/moy_app builds, as Python loads it by ctypes from the spine's host
library (tools/moy_index_spike.py's SPINE, which carries native/moy_app). The
runtime package registers it as `moy_app` (runtime/__init__.py), so the host's
roles are the C rows every image runs.

`binding()` returns a module-like object with modmoy_app.c's names -- App,
kernel, Damage, Surface, Theme, Prefs, Artwork, Clipboard, the roles served
in Python (Files, Carts, Nav, Notify, Wallpaper, Install), policy, manifest_error,
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
ROLE_N, ROW_N, KINDS_N, TOKENS_N = 12, 54, 4, 28
NAME_MAX = 31
UF_NAME_MAX = 255        # moy_ufiles.h's MOY_UF_NAME_MAX
NONE_UF, BAD_UF = -1, -2
DOC_MAX = 255
TOKEN_ABSENT = -(1 << 31)
CLIP_MAX = 4096
SLOTS = 32
ID_MAX = 63
_ENOSPC = 28


# The role table: the served roles' rows are its own.
with open(os.path.join(HERE, "..", "native", "moy_app", "roles.json")) as _f:
    _TABLE = json.load(_f)["rows"]


class _Buf(c.Structure):
    """moy_fs.h's moy_buf_t: an answer the user-files layer allocated."""
    _fields_ = [("p", c.c_void_p), ("n", c.c_size_t)]


# surface.pointer()'s answer: the row's five numbers, by name as by index.
Pointer = collections.namedtuple("Pointer", ("x", "y", "down", "click", "visible"))


class _Surf(c.Structure):
    _fields_ = [("canvas", c.c_uint32), ("w", c.c_int32), ("h", c.c_int32),
                ("ox", c.c_int32), ("oy", c.c_int32), ("bar_h", c.c_int32),
                ("font_scale", c.c_uint8), ("chrome_scale", c.c_uint8),
                ("windowed", c.c_uint8)]


class _Grant(c.Structure):
    _fields_ = [("id_len", c.c_uint8), ("ns_len", c.c_uint8), ("cls", c.c_uint8),
                ("kind", c.c_int8), ("roles", c.c_uint32), ("owner", c.c_uint32),
                ("id", c.c_char * (ID_MAX + 1))]


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
    ("moy_app_artwork_current", [_P, _U32, _P, _SIZE, _PSIZE, _P, _SIZE, _PSIZE],
     c.c_int),
    ("moy_app_artwork_follow", [_P, _U32, _PCHAR, _SIZE, _PCHAR, _SIZE, _PCHAR, _SIZE],
     c.c_int),
    ("moy_app_holds", [_P, _U32, c.c_int], c.c_int),
    ("moy_app_role", [_P, _U32, _U32, _P, _SIZE, _P, _SIZE], c.c_int32),
    ("moy_app_table_name", [_U32], _PCHAR),
    ("moy_app_door_bind", [_P], None),
    ("moy_app_store_bind", [_P, _P, _PCHAR, _SIZE, c.c_int, c.c_int], c.c_int),
    ("moy_app_why", [_P], c.c_int),
    ("moy_app_buf_free", [_P, c.POINTER(_Buf)], None),
    ("moy_app_files_end_all", [_P], _U32),
    ("moy_app_copy_gen", [_P], _U32),
    ("moy_app_files_readable", [_P, _U32], c.c_int32),
    ("moy_app_files_ready", [_P, _U32], c.c_int32),
    ("moy_app_files_begin", [_P, _U32], c.c_int),
    ("moy_app_files_end", [_P, _U32], c.c_int),
    ("moy_app_files_list", [_P, _U32, _PCHAR, c.POINTER(_Buf), _PU32], c.c_int),
    ("moy_app_files_count", [_P, _U32, _PCHAR, _PU32], c.c_int),
    ("moy_app_files_load", [_P, _U32, _PCHAR, _PCHAR, c.POINTER(_Buf),
                            c.POINTER(c.c_int)], c.c_int),
    ("moy_app_files_save", [_P, _U32, _PCHAR, _PCHAR, _PCHAR, _SIZE, _PCHAR], c.c_int),
    ("moy_app_files_delete", [_P, _U32, _PCHAR, _PCHAR, _PCHAR], c.c_int),
    ("moy_app_files_duplicate", [_P, _U32, _PCHAR, _PCHAR, _PCHAR], c.c_int),
    ("moy_app_files_restore", [_P, _U32, _PCHAR, _PCHAR, _PCHAR], c.c_int),
    ("moy_app_files_rename", [_P, _U32, _PCHAR, _PCHAR, _PCHAR, _PCHAR], c.c_int),
    ("moy_app_files_new_name", [_P, _U32, _PCHAR, _PCHAR, _PCHAR], c.c_int),
    ("moy_app_files_trash_list", [_P, _U32, c.POINTER(_Buf), _PU32], c.c_int),
    ("moy_app_files_empty_trash", [_P, _U32], c.c_int),
    ("moy_app_files_history", [_P, _U32, _PCHAR, _PCHAR, c.POINTER(_Buf)], c.c_int),
    ("moy_app_files_history_ops", [_P, _U32, _PCHAR, _PCHAR, c.POINTER(_Buf)], c.c_int),
    ("moy_app_files_history_commit", [_P, _U32, _PCHAR, _PCHAR, _PCHAR, _SIZE, _PCHAR,
                                      _SIZE, c.POINTER(c.c_int)], c.c_int),
    ("moy_app_files_encode_image", [_P, _U32, _U32, _U32, _PCHAR, _SIZE,
                                    c.POINTER(_Buf)], c.c_int),
    ("moy_app_files_decode_image", [_P, _U32, _PCHAR, _SIZE, c.POINTER(_Buf), _PU32,
                                    _PU32], c.c_int),
    ("moy_app_files_decode_cover", [_P, _U32, _PCHAR, _SIZE, c.POINTER(_Buf)], c.c_int),
    ("moy_app_files_encode_cover", [_P, _U32, _PCHAR, _SIZE, c.POINTER(_Buf)], c.c_int),
    ("moy_app_files_sig", [_P, _U32, _PCHAR, _SIZE, _PU32], c.c_int),
    ("moy_app_files_stamp", [_P, _U32, _PCHAR, _SIZE, _PCHAR, _PCHAR, _U32,
                             c.POINTER(_Buf)], c.c_int),
    ("moy_app_files_encode_text", [_P, _U32], c.c_int),
    ("moy_app_files_decode_text", [_P, _U32], c.c_int),
    ("moy_app_files_provenance", [_P, _U32, _PCHAR, _SIZE, c.POINTER(_Buf),
                                  c.POINTER(c.c_int64)], c.c_int),
    ("moy_app_wallpaper_load_copy", [_P, _U32, c.POINTER(_Buf)], c.c_int),
    ("moy_app_wallpaper_save_copy", [_P, _U32, _PCHAR, _SIZE], c.c_int),
    ("moy_app_grant_get", [_P, _U32, c.POINTER(c.c_void_p)], c.c_int),
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
            self._no_store = None
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

        def grant_id(self, h):
            row = c.c_void_p()
            _check(lib.moy_app_grant_get(self._p, int(h), c.byref(row)))
            g = _Grant.from_address(row.value)
            return g.id[:g.id_len].decode()

        def count(self):
            return lib.moy_app_grants(self._p)

        def role(self, g, row, args, cap):
            """The ROLE door (moy_app_role): (answer, the bytes it wrote)."""
            args = bytes(args)
            cap = int(cap)
            if cap < 0:
                raise ValueError("cap")
            ans = c.create_string_buffer(max(cap, 1))
            r = lib.moy_app_role(self._p, int(g) & 0xFFFFFFFF, int(row), args,
                                 len(args), ans, cap)
            return r, ans.raw[:max(0, min(r, cap))]

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

        def store_bind(self, root, readable, writable, no_store):
            """The user-files store the files and wallpaper rows reach (root
            None: none), and the `err` they answer with no store. The layer is
            native/moy_store's (tools/moy_ufiles_binding.py), built at the
            first bind of a root."""
            self._no_store = no_store
            if root is None:
                _check(lib.moy_app_store_bind(self._p, None, b"", 0, 0, 0))
                return
            import moy_ufiles_binding
            rb = _str(str(root))
            parent = str(root).rsplit("/", 1)[0]
            moy_ufiles_binding.library().moy_fs_root((parent or str(root)).encode())
            _check(lib.moy_app_store_bind(self._p, moy_ufiles_binding.ops(), rb, len(rb),
                                          1 if readable else 0, 1 if writable else 0))

        def files_end_all(self):
            return lib.moy_app_files_end_all(self._p)

        def copy_gen(self):
            return lib.moy_app_copy_gen(self._p)

        def _err(self, rc):
            """The `err` of a storage row's code; a grant problem raises."""
            if rc == NOSTORE:
                return self._no_store
            if rc == IO:
                e = lib.moy_app_why(self._p)
                return str(OSError(e, os.strerror(e)))
            if rc == BAD:
                return "a refused argument"
            if rc == NOMEM:
                return "memory allocation failed"
            _raise(rc)

        def _take(self, buf, text=True):
            try:
                raw = c.string_at(buf.p, buf.n) if buf.p else b""
            finally:
                lib.moy_app_buf_free(self._p, c.byref(buf))
            return raw.decode("utf-8", "surrogateescape") if text else raw

        def served(self):
            return dict(self._served)

        def _serve(self, g, role, verb):
            _check(lib.moy_app_holds(self._p, g, role_names.index(role)))
            server = self._servers.get(role)
            if server is None:
                raise ValueError("no server for the role")
            key = role + "." + verb
            self._served[key] = self._served.get(key, 0) + 1
            return getattr(server, verb)

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
            return self._app._serve(self._g, "surface", "glyph")(self._g, *a, **kw)

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
            return self._app._serve(self._g, "theme", "set")(self._g, *a, **kw)

        def set_variant(self, *a, **kw):
            return self._app._serve(self._g, "theme", "set_variant")(self._g, *a, **kw)

        def set_skin(self, *a, **kw):
            return self._app._serve(self._g, "theme", "set_skin")(self._g, *a, **kw)

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

    class Artwork(_Role):
        def current(self):
            kb = c.create_string_buffer(DOC_MAX)
            nb = c.create_string_buffer(DOC_MAX)
            kn, nn = c.c_size_t(), c.c_size_t()
            rc = lib.moy_app_artwork_current(self._app._p, self._g, kb, DOC_MAX,
                                             c.byref(kn), nb, DOC_MAX, c.byref(nn))
            if rc == ABSENT:
                return None
            _check(rc)
            if kn.value > DOC_MAX or nn.value > DOC_MAX:
                return None
            return (kb.raw[:kn.value].decode(), nb.raw[:nn.value].decode())

        def follow(self, kind, old, new):
            kb, ob, nb = _str(kind), _str(old), _str(new)
            rc = lib.moy_app_artwork_follow(self._app._p, self._g, kb, len(kb),
                                            ob, len(ob), nb, len(nb))
            Prefs._flushed(self, rc)
            return rc == OK

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

    def _names(raw, count, pairs):
        parts = raw.split("\0")
        if pairs:
            return [(parts[2 * i], parts[2 * i + 1]) for i in range(count)]
        return parts[:count]

    class Files(_Role):
        """The user-files layer's rows (native/moy_store/moy_ufiles.h), C over
        the store the console bound: `(value, err)` as the Python role
        answered, the codecs their value."""

        def _a(self):
            return self._app._p

        def readable(self):
            return bool(_scalar(lib.moy_app_files_readable(self._a(), self._g)))

        def ready(self):
            return bool(_scalar(lib.moy_app_files_ready(self._a(), self._g)))

        def begin(self):
            rc = lib.moy_app_files_begin(self._a(), self._g)
            return (True, None) if rc == OK else (None, self._app._err(rc))

        def end(self):
            _check(lib.moy_app_files_end(self._a(), self._g))

        def list(self, kind):
            buf, n = _Buf(), c.c_uint32()
            rc = lib.moy_app_files_list(self._a(), self._g, _str(kind), c.byref(buf),
                                        c.byref(n))
            if rc != OK:
                return (None, self._app._err(rc))
            return (_names(self._app._take(buf), n.value, False), None)

        def count(self, kind):
            n = c.c_uint32()
            rc = lib.moy_app_files_count(self._a(), self._g, _str(kind), c.byref(n))
            return (n.value, None) if rc == OK else (None, self._app._err(rc))

        def load(self, kind, name):
            buf, binary = _Buf(), c.c_int()
            rc = lib.moy_app_files_load(self._a(), self._g, _str(kind), _str(name),
                                        c.byref(buf), c.byref(binary))
            if rc == ABSENT:
                return (None, None)
            if rc != OK:
                return (None, self._app._err(rc))
            return (self._app._take(buf, not binary.value), None)

        def _named(self, fn, *args):
            out = c.create_string_buffer(UF_NAME_MAX + 1)
            rc = fn(self._a(), self._g, *args, out)
            if rc != OK:
                return (None, self._app._err(rc))
            return (out.value.decode("utf-8", "surrogateescape"), None)

        def save(self, kind, name, blob):
            b = blob if isinstance(blob, bytes) else _str(str(blob))
            return self._named(lib.moy_app_files_save, _str(kind), _str(name), b, len(b))

        def delete(self, kind, name):
            return self._named(lib.moy_app_files_delete, _str(kind), _str(name))

        def duplicate(self, kind, name):
            return self._named(lib.moy_app_files_duplicate, _str(kind), _str(name))

        def restore(self, kind, name):
            return self._named(lib.moy_app_files_restore, _str(kind), _str(name))

        def rename(self, kind, name, new):
            return self._named(lib.moy_app_files_rename, _str(kind), _str(name),
                               _str(str(new)))

        def new_name(self, kind, title=None):
            return self._named(lib.moy_app_files_new_name, _str(kind),
                               _str(str(title)) if title else None)

        def trash_list(self):
            buf, n = _Buf(), c.c_uint32()
            rc = lib.moy_app_files_trash_list(self._a(), self._g, c.byref(buf), c.byref(n))
            if rc != OK:
                return (None, self._app._err(rc))
            return (_names(self._app._take(buf), n.value, True), None)

        def empty_trash(self):
            rc = lib.moy_app_files_empty_trash(self._a(), self._g)
            return (None, None if rc == OK else self._app._err(rc))

        def _json(self, fn, kind, name):
            buf = _Buf()
            rc = fn(self._a(), self._g, _str(kind), _str(name), c.byref(buf))
            if rc != OK:
                return (None, self._app._err(rc))
            return (json.loads(self._app._take(buf)), None)

        def history(self, kind, name):
            return self._json(lib.moy_app_files_history, kind, name)

        def history_ops(self, kind, name):
            return self._json(lib.moy_app_files_history_ops, kind, name)

        def history_commit(self, kind, name, ops, keyframe=None):
            o = json.dumps(list(ops)).encode() if ops else None
            k = json.dumps(keyframe).encode() if keyframe is not None else None
            err = c.c_int()
            rc = lib.moy_app_files_history_commit(self._a(), self._g, _str(kind),
                                                  _str(name), o, len(o or b""), k,
                                                  len(k or b""), c.byref(err))
            if rc != OK:
                return (None, self._app._err(rc))
            if err.value == 0:
                return (None, None)
            if err.value == BAD_UF:
                return ("a refused argument", None)
            return (str(OSError(err.value, os.strerror(err.value))), None)

        # -- the codecs: their value, or None

        def _codec(self, rc, buf, text):
            if rc == ABSENT:
                return None
            _check(rc)
            return self._app._take(buf, text)

        def encode_image(self, w, h, indices):
            pix = indices if isinstance(indices, bytes) else bytes(bytearray(indices))
            if int(w) <= 0 or int(h) <= 0:
                raise ValueError("bad artwork size")
            buf = _Buf()
            rc = lib.moy_app_files_encode_image(self._a(), self._g, int(w), int(h), pix,
                                                len(pix), c.byref(buf))
            if rc == BAD:
                raise ValueError("bad artwork size")
            return self._codec(rc, buf, True)

        def decode_image(self, blob):
            if not blob:
                _check(lib.moy_app_holds(self._a(), self._g, role_names.index("files")))
                return None
            raw = blob if isinstance(blob, bytes) else _str(blob)
            buf, w, h = _Buf(), c.c_uint32(), c.c_uint32()
            rc = lib.moy_app_files_decode_image(self._a(), self._g, raw, len(raw),
                                                c.byref(buf), c.byref(w), c.byref(h))
            got = self._codec(rc, buf, False)
            return None if got is None else (w.value, h.value, got)

        def decode_cover(self, blob):
            if not blob:
                _check(lib.moy_app_holds(self._a(), self._g, role_names.index("files")))
                return None
            raw = bytes(blob)
            buf = _Buf()
            rc = lib.moy_app_files_decode_cover(self._a(), self._g, raw, len(raw),
                                                c.byref(buf))
            got = self._codec(rc, buf, False)
            return None if got is None else (128, 128, got)

        def encode_cover(self, indices):
            pix = bytes(bytearray(indices))
            buf = _Buf()
            rc = lib.moy_app_files_encode_cover(self._a(), self._g, pix, len(pix),
                                                c.byref(buf))
            return self._codec(rc, buf, False)

        def sig(self, blob):
            raw = _str(blob) if blob else b""
            v = c.c_uint32()
            rc = lib.moy_app_files_sig(self._a(), self._g, raw, len(raw), c.byref(v))
            if rc == ABSENT:
                return None
            _check(rc)
            return v.value

        def stamp(self, blob, kind, name, sig):
            if not isinstance(blob, str):
                _check(lib.moy_app_holds(self._a(), self._g, role_names.index("files")))
                return blob
            raw = _str(blob)
            buf = _Buf()
            rc = lib.moy_app_files_stamp(self._a(), self._g, raw, len(raw), _str(kind),
                                         _str(name), int(sig) & 0xFFFFFFFF, c.byref(buf))
            got = self._codec(rc, buf, True)
            return blob if got is None else got

        def encode_text(self, body):
            rc = lib.moy_app_files_encode_text(self._a(), self._g)
            if rc == ABSENT:
                return None
            _check(rc)
            return str(body)

        def decode_text(self, blob):
            rc = lib.moy_app_files_decode_text(self._a(), self._g)
            if rc != ABSENT:
                _check(rc)
            if rc != OK or not isinstance(blob, str) or not blob:
                return []
            return blob.split("\n")

        def provenance(self, blob):
            if not isinstance(blob, str) or not blob:
                _check(lib.moy_app_holds(self._a(), self._g, role_names.index("files")))
                return (None, None)
            raw = _str(blob)
            buf, sig = _Buf(), c.c_int64()
            rc = lib.moy_app_files_provenance(self._a(), self._g, raw, len(raw),
                                              c.byref(buf), c.byref(sig))
            src = self._codec(rc, buf, True)
            return (None, None) if src is None else (src, sig.value)

    def _served_role(role):
        """A role whose rows are all served in Python: one method per row of
        the table, each the grant checked, the row counted and the server
        called as modmoy_app.c's SERVED rows are."""
        def row(verb):
            def call(self, *a, **kw):
                return self._app._serve(self._g, role, verb)(self._g, *a, **kw)
            call.__name__ = verb
            return call
        verbs = [r["verb"] for r in _TABLE if r["role"] == role and r["server"] != "c"]
        return type(role.capitalize(), (_Role,), {v: row(v) for v in verbs})

    Carts, Nav, Notify, Install = (
        _served_role(r) for r in ("carts", "nav", "notify", "install"))

    class Wallpaper(_served_role("wallpaper")):
        """The wallpaper's rows served in Python, and its copy's, in C."""

        def load_copy(self):
            buf = _Buf()
            rc = lib.moy_app_wallpaper_load_copy(self._app._p, self._g, c.byref(buf))
            if rc == ABSENT:
                return (None, None)
            if rc != OK:
                return (None, self._app._err(rc))
            return (self._app._take(buf), None)

        def save_copy(self, blob):
            b = _str(str(blob))
            rc = lib.moy_app_wallpaper_save_copy(self._app._p, self._g, b, len(b))
            return (None, None if rc == OK else self._app._err(rc))

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

    table_names = tuple(lib.moy_app_table_name(i).decode() for i in range(len(_TABLE)))

    def door_bind(on):
        """The shell rows' door: the kernel's ROLE upcall, the loop's own
        library's moy_loop_role (runtime/moy_loop.py), or none."""
        if not on:
            lib.moy_app_door_bind(None)
            return
        try:
            import moy_loop
        except ImportError:
            from runtime import moy_loop
        fn = moy_loop._lib().moy_loop_role
        lib.moy_app_door_bind(c.cast(fn, c.c_void_p))

    m = types.ModuleType("moy_app")
    m.__dict__.update(
        App=App, kernel=kernel, Damage=Damage, Surface=Surface, Theme=Theme,
        Prefs=Prefs, Clipboard=Clipboard, Artwork=Artwork, Files=Files,
        Carts=Carts, Nav=Nav, Notify=Notify, Wallpaper=Wallpaper, Install=Install,
        policy=policy, manifest_error=manifest_error, id_for=id_for,
        roles=lambda: role_names, rows=lambda: row_names, perms=perms,
        table=lambda: table_names, door_bind=door_bind,
        kinds=lambda: kind_names, tokens=lambda: token_names,
        CLIP_MAX=CLIP_MAX, SLOTS=SLOTS)
    return m


def host_bindings():
    """{name: module} for the parity suite: the C over ctypes; sanitized
    under MOY_SPINE_SANITIZE=1."""
    mod = binding(sanitize=os.environ.get("MOY_SPINE_SANITIZE") == "1")
    return {} if mod is None else {"c": mod}
