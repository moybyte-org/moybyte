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
  ota_*            the updater (moy_ota.h): the manifest's check, the
                   download into the slot or the C6, the copied image's
                   install, the keys and the signature
  http_*           the streaming client Get Carts and the updater fetch through

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
            "moy_webhost.c", "moy_ota.h", "moy_ota.c", "moy_webconsole.c", "moy_gpio.c", "moy_dns.c", "moy_json.h", "moy_json.c", "moy_vol.h",
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


class _OtaState(ctypes.Structure):
    _fields_ = [("phase", ctypes.c_uint8), ("sink", ctypes.c_uint8),
                ("dl_done", ctypes.c_uint32), ("dl_total", ctypes.c_uint32),
                ("done", ctypes.c_uint32), ("total", ctypes.c_uint32),
                ("err", ctypes.c_char * 48)]


class _WcState(ctypes.Structure):
    _fields_ = [("state", ctypes.c_uint8), ("parked", ctypes.c_uint8),
                ("dialled", ctypes.c_uint8), ("port", ctypes.c_uint16),
                ("ip", ctypes.c_uint32), ("err", _I)]


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
    ("moy_ota_keys", [], _I),
    ("moy_ota_key_hex", [_P, _Z, ctypes.c_void_p], _I),
    ("moy_ota_key", [_I], ctypes.c_void_p),
    ("moy_ota_verify", [ctypes.c_void_p, _Z, _P, _Z, ctypes.c_void_p, _I], _I),
    ("moy_ota_canonical", [_P, _Z, ctypes.c_void_p, _Z], _Z),
    ("moy_ota_canonical_c6", [_P, _Z, ctypes.c_void_p, _Z], _Z),
    ("moy_ota_judge", [_P, _Z, _P, _I, ctypes.c_void_p, _I,
                       ctypes.c_void_p, _Z], _I),
    ("moy_ota_judge_c6", [_P, _Z, _I, ctypes.c_void_p, _I, ctypes.c_void_p, _Z],
     _I),
    ("moy_ota_check", [_P, _P, _I, ctypes.POINTER(ctypes.c_void_p), _PZ], _I),
    ("moy_ota_trust", [ctypes.c_void_p, _I], None),
    ("moy_ota_error", [], _P),
    ("moy_ota_dl_begin", [_P, ctypes.c_uint32, _P, _I], _I),
    ("moy_ota_dl_step", [ctypes.c_uint32], _I),
    ("moy_ota_dl_finish", [], _I),
    ("moy_ota_cancel", [], None),
    ("moy_ota_slot_begin", [ctypes.c_uint32], _I),
    ("moy_ota_slot_write", [ctypes.c_void_p, _Z], _I),
    ("moy_ota_slot_close", [], _I),
    ("moy_ota_activate", [ctypes.c_void_p, _Z], _I),
    ("moy_ota_c6_commit", [], _I),
    ("moy_ota_state", [ctypes.c_void_p], None),
    ("moy_httpc_open", [_P, _P, ctypes.POINTER(_I),
                        ctypes.POINTER(ctypes.c_uint32)], _I),
    ("moy_httpc_read", [_I, ctypes.c_void_p, _Z], _I),
    ("moy_httpc_close", [_I], None),
    ("moy_net_vm_stop", [], None),
    ("moy_wc_on", [ctypes.c_void_p, ctypes.c_uint32], _I),
    ("moy_dns_reply", [ctypes.c_void_p, _Z, ctypes.c_uint32, ctypes.c_void_p, _Z], _Z),
    ("moy_dns_start", [ctypes.c_uint32, ctypes.c_uint16], _I),
    ("moy_dns_port", [], ctypes.c_uint16),
    ("moy_dns_poll", [], _I),
    ("moy_dns_stop", [], None),
    ("moy_wc_off", [_P], None),
    ("moy_wc_poll", [], _I),
    ("moy_wc_state", [ctypes.c_void_p], None),
    ("moy_wc_set_pin", [_P], None),
    ("moy_wc_park", [_I], None),
    ("moy_wc_url", [ctypes.c_void_p, _Z, _I], _Z),
    ("moy_c6_version", [], _I),
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


