# KidCode device web view (#41 / #22) -- the DEVICE serves its own console over
# HTTP + a persistent WebSocket so a phone/desktop on the same WiFi can SEE the
# running cart and PLAY it.
#
# This is the on-device counterpart of the HOST web console (tools/web_console.py +
# tools/web_console.html). It speaks the SAME draw-command protocol, so the same
# browser page renders the device frames. The static handshake is plain HTTP; the
# LIVE channel is a WebSocket (frames push down, input pushes up, one socket):
#
#   GET  /         -> the HTML page (a scaled <canvas> + JS WebSocket replayer).
#   GET  /assets   -> palette (KID64 -> RGB) + petme128 font + open cart sheet/tilemap.
#   GET  /ws       -> (Upgrade: websocket) the PERSISTENT live channel:
#                       server -> client: a frame_payload JSON text message per committed
#                                         frame (capped at WEB_FPS_CAP), the SAME shape
#                                         GET /frame used to return.
#                       client -> server: {"events":[...]} text messages (the SAME event
#                                         batches POST /input carried) -> apply_events.
#   GET/POST /frame, POST /input  -> the legacy HTTP poll transport, kept as a FALLBACK
#                                    (the page no longer uses them; the live path is /ws).
#
# WHY A WEBSOCKET (the #41 transport swap): the old transport opened a NEW TCP connection
# per /frame (HTTP, Connection: close) plus a separate POST for input -- that per-frame
# handshake/teardown capped the browser at ~20-25fps and added an input round-trip per
# tick. A persistent WebSocket removes the per-frame handshake: frames stream down and
# input streams up on ONE long-lived socket. It does NOT lift the ~72KB/s WiFi ceiling --
# light screens (Battle City <1KB/frame) reach ~30-40fps, the heavy launcher (~4KB/frame)
# stays ~18fps -- but it is smoother + lower-latency than the poll. (The protocol below is
# UNCHANGED -- this is a TRANSPORT swap only; the draw-command/atlas/stream-mode/input
# model is byte-identical to the HTTP path.)
#
# WHY DRAW COMMANDS, NOT PIXELS (a hard device constraint): WiFi throughput on this
# board is ~72 KB/s (MicroPython lwIP ceiling), so streaming the raw 320x240 RGB565
# framebuffer (153 KB/frame) is unplayable. Instead we record the cart's per-frame
# draw calls (cls/rect/spr/print/... -- a few KB) and the browser REPLAYS them onto a
# <canvas> using the KID64 palette + the cart's spritesheet from /assets. The device
# keeps rendering to its OWN panel as normal; the web view is an ADDITIONAL consumer.
#
# PAYLOAD DIET (#41, the 10x lever): a NAIVE recorder embeds the sprite's full pixel
# array in every `spr` and expands a `map()` into one fat per-cell spr (a 20x15 map ~=
# 100 KB/frame -> ~1.4 s/refresh over WiFi -- unplayable for tile carts like Battle
# City). This recorder ships pixels ONCE and then references them by index:
#
#   ["defspr", index, w, h, t, pix]      ship a unique bitmap ONCE (an atlas entry).
#   ["spr", index, x, y, scale, flip]    blit an already-shipped bitmap (~6 numbers).
#   ["map", mx, my, w, h, sx, sy, scale, colorkey]   ONE op; the browser replays it from
#                                        its CACHED tilemap + sheet (both from /assets).
#   ["settiles", w, h, cells]            sync the browser's tilemap before a map op when
#                                        the cart mutated it (mset, e.g. a destroyed brick).
#
# The atlas is keyed by id(img): make_api reuses one Image per (tile id, colorkey) across
# frames (the tile cache), so id(img) is a stable key for a unique bitmap. The recorder
# also HOLDS a reference to each img in the atlas, so the Image can't be GC'd and have its
# id reused for a different bitmap. The atlas resets when the cart/sheet changes. Net: a
# Battle City frame drops from tens of KB to <1 KB (one map op + a few dozen 6-int sprite
# refs + the atlas amortized once over the session).
#
# SERVE-TIME defspr (the drop-robust fix, #41): `spr` ALWAYS records a tiny ["spr", idx,
# ...] and only ENSURES the bitmap is in the recorder's atlas -- it never inlines a defspr
# based on record-time first-sight. The defspr is injected at SERVE time instead: the
# WebServer keeps a `served` set of atlas indices it has actually handed to the browser,
# and when it builds a /frame response it PREPENDS a ["defspr", ...] for every spr index in
# that frame the browser hasn't received yet (reconstructed via DrawRecorder.defspr_cmd).
# This is the load-bearing correctness fix: the frame cap DROPS frames and the browser only
# polls the LATEST /frame, so a defspr inlined at record-time rode a frame that was almost
# never the one served -> the browser had ["spr", idx] with no atlas entry -> nothing drawn.
# At serve time every served frame is self-contained for the sprites it references, robust
# to dropped frames, while still sending each bitmap only ONCE per browser session.
#
# SINGLE-THREADED, NON-BLOCKING (a hard device constraint): run_desktop's native loop
# does one render frame at a time and never services anything mid-frame. So this server
# uses a NON-BLOCKING listening socket and a `service()` (called once per loop iteration,
# BETWEEN frames) that accepts new connections, drains the persistent WebSocket's queued
# input frames, and PUSHES at most one committed frame down it -- all without blocking.
# A WS frame may arrive split across reads, so the conn keeps a small read buffer and a
# parser that yields only COMPLETE frames (retaining the partial remainder for the next
# iteration); a multi-KB send that can't fully drain over slow WiFi uses a short blocking
# send budget, and a stalled client is DROPPED, not waited on.
#
# ZERO COST WHEN OFF / NO BROWSER: recording is gated. TeeCanvas only appends commands
# while `recorder.enabled` is True, which is set only when the server is running AND a
# WebSocket client is currently connected (see WebServer.recording_wanted). With the
# server off (the default), or with no browser connected, the Tee is a thin pass-through
# to the real DeviceCanvas -- one extra attribute check per draw call, no list building,
# no allocation.
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

# WebSocket handshake needs sha1 + base64. MicroPython exposes uhashlib/ubinascii; CPython
# has hashlib/binascii. Both provide sha1 + b2a_base64, which is all the RFC 6455 accept
# computation uses.
try:
    import uhashlib as _hashlib
except Exception:  # noqa: BLE001 -- host / CPython
    import hashlib as _hashlib

try:
    import ubinascii as _binascii
except Exception:  # noqa: BLE001 -- host / CPython
    import binascii as _binascii


DEFAULT_PORT = 8080

# Consider a WebSocket client DEAD (and drop it -> stop recording) if we haven't seen any
# read activity from it for this long. The browser sends input batches every tick and we
# answer ws pings with pongs, so a live client is never idle this long; this just reaps a
# half-open conn (closed tab, dropped WiFi) that never sent a TCP close, so the recorder
# goes back to a pure pass-through. Liveness = "a WS client is connected AND not timed out".
RECORD_IDLE_MS = 4000

# Web-stream frame-rate cap (#41 payload diet). The device panel runs as fast as it can
# (~18-42fps), but a browser over the ~72KB/s WiFi can't consume that -- and recording a
# frame costs the loop list-building + JSON. So we RECORD at most this many frames/sec:
# at most one frame in every WEB_FRAME_INTERVAL_MS is recorded, the rest are pure
# pass-through (the Tee adds nothing, the panel keeps full speed). The browser polls and
# always gets the LAST fully-recorded frame. A frame is recorded completely or not at all
# (the gate is decided once, at begin_frame).
#
# 30fps (#41 stream mode): with STREAM MODE the device goes headless while a browser is
# playing (skips its OWN panel render + flush -- the ~14-20fps render+flush ceiling), so the
# cart can produce 30+fps of cheap commands. A diet frame is <1KB, so 30fps ~= 19KB/s, well
# under the ~72KB/s WiFi. The cap is what bounds it; raised 12 -> 30 to take that headroom.
WEB_FPS_CAP = 30
WEB_FRAME_INTERVAL_MS = 1000 // WEB_FPS_CAP

# Per-connection socket timeouts (seconds). A freshly accepted conn (an HTTP request OR the
# WS-upgrade GET) is read BLOCKING with a short bound: the request is already en route (we
# just accepted), so a real one arrives in a recv or two, while a speculative/empty preconnect
# stalls the loop at most WEB_RECV_TIMEOUT. A non-blocking sendall can't push a multi-KB body
# over the device's ~72KB/s WiFi (fails [Errno 116] ETIMEDOUT -- it only worked on fast
# localhost), so the SEND uses a longer blocking budget to let the HTML page / assets / a WS
# frame drain. The PERSISTENT WS conn itself is then made NON-blocking for reads (input may
# arrive split across iterations) but keeps the blocking send budget for pushes.
WEB_RECV_TIMEOUT = 0.4
WEB_SEND_TIMEOUT = 2.0

