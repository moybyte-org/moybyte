# Moybyte device socket/HTTP TRANSPORT CORE.
#
# This file used to be the device web view (#41/#22) -- the streaming browser
# mirror that pushed the console's draw commands over a WebSocket. That whole
# feature DIED in the 2026-08 streaming sunset (docs/history/moycore_plan_2026-08.md
# 3.2, owner decision): the frame push, the DrawRecorder wiring, the TeeCanvas
# lane, stream mode and the Settings WEB VIEW row are deleted; the browser's
# job moved to the wasm head (firmware/web_runner), synced per plan 3.4.
#
# The WEBSOCKET HALF went with it (2026-09-15) and the reason is worth stating,
# because it survived the sunset on a claim that was never true: the RFC 6455
# upgrade, framing and persistent conn were kept "for the 3.4 sync RPC to ride",
# and the RPC shipped as PLAIN HTTP (moy_webhost.handle_http, runtime/moy_sync).
# So ~180 lines here plus the whole `web_view_ws` framing leaf were frozen into
# five board images with no caller of any kind -- and `.claude/rules/web.md`
# already records the opposite decision on the merits: the update routes go
# through the HTTP host "never the idle WebSocket core", because WS_IDLE_MS
# reaped a client through a flash write. A transport kept for a consumer that
# chose otherwise is a transport with no consumer.
#
# What survives is the HTTP transport: a non-blocking listening socket, a
# request parser/response builder, and one-shot request serving. The hardware
# constraints this code embodies were learned on glass and should not be
# re-derived:
#
#   * SINGLE-THREADED, NON-BLOCKING: run_desktop's native loop does one render
#     frame at a time and never services anything mid-frame, so the listener is
#     non-blocking and poll() runs once per loop iteration, between frames.
#   * Sends block with a short budget (a non-blocking sendall can't push a
#     multi-KB body over the device's WiFi); the accept path reads with a short
#     blocking bound, because the request is already en route.

try:
    import usocket as socket
except Exception:  # noqa: BLE001 -- host / CPython
    import socket

# PORT 80, the one a browser assumes (owner decision, 2026-08-29 -- it was 8080
# from the start, as a bare constant with no argument behind it).
#
# Nothing here ever needed the high port. The privileged-port rule that makes
# 8080 the reflex is a Unix-root thing; MicroPython/lwIP on a board has no such
# restriction, and the host dev twin does not use 8080 either
# (firmware/web_runner/serve.py defaults to 8321), so 8080 was only ever the
# on-board default and nothing depended on it.
#
# What it cost is five characters of an address a KID has to carry from a
# 320x240 panel to a phone. `http://<ip>:8080/?pin=NNNN` is 35 characters, which
# moy_qr encodes as a 29-module version-3 symbol; dropping the port makes it 30,
# a 25-module version 2 -- meaningfully bigger modules in the same rect. The
# Settings row fits the address without falling back to lying about it, and
# `moybyte-zero.local` typed bare in a browser resolves only on 80, on the one
# board with no screen to show a kid the address.
#
# NOT a hard-coded 80 anywhere else: `url()` omits the port only when the port
# IS 80, so `WebHost(port=8321)` still renders `:8321` and stays reachable.
DEFAULT_PORT = 80

# Per-connection socket timeouts (seconds). A freshly accepted conn is read
# BLOCKING with a short bound (the request is already en route); sends use a
# longer blocking budget (see the header).
WEB_RECV_TIMEOUT = 0.4
WEB_SEND_TIMEOUT = 2.0

# Max NEW connections poll() accepts per loop iteration. accept() EAGAINs the
# instant nothing is pending, so this only caps a flood.
POLL_MAX = 4

# Listen backlog. NOT 1: every response here is one-shot close (no keep-alive),
# so a browser loading the console opens 4-6 connections at once, and lwIP's
# tcp_listen_input SILENTLY DROPS a SYN past the backlog -- the extras wait out
# a client SYN-retransmit (~1s each) rather than failing, which is why this
# reads as a slow page and never as an error.
#
# Measured on a T-Deck, 6 simultaneous SYNs x 3 trials: at 4, exactly 2 of 6
# connects took ~1.25s and the rest ~30ms -- the split lands on the backlog
# depth. At 6 none did, and the wall time halved.
#
# 6 is the CEILING, not a preference: it is IDF's LWIP_TCP_ACCEPTMBOX_SIZE
# default (no board overrides it), and past a full accept mbox lwIP aborts the
# new pcb with an RST -- turning a slow page into a refused one.
LISTEN_BACKLOG = 6


