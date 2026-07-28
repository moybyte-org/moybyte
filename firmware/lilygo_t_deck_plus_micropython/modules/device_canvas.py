"""The device DRAWING backend (extracted from moy_runtime.py) -- the single most
performance-critical + native-coupled unit on the device.

DeviceCanvas implements the indexed v0.4 canvas API (cls/pset/line/rect/circ/spr/
map/print + the #54 scroll layers + the #63 sprite-batch/spr-gate) against the
compositor's RGB565 framebuffer. The hot verbs go through the native moy_gfx kernel
(fill/fill_rect/blit565/blit_map/blit_batch/blit_indices/circ/line/text/copy_async);
framebuf is the text/line + no-moy_gfx fallback; moy_alloc gives _LayerComp its
off-GC-heap DMA buffer. Also here: Image (indexed sprite), _decode_moyimg (.moyimg
paint asset), _LayerComp/_Layer (the scroll-layer compositor), the MOY64 RGB565
palette LUTs (PAL565 / PAL565_SW / PAL565_WIRE / _PAL565_WIRE_BUF), and the native-detection flags
(_USE_GFX / LAYER_COPY_ASYNC / _RGB_KEY / _FONT8).

Imports: `array` + the leaf device_util tick helpers; the native modules
(moy_gfx/moy_alloc/lcd_bus/framebuf) are imported lazily inside methods, and the
staged `moy_font` + `moy_compositor.SRAM_BOUNCE_FLUSH` at module load (guarded).
No moy_runtime cycle. Device-only module (modules/, auto-frozen).

NEEDS ON-DEVICE SMOKE BEFORE TRUSTING -- this is a pure code move, but EVERY pixel
the device draws flows through here and the native moy_gfx/moy_alloc/lcd_bus paths
cannot be exercised by the host test shim (those modules are absent under CPython).
Host tests prove the import DAG + structure; only a board confirms the panel still
draws. The module-load reads (_PAL565_WIRE_BUF buffer, _SRAM_BOUNCE_FLUSH->
LAYER_COPY_ASYNC) must stay intact -- they travelled with the block verbatim.
"""
from array import array

from device_util import _ticks_us, _ticks_diff


# petme128 glyph blob for the native moy_gfx.text kernel (#62) -- staged from
# runtime/font.py as moy_font by build.sh, so device text rasterizes from the SAME
# bytes the host Canvas.print does (pixel parity). Absent (old build) -> print()
# stays on framebuf.text (same glyphs, no clip rect).
try:
    import moy_font as _moy_font
    _FONT8 = _moy_font.DATA
    _FONT8_FIRST = _moy_font.FIRST
except ImportError:
    _FONT8 = None
    _FONT8_FIRST = 0x20

# MOY64 palette as RGB565 (generated from runtime/palette.py; no colorsys here).
PAL565 = (
    0x0000, 0x194A, 0x792A, 0x042A, 0xAA86, 0x5AA9, 0xC618, 0xFF9D,
    0xF809, 0xFD00, 0xFF64, 0x0726, 0x2D7F, 0x83B3, 0xFBB5, 0xFE75,
    0xE514, 0xE5D4, 0xE694, 0xAF34, 0xA73A, 0xA69C, 0xA5BC, 0xB51C,
    0xDD1C, 0xE519, 0xE516, 0xA73C, 0x8B49, 0xAC6F, 0x6285, 0xCDF3,
    0x5388, 0x51C5, 0x83CD, 0x9C8D, 0xE2C5, 0xE4C5, 0xD705, 0x3705,
    0x2F11, 0x2F1C, 0x2C5C, 0x517C, 0xA17C, 0xE178, 0xE16F, 0x7705,
    0xDF1E, 0xADB8, 0x8453, 0x5AED, 0xE6FA, 0x8C2F, 0x39E8, 0x2945,
    0x6165, 0x6245, 0x2B2A, 0x2AAC, 0x29EC, 0x416C, 0x616C, 0x6168,
)

# Same palette, byte-swapped to the T-Deck PANEL's wire order (#43). PAL565 above is
# the canonical little-endian RGB565 (the host parity test asserts it == rgb565(MOY64));
# PAL565_SW is what the T-Deck WRITEs into the device framebuffer so the per-flush
# CPU byte-swap in lcd_bus.tx_color can be turned OFF (tdeck_display rgb565_byte_swap
# =False). That swap was ~17 ms/frame over PSRAM -- the synchronous wall left once the
# DMA-overlap flush (#43) hid the SPI transfer. Folding it into this LUT makes it free
# (the index->colour lookup happens anyway), so the kick drops from ~17 ms to ~2 ms and
# the SPI finally overlaps render. PAL565 stays the canonical reference.
PAL565_SW = tuple(((c << 8) | (c >> 8)) & 0xFFFF for c in PAL565)

# The WIRE-order LUT every buffer-writing path (_col + the sprite/atlas bakes)
# actually uses. T-Deck (SPI panel): the byte-swapped PAL565_SW, per the above.
# P4 (#58, MIPI-DSI DPI mode): the scan-out hardware reads the framebuffer
# directly as little-endian RGB565, so the CANONICAL PAL565 -- byte-swapped
# writes render every color wrong (glass-confirmed 2026-07-08). moy_dsi exists
# only on the DPI build, so its importability IS the board answer; under CPython
# (parity tests) it is absent, so the host harness keeps the swapped order the
# tests swap back from.
try:
    import moy_dsi as _moy_dsi_probe  # noqa: F401 -- presence probe only
    PAL565_WIRE = PAL565
except ImportError:
    PAL565_WIRE = PAL565_SW
# Buffer form of PAL565_WIRE for the native blit_indices kernel (#63): the C reads the
# palette via the BUFFER PROTOCOL (moy_gfx_buf_r), and a tuple has none ("object with
# buffer protocol required"). An array("H") is a contiguous uint16 buffer AND still
# indexes in Python, so it serves both. (The tuple stays for the other PAL565_WIRE uses.)
_PAL565_WIRE_BUF = array("H", PAL565_WIRE)

# RGB565 colour-key for native sprite blits: transparent sprite pixels are baked
# to this value so moy_gfx.blit565 skips them. Magenta is absent from MOY64; a
# visible pixel that happens to equal it is nudged by one LSB when the cache is
# built (see DeviceCanvas._cache_rgb), so it can never read as transparent.
_RGB_KEY = 0xF81F

# new_layer pre-collects (defragment PSRAM) only for a layer at least this many
# pixels -- a cart's scrolling world (~192K px), not a UI cache like the bar's
# 1024x18 strip (~18K px), whose rebuild was paying a full mark-sweep. See
# new_layer's COMPACT FIRST note.
_COMPACT_MIN_PX = 64 * 1024

# Flip to False to force the slow Python per-pixel drawing path (no native moy_gfx)
# for an FPS A/B comparison against the native-blit build. NOT dead config: the
# host parity suite (tests/test_device_canvas_parity.py) sets this module attribute
# per-case to prove the Python fallbacks render byte-identical to the native kernel.
_USE_GFX = True

# GDMA async layer copy (#54 St.2 / #63 / #66): tied to the SRAM-bounce flush.
# The copy is correct and fast (layer 7ms -> 0.04ms, plus it keeps the dcache
# warm: Sakura logic 13-21ms vs 29-41ms with the CPU sync copy), but it is a
# full-throttle PSRAM->PSRAM GDMA blit -- run against a panel DMA that READS
# PSRAM it starves the SPI FIFO into horizontal garbage bands (hardware,
# 2026-07-03). Under the #66 SRAM-bounce flush the panel only ever reads
# internal SRAM, so the contention target is gone and the copy is safe again.
# One flag feeds both: bounce off -> this must go off with it.
try:
    from moy_compositor import SRAM_BOUNCE_FLUSH as _SRAM_BOUNCE_FLUSH
except Exception:
    _SRAM_BOUNCE_FLUSH = False
LAYER_COPY_ASYNC = _SRAM_BOUNCE_FLUSH

# #75: immutable templates the per-frame reset_state restores the pal tables from
# IN PLACE (no per-frame bytearray allocation; 0 == identity map / all-opaque).
_PAL_IDENTITY = bytes(range(64))
_PALT_OPAQUE = bytes(64)


def tri_spans(x1, y1, x2, y2, x3, y3):
    """The horizontal spans covering a filled triangle, packed flat as
    (x, y, w, 1, 0) quints for fill_rects (#167). Pure integer scanline walk --
    sort the vertices by y, then for each row take the long edge a->c against
    whichever short edge (a->b above the middle vertex, b->c below) is active.

    Byte-for-byte the host twin (runtime/canvas.py tri_spans); the colour slot is
    left 0 because tri() passes the colour as fill_rects' `c` override."""
    x1 = int(x1); y1 = int(y1)
    x2 = int(x2); y2 = int(y2)
    x3 = int(x3); y3 = int(y3)
    if y1 > y2:
        x1, y1, x2, y2 = x2, y2, x1, y1
    if y1 > y3:
        x1, y1, x3, y3 = x3, y3, x1, y1
    if y2 > y3:
        x2, y2, x3, y3 = x3, y3, x2, y2
    if y3 == y1:                       # flat: one span through all three x
        lo = x1 if x1 < x2 else x2
        if x3 < lo:
            lo = x3
        hi = x1 if x1 > x2 else x2
        if x3 > hi:
            hi = x3
        return [lo, y1, hi - lo + 1, 1, 0]
    out = []
    dy_long = y3 - y1
    dy_top = y2 - y1
    dy_bot = y3 - y2
    for y in range(y1, y3 + 1):
        xa = x1 + (x3 - x1) * (y - y1) // dy_long
        if y < y2:                     # dy_top > 0 whenever this branch is taken
            xb = x1 + (x2 - x1) * (y - y1) // dy_top
        elif dy_bot:
            xb = x2 + (x3 - x2) * (y - y2) // dy_bot
        else:
            xb = x3
        if xa > xb:
            xa, xb = xb, xa
        out.append(xa)
        out.append(y)
        out.append(xb - xa + 1)
        out.append(1)
        out.append(0)
    return out


# Native draw gates (#155). The state-array indices and the gate kinds MUST match
# the enums in native/moy_gfx/modmoy_gfx.c -- they are one binary layout shared
# between this module and the C kernel.
_ST_CAM_X, _ST_CAM_Y = 0, 1
_ST_CX0, _ST_CY0, _ST_CX1, _ST_CY1 = 2, 3, 4, 5
_ST_W, _ST_H = 6, 7
_ST_FONT_SCALE = 8
_ST_PROF = 9
_ST_N_FILL, _ST_N_TEXT = 10, 11
_ST_T_FILL, _ST_T_TEXT = 12, 13
_ST_LEN = 14
_GATE_RECT, _GATE_RECTB, _GATE_PRINT, _GATE_PIX = 0, 1, 2, 3

# Layer-buffer pool (#63 GC-wall follow-up): moy_alloc has NO free(), so a layer
# buffer handed back by a dead cart is returned HERE (keyed by byte size) and the
# next new_layer of the same dims reuses it -- without this, every cart re-run
# leaked its world (~150-384KB) from the heap_caps PSRAM pool until the allocator
# started failing (~20-30 opens) and silently degraded to gc-heap buffers (the
# GC wall back again). Only moy_alloc-backed buffers are pooled (a gc-heap
# fallback bytearray is the collector's job); nothing is ever dropped from the
# pool -- the set of distinct layer sizes across carts is small and stable.
_LAYER_POOL = {}


# Fold 2 (#63) knob: the map() auto-cache trades the per-cell blit_map walk for a
# blit565 composite of a cached raster. HARDWARE VERDICT (T-Deck, 2026-07-07 owner
# flash): the composite LOSES -- Battle City map 4.3-5.7ms direct -> 13.4ms cached
# steady state (the keyed blit reads every pixel of the 240x240 region; blit_map
# skips empty cells and PSRAM magnifies that), 32-55ms on brick-destruction
# re-rasters, fps 29-33 -> 24-25. Even the opaque row-memcpy lane only breaks even
# (~5ms for 115KB PSRAM->PSRAM). So the cache ships DEFAULT OFF; the machinery +
# counters stay for a future native keyed-blit kernel or the P4's 2D DMA (#58),
# and the parity tests force it on to keep the logic pinned.
MAP_AUTO_CACHE = False