# Max NEW connections service() accepts per loop iteration. accept() EAGAINs the instant
# nothing is pending, so this only caps a flood -- the common case (the page load + /assets +
# the one WS upgrade) is a handful at startup, then nothing (the WS is persistent). The
# persistent WS conn is serviced once per iteration regardless (its input is drained + one
# frame pushed); it does NOT go through this accept cap.
POLL_MAX = 4

# Max bytes a single inbound WS text frame may carry (input batches are tiny -- a few events
# of a few ints each -- well under 1KB). A frame claiming more than this is treated as a
# protocol error and the conn is dropped, so a malformed/hostile client can't make us buffer
# unboundedly while waiting for a "complete" frame that never arrives.
WS_MAX_FRAME = 8192

# Max bytes we let a WS conn's read buffer grow to before giving up (defensive: a peer that
# dribbles header bytes without ever completing a frame). Dropping the conn is always safe --
# the browser auto-reconnects.
WS_MAX_BUFFER = 16384

# Defensive cap on the sprite atlas. The atlas is keyed by id(img) and assumes stable sprite
# identity (carts reuse one Image per tile via make_api's cache). A screen that makes FRESH
# Image objects every frame would grow it unbounded; past this many entries, reset (bump gen,
# browser re-ships) so memory + per-frame payload stay bounded. Normal carts never hit it.
MAX_ATLAS = 512

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
    """Records draw calls into a per-frame, JSON-serializable command list. TeeCanvas
    forwards every draw call here (in addition to the real DeviceCanvas) ONLY while
    `enabled` is True, so a frame with no browser connected costs nothing. `commit()`
    hands off the finished frame's commands and starts a fresh list -- called once per
    frame from the loop.

    PAYLOAD DIET (#41): primitives mirror the literal Canvas calls, but bitmaps are
    deduplicated. `spr` ALWAYS records a tiny ["spr", index, x, y, scale, flip] and just
    ENSURES the bitmap is in the atlas (assigning it the next dense index the first time
    the Image is seen). The ["defspr", index, w, h, t, pix] that ships the actual pixels is
    NOT emitted here -- the WebServer prepends it at SERVE time, once per browser session,
    for the indices a served frame references but the browser hasn't received yet (see
    defspr_cmd + WebServer.served). This is drop-robust: the frame cap discards frames and
    the browser polls only the latest, so a record-time defspr was almost never on the
    served frame. `map` records ONE ["map", ...] op (the browser replays it from its cached
    tilemap + sheet), preceded by a ["settiles", w, h, cells] when the recorder notices the
    tilemap changed (an mset). The sprite atlas {id(img): index} is keyed by id(img) --
    make_api reuses one Image per (tile id, colorkey), so the id is a STABLE key for a
    unique bitmap; the recorder also holds a reference to each img (self._atlas_keep) so the
    Image can't be GC'd and its id reused, AND so defspr_cmd can reconstruct the pixels at
    serve time. reset_atlas() drops the atlas + tilemap snapshot when the cart/sheet changes."""

    def __init__(self, w, h):
        self.w = w
        self.h = h
        self.enabled = False
        # STREAM MODE (#41 30fps lever): when True, the TeeCanvas RECORDS commands but does
        # NOT forward draws to the real DeviceCanvas (no rasterization), and run_desktop
        # skips the panel flush -- so the cart runs logic + records cheap commands and can
        # outrun the panel's render+flush ceiling. Set by the loop only while a browser is
        # actively playing; ignored when recording is disabled (no browser -> no streaming).
        self.record_only = False
        self._cmds = []
        self._frame = []        # the last COMPLETE frame handed to the server
        # Sprite atlas: id(img) -> dense index. _atlas_keep holds the Image objects so a
        # cached bitmap can't be collected and have its id reused for a different one (and
        # so defspr_cmd can reconstruct the pixels at serve time).
        self._atlas = {}
        self._atlas_keep = []
        # Atlas generation: bumped by reset_atlas() so the WebServer can notice the atlas
        # was dropped (cart/sheet change) and re-ship every defspr (its `served` set resets).
        self.atlas_gen = 0
        # Tilemap-change detection (for map()): the cells snapshot + the .gen counter
        # last shipped to the browser, so an unchanged map doesn't re-ship settiles.
        self._tiles_cells = None
        self._tiles_gen = None
        # spr_batch tile-image cache: (tid, colorkey) -> Image, so a batch tile resolves to
        # a STABLE Image across frames (id() stays put -> the atlas dedups it like spr()
        # does). Keyed-rebuilt when the sheet's paint gen changes; dropped on reset_atlas.
        self._batch_imgs = {}
        self._batch_gen = None
        # OFF-SCREEN LAYERS (#54 scroll + #43 cached top bar): RecordingLayers minted via
        # the Tee, indexed by their dense id. A blit_layer references one; the WebServer
        # ships its ["deflayer", ...] stream ONCE (serve-time, like defspr). Dropped on
        # reset_atlas (cart change), in lock-step with the atlas_gen the server keys off.
        self._layers = []

    # -- frame handoff -------------------------------------------------------
    def begin(self):
        """Start recording a fresh frame (drop a partial one defensively). Reset the atlas
        if it has grown past MAX_ATLAS (a screen with unstable sprite ids) so it can't leak
        -- bumps atlas_gen, so the browser re-ships in lock-step."""
        if len(self._atlas_keep) > MAX_ATLAS:
            self.reset_atlas()
        self._cmds = []

    def commit(self):
        """Finish the frame: the accumulated commands become the served frame."""
        self._frame = self._cmds
        self._cmds = []

    def frame(self):
        """The last committed frame's command list (what GET /frame serves)."""
        return self._frame

    def reset_atlas(self):
        """Drop the sprite atlas + tilemap snapshot. Called when the open cart / sheet /
        tilemap changes, so a new cart's bitmaps start at index 0 and can't collide with
        a previous cart's stale indices, and the next map() re-ships its tiles. The
        browser mirrors this by clearing its caches when /assets (the cart) changes. Bumps
        atlas_gen so the WebServer drops its `served` set and re-ships every defspr."""
        self._atlas = {}
        self._atlas_keep = []
        self._tiles_cells = None
        self._tiles_gen = None
        self._batch_imgs = {}
        self._batch_gen = None
        self._layers = []           # a new cart's layers start at id 0 (atlas_gen bumps -> re-ship)
        self.atlas_gen += 1

    def batch_tile_image(self, sheet, tid, colorkey):
        """Resolve a sheet tile to a STABLE Image (reused across frames) for spr_batch, so
        the atlas dedups it. Rebuilds the cache when the sheet's paint gen changes (a live
        edit), mirroring make_api's tile cache. Returns None for an empty/out-of-range tile."""
        gen = getattr(sheet, "gen", 0)
        if gen != self._batch_gen:
            self._batch_imgs = {}
            self._batch_gen = gen
        key = (tid, colorkey)
        img = self._batch_imgs.get(key)
        if img is None:
            img = sheet.tile_image(tid, colorkey)
            self._batch_imgs[key] = img if img is not None else False
        return img if img else None

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
        # resolved to pixels by the time the canvas sees it. SERVE-TIME defspr (#41): ALWAYS
        # record a tiny ["spr", index, x, y, scale, flip] and just ENSURE the bitmap is in
        # the atlas (assigning the next dense index the first time this Image is seen). The
        # ["defspr", ...] that carries the pixels is injected by the WebServer at SERVE time
        # (defspr_cmd), once per browser session, so a dropped frame can never strand a spr
        # without its bitmap. make_api reuses one Image per (tile id, colorkey) across
        # frames, so id(img) is a stable key for a unique bitmap.
        key = id(img)
        idx = self._atlas.get(key)
        if idx is None:
            idx = len(self._atlas_keep)
            self._atlas[key] = idx
            self._atlas_keep.append(img)        # hold a ref: id() can't be reused AND
                                                # defspr_cmd can reconstruct the pixels later
        self._cmds.append(["spr", idx, int(x), int(y), int(scale), int(flip)])

    def defspr_cmd(self, idx):
        """Reconstruct the ["defspr", idx, w, h, t, pix] for an atlas index from the held
        Image (self._atlas_keep[idx]). The WebServer calls this at SERVE time to prepend the
        bitmap the first time a served frame references it (see WebServer.served). Returns
        None for an out-of-range index (defensive: a stale frame referencing a reset atlas)."""
        if idx < 0 or idx >= len(self._atlas_keep):
            return None
        img = self._atlas_keep[idx]
        t = img.transparent
        if t is None:
            t = -1
        return ["defspr", int(idx), int(img.w), int(img.h), int(t), list(img.pix)]

    def settiles(self, tilemap):
        """Sync the browser's cached tilemap when it has CHANGED since last shipped, as a
        ["settiles", w, h, cells] command. The cart mutates the map via mset (which the
        Tee never sees directly), so we detect a change cheaply via the TileMap.gen counter
        (bumped on every mset) when present, falling back to a cells-snapshot compare. We
        track our OWN last-sent state (gen + a cells copy) and never touch the cart's dirty
        flag, so other code that relies on it is unaffected. Returns True if it shipped."""
        gen = getattr(tilemap, "gen", None)
        cells = tilemap.cells
        if gen is not None:
            if gen == self._tiles_gen:
                return False
            self._tiles_gen = gen
        else:
            # No gen counter: compare against the last-sent cells snapshot.
            if self._tiles_cells is not None and self._tiles_cells == cells:
                return False
        # Snapshot the cells we're shipping (a small ~w*h bytes copy) so a later compare
        # is against exactly what the browser holds.
        snap = list(cells)
        self._tiles_cells = list(cells)
        self._cmds.append(["settiles", int(tilemap.w), int(tilemap.h), snap])
        return True

    def map(self, mx, my, w, h, sx, sy, scale, colorkey):
        # PAYLOAD DIET (#41): ONE ["map", ...] op. The browser already has the sheet +
        # tilemap from /assets (kept in sync via settiles), so it replays the cell walk
        # itself instead of receiving ~300 fat per-cell spr commands.
        self._cmds.append(["map", int(mx), int(my), int(w), int(h),
                           int(sx), int(sy), int(scale), int(colorkey)])

    def print(self, s, x, y, c):
        self._cmds.append(["print", str(s), int(x), int(y), c & 63])

    # -- off-screen layers (#54 scroll + #43 cached top bar) -----------------
    # ONE recorded-layer mechanism: a layer the cart/console pre-renders into ONCE then
    # window-copies (draw_layer) or full-copies (blit_strip) each frame. The device's
    # layers are RGB565 (no index buffer kept), so we CANNOT ship the layer's finished
    # pixels as an indexed image. Instead the RecordingLayer (below) records the layer's
    # INDEXED DRAW-COMMAND STREAM (cls/rect/circ/line/print/spr -- palette indices), and
    # the browser REPLAYS that stream into an off-screen index buffer once (deflayer),
    # then blits a window/full of it (blit_layer). Same mechanism for scroll AND bar.
    def register_layer(self, layer):
        """Assign a RecordingLayer the next dense layer id and remember it so a
        blit reference can ship its stream once. Returns the id."""
        lid = len(self._layers)
        self._layers.append(layer)
        return lid

    def _ensure_registered(self, layer):
        """Self-heal a layer's id before it's referenced. reset_atlas (cart change) clears
        _layers, so the new cart's layers re-register from id 0 -- but a LONG-LIVED layer
        that outlives the reset keeps a STALE id. The console's cached top-bar strip
        (self._cart_bar_strip, reused across carts) is exactly this: after a cart change it
        still carries (say) id 0, which now COLLIDES with the new cart's freshly-registered
        scroll layer (also id 0) -> both emit ["blit_layer", 0] but reference DIFFERENT
        buffers (the bar blits the scroll's pixels and vice-versa: the "only background /
        previous cart's layer bleeds through / duplication" bug). Re-register an orphan
        here so its id is current + unique; served_layers reset on the gen bump re-ships its
        deflayer. Idempotent -- a correctly-registered layer is left untouched."""
        lid = layer.id
        if 0 <= lid < len(self._layers) and self._layers[lid] is layer:
            return
        layer.id = len(self._layers)
        self._layers.append(layer)

    def blit_layer_window(self, layer, cam_x, cam_y):
        """draw_layer's window-copy -> ["blit_layer", id, cam_x, cam_y] (clamped >=0 to
        mirror DeviceCanvas.blit_window_from). Opaque + full-screen, so the browser's
        copy also clears last frame -> no actor trails."""
        self._ensure_registered(layer)
        self._cmds.append(["blit_layer", int(layer.id),
                           cam_x if cam_x > 0 else 0, cam_y if cam_y > 0 else 0])

    def blit_layer_full(self, layer, dst_x, dst_y):
        """blit_strip's full-copy -> ["blit_layer", id, dst_x, dst_y, "full"]. The cached
        top bar + the scroll layer share this op."""
        self._ensure_registered(layer)
        self._cmds.append(["blit_layer", int(layer.id), int(dst_x), int(dst_y), "full"])

    def deflayer_cmd(self, idx):
        """Reconstruct ["deflayer", id, w, h, cmds] for a registered layer -- the
        WebServer prepends it at SERVE time the first time a served frame references the
        layer (and re-ships if its gen changed). None for an out-of-range/dropped id."""
        if idx < 0 or idx >= len(self._layers):
            return None
        layer = self._layers[idx]
        return ["deflayer", int(idx), int(layer.w), int(layer.h),
                list(layer.layer_cmds())]


