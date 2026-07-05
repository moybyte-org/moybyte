# Moybyte v0.4 workstation -- DEVICE side.
#
# Boots the fantasy workstation on the T-Deck: a cartridge launcher + the carts,
# navigated with the keyboard/trackball, each cart drawn through the native
# moy_compositor. The drawing API (cls/pix/rect/rectb/circ/circb/spr/print/btn/...,
# TIC-80 style: rect/circ are filled, rectb/circb are outlines) matches the host
# `runtime/` reference, so cartridges are portable; only the
# canvas backend differs (framebuf over the compositor buffer + palette->RGB565).
#
# v1 embeds the cart sources; loading real .moy files from SD is the follow-on.

import time
from array import array

# Editor cores (CodeEditor / SpriteSheet / PaintEditor) are backend-agnostic and
# shared verbatim with the host (canonical: runtime/editors.py; build.sh stages a
# copy into modules/ so it freezes here as the top-level module `editors`).
from editors import CodeEditor, PaintEditor, SpriteSheet
from console import NAMES, Pointer, Workstation, _cursor_delta, color
from carts_data import CARTS  # build-time generated from system_carts/ (tools/gen_device_carts.py)
# Leaf tick + diag helpers (extracted to device_util.py so every device cluster can
# import them without a moy_runtime cycle -- see device_util.py's module docstring).
from device_util import (
    _ticks_ms, _ticks_diff, _ticks_us, _diag_note, _diag_log,
)
# The device WiFi service (#38, extracted to device_wifi.py). run_desktop calls
# make_wifi()/autoconnect_wifi(); DeviceWifi is the injected `wifi` backend.
from device_wifi import DeviceWifi, make_wifi, autoconnect_wifi
# The pointer input drivers (extracted to device_input.py): the trackball + GT911
# touch. run_desktop constructs TrackBall()/Touch(...) and feeds them to Pointer.
from device_input import TrackBall, Touch
# Serial diagnostics (extracted to device_diag.py): the between-frames logging
# functions run_desktop calls when perf capture is on. HITCH_MS + _CALIB_DONE are
# the loop's hitch threshold + one-shot calib flag (mutated in place).
from device_diag import (
    _diag_flush, _diag_perf_sample, _diag_hitch, _diag_drawbrk, _diag_draw2,
    _diag_chromebrk, _diag_pump, _diag_i2cstat, _diag_calib, _diag_gc,
    HITCH_MS, _CALIB_DONE,
)
# The device WEB VIEW controller (#41/#22, extracted to device_webview.py).
# run_desktop constructs WebView(...) and services it between frames.
from device_webview import WebView
# The device AUDIO backend (#16, extracted to device_audio.py). run_desktop wires
# ws.make_audio = make_audio; DeviceAudio is the injected I2S backend.
from device_audio import DeviceAudio, make_audio

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

# Same palette, byte-swapped to the PANEL's wire order (#43). PAL565 above is the
# canonical little-endian RGB565 (the host parity test asserts it == rgb565(MOY64));
# PAL565_SW is what we actually WRITE into the device framebuffer so the per-flush
# CPU byte-swap in lcd_bus.tx_color can be turned OFF (tdeck_display rgb565_byte_swap
# =False). That swap was ~17 ms/frame over PSRAM -- the synchronous wall left once the
# DMA-overlap flush (#43) hid the SPI transfer. Folding it into this LUT makes it free
# (the index->colour lookup happens anyway), so the kick drops from ~17 ms to ~2 ms and
# the SPI finally overlaps render. Every buffer-writing path (_col + the sprite/atlas
# bakes) uses PAL565_SW; PAL565 stays the canonical reference.
PAL565_SW = tuple(((c << 8) | (c >> 8)) & 0xFFFF for c in PAL565)
# Buffer form of PAL565_SW for the native blit_indices kernel (#63): the C reads the
# palette via the BUFFER PROTOCOL (moy_gfx_buf_r), and a tuple has none ("object with
# buffer protocol required"). An array("H") is a contiguous uint16 buffer AND still
# indexes in Python, so it serves both. (The tuple stays for the other PAL565_SW uses.)
_PAL565_SW_BUF = array("H", PAL565_SW)

# RGB565 colour-key for native sprite blits: transparent sprite pixels are baked
# to this value so moy_gfx.blit565 skips them. Magenta is absent from MOY64; a
# visible pixel that happens to equal it is nudged by one LSB when the cache is
# built (see DeviceCanvas._cache_rgb), so it can never read as transparent.
_RGB_KEY = 0xF81F

