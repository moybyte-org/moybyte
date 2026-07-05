# Moybyte web view -- the SHARED, transport-agnostic core of the console web view
# (#41/#22). ONE source of truth for the draw-command recorder, the payload builders,
# the serve-time defspr/deflayer logic, the browser replayer JS, and the protocol
# constants -- used by BOTH the HOST web console (tools/web_console.py, an http.server
# transport) and the DEVICE web view (firmware/.../modules/moy_webserver.py, a
# MicroPython socket + WebSocket transport). The two transports are thin adapters over
# this module; everything below the socket lives here so the two can't drift.
#
# PORTABLE SUBSET (imports cleanly on CPython AND MicroPython): this module uses ONLY
# `json` (with a ujson fallback) + plain Python. NO sockets, NO http.server, NO
# threading/asyncio, NO hashlib/base64 at module top. The WebSocket handshake (sha1 +
# base64) stays in the DEVICE transport, not here. Host-only helpers (CommandCanvas,
# replay_to_canvas) import runtime.font / runtime.canvas LAZILY, inside the methods
# that need them, so the module body stays device-safe (the device has no `runtime`
# package -- it freezes this file as a top-level `web_view` module).
#
# THE PROTOCOL (draw commands, NOT pixels -- a hard device constraint): WiFi on the
# device is ~72KB/s, so streaming the raw 320x240 RGB565 framebuffer (153KB/frame) is
# unplayable. Instead the cart's per-frame draw calls are RECORDED (cls/rect/spr/print/
# map/...), shipped as a compact JSON command list, and the browser REPLAYS them onto a
# <canvas> using the MOY64 palette + the cart's sheet (both from /assets). Bitmaps ship
# ONCE (defspr) then reference by index (spr); a map() is ONE op the browser replays from
# its cached tilemap; off-screen layers ship their stream ONCE (deflayer) then blit_layer.
#
# HOST vs DEVICE sprites -- the ONE genuine format difference: the device's make_api reuses
# one Image per (tile, colorkey) across frames, so id(img) is a STABLE key for a unique
# bitmap and `spr` records a tiny atlas index (defspr ships the pixels once, at serve time).
# The HOST rebuilds tile Images every frame (unstable id) and composites the whole game
# frame as a fresh full-frame bitmap, so the host records SELF-CONTAINED sprs (pixels
# inline) via DrawRecorder.self_contained. The unified browser page + replay handle BOTH
# forms (atlas by index vs self-contained by pixel array), and ServedState only prepends a
# defspr for the atlas form -- so both consoles share this module unchanged.

try:
    import ujson as json
except Exception:  # noqa: BLE001 -- host / CPython
    import json


# ---------------------------------------------------------------------------
# Protocol constants (shared by both transports + the recorder + the serve logic).
# ---------------------------------------------------------------------------

# Web-stream frame-rate cap (#41 payload diet). The device panel runs as fast as it can,
# but a browser over the ~72KB/s WiFi can't consume that. So RECORD at most one frame per
# WEB_FRAME_INTERVAL_MS; the browser always gets the LAST fully-recorded frame. 60 so a push
# happens EVERY loop tick under stream mode (at 30 the gate quantised pushes to ~21fps).
WEB_FPS_CAP = 60
WEB_FRAME_INTERVAL_MS = 1000 // WEB_FPS_CAP

# Bandwidth cap (#41): the fps cap alone let a HEAVY screen saturate WiFi. The push interval
# is ALSO floored by payload size so a heavy frame self-throttles (never faster than
# WEB_MAX_BYTES_PER_SEC); a light game frame stays fps-cap-bound. 80KB/s leaves headroom
# under the ~100KB/s measured ceiling so the TX buffer never backs up.
WEB_MAX_BYTES_PER_SEC = 80000

# Defensive cap on the sprite atlas. Keyed by id(img); a screen that makes FRESH Image
# objects every frame would grow it unbounded, so past this many entries reset (bump gen,
# browser re-ships). Normal carts never hit it.
MAX_ATLAS = 512

# Serve-time defspr SPREAD (#41): a burst of new sprites (a wave of enemies) would prepend
# all their bitmaps to ONE frame -- a fat frame that stalls the WiFi send. Cap the defspr
# bytes prepended per frame; the rest ride the next frames (a just-appeared sprite no-ops
# for a frame or two -- a brief flicker, far better than a freeze). ~1200 bytes ~= 2 tiles.
MAX_DEFSPR_BYTES_PER_FRAME = 1200


# --- paint-image (#63 Fold 3/4) compact wire encoding -----------------------
# A paint image (a big MOY64 index bitmap, images/*.moyimg) recorded via spr() must NOT ship
# as a self-contained spr's JSON int array (up to 76,800 ints ~= 1MB -- the reason sakura's
# layer wouldn't load in the browser).
#
# NAMED (the normal path): image('bg') tags the Image with ._name, so spr() records a tiny
# ["imgref", x, y, name] and the PIXELS ride /assets ONCE (browser-cached, index bytes base64'd
# -- like the sheet + tilemap), NEVER the per-frame stream. This is the Fold 4 fix: shipping the
# ~110KB blob inline (below) re-json.dumps'd it every frame it rode (~1.3s on the ESP32) and
# starved the defspr budget, so a cart's sprites (sakura's petals) never got their bitmaps.
#
# NAMELESS (fallback): a paint Image with no asset name (built ad-hoc, not via image('name'))
# has no /assets entry to reference, so it ships inline as ["img", x, y, w, h, b64] -- base64 of
# the raw index bytes, shipped ONCE inside the layer's deflayer. The browser atob()s it and blits
# index->palette (no JS inflate: base64 of the RAW indices, not the zlib bytes, keeps the replay
# synchronous + dependency-free).


def _paint_cmd(img, x, y):
    """The compact wire form for a 1:1 (scale 1, no flip) paint-image blit. A NAMED paint image
    (image('bg') tags img._name) ships a tiny ["imgref", x, y, name] -- its pixels ride /assets
    ONCE. A NAMELESS paint image falls back to the inline ["img", x, y, w, h, b64]. Shared by
    DrawRecorder.spr + _LayerRecorder.spr so the two wire forms can't drift."""
    name = getattr(img, "_name", None)
    if name is not None:
        return ["imgref", int(x), int(y), name]
    return ["img", int(x), int(y), int(img.w), int(img.h), _b64_indices(img.pix)]


def _b64_indices(pix):
    """Base64-encode a paint image's raw MOY64 index bytes for the ["img", ...] command.
    Lazy binascii import (ubinascii on the device) so the module body stays import-free."""
    try:
        import ubinascii as _binascii
    except Exception:  # noqa: BLE001 -- host / CPython
        import binascii as _binascii
    return _binascii.b2a_base64(bytes(pix)).decode("ascii").strip()


def _unb64_indices(b64):
    """Decode an ["img", ...] command's base64 back to raw index bytes (the replay twin)."""
    try:
        import ubinascii as _binascii
    except Exception:  # noqa: BLE001 -- host / CPython
        import binascii as _binascii
    return _binascii.a2b_base64(b64)


def _is_paint_image(img):
    """True for a paint-image asset Image (image('name'), tagged _paint). Used by the recorder
    to pick the compact ["img", ...] wire form over a fat self-contained/atlas spr."""
    return bool(getattr(img, "_paint", False))


# ---------------------------------------------------------------------------
# petme128 8x8 font (host == device): the SAME glyphs runtime/font.py ships, baked here
# as a hex blob so the device (whose panel text uses framebuf's own font) can still hand
# the browser the petme128 glyphs the page renders `print` with. 96 glyphs (ASCII
# 0x20..0x7f), 8 column-bytes each, LSB = top row -- byte-identical to font._FONT.
# ---------------------------------------------------------------------------

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
_GLYPHS = None


def _font_glyphs():
    """The petme128 glyphs as a list of 8-column-byte lists (the /assets JSON shape the
    browser replayer reads). Baked from the hex blob so it needs no runtime.font on the
    device; byte-identical to tools/web_console's old font_glyphs() (verified equal)."""
    global _GLYPHS
    if _GLYPHS is None:
        blob = bytes(bytearray.fromhex(_FONT_HEX))
        n = len(blob) // FONT_W
        _GLYPHS = [list(blob[i * FONT_W:(i + 1) * FONT_W]) for i in range(n)]
    return _GLYPHS


# The logical buttons a browser key/joystick maps to (mirrors the host BUTTON_NAMES); only
# forward names the console knows so a stray key can't wedge it.
BUTTON_NAMES = ("left", "right", "up", "down", "a", "b", "run", "home")


