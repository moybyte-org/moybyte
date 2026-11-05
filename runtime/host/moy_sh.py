"""The C shell (native/moy_sh/) on CPython, by ctypes: the module `moy_sh` as
the boards import it, name for name (native/moy_sh/modmoy_sh.c has the list),
so the console's door handler (runtime/shell/sh_door.py), its role commands
and the Terminal app (runtime/apps/terminal_app.py) run a session in the
simulator and in host tests.

The shell is its own static archive (`archive()`), as a board builds it its
own static library: dash's sources take `-include dash_config.h` and the
shell's POSIX shim, which must reach no other file, and keep their folder
(`bltin/`). The host's one library (runtime/host/host_native.py) links the
archive whole, so the session's board table crosses the same ROLE door, log,
lease table and store the rest of the console's C uses: one library, one
state.
"""

from __future__ import annotations

import ctypes
import glob
import hashlib
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor

try:                                    # the host's runtime package
    from . import host_native, native_build
except ImportError:                     # loaded flat, runtime/ on sys.path
    import host_native
    import native_build

SH = os.path.join(native_build.ROOT, "native", "moy_sh")
_DASH = os.path.join(SH, "dash")
_KERNEL = os.path.join(native_build.ROOT, "native", "moy_kernel")
_STORE = os.path.join(native_build.ROOT, "native", "moy_store")
_CACHE = os.path.join(native_build.ROOT, ".build", "host")

DOOR = 0x7368
HOST = True                             # a host face names its files tree (Screen's root)
_FLAGS = ["-std=gnu99", "-O2", "-fPIC", "-w"]
_COLS_BYTES = 9                         # a cell's most bytes in a line: its SGR and UTF-8


def _sources():
    """[(source, own flags)]: dash's under its shim, the board's side plain."""
    shim = ["-include", os.path.join(SH, "dash_config.h"), "-I", SH, "-I", _DASH, "-I", _STORE]
    plain = ["-I", SH, "-I", _KERNEL, "-I", _STORE]
    dash = sorted(glob.glob(os.path.join(_DASH, "*.c")))
    dash += [os.path.join(_DASH, "bltin", n) for n in ("printf.c", "test.c")]
    dash += [os.path.join(SH, n) for n in ("moy_sh.c", "moy_sh_sys.c", "moy_sh_cmds.c")]
    own = [os.path.join(SH, n) for n in ("moy_sh_arena.c", "moy_sh_fmt.c", "moy_sh_board.c",
                                         "moy_sh_screen.c", "moy_term.c")]
    return [(s, shim) for s in dash] + [(s, plain) for s in own]


def archive(cflags=(), verbose=False):
    """The shell's archive for the one library (built once per content), or
    None with no C compiler."""
    cc = native_build.cc()
    if cc is None:
        return None
    flags = _FLAGS + list(cflags)
    h = hashlib.sha256(" ".join(flags).encode())
    for d, pat in ((SH, "**/*.[ch]"), (_KERNEL, "*.h"), (_STORE, "*.h")):
        for path in sorted(glob.glob(os.path.join(d, pat), recursive=True)):
            h.update(path[len(native_build.ROOT):].encode())
            with open(path, "rb") as f:
                h.update(f.read())
    out = os.path.join(_CACHE, "moysh-%s.a" % h.hexdigest()[:16])
    if os.path.exists(out):
        return out
    os.makedirs(_CACHE, exist_ok=True)
    with native_build.build_lock(out + ".lock"):
        if os.path.exists(out):
            return out
        obj_dir = "%s.%d.obj" % (out, os.getpid())
        os.makedirs(obj_dir, exist_ok=True)

        def one(job):
            src, own = job
            obj = os.path.join(obj_dir, os.path.basename(src)[:-2] + ".o")
            r = subprocess.run([cc] + flags + own + ["-c", src, "-o", obj],
                               capture_output=True, text=True)
            return obj, r

        with ThreadPoolExecutor(max_workers=os.cpu_count() or 4) as pool:
            done = list(pool.map(one, _sources()))
        bad = [r.stderr for _o, r in done if r.returncode]
        if bad:
            raise RuntimeError("moy_sh build failed:\n%s" % "".join(bad))
        tmp = "%s.%d.tmp" % (out, os.getpid())
        r = subprocess.run(["ar", "rcs", tmp] + [o for o, _r in done],
                           capture_output=True, text=True)
        if r.returncode:
            raise RuntimeError("moy_sh archive failed:\n%s" % r.stderr)
        os.replace(tmp, out)
        for o, _r in done:
            os.remove(o)
        os.rmdir(obj_dir)
        if verbose:
            print("moy_sh: built %s" % os.path.relpath(out, native_build.ROOT))
    return out


_P = ctypes.c_void_p
_I = ctypes.c_int
_SZ = ctypes.c_size_t
_S = ctypes.c_char_p
_ENT_FN = ctypes.CFUNCTYPE(_I, _P, _S, _I, ctypes.c_uint32)