# ---------------------------------------------------------------------------
# HTTP request parsing (host-testable, no socket).
# ---------------------------------------------------------------------------


def parse_request(raw):
    """Parse a raw HTTP request (bytes or str) into (method, target, content_length,
    header_end). header_end is the index just past the blank line ending the headers (-1 if
    the headers aren't complete yet). A malformed request returns (None, None, 0, -1).

    `target` is the REQUEST TARGET VERBATIM, query string and all. It used to be
    stripped at "?" here, which was the wrong place to do it (2026-08-25): a GET
    carries its pin as `?pin=NNNN` -- the only place a GET can carry anything --
    and the transport was discarding the credential before any handler could see
    it, so gating a read was not expressible. Handlers split it themselves (they
    always did, defensively) and reach the query through `query_param`."""
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
    path = parts[1]
    clen = 0
    for ln in lines[1:]:
        c = ln.find(":")
        if c > 0 and ln[:c].strip().lower() == "content-length":
            try:
                clen = int(ln[c + 1:].strip())
            except Exception:  # noqa: BLE001
                clen = 0
    return (method, path, clen, sep + nlen)


def query_param(target, name):
    """The value of `name` in a request target's query string, or "".

    Deliberately small: no percent-decoding and no `+` handling, because the ONE
    thing that rides a query here is a four-digit pin and a decoder is code that
    can be wrong about a credential. A parameter whose value would need decoding
    is one this does not serve.
    """
    if not target:
        return ""
    q = target.split("?", 1)
    if len(q) < 2:
        return ""
    for pair in q[1].split("&"):
        kv = pair.split("=", 1)
        if kv[0] == name:
            return kv[1] if len(kv) > 1 else ""
    return ""


def http_response(status, body, content_type="application/json"):
    """Build a complete HTTP/1.1 response (bytes). `body` may be str or bytes. The server
    closes the connection after each response (Connection: close), which keeps the
    single-request-per-poll model simple and robust to half-open clients."""
    if isinstance(body, str):
        body = body.encode("utf-8")
    # 403/405/501 joined the table on 2026-08-25: all three were already being
    # SENT (the pin gate, the write-surface refusal, "no runner") and all three
    # went out reading `HTTP/1.1 403 OK`, because an unknown status fell through
    # to "OK". Browsers do not care, but a human reading a capture does, and the
    # page now branches on exactly these.
    reason = {200: "OK", 400: "Bad Request", 403: "Forbidden",
              404: "Not Found", 405: "Method Not Allowed",
              500: "Server Error", 501: "Not Implemented"}.get(status, "OK")
    head = (
        "HTTP/1.1 %d %s\r\n"
        "Content-Type: %s\r\n"
        "Content-Length: %d\r\n"
        "Cache-Control: no-store\r\n"
        "Access-Control-Allow-Origin: *\r\n"
        "Connection: close\r\n\r\n"
    ) % (status, reason, content_type, len(body))
    return head.encode("utf-8") + body


class _SizedResponse:
    """The head of a 200 whose body length is known up front.

    Shared by the two bodies that have one -- a file on storage and a blob in
    the image -- because the header block is the part a browser is unforgiving
    about (a wrong Content-Length or a missing Content-Encoding on gzipped
    bytes is a page that fails with nothing useful in the console), and two
    copies of it is two chances to get it wrong in one place only.
    """

    # How much body per sendall. Small on purpose: the file body reads into a
    # reused buffer of exactly this size, and #66 measures ~23KB of internal
    # SRAM free during play on the S3.
    CHUNK = 1024

    def __init__(self, size, content_type, max_age=0, encoding=None):
        self.size = size
        self.content_type = content_type
        self.max_age = max_age
        # `encoding` names a Content-Encoding the body is ALREADY stored in --
        # the board never compresses anything. A pre-gzipped bundle halves the
        # bytes on the wire (1,155,953 -> 572,693 for the four console assets)
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


class BlobResponse(_SizedResponse):
    """A response whose body is a memoryview -- the bundle baked into the image.

    `moy_web` (native) hands out read-only memoryviews straight at the
    flash-mapped `.rodata` the build embedded, so this response holds no bytes
    of its own and neither does the send: `_send_blob` slices the view and
    sendall reads the slice through the buffer protocol, flash -> lwip. That is
    the entire reason the embedded copy is affordable on a board with ~23KB of
    internal SRAM free in play; a `bytes(blob)` for the 523KB wasm would be a
    path that does not run rather than a slow one.

    No storage gate here, unlike FileResponse: there is no card and no shared
    SPI host involved, so the T-Deck's panel-DMA hazard does not apply.
    """

    # Bigger than the file CHUNK because there is no buffer to size: this only
    # bounds how much one sendall is asked to push, and each slice of a
    # memoryview costs an object header and copies nothing.
    CHUNK = 4096

    def __init__(self, data, content_type, max_age=0, encoding=None):
        _SizedResponse.__init__(self, len(data), content_type, max_age,
                                encoding)
        self.data = data


