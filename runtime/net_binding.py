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
  sync_batch       a POST /sync body -> (root id, pin, ops JSON), or None
  sync_apply       one batch's ops applied into a store (native/moy_store's C)
  web_*            the webhost: start, stop, poll, state, the parked request,
                   the router without a socket (web_handle) and the pull

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
_STORE = os.path.join(native_build.ROOT, "native", "moy_store")
_WEB = os.path.join(native_build.ROOT, "native", "moy_web")
_SHIM = os.path.join(_NET, "moy_net_host.c")
_CACHE = os.path.join(native_build.ROOT, ".build", "host_net")
_SOURCES = ("moy_net.h", "moy_http.c", "moy_net_port.c","moy_sync.c", "moy_sync_apply.c",
            "moy_webhost.c", "moy_json.h", "moy_json.c", "moy_vol.h",
            "moy_vol.c", "moy_fs.h", "moy_fs.c", "moy_arena.h",
            "moy_journal.h", "moy_journal.c", "moy_store_host.c",
            "moy_web_blob.h")
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


class _Batch(ctypes.Structure):
    _fields_ = [("root", _I), ("ops", ctypes.c_void_p),
                ("ops_end", ctypes.c_void_p), ("pin", ctypes.c_void_p),
                ("pin_end", ctypes.c_void_p)]


class _Store(ctypes.Structure):
    _fields_ = [("carts", _P), ("files", _P), ("kinds", _P), ("journal", _I),
                ("ts", ctypes.c_int64)]


class _Err(ctypes.Structure):
    _fields_ = [("index", ctypes.c_uint32), ("why", ctypes.c_char * 40)]


class _Result(ctypes.Structure):
    _fields_ = [("applied", ctypes.c_uint32), ("refused", ctypes.c_uint32),
                ("shelf", _I), ("nerr", ctypes.c_uint32), ("err", _Err * 8)]


class _WebCfg(ctypes.Structure):
    _fields_ = [("port", ctypes.c_uint16), ("carts", _P), ("files", _P),
                ("kinds", _P), ("pin", _P), ("defer", _P),
                ("epoch", ctypes.c_int64)]


class _WebState(ctypes.Structure):
    _fields_ = [("serving", ctypes.c_uint8), ("closing", ctypes.c_uint8),
                ("parked", ctypes.c_uint8), ("listening", ctypes.c_uint8),
                ("port", ctypes.c_uint16),
                ("requests", ctypes.c_uint32), ("events", ctypes.c_uint32),
                ("err", _I)]


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
    ("moy_sync_batch", [ctypes.c_void_p, _Z, ctypes.POINTER(_Batch)], _I),
    ("moy_sync_apply", [ctypes.POINTER(_Store), _I, ctypes.c_void_p,
                        ctypes.c_void_p, ctypes.POINTER(_Result)], None),
    ("moy_web_start", [ctypes.POINTER(_WebCfg)], _I),
    ("moy_web_stop", [_P], None),
    ("moy_web_set_pin", [_P], None),
    ("moy_web_poll", [], _I),
    ("moy_web_state", [ctypes.POINTER(_WebState)], None),
    ("moy_web_events", [], ctypes.c_uint32),
    ("moy_web_take", [ctypes.POINTER(ctypes.c_void_p), _PZ,
                      ctypes.POINTER(ctypes.c_void_p), _PZ,
                      ctypes.POINTER(ctypes.c_void_p), _PZ], _I),
    ("moy_web_answer", [ctypes.c_void_p, _Z], None),
    ("moy_web_handle", [_P, _Z, _P, _Z, ctypes.c_void_p, _Z,
                        ctypes.POINTER(ctypes.c_void_p), _PZ], _I),
    ("moy_web_pack", [_P, _P, ctypes.POINTER(ctypes.c_void_p), _PZ], _I),
    ("moy_web_stamp_text", [], _P),
    ("moy_net_free", [ctypes.c_void_p], None),
    ("moy_net_host_bake", [ctypes.c_uint, _P, ctypes.c_void_p, ctypes.c_uint,
                           ctypes.c_uint, _P], None),
    ("moy_net_host_use_stored", [_Z, _Z], _I),
    ("moy_net_host_remember", [_I, _P, _Z, _P, _Z], _I),
)


def build(verbose=False):
    return native_build.build("moy_net", _SHIM, _SOURCES, _CACHE,
                              libmoy_dir=(_NET, _SPINE, _STORE, _WEB),
                              verbose=verbose)


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
    use = _lib().moy_net_host_use_stored(len(password or ""), len(stored or ""))
    return stored if use else password


