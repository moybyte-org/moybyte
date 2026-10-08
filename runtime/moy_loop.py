"""The kernel's frame loop (native/moy_kernel/moy_loop.c) on CPython, by
ctypes: the module `moy_loop` as the boards and the desktop MicroPython import
it, name for name (native/moy_kernel/modmoy_loop.c has the list). A VM's
`import moy_loop` is the native module, which always wins over this file;
CPython has no such module, so this is what it finds.

The loop is C on every tier; what this file adds is the upcall dispatcher a
VM's binding has in C: the registered callables are kept here, an exception
an upcall raises is reported and survived, and a KeyboardInterrupt ends the
loop (INTERRUPT). The trace tier (moy_loop_host.c) is the stage ops the host
drives it with.
"""

from __future__ import annotations

import ctypes
import os
import sys
import traceback

try:                                    # the host's runtime package
    from . import native_build
except ImportError:                     # loaded flat, runtime/ on sys.path
    import native_build

_KERNEL = os.path.join(native_build.ROOT, "native", "moy_kernel")
_CACHE = os.path.join(native_build.ROOT, ".build", "host_loop")
_SHIM = os.path.join(_KERNEL, "moy_loop.c")

OK, QUIT, INTERRUPT, STOPPED, EXIT = 0, 1, 2, 3, 4
DIM, SAVER, BLANK = 1, 2, 3
SVC_WEB, SVC_LINK, SVC_UPDATE, SVC_HEALTHY = 1, 2, 4, 8
_UP_INPUT, _UP_POINTER, _UP_FRAME, _UP_WORD, _UP_SERVICE = range(5)
_RAISED, _INTERRUPTED, _ABSENT, _EXIT = -1, -2, -3, -4
_CLASSES = 5
_STAGES = 11

_U32 = ctypes.c_uint32
_I32 = ctypes.c_int32
_B = ctypes.c_bool
_I = ctypes.c_int
_P = ctypes.c_void_p
_UP_FN = ctypes.CFUNCTYPE(_I, _I, _U32, ctypes.c_char_p)


class _Meter(ctypes.Structure):
    _fields_ = [("budget_us", _I32), ("avg_us", _U32), ("last_us", _U32),
                ("max_us", _U32), ("misses", _U32), ("n", _U32)]


class _Pump(ctypes.Structure):
    _fields_ = [("frame_ms", _U32), ("slot", _U32), ("debt", _U32),
                ("slack", _U32), ("tick_ms", _U32)]


class _Idle(ctypes.Structure):
    _fields_ = [("after_s", _U32 * 5), ("idle_at", _U32), ("state", ctypes.c_uint8),
                ("can_dim", _B), ("force", _B), ("wake", _B)]


