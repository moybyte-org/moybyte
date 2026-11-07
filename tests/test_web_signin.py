"""The webhost's sign-in and its sealed routes (native/moy_net/moy_webauth.c,
moy_seal.h; docs/os_2026-11/system.md section 3), on the host's one library.

Two clients drive the board's C. `seal_client.Browser` is the browser's own C
(moy_seal_web.c, what the worker runs in the wasm). `Ref` below is a second
page written here from moy_seal.h's protocol text alone -- hashlib, hmac, the
`cryptography` package's AES-GCM and a P-256 in plain Python -- so a mistake
both ends share through moy_seal.c (a label, a nonce layout, a transcript)
fails here instead of agreeing with itself.

What it holds: pairing by the QR's token and by the console's password
(SPAKE2, RFC 9382's M and N), a key login after either; a token used once and
gone after its ten minutes; a wrong token, key or password counted by the
backoff, per address and from anywhere; a recorded login, a replayed or
tampered request and a tampered, truncated or reordered answer refused; a
dropped key ending its sessions; an old pin URL refused; the keys kept in
system/webkeys across a restart and a broken file read as none; the open
routes open and a refused request never reaching the store; /run and
/update answered sealed by the VM; and /auth's parser fuzzed.
"""

import ctypes
import hashlib
import hmac
import json
import os
import random
import socket
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(__file__))
from net_ports import free_port  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "device"))

import moy_webhost as wh  # noqa: E402
import seal_client  # noqa: E402
from seal_client import Refused, split  # noqa: E402
from runtime import net_binding as nb  # noqa: E402
from runtime import ssh_service  # noqa: E402

AESGCM = pytest.importorskip("cryptography.hazmat.primitives.ciphers.aead").AESGCM

FREE, FREE_ALL, FIRST_MS, FORGET_MS = 10, 30, 1000, 3600 * 1000
TOKEN_MS = 600000
PASSWORD = "correct horse"


def _store(tmp_path):
    root = tmp_path / "carts"
    (root / "hop.moy").mkdir(parents=True)
    (root / "hop.moy" / "main.py").write_text("x = 1\n")
    (root / "hop.moy" / "manifest.json").write_text('{"title": "Hop"}')
    return root


def _system(tmp_path, password=None):
    d = tmp_path / "system"
    d.mkdir(exist_ok=True)
    if password:
        (d / "password").write_text(ssh_service.hashed(password, "5a1t"))
    return d


def _host(tmp_path, password=None, **kw):
    """A host off a socket with its sign-in on, configured."""
    kw.setdefault("port", 0)
    h = wh.WebHost(str(_store(tmp_path)), auth=str(_system(tmp_path, password)), **kw)
    h.handle_http("GET", "/sync", b"")
    return h


def _page(h):
    return seal_client.Browser(h.handle_http)


def _paired(h):
    p = _page(h)
    p.pair_token(nb.web_token())
    return p


def status(resp):
    return split(resp)[0]


def doc(resp):
    return json.loads(split(resp)[2])


# -- a second page, from the protocol's text ---------------------------------------

P = 2 ** 256 - 2 ** 224 + 2 ** 192 + 2 ** 96 - 1
CB = 0x5ac635d8aa3a93e7b3ebbd55769886bc651d06b0cc53b0f63bce3c3e27d2604b
N = 0xffffffff00000000ffffffffffffffffbce6faada7179e84f3b9cac2fc632551
G = (0x6b17d1f2e12c4247f8bce6e563a440f277037d812deb33a0f4a13945d898c296,
     0x4fe342e2fe1a7f9b8ee7eb4a7c0f9e162bce33576b315ececbb6406837bf51f5)
# RFC 9382 section 6, P-256's M and N, compressed as the RFC prints them.
RFC_M = "02886e2f97ace46e55ba9dd7242579f2993b64e16ef3dcab95afd497333d8fa12f"
RFC_N = "03d8bbd6c639c62937b04d997f38c3770719c629d7014d49a24b4f98baa1292b49"


def ec_add(a, b):
    if a is None:
        return b
    if b is None:
        return a
    if a[0] == b[0] and (a[1] + b[1]) % P == 0:
        return None
    if a == b:
        m = (3 * a[0] * a[0] - 3) * pow(2 * a[1], -1, P)
    else:
        m = (b[1] - a[1]) * pow(b[0] - a[0], -1, P)
    x = (m * m - a[0] - b[0]) % P
    return x, (m * (a[0] - x) - a[1]) % P


def ec_mul(k, pt):
    out = None
    while k:
        if k & 1:
            out = ec_add(out, pt)
        pt = ec_add(pt, pt)
        k >>= 1
    return out