def wifi_remember(ok, password, stored):
    """Whether a connect's credentials are written to the store."""
    p = _bytes(password or "")
    s = None if stored is None else _bytes(stored)
    return bool(_lib().moy_net_host_remember(1 if ok else 0, p, len(p), s,
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


def sync_batch(body):
    """(root id, pin, ops JSON text) of a POST /sync body, or None when the
    batch is malformed or speaks for a root this build does not serve."""
    raw = _bytes(body)
    buf = ctypes.create_string_buffer(raw, len(raw))
    b = _Batch()
    if _lib().moy_sync_batch(buf, len(raw), ctypes.byref(b)) != 0:
        return None
    base = ctypes.addressof(buf)
    pin = None
    if b.pin:
        pin = json.loads(raw[b.pin - base:b.pin_end - base].decode("utf-8"))
    ops = raw[b.ops - base:b.ops_end - base].decode("utf-8")
    return ("files" if b.root == 1 else "carts", pin, ops)


def _names(names):
    if names is None:
        return None
    return b"".join(_bytes(n) + b"\0" for n in names) + b"\0"


def sync_apply(carts, files, kinds, root_id, ops, journal):
    """(applied, [(index, reason)], shelf, refused) of the ops JSON text `ops`
    applied into `root_id` of the store whose roots are `carts` and `files`."""
    raw = _bytes(ops)
    buf = ctypes.create_string_buffer(raw, len(raw))
    if not raw.strip().startswith(b"["):
        raise ValueError("ops")
    import time
    st = _Store(_bytes(carts), None if files is None else _bytes(files),
                _names(kinds), 1 if journal else 0, int(time.time()))
    r = _Result()
    base = ctypes.addressof(buf)
    lead = len(raw) - len(raw.lstrip())
    end = len(raw.rstrip())
    _lib().moy_sync_apply(ctypes.byref(st), 1 if root_id == "files" else 0,
                          base + lead, base + end, ctypes.byref(r))
    errs = [(r.err[i].index, r.err[i].why.decode("utf-8"))
            for i in range(r.nerr)]
    return (r.applied, errs, bool(r.shelf), r.refused)


def web_start(port, carts, files, kinds, pin, defer):
    """Bind and serve; OSError on a bind failure."""
    cfg = _WebCfg(port, _bytes(carts), None if files is None else _bytes(files),
                  _names(kinds), None if not pin else _bytes(pin),
                  _names(defer), 0)
    rc = _lib().moy_web_start(ctypes.byref(cfg))
    if rc:
        raise OSError(rc, os.strerror(rc))


def web_stop(why=None):
    _lib().moy_web_stop(None if why is None else _bytes(why))


def web_set_pin(pin):
    _lib().moy_web_set_pin(None if not pin else _bytes(pin))


def web_poll():
    return bool(_lib().moy_web_poll())


def web_state():
    """(serving, closing, parked, port, requests, err)"""
    st = _WebState()
    _lib().moy_web_state(ctypes.byref(st))
    return (bool(st.serving), bool(st.closing), bool(st.parked), st.port,
            st.requests, st.err)


def web_events():
    return _lib().moy_web_events()


def web_take():
    """(method, target, body bytes) of the parked request, or None."""
    p = [ctypes.c_void_p() for _ in range(3)]
    n = [ctypes.c_size_t() for _ in range(3)]
    if not _lib().moy_web_take(ctypes.byref(p[0]), ctypes.byref(n[0]),
                               ctypes.byref(p[1]), ctypes.byref(n[1]),
                               ctypes.byref(p[2]), ctypes.byref(n[2])):
        return None
    got = [ctypes.string_at(p[i].value, n[i].value) if n[i].value else b""
           for i in range(3)]
    return (_text(got[0]), _text(got[1]), got[2])


def web_answer(resp):
    raw = _bytes(resp)
    _lib().moy_web_answer(raw, len(raw))


def _taken(out, n):
    d = _lib()
    try:
        return ctypes.string_at(out.value, n.value) if n.value else b""
    finally:
        d.moy_net_free(out)


def web_handle(method, target, body):
    """The whole response to one request, or None when the VM answers it."""
    m, t, b = _bytes(method), _bytes(target), _bytes(body or b"")
    out = ctypes.c_void_p()
    n = ctypes.c_size_t()
    done = _lib().moy_web_handle(m, len(m), t, len(t), b, len(b),
                                 ctypes.byref(out), ctypes.byref(n))
    raw = _taken(out, n)
    return raw if done else None


def web_pack(root, kinds):
    """The store under `root` as the pull's JSON text."""
    out = ctypes.c_void_p()
    n = ctypes.c_size_t()
    rc = _lib().moy_web_pack(_bytes(root), _names(kinds), ctypes.byref(out),
                             ctypes.byref(n))
    raw = _taken(out, n)
    if rc:
        raise OSError(rc, os.strerror(rc))
    return raw.decode("utf-8")


def web_stamp():
    s = _lib().moy_web_stamp_text()
    return None if s is None else s.decode("utf-8")


# -- the host's stand-ins for what an image links (tests only) -------------------

_BAKED = []


def _bake(blobs, stamp="0 0 none"):
    """Make {served name: bytes} the image's baked bundle; {} for none."""
    d = _lib()
    del _BAKED[:]
    items = sorted(blobs.items())
    for i, (name, data) in enumerate(items):
        n = _bytes(name)
        buf = ctypes.create_string_buffer(bytes(data), len(data) or 1)
        _BAKED.append((n, buf))
        d.moy_net_host_bake(i, n, buf, len(data), len(items), _bytes(stamp))
    if not items:
        d.moy_net_host_bake(99, None, None, 0, 0, None)


def _drains():
    """How many times the webhost has drained the panel feeder."""
    return ctypes.c_long.in_dll(_lib(), "moy_net_host_drains").value


def _skew_clock(ms):
    """Move the links' clock on by `ms` (a window running out in a test)."""
    v = ctypes.c_uint32.in_dll(_lib(), "moy_net_ms_skew")
    v.value = (v.value + ms) & 0xFFFFFFFF
