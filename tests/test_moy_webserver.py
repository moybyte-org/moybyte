"""The device socket/HTTP TRANSPORT CORE (moy_webserver.py).

The streaming web view this module used to serve died in the 2026-08 sunset
(docs/history/moycore_plan_2026-08.md 3.2); what ships now -- and what this file
tests -- is the bare transport the plan's 3.4 sync RPC rides: the HTTP request
parser/response builder, the chunked and file/blob response forms, and the
stripped WebServer (accept/dispatch, the handle_http endpoint seam, the 404
default). The recording/replay stack's tests moved to
tests/test_web_recording.py.

The WEBSOCKET half went in 2026-09 and its tests went with it: the RPC it had
been kept for shipped as plain HTTP, so the upgrade, the framing leaf and the
persistent conn were frozen onto five boards with no caller
(tests/test_streaming_sunset.py pins the absence). A browser that still asks for
an upgrade here now gets an ordinary 404, which is what the last test below
says.

The module is written MicroPython-first but imports + runs on CPython (it has
usocket/utime fallbacks), so everything testable off-device is exercised here;
the MicroPython socket layer + WiFi coexistence stay on-glass concerns.
"""

import os
import socket as _sk
import sys

# Import the device module straight off the firmware modules tree (CPython-ok).
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODULES = os.path.join(ROOT, "device")
if MODULES not in sys.path:
    sys.path.insert(0, MODULES)

import moy_webserver as web  # noqa: E402  (the DEVICE transport core)


# ---------------------------------------------------------------------------
# HTTP request parsing + response building (pure functions).
# ---------------------------------------------------------------------------


def test_parse_request_keeps_the_query_for_the_handler():
    """The transport hands over the request TARGET, query and all.

    It used to strip at "?" here, and that is where the pin gate died before it
    was written: a GET carries its credential as `?pin=NNNN` and nowhere else,
    so discarding it in the parser made gating a read inexpressible. Handlers
    route on the bare path themselves (moy_webhost always did, defensively)."""
    m, p, clen, end = web.parse_request(b"GET /sync?t=1 HTTP/1.1\r\nHost: x\r\n\r\n")
    assert m == "GET" and p == "/sync?t=1" and clen == 0 and end > 0
    m, p, clen, end = web.parse_request(b"GET /sync HTTP/1.1\r\nHost: x\r\n\r\n")
    assert p == "/sync", "a target with no query must not grow one"


def test_query_param_reads_a_pin_off_a_target():
    assert web.query_param("/carts.json?pin=1234", "pin") == "1234"
    assert web.query_param("/carts.json?dev=1&pin=99&x=2", "pin") == "99"
    # Absent, empty, and no query at all are all "" -- one answer, so a caller
    # never has to tell three kinds of nothing apart.
    assert web.query_param("/carts.json?dev=1", "pin") == ""
    assert web.query_param("/carts.json?pin=", "pin") == ""
    assert web.query_param("/carts.json", "pin") == ""
    assert web.query_param("", "pin") == ""
    # A prefix match is not a match: `?pinned=1` must not read as a pin.
    assert web.query_param("/carts.json?pinned=1234", "pin") == ""


def test_the_handler_seam_receives_the_query():
    """The parser keeping the query is only half of it -- `_serve_http` has to
    pass the target through to `handle_http` or the gate never sees it."""
    seen = []

    class Srv(web.WebServer):
        def handle_http(self, method, path, body):
            seen.append(path)
            return web.http_response(200, "{}")

    srv = Srv.__new__(Srv)
    srv.requests = 0
    srv._http_send_close = lambda conn, data: None
    srv._serve_http(None, "GET", "/carts.json?pin=1234", b"")
    assert seen == ["/carts.json?pin=1234"]


def test_parse_request_post_reads_content_length():
    raw = b"POST /push HTTP/1.1\r\nContent-Length: 11\r\n\r\nhello world"
    m, p, clen, end = web.parse_request(raw)
    assert m == "POST" and p == "/push" and clen == 11
    assert raw[end:end + clen] == b"hello world"


def test_parse_request_incomplete_headers():
    m, p, clen, end = web.parse_request(b"GET /sync HTTP/1.1\r\nHost: x")
    assert end == -1 and m is None


def test_http_response_well_formed():
    r = web.http_response(200, '{"ok":true}')
    head, _, body = r.partition(b"\r\n\r\n")
    assert head.startswith(b"HTTP/1.1 200 OK")
    assert b"Content-Type: application/json" in head
    assert b"Content-Length: 11" in head
    assert b"Cache-Control: no-store" in head
    assert body == b'{"ok":true}'


# ---------------------------------------------------------------------------
# ChunkedResponse framing -- the generated-body path /carts.json rides.
# ---------------------------------------------------------------------------


class _Wire:
    """A conn that only records. `sendall` is handed a memoryview over a buffer
    the sender REUSES, so copy at the seam or the recording reads as the last
    chunk repeated."""

    def __init__(self):
        self.out = bytearray()
        self.sends = 0

    def sendall(self, data):
        self.out += bytes(data)
        self.sends += 1


def _dechunk(raw):
    """(head, body, chunk sizes) for a chunked response, framing checked."""
    head, _, rest = raw.partition(b"\r\n\r\n")
    body = bytearray()
    sizes = []
    while True:
        line, _, rest = rest.partition(b"\r\n")
        n = int(line, 16)
        if n == 0:
            break
        body += rest[:n]
        assert rest[n:n + 2] == b"\r\n", "chunk %d had no trailer" % len(sizes)
        sizes.append(n)
        rest = rest[n + 2:]
    return head, bytes(body), sizes