def _ip_of(ip):
    try:
        parts = [int(p) for p in ip.split(".")]
    except (AttributeError, ValueError):
        return 0
    if len(parts) != 4 or any(p < 0 or p > 255 for p in parts):
        return 0
    return parts[0] | parts[1] << 8 | parts[2] << 16 | parts[3] << 24


def wc_on(port, carts, files, kinds, pin, defer, ip):
    """The web-console switch on: never waits; OSError for a refused
    configuration."""
    cfg = _WebCfg(port, _bytes(carts), None if files is None else _bytes(files),
                  _names(kinds), None if not pin else _bytes(pin),
                  _names(defer), 0)
    rc = _lib().moy_wc_on(ctypes.byref(cfg), _ip_of(ip))
    if rc:
        raise OSError(rc, os.strerror(rc))


def wc_off(why=None):
    _lib().moy_wc_off(None if why is None else _bytes(why))


def wc_poll():
    return bool(_lib().moy_wc_poll())


def wc_state():
    """(state, parked, dialled, port, ip or None, err)"""
    st = _WcState()
    _lib().moy_wc_state(ctypes.byref(st))
    ip = None
    if st.ip:
        ip = "%d.%d.%d.%d" % (st.ip & 255, (st.ip >> 8) & 255,
                              (st.ip >> 16) & 255, st.ip >> 24)
    return (st.state, bool(st.parked), bool(st.dialled), st.port, ip, st.err)


def wc_phase(port):
    """The switch's state when it is on `port`, else 0."""
    st = _WcState()
    _lib().moy_wc_state(ctypes.byref(st))
    return st.state if st.port == port else 0


def dns_reply(query, ip):
    """The portal's answer to one query datagram, or None to drop it."""
    q = bytes(query)
    out = ctypes.create_string_buffer(600)
    n = _lib().moy_dns_reply(q, len(q), _ip_of(ip) if ip else 0, out, 600)
    return out.raw[:n] if n else None


def dns_start(ip, port):
    return 0 <= port <= 65535 and _lib().moy_dns_start(_ip_of(ip), port) == 0


def dns_port():
    return _lib().moy_dns_port()


def dns_poll():
    return _lib().moy_dns_poll()


def dns_stop():
    _lib().moy_dns_stop()


def wc_set_pin(pin):
    _lib().moy_wc_set_pin(None if not pin else _bytes(pin))


def wc_park(on):
    _lib().moy_wc_park(1 if on else 0)


def wc_url(paired):
    out = ctypes.create_string_buffer(96)
    n = _lib().moy_wc_url(out, 96, 1 if paired else 0)
    return out.raw[:min(n, 95)].decode("utf-8")


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


# -- the updater and its client (moy_ota.h) -------------------------------------

_KEY_BYTES = 256


def ota_keys():
    """The moduli this image trusts, big-endian bytes each."""
    d = _lib()
    return tuple(ctypes.string_at(d.moy_ota_key(i), _KEY_BYTES)
                 for i in range(d.moy_ota_keys()))


def _keys_arg(keys):
    """`keys` as (modulus_hex, exponent) pairs -> the C's key array and its
    count, an entry that is not a usable key skipped; (None, -1) for the baked
    ones."""
    if keys is None:
        return None, -1
    arr = (ctypes.c_uint8 * (_KEY_BYTES * max(1, len(keys))))()
    n = 0
    for k in keys:
        h = k[0] if isinstance(k, tuple) and k else None
        if not isinstance(h, str):
            continue
        hb = h.encode()
        if _lib().moy_ota_key_hex(hb, len(hb), ctypes.addressof(arr) + n * _KEY_BYTES) == 0:
            n += 1
    return arr, n


def _with_keys(keys, fn):
    arr, n = _keys_arg(keys)
    if n == 0:
        return None
    return fn(arr if n > 0 else None, max(n, 0))


