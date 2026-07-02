# Moybyte device web view (#41 / #22) -- the DEVICE TRANSPORT for the console web view: a
# MicroPython socket server + a persistent WebSocket so a phone/desktop on the same WiFi can
# SEE the running cart and PLAY it.
#
# This file is now JUST the transport. The draw-command recorder, the payload builders, the
# serve-time defspr/deflayer logic (ServedState), the browser page (PAGE_HTML), and the
# protocol constants live in the SHARED, transport-agnostic `web_view` module (canonical
# source runtime/web_view.py; frozen onto the device as a top-level `web_view`). The HOST web
# console (tools/web_console.py) imports the SAME module, so the two consoles can't drift --
# only the socket/WebSocket plumbing differs, and that's here.
#
# THE LIVE CHANNEL is a WebSocket; the static handshake is plain HTTP:
#   GET  /         -> the HTML page (web_view.PAGE_HTML: a scaled <canvas> + JS replayer).
#   GET  /assets   -> palette (MOY64 -> RGB) + petme128 font + open cart sheet/tilemap +
#                     paint images (#63 Fold 4: {name:{w,h,b64}}, referenced by ["imgref",...]).
#   GET  /ws       -> (Upgrade: websocket) the PERSISTENT live channel + the ONLY transport:
#                     frame_payload text messages PUSH down per committed frame (capped),
#                     {"events":[...]} text messages push up -> apply_events. (The legacy HTTP
#                     poll transport -- GET/POST /frame + POST /input -- was removed; the page
#                     is WebSocket-only now, matching the host web console.)
#
# WHY A WEBSOCKET (the #41 transport swap): the old transport opened a NEW TCP connection per
# /frame plus a separate POST for input -- that per-frame handshake capped the browser at
# ~20-25fps. A persistent WebSocket removes it: frames stream down + input up on ONE socket.
# It does NOT lift the ~72KB/s WiFi ceiling (light screens ~30-40fps, the heavy launcher
# ~18fps) but it's smoother + lower-latency. The draw-command/atlas/stream-mode/input model
# is UNCHANGED -- this is a transport swap only.
#
# SINGLE-THREADED, NON-BLOCKING (a hard device constraint): run_desktop's native loop does one
# render frame at a time and never services anything mid-frame. So this server uses a
# NON-BLOCKING listening socket and a `service()` (called once per loop iteration, BETWEEN
# frames) that accepts new connections, drains the persistent WebSocket's queued input, and
# PUSHES at most one committed frame down it -- all without blocking. A WS frame may arrive
# split across reads, so the conn keeps a small read buffer and a parser that yields only
# COMPLETE frames; a stalled client is DROPPED, not waited on.
#
# ZERO COST WHEN OFF / NO BROWSER: recording is gated (web_view.TeeCanvas only appends while
# recorder.enabled is True, set only when a WebSocket client is connected -- see
# recording_wanted). With the server off (the default) or no browser, the Tee is a thin
# pass-through.
#
# NEEDS ON-DEVICE VERIFICATION. The recorder + protocol + routing are host-tested
# (tests/test_moy_webserver.py drives the SAME code), but the actual MicroPython socket
# server, the WiFi<->LCD-DMA RAM coexistence (#38/#40), and the live throughput are UNPROVEN
# on hardware here. Treat the socket layer as a sketch until flashed.

try:
    import ujson as json
except Exception:  # noqa: BLE001 -- host / CPython
    import json

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

# The SHARED web-view core: recorder + payload builders + serve logic + page + constants. On
# the device this is the frozen top-level `web_view` module (staged from runtime/web_view.py by
# build.sh); on the host / CPython it's runtime.web_view.
try:
    import web_view as _wv                     # frozen top-level (device)
except ImportError:                            # host / CPython: canonical source in runtime/
    from runtime import web_view as _wv

