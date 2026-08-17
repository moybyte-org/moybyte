"""The device socket/HTTP/WebSocket TRANSPORT CORE (moy_webserver.py).

The streaming web view this module used to serve died in the 2026-08 sunset
(docs/moycore_plan_2026-08.md 3.2); what ships now -- and what this file tests
-- is the bare transport the plan's 3.4 sync RPC rides: the HTTP request
parser/response builder, the RFC 6455 handshake + framing (runtime/
web_view_ws.py, re-exported), the cross-iteration _WSConn buffering, and the
stripped WebServer (accept/dispatch, WS upgrade latest-wins, on_text routing,
the handle_http endpoint seam, the idle reaper). The recording/replay stack's
tests moved to tests/test_web_recording.py.

The module is written MicroPython-first but imports + runs on CPython (it has
usocket/utime fallbacks), so everything testable off-device is exercised here;
the MicroPython socket layer + WiFi coexistence stay on-glass concerns.
"""

import json
import os
import socket as _sk
import sys
import time

# Import the device module straight off the firmware modules tree (CPython-ok).
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODULES = os.path.join(ROOT, "device")
if MODULES not in sys.path:
    sys.path.insert(0, MODULES)

import moy_webserver as web  # noqa: E402  (the DEVICE transport core)

from runtime import web_view_ws as wsp  # noqa: E402  (the shared WS primitives)


# ---------------------------------------------------------------------------
# HTTP request parsing + response building (pure functions).
# ---------------------------------------------------------------------------


def test_parse_request_get_strips_query():
    m, p, clen, end = web.parse_request(b"GET /sync?t=1 HTTP/1.1\r\nHost: x\r\n\r\n")
    assert m == "GET" and p == "/sync" and clen == 0 and end > 0


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
# WebSocket handshake + framing (RFC 6455) -- the shared web_view_ws primitives
# the transport re-exports.
# ---------------------------------------------------------------------------


def _mask_client_frame(payload, opcode=0x1, mask=b"\x37\xfa\x21\x3d"):
    """A MASKED client->server frame (the shape a browser/controller sends),
    per RFC 6455 5.3, using the 7/16/64-bit length form the size demands."""
    if isinstance(payload, str):
        payload = payload.encode("utf-8")
    n = len(payload)
    b0 = 0x80 | (opcode & 0x0F)
    if n < 126:
        hdr = bytes((b0, 0x80 | n))
    elif n < 65536:
        hdr = bytes((b0, 0x80 | 126, (n >> 8) & 0xFF, n & 0xFF))
    else:
        hdr = bytes((b0, 0x80 | 127)) \
            + bytes((n >> (8 * (7 - i))) & 0xFF for i in range(8))
    body = bytearray(payload)
    for i in range(n):
        body[i] ^= mask[i & 3]
    return hdr + mask + bytes(body)


def test_ws_accept_key_rfc6455_example():
    assert wsp.ws_accept_key("dGhlIHNhbXBsZSBub25jZQ==") \
        == "s3pPLMBiTxaQ9kYGzzhZRbK+xOo="
    resp = web.ws_handshake_response("dGhlIHNhbXBsZSBub25jZQ==")
    assert resp.startswith(b"HTTP/1.1 101 Switching Protocols\r\n")
    assert b"Upgrade: websocket\r\n" in resp
    assert b"Connection: Upgrade\r\n" in resp
    assert b"Sec-WebSocket-Accept: s3pPLMBiTxaQ9kYGzzhZRbK+xOo=\r\n" in resp


def test_ws_upgrade_header_detection():
    raw = (b"GET /ws HTTP/1.1\r\nHost: x\r\nUpgrade: websocket\r\n"
           b"Connection: Upgrade\r\nSec-WebSocket-Key: abc123==\r\n\r\n")
    assert web.is_ws_upgrade(raw) is True
    assert web.ws_header_key(raw) == "abc123=="
    plain = b"GET / HTTP/1.1\r\nHost: x\r\n\r\n"
    assert web.is_ws_upgrade(plain) is False
    assert web.ws_header_key(plain) is None


def test_ws_encode_is_unmasked_fin_text():
    f = web.ws_encode("hi")
    assert f == b"\x81\x02hi"
    pong = web.ws_encode(b"\x01\x02", opcode=web.WS_OP_PONG)
    assert pong[0] == 0x8A and pong[1] == 0x02 and pong[2:] == b"\x01\x02"


def test_ws_encode_length_forms_7_16_64():
    small = web.ws_encode(b"x" * 10)
    assert small[1] == 10
    mid = web.ws_encode(b"x" * 200)
    assert mid[1] == 126 and mid[2] == 0 and mid[3] == 200
    big = web.ws_encode(b"x" * 70000)
    assert big[1] == 127
    n = 0
    for i in range(8):
        n = (n << 8) | big[2 + i]
    assert n == 70000


def test_ws_decode_roundtrip_masked_text():
    wire = _mask_client_frame('{"events":[{"type":"hold","name":"left","down":true}]}')
    op, payload, consumed = web.ws_decode(wire)
    assert op == web.WS_OP_TEXT
    assert json.loads(payload.decode("utf-8"))["events"][0]["name"] == "left"
    assert consumed == len(wire)


