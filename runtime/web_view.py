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


# PAGE_HTML (the browser page) + the ws_* WebSocket protocol (handshake + framing) were
# extracted to their own leaf modules to shrink this file; imported back + re-exported here
# so every `web_view.PAGE_HTML` / `web_view.ws_encode` / `web_view.WS_GUID` reference -- both
# transports + the tests -- is unchanged. Neither leaf imports web_view (page is a string,
# ws_* are self-contained), so there is no cycle. Bare-or-package fallback: bare top-level on
# the device (frozen), runtime.* on the host / CPython.
try:
    from web_view_page import PAGE_HTML
    from web_view_ws import (WS_GUID, WS_MAX_FRAME, ws_accept_key, ws_handshake_response,
                             ws_header_key, is_ws_upgrade, ws_encode, ws_decode)
except ImportError:  # pragma: no cover - host / package import
    from runtime.web_view_page import PAGE_HTML
    from runtime.web_view_ws import (WS_GUID, WS_MAX_FRAME, ws_accept_key,
                                     ws_handshake_response, ws_header_key, is_ws_upgrade,
                                     ws_encode, ws_decode)


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
# WM-surface partitioning (Stage 9 of docs/shell_ux_technical_plan_v1.md): one command
# stream PER window-manager surface (the wm.draw_stack() layers -- bar / app-content /
# player-viewport) instead of one flat frame, so the browser page becomes a SECOND window
# manager (the spec Section 3 tier table: the S3 fullscreen stack + the browser). The
# surfaces are a VIEW of the SAME flat _cmds -- the recorder just remembers WHERE in the
# stream each surface begins, and _slice_surfaces cuts the frame at those offsets. So the
# flat frame (and every serve/replay/test path over it) is byte-identical; surfaces are
# just sliced out when asked. OFF by default -> the flat stream is the only output.
# ---------------------------------------------------------------------------


def _slice_surfaces(cmds, marks):
    """Cut a flat frame `cmds` into per-surface streams at the recorded start offsets.

    `marks` is [(start_index_into_cmds, sid, domain), ...] in draw order. Returns
    [[sid, domain, sub_cmds], ...] whose sub_cmds CONCATENATE back to exactly `cmds`
    (so the surfaces composite to the same pixels as the flat frame). Any commands drawn
    BEFORE the first surface was begun ride a leading "_pre" surface so nothing is dropped;
    a frame with no surface marks at all becomes one implicit "_all" surface."""
    if not marks:
        return [["_all", "system", cmds]] if cmds else []
    out = []
    first = marks[0][0]
    if first > 0:
        out.append(["_pre", "system", cmds[:first]])
    n = len(marks)
    for i in range(n):
        start, sid, domain = marks[i]
        end = marks[i + 1][0] if i + 1 < n else len(cmds)
        out.append([sid, domain, cmds[start:end]])
    return out


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
        # WM-SURFACE PARTITION (Stage 9): when surfaces_on, the console marks each WM-stack
        # surface (begin_surface) and commit() slices the flat frame into one stream per
        # surface (bar / app-content / player-viewport). OFF by default -> begin_surface is a
        # no-op and frame_surfaces() is None, so the flat frame + every path over it are
        # byte-identical (zero cost when the web view is off). Only the host web console turns
        # it on; the DEVICE keeps it off (per-surface browser render is a hardware gate).
        self.surfaces_on = False
        self._surf_marks = []          # [(start_index, sid, domain), ...] for this frame
        self._frame_surfaces = None    # the last committed frame's [[sid, domain, cmds], ...]

    # -- frame handoff -------------------------------------------------------
    def begin(self):
        """Start recording a fresh frame (drop a partial one defensively). Reset the atlas
        if it has grown past MAX_ATLAS (a screen with unstable sprite ids) so it can't leak."""
        if len(self._atlas_keep) > MAX_ATLAS:
            self.reset_atlas()
        self._cmds = []
        self._surf_marks = []

    def begin_surface(self, sid, domain="system"):
        """Mark the start of a WM surface's command run (Stage 9). The injected recording
        canvas forwards the console's per-draw-stack-layer begin_surface here; while
        surfaces_on the recorder remembers WHERE in the flat stream this surface begins, so
        commit() can slice the frame WITHOUT a second command list -- the flat _cmds every
        existing path uses is untouched. A NO-OP while surfaces_on is False (the default) ->
        zero cost + byte-identical when the web view is off."""
        if self.surfaces_on:
            self._surf_marks.append((len(self._cmds), str(sid), str(domain)))

    def commit(self):
        """Finish the frame: the accumulated commands become the served frame. While
        surfaces_on, ALSO slice the flat stream into per-surface streams at the marked offsets
        (Stage 9) -- a view of the same commands, so the flat frame stays identical."""
        self._frame = self._cmds
        self._frame_surfaces = (_slice_surfaces(self._cmds, self._surf_marks)
                                if self.surfaces_on else None)
        self._cmds = []

    def frame(self):
        """The last committed frame's command list."""
        return self._frame

    def frame_surfaces(self):
        """The last committed frame's per-surface streams ([[sid, domain, cmds], ...]) or None
        when surfaces_on was False for that frame (Stage 9). The flat frame() is always
        available; the surfaces are just a sliced view of it."""
        return self._frame_surfaces

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

    def begin_surface(self, sid, domain="system"):
        # Stage 9 WM-surface mark (host == device API parity): forward to the recorder, which
        # only slices while surfaces_on. The DEVICE keeps surfaces_on False -- per-surface render
        # in the browser + the WiFi<->LCD-DMA coexistence are a STANDING HARDWARE GATE (#38/#40)
        # this stage does not close -- so this is a NO-OP there and the device stream stays a
        # byte-identical flat frame. Only the host web console turns surfaces on.
        self._r.begin_surface(sid, domain)

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

    def begin_surface(self, sid, domain="system"):
        """Stage 9: forward the console's per-WM-surface mark to the recorder so the frame is
        sliced per surface (the browser composites them). A no-op unless the recorder's
        surfaces_on is set (web_console turns it on). See DrawRecorder.begin_surface."""
        self._rec.begin_surface(sid, domain)

    # -- frame handoff -------------------------------------------------------
    def take_commands(self):
        """Return this frame's command list and start a fresh one. The deflayer ship-once
        prepend is done by ServedState.served_frame (the host web console routes through it),
        so this returns the raw recorded commands.

        Stage 9: when surfaces_on, ALSO slice the frame into per-surface streams from the marks
        recorded THIS frame, BEFORE the buffer is cleared, so take_surfaces() (called right
        after) returns them. The CommandCanvas records straight into _cmds (no begin/commit
        cycle), so the slice lives here rather than in DrawRecorder.commit()."""
        rec = self._rec
        cmds = rec._cmds
        rec._frame_surfaces = (_slice_surfaces(cmds, rec._surf_marks)
                               if rec.surfaces_on else None)
        rec._cmds = []
        rec._surf_marks = []
        return cmds

    def take_surfaces(self):
        """Stage 9: the per-surface streams ([[sid, domain, cmds], ...]) for the frame
        take_commands() last returned, or None when surfaces_on is off. Call right after
        take_commands()."""
        return self._rec._frame_surfaces

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


