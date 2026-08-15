# Moybyte device socket/HTTP/WebSocket TRANSPORT CORE.
#
# This file used to be the device web view (#41/#22) -- the streaming browser
# mirror that pushed the console's draw commands over a WebSocket. That whole
# feature DIED in the 2026-08 streaming sunset (docs/moycore_plan_2026-08.md
# 3.2, owner decision): the frame push, the DrawRecorder wiring, the TeeCanvas
# lane, stream mode and the Settings WEB VIEW row are deleted; the browser's
# job moved to the wasm head (firmware/web_runner), synced per plan 3.4.
#
# What survives -- deliberately, per the same decision -- is the TRANSPORT:
# a non-blocking listening socket, an HTTP request parser/response builder,
# and a persistent single-client WebSocket connection serviced BETWEEN frames.
# The plan's 3.4 sync RPC (commit-shaped cart sync + the controller input
# role) is specified to ride exactly this core; it has no consumer today and
# is deliberately not wired into run_desktop. The hardware constraints this
# code embodies were learned on glass and should not be re-derived:
#
#   * SINGLE-THREADED, NON-BLOCKING: run_desktop's native loop does one render
#     frame at a time and never services anything mid-frame, so the listener is
#     non-blocking and poll() runs once per loop iteration, between frames. A
#     WS frame may arrive split across reads -- the conn keeps a cross-iteration
#     read buffer and yields only COMPLETE frames; a stalled client is DROPPED,
#     never waited on.
#   * Sends block with a short budget (a non-blocking sendall can't push a
#     multi-KB body over the device's WiFi); reads on the persistent conn are
#     non-blocking.
#
# The RFC 6455 handshake + byte framing live in the shared `web_view_ws`
# (canonical source runtime/web_view_ws.py, frozen as a top-level module).

try:
    import usocket as socket
except Exception:  # noqa: BLE001 -- host / CPython
    import socket

try:
    from utime import ticks_ms, ticks_diff
except Exception:  # noqa: BLE001 -- host / CPython: provide ms-based shims
    import time as _time

    def ticks_ms():
        return int(_time.monotonic() * 1000)

    def ticks_diff(a, b):
        return a - b

try:
    import web_view_ws as _ws                  # frozen top-level (device)
except ImportError:                            # host / CPython: canonical source
    from runtime import web_view_ws as _ws

ws_handshake_response = _ws.ws_handshake_response
ws_header_key = _ws.ws_header_key
is_ws_upgrade = _ws.is_ws_upgrade
ws_encode = _ws.ws_encode
ws_decode = _ws.ws_decode


DEFAULT_PORT = 8080

# Consider the WebSocket client DEAD (and drop it) if we haven't seen any read
# activity for this long. A live client answers pings / sends input; this reaps
# a half-open conn (closed tab, dropped WiFi) that never sent a TCP close.
WS_IDLE_MS = 4000

# Per-connection socket timeouts (seconds). A freshly accepted conn is read
# BLOCKING with a short bound (the request is already en route); sends use a
# longer blocking budget (see the header). The persistent WS conn is then
# non-blocking for reads but keeps the blocking send budget.
WEB_RECV_TIMEOUT = 0.4
WEB_SEND_TIMEOUT = 2.0

# Max NEW connections poll() accepts per loop iteration. accept() EAGAINs the
# instant nothing is pending, so this only caps a flood.
POLL_MAX = 4

# Max bytes a WS conn's read buffer may grow to before giving up (a peer that
# dribbles header bytes without ever completing a frame). Dropping is safe.
WS_MAX_BUFFER = 16384


# ---------------------------------------------------------------------------
# HTTP request parsing (host-testable, no socket).
# ---------------------------------------------------------------------------