_SIGS = (
    ("moy_loop_set_upcall", [_UP_FN], None),
    ("moy_loop_set_registered", [_U32], None),
    ("moy_loop_registered", [], _U32),
    ("moy_loop_step", [], _I),
    ("moy_loop_run", [], _I),
    ("moy_loop_upcalls", [ctypes.POINTER(_U32), ctypes.POINTER(_U32)], None),
    ("moy_loop_set_tick", [_U32], None),
    ("moy_loop_set_fps", [_I], None),
    ("moy_loop_set_services", [_U32], None),
    ("moy_loop_services", [], _U32),
    ("moy_devch_unread", [ctypes.c_char_p, ctypes.c_size_t], None),
    ("moy_loop_set_vm", [_B], None),
    ("moy_loop_word", [ctypes.c_char_p], _I),
    ("moy_loop_set_capture", [_B], None),
    ("moy_loop_capture", [], _B),
    ("moy_loop_meter", [_I, ctypes.POINTER(_Meter)], _B),
    ("moy_loop_meters_reset", [], None),
    ("moy_loop_stage_name", [_I], ctypes.c_char_p),
    ("moy_loop_pump", [ctypes.POINTER(_Pump)], None),
    ("moy_loop_pace", [_U32], _U32),
    ("moy_loop_set_lit", [_B], None),
    ("moy_loop_arm_health", [_B], None),
    ("moy_loop_frames", [], _U32),
    ("moy_loop_drawn", [], _U32),
    ("moy_loop_frame_at", [], _U32),
    ("moy_loop_last", [ctypes.POINTER(_U32), ctypes.POINTER(_U32)], None),
    ("moy_loop_idle", [], ctypes.POINTER(_Idle)),
    ("moy_idle_set", [ctypes.POINTER(_Idle), _I, _U32], None),
    ("moy_idle_get", [ctypes.POINTER(_Idle), _I], _U32),
    ("moy_idle_blank", [ctypes.POINTER(_Idle)], None),
    ("moy_idle_wake", [ctypes.POINTER(_Idle)], None),
    ("moy_loop_perf_due", [], _B),
    ("moy_loop_perf_console", [], _P),
    ("moy_perf_field", [ctypes.c_char_p], _I),
    ("moy_perf_unset", [_P, _I], None),
    ("moy_perf_set", [_P, _I, _I, ctypes.c_double], None),
    ("moy_perf_set_cart", [_P, ctypes.c_char_p], None),
    ("moy_perf_clear", [_P], None),
    ("moy_perf_values_size", [], ctypes.c_size_t),
    ("moy_perf_format", [_P, ctypes.c_char_p, ctypes.c_size_t], ctypes.c_size_t),
    ("moy_devch_tap", [_I32, _I32], None),
    ("moy_devch_swipe", [_I32, _I32, _I32, _I32, _I32], None),
    ("moy_devch_drag", [_I32, _I32, _I32, _I32], None),
    ("moy_devch_line", [ctypes.c_char_p, ctypes.POINTER(_B)], _B),
    ("moy_devch_set_budget", [_U32], None),
    ("moy_devch_set_armed", [_B], None),
    ("moy_devch_armed", [], _B),
    ("moy_loop_host_init", [_I, _B, _U32], None),
    ("moy_loop_host_clock", [_U32], None),
    ("moy_loop_host_input", [_B, _B], None),
    ("moy_loop_host_advance", [_U32], None),
    ("moy_loop_host_cost", [_I, _U32], None),
    ("moy_loop_host_feed", [ctypes.c_char_p, ctypes.c_size_t], None),
    ("moy_loop_host_log", [], ctypes.c_char_p),
    ("moy_loop_host_clear", [], None),
    ("moy_loop_host_note", [ctypes.c_char_p], None),
    ("moy_loop_driver_step", [_U32], _I),
)

_LIB = [None]


def build(verbose=False):
    return native_build.build("moy_loop", _SHIM,
                              ("moy_loop.h", "moy_idle.h", "moy_idle.c", "moy_perf.h",
                               "moy_perf.c", "moy_devch.h", "moy_devch.c",
                               "moy_loop_host.h", "moy_loop_host.c"),
                              _CACHE, libmoy_dir=_KERNEL, verbose=verbose)


def _lib():
    if _LIB[0] is None:
        path = build()
        if path is None:
            raise ImportError("moy_loop: no C compiler for the host build")
        d = ctypes.CDLL(path)
        for name, args, res in _SIGS:
            fn = getattr(d, name)
            fn.argtypes = args
            fn.restype = res
        _LIB[0] = d
    return _LIB[0]


# The registered upcalls, by MOY_UP_*: the host's form of the root pointers.
_UPS = [None] * 5


def _report(exc, which):
    kind = "REMOTE" if which == _UP_WORD else "service" if which == _UP_SERVICE else "frame"
    print("Moybyte %s error: %s" % (kind, exc))
    traceback.print_exception(type(exc), exc, exc.__traceback__, file=sys.stdout)


# The driver tier's pending exception: handed back to its harness by drive().
_EXC = [None]
_DRIVING = [False]


