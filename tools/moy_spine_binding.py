"""The kernel's spine on the CPython host: the `moy_spine` module native/moy_spine
builds, as Python loads its host library by ctypes. The runtime package
registers it as `moy_spine` (runtime/__init__.py), so the host's console runs
the C spine as every image does; the suites hold it to the Python oracle,
tests/spine_twin.py (tests/test_moy_spine.py's BINDINGS, the random walk of
tests/test_moy_spine_twins.py).

`binding()` returns a module-like object with the Python twin's names -- Table,
AppRegistry, BackStack, Returns, Leases, Settings, StaleHandle and the
constants -- or None where there is no C compiler. Each call into C is the ABI
of moy_htab.h, moy_route.h and moy_settings.h and nothing more; what a binding
adds is what C cannot hold (a Table's row objects) and the mapping of the ABI's
codes to the exceptions the twin raises, which is modmoy_spine.c's job on a VM.
"""

import ctypes
import json
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import moy_index_spike  # noqa: E402

c = ctypes
OK, STALE, FULL, NOMEM, DUP, BAD = range(6)
NOSLOT = 0xFFFFFFFF
_ENOSPC = 28


class _Kind(c.Structure):
    _fields_ = [("len", c.c_uint8), ("s", c.c_char * 15)]


class _App(c.Structure):
    _fields_ = [("id", _Kind), ("text_mode", c.c_uint8), ("has_min", c.c_uint8),
                ("min_w", c.c_int32), ("min_h", c.c_int32),
                ("title_len", c.c_uint32), ("title", c.c_void_p)]


_P = c.c_void_p
_U32 = c.c_uint32
_SIZE = c.c_size_t
_PU32 = c.POINTER(c.c_uint32)
_PINT = c.POINTER(c.c_int)
_PCHAR = c.c_char_p
_PP = c.POINTER(c.c_void_p)

_SIGS = (
    ("moy_host_table_new", [c.c_uint8, _U32], _P),
    ("moy_host_table_add", [_P, _PU32], c.c_int),
    ("moy_host_table_slot_of", [_P, _U32], _U32),
    ("moy_host_table_count", [_P], _U32),
    ("moy_host_table_slots", [_P], _U32),
    ("moy_host_table_at", [_P, _U32], _U32),
    ("moy_host_table_handle", [_P, _U32], _U32),
    ("moy_htab_release", [_P, _U32], c.c_int),
    ("moy_htab_free", [_P], None),
    ("moy_apps_new", [_P], _P),
    ("moy_apps_free", [_P], None),
    ("moy_apps_register", [_P, _PCHAR, _SIZE, _PCHAR, _SIZE, c.c_int, c.c_int,
                           c.c_int32, c.c_int32, _PU32], c.c_int),
    ("moy_apps_find", [_P, _PCHAR, _SIZE], _U32),
    ("moy_apps_get", [_P, _U32, c.POINTER(c.POINTER(_App))], c.c_int),
    ("moy_apps_valid", [_P, _U32], c.c_int),
    ("moy_apps_count", [_P], _U32),
    ("moy_apps_slots", [_P], _U32),
    ("moy_apps_at", [_P, _U32], _U32),
    ("moy_back_new", [_P], _P),
    ("moy_back_free", [_P], None),
    ("moy_back_goto", [_P, _PCHAR, _SIZE, _PINT], c.c_int),
    ("moy_back_remove", [_P, _PCHAR, _SIZE, _PINT], c.c_int),
    ("moy_back_top", [_P], c.POINTER(_Kind)),
    ("moy_back_depth", [_P], _U32),
    ("moy_back_at", [_P, _U32], c.POINTER(_Kind)),
    ("moy_back_index", [_P, _PCHAR, _SIZE], c.c_int),
    ("moy_returns_new", [_P, _P], _P),
    ("moy_returns_free", [_P], None),
    ("moy_returns_run", [_P, _PCHAR, _SIZE], c.c_int),
    ("moy_returns_caller", [_P], c.POINTER(_Kind)),
    ("moy_returns_spend", [_P, c.POINTER(_Kind)], c.c_int),
    ("moy_returns_route", [_P, c.c_int], c.c_int),
    ("moy_returns_note", [_P, _PCHAR, _SIZE, _PINT], c.c_int),
    ("moy_returns_back", [_P], c.POINTER(_Kind)),
    ("moy_returns_take_back", [_P, c.POINTER(_Kind)], c.c_int),
    ("moy_leases_new", [_P], _P),
    ("moy_leases_free", [_P], None),
    ("moy_lease_tag", [_U32], _PCHAR),
    ("moy_lease_bit", [_PCHAR, _SIZE], _U32),
    ("moy_leases_hold", [_P, _PCHAR, _SIZE, _PU32], c.c_int),
    ("moy_leases_release", [_P, _PCHAR, _SIZE, _PU32], c.c_int),
    ("moy_leases_mask", [_P], _U32),
    ("moy_apps_clear", [_P], None),
    ("moy_back_reset", [_P], None),
    ("moy_returns_reset", [_P], None),
    ("moy_leases_reset", [_P], None),
    ("moy_spine_kernel", [_P], c.POINTER(_P * 5)),
    ("moy_settings_new", [_P], _P),
    ("moy_settings_free", [_P], None),
    ("moy_settings_validate", [_PCHAR, _SIZE], c.c_int),
    ("moy_settings_load", [_P, _PCHAR, _SIZE, _PU32], c.c_int),
    ("moy_settings_get", [_P, _PCHAR, _SIZE, _PP, c.POINTER(_SIZE)], c.c_int),
    ("moy_settings_set", [_P, _PCHAR, _SIZE, _PCHAR, _SIZE], c.c_int),
    ("moy_settings_delete", [_P, _PCHAR, _SIZE], c.c_int),
    ("moy_settings_dirty", [_P], _U32),
    ("moy_settings_clean", [_P], None),
    ("moy_settings_count", [_P], _U32),
    ("moy_settings_at", [_P, _U32, _PP, c.POINTER(_SIZE), _PP,
                         c.POINTER(_SIZE)], c.c_int),
    ("moy_settings_dump", [_P, c.c_char_p, _SIZE], _SIZE),
    ("moy_settings_saver", [_P, _P, _P], None),
    ("moy_settings_flush", [_P], c.c_int),
    ("moy_settings_saver_ctx", [_P], _P),
)