def test_ws_decode_16bit_and_64bit_length_paths():
    mid_payload = "e" * 300
    op, payload, consumed = web.ws_decode(_mask_client_frame(mid_payload))
    assert op == web.WS_OP_TEXT and payload.decode("utf-8") == mid_payload \
        and consumed > 300
    p = b"hello"
    mask = b"\x01\x02\x03\x04"
    hdr = bytes((0x81, 0x80 | 127)) \
        + bytes((len(p) >> (8 * (7 - i))) & 0xFF for i in range(8))
    body = bytearray(p)
    for i in range(len(p)):
        body[i] ^= mask[i & 3]
    wire = hdr + mask + bytes(body)
    op, payload, consumed = web.ws_decode(wire)
    assert op == web.WS_OP_TEXT and payload == b"hello" and consumed == len(wire)


def test_ws_decode_incomplete_frame_yields_none():
    wire = _mask_client_frame("x" * 300)
    for cut in (1, 2, 3, 5, 8, len(wire) - 1):
        assert web.ws_decode(wire[:cut]) == (None, None, 0), cut
    op, _payload, consumed = web.ws_decode(wire)
    assert op == web.WS_OP_TEXT and consumed == len(wire)


def test_ws_decode_unmasked_client_frame_is_protocol_error():
    server_frame = web.ws_encode("not a client frame")
    op, payload, consumed = web.ws_decode(server_frame)
    assert op == -1 and payload is None and consumed == 0


def test_ws_decode_oversize_frame_is_protocol_error():
    n = wsp.WS_MAX_FRAME + 1
    hdr = bytes((0x81, 0x80 | 127)) \
        + bytes((n >> (8 * (7 - i))) & 0xFF for i in range(8))
    wire = hdr + b"\x00\x00\x00\x00"
    op, payload, consumed = web.ws_decode(wire)
    assert op == -1 and consumed == 0


# ---------------------------------------------------------------------------
# _WSConn: the cross-iteration buffering invariant + inline keepalive.
# ---------------------------------------------------------------------------


def test_ws_conn_buffers_partial_frames_across_reads():
    a, b = _sk.socketpair()
    try:
        conn = web._WSConn(a)
        wire = _mask_client_frame('{"events":[{"type":"press","name":"run"}]}')
        for byte in wire[:-1]:
            b.send(bytes((byte,)))
            assert conn.drain_input() == [], "an incomplete frame must not decode early"
        b.send(bytes((wire[-1],)))
        got = conn.drain_input()
        assert len(got) == 1
        assert json.loads(got[0].decode("utf-8"))["events"][0]["name"] == "run"
        assert conn.alive is True
    finally:
        a.close()
        b.close()


def test_ws_conn_ping_is_answered_with_pong():
    a, b = _sk.socketpair()
    try:
        conn = web._WSConn(a)
        b.send(_mask_client_frame(b"pingdata", opcode=web.WS_OP_PING))
        assert conn.drain_input() == [], "a ping is not input"
        b.setblocking(False)
        time.sleep(0.05)
        reply = b.recv(64)
        op = reply[0] & 0x0F if (reply[1] & 0x80) == 0 else None
        assert op == web.WS_OP_PONG and reply[2:] == b"pingdata"
    finally:
        a.close()
        b.close()


# ---------------------------------------------------------------------------
# The stripped WebServer over a real localhost socket: upgrade, on_text routing,
# send_text push, the handle_http seam, the 404 default, the idle reaper.
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


def _upgrade(srv):
    c = _sk.create_connection(("127.0.0.1", srv.port), timeout=2.0)
    c.sendall(b"GET /ws HTTP/1.1\r\nHost: x\r\nUpgrade: websocket\r\n"
              b"Connection: Upgrade\r\nSec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n\r\n")
    srv.poll()
    resp = _recv_until(c, b"\r\n\r\n")
    assert b"101 Switching Protocols" in resp
    return c


def test_server_ws_upgrade_on_text_and_push():
    got = []
    srv = _start_server(on_text=lambda p: got.append(p))
    try:
        c = _upgrade(srv)
        assert srv.connected()
        c.sendall(_mask_client_frame('{"op":"hello"}'))
        for _ in range(20):
            srv.poll()
            if got:
                break
            time.sleep(0.01)
        assert got and json.loads(got[0].decode("utf-8"))["op"] == "hello"
        # Push down: send_text frames the payload for the connected client.
        assert srv.send_text('{"ok":1}')
        frame = _recv_until(c, b"}")
        # Server frames are unmasked; decode by hand (ws_decode requires masks).
        assert frame[0] == 0x81 and frame[2:2 + frame[1]] == b'{"ok":1}'
        c.close()
    finally:
        srv.stop()


def test_server_latest_wins_replaces_the_ws_client():
    srv = _start_server()
    try:
        c1 = _upgrade(srv)
        first = srv._ws
        c2 = _upgrade(srv)
        assert srv._ws is not first, "a new upgrade replaces the old conn"
        assert srv.connected()
        c1.close()
        c2.close()
    finally:
        srv.stop()


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


def test_server_idle_reaper_drops_a_silent_client():
    srv = _start_server()
    try:
        c = _upgrade(srv)
        assert srv.connected()
        srv._ws.last_recv -= (web.WS_IDLE_MS + 1)   # simulate the silence
        srv.poll()
        assert srv._ws is None and not srv.connected()
        c.close()
    finally:
        srv.stop()