def parse_request(raw):
    """Parse a raw HTTP request (bytes or str) into (method, path, content_length,
    header_end). header_end is the index just past the blank line ending the headers (-1 if
    the headers aren't complete yet). path has its query string stripped. A malformed request
    returns (None, None, 0, -1)."""
    if isinstance(raw, bytes):
        try:
            text = raw.decode("utf-8")
        except Exception:  # noqa: BLE001
            text = raw.decode("latin-1")
    else:
        text = raw
    sep = text.find("\r\n\r\n")
    nlen = 4
    if sep < 0:
        sep = text.find("\n\n")
        nlen = 2
    if sep < 0:
        return (None, None, 0, -1)               # headers incomplete
    head = text[:sep]
    lines = head.replace("\r\n", "\n").split("\n")
    if not lines:
        return (None, None, 0, -1)
    parts = lines[0].split(" ")
    if len(parts) < 2:
        return (None, None, 0, -1)
    method = parts[0]
    path = parts[1].split("?", 1)[0]
    clen = 0
    for ln in lines[1:]:
        c = ln.find(":")
        if c > 0 and ln[:c].strip().lower() == "content-length":
            try:
                clen = int(ln[c + 1:].strip())
            except Exception:  # noqa: BLE001
                clen = 0
    return (method, path, clen, sep + nlen)


def http_response(status, body, content_type="application/json"):
    """Build a complete HTTP/1.1 response (bytes). `body` may be str or bytes. The server
    closes the connection after each response (Connection: close), which keeps the
    single-request-per-poll model simple and robust to half-open clients."""
    if isinstance(body, str):
        body = body.encode("utf-8")
    reason = {200: "OK", 400: "Bad Request", 404: "Not Found",
              500: "Server Error"}.get(status, "OK")
    head = (
        "HTTP/1.1 %d %s\r\n"
        "Content-Type: %s\r\n"
        "Content-Length: %d\r\n"
        "Cache-Control: no-store\r\n"
        "Access-Control-Allow-Origin: *\r\n"
        "Connection: close\r\n\r\n"
    ) % (status, reason, content_type, len(body))
    return head.encode("utf-8") + body


class FileResponse:
    """A response whose BODY is a file on the device, streamed rather than read.

    `handle_http` may return one of these instead of bytes. The transport sends
    the head, then pumps the file in CHUNK-sized pieces, so serving the wasm
    head (a 1.0MB `micropython.wasm`) costs a 1KB buffer instead of a 1MB one.
    That is not a nicety on the S3: #66 measures ~23KB of internal SRAM free in
    play, and a whole-file `bytes` would have to come out of PSRAM and be built
    before the first byte reached the wire.

    NOT CACHED, and the reasoning that said otherwise was wrong (fixed
    2026-08-15). This shipped with `max-age=86400` on the argument that these
    are build artifacts which "change only when the console is reflashed" --
    but they change whenever someone pushes a new web build, which is the
    routine action and the entire point of tools/p4_push_web.py needing NO
    reflash. The result was a board serving a correct new console to a browser
    that kept showing yesterday's, for a day, with nothing to indicate why.

    The cost of being right is small and measured: the 1MB wasm streams at
    ~700KB/s off the P4, so a full reload is ~1.5s. `max_age` stays a parameter
    for a caller that genuinely has immutable assets; the default is honest.
    """

    CHUNK = 1024

    def __init__(self, path, size, content_type, max_age=0, encoding=None):
        self.path = path
        self.size = size
        self.content_type = content_type
        self.max_age = max_age
        # `encoding` names a Content-Encoding the file is ALREADY stored in --
        # the board never compresses anything. A pre-gzipped bundle halves the
        # bytes on the wire (1,155,953 -> 572,747 for the four console assets)
        # and the BROWSER inflates it, which it does for most of the web
        # already. Content-Length stays the compressed length, which is what
        # HTTP wants: it describes the body actually sent.
        self.encoding = encoding

    def head(self):
        return ((
            "HTTP/1.1 200 OK\r\n"
            "Content-Type: %s\r\n"
            "Content-Length: %d\r\n"
            "%s"
            "Cache-Control: %s\r\n"
            "Access-Control-Allow-Origin: *\r\n"
            "Connection: close\r\n\r\n"
        ) % (self.content_type, self.size,
             ("Content-Encoding: %s\r\n" % self.encoding) if self.encoding
             else "",
             ("max-age=%d" % self.max_age) if self.max_age
             else "no-store")).encode("utf-8")


