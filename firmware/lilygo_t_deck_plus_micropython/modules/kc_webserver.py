# KidCode device web view (#41 / #22) -- the DEVICE serves its own console over
# HTTP so a phone/desktop on the same WiFi can SEE the running cart and PLAY it.
#
# This is the on-device counterpart of the HOST web console (tools/web_console.py +
# tools/web_console.html). It speaks the SAME draw-command protocol, so the same
# browser page renders the device frames:
#
#   GET  /         -> the HTML page (a scaled <canvas> + JS replayer/poller).
#   GET  /assets   -> palette (KID64 -> RGB) + petme128 font + open cart sheet/tilemap.
#   GET  /frame    -> {"cmds":[...], "cart":...}: the LAST frame's recorded draw calls.
#   POST /input    -> browser events injected into the console's InputState/Pointer.
#
# WHY DRAW COMMANDS, NOT PIXELS (a hard device constraint): WiFi throughput on this
# board is ~72 KB/s (MicroPython lwIP ceiling), so streaming the raw 320x240 RGB565
# framebuffer (153 KB/frame) is unplayable. Instead we record the cart's per-frame
# draw calls (cls/rect/spr/print/... -- a few KB) and the browser REPLAYS them onto a
# <canvas> using the KID64 palette + the cart's spritesheet from /assets. The device
# keeps rendering to its OWN panel as normal; the web view is an ADDITIONAL consumer.
#
# SINGLE-THREADED, NON-BLOCKING (a hard device constraint): run_desktop's native loop
# does one render frame at a time and never services anything mid-frame. So this server
# uses a NON-BLOCKING listening socket and `poll()` accepts/handles AT MOST ONE request
# per call, BETWEEN frames. It must never block the render loop -- every socket op is
# guarded and a slow/partial client is dropped rather than waited on.
#
# ZERO COST WHEN OFF / NO BROWSER: recording is gated. TeeCanvas only appends commands
# while `recorder.enabled` is True, which is set only when the server is running AND a
# browser fetched a frame recently (see WebServer.recording_wanted). With the server off
# (the default) the Tee is a thin pass-through to the real DeviceCanvas -- one extra
# attribute check per draw call, no list building, no allocation.
#
# WiFi STA and the display SPI are SEPARATE peripherals, so the socket work does NOT
# collide with the SD/display bus rules -- but it DOES share CPU in the single-threaded
# loop, so per-frame server work is kept tiny (one accept, one short request).
#
# NEEDS ON-DEVICE VERIFICATION. The recorder + protocol + routing are host-tested
# (tests/test_kc_webserver.py drives the SAME code), but the actual MicroPython socket
# server, the WiFi<->LCD-DMA RAM coexistence (#38/#40), and the live throughput are
# UNPROVEN on hardware here. Treat the socket layer as a sketch until flashed.

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


DEFAULT_PORT = 8080

# Drop the recorder back to disabled this many ms after the last /frame fetch, so a
# browser that closed its tab stops costing the render loop anything (the Tee goes
# back to a pure pass-through). A live browser polls ~30x/s, so this is generous.
RECORD_IDLE_MS = 2000

# Per-connection socket timeouts (seconds). The accepted conn is BLOCKING with a bound,
# NOT non-blocking: a non-blocking sendall can't push a multi-KB response over the device's
# ~72KB/s WiFi and fails [Errno 116] ETIMEDOUT (it only worked on fast localhost in tests).
# A short READ timeout caps how long a speculative/empty browser preconnect can stall the
# single-threaded loop; a longer SEND timeout lets the HTML page / assets drain.
WEB_RECV_TIMEOUT = 0.4
WEB_SEND_TIMEOUT = 2.0

# petme128 8x8 font (host == device): the SAME glyphs runtime/font.py ships, baked here
# as a hex blob so the device (whose panel text uses framebuf's own font) can still hand
# the browser the petme128 glyphs the shared web_console.html renders `print` with. 96
# glyphs (ASCII 0x20..0x7f), 8 column-bytes each, LSB = top row -- exactly font._FONT.
FONT_FIRST = 0x20
FONT_W = 8
FONT_H = 8
_FONT_HEX = (
    "00000000000000000000004f4f0000000007070000070700147f7f14147f7f14"
    "00242e6b6b3a1200006333180c66630000327f4d4d777250000000040603010000"
    "001c3e63410000000041633e1c0000082a3e1c1c3e2a080008083e3e080800000080"
    "e0600000000008080808080800000000606000000000406030180c0602003e7f4945"
    "7f3e000040447f7f40400000627351494f460000226349497f360000181814167f7f"
    "1000276745457d3900003e7f49497b3200000303797d07030000367f49497f360000"
    "266f49497f3e000000002424000000000080e46400000000081c366341410000141414"
    "1414140000414163361c080000020351590f0600003e7f414d4f2e00007c7e0b0b7e7c"
    "00007f7f49497f3600003e7f4141632200007f7f41633e1c00007f7f49494141"
    "00007f7f0909010100003e7f41497b3a00007f7f08087f7f000000417f7f410000002060"
    "417f3f0100007f7f1c36634100007f7f4040404000007f7f060c067f7f007f7f0e1c7f7f"
    "00003e7f41417f3e00007f7f09090f0600001e3f21617f5e00007f7f19396f460000266f"
    "49497b32000001017f7f010100003f7f40407f3f00001f3f60603f1f00007f7f3018307f7f"
    "0063771c1c77630000070f78780f0700006171594d47430000007f7f414100000002060c"
    "18306040000041417f7f000000080c06060c0800c0c0c0c0c0c0c0c0000001030604000000"
    "207454547c7800007f7f44447c380000387c44446c280000387c44447f7f0000387c5454"
    "5c580000087e7f090302000098bca4a4fc7c00007f7f04047c78000000007d7d0000000040"
    "c08080fd7d00007f7f30386c44000000417f7f400000007c7c1830187c7c007c7c04047c78"
    "0000387c44447c380000fcfc24243c180000183c2424fcfc00007c7c04040c080000485c54"
    "5474200004043f7f44642000003c7c40407c3c00001c3c60603c1c00001c7c3018307c1c00"
    "446c38386c4400009cbca0a0fc7c00004464745c4c44000008083e7741410000"
    "0000ffff000000004141773e0808000002030103020301aa55aa55aa55aa55"
)