# Re-export the shared names so existing device consumers (moy_runtime.WebView) + the tests can
# keep referencing moy_webserver.DrawRecorder / TeeCanvas / assets_payload / apply_events /
# frame_payload / PAGE_HTML / the constants unchanged -- this file is an import swap for them.
DrawRecorder = _wv.DrawRecorder
_LayerRecorder = _wv._LayerRecorder
RecordingLayer = _wv.RecordingLayer
TeeCanvas = _wv.TeeCanvas
ServedState = _wv.ServedState
palette_rgb = _wv.palette_rgb
sheet_payload = _wv.sheet_payload
tilemap_payload = _wv.tilemap_payload
assets_payload = _wv.assets_payload
frame_payload = _wv.frame_payload
apply_events = _wv.apply_events
BUTTON_NAMES = _wv.BUTTON_NAMES
PAGE_HTML = _wv.PAGE_HTML
FONT_FIRST = _wv.FONT_FIRST
FONT_W = _wv.FONT_W
FONT_H = _wv.FONT_H
_font_glyphs = _wv._font_glyphs
MAX_ATLAS = _wv.MAX_ATLAS
MAX_DEFSPR_BYTES_PER_FRAME = _wv.MAX_DEFSPR_BYTES_PER_FRAME
WEB_FPS_CAP = _wv.WEB_FPS_CAP
WEB_FRAME_INTERVAL_MS = _wv.WEB_FRAME_INTERVAL_MS
WEB_MAX_BYTES_PER_SEC = _wv.WEB_MAX_BYTES_PER_SEC
# The WebSocket handshake + byte framing now live in the SHARED web_view too (canonical home);
# re-export them so _WSConn + the upgrade path below reference the module-level names UNCHANGED.
ws_accept_key = _wv.ws_accept_key
ws_handshake_response = _wv.ws_handshake_response
ws_header_key = _wv.ws_header_key
is_ws_upgrade = _wv.is_ws_upgrade
ws_encode = _wv.ws_encode
ws_decode = _wv.ws_decode
WS_GUID = _wv.WS_GUID
WS_MAX_FRAME = _wv.WS_MAX_FRAME


DEFAULT_PORT = 8080

# Consider a WebSocket client DEAD (and drop it -> stop recording) if we haven't seen any read
# activity from it for this long. The browser sends input batches every tick and we answer ws
# pings with pongs, so a live client is never idle this long; this reaps a half-open conn
# (closed tab, dropped WiFi) that never sent a TCP close. Liveness = "a WS client is connected
# AND not timed out".
RECORD_IDLE_MS = 4000

# Free-heap sample interval (#41 perf). gc.mem_free() WALKS the whole 6MB PSRAM heap (~tens of
# ms), and it runs in the single-threaded render loop -- so sampling it every frame (or even 1/s)
# is itself a periodic ~60ms STALL that shows up as a stutter (and, when timed inside the json
# encode, masqueraded as a huge `js`). The heap is a slow-moving leak-watch diagnostic, so 5s
# resolution is plenty and the stall drops 5x. Kept OUT of the js timing (see _push_frame).
HEAP_SAMPLE_MS = 5000

# Per-connection socket timeouts (seconds). A freshly accepted conn is read BLOCKING with a
# short bound (the request is already en route). A non-blocking sendall can't push a multi-KB
# body over the device's ~72KB/s WiFi, so the SEND uses a longer blocking budget. The
# persistent WS conn is then NON-blocking for reads but keeps the blocking send budget.
WEB_RECV_TIMEOUT = 0.4
WEB_SEND_TIMEOUT = 2.0

# Max NEW connections service() accepts per loop iteration. accept() EAGAINs the instant
# nothing is pending, so this only caps a flood.
POLL_MAX = 4

# (WS_MAX_FRAME -- the max inbound WS text-frame size -- now lives in web_view alongside
# ws_decode, which enforces it; it's re-exported above.)

# Max bytes we let a WS conn's read buffer grow to before giving up (a peer that dribbles
# header bytes without ever completing a frame). Dropping the conn is always safe.
WS_MAX_BUFFER = 16384


# ---------------------------------------------------------------------------
# HTTP request parsing (host-testable, no socket). Parse a raw HTTP request head into
# (method, path, content_length, header_end) so the server logic is unit-testable.
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


# ---------------------------------------------------------------------------
# WebSocket transport (RFC 6455): the LIVE channel. The handshake + byte-framing helpers
# (ws_accept_key / ws_handshake_response / ws_header_key / is_ws_upgrade / ws_encode / ws_decode
# + WS_GUID / WS_MAX_FRAME) are now the SHARED web_view's -- re-exported above, so _WSConn + the
# upgrade path reference the same module-level names UNCHANGED. Only the opcodes + _WSConn (the
# socket wiring) live here.
# ---------------------------------------------------------------------------