class ChunkedResponse:
    """A response whose body is GENERATED, streamed as it is produced.

    `handle_http` may return one of these; `body()` is a generator of str/bytes
    pieces, sent with `Transfer-Encoding: chunked` so no length is needed up
    front. That is the whole point: the alternative is building the answer
    first to measure it.

    Measured on P4 glass 2026-08-14, which is why this exists rather than a
    `json.dumps` -- packing that board's 46-cart store took 21.8s and dumping
    it 39.3s, for a 982KB string. 61 seconds, one allocation, and the frame
    loop blocked for all of it; the request timed out before a byte moved. The
    S3 would not have survived the string at all.
    """

    def __init__(self, body_iter, content_type="application/json"):
        self.body_iter = body_iter
        self.content_type = content_type

    def head(self):
        return ((
            "HTTP/1.1 200 OK\r\n"
            "Content-Type: %s\r\n"
            "Transfer-Encoding: chunked\r\n"
            "Cache-Control: no-store\r\n"
            "Access-Control-Allow-Origin: *\r\n"
            "Connection: close\r\n\r\n"
        ) % self.content_type).encode("utf-8")


# How much generated body to gather before one TCP send (ChunkedResponse).
# 8KB, not 1KB: measured on P4 glass 2026-08-14, serving the 981KB store took
# 41.3s at 23.7KB/s with 1KB chunks -- ~960 chunks, and each was THREE sendall
# calls (size line, body, CRLF). The OTA path moves 137KB/s on the same radio,
# so the wire was never the limit; the syscalls were.
CHUNK_MIN = 8192

# WebSocket opcodes we care about.
WS_OP_TEXT = 0x1
WS_OP_CLOSE = 0x8
WS_OP_PING = 0x9
WS_OP_PONG = 0xA


class _WSConn:
    """The persistent WebSocket connection (one client at a time). Non-blocking reads with a
    cross-iteration read buffer + a parser that yields only COMPLETE inbound frames and retains
    the partial remainder; blocking-with-a-budget sends so a multi-KB frame can drain over slow
    WiFi. Every op is guarded -- a closed/stalled peer sets .alive False and the server drops it
    (the client reconnects)."""

    def __init__(self, conn):
        self._c = conn
        self._buf = b""             # inbound bytes not yet forming a complete frame
        self.alive = True
        self.last_recv = ticks_ms()  # for the idle reaper (WS_IDLE_MS)
        try:
            conn.setblocking(False)  # reads must never block the render loop
        except Exception:  # noqa: BLE001 -- not all ports expose setblocking
            pass

    def close(self):
        self.alive = False
        try:
            self._c.close()
        except Exception:  # noqa: BLE001
            pass

    def _read_some(self):
        """Drain whatever bytes are pending on the non-blocking socket into the buffer. Returns
        False (and marks dead) on a clean peer close; True otherwise. EAGAIN (no data) is the
        normal case and just returns True with nothing appended."""
        got_close = False
        for _ in range(8):           # bounded: don't spin draining a fast firehose forever
            try:
                chunk = self._c.recv(1024)
            except Exception:  # noqa: BLE001 -- EAGAIN / would-block: nothing more pending
                break
            if chunk == b"" or chunk is None:
                got_close = True     # peer closed the TCP connection
                break
            self._buf += chunk
            self.last_recv = ticks_ms()
            if len(self._buf) > WS_MAX_BUFFER:
                self.alive = False   # a peer dribbling bytes without completing a frame
                return False
            if len(chunk) < 1024:    # short read -> the socket is drained for now
                break
        if got_close:
            self.alive = False
            return False
        return True

    def drain_input(self):
        """Read pending bytes and return a list of decoded inbound TEXT payloads (bytes).
        Handles ping (reply pong) + close (drop) inline. Non-blocking; returns [] when no
        complete frame is ready. The render loop calls this once per iteration."""
        if not self.alive:
            return []
        if not self._read_some():
            return []
        texts = []
        while self.alive:
            opcode, payload, consumed = ws_decode(self._buf)
            if opcode is None:
                break                # incomplete frame: keep the buffer, try next iteration
            if consumed <= 0:        # ws_decode protocol error (-1) -> drop the conn
                self.alive = False
                break
            self._buf = self._buf[consumed:]
            if opcode == WS_OP_TEXT:
                texts.append(payload)
            elif opcode == WS_OP_PING:
                self.send(payload, opcode=WS_OP_PONG)
            elif opcode == WS_OP_CLOSE:
                self.alive = False
                break
            # WS_OP_PONG / continuation / other control frames: ignored.
        return texts

    def send(self, payload, opcode=WS_OP_TEXT):
        """Send one UNMASKED frame, blocking with a short budget so a multi-KB frame can drain
        over slow WiFi. A send error (a stalled/closed client) drops the conn rather than
        waiting on it. Returns True if it went out."""
        if not self.alive:
            return False
        frame = ws_encode(payload, opcode)
        try:
            self._c.settimeout(WEB_SEND_TIMEOUT)
        except Exception:  # noqa: BLE001 -- not all ports expose settimeout
            pass
        try:
            self._c.sendall(frame)
            ok = True
        except Exception:  # noqa: BLE001 -- ETIMEDOUT / broken pipe: the client stalled
            ok = False
            self.alive = False
        finally:
            try:
                self._c.setblocking(False)   # back to non-blocking for the next read
            except Exception:  # noqa: BLE001
                pass
        return ok