def _font_glyphs():
    """The petme128 glyphs as a list of 8-column-byte lists (the /assets JSON shape
    the browser replayer reads, identical to tools/web_console.font_glyphs())."""
    blob = bytes.fromhex(_FONT_HEX)
    n = len(blob) // FONT_W
    return [list(blob[i * FONT_W:(i + 1) * FONT_W]) for i in range(n)]


# ---------------------------------------------------------------------------
# The recorder: a draw-command list, command format IDENTICAL to the host's
# tools/command_canvas.CommandCanvas (so the same web_console.html replays it).
# ---------------------------------------------------------------------------


class DrawRecorder:
    """Records draw calls into a per-frame, JSON-serializable command list, in the
    SAME format as tools/command_canvas.CommandCanvas. TeeCanvas forwards every draw
    call here (in addition to the real DeviceCanvas) ONLY while `enabled` is True, so
    a frame with no browser connected costs nothing. `swap()` hands off the finished
    frame's commands and starts a fresh list -- called once per frame from the loop."""

    def __init__(self, w, h):
        self.w = w
        self.h = h
        self.enabled = False
        self._cmds = []
        self._frame = []        # the last COMPLETE frame handed to the server

    # -- frame handoff -------------------------------------------------------
    def begin(self):
        """Start recording a fresh frame (drop a partial one defensively)."""
        self._cmds = []

    def commit(self):
        """Finish the frame: the accumulated commands become the served frame."""
        self._frame = self._cmds
        self._cmds = []

    def frame(self):
        """The last committed frame's command list (what GET /frame serves)."""
        return self._frame

    # -- draw state (camera / clip / pal / palt) -----------------------------
    def reset_state(self):
        self._cmds.append(["reset_state"])

    def camera(self, x=0, y=0):
        self._cmds.append(["camera", int(x), int(y)])

    def clip(self, x=None, y=None, w=None, h=None):
        if x is None:
            self._cmds.append(["clip"])
        else:
            self._cmds.append(["clip", int(x), int(y), int(w), int(h)])

    def pal(self, c0=None, c1=None):
        if c0 is None:
            self._cmds.append(["pal"])
        else:
            self._cmds.append(["pal", int(c0) & 63, int(c1) & 63])

    def palt(self, c=None, on=None):
        if c is None:
            self._cmds.append(["palt"])
        else:
            self._cmds.append(["palt", int(c) & 63, 1 if on else 0])

    # -- primitives ----------------------------------------------------------
    def cls(self, c=0):
        self._cmds.append(["cls", c & 63])

    def pix(self, x, y, c):
        self._cmds.append(["pix", int(x), int(y), c & 63])

    def line(self, x0, y0, x1, y1, c):
        self._cmds.append(["line", int(x0), int(y0), int(x1), int(y1), c & 63])

    def rect(self, x, y, w, h, c):
        self._cmds.append(["rect", int(x), int(y), int(w), int(h), c & 63])

    def rectb(self, x, y, w, h, c):
        self._cmds.append(["rectb", int(x), int(y), int(w), int(h), c & 63])

    def circ(self, cx, cy, r, c):
        self._cmds.append(["circ", int(cx), int(cy), int(r), c & 63])

    def circb(self, cx, cy, r, c):
        self._cmds.append(["circb", int(cx), int(cy), int(r), c & 63])

    def spr(self, img, x, y, scale=1, flip=0):
        # img is an Image / _SheetSprite (.w/.h/.pix/.transparent); ids are already
        # resolved to pixels by the time the canvas sees it. Carry the raw pixels so
        # the stream is self-contained (no sheet lookup needed to be correct).
        t = img.transparent
        if t is None:
            t = -1
        self._cmds.append(["spr", int(x), int(y), int(scale),
                           int(img.w), int(img.h), int(t), list(img.pix), int(flip)])

    def print(self, s, x, y, c):
        self._cmds.append(["print", str(s), int(x), int(y), c & 63])