class FileResponse(_SizedResponse):
    """A response whose BODY is a file on the device, streamed rather than read.

    `handle_http` may return one of these instead of bytes. The transport sends
    the head, then pumps the file in CHUNK-sized pieces, so serving the wasm
    head (a 1.0MB `micropython.wasm`) costs a 1KB buffer instead of a 1MB one.
    That is not a nicety on the S3: #66 measures ~23KB of internal SRAM free in
    play, and a whole-file `bytes` would have to come out of PSRAM and be built
    before the first byte reached the wire.

    NOT CACHED. These are build artifacts, but a browser that has cached one
    holds a console the board is no longer serving, with nothing on either side
    to indicate why -- and it stays that way for the whole max-age. That
    failure is silent and lasts a day; the cost of being right is measured
    below and is 1.5 seconds.

    The cost of being right is small and measured: the 1MB wasm streams at
    ~700KB/s off the P4, so a full reload is ~1.5s. `max_age` stays a parameter
    for a caller that genuinely has immutable assets; the default is honest.
    """

    def __init__(self, path, size, content_type, max_age=0, encoding=None):
        _SizedResponse.__init__(self, size, content_type, max_age, encoding)
        self.path = path


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
#
# It is also a CEILING now and not only a floor: a chunk is assembled in one
# reusable buffer this size (see _send_chunked), so an oversized generated
# piece is split across fills instead of framed whole.
CHUNK_MIN = 8192

# Bytes reserved in front of that buffer for the chunk's own size line, which
# is written backwards into it so the frame goes out as ONE send with nothing
# concatenated. "%x\r\n" of any CHUNK_MIN under 0x1000000 fits in 8.
CHUNK_HEAD = 8