# ---------------------------------------------------------------------------
# The recorder: a draw-command list. The command format is the SINGLE source of truth for
# both the browser JS replayer (PAGE_HTML) and the Python cross-check (replay_to_canvas).
# ---------------------------------------------------------------------------


class DrawRecorder:
    """Records draw calls into a per-frame, JSON-serializable command list.

    Two consumers:
      * DEVICE: a TeeCanvas forwards every draw call here (in addition to the real
        DeviceCanvas) ONLY while `enabled` is True, so a no-browser frame costs nothing.
        `spr` records a tiny atlas index; the pixels ship once as a defspr at SERVE time
        (ServedState). `map` records ONE op the browser replays from its cached tilemap.
      * HOST: the record-only CommandCanvas draws straight into a recorder with
        `self_contained = True`, so `spr` embeds the pixels inline (the host rebuilds tile
        Images every frame -- unstable id -- and composites the whole game frame as a fresh
        bitmap, neither of which the id-keyed atlas can dedup). CommandCanvas keeps its own
        map()/spr_batch() expansion; this recorder just holds the shared command format.

    The sprite atlas {id(img): index} is keyed by id(img) -- the device's make_api reuses one
    Image per (tile, colorkey), so id is a STABLE key for a unique bitmap; the recorder also
    holds a reference to each img (self._atlas_keep) so the Image can't be GC'd and its id
    reused, AND so defspr_cmd can reconstruct the pixels at serve time. reset_atlas() drops
    the atlas + tilemap snapshot + layers when the cart/sheet changes."""

    def __init__(self, w, h):
        self.w = w
        self.h = h
        self.enabled = False
        # STREAM MODE (#41): when True the TeeCanvas RECORDS commands but does NOT forward
        # draws to the real DeviceCanvas (no rasterization), so the cart can outrun the
        # panel's render+flush ceiling. Ignored when disabled (no browser -> no streaming).
        self.record_only = False
        # SELF-CONTAINED spr (HOST): when True `spr` embeds the pixels inline instead of
        # using the id-keyed atlas + serve-time defspr. False = the device atlas path.
        self.self_contained = False
        self._cmds = []
        self._frame = []        # the last COMPLETE frame handed to the server
        # Sprite atlas: id(img) -> dense index. _atlas_keep holds the Image objects.
        self._atlas = {}
        self._atlas_keep = []
        # Atlas generation: bumped by reset_atlas() so the serve logic notices the atlas was
        # dropped (cart/sheet change) and re-ships every defspr (its `served` set resets).
        self.atlas_gen = 0
        # Tilemap-change detection (for map()): the cells snapshot + the .gen counter last
        # shipped to the browser, so an unchanged map doesn't re-ship settiles.
        self._tiles_cells = None
        self._tiles_gen = None
        # spr_batch tile-image cache: (tid, colorkey) -> Image, so a batch tile resolves to a
        # STABLE Image across frames. Rebuilt when the sheet's paint gen changes.
        self._batch_imgs = {}
        self._batch_gen = None
        # OFF-SCREEN LAYERS (#54 scroll + #43 cached top bar): RecordingLayers minted via the
        # Tee/CommandCanvas, indexed by their dense id. Dropped on reset_atlas (cart change),
        # in lock-step with the atlas_gen the serve logic keys off.
        self._layers = []

    # -- frame handoff -------------------------------------------------------
    def begin(self):
        """Start recording a fresh frame (drop a partial one defensively). Reset the atlas
        if it has grown past MAX_ATLAS (a screen with unstable sprite ids) so it can't leak."""
        if len(self._atlas_keep) > MAX_ATLAS:
            self.reset_atlas()
        self._cmds = []

    def commit(self):
        """Finish the frame: the accumulated commands become the served frame."""
        self._frame = self._cmds
        self._cmds = []

    def frame(self):
        """The last committed frame's command list."""
        return self._frame

    def reset_atlas(self):
        """Drop the sprite atlas + tilemap snapshot + layers. Called when the open cart /
        sheet / tilemap changes so a new cart's bitmaps start at index 0. Bumps atlas_gen so
        the serve logic drops its `served` set and re-ships every defspr."""
        self._atlas = {}
        self._atlas_keep = []
        self._tiles_cells = None
        self._tiles_gen = None
        self._batch_imgs = {}
        self._batch_gen = None
        self._layers = []
        self.atlas_gen += 1

    def batch_tile_image(self, sheet, tid, colorkey):
        """Resolve a sheet tile to a STABLE Image (reused across frames) for spr_batch, so
        the atlas dedups it. Rebuilds the cache when the sheet's paint gen changes."""
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
        # img is an Image / _SheetSprite (.w/.h/.pix/.transparent); ids are already resolved
        # to pixels by the time the canvas sees it.
        # PAINT-IMAGE fast wire (#63 Fold 3/4): a big MOY64 index bitmap placed 1:1 ships as a
        # compact ["imgref", x, y, name] (named -> pixels ride /assets once) or the inline
        # ["img", x, y, w, h, b64] fallback -- BEFORE either the self-contained or atlas path
        # (both would balloon it to ~1MB).
        if _is_paint_image(img) and int(scale) == 1 and int(flip) == 0:
            self._cmds.append(_paint_cmd(img, x, y))
            return
        if self.self_contained:
            # HOST record-only: embed the pixels (the layer-stream / self-contained shape,
            # ["spr", x, y, scale, w, h, t, pix, flip] -- the unified page + replay handle
            # it). No atlas: host tile Images aren't stable-id and the game-viewport blit is
            # a fresh full-frame bitmap every frame, so there's nothing to dedup.
            t = img.transparent
            if t is None:
                t = -1
            self._cmds.append(["spr", int(x), int(y), int(scale),
                               int(img.w), int(img.h), int(t), list(img.pix), int(flip)])
            return
        # DEVICE atlas path: ALWAYS record a tiny ["spr", index, x, y, scale, flip] and just
        # ENSURE the bitmap is in the atlas. The ["defspr", ...] that carries the pixels is
        # injected by ServedState at SERVE time, once per browser session, so a dropped frame
        # can never strand a spr without its bitmap.
        key = id(img)
        idx = self._atlas.get(key)
        if idx is None:
            idx = len(self._atlas_keep)
            self._atlas[key] = idx
            self._atlas_keep.append(img)        # hold a ref: id() can't be reused AND
                                                # defspr_cmd can reconstruct the pixels later
        self._cmds.append(["spr", idx, int(x), int(y), int(scale), int(flip)])

    def defspr_cmd(self, idx):
        """Reconstruct ["defspr", idx, w, h, t, pix] for an atlas index from the held Image
        (self._atlas_keep[idx]). ServedState prepends this at SERVE time the first time a
        served frame references the index. None for an out-of-range index."""
        if idx < 0 or idx >= len(self._atlas_keep):
            return None
        img = self._atlas_keep[idx]
        t = img.transparent
        if t is None:
            t = -1
        return ["defspr", int(idx), int(img.w), int(img.h), int(t), list(img.pix)]

    def settiles(self, tilemap):
        """Sync the browser's cached tilemap when it has CHANGED since last shipped, as a
        ["settiles", w, h, cells] command. Detects a change via the TileMap.gen counter
        (bumped on every mset) when present, else a cells-snapshot compare. Returns True if
        it shipped."""
        gen = getattr(tilemap, "gen", None)
        cells = tilemap.cells
        if gen is not None:
            if gen == self._tiles_gen:
                return False
            self._tiles_gen = gen
        else:
            if self._tiles_cells is not None and self._tiles_cells == cells:
                return False
        snap = list(cells)
        self._tiles_cells = list(cells)
        self._cmds.append(["settiles", int(tilemap.w), int(tilemap.h), snap])
        return True

    def map(self, mx, my, w, h, sx, sy, scale, colorkey):
        # PAYLOAD DIET (#41): ONE ["map", ...] op. The browser already has the sheet +
        # tilemap from /assets (kept in sync via settiles), so it replays the cell walk itself.
        self._cmds.append(["map", int(mx), int(my), int(w), int(h),
                           int(sx), int(sy), int(scale), int(colorkey)])

    def print(self, s, x, y, c):
        self._cmds.append(["print", str(s), int(x), int(y), c & 63])

    # -- off-screen layers (#54 scroll + #43 cached top bar) -----------------
    def register_layer(self, layer):
        """Assign a RecordingLayer the next dense layer id and remember it. Returns the id."""
        lid = len(self._layers)
        self._layers.append(layer)
        return lid

    def _ensure_registered(self, layer):
        """Self-heal a layer's id before it's referenced. reset_atlas clears _layers, so a new
        cart's layers re-register from id 0 -- but a LONG-LIVED layer (the console's cached
        top-bar strip, reused across carts) keeps a STALE id that now COLLIDES with the new
        cart's freshly-registered scroll layer (also id 0). Re-register an orphan so its id is
        current + unique. Idempotent -- a correctly-registered layer is left untouched."""
        lid = layer.id
        if 0 <= lid < len(self._layers) and self._layers[lid] is layer:
            return
        layer.id = len(self._layers)
        self._layers.append(layer)

    def blit_layer_window(self, layer, cam_x, cam_y):
        """draw_layer's window-copy -> ["blit_layer", id, cam_x, cam_y] (clamped >=0)."""
        self._ensure_registered(layer)
        self._cmds.append(["blit_layer", int(layer.id),
                           cam_x if cam_x > 0 else 0, cam_y if cam_y > 0 else 0])

    def blit_layer_full(self, layer, dst_x, dst_y):
        """blit_strip's full-copy -> ["blit_layer", id, dst_x, dst_y, "full"]."""
        self._ensure_registered(layer)
        self._cmds.append(["blit_layer", int(layer.id), int(dst_x), int(dst_y), "full"])

    def deflayer_cmd(self, idx):
        """Reconstruct ["deflayer", id, w, h, cmds] for a registered layer -- ServedState
        prepends it at SERVE time the first time a served frame references the layer (and
        re-ships if its gen changed). None for an out-of-range/dropped id."""
        if idx < 0 or idx >= len(self._layers):
            return None
        layer = self._layers[idx]
        return ["deflayer", int(idx), int(layer.w), int(layer.h),
                list(layer.layer_cmds())]


