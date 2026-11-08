"""The service model's kernel half on CPython, by ctypes: the module `moy_evq`
as the boards and the desktop MicroPython import it, name for name
(native/moy_kernel/modmoy_evq.c has the list): the event queues
(moy_evq.h), the supervisor (moy_super.h) and the queue of calls into the VM
(moy_vmq.h), the C ones in the host's one library
(runtime/host/host_native.py)."""

from __future__ import annotations

import ctypes
import os

try:                                    # the host's runtime package
    from . import host_native, native_build
except ImportError:                     # loaded flat, runtime/ on sys.path
    import host_native
    import native_build

_KERNEL = os.path.join(native_build.ROOT, "native", "moy_kernel")

PART = native_build.Part("evq", ("moy_sync.h", "moy_evq.h", "moy_evq.c", "moy_super.h",
                                 "moy_super.c", "moy_vmq.h", "moy_vmq.c"), (_KERNEL,))

OK, STALE, FULL, NOMEM, DENIED, DEAF, EMPTY, RANGE = range(8)
FINE, WARNED, STRUCK, SUSPENDED = range(4)
SLICE_US = 20000
ABORT_US = 300000
STRIKES = 3
DEPTH = 16
SYSTEM = "system"
_VM = 0x01
_APP = 48
_NAME = 24

_U32, _I, _P, _SZ, _B = ctypes.c_uint32, ctypes.c_int, ctypes.c_char_p, ctypes.c_size_t, ctypes.c_bool
_VP = ctypes.c_void_p


class _Ev(ctypes.Structure):
    _fields_ = [("frm", _U32), ("n", _U32), ("app", ctypes.c_char * _APP),
                ("name", ctypes.c_char * _NAME)]


_SIGS = (
    ("moy_evq_open", [_U32, _P, _P, _U32], _I),
    ("moy_evq_close", [_U32], _I),
    ("moy_evq_has", [_U32], _B),
    ("moy_evq_app", [_U32], _P),
    ("moy_evq_listen", [_U32, _P, _B], _I),
    ("moy_evq_post", [_U32, _U32, _P, _P, _SZ], _I),
    ("moy_evq_send", [_U32, _P, _P, _P, _SZ, ctypes.POINTER(_U32)], _I),
    ("moy_evq_route", [_P, _P, _SZ], _I),
    ("moy_evq_take", [_U32, ctypes.POINTER(_Ev), _VP, _SZ], _I),
    ("moy_evq_queued", [_U32], _U32),
    ("moy_evq_ready", [], _U32),
    ("moy_evq_kick", [_U32], None),
    ("moy_evq_vm_stop", [], _U32),
    ("moy_evq_live", [], _U32),
    ("moy_evq_reset", [], None),
    ("moy_super_charge", [_U32, _U32, _B], _I),
    ("moy_super_held", [_U32], _B),
    ("moy_super_resume", [_U32], _B),
    ("moy_super_info", [_U32, ctypes.POINTER(_U32), ctypes.POINTER(_U32)], None),
    ("moy_super_forget", [_U32], None),
    ("moy_super_reset", [], None),
    ("moy_vmq_serve", [], _U32),
    ("moy_vmq_next", [ctypes.POINTER(_U32), ctypes.POINTER(_U32), ctypes.POINTER(_P),
                      ctypes.POINTER(_VP), ctypes.POINTER(_SZ)], _I),
    ("moy_vmq_answer", [_U32, _P, _SZ], _I),
    ("moy_vmq_drop", [_U32], None),
    ("moy_vmq_reset", [], None),
)

_LIB = [None]


def _lib():
    if _LIB[0] is None:
        path = host_native.build()
        if path is None:
            raise ImportError("moy_evq: no C compiler for the host build")
        _LIB[0] = native_build.load_cdll(path, _SIGS)
    return _LIB[0]


def _b(s):
    return s.encode() if isinstance(s, str) else s


def open(pid, app, receives="", vm=False):  # noqa: A001 -- the module's name for it
    return _lib().moy_evq_open(pid, _b(app), _b(receives) if receives else None,
                               _VM if vm else 0)


def close(pid):
    return _lib().moy_evq_close(pid)


def has(pid):
    return bool(_lib().moy_evq_has(pid))


def app(pid):
    got = _lib().moy_evq_app(pid)
    return got.decode() if got is not None else None


def listen(pid, name, on):
    return _lib().moy_evq_listen(pid, _b(name), bool(on))


def post(to, frm, name, args):
    b = _b(args)
    return _lib().moy_evq_post(to, frm, _b(name), b, len(b))


def send(frm, app, name, args):
    b = _b(args)
    denied = _U32(0)
    took = _lib().moy_evq_send(frm, _b(app), _b(name), b, len(b), ctypes.byref(denied))
    return (-1 if took < 0 else took, denied.value)


def route(name, args):
    b = _b(args)
    return _lib().moy_evq_route(_b(name), b, len(b))


def take(pid):
    ev = _Ev()
    buf = ctypes.create_string_buffer(256)
    rc = _lib().moy_evq_take(pid, ctypes.byref(ev), buf, len(buf))
    if rc == RANGE:
        buf = ctypes.create_string_buffer(ev.n + 1)
        rc = _lib().moy_evq_take(pid, ctypes.byref(ev), buf, len(buf))
    if rc != OK:
        return None
    return (ev.frm, ev.app.decode(), ev.name.decode(), buf.raw[:ev.n].decode())


def queued(pid):
    return _lib().moy_evq_queued(pid)


def ready():
    return _lib().moy_evq_ready()


def charge(pid, cost_us, aborted):
    return _lib().moy_super_charge(pid, max(0, int(cost_us)) & 0xFFFFFFFF, bool(aborted))


def held(pid):
    return bool(_lib().moy_super_held(pid))


def resume(pid):
    was = bool(_lib().moy_super_resume(pid))
    _lib().moy_evq_kick(pid)
    return was


def rungs(pid):
    o, s = _U32(0), _U32(0)
    _lib().moy_super_info(pid, ctypes.byref(o), ctypes.byref(s))
    return (o.value, s.value)


def forget(pid):
    _lib().moy_super_forget(pid)


def serve():
    return _lib().moy_vmq_serve()


def next_call():
    t, pid, name, args, n = _U32(0), _U32(0), _P(), _VP(), _SZ(0)
    if _lib().moy_vmq_next(ctypes.byref(t), ctypes.byref(pid), ctypes.byref(name),
                           ctypes.byref(args), ctypes.byref(n)) != OK:
        return None
    text = ctypes.string_at(args, n.value).decode() if n.value else ""
    return (t.value, pid.value, name.value.decode(), text)


def answer(ticket, ans):
    b = _b(ans) if ans is not None else None
    return _lib().moy_vmq_answer(ticket, b, len(b) if b is not None else 0)


def drop(pid):
    _lib().moy_vmq_drop(pid)


def vm_stop():
    return _lib().moy_evq_vm_stop()


def live():
    return _lib().moy_evq_live()


def reset():
    d = _lib()
    d.moy_evq_reset()
    d.moy_super_reset()
    d.moy_vmq_reset()