# ---------------------------------------------------------------------------
# TeeCanvas: forward every draw call to the real DeviceCanvas (the panel still
# renders) AND, while recording is enabled, to the DrawRecorder. The console reads
# ws.canvas every frame, so run_desktop swaps in this Tee transparently.
# ---------------------------------------------------------------------------


class TeeCanvas:
    """Wraps the device's real `Canvas` (DeviceCanvas) so the panel renders exactly as
    before, and ALSO records draw calls for the web view -- but only while
    `recorder.enabled` is True. When disabled, each method is a single extra branch
    over a direct delegate call (no list ops, no allocation), so the normal no-browser
    path is effectively free.

    It mirrors the full Canvas surface the console + carts call. Reads (`pix` with two
    args, attribute reads like `.buf`/`.w`) pass straight through to the real canvas.
    Scroll layers (new_layer) are NOT teed -- a layer is an off-screen pre-render
    source, not part of the per-frame draw stream; its window-copy into the framebuffer
    appears in the stream as the cls/draws around it on the main canvas. (A scrolling
    cart's web view shows the composed frame minus the layer's static background; this
    is a known limitation, documented for the device-verification follow-up.)"""

    def __init__(self, canvas, recorder):
        self._c = canvas
        self._r = recorder
        self.w = canvas.w
        self.h = canvas.h

    # framebuffer-shaped reads the console uses (composite, sizing) pass through.
    def __getattr__(self, name):
        # Only reached for attrs not set on the Tee (e.g. `buf`, `_comp`, `sync_back`,
        # `new_layer`, `blit_window_from`). Delegate to the wrapped canvas.
        return getattr(self._c, name)

    # -- draw state ----------------------------------------------------------
    def reset_state(self):
        self._c.reset_state()
        if self._r.enabled:
            self._r.reset_state()

    def camera(self, x=0, y=0):
        if self._r.enabled:
            self._r.camera(x, y)
        return self._c.camera(x, y)

    def clip(self, x=None, y=None, w=None, h=None):
        self._c.clip(x, y, w, h)
        if self._r.enabled:
            self._r.clip(x, y, w, h)

    def pal(self, c0=None, c1=None):
        self._c.pal(c0, c1)
        if self._r.enabled:
            self._r.pal(c0, c1)

    def palt(self, c=None, on=None):
        self._c.palt(c, on)
        if self._r.enabled:
            self._r.palt(c, on)

    # -- primitives ----------------------------------------------------------
    def cls(self, c=0):
        self._c.cls(c)
        if self._r.enabled:
            self._r.cls(c)

    def pix(self, x, y, c=None):
        if c is None:
            return self._c.pix(x, y)           # a read -> the real framebuffer
        self._c.pix(x, y, c)
        if self._r.enabled:
            self._r.pix(x, y, c)

    def line(self, x0, y0, x1, y1, c):
        self._c.line(x0, y0, x1, y1, c)
        if self._r.enabled:
            self._r.line(x0, y0, x1, y1, c)

    def rect(self, x, y, w, h, c):
        self._c.rect(x, y, w, h, c)
        if self._r.enabled:
            self._r.rect(x, y, w, h, c)

    def rectb(self, x, y, w, h, c):
        self._c.rectb(x, y, w, h, c)
        if self._r.enabled:
            self._r.rectb(x, y, w, h, c)

    def circ(self, cx, cy, r, c):
        self._c.circ(cx, cy, r, c)
        if self._r.enabled:
            self._r.circ(cx, cy, r, c)

    def circb(self, cx, cy, r, c):
        self._c.circb(cx, cy, r, c)
        if self._r.enabled:
            self._r.circb(cx, cy, r, c)

    def spr(self, img, x, y, scale=1, flip=0):
        self._c.spr(img, x, y, scale, flip)
        if self._r.enabled:
            self._r.spr(img, x, y, scale, flip)

    def spr_batch(self, sheet, items, colorkey=-1, scale=1):
        self._c.spr_batch(sheet, items, colorkey, scale)
        if self._r.enabled:
            # Expand to per-tile spr commands (mirrors CommandCanvas.spr_batch): the
            # browser replayer has no batch op, so emit one self-contained spr each.
            cache = {}
            for it in items:
                tid = int(it[0])
                if tid < 0:
                    continue
                flip = it[3] if len(it) > 3 else 0
                img = cache.get(tid)
                if img is None:
                    img = sheet.tile_image(tid, colorkey)
                    cache[tid] = img if img is not None else False
                if not img:
                    continue
                self._r.spr(img, it[1], it[2], scale, flip)

    def map(self, tilemap, sheet, mx=0, my=0, w=None, h=None,
            sx=0, sy=0, colorkey=-1, scale=1):
        self._c.map(tilemap, sheet, mx, my, w, h, sx, sy, colorkey, scale)
        if self._r.enabled:
            # Expand to per-cell spr commands (mirrors CommandCanvas.map), so the
            # browser needs no map op and stays pixel-identical.
            mx = int(mx)
            my = int(my)
            scale = int(scale) if int(scale) >= 1 else 1
            if w is None:
                w = tilemap.w - mx
            if h is None:
                h = tilemap.h - my
            tile = sheet.TILE
            step = tile * scale
            cache = {}
            for cy in range(int(h)):
                ty = my + cy
                py = sy + cy * step
                for cx in range(int(w)):
                    tid = tilemap.mget(mx + cx, ty)
                    if tid < 0:
                        continue
                    img = cache.get(tid)
                    if img is None:
                        img = sheet.tile_image(tid, colorkey)
                        cache[tid] = img if img is not None else False
                    if not img:
                        continue
                    self._r.spr(img, sx + cx * step, py, scale)

    def print(self, s, x, y, c, scale=2):
        self._c.print(s, x, y, c, scale)
        if self._r.enabled:
            self._r.print(s, x, y, c)