class Image:
    def __init__(self, width, height, pix, transparent=-1):
        self.w = width
        self.h = height
        self.pix = pix
        self.transparent = transparent

    @classmethod
    def from_ascii(cls, rows, mapping, transparent="."):
        h = len(rows)
        w = max(len(r) for r in rows) if rows else 0
        pix = []
        for y in range(h):
            row = rows[y]
            for x in range(w):
                ch = row[x] if x < len(row) else transparent
                pix.append(-1 if ch == transparent else (mapping[ch] & 63))
        return cls(w, h, pix, -1)


def _decode_moyimg(text):
    """Decode a .moyimg paint-image asset (#63 Fold 3) into (w, h, index_bytes), or
    None on any error (a bad image just doesn't draw). The blob is a JSON header
    {w, h, data} where `data` is base64 of the zlib-compressed MOY64 index bitmap
    (1 byte/pixel) -- the SAME base64+zlib envelope sprites author with. The device
    inflates it with the `deflate` module (MicroPython's zlib), mirroring the host's
    zlib in host_app._decode_moyimg."""
    try:
        import json as _json
        meta = _json.loads(text)
        if meta.get("codec") == "rle":
            import moy_carts as _moy_carts
            return _moy_carts.decode_moyimg(text)
        import ubinascii as _binascii
        import deflate
        import io
        w = int(meta["w"])
        h = int(meta["h"])
        data = _binascii.a2b_base64(meta["data"])
        idx = deflate.DeflateIO(io.BytesIO(data), deflate.ZLIB).read()
        return (w, h, idx)
    except Exception:  # noqa: BLE001 -- bad/absent image -> caller gets None
        return None


_GATE_SEQ = [0]     # spr_gate token counter (#63): unique per gate, int16-safe, never 0.


