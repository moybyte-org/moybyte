"""The links' wire half (native/moy_net) on CPython, by ctypes: the module
`moy_net` as the boards and the browser import it, name for name, over the
same C built for the host.

  wifi_password    the password a connect uses: the one given, else the stored
  wifi_remember    whether a connect's credentials are written to the store
  parse_request    a raw HTTP request -> (method, target, length, header end)
  query_param      one parameter of a request target's query string
  http_response    a complete HTTP/1.1 response, Connection: close
  encode_batch     a sync batch's wire JSON
  decode_batch     a sync batch's fields off the wire, or None when malformed

`install()` puts this module where `import moy_net` finds it. What it adds to
the C is what ctypes cannot do by itself: JSON text for a batch's ops (the
boards' module prints them with the port's own printer) and Python values from
the spans the C returns. The rules each function keeps are moy_net.h's.
"""

from __future__ import annotations

import ctypes
import json
import os
import sys

from . import native_build

_NET = os.path.join(native_build.ROOT, "native", "moy_net")
_SPINE = os.path.join(native_build.ROOT, "native", "moy_spine")
_SHIM = os.path.join(_NET, "moy_http.c")
_CACHE = os.path.join(native_build.ROOT, ".build", "host_net")
_SOURCES = ("moy_net.h", "moy_sync.c", "moy_wifi.c", "moy_json.h", "moy_json.c")
_LIB = [None]

_P = ctypes.c_char_p
_Z = ctypes.c_size_t
_I = ctypes.c_int
_PP = ctypes.POINTER(ctypes.c_char_p)
_PZ = ctypes.POINTER(ctypes.c_size_t)


class _Req(ctypes.Structure):
    _fields_ = [("method", ctypes.c_void_p), ("method_n", _Z),
                ("target", ctypes.c_void_p), ("target_n", _Z),
                ("clen", ctypes.c_uint32), ("head_end", _Z)]


class _Env(ctypes.Structure):
    _fields_ = [(n, ctypes.c_void_p) for n in (
        "v", "v_end", "root", "root_end", "ops", "ops_end", "pin", "pin_end")]


_SIGS = (
    ("moy_http_parse", [ctypes.c_void_p, _Z, ctypes.POINTER(_Req)], _I),
    ("moy_http_query", [ctypes.c_void_p, _Z, _P, _Z,
                        ctypes.POINTER(ctypes.c_void_p), _PZ], _I),
    ("moy_http_head", [ctypes.c_void_p, _Z, _I, _P, _Z], _Z),
    ("moy_utf8_valid", [ctypes.c_void_p, _Z], _I),
    ("moy_sync_decode", [ctypes.c_void_p, _Z, ctypes.POINTER(_Env)], _I),
    ("moy_sync_encode", [ctypes.c_void_p, _Z, _P, _Z, _P, _Z, _P, _Z, _P, _Z],
     _Z),
    ("moy_wifi_use_stored", [_Z, _Z], _I),
    ("moy_wifi_remember", [_I, _P, _Z, _P, _Z], _I),
)


def build(verbose=False):
    return native_build.build("moy_net", _SHIM, _SOURCES, _CACHE,
                              libmoy_dir=(_NET, _SPINE), verbose=verbose)


def _lib():
    if _LIB[0] is None:
        path = build()
        if path is None:
            raise ImportError("moy_net: no C compiler for the host build")
        d = ctypes.CDLL(path)
        for name, args, res in _SIGS:
            fn = getattr(d, name)
            fn.argtypes = args
            fn.restype = res
        _LIB[0] = d
    return _LIB[0]


def install():
    """Make `import moy_net` reach this module, as a board's import reaches
    the usermod."""
    sys.modules.setdefault("moy_net", sys.modules[__name__])


def _bytes(x):
    return x.encode("utf-8") if isinstance(x, str) else bytes(x)


def _text(raw):
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("latin-1")


# -- WiFi ---------------------------------------------------------------------

def wifi_password(password, stored):
    """The password a connect to a network uses: the stored one when the one
    given is empty."""
    use = _lib().moy_wifi_use_stored(len(password or ""), len(stored or ""))
    return stored if use else password


def wifi_remember(ok, password, stored):
    """Whether a connect's credentials are written to the store."""
    p = _bytes(password or "")
    s = None if stored is None else _bytes(stored)
    return bool(_lib().moy_wifi_remember(1 if ok else 0, p, len(p), s,
                                         0 if s is None else len(s)))


# -- HTTP -----------------------------------------------------------------------

def parse_request(raw):
    """(method, target, content_length, header_end) of a raw request (bytes or
    str); header_end is a byte offset, -1 while the head is incomplete, and a
    malformed head is (None, None, 0, -1)."""
    raw = _bytes(raw)
    buf = ctypes.create_string_buffer(raw, len(raw))
    r = _Req()
    if _lib().moy_http_parse(buf, len(raw), ctypes.byref(r)) != 1:
        return (None, None, 0, -1)
    base = ctypes.addressof(buf)
    method = raw[r.method - base:r.method - base + r.method_n]
    target = raw[r.target - base:r.target - base + r.target_n]
    return (_text(method), _text(target), r.clen, r.head_end)


def query_param(target, name):
    """The value of `name` in a request target's query string, or ""."""
    if not target:
        return ""
    t = _bytes(target)
    n = _bytes(name)
    buf = ctypes.create_string_buffer(t, len(t))
    v = ctypes.c_void_p()
    vn = ctypes.c_size_t()
    _lib().moy_http_query(buf, len(t), n, len(n), ctypes.byref(v),
                          ctypes.byref(vn))
    if not vn.value:
        return ""
    off = v.value - ctypes.addressof(buf)
    return t[off:off + vn.value].decode("utf-8")


def http_response(status, body, content_type="application/json"):
    """A complete HTTP/1.1 response (bytes), Connection: close."""
    body = _bytes(body)
    ct = content_type.encode("utf-8")
    d = _lib()
    need = d.moy_http_head(None, 0, status, ct, len(body))
    out = ctypes.create_string_buffer(need + 1)
    d.moy_http_head(out, need + 1, status, ct, len(body))
    return out.raw[:need] + body


# -- the sync batch -------------------------------------------------------------

def encode_batch(v, root_id, ops, pin=None):
    """A batch's wire JSON: version `v`, its root named only when `root_id` is
    not None, its ops and an optional pin."""
    fields = [json.dumps(v).encode()]
    fields.append(None if root_id is None else json.dumps(root_id).encode())
    fields.append(json.dumps(ops).encode())
    fields.append(json.dumps(pin).encode() if pin else None)
    args = []
    for f in fields:
        args += [f, 0 if f is None else len(f)]
    d = _lib()
    need = d.moy_sync_encode(None, 0, *args)
    out = ctypes.create_string_buffer(need + 1)
    d.moy_sync_encode(out, need, *args)
    return out.raw[:need].decode("utf-8")


def decode_batch(body):
    """(v, root, ops, pin) off the wire, or None when the body is not UTF-8,
    not JSON or not an object."""
    raw = _bytes(body)
    buf = ctypes.create_string_buffer(raw, len(raw))
    e = _Env()
    if _lib().moy_sync_decode(buf, len(raw), ctypes.byref(e)) != 0:
        return None
    base = ctypes.addressof(buf)

    def field(a, b):
        if not a:
            return None
        return json.loads(raw[a - base:b - base].decode("utf-8"))
    return (field(e.v, e.v_end), field(e.root, e.root_end),
            field(e.ops, e.ops_end), field(e.pin, e.pin_end))