# ---------------------------------------------------------------------------
# Protocol payload builders (pure data -> JSON-serializable dicts). Shared shape
# with tools/web_console.py so the same web_console.html consumes them.
# ---------------------------------------------------------------------------


def palette_rgb(pal565):
    """The KID64 palette as 64 [r,g,b] triples, decoded from the device's RGB565 LUT
    so the browser resolves indices to the SAME colours the panel shows. pal565 is
    kid_runtime.PAL565 (canonical little-endian RGB565, NOT the byte-swapped LUT)."""
    out = []
    for c in pal565:
        r = (c >> 11) & 0x1F
        g = (c >> 5) & 0x3F
        b = c & 0x1F
        out.append([(r * 255) // 31, (g * 255) // 63, (b * 255) // 31])
    return out


def sheet_payload(sheet):
    """The open cart's sprite sheet as JSON (cols/rows/TILE + flat pixels), matching
    tools/web_console.sheet_json. None when there's no sheet."""
    if sheet is None:
        return None
    return {
        "cols": sheet.cols, "rows": sheet.rows, "tile": sheet.TILE,
        "w": sheet.w, "h": sheet.h,
        "pix": list(sheet.pix),
    }


def tilemap_payload(tilemap):
    """The open cart's tilemap as JSON (w/h + flat cells), matching
    tools/web_console.tilemap_json. None when there's no tilemap."""
    if tilemap is None:
        return None
    return {"w": tilemap.w, "h": tilemap.h, "cells": list(tilemap.cells)}


def assets_payload(w, h, pal565, sheet, tilemap, cart_title, audio_rate=8000):
    """The static render assets the browser needs (re-fetched on a cart change):
    palette + petme128 font + the open cart's sheet/tilemap + cart title. Same shape
    as tools/web_console.WebConsole.assets so web_console.html consumes it unchanged."""
    return {
        "w": w, "h": h,
        "palette": palette_rgb(pal565),
        "font": {"first": FONT_FIRST, "w": FONT_W, "h": FONT_H, "glyphs": _font_glyphs()},
        "sheet": sheet_payload(sheet),
        "tilemap": tilemap_payload(tilemap),
        "cart": cart_title,
        "audio_rate": audio_rate,
    }


def frame_payload(cmds, cart_title):
    """The per-frame payload: the recorded draw-command list + the cart title (so the
    client notices a cart change and refetches /assets). Matches GET /frame on the
    host (minus audio -- the device doesn't stream PCM over the web view)."""
    return {"cmds": cmds, "cart": cart_title, "audio": ""}


# The logical buttons a browser key/joystick maps to (mirrors the host BUTTON_NAMES);
# only forward names the console knows so a stray key can't wedge it.
BUTTON_NAMES = ("left", "right", "up", "down", "a", "b", "run", "home")


def apply_events(events, input, pointer, on_press=None, on_pan=None,
                 on_key=None, on_esc=None):
    """Inject a batch of browser events into the device's InputState + Pointer, the
    device twin of host_app.ConsoleDriver's event handling. `input` is the InputState,
    `pointer` the cursor; the hooks let run_desktop wire press/pan/key/esc to the same
    paths the keyboard/trackball use. Each event is fully guarded (a malformed one is
    skipped, never raised) so a buggy client can't crash the loop.

      {"type":"down","x":..,"y":..}  -> pointer tap (place + click + down)
      {"type":"move","x":..,"y":..}  -> pointer drag (place, down, no tap)
      {"type":"up"}                  -> release (pointer up)
      {"type":"pan","dx":..,"dy":..} -> trackball nudge (on_pan)
      {"type":"press","name":..}     -> one-shot button press (on_press)
      {"type":"hold","name":..,"down":bool} -> held button (input.set_button)
      {"type":"key","code":<ascii>}  -> typed key (on_key)
      {"type":"esc"}                 -> close panel (on_esc)
    """
    for ev in events:
        try:
            t = ev.get("type")
            if t == "down":
                pointer.place(int(ev.get("x", 0)), int(ev.get("y", 0)))
                pointer.down = True
                pointer.click = True
            elif t == "move":
                pointer.place(int(ev.get("x", 0)), int(ev.get("y", 0)))
                pointer.down = True
            elif t == "up":
                pointer.down = False
            elif t == "pan":
                if on_pan is not None:
                    on_pan(int(ev.get("dx", 0)), int(ev.get("dy", 0)))
            elif t == "press":
                name = ev.get("name")
                if name in BUTTON_NAMES and on_press is not None:
                    on_press(name)
            elif t == "hold":
                name = ev.get("name")
                if name in BUTTON_NAMES:
                    input.set_button(name, bool(ev.get("down")))
            elif t == "key":
                code = ev.get("code")
                if isinstance(code, int) and 0 <= code <= 0xFF and on_key is not None:
                    on_key(code)
            elif t == "esc":
                if on_esc is not None:
                    on_esc()
        except Exception:  # noqa: BLE001 -- one bad event must not drop the batch
            pass


# ---------------------------------------------------------------------------
# HTTP request parsing (host-testable, no socket). Parse a raw HTTP request head
# into (method, path, headers, body_start) so the server logic is unit-testable.
# ---------------------------------------------------------------------------


def parse_request(raw):
    """Parse a raw HTTP request (bytes or str) into (method, path, content_length,
    header_end). header_end is the index just past the blank line ending the headers
    (-1 if the headers aren't complete yet). path has its query string stripped. A
    malformed request returns (None, None, 0, -1)."""
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
    """Build a complete HTTP/1.1 response (bytes). `body` may be str or bytes. The
    server closes the connection after each response (Connection: close), which keeps
    the single-request-per-poll model simple and robust to half-open clients."""
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
# The server: a non-blocking listening socket, one request handled per poll().
# ---------------------------------------------------------------------------


class WebServer:
    """A cooperative, single-request-per-poll HTTP server for the device web view.

    run_desktop calls:
      * begin_frame()      once at the top of each frame, to start a fresh recording
                           (only when recording is wanted).
      * commit_frame()     after ws.frame(), to publish the frame's draw commands.
      * poll()             once per loop iteration, BETWEEN frames -- accepts at most
                           ONE connection and serves one request, fully non-blocking.

    The `provider` is a small object the server queries for live data without holding
    any console references itself:
      provider.assets()    -> the /assets dict
      provider.frame()     -> (cmds, cart_title) for /frame
      provider.apply(events)-> inject /input events
    """

    def __init__(self, recorder, provider, port=DEFAULT_PORT):
        self.recorder = recorder
        self.provider = provider
        self.port = port
        self.sock = None
        self.ip = None
        self._last_frame_req = 0      # ticks_ms of the last /frame fetch (browser live?)
        self.requests = 0             # served-request counter (diag)

    def start(self, ip=None):
        """Open the non-blocking listening socket. `ip` is the device's STA IP (for
        the printed URL). Returns True on success. Guarded -- a bind failure leaves the
        server inert (poll() then no-ops), never crashing the console."""
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
            print("KidCode web: server start failed:", exc)
            self.sock = None
            return False

    def stop(self):
        if self.sock is not None:
            try:
                self.sock.close()
            except Exception:  # noqa: BLE001
                pass
        self.sock = None
        self.recorder.enabled = False

    def url(self):
        return "http://%s:%d/" % (self.ip or "0.0.0.0", self.port)

    def recording_wanted(self):
        """True when a browser fetched a frame within RECORD_IDLE_MS -- the gate that
        keeps the Tee a pure pass-through unless something is actually watching."""
        if self.sock is None:
            return False
        return ticks_diff(ticks_ms(), self._last_frame_req) < RECORD_IDLE_MS

    def begin_frame(self):
        """Set the recorder's gate for THIS frame + start a fresh command list when a
        browser is live. Called at the top of the loop, before ws.frame()."""
        if self.sock is None:
            self.recorder.enabled = False
            return
        self.recorder.enabled = self.recording_wanted()
        if self.recorder.enabled:
            self.recorder.begin()

    def commit_frame(self):
        """Publish the frame's recorded commands (if we recorded this frame)."""
        if self.recorder.enabled:
            self.recorder.commit()

    def poll(self):
        """Accept + serve AT MOST ONE request, fully non-blocking. Returns True if a
        request was handled (diagnostic), False if there was nothing to do. Never
        blocks the render loop: a slow/partial client is dropped, not waited on."""
        if self.sock is None:
            return False
        try:
            conn, _addr = self.sock.accept()
        except Exception:  # noqa: BLE001 -- EAGAIN/no pending connection: the common path
            return False
        try:
            self._serve(conn)
            return True
        except Exception as exc:  # noqa: BLE001 -- a bad request must not crash the loop
            print("KidCode web: request error:", exc)
            return False
        finally:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass

    def _recv_request(self, conn):
        """Read one request: headers, then body up to Content-Length. Briefly blocking
        on THIS one connection only (it was just accepted, so the request is en route),
        with a small read budget so a stalled client can't hang the loop. Returns
        (method, path, body) or (None, None, b'')."""
        # BLOCKING with a short bound (not non-blocking): a non-blocking sendall later
        # can't drain a multi-KB response over slow WiFi (-> ETIMEDOUT). The short read
        # timeout means a speculative/empty preconnect stalls the loop at most
        # WEB_RECV_TIMEOUT, while a real request (already en route -- we just accepted)
        # arrives in one or two recvs.
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

    def _serve(self, conn):
        method, path, body = self._recv_request(conn)
        if method is None:
            return
        # Give the response room to drain over slow WiFi (the read used a short bound).
        try:
            conn.settimeout(WEB_SEND_TIMEOUT)
        except Exception:  # noqa: BLE001
            pass
        self.requests += 1
        if method == "GET" and path in ("/", "/index.html"):
            conn.sendall(http_response(200, PAGE_HTML, "text/html; charset=utf-8"))
        elif method == "GET" and path == "/assets":
            conn.sendall(http_response(200, json.dumps(self.provider.assets())))
        elif method == "GET" and path == "/frame":
            self._last_frame_req = ticks_ms()      # mark the browser live -> keep recording
            cmds, cart = self.provider.frame()
            conn.sendall(http_response(200, json.dumps(frame_payload(cmds, cart))))
        elif method == "POST" and path == "/input":
            events = []
            if body:
                try:
                    payload = json.loads(body)
                    events = payload.get("events", []) if isinstance(payload, dict) else payload
                    if not isinstance(events, list):
                        events = []
                except Exception:  # noqa: BLE001 -- a bad body -> no events, still 200
                    events = []
            self.provider.apply(events)
            conn.sendall(http_response(200, '{"ok":true}'))
        else:
            conn.sendall(http_response(404, "not found", "text/plain; charset=utf-8"))


def _sleep_ms(ms):
    try:
        from utime import sleep_ms
        sleep_ms(ms)
    except Exception:  # noqa: BLE001 -- host / CPython
        import time
        time.sleep(ms / 1000.0)


# ---------------------------------------------------------------------------
# The page: a minimal embedded replayer that speaks the SAME protocol as
# tools/web_console.html (so the device serves a self-contained playable view).
# Kept compact to save frozen flash; it does the load-bearing job: fetch /assets,
# poll /frame, replay the indexed draw commands against the KID64 palette, and POST
# /input (touch drag + an on-screen joystick/A/B + WASD/arrows).
# ---------------------------------------------------------------------------

PAGE_HTML = """<!doctype html><html><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>KidCode device</title><style>
html,body{margin:0;height:100%;background:#0b0f1a;color:#c2c3c7;
font:14px ui-monospace,Menlo,Consolas,monospace;display:flex;flex-direction:column;
align-items:center}
h1{font-size:14px;color:#fff1e8;margin:8px}#s{color:#ffec27}
canvas{image-rendering:pixelated;background:#000;border:1px solid #1d2b53;border-radius:6px;
width:min(96vw,112vh);height:auto;max-width:100%;touch-action:none;cursor:crosshair}
#ctl{display:flex;justify-content:space-between;gap:24px;width:min(96vw,112vh);
max-width:100%;padding:12px 8px;box-sizing:border-box;touch-action:none;user-select:none}
#joy{position:relative;width:120px;height:120px;border-radius:50%;background:#1d2b53;
border:2px solid #29366f}#th{position:absolute;top:50%;left:50%;width:52px;height:52px;
margin:-26px 0 0 -26px;border-radius:50%;background:#5f6f9f;border:2px solid #c2c3c7;
pointer-events:none}.b{width:72px;height:72px;border-radius:50%;display:flex;
align-items:center;justify-content:center;font:700 26px ui-monospace;color:#fff1e8;
background:#7e2553;border:2px solid #c2c3c7;margin-left:18px}#bb{background:#29366f}
.pr{background:#ffec27;color:#1d2b53}</style></head><body>
<h1>KidCode &mdash; device <span id=s>connecting...</span></h1>
<canvas id=cv width=320 height=240 tabindex=0></canvas>
<div id=ctl><div id=joy><div id=th></div></div>
<div><span class=b id=bb>B</span><span class=b id=ba>A</span></div></div>
<script>
var FPS=20,cv=document.getElementById("cv"),cx=cv.getContext("2d"),sEl=document.getElementById("s");
cx.imageSmoothingEnabled=false;
var W=320,H=240,PAL=null,FONT=null,ready=false,assCart=undefined,idx=null,img=null,rgba=null;
function alloc(){cv.width=W;cv.height=H;cx=cv.getContext("2d");cx.imageSmoothingEnabled=false;
idx=new Uint8Array(W*H);img=cx.createImageData(W,H);rgba=img.data;}
function getA(){return fetch("/assets").then(function(r){return r.json();}).then(function(a){
W=a.w;H=a.h;PAL=a.palette;FONT=a.font;assCart=a.cart;alloc();ready=true;});}
var caX=0,caY=0,cl0=0,cm0=0,cl1=W,cm1=H,pm=null,pt=null;
function rs(){caX=0;caY=0;cl0=0;cm0=0;cl1=W;cm1=H;pm=new Uint8Array(64);pt=new Uint8Array(64);
for(var i=0;i<64;i++)pm[i]=i;}rs();
function put(x,y,c){x=(x-caX)|0;y=(y-caY)|0;if(x<cl0||x>=cl1||y<cm0||y>=cm1)return;idx[y*W+x]=pm[c&63];}
function fr(x,y,w,h,c){x=(x|0)-caX;y=(y|0)-caY;w|=0;h|=0;var a=Math.max(cl0,x),b=Math.max(cm0,y),
e=Math.min(cl1,x+w),f=Math.min(cm1,y+h);if(e<=a||f<=b)return;var ci=pm[c&63];
for(var yy=b;yy<f;yy++){var bs=yy*W;for(var xx=a;xx<e;xx++)idx[bs+xx]=ci;}}
function rb(x,y,w,h,c){fr(x,y,w,1,c);fr(x,y+h-1,w,1,c);fr(x,y,1,h,c);fr(x+w-1,y,1,h,c);}
function ln(x0,y0,x1,y1,c){x0|=0;y0|=0;x1|=0;y1|=0;var dx=Math.abs(x1-x0),dy=-Math.abs(y1-y0),
sx=x0<x1?1:-1,sy=y0<y1?1:-1,er=dx+dy,e2;while(true){put(x0,y0,c);if(x0==x1&&y0==y1)break;
e2=2*er;if(e2>=dy){er+=dy;x0+=sx;}if(e2<=dx){er+=dx;y0+=sy;}}}
function ci(cxx,cyy,r,c){cxx|=0;cyy|=0;r|=0;for(var dy=-r;dy<=r;dy++){var sp=Math.floor(Math.sqrt(r*r-dy*dy));
fr(cxx-sp,cyy+dy,2*sp+1,1,c);}}
function cb(cxx,cyy,r,c){cxx|=0;cyy|=0;r|=0;var x=r,y=0,er=0;while(x>=y){
var p=[[x,y],[y,x],[-y,x],[-x,y],[-x,-y],[-y,-x],[y,-x],[x,-y]];
for(var i=0;i<8;i++)put(cxx+p[i][0],cyy+p[i][1],c);y++;if(er<=0){er+=2*y+1;}else{x--;er-=2*x+1;}}}
function sp(x,y,sc,sw,sh,t,px,fl){x|=0;y|=0;sc|=0;fl|=0;var fx=fl&1,fy=(fl>>1)&1;
for(var yy=0;yy<sh;yy++){var ry=fy?sh-1-yy:yy,bs=ry*sw;for(var xx=0;xx<sw;xx++){var rx=fx?sw-1-xx:xx,
p=px[bs+rx];if(p===t||p<0||pt[p&63])continue;if(sc<=1)put(x+xx,y+yy,p);else fr(x+xx*sc,y+yy*sc,sc,sc,p);}}}
function tx(s,x,y,c){if(!FONT)return;var X=x|0;y|=0;var fi=FONT.first,gw=FONT.w,g=FONT.glyphs,n=g.length;
for(var k=0;k<s.length;k++){var gi=s.charCodeAt(k)-fi,co=(gi>=0&&gi<n)?g[gi]:g[0];
for(var j=0;j<gw;j++){var bt=co[j],py=y;while(bt){if(bt&1)put(X+j,py,c);bt>>=1;py++;}}X+=gw;}}
function rep(cs){for(var i=0;i<cs.length;i++){var c=cs[i],o=c[0];
if(o=="cls")idx.fill(pm[c[1]&63]);else if(o=="pix")put(c[1],c[2],c[3]);
else if(o=="line")ln(c[1],c[2],c[3],c[4],c[5]);else if(o=="rect")fr(c[1],c[2],c[3],c[4],c[5]);
else if(o=="rectb")rb(c[1],c[2],c[3],c[4],c[5]);else if(o=="circ")ci(c[1],c[2],c[3],c[4]);
else if(o=="circb")cb(c[1],c[2],c[3],c[4]);else if(o=="spr")sp(c[1],c[2],c[3],c[4],c[5],c[6],c[7],c[8]||0);
else if(o=="print")tx(c[1],c[2],c[3],c[4]);else if(o=="reset_state")rs();
else if(o=="camera"){caX=c[1]|0;caY=c[2]|0;}
else if(o=="clip"){if(c.length>1){var a=c[1]|0,b=c[2]|0,w=c[3]|0,h=c[4]|0;cl0=Math.max(0,a);cm0=Math.max(0,b);
cl1=Math.min(W,a+w);cm1=Math.min(H,b+h);}else{cl0=0;cm0=0;cl1=W;cm1=H;}}
else if(o=="pal"){if(c.length>1)pm[c[1]&63]=c[2]&63;else for(var q=0;q<64;q++)pm[q]=q;}
else if(o=="palt"){if(c.length>1)pt[c[1]&63]=c[2]?1:0;else pt.fill(0);}}}
function blit(){var n=W*H,j=0;for(var i=0;i<n;i++){var p=PAL[idx[i]];rgba[j++]=p[0];rgba[j++]=p[1];
rgba[j++]=p[2];rgba[j++]=255;}cx.putImageData(img,0,0);}
var q=[];function send(e){q.push(e);}
function xy(cX,cY){var r=cv.getBoundingClientRect();var x=Math.floor((cX-r.left)/r.width*W),
y=Math.floor((cY-r.top)/r.height*H);return{x:Math.max(0,Math.min(W-1,x)),y:Math.max(0,Math.min(H-1,y))};}
var drag=false;
cv.addEventListener("pointerdown",function(e){cv.focus();cv.setPointerCapture(e.pointerId);drag=true;
var p=xy(e.clientX,e.clientY);send({type:"down",x:p.x,y:p.y});e.preventDefault();});
cv.addEventListener("pointermove",function(e){if(!drag)return;var p=xy(e.clientX,e.clientY);
send({type:"move",x:p.x,y:p.y});e.preventDefault();});
function up(e){if(!drag)return;drag=false;send({type:"up"});if(e)e.preventDefault();}
cv.addEventListener("pointerup",up);cv.addEventListener("pointercancel",up);
var jE=document.getElementById("joy"),tE=document.getElementById("th"),jA=false,jP=null,
jH={left:false,right:false,up:false,down:false};
function jAp(d){["left","right","up","down"].forEach(function(n){var w=!!d[n];if(w!=jH[n]){jH[n]=w;
send({type:"hold",name:n,down:w});}});}
function jT(e){var r=jE.getBoundingClientRect(),cX=r.left+r.width/2,cY=r.top+r.height/2,
dx=e.clientX-cX,dy=e.clientY-cY,rad=r.width/2,d=Math.sqrt(dx*dx+dy*dy);
if(d>rad&&d>0){var s=rad/d;dx*=s;dy*=s;}tE.style.transform="translate("+dx+"px,"+dy+"px)";
var dz=rad*0.35;jAp({left:dx<-dz,right:dx>dz,up:dy<-dz,down:dy>dz});}
jE.addEventListener("pointerdown",function(e){jA=true;jP=e.pointerId;jE.setPointerCapture(e.pointerId);
jT(e);e.preventDefault();});
jE.addEventListener("pointermove",function(e){if(!jA||e.pointerId!=jP)return;jT(e);e.preventDefault();});
function jEnd(e){if(!jA||(e&&e.pointerId!=jP))return;jA=false;jP=null;jAp({});
tE.style.transform="translate(0,0)";if(e)e.preventDefault();}
jE.addEventListener("pointerup",jEnd);jE.addEventListener("pointercancel",jEnd);
function wb(id,nm){var el=document.getElementById(id),dn=false;
function pr(e){if(dn)return;dn=true;el.classList.add("pr");send({type:"hold",name:nm,down:true});if(e)e.preventDefault();}
function rl(e){if(!dn)return;dn=false;el.classList.remove("pr");send({type:"hold",name:nm,down:false});if(e)e.preventDefault();}
el.addEventListener("pointerdown",function(e){el.setPointerCapture(e.pointerId);pr(e);});
el.addEventListener("pointerup",rl);el.addEventListener("pointercancel",rl);el.addEventListener("pointerleave",rl);}
wb("ba","a");wb("bb","b");
var PAN={ArrowLeft:[-1,0],ArrowRight:[1,0],ArrowUp:[0,-1],ArrowDown:[0,1]},
NAV={a:"left",d:"right",w:"up",s:"down"},SC={Enter:"run",z:"a",x:"b",h:"home"},pH={},nH={};
function nv(e){var k=e.key.length==1?e.key.toLowerCase():e.key;return NAV[k];}
cv.addEventListener("keydown",function(e){if(e.key in PAN){pH[e.key]=true;e.preventDefault();return;}
if(e.key=="Escape"){send({type:"esc"});e.preventDefault();return;}var cd=null;
if(e.key=="Enter")cd=13;else if(e.key=="Backspace")cd=8;else if(e.key.length==1&&e.key.charCodeAt(0)>=32&&e.key.charCodeAt(0)<=126)cd=e.key.charCodeAt(0);
if(cd!==null)send({type:"key",code:cd});var s=SC[e.key.length==1?e.key.toLowerCase():e.key];
if(s&&!e.repeat)send({type:"press",name:s});var n=nv(e);if(n&&!nH[n]){nH[n]=true;send({type:"hold",name:n,down:true});}
if(s||n||cd!==null)e.preventDefault();});
cv.addEventListener("keyup",function(e){if(e.key in PAN){delete pH[e.key];e.preventDefault();return;}
var n=nv(e);if(n&&nH[n]){delete nH[n];send({type:"hold",name:n,down:false});}});
var infl=false,ok=false;
function pv(){return[(pH.ArrowRight?1:0)-(pH.ArrowLeft?1:0),(pH.ArrowDown?1:0)-(pH.ArrowUp?1:0)];}
function fl(){var v=pv();if(v[0]||v[1])send({type:"pan",dx:v[0],dy:v[1]});if(!q.length)return;
var b=q;q=[];fetch("/input",{method:"POST",headers:{"Content-Type":"application/json"},
body:JSON.stringify({events:b})}).catch(function(){});}
function df(){if(infl||!ready)return;infl=true;fetch("/frame").then(function(r){return r.json();}).then(function(f){
if(f.cart!==assCart){assCart=f.cart;getA().catch(function(){});}rep(f.cmds||[]);blit();infl=false;
if(!ok){ok=true;sEl.textContent="live";sEl.style.color="#00e436";}}).catch(function(){infl=false;
sEl.textContent="reconnecting...";sEl.style.color="#ff004d";});}
function tick(){fl();df();}
getA().then(function(){setInterval(tick,Math.round(1000/FPS));}).catch(function(){
sEl.textContent="no assets";sEl.style.color="#ff004d";});cv.focus();
</script></body></html>"""
