"""The host's native (ELF) tier (native/moy_elf/README.md): a module the SDK
built for the host, run through the boards' C -- the verdict, the process, the
OS table's rows (native/moy_elf/moy_os.c) -- over the host's dynamic loader
(runtime/host/moyhost_elf.c), in the host's one library
(runtime/host/host_native.py).

`command` is a native command's run in the shell session on this thread,
as the boards' `moy_elf.command` is (runtime/run/elf_run.py runs either);
`service`, `poll`, `stop` and `info` are a native service's life.
"""

import ctypes
import os

from . import native_build

_HERE = os.path.dirname(os.path.abspath(__file__))
_NATIVE = os.path.join(native_build.ROOT, "native")
_ELF = os.path.join(_NATIVE, "moy_elf")

PART = native_build.Part("elf", (
    "moy_os.h", "moy_os.c", "moy_os_table.h", "moy_os_rows.h", "moy_os_table.c",
    "moy_elf.h", "moy_elf_check.c", "moyhost_elf.c", "moy_ota.h",
    "moy_sh.h", "moy_sh_board.h", "moy_sh_prog.h", "moy_log.h", "moy_proc.h", "moy_input.h",
    "moy_event.h"),
    (_ELF, _HERE, os.path.join(_NATIVE, "moy_sh"), os.path.join(_NATIVE, "moy_kernel"),
     os.path.join(_NATIVE, "moy_input"), os.path.join(_NATIVE, "moy_net")))

TRAPPED, REFUSED = -1, -3       # moy_os.h's MOY_OS_REFUSED; a host module that faults takes the process

_C, _I, _P, _U32 = ctypes.c_char_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32
_LIB = [None]


class Info(ctypes.Structure):
    """moy_os_info_t."""
    _fields_ = [("pid", _U32), ("threads", _U32), ("handles", _U32), ("calls", _U32),
                ("timers", _U32), ("events", _U32), ("service", ctypes.c_uint8),
                ("ending", ctypes.c_uint8)]


class Stats(ctypes.Structure):
    """moy_os_stats_t."""
    _fields_ = [("started", _U32), ("swept", _U32), ("killed", _U32), ("leaked", _U32)]


class Event(ctypes.Structure):
    """moy_event_t (native/moy_input/moy_event.h)."""
    _fields_ = [("t_us", _U32), ("src", ctypes.c_uint16), ("kind", ctypes.c_uint8),
                ("prov", ctypes.c_uint8), ("a", ctypes.c_uint16), ("id", ctypes.c_uint16),
                ("b", _U32)]


def _lib():
    if _LIB[0] is None:
        from . import host_native
        path = host_native.build()
        if path is None:
            _LIB[0] = False
            return None
        d = ctypes.CDLL(path)
        d.hl_elf_command.argtypes = [_C, _I, ctypes.POINTER(_C), _C, _U32, _C, _U32, _U32, _C, _I]
        d.hl_elf_command.restype = _I
        d.hl_elf_service.argtypes = [_C, _U32, _C, _C, _I]
        d.hl_elf_service.restype = _I
        d.hl_elf_check.argtypes = [_C, _C, _I]
        d.hl_elf_check.restype = _I
        d.moy_os_poll.argtypes = [ctypes.POINTER(_U32), ctypes.POINTER(ctypes.c_int32), _U32]
        d.moy_os_poll.restype = _U32
        d.moy_os_stop.argtypes = [_U32]
        d.moy_os_stop.restype = _I
        d.moy_os_info.argtypes = [_U32, ctypes.POINTER(Info)]
        d.moy_os_info.restype = _I
        d.moy_os_stats.argtypes = [ctypes.POINTER(Stats)]
        d.moy_os_stats.restype = None
        d.moy_os_post_event.argtypes = [ctypes.POINTER(Event)]
        d.moy_os_post_event.restype = None
        _LIB[0] = d
    return _LIB[0] or None


def available():
    return _lib() is not None


# The chip a module for this console is built for (tools/elf_cart.py).
CHIP = "host"


def command(path, argv, roots=None, pid=0, cid="", bg_pid=0, budget_ms=0):
    """A native command, as the boards' `moy_elf.command`: the module at
    `path` run as `argv` in the shell session running on this thread,
    reaching `roots` (folders relative to the store, each ending in a NUL;
    None for none). (exit status, None), or (REFUSED, why)."""
    d = _lib()
    if d is None:
        return REFUSED, "no host library"
    args = (_C * max(1, len(argv)))(*[a.encode() for a in argv])
    err = ctypes.create_string_buffer(256)
    code = d.hl_elf_command(path.encode(), len(argv), args, (roots or "").encode() + b"\0",
                            int(pid), (cid or "").encode(), int(bg_pid), int(budget_ms), err,
                            len(err))
    if code < 0:
        return REFUSED, err.value.decode("utf-8", "replace")
    return code, None


def check(path):
    """None when the module at `path` would load here, else why not."""
    d = _lib()
    err = ctypes.create_string_buffer(256)
    if d.hl_elf_check(path.encode(), err, len(err)) != 0:
        return err.value.decode("utf-8", "replace")
    return None


def service(path, pid, cid=""):
    """Start the native service at `path` as process `pid`: None, or why not."""
    d = _lib()
    err = ctypes.create_string_buffer(256)
    if d.hl_elf_service(path.encode(), int(pid), (cid or "").encode(), err, len(err)) != 0:
        return err.value.decode("utf-8", "replace")
    return None


def poll():
    """[(pid, exit status)] of the services that ended by themselves."""
    d = _lib()
    pids = (_U32 * 8)()
    codes = (ctypes.c_int32 * 8)()
    n = d.moy_os_poll(pids, codes, 8)
    return [(pids[i], codes[i]) for i in range(n)]


def stop(pid):
    return _lib().moy_os_stop(int(pid))


def info(pid):
    out = Info()
    if _lib().moy_os_info(int(pid), ctypes.byref(out)) != 0:
        return None
    return {f: getattr(out, f) for f, _t in Info._fields_}


def stats():
    out = Stats()
    _lib().moy_os_stats(ctypes.byref(out))
    return {f: getattr(out, f) for f, _t in Stats._fields_}


def post_event(kind, a=0, b=0):
    e = Event(0, 0, kind, 2, a, 0, b)
    _lib().moy_os_post_event(ctypes.byref(e))