def test_chunked_coalesces_small_pieces_and_splits_big_ones():
    """The packer yields a few bytes at a time and one TCP send per token would
    spend the transfer in syscall overhead -- so pieces COALESCE to CHUNK_MIN.
    A piece bigger than that is SPLIT across fills rather than framed whole,
    which is the half that matters: the buffer is what makes this path hold
    nothing, and framing a piece whole would defeat it.
    """
    srv = web.WebServer()
    small = _Wire()
    srv._send_chunked(small, web.ChunkedResponse(iter(["a", "b", "c"])))
    head, body, sizes = _dechunk(bytes(small.out))
    assert b"Transfer-Encoding: chunked" in head
    assert body == b"abc" and sizes == [3], (body, sizes)

    big = _Wire()
    srv._send_chunked(big, web.ChunkedResponse(
        iter(["z" * (web.CHUNK_MIN * 3 + 17)])))
    _, body, sizes = _dechunk(bytes(big.out))
    assert sizes == [web.CHUNK_MIN] * 3 + [17], sizes
    assert body == b"z" * (web.CHUNK_MIN * 3 + 17)


def test_chunked_sends_never_grow_with_the_piece():
    """The 2026-09-09 Guition defect, pinned at the transport: this used to
    `b"".join(pieces)` and then `size + data + CRLF`, so every chunk minted two
    or three copies of ITSELF -- fine at 8KB, a MemoryError at the 150KB one
    PICO-8 cart arrived as. Nothing here may allocate per chunk, so one send
    can never carry more than the one reusable buffer holds.
    """
    srv = web.WebServer()
    w = _Wire()
    srv._send_chunked(w, web.ChunkedResponse(
        iter(["q" * 40000, "r" * 3, "s" * 40000])))
    _, body, sizes = _dechunk(bytes(w.out))
    assert body == b"q" * 40000 + b"r" * 3 + b"s" * 40000
    assert max(sizes) <= web.CHUNK_MIN, sizes
    # ...and the size line + CRLF ride INSIDE that buffer: one send per chunk.
    assert w.sends == len(sizes) + 2, (w.sends, len(sizes))   # + head + terminator


def test_chunked_survives_a_body_that_yields_bytes_and_str():
    srv = web.WebServer()
    w = _Wire()
    srv._send_chunked(w, web.ChunkedResponse(iter([b"\x01\x02", "ok", b""])))
    _, body, _ = _dechunk(bytes(w.out))
    assert body == b"\x01\x02ok"


# ---------------------------------------------------------------------------
# The stripped WebServer over a real localhost socket: the handle_http seam, the
# 404 default, and the upgrade request that is now just another 404.
# ---------------------------------------------------------------------------


def _start_server(**kw):
    srv = web.WebServer(port=0, **kw)
    assert srv.start(ip="127.0.0.1")
    srv.port = srv.sock.getsockname()[1]
    return srv


def _recv_until(sock, marker, budget=2.0):
    sock.settimeout(budget)
    buf = b""
    while marker not in buf:
        chunk = sock.recv(4096)
        if not chunk:
            break
        buf += chunk
    return buf


def test_server_http_defaults_to_404():
    srv = _start_server()
    try:
        c = _sk.create_connection(("127.0.0.1", srv.port), timeout=2.0)
        c.sendall(b"GET /anything HTTP/1.1\r\nHost: x\r\n\r\n")
        srv.poll()
        resp = _recv_until(c, b"\r\n\r\n")
        assert b"404 Not Found" in resp
        c.close()
    finally:
        srv.stop()


def test_server_handle_http_seam_serves_a_subclass_endpoint():
    class _Rpc(web.WebServer):
        def handle_http(self, method, path, body):
            if method == "GET" and path == "/ping":
                return web.http_response(200, '{"pong":1}')
            return None

    srv = _Rpc(port=0)
    assert srv.start(ip="127.0.0.1")
    srv.port = srv.sock.getsockname()[1]
    try:
        c = _sk.create_connection(("127.0.0.1", srv.port), timeout=2.0)
        c.sendall(b"GET /ping HTTP/1.1\r\nHost: x\r\n\r\n")
        srv.poll()
        resp = _recv_until(c, b"\r\n\r\n" + b'{"pong":1}')
        assert b"200 OK" in resp and resp.endswith(b'{"pong":1}')
        c.close()
    finally:
        srv.stop()


def test_an_upgrade_request_is_served_and_closed_like_any_other_get():
    """The transport stopped sniffing for RFC 6455 in 2026-09. A client that
    still asks must get an ordinary answer and its connection back, not a
    half-open socket the loop then carries."""
    srv = _start_server()
    try:
        c = _sk.create_connection(("127.0.0.1", srv.port), timeout=2.0)
        c.sendall(b"GET /ws HTTP/1.1\r\nHost: x\r\nUpgrade: websocket\r\n"
                  b"Connection: Upgrade\r\n"
                  b"Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n\r\n")
        srv.poll()
        resp = _recv_until(c, b"\r\n\r\n")
        assert b"404 Not Found" in resp
        assert b"101" not in resp
        c.close()
    finally:
        srv.stop()