class _LayerRecorder:
    """Records ONE off-screen layer's indexed draw-command stream. Primitives mirror the main
    DrawRecorder 1:1 (palette indices), but `spr` is recorded SELF-CONTAINED with its raw
    pixels (["spr", x, y, scale, w, h, t, pix, flip]) rather than via the main atlas: a
    layer's stream ships as ONE deflayer, so it must carry everything it needs with no atlas
    coupling."""

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
        # `scale` is accepted but IGNORED (the browser renders petme128 at a fixed scale). It
        # MUST be in the signature: RecordingLayer forwards a draw verb's full arg list, and
        # the cached top bar prints its clock with a scale arg.
        self._cmds.append(["print", str(s), int(x), int(y), c & 63])

    def spr(self, img, x, y, scale=1, flip=0):
        # A paint-image background baked into this layer (sakura's spr(bg, 0, 0)) ships as a
        # compact ["imgref", x, y, name] (Fold 4: pixels ride /assets once) or the inline
        # ["img", ...] fallback for a nameless paint image -- the whole point of #63: the layer's
        # deflayer is a tiny reference (or a one-time blob), not a 32k-command ~1MB stream.
        # Everything else is a SELF-CONTAINED full-pixel spr so the layer stream needs no atlas.
        if _is_paint_image(img) and int(scale) == 1 and int(flip) == 0:
            self._cmds.append(_paint_cmd(img, x, y))
            return
        t = img.transparent
        if t is None:
            t = -1
        self._cmds.append(["spr", int(x), int(y), int(scale),
                           int(img.w), int(img.h), int(t), list(img.pix), int(flip)])


class RecordingLayer:
    """An off-screen layer that is BOTH a real rasterizing canvas (so the DEVICE panel / the
    HOST cross-check render the scroll/bar exactly) AND a recorder of its own indexed
    draw-command stream (so the browser replays it into an off-screen index buffer instead of
    receiving finished pixels). The ONE mechanism behind both the #54 scroll layer and the
    #43 cached top bar.

    Every draw verb forwards to the real layer canvas AND to a _LayerRecorder. A stable `id`
    keys the deflayer; `gen` bumps once at the start of each REDRAW batch (the first draw
    after the layer was last referenced by a blit), so a layer pre-rendered once then blitted
    every frame ships its deflayer ONCE -- a periodically rebuilt layer (the top bar) re-ships
    only on the rebuild.

    RECORDED VERBS: primitives + print + spr (self-contained). map()/spr_batch() INTO a layer
    fall through __getattr__ to the real canvas (panel/host stays correct) but are NOT recorded
    -- no shipped cart draws a tilemap into a layer (Sky Run is primitives only)."""

    _VERBS = ("cls", "pix", "line", "rect", "rectb", "circ", "circb",
              "spr", "print", "camera", "clip", "pal", "palt", "reset_state")

    def __init__(self, canvas, recorder):
        self._c = canvas               # the real backing canvas (DeviceCanvas or host Canvas)
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
        # Reads not set on the layer (spr_batch/map fallbacks, sizing, buf) go to the real
        # canvas. Never reached for the bound draw verbs (instance attrs).
        return getattr(self._c, name)

    def spr_tile(self, sheet, tile, x, y, colorkey=-1, scale=1, flip=0):
        # A layer is pre-rendered once, so there's no per-frame batching to win here:
        # resolve the tile to an Image and record it through the layer's own (bound,
        # recording) spr -- keeps the layer draw IMMEDIATE and its stream per-spr (#63).
        img = sheet.tile_image(int(tile), colorkey)
        if img is not None:
            self.spr(img, x, y, scale, flip)

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
# TeeCanvas (DEVICE): forward every draw call to the real DeviceCanvas (the panel still
# renders) AND, while recording is enabled, to the DrawRecorder. run_desktop swaps this in
# as ws.canvas transparently.
# ---------------------------------------------------------------------------


class TeeCanvas:
    """Wraps the device's real Canvas (DeviceCanvas) so the panel renders exactly as before,
    and ALSO records draw calls for the web view -- but only while `recorder.enabled` is True.
    When disabled, each method is a single extra branch over a direct delegate call (no list
    ops, no allocation), so the normal no-browser path is effectively free.

    STREAM MODE (#41): when `recorder.record_only` is True the pixel-PRODUCING ops RECORD but
    do NOT forward to the real canvas (the device skips rasterizing the panel; run_desktop
    skips the flush). The cheap state ops STILL forward. record_only only matters while
    enabled is True.

    Reads (`pix` with two args, attribute reads like `.buf`) pass through. new_layer() mints a
    RecordingLayer; blit_window_from/blit_strip record a tiny ["blit_layer", ...] reference
    (the layer's stream ships ONCE as a deflayer at serve time)."""

    def __init__(self, canvas, recorder):
        self._c = canvas
        self._r = recorder
        self.w = canvas.w
        self.h = canvas.h

    def __getattr__(self, name):
        # Only reached for attrs not set on the Tee (e.g. buf, _comp, sync_back).
        return getattr(self._c, name)

    def make_spr_gate(self, sheet, fallback):
        # #63 spr_gate: explicitly DECLINE the native fast path. Without this,
        # __getattr__ would forward to the real DeviceCanvas's make_spr_gate and
        # the C gate would append quads straight to the panel batch -- pixels the
        # recorder never sees, so the browser would render a cart with NO sprites
        # (and stream mode would rasterize what it's meant to skip). Returning
        # None keeps make_api on the Python spr closure -> every call crosses
        # Tee.spr_tile -> recorded. The web path is the deliberate slow lane.
        return None

    # -- off-screen layers ---------------------------------------------------
    def new_layer(self, w, h):
        real = self._c.new_layer(w, h)
        return RecordingLayer(real, self._r)

    def blit_window_from(self, layer, cam_x=0, cam_y=0):
        # A layer passed here is normally a RecordingLayer. But a layer built on the RAW
        # DeviceCanvas BEFORE the web view bound this Tee (the console's cached bar strip
        # carried across the swap) is a plain canvas -- tolerate it (blit + skip recording)
        # instead of crashing the frame.
        cam_x = int(cam_x)
        cam_y = int(cam_y)
        rl = isinstance(layer, RecordingLayer)
        if rl:
            layer._end_batch()
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

    def spr_tile(self, sheet, tile, x, y, colorkey=-1, scale=1, flip=0):
        # Fold-1 auto-batch (#63): the real DeviceCanvas queues it (coalesced into one
        # native blit_batch); the browser stream stays PER-SPR, so resolve the tile via
        # the recorder's STABLE tile-image cache and record one self-contained spr --
        # exactly like spr_batch's per-item path (wire format unchanged).
        if not self._r.record_only:
            self._c.spr_tile(sheet, tile, x, y, colorkey, scale, flip)
        if self._r.enabled:
            img = self._r.batch_tile_image(sheet, int(tile), colorkey)
            if img is not None:
                self._r.spr(img, x, y, scale, flip)

    def spr_batch(self, sheet, items, colorkey=-1, scale=1):
        if not self._r.record_only:
            self._c.spr_batch(sheet, items, colorkey, scale)
        if self._r.enabled:
            # Expand to per-tile spr commands (the browser has no batch op). Resolve each tile
            # through the recorder's STABLE tile-image cache so a repeated tile maps to the
            # SAME Image across frames -> the atlas dedups its bitmap to ONE defspr per session.
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
            # PAYLOAD DIET (#41): record ONE map op. Sync the browser's tilemap if the cart
            # mutated it (mset), then emit the map op with the resolved (default-None) region.
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
# CommandCanvas (HOST): a drop-in RECORD-ONLY canvas the host web console swaps in as the
# console's SYSTEM canvas. Same public API as runtime.canvas.Canvas, so it's a drop-in
# ws.canvas with no change to the shared console. It records SELF-CONTAINED sprs (via a
# DrawRecorder in self_contained mode) + per-cell map expansion, matching the old host
# tools/command_canvas format, but shares the recorder / RecordingLayer / _LayerRecorder /
# ServedState with the device. It has NO framebuffer (to_rgb888 -> b"", pix-read -> 0), so the
# console's _composite_game takes the composite-via-spr path (the whole game frame is recorded
# as ONE scaled sprite).
# ---------------------------------------------------------------------------


