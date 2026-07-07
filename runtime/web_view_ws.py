"""Moybyte web view -- the WebSocket transport primitives (RFC 6455): the LIVE
channel's handshake + byte framing, extracted from web_view.py to shrink it.

CANONICAL HOME for both transports -- the DEVICE raw-socket server
(moy_webserver._WSConn) AND the HOST http.server (tools/web_console). web_view.py
imports these back and re-exports them, so every `web_view.ws_*` / `web_view.WS_*`
reference is unchanged. Pure functions, no socket, so the handshake + framing stay
host-unit-testable and byte-identical on both.

PORTABLE SUBSET (device-freezable): NO top-level imports at all -- `ws_accept_key`
imports sha1 + base64 LAZILY inside it (uhashlib/ubinascii on MicroPython,
hashlib/binascii on CPython). Canonical home runtime/web_view_ws.py; build.sh stages a
copy into the firmware modules/ tree so the device freezes it as top-level web_view_ws.
"""


# ---------------------------------------------------------------------------
# WebSocket transport primitives (RFC 6455): the LIVE channel's handshake + byte framing.
# CANONICAL HOME for both transports -- the DEVICE raw-socket server (moy_webserver._WSConn,
# which re-exports these) AND the HOST http.server (tools/web_console). Pure functions, no
# socket, so the handshake + framing stay host-unit-testable and byte-identical on both.
#
# MicroPython-safe: `ws_accept_key` needs sha1 + base64, imported LAZILY inside it
# (uhashlib/ubinascii on MicroPython, hashlib/binascii on CPython) so this module keeps NO
# top-level imports beyond json -- the portable-subset rule that lets the device freeze it.
# The byte framing (ws_encode/ws_decode) is pure and needs no imports at all.
# ---------------------------------------------------------------------------

# The RFC 6455 magic GUID concatenated with the client's Sec-WebSocket-Key before the sha1;
# the base64 of that digest is the Sec-WebSocket-Accept the server echoes back.
WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

# Max bytes a single inbound WS text frame may carry (input batches are tiny). A frame claiming
# more than this is a protocol error and the conn is dropped (enforced in ws_decode).
WS_MAX_FRAME = 8192


def ws_accept_key(key):
    """The Sec-WebSocket-Accept value for a client's Sec-WebSocket-Key (RFC 6455 4.2.2):
    base64(sha1(key + WS_GUID)). `key` is the raw header value (str).

    sha1 + base64 are imported HERE (lazily, not at module top) so web_view stays import-free
    on both runtimes: MicroPython exposes uhashlib/ubinascii, CPython hashlib/binascii; both
    provide sha1 + b2a_base64, all the accept computation uses."""
    try:
        import uhashlib as _hashlib
    except Exception:  # noqa: BLE001 -- host / CPython
        import hashlib as _hashlib
    try:
        import ubinascii as _binascii
    except Exception:  # noqa: BLE001 -- host / CPython
        import binascii as _binascii
    digest = _hashlib.sha1((key + WS_GUID).encode("utf-8")).digest()
    return _binascii.b2a_base64(digest).decode("utf-8").strip()


def ws_handshake_response(key):
    """The full HTTP/1.1 101 Switching Protocols response (bytes) that completes a WebSocket
    upgrade for the client key. No body; the socket then carries WS frames."""
    accept = ws_accept_key(key)
    head = (
        "HTTP/1.1 101 Switching Protocols\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        "Sec-WebSocket-Accept: %s\r\n\r\n"
    ) % accept
    return head.encode("utf-8")


def ws_header_key(raw):
    """Pull the Sec-WebSocket-Key header value out of a raw request (bytes/str), or None.
    Used by the upgrade path; case-insensitive header name match."""
    if isinstance(raw, bytes):
        try:
            text = raw.decode("utf-8")
        except Exception:  # noqa: BLE001
            text = raw.decode("latin-1")
    else:
        text = raw
    for ln in text.replace("\r\n", "\n").split("\n"):
        c = ln.find(":")
        if c > 0 and ln[:c].strip().lower() == "sec-websocket-key":
            return ln[c + 1:].strip()
    return None


def is_ws_upgrade(raw):
    """True when a raw request asks to upgrade to a WebSocket (an `Upgrade: websocket`
    header). Case-insensitive; tolerant of a comma-list Connection header."""
    if isinstance(raw, bytes):
        try:
            text = raw.decode("utf-8")
        except Exception:  # noqa: BLE001
            text = raw.decode("latin-1")
    else:
        text = raw
    for ln in text.replace("\r\n", "\n").split("\n"):
        c = ln.find(":")
        if c > 0 and ln[:c].strip().lower() == "upgrade":
            if "websocket" in ln[c + 1:].strip().lower():
                return True
    return False


def ws_encode(payload, opcode=0x1):
    """Encode an UNMASKED server->client WebSocket frame (RFC 6455 5.2). `payload` is bytes
    (or str -> utf-8). opcode 0x1 = text (the default), 0xA = pong. FIN is always set (we never
    fragment outbound). 7/16/64-bit length forms per the spec; no mask."""
    if isinstance(payload, str):
        payload = payload.encode("utf-8")
    n = len(payload)
    b0 = 0x80 | (opcode & 0x0F)              # FIN + opcode
    if n < 126:
        header = bytes((b0, n))
    elif n < 65536:
        header = bytes((b0, 126, (n >> 8) & 0xFF, n & 0xFF))
    else:
        header = bytes((b0, 127,
                        (n >> 56) & 0xFF, (n >> 48) & 0xFF, (n >> 40) & 0xFF,
                        (n >> 32) & 0xFF, (n >> 24) & 0xFF, (n >> 16) & 0xFF,
                        (n >> 8) & 0xFF, n & 0xFF))
    return header + payload


def ws_decode(buf):
    """Decode ONE client->server WebSocket frame from the front of `buf` (bytes).

    Returns (opcode, payload, consumed):
      * opcode is the frame opcode, payload the UNMASKED bytes, consumed the frame length.
      * (None, None, 0)  -> not enough bytes yet for a complete frame (try again next read).
      * (-1, None, 0)    -> a protocol error (oversize / an unmasked server-bound frame) -> drop.

    Client frames are ALWAYS masked (RFC 6455 5.3), so the MASK bit must be set; the 4-byte key
    XOR-unmasks the payload. 126/127 extended lengths supported; over WS_MAX_FRAME is an error."""
    n = len(buf)
    if n < 2:
        return (None, None, 0)
    b0 = buf[0]
    b1 = buf[1]
    opcode = b0 & 0x0F
    masked = (b1 & 0x80) != 0
    ln = b1 & 0x7F
    off = 2
    if ln == 126:
        if n < off + 2:
            return (None, None, 0)
        ln = (buf[off] << 8) | buf[off + 1]
        off += 2
    elif ln == 127:
        if n < off + 8:
            return (None, None, 0)
        ln = 0
        for i in range(8):
            ln = (ln << 8) | buf[off + i]
        off += 8
    if ln > WS_MAX_FRAME:
        return (-1, None, 0)                 # oversize -> drop the conn
    if not masked:
        return (-1, None, 0)                 # a client frame MUST be masked
    if n < off + 4 + ln:
        return (None, None, 0)               # mask key + payload not all here yet
    mask = buf[off:off + 4]
    off += 4
    raw = buf[off:off + ln]
    # Unmask: payload[i] ^ mask[i % 4]. bytearray for in-place XOR (works on host + MP).
    out = bytearray(raw)
    for i in range(ln):
        out[i] ^= mask[i & 3]
    return (opcode, bytes(out), off + ln)