def _dispatch(which, arg, line):
    fn = _UPS[which] if 0 <= which < 5 else None
    if fn is None:
        return _ABSENT
    if _DRIVING[0]:
        if _EXC[0] is not None:
            return _ABSENT
        try:
            r = fn(arg / 1000000.0) if which == _UP_FRAME else fn()
        except BaseException as exc:  # noqa: BLE001 -- the harness gets it back
            _EXC[0] = exc
            return _RAISED
        return r if isinstance(r, int) and r > 0 else 0
    try:
        if which == _UP_FRAME:
            r = fn(arg / 1000000.0)
        elif which == _UP_WORD:
            r = fn(line.decode("utf-8"))
        elif which == _UP_SERVICE:
            r = fn(arg)
        else:
            r = fn()
    except KeyboardInterrupt:
        return _INTERRUPTED
    except SystemExit:
        return _EXIT
    except Exception as exc:  # noqa: BLE001 -- one bad frame never ends the loop
        _report(exc, which)
        return _RAISED
    if r is True:
        return 1
    if isinstance(r, int) and r > 0:
        return min(r, 0x7FFFFFFF)
    return 0


_UP_CB = _UP_FN(_dispatch)


def _set_registered():
    bits = 0
    for i in range(3):
        if _UPS[i] is not None:
            bits |= 1 << i
    _lib().moy_loop_set_registered(bits)


def register(handle_input, handle_pointer, frame, words=None, service=None):
    _UPS[:] = [handle_input, handle_pointer, frame, words, service]
    _lib().moy_loop_set_upcall(_UP_CB)
    _set_registered()


def unregister():
    _UPS[:] = [None] * 5
    _lib().moy_loop_set_registered(0)


def drive(handle_input, handle_pointer, frame, dt):
    """One frame of a harness that owns the clock and the input (the host's
    and the browser's ConsoleDriver): the loop over no stages but the clock,
    the three upcalls this harness's, and an exception one of them raised
    raised here, as a direct call would."""
    _UPS[:] = [handle_input, handle_pointer, frame, None, None]
    _lib().moy_loop_set_upcall(_UP_CB)
    _set_registered()
    _EXC[0] = None
    _DRIVING[0] = True
    try:
        r = _lib().moy_loop_driver_step(int(dt * 1000000 + 0.5) if dt > 0 else 0)
    finally:
        _DRIVING[0] = False
    exc = _EXC[0]
    if exc is not None:
        _EXC[0] = None
        raise exc
    return r


def step():
    return _lib().moy_loop_step()


def vm(up):
    """Whether a VM runs: the host's never stops, so only a test clears it."""
    _lib().moy_loop_set_vm(bool(up))


def word(line):
    """A dev-channel line to the console's words, as the kernel's channel
    hands one up: its value, or a MOY_UP_* failure."""
    return _lib().moy_loop_word(line.encode())


def run():
    return _lib().moy_loop_run()


def upcalls():
    f = (_U32 * _CLASSES)()
    t = (_U32 * _CLASSES)()
    _lib().moy_loop_upcalls(f, t)
    return tuple(f), tuple(t)


def tick(ms):
    _lib().moy_loop_set_tick(int(ms))


def fps(cap):
    _lib().moy_loop_set_fps(int(cap))


def services(bits=None):
    if bits is not None:
        _lib().moy_loop_set_services(int(bits))
    return _lib().moy_loop_services()


def devch_unread(data):
    data = bytes(data)
    _lib().moy_devch_unread(data, len(data))


def capture(on=None):
    if on is not None:
        _lib().moy_loop_set_capture(bool(on))
    return bool(_lib().moy_loop_capture())


def stages():
    return tuple(_lib().moy_loop_stage_name(i).decode() for i in range(_STAGES))


def meters():
    out = {}
    m = _Meter()
    for i in range(_STAGES):
        seen = _lib().moy_loop_meter(i, ctypes.byref(m))
        dl = m.budget_us >= 0
        out[_lib().moy_loop_stage_name(i).decode()] = (
            m.budget_us if dl else None,
            m.avg_us if seen else None, m.last_us if seen else None,
            m.max_us if seen else None, m.misses if (seen and dl) else None, m.n)
    return out


def meters_reset():
    _lib().moy_loop_meters_reset()


def pump():
    p = _Pump()
    _lib().moy_loop_pump(ctypes.byref(p))
    return (p.frame_ms, p.slot, p.debt, p.slack, p.tick_ms)


def pace(elapsed):
    return _lib().moy_loop_pace(int(elapsed))


def lit(on):
    _lib().moy_loop_set_lit(bool(on))


def health(on):
    _lib().moy_loop_arm_health(bool(on))