def ota_verify(payload, sig, keys=None):
    """Whether `sig` (hex) signs `payload` under a baked key (or `keys`)."""
    if not sig or not isinstance(sig, str):
        return False
    p, s = _bytes(payload), _bytes(sig)
    got = _with_keys(keys, lambda arr, n: _lib().moy_ota_verify(
        p, len(p), s, len(s), arr, n))
    return bool(got)


def _canon(fn, manifest_text):
    t = _bytes(manifest_text)
    need = fn(t, len(t), None, 0)
    if need == ctypes.c_size_t(-1).value:
        return None
    out = ctypes.create_string_buffer(need + 1)
    fn(t, len(t), out, need)
    return out.raw[:need]


def ota_canonical(manifest_text):
    return _canon(_lib().moy_ota_canonical, manifest_text)


def ota_canonical_c6(manifest_text):
    return _canon(_lib().moy_ota_canonical_c6, manifest_text)


def ota_judge(text, board, require_sig, keys=None):
    """None when the manifest passes, else the reason it is refused."""
    t = _bytes(text)
    why = ctypes.create_string_buffer(48)
    rc = _with_keys(keys, lambda arr, n: _lib().moy_ota_judge(
        t, len(t), None if board is None else _bytes(board),
        1 if require_sig else 0, arr, n, why, 48))
    if rc is None:
        return "no usable key"
    return None if rc == 0 else why.value.decode("utf-8")


def ota_judge_c6(text, require_sig, keys=None):
    t = _bytes(text)
    why = ctypes.create_string_buffer(48)
    rc = _with_keys(keys, lambda arr, n: _lib().moy_ota_judge_c6(
        t, len(t), 1 if require_sig else 0, arr, n, why, 48))
    if rc is None:
        return "no usable key"
    return None if rc == 0 else why.value.decode("utf-8")


def _ota_err():
    return _lib().moy_ota_error().decode("utf-8")


def ota_check(url, board, require_sig):
    """(0, manifest text) | (1, None) when the channel has nothing | (-1, why)"""
    out = ctypes.c_void_p()
    n = ctypes.c_size_t()
    rc = _lib().moy_ota_check(_bytes(url), _bytes(board), 1 if require_sig else 0,
                              ctypes.byref(out), ctypes.byref(n))
    if rc == 0:
        return (0, _taken(out, n).decode("utf-8"))
    return (rc, _ota_err() if rc < 0 else None)


def ota_dl_begin(url, size, sha256, sink):
    if _lib().moy_ota_dl_begin(_bytes(url), int(size), _bytes(sha256 or ""),
                               int(sink)) != 0:
        raise ValueError(_ota_err())


def ota_dl_step(max_bytes):
    return _lib().moy_ota_dl_step(int(max_bytes))


def ota_dl_finish():
    return _lib().moy_ota_dl_finish() == 0


def ota_cancel():
    _lib().moy_ota_cancel()


def ota_slot_begin(size):
    if _lib().moy_ota_slot_begin(int(size)) != 0:
        raise ValueError(_ota_err())


def ota_slot_write(data):
    raw = bytes(data)
    return _lib().moy_ota_slot_write(raw, len(raw)) == 0


def ota_slot_close():
    return _lib().moy_ota_slot_close() == 0


def ota_activate():
    out = ctypes.create_string_buffer(24)
    if _lib().moy_ota_activate(out, 24) != 0:
        return None
    return out.value.decode("utf-8")


def ota_c6_commit():
    return _lib().moy_ota_c6_commit() == 0


def ota_c6_version():
    v = _lib().moy_c6_version()
    return None if v < 0 else v


def ota_state():
    """(phase, sink, dl_done, dl_total, done, total, error)"""
    st = _OtaState()
    _lib().moy_ota_state(ctypes.byref(st))
    return (st.phase, st.sink, st.dl_done, st.dl_total, st.done, st.total,
            st.err.decode("utf-8"))


def http_open(url, agent):
    """(handle, status, content_length) of a GET with redirects followed;
    OSError(errno) when it cannot be made."""
    status = ctypes.c_int()
    clen = ctypes.c_uint32()
    h = _lib().moy_httpc_open(_bytes(url), _bytes(agent), ctypes.byref(status),
                              ctypes.byref(clen))
    if h < 0:
        raise OSError(-h, os.strerror(-h))
    return (h, status.value, clen.value)