class CommandCanvas:
    """A record-only host canvas. `take_commands()` returns the frame's recorded command list
    and clears it. `_rec` is the shared DrawRecorder (self_contained) the host web console hands
    to a ServedState for the serve-time deflayer prepend + gen bookkeeping."""

    def __init__(self, width=320, height=240, palette=None, font_scale=1):
        self.w = width
        self.h = height
        if palette is None:
            from runtime import palette as _pal   # host-only lazy import
            palette = _pal.MOY64
        self.palette = palette
        # System-UI font scale (#39): when this recorder stands in for the SYSTEM canvas the
        # console calls set_font_scale + reads font_scale. Scaled text is recorded as rect
        # commands (the replayer/page already handle rect), so the browser shows the bigger font.
        self.font_scale = max(1, int(font_scale))
        self._rec = DrawRecorder(width, height)
        self._rec.self_contained = True
        self._rec.enabled = True                  # the host always records

    def set_font_scale(self, scale):
        self.font_scale = max(1, int(scale))

    # -- frame handoff -------------------------------------------------------
    def take_commands(self):
        """Return this frame's command list and start a fresh one. The deflayer ship-once
        prepend is done by ServedState.served_frame (the host web console routes through it),
        so this returns the raw recorded commands."""
        cmds = self._rec._cmds
        self._rec._cmds = []
        return cmds

    # -- draw state ----------------------------------------------------------
    def reset_state(self):
        self._rec.reset_state()

    def camera(self, x=0, y=0):
        self._rec.camera(x, y)
        return (0, 0)

    def clip(self, x=None, y=None, w=None, h=None):
        self._rec.clip(x, y, w, h)

    def pal(self, c0=None, c1=None):
        self._rec.pal(c0, c1)

    def palt(self, c=None, on=None):
        self._rec.palt(c, on)

    # -- primitives ----------------------------------------------------------
    def cls(self, c=0):
        self._rec.cls(c)

    def pix(self, x, y, c=None):
        if c is None:
            # A read. A command stream has no framebuffer to read back; the console never
            # reads during draw (only the rasterizing Canvas does, internally).
            return 0
        self._rec.pix(x, y, c)

    def line(self, x0, y0, x1, y1, c):
        self._rec.line(x0, y0, x1, y1, c)

    def rect(self, x, y, w, h, c):
        self._rec.rect(x, y, w, h, c)

    def rectb(self, x, y, w, h, c):
        self._rec.rectb(x, y, w, h, c)

    def circ(self, cx, cy, r, c):
        self._rec.circ(cx, cy, r, c)

    def circb(self, cx, cy, r, c):
        self._rec.circb(cx, cy, r, c)

    def spr(self, img, x, y, scale=1, flip=0):
        self._rec.spr(img, x, y, scale, flip)     # self_contained -> pixels inline

    def spr_tile(self, sheet, tile, x, y, colorkey=-1, scale=1, flip=0):
        # Fold-1 auto-batch queue entry (#63): a recording canvas doesn't batch --
        # resolve the tile and emit one self-contained spr, so the browser stream stays
        # per-spr (wire format unchanged), mirroring spr_batch's per-item path.
        img = sheet.tile_image(int(tile), colorkey)
        if img is not None:
            self._rec.spr(img, x, y, scale, flip)

    def spr_batch(self, sheet, items, colorkey=-1, scale=1):
        # Mirror Canvas.spr_batch: one self-contained spr per item, resolving each tile through
        # the sheet like map() does. Tile Images are cached per id within the batch.
        cache = {}
        for it in items:
            tid = int(it[0])
            flip = it[3] if len(it) > 3 else 0
            img = cache.get(tid)
            if img is None:
                img = sheet.tile_image(tid, colorkey)
                cache[tid] = img if img is not None else False
            if not img:
                continue
            self._rec.spr(img, it[1], it[2], scale, flip)

    def map(self, tilemap, sheet, mx=0, my=0, w=None, h=None,
            sx=0, sy=0, colorkey=-1, scale=1):
        # Mirror Canvas.map EXACTLY: walk the w x h cell region and emit one self-contained spr
        # per non-empty cell (host tile Images aren't stable-id, so the atlas/diet map isn't
        # used here). Reusing spr() keeps the replay pixel-identical to the host rasterizer.
        mx = int(mx)
        my = int(my)
        scale = int(scale)
        if scale < 1:
            scale = 1
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
                self._rec.spr(img, sx + cx * step, py, scale)

    def print(self, s, x, y, c, scale=1):
        # At font_scale 1 record a `print` op (petme128, pixel-identical to font.draw). At
        # font_scale > 1 (the resizable system font, #39) record the scaled glyph as rect
        # blocks -- exactly what SystemCanvas.print rasterizes.
        fs = self.font_scale
        if fs <= 1:
            self._rec.print(s, x, y, c)
            return
        from runtime import font as _font        # host-only lazy import
        ci = c & 63
        rec = self._rec

        def block(bx, by, n):
            rec.rect(bx, by, n, n, ci)

        _font.draw_scaled(block, s, x, y, fs)

    # -- offscreen layers ----------------------------------------------------
    def new_layer(self, w, h):
        from runtime.canvas import Canvas          # host-only lazy import
        real = Canvas(int(w), int(h), self.palette)
        return RecordingLayer(real, self._rec)

    def blit_window_from(self, layer, cam_x=0, cam_y=0):
        layer._end_batch()
        self._rec.blit_layer_window(layer, int(cam_x), int(cam_y))

    def blit_strip(self, layer, dst_x=0, dst_y=0):
        layer._end_batch()
        self._rec.blit_layer_full(layer, int(dst_x), int(dst_y))

    # -- output (parity with Canvas; not used by the web path) ---------------
    def to_rgb888(self):
        return b""


# ---------------------------------------------------------------------------
# Protocol payload builders (pure data -> JSON-serializable dicts). ONE shape for both
# transports so the same page consumes them.
# ---------------------------------------------------------------------------