class DeviceCanvas:
    """The kid drawing API. The hot ops (cls/rect/circ/spr) go through the native
    moy_gfx C kernel writing straight into the compositor's RGB565 framebuffer --
    this is what keeps complex carts off the slow per-pixel Python path. framebuf
    over the same buffer still serves text/lines/pixels and is the fallback on an
    image built without moy_gfx."""

    # PARTIAL-repaint capability (the Library shelf's drag fast path, see
    # runtime/canvas.py): with the #40 ping-pong double buffer the back buffer
    # holds the frame BEFORE last -> 2. (Single-buffer mode retains frame-1;
    # advertising 2 stays conservative-correct there too.)
    RETAINED_FRAMES = 2

    def __init__(self, compositor):
        import framebuf

        self._comp = compositor
        self.w, self.h = compositor.size()
        self._buf = compositor.framebuffer()          # raw RGB565 bytearray (for moy_gfx)
        self._fb = framebuf.FrameBuffer(self._buf, self.w, self.h, framebuf.RGB565)
        self._gfx = compositor.gfx() if _USE_GFX else None   # native kernel, or None
        # Native petme128 text (#62): resolved once -- needs both the moy_gfx.text
        # op (old firmware lacks it) and the staged moy_font glyph blob.
        self._gfx_text = (getattr(self._gfx, "text", None)
                          if (self._gfx is not None and _FONT8 is not None) else None)
        # #66 pump poke: feed the in-flight SRAM-bounce flush between native draw
        # ops. The 2ms pump "timer" is a soft timer (fires between bytecodes) --
        # it CANNOT fire while the interpreter sits inside one long C op (a 15ms
        # fill, a 10ms map), which measured as PUMP idle=2-6ms of starved SPI on
        # virtually every frame. The big verbs call self._pump() right after
        # their native call instead. None on layers / host fakes (no bounce).
        self._pump = getattr(compositor, "pump_if_pending", None)
        # DMA double-buffer (#40, DEFAULT ON -- moy_compositor.DOUBLE_BUFFER, device-
        # confirmed stable): the compositor's BACK buffer ping-pongs between two
        # physical buffers each flush, so this canvas must re-point its draw target
        # at it every frame (sync_back) -- a stale pointer would draw into the
        # buffer that's being DMA'd (tear). framebuf can't retarget its backing
        # store in place, so cache one framebuf per physical buffer keyed by id(buf)
        # and pick the matching one on each swap; no per-frame allocation. In
        # single-buffer mode framebuffer() never moves, so sync_back is a cheap no-op.
        self._fb_by_buf = {id(self._buf): self._fb}
        # Async layer copy (#54 Stage 2): prediction + in-flight state. Armed by
        # blit_window_from when the copy shape is ONE contiguous memcpy (cam_x==0,
        # layer exactly screen-wide, full-height coverage -- sakura's shape);
        # kicked by sync_back at frame start; consumed (copy_wait) by the next
        # blit_window_from. _async_ok latches False on the first driver refusal
        # (old firmware / no gdma / bad alignment) so we never retry per frame.
        #
        # LAYER_COPY_ASYNC is now DEFAULT ON (tied 1:1 to moy_compositor.SRAM_BOUNCE_
        # FLUSH, see the module-level comment above): it was hardware-verdict FALSE
        # on 2026-07-03 because the copy is a second GDMA engine doing a full-
        # throttle PSRAM->PSRAM blit that, run against a panel DMA reading PSRAM
        # directly, starved the SPI FIFO into horizontal garbage bands (worst in the
        # one cart using layers). The #66 SRAM-bounce flush removed the contention
        # target -- the panel now only ever reads internal SRAM -- so the async copy
        # (layer= 7ms -> 0.04ms on Sakura) is safe again and shipped as the default.
        self._lcopy_pred = None
        self._lcopy = None
        self._lcopy_trips = 0     # copy_wait timeouts (#66 HITCH v3 diagnostics)
        self._async_ok = (LAYER_COPY_ASYNC and self._gfx is not None
                          and hasattr(self._gfx, "copy_async"))
        # Pending sprite batch (Fold 1 -> #63 spr_gate): 1x1 sheet-tile blits queue
        # into ONE flat array('h') instead of a list of tuples -- layout
        # [next, colorkey, scale, token, (tile x y flip)*N], items from index 4.
        # WHY an array: (a) the native spr_gate appends to it straight from C with
        # zero Python-object churn (the fix for the warm-heap call-frame-spill
        # pathology -- see make_spr_gate below), and (b) blit_batch reads it
        # directly (array mode), so a full run draws without ever materialising
        # tuples. token tags WHICH writer owns the pending run (a C gate's id, or
        # 0 for the Python spr_tile path) so interleaved writers force a clean
        # flush+begin instead of silently mixing sheets.
        # Pal/palt state (#75): the tables are built by the first reset_state (below)
        # and afterwards restored IN PLACE only when a pal()/palt() dirtied them, so
        # the per-frame reset never allocates. Initialised BEFORE reset_state.
        self._pal_dirty = True
        self._pal_map = None
        self._palt = None
        # Content-keyed pal-state ids (#63 fast-by-default): _palgen is no longer a
        # monotonic counter but the STABLE id of the current (pal map, palt) CONTENT --
        # identity is always 0, and returning to a previously-seen remap returns its
        # old id. Every cache keyed on _palgen (per-sprite RGB bakes, the sheet atlas,
        # the Fold-2 identity gate) therefore survives a pal()/spr()/pal() tint
        # sandwich: the kid idiom that used to re-bake every sprite every frame
        # (#72's Letter Blitz disease) now re-bakes once per distinct tint.
        self._pal_state_ids = None    # state key -> id (lazy; identity = 0)
        self._pal_state_next = 1
        self._rgb_bakes = 0           # per-pixel bake count (test/diag proof)
        # Alloc-free state keys for the COMMON tints: a running count of entries that
        # differ from identity (pal) / opaque (palt), and the single differing index
        # while exactly one does (-1 none, -2 unknown/multi). A one-entry remap -- the
        # kid tint sandwich -- then keys as a smallint (no bytes build per pal() call);
        # only multi-entry states pay the 128-byte content key.
        self._pal_delta = 0
        self._palt_delta = 0
        self._pal_single = -1
        self._palt_single = -1
        # Auto-cache for map() (Fold 2, #63): the rasterized tilemap region is cached in a
        # hidden 565 layer so a camera-only change keyed-blits it (one blit565) instead of a
        # full re-raster (blit_map over every cell) -- the make_layer/draw_layer win, made
        # automatic for a naive camera()+map() cart. _mapcache is (key, layer, lw, lh); kept
        # ACROSS frames (NOT cleared in reset_state, or it could never hit) and rebuilt when
        # the key -- (tilemap.gen, sheet.gen, region, colorkey, scale) -- changes. Counters
        # prove it: a re-raster bumps _map_raster_count, a re-use bumps _map_hits
        # (map_cache_reset). Set BEFORE reset_state so it's live before the first draw.
        self._mapcache = None
        self._map_raster_count = 0
        self._map_hits = 0
        self._lent_layers = None      # owner -> [(buf, nbytes)] pooled loans (#63 leak fix)
        # A hidden layer (new_layer) sets this True: a layer is a draw-ONCE scratch buffer
        # (the escape hatch's make_layer, or this cache's own hidden layer), so its own map()
        # rasters DIRECTLY -- never a nested cache (which would double the layer's PSRAM and
        # add a redundant composite). The main canvas keeps it False and caches.
        self._nocache = False
        # Initialised BEFORE reset_state so its flush no-ops.
        self._batch_sheet = None
        self._batch_arr = array("h", bytearray(2 * (4 + 4 * 512)))
        self._batch_arr[0] = 4
        # Auto-batch profiling counters (#63, perf_capture): per frame, run count / total
        # sprites batched / largest run -- so a profile can PROVE N sprites coalesced into
        # ONE blit_batch (flushes=1, maxrun=N) vs drawn one-by-one (flushes=N, maxrun=1).
        self._batch_flushes = 0
        self._batch_sprites = 0
        self._batch_maxrun = 0
        # #63 DRAW2: per-frame microseconds spent in the two native pixel ops that dominate
        # a full-frame cart -- the layer window-copy (draw_layer -> blit_window) and the
        # sprite batch (blit_batch). render (_draw EMA) mixes them; this splits which one
        # actually costs the time, so an optimisation targets the real hot op. Reset each
        # frame by batch_reset (perf capture only); read via _diag_draw2.
        self._t_layer_us = 0
        self._t_batch_us = 0
        # #66: the render-bound carts' remaining verbs, so DRAW2 attributes the WHOLE
        # render ms -- map (blit_map), text (moy_gfx.text), fill (cls + rect/circ spans).
        # Battle City's ~26ms render is cls + a 240px backdrop rect + a full map() +
        # one spr_batch + 11 prints; these say which C op actually eats it.
        self._t_map_us = 0
        self._t_text_us = 0
        self._t_fill_us = 0
        # DRAW2 timing gate. The per-op ticks_us pair costs ~6us -- meaningless
        # against a cart's big native verbs (which is why it shipped ungated), but
        # ~6% of a CHROME fill, of which a single picker draw issues ~155. The
        # frame loop turns this on with batch_reset() under perf capture and off
        # otherwise (console.py), so a shipping frame pays nothing.
        self._prof = False
        # Native draw gates (#155): None until _install_draw_gates succeeds. Set
        # BEFORE reset_state, whose _sync_gate_* calls read them.
        self._gate_ctx = None
        self._wire_pal_arr = None      # #167 fill_spans palette cache (see _wire_pal)
        self._wire_pal_gen = -1
        self._gate_state = None
        self._gate_pal = None
        # VIEWPORT (#155) -- host twin: runtime/canvas.py Canvas.set_viewport.
        # w/h are the LOGICAL surface a caller draws on (0,0 based); _stride/_bh
        # are the real buffer, _ox/_oy where the surface sits inside it. Equal on
        # a full-surface canvas, so nothing here changes until set_viewport runs.
        # The translation rides the EXISTING camera (_cam_* is the EFFECTIVE
        # offset = user camera - origin), so every hot path costs what it did.
        self._stride = self.w
        self._bh = self.h
        self._ox = 0
        self._oy = 0
        self._user_cam_x = 0
        self._user_cam_y = 0
        # DMA fill hook (#155), resolved once: the P4 backend can clear a block
        # on the PPA, which writes PSRAM WITHOUT the CPU cache's write-allocate
        # read -- 4-5x a CPU fill above the crossover. None on backends without
        # one, so those keep the moy_gfx path untouched.
        self._ppa_fill = getattr(self, "ppa_fill", None)
        self.reset_state()
        self._install_draw_gates()

    def sync_back(self):
        """Re-point the draw target at the compositor's current BACK buffer (#40
        double-buffer). Called once per frame BEFORE drawing: the prior flush() swapped
        the back buffer, so cls/rect/spr/map/text/pix/line must target the NEW back or
        they'd write the buffer mid-DMA (tear). framebuf is cached per physical buffer
        so a swap just re-selects, never reallocates.

        ALSO the async layer-copy kick point (#54 Stage 2): this runs BEFORE the
        cart's _update, so a predicted draw_layer background restore started here
        runs on the GDMA engine WHILE the kid's Python logic executes -- by the
        time _draw calls draw_layer, the ~7ms copy is already done (copy_wait
        returns immediately). Prediction armed by blit_window_from (below)."""
        buf = self._comp.back_buffer()
        if buf is not self._buf:
            self._buf = buf
            fb = self._fb_by_buf.get(id(buf))
            if fb is None:
                import framebuf
                fb = framebuf.FrameBuffer(buf, self._stride, self._bh,
                                          framebuf.RGB565)
                self._fb_by_buf[id(buf)] = fb
            self._fb = fb
            if self._gate_ctx is not None:
                self._gate_ctx.set_buf(buf)   # #155: gates draw into the NEW back
        if self._gate_state is not None:
            # DRAW2 timing gate, synced once per frame (console.py flips _prof by
            # direct attribute store, so there is no setter to hook).
            self._gate_state[_ST_PROF] = 1 if self._prof else 0
        if self._lcopy is not None:
            self._drain_lcopy()           # last frame's copy never consumed: drain
        pred = self._lcopy_pred
        if pred is not None:
            self._lcopy_pred = None
            layer, cam_y, npix = pred
            try:
                if self._gfx.copy_async(self._buf, 0, layer._buf,
                                        cam_y * self.w, npix):
                    self._lcopy = pred    # in flight; consumed by blit_window_from
                else:
                    self._async_ok = False    # driver refused -> stay sync from now on
            except Exception:  # noqa: BLE001 -- any C-side surprise -> sync path
                self._async_ok = False

    def _drain_lcopy(self):
        # Complete an in-flight async layer restore that nothing consumed -- the
        # frame changed shape (cart exit, screen switch). Cheap; never raises.
        self._lcopy = None
        try:
            if self._gfx.copy_wait() is False:
                self._lcopy_trips += 1    # tripped: count it (#66 diagnostics)
        except Exception:  # noqa: BLE001
            self._async_ok = False

    # -- draw state (camera / clip / pal / palt, #11) ------------------------
    # Mirror runtime/canvas.py exactly so a .moy draws the same pixels host-side
    # and on-device: camera offsets all coords, clip bounds the write region (passed
    # to the moy_gfx kernel for blits / intersected for fills), pal remaps draw
    # indices (applied in _col, so every primitive inherits it), palt marks sprite
    # indices transparent. _palgen bumps on a pal/palt change so the per-sprite RGB
    # cache (which bakes pal+palt in) knows to re-bake.

    def reset_state(self):
        # Draw any queued sprites FIRST: they were spr_tile()'d under the current
        # camera/clip/pal/palt, so they must be emitted before that state is wiped (#63).
        self.flush_batch()
        # #155: effective offset + BUFFER-space clip (identities without a viewport).
        self._user_cam_x = 0
        self._user_cam_y = 0
        self._cam_x = -self._ox
        self._cam_y = -self._oy
        self._clip_x0 = self._ox
        self._clip_y0 = self._oy
        self._clip_x1 = self._ox + self.w
        self._clip_y1 = self._oy + self.h
        # #75: this runs EVERY cart frame (ws._reset_canvas_state), and rebuilding the
        # two 64-byte tables was two heap allocations per frame. The tables are created
        # once (first call: the attributes don't exist yet) and afterwards restored
        # IN PLACE, and only when a pal()/palt() actually touched them (_pal_dirty) --
        # a cart that never remaps pays two int compares. _palgen returns to 0 exactly
        # as before (0 == identity map: the cached sprite RGB fast path keys on it).
        if self._pal_dirty:
            self._pal_dirty = False
            if self._pal_map is None:
                self._pal_map = bytearray(_PAL_IDENTITY)
                self._palt = bytearray(64)  # 0 opaque, 1 transparent (default opaque)
            else:
                self._pal_map[:] = _PAL_IDENTITY
                self._palt[:] = _PALT_OPAQUE
            self._palgen = 0
            self._pal_delta = 0           # #63: back to identity content (state id 0)
            self._palt_delta = 0
            self._pal_single = -1
            self._palt_single = -1
            self._sync_gate_pal()
        self._sync_gate_state()

    def camera(self, x=0, y=0):
        self.flush_batch()             # queued sprites belong to the OLD camera (#63)
        prev = (self._user_cam_x, self._user_cam_y)
        self._user_cam_x = int(x)
        self._user_cam_y = int(y)
        self._cam_x = self._user_cam_x - self._ox
        self._cam_y = self._user_cam_y - self._oy
        self._sync_gate_state()
        return prev

    def clip(self, x=None, y=None, w=None, h=None):
        self.flush_batch()             # queued sprites belong to the OLD clip (#63)
        if x is None:
            self._clip_x0 = self._ox
            self._clip_y0 = self._oy
            self._clip_x1 = self._ox + self.w
            self._clip_y1 = self._oy + self.h
            self._sync_gate_state()
            return
        x = int(x); y = int(y); w = int(w); h = int(h)
        # Caller's rect is surface-local; the stored clip is buffer-space (#155).
        self._clip_x0 = self._ox + max(0, x)
        self._clip_y0 = self._oy + max(0, y)
        self._clip_x1 = self._ox + min(self.w, x + w)
        self._clip_y1 = self._oy + min(self.h, y + h)
        self._sync_gate_state()

    def _pal_state_id(self):
        # The stable id of the CURRENT (pal map, palt) content: identity is 0, any
        # other state gets a small int the first time it is seen and the SAME int
        # every time after -- so pal-keyed caches hit when a cart returns to a tint
        # it used before (the pal()/spr()/pal() sandwich). Computed only on a
        # pal()/palt() CALL (never per draw); the common single-entry remap keys as
        # a SMALLINT (alloc-free -- the tint sandwich runs dozens of pal calls per
        # frame and must not feed the GC), only multi-entry states build the 128-byte
        # content key. A runaway animated palette (>64 distinct states) drops the
        # learned table and re-learns; ids keep rising so a stale bake can never
        # alias a new state. (An int-keyed and a bytes-keyed id for the same content
        # can coexist after a multi->single transition -- that costs one redundant
        # bake, never a wrong pixel.)
        pd = self._pal_delta
        td = self._palt_delta
        if pd == 0 and td == 0:
            return 0
        if td == 0 and pd == 1 and self._pal_single >= 0:
            c = self._pal_single
            key = 0x10000 + (c << 6) + self._pal_map[c]
        elif pd == 0 and td == 1 and self._palt_single >= 0:
            key = 0x20000 + self._palt_single
        else:
            key = bytes(self._pal_map) + bytes(self._palt)
        ids = self._pal_state_ids
        if ids is None:
            ids = self._pal_state_ids = {}
        i = ids.get(key)
        if i is None:
            if len(ids) > 64:
                ids.clear()
            i = self._pal_state_next
            self._pal_state_next += 1
            ids[key] = i
        return i

    def pal(self, c0=None, c1=None):
        self.flush_batch()             # queued sprites belong to the OLD pal map (#63)
        pm = self._pal_map
        if c0 is None:
            if self._pal_delta:
                pm[:] = _PAL_IDENTITY
                self._pal_delta = 0
            self._pal_single = -1
        else:
            c = int(c0) & 63
            v = int(c1) & 63
            old = pm[c]
            if old != v:
                pm[c] = v
                was = old != c
                now = v != c
                if was != now:                # identity-membership flipped at c
                    if now:
                        self._pal_delta += 1
                        self._pal_single = c if self._pal_delta == 1 else -2
                    else:
                        self._pal_delta -= 1
                        self._pal_single = -2 if self._pal_delta else -1
                # was == now (True): value changed at an already-remapped index --
                # delta/single unchanged, the id below keys on the new value.
        self._palgen = self._pal_state_id()   # content id: re-seen tints reuse bakes
        self._pal_dirty = True              # #75: the next reset_state must restore
        self._sync_gate_pal()

    def palt(self, c=None, on=None):
        self.flush_batch()             # queued sprites belong to the OLD palt (#63)
        pt = self._palt
        if c is None:
            if self._palt_delta:
                pt[:] = _PALT_OPAQUE
                self._palt_delta = 0
            self._palt_single = -1
        else:
            i = int(c) & 63
            v = 1 if on else 0
            if pt[i] != v:
                pt[i] = v
                if v:
                    self._palt_delta += 1
                    self._palt_single = i if self._palt_delta == 1 else -2
                else:
                    self._palt_delta -= 1
                    self._palt_single = -2 if self._palt_delta else -1
        self._palgen = self._pal_state_id()   # content id: re-seen tints reuse bakes
        self._pal_dirty = True              # #75: the next reset_state must restore

    # -- native draw gates (#155) -------------------------------------------
    #
    # rect/rectb/print/pix become NATIVE callables that draw immediately, with
    # camera+clip+pal applied in C. Measured on P4 glass 2026-07-26: cv.rect was
    # 50.2us against 5.2us for the moy_gfx.fill_rect it ends in, and an EMPTY
    # 5-arg Python method costs 5.5us -- so ~90% of a chrome rect was two Python
    # frames (rect -> _fill) around an already-fast kernel. A windowed Settings
    # scroll issues ~94 rects + ~29 prints a frame.
    #
    # The gates read live state through a shared moy_gfx DrawCtx: an array('i')
    # this class keeps in step (camera/clip/reset_state/pal/set_font_scale, all
    # cold paths that already call flush_batch) plus a 64-entry pal-resolved
    # RGB565 table. The Python methods stay as the gates' `fallback` for anything
    # unusual (kwargs, odd arity, a non-numeric coord, a non-string print), so
    # semantics are unchanged -- this is purely a fast lane.

    def set_viewport(self, x, y, w, h):
        """Point this canvas at the (x, y, w, h) sub-rect of its own buffer (#155).

        Host twin: runtime/canvas.py Canvas.set_viewport -- see it for why. On
        this backend it also re-seeds the native draw gates, whose state array
        carries the stride and the buffer-space clip."""
        # Clamped to the buffer -- see the host twin for why (a window can hang
        # off the screen edge, and an unclamped write runs past the end).
        ox = max(0, int(x))
        oy = max(0, int(y))
        self._ox = ox
        self._oy = oy
        self.w = max(0, min(int(w), self._stride - ox))
        self.h = max(0, min(int(h), self._bh - oy))
        st = self._gate_state
        if st is not None:
            st[_ST_W] = self._stride
            st[_ST_H] = self._bh
        self.reset_state()

    def clear_viewport(self):
        """Back to owning the whole buffer."""
        self._ox = 0
        self._oy = 0
        self.w = self._stride
        self.h = self._bh
        self.reset_state()

    def retarget(self, buf):
        """Re-point at another RGB565 buffer of the SAME stride -- the DPI
        ping-pong swaps the framebuffer every frame, so a viewport canvas onto it
        must follow (the root canvas does this in sync_back)."""
        self._buf = buf
        self._fb = self._fb_by_buf.get(id(buf)) or self._fb
        if self._gate_ctx is not None:
            self._gate_ctx.set_buf(buf)

    def _install_draw_gates(self):
        """Swap in the native rect/rectb/print/pix. Returns True if gated."""
        gfx = self._gfx
        if gfx is None or _FONT8 is None:
            return False
        make_ctx = getattr(gfx, "make_draw_ctx", None)
        if make_ctx is None:
            return False               # older firmware: keep the Python verbs
        if self._pump is not None:
            # T-Deck ROOT canvas only: _fill pokes the SRAM-bounce flush pump
            # between native ops (#66), and a C gate has no cheap way back into
            # Python to do that. Layers on both boards and the P4 root have no
            # pump, so everything that matters here still gates.
            return False
        st = array("i", bytearray(4 * _ST_LEN))
        pal = array("H", bytearray(2 * 64))
        try:
            ctx = make_ctx(self, st, pal, self._batch_arr, _FONT8, _FONT8_FIRST)
        except Exception:  # noqa: BLE001 -- never let a probe break a canvas
            return False
        # Grab the bound Python methods BEFORE shadowing them: they become the
        # gates' fallbacks (and on P4SystemCanvas that correctly picks up the
        # font_scale-aware print override).
        fb_rect, fb_rectb = self.rect, self.rectb
        fb_print, fb_pix = self.print, self.pix
        self._gate_state = st
        self._gate_pal = pal
        self._gate_ctx = ctx
        st[_ST_W] = self._stride
        st[_ST_H] = self._bh
        st[_ST_FONT_SCALE] = max(1, int(getattr(self, "font_scale", 1)))
        self._sync_gate_state()
        self._sync_gate_pal()
        ctx.set_buf(self._buf)
        mk = gfx.make_draw_gate
        self.rect = mk(ctx, _GATE_RECT, fb_rect)
        self.rectb = mk(ctx, _GATE_RECTB, fb_rectb)
        self.print = mk(ctx, _GATE_PRINT, fb_print)
        self.pix = mk(ctx, _GATE_PIX, fb_pix)
        return True

    def _sync_gate_state(self):
        st = self._gate_state
        if st is not None:
            st[_ST_CAM_X] = self._cam_x
            st[_ST_CAM_Y] = self._cam_y
            st[_ST_CX0] = self._clip_x0
            st[_ST_CY0] = self._clip_y0
            st[_ST_CX1] = self._clip_x1
            st[_ST_CY1] = self._clip_y1

    def _sync_gate_pal(self):
        pal = self._gate_pal
        if pal is None:
            return
        pm = self._pal_map
        if pm is None:
            return
        for i in range(64):
            pal[i] = PAL565_WIRE[pm[i]]

    def gate_counts(self):
        """(fills, texts, fill_us, text_us) drawn through the native gates since
        the last gate_counts_reset -- the on-glass proof the fast lane is live."""
        st = self._gate_state
        if st is None:
            return (0, 0, 0, 0)
        return (st[_ST_N_FILL], st[_ST_N_TEXT], st[_ST_T_FILL], st[_ST_T_TEXT])

    def gate_counts_reset(self):
        st = self._gate_state
        if st is not None:
            st[_ST_N_FILL] = 0
            st[_ST_N_TEXT] = 0
            st[_ST_T_FILL] = 0
            st[_ST_T_TEXT] = 0

    def _col(self, c):
        # Resolve a draw index to RGB565 through the pal remap, so cls/pix/line/rect/
        # circ/circb/rectb all honour pal() for free.
        return PAL565_WIRE[self._pal_map[c & 63]]

    def _fill(self, x, y, w, h, col):
        # Filled rect of a pre-resolved RGB565 colour, camera-offset and intersected
        # with the clip rect; native (clamped in C) when moy_gfx is present, else
        # framebuf. Shared by rect()/circ()/rectb().
        #
        # HOT PATH -- this is the console CHROME's dominant verb (a picker grid draw
        # issues ~155 of them). On-glass P4 2026-07-25: the native fill_rect kernel
        # costs 6us, but this wrapper made the whole call ~96us, and pixels are
        # nearly free (a 1x1 fill and a 181x121 fill differ by 0.11ms/21901px =
        # 5ns/px). So the ENTIRE desktop-UI draw budget was wrapper overhead, and
        # every microsecond removed here shows up across every surface. Hence:
        # attributes hoisted into locals (each self.X is an interpreter dict
        # lookup), min/max builtin calls replaced with comparisons, and the DRAW2
        # timing gated behind _prof -- the ticks_us pair alone measured 6us/call and
        # it used to run on EVERY fill in a shipping build, not just under perf
        # capture. Keep it lookup-free.
        cx = self._cam_x
        x -= cx
        y -= self._cam_y
        x0 = self._clip_x0
        y0 = self._clip_y0
        if x > x0:
            x0 = x
        if y > y0:
            y0 = y
        x1 = x + w
        y1 = y + h
        cx1 = self._clip_x1
        cy1 = self._clip_y1
        if x1 > cx1:
            x1 = cx1
        if y1 > cy1:
            y1 = cy1
        if x1 <= x0 or y1 <= y0:
            return
        gfx = self._gfx
        if gfx is not None:
            if self._prof:
                _t0 = _ticks_us()      # #66 DRAW2: fill bucket (rect/rectb/circ spans)
                gfx.fill_rect(self._buf, self._stride, x0, y0, x1 - x0, y1 - y0, col)
                self._t_fill_us += _ticks_diff(_ticks_us(), _t0)
            else:
                gfx.fill_rect(self._buf, self._stride, x0, y0, x1 - x0, y1 - y0, col)
            if self._pump is not None:
                self._pump()           # #66: feed the bounce flush between native ops
        else:
            self._fb.fill_rect(x0, y0, x1 - x0, y1 - y0, col)

    def _put(self, x, y, col):
        # Single clipped, camera-offset framebuf pixel write (pal already applied in
        # the resolved `col`). Used by pix/line/circb so they honour camera+clip.
        x -= self._cam_x
        y -= self._cam_y
        if self._clip_x0 <= x < self._clip_x1 and self._clip_y0 <= y < self._clip_y1:
            self._fb.pixel(x, y, col)

    def cls(self, c=0):
        # Full-surface reset: ignores camera/clip (like TIC-80) but honours pal.
        self.flush_batch()             # #63: a non-spr primitive breaks the batch
        if self._lcopy is not None:    # #54 St.2: a predicted layer restore is in
            self._drain_lcopy()        # flight for a frame that ISN'T drawing the
                                       # layer (screen switch) -- drain, don't race
        col = self._col(c)
        # A whole-surface clear is the biggest single fill the console issues, so
        # it is the first thing to hand to the DMA engine (#155).
        pf = self._ppa_fill
        if pf is not None and pf(self._ox, self._oy, self.w, self.h, col):
            return
        if self._gfx is not None:
            _t0 = _ticks_us()          # #66 DRAW2: fill bucket (cls is its big half)
            if self._ox == 0 and self._oy == 0 and self.w == self._stride:
                self._gfx.fill(self._buf, self.w * self.h, col)
            else:
                # #155: "full-surface" means the VIEWPORT -- a windowed layer
                # clearing itself must not wipe the desktop it draws on.
                self._gfx.fill_rect(self._buf, self._stride,
                                    self._ox, self._oy, self.w, self.h, col)
            self._t_fill_us += _ticks_diff(_ticks_us(), _t0)
            if self._pump is not None:
                self._pump()           # #66: feed the bounce flush between native ops
        else:
            self._fb.fill(col)

    def pix(self, x, y, c=None):
        # TIC-80 pix: read the index with two args, set it with three. Reads are
        # camera-relative; the buffer holds RGB565 so a read returns that, not an index.
        # Flush the pending sprite batch first so a WRITE keeps draw order and a READ
        # never samples a stale pixel under a queued-but-unblitted sprite (#63).
        self.flush_batch()
        x = int(x) - self._cam_x
        y = int(y) - self._cam_y
        if c is None:
            return self._fb.pixel(x, y)
        if self._clip_x0 <= x < self._clip_x1 and self._clip_y0 <= y < self._clip_y1:
            self._fb.pixel(x, y, self._col(c))

    def line(self, x1, y1, x2, y2, c):
        # Bresenham through _put so camera+clip+pal apply (matches the host rasterizer
        # pixel-for-pixel; framebuf.line can't clip to an arbitrary rect).
        self.flush_batch()             # #63: a non-spr primitive breaks the batch
        x0 = int(x1); y0 = int(y1); xe = int(x2); ye = int(y2)
        col = self._col(c)
        if self._gfx is not None:
            self._gfx.line(self._buf, self._stride, self._bh, x0, y0, xe, ye, col,
                           self._cam_x, self._cam_y,
                           self._clip_x0, self._clip_y0, self._clip_x1, self._clip_y1)
            return
        dx = abs(xe - x0); dy = -abs(ye - y0)
        sx = 1 if x0 < xe else -1
        sy = 1 if y0 < ye else -1
        err = dx + dy
        while True:
            self._put(x0, y0, col)
            if x0 == xe and y0 == ye:
                break
            e2 = 2 * err
            if e2 >= dy:
                err += dy; x0 += sx
            if e2 <= dx:
                err += dx; y0 += sy

    def rect(self, x, y, w, h, c):
        # TIC-80 rect = FILLED rectangle. HOT PATH (see _fill): the batch break is
        # inlined to the array-header test so an already-empty batch costs a load
        # instead of a method call, and _col is inlined for the same reason.
        if self._batch_arr[0] > 4:
            self.flush_batch()         # #63: a non-spr primitive breaks the batch
        self._fill(int(x), int(y), int(w), int(h),
                   PAL565_WIRE[self._pal_map[c & 63]])

    def rectb(self, x, y, w, h, c):
        # TIC-80 rectb = rectangle outline (4 clipped fills, like the host).
        if self._batch_arr[0] > 4:
            self.flush_batch()         # #63: a non-spr primitive breaks the batch
        x = int(x); y = int(y); w = int(w); h = int(h)
        col = PAL565_WIRE[self._pal_map[c & 63]]
        self._fill(x, y, w, 1, col)
        self._fill(x, y + h - 1, w, 1, col)
        self._fill(x, y, 1, h, col)
        self._fill(x + w - 1, y, 1, h, col)

    def fill_rects(self, arr, n=-1, ox=0, oy=0, c=-1):
        # #163 span-batch: n packed int16 quads (x, y, w, h, ci) in ONE call.
        # Native lane: the shared DrawCtx loops gate_fill in C (camera/clip/pal
        # identical to the rect gate, sprite-batch flushed once at entry).
        # Fallback mirrors the host loop through self.rect. Host twin:
        # runtime/canvas.py Canvas.fill_rects.
        ctx = self._gate_ctx
        if ctx is not None:
            ctx.fill_rects(arr, -1 if n is None else n, ox, oy, c)
            return
        # #167: the T-Deck ROOT canvas never installs the gates (its _fill pokes
        # the SRAM-bounce pump, see _install_draw_gates), so without this lane the
        # "batch" below is one INTERPRETER rect() per span -- the exact dispatch
        # cost #163 exists to delete, silently absent on that board. fill_spans
        # takes buffer/camera/clip as plain args like circ/line, so it works on
        # every canvas whether or not the gate is there.
        gfx = self._gfx
        fs = None if gfx is None else getattr(gfx, "fill_spans", None)
        if fs is not None:
            self.flush_batch()         # #63: a non-spr primitive breaks the batch
            col = -1 if c < 0 else PAL565_WIRE[self._pal_map[c & 63]]
            fs(self._buf, self._stride, self._bh, arr,
               -1 if n is None else n, ox, oy, col,
               None if col >= 0 else self._wire_pal(),
               self._cam_x, self._cam_y,
               self._clip_x0, self._clip_y0, self._clip_x1, self._clip_y1)
            if self._pump is not None:
                self._pump()           # #66: feed the bounce flush between native ops
            return
        if n < 0 or n is None:
            n = len(arr) // 5
        rect = self.rect
        for i in range(0, n * 5, 5):
            rect(arr[i] + ox, arr[i + 1] + oy, arr[i + 2], arr[i + 3],
                 c if c >= 0 else arr[i + 4])

    def _wire_pal(self):
        """A 64-entry RGB565 table for fill_spans' per-quad colour lookup, rebuilt
        only when the pal state changes (`_palgen` is the same content id the
        sprite bakes key on)."""
        if self._wire_pal_gen != self._palgen or self._wire_pal_arr is None:
            wp = self._wire_pal_arr
            if wp is None:
                wp = array("H", bytearray(2 * 64))
                self._wire_pal_arr = wp
            pm = self._pal_map
            for i in range(64):
                wp[i] = PAL565_WIRE[pm[i]]
            self._wire_pal_gen = self._palgen
        return self._wire_pal_arr

    def circ(self, cx, cy, r, c):
        # TIC-80 circ = FILLED circle. Native (#43): one moy_gfx.circ call rasterizes
        # the scanline spans in C (was 2r+1 MP->C _fill calls); else the Python path.
        self.flush_batch()             # #63: a non-spr primitive breaks the batch
        cx = int(cx); cy = int(cy); r = int(r)
        col = self._col(c)
        if self._gfx is not None:
            self._gfx.circ(self._buf, self._stride, self._bh, cx, cy, r, col,
                           self._cam_x, self._cam_y,
                           self._clip_x0, self._clip_y0, self._clip_x1, self._clip_y1)
            return
        for dy in range(-r, r + 1):
            span = int((r * r - dy * dy) ** 0.5)
            self._fill(cx - span, cy + dy, 2 * span + 1, 1, col)

    def circb(self, cx, cy, r, c):
        # TIC-80 circb = circle outline. Native (#43): one moy_gfx.circb call runs the
        # Bresenham midpoint circle in C (was ~8r MP->C _put calls); else Python.
        self.flush_batch()             # #63: a non-spr primitive breaks the batch
        cx = int(cx); cy = int(cy); r = int(r)
        col = self._col(c)
        if self._gfx is not None:
            self._gfx.circb(self._buf, self._stride, self._bh, cx, cy, r, col,
                            self._cam_x, self._cam_y,
                            self._clip_x0, self._clip_y0, self._clip_x1, self._clip_y1)
            return
        x = r; y = 0; err = 0
        while x >= y:
            for px, py in ((x, y), (y, x), (-y, x), (-x, y), (-x, -y), (-y, -x), (y, -x), (x, -y)):
                self._put(cx + px, cy + py, col)
            y += 1
            if err <= 0:
                err += 2 * y + 1
            else:
                x -= 1
                err -= 2 * x + 1

    def tri(self, x1, y1, x2, y2, x3, y3, c):
        # TIC-80 tri = FILLED triangle (#167). Scanline spans packed once, then ONE
        # fill_rects -- which on this backend is one MP->C crossing for the whole
        # triangle (the #163 native lane), not one per row. That ratio is what makes
        # software 3D affordable here. Host twin: runtime/canvas.py Canvas.tri.
        self.flush_batch()             # #63: a non-spr primitive breaks the batch
        spans = tri_spans(x1, y1, x2, y2, x3, y3)
        if spans:
            self.fill_rects(array("h", spans), len(spans) // 5, 0, 0, int(c) & 63)

    def trib(self, x1, y1, x2, y2, x3, y3, c):
        # TIC-80 trib = triangle outline (three lines, like rectb's four fills).
        self.flush_batch()             # #63: a non-spr primitive breaks the batch
        self.line(x1, y1, x2, y2, c)
        self.line(x2, y2, x3, y3, c)
        self.line(x3, y3, x1, y1, c)

    def sspr(self, sheet, sx, sy, sw, sh, dx, dy, dw=None, dh=None,
             colorkey=-1, flip=0):
        # Stretch a sw x sh PIXEL sheet region into a dw x dh destination rect --
        # arbitrary scale, unlike spr()'s integer one (#167). Nearest-neighbour, and
        # per-destination-pixel by nature (every pixel is a different texel), so this
        # is the correctness lane: a cart leaning on it in a frame loop wants the
        # native kernel first. Host twin: runtime/canvas.py Canvas.sspr.
        self.flush_batch()             # #63: a non-spr primitive breaks the batch
        sx = int(sx); sy = int(sy); sw = int(sw); sh = int(sh)
        dx = int(dx); dy = int(dy)
        dw = sw if dw is None else int(dw)
        dh = sh if dh is None else int(dh)
        if sw <= 0 or sh <= 0 or dw <= 0 or dh <= 0:
            return
        flip = int(flip)
        fx = flip & 1
        fy = (flip >> 1) & 1
        ck = int(colorkey)
        pt = self._palt
        pget = sheet.pget
        put = self._put
        pal = self._pal_map
        for j in range(dh):
            v = (j * sh) // dh
            if fy:
                v = sh - 1 - v
            row_y = sy + v
            ty = dy + j
            for i in range(dw):
                u = (i * sw) // dw
                if fx:
                    u = sw - 1 - u
                p = pget(sx + u, row_y)
                if p == ck:
                    continue
                p &= 63
                if pt is not None and pt[p]:
                    continue
                put(dx + i, ty, PAL565_WIRE[pal[p]])

    def spr(self, img, x, y, scale=1, flip=0):
        # TIC-80 flip: 0=none, 1=h, 2=v, 3=both (#11). Camera offsets the dst; the
        # clip rect is passed to the native blit (or honoured in the fallback). pal +
        # palt are baked into the cached RGB565 copy (re-baked when _palgen changes).
        # An Image blit is NOT part of an auto-batch (#63): flush any pending sheet-tile
        # run first (so it lands underneath), then draw this one immediately.
        self.flush_batch()
        x = int(x) - self._cam_x
        y = int(y) - self._cam_y
        scale = int(scale)
        flip = int(flip)
        if scale < 1:
            scale = 1
        if self._gfx is None:
            self._spr_py(img, x, y, scale, flip)
            return
        # Paint-image fast path (#63 Fold 3): a big MOY64 index bitmap (images/*.moyimg)
        # bakes to RGB565 ONCE via the native blit_indices kernel (one C call over the
        # whole bitmap, NOT the per-pixel _cache_rgb loop over ~77k px), cached on the
        # Image by identity; then blit565 stamps it opaquely per frame. Only the 1:1
        # placement under an identity palette takes it (a scaled/flipped/recoloured paint
        # image falls through to the general cached path -- correct, just slower once).
        # The clean full-screen-background path is spr(bg, 0, 0) into a make_layer once,
        # then draw_layer per frame -- the bake happens off the per-frame hot path.
        if (getattr(img, "_paint", False) and scale == 1 and flip == 0
                and self._palgen == 0):
            if getattr(img, "_rgb_i", None) is None:
                self._bake_indices(img)
            self._gfx.blit565(self._buf, self._stride, self._bh, x, y,
                              img._rgb_i, img.w, img.h, -1,
                              self._clip_x0, self._clip_y0, self._clip_x1, self._clip_y1)
            return
        # Blit a cached, pre-scaled+flipped+pal-applied RGB565 copy in one C call. The
        # cache lives on the Image (sheet tiles are reused across frames via the
        # make_api tile cache, so the rebuild is once-per-(sprite,scale,flip,pal)).
        # #63 fast-by-default: the last-used bake is the hot single slot; on a miss the
        # per-Image VARIANT dict ((scale, flip, pal-state-id) -> bake) is consulted
        # before re-baking, so a sprite drawn at alternating tints/scales each frame
        # (the pal()/spr()/pal() kid idiom, glyphs at 2 sizes, ...) bakes each variant
        # ONCE and swaps, instead of the per-frame per-pixel rebake #72 diagnosed.
        if (getattr(img, "_rgb", None) is None
                or getattr(img, "_rgb_scale", 0) != scale
                or getattr(img, "_rgb_flip", -1) != flip
                or getattr(img, "_rgb_palgen", -1) != self._palgen):
            if not self._rgb_variant(img, scale, flip):
                self._cache_rgb(img, scale, flip)
        self._gfx.blit565(self._buf, self._stride, self._bh, x, y,
                          img._rgb, img._rgb_w, img._rgb_h, _RGB_KEY,
                          self._clip_x0, self._clip_y0, self._clip_x1, self._clip_y1)

    def _rgb_variant(self, img, scale, flip):
        # Promote a previously-baked (scale, flip, pal-state) variant into the hot
        # single slot (reference swaps, no pixel work). False -> the caller re-bakes.
        var = getattr(img, "_rgb_variants", None)
        if var is None:
            return False
        v = var.get((scale, flip, self._palgen))
        if v is None:
            return False
        img._rgb, img._rgb_w, img._rgb_h = v
        img._rgb_scale = scale
        img._rgb_flip = flip
        img._rgb_palgen = self._palgen
        return True

    def _cache_rgb(self, img, scale, flip=0):
        # Bake the indexed sprite into an RGB565 buffer at `scale`, mirrored per
        # `flip`, with pal remap + palt transparency applied; transparent pixels set
        # to _RGB_KEY so blit565 skips them. Built rarely (cached), so the per-pixel
        # loop here is fine -- it's the per-frame blit that matters.
        import framebuf

        w = img.w * scale
        h = img.h * scale
        buf = bytearray(w * h * 2)
        fb = framebuf.FrameBuffer(buf, w, h, framebuf.RGB565)
        fb.fill(_RGB_KEY)
        pal = PAL565_WIRE
        pmap = self._pal_map
        palt = self._palt
        t = img.transparent
        pix = img.pix
        iw = img.w
        ih = img.h
        fx = flip & 1
        fy = (flip >> 1) & 1
        for sy in range(ih):
            ssy = (ih - 1 - sy) if fy else sy
            base = ssy * iw
            for sx in range(iw):
                ssx = (iw - 1 - sx) if fx else sx
                p = pix[base + ssx]
                if p == t or p < 0 or palt[p & 63]:
                    continue
                col = pal[pmap[p & 63]]
                if col == _RGB_KEY:
                    col ^= 0x20          # nudge a visible pixel off the colour-key
                fb.fill_rect(sx * scale, sy * scale, scale, scale, col)
        img._rgb = buf
        img._rgb_w = w
        img._rgb_h = h
        img._rgb_scale = scale
        img._rgb_flip = flip
        img._rgb_palgen = self._palgen
        # #63 fast-by-default: remember this bake in the per-Image variant dict so a
        # later return to this (scale, flip, tint) swaps references instead of
        # re-baking. Capped small: sprites are tiny (a variant is w*h*scale^2*2B) but
        # a pathological cart could churn states -- past 6 variants, drop and re-learn.
        var = getattr(img, "_rgb_variants", None)
        if var is None:
            var = img._rgb_variants = {}
        elif len(var) >= 6:
            var.clear()
        var[(scale, flip, self._palgen)] = (buf, w, h)
        self._rgb_bakes += 1

    def _bake_indices(self, img):
        # Bake a paint image's MOY64 indices -> an opaque RGB565 buffer ONCE via the
        # native blit_indices kernel (index -> PAL565_WIRE converted in C, ~ms for a full
        # 320x240), cached on the Image as _rgb_i; spr() then blit565s it every frame.
        # The "images are data, not draw calls" bake (#63 Fold 3), off the hot path.
        w = img.w
        h = img.h
        buf = bytearray(w * h * 2)
        self._gfx.blit_indices(buf, w, h, 0, 0, img.pix, w, h, _PAL565_WIRE_BUF)
        img._rgb_i = buf

    def _spr_py(self, img, x, y, scale, flip=0):
        # Per-pixel fallback when moy_gfx is absent (image built without it). Honours
        # camera (applied by the caller into x,y), clip, pal, palt, and flip.
        pal = PAL565_WIRE
        pmap = self._pal_map
        palt = self._palt
        t = img.transparent
        iw = img.w
        ih = img.h
        pix = img.pix
        fx = flip & 1
        fy = (flip >> 1) & 1
        for sy in range(ih):
            ssy = (ih - 1 - sy) if fy else sy
            base = ssy * iw
            for sx in range(iw):
                ssx = (iw - 1 - sx) if fx else sx
                p = pix[base + ssx]
                if p == t or p < 0 or palt[p & 63]:
                    continue
                col = pal[pmap[p & 63]]
                # Clipped fill block (camera already applied into x,y).
                bx = x + sx * scale
                by = y + sy * scale
                x0 = max(self._clip_x0, bx)
                y0 = max(self._clip_y0, by)
                x1 = min(self._clip_x1, bx + scale)
                y1 = min(self._clip_y1, by + scale)
                if x1 > x0 and y1 > y0:
                    self._fb.fill_rect(x0, y0, x1 - x0, y1 - y0, col)

    def _blit_map_into(self, dst, dw, dh, dsx, dsy, tilemap, sheet, mx, my, w, h,
                       colorkey, tile, scale, cx0, cy0, cx1, cy1):
        # One native moy_gfx.blit_map into `dst` -- the framebuffer (a direct draw) or a
        # hidden cache layer (Fold 2 fill). Bakes/reuses the sheet's RGB565 tile atlas
        # (cached on the sheet, keyed on gen/colorkey/palgen) then walks the w x h region.
        atlas, ntiles = self._sheet_atlas(sheet, colorkey)
        self._gfx.blit_map(dst, dw, dh, dsx, dsy,
                           tilemap.cells, tilemap.w, tilemap.h,
                           mx, my, w, h,
                           atlas, ntiles, tile, scale, _RGB_KEY,
                           cx0, cy0, cx1, cy1)

    def map(self, tilemap, sheet, mx=0, my=0, w=None, h=None,
            sx=0, sy=0, colorkey=-1, scale=1):
        # TIC-80 map(): blit a w x h cell region of the tilemap over `sheet` to screen
        # (sx, sy). Fold 2 (#63): the rasterized region is CACHED in a hidden 565 layer so a
        # subsequent camera-only call keyed-blits it (one blit565) instead of re-walking every
        # cell (blit_map) -- the make_layer win, made automatic for a naive camera()+map()
        # cart. The cache keys on (tilemap.gen, sheet.gen, region, colorkey, scale); an mset /
        # sheet paint edit / scale change bumps the key and the region re-rasters.
        # Camera/clip/sx/sy are COMPOSITE-time (not in the key), so a scroll is a cache HIT.
        # Mirrors the host Canvas.map cache exactly (palette indices there, RGB565 here).
        #
        # pal/palt bake into the 565 layer via the sheet atlas under the parent's identity
        # state, so caching is gated to identity pal/palt (_palgen == 0); under an active
        # palette (and in the no-moy_gfx fallback) map() rasters directly to the framebuffer
        # (correctness over cleverness -- an active palette on a scrolling map is rare). The
        # sheet atlas is still baked once (cached on the sheet, keyed on gen/colorkey/palgen).
        self.flush_batch()             # #63: map() is a non-spr primitive -> break batch
        mx = int(mx); my = int(my); scale = int(scale)
        if scale < 1:
            scale = 1
        if w is None:
            w = tilemap.w - mx
        if h is None:
            h = tilemap.h - my
        w = int(w); h = int(h)
        dsx = int(sx) - self._cam_x
        dsy = int(sy) - self._cam_y
        tile = sheet.TILE
        if self._gfx is None:
            self._map_py(tilemap, sheet, mx, my, w, h, dsx, dsy, colorkey, scale)
            return
        _t0 = _ticks_us()              # #66 DRAW2: the whole map path (raster or composite)
        if (self._nocache or self._palgen != 0     # layer / active palette / revert knob
                or not MAP_AUTO_CACHE):            # -> direct raster
            self._blit_map_into(self._buf, self._stride, self._bh, dsx, dsy,
                                tilemap, sheet, mx, my, w, h, colorkey, tile, scale,
                                self._clip_x0, self._clip_y0, self._clip_x1, self._clip_y1)
            self._t_map_us += _ticks_diff(_ticks_us(), _t0)
            if self._pump is not None:
                self._pump()           # #66: feed the bounce flush between native ops
            return
        step = tile * scale
        lw = w * step
        lh = h * step
        if lw <= 0 or lh <= 0:
            return
        key = (id(tilemap), tilemap.gen, id(sheet), getattr(sheet, "gen", 0),
               mx, my, w, h, int(colorkey), scale)
        mc = self._mapcache
        if mc is None or mc[0] != key:
            # MISS -> (re)raster the region into a hidden layer at local (0,0): fill it with
            # the transparent key so empty/colorkey cells stay transparent, then blit_map.
            # Re-use the layer buffer when the pixel dims are unchanged (only the content/key
            # changed) so a live-editing cart doesn't re-allocate (and re-gc.collect) each
            # rebuild.
            if mc is not None and mc[2] == lw and mc[3] == lh:
                layer = mc[1]
            else:
                layer = self.new_layer(lw, lh, owner="_mapcache")
            self._gfx.fill(layer._buf, lw * lh, _RGB_KEY)
            self._blit_map_into(layer._buf, lw, lh, 0, 0,
                                tilemap, sheet, mx, my, w, h, colorkey, tile, scale,
                                0, 0, lw, lh)
            # OPAQUE lane eligibility: with no colorkey and no empty cells the cached
            # region has no transparent pixel (palt is identity under the _palgen == 0
            # gate, and the atlas bake nudges accidental _RGB_KEY collisions off the
            # key), so the composite can use blit565's opaque row-memcpy lane (key=-1,
            # the #66 chrome-trim lane) instead of testing every pixel. Decided ONCE
            # per raster with a cheap cell walk; sparse maps keep the keyed blit.
            opaque = colorkey < 0
            if opaque:
                mg = tilemap.mget
                for cy in range(h):
                    for cx in range(w):
                        if mg(mx + cx, my + cy) < 0:
                            opaque = False
                            break
                    if not opaque:
                        break
            self._mapcache = mc = (key, layer, lw, lh, -1 if opaque else _RGB_KEY)
            self._map_raster_count += 1
        else:
            self._map_hits += 1
        # COMPOSITE: blit the cached region at the camera-offset (dsx, dsy), clipped --
        # keyed (skips _RGB_KEY) for sparse regions, opaque row-memcpy for full-coverage
        # ones. Overdrawing the region each frame still erases last frame's actors for
        # free, exactly like a direct map().
        layer = mc[1]
        self._gfx.blit565(self._buf, self._stride, self._bh, dsx, dsy,
                          layer._buf, lw, lh, mc[4],
                          self._clip_x0, self._clip_y0, self._clip_x1, self._clip_y1)
        self._t_map_us += _ticks_diff(_ticks_us(), _t0)
        if self._pump is not None:
            self._pump()               # #66: feed the bounce flush between native ops

    def map_cache_reset(self):
        # Zero the Fold-2 map-cache profiling counters (#63): after a run of same-key map()
        # calls _map_raster_count == 1 / _map_hits == (n-1) PROVES the region rasterized ONCE
        # and every later frame re-used the cache. The map() analogue of batch_reset.
        self._map_raster_count = 0
        self._map_hits = 0

    def _sheet_atlas(self, sheet, colorkey):
        # Bake the whole sheet into a contiguous RGB565 tile atlas (ntiles tiles of
        # TILE x TILE, tile-major) for moy_gfx.blit_map. Cached on the sheet and keyed
        # by (gen, colorkey, palgen) so a paint edit, a different colorkey, or a
        # pal/palt change rebakes; this is the map() analogue of _cache_rgb. pal remap
        # + palt transparency are applied (so map honours them, host==device).
        # Transparent indices (== colorkey, or palt) bake to _RGB_KEY -> blit_map skips.
        gen = getattr(sheet, "gen", 0)
        if (getattr(sheet, "_atlas", None) is not None
                and sheet._atlas_gen == gen and sheet._atlas_key == colorkey
                and getattr(sheet, "_atlas_palgen", -1) == self._palgen):
            return sheet._atlas, sheet._atlas_n
        tile = sheet.TILE
        ntiles = sheet.count
        tpx = tile * tile
        buf = bytearray(ntiles * tpx * 2)
        pal = PAL565_WIRE
        pmap = self._pal_map
        palt = self._palt
        cols = sheet.cols
        sw = sheet.w
        spix = sheet.pix
        key = _RGB_KEY
        pos = 0
        for n in range(ntiles):
            ox = (n % cols) * tile
            oy = (n // cols) * tile
            for ly in range(tile):
                base = (oy + ly) * sw + ox
                for lx in range(tile):
                    p = spix[base + lx]
                    if p == colorkey or palt[p & 63]:
                        col = key
                    else:
                        col = pal[pmap[p & 63]]
                        if col == key:
                            col ^= 0x20      # nudge a visible pixel off the key
                    buf[pos] = col & 0xFF
                    buf[pos + 1] = (col >> 8) & 0xFF
                    pos += 2
        sheet._atlas = buf
        sheet._atlas_n = ntiles
        sheet._atlas_gen = gen
        sheet._atlas_key = colorkey
        sheet._atlas_palgen = self._palgen
        return buf, ntiles

    def _map_py(self, tilemap, sheet, mx, my, w, h, sx, sy, colorkey, scale):
        # Per-tile fallback when moy_gfx is absent: draw each non-empty cell via the
        # framebuf spr path. Tile images cached by id so a repeat tile builds once.
        tile = sheet.TILE
        step = tile * scale
        cache = {}
        for cy in range(h):
            ty = my + cy
            py = sy + cy * step
            for cx in range(w):
                tid = tilemap.mget(mx + cx, ty)
                if tid < 0:
                    continue
                img = cache.get(tid)
                if img is None:
                    img = sheet.tile_image(tid, colorkey)
                    cache[tid] = img if img is not None else False
                if not img:
                    continue
                self._spr_py(img, sx + cx * step, py, scale)

    def begin_batch(self, sheet, colorkey=-1, scale=1, token=0):
        # Register a new batch run: flush whatever is pending, then stamp the run
        # state into the array header. Called on every run BREAK only (first item,
        # colorkey/scale change, writer change, full queue) -- never per sprite --
        # by both the Python spr_tile path (token 0) and the native spr_gate (its
        # own token), so the two writers can interleave safely.
        a = self._batch_arr
        if a[0] > 4:
            self.flush_batch()
        self._batch_sheet = sheet
        a[1] = int(colorkey)
        a[2] = int(scale)
        a[3] = token

    def spr_tile(self, sheet, tile, x, y, colorkey=-1, scale=1, flip=0):
        # Queue a 1x1 sheet-tile sprite into the pending batch instead of blitting it
        # now (Fold 1, #63). A contiguous run coalesces into ONE native moy_gfx.blit_batch
        # at flush -- so a kid's naive `for e in enemies: spr(e.tile, ...)` loop is as
        # fast as a hand-rolled spr_batch, automatically. A colorkey/scale change breaks
        # the run (flush, start anew); camera/clip/pal/palt changes break it via those
        # methods; every non-spr primitive breaks it via its own flush_batch. flip is
        # per-item -> no break. This is the PYTHON writer (console chrome, no-gfx
        # parity); a running cart's spr goes through the native spr_gate instead, which
        # appends to the SAME array from C (token-tagged, see begin_batch).
        a = self._batch_arr
        k = a[0]
        if k == 4:
            self.begin_batch(sheet, colorkey, scale, 0)
        elif (sheet is not self._batch_sheet or a[3] != 0
                or colorkey != a[1] or scale != a[2] or k + 4 > 2052):
            self.begin_batch(sheet, colorkey, scale, 0)
            k = 4
        t = int(tile)
        if t < -32768 or t > 32767:
            t = -1                     # invalid tile id -> skipped at draw
        xi = int(x)
        if xi < -32768:
            xi = -32768
        elif xi > 32767:
            xi = 32767                 # off-screen clamps stay off-screen (C clips)
        yi = int(y)
        if yi < -32768:
            yi = -32768
        elif yi > 32767:
            yi = 32767
        a[k] = t
        a[k + 1] = xi
        a[k + 2] = yi
        a[k + 3] = int(flip) & 3
        a[0] = k + 4

    def make_spr_gate(self, sheet, fallback):
        # Build the native kid-facing spr() callable (#63): a moy_gfx C object that
        # appends (tile, x, y, flip) quads to _batch_arr with NO Python call frame.
        # WHY: a Python function whose frame exceeds ~11 words heap-allocates it on
        # EVERY call, and on a warm fragmented heap that alloc costs ~1.5ms -- so a
        # kid's 120-sprite loop through the old spr closure -> spr_tile chain cost
        # ~150ms/frame (sakura's 29->12fps collapse, measured on S3). The C gate is
        # allocation-free (~2-5us/call) and delegates anything unusual (Image, w/h
        # spans, kwargs) to `fallback` -- the Python spr closure -- unchanged.
        # Returns None (caller keeps the Python path) off-gfx or on old firmware.
        g = self._gfx
        if g is None or sheet is None:
            return None
        mk = getattr(g, "make_spr_gate", None)
        if mk is None:
            return None
        _GATE_SEQ[0] = (_GATE_SEQ[0] & 0x3FFF) + 1     # int16-safe, never 0
        try:
            return mk(self, sheet, self._batch_arr, _GATE_SEQ[0], fallback)
        except Exception:  # noqa: BLE001 -- any C-side refusal -> Python path
            return None

    def flush_batch(self):
        # Emit the pending batch in queue order, then clear it. A lone item falls
        # back to a direct blit565 (no batch overhead -- no regression for a single
        # sprite between primitives); a run goes through ONE native blit_batch in
        # ARRAY MODE (the C kernel reads the int16 quads straight from _batch_arr --
        # no tuples ever exist on this path). Header reset FIRST so the re-entrant
        # flush_batch() inside spr() is a harmless no-op.
        a = self._batch_arr
        k = a[0]
        if k <= 4:
            return
        sheet = self._batch_sheet
        colorkey = a[1]
        scale = a[2]
        a[0] = 4
        self._batch_sheet = None
        n = (k - 4) >> 2               # profiling: count the run (see batch_reset, #63)
        self._batch_flushes += 1
        self._batch_sprites += n
        if n > self._batch_maxrun:
            self._batch_maxrun = n
        if sheet is None:
            return                     # defensive: state lost -> drop the quads
        if n == 1:
            tile, x, y, flip = a[4], a[5], a[6], a[7]
            img = sheet.tile_image(tile, colorkey)
            if img is not None:
                self.spr(img, x, y, scale, flip)
            return
        if self._gfx is None:
            # Fallback: per-item framebuf spr (camera+clip applied inside spr()).
            # Tile images cached by id so a repeated tile builds once.
            cache = {}
            i = 4
            while i < k:
                tid = a[i]
                if tid >= 0:
                    img = cache.get(tid)
                    if img is None:
                        img = sheet.tile_image(tid, colorkey)
                        cache[tid] = img if img is not None else False
                    if img:
                        self.spr(img, a[i + 1], a[i + 2], scale, a[i + 3])
                i += 4
            return
        atlas, ntiles = self._sheet_atlas(sheet, colorkey)
        a[0] = k                       # array mode: C reads the count from a[0]
        _t0 = _ticks_us()              # #63 DRAW2: time the native sprite batch
        self._gfx.blit_batch(self._buf, self._stride, self._bh, a,
                             atlas, ntiles, sheet.TILE, scale, _RGB_KEY,
                             self._cam_x, self._cam_y,
                             self._clip_x0, self._clip_y0,
                             self._clip_x1, self._clip_y1)
        self._t_batch_us += _ticks_diff(_ticks_us(), _t0)
        a[0] = 4
        if self._pump is not None:
            self._pump()               # #66: feed the bounce flush between native ops

    def batch_reset(self):
        # Zero the auto-batch profiling counters (#63) at the top of a frame when perf
        # capture is on. Read afterward via the Workstation's perf_batch().
        self._batch_flushes = 0
        self._batch_sprites = 0
        self._batch_maxrun = 0
        self._t_layer_us = 0        # #63 DRAW2: reset the per-frame native-op timers too
        self._t_batch_us = 0
        self._t_map_us = 0          # #66: the render-bound carts' remaining verbs
        self._t_text_us = 0
        self._t_fill_us = 0
        self._prof = True           # perf capture is on this frame -- time the ops

    def spr_batch(self, sheet, items, colorkey=-1, scale=1):
        # Draw N sheet tiles in ONE native moy_gfx.blit_batch call (#43) -- the sprite
        # analogue of map(). `items` is a list of (tile, x, y) or (tile, x, y, flip)
        # tuples (world coords; camera offsets each, clip honoured). It reuses the SAME
        # cached RGB565 tile atlas map() bakes (_sheet_atlas, keyed on sheet.gen so a
        # paint edit / colorkey / pal change rebakes), so the per-frame cost is just the
        # C walk over the items -- N per-sprite MP->C blits collapse to one call.
        self.flush_batch()             # #63: emit any auto-batched run first (z-order)
        scale = int(scale)
        if scale < 1:
            scale = 1
        tile = sheet.TILE
        if self._gfx is None:
            # Fallback: per-item framebuf spr (camera+clip applied inside spr()). Tile
            # images cached by id so a repeated tile builds once, like _map_py.
            cache = {}
            for it in items:
                tid = it[0]
                if tid < 0:
                    continue
                flip = it[3] if len(it) > 3 else 0
                img = cache.get(tid)
                if img is None:
                    img = sheet.tile_image(tid, colorkey)
                    cache[tid] = img if img is not None else False
                if not img:
                    continue
                self.spr(img, it[1], it[2], scale, flip)
            return
        atlas, ntiles = self._sheet_atlas(sheet, colorkey)
        _t0 = _ticks_us()                           # #63 DRAW2: time the native sprite batch
        self._gfx.blit_batch(self._buf, self._stride, self._bh, items,
                             atlas, ntiles, tile, scale, _RGB_KEY,
                             self._cam_x, self._cam_y,
                             self._clip_x0, self._clip_y0, self._clip_x1, self._clip_y1)
        self._t_batch_us += _ticks_diff(_ticks_us(), _t0)

    def print(self, s, x, y, c, scale=2):
        # Native petme128 text (#62): the whole string in ONE moy_gfx.text call,
        # camera + clip + pal honoured in C -- and pixel parity with the host at
        # last (same glyph bytes, staged from runtime/font.py). The legacy per-call
        # `scale` arg stays IGNORED, exactly like the host Canvas.print (system-UI
        # scaling is the #39 font_scale path, not this arg). framebuf.text (same
        # glyphs, screen-bounds clip only) remains the no-gfx / old-build fallback.
        if self._batch_arr[0] > 4:
            self.flush_batch()         # #63: print() is a non-spr primitive -> break batch
        if self._gfx_text is not None:
            # Chrome draws text by the dozen per frame, so the DRAW2 ticks pair
            # is gated here like _fill's (see its note).
            _prof = self._prof
            _t0 = _ticks_us() if _prof else 0
            self._gfx_text(self._buf, self._stride, self._bh, str(s), int(x), int(y),
                           PAL565_WIRE[self._pal_map[c & 63]],
                           _FONT8, _FONT8_FIRST, 1,
                           self._cam_x, self._cam_y,
                           self._clip_x0, self._clip_y0,
                           self._clip_x1, self._clip_y1)
            if _prof:
                self._t_text_us += _ticks_diff(_ticks_us(), _t0)
            if self._pump is not None:
                self._pump()           # #66: feed the bounce flush between native ops
            return
        self._fb.text(str(s), int(x) - self._cam_x, int(y) - self._cam_y, self._col(c))

    def blit_indices(self, indices, iw, ih, x, y):
        # Place an iw x ih palette-INDEX bitmap (1 byte/pixel) at (x, y), converting each index
        # to RGB565 via the panel-wire-order PAL565_WIRE table. The "images are data, not draw calls"
        # bake (#63 Fold 3): one native moy_gfx.blit_indices call turns a paint-app image into
        # pixels instead of thousands of rect() replays. Meant for cart load (off the per-frame
        # hot path). Opaque; bounds-clamped; per-pixel memoryview fallback when moy_gfx absent.
        self.flush_batch()             # #63: a non-spr primitive breaks the batch
        x = int(x)
        y = int(y)
        iw = int(iw)
        ih = int(ih)
        if iw <= 0 or ih <= 0:
            return
        if self._gfx is not None:
            self._gfx.blit_indices(self._buf, self._stride, self._bh, x, y,
                                   indices, iw, ih, _PAL565_WIRE_BUF)
            return
        d = memoryview(self._buf).cast("H")
        w = self.w
        h = self.h
        n = len(indices)
        pn = len(PAL565_WIRE)
        for row in range(ih):
            ty = y + row
            if ty < 0 or ty >= h:
                continue
            srow = row * iw
            drow = ty * w
            for col in range(iw):
                tx = x + col
                if tx < 0 or tx >= w:
                    continue
                si = srow + col
                if si >= n:
                    continue
                v = indices[si]
                if v >= pn:
                    continue
                d[drow + tx] = PAL565_WIRE[v]

    # -- scroll layers (#54) -------------------------------------------------

    def new_layer(self, w, h, owner=None):
        # A blank, wider RGB565 off-screen canvas the cart pre-renders a level into
        # ONCE, then window-copies per frame (draw_layer -> blit_window_from). Built
        # through a tiny _LayerComp so it reuses DeviceCanvas.__init__ verbatim and
        # shares this canvas's native moy_gfx kernel -- so map/spr/rect/... draw into it
        # pixel-identically. The buffer is allocated OFF the gc heap in PSRAM via moy_alloc
        # (see _LayerComp) so gc.collect() never marks it -- the GC-wall fix (#63): a layer
        # cart's live set stays small, keeping collect cheap and the heap unfragmented.
        #
        # COMPACT FIRST (#54/#41): a scroll cart re-execs fresh on every entry (lay=None),
        # so it re-allocates its ~384KB world each time. The previous run's layer is already
        # unpinned (you exit through the launcher: its ns is dropped + the recorder's atlas/
        # layer registry was reset) but not yet collected; under the web view's per-frame
        # JSON/command churn the PSRAM gc heap fragments and a fresh contiguous 384KB
        # eventually fails (MemoryError). Collecting right before the alloc reclaims the dead
        # layer + transient strings so the region is contiguous again.
        #
        # BIG layers only. "Cart-start only, so the ~10ms collect is invisible" was
        # wrong on two counts: the bar's strip cache also builds layers (1024x18) and
        # rebuilds them on a canvas switch, i.e. twice per gesture, and on the P4 the
        # collect is ~55ms, not 10 -- 72ms of an 86ms Settings frame at the press and
        # release edges (measured 2026-07-26). Defragmenting PSRAM only earns its
        # keep ahead of a cart-world-sized contiguous request, so small layers skip it.
        if int(w) * int(h) >= _COMPACT_MIN_PX:
            try:
                import gc
                gc.collect()
            except Exception:  # noqa: BLE001 -- gc is always present; never block a layer alloc
                pass
        lay = DeviceCanvas(_LayerComp(int(w), int(h), self._gfx))
        lay._nocache = True            # #63: a layer's own map() rasters directly (no nesting)
        lay.RETAINED_FRAMES = 1        # #113: a layer is ONE persistent buffer (the class
                                       # default 2 describes the ROOT ping-pong only) -- a
                                       # windowed surface blit-scrolling its win.buf must
                                       # measure against the LAST paint, not two back
        # Layer lending (#63 leak fix): a pooled (moy_alloc-backed) buffer created for a
        # program (`owner`: "cart" via make_api, "wallpaper" via the wallpaper runner,
        # "_mapcache" for Fold 2's hidden cache) is recorded so reclaim_layers(owner)
        # can return it to _LAYER_POOL when that program dies. owner=None (console
        # chrome, tests) is never reclaimed.
        comp = lay._comp
        if owner is not None and comp.pooled:
            lent = self._lent_layers
            if lent is None:
                lent = self._lent_layers = {}
            lent.setdefault(owner, []).append((comp._buf, comp._nbytes))
        return lay

    def reclaim_layers(self, owner):
        """Return a dead program's pooled layer buffers to _LAYER_POOL for reuse
        (#63 leak fix: moy_alloc has no free(), so without this every cart re-run
        leaked its world from the heap_caps pool). Also drops the Fold-2 map cache
        (its hidden layer is program content) and any in-flight async layer copy.
        Callers probe via getattr (the host Canvas has no pool -- gc reclaims)."""
        if self._lcopy is not None:
            self._drain_lcopy()
        self._lcopy_pred = None
        self._mapcache = None
        lent = self._lent_layers
        if not lent:
            return
        for own in (owner, "_mapcache"):
            lst = lent.pop(own, None)
            if lst:
                for buf, n in lst:
                    _LAYER_POOL.setdefault(n, []).append(buf)

    def blit_window_from(self, layer, cam_x=0, cam_y=0):
        # Copy the visible self.w x self.h window of `layer` into the framebuffer at
        # (cam_x, cam_y): native moy_gfx.blit_window (one flat per-row memcpy, ~7ms for
        # a full frame) when present, else a memoryview row-copy fallback (no framebuf,
        # so it also runs under the host parity test). Overwrites -- it's the background,
        # drawn first each frame, erasing last frame's sprites for free.
        # #63: flush BOTH sides -- this canvas's queued sprites (drawn, then overwritten
        # by the opaque copy, exactly as immediate mode) and the source layer's, so its
        # pixels are complete before we read them.
        self.flush_batch()
        _dirty = getattr(layer, "_batch_arr", None)
        _dirty = _dirty is not None and _dirty[0] > 4    # layer edited THIS frame
        _fb = getattr(layer, "flush_batch", None)
        if _fb is not None:
            _fb()
        cam_x = int(cam_x)
        cam_y = int(cam_y)
        if cam_x < 0:
            cam_x = 0
        if cam_y < 0:
            cam_y = 0
        # Async layer copy (#54 Stage 2): if sync_back predicted THIS restore and
        # kicked it on the GDMA engine at frame start, the ~7ms copy overlapped the
        # cart's _update -- just wait out the tail (usually ~0) and we're done.
        # A mispredicted in-flight copy is harmless: it painted a full-screen
        # background that the sync path below fully overwrites. A layer that was
        # EDITED this frame is a forced miss (the pre-kicked copy read stale
        # pixels), so live layer edits stay exact at the cost of that frame's
        # overlap.
        pend = self._lcopy
        if pend is not None:
            self._lcopy = None
            _t0 = _ticks_us()
            _ok = True
            try:
                _ok = self._gfx.copy_wait()
            except Exception:  # noqa: BLE001
                self._async_ok = False
            if _ok is False:
                # copy_wait TRIPPED (#66): the GDMA copy hadn't finished within
                # the bounded spin. Count it and force the miss path -- the sync
                # blit below rewrites the same region, so pixels stay correct
                # even if the late copy still lands (same source bytes).
                self._lcopy_trips += 1
                hit = False
            else:
                hit = (not _dirty and pend[0] is layer and pend[1] == cam_y
                       and cam_x == 0 and layer.w == self.w)
            self._t_layer_us += _ticks_diff(_ticks_us(), _t0)
            if hit:
                self._arm_layer_pred(layer, cam_x, cam_y)
                return
        if self._gfx is not None:
            _t0 = _ticks_us()                       # #63 DRAW2: time the native window-copy
            self._gfx.blit_window(self._buf, self.w, self.h,
                                  layer._buf, layer.w, cam_x, cam_y)
            self._t_layer_us += _ticks_diff(_ticks_us(), _t0)
            self._arm_layer_pred(layer, cam_x, cam_y)
            if self._pump is not None:
                self._pump()           # #66: feed the bounce flush between native ops
            return
        d = memoryview(self._buf).cast("H")
        s = memoryview(layer._buf).cast("H")
        dw = self.w
        dh = self.h
        src_w = layer.w
        if src_w <= 0 or dw <= 0 or dh <= 0:
            return
        if cam_x + dw > src_w:
            dw = src_w - cam_x
        if dw <= 0:
            return
        src_rows = len(s) // src_w
        if cam_y + dh > src_rows:
            dh = src_rows - cam_y
        if dh <= 0:
            return
        for row in range(dh):
            d0 = row * dw
            s0 = (cam_y + row) * src_w + cam_x
            d[d0:d0 + dw] = s[s0:s0 + dw]

    def _arm_layer_pred(self, layer, cam_x, cam_y):
        # Arm next frame's async restore (#54 Stage 2) -- ONLY when the copy shape
        # is a single contiguous memcpy covering the whole screen: cam_x==0, the
        # layer exactly screen-wide, and cam_y + screen height inside the layer
        # (a misprediction then just paints a background the sync path repaints).
        # Scroll carts with WIDER layers (Sky Run) keep the sync blit_window; the
        # static full-screen shape (sakura) is the one that wins the overlap.
        if not self._async_ok or cam_x != 0 or layer.w != self.w:
            return
        lbuf = getattr(layer, "_buf", None)
        if lbuf is None:
            return
        if (cam_y + self.h) * self.w * 2 > len(lbuf):
            return
        self._lcopy_pred = (layer, cam_y, self.w * self.h)

    def blit_strip(self, layer, dst_x=0, dst_y=0):
        # Copy ALL of `layer` (its full layer.w x layer.h RGB565 buffer) into the
        # framebuffer with its top-left at (dst_x, dst_y), opaquely -- the positioned,
        # partial-height sibling of blit_window_from (which window-copies a full-screen
        # slice of a WIDER source). Used to stamp a cached top-bar strip each frame
        # instead of re-rendering it (#43 chrome cache). Native via moy_gfx.blit565 with
        # key=-1 (fully opaque) -- the same C kernel the sprite path uses, clamped to the
        # framebuffer in C; else a memoryview row-copy fallback (mirrors the host index
        # copy + the parity stub). Ignores camera/clip (it's chrome over a finished frame).
        self.flush_batch()             # #63: emit this canvas's queued sprites first
        _fb = getattr(layer, "flush_batch", None)
        if _fb is not None:
            _fb()                      # ... and complete the source strip's pixels
        dst_x = int(dst_x)
        dst_y = int(dst_y)
        sw = layer.w
        sh = layer.h
        if self._gfx is not None:
            self._gfx.blit565(self._buf, self._stride, self._bh,
                              dst_x + self._ox, dst_y + self._oy,
                              layer._buf, sw, sh, -1,
                              self._ox, self._oy,
                              self._ox + self.w, self._oy + self.h)
            return
        d = memoryview(self._buf).cast("H")
        s = memoryview(layer._buf).cast("H")
        dw = self.w
        dh = self.h
        if sw <= 0 or dw <= 0 or dh <= 0:
            return
        for row in range(sh):
            ty = dst_y + row
            if ty < 0 or ty >= dh:
                continue
            s0 = row * sw
            cw = sw
            sx0 = 0
            tx0 = dst_x
            if tx0 < 0:
                sx0 = -tx0
                cw += tx0
                tx0 = 0
            if tx0 + cw > dw:
                cw = dw - tx0
            if cw <= 0:
                continue
            d0 = ty * dw + tx0
            d[d0:d0 + cw] = s[s0 + sx0:s0 + sx0 + cw]

    def blit_strip_rect(self, layer, dst_x, dst_y, rx, ry, rw, rh):
        # blit_strip restricted to the destination rect (rx, ry, rw, rh) -- the
        # #58 dirty-union drag/resize restore primitive (see the host twin in
        # runtime/canvas.py). Native: moy_gfx.blit565's dest-space clip rect, so
        # only the rect's bytes are copied (the opaque lane memcpys the clipped
        # span per row); rows outside the clip are one compare each. Fallback:
        # the blit_strip row loop with the extra bounds.
        self.flush_batch()             # #63: emit this canvas's queued sprites first
        _fb = getattr(layer, "flush_batch", None)
        if _fb is not None:
            _fb()
        dst_x = int(dst_x)
        dst_y = int(dst_y)
        rx = int(rx)
        ry = int(ry)
        if rw <= 0 or rh <= 0:
            return
        sw = layer.w
        sh = layer.h
        if self._gfx is not None:
            cx0 = self._ox + rx
            cy0 = self._oy + ry
            if cx0 < self._ox:
                cx0 = self._ox
            if cy0 < self._oy:
                cy0 = self._oy
            cx1 = self._ox + rx + rw
            cy1 = self._oy + ry + rh
            if cx1 > self._ox + self.w:
                cx1 = self._ox + self.w
            if cy1 > self._oy + self.h:
                cy1 = self._oy + self.h
            self._gfx.blit565(self._buf, self._stride, self._bh,
                              dst_x + self._ox, dst_y + self._oy,
                              layer._buf, sw, sh, -1, cx0, cy0, cx1, cy1)
            return
        d = memoryview(self._buf).cast("H")
        s = memoryview(layer._buf).cast("H")
        dw = self.w
        dh = self.h
        if sw <= 0 or dw <= 0 or dh <= 0:
            return
        cx0 = max(0, rx)
        cy0 = max(0, ry)
        cx1 = min(dw, rx + rw)
        cy1 = min(dh, ry + rh)
        for row in range(sh):
            ty = dst_y + row
            if ty < cy0 or ty >= cy1:
                continue
            sx0 = max(0, cx0 - dst_x)
            sx1 = min(sw, cx1 - dst_x)
            if sx0 >= sx1:
                continue
            s0 = row * sw
            d0 = ty * dw + (dst_x + sx0)
            d[d0:d0 + (sx1 - sx0)] = s[s0 + sx0:s0 + sx1]

    def scroll_rect(self, rx, ry, rw, rh, dx, dy):
        # Shift the pixels inside rect (rx, ry, rw, rh) by (dx, dy) IN PLACE --
        # the #113 scroll-as-blit primitive (host twin: runtime/canvas.py
        # Canvas.scroll_rect, same semantics: exposed strips keep stale content,
        # the caller repaints the band; ignores camera/clip/pal). Native via the
        # moy_gfx.scroll_rect row-memmove kernel; else a bytearray row loop
        # (slice reads copy, so overlap is safe -- vertical order follows dy).
        self.flush_batch()             # #63: emit queued sprites into the buffer first
        dx = int(dx)
        dy = int(dy)
        if dx == 0 and dy == 0:
            return
        if self._gfx is not None:
            self._gfx.scroll_rect(self._buf, self._stride,
                                  rx + self._ox, ry + self._oy, rw, rh, dx, dy)
            return
        buf = self._buf
        w = self.w
        x0 = max(0, int(rx))
        y0 = max(0, int(ry))
        x1 = min(w, int(rx) + int(rw))
        y1 = min(self.h, int(ry) + int(rh))
        tx0 = x0 + max(0, dx)
        tx1 = x1 + min(0, dx)
        ty0 = y0 + max(0, dy)
        ty1 = y1 + min(0, dy)
        if tx0 >= tx1 or ty0 >= ty1:
            return
        cw = (tx1 - tx0) * 2
        rows = range(ty1 - 1, ty0 - 1, -1) if dy > 0 else range(ty0, ty1)
        for ty in rows:
            s0 = ((ty - dy) * w + (tx0 - dx)) * 2
            d0 = (ty * w + tx0) * 2
            buf[d0:d0 + cw] = buf[s0:s0 + cw]


class _LayerComp:
    """Minimal compositor stand-in so DeviceCanvas can back a scroll layer (#54): a
    fresh RGB565 buffer of the requested size sharing the parent's moy_gfx kernel. No
    flush / double-buffer (a layer is a draw SOURCE, never flushed), so back_buffer()
    just returns the one buffer -- a sync_back() on a layer is a harmless no-op."""

    def __init__(self, w, h, gfx):
        self._w = w
        self._h = h
        # #63 (GC wall): the layer's RGB565 buffer is the single biggest object a
        # scroll/paint cart keeps live (150KB for a full screen). A plain bytearray
        # lands in the MicroPython gc heap, so every gc.collect() MARKS it -- and
        # collect cost scales with the LIVE set (measured ~0.16ms/KB on device: launcher
        # live=407k -> 71ms, sakura live=902k -> 143ms). So a layer cart pays ~24ms/collect
        # for this buffer alone, and its bulk fragments the heap -- slowing every transient
        # float box in the kid's per-frame physics loop (the sustained 29->12fps sag we
        # measured). Allocate it OFF the gc heap in PSRAM via moy_alloc (the SAME allocator
        # the compositor framebuffers already use -- so DeviceCanvas drawing into a memoryview
        # is the proven main-framebuffer path, not a new one). gc then never scans it: the
        # cart's live set -- and thus its collect cost AND heap fragmentation -- collapses,
        # kid code untouched (fast by default). draw_layer today CPU-copies the layer into
        # the framebuffer, so DMA isn't strictly needed -- but we tag the buffer SPIRAM|DMA
        # anyway: on the S3 all PSRAM is DMA-reachable so it costs nothing, and it keeps the
        # layer eligible for the #54 Stage-2 GDMA async window-copy (off-CPU draw_layer) --
        # a SEPARATE draw-ceiling lever, orthogonal to this GC fix. Falls back to a gc-heap
        # bytearray on the host / if the DMA allocator is unavailable, so this can only match
        # or beat the old behaviour, never regress.
        nbytes = w * h * 2
        buf = None
        pooled = False
        free = _LAYER_POOL.get(nbytes)
        if free:
            buf = free.pop()          # a dead cart's buffer of the same dims -> reuse
            pooled = True
        else:
            try:
                import moy_alloc
                try:
                    import lcd_bus as _mem      # lvgl build (T-Deck): caps live here
                except ImportError:             # mainline build (P4 #58): moy_alloc
                    _mem = moy_alloc            # exports the same MEMORY_* constants
                buf = moy_alloc.malloc_dma(nbytes, _mem.MEMORY_SPIRAM | _mem.MEMORY_DMA)
                pooled = buf is not None    # heap_caps memory: pool it on reclaim (no free())
            except Exception:  # noqa: BLE001 -- host / no DMA allocator -> gc-heap bytearray
                buf = None
        if buf is None:
            buf = bytearray(nbytes)
        self._buf = buf
        self._nbytes = nbytes
        self.pooled = pooled
        self._gfx = gfx

    def size(self):
        return (self._w, self._h)

    def framebuffer(self):
        return self._buf

    def back_buffer(self):
        return self._buf

    def gfx(self):
        return self._gfx


class _Layer:
    """A scroll background (#54): a wider off-screen canvas the cart pre-renders a
    level into ONCE, then window-copies to the screen per frame via draw_layer. Exposes
    the draw verbs (sheet/tilemap-aware, pixel-identical to the main api) bound to its
    OWN canvas, plus W/H. Built by the api's make_layer(w, h)."""

    _VERBS = ("cls", "pix", "line", "rect", "rectb", "circ", "circb",
              
              "tri", "trib", "rect_batch", "sspr",
              "spr", "spr_batch", "map", "mget", "mset", "print",
              "camera", "clip", "pal", "palt")

    def __init__(self, canvas, ns):
        self._canvas = canvas
        self.W = canvas.w
        self.H = canvas.h
        for k in _Layer._VERBS:
            setattr(self, k, ns[k])