def http_readinto(h, buf):
    n = len(buf)
    tmp = (ctypes.c_char * max(1, n))()
    k = _lib().moy_httpc_read(int(h), tmp, n)
    if k < 0:
        raise OSError(-k, os.strerror(-k))
    buf[:k] = tmp.raw[:k]
    return k


def http_close(h):
    _lib().moy_httpc_close(int(h))


def net_vm_stop():
    """The kernel's VM teardown for the links (moy_net_vm_stop)."""
    _lib().moy_net_vm_stop()


# -- the host's stand-ins for what an image links (tests only) -------------------

def _trust(keys=None):
    """The keys ota_check trusts: `keys` ((modulus_hex, e) pairs), or the
    baked ones for None."""
    arr, n = _keys_arg(keys)
    _lib().moy_ota_trust(arr if n > 0 else None, max(n, 0))


def _slot():
    """(bytes written to the host's slot, the label it booted or "")."""
    d = _lib()
    n = ctypes.c_uint32.in_dll(d, "moy_slot_host_n").value
    p = ctypes.c_void_p.in_dll(d, "moy_slot_host_data").value
    booted = (ctypes.c_char * 16).in_dll(d, "moy_slot_host_booted").value
    return (ctypes.string_at(p, n) if p and n else b""), booted.decode("utf-8")


def _slot_reset(cap=4 << 20):
    d = _lib()
    ctypes.c_uint32.in_dll(d, "moy_slot_host_cap").value = cap
    ctypes.memset(ctypes.addressof((ctypes.c_char * 16).in_dll(
        d, "moy_slot_host_booted")), 0, 16)


def _link(ip=None):
    """The host's stand-in for the kernel's link: its address, or None (down)."""
    ctypes.c_uint32.in_dll(_lib(), "moy_net_host_link_ip").value = _ip_of(ip) if ip else 0


def _pins(pins=()):
    """The host's pin table: the allowlist the /gpio route serves, every pin
    untouched again."""
    d = _lib()
    arr = (ctypes.c_uint8 * 16).in_dll(d, "moy_net_host_pins")
    for i, p in enumerate(pins):
        arr[i] = p
    ctypes.c_int.in_dll(d, "moy_net_host_npins").value = len(pins)
    for name in ("moy_net_host_level", "moy_net_host_mode"):
        ctypes.memset(ctypes.addressof((ctypes.c_int * 64).in_dll(d, name)), 0, 64 * 4)


def _pin_state(pin):
    """(mode, level) of a host pin: 0 untouched, 1 input, 2 output."""
    d = _lib()
    return ((ctypes.c_int * 64).in_dll(d, "moy_net_host_mode")[pin],
            (ctypes.c_int * 64).in_dll(d, "moy_net_host_level")[pin])


def _c6(enabled=True, fail_at=None, version=-1):
    """The host's C6 stand-in: on or off, a write that fails at its Nth call,
    the version it reports. Returns its record: bytes written, ended, activated."""
    d = _lib()
    ctypes.c_int.in_dll(d, "moy_net_host_c6_on").value = 1 if enabled else 0
    ctypes.c_int.in_dll(d, "moy_net_host_c6_fail_at").value = (
        -1 if fail_at is None else fail_at)
    ctypes.c_int.in_dll(d, "moy_net_host_c6_ver").value = version


def _c6_record():
    d = _lib()
    n = ctypes.c_uint32.in_dll(d, "moy_net_host_c6_n").value
    p = ctypes.c_void_p.in_dll(d, "moy_net_host_c6_data").value
    return ((ctypes.string_at(p, n) if p and n else b""),
            ctypes.c_int.in_dll(d, "moy_net_host_c6_ended").value,
            ctypes.c_int.in_dll(d, "moy_net_host_c6_active").value,
            ctypes.c_int.in_dll(d, "moy_net_host_c6_writes").value)

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