class _LayerRecorder:
    """Records ONE off-screen layer's indexed draw-command stream. Primitives mirror the
    main DrawRecorder 1:1 (palette indices), but `spr` is recorded SELF-CONTAINED with
    its raw pixels (["spr", x, y, scale, w, h, t, pix, flip] -- the host command_canvas
    shape) rather than via the main atlas: a layer's stream ships as ONE deflayer, so it
    must carry everything it needs with no atlas coupling. (Sky Run's layer is primitives
    only; the device top-bar layer also blits icon sprites -- both covered.)"""

    def __init__(self):
        self._cmds = []

    def take(self):
        c = self._cmds
        self._cmds = []
        return c

    def cmds(self):
        return self._cmds

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

    def print(self, s, x, y, c, scale=2):
        # `scale` is accepted but IGNORED, exactly like the main DrawRecorder.print +
        # the real DeviceCanvas.print recording path (the browser renders petme128 at a
        # fixed scale). It MUST be in the signature: RecordingLayer._bind forwards a draw
        # verb's full arg list to this recorder, and the cached top bar prints its clock
        # with a scale arg (`print(s, x, y, c, scale)`) -- without this param that call
        # raised "takes 5 positional arguments but 6 were given" every frame.
        self._cmds.append(["print", str(s), int(x), int(y), c & 63])

    def spr(self, img, x, y, scale=1, flip=0):
        # SELF-CONTAINED full-pixel spr (the host command_canvas shape) so the layer
        # stream needs no atlas. img is an Image / _SheetSprite (.w/.h/.pix/.transparent).
        t = img.transparent
        if t is None:
            t = -1
        self._cmds.append(["spr", int(x), int(y), int(scale),
                           int(img.w), int(img.h), int(t), list(img.pix), int(flip)])