def frames():
    return _lib().moy_loop_frames()


def last():
    e, sl = _U32(), _U32()
    _lib().moy_loop_last(ctypes.byref(e), ctypes.byref(sl))
    return min(e.value, 0x3FFF) * 65536 + min(sl.value, 0xFFFF)


def diag_take():
    hitch = ctypes.create_string_buffer(320)
    loop = ctypes.create_string_buffer(320)
    got = _lib().moy_loop_diag_take(hitch, loop, 320)
    return (hitch.value.decode() if got & 1 else None,
            loop.value.decode() if got & 2 else None)


def frame_at():
    return _lib().moy_loop_frame_at()


def drawn():
    return _lib().moy_loop_drawn()


def idle(rung=None, secs=None):
    d = _lib().moy_loop_idle()
    if rung is not None:
        _lib().moy_idle_set(d, int(rung), int(secs))
    get = _lib().moy_idle_get
    return (d.contents.state, get(d, DIM), get(d, SAVER), get(d, BLANK))


def idle_state():
    return _lib().moy_loop_idle().contents.state


def idle_can_dim():
    return bool(_lib().moy_loop_idle().contents.can_dim)


def power(on):
    d = _lib().moy_loop_idle()
    if on:
        _lib().moy_idle_wake(d)
    else:
        _lib().moy_idle_blank(d)


def perf_due():
    return bool(_lib().moy_loop_perf_due())


def _perf_put(v, name, value):
    f = _lib().moy_perf_field(name.encode())
    if f < 0:
        return False
    _lib().moy_perf_unset(v, f)
    if value is None:
        return True
    if name == "cart":
        _lib().moy_perf_set_cart(v, str(value).encode("utf-8"))
        return True
    parts = value if isinstance(value, (tuple, list)) else (value,)
    for i, x in enumerate(parts[:5]):
        if x is not None:
            _lib().moy_perf_set(v, f, i, float(x))
    return True


def perf(name, value):
    v = _lib().moy_loop_perf_console()
    if not v:
        return False
    return _perf_put(v, name, value)


def perf_cart(title):
    v = _lib().moy_loop_perf_console()
    if v:
        _lib().moy_perf_set_cart(v, None if title is None else str(title).encode("utf-8"))


def perf_format(values):
    buf = ctypes.create_string_buffer(_lib().moy_perf_values_size())
    _lib().moy_perf_clear(buf)
    for name, value in values.items():
        _perf_put(buf, name, value)
    out = ctypes.create_string_buffer(320)
    n = _lib().moy_perf_format(buf, out, 320)
    return out.raw[:n].decode()


def tap(x, y):
    _lib().moy_devch_tap(int(x), int(y))


def swipe(x0, y0, x1, y1, n=20):
    _lib().moy_devch_swipe(int(x0), int(y0), int(x1), int(y1), int(n))


def drag(cx, cy, n=120, step=6):
    _lib().moy_devch_drag(int(cx), int(cy), int(n), int(step))


def devch(line):
    quit_ = _B(False)
    _lib().moy_devch_line(line.encode("utf-8"), ctypes.byref(quit_))
    return bool(quit_.value)


def devch_budget(n):
    _lib().moy_devch_set_budget(int(n))


def devch_armed(on=None):
    if on is not None:
        _lib().moy_devch_set_armed(bool(on))
    return bool(_lib().moy_devch_armed())


def trace_init(fps, can_dim, clock):
    _lib().moy_loop_host_init(int(fps), bool(can_dim), int(clock))


def trace_clock(ms):
    _lib().moy_loop_host_clock(int(ms))


def trace_advance(us):
    _lib().moy_loop_host_advance(int(us))


def trace_cost(stage, us):
    _lib().moy_loop_host_cost(int(stage), int(us))


def trace_input(click, active):
    _lib().moy_loop_host_input(bool(click), bool(active))


def trace_feed(data):
    data = bytes(data)
    _lib().moy_loop_host_feed(data, len(data))


def trace_log():
    s = _lib().moy_loop_host_log().decode()
    _lib().moy_loop_host_clear()
    return s


def trace_note(token):
    _lib().moy_loop_host_note(token.encode())