# moy_settings_save_fn: (ctx, text, len) -> nonzero when it landed.
SAVE_FN = c.CFUNCTYPE(c.c_int, _P, _P, _SIZE)

LEASE_TAGS = ("web", "update", "settings", "cart", "link", "carts", "dev")


def binding(sanitize=False):
    """The spine module over the host library, or None with no C compiler."""
    path = moy_index_spike.host_library("c", sanitize, moy_index_spike.SPINE)
    if path is None:
        return None
    lib = c.CDLL(path)
    for name, args, res in _SIGS:
        fn = getattr(lib, name)
        fn.argtypes = args
        fn.restype = res
    mem = c.c_void_p(c.addressof(c.c_char.in_dll(lib, "moy_spine_host_mem")))
    m = types.ModuleType("moy_spine_c")
    m.__dict__.update(
        SLOT_BITS=8, KIND_SHIFT=8, GEN_SHIFT=12, GEN_MAX=(1 << 18) - 1,
        SLOTS=256, ID_MAX=15, KIND_APP=1, KIND_BUF=2, KIND_CANVAS=3,
        KIND_SURF=4, KIND_OWNER=5, KIND_SRC=6, KIND_PEER=7, KIND_AUDIO=8, KIND_CLIP=9,
        KIND_IMAGE=10, KIND_ACTOR=11, KIND_GRANT=12,
        STAYED=0, PUSHED=1, RETURNED=2,
        ROOT="launcher", EDITOR="menu", ROUTE_HOME=0, ROUTE_EDITOR=1,
        ROUTE_APP=2, ROUTE_WINDOW=3, LEASE_TAGS=LEASE_TAGS, IMPL="c")

    class StaleHandle(ValueError):
        """A handle that names no live row of the table it was given to."""

    m.StaleHandle = StaleHandle

    def nomem(what):
        raise MemoryError(what)

    def alloc_error(rc, what):
        if rc == FULL:
            raise OSError(_ENOSPC, "%s full" % what)
        raise MemoryError(what)

    def handle(h):
        """The u32 an int names, 0 for one no row can have; TypeError else."""
        if not isinstance(h, int):
            raise TypeError("a handle is an int")
        return h if 0 < h < (1 << 30) else 0

    def raw(s, what):
        if not isinstance(s, str):
            raise TypeError(what)
        return s.encode()

    def kind(k):
        b = raw(k, "a kind is a str")
        if not 1 <= len(b) <= 15:
            raise ValueError("a kind is 1..15 bytes")
        return b

    def kind_str(k):
        return k.s[:k.len].decode()

    def free_with(name):
        def free(self):
            p, self._p = getattr(self, "_p", None), None
            if p:
                getattr(lib, name)(p)
        return free

    class Table:
        def __init__(self, kind, name, slots=256):
            if not isinstance(name, str):
                raise TypeError("a table name is a str")
            if not 1 <= kind <= 15 or not 1 <= slots <= 256:
                raise ValueError("table kind 1..15, slots 1..256")
            self._p = lib.moy_host_table_new(kind, slots)
            if not self._p:
                nomem("table")
            self.name = name
            self._rows = [None] * slots

        __del__ = free_with("moy_htab_free")

        def _slot(self, h):
            v = handle(h)
            s = lib.moy_host_table_slot_of(self._p, v)
            if s == NOSLOT:
                raise StaleHandle("stale %s handle %d" % (self.name, v))
            return s

        def new(self, row):
            h = c.c_uint32()
            rc = lib.moy_host_table_add(self._p, c.byref(h))
            if rc != OK:
                alloc_error(rc, "%s table" % self.name)
            self._rows[h.value & 0xFF] = row
            return h.value

        def get(self, h):
            return self._rows[self._slot(h)]

        def put(self, h, row):
            self._rows[self._slot(h)] = row

        def valid(self, h):
            try:
                v = handle(h)
            except TypeError:
                return False
            return lib.moy_host_table_slot_of(self._p, v) != NOSLOT

        def release(self, h):
            s = self._slot(h)
            row, self._rows[s] = self._rows[s], None
            lib.moy_htab_release(self._p, lib.moy_host_table_handle(self._p, s))
            return row

        def handles(self):
            return [h for h in (lib.moy_host_table_at(self._p, s) for s in
                                range(lib.moy_host_table_slots(self._p))) if h]

        def count(self):
            return lib.moy_host_table_count(self._p)

    class AppRegistry:
        SLOTS = 64

        def __init__(self):
            self._p = lib.moy_apps_new(mem)
            if not self._p:
                nomem("app registry")

        __del__ = free_with("moy_apps_free")

        def register(self, app_id, title, text_mode=False, min_size=None):
            idb = kind(app_id)
            if lib.moy_apps_find(self._p, idb, len(idb)):
                raise ValueError("duplicate app id: " + app_id)
            has_min, w, h = 0, 0, 0
            if min_size is not None:
                w, h = int(min_size[0]), int(min_size[1])
                if not all(-(1 << 31) <= v < (1 << 31) for v in (w, h)):
                    raise ValueError("min_size must fit 32 bits")
                has_min = 1
            tb = str(title).encode()
            out = c.c_uint32()
            rc = lib.moy_apps_register(self._p, idb, len(idb), tb, len(tb),
                                       int(bool(text_mode)), has_min, w, h,
                                       c.byref(out))
            if rc != OK:
                alloc_error(rc, "app table")
            return out.value

        def find(self, app_id):
            b = raw(app_id, "an app id is a str")
            return lib.moy_apps_find(self._p, b, len(b))

        def _row(self, h):
            v = handle(h)
            row = c.POINTER(_App)()
            if lib.moy_apps_get(self._p, v, c.byref(row)) != OK:
                raise StaleHandle("stale app handle %d" % v)
            return row.contents

        def app_id(self, h):
            return kind_str(self._row(h).id)

        def title(self, h):
            r = self._row(h)
            return c.string_at(r.title, r.title_len).decode()

        def text_mode(self, h):
            return bool(self._row(h).text_mode)

        def min_size(self, h):
            r = self._row(h)
            return (r.min_w, r.min_h) if r.has_min else None

        def valid(self, h):
            try:
                v = handle(h)
            except TypeError:
                return False
            return bool(lib.moy_apps_valid(self._p, v))

        def handles(self):
            return [h for h in (lib.moy_apps_at(self._p, s) for s in
                                range(lib.moy_apps_slots(self._p))) if h]

        def count(self):
            return lib.moy_apps_count(self._p)

    class BackStack:
        DEPTH = 32

        def __init__(self):
            self._p = lib.moy_back_new(mem)
            if not self._p:
                nomem("back-stack")

        __del__ = free_with("moy_back_free")

        def goto(self, k):
            b = kind(k)
            answer = c.c_int()
            rc = lib.moy_back_goto(self._p, b, len(b), c.byref(answer))
            if rc != OK:
                alloc_error(rc, "back-stack")
            return answer.value

        def remove(self, k):
            b = kind(k)
            removed = c.c_int()
            lib.moy_back_remove(self._p, b, len(b), c.byref(removed))
            return bool(removed.value)

        def top(self):
            return kind_str(lib.moy_back_top(self._p).contents)

        def _index(self, k):
            if not isinstance(k, str):
                return -1
            b = k.encode()
            return lib.moy_back_index(self._p, b, len(b))

        def has(self, k):
            return self._index(k) >= 0

        def index(self, k):
            return self._index(k)

        def depth(self):
            return lib.moy_back_depth(self._p)

        def kinds(self):
            return [kind_str(lib.moy_back_at(self._p, i).contents)
                    for i in range(self.depth())]

    class Returns:
        def __init__(self, apps):
            if not isinstance(apps, AppRegistry):
                raise TypeError("Returns takes an AppRegistry")
            self._apps = apps
            self._p = lib.moy_returns_new(mem, apps._p)
            if not self._p:
                nomem("returns")

        __del__ = free_with("moy_returns_free")

        def run(self, caller):
            if caller is None:
                lib.moy_returns_run(self._p, None, 0)
                return
            b = kind(caller)
            lib.moy_returns_run(self._p, b, len(b))

        def caller(self):
            k = lib.moy_returns_caller(self._p)
            return kind_str(k.contents) if k else None

        def spend(self):
            k = _Kind()
            return kind_str(k) if lib.moy_returns_spend(self._p, c.byref(k)) \
                else None

        def route(self, windowed):
            return lib.moy_returns_route(self._p, int(bool(windowed)))

        def note(self, k):
            b = kind(k)
            did = c.c_int()
            lib.moy_returns_note(self._p, b, len(b), c.byref(did))
            return bool(did.value)

        def back(self):
            k = lib.moy_returns_back(self._p)
            return kind_str(k.contents) if k else None

        def take_back(self):
            k = _Kind()
            return kind_str(k) if lib.moy_returns_take_back(
                self._p, c.byref(k)) else None

    class Leases:
        def __init__(self):
            self._p = lib.moy_leases_new(mem)
            if not self._p:
                nomem("leases")

        __del__ = free_with("moy_leases_free")

        def _tag(self, tag):
            b = raw(tag, "a lease tag is a str")
            if not lib.moy_lease_bit(b, len(b)):
                raise ValueError("unknown lease tag %r" % tag)
            return b

        def hold(self, tag):
            b, mask = self._tag(tag), c.c_uint32()
            lib.moy_leases_hold(self._p, b, len(b), c.byref(mask))
            return mask.value

        def release(self, tag):
            b, mask = self._tag(tag), c.c_uint32()
            lib.moy_leases_release(self._p, b, len(b), c.byref(mask))
            return mask.value

        def mask(self):
            return lib.moy_leases_mask(self._p)

        def held(self, tag):
            b = self._tag(tag)
            return bool(self.mask() & lib.moy_lease_bit(b, len(b)))

        def holders(self):
            mask = self.mask()
            return [LEASE_TAGS[i] for i in range(len(LEASE_TAGS))
                    if mask & (1 << i)]

    def settings_key(k):
        b = raw(k, "a settings key is a str")
        if not b:
            raise ValueError("an empty settings key")
        return b

    def settings_error(rc):
        if rc == NOMEM:
            raise MemoryError("settings")
        raise ValueError("not JSON the settings store holds")

    class Settings:
        def __init__(self, save=None):
            self._p = lib.moy_settings_new(mem)
            if not self._p:
                nomem("settings")
            self._save = save
            self._saver()

        def _saver(self):
            # The rows' saver (moy_settings_saver): the hook behind a thunk,
            # which holds what the hook raises for flush() to raise.
            def thunk(_ctx, text, n):
                if self._save is None:
                    return 0
                try:
                    return int(self._save(c.string_at(text, n).decode()) is not False)
                except BaseException as exc:    # noqa: BLE001 -- raised by flush()
                    self._exc = exc
                    return 0
            self._exc = None
            self._thunk = SAVE_FN(thunk)
            lib.moy_settings_saver(self._p, c.cast(self._thunk, _P), id(self))

        def __del__(self):
            if self._p:
                if self.__dict__.get("_own", True):
                    lib.moy_settings_free(self._p)
                elif lib.moy_settings_saver_ctx(self._p) == id(self):
                    # The kernel's rows outlive the view; its saver goes.
                    lib.moy_settings_saver(self._p, None, None)
                self._p = None

        def load(self, text):
            b = raw(text, "system.json is a str")
            rows = c.c_uint32()
            rc = lib.moy_settings_load(self._p, b, len(b), c.byref(rows))
            if rc != OK:
                settings_error(rc)
            return rows.value

        def adopt(self, d):
            for k in d:
                settings_key(k)
            self.load(json.dumps(d))

        def _text(self, key):
            kb = settings_key(key)
            j, n = c.c_void_p(), c.c_size_t()
            if not lib.moy_settings_get(self._p, kb, len(kb), c.byref(j),
                                        c.byref(n)):
                return None
            return c.string_at(j.value, n.value).decode()

        def get(self, key, default=None):
            t = self._text(key)
            return default if t is None else json.loads(t)

        def text(self, key):
            return self._text(key)

        def set(self, key, value, persist=True):
            self.set_text(key, json.dumps(value), persist)

        def set_text(self, key, text, persist=True):
            jb = raw(text, "a settings value is JSON text")
            if lib.moy_settings_validate(jb, len(jb)) != OK:
                settings_error(1)
            kb = settings_key(key)
            rc = lib.moy_settings_set(self._p, kb, len(kb), jb, len(jb))
            if rc != OK:
                settings_error(rc)
            if persist:
                self.flush()

        def delete(self, key, persist=True):
            kb = settings_key(key)
            if not lib.moy_settings_delete(self._p, kb, len(kb)):
                return False
            if persist:
                self.flush()
            return True

        def dirty(self):
            return lib.moy_settings_dirty(self._p) > 0

        def flush(self):
            clean = lib.moy_settings_flush(self._p)
            exc, self._exc = self._exc, None
            if exc is not None:
                raise exc
            return bool(clean)

        def keys(self):
            out = []
            for i in range(lib.moy_settings_count(self._p)):
                k, kn, j, jn = c.c_void_p(), c.c_size_t(), c.c_void_p(), \
                    c.c_size_t()
                lib.moy_settings_at(self._p, i, c.byref(k), c.byref(kn),
                                    c.byref(j), c.byref(jn))
                out.append(c.string_at(k.value, kn.value).decode())
            return out

        def dump(self):
            n = lib.moy_settings_dump(self._p, None, 0)
            buf = c.create_string_buffer(n + 1)
            lib.moy_settings_dump(self._p, buf, n)
            return buf.raw[:n].decode()

    def view(cls, p, **extra):
        # A view of a kernel table: its finaliser frees nothing.
        o = cls.__new__(cls)
        o._p = p
        o.__dict__.update(extra)
        return o

    class _View:
        def __del__(self):
            pass

    def kernel(fresh):
        k = lib.moy_spine_kernel(mem)
        if not k:
            nomem("kernel tables")
        apps_p, back_p, returns_p, leases_p, _rows = k.contents
        lib.moy_apps_clear(apps_p)
        if fresh:
            lib.moy_back_reset(back_p)
            lib.moy_returns_reset(returns_p)
            lib.moy_leases_reset(leases_p)
        apps = type("AppRegistry", (_View, AppRegistry), {})
        back = type("BackStack", (_View, BackStack), {})
        rets = type("Returns", (_View, Returns), {})
        leas = type("Leases", (_View, Leases), {})
        a = view(apps, apps_p)
        return (a, view(back, back_p), view(rets, returns_p, _apps=a), view(leas, leases_p))

    def kernel_settings(save, fresh):
        k = lib.moy_spine_kernel(mem)
        if not k:
            nomem("kernel tables")
        rows = k.contents[4]
        if fresh:
            lib.moy_settings_load(rows, b"{}", 2, c.byref(c.c_uint32()))
        o = Settings.__new__(Settings)
        o._p, o._own, o._save = rows, False, save
        o._saver()
        return o

    for cls in (Table, AppRegistry, BackStack, Returns, Leases, Settings):
        m.__dict__[cls.__name__] = cls
    m.kernel = kernel
    m.kernel_settings = kernel_settings
    m.lib = lib
    m.mem = mem
    return m


def host_bindings():
    """{name: module} for the suite's BINDINGS: the C twin; sanitized under
    MOY_SPINE_SANITIZE=1."""
    san = os.environ.get("MOY_SPINE_SANITIZE") == "1"
    mod = binding(sanitize=san)
    return {} if mod is None else {"c": mod}