def ec_neg(pt):
    return pt[0], (-pt[1]) % P


def ec_dec(raw):
    if raw[0] == 4:
        return int.from_bytes(raw[1:33], "big"), int.from_bytes(raw[33:], "big")
    x = int.from_bytes(raw[1:], "big")
    y = pow((x ** 3 - 3 * x + CB) % P, (P + 1) // 4, P)
    return x, y if y % 2 == raw[0] % 2 else P - y


def ec_enc(pt):
    return b"\x04" + pt[0].to_bytes(32, "big") + pt[1].to_bytes(32, "big")


def hm(key, *parts):
    return hmac.new(key, b"".join(parts), hashlib.sha256).digest()


def hkdf(salt, ikm, info, n):
    prk = hm(salt or bytes(32), ikm)
    out, t, i = b"", b"", 1
    while len(out) < n:
        t = hm(prk, t, info, bytes([i]))
        out += t
        i += 1
    return out[:n]


def nonce(kind, index, seq):
    return bytes([kind]) + index.to_bytes(3, "big") + seq.to_bytes(8, "big")


def pw_digest(password, salt):
    d = hashlib.sha256((salt + password).encode()).digest()
    for _ in range(4095):
        d = hashlib.sha256(d + password.encode()).digest()
    return d


class Ref:
    """A page from moy_seal.h's text: the sign-in, sealed requests, answers
    opened record by record."""

    def __init__(self, send, name="Ref on Host"):
        self.send = send
        self.name = name
        self.seq = 0

    def _auth(self, body):
        resp = self.send("POST", "/auth", json.dumps(body).encode())
        st, _h, b = split(resp)
        if st != 200:
            raise Refused(st, json.loads(b))
        return json.loads(b)

    def hello(self):
        d = self._auth({"op": "hello"})
        self.id, self.nb = bytes.fromhex(d["id"]), bytes.fromhex(d["nb"])
        self.nc = os.urandom(16)
        self.salt = d.get("salt")
        return d

    def _ids(self):
        return self.id + self.nb + self.nc

    def _signed(self, d, key, kid):
        sid = bytes.fromhex(d["sid"])
        assert hmac.compare_digest(bytes.fromhex(d["proof"]),
                                   hm(key, b"moy1 board\0", self._ids(), kid, sid))
        okm = hkdf(self.nb + self.nc, key, b"moy1 session\0" + kid, 32)
        self.key, self.kid, self.sid = key, kid, sid
        self.kc, self.kb = okm[:16], okm[16:]
        self.seq = 0

    def pair_token(self, token_hex):
        self.hello()
        t = bytes.fromhex(token_hex)
        mac = hm(t, b"moy1 token\0", self._ids(), self.name.encode())
        d = self._auth({"op": "token", "nb": self.nb.hex(), "nc": self.nc.hex(),
                        "mac": mac.hex(), "name": self.name})
        pad = hm(t, b"moy1 wrap\0", self._ids())[:16]
        key = bytes(a ^ b for a, b in zip(bytes.fromhex(d["key"]), pad))
        self._signed(d, key, bytes.fromhex(d["kid"]))

    def sign_in(self):
        key, kid = self.key, self.kid
        self.hello()
        mac = hm(key, b"moy1 key\0", self._ids(), kid)
        d = self._auth({"op": "key", "nb": self.nb.hex(), "nc": self.nc.hex(),
                        "kid": kid.hex(), "mac": mac.hex()})
        self._signed(d, key, kid)

    def pair_password(self, password):
        self.hello()
        w = int.from_bytes(hkdf(None, pw_digest(password, self.salt), b"moy1 spake2 w", 40),
                           "big") % N or 1
        M, Nn = ec_dec(bytes.fromhex(RFC_M)), ec_dec(bytes.fromhex(RFC_N))
        x = random.SystemRandom().randrange(1, N)
        X = ec_enc(ec_add(ec_mul(x, G), ec_mul(w, M)))
        d = self._auth({"op": "pake", "nb": self.nb.hex(), "nc": self.nc.hex(),
                        "X": X.hex(), "name": self.name})
        Y = bytes.fromhex(d["Y"])
        K = ec_enc(ec_mul(x, ec_add(ec_dec(Y), ec_neg(ec_mul(w, Nn)))))

        def lp(b):
            return len(b).to_bytes(8, "little") + b
        tt = (lp(b"moy1 browser" + self.nc) + lp(b"moy1 board" + self.id + self.nb)
              + lp(X) + lp(Y) + lp(K) + lp(w.to_bytes(32, "big")))
        h = hashlib.sha256(tt).digest()
        ke, kca = h[:16], hkdf(None, h[16:], b"ConfirmationKeys", 32)
        assert hmac.compare_digest(bytes.fromhex(d["cB"]), hm(kca[16:], tt)), \
            "the board's confirmation does not check"
        d = self._auth({"op": "confirm", "nb": self.nb.hex(), "cA": hm(kca[:16], tt).hex()})
        pad = hm(ke, b"moy1 wrap\0", self._ids())[:16]
        key = bytes(a ^ b for a, b in zip(bytes.fromhex(d["key"]), pad))
        self._signed(d, key, bytes.fromhex(d["kid"]))

    def seal(self, method, path, body=b""):
        self.seq += 1
        target = path + ("&" if "?" in path else "?") + "s=%s&n=%d" % (self.sid.hex(), self.seq)
        aad = b"moy1 req\0" + method.encode() + b"\0" + target.encode()
        sealed = AESGCM(self.kc).encrypt(nonce(1, 0, self.seq), body, aad)
        if body:
            return target, sealed, self.seq
        return target + "&t=" + sealed.hex(), b"", self.seq

    def open(self, seq, st, raw):
        out, i, at = b"", 0, 0
        while True:
            head = raw[at:at + 4]
            v = int.from_bytes(head, "big")
            n = v & 0x7fffffff
            aad = b"moy1 res\0" + st.to_bytes(2, "big") + head
            out += AESGCM(self.kb).decrypt(nonce(2, i, seq), raw[at + 4:at + 4 + n + 16], aad)
            at += 4 + n + 16
            i += 1
            if v & 0x80000000:
                assert at == len(raw), "bytes after the last record"
                return out

    def call(self, method, path, body=b""):
        target, sealed, seq = self.seal(method, path, body)
        st, head, raw = split(self.send(method, target, sealed))
        assert "application/octet-stream" in head, (st, raw)
        return st, self.open(seq, st, raw)


# -- the reference against the board ---------------------------------------------

def test_the_spake2_points_are_rfc_9382s():
    """The C's uncompressed M and N are the RFC's compressed ones, on the curve."""
    src = (ROOT / "native" / "moy_net" / "moy_seal.c").read_text()
    for name, rfc in (("SPAKE_M", RFC_M), ("SPAKE_N", RFC_N)):
        body = src.split("static const uint8_t %s[MOY_SEAL_POINT] = {" % name)[1].split("}")[0]
        raw = bytes(int(b, 16) for b in body.replace(",", " ").split())
        pt = ec_dec(bytes.fromhex(rfc))
        assert raw == ec_enc(pt), name
        assert (pt[1] ** 2 - pt[0] ** 3 + 3 * pt[0] - CB) % P == 0


def test_a_reference_page_pairs_by_token_and_its_calls_seal_both_ways(tmp_path):
    h = _host(tmp_path)
    ref = Ref(h.handle_http)
    ref.pair_token(nb.web_token())
    st, plain = ref.call("GET", "/carts.json")
    assert st == 200 and json.loads(plain)["hop.moy/main.py"] == "x = 1\n"
    batch = json.dumps({"v": 1, "ops": [{"p": "hop.moy/main.py", "t": "x = 2\n"}]}).encode()
    st, plain = ref.call("POST", "/sync", batch)
    assert st == 200 and json.loads(plain) == {"ok": 1, "err": []}
    assert (tmp_path / "carts" / "hop.moy" / "main.py").read_text() == "x = 2\n"
    ref.sign_in()
    assert ref.call("GET", "/files.json")[0] in (200, 404)


def test_a_reference_page_pairs_by_password_and_the_browser_s_c_signs_in_with_its_key(
        tmp_path):
    h = _host(tmp_path, password=PASSWORD)
    ref = Ref(h.handle_http)
    ref.pair_password(PASSWORD)
    assert ref.call("GET", "/carts.json")[0] == 200
    page = _page(h)
    page.hello()
    page.keys[page.board] = (ref.kid.hex(), ref.key.hex())
    page.sign_in()
    assert page.json("GET", "/carts.json")[0] == 200


def test_the_browser_s_c_and_the_reference_agree_on_a_password_pairing(tmp_path):
    h = _host(tmp_path, password=PASSWORD)
    page = _page(h)
    page.pair_password(PASSWORD)
    kid, key = page.keys[page.board]
    ref = Ref(h.handle_http)
    ref.key, ref.kid = bytes.fromhex(key), bytes.fromhex(kid)
    ref.sign_in()
    assert ref.call("GET", "/carts.json")[0] == 200


def test_an_answer_of_many_records_opens_and_any_change_to_it_does_not(tmp_path):
    h = _host(tmp_path)
    (tmp_path / "carts" / "hop.moy" / "big.txt").write_text("y" * 20000)
    ref = Ref(h.handle_http)
    ref.pair_token(nb.web_token())
    target, _b, seq = ref.seal("GET", "/carts.json")
    st, _head, raw = split(h.handle_http("GET", target, b""))
    assert json.loads(ref.open(seq, st, raw))["hop.moy/big.txt"] == "y" * 20000
    n0 = int.from_bytes(raw[:4], "big")
    first, rest = raw[:4 + n0 + 16], raw[4 + n0 + 16:]
    assert rest[:4] != b"\x80\0\0\0", "one record: the test proves nothing"
    flipped = bytearray(raw)
    flipped[10] ^= 1
    for bad in (bytes(flipped), raw[:-20], rest + first, first + raw[-20:]):
        with pytest.raises(Exception):
            ref.open(seq, st, bad)
    with pytest.raises(Exception):
        ref.open(seq, 201, raw)                          # the status is sealed too


# -- the browser's C against the board ------------------------------------------

def test_the_browser_s_c_refuses_what_the_reference_refuses(tmp_path):
    h = _host(tmp_path)
    (tmp_path / "carts" / "hop.moy" / "big.txt").write_text("y" * 20000)
    page = _paired(h)
    target, _b, seq = page.seal("GET", "/carts.json")
    st, _head, raw = split(h.handle_http("GET", target, b""))
    n0 = int.from_bytes(raw[:4], "big")
    first, rest = raw[:4 + n0 + 16], raw[4 + n0 + 16:]
    flipped = bytearray(raw)
    flipped[10] ^= 1
    for bad in (bytes(flipped), raw[:-20], rest + first, first + raw[-20:]):
        assert page.open(seq, st, bad) is None
    assert page.open(seq, 201, raw) is None
    assert json.loads(page.open(seq, st, raw))["hop.moy/big.txt"] == "y" * 20000


def test_a_token_pairs_once_and_not_after_its_ten_minutes(tmp_path):
    h = _host(tmp_path)
    token = nb.web_token()
    _page(h).pair_token(token)
    with pytest.raises(Refused) as e:
        _page(h).pair_token(token)
    assert (e.value.status, e.value.doc) == (403, {"error": "token"})
    late = nb.web_token()
    nb._skew_clock(TOKEN_MS + 1)
    with pytest.raises(Refused) as e:
        _page(h).pair_token(late)
    assert e.value.status == 403
    fresh = nb.web_token()
    assert fresh != late and len(fresh) == 32
    old = nb.web_token()
    with pytest.raises(Refused):
        _page(h).pair_token(fresh)                       # a new token drops the last
    _page(h).pair_token(old)


def test_a_stored_key_signs_in_and_a_wrong_one_is_refused(tmp_path):
    h = _host(tmp_path)
    page = _paired(h)
    again = _page(h)
    again.keys = dict(page.keys)
    again.sign_in()
    assert again.json("GET", "/carts.json")[0] == 200
    kid, key = page.keys[page.board]
    wrong = _page(h)
    wrong.keys = {page.board: (kid, ("0" if key[0] != "0" else "1") + key[1:])}
    with pytest.raises(Refused) as e:
        wrong.sign_in()
    assert (e.value.status, e.value.doc) == (403, {"error": "key"})
    wrong.keys = {page.board: ("00" * 8, key)}
    with pytest.raises(Refused) as e:
        wrong.sign_in()
    assert e.value.status == 403


def test_a_password_pairs_and_a_wrong_one_fails_at_the_browser(tmp_path):
    h = _host(tmp_path, password=PASSWORD)
    assert _page(h).hello()["pw"] is True
    with pytest.raises(AssertionError):
        _page(h).pair_password("correct horsf")
    page = _page(h)
    page.pair_password(PASSWORD)
    assert page.json("GET", "/carts.json")[0] == 200
    names = [k[3] for k in nb.web_keys()]
    assert names == ["Test on Host"]


def test_with_no_password_a_browser_pairs_only_by_the_qr(tmp_path):
    h = _host(tmp_path)
    hello = _page(h).hello()
    assert hello["pw"] is False
    with pytest.raises(Refused) as e:
        _page(h).pair_password(PASSWORD)
    assert (e.value.status, e.value.doc) == (403, {"error": "nopw"})


def test_a_recorded_sign_in_does_not_sign_in_again(tmp_path):
    """Every login answers a hello's fresh nonce, used once."""
    h = _host(tmp_path, password=PASSWORD)
    page = _paired(h)
    page.sign_in()
    body, _resp = page.last
    assert status(h.handle_http("POST", "/auth", body)) == 403
    other = _page(h)
    other.pair_password(PASSWORD)
    confirm, _resp = other.last
    assert status(h.handle_http("POST", "/auth", confirm)) in (400, 403)
    token = nb.web_token()
    pairing = _page(h)
    pairing.pair_token(token)
    assert status(h.handle_http("POST", "/auth", pairing.last[0])) == 403


def test_a_replayed_request_is_refused_and_one_out_of_order_is_taken_once(tmp_path):
    h = _host(tmp_path)
    page = _paired(h)
    first = page.seal("GET", "/carts.json")
    second = page.seal("GET", "/carts.json")
    assert status(h.handle_http("GET", second[0], b"")) == 200
    assert status(h.handle_http("GET", first[0], b"")) == 200, "inside the window"
    for t in (first[0], second[0]):
        r = h.handle_http("GET", t, b"")
        assert (status(r), doc(r)) == (401, {"error": "auth"})
    old = page.seal("GET", "/carts.json")
    for _ in range(70):
        page.json("GET", "/carts.json")
    assert status(h.handle_http("GET", old[0], b"")) == 401, "older than the window"


def test_a_tampered_request_is_refused_before_its_body_is_read(tmp_path):
    h = _host(tmp_path)
    page = _paired(h)
    root = tmp_path / "carts" / "hop.moy" / "main.py"
    batch = json.dumps({"v": 1, "ops": [{"p": "hop.moy/main.py", "t": "x = 9\n"}]}).encode()
    target, sealed, _seq = page.seal("POST", "/sync", batch)
    flipped = bytearray(sealed)
    flipped[3] ^= 1
    assert status(h.handle_http("POST", target, bytes(flipped))) == 401
    target, sealed, _seq = page.seal("POST", "/sync", batch)
    assert status(h.handle_http("POST", target.replace("/sync", "/sync?x=1&"), sealed)) == 401
    assert status(h.handle_http("PUT", target, sealed)) in (401, 405)
    assert root.read_text() == "x = 1\n"
    get, _b, _s = page.seal("GET", "/carts.json")
    assert status(h.handle_http("GET", get.replace("/carts.json", "/files.json"), b"")) == 401
    cut = get[:-2] + ("00" if get[-2:] != "00" else "11")
    assert status(h.handle_http("GET", cut, b"")) == 401
    assert status(h.handle_http("POST", target, batch)) == 401, "a plain body under a session"


def test_a_request_with_no_session_and_an_old_pin_url_are_refused(tmp_path):
    h = _host(tmp_path)
    before = nb._drains()
    for path in ("/carts.json", "/files.json", "/carts.json?pin=4321", "/gpio", "/term"):
        r = h.handle_http("GET", path, b"")
        assert (status(r), doc(r)) == (401, {"error": "session"}), path
    r = h.handle_http("POST", "/sync", json.dumps(
        {"v": 1, "pin": "4321", "ops": [{"p": "hop.moy/main.py", "t": "x\n"}]}).encode())
    assert status(r) == 401
    assert nb._drains() == before, "a refused request touched the store"
    assert (tmp_path / "carts" / "hop.moy" / "main.py").read_text() == "x = 1\n"


def test_the_bundle_and_the_marker_stay_open(tmp_path):
    nb._bake({"index.html.gz": b"\x1f\x8bpage"})
    try:
        h = _host(tmp_path)
        assert status(h.handle_http("GET", "/", b"")) == 200
        r = h.handle_http("GET", "/sync", b"")
        assert (status(r), doc(r)) == (200, {"sync": 1})
    finally:
        nb._bake({})


def test_an_open_host_seals_nothing(tmp_path):
    h = wh.WebHost(str(_store(tmp_path)), port=0)
    r = h.handle_http("GET", "/carts.json", b"")
    assert status(r) == 200 and "application/json" in split(r)[1]
    assert doc(h.handle_http("POST", "/auth", b'{"op":"hello"}')) == {"v": 1, "open": True}


def test_a_dropped_key_ends_its_sessions_and_signs_in_no_more(tmp_path):
    h = _host(tmp_path)
    keep = _paired(h)
    page = _paired(h)                    # the browser's C holds one session: this one
    kid = page.keys[page.board][0]
    assert {k[0] for k in nb.web_keys()} == {kid, keep.keys[keep.board][0]}
    assert nb.web_key_drop(kid) == 1
    with pytest.raises(Refused) as e:
        page.call("GET", "/carts.json")
    assert e.value.status == 401
    with pytest.raises(Refused) as e:
        page.sign_in()
    assert (e.value.status, e.value.doc) == (403, {"error": "key"})
    keep.sign_in()
    assert keep.json("GET", "/carts.json")[0] == 200
    assert nb.web_key_drop("all") == 1 and nb.web_keys() == []


def test_the_keys_outlive_a_restart_and_a_broken_file_reads_as_none(tmp_path):
    h = _host(tmp_path)
    page = _paired(h)
    text = (tmp_path / "system" / "webkeys").read_text()
    assert text.startswith("moy-webkeys 1 %s\n" % page.board)
    assert page.keys[page.board][1] not in text.splitlines()[0]
    nb.web_auth(None)
    assert nb.web_auth(str(tmp_path / "system")) == page.board
    page.sign_in()
    (tmp_path / "system" / "webkeys").write_text("moy-webkeys 9 zz\njunk\n")
    nb.web_auth(None)
    other = nb.web_auth(str(tmp_path / "system"))
    assert len(other) == 32 and other != page.board
    assert nb.web_keys() == []


def test_a_system_folder_past_the_path_limit_is_refused_never_cut(tmp_path):
    """A folder the sign-in's path cannot hold would read a truncated path: no
    password, keys saved nowhere. The sign-in and the webhost refuse it."""
    deep = tmp_path / ("d" * 120) / "system"
    deep.mkdir(parents=True)
    with pytest.raises(OSError) as e:
        nb.web_auth(str(deep))
    assert e.value.errno == 22
    with pytest.raises(OSError) as e:
        nb.web_start(0, str(tmp_path / "carts"), None, [], str(deep), [])
    assert e.value.errno == 22
    nb.web_auth(None)


# -- the backoff -------------------------------------------------------------------

def _wait_ms(e):
    assert e.status == 429 and e.doc["error"] == "wait", (e.status, e.doc)
    return e.doc["ms"]


def _wrong_token(h):
    with pytest.raises(Refused) as e:
        _page(h).pair_token("00" * 16)
    return e.value


def test_wrong_credentials_past_the_free_ones_wait_a_doubling_window(tmp_path):
    h = _host(tmp_path, password=PASSWORD)
    page = _paired(h)
    for _ in range(FREE):
        assert _wrong_token(h).status == 403
    assert _wrong_token(h).status == 403, "the first one past is still answered"
    with pytest.raises(Refused) as e:
        page.sign_in()
    assert 0 < _wait_ms(e.value) <= FIRST_MS, "a right key is not even looked at"
    nb._skew_clock(FIRST_MS)
    with pytest.raises(AssertionError):
        _page(h).pair_password("wrong")                  # a PAKE counts until it confirms
    assert FIRST_MS < _wait_ms(_wrong_token(h)) <= 2 * FIRST_MS
    nb._skew_clock(2 * FIRST_MS)
    page.sign_in()                                       # and a right one clears the count
    for _ in range(FREE):
        assert _wrong_token(h).status == 403


def test_a_clock_past_two_to_the_31_holds_no_source_that_never_failed(tmp_path):
    """The links' clock is a uint32 of ms, past 2^31 after 24.8 days up: a
    source with no window then reads as none, not as one ending 24 days on."""
    h = _host(tmp_path)
    ms = nb._lib().moy_net_ms
    ms.restype = ctypes.c_uint32
    nb._skew_clock((0x80000000 + 1000 - ms()) & 0xFFFFFFFF)
    assert ms() >= 0x80000000
    _paired(h).sign_in()
    for _ in range(FREE + 1):
        assert _wrong_token(h).status == 403
    assert 0 < _wait_ms(_wrong_token(h)) <= FIRST_MS


def _from(host, src, body):
    """POST /auth `body` from the address `src` over the serving socket."""
    c = socket.socket()
    c.settimeout(5)
    c.bind((src, 0))
    c.connect(("127.0.0.1", host.port))
    c.sendall(b"POST /auth HTTP/1.1\r\nContent-Length: %d\r\n\r\n%s" % (len(body), body))
    got = b""
    for _ in range(400):
        host.poll()
        try:
            c.settimeout(0.005)
            k = c.recv(65536)
        except socket.timeout:
            continue
        if not k:
            break
        got += k
    c.close()
    return got


@pytest.fixture
def serving(tmp_path):
    hosts = []

    def make():
        h = wh.WebHost(str(_store(tmp_path)), port=free_port(), auth=str(_system(tmp_path)))
        h.start(ip="127.0.0.1")
        hosts.append(h)
        return h
    yield make
    for h in hosts:
        h.stop()


def _guess(h, src):
    page = seal_client.Browser(lambda m, t, b: _from(h, src, b))
    try:
        page.pair_token("00" * 16)
    except Refused as e:
        return e
    raise AssertionError("a wrong token paired")


def test_each_address_has_its_own_count(serving):
    h = serving()
    for _ in range(FREE + 1):
        assert _guess(h, "127.0.0.2").status == 403
    _wait_ms(_guess(h, "127.0.0.2"))
    assert _guess(h, "127.0.0.3").status == 403


def test_more_addresses_buy_no_more_guesses_and_a_paired_page_keeps_working(serving):
    h = serving()
    page = seal_client.Browser(lambda m, t, b: _from(h, "127.0.0.9", b))
    page.pair_token(nb.web_token())
    srcs = ["127.0.0.%d" % i for i in range(2, 6)]
    wrong = 0
    while wrong <= FREE_ALL:
        for src in srcs:
            if wrong <= FREE_ALL:
                assert _guess(h, src).status == 403
                wrong += 1
    _wait_ms(_guess(h, "127.0.0.7"))
    page.sign_in()
    nb._skew_clock(FORGET_MS)
    assert _guess(h, "127.0.0.7").status == 403, "an hour with no wrong one clears it"


def test_a_password_s_pake_answers_from_its_worker_over_a_socket(serving, tmp_path):
    h = serving()
    (tmp_path / "system" / "password").write_text(ssh_service.hashed(PASSWORD, "5a1t"))
    page = seal_client.Browser(lambda m, t, b: _from(h, "127.0.0.4", b))
    page.pair_password(PASSWORD)
    assert len(nb.web_keys()) == 1


# -- the routes the VM answers ----------------------------------------------------

class _StubUpdate:
    def status(self):
        return {"running": {"board": "test", "version": 1}, "state": "none"}

    def request(self, action, channel=None):
        return True, "queued"

    def step(self):
        return False


def test_run_and_update_are_sealed_routes_the_vm_answers(tmp_path):
    launched = []
    h = _host(tmp_path, on_run=lambda n: (launched.append(n), "Hop")[1])
    h.update = _StubUpdate()
    r = h.handle_http("POST", "/run", b'{"cart": "hop"}')
    assert status(r) == 401 and not launched, "a request with no session reached the launcher"
    page = _paired(h)
    assert page.json("POST", "/run", {"cart": "hop"}) == (200, {"run": "Hop"})
    assert launched == ["hop"]
    st, d = page.json("GET", "/update")
    assert st == 200 and d["state"] == "none"
    st, d = page.json("POST", "/update", {"action": "check"})
    assert st == 200 and d["ok"] is True


# -- the parser -------------------------------------------------------------------

def test_auth_bodies_that_are_not_a_sign_in_are_refused(tmp_path):
    h = _host(tmp_path, password=PASSWORD)
    rnd = random.Random(7)
    hello = doc(h.handle_http("POST", "/auth", b'{"op":"hello"}'))
    shapes = [b"", b"{", b"[]", b"null", b'{"op":1}', b'{"op":"key"}',
              b'{"op":"key","nb":"zz","nc":"00","kid":"00","mac":"00"}',
              ('{"op":"token","nb":"%s","nc":"%s","mac":"%s","name":"%s"}'
               % (hello["nb"], "11" * 16, "22" * 32, "n" * 400)).encode(),
              ('{"op":"pake","nb":"%s","nc":"%s","X":"04%s","name":"x"}'
               % (hello["nb"], "11" * 16, "00" * 64)).encode(),
              ('{"op":"pake","nb":"%s","nc":"%s","X":"%s","name":"x"}'
               % (hello["nb"], "11" * 16, "04" + "ff" * 64)).encode(),
              b'{"op":"confirm","nb":"00","cA":"00"}']
    for _ in range(300):
        n = rnd.randrange(0, 200)
        shapes.append(bytes(rnd.randrange(256) for _ in range(n)))
        base = bytearray(b'{"op":"token","nb":"%s","nc":"%s","mac":"%s","name":"x"}'
                         % (hello["nb"].encode(), b"11" * 16, b"22" * 32))
        for _ in range(rnd.randrange(1, 4)):
            base[rnd.randrange(len(base))] = rnd.randrange(256)
        shapes.append(bytes(base))
    for body in shapes:
        nb._skew_clock(FORGET_MS)                         # past any wait it earned
        r = h.handle_http("POST", "/auth", body)
        assert status(r) in (400, 403, 413, 429), (body[:80], r[:120])
    assert nb.web_keys() == []


# -- where a person sees and forgets them -------------------------------------------

def test_settings_lists_the_paired_browsers_and_forgets_one(tmp_path, monkeypatch):
    """Settings -> Paired browsers: each browser by its name and the day it
    was last seen, choosing one forgets it (its sessions end), and the SSH
    host key's fingerprint under them."""
    from runtime import ui
    from runtime import host_app
    ws = host_app.build_workstation(str(tmp_path / "ws" / "carts"))
    h = _host(tmp_path)
    ws.webhost = h
    monkeypatch.setattr(h, "_read_auth", lambda: None)
    page = _paired(h)
    other = _page(h)
    other.name = "Firefox on Linux"
    other.pair_token(nb.web_token())
    monkeypatch.setattr(ssh_service, "fingerprint", lambda ws: "SHA256:" + "A" * 43)
    drv = host_app.ConsoleDriver(ws)
    ws.open_settings()
    sl = ws.settings_layer
    assert sl._paired_words == "2"
    sl.open_paired()
    said, links = [], []
    real_label, real_link = ui.label, ui.link
    monkeypatch.setattr(ui, "label", lambda t, o=None: (said.append(t), real_label(t, o))[1])
    monkeypatch.setattr(ui, "link", lambda t, v=None, o=None: (links.append((t, v)),
                                                               real_link(t, v, o))[1])
    drv.frame(1 / 30.0)
    assert ("Test on Host", "Seen " + _day_of(nb.web_keys()[0][2])) in links
    assert any(t == "Firefox on Linux" for t, _v in links)
    assert "".join(t for t in said if t.startswith("SHA256:") or t.isalnum()) \
        .startswith("SHA256:"), said
    kid = page.keys[page.board][0]
    sl._forget(kid)
    assert [k[0] for k in nb.web_keys()] == [other.keys[other.board][0]]
    assert sl._paired_words == "1"
    with pytest.raises(Refused):
        page.sign_in()


def _day_of(t):
    from runtime import wallclock
    y, m, d = wallclock.localtime(t)[:3]
    return "%d-%02d-%02d" % (y, m, d)


def test_passwd_lists_and_forgets_paired_browsers_and_shows_the_fingerprint(
        tmp_path, monkeypatch):
    from runtime import sh_roles
    h = _host(tmp_path)
    page = _paired(h)

    class WS:
        webhost = h

    class Door:
        def __init__(self):
            self.ws, self.text, self.errs = WS(), "", []

        def out(self, t):
            self.text += t

        def err(self, name, t):
            self.errs.append((name, t))

    monkeypatch.setattr(ssh_service, "fingerprint", lambda ws: "SHA256:abc")
    monkeypatch.setattr(ssh_service, "is_set", lambda ws: False)
    d = Door()
    assert sh_roles.r_passwd(d, []) == 0
    assert "host key SHA256:abc\n" in d.text and "1 browser(s) paired" in d.text
    d = Door()
    assert sh_roles.r_passwd(d, ["-l"]) == 0
    kid = page.keys[page.board][0]
    assert d.text.startswith(kid + "  paired ") and d.text.rstrip().endswith("Test on Host")
    d = Door()
    assert sh_roles.r_passwd(d, ["-r", "00" * 8]) == 1 and d.errs
    assert sh_roles.r_passwd(d, ["-r", kid]) == 0
    assert nb.web_keys() == []


def test_every_stamp_is_unix_seconds(tmp_path):
    """A paired browser's times and a synced file's journal entry are Unix
    seconds on every tier, the console's wallclock's own (moy_net.h,
    moy_net_time: a board's system clock counts from 2000 and takes its
    epoch back there, once); nothing else in moy_net reads the clock or
    shifts it."""
    import time
    h = _host(tmp_path)
    _paired(h)
    (_kid, created, seen, _name), = nb.web_keys()
    assert abs(created - time.time()) < 5 and seen == created
    from runtime import moy_journal, wallclock
    assert moy_journal._EPOCH == wallclock._EPOCH
    hdr = (ROOT / "native" / "moy_net" / "moy_net.h").read_text()
    assert "#ifdef ESP_PLATFORM\n#define MOY_NET_CLOCK_EPOCH 946684800" in hdr
    assert "return (int64_t)time(NULL) + MOY_NET_CLOCK_EPOCH;" in hdr
    for name in ("moy_webauth.c", "moy_webhost.c", "modmoy_net.c"):
        src = (ROOT / "native" / "moy_net" / name).read_text()
        for word in ("time(NULL)", "mp_hal_time_ns", "VM_EPOCH", "epoch"):
            assert word not in src, (name, word)