# WebSocket opcodes we care about.
WS_OP_TEXT = 0x1
WS_OP_CLOSE = 0x8
WS_OP_PING = 0x9
WS_OP_PONG = 0xA


# ---------------------------------------------------------------------------
# The server: a non-blocking listening socket; HTTP for the page/assets + a persistent
# WebSocket for the live frame-push / input-up channel. service() runs once per loop.
# ---------------------------------------------------------------------------


class _WSConn:
    """The persistent WebSocket connection (one client at a time). Non-blocking reads with a
    cross-iteration read buffer + a parser that yields only COMPLETE inbound frames and retains
    the partial remainder; blocking-with-a-budget sends so a multi-KB frame can drain over slow
    WiFi. Every op is guarded -- a closed/stalled peer sets .alive False and the server drops it
    (the browser auto-reconnects)."""

    def __init__(self, conn):
        self._c = conn
        self._buf = b""             # inbound bytes not yet forming a complete frame
        self.alive = True
        self.last_recv = ticks_ms()  # for the idle reaper (RECORD_IDLE_MS)
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
    """A cooperative web-view server: plain HTTP for the page + assets, and a PERSISTENT
    WebSocket (one client) for the live frame-push / input-up channel.

    run_desktop calls:
      * begin_frame()      once at the top of each frame, to start a fresh recording (only
                           when a WS client is live).
      * commit_frame()     after ws.frame(), to publish the frame's draw commands.
      * poll() / service() once per loop iteration, BETWEEN frames -- accepts new connections
                           (HTTP one-shot, or a WS upgrade promoted to the persistent conn),
                           drains the WS's queued input -> apply, and PUSHES the latest
                           committed frame down it (capped). Non-blocking; a stalled client is
                           dropped, never waited on.

    The `provider` is a small object the server queries for live data without holding any
    console references itself:
      provider.assets()    -> the /assets dict
      provider.frame()     -> (cmds, cart_title) for /frame and the WS push
      provider.apply(events)-> inject browser events

    The serve-time defspr/deflayer ship-once bookkeeping lives in the SHARED web_view.ServedState
    (self._served_state), so the host web console runs the exact same serve path."""

    def __init__(self, recorder, provider, port=DEFAULT_PORT):
        self.recorder = recorder
        self.provider = provider
        self.port = port
        self.sock = None
        self.ip = None
        self._last_record_ms = 0      # ticks_ms of the last RECORDED frame (the fps cap)
        self._last_push_ms = 0        # ticks_ms of the last frame PUSHED down the WS (cap)
        self.requests = 0             # served-request counter (diag)
        self._frames_pushed = 0       # frames sent down the WS since boot (#41 perf log)
        self._last_json_ms = 0        # ms spent json-encoding the last pushed frame ...
        self._last_send_ms = 0        # ... and ms in the socket send (#41 perf log)
        # Worst-case stutter diag (#41): these are per-frame INSTANTANEOUS samples; the browser
        # accumulates each into a 2s-window MAX, so a lone slow frame (a hitch) isn't averaged
        # away the way recv/dev/bw are. A stutter is a tail event, so the max is what matters.
        self._frame_begin_ms = 0      # ticks at begin_frame -> the draw+commit span below
        self._last_draw_ms = 0        # device draw+commit ms of the last frame (begin->commit):
                                      # cart logic + rasterize, separate from the js/tx push cost
        self._last_gap_ms = 0         # ms since the previous push -- the REAL inter-frame period
                                      # (max over a window = the worst frame, i.e. the stutter)
        self._last_throttled = 0      # 1 if the bandwidth cap raised THIS push's interval above
                                      # the fps floor (resolves throttle-limited vs device-limited)
        self._heap_kb = 0             # cached gc.mem_free KB + when it was last sampled:
        self._heap_ms = 0             # gc.mem_free WALKS the whole heap (~tens of ms), sample ~1/s
        self._last_payload_bytes = 0  # size of the last pushed frame -> floors the next push
                                      # interval so a heavy screen can't saturate WiFi (#41)
        # The PERSISTENT WebSocket client (one at a time). None = no browser connected -> the
        # recorder stays a pure pass-through. A new upgrade drops the old conn (latest-wins).
        self._ws = None
        # SERVE-TIME defspr + deflayer ship-once, in the SHARED, transport-agnostic ServedState:
        # each sprite bitmap + layer stream travels ONCE per browser session, yet every served
        # frame is self-contained (drop-robust). Resets on /assets (reset_served) + on a dropped
        # atlas (atlas_gen change).
        self._served_state = ServedState(recorder)

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
        self.recorder.enabled = False
        self.recorder.record_only = False

    def _drop_ws(self):
        """Close + forget the persistent WS client (a disconnect / latest-wins replacement / a
        stalled send). The recorder gate then falls back to pass-through next begin_frame."""
        if self._ws is not None:
            try:
                self._ws.close()
            except Exception:  # noqa: BLE001
                pass
            self._ws = None

    def url(self):
        return "http://%s:%d/" % (self.ip or "0.0.0.0", self.port)

    def recording_wanted(self):
        """True when a WebSocket client is currently connected AND not idle-timed-out -- the
        gate that keeps the Tee a pure pass-through unless a browser is actually watching."""
        if self.sock is None or self._ws is None or not self._ws.alive:
            return False
        return ticks_diff(ticks_ms(), self._ws.last_recv) < RECORD_IDLE_MS

    def stream_mode(self):
        """True when the device should go HEADLESS for the web stream this frame -- i.e. a
        browser is actively connected, so the loop can skip the device's OWN panel
        rasterization + flush (#41 30fps lever). Ties to the SAME gate as recording."""
        return self.recording_wanted()

    def served_frame(self, cmds):
        """Build the command list to actually SEND: the SHARED ServedState prepends any
        not-yet-shipped defsprs (serve-time defspr, #41) + deflayers (ship-once layers) for the
        indices/layers `cmds` references, so a served frame is self-contained even though the
        recorder no longer inlines them (and dropped frames can't strand a sprite)."""
        return self._served_state.served_frame(cmds)

    def reset_served(self):
        """Forget which defsprs + deflayers the browser has -- so the next /frame re-ships every
        sprite bitmap AND layer stream it references. Called when /assets is (re)served."""
        self._served_state.reset()

    def begin_frame(self):
        """Set the recorder's gate for THIS frame + start a fresh command list when a browser is
        live AND the fps cap allows. Called at the top of the loop, before ws.frame(). The cap
        (WEB_FPS_CAP) decouples the web stream from the cart; the decision is made ONCE here so a
        frame is recorded completely or not at all.

        STREAM MODE (#41): `record_only` (go headless) is DECOUPLED from the record cap. While a
        browser is live it's True EVERY frame -- so the loop's stream-mode edge fires ONCE and
        the panel stays frozen. The cap only throttles RECORDING (enabled)."""
        self._frame_begin_ms = ticks_ms()   # start the draw+commit span (perf log, #41)
        if self.sock is None or not self.recording_wanted():
            self.recorder.enabled = False
            self.recorder.record_only = False
            return
        # Browser live -> headless EVERY frame (stable across the cap, so no per-frame flap).
        self.recorder.record_only = self.stream_mode()
        now = ticks_ms()
        if ticks_diff(now, self._last_record_ms) < WEB_FRAME_INTERVAL_MS:
            self.recorder.enabled = False     # within the cap -> stay headless, don't record
            return
        self._last_record_ms = now
        self.recorder.enabled = True
        self.recorder.begin()

    def commit_frame(self):
        """Publish the frame's recorded commands (if we recorded this frame)."""
        if self.recorder.enabled:
            self.recorder.commit()
        # Device draw+commit ms = the begin_frame->here span (cart logic + rasterize, sans the
        # push). Reported every frame; the browser keeps the window max (perf log, #41).
        if self._frame_begin_ms:
            self._last_draw_ms = ticks_diff(ticks_ms(), self._frame_begin_ms)

    def poll(self):
        """Run once per loop iteration, BETWEEN frames. Two non-blocking jobs, never blocking
        the render loop:
          1. ACCEPT new connections (up to POLL_MAX). Each is either a one-shot HTTP request
             served + closed, or a WebSocket UPGRADE that becomes the PERSISTENT live conn.
          2. SERVICE the persistent WS conn: drain its queued input frames -> provider.apply,
             then PUSH the latest committed frame down it (capped at WEB_FPS_CAP).
        Returns True if anything was handled. A stalled client is dropped, never waited on."""
        if self.sock is None:
            return False
        did = self._accept_new()
        did = self._service_ws() or did
        return did

    # service() is the conceptual name; poll() is the established hook in run_desktop.
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
        # WebSocket upgrade on /ws (or /): complete the handshake + keep the conn.
        if method == "GET" and is_ws_upgrade(raw):
            key = ws_header_key(raw)
            if key:
                self._upgrade_ws(conn, key)
                return
            # Malformed upgrade (no key) -> 400 + close.
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
        # A fresh client starts with an empty atlas + no assets -> re-ship every defspr it
        # references, exactly as /assets did for the poll transport.
        self.reset_served()
        self._last_push_ms = 0                     # push the first frame promptly
        self.requests += 1
        _diag = "Moybyte web: ws client connected"
        print(_diag)

    def _service_ws(self):
        """Drain the persistent WS conn's queued input frames (-> provider.apply) and PUSH the
        latest committed frame down it (capped at WEB_FPS_CAP). Drops the conn if it died. No-op
        when no client is connected."""
        ws = self._ws
        if ws is None:
            return False
        if not ws.alive:
            self._drop_ws()
            return False
        did = False
        # 1. Inbound: decode queued text frames -> the SAME apply path as POST /input.
        for payload in ws.drain_input():
            did = True
            self._apply_ws_text(payload)
        if not ws.alive:                           # a close/oversize frame killed it
            self._drop_ws()
            return did
        # Idle reaper: a half-open conn (closed tab, dropped WiFi) that stopped sending.
        if ticks_diff(ticks_ms(), ws.last_recv) >= RECORD_IDLE_MS:
            self._drop_ws()
            return did
        # 2. Outbound: push the latest committed frame, capped. The interval is the fps cap,
        # RAISED for a heavy frame so WiFi never saturates (#41).
        now = ticks_ms()
        interval = self._push_interval_ms()
        if ticks_diff(now, self._last_push_ms) >= interval:
            # perf (#41): the ACTUAL inter-push gap (window-max = the worst frame = the stutter),
            # and whether the bandwidth cap raised this interval above the fps floor (throttle-
            # limited vs device-limited -- the launcher's low fps could be either).
            self._last_gap_ms = ticks_diff(now, self._last_push_ms) if self._last_push_ms else 0
            self._last_throttled = 1 if interval > WEB_FRAME_INTERVAL_MS else 0
            self._last_push_ms = now
            self._push_frame(ws)
            did = True
            if not ws.alive:                       # the push detected a stalled client
                self._drop_ws()
        return did

    def _apply_ws_text(self, payload):
        """Decode one inbound WS text payload ({"events":[...]}) and feed it through the SAME
        apply path POST /input used. A bad payload yields no events (never raises)."""
        try:
            data = payload.decode("utf-8") if isinstance(payload, (bytes, bytearray)) else payload
            obj = json.loads(data)
            events = obj.get("events", []) if isinstance(obj, dict) else obj
            if isinstance(events, list):
                self.provider.apply(events)
        except Exception:  # noqa: BLE001 -- a malformed message just yields no input
            pass

    def _perf_snapshot(self):
        """A tiny device-side stats dict for the per-frame payload (#41 perf log): free heap
        (KB) + the running pushed-frame count + last encode/send ms. gc.mem_free is
        MicroPython-only, so it's guarded -- on the host (CPython tests) heap is 0."""
        # gc.mem_free() WALKS the entire heap (~tens of ms on the 6MB PSRAM heap), so it must NOT
        # run every frame. Sample it at most once per HEAP_SAMPLE_MS and cache; it's just a
        # diagnostic. The CALLER (_push_frame) also keeps this out of the js-encode timing.
        now = ticks_ms()
        if self._heap_ms == 0 or ticks_diff(now, self._heap_ms) >= HEAP_SAMPLE_MS:
            self._heap_ms = now
            try:
                import gc
                self._heap_kb = gc.mem_free() // 1024
            except Exception:  # noqa: BLE001 -- no gc.mem_free on CPython; perf is best-effort
                self._heap_kb = 0
        # js/tx are the PREVIOUS frame's encode/send ms (set at the end of _push_frame); dr/gap
        # are this frame's draw + inter-push period; thr flags a bandwidth-throttled push. All
        # per-frame instants -- the browser folds them into a 2s-window MAX (the stutter signal).
        return {"heap": self._heap_kb, "pf": self._frames_pushed,
                "js": self._last_json_ms, "tx": self._last_send_ms,
                "dr": self._last_draw_ms, "gap": self._last_gap_ms,
                "thr": self._last_throttled}

    def _push_interval_ms(self):
        """Minimum ms between WS pushes: the fps-cap floor (WEB_FRAME_INTERVAL_MS), RAISED for a
        big last frame so the stream never exceeds WEB_MAX_BYTES_PER_SEC. A heavy screen
        self-throttles; a light game frame stays at the fps cap. Keyed on the LAST payload's
        size -- a cheap 1-frame-lagged estimate."""
        iv = WEB_FRAME_INTERVAL_MS
        if self._last_payload_bytes > 0:
            bw = self._last_payload_bytes * 1000 // WEB_MAX_BYTES_PER_SEC
            if bw > iv:
                iv = bw
        return iv

    def _push_frame(self, ws):
        """Send the latest committed frame as a WS text message: the SAME frame_payload (run
        through served_frame for the serve-time defspr prepend + the atlas gen) the HTTP /frame
        path returned -- only the transport differs. Times the json-encode + the socket send
        separately (#41 perf log)."""
        cmds, cart = self.provider.frame()
        cmds = self.served_frame(cmds)
        self._frames_pushed += 1
        # Snapshot perf BEFORE the timing: _perf_snapshot may do the gc.mem_free heap walk (tens
        # of ms), which must NOT be attributed to json.dumps (that made `js` read ~60ms on even a
        # 0.5KB frame -- a phantom). Now `js` is the pure encode cost.
        perf = self._perf_snapshot()
        t0 = ticks_ms()
        payload = json.dumps(frame_payload(cmds, cart, self.recorder.atlas_gen, perf))
        t1 = ticks_ms()
        ws.send(payload)
        t2 = ticks_ms()
        self._last_json_ms = ticks_diff(t1, t0)
        self._last_send_ms = ticks_diff(t2, t1)
        self._last_payload_bytes = len(payload)

    def _http_send_close(self, conn, data):
        """sendall `data` (with a short send budget) then close -- the one-shot HTTP path."""
        try:
            conn.settimeout(WEB_SEND_TIMEOUT)
        except Exception:  # noqa: BLE001
            pass
        try:
            conn.sendall(data)
        except Exception:  # noqa: BLE001 -- a stalled client: drop it, nothing to wait on
            pass
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass

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

    def _serve_http(self, conn, method, path, body):
        """Serve a one-shot HTTP request (the page or /assets) and close the conn. The LIVE
        channel is the WebSocket; only the page + assets still load over plain HTTP (the legacy
        /frame & /input poll endpoints were removed -- the page is WebSocket-only now)."""
        self.requests += 1
        if method == "GET" and path in ("/", "/index.html"):
            self._http_send_close(conn, http_response(200, PAGE_HTML, "text/html; charset=utf-8"))
        elif method == "GET" and path == "/assets":
            # A page load / cart change: the browser clears its atlas + refetches /assets, so
            # forget what we've shipped -> the next frame re-ships the defsprs it references.
            self.reset_served()
            self._http_send_close(conn, http_response(200, json.dumps(self.provider.assets())))
        else:
            self._http_send_close(conn, http_response(404, "not found",
                                                      "text/plain; charset=utf-8"))


def _sleep_ms(ms):
    try:
        from utime import sleep_ms
        sleep_ms(ms)
    except Exception:  # noqa: BLE001 -- host / CPython
        import time
        time.sleep(ms / 1000.0)