# Flip to False to force the slow Python per-pixel drawing path (no native moy_gfx)
# for an FPS A/B comparison against the native-blit build.
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
        import ubinascii as _binascii
        import deflate
        import io
        meta = _json.loads(text)
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
        self.reset_state()

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
                fb = framebuf.FrameBuffer(buf, self.w, self.h, framebuf.RGB565)
                self._fb_by_buf[id(buf)] = fb
            self._fb = fb
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
        self._cam_x = 0
        self._cam_y = 0
        self._clip_x0 = 0
        self._clip_y0 = 0
        self._clip_x1 = self.w
        self._clip_y1 = self.h
        self._pal_map = bytearray(range(64))
        self._palt = bytearray(64)          # 0 opaque, 1 transparent (default opaque)
        self._palgen = 0

    def camera(self, x=0, y=0):
        self.flush_batch()             # queued sprites belong to the OLD camera (#63)
        prev = (self._cam_x, self._cam_y)
        self._cam_x = int(x)
        self._cam_y = int(y)
        return prev

    def clip(self, x=None, y=None, w=None, h=None):
        self.flush_batch()             # queued sprites belong to the OLD clip (#63)
        if x is None:
            self._clip_x0 = 0
            self._clip_y0 = 0
            self._clip_x1 = self.w
            self._clip_y1 = self.h
            return
        x = int(x); y = int(y); w = int(w); h = int(h)
        self._clip_x0 = max(0, x)
        self._clip_y0 = max(0, y)
        self._clip_x1 = min(self.w, x + w)
        self._clip_y1 = min(self.h, y + h)

    def pal(self, c0=None, c1=None):
        self.flush_batch()             # queued sprites belong to the OLD pal map (#63)
        if c0 is None:
            for i in range(64):
                self._pal_map[i] = i
        else:
            self._pal_map[int(c0) & 63] = int(c1) & 63
        self._palgen += 1                   # invalidate cached sprite RGB (pal baked in)

    def palt(self, c=None, on=None):
        self.flush_batch()             # queued sprites belong to the OLD palt (#63)
        if c is None:
            for i in range(64):
                self._palt[i] = 0
        else:
            self._palt[int(c) & 63] = 1 if on else 0
        self._palgen += 1                   # invalidate cached sprite RGB (palt baked in)

    def _col(self, c):
        # Resolve a draw index to RGB565 through the pal remap, so cls/pix/line/rect/
        # circ/circb/rectb all honour pal() for free.
        return PAL565_SW[self._pal_map[c & 63]]

    def _fill(self, x, y, w, h, col):
        # Filled rect of a pre-resolved RGB565 colour, camera-offset and intersected
        # with the clip rect; native (clamped in C) when moy_gfx is present, else
        # framebuf. Shared by rect()/circ()/rectb().
        x -= self._cam_x
        y -= self._cam_y
        x0 = max(self._clip_x0, x)
        y0 = max(self._clip_y0, y)
        x1 = min(self._clip_x1, x + w)
        y1 = min(self._clip_y1, y + h)
        if x1 <= x0 or y1 <= y0:
            return
        if self._gfx is not None:
            _t0 = _ticks_us()          # #66 DRAW2: fill bucket (rect/rectb/circ spans)
            self._gfx.fill_rect(self._buf, self.w, x0, y0, x1 - x0, y1 - y0, col)
            self._t_fill_us += _ticks_diff(_ticks_us(), _t0)
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
        if self._gfx is not None:
            _t0 = _ticks_us()          # #66 DRAW2: fill bucket (cls is its big half)
            self._gfx.fill(self._buf, self.w * self.h, col)
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
            self._gfx.line(self._buf, self.w, self.h, x0, y0, xe, ye, col,
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
        # TIC-80 rect = FILLED rectangle.
        self.flush_batch()             # #63: a non-spr primitive breaks the batch
        self._fill(int(x), int(y), int(w), int(h), self._col(c))

    def rectb(self, x, y, w, h, c):
        # TIC-80 rectb = rectangle outline (4 clipped fills, like the host).
        self.flush_batch()             # #63: a non-spr primitive breaks the batch
        x = int(x); y = int(y); w = int(w); h = int(h)
        col = self._col(c)
        self._fill(x, y, w, 1, col)
        self._fill(x, y + h - 1, w, 1, col)
        self._fill(x, y, 1, h, col)
        self._fill(x + w - 1, y, 1, h, col)

    def circ(self, cx, cy, r, c):
        # TIC-80 circ = FILLED circle. Native (#43): one moy_gfx.circ call rasterizes
        # the scanline spans in C (was 2r+1 MP->C _fill calls); else the Python path.
        self.flush_batch()             # #63: a non-spr primitive breaks the batch
        cx = int(cx); cy = int(cy); r = int(r)
        col = self._col(c)
        if self._gfx is not None:
            self._gfx.circ(self._buf, self.w, self.h, cx, cy, r, col,
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
            self._gfx.circb(self._buf, self.w, self.h, cx, cy, r, col,
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
            self._gfx.blit565(self._buf, self.w, self.h, x, y,
                              img._rgb_i, img.w, img.h, -1,
                              self._clip_x0, self._clip_y0, self._clip_x1, self._clip_y1)
            return
        # Blit a cached, pre-scaled+flipped+pal-applied RGB565 copy in one C call. The
        # cache lives on the Image (sheet tiles are reused across frames via the
        # make_api tile cache, so the rebuild is once-per-(sprite,scale,flip,pal)).
        if (getattr(img, "_rgb", None) is None
                or getattr(img, "_rgb_scale", 0) != scale
                or getattr(img, "_rgb_flip", -1) != flip
                or getattr(img, "_rgb_palgen", -1) != self._palgen):
            self._cache_rgb(img, scale, flip)
        self._gfx.blit565(self._buf, self.w, self.h, x, y,
                          img._rgb, img._rgb_w, img._rgb_h, _RGB_KEY,
                          self._clip_x0, self._clip_y0, self._clip_x1, self._clip_y1)

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
        pal = PAL565_SW
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

    def _bake_indices(self, img):
        # Bake a paint image's MOY64 indices -> an opaque RGB565 buffer ONCE via the
        # native blit_indices kernel (index -> PAL565_SW converted in C, ~ms for a full
        # 320x240), cached on the Image as _rgb_i; spr() then blit565s it every frame.
        # The "images are data, not draw calls" bake (#63 Fold 3), off the hot path.
        w = img.w
        h = img.h
        buf = bytearray(w * h * 2)
        self._gfx.blit_indices(buf, w, h, 0, 0, img.pix, w, h, _PAL565_SW_BUF)
        img._rgb_i = buf

    def _spr_py(self, img, x, y, scale, flip=0):
        # Per-pixel fallback when moy_gfx is absent (image built without it). Honours
        # camera (applied by the caller into x,y), clip, pal, palt, and flip.
        pal = PAL565_SW
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

    def map(self, tilemap, sheet, mx=0, my=0, w=None, h=None,
            sx=0, sy=0, colorkey=-1, scale=1):
        # TIC-80 map(): blit a w x h cell region of the tilemap over `sheet` to
        # screen (sx, sy) in ONE native moy_gfx.blit_map call (issue #32). The sheet
        # is baked once into an RGB565 tile atlas (cached on the sheet, rebuilt only
        # on a paint edit via sheet.gen, a different colorkey, or a pal/palt change),
        # so per-frame cost is just the C walk. camera offsets (sx,sy); the clip rect
        # is passed to the kernel (#11).
        self.flush_batch()             # #63: map() is a non-spr primitive -> break batch
        mx = int(mx); my = int(my); scale = int(scale)
        sx = int(sx) - self._cam_x
        sy = int(sy) - self._cam_y
        if scale < 1:
            scale = 1
        if w is None:
            w = tilemap.w - mx
        if h is None:
            h = tilemap.h - my
        tile = sheet.TILE
        if self._gfx is None:
            self._map_py(tilemap, sheet, mx, my, int(w), int(h), sx, sy, colorkey, scale)
            return
        atlas, ntiles = self._sheet_atlas(sheet, colorkey)
        _t0 = _ticks_us()              # #66 DRAW2: time the native tilemap blit
        self._gfx.blit_map(self._buf, self.w, self.h, sx, sy,
                           tilemap.cells, tilemap.w, tilemap.h,
                           mx, my, int(w), int(h),
                           atlas, ntiles, tile, scale, _RGB_KEY,
                           self._clip_x0, self._clip_y0, self._clip_x1, self._clip_y1)
        self._t_map_us += _ticks_diff(_ticks_us(), _t0)
        if self._pump is not None:
            self._pump()               # #66: feed the bounce flush between native ops

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
        pal = PAL565_SW
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
        self._gfx.blit_batch(self._buf, self.w, self.h, a,
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
        self._gfx.blit_batch(self._buf, self.w, self.h, items,
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
        self.flush_batch()             # #63: print() is a non-spr primitive -> break batch
        if self._gfx_text is not None:
            _t0 = _ticks_us()          # #66 DRAW2: time the native text blit
            self._gfx_text(self._buf, self.w, self.h, str(s), int(x), int(y),
                           self._col(c), _FONT8, _FONT8_FIRST, 1,
                           self._cam_x, self._cam_y,
                           self._clip_x0, self._clip_y0,
                           self._clip_x1, self._clip_y1)
            self._t_text_us += _ticks_diff(_ticks_us(), _t0)
            if self._pump is not None:
                self._pump()           # #66: feed the bounce flush between native ops
            return
        self._fb.text(str(s), int(x) - self._cam_x, int(y) - self._cam_y, self._col(c))

    def blit_indices(self, indices, iw, ih, x, y):
        # Place an iw x ih palette-INDEX bitmap (1 byte/pixel) at (x, y), converting each index
        # to RGB565 via the panel-order PAL565_SW table. The "images are data, not draw calls"
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
            self._gfx.blit_indices(self._buf, self.w, self.h, x, y,
                                   indices, iw, ih, _PAL565_SW_BUF)
            return
        d = memoryview(self._buf).cast("H")
        w = self.w
        h = self.h
        n = len(indices)
        pn = len(PAL565_SW)
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
                d[drow + tx] = PAL565_SW[v]

    # -- scroll layers (#54) -------------------------------------------------

    def new_layer(self, w, h):
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
        # layer + transient strings so the region is contiguous again. Cart-start only (a
        # layer is built once per run, not per frame), so the ~10ms collect is invisible.
        try:
            import gc
            gc.collect()
        except Exception:  # noqa: BLE001 -- gc is always present; never block a layer alloc
            pass
        return DeviceCanvas(_LayerComp(int(w), int(h), self._gfx))

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
            self._gfx.blit565(self._buf, self.w, self.h, dst_x, dst_y,
                              layer._buf, sw, sh, -1)
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
        try:
            import moy_alloc
            import lcd_bus
            buf = moy_alloc.malloc_dma(nbytes, lcd_bus.MEMORY_SPIRAM | lcd_bus.MEMORY_DMA)
        except Exception:  # noqa: BLE001 -- host / no DMA allocator -> gc-heap bytearray
            buf = None
        if buf is None:
            buf = bytearray(nbytes)
        self._buf = buf
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
              "spr", "spr_batch", "map", "mget", "mset", "print",
              "camera", "clip", "pal", "palt")

    def __init__(self, canvas, ns):
        self._canvas = canvas
        self.W = canvas.w
        self.H = canvas.h
        for k in _Layer._VERBS:
            setattr(self, k, ns[k])


def make_api(canvas, input, config, sheet=None, audio=None, tilemap=None,
             pmem=None, wifi=None, images=None):
    import random

    _img_cache = {}        # name -> decoded paint Image (see image() below), so a
                           # repeated image(name) returns the SAME Image (#63) and its
                           # RGB565 bake cache survives across frames.
    tile_cache = {}        # (tile id, colorkey) -> Image, so a redrawn sheet sprite
                           # reuses one Image (and its RGB565 blit cache) every frame
                           # instead of rebuilding it. Invalidated when the sheet's
                           # gen counter changes (a paint edit), so a live sprite edit
                           # shows fresh art instead of stale cached pixels.
    _cache_gen = [None]

    def cfg(key, default=None):
        return config.get(key, default)

    # Audio (#16): same names/signature as the host make_api. Bound to the injected
    # device audio backend (DeviceAudio / a silent fallback); no-op if absent so a
    # cart's sfx()/beep()/music() never crash when audio isn't wired.
    def _sfx(n, chan=None):
        if audio is not None:
            audio.sfx(n, chan)

    def _beep(freq, dur=0.15):
        if audio is not None:
            audio.beep(freq, dur)

    def _music(track, loop=True):
        if audio is not None:
            audio.music(track, loop)

    def _music_stop():
        if audio is not None:
            audio.music_stop()

    def _sound_stop(chan=None):
        if audio is not None:
            audio.sound_stop(chan)

    def _volume(level):
        if audio is not None:
            audio.volume(level)

    def spr(n, x, y, colorkey=-1, scale=1, flip=0, w=1, h=1):
        # TIC-80 spr(id, x, y[, colorkey, scale, flip, w, h]) from the cart's sheet.
        # w/h are the tile span: spr(n, x, y, w=2, h=2) draws the 16x16 multi-tile
        # sprite whose top-left is tile n (#30). flip (0=none, 1=h, 2=v, 3=both, #11)
        # mirrors the sprite. w=h=1, flip=0 is the plain 8x8 sprite. Also accepts an
        # Image directly (ASCII-art sprites); then a 4th positional is treated as
        # scale, e.g. spr(pet, x, y, scale=4).
        if isinstance(n, Image):
            return canvas.spr(n, x, y, colorkey if colorkey != -1 else scale, flip)
        if sheet is None:
            return
        if w > 1 or h > 1:
            # Multi-tile sprite: resolve the span Image and draw it immediately (the
            # canvas flushes any pending 1x1 auto-batch first). Cached per (n,ck,w,h).
            g = getattr(sheet, "gen", 0)
            if g != _cache_gen[0]:
                tile_cache.clear()
                _cache_gen[0] = g
            ck = (int(n), colorkey, int(w), int(h))
            img = tile_cache.get(ck)
            if img is None:
                img = sheet.tile_span_image(int(n), int(w), int(h), colorkey)
                if img is None:
                    return
                tile_cache[ck] = img
            canvas.spr(img, x, y, scale, flip)
            return
        # Plain 1x1 sheet tile: auto-batch (#63). The canvas queues it and coalesces a
        # contiguous run into one native blit_batch, flushing on any state break.
        canvas.spr_tile(sheet, int(n), x, y, colorkey, scale, flip)

    def map_(mx=0, my=0, w=None, h=None, sx=0, sy=0, colorkey=-1, scale=1):
        # TIC-80 map(): blit a region of the cart's tilemap over the sheet (#32).
        # Same signature/semantics as the host make_api -- one native blit_map call.
        if tilemap is None or sheet is None:
            return
        canvas.map(tilemap, sheet, mx, my, w, h, sx, sy, colorkey, scale)

    def spr_batch(items, colorkey=-1, scale=1):
        # spr_batch(items[, colorkey, scale]): draw MANY sheet tiles in ONE native
        # call (#43) -- the sprite analogue of map(). `items` is a sequence of
        # (tile, x, y) or (tile, x, y, flip) tuples (flip 0=none/1=h/2=v/3=both,
        # like spr()); `colorkey` + `scale` apply uniformly to the whole batch. Coords
        # are world space (the camera offsets each; the clip rect is honoured) and the
        # tiles come from the cart's sheet -- the SAME RGB565 atlas map() uses, so the
        # cost is one C walk over the items instead of N per-sprite MP->C blits. This
        # is the lever for explosion-heavy frames (the per-sprite draw-call count is
        # the device's FPS bottleneck). SHEET TILES ONLY, 1x1 tiles: Image sprites and
        # multi-tile (w/h>1) sprites still use spr(). No-op when the cart has no sheet.
        if sheet is None:
            return
        # No tile-cache refresh needed (unlike spr()): the atlas is keyed on sheet.gen,
        # so _sheet_atlas rebakes itself after a live paint edit.
        canvas.spr_batch(sheet, items, colorkey, scale)

    def mget(x, y):
        return tilemap.mget(x, y) if tilemap is not None else -1

    def mset(x, y, tile):
        if tilemap is not None:
            tilemap.mset(x, y, tile)

    def touch():
        # GT911 pointer exposed to touch-driven carts: (x, y, tapped, held) this
        # frame, or None when there is no pointer. `tapped` is the press edge so a
        # cart scores at most one hit per tap; `held` stays True while the finger
        # is on the glass (run_desktop drives pointer.down from the GT911 poll), so
        # a cart can track a DRAG (drawing, sliders). Same contract as the host.
        p = getattr(input, "pointer", None)
        if p is None:
            return None
        return (p.x, p.y, bool(p.click), bool(getattr(p, "down", False)))

    def mouse():
        # TIC-80-shaped 7-tuple (x, y, left, middle, right, scrollx, scrolly)
        # aliasing touch(): tap -> left button. The touchscreen has no
        # middle/right/scroll, so those are constant 0/False.
        p = getattr(input, "pointer", None)
        if p is None:
            return (0, 0, False, False, False, 0, 0)
        return (p.x, p.y, bool(p.click), False, False, 0, 0)

    def time():
        # Milliseconds since the cart started (set by Workstation._start).
        start = getattr(input, "cart_start_ms", 0)
        return _ticks_diff(_ticks_ms(), start)

    def key(code=None):
        # key([code]) -> is that ASCII key held this frame (key(ord("a"))). The
        # T-Deck keyboard reports one byte per frame, so key() tracks that single
        # last key, not a full held-set: only one key reads as down at a time. With
        # no arg, returns the last key code (0 when nothing is down).
        cur = getattr(input, "cart_key", 0)
        if code is None:
            return cur
        return cur == int(code)

    def keyp(code=None):
        # keyp([code]) -> pressed THIS frame (the 0->key edge). Same single-key
        # limitation as key(); no auto-repeat hold/period args.
        edge = getattr(input, "cart_keyp", 0)
        if code is None:
            return edge
        return edge == int(code)

    def textmode(on=True):
        # textmode([on]) -> opt a RUNNING cart into TEXT-keyboard input (#38/#42).
        # By default a running cart is in GAME mode: the T-Deck keyboard is in raw
        # matrix mode so a held WASD/arrow keeps driving btn() (true hold-to-move),
        # but it yields no clean typeable ASCII. Call textmode(True) to switch to
        # text mode -- the Workstation flips the keyboard to clean 1-byte ASCII so
        # key()/keyp() return typeable bytes (a password, a name); textmode(False)
        # restores game mode. Same name + behavior on the host (host_app). Resets to
        # game mode automatically when the cart exits. (On older keyboard firmware
        # that ignores raw mode the keyboard is always ASCII; textmode is then a
        # no-op flip but key()/keyp() still work via the hold-latch path.)
        input.text_mode = bool(on)

    def pmem_fn(index, value=None):
        # TIC-80 pmem(i[, v]): read pmem(i) -> int, write pmem(i, v) -> persists.
        if pmem is None:
            return 0
        return pmem.cell(index, value)

    def make_layer(w, h):
        # make_layer(w, h) -> a scroll background (#54): a wider off-screen canvas the
        # cart pre-renders a level into ONCE (with the SAME verbs -- cls/map/spr/rect/
        # circ/print/...), then window-copies to the screen each frame via draw_layer.
        # Replaces a per-frame full-background re-render (map() over a scrolling level,
        # ~12-14ms) with a flat memory copy (~7ms) -- the lever for ~60fps scrollers.
        lc = canvas.new_layer(w, h)
        lns = make_api(lc, input, config, sheet, audio, tilemap, pmem, wifi, images)
        return _Layer(lc, lns)

    def draw_layer(layer, cam_x=0, cam_y=0):
        # draw_layer(layer, cam_x, cam_y): blit the visible W x H window of `layer` at
        # the camera offset into the framebuffer (this frame's background; draw actors
        # on top afterwards). The camera is clamped to [0, layer - screen] so the full
        # window always lands -- no torn edge at the world boundary.
        lc = layer._canvas
        cx = int(cam_x)
        cy = int(cam_y)
        maxx = lc.w - canvas.w
        maxy = lc.h - canvas.h
        if cx < 0:
            cx = 0
        elif maxx > 0 and cx > maxx:
            cx = maxx
        if cy < 0:
            cy = 0
        elif maxy > 0 and cy > maxy:
            cy = maxy
        canvas.blit_window_from(lc, cx, cy)

    def image(a, mapping=None, transparent="."):
        # Two forms, dispatched on the first arg (str vs ASCII rows) -- host==device:
        #   image("bg")          -> the cart's paint-image asset images/bg.moyimg as a
        #     big Image (a 64-colour MOY64 index bitmap), placed with spr(img, x, y).
        #     The #63 Fold 3 background path; DeviceCanvas.spr bakes it index->565 ONCE
        #     via blit_indices. None when the cart has no such image; the SAME Image is
        #     returned across calls (memoised) so its 565 bake survives frames.
        #   image(rows, mapping) -> build a small Image from ASCII art (kid convenience).
        if isinstance(a, str):
            im = _img_cache.get(a)
            if im is None:
                blob = images.get(a) if images else None
                if blob is None:
                    return None
                dec = _decode_moyimg(blob)
                if dec is None:
                    return None
                w, h, idx = dec
                im = Image(w, h, idx, -1)      # opaque (no transparent index)
                im._paint = True               # marks the paint-image bake/ship fast paths
                im._name = a                   # web view (#63 Fold 4): spr() ships ["imgref",
                                               # x, y, name]; the pixels ride /assets, not the frame
                _img_cache[a] = im
            return im
        return Image.from_ascii(a, mapping, transparent)

    # #63: hand the kid the NATIVE spr fast path when available. The C gate parses
    # (n, x, y[, colorkey[, scale[, flip]]]) and appends to the canvas batch array
    # with no Python call frame -- the fix for the warm-heap frame-spill pathology
    # that made a 120-sprite kid loop cost ~150ms/frame (see make_spr_gate). The
    # Python closure above stays as its fallback (Image sprites, w/h spans, kwargs)
    # and as the whole path off-gfx (host parity, web TeeCanvas), so pixels and
    # semantics are identical either way. Kid code never changes: it's still spr().
    _spr_entry = spr
    _mkgate = getattr(canvas, "make_spr_gate", None)
    if _mkgate is not None:
        _gate = _mkgate(sheet, spr)
        if _gate is not None and callable(_gate):
            _spr_entry = _gate

    ns = {
        "W": canvas.w, "H": canvas.h,
        "cls": canvas.cls, "pix": canvas.pix,
        "line": canvas.line, "rect": canvas.rect, "rectb": canvas.rectb,
        "circ": canvas.circ, "circb": canvas.circb, "spr": _spr_entry,
        "spr_batch": spr_batch,
        "make_layer": make_layer, "draw_layer": draw_layer,
        "map": map_, "mget": mget, "mset": mset,
        "print": canvas.print, "touch": touch, "mouse": mouse,
        "clip": canvas.clip, "camera": canvas.camera,
        "pal": canvas.pal, "palt": canvas.palt,
        "btn": input.held, "btnp": input.pressed,
        "key": key, "keyp": keyp, "time": time, "pmem": pmem_fn,
        "textmode": textmode,
        "cfg": cfg, "col": color,
        "sfx": _sfx, "beep": _beep, "music": _music,
        "music_stop": _music_stop, "sound_stop": _sound_stop, "volume": _volume,
        "rnd": lambda n=1.0: random.random() * n,
        "flr": lambda x: int(x // 1),
        "Image": Image,
        "image": image,
    }
    # Capability-gated network API (#38): the shared Workstation passes a non-None
    # wifi backend ONLY for a cart with the "network" permission, so a normal kid
    # cart's namespace never carries `wifi` (the base key-set is identical here and
    # on the host).
    if wifi is not None:
        ns["wifi"] = wifi
    return ns


# --- Audio backend (#16) now lives in device_audio.py (DeviceAudio + make_audio +
# the MOY_AUDIO_CORE1 / I2S_* / AUDIO_* consts), imported at the top of this module.
# run_desktop wires ws.make_audio = make_audio.


# --- WiFi service (#38) now lives in device_wifi.py (DeviceWifi + make_wifi +
# autoconnect_wifi), imported at the top of this module. run_desktop still calls
# make_wifi()/autoconnect_wifi() to build + boot-connect the injected wifi service.

# --- pointer input drivers now live in device_input.py (TrackBall + the GT911
# Touch + the TOUCH_* calibration constants), imported at the top of this module.
# run_desktop constructs them and (in poller mode) pokes touch._source.

# --- #69 the input-poller thread knob ----------------------------------------
#
# THE keyboard/touch stall fix: moybyte.input.InputPoller (see its docstring for
# the full story) owns every I2C0 transaction on a dedicated Python thread; the
# frame loop only consumes staged state, so a C3 clock-stretch stall (40-60ms,
# I2CSTAT-sized) blocks the poller thread instead of a frame. Needs the build's
# I2C GIL-release patch (esp32_i2c_gil_release.patch) to actually isolate the
# stall -- without it the stall holds the GIL and freezes the loop from any
# thread (the poller is then harmless, just useless). Set False -- or lose
# _thread, or let the thread die -- and run_desktop stays on / falls back to
# the synchronous keyboard.poll()/touch path, exactly the pre-poller behavior
# (revert with NO rebuild, same pattern as MOY_AUDIO_CORE1).
MOY_INPUT_POLLER = True


# _ticks_ms/_ticks_diff/_ticks_us + _diag_note/_diag_log now live in the leaf
# device_util.py (imported at the top of this module), so extracted device
# clusters can share them without a moy_runtime import cycle.

# --- serial diagnostics now live in device_diag.py (_diag_flush / _diag_perf_sample
# / _diag_hitch / _diag_drawbrk / _diag_draw2 / _diag_chromebrk / _diag_pump /
# _diag_i2cstat / _diag_calib / _diag_gc), imported at the top of this module.
# run_desktop calls them between frames when perf capture is on.


def _load_carts(session=None):
    """Load cartridges from SD (seeding the built-ins on first boot). Returns
    (carts, carts_root); carts_root is None (management disabled) on fallback to
    the embedded carts if the SD card is missing/unreadable.

    `session` is the SD lifecycle wrapper to mount under. Default is the
    pre-display machine.SDCard path (used by the boot prefetch); pass
    moybyte_sd.with_sd_live for the post-display native path."""
    try:
        import moybyte_sd
        import moy_carts

        if session is None:
            session = moybyte_sd.with_sd

        def _seed_and_scan():
            moy_carts.ensure_dirs()
            moy_carts.seed_builtins(CARTS)
            return moy_carts.scan()

        # Mount only for the seed+scan, then unmount: the render loop must own
        # the shared SPI bus with no SDCard device attached, or flushes hang.
        carts = session(_seed_and_scan)
        if carts:
            print("Moybyte loaded %d carts from SD" % len(carts))
            return carts, moy_carts.CARTS_DIR
    except Exception as exc:  # noqa: BLE001
        print("Moybyte SD carts unavailable:", exc)
    print("Moybyte using built-in carts")
    return [dict(c) for c in CARTS], None


# --- the device WEB VIEW controller now lives in device_webview.py (WebView +
# _PointerSink + _WebProvider), imported at the top of this module. run_desktop
# constructs WebView(...) and services it between frames (begin_frame/commit_frame/
# poll); Settings -> WEB VIEW swaps its TeeCanvas in.


# Kid-side bench source (#63 run_perf_bench): sakura's exact _update/_draw shape,
# compiled AT RUNTIME with exec() like a real SD cart -- so the kid side runs RAM
# bytecode against the frozen engine, the same split as production. 120 petals of
# float physics + the naive per-petal spr() loop.
_BENCH_KID_CODE = """
import math
SIN = [math.sin(i / 256.0 * 6.2831853) for i in range(256)]
petals = []
t = 0.0

def _sin(turn):
    return SIN[int(turn * 256.0) & 255]

def _init():
    global petals
    petals = []
    for i in range(120):
        shade = i % 3
        petals.append([(i * 37) % 320 * 1.0, (i * 53) % 240 * 1.0,
                       30.0 * (1.0 - 0.18 * shade), 0.3 + i * 0.01,
                       4.0 + (i % 9), shade])

def _update(dt):
    global t
    t += dt
    breeze = 18.0
    cx = -999.0
    cy = -999.0
    R = 52.0
    for p in petals:
        p[3] += dt * (0.32 + 0.06 * p[5])
        sway = _sin(p[3]) * p[4]
        p[0] += (breeze * (1.0 - 0.15 * p[5]) + sway) * dt
        p[1] += p[2] * dt
        dx = p[0] - cx
        dy = p[1] - cy
        if -R < dx < R and -R < dy < R:
            far = dx if dx >= 0 else -dx
            ady = dy if dy >= 0 else -dy
            if ady > far:
                far = ady
            k = (R - far) / R * 130.0
            inv = 1.0 / (far + 4.0)
            p[0] += dx * inv * k * dt
            p[1] += dy * inv * k * dt
        if p[1] > H + 4.0:
            p[1] = 0.0
        elif p[0] < -8.0:
            p[0] += W + 16.0
        elif p[0] > W + 8.0:
            p[0] -= W + 16.0

def _draw():
    draw_layer(lay, 0, 0)
    for p in petals:
        spr(p[5], int(p[0]), int(p[1]), 0)
"""


def run_perf_bench(handler):
    """Self-terminating perf bench (#63): boots the REAL device pipeline (compositor,
    DeviceCanvas, frozen engine, runtime-exec'd kid code, real flush DMA) and measures
    the sakura-shaped frame under every combination that matters:
      - Python spr path vs the native spr_gate
      - cold heap vs warm/fragmented heap (the frame-spill pathology trigger)
      - flush on vs off (PSRAM DMA cache-contention probe)
    plus the CALIB cost-model line on the frozen interpreter. Prints BENCH lines and
    RETURNS (no takeover loop): the board drops back to the REPL, so a headless bench
    board (XIAO, no buttons) stays reflashable. Never enabled in user images -- boots
    only via the _moy_bench build stamp (MOYBYTE_BENCH=1)."""
    if handler is not None:
        try:
            handler.deinit()
        except Exception as exc:
            print("Moybyte bench: takeover failed:", exc)
    try:
        from tdeck_display import get_display_bus
        from moy_compositor import make_compositor
        from moybyte.input import InputState
        from editors import SpriteSheet
    except Exception as exc:
        print("Moybyte bench unavailable:", exc)
        return
    comp = make_compositor(get_display_bus(), 320, 240, strip_h=24)
    if comp is None:
        print("Moybyte bench: no compositor")
        return
    import gc

    canvas = DeviceCanvas(comp)
    sheet = SpriteSheet()
    px = sheet.pix
    for i in range(len(px)):
        px[i] = (i * 7) % 15 + 1       # non-transparent noise tiles
    inp = InputState()

    class _Diag:
        def log(self, tag, msg):
            print("BENCH %s %s" % (tag, msg))

    diag = _Diag()

    def build_ns(use_gate):
        # Fresh kid namespace, exec'd at runtime like a real cart. use_gate=False
        # shadows make_spr_gate so make_api keeps the Python spr closure.
        if not use_gate:
            canvas.make_spr_gate = lambda s, f: None       # instance shadow
        elif "make_spr_gate" in canvas.__dict__:
            del canvas.make_spr_gate                       # restore the C gate
        ns = make_api(canvas, inp, {}, sheet=sheet)
        exec(_BENCH_KID_CODE, ns)                           # noqa: S102 -- bench-only
        ns["lay"] = ns["make_layer"](canvas.w, canvas.h)
        ns["lay"].cls(3)
        ns["_init"]()
        return ns

    def run_cfg(label, use_gate, frames=60, flush=True):
        ns = build_ns(use_gate)
        upd = ns["_update"]
        drw = ns["_draw"]
        gc.collect()
        tu = 0
        td = 0
        tf = 0
        for i in range(frames):
            canvas.sync_back()
            canvas.batch_reset()
            t0 = _ticks_us()
            upd(0.033)
            t1 = _ticks_us()
            drw()
            canvas.flush_batch()
            t2 = _ticks_us()
            if flush:
                comp.flush()
            t3 = _ticks_us()
            tu += _ticks_diff(t1, t0)
            td += _ticks_diff(t2, t1)
            tf += _ticks_diff(t3, t2)
        print("BENCH %s: update=%.2fms draw=%.2fms flush=%.2fms (batch=%d/%d)"
              % (label, tu / frames / 1000.0, td / frames / 1000.0,
                 tf / frames / 1000.0, canvas._batch_flushes, canvas._batch_sprites))

    print("BENCH start (frozen engine, runtime-exec kid code)")
    _diag_calib(diag)                       # cost model, cold-ish heap
    run_cfg("cold pyspr ", False)
    run_cfg("cold gate  ", True)
    # warm/fragmented heap: the production trigger (live buffers + churn holes)
    ballast = [bytearray(150 * 1024)]
    for i in range(6000):
        ballast.append([i, i, i, i, i, i, i, i])
    frag = [(i, i, i, i) for i in range(20000)]
    keep = []
    for i in range(0, 20000, 2):
        keep.append(frag[i])
    frag = keep
    gc.collect()
    print("BENCH warm heap live=%dk" % (gc.mem_alloc() >> 10))
    _CALIB_DONE[0] = False
    _diag_calib(diag)                       # cost model again, warm heap
    run_cfg("warm pyspr ", False)
    run_cfg("warm gate  ", True)
    run_cfg("warm gate noflush", True, flush=False)
    print("BENCH done -- returning to REPL")


def run_desktop(handler, prefetched=None, fps_cap=60):
    """Boot the workstation on the device: launcher + carts + keyboard.

    `prefetched` is the (carts, carts_root) tuple read from SD BEFORE display
    init (see moybyte_shell._prefetch_carts). SD shares the panel's SPI bus, so
    mounting after the panel runs hard-hangs the device -- never call _load_carts
    here once the display is live."""
    if handler is not None:
        try:
            handler.deinit()  # stop the LVGL TaskHandler; the compositor owns the bus
        except Exception as exc:
            print("Moybyte desktop: takeover failed:", exc)
    try:
        from tdeck_display import get_display_bus
        from moy_compositor import make_compositor
        from moybyte.input import InputState, TDeckKeyboard, InputPoller
    except Exception as exc:
        print("Moybyte desktop unavailable:", exc)
        return
    comp = make_compositor(get_display_bus(), 320, 240, strip_h=24)
    if comp is None:
        print("Moybyte desktop: no compositor")
        return
    # The compositor flushes the dedicated _frame buffer in strip_h-row bands: each a
    # distinct, stable slice (the async esp_lcd DMA can't race a reused buffer -> no
    # offset/duplication) and small enough that the per-band DMA bounce fits the S3's
    # fragmented internal heap (a single 320x240 tx_color NO_MEMs). strip_h=24 = band.

    canvas = DeviceCanvas(comp)
    inp = InputState()
    keyboard = TDeckKeyboard(inp)
    ball = TrackBall()
    touch = Touch(canvas.w, canvas.h, i2c=getattr(keyboard, "_i2c", None))
    pointer = Pointer(canvas.w, canvas.h)
    inp.pointer = pointer         # touch-driven carts read it via the api touch()
    # #69 input-poller thread: move EVERY I2C0 transaction (kbd + GT911 + mode
    # switches) off the frame loop so a C3 clock-stretch stall can't freeze a
    # frame (needs the build's GIL-release patch to bite; see the class comment).
    # Any failure leaves the synchronous path untouched.
    poller = None
    if MOY_INPUT_POLLER:
        try:
            _p = InputPoller(keyboard, touch)
            if _p.start():
                poller = _p
                keyboard._poller_owned = True
                touch._source = poller.consume_touch
                _diag_note("input", "poller thread running (#69, %dms cadence)"
                           % poller.period)
        except Exception as exc:  # noqa: BLE001 -- input must never fail closed
            _diag_note("input", "poller setup failed: %s" % (exc,))
            poller = None
    import moybyte_sd
    # Carts are read from SD before display init; only fall back to a post-display
    # mount (now safe via the native moy_sd path) if the shell didn't prefetch.
    carts, carts_root = (prefetched if prefetched is not None
                         else _load_carts(moybyte_sd.with_sd_live))
    import moy_carts
    ws = Workstation(comp, canvas, inp, carts)
    ws.make_api = make_api        # device cart namespace (DeviceCanvas + Image + color)
    ws.make_audio = make_audio    # device I2S audio backend (#16, NEEDS HW VERIFICATION)
    ws.carts_store = moy_carts    # SD .moy store (scan/load/save/create/dup/delete)
    ws.carts_root = carts_root
    # Writes are enabled on-device via moy_sd: it attaches the SD card to the SPI
    # host esp_lcd already initialized (instead of machine.SDCard re-initializing
    # it, which hangs the live bus). with_sd_live mounts the card once and keeps
    # it resident -- tearing it down per op silent-hangs the next panel flush.
    # can_manage falls back off if the SD root is unknown (booted on embedded carts).
    ws.can_manage = carts_root is not None
    # SD vs panel-DMA mutual exclusion (#40 double-buffer): SD shares the panel's SPI
    # host, so an SD op can NOT overlap an in-flight panel DMA. Wrap with_sd_live so it
    # drains any pending panel DMA (comp.sync()) BEFORE touching the SD card -- the
    # desktop loop is single-threaded so SD ops run between frames, but with double-
    # buffer a frame's flush DMA may still be in flight when the op starts. sync() is a
    # no-op in single-buffer mode (the flush already blocked), so this is safe either
    # way and the wrapper is transparent to the shared console code.
    def _with_sd_synced(fn):
        comp.sync()
        return moybyte_sd.with_sd_live(fn)
    ws._with_sd = _with_sd_synced
    # OTA firmware update (#53): the shared console's Settings -> UPDATE FW row flashes a
    # new app image from /sd/update into the inactive OTA slot (esp32.Partition) and
    # reboots. SD shares the panel SPI host, so the updater reads through the SAME
    # _with_sd_synced wrapper as cart saves (drain panel DMA -> native single-bus mount).
    # Available only on an --ota build (running slot is ota_0/ota_1); on a legacy single-
    # factory image available() is False and the row never shows.
    try:
        import moy_ota
        ws.updater = moy_ota.OtaUpdater(_with_sd_synced)
    except Exception as exc:
        print("Moybyte: OTA updater unavailable:", exc)
    ws.pointer = pointer
    ws.keyboard = keyboard        # lets the code editor switch to text (ASCII) mode
    # WiFi (#38): one SYSTEM service (network.WLAN STA) shared across carts, so the
    # connection persists when the WiFi-manager cart exits and #22/#8 can use it.
    # Injected into a cart's namespace ONLY when its manifest grants "network".
    # Autoconnect from the saved creds at boot. NEEDS ON-DEVICE VERIFICATION.
    ws.wifi = make_wifi(moy_carts, carts_root)
    # OTA online update (#53, Phase 3): hand the updater the wifi service so Settings ->
    # UPDATE ONLINE can fetch a manifest + stream a new image to SD. go_online reuses the
    # saved-credential autoconnect (autoconnect_wifi) so the kid needn't re-enter wifi to
    # update -- it only connects to a network they already joined via the WiFi cart.
    if getattr(ws, "updater", None) is not None:
        try:
            ws.updater.set_wifi(ws.wifi, go_online=lambda: autoconnect_wifi(ws.wifi))
        except Exception as exc:
            print("Moybyte: OTA wifi wiring failed:", exc)
    # System menu (#52): the ≡ dropdown's "Reboot" row. On device a real reboot is
    # machine.reset(); the shared console calls this injected hook (None on host -> a
    # safe go_home stub). Additive -- it never touches the render/flush path.
    try:
        import machine
        ws.reboot_hook = machine.reset
    except Exception as exc:
        print("Moybyte: reboot hook unavailable:", exc)
    # Web view (#41/#22): serve the running console to a browser on the same WiFi via a
    # draw-command stream (NOT raw pixels -- WiFi is ~72KB/s, 153KB/frame is unplayable).
    # It starts OFF: ws.canvas stays the RAW DeviceCanvas so there is ZERO per-draw cost
    # in the normal (no-browser) path. Only when Settings -> WEB VIEW turns it ON does the
    # WebView swap a recording TeeCanvas in as ws.canvas (and even then it records only
    # while a browser is actively polling /frame). web is None on a build without
    # moy_webserver -> the Settings row is hidden.
    web = WebView(ws, canvas, inp, pointer, ws.wifi)
    if web.available():
        canvas = web.install()        # boot no-op; keeps sync_back() on the real canvas
        ws.web_hook = web
    # WiFi is deliberately NOT brought up at boot: the WLAN stack reserves internal RAM
    # the LCD DMA flush needs, so autoconnecting here starved the panel flush (OSError
    # 257 / ESP_ERR_NO_MEM) and froze the desktop. DeviceWifi is lazy now -- the radio
    # only spins up when the WiFi-manager cart scans/connects. WiFi<->display coexistence
    # on this RAM budget is an open #38 item. (autoconnect_wifi left defined, not called.)
    # Desktop shell (#28): load system.json + apply the saved wallpaper. On device
    # the wallpaper backdrop runs the chosen wallpaper cart's _draw (and _update if
    # cheap) each home frame; _wp_live can be set False to keep it _draw-only.
    ws.load_system()
    # Unified top bar (Stage 1): build the 16x16 IconSheet the bar draws its chrome
    # icons from -- from system_icons.moygfx on SD if present, else the baked default
    # theme. Same store + with_sd_live path as system.json.
    ws.load_icon_sheet()
    # Achievements (#21): load the unlocked badges (achievements.json) so earned
    # milestones survive a reboot. Same store + with_sd_live path as system.json.
    ws.load_achievements()
    # Offline diagnostics (moybyte_diag): RAM ring now, flushed to SD every ~5s and
    # on a crash, dumped to serial at next boot. perf_capture makes ws.frame() record
    # the flush/draw split each frame WITHOUT drawing the on-screen HUD, so the perf
    # sampler below can read steady numbers. Guarded import: no diag -> plain loop.
    try:
        import moybyte_diag as diag
    except Exception:
        diag = None
    if diag is not None:
        try:
            ws.perf_capture = True   # measure flush/draw for the diag perf samples
        except Exception:
            pass
    _diag_log("boot", "desktop running kb=%d ball=%d touch=%d"
              % (1 if keyboard.available else 0, 1 if ball.available else 0,
                 1 if touch.available else 0), diag)

    # OTA rollback confirm (#53): reaching here means this image booted, mounted the
    # panel + SD + keyboard, and loaded the desktop -- a strong "healthy" signal. Mark
    # the running app valid so the bootloader cancels the pending rollback it would
    # otherwise trigger on the next reset (CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE). No-op
    # if the image was already confirmed or this is a non-OTA build.
    if getattr(ws, "updater", None) is not None:
        try:
            if ws.updater.mark_valid():
                _diag_log("ota", "marked app valid (slot %s)" % ws.updater.slot(), diag)
        except Exception as exc:
            _diag_log("ota", "mark_valid failed: %s" % exc, diag)

    import gc
    gc.collect()                                # defrag after the heavy boot so the LCD
                                                # DMA flush has the internal RAM it needs
    try:                                        # one-shot heap snapshot (diagnostic):
        import esp32                            # each region = (total, free, max_contiguous, min_free);
        # the small regions are internal SRAM, the huge one is PSRAM. The LCD DMA
        # bounce needs a contiguous INTERNAL block, so watch the small regions' max.
        _diag_log("mem", "gc_free=%d heap=%s"
                  % (gc.mem_free(), esp32.idf_heap_info(esp32.HEAP_DATA)), diag)
    except Exception as _e:                     # noqa: BLE001 -- diagnostic only
        _diag_log("mem", "gc_free=%d (esp32 n/a: %s)" % (gc.mem_free(), _e), diag)
    frame_ms = 1000 // fps_cap
    last = _ticks_ms()
    _backlight_on = False         # #45: panel stays dark until the first frame ships
    # Diag timers: flush the RAM ring to SD every ~5s (between frames, never during a
    # panel flush -- with_sd_live mounts on the native single-bus path), and sample
    # the perf HUD numbers into a PERF line every ~3s while a cart runs.
    _diag_flush_at = _ticks_ms() + 5000
    _diag_perf_at = _ticks_ms() + 3000
    _diag_prev_cart_err = None    # last ws.cart_error we logged, so we log each crash once
    _diag_cart_prev = False       # #68: cart-running edge -> flush the ring on cart EXIT
    ws.arm_splash()               # boot logo: show the moybyte mascot before the launcher
    while True:
        now = _ticks_ms()
        dt = max(0.0, min(0.1, _ticks_diff(now, last) / 1000.0))
        last = now
        # #69: with the poller thread live, the frame loop only APPLIES staged
        # input (no I2C -> no stall can land here). If the thread ever dies,
        # detach and fall back to the synchronous poll -- input never goes dark.
        if poller is not None and not poller.alive:
            _diag_note("input", "poller thread died -> synchronous fallback")
            keyboard._poller_owned = False
            touch._source = None
            poller = None
        try:
            if poller is not None:
                poller.consume()
            else:
                keyboard.poll()
        except Exception:
            pass
        # HITCH v2 (#66): the first hardware pass showed ~188ms hitches every
        # ~1.3s with every MEASURED phase normal -- the pause lives in the input
        # polls (I2C keyboard/touch), the one loop stage that wasn't timed. Time
        # kbd / (ball+touch) / ws.frame separately so a HITCH line names it.
        _t_kbd = _ticks_diff(_ticks_ms(), now)
        # Web view (#41): start this frame's recording (no-op unless a browser is live)
        # and inject any queued browser button/pan input BEFORE begin_frame, so a
        # browser press registers a clean one-frame edge like the keyboard's.
        web.begin_frame()
        web.feed_input(now)
        _t0 = _ticks_ms()
        inp.begin_frame()                       # keyboard edges (still a fallback)
        counts, click = ball.poll()             # trackball
        nx = counts[3] - counts[2]              # right - left (raw pulses)
        ny = counts[1] - counts[0]              # down - up
        if ws.screen == "menu" and ws.menu_view == "code":
            ws.nav(nx, ny)                      # in the code editor the trackball moves the caret
        else:
            dx = _cursor_delta(nx)
            dy = _cursor_delta(ny)
            if dx or dy:
                pointer.move(dx, dy)            # elsewhere it moves the cursor
        tp = touch.poll()                       # touch -> absolute position + tap
        pointer.down = tp is not None           # held finger drives drag-scroll
        if tp is not None:
            pointer.place(tp[0], tp[1])
            if tp[2]:                           # press edge = tap = click
                click = True
        # Web view (#41): merge a browser finger/tap AFTER the physical touch read (so
        # it isn't clobbered); a real finger on the device wins over the browser.
        if web.feed_pointer(tp is not None):
            click = True
        pointer.click = click
        pointer.tick(now)                       # auto-hide the idle trackball cursor
        _t_inp = _ticks_diff(_ticks_ms(), _t0)  # trackball + touch + pointer (HITCH v2)
        # DMA double-buffer (#40, DEFAULT ON): point the canvas at the compositor's
        # current BACK buffer before drawing. The previous flush() swapped it, so this
        # frame's cls/rect/spr/map must target the new back, never the buffer that's
        # mid-DMA. No-op (buffer unchanged) in single-buffer mode or on a skipped frame.
        _frames_before = getattr(ws, "_frames_drawn", 0)
        _t0 = _ticks_ms()
        canvas.sync_back()                      # buffer repoint + GDMA layer kick
        _t_sb = _ticks_diff(_ticks_ms(), _t0)   # (was an unmeasured stage; HITCH v3)
        _t0 = _ticks_ms()
        try:
            ws.handle_input()                   # keyboard W/A/S/D etc.
            ws.handle_pointer()                 # cursor hover + click
            ws.frame(dt)                        # draw + composite + flush
        except Exception as exc:                # never let one bad frame brick the device:
            # Capture the crash in diag AND print it live: a crash we can't see live
            # (the takeover loop has starved USB) is the whole reason diag exists, so
            # flush it to SD immediately so next boot's dump has it.
            _diag_log("frame error", exc, diag)
            print("Moybyte frame error:", exc)  # print the traceback's reason to serial
            _diag_flush(diag, ws)
            gc.collect()                        # a NO_MEM flush may recover after a collect
        _t_ws = _ticks_diff(_ticks_ms(), _t0)   # handle_input/pointer + ws.frame (HITCH v2)
        # Web view (#41): publish this frame's recorded draw commands to the browser --
        # but ONLY if the frame actually drew (the redraw-on-change gate #44 may skip a
        # static screen, which would record nothing; keep serving the last full frame).
        if getattr(ws, "_frames_drawn", 0) != _frames_before:
            web.commit_frame()
        # DMA double-buffer (#40): finish the displayed frame when the UI goes IDLE.
        # flush() holds back the final band (the busy-wait completion point) for the
        # NEXT flush's drain so render overlaps the DMA -- but the redraw-on-change gate
        # (#44) may skip flush() for many idle frames, which would leave that final band
        # un-issued and the panel showing an incomplete frame. So when THIS frame did
        # not draw (no flush happened), drain any pending band: the panel is then fully
        # painted and stays idle (pending -> None, so this fires once, not every idle
        # frame). No-op in single-buffer mode (sync() is a no-op there).
        if getattr(ws, "_frames_drawn", 0) == _frames_before:
            try:
                comp.sync()
            except Exception:
                pass
        # A cart that raises inside its own _update/_draw is caught INSIDE ws.frame()
        # (so it never reaches the except above) -- it sets ws.cart_error. Mirror any
        # NEW cart_error into diag + flush it, so an in-cart crash is captured offline
        # for the next-boot dump (the takeover loop has starved live serial by now).
        if diag is not None:
            _ce = getattr(ws, "cart_error", None)
            if _ce is not None and _ce != _diag_prev_cart_err:
                _diag_prev_cart_err = _ce
                _diag_log("cart error", _ce, diag)
                _diag_flush(diag, ws)
            elif _ce is None:
                _diag_prev_cart_err = None
        # Boot "CRT" flash fix (#45): the backlight booted OFF (tdeck_board/tdeck_display)
        # so the ST7789 power-on GRAM noise is never lit. Turn it on the instant the
        # first real frame has been composed+flushed -- ws._frames_drawn ticks past 0
        # only inside frame() after comp.flush() -- so the user's first sight is the
        # desktop, not garbage. One-shot; guarded so a no-op redraw frame won't re-light.
        if not _backlight_on and getattr(ws, "_frames_drawn", 0) > 0:
            try:
                import tdeck_display
                tdeck_display.set_backlight(True)
            except Exception as _bl:            # display-less host / bring-up: ignore
                print("Moybyte backlight on failed:", _bl)
            _backlight_on = True
        # Diag perf sample (~3s): a structured "PERF cart=<name> fps=<n> flush=<ms>
        # draw=<ms>" line while a cart runs -- the payload that makes "play -> reboot
        # -> paste the serial" yield per-cart frame timings offline. No SD touch here
        # (just the RAM ring); the 5s flush below is what writes it out.
        _tnow = _ticks_ms()
        _t_diag = 0
        # #68 "kid mode" gate: Settings -> PERF DIAG (ws.diag_live, persisted,
        # default OFF). OFF skips the two diag costs a player can FEEL -- the 30s
        # forced GC sample (~130-230ms) and the periodic diag->SD write (~115ms) --
        # and hushes the live echo. The RAM ring still collects every line
        # (us-cheap) and still reaches SD on crash / cart exit below, so
        # "play -> crash -> read diag.log" works in kid mode too.
        _live = bool(getattr(ws, "diag_live", False))
        if diag is not None and _ticks_diff(_tnow, _diag_perf_at) >= 0:
            _diag_perf_at = _tnow + 3000
            try:
                diag.ECHO_LIVE = _live   # echo follows the toggle (boot lines echoed
            except Exception:            # before the first 3s tick either way)
                pass
            _diag_perf_sample(diag, ws)
            _diag_drawbrk(diag, ws)
            _diag_draw2(diag, ws)       # #63: split render into layer-copy vs sprite-batch us
            _diag_chromebrk(diag, ws)   # #66 lever 5: bar/composite/cursor chrome sub-split
            _diag_pump(diag, comp)      # #66 lever 4: bounce-feed pacing (SPI idle gaps)
            _diag_i2cstat(diag, keyboard, touch)  # #69: kbd/touch I2C session latency
            _diag_calib(diag)           # #63: one-shot interpreter cost model (spill probe)
            if _live:
                _diag_gc(diag)          # #63/#68: the FORCED collect sample -- diag-only,
                                        # never during kid play (it costs a felt frame)
            _t_diag = _ticks_diff(_ticks_ms(), _tnow)
        # Diag SD flush: overwrite /sd/moybyte/diag.log with the current ring.
        # Runs between frames on the native single-bus path (with_sd_live), never
        # during a panel flush. Guarded -> a flush failure degrades to a no-op.
        # CADENCE (#66, hardware-measured): the write costs 80-120ms -- at the old
        # flat 5s that was a visible stutter DURING PLAY (HITCH sdflush=82-118
        # confirmed it). #68: the timer flush now runs ONLY with PERF DIAG on
        # (20s in-cart / 5s otherwise); in kid mode the ring is persisted at cart
        # EXIT instead (one write, off the play path) + the crash paths.
        _cart_now = ws.cart is not None
        _t_sd = 0
        if diag is not None and _diag_cart_prev and not _cart_now:
            _t_sd = _diag_flush(diag, ws)  # #68: cart exited -> persist the session's ring
        _diag_cart_prev = _cart_now
        if diag is not None and _live and _ticks_diff(_tnow, _diag_flush_at) >= 0:
            _diag_flush_at = _tnow + (20000 if ws.cart is not None else 5000)
            _t_sd = _diag_flush(diag, ws)
        # Web view (#41): service the server BETWEEN frames, fully non-blocking -- accept
        # new connections + drain the persistent WebSocket's queued input and push the
        # latest committed frame down it (WiFi STA is a separate peripheral from the display
        # SPI, so this never touches the SD/panel bus -- it only competes for CPU here).
        # No-op when the server is off; a slow client is dropped, never waited on.
        _t0 = _ticks_ms()
        web.poll()
        _t_web = _ticks_diff(_ticks_ms(), _t0)
        elapsed = _ticks_diff(_ticks_ms(), now)
        # Hitch logger (#66): any frame past HITCH_MS gets a HITCH line naming the
        # measured stages -- kbd (I2C keyboard poll), inp (trackball+touch+pointer),
        # ws (input handlers + frame: logic/render/chrome/flush), the 3s diag
        # sample, the diag->SD write, web.poll -- the tool for catching the
        # "micro-stutter every couple of seconds" class of bug. A spike with all
        # the named parts small = the pause was between stages (e.g. an implicit
        # GC collect inside an alloc), which is itself the answer.
        if diag is not None and elapsed >= HITCH_MS:
            _diag_hitch(diag, ws, comp, elapsed, _t_kbd, _t_inp, _t_sb, _t_ws,
                        _t_diag, _t_sd, _t_web)
        if elapsed < frame_ms:
            time.sleep_ms(frame_ms - elapsed)


def run_touch_calibrate(handler):
    """Touch bring-up aid (moybyte_shell.RUN_TOUCH_CALIBRATE). Draws corner
    targets and prints each GT911 sample (raw + current mapping) over serial.

    It flushes the panel only ONCE up front and then just polls + prints, so USB
    serial keeps draining -- the normal desktop loop's continuous flush starves
    USB and you'd see nothing. Touch each yellow corner, read the raw coords over
    serial, then set TOUCH_SWAP / TOUCH_FLIP_X / TOUCH_FLIP_Y / TOUCH_RAW_* in device_input.py
    so the mapped value lands on that corner, and rebuild."""
    if handler is not None:
        try:
            handler.deinit()
        except Exception as exc:  # noqa: BLE001
            print("Moybyte touch-cal: takeover failed:", exc)
    try:
        from tdeck_display import get_display_bus
        from moy_compositor import make_compositor
        from moybyte.input import InputState, TDeckKeyboard
    except Exception as exc:  # noqa: BLE001
        print("Moybyte touch-cal unavailable:", exc)
        return
    comp = make_compositor(get_display_bus(), 320, 240, strip_h=40)
    if comp is None:
        print("Moybyte touch-cal: no compositor")
        return
    canvas = DeviceCanvas(comp)
    inp = InputState()
    keyboard = TDeckKeyboard(inp)
    touch = Touch(canvas.w, canvas.h, i2c=getattr(keyboard, "_i2c", None))
    canvas.cls(NAMES["black"])
    for (cx, cy) in ((8, 8), (canvas.w - 9, 8), (8, canvas.h - 9),
                     (canvas.w - 9, canvas.h - 9), (canvas.w // 2, canvas.h // 2)):
        canvas.rectb(cx - 6, cy - 6, 12, 12, NAMES["yellow"])
    canvas.print("TOUCH CORNERS", 100, canvas.h // 2 - 24, NAMES["white"], 2)
    canvas.print("watch serial", 108, canvas.h // 2 + 8, NAMES["light_grey"], 1)
    comp.flush()
    print("Moybyte touch-cal start avail=%d addr=%s"
          % (1 if touch.available else 0, hex(touch.addr) if touch.addr else "?"))
    while True:
        r = touch.debug_read()
        if r and r[1]:  # (status, 8 raw bytes) on a real touch
            status, d = r
            print("Moybyte touch-cal status=0x%02x bytes=%s"
                  % (status, " ".join("%02x" % b for b in d)))
        time.sleep_ms(50)


def run_keyboard_probe(handler):
    """Keyboard bring-up aid (moybyte_shell.RUN_KEYBOARD_PROBE): read the T-Deck
    keyboard over I2C0 and print the byte each key returns -- the code-editor's
    1-byte ASCII read path. No panel takeover/flush, so USB serial stays alive
    (the desktop loop's continuous flush would starve it).

    Tap each key left->right, top->bottom; each new key prints one `KEY ...` line.
    We deliberately do NOT send the raw-matrix command (0x03) so this shows the
    keyboard's plain ASCII protocol -- exactly what the editor should consume."""
    if handler is not None:
        try:
            handler.deinit()
        except Exception as exc:  # noqa: BLE001
            print("Moybyte kb-probe: takeover failed:", exc)
    try:
        from machine import I2C, Pin
    except Exception as exc:  # noqa: BLE001
        print("Moybyte kb-probe unavailable:", exc)
        return
    addr = 0x55
    try:
        i2c = I2C(0, scl=Pin(8), sda=Pin(18), freq=400000)
    except Exception as exc:  # noqa: BLE001
        print("Moybyte kb-probe i2c failed:", exc)
        return
    found = []
    try:
        found = i2c.scan()
    except Exception:  # noqa: BLE001
        pass
    print("Moybyte keyboard probe start; i2c scan=%s addr=0x%02x"
          % ([hex(a) for a in found], addr))
    print("Moybyte kb-probe: tap keys L->R, T->B. lines = KEY <n> 0x<hex> <dec> '<char>'")
    prev = 0
    n = 0
    beat = 0
    while True:
        try:
            d = i2c.readfrom(addr, 1)
            k = d[0] if d else 0
        except Exception as exc:  # noqa: BLE001
            print("Moybyte kb-probe read err:", exc)
            time.sleep_ms(300)
            continue
        if k and k != prev:
            n += 1
            ch = chr(k) if 0x20 <= k <= 0x7E else "."
            print("KEY %d 0x%02x %d '%s'" % (n, k, k, ch))
        prev = k
        beat += 1
        if beat % 250 == 0:        # ~5s heartbeat so you know it's alive
            print("Moybyte kb-probe alive (keys so far: %d)" % n)
        time.sleep_ms(20)