class WebServer:
    """The bare cooperative transport: a non-blocking listener and one-shot HTTP
    requests. No routes of its own.

    The seam a consumer implements is `handle_http(method, path, body)` --
    override it in a subclass to serve endpoints; return a complete
    http_response() bytes blob, or None for 404. The base serves 404 for
    everything. `path` is the REQUEST TARGET, so it may carry a query string:
    split it for routing and read it with `query_param`.

    poll() runs once per loop iteration, BETWEEN frames: accept up to POLL_MAX
    pending connections and serve each one-shot. Non-blocking throughout."""

    def __init__(self, port=DEFAULT_PORT):
        self.port = port
        self.sock = None
        self.ip = None
        self.requests = 0             # served-request counter (diag)

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
            s.listen(LISTEN_BACKLOG)
            s.setblocking(False)
            self.sock = s
            return True
        except Exception as exc:  # noqa: BLE001
            print("Moybyte web: server start failed:", exc)
            self.sock = None
            return False

    def stop(self):
        if self.sock is not None:
            try:
                self.sock.close()
            except Exception:  # noqa: BLE001
                pass
        self.sock = None

    def url(self):
        """The address to hand a human. The port is SPELLED unless it is 80.

        Omitting `:80` is what makes the default address short enough to type
        and small enough to encode (see DEFAULT_PORT); omitting anything else
        would produce a url a browser sends to the wrong port, so the rule is
        the port's identity, never "no port was passed"."""
        host = self.ip or "0.0.0.0"
        if self.port == 80:
            return "http://%s/" % host
        return "http://%s:%d/" % (host, self.port)

    def poll(self):
        """Run once per loop iteration, BETWEEN frames. Accept and serve whatever
        is pending. Returns True if anything was handled."""
        if self.sock is None:
            return False
        return self._accept_new()

    # service() is the conceptual name; poll() is the established hook name.
    service = poll

    def _accept_new(self):
        """Accept + dispatch up to POLL_MAX pending NEW connections (non-blocking). accept()
        EAGAINs the instant nothing is pending. Every request is a one-shot HTTP
        serve + close."""
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
        """Read one request head off a freshly accepted conn, serve it and close."""
        method, path, body = self._recv_request(conn)
        if method is None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
            return
        self._serve_http(conn, method, path, body)

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
            elif isinstance(data, BlobResponse):
                self._send_blob(conn, data)
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
        """Head, then the generated body reframed into HTTP chunks through ONE
        reusable buffer, then the terminator.

        Pieces are COALESCED to CHUNK_MIN bytes because the packer yields a few
        bytes at a time (a key, a piece of a value) and one TCP send per token
        would spend the whole transfer in syscall overhead. The BUFFER is why
        that coalescing now costs nothing: it used to be `b"".join(pieces)` and
        then `size + data + CRLF`, so every chunk minted two or three copies of
        itself, and the copies were as big as whatever the packer handed over.

        Affordable at 8KB, fatal at 150KB -- which is what one 142KB PICO-8 cart
        used to arrive as before the packer started yielding in pieces. MEASURED
        ON GUITION GLASS 2026-09-09: `MemoryError: memory allocation failed,
        allocating 150641 bytes` on a heap reporting 3.4MB free, because the
        largest free RUN on a 4MB MicroPython heap was smaller than the copies
        in flight. The response died mid-body and the browser read an
        IncompleteRead.

        So the payload is copied ONCE, into a bytearray that outlives every
        chunk, and a piece bigger than the buffer is split across fills rather
        than framed whole -- this holds nothing, whatever a generator yields.
        """
        conn.sendall(resp.head())
        buf = bytearray(CHUNK_HEAD + CHUNK_MIN + 2)
        mv = memoryview(buf)
        n = 0
        for piece in resp.body_iter:
            if isinstance(piece, str):
                piece = piece.encode("utf-8")
            src = memoryview(piece)
            while len(src):
                take = CHUNK_MIN - n
                if take > len(src):
                    take = len(src)
                mv[CHUNK_HEAD + n:CHUNK_HEAD + n + take] = src[:take]
                n += take
                src = src[take:]
                if n == CHUNK_MIN:
                    self._send_chunk(conn, buf, mv, n)
                    n = 0
        if n:
            self._send_chunk(conn, buf, mv, n)
        conn.sendall(b"0\r\n\r\n")

    @staticmethod
    def _send_chunk(conn, buf, mv, n):
        # ONE sendall, not three. The size line and the trailing CRLF are tiny,
        # and a separate send for each is a separate trip through the stack --
        # on a board that is measurable, and it also invites Nagle to sit on the
        # small ones waiting for an ACK. They are written INTO the payload
        # buffer, right in front of and right behind the bytes, so that one send
        # still allocates nothing.
        head = b"%x\r\n" % n
        i = CHUNK_HEAD - len(head)
        buf[i:CHUNK_HEAD] = head
        buf[CHUNK_HEAD + n] = 0x0D
        buf[CHUNK_HEAD + n + 1] = 0x0A
        conn.sendall(mv[i:CHUNK_HEAD + n + 2])

    def _send_blob(self, conn, resp):
        """Head, then the baked bundle, sliced out of flash.

        No buffer and no gate. `resp.data` is a read-only memoryview at the
        image's own `.rodata` (moy_web), so a slice allocates an object header
        and copies nothing, and sendall reads it through the buffer protocol --
        the bytes go flash -> lwip with nothing resident in between. The slice
        exists at all only to bound how much one sendall is asked to push over
        a slow link; one call for 523KB would sit inside the send timeout with
        no way to tell a busy client from a dead one.
        """
        conn.sendall(resp.head())
        mv = resp.data
        n = len(mv)
        i = 0
        while i < n:
            j = i + resp.CHUNK
            if j > n:
                j = n
            conn.sendall(mv[i:j])
            i = j

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
        Blocking with a short bound (the request is already en route). Returns
        (method, path, body), or (None, None, b"") on an unparseable request."""
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
            return (None, None, b"")
        body = buf[head_end:head_end + clen] if clen else b""
        return (method, path, body)

    def handle_http(self, method, path, body):
        """Endpoint seam for the 3.4 sync RPC: return complete http_response()
        bytes, or None for a 404. The base transport serves nothing.

        `path` arrives as the request TARGET -- `/carts.json?pin=1234`, not
        `/carts.json` -- because a GET has nowhere else to carry a credential
        and the transport must not spend it."""
        return None

    def _serve_http(self, conn, method, path, body):
        """Serve a one-shot HTTP request through handle_http and close the conn."""
        self.requests += 1
        resp = self.handle_http(method, path, body)
        if resp is None:
            resp = http_response(404, "not found", "text/plain; charset=utf-8")
        self._http_send_close(conn, resp)