class WebServer:
    """The bare cooperative transport: a non-blocking listener, one-shot HTTP
    requests, and ONE persistent WebSocket client. No routes and no frame push
    -- the 3.4 sync RPC supplies both when it lands.

    Seams for that consumer:
      * `on_text(payload)`  -- constructor arg, called for each inbound WS TEXT
                               frame (bytes). None = inbound frames are dropped.
      * `handle_http(method, path, body)` -- override in a subclass to serve
                               endpoints; return a complete http_response()
                               bytes blob, or None for 404. The base serves 404
                               for everything.
      * `send_text(payload)` -- push one WS text frame to the connected client
                               (False when none is connected).

    poll() runs once per loop iteration, BETWEEN frames: accept up to POLL_MAX
    new conns (HTTP one-shot, or a WS upgrade promoted to the persistent conn,
    latest-wins), then drain the WS's queued input. Non-blocking throughout."""

    def __init__(self, port=DEFAULT_PORT, on_text=None):
        self.port = port
        self.on_text = on_text
        self.sock = None
        self.ip = None
        self.requests = 0             # served-request counter (diag)
        self._ws = None               # the persistent client (one at a time)

    def start(self, ip=None):
        """Open the non-blocking listening socket. `ip` is the device's STA IP (for the printed
        URL). Returns True on success. Guarded -- a bind failure leaves the server inert."""
        self.ip = ip
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            except Exception:  # noqa: BLE001 -- not all ports expose SO_REUSEADDR
                pass
            s.bind(("0.0.0.0", self.port))
            s.listen(1)
            s.setblocking(False)
            self.sock = s
            return True
        except Exception as exc:  # noqa: BLE001
            print("Moybyte web: server start failed:", exc)
            self.sock = None
            return False

    def stop(self):
        self._drop_ws()
        if self.sock is not None:
            try:
                self.sock.close()
            except Exception:  # noqa: BLE001
                pass
        self.sock = None

    def _drop_ws(self):
        """Close + forget the persistent WS client (a disconnect / latest-wins replacement / a
        stalled send)."""
        if self._ws is not None:
            try:
                self._ws.close()
            except Exception:  # noqa: BLE001
                pass
            self._ws = None

    def url(self):
        return "http://%s:%d/" % (self.ip or "0.0.0.0", self.port)

    def connected(self):
        """True while a WebSocket client is connected and not idle-timed-out."""
        if self.sock is None or self._ws is None or not self._ws.alive:
            return False
        return ticks_diff(ticks_ms(), self._ws.last_recv) < WS_IDLE_MS

    def send_text(self, payload):
        """Push one WS text frame to the connected client. False when none."""
        ws = self._ws
        if ws is None or not ws.alive:
            return False
        ok = ws.send(payload)
        if not ws.alive:
            self._drop_ws()
        return ok

    def poll(self):
        """Run once per loop iteration, BETWEEN frames. Accept new connections, then service
        the persistent WS conn (drain input -> on_text; reap an idle client). Returns True if
        anything was handled. A stalled client is dropped, never waited on."""
        if self.sock is None:
            return False
        did = self._accept_new()
        did = self._service_ws() or did
        return did

    # service() is the conceptual name; poll() is the established hook name.
    service = poll

    def _accept_new(self):
        """Accept + dispatch up to POLL_MAX pending NEW connections (non-blocking). accept()
        EAGAINs the instant nothing is pending. A WS upgrade is promoted to the persistent conn;
        any other request is a one-shot HTTP serve + close."""
        did = False
        for _ in range(POLL_MAX):
            try:
                conn, _addr = self.sock.accept()
            except Exception:  # noqa: BLE001 -- EAGAIN: no more pending connections
                break
            did = True
            try:
                self._dispatch(conn)
            except Exception as exc:  # noqa: BLE001 -- a bad request must not crash the loop
                print("Moybyte web: request error:", exc)
                try:
                    conn.close()
                except Exception:  # noqa: BLE001
                    pass
        return did

    def _dispatch(self, conn):
        """Read one request head off a freshly accepted conn and route it: a WS upgrade is
        promoted to the persistent live conn (the conn STAYS OPEN); any HTTP request is served
        and the conn closed."""
        method, path, body, raw = self._recv_request(conn)
        if method == "GET" and is_ws_upgrade(raw):
            key = ws_header_key(raw)
            if key:
                self._upgrade_ws(conn, key)
                return
            self._http_send_close(conn, http_response(400, "bad upgrade",
                                                      "text/plain; charset=utf-8"))
            return
        if method is None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
            return
        self._serve_http(conn, method, path, body)

    def _upgrade_ws(self, conn, key):
        """Complete the WebSocket handshake (101) and install the conn as the persistent live
        client, dropping any previous one (latest-wins). On a handshake send failure, close."""
        try:
            conn.settimeout(WEB_SEND_TIMEOUT)
        except Exception:  # noqa: BLE001
            pass
        try:
            conn.sendall(ws_handshake_response(key))
        except Exception as exc:  # noqa: BLE001 -- couldn't 101 the client: drop it
            print("Moybyte web: ws handshake failed:", exc)
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
            return
        self._drop_ws()                            # latest-wins: one client at a time
        self._ws = _WSConn(conn)
        self.requests += 1
        print("Moybyte web: ws client connected")

    def _service_ws(self):
        """Drain the persistent WS conn's queued input frames (-> on_text) and reap an idle or
        dead client. No-op when no client is connected."""
        ws = self._ws
        if ws is None:
            return False
        if not ws.alive:
            self._drop_ws()
            return False
        did = False
        for payload in ws.drain_input():
            did = True
            if self.on_text is not None:
                self.on_text(payload)
        if not ws.alive:                           # a close/oversize frame killed it
            self._drop_ws()
            return did
        # Idle reaper: a half-open conn (closed tab, dropped WiFi) that stopped sending.
        if ticks_diff(ticks_ms(), ws.last_recv) >= WS_IDLE_MS:
            self._drop_ws()
        return did

    def _http_send_close(self, conn, data):
        """sendall `data` (with a short send budget) then close -- the one-shot HTTP path.

        `data` is either complete response bytes or a FileResponse, which is
        streamed in chunks so a megabyte asset never has to be resident."""
        try:
            conn.settimeout(WEB_SEND_TIMEOUT)
        except Exception:  # noqa: BLE001
            pass
        try:
            if isinstance(data, FileResponse):
                self._send_file(conn, data)
            elif isinstance(data, ChunkedResponse):
                self._send_chunked(conn, data)
            else:
                conn.sendall(data)
        except Exception:  # noqa: BLE001 -- a stalled client: drop it, nothing to wait on
            pass
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass

    def _send_chunked(self, conn, resp):
        """Head, then each generated piece as one HTTP chunk, then the
        terminator. Pieces are COALESCED to at least CHUNK_MIN bytes: the
        packer yields a few bytes at a time (a key, a value) and one TCP send
        per token would spend the whole transfer in syscall overhead."""
        conn.sendall(resp.head())
        buf = []
        n = 0
        for piece in resp.body_iter:
            if isinstance(piece, str):
                piece = piece.encode("utf-8")
            buf.append(piece)
            n += len(piece)
            if n >= CHUNK_MIN:
                self._send_chunk(conn, b"".join(buf))
                buf = []
                n = 0
        if n:
            self._send_chunk(conn, b"".join(buf))
        conn.sendall(b"0\r\n\r\n")

    @staticmethod
    def _send_chunk(conn, data):
        # ONE sendall, not three. The size line and the trailing CRLF are tiny,
        # and a separate send for each is a separate trip through the stack --
        # on a board that is measurable, and it also invites Nagle to sit on the
        # small ones waiting for an ACK.
        conn.sendall(b"%x\r\n" % len(data) + data + b"\r\n")

    def _send_file(self, conn, resp):
        """Head, then the file, through the subclass's storage gate if it has one.

        The gate matters on the T-Deck and nowhere else. Its SD card shares the
        panel's SPI host, so an SD op may not overlap an in-flight panel DMA --
        `moybyte_sd.with_sd_live` is reached through a wrapper that drains it
        first (`comp.sync()`), and skipping that is the documented hard-hang:
        the read lands, then the NEXT panel flush freezes the board with no
        panic and nothing on serial.

        This whole transfer is ONE blocking call rather than a per-frame pump,
        so a single gate entry covers it -- the hazard is the first read racing
        the DMA the last frame left in flight, not a thousand interleavings.
        Boards without shared storage set no gate and pay nothing.
        """
        gate = getattr(self, "stream_gate", None)
        if gate is not None:
            gate(lambda: self._send_file_body(conn, resp))
        else:
            self._send_file_body(conn, resp)

    def _send_file_body(self, conn, resp):
        """Head, then the file in CHUNK-sized pieces off a reused buffer.

        `readinto` and a memoryview slice, not `read(n)`: read() mints a fresh
        bytes object per chunk, and a thousand of those during a 1MB transfer is
        exactly the allocation churn that costs a collect mid-frame on the S3.
        """
        conn.sendall(resp.head())
        buf = bytearray(resp.CHUNK)
        mv = memoryview(buf)
        with open(resp.path, "rb") as f:
            while True:
                n = f.readinto(buf)
                if not n:
                    break
                conn.sendall(mv[:n] if n < resp.CHUNK else buf)

    def _recv_request(self, conn):
        """Read one request head (+ body up to Content-Length) off a freshly accepted conn.
        Blocking with a short bound (the request is already en route). Returns (method, path,
        body, raw_head) -- raw_head lets the caller sniff a WebSocket upgrade. (None, None, b'',
        b'') on an unparseable request."""
        try:
            conn.settimeout(WEB_RECV_TIMEOUT)
        except Exception:  # noqa: BLE001 -- not all ports expose settimeout
            pass
        buf = b""
        method = path = None
        clen = 0
        head_end = -1
        while len(buf) <= 65536:                  # cap: a runaway client can't OOM us
            try:
                chunk = conn.recv(512)
            except Exception:  # noqa: BLE001 -- timeout / error: use what we have
                break
            if not chunk:                         # peer closed
                break
            buf += chunk
            if head_end < 0:
                method, path, clen, head_end = parse_request(buf)
            if head_end >= 0 and len(buf) - head_end >= clen:
                break
        if head_end < 0:
            return (None, None, b"", buf)
        body = buf[head_end:head_end + clen] if clen else b""
        return (method, path, body, buf[:head_end])

    def handle_http(self, method, path, body):
        """Endpoint seam for the 3.4 sync RPC: return complete http_response()
        bytes, or None for a 404. The base transport serves nothing."""
        return None

    def _serve_http(self, conn, method, path, body):
        """Serve a one-shot HTTP request through handle_http and close the conn."""
        self.requests += 1
        resp = self.handle_http(method, path, body)
        if resp is None:
            resp = http_response(404, "not found", "text/plain; charset=utf-8")
        self._http_send_close(conn, resp)