class RecordingLayer:
    """An off-screen layer that is BOTH a real rasterizing DeviceCanvas layer (so the
    device PANEL renders the scroll/bar exactly as today) AND a recorder of its own
    indexed draw-command stream (so the browser can replay it into an off-screen index
    buffer instead of receiving RGB565 pixels). The ONE mechanism behind both the #54
    scroll layer (make_layer/draw_layer) and the #43 cached top bar (blit_strip).

    Every draw verb forwards to the real device layer canvas AND to a _LayerRecorder. A
    stable `id` keys the deflayer; `gen` bumps once at the start of each REDRAW batch
    (the first draw after the layer was last referenced by a blit), so a layer
    pre-rendered once then blitted every frame ships its deflayer ONCE -- a periodically
    rebuilt layer (the top bar repainted on a clock tick) re-ships only on the rebuild.

    RECORDED VERBS: primitives + print + spr (recorded self-contained / full-pixel). A
    map()/spr_batch() INTO a layer is not in _VERBS -- it falls through __getattr__ to the
    real device canvas (the panel stays correct) but is NOT recorded into the layer stream;
    no shipped cart draws a tilemap into a layer (Sky Run is primitives only), so this is a
    documented follow-up."""

    _VERBS = ("cls", "pix", "line", "rect", "rectb", "circ", "circb",
              "spr", "print", "camera", "clip", "pal", "palt", "reset_state")

    def __init__(self, canvas, recorder):
        self._c = canvas               # the real DeviceCanvas backing the layer
        self._lr = _LayerRecorder()
        self.w = canvas.w
        self.h = canvas.h
        self.id = recorder.register_layer(self)
        self.gen = 0
        self._in_batch = False
        for name in RecordingLayer._VERBS:
            self._bind(name)

    def _bind(self, name):
        real = getattr(self._c, name)
        rec = getattr(self._lr, name)

        def fn(*args):
            self._begin_batch()
            r = real(*args)
            rec(*args)
            return r

        setattr(self, name, fn)

    def __getattr__(self, name):
        # Reads not set on the layer (spr_batch/map fallbacks, sizing, _buf) go to the
        # real device canvas. Never reached for the bound draw verbs (instance attrs).
        return getattr(self._c, name)

    def _begin_batch(self):
        if not self._in_batch:
            self._in_batch = True
            self.gen += 1
            self._lr.take()            # drop the previous redraw's stream

    def _end_batch(self):
        self._in_batch = False

    def layer_cmds(self):
        return self._lr.cmds()


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

    STREAM MODE (#41 30fps lever): when `recorder.record_only` is True (a browser is
    actively playing -- the loop set it), the pixel-PRODUCING ops (cls/pix-write/line/rect/
    rectb/circ/circb/spr/spr_batch/map/print) RECORD but do NOT forward to the real canvas,
    so the device skips rasterizing the panel (run_desktop also skips the flush). The cheap
    state ops (reset_state/camera/clip/pal/palt) STILL forward, so the canvas's draw state +
    camera()'s return value stay correct -- they don't touch pixels. record_only only ever
    matters while enabled is True; with no browser the Tee is the unchanged pass-through.

    It mirrors the full Canvas surface the console + carts call. Reads (`pix` with two
    args, attribute reads like `.buf`/`.w`) pass straight through to the real canvas.

    OFF-SCREEN LAYERS (#54 scroll + #43 cached top bar) ARE teed now: new_layer() mints a
    RecordingLayer (a real DeviceCanvas layer for the panel + a recorded indexed command
    stream for the browser), and blit_window_from/blit_strip record a tiny ["blit_layer",
    ...] reference (the layer's stream ships ONCE as a deflayer at serve time). This is
    what fixes a scroll cart's web view (it used to show black + trailing actors because
    the layer's draws + the window-copy bypassed the recorder via __getattr__)."""

    def __init__(self, canvas, recorder):
        self._c = canvas
        self._r = recorder
        self.w = canvas.w
        self.h = canvas.h

    # framebuffer-shaped reads the console uses (composite, sizing) pass through.
    def __getattr__(self, name):
        # Only reached for attrs not set on the Tee (e.g. `buf`, `_comp`, `sync_back`).
        # Delegate to the wrapped canvas. (new_layer/blit_window_from/blit_strip are now
        # methods on the Tee -- they record -- so they no longer fall through here.)
        return getattr(self._c, name)

    # -- off-screen layers ---------------------------------------------------
    def new_layer(self, w, h):
        # A real DeviceCanvas layer (the panel renders the scroll/bar) wrapped so its
        # draws ALSO record an indexed command stream. The RecordingLayer registers
        # itself with the recorder, so a later blit can ship its deflayer once.
        real = self._c.new_layer(w, h)
        return RecordingLayer(real, self._r)

    # A layer passed here is normally a RecordingLayer (minted by new_layer above). But a
    # layer built on the RAW DeviceCanvas BEFORE the web view bound this Tee -- e.g. the
    # console's cached top-bar strip carried across the canvas swap -- is a plain DeviceCanvas
    # with no RecordingLayer hooks. Tolerate it (blit it straight to the panel, skip recording
    # so the browser just misses it for the one frame until it's rebuilt as a RecordingLayer)
    # instead of crashing the frame on a missing `_end_batch`/`_c`.
    def blit_window_from(self, layer, cam_x=0, cam_y=0):
        cam_x = int(cam_x)
        cam_y = int(cam_y)
        rl = isinstance(layer, RecordingLayer)
        if rl:
            layer._end_batch()                   # the redraw (if any) is done
        if not self._r.record_only:
            self._c.blit_window_from(layer._c if rl else layer, cam_x, cam_y)
        if self._r.enabled and rl:
            self._r.blit_layer_window(layer, cam_x, cam_y)

    def blit_strip(self, layer, dst_x=0, dst_y=0):
        rl = isinstance(layer, RecordingLayer)
        if rl:
            layer._end_batch()
        if not self._r.record_only:
            self._c.blit_strip(layer._c if rl else layer, dst_x, dst_y)
        if self._r.enabled and rl:
            self._r.blit_layer_full(layer, dst_x, dst_y)

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
    # In STREAM MODE (recorder.record_only) these pixel-producing ops RECORD but skip the
    # `self._c.<op>()` forward, so the device doesn't rasterize the panel (run_desktop also
    # skips the flush). `record_only` is honoured only while `enabled` (a browser is live);
    # otherwise the forward always runs, identical to today.
    def cls(self, c=0):
        if not self._r.record_only:
            self._c.cls(c)
        if self._r.enabled:
            self._r.cls(c)

    def pix(self, x, y, c=None):
        if c is None:
            return self._c.pix(x, y)           # a read -> the real framebuffer
        if not self._r.record_only:
            self._c.pix(x, y, c)
        if self._r.enabled:
            self._r.pix(x, y, c)

    def line(self, x0, y0, x1, y1, c):
        if not self._r.record_only:
            self._c.line(x0, y0, x1, y1, c)
        if self._r.enabled:
            self._r.line(x0, y0, x1, y1, c)

    def rect(self, x, y, w, h, c):
        if not self._r.record_only:
            self._c.rect(x, y, w, h, c)
        if self._r.enabled:
            self._r.rect(x, y, w, h, c)

    def rectb(self, x, y, w, h, c):
        if not self._r.record_only:
            self._c.rectb(x, y, w, h, c)
        if self._r.enabled:
            self._r.rectb(x, y, w, h, c)

    def circ(self, cx, cy, r, c):
        if not self._r.record_only:
            self._c.circ(cx, cy, r, c)
        if self._r.enabled:
            self._r.circ(cx, cy, r, c)

    def circb(self, cx, cy, r, c):
        if not self._r.record_only:
            self._c.circb(cx, cy, r, c)
        if self._r.enabled:
            self._r.circb(cx, cy, r, c)

    def spr(self, img, x, y, scale=1, flip=0):
        if not self._r.record_only:
            self._c.spr(img, x, y, scale, flip)
        if self._r.enabled:
            self._r.spr(img, x, y, scale, flip)

    def spr_batch(self, sheet, items, colorkey=-1, scale=1):
        if not self._r.record_only:
            self._c.spr_batch(sheet, items, colorkey, scale)
        if self._r.enabled:
            # Expand to per-tile spr commands (the browser has no batch op). Resolve each
            # tile through the recorder's STABLE tile-image cache (not a per-call dict), so
            # a repeated tile maps to the SAME Image across frames -> the atlas dedups its
            # bitmap to ONE defspr per session, and each batch item is a 6-int spr-by-index.
            for it in items:
                tid = int(it[0])
                if tid < 0:
                    continue
                flip = it[3] if len(it) > 3 else 0
                img = self._r.batch_tile_image(sheet, tid, colorkey)
                if img is None:
                    continue
                self._r.spr(img, it[1], it[2], scale, flip)

    def map(self, tilemap, sheet, mx=0, my=0, w=None, h=None,
            sx=0, sy=0, colorkey=-1, scale=1):
        if not self._r.record_only:
            self._c.map(tilemap, sheet, mx, my, w, h, sx, sy, colorkey, scale)
        if self._r.enabled:
            # PAYLOAD DIET (#41): record ONE map op (was ~300 fat per-cell sprs). The
            # browser has the sheet + tilemap from /assets and replays the cell walk
            # itself. First sync the browser's tilemap if the cart mutated it (mset),
            # then emit the map op with the resolved (default-None) region.
            mx = int(mx)
            my = int(my)
            scale = int(scale) if int(scale) >= 1 else 1
            if w is None:
                w = tilemap.w - mx
            if h is None:
                h = tilemap.h - my
            self._r.settiles(tilemap)
            self._r.map(mx, my, int(w), int(h), int(sx), int(sy), scale, int(colorkey))

    def print(self, s, x, y, c, scale=2):
        if not self._r.record_only:
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


def frame_payload(cmds, cart_title, gen=0, perf=None):
    """The per-frame payload: the recorded draw-command list + the cart title (so the
    client refetches /assets on a cart change) + the atlas generation `gen`. The browser
    resets its sprite atlas (ATL) ONLY when `gen` changes -- lock-step with the device's
    `served` reset -- so a /assets refetch (e.g. scrolling the launcher changes cart_title)
    no longer wipes the atlas and strand sprites (the launcher unknown-growth bug, #41).

    `perf` (optional) is a tiny device-side stats dict {"heap": free KB, "pf": frames pushed
    since boot} the browser logs alongside its own render/bandwidth numbers (#41 perf log),
    so a single pasted line shows BOTH sides: device push-rate vs browser recv-rate (a gap =
    frames dropped over WiFi) and free heap. None on the host poll path / older callers."""
    return {"cmds": cmds, "cart": cart_title, "gen": gen, "audio": "", "perf": perf}


# The logical buttons a browser key/joystick maps to (mirrors the host BUTTON_NAMES);
# only forward names the console knows so a stray key can't wedge it.
BUTTON_NAMES = ("left", "right", "up", "down", "a", "b", "run", "home")


def apply_events(events, input, pointer, on_press=None, on_pan=None,
                 on_key=None, on_esc=None, on_hold=None):
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
                    # Route to on_hold when wired: the device's per-frame keyboard.poll()
                    # clears buttons (no physical key down), so a hold set HERE (in poll(),
                    # before the next keyboard.poll) is wiped before the cart reads btn().
                    # on_hold lets the loop track + re-assert it in feed_input AFTER the
                    # keyboard poll (the joystick/WASD fix). Falls back to a direct set.
                    if on_hold is not None:
                        on_hold(name, bool(ev.get("down")))
                    else:
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
# WebSocket transport (RFC 6455): the LIVE channel. Pure functions (no socket) so the
# handshake + framing are host-unit-testable; _WSConn below wires them to a real socket.
# ---------------------------------------------------------------------------

# The RFC 6455 magic GUID concatenated with the client's Sec-WebSocket-Key before the
# sha1; the base64 of that digest is the Sec-WebSocket-Accept the server echoes back.
WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


def ws_accept_key(key):
    """The Sec-WebSocket-Accept value for a client's Sec-WebSocket-Key (RFC 6455 4.2.2):
    base64(sha1(key + WS_GUID)). `key` is the raw header value (str). Returns the accept
    string. (Example: "dGhlIHNhbXBsZSBub25jZQ==" -> "s3pPLMBiTxaQ9kYGzzhZRbK+xOo=".)"""
    digest = _hashlib.sha1((key + WS_GUID).encode("utf-8")).digest()
    return _binascii.b2a_base64(digest).decode("utf-8").strip()


def ws_handshake_response(key):
    """The full HTTP/1.1 101 Switching Protocols response (bytes) that completes a
    WebSocket upgrade for the client key. No body; the socket then carries WS frames."""
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
    """Encode an UNMASKED server->client WebSocket frame (RFC 6455 5.2). `payload` is
    bytes (or str -> utf-8). opcode 0x1 = text (the default, our frame JSON), 0xA = pong.
    FIN is always set (we never fragment outbound). 7/16/64-bit length forms per the spec;
    no mask (server frames are never masked)."""
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
      * opcode is the frame opcode (0x1 text, 0x8 close, 0x9 ping, 0xA pong, ...),
        payload the UNMASKED bytes, consumed the number of bytes the frame used.
      * (None, None, 0)  -> not enough bytes yet for a complete frame (try again next read).
      * (-1, None, 0)    -> a protocol error (oversize / a server-bound frame that isn't
                            masked, which a conformant client must always mask) -> drop conn.

    Handles the common case: a single unfragmented client frame. Client frames are ALWAYS
    masked (RFC 6455 5.3), so the MASK bit must be set; the 4-byte masking key XOR-unmasks
    the payload. 126/127 extended lengths are supported; anything over WS_MAX_FRAME is a
    protocol error (we never expect a large inbound frame -- input batches are tiny)."""
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
    """The persistent WebSocket connection (one client at a time). Non-blocking reads with
    a cross-iteration read buffer + a parser that yields only COMPLETE inbound frames and
    retains the partial remainder; blocking-with-a-budget sends so a multi-KB frame can
    drain over slow WiFi. Every op is guarded -- a closed/stalled peer sets .alive False
    and the server drops it (the browser auto-reconnects)."""

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
        """Drain whatever bytes are pending on the non-blocking socket into the buffer.
        Returns False (and marks dead) on a clean peer close; True otherwise. EAGAIN (no
        data) is the normal case and just returns True with nothing appended."""
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
        """Send one UNMASKED frame, blocking with a short budget so a multi-KB frame can
        drain over slow WiFi. A send error (a stalled/closed client) drops the conn rather
        than waiting on it. Returns True if it went out."""
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
      * begin_frame()      once at the top of each frame, to start a fresh recording
                           (only when a WS client is live).
      * commit_frame()     after ws.frame(), to publish the frame's draw commands.
      * poll() / service() once per loop iteration, BETWEEN frames -- accepts new
                           connections (HTTP one-shot, or a WS upgrade promoted to the
                           persistent conn), drains the WS's queued input -> apply, and
                           PUSHES the latest committed frame down it (capped). Fully
                           non-blocking; a stalled client is dropped, never waited on.

    The `provider` is a small object the server queries for live data without holding
    any console references itself:
      provider.assets()    -> the /assets dict
      provider.frame()     -> (cmds, cart_title) for /frame and the WS push
      provider.apply(events)-> inject browser events
    """

    def __init__(self, recorder, provider, port=DEFAULT_PORT):
        self.recorder = recorder
        self.provider = provider
        self.port = port
        self.sock = None
        self.ip = None
        self._last_record_ms = 0      # ticks_ms of the last RECORDED frame (the fps cap)
        self._last_push_ms = 0        # ticks_ms of the last frame PUSHED down the WS (cap)
        self.requests = 0             # served-request counter (diag)
        self._frames_pushed = 0       # frames sent down the WS since boot (#41 perf log: the
                                      # browser diffs this to derive device push-rate vs its
                                      # own recv-rate -- a gap = frames dropped over WiFi)
        self._last_json_ms = 0        # ms spent json-encoding the last pushed frame ...
        self._last_send_ms = 0        # ... and ms in the socket send (#41 perf log: localizes
                                      # device-bound stutter -- encode vs transport vs the rest)
        # The PERSISTENT WebSocket client (one at a time). None = no browser connected ->
        # the recorder stays a pure pass-through (zero overhead). A new upgrade drops the
        # old conn (latest-wins). Liveness is keyed off this being non-None + not idle-timed.
        self._ws = None
        # SERVE-TIME defspr (#41): the atlas indices we've ALREADY shipped to the browser as
        # a ["defspr", ...]. When a served frame references an index not in here we PREPEND
        # its defspr (once) and add it; the set resets when /assets is (re)served (a page
        # load / cart change clears the browser's atlas) or when the recorder's atlas_gen
        # changes (reset_atlas). So each bitmap travels once per browser session, yet every
        # served frame is self-contained for the sprites it draws -- robust to dropped frames.
        self._served = set()
        self._served_gen = recorder.atlas_gen
        # SERVE-TIME deflayer (#54/#43, the SAME pattern as the sprite atlas above): the
        # layer ids we've ALREADY shipped as a ["deflayer", ...], mapped id -> the gen we
        # shipped. A served frame referencing a layer id whose gen differs (new layer OR a
        # redraw) re-ships its stream once. Resets on /assets (reset_served) + on a dropped
        # atlas (atlas_gen change) so a reconnecting browser / a new cart re-gets its layers.
        self._served_layers = {}

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
        """Close + forget the persistent WS client (a disconnect / latest-wins replacement /
        a stalled send). The recorder gate then falls back to pass-through next begin_frame."""
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
        gate that keeps the Tee a pure pass-through unless a browser is actually watching.
        (The WS transport re-keys this off a LIVE connection instead of a recent /frame poll:
        no per-frame handshake, so the old 'fetched within RECORD_IDLE_MS' window is replaced
        by 'a live socket'. RECORD_IDLE_MS now just reaps a half-open conn that went silent.)"""
        if self.sock is None or self._ws is None or not self._ws.alive:
            return False
        return ticks_diff(ticks_ms(), self._ws.last_recv) < RECORD_IDLE_MS

    def stream_mode(self):
        """True when the device should go HEADLESS for the web stream this frame -- i.e. a
        browser is actively connected (recording_wanted), so the loop can skip the device's
        OWN panel rasterization + flush and let the cart run logic + record cheap commands
        only, lifting the web frame rate above the panel's render+flush ceiling (#41 30fps
        lever). Ties to the SAME gate as recording -- no new toggle -- so it's identical to
        today (normal panel rendering, zero overhead) whenever no browser is connected."""
        return self.recording_wanted()

    def served_frame(self, cmds):
        """Build the command list to actually SEND for /frame: PREPEND a ["defspr", ...] for
        every spr index in `cmds` the browser hasn't received yet (then mark it served), so
        the frame is self-contained for its sprites even though the recorder no longer inlines
        defspr (and earlier defspr-carrying frames may have been dropped). The defspr pixels
        are reconstructed from the recorder's atlas (defspr_cmd). ALSO prepends a
        ["deflayer", ...] for every blit_layer whose layer id+gen the browser hasn't received
        (#54/#43 ship-once layers -- the SAME serve-time mechanism). Resets both served sets
        if the recorder's atlas was dropped (reset_atlas bumped atlas_gen) -- the browser does
        the matching reset by refetching /assets + clearing its caches on a cart change."""
        rec = self.recorder
        if rec.atlas_gen != self._served_gen:
            self._served = set()
            self._served_layers = {}
            self._served_gen = rec.atlas_gen
        prefix = None
        for c in cmds:
            if not c:
                continue
            op = c[0]
            if op == "spr":
                idx = c[1]
                if idx not in self._served:
                    d = rec.defspr_cmd(idx)
                    if d is not None:
                        if prefix is None:
                            prefix = []
                        prefix.append(d)
                        self._served.add(idx)
            elif op == "blit_layer":
                lid = c[1]
                layer = rec._layers[lid] if 0 <= lid < len(rec._layers) else None
                if layer is not None and self._served_layers.get(lid) != layer.gen:
                    d = rec.deflayer_cmd(lid)
                    if d is not None:
                        if prefix is None:
                            prefix = []
                        prefix.append(d)
                        self._served_layers[lid] = layer.gen
        if prefix is None:
            return cmds
        return prefix + cmds

    def reset_served(self):
        """Forget which defsprs + deflayers the browser has -- so the next /frame re-ships
        every sprite bitmap AND layer stream it references. Called when /assets is (re)served
        (a page load / cart change clears the browser's atlas + layer caches)."""
        self._served = set()
        self._served_layers = {}
        self._served_gen = self.recorder.atlas_gen

    def begin_frame(self):
        """Set the recorder's gate for THIS frame + start a fresh command list when a
        browser is live AND the fps cap allows. Called at the top of the loop, before
        ws.frame(). The cap (WEB_FPS_CAP) decouples the web stream from the cart: the
        panel renders full speed, but we RECORD at most one frame per WEB_FRAME_INTERVAL_MS
        -- so a 40fps cart still only feeds the ~72KB/s WiFi at ~30fps. The decision is
        made ONCE here so a frame is recorded completely or not at all; on a skipped frame
        the Tee is a pure pass-through (recorder.enabled stays False).

        STREAM MODE (#41): `record_only` (go headless) is DECOUPLED from the record cap.
        While a browser is live it is True EVERY frame -- so the loop's stream-mode enter/
        exit edge fires ONCE and the panel stays frozen. (Tying it to the cap made it flap
        True/False per frame -> the panel "resumed" on every capped frame and the enter/exit
        churn -- notice force-flush + full redraw -- ran every frame, the lag bug.) The cap
        only throttles RECORDING (enabled): a capped frame stays headless but records nothing
        (the Tee skips the panel forward on record_only and only appends on enabled, so a
        headless-not-recorded frame is a cheap no-op; the browser keeps the last recorded
        frame)."""
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

    def poll(self):
        """Run once per loop iteration, BETWEEN frames (kept named poll() for the existing
        run_desktop wiring; service() is an alias). Two non-blocking jobs, never blocking the
        render loop:
          1. ACCEPT new connections (up to POLL_MAX). Each is either a one-shot HTTP request
             (GET / , GET /assets, the legacy /frame & /input fallbacks) served + closed, or a
             WebSocket UPGRADE that becomes the PERSISTENT live conn (latest-wins: a new client
             drops the old).
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
        EAGAINs the instant nothing is pending, so the steady state (a persistent WS, no new
        conns) costs one failed accept. A WS upgrade is promoted to the persistent conn; any
        other request is a one-shot HTTP serve + close."""
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
                print("KidCode web: request error:", exc)
                try:
                    conn.close()
                except Exception:  # noqa: BLE001
                    pass
        return did

    def _dispatch(self, conn):
        """Read one request head off a freshly accepted conn and route it: a WS upgrade is
        promoted to the persistent live conn (the conn STAYS OPEN); any HTTP request is served
        and the conn closed. The read is blocking with a short bound (the request is already
        en route -- we just accepted)."""
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
        client, dropping any previous one (latest-wins). The browser's reconnect-on-close means
        a refresh cleanly replaces the old socket. On a handshake send failure, just close."""
        try:
            conn.settimeout(WEB_SEND_TIMEOUT)
        except Exception:  # noqa: BLE001
            pass
        try:
            conn.sendall(ws_handshake_response(key))
        except Exception as exc:  # noqa: BLE001 -- couldn't 101 the client: drop it
            print("KidCode web: ws handshake failed:", exc)
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
        _diag = "KidCode web: ws client connected"
        print(_diag)

    def _service_ws(self):
        """Drain the persistent WS conn's queued input frames (-> provider.apply) and PUSH the
        latest committed frame down it (capped at WEB_FPS_CAP). Drops the conn if it died
        (clean close, stalled send, or idle-timed-out). No-op when no client is connected."""
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
        # 2. Outbound: push the latest committed frame, capped (a frame may be unchanged --
        # the recorder serves the last committed one regardless, like the poll did).
        now = ticks_ms()
        if ticks_diff(now, self._last_push_ms) >= WEB_FRAME_INTERVAL_MS:
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
        (KB) + the running pushed-frame count. gc.mem_free is MicroPython-only, so it's
        guarded -- on the host (CPython tests) heap is 0 and nothing raises."""
        heap = 0
        try:
            import gc
            heap = gc.mem_free() // 1024
        except Exception:  # noqa: BLE001 -- no gc.mem_free on CPython; perf is best-effort
            pass
        # js/tx are the PREVIOUS frame's encode/send ms (set at the end of _push_frame), so
        # the snapshot is one frame stale -- negligible, and it avoids timing ourselves.
        return {"heap": heap, "pf": self._frames_pushed,
                "js": self._last_json_ms, "tx": self._last_send_ms}

    def _push_frame(self, ws):
        """Send the latest committed frame as a WS text message: the SAME frame_payload (run
        through served_frame for the serve-time defspr prepend + the atlas gen) the HTTP
        /frame path returned -- only the transport differs. Times the json-encode + the
        socket send separately (#41 perf log) so a device-bound stutter is pinned to encode
        vs transport vs the rest of the loop (total frame period = the browser's 1/dev)."""
        cmds, cart = self.provider.frame()
        cmds = self.served_frame(cmds)
        self._frames_pushed += 1
        t0 = ticks_ms()
        payload = json.dumps(frame_payload(cmds, cart, self.recorder.atlas_gen,
                                           self._perf_snapshot()))
        t1 = ticks_ms()
        ws.send(payload)
        t2 = ticks_ms()
        self._last_json_ms = ticks_diff(t1, t0)
        self._last_send_ms = ticks_diff(t2, t1)

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
        Blocking with a short bound (not non-blocking): the request is already en route, so it
        arrives in a recv or two, while a speculative/empty preconnect stalls at most
        WEB_RECV_TIMEOUT. Returns (method, path, body, raw_head) -- raw_head lets the caller
        sniff a WebSocket upgrade. (None, None, b'', b'') on an unparseable request."""
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
        """Serve a one-shot HTTP request (the page, /assets, or the legacy /frame & /input
        fallbacks) and close the conn. The LIVE channel is the WebSocket; these stay so the
        page can still load over HTTP and a poll-only client can fall back."""
        self.requests += 1
        if method == "GET" and path in ("/", "/index.html"):
            self._http_send_close(conn, http_response(200, PAGE_HTML, "text/html; charset=utf-8"))
        elif method == "GET" and path == "/assets":
            # A page load / cart change: the browser clears its atlas + refetches /assets, so
            # forget what we've shipped -> the next frame re-ships the defsprs it references.
            self.reset_served()
            self._http_send_close(conn, http_response(200, json.dumps(self.provider.assets())))
        elif path == "/frame" and method in ("GET", "POST"):
            # LEGACY poll fallback: input rides along on a POST body, then return the frame.
            if method == "POST" and body:
                try:
                    payload = json.loads(body)
                    events = (payload.get("events", []) if isinstance(payload, dict)
                              else payload)
                    if isinstance(events, list):
                        self.provider.apply(events)
                except Exception:  # noqa: BLE001 -- a bad body just yields no events
                    pass
            cmds, cart = self.provider.frame()
            cmds = self.served_frame(cmds)
            self._frames_pushed += 1
            self._http_send_close(conn, http_response(200, json.dumps(
                frame_payload(cmds, cart, self.recorder.atlas_gen, self._perf_snapshot()))))
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
            self._http_send_close(conn, http_response(200, '{"ok":true}'))
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


# ---------------------------------------------------------------------------
# The page: a self-contained replayer for the PAYLOAD-DIET protocol (#41). It is the
# matched pair of the DrawRecorder above (NOT the host tools/web_console.html, which
# stays on the old fat-spr/expanded-map format). It does the load-bearing job: fetch
# /assets (palette + font + sheet + tilemap) ONCE over HTTP, open a persistent WebSocket
# (/ws), replay the indexed draw commands each pushed frame against the KID64 palette, and
# send input UP the same socket (touch drag + on-screen joystick/A/B + WASD/arrows) -- no
# per-frame HTTP handshake. Replay must be PIXEL-IDENTICAL to the device panel:
#   defspr  -> cache the bitmap by index (ATL[index] = {w,h,t,px}).
#   spr     -> blit ATL[index] at x,y with browser-side scale/flip + colorkey/palt.
#   map     -> walk the CACHED tilemap (kept current by settiles + /assets) over the
#              CACHED sheet, mirroring the device map() cell layout (step=tile*scale,
#              cell (cx,cy) -> screen (sx+cx*step, sy+cy*step), colorkey transparent).
#   settiles-> overwrite the cached tilemap (cells/w/h) before a map op.
# On a cart change /frame.cart != assCart -> refetch /assets, which clears ATL + the
# tilemap cache, in lock-step with the device recorder's reset_atlas().
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
.pr{background:#ffec27;color:#1d2b53}
/* Debug HUD (#41): toggled with the `d` key; lightweight live stream stats. */
#hud{position:fixed;top:6px;left:6px;z-index:9;display:none;padding:6px 8px;border-radius:5px;
background:rgba(11,15,26,.82);border:1px solid #1d2b53;color:#00e436;font:12px ui-monospace;
white-space:pre;pointer-events:none}#hud b{color:#ffec27}#hud .w{color:#ff004d}</style></head><body>
<div id=hud></div>
<h1>KidCode &mdash; device <span id=s>connecting...</span> <small style="color:#5f6f9f">(press ` for stats)</small></h1>
<canvas id=cv width=320 height=240 tabindex=0></canvas>
<div id=ctl><div id=joy><div id=th></div></div>
<div><span class=b id=bb>B</span><span class=b id=ba>A</span></div></div>
<script>
var FPS=30,cv=document.getElementById("cv"),cx=cv.getContext("2d"),sEl=document.getElementById("s");
cx.imageSmoothingEnabled=false;
var W=320,H=240,PAL=null,FONT=null,ready=false,assCart=undefined,idx=null,img=null,rgba=null;
// Payload-diet caches (#41): SHEET = the cart sprite sheet (cols/rows/tile/w/h/pix from
// /assets, for map replay); TM = the cart tilemap (w/h/cells, kept current by settiles);
// ATL = the per-session sprite atlas filled by defspr and referenced by spr index.
var SHEET=null,TM=null,ATL=[],curGen=-1;
// Debug HUD (#41): toggle with `d`. Live stream stats -- browser render fps (EMA), KB of
// the last /frame payload, atlas count (defsprs cached), and an unknown-index counter
// (a spr that references an ATL slot with no defspr -- this would've caught the dropped-
// defspr bug instantly). All cheap; the overlay only redraws when shown.
var HUD={on:false,fps:0,kb:0,unknown:0,el:document.getElementById("hud"),last:0};
// Periodic perf LOG (#41): one console.log line every PERF_MS with the client's recv +
// render fps, bandwidth, avg/peak payload, AND the device's push-rate + free heap (from
// f.perf). A single pasted line tells device-bound (low dev fps, small payloads, bw under
// ~72KB/s) from WiFi-bound (dev fps >> recv fps, bw pinned near ~72KB/s) stutter.
var PERF_MS=2000,PERF={f:0,b:0,pk:0,t:0,dh:0,pf:0,lpf:0,js:0,tx:0};
function plog(){var now=(window.performance&&performance.now)?performance.now():Date.now();
if(!PERF.t){PERF.t=now;PERF.lpf=PERF.pf;return;}var dt=(now-PERF.t)/1000;if(dt<=0)return;
console.log("[kidcode] "+(assCart||"?")+" | recv "+(PERF.f/dt).toFixed(1)+" render "+HUD.fps.toFixed(1)
+" dev "+((PERF.pf-PERF.lpf)/dt).toFixed(1)+" fps (js "+PERF.js+" tx "+PERF.tx+"ms) | bw "
+(PERF.b/dt/1024).toFixed(1)+" KB/s avg "+(PERF.f?(PERF.b/PERF.f/1024):0).toFixed(2)+" peak "
+(PERF.pk/1024).toFixed(2)+" KB | heap "+PERF.dh+" KB | unknown "+HUD.unknown);
PERF.f=0;PERF.b=0;PERF.pk=0;PERF.t=now;PERF.lpf=PERF.pf;}
function alloc(){cv.width=W;cv.height=H;cx=cv.getContext("2d");cx.imageSmoothingEnabled=false;
idx=new Uint8Array(W*H);img=cx.createImageData(W,H);rgba=img.data;}
function getA(){return fetch("/assets").then(function(r){return r.json();}).then(function(a){
W=a.w;H=a.h;PAL=a.palette;FONT=a.font;assCart=a.cart;SHEET=a.sheet||null;
TM=a.tilemap?{w:a.tilemap.w,h:a.tilemap.h,cells:a.tilemap.cells.slice()}:null;
alloc();ready=true;});}  // NB: do NOT clear ATL here -- the atlas resets on `gen` change
                         // (see df), so a /assets refetch on cart change keeps the atlas.
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
// Blit a bitmap (raw pixels px, sw x sh, transparent index t) at x,y with scale+flip.
// Mirrors the device DeviceCanvas.spr / host Canvas.spr exactly (camera/clip/pal/palt via
// put/fr). Used by both spr (atlas lookup) and map (per-cell sheet slice).
function blt(px,sw,sh,t,x,y,sc,fl){x|=0;y|=0;sc|=0;fl|=0;var fx=fl&1,fy=(fl>>1)&1;
for(var yy=0;yy<sh;yy++){var ry=fy?sh-1-yy:yy,bs=ry*sw;for(var xx=0;xx<sw;xx++){var rx=fx?sw-1-xx:xx,
p=px[bs+rx];if(p===t||p<0||pt[p&63])continue;if(sc<=1)put(x+xx,y+yy,p);else fr(x+xx*sc,y+yy*sc,sc,sc,p);}}}
// spr by atlas index: look up the bitmap defspr shipped, blit it. Unknown index = no-op
// (and bump the HUD's unknown-index counter -- a missing defspr is the dropped-frame bug).
function sp(ix,x,y,sc,fl){var a=ATL[ix];if(!a){HUD.unknown++;return;}blt(a.px,a.w,a.h,a.t,x,y,sc,fl);}
// map(): walk the cached tilemap region (mx,my .. +w,+h) over the cached sheet, drawing
// each non-empty cell's tile at (sx+cx*step, sy+cy*step), colorkey transparent. Mirrors
// the device map() cell layout (step = tile*scale; tile origin row-major in the sheet).
function mp(mx,my,w,h,sx,sy,sc,ck){if(!SHEET||!TM)return;sc=sc<1?1:sc;
var tile=SHEET.tile,step=tile*sc,cols=SHEET.cols,sw=SHEET.w,spx=SHEET.pix,tw=TM.w,th=TM.h,cells=TM.cells;
for(var cy=0;cy<h;cy++){var ty=my+cy;for(var cx=0;cx<w;cx++){var gx=mx+cx;
var tid=(gx>=0&&gx<tw&&ty>=0&&ty<th)?cells[ty*tw+gx]-1:-1;if(tid<0)continue;
var ox=(tid%cols)*tile,oy=((tid/cols)|0)*tile,dx=sx+cx*step,dy=sy+cy*step;
for(var ly=0;ly<tile;ly++){var srow=(oy+ly)*sw+ox;for(var lx=0;lx<tile;lx++){var p=spx[srow+lx];
if(p===ck||p<0||pt[p&63])continue;if(sc<=1)put(dx+lx,dy+ly,p);else fr(dx+lx*sc,dy+ly*sc,sc,sc,p);}}}}}
// settiles: overwrite the cached tilemap (a cart mutated it via mset).
function st(w,h,cells){TM={w:w,h:h,cells:cells};}
// OFF-SCREEN LAYERS (#54 scroll + #43 cached top bar): ONE mechanism. deflayer (re)builds
// an off-screen index buffer by REPLAYING the layer's recorded stream into it (reusing
// rep() -- pixel-identical to the main canvas); blit_layer copies a window (draw_layer /
// scroll) or the full layer (blit_strip / bar) into idx. The stream ships ONCE (deflayer
// rides only the frame its gen changed); LAY keeps each built buffer by id.
var LAY={};
function dfl(id,lw,lh,cmds){var sI=idx,sW=W,sH=H,sX=caX,sY=caY,s0=cl0,s1=cm0,s2=cl1,s3=cm1,sm=pm,spt=pt;
var buf=new Uint8Array(lw*lh);idx=buf;W=lw;H=lh;rs();rep(cmds);
idx=sI;W=sW;H=sH;caX=sX;caY=sY;cl0=s0;cm0=s1;cl1=s2;cm1=s3;pm=sm;pt=spt;LAY[id]={w:lw,h:lh,buf:buf};}
// Window-copy the W x H screen slice at (cx,cy), opaque (mirrors blit_window_from; clamped
// to the source). Opaque + full-screen, so it also clears last frame -> no actor trails.
function blw(L,cx,cy){cx=cx<0?0:cx|0;cy=cy<0?0:cy|0;var dw=W,dh=H,sw=L.w,src=L.buf;
if(sw<=0||dw<=0||dh<=0)return;if(cx+dw>sw)dw=sw-cx;if(dw<=0)return;var sr=(src.length/sw)|0;
if(cy+dh>sr)dh=sr-cy;if(dh<=0)return;for(var r=0;r<dh;r++){var d0=r*W,o0=(cy+r)*sw+cx;
for(var x=0;x<dw;x++)idx[d0+x]=src[o0+x];}}
// Full-copy the whole layer at (dx,dy), opaque, clamped per row/col (mirrors blit_strip).
function blf(L,dx,dy){dx|=0;dy|=0;var dw=W,dh=H,sw=L.w,sh=L.h,src=L.buf;if(sw<=0||dw<=0||dh<=0)return;
for(var r=0;r<sh;r++){var ty=dy+r;if(ty<0||ty>=dh)continue;var cw=sw,x0=0,t0=dx,o0=r*sw;
if(t0<0){x0=-t0;cw+=t0;t0=0;}if(t0+cw>dw)cw=dw-t0;if(cw<=0)continue;var d0=ty*W+t0;
for(var x=0;x<cw;x++)idx[d0+x]=src[o0+x0+x];}}
function bl(c){var L=LAY[c[1]];if(!L)return;if(c.length>4&&c[4]==="full")blf(L,c[2],c[3]);else blw(L,c[2],c[3]);}
function tx(s,x,y,c){if(!FONT)return;var X=x|0;y|=0;var fi=FONT.first,gw=FONT.w,g=FONT.glyphs,n=g.length;
for(var k=0;k<s.length;k++){var gi=s.charCodeAt(k)-fi,co=(gi>=0&&gi<n)?g[gi]:g[0];
for(var j=0;j<gw;j++){var bt=co[j],py=y;while(bt){if(bt&1)put(X+j,py,c);bt>>=1;py++;}}X+=gw;}}
function rep(cs){for(var i=0;i<cs.length;i++){var c=cs[i],o=c[0];
if(o=="cls")idx.fill(pm[c[1]&63]);else if(o=="pix")put(c[1],c[2],c[3]);
else if(o=="line")ln(c[1],c[2],c[3],c[4],c[5]);else if(o=="rect")fr(c[1],c[2],c[3],c[4],c[5]);
else if(o=="rectb")rb(c[1],c[2],c[3],c[4],c[5]);else if(o=="circ")ci(c[1],c[2],c[3],c[4]);
else if(o=="circb")cb(c[1],c[2],c[3],c[4]);
else if(o=="defspr")ATL[c[1]]={w:c[2],h:c[3],t:c[4],px:c[5]};
// spr has TWO shapes: the main stream's atlas form ["spr",idx,x,y,sc,fl] (<=6 fields) and
// a layer stream's SELF-CONTAINED full-pixel form ["spr",x,y,sc,w,h,t,pix,fl] (a pix array
// at c[7]). Branch on the pix array so a layer's icon sprites replay without the atlas.
else if(o=="spr"){if(c.length>7&&c[7]&&c[7].length!==undefined)blt(c[7],c[4],c[5],c[6],c[1],c[2],c[3],c[8]||0);else sp(c[1],c[2],c[3],c[4],c[5]||0);}
else if(o=="deflayer")dfl(c[1],c[2],c[3],c[4]);else if(o=="blit_layer")bl(c);
else if(o=="settiles")st(c[1],c[2],c[3]);else if(o=="map")mp(c[1],c[2],c[3],c[4],c[5],c[6],c[7],c[8]);
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
var ok=false;
function pv(){return[(pH.ArrowRight?1:0)-(pH.ArrowLeft?1:0),(pH.ArrowDown?1:0)-(pH.ArrowUp?1:0)];}
// df(): render ONE frame payload (the JSON the WebSocket pushes). Atlas reset is driven by
// the device's `gen` (lock-step with its served reset), NOT by the cart change -- so scrolling
// the launcher (which changes cart_title -> /assets refetch) no longer wipes ATL and strands
// sprites (the unknown-growth bug, #41).
function df(f){if(f.perf){PERF.dh=f.perf.heap;PERF.pf=f.perf.pf;PERF.js=f.perf.js;PERF.tx=f.perf.tx;}
if(f.gen!==curGen){curGen=f.gen;ATL=[];LAY={};HUD.unknown=0;}
if(f.cart!==assCart){assCart=f.cart;getA().catch(function(){});}rep(f.cmds||[]);blit();
var t=(window.performance&&performance.now)?performance.now():Date.now();if(HUD.last){var inst=1000/Math.max(1,t-HUD.last);
HUD.fps=HUD.fps?HUD.fps+(inst-HUD.fps)*0.2:inst;}HUD.last=t;if(HUD.on)drawHud();
if(!ok){ok=true;sEl.textContent="live";sEl.style.color="#00e436";}}
// THE LIVE CHANNEL (#41 transport swap): a persistent WebSocket. Frames PUSH down
// (ws.onmessage -> df), input pushes up (flush() sends our queued events as one text
// message per tick). No per-frame handshake -> smoother + lower-latency than the old poll.
var ws=null,wsOpen=false,reconn=null;
function flush(){if(!wsOpen)return;var v=pv();if(v[0]||v[1])send({type:"pan",dx:v[0],dy:v[1]});
if(!q.length)return;var b=q;q=[];try{ws.send(JSON.stringify({events:b}));}catch(e){}}
function connect(){if(reconn){clearTimeout(reconn);reconn=null;}
try{ws=new WebSocket((location.protocol=="https:"?"wss://":"ws://")+location.host+"/ws");}
catch(e){retry();return;}
ws.onopen=function(){wsOpen=true;ok=false;sEl.textContent="live";sEl.style.color="#00e436";};
ws.onmessage=function(ev){var n=ev.data.length;HUD.kb=n/1024;PERF.f++;PERF.b+=n;if(n>PERF.pk)PERF.pk=n;
var f;try{f=JSON.parse(ev.data);}catch(e){return;}df(f);};
ws.onclose=function(){wsOpen=false;retry();};
ws.onerror=function(){try{ws.close();}catch(e){}};}
// Reconnect with a small fixed backoff (the device drops the old socket when a new one
// connects, so a refresh / WiFi blip just reconnects). assCart is left as-is so the atlas
// survives a reconnect; the device re-ships defsprs to a fresh socket anyway (reset_served).
function retry(){wsOpen=false;sEl.textContent="reconnecting...";sEl.style.color="#ff004d";
if(reconn)return;reconn=setTimeout(function(){reconn=null;connect();},800);}
// HUD render: cheap textContent update, only when shown. atlas count = defined ATL slots.
function drawHud(){var n=0;for(var i=0;i<ATL.length;i++)if(ATL[i])n++;
var u=HUD.unknown?'<span class=w>'+HUD.unknown+'</span>':'0';
HUD.el.innerHTML="fps <b>"+HUD.fps.toFixed(1)+"</b>   "+HUD.kb.toFixed(2)+" KB/f<br>atlas <b>"+n+"</b>   unknown "+u;}
// Toggle the debug HUD with the backtick key, at the WINDOW level so it works whether or
// not the canvas has focus (and never steals a WASD/arrow movement key from the cart).
window.addEventListener("keydown",function(e){if(e.key==="`"||e.key==="~"){HUD.on=!HUD.on;
HUD.el.style.display=HUD.on?"block":"none";if(HUD.on)drawHud();e.preventDefault();}});
// Fetch /assets once over HTTP, then open the WebSocket; pump queued input up on a timer.
getA().then(function(){connect();setInterval(flush,Math.round(1000/FPS));setInterval(plog,PERF_MS);}).catch(function(){
sEl.textContent="no assets";sEl.style.color="#ff004d";});cv.focus();
</script></body></html>"""