def frame_payload(cmds, cart_title, gen=0, perf=None, audio="", surfaces=None):
    """The per-frame payload: the recorded draw-command list + the cart title (so the client
    refetches /assets on a cart change) + the atlas generation `gen` (the browser resets its
    ATL/LAY caches ONLY when gen changes, lock-step with the served reset). `perf` (device) is
    a tiny stats dict the browser logs; `audio` (host) is base64 PCM for the browser player
    (the device streams no audio -> "").

    `surfaces` (Stage 9): when present it's the per-WM-surface streams
    ([{"id","domain","cmds"}, ...]) the browser composites IN ORDER (bottom->top) instead of
    the flat `cmds` -- the browser as a second window manager. None keeps the flat-frame shape
    unchanged (the device + web-view-off path), so every existing consumer is untouched."""
    p = {"cmds": cmds, "cart": cart_title, "gen": gen, "audio": audio, "perf": perf}
    if surfaces is not None:
        p["surfaces"] = surfaces
    return p


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
                    # ONE console key on the web too: outside text mode a browser
                    # Backspace also fires the HOME button (Stage 5: HOME is the EXIT
                    # key -- a single edge the running cart reads; the ☰ button, wired
                    # as a HELD "home" below, is the hold-to-exit gesture for games),
                    # mirroring the physical key -- the raw-matrix path likewise
                    # reports last_key=0x08 AND the home button. In text mode it stays
                    # a typed 0x08 only (DELETE for a tool -- zero special-casing).
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

    def served_surfaces(self, flat_cmds, surfaces):
        """Stage 9: serve a frame as per-WM-surface streams. Runs the SAME ship-once
        defspr/deflayer bookkeeping as served_frame() -- ONCE, over the flat stream -- and
        returns (served_flat, surface_dicts):

          * served_flat   = served_frame(flat_cmds) (prefix + flat) -- the flat wire form,
                            handy for callers that also want it and for the faithfulness cross-
                            check (its pixels are the target the surfaces must reproduce).
          * surface_dicts = [{"id","domain","cmds"}, ...]: the ship-once prefix delivered as a
                            LEADING "_defs" surface, then each recorder surface ([sid, domain,
                            cmds]) as a dict. A browser replaying the surfaces IN ORDER thus
                            populates its atlas/layer caches (the "_defs" surface) BEFORE the
                            sprs/blits that reference them -- exactly as the flat served frame
                            does (the surfaces are a slice of the flat stream, "_defs" prepended).

        `surfaces` is the recorder's frame_surfaces() ([[sid, domain, cmds], ...])."""
        served = self.served_frame(flat_cmds)
        prefix_len = len(served) - len(flat_cmds)
        out = []
        if prefix_len > 0:
            out.append({"id": "_defs", "domain": "system", "cmds": served[:prefix_len]})
        for sid, domain, cmds in (surfaces or []):
            out.append({"id": sid, "domain": domain, "cmds": cmds})
        return served, out

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


def replay_surfaces_to_canvas(surfaces, canvas, layers=None, assets=None):
    """Replay per-WM-surface streams ([{"id","domain","cmds"}, ...] OR [[sid, domain, cmds],
    ...]) onto `canvas` by compositing them IN ORDER (bottom -> top) -- the Python twin of the
    browser page's per-surface compositor (Stage 9). Because the surfaces are an in-order slice
    of the flat frame (with the ship-once "_defs" prefix leading), replaying their command runs
    back-to-back through ONE replay pass shares the atlas / layer cache across surfaces -- so a
    defspr in the leading surface populates the atlas the later surfaces' sprs reference, exactly
    as the browser's global ATL/LAY do. Pixel-identical to replaying the flat served frame."""
    flat = []
    for s in (surfaces or []):
        flat.extend(s["cmds"] if isinstance(s, dict) else s[2])
    return replay_to_canvas(flat, canvas, layers=layers, assets=assets)