def palette_rgb(pal565):
    """A palette given as 16/64 RGB565 ints, decoded to [r,g,b] triples (the device path --
    it stores its panel colours as RGB565, so the browser resolves indices to the SAME colours
    the panel shows). pal565 is little-endian RGB565."""
    out = []
    for c in pal565:
        r = (c >> 11) & 0x1F
        g = (c >> 5) & 0x3F
        b = c & 0x1F
        out.append([(r * 255) // 31, (g * 255) // 63, (b * 255) // 31])
    return out


def sheet_payload(sheet):
    """The open cart's sprite sheet as JSON (cols/rows/TILE + flat pixels). None when there's
    no sheet."""
    if sheet is None:
        return None
    return {
        "cols": sheet.cols, "rows": sheet.rows, "tile": sheet.TILE,
        "w": sheet.w, "h": sheet.h,
        "pix": list(sheet.pix),
    }


def tilemap_payload(tilemap):
    """The open cart's tilemap as JSON (w/h + flat cells). None when there's no tilemap."""
    if tilemap is None:
        return None
    return {"w": tilemap.w, "h": tilemap.h, "cells": list(tilemap.cells)}


def images_payload(images):
    """The open cart's DECODED paint images as JSON: {name: {"w","h","b64"}} where b64 is
    base64 of the raw MOY64 index bytes -- shipped ONCE per cart via /assets (like the sheet +
    tilemap), so a paint image referenced by ["imgref", x, y, name] never carries pixels on the
    per-frame stream (#63 Fold 4).

    `images` is {name: (w, h, index_bytes)} -- each provider DECODED its .moyimg text with its
    own zlib/deflate _decode_moyimg (the decompressor differs host vs device), so this shared
    builder only base64-wraps the raw indices. None/empty -> None (no `images` payload)."""
    if not images:
        return None
    out = {}
    for name in images:
        w, h, idx = images[name]
        out[name] = {"w": int(w), "h": int(h), "b64": _b64_indices(idx)}
    return out


def assets_payload(w, h, palette, sheet, tilemap, cart_title, audio_rate=8000, images=None):
    """The static render assets the browser needs (re-fetched on a cart change): palette +
    petme128 font + the open cart's sheet/tilemap + cart title + PCM rate.

    `palette` is EITHER a list of RGB565 ints (the DEVICE panel LUT -> decoded via palette_rgb)
    OR a list of [r,g,b] triples (the HOST MOY64 palette -> used exactly). Detected by the
    element type so both transports share this builder.

    `images` (#63 Fold 4) is the open cart's DECODED paint images ({name: (w, h, index_bytes)},
    one per images/*.moyimg); it ships as {name: {"w","h","b64"}} so a ["imgref", ...] draw
    command references a browser-cached image by name instead of carrying its pixels."""
    if palette and isinstance(palette[0], int):
        pal = palette_rgb(palette)                 # RGB565 ints (device)
    else:
        pal = [list(rgb) for rgb in palette]       # already RGB triples (host)
    return {
        "w": w, "h": h,
        "palette": pal,
        "font": {"first": FONT_FIRST, "w": FONT_W, "h": FONT_H, "glyphs": _font_glyphs()},
        "sheet": sheet_payload(sheet),
        "tilemap": tilemap_payload(tilemap),
        "images": images_payload(images),
        "cart": cart_title,
        "audio_rate": audio_rate,
    }


def frame_payload(cmds, cart_title, gen=0, perf=None, audio=""):
    """The per-frame payload: the recorded draw-command list + the cart title (so the client
    refetches /assets on a cart change) + the atlas generation `gen` (the browser resets its
    ATL/LAY caches ONLY when gen changes, lock-step with the served reset). `perf` (device) is
    a tiny stats dict the browser logs; `audio` (host) is base64 PCM for the browser player
    (the device streams no audio -> "")."""
    return {"cmds": cmds, "cart": cart_title, "gen": gen, "audio": audio, "perf": perf}


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


# ---------------------------------------------------------------------------
# Input event injection: browser events -> the console's InputState + Pointer + hooks. The
# host ConsoleDriver and the device run_desktop wire the hooks to their own input paths.
# ---------------------------------------------------------------------------


def apply_events(events, input, pointer, on_press=None, on_pan=None,
                 on_key=None, on_esc=None, on_hold=None):
    """Inject a batch of browser events into an InputState + Pointer. Each event is fully
    guarded (a malformed one is skipped, never raised) so a buggy client can't crash the loop.

      {"type":"down","x":..,"y":..}  -> pointer tap (place + click + down)
      {"type":"move","x":..,"y":..}  -> pointer drag (place, down, no tap)
      {"type":"up"}                  -> release (pointer up)
      {"type":"pan","dx":..,"dy":..} -> trackball nudge (on_pan)
      {"type":"press","name":..}     -> one-shot button press (on_press)
      {"type":"hold","name":..,"down":bool} -> held button (on_hold, else input.set_button)
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
                    # Route to on_hold when wired (the device's keyboard.poll clears buttons,
                    # so a hold must be re-asserted AFTER the poll); else a direct set.
                    if on_hold is not None:
                        on_hold(name, bool(ev.get("down")))
                    else:
                        input.set_button(name, bool(ev.get("down")))
            elif t == "key":
                code = ev.get("code")
                if isinstance(code, int) and 0 <= code <= 0xFF and on_key is not None:
                    on_key(code)
                    # ONE console key on the web too (#71): outside text mode a
                    # browser Backspace also acts as HOME (toggles the pause
                    # screen -- never "exit", which is the pause screen's own
                    # explicit QUIT button), mirroring the physical key -- the
                    # raw-matrix path likewise reports last_key=0x08 AND the
                    # home button. In text mode it stays a typed 0x08 only
                    # (delete for a tool; the Workstation edge-detects the
                    # game's pause toggle itself).
                    if (code == 0x08 and on_press is not None
                            and not getattr(input, "text_mode", False)):
                        on_press("home")
            elif t == "esc":
                if on_esc is not None:
                    on_esc()
        except Exception:  # noqa: BLE001 -- one bad event must not drop the batch
            pass


# ---------------------------------------------------------------------------
# ServedState: the serve-time defspr/deflayer ship-once logic. Transport-agnostic -- BOTH the
# device WebServer and the host web console hold one against their recorder and call
# served_frame() per frame. Tracks which sprite bitmaps + layer streams a browser SESSION has
# already received, so each ships ONCE yet every served frame stays self-contained
# (drop-robust: the frame cap discards frames and the browser sees only the latest).
# ---------------------------------------------------------------------------


class ServedState:
    """Holds the per-session `served` sets against a DrawRecorder. served_frame() prepends a
    ["defspr", ...] for every ATLAS-form spr index the browser hasn't received yet (host
    self-contained sprs carry their own pixels, so they're skipped), and a ["deflayer", ...]
    for every blit_layer whose layer id+gen the browser hasn't received. Resets both sets when
    the recorder's atlas_gen changes (reset_atlas) or on reset() (/assets re-served)."""

    def __init__(self, recorder):
        self.recorder = recorder
        self._served = set()
        self._served_gen = recorder.atlas_gen
        self._served_layers = {}

    def served_frame(self, cmds):
        rec = self.recorder
        if rec.atlas_gen != self._served_gen:
            self._served = set()
            self._served_layers = {}
            self._served_gen = rec.atlas_gen
        prefix = None
        budget = MAX_DEFSPR_BYTES_PER_FRAME       # defspr bytes we'll prepend THIS frame
        for c in cmds:
            if not c:
                continue
            op = c[0]
            # An ATLAS-form spr is ["spr", idx, x, y, scale, flip] (<= 6 fields). A HOST
            # self-contained spr is ["spr", x, y, scale, w, h, t, pix, flip] (9 fields) and
            # carries its own bitmap -> nothing to ship.
            if op == "spr" and len(c) <= 6:
                idx = c[1]
                if idx not in self._served and budget > 0:
                    d = rec.defspr_cmd(idx)
                    if d is not None:
                        if prefix is None:
                            prefix = []
                        prefix.append(d)
                        self._served.add(idx)
                        budget -= len(d[5]) * 3 + 24   # ~JSON bytes of the pix list + wrapper
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
                        # A deflayer is one big one-time blob; ship it whole but then stop
                        # prepending defsprs this frame so the frame stays bounded.
                        budget = 0
        if prefix is None:
            return cmds
        return prefix + cmds

    def reset(self):
        """Forget which defsprs + deflayers the browser has -- so the next served frame
        re-ships everything it references. Called when /assets is (re)served (a page load /
        cart change clears the browser's caches)."""
        self._served = set()
        self._served_layers = {}
        self._served_gen = self.recorder.atlas_gen


# ---------------------------------------------------------------------------
# Reference replayer (the Python twin of the browser JS): execute a command list onto a real
# rasterizing runtime.canvas.Canvas. Used by the tests to prove the stream reproduces the
# rasterized frame pixel-for-pixel. Handles BOTH spr forms (atlas-by-index + self-contained),
# the diet map (via a cached sheet/tilemap from `assets` or settiles), and off-screen layers.
# Host-only (lazy-imports runtime.canvas); never called on the device.
# ---------------------------------------------------------------------------


def replay_to_canvas(commands, canvas, layers=None, assets=None):
    """Replay `commands` onto `canvas` (a runtime.canvas.Canvas of the same size).

    `layers` is the off-screen layer cache (id -> rasterized Canvas), the twin of the
    browser's per-id off-screen canvases -- pass a dict persisted across frames so a deflayer
    shipped ONCE keeps serving later frames' blit_layers. `assets` (optional) seeds the sheet +
    tilemap the diet map op replays from, exactly as the browser's GET /assets does."""
    from runtime.canvas import Image, Canvas

    if layers is None:
        layers = {}
    atlas = {}                                     # index -> Image (browser's ATL)
    sheet_pix = sheet_cols = sheet_tile = sheet_w = None
    tm = None
    imgs = None                                    # name -> {"w","h","b64"} (browser's IMG)
    if assets is not None:
        sh = assets.get("sheet")
        if sh is not None:
            sheet_pix = list(sh["pix"])
            sheet_cols = sh["cols"]
            sheet_tile = sh["tile"]
            sheet_w = sh["w"]
        tmp = assets.get("tilemap")
        if tmp is not None:
            tm = {"w": tmp["w"], "h": tmp["h"], "cells": list(tmp["cells"])}
        imgs = assets.get("images")

    def _tile_image(tid, colorkey):
        ox = (tid % sheet_cols) * sheet_tile
        oy = (tid // sheet_cols) * sheet_tile
        pix = []
        for ly in range(sheet_tile):
            base = (oy + ly) * sheet_w + ox
            for lx in range(sheet_tile):
                pix.append(sheet_pix[base + lx])
        return Image(sheet_tile, sheet_tile, pix, transparent=colorkey)

    for cmd in commands:
        op = cmd[0]
        if op == "cls":
            canvas.cls(cmd[1])
        elif op == "pix":
            canvas.pix(cmd[1], cmd[2], cmd[3])
        elif op == "line":
            canvas.line(cmd[1], cmd[2], cmd[3], cmd[4], cmd[5])
        elif op == "rect":
            canvas.rect(cmd[1], cmd[2], cmd[3], cmd[4], cmd[5])
        elif op == "rectb":
            canvas.rectb(cmd[1], cmd[2], cmd[3], cmd[4], cmd[5])
        elif op == "circ":
            canvas.circ(cmd[1], cmd[2], cmd[3], cmd[4])
        elif op == "circb":
            canvas.circb(cmd[1], cmd[2], cmd[3], cmd[4])
        elif op == "defspr":
            idx, w, h, t, pix = cmd[1:6]
            atlas[idx] = Image(w, h, list(pix), transparent=t)
        elif op == "spr":
            # TWO shapes: atlas ["spr", idx, x, y, scale, flip] (<= 6 fields) OR self-contained
            # ["spr", x, y, scale, w, h, t, pix, flip] (a pix list at cmd[7]).
            if len(cmd) > 7 and isinstance(cmd[7], (list, tuple)):
                _x, _y, scale, w, h, t, pix = cmd[1:8]
                flip = cmd[8] if len(cmd) > 8 else 0
                canvas.spr(Image(w, h, list(pix), transparent=t), _x, _y, scale, flip)
            else:
                idx, x, y, scale = cmd[1:5]
                flip = cmd[5] if len(cmd) > 5 else 0
                im = atlas.get(idx)
                if im is not None:
                    canvas.spr(im, x, y, scale, flip)
        elif op == "img":
            # PAINT-IMAGE inline fallback (#63 Fold 3): decode the base64 raw MOY64 indices and
            # blit them (opaque, clamped) via the same blit_indices the device bakes with -- into
            # the CURRENT canvas (the layer buffer when replayed inside a deflayer).
            x, y, w, h = cmd[1:5]
            canvas.blit_indices(_unb64_indices(cmd[5]), int(w), int(h), int(x), int(y))
        elif op == "imgref":
            # PAINT-IMAGE by NAME (#63 Fold 4): resolve the cart image from the /assets `images`
            # dict (shipped once, browser-cached) and blit its raw indices -- the imgref twin of
            # img. A missing name (assets not seeded) no-ops, like the browser's IMG cache miss.
            x, y, name = cmd[1], cmd[2], cmd[3]
            im = imgs.get(name) if imgs else None
            if im is not None:
                canvas.blit_indices(_unb64_indices(im["b64"]),
                                    int(im["w"]), int(im["h"]), int(x), int(y))
        elif op == "deflayer":
            lid, lw, lh, lcmds = cmd[1:5]
            layer = Canvas(int(lw), int(lh), canvas.palette)
            replay_to_canvas(lcmds, layer, layers, assets)
            layers[lid] = layer
        elif op == "blit_layer":
            lid = cmd[1]
            layer = layers.get(lid)
            if layer is not None:
                if len(cmd) > 4 and cmd[4] == "full":
                    canvas.blit_strip(layer, cmd[2], cmd[3])
                else:
                    canvas.blit_window_from(layer, cmd[2], cmd[3])
        elif op == "settiles":
            tm = {"w": cmd[1], "h": cmd[2], "cells": list(cmd[3])}
        elif op == "map":
            mx, my, w, h, sx, sy, scale, colorkey = cmd[1:9]
            if sheet_pix is not None and tm is not None:
                if scale < 1:
                    scale = 1
                step = sheet_tile * scale
                cache = {}
                for cy in range(h):
                    ty = my + cy
                    for cx in range(w):
                        gx = mx + cx
                        if 0 <= gx < tm["w"] and 0 <= ty < tm["h"]:
                            tid = tm["cells"][ty * tm["w"] + gx] - 1
                        else:
                            tid = -1
                        if tid < 0:
                            continue
                        im = cache.get(tid)
                        if im is None:
                            im = _tile_image(tid, colorkey)
                            cache[tid] = im
                        canvas.spr(im, sx + cx * step, sy + cy * step, scale)
        elif op == "print":
            canvas.print(cmd[1], cmd[2], cmd[3], cmd[4])
        elif op == "reset_state":
            canvas.reset_state()
        elif op == "camera":
            canvas.camera(cmd[1], cmd[2])
        elif op == "clip":
            if len(cmd) > 1:
                canvas.clip(cmd[1], cmd[2], cmd[3], cmd[4])
            else:
                canvas.clip()
        elif op == "pal":
            if len(cmd) > 1:
                canvas.pal(cmd[1], cmd[2])
            else:
                canvas.pal()
        elif op == "palt":
            if len(cmd) > 1:
                canvas.palt(cmd[1], bool(cmd[2]))
            else:
                canvas.palt()
        # unknown ops are ignored (forward-compatible)
    return canvas


# ---------------------------------------------------------------------------
# The browser page: a self-contained replayer for the payload-diet protocol (#41). ONE page
# for BOTH transports. It fetches /assets (palette + font + sheet + tilemap) ONCE over HTTP,
# then opens a persistent WebSocket (/ws) for the live channel (frames PUSH down, input pushes
# up). The live channel is WebSocket-ONLY now -- BOTH the device (raw-socket) and the host
# (http.server) speak WS, so a closed socket just reconnects; there is no HTTP poll fallback.
# Replay is PIXEL-IDENTICAL to the panel:
#   defspr  -> cache the bitmap by index (ATL[index]).
#   spr     -> atlas form (by index) OR self-contained (full-pixel) -> blit with scale/flip.
#   map     -> walk the CACHED tilemap over the CACHED sheet (kept current by settiles).
#   imgref  -> blit a /assets-cached paint image (IMG[name]) by NAME (#63 Fold 4); img is the
#              inline-pixel fallback for a nameless paint image.
#   deflayer/blit_layer -> replay a layer's stream into an off-screen buffer once, then blit.
# On a cart change (a frame's cart != assCart) -> refetch /assets; ATL/LAY reset on `gen` change.
# A deflayer may carry an imgref whose IMG isn't loaded yet (a re-shipped layer racing the async
# /assets fetch); an imgref cache-MISS latches imgWant, and df() re-fetches /assets (which makes
# the server re-ship the deflayer) until the image is cached -- so a ship-once layer converges.
# ---------------------------------------------------------------------------

PAGE_HTML = """<!doctype html><html><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>Moybyte device</title><style>
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
#bh{background:#5f574f;width:52px;height:52px;font-size:20px}
.pr{background:#ffec27;color:#1d2b53}
/* Debug HUD (#41): toggled with the ` key; lightweight live stream stats. */
#hud{position:fixed;top:6px;left:6px;z-index:9;display:none;padding:6px 8px;border-radius:5px;
background:rgba(11,15,26,.82);border:1px solid #1d2b53;color:#00e436;font:12px ui-monospace;
white-space:pre;pointer-events:none}#hud b{color:#ffec27}#hud .w{color:#ff004d}</style></head><body>
<div id=hud></div>
<h1>Moybyte &mdash; device <span id=s>connecting...</span> <small style="color:#5f6f9f">(press ` for stats)</small></h1>
<canvas id=cv width=320 height=240 tabindex=0></canvas>
<div id=ctl><div id=joy><div id=th></div></div>
<div><span class=b id=bh>&#9776;</span><span class=b id=bb>B</span><span class=b id=ba>A</span></div></div>
<script>
var FPS=30,cv=document.getElementById("cv"),cx=cv.getContext("2d"),sEl=document.getElementById("s");
cx.imageSmoothingEnabled=false;
var W=320,H=240,PAL=null,FONT=null,ready=false,assCart=undefined,idx=null,img=null,rgba=null;
// Payload-diet caches (#41): SHEET = the cart sprite sheet; TM = the cart tilemap (kept
// current by settiles); ATL = the per-session sprite atlas filled by defspr.
var SHEET=null,TM=null,ATL=[],curGen=-1;
// Paint-image cache (#63 Fold 4): IMG[name] = {w,h,px:Uint8Array of raw MOY64 indices},
// loaded from /assets (once per cart) so an ["imgref",x,y,name] blits without carrying pixels.
// imgWant latches an imgref cache MISS (a deflayer racing the async /assets fetch); assLoading
// guards a single in-flight /assets fetch so the miss-retry (df) doesn't spam the server.
var IMG={},imgWant=false,assLoading=false;
var HUD={on:false,fps:0,kb:0,unknown:0,el:document.getElementById("hud"),last:0};
// Periodic perf LOG (#41): recv + render fps, bandwidth, avg/peak payload, AND the device's
// push-rate + free heap (from f.perf), one console.log line every PERF_MS. The recv/dev/bw
// figures are 2s MEANS -- which hide a stutter (a lone slow frame averages away), so we also
// fold the device's per-frame instants (gap/draw/js/tx) into a window MAX + count throttled
// frames: mg=worst inter-frame gap ms (THE stutter number), md=worst device draw+commit ms,
// mj/mt=worst json-encode/socket-send ms, thr=# of bandwidth-throttled pushes this window.
var PERF_MS=2000,PERF={f:0,b:0,pk:0,t:0,dh:0,pf:0,lpf:0,js:0,tx:0,mg:0,md:0,mj:0,mt:0,thr:0};
function plog(){var now=(window.performance&&performance.now)?performance.now():Date.now();
if(!PERF.t){PERF.t=now;PERF.lpf=PERF.pf;return;}var dt=(now-PERF.t)/1000;if(dt<=0)return;
console.log("[moybyte] "+(assCart||"?")+" | recv "+(PERF.f/dt).toFixed(1)+" render "+HUD.fps.toFixed(1)
+" dev "+((PERF.pf-PERF.lpf)/dt).toFixed(1)+" fps | worst gap "+PERF.mg+" draw "+PERF.md+" js "+PERF.mj
+" tx "+PERF.mt+" ms | thr "+PERF.thr+" | bw "+(PERF.b/dt/1024).toFixed(1)+" KB/s avg "
+(PERF.f?(PERF.b/PERF.f/1024):0).toFixed(2)+" peak "+(PERF.pk/1024).toFixed(2)
+" KB | heap "+PERF.dh+" KB | unknown "+HUD.unknown);
PERF.f=0;PERF.b=0;PERF.pk=0;PERF.t=now;PERF.lpf=PERF.pf;PERF.mg=0;PERF.md=0;PERF.mj=0;PERF.mt=0;PERF.thr=0;}
// Audio (host web console): play the server's FINISHED PCM (no JS synth). The device streams
// no audio (f.audio ""), so this is a no-op there.
var AUDIO_RATE=11025,actx=null,audioNext=0,audioBlocked=false;
function ensureAudio(){if(!actx){var AC=window.AudioContext||window.webkitAudioContext;
if(AC){try{actx=new AC();}catch(e){actx=null;}}}if(actx&&actx.state==="suspended")actx.resume();}
// Audio frames that arrive while the context is blocked by the browser's autoplay
// policy used to be dropped SILENTLY -- undiagnosable on a phone. Surface the state
// in the status chip instead, and self-heal once a gesture unlocks the context.
function playPCM(b64){if(!b64)return;
if(!actx||actx.state!=="running"){if(!audioBlocked){audioBlocked=true;
sEl.textContent="tap screen to enable sound";sEl.style.color="#ffa300";}return;}
if(audioBlocked){audioBlocked=false;ok=false;}
var bin=atob(b64),n=bin.length>>1;if(n<=0)return;
var buf=actx.createBuffer(1,n,AUDIO_RATE),ch=buf.getChannelData(0);
for(var i=0;i<n;i++){var v=bin.charCodeAt(i*2)|(bin.charCodeAt(i*2+1)<<8);if(v>=32768)v-=65536;ch[i]=v/32768;}
var src=actx.createBufferSource();src.buffer=buf;src.connect(actx.destination);
var t=Math.max(actx.currentTime+0.02,audioNext);src.start(t);audioNext=t+buf.duration;}
function alloc(){cv.width=W;cv.height=H;cx=cv.getContext("2d");cx.imageSmoothingEnabled=false;
idx=new Uint8Array(W*H);img=cx.createImageData(W,H);rgba=img.data;rs();}
function getA(){assLoading=true;return fetch("/assets").then(function(r){return r.json();}).then(function(a){
W=a.w;H=a.h;PAL=a.palette;FONT=a.font;assCart=a.cart;SHEET=a.sheet||null;if(a.audio_rate)AUDIO_RATE=a.audio_rate;
TM=a.tilemap?{w:a.tilemap.w,h:a.tilemap.h,cells:a.tilemap.cells.slice()}:null;
// Decode each paint image's base64 raw indices into a Uint8Array ONCE (#63 Fold 4), so an
// imgref just blits the cached bytes (index->palette). Keyed by the SAME name image('name') tags.
IMG={};if(a.images){for(var nm in a.images){var gi=a.images[nm],bs=atob(gi.b64),bn=bs.length,bp=new Uint8Array(bn);
for(var bk=0;bk<bn;bk++)bp[bk]=bs.charCodeAt(bk);IMG[nm]={w:gi.w,h:gi.h,px:bp};}}
assLoading=false;alloc();ready=true;}).catch(function(e){assLoading=false;throw e;});}
// NB: do NOT clear ATL here -- it resets on `gen` change (see df). imgWant is cleared by df's retry.
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
// Blit a bitmap (raw pixels px, sw x sh, transparent index t) at x,y with scale+flip. Mirrors
// DeviceCanvas.spr / Canvas.spr exactly. Used by spr (atlas + self-contained) and map.
function blt(px,sw,sh,t,x,y,sc,fl){x|=0;y|=0;sc|=0;fl|=0;var fx=fl&1,fy=(fl>>1)&1;
for(var yy=0;yy<sh;yy++){var ry=fy?sh-1-yy:yy,bs=ry*sw;for(var xx=0;xx<sw;xx++){var rx=fx?sw-1-xx:xx,
p=px[bs+rx];if(p===t||p<0||pt[p&63])continue;if(sc<=1)put(x+xx,y+yy,p);else fr(x+xx*sc,y+yy*sc,sc,sc,p);}}}
// spr by atlas index. Unknown index = no-op (a missing defspr is the dropped-frame bug).
function sp(ix,x,y,sc,fl){var a=ATL[ix];if(!a){HUD.unknown++;return;}blt(a.px,a.w,a.h,a.t,x,y,sc,fl);}
// img (#63 Fold 3): a paint image (a big MOY64 index bitmap) as base64 of its RAW indices --
// the INLINE FALLBACK for a nameless paint image. atob -> write indices OPAQUE (index>=64
// skipped) into the CURRENT target (idx/W/H) clamped, the browser twin of blit_indices.
function im(x,y,w,h,b64){var s=atob(b64);x|=0;y|=0;w|=0;h|=0;
for(var yy=0;yy<h;yy++){var ty=y+yy;if(ty<0||ty>=H)continue;var sr=yy*w,dr=ty*W;
for(var xx=0;xx<w;xx++){var tx=x+xx;if(tx<0||tx>=W)continue;var p=s.charCodeAt(sr+xx);if(p<64)idx[dr+tx]=p;}}}
// imgref (#63 Fold 4): a paint image by NAME, blitted from the /assets IMG cache -- the normal
// path (pixels shipped once, not per-frame). Same opaque index->target blit as im, but reading
// the pre-decoded Uint8Array. A cache MISS latches imgWant so df() re-fetches /assets (the layer
// deflayer re-ships once assets arrive) -- a ship-once layer can otherwise strand its background.
function imr(x,y,nm){var G=IMG[nm];if(!G){imgWant=true;return;}var s=G.px,w=G.w,h=G.h;x|=0;y|=0;
for(var yy=0;yy<h;yy++){var ty=y+yy;if(ty<0||ty>=H)continue;var sr=yy*w,dr=ty*W;
for(var xx=0;xx<w;xx++){var tx=x+xx;if(tx<0||tx>=W)continue;var p=s[sr+xx];if(p<64)idx[dr+tx]=p;}}}
// map(): walk the cached tilemap region over the cached sheet (step=tile*scale, colorkey
// transparent), mirroring the device map() cell layout.
function mp(mx,my,w,h,sx,sy,sc,ck){if(!SHEET||!TM)return;sc=sc<1?1:sc;
var tile=SHEET.tile,step=tile*sc,cols=SHEET.cols,sw=SHEET.w,spx=SHEET.pix,tw=TM.w,th=TM.h,cells=TM.cells;
for(var cy=0;cy<h;cy++){var ty=my+cy;for(var cx=0;cx<w;cx++){var gx=mx+cx;
var tid=(gx>=0&&gx<tw&&ty>=0&&ty<th)?cells[ty*tw+gx]-1:-1;if(tid<0)continue;
var ox=(tid%cols)*tile,oy=((tid/cols)|0)*tile,dx=sx+cx*step,dy=sy+cy*step;
for(var ly=0;ly<tile;ly++){var srow=(oy+ly)*sw+ox;for(var lx=0;lx<tile;lx++){var p=spx[srow+lx];
if(p===ck||p<0||pt[p&63])continue;if(sc<=1)put(dx+lx,dy+ly,p);else fr(dx+lx*sc,dy+ly*sc,sc,sc,p);}}}}}
// settiles: overwrite the cached tilemap (a cart mutated it via mset).
function st(w,h,cells){TM={w:w,h:h,cells:cells};}
// OFF-SCREEN LAYERS (#54 scroll + #43 cached top bar): deflayer (re)builds an off-screen
// index buffer by REPLAYING the layer's recorded stream (reusing rep()); blit_layer copies a
// window (draw_layer) or the full layer (blit_strip) into idx. LAY keeps each built buffer.
var LAY={};
function dfl(id,lw,lh,cmds){var sI=idx,sW=W,sH=H,sX=caX,sY=caY,s0=cl0,s1=cm0,s2=cl1,s3=cm1,sm=pm,spt=pt;
var buf=new Uint8Array(lw*lh);idx=buf;W=lw;H=lh;rs();rep(cmds);
idx=sI;W=sW;H=sH;caX=sX;caY=sY;cl0=s0;cm0=s1;cl1=s2;cm1=s3;pm=sm;pt=spt;LAY[id]={w:lw,h:lh,buf:buf};}
function blw(L,cx,cy){cx=cx<0?0:cx|0;cy=cy<0?0:cy|0;var dw=W,dh=H,sw=L.w,src=L.buf;
if(sw<=0||dw<=0||dh<=0)return;if(cx+dw>sw)dw=sw-cx;if(dw<=0)return;var sr=(src.length/sw)|0;
if(cy+dh>sr)dh=sr-cy;if(dh<=0)return;for(var r=0;r<dh;r++){var d0=r*W,o0=(cy+r)*sw+cx;
for(var x=0;x<dw;x++)idx[d0+x]=src[o0+x];}}
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
// spr has TWO shapes: atlas ["spr",idx,x,y,sc,fl] (<=6 fields) and self-contained
// ["spr",x,y,sc,w,h,t,pix,fl] (a pix array at c[7]). Branch on the pix array.
else if(o=="spr"){if(c.length>7&&c[7]&&c[7].length!==undefined)blt(c[7],c[4],c[5],c[6],c[1],c[2],c[3],c[8]||0);else sp(c[1],c[2],c[3],c[4],c[5]||0);}
else if(o=="img")im(c[1],c[2],c[3],c[4],c[5]);else if(o=="imgref")imr(c[1],c[2],c[3]);
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
wb("ba","a");wb("bb","b");wb("bh","home");  // &#9776; = HOME: toggles the pause screen (#71)
var PAN={ArrowLeft:[-1,0],ArrowRight:[1,0],ArrowUp:[0,-1],ArrowDown:[0,1]},
NAV={a:"left",d:"right",w:"up",s:"down"},SC={Enter:"run",z:"a",x:"b"},pH={},nH={};
// No letter->HOME shortcut: page buttons BYPASS the device's text-mode alias
// suppression, so h-as-HOME stole the letter h from typing carts (Letter
// Blitz). Pause from the page = the burger button, or Backspace: it is sent
// as typed cd=8 and the SERVER maps it to HOME outside text mode (#71 one
// console key) -- either way it only ever TOGGLES the pause screen; QUIT is
// the pause screen's own explicit button, tapped like any other.
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
// df(): render ONE frame payload. Atlas reset is driven by the device's `gen` (lock-step with
// its served reset), NOT the cart change -- so scrolling the launcher (cart_title -> /assets
// refetch) no longer wipes ATL and strands sprites (the unknown-growth bug, #41).
function df(f){if(f.perf){var p=f.perf;PERF.dh=p.heap;PERF.pf=p.pf;PERF.js=p.js;PERF.tx=p.tx;
// Fold this frame's device instants into the window MAX (undefined on a host that omits them
// stays 0 -- `undefined>0` is false). thr counts bandwidth-throttled pushes.
if(p.js>PERF.mj)PERF.mj=p.js;if(p.tx>PERF.mt)PERF.mt=p.tx;
if(p.dr>PERF.md)PERF.md=p.dr;if(p.gap>PERF.mg)PERF.mg=p.gap;if(p.thr)PERF.thr++;}
if(f.gen!==curGen){curGen=f.gen;ATL=[];LAY={};HUD.unknown=0;}
if(f.cart!==assCart){assCart=f.cart;getA().catch(function(){});}rep(f.cmds||[]);blit();
// A deflayer's imgref cache-MISS (racing the async /assets fetch) latched imgWant: re-fetch
// /assets (the server re-ships the deflayer on reset) until the paint image is cached (#63 F4).
if(imgWant&&!assLoading){imgWant=false;getA().catch(function(){});}
if(f.audio)playPCM(f.audio);
var t=(window.performance&&performance.now)?performance.now():Date.now();if(HUD.last){var inst=1000/Math.max(1,t-HUD.last);
HUD.fps=HUD.fps?HUD.fps+(inst-HUD.fps)*0.2:inst;}HUD.last=t;if(HUD.on)drawHud();
if(!ok){ok=true;sEl.textContent="live";sEl.style.color="#00e436";}}
// THE LIVE CHANNEL (#41): a persistent WebSocket, the ONLY transport now. Frames PUSH down
// (ws.onmessage -> df), input pushes up (pump() sends queued events per tick). BOTH the device
// and the host speak WS, so a closed/failed socket just reconnects with a small backoff -- no
// HTTP poll fallback. The onmessage byte-count feeds the perf log's bw/avg on both.
var ws=null,wsOpen=false,reconn=null;
function pump(){var v=pv();if(v[0]||v[1])send({type:"pan",dx:v[0],dy:v[1]});
if(!q.length)return;var b=q;q=[];
if(wsOpen){try{ws.send(JSON.stringify({events:b}));}catch(e){}}}
function connect(){if(reconn){clearTimeout(reconn);reconn=null;}
try{ws=new WebSocket((location.protocol=="https:"?"wss://":"ws://")+location.host+"/ws");}
catch(e){retry();return;}
ws.onopen=function(){wsOpen=true;ok=false;sEl.textContent="live";sEl.style.color="#00e436";};
ws.onmessage=function(ev){var n=ev.data.length;HUD.kb=n/1024;PERF.f++;PERF.b+=n;if(n>PERF.pk)PERF.pk=n;
var f;try{f=JSON.parse(ev.data);}catch(e){return;}df(f);};
ws.onclose=function(){wsOpen=false;retry();};
ws.onerror=function(){try{ws.close();}catch(e){}};}
// Reconnect with a small fixed backoff (the socket dropped / the server restarted).
function retry(){wsOpen=false;sEl.textContent="reconnecting...";sEl.style.color="#ff004d";
if(reconn)return;reconn=setTimeout(function(){reconn=null;connect();},800);}
function drawHud(){var n=0;for(var i=0;i<ATL.length;i++)if(ATL[i])n++;
var u=HUD.unknown?'<span class=w>'+HUD.unknown+'</span>':'0';
HUD.el.innerHTML="fps <b>"+HUD.fps.toFixed(1)+"</b>   "+HUD.kb.toFixed(2)+" KB/f<br>atlas <b>"+n+"</b>   unknown "+u;}
window.addEventListener("keydown",function(e){if(e.key==="`"||e.key==="~"){HUD.on=!HUD.on;
HUD.el.style.display=HUD.on?"block":"none";if(HUD.on)drawHud();e.preventDefault();}});
document.addEventListener("pointerdown",ensureAudio);
document.addEventListener("touchend",ensureAudio);  // legacy iOS only unlocks here
document.addEventListener("keydown",ensureAudio);
// Fetch /assets once over HTTP, then open the WebSocket live channel; pump queued input up on
// a timer.
getA().then(function(){connect();setInterval(pump,Math.round(1000/FPS));setInterval(plog,PERF_MS);}).catch(function(){
sEl.textContent="no assets";sEl.style.color="#ff004d";});cv.focus();
</script></body></html>"""