_SIGS = (
    ("moy_sh_running", [], _P),
    ("moy_sh_fd_write", [_P, _I, _P, _SZ], ctypes.c_long),
    ("moy_sh_fd_read", [_P, _I, _P, _SZ], ctypes.c_long),
    ("moy_sh_arena_in_use", [_P], _SZ),
    ("moy_sh_board_line", [_S], ctypes.c_bool),
    ("moy_sh_interrupted", [_P], _I),
    ("moy_sh_screen_new", [_I, _I, _S], _P),
    ("moy_sh_screen_free", [_P], None),
    ("moy_sh_screen_run", [_P, _S, _SZ], _I),
    ("moy_sh_screen_interrupt", [_P], None),
    ("moy_sh_screen_session", [_P], _P),
    ("moy_sh_screen_term", [_P], _P),
    ("moy_sh_prompt", [_P, _I, _S, _SZ], _SZ),
    ("moy_sh_cancel", [_P], None),
    ("moy_sh_status", [_P], _I),
    ("moy_sh_cwd", [_P], _S),
    ("moy_sh_complete", [_P, _S, _ENT_FN, _P], _I),
    ("moy_term_write", [_P, _S, _SZ], None),
    ("moy_term_resize", [_P, _I, _I], _I),
    ("moy_term_size", [_P, ctypes.POINTER(_I), ctypes.POINTER(_I)], None),
    ("moy_term_gen", [_P], ctypes.c_uint32),
    ("moy_term_take", [_P, ctypes.POINTER(ctypes.c_uint32)], None),
    ("moy_term_line", [_P, _I, _S, _SZ], _SZ),
    ("moy_term_cursor", [_P, ctypes.POINTER(_I)], None),
)

_LIB = [None]


def _lib():
    if _LIB[0] is None:
        path = host_native.build()
        if path is None:
            raise ImportError("moy_sh: no C compiler for the host build")
        _LIB[0] = native_build.load_cdll(path, _SIGS)
    return _LIB[0]


def _running():
    s = _lib().moy_sh_running()
    if not s:
        raise OSError(9, "no session runs")     # EBADF, as the module raises
    return s


def write(fd, data):
    """A spawned program's or a role command's output, through the session
    that called the door."""
    b = bytes(data)
    r = _lib().moy_sh_fd_write(_running(), int(fd), b, len(b))
    if r < 0:
        raise OSError(5, "i/o error")
    return r


def read(fd, n):
    s = _running()
    buf = ctypes.create_string_buffer(max(1, int(n)))
    r = _lib().moy_sh_fd_read(s, int(fd), buf, int(n))
    if r < 0:
        raise OSError(5, "i/o error")
    return buf.raw[:r]


def arena():
    s = _lib().moy_sh_running()
    return _lib().moy_sh_arena_in_use(s) if s else None


def line(text):
    return bool(_lib().moy_sh_board_line(str(text).encode()))


def interrupted():
    """Ctrl-C on the running session's face: True, and the rest of its line
    dropped."""
    s = _lib().moy_sh_running()
    return bool(s) and bool(_lib().moy_sh_interrupted(s))


class Screen:
    """The screen face (native/moy_sh/moy_sh_screen.h): modmoy_sh.c's
    Screen. `root` names the files tree on the host (the board's is "")."""

    def __init__(self, cols, rows, root=None):
        L = _lib()
        self._f = L.moy_sh_screen_new(int(cols), int(rows),
                                      None if root is None else str(root).encode())
        if not self._f:
            raise OSError(12, "no memory")
        self._s = L.moy_sh_screen_session(self._f)
        self._t = L.moy_sh_screen_term(self._f)
        self._v = (ctypes.c_uint32 * 3)()
        self._c = (_I * 3)()
        self._buf = None

    def _face(self):
        if not self._f:
            raise OSError(9, "closed")
        return self._f

    def close(self):
        if self._f:
            _lib().moy_sh_screen_free(self._f)
            self._f = None

    __del__ = close

    def run(self, text):
        b = text.encode() if isinstance(text, str) else bytes(text)
        return _lib().moy_sh_screen_run(self._face(), b, len(b))

    def prompt(self, more):
        self._face()
        buf = ctypes.create_string_buffer(256)
        n = _lib().moy_sh_prompt(self._s, 1 if more else 0, buf, 256)
        return buf.value[:n].decode("utf-8", "replace")

    def write(self, text):
        self._face()
        b = text.encode() if isinstance(text, str) else bytes(text)
        _lib().moy_term_write(self._t, b, len(b))

    def resize(self, cols, rows):
        self._face()
        return _lib().moy_term_resize(self._t, int(cols), int(rows)) == 0

    def gen(self):
        self._face()
        return _lib().moy_term_gen(self._t) & 0x3FFFFFFF

    def take(self, into):
        self._face()
        _lib().moy_term_take(self._t, self._v)
        for i in range(3):
            into[i] = self._v[i] & 0x3FFFFFFF

    def cursor(self, into):
        self._face()
        _lib().moy_term_cursor(self._t, self._c)
        for i in range(3):
            into[i] = self._c[i]

    def line(self, i):
        self._face()
        cols, rows = _I(), _I()
        _lib().moy_term_size(self._t, ctypes.byref(cols), ctypes.byref(rows))
        cap = cols.value * _COLS_BYTES + 8
        if self._buf is None or len(self._buf) < cap:
            self._buf = ctypes.create_string_buffer(cap)
        n = _lib().moy_term_line(self._t, int(i), self._buf, cap)
        return self._buf.raw[:n].decode("utf-8", "replace")

    def complete(self, prefix):
        self._face()
        out = []

        def one(_arg, name, _is_dir, _size):
            out.append(name.decode("utf-8", "replace"))
            return 0

        _lib().moy_sh_complete(self._s, str(prefix).encode(), _ENT_FN(one), None)
        return out

    def cwd(self):
        self._face()
        return _lib().moy_sh_cwd(self._s).decode("utf-8", "replace")

    def status(self):
        self._face()
        return _lib().moy_sh_status(self._s)

    def arena(self):
        self._face()
        return _lib().moy_sh_arena_in_use(self._s)

    def interrupt(self):
        _lib().moy_sh_screen_interrupt(self._face())

    def cancel(self):
        self._face()
        _lib().moy_sh_cancel(self._s)
