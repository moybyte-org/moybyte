"""The browser's end of the sign-in (native/moy_net/moy_seal_web.c), driven
from a test as firmware/web_runner/worker.js drives it in a page: the same C,
over ctypes on the host's one library. `Browser` moves the bytes the worker
moves -- POST /auth, the sealed request, the sealed answer -- through a
transport the test chooses: `send(method, target, body) -> raw response`."""

import ctypes
import json

from runtime import net_binding as nb

_P = ctypes.c_char_p
_Z = ctypes.c_size_t
_I = ctypes.c_int
_D = ctypes.c_double

_SIGS = (
    ("moy_sw_buf", [_Z], ctypes.c_void_p),
    ("moy_sw_hello", [_Z], _I),
    ("moy_sw_board", [], _P),
    ("moy_sw_has_password", [], _I),
    ("moy_sw_key", [_P, _P], _I),
    ("moy_sw_token", [_P, _P], _I),
    ("moy_sw_pake", [_P, _P], _I),
    ("moy_sw_confirm", [_Z], _I),
    ("moy_sw_done", [_Z], _I),
    ("moy_sw_kid", [], _P),
    ("moy_sw_device_key", [], _P),
    ("moy_sw_seal", [_P, _P, _Z], _I),
    ("moy_sw_target", [], _P),
    ("moy_sw_seq", [], _D),
    ("moy_sw_open", [_D, _I, _Z], _I),
    ("moy_sw_ws", [_D], None),
    ("moy_sw_ws_up", [_Z], _I),
    ("moy_sw_ws_down", [_Z], _I),
    ("moy_sw_reset", [], None),
)

_LIB = [None]


def lib():
    if _LIB[0] is None:
        d = nb._lib()
        for name, args, res in _SIGS:
            f = getattr(d, name)
            f.argtypes = args
            f.restype = res
        _LIB[0] = d
    return _LIB[0]


def put(data):
    """`data` into the exchange buffer."""
    p = lib().moy_sw_buf(len(data))
    ctypes.memmove(p, data, len(data))
    return len(data)


def got(n):
    p = lib().moy_sw_buf(0)
    return ctypes.string_at(p, n)


def split(resp):
    """(status, headers text, body bytes) of a raw HTTP/1.1 response,
    a chunked body joined."""
    head, _, body = resp.partition(b"\r\n\r\n")
    status = int(head.split(b" ", 2)[1])
    text = head.decode("latin-1")
    if "Transfer-Encoding: chunked" in text:
        out, wire = b"", body
        while True:
            size, _, wire = wire.partition(b"\r\n")
            n = int(size, 16)
            if n == 0:
                break
            out += wire[:n]
            wire = wire[n + 2:]
        body = out
    return status, text, body


class Refused(Exception):
    def __init__(self, status, doc):
        super().__init__("%s %s" % (status, doc))
        self.status = status
        self.doc = doc


class Browser:
    """One page: its stored key (kid, key) per board id. Its session is the
    browser's C's, which holds one, as a page does: the last Browser to sign
    in is the one whose calls seal."""

    def __init__(self, send, name="Test on Host"):
        self.send = send
        self.name = name
        self.keys = {}
        self.board = None
        self.hello_doc = None
        self.last = None                 # the last raw /auth exchange: (request, answer)

    def _auth(self, body):
        resp = self.send("POST", "/auth", body)
        self.last = (body, resp)
        status, _h, b = split(resp)
        try:
            doc = json.loads(b)
        except ValueError:
            doc = None
        if status != 200:
            raise Refused(status, doc)
        return b

    def hello(self):
        b = self._auth(b'{"op":"hello"}')
        self.hello_doc = json.loads(b)
        assert lib().moy_sw_hello(put(b)) == 0
        self.board = lib().moy_sw_board().decode()
        return self.hello_doc

    def _finish(self, body):
        b = self._auth(body)
        if lib().moy_sw_done(put(b)) != 0:
            raise AssertionError("the board's proof did not check")
        self.keys[self.board] = (lib().moy_sw_kid().decode(),
                                 lib().moy_sw_device_key().decode())

    def _request(self, n):
        assert n > 0, "the client refused to build the request"
        return got(n)

    def sign_in(self):
        """A key login with the key this page holds for the board."""
        self.hello()
        kid, key = self.keys[self.board]
        self._finish(self._request(lib().moy_sw_key(kid.encode(), key.encode())))

    def pair_token(self, token_hex):
        self.hello()
        self._finish(self._request(lib().moy_sw_token(token_hex.encode(),
                                                      self.name.encode())))

    def pair_password(self, password):
        self.hello()
        b = self._auth(self._request(lib().moy_sw_pake(password.encode(),
                                                       self.name.encode())))
        n = lib().moy_sw_confirm(put(b))
        if n < 0:
            raise AssertionError("the board's confirmation did not check")
        self._finish(got(n))

    def seal(self, method, path, body=b""):
        """(target, sealed body, seq) of one request."""
        n = lib().moy_sw_seal(method.encode(), path.encode(), put(body) if body else 0)
        assert n >= 0
        return (lib().moy_sw_target().decode(), got(n) if n else b"",
                lib().moy_sw_seq())

    def open(self, seq, status, body):
        n = lib().moy_sw_open(seq, status, put(body) if body else 0)
        return None if n < 0 else got(n)

    def call(self, method, path, body=b""):
        """(status, plaintext) of a sealed request; Refused for an answer in
        the clear (a 401, a 429)."""
        target, sealed, seq = self.seal(method, path, body)
        resp = self.send(method, target, sealed)
        status, head, raw = split(resp)
        if "application/octet-stream" not in head:
            raise Refused(status, raw)
        plain = self.open(seq, status, raw)
        assert plain is not None, "a sealed answer that does not open"
        return status, plain

    def json(self, method, path, body=None):
        status, plain = self.call(method, path,
                                  b"" if body is None else json.dumps(body).encode())
        return status, json.loads(plain)
