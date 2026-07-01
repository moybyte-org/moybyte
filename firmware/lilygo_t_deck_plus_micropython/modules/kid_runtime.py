# KidCode v0.4 workstation -- DEVICE side.
#
# Boots the fantasy workstation on the T-Deck: a cartridge launcher + the carts,
# navigated with the keyboard/trackball, each cart drawn through the native
# kc_compositor. The drawing API (cls/pix/rect/rectb/circ/circb/spr/print/btn/...,
# TIC-80 style: rect/circ are filled, rectb/circb are outlines) matches the host
# `runtime/` reference, so cartridges are portable; only the
# canvas backend differs (framebuf over the compositor buffer + palette->RGB565).
#
# v1 embeds the cart sources; loading real .kcart files from SD is the follow-on.

import time

# Editor cores (CodeEditor / SpriteSheet / PaintEditor) are backend-agnostic and
# shared verbatim with the host (canonical: runtime/editors.py; build.sh stages a
# copy into modules/ so it freezes here as the top-level module `editors`).
from editors import CodeEditor, PaintEditor, SpriteSheet
from console import NAMES, Pointer, Workstation, _cursor_delta, color
from carts_data import CARTS  # build-time generated from system_carts/ (tools/gen_device_carts.py)

# KID64 palette as RGB565 (generated from runtime/palette.py; no colorsys here).
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
# canonical little-endian RGB565 (the host parity test asserts it == rgb565(KID64));
# PAL565_SW is what we actually WRITE into the device framebuffer so the per-flush
# CPU byte-swap in lcd_bus.tx_color can be turned OFF (tdeck_display rgb565_byte_swap
# =False). That swap was ~17 ms/frame over PSRAM -- the synchronous wall left once the
# DMA-overlap flush (#43) hid the SPI transfer. Folding it into this LUT makes it free
# (the index->colour lookup happens anyway), so the kick drops from ~17 ms to ~2 ms and
# the SPI finally overlaps render. Every buffer-writing path (_col + the sprite/atlas
# bakes) uses PAL565_SW; PAL565 stays the canonical reference.
PAL565_SW = tuple(((c << 8) | (c >> 8)) & 0xFFFF for c in PAL565)

# RGB565 colour-key for native sprite blits: transparent sprite pixels are baked
# to this value so kc_gfx.blit565 skips them. Magenta is absent from KID64; a
# visible pixel that happens to equal it is nudged by one LSB when the cache is
# built (see DeviceCanvas._cache_rgb), so it can never read as transparent.
_RGB_KEY = 0xF81F

# Flip to False to force the slow Python per-pixel drawing path (no native kc_gfx)
# for an FPS A/B comparison against the native-blit build.
_USE_GFX = True


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


class DeviceCanvas:
    """The kid drawing API. The hot ops (cls/rect/circ/spr) go through the native
    kc_gfx C kernel writing straight into the compositor's RGB565 framebuffer --
    this is what keeps complex carts off the slow per-pixel Python path. framebuf
    over the same buffer still serves text/lines/pixels and is the fallback on an
    image built without kc_gfx."""

    def __init__(self, compositor):
        import framebuf

        self._comp = compositor
        self.w, self.h = compositor.size()
        self._buf = compositor.framebuffer()          # raw RGB565 bytearray (for kc_gfx)
        self._fb = framebuf.FrameBuffer(self._buf, self.w, self.h, framebuf.RGB565)
        self._gfx = compositor.gfx() if _USE_GFX else None   # native kernel, or None
        # DMA double-buffer (#40, default OFF): the compositor's BACK buffer ping-pongs
        # between two physical buffers each flush, so this canvas must re-point its
        # draw target at it every frame (sync_back) -- a stale pointer would draw into
        # the buffer that's being DMA'd (tear). framebuf can't retarget its backing
        # store in place, so cache one framebuf per physical buffer keyed by id(buf)
        # and pick the matching one on each swap; no per-frame allocation. In
        # single-buffer mode framebuffer() never moves, so sync_back is a cheap no-op.
        self._fb_by_buf = {id(self._buf): self._fb}
        self.reset_state()

    def sync_back(self):
        """Re-point the draw target at the compositor's current BACK buffer (#40
        double-buffer). Called once per frame BEFORE drawing: the prior flush() swapped
        the back buffer, so cls/rect/spr/map/text/pix/line must target the NEW back or
        they'd write the buffer mid-DMA (tear). No-op when the buffer is unchanged
        (single-buffer mode, or the very first frame). framebuf is cached per physical
        buffer so a swap just re-selects, never reallocates."""
        buf = self._comp.back_buffer()
        if buf is self._buf:
            return                        # unchanged -> no-op (the common path)
        self._buf = buf
        fb = self._fb_by_buf.get(id(buf))
        if fb is None:
            import framebuf
            fb = framebuf.FrameBuffer(buf, self.w, self.h, framebuf.RGB565)
            self._fb_by_buf[id(buf)] = fb
        self._fb = fb

    # -- draw state (camera / clip / pal / palt, #11) ------------------------
    # Mirror runtime/canvas.py exactly so a .kcart draws the same pixels host-side
    # and on-device: camera offsets all coords, clip bounds the write region (passed
    # to the kc_gfx kernel for blits / intersected for fills), pal remaps draw
    # indices (applied in _col, so every primitive inherits it), palt marks sprite
    # indices transparent. _palgen bumps on a pal/palt change so the per-sprite RGB
    # cache (which bakes pal+palt in) knows to re-bake.

    def reset_state(self):
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
        prev = (self._cam_x, self._cam_y)
        self._cam_x = int(x)
        self._cam_y = int(y)
        return prev

    def clip(self, x=None, y=None, w=None, h=None):
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
        if c0 is None:
            for i in range(64):
                self._pal_map[i] = i
        else:
            self._pal_map[int(c0) & 63] = int(c1) & 63
        self._palgen += 1                   # invalidate cached sprite RGB (pal baked in)

    def palt(self, c=None, on=None):
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
        # with the clip rect; native (clamped in C) when kc_gfx is present, else
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
            self._gfx.fill_rect(self._buf, self.w, x0, y0, x1 - x0, y1 - y0, col)
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
        col = self._col(c)
        if self._gfx is not None:
            self._gfx.fill(self._buf, self.w * self.h, col)
        else:
            self._fb.fill(col)

    def pix(self, x, y, c=None):
        # TIC-80 pix: read the index with two args, set it with three. Reads are
        # camera-relative; the buffer holds RGB565 so a read returns that, not an index.
        x = int(x) - self._cam_x
        y = int(y) - self._cam_y
        if c is None:
            return self._fb.pixel(x, y)
        if self._clip_x0 <= x < self._clip_x1 and self._clip_y0 <= y < self._clip_y1:
            self._fb.pixel(x, y, self._col(c))

    def line(self, x1, y1, x2, y2, c):
        # Bresenham through _put so camera+clip+pal apply (matches the host rasterizer
        # pixel-for-pixel; framebuf.line can't clip to an arbitrary rect).
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
        self._fill(int(x), int(y), int(w), int(h), self._col(c))

    def rectb(self, x, y, w, h, c):
        # TIC-80 rectb = rectangle outline (4 clipped fills, like the host).
        x = int(x); y = int(y); w = int(w); h = int(h)
        col = self._col(c)
        self._fill(x, y, w, 1, col)
        self._fill(x, y + h - 1, w, 1, col)
        self._fill(x, y, 1, h, col)
        self._fill(x + w - 1, y, 1, h, col)

    def circ(self, cx, cy, r, c):
        # TIC-80 circ = FILLED circle. Native (#43): one kc_gfx.circ call rasterizes
        # the scanline spans in C (was 2r+1 MP->C _fill calls); else the Python path.
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
        # TIC-80 circb = circle outline. Native (#43): one kc_gfx.circb call runs the
        # Bresenham midpoint circle in C (was ~8r MP->C _put calls); else Python.
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
        x = int(x) - self._cam_x
        y = int(y) - self._cam_y
        scale = int(scale)
        flip = int(flip)
        if scale < 1:
            scale = 1
        if self._gfx is None:
            self._spr_py(img, x, y, scale, flip)
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

    def _spr_py(self, img, x, y, scale, flip=0):
        # Per-pixel fallback when kc_gfx is absent (image built without it). Honours
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
        # screen (sx, sy) in ONE native kc_gfx.blit_map call (issue #32). The sheet
        # is baked once into an RGB565 tile atlas (cached on the sheet, rebuilt only
        # on a paint edit via sheet.gen, a different colorkey, or a pal/palt change),
        # so per-frame cost is just the C walk. camera offsets (sx,sy); the clip rect
        # is passed to the kernel (#11).
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
        self._gfx.blit_map(self._buf, self.w, self.h, sx, sy,
                           tilemap.cells, tilemap.w, tilemap.h,
                           mx, my, int(w), int(h),
                           atlas, ntiles, tile, scale, _RGB_KEY,
                           self._clip_x0, self._clip_y0, self._clip_x1, self._clip_y1)

    def _sheet_atlas(self, sheet, colorkey):
        # Bake the whole sheet into a contiguous RGB565 tile atlas (ntiles tiles of
        # TILE x TILE, tile-major) for kc_gfx.blit_map. Cached on the sheet and keyed
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
        # Per-tile fallback when kc_gfx is absent: draw each non-empty cell via the
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

    def spr_batch(self, sheet, items, colorkey=-1, scale=1):
        # Draw N sheet tiles in ONE native kc_gfx.blit_batch call (#43) -- the sprite
        # analogue of map(). `items` is a list of (tile, x, y) or (tile, x, y, flip)
        # tuples (world coords; camera offsets each, clip honoured). It reuses the SAME
        # cached RGB565 tile atlas map() bakes (_sheet_atlas, keyed on sheet.gen so a
        # paint edit / colorkey / pal change rebakes), so the per-frame cost is just the
        # C walk over the items -- N per-sprite MP->C blits collapse to one call.
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
        self._gfx.blit_batch(self._buf, self.w, self.h, items,
                             atlas, ntiles, tile, scale, _RGB_KEY,
                             self._cam_x, self._cam_y,
                             self._clip_x0, self._clip_y0, self._clip_x1, self._clip_y1)

    def print(self, s, x, y, c, scale=2):
        # camera offsets the text origin (#11). The clip rect is NOT applied to text:
        # framebuf.text can't clip to an arbitrary rect, and device text already uses
        # framebuf's 8x8 font (not the host's petme128), so text was never pixel-exact
        # across backends. clip + text is a rare combo; the host clips text per-pixel.
        self._fb.text(str(s), int(x) - self._cam_x, int(y) - self._cam_y, self._col(c))

    # -- scroll layers (#54) -------------------------------------------------

    def new_layer(self, w, h):
        # A blank, wider RGB565 off-screen canvas the cart pre-renders a level into
        # ONCE, then window-copies per frame (draw_layer -> blit_window_from). Built
        # through a tiny _LayerComp so it reuses DeviceCanvas.__init__ verbatim and
        # shares this canvas's native kc_gfx kernel -- so map/spr/rect/... draw into it
        # pixel-identically. The buffer is a plain bytearray (the gc heap is PSRAM here,
        # so a 2x-screen 614KB layer fits); Stage 2 (GDMA) switches it to kc_alloc DMA.
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
        # (cam_x, cam_y): native kc_gfx.blit_window (one flat per-row memcpy, ~7ms for
        # a full frame) when present, else a memoryview row-copy fallback (no framebuf,
        # so it also runs under the host parity test). Overwrites -- it's the background,
        # drawn first each frame, erasing last frame's sprites for free.
        cam_x = int(cam_x)
        cam_y = int(cam_y)
        if cam_x < 0:
            cam_x = 0
        if cam_y < 0:
            cam_y = 0
        if self._gfx is not None:
            self._gfx.blit_window(self._buf, self.w, self.h,
                                  layer._buf, layer.w, cam_x, cam_y)
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

    def blit_strip(self, layer, dst_x=0, dst_y=0):
        # Copy ALL of `layer` (its full layer.w x layer.h RGB565 buffer) into the
        # framebuffer with its top-left at (dst_x, dst_y), opaquely -- the positioned,
        # partial-height sibling of blit_window_from (which window-copies a full-screen
        # slice of a WIDER source). Used to stamp a cached top-bar strip each frame
        # instead of re-rendering it (#43 chrome cache). Native via kc_gfx.blit565 with
        # key=-1 (fully opaque) -- the same C kernel the sprite path uses, clamped to the
        # framebuffer in C; else a memoryview row-copy fallback (mirrors the host index
        # copy + the parity stub). Ignores camera/clip (it's chrome over a finished frame).
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
    fresh RGB565 buffer of the requested size sharing the parent's kc_gfx kernel. No
    flush / double-buffer (a layer is a draw SOURCE, never flushed), so back_buffer()
    just returns the one buffer -- a sync_back() on a layer is a harmless no-op."""

    def __init__(self, w, h, gfx):
        self._w = w
        self._h = h
        self._buf = bytearray(w * h * 2)
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
             pmem=None, wifi=None):
    import random

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
        g = getattr(sheet, "gen", 0)
        if g != _cache_gen[0]:
            tile_cache.clear()
            _cache_gen[0] = g
        ck = (int(n), colorkey, int(w), int(h))
        img = tile_cache.get(ck)
        if img is None:
            if w > 1 or h > 1:
                img = sheet.tile_span_image(int(n), int(w), int(h), colorkey)
            else:
                img = sheet.tile_image(int(n), colorkey)
            if img is None:
                return
            tile_cache[ck] = img
        canvas.spr(img, x, y, scale, flip)

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
        # GT911 pointer exposed to touch-driven carts: (x, y, tapped) this frame,
        # or None when there is no pointer. `tapped` is the press edge so a cart
        # scores at most one hit per tap. Same contract as the host make_api.
        p = getattr(input, "pointer", None)
        if p is None:
            return None
        return (p.x, p.y, bool(p.click))

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
        lns = make_api(lc, input, config, sheet, audio, tilemap, pmem, wifi)
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

    ns = {
        "W": canvas.w, "H": canvas.h,
        "cls": canvas.cls, "pix": canvas.pix,
        "line": canvas.line, "rect": canvas.rect, "rectb": canvas.rectb,
        "circ": canvas.circ, "circb": canvas.circb, "spr": spr,
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
        "image": lambda rows, mapping, transparent=".": Image.from_ascii(rows, mapping, transparent),
    }
    # Capability-gated network API (#38): the shared Workstation passes a non-None
    # wifi backend ONLY for a cart with the "network" permission, so a normal kid
    # cart's namespace never carries `wifi` (the base key-set is identical here and
    # on the host).
    if wifi is not None:
        ns["wifi"] = wifi
    return ns


# --- Audio backend (#16) -- I2S to the MAX98357 amp -------------------------
# The T-Deck Plus has a MAX98357 I2S class-D amp + speaker on a SEPARATE peripheral
# from the shared display/SD SPI host, so audio does NOT collide with the SD/display
# bus-takeover constraints (see CLAUDE.md). Pin map + power gate from the LilyGO
# reference (examples/I2SPlay/utilities.h + I2SPlay.ino / SimpleTone.ino):
#     I2S_BCK = GPIO 7, I2S_WS = GPIO 5 (LRCK), I2S_DOUT = GPIO 6
#     BOARD_POWERON = GPIO 10 must be HIGH (already driven at boot by tdeck_board)
# There is NO separate amp enable / SD-mode / gain GPIO on this board -- the amp's
# SD pin is hardwired and the only power gate is BOARD_POWERON (confirmed: the panel
# also lives behind it, and the panel works, so the amp is powered too). I2S.MONO
# puts samples on the left slot, which is the MAX98357's mono input. So pins, power
# and format are all correct; if it is silent the failure is the I2S *init* (made
# loud below) or the *feed*, not the wiring.
#
# THE FEED -- THE CRACKLE FIX (#41): a dedicated core-1 audio task.
# The root cause of the crackle was that the I2S feed was COUPLED to the render loop:
# DeviceAudio.tick() ran once per frame on core 0 (the MicroPython VM core), and a
# render frame is ~50-80 ms (12-14 fps). During a long draw the I2S DMA ring drained
# and under-ran -> crackle. A deeper ring + bigger feed cap only helped "a bit"
# because the feed CADENCE was still the slow, jittery frame rate.
#
# The fix (owner's call, matches PixelRoot / most MCU games): feed I2S from a
# DEDICATED native FreeRTOS task PINNED TO CORE 1, decoupled from rendering, so the
# DMA is topped up continuously no matter how slow core 0's frame is. The ESP32-S3 is
# dual-core; MicroPython's VM is single-core (core 0), but the native kc_audio C task
# runs on core 1 fully independently. I2S is on its OWN pins (separate from the shared
# SPI display/SD bus -- see #40), so core 1 owning I2S never touches the panel/SD path.
#
# THE SPLIT (see native/kc_audio/modkc_audio.c for the C side):
#   core 0 (this Python): owns the model + control surface + music scheduler. Each
#       frame tick() runs the music scheduler and COMMITS every voice's state into the
#       shared C kc_voices[] (kc_audio.voice_commit, bracketed by voice_lock/unlock so
#       the commit is atomic vs. the task's snapshot). It does NO per-sample work and
#       NO I2S write. To advance phrases it reads kc_audio.active_mask() -- the bit set
#       the core-1 task last published per still-playing voice.
#   core 1 (C task): owns the IDF i2s_std channel + the write loop. Each block it
#       snapshots kc_voices[] under the mutex, mixes (the heavy per-sample loop), and
#       writes to I2S (blocking on the DMA drain -- which paces it to the audio clock,
#       on core 1, so the VM never stalls), then folds the advanced cursor back.
#
# FALLBACK (revert-able with NO rebuild): if the core-1 task can't start (no kc_audio,
# old build, channel/task creation fails) DeviceAudio uses the LEGACY single-core feed
# -- machine.I2S non-blocking writes fed per-frame from tick(), the per-block
# voice_set/render/voice_read kernel. Set KC_AUDIO_CORE1 = False below to force that
# path even when the task is available (e.g. to A/B a bad result).
#
# The legacy feed's mechanics (for reference): MicroPython machine.I2S.write() is
# BLOCKING by default; I2S.irq() flips it NON_BLOCKING (the port copies our buffer on
# its own task and fires our callback). The non-blocking write keeps a POINTER to the
# caller's buffer until the copy finishes, so we keep the buffer alive (a persistent
# double-buffer) and only write when the previous copy is done.
#
# STILL NEEDS ON-DEVICE VERIFICATION: that the core-1 task actually drives the amp
# audibly with no crackle and no FPS drop. Do NOT claim it is tested on hardware.

# Master switch for the core-1 audio task (#41). True: prefer the dedicated core-1
# I2S feeder (the crackle fix); fall back to the legacy per-frame feed if it can't
# start. Set False to FORCE the legacy single-core feed (revert without a rebuild).
KC_AUDIO_CORE1 = True

I2S_BCK = 7
I2S_WS = 5
I2S_DOUT = 6
# 8 kHz mono: matches the reference SimpleTone rate and halves the per-frame mixer
# cost vs. 11025. DeviceAudio retunes the shared engine to this rate in __init__ so
# render_into() sizes its blocks to match the I2S port.
AUDIO_RATE = 8000
# DMA ring buffer (bytes). ~0.5 s of 8 kHz/16-bit mono -- a deep cushion so the
# speaker never under-runs across the slow/variable render frames (12-15 fps today
# -> 66-83 ms apart) plus jitter. The ring is internal DMA RAM but tiny in bytes
# (8 KB), so a generous cushion is cheap.
AUDIO_IBUF = 8192
# Ring capacity in FRAMES (16-bit mono -> 2 bytes/frame). The single-core feed tops
# the ring up TOWARD this each tick (see below) instead of feeding exactly rate*dt.
AUDIO_IBUF_FRAMES = AUDIO_IBUF // 2
# Cap on a single tick's render/write, in frames. SINGLE-CORE CRACKLE FIX: tick()
# no longer feeds exactly rate*dt (which kept the ring hovering near-empty -> any
# 50-60 ms draw under-ran it). Instead it TOPS THE RING UP toward AUDIO_IBUF_FRAMES
# every tick, so the deep ~0.5 s ring stays full and rides out long draws + jitter.
# A single non-blocking write can therefore be as large as the whole ring (the
# native kc_audio mixer makes a big block cheap), so the cap is the full ring -- it
# only bounds the rare cold-start fill, never a steady-state top-up.
AUDIO_MAX_FRAME = AUDIO_IBUF_FRAMES

# Audio diagnostics (kidcode_diag): log each sfx/music trigger and, in core-1 mode,
# a periodic "active=N committed=M" sample, so the owner can read on serial/SD
# exactly what reached the mixer (the Battle City rapid-sfx case). Gated so it can
# NEVER flood the diag ring: triggers log on the event (each sfx/music call), and
# the core-1 active sample logs at most once every AUDIO_DIAG_SAMPLE_MS.
AUDIO_DIAG = True
AUDIO_DIAG_SAMPLE_MS = 1000


class DeviceAudio:
    """I2S audio backend for the T-Deck. Wraps the shared AudioEngine. Two feeds:

    * CORE-1 task (#41, default -- the crackle fix): the native kc_audio C module owns
      the IDF i2s_std channel and a FreeRTOS task pinned to core 1 that mixes + writes
      I2S continuously, decoupled from the render loop. tick() only runs the music
      scheduler and commits voice state across to C -- no per-sample mix, no I2S write
      on core 0. This is what stops the crackle (audio is fed no matter how slow a
      frame is).
    * LEGACY single-core feed (fallback): if the core-1 task can't start, fall back to
      machine.I2S non-blocking writes fed per-frame from tick(), with the native
      per-block kernel (or the pure-Python mixer if kc_audio is absent). Set the
      module-level KC_AUDIO_CORE1 = False to FORCE this path (revert with no rebuild).

    Constructed behind try/except at every step so a board/build without kc_audio or
    I2S degrades to a quieter mode (or silence), never a crash.

    NEEDS ON-DEVICE VERIFICATION -- written to the reference pins/power/format but
    unproven on hardware in this environment (see the module comment above)."""

    def __init__(self, engine):
        self.engine = engine
        # The shared engine is built at its default 11025 Hz; the device renders at
        # 8 kHz to halve the per-frame mixer cost (only render_into reads .rate, live,
        # so retuning it here is safe) and to match the I2S port's configured rate.
        engine.rate = AUDIO_RATE
        self.i2s = None
        self._core1 = False        # True once the core-1 feeder task is running
        # Core-1 commit tracking: the C task owns per-sample advancement (idx/t/phase)
        # once a voice is committed, so we must NOT re-commit a voice's (now stale)
        # Python cursor every frame -- that would reset it to step 0 and stutter. We
        # only commit a voice the frame it is (re)triggered or stopped. THE BATTLE CITY
        # FIX (#41): detect that by the voice's monotonic _Voice.gen counter (bumped on
        # EVERY play()/stop()), NOT by (id(steps), active). id(steps) is unreliable --
        # MicroPython's GC can hand a freshly allocated steps list the SAME address as
        # the just-freed previous one, so a rapid retrigger of the same SFX (Battle City
        # fires many sfx/s) read as "unchanged" and was silently never committed -> the
        # note never reached the mixer. gen changes on every trigger, so every sfx --
        # rapid, overlapping, channel-reused -- now reliably commits.
        self._commit_gen = [None] * len(engine.voices)
        # Per-voice flag: a voice committed-active whose play the core-1 task has NOT
        # yet confirmed in active_mask() (the task snapshots ~every block, ~32 ms, so a
        # fresh trigger may not be reflected for a frame or two). While pending we do
        # NOT let a stale clear mask mark the voice free -- otherwise a fast frame could
        # see the just-started voice as idle and steal the channel mid-note.
        self._await_active = [False] * len(engine.voices)
        # Diag: a periodic core-1 "active=N committed=M" sample (rate-limited) + a
        # running count of triggers committed since the last sample, so the owner can
        # read whether Battle City's rapid sfx reach the task. _diag_t0 is the last
        # sample's ticks_ms; _diag_committed accumulates triggers between samples.
        self._diag_t0 = 0
        self._diag_committed = 0
        # Legacy-feed double buffer (only used in the fallback path): render alternates
        # into bufs[_buf], write()s it non-blocking; the port copies it on a background
        # task and fires _on_done. We never touch a buffer while its copy is in flight
        # (_busy). Persistent bytearrays => the GC can't collect an in-flight one.
        self._bufs = (bytearray(AUDIO_MAX_FRAME * 2), bytearray(AUDIO_MAX_FRAME * 2))
        self._buf = 0
        self._busy = False
        self._busy_ticks = 0       # watchdog: frames the busy flag has been stuck set
        # Single-core TOP-UP accounting (#41 single-core crackle fix): a software
        # estimate of how many frames are still queued in the I2S DMA ring. Each tick
        # we subtract what the speaker consumed (rate*dt) and add what we wrote, then
        # refill toward AUDIO_IBUF_FRAMES. This is the lever that keeps the ring full
        # (a deep cushion) instead of hovering near-empty. Conservative: it can only
        # UNDER-estimate occupancy (we floor at 0 and the ring's own back-pressure --
        # a full ring drops the tail of an over-long write -- caps the real depth), so
        # it never tricks us into starving the ring.
        self._buffered = 0
        # NATIVE kc_audio (#16/#41): the per-sample mix is the device bottleneck (the
        # pure-Python render_into runs the beeper at ~12 FPS and crackles), and the
        # I2S feed must run off the render core (#41). When kc_audio is frozen in we
        # prefer it for both; Python still owns the model/control/music scheduler. A
        # build WITHOUT kc_audio falls back to engine.render_into + machine.I2S, so the
        # firmware still works and the host is unaffected.
        try:
            import kc_audio
            self._kc_audio = kc_audio
            print("KidCode audio: native kc_audio mixer ENABLED")
        except Exception:   # noqa: BLE001 -- no native module -> Python mixer fallback
            self._kc_audio = None
            print("KidCode audio: native kc_audio absent, using Python mixer")

        # 1) Preferred path: hand I2S to the dedicated core-1 task (the crackle fix).
        if KC_AUDIO_CORE1 and self._kc_audio is not None:
            try:
                self._kc_audio.set_master(engine.volume)
                if self._kc_audio.audio_start(I2S_BCK, I2S_WS, I2S_DOUT, AUDIO_RATE):
                    self._core1 = True
                    _diag_note("audio", "core-1 I2S task running (%d Hz mono, "
                               "BCK=%d WS=%d DOUT=%d)"
                               % (AUDIO_RATE, I2S_BCK, I2S_WS, I2S_DOUT))
                    print("KidCode audio: core-1 I2S feeder ENABLED")
                else:
                    _diag_note("audio", "core-1 task unavailable, legacy feed")
            except Exception as exc:  # noqa: BLE001 -- any failure -> legacy feed
                _diag_note("audio", "core-1 start failed (%s), legacy feed" % (exc,))
                self._core1 = False

        # 2) Fallback path: open machine.I2S for the legacy per-frame feed. Skip it
        #    when the core-1 task owns the I2S peripheral (two owners would clash).
        if not self._core1:
            try:
                from machine import I2S, Pin
                self.i2s = I2S(
                    0,
                    sck=Pin(I2S_BCK),
                    ws=Pin(I2S_WS),
                    sd=Pin(I2S_DOUT),
                    mode=I2S.TX,
                    bits=16,
                    format=I2S.MONO,
                    rate=AUDIO_RATE,
                    ibuf=AUDIO_IBUF,
                )
                # irq() flips the port into NON_BLOCKING mode and registers our
                # completion callback -- write() now returns immediately.
                self.i2s.irq(self._on_done)
                _diag_note("audio", "legacy I2S ready (%d Hz mono, BCK=%d WS=%d DOUT=%d)"
                           % (AUDIO_RATE, I2S_BCK, I2S_WS, I2S_DOUT))
            except Exception as exc:  # noqa: BLE001 -- no amp / no I2S -> stay silent
                # LOUD: if audio is silent on-device this is the line to look for in
                # the ~2 s boot log AND the persisted diag dump (the takeover loop
                # starves serial, so the offline diag is the only post-boot view).
                _diag_note("audio", "I2S UNAVAILABLE, silent: %s" % (exc,))
                self.i2s = None

    def _on_done(self, _i2s):
        """I2S non-blocking completion callback (legacy feed): the background copy of
        the last buffer into the DMA ring is done, so it's safe to render into / write
        the next one. Runs via mp_sched (between bytecodes), so just clears the flag."""
        self._busy = False
        self._busy_ticks = 0

    # control surface (mirrors host FakeAudio / _SilentAudio) -------------
    def sfx(self, n, chan=None):
        self.engine.play_sfx(n, chan)
        if AUDIO_DIAG:
            self._diag_trigger("sfx", n, chan)

    def beep(self, freq, dur=0.15):
        self.engine.play_beep(freq, dur)
        if AUDIO_DIAG:
            self._diag_trigger("beep", int(freq), None)

    def music(self, track, loop=True):
        self.engine.play_music(track, loop)
        if AUDIO_DIAG:
            self._diag_trigger("music", track, None)

    def _diag_trigger(self, kind, n, chan):
        """Log one sfx/beep/music trigger to kidcode_diag (event-gated, so it cannot
        flood -- one line per actual call). Reports the path so the owner can tell at
        a glance which feed is live: feed=core1 vs feed=single. Fully guarded."""
        try:
            feed = "core1" if self._core1 else "single"
            ch = "auto" if chan is None else chan
            _diag_note("AUDIO", "%s=%s chan=%s feed=%s" % (kind, n, ch, feed))
        except Exception:   # noqa: BLE001 -- diag must never crash a trigger
            pass

    def music_stop(self):
        self.engine.stop_music()

    def sound_stop(self, chan=None):
        self.engine.stop(chan)

    def volume(self, level):
        self.engine.set_volume(level)
        # Publish the live master volume to the core-1 task (read each mix block).
        if self._core1:
            try:
                self._kc_audio.set_master(self.engine.volume)
            except Exception:   # noqa: BLE001 -- volume must never crash the loop
                pass

    # -- core-1 feed: commit voice state across, advance the scheduler ----
    def _tick_core1(self, dt):
        """The crackle fix's per-frame core-0 work: run the music scheduler in Python,
        commit every voice that was (re)triggered/stopped this frame into the shared C
        kc_voices[] (atomically, under the kc_audio mutex), and read the core-1 task's
        published active mask back so the scheduler / is_active() see the truth. NO
        per-sample mix and NO I2S write happen here -- the core-1 task does both,
        continuously, off the render core. Intentionally cheap (a few C calls), so a
        slow frame can never starve the speaker.

        WHY ONLY DIRTY VOICES: once a voice is committed the C task owns its per-sample
        advancement (idx/t/phase/noise). The Python _Voice's cursor goes stale (we do
        NOT pull it back -- that would be a chatty cross-core read), so re-committing it
        every frame would reset the C voice to step 0 and stutter. A voice only needs a
        fresh commit when Python (re)triggers or stops it, which we detect by a change
        in _Voice.gen (bumped on every play()/stop()). gen -- not (id(steps), active) --
        is what fixes the Battle City regression: id(steps) aliases on GC reuse, so a
        rapid same-SFX retrigger read as unchanged and was never committed (#41)."""
        eng = self.engine
        ka = self._kc_audio
        voices = eng.voices
        nv = len(voices)
        # Read the task's published activity FIRST, into the Python voices that the C
        # task owns, so the scheduler's free-channel pick / is_active() reflect voices
        # the task has finished playing.
        try:
            mask = ka.active_mask()
        except Exception:   # noqa: BLE001 -- never crash the loop on a status read
            mask = None
        if mask is not None:
            for c in range(nv):
                v = voices[c]
                bit_set = bool(mask & (1 << c))
                if bit_set:
                    # task confirms this play is live -> a later clear is now trusted.
                    self._await_active[c] = False
                elif (v.active and not self._await_active[c]
                      and v.gen == self._commit_gen[c]):
                    # task says voice c is done AND we've already seen it go live AND
                    # Python hasn't RE-triggered it since our last commit (gen still
                    # matches) -> this clear is real, reflect it so the scheduler can
                    # reuse the channel. The gen guard is critical: if the cart already
                    # fired a fresh sfx on this channel this frame (gen advanced), the
                    # commit below owns it -- we must NOT clobber its active flag here.
                    v.active = False
        # Music scheduler (Python) -- step it by the real elapsed frame time. It may
        # retrigger SFX onto voices; those bump gen and are committed below.
        eng._advance_music(dt)
        # Commit EVERY voice whose gen changed since our last commit, atomically vs. the
        # task's snapshot (voice_lock brackets the whole set). Every (re)trigger bumps
        # gen, so rapid/overlapping/channel-reused sfx all commit -- nothing is dropped.
        dirty = []
        for c in range(nv):
            if voices[c].gen != self._commit_gen[c]:
                dirty.append(c)
        if dirty:
            ka.voice_lock()
            try:
                for c in dirty:
                    v = voices[c]
                    ka.voice_set(c, v.active, v.steps, v.step_dur, v.loop,
                                 v.idx, v.t, v.phase, v.noise)
            finally:
                ka.voice_unlock()
            for c in dirty:
                v = voices[c]
                self._commit_gen[c] = v.gen
                # A freshly committed ACTIVE voice must not be cleared by a stale mask
                # until the task confirms it live at least once (see __init__).
                self._await_active[c] = bool(v.active)
                if v.active:
                    self._diag_committed += 1
        if AUDIO_DIAG:
            self._diag_core1_sample(mask)

    def _diag_core1_sample(self, mask):
        """Rate-limited core-1 health sample: at most once per AUDIO_DIAG_SAMPLE_MS log
        the active-voice count (from the task's published mask) + how many triggers we
        committed since the last sample. Lets the owner confirm Battle City's rapid sfx
        are actually reaching the task (committed climbs, active>0). Fully guarded so it
        can never crash the loop, and gated so it can never flood the diag ring."""
        try:
            now = _ticks_ms()
            if _ticks_diff(now, self._diag_t0) < AUDIO_DIAG_SAMPLE_MS:
                return
            self._diag_t0 = now
            active = 0
            if mask:
                m = mask
                while m:
                    active += m & 1
                    m >>= 1
            committed = self._diag_committed
            self._diag_committed = 0
            # Only emit a line when something is going on, so a silent UI never logs.
            if active or committed:
                _diag_note("AUDIO", "core1 active=%d committed=%d" % (active, committed))
        except Exception:   # noqa: BLE001 -- diag must never crash the loop
            pass

    def tick(self, dt):
        """Per-frame audio work. In core-1 mode this only schedules + commits voice
        state (the core-1 task feeds I2S); in the legacy fallback it renders this
        frame's PCM and streams it to the DMA ring NON-BLOCKINGLY. Either way it must
        never stall the single-threaded desktop loop."""
        if self._core1:
            try:
                self._tick_core1(dt)
            except Exception as exc:  # noqa: BLE001 -- audio must never crash the loop
                print("KidCode audio tick (core1) failed:", exc)
            return

        # --- legacy single-core feed (fallback) -- TOP-UP, the crackle fix ---
        # CRACKLE ROOT CAUSE (single-core): the old feed rendered exactly rate*dt per
        # frame, so the DMA ring only ever held about one frame's worth -- it hovered
        # near-empty and ANY 50-60 ms long draw / GC pause drained it to an under-run
        # (the crackle). THE FIX: top the deep (~0.5 s) ring UP toward full each tick
        # instead of just replacing what was consumed, so the cushion absorbs long
        # frames + jitter. We track buffered frames in software (_buffered): subtract
        # what the speaker drained since the last tick, then refill toward the cap.
        if self.i2s is None:
            return
        # Account for what the DMA drained since the last tick (real elapsed audio
        # time). Floor at 0 so the estimate can only UNDER-state occupancy (safe: we
        # over-fill rather than starve; the ring's own back-pressure caps the truth).
        drained = int(self.engine.rate * dt)
        self._buffered -= drained
        if self._buffered < 0:
            self._buffered = 0
        if self._busy:
            # Previous buffer still in flight -> the port is still copying it into the
            # ring, so we can't reuse the buffer yet. Watchdog: if the completion irq
            # somehow never fired (so _busy would stick and silence the rest of the
            # session), force-clear after a few frames -- by then even a full-ring
            # buffer has long since been copied, so a fresh write is safe.
            self._busy_ticks += 1
            if self._busy_ticks < 4:
                return
            self._busy = False
            self._busy_ticks = 0
        if not self.engine.is_active():
            # Nothing playing: let the ring drain to silence; reset the estimate so the
            # next sound starts from a known-empty ring (auto_clear emits silence).
            self._buffered = 0
            return
        # Refill toward a FULL ring. want = the deficit; render that much (capped to
        # the persistent buffer / a single write). The native kc_audio mixer makes a
        # big block cheap, so a deep top-up costs little and buys a long cushion.
        want = AUDIO_IBUF_FRAMES - self._buffered
        if want <= 0:
            return                  # ring already full -> skip this tick, no under-run
        n = want
        if n > AUDIO_MAX_FRAME:
            n = AUDIO_MAX_FRAME
        try:
            # render reuses our persistent buffer (no per-frame allocation, and the
            # buffer the port holds a pointer to stays alive); memoryview gives
            # write() exactly the rendered slice. Prefer the native kc_audio kernel
            # for the heavy mix; fall back to the pure-Python render_into when the
            # native module isn't frozen in (so a build without it still works).
            buf = self._bufs[self._buf]
            if self._kc_audio is not None:
                self._render_native(buf, n)
            else:
                self.engine.render_into(buf, n)
            self._buf ^= 1
            self._busy = True
            self.i2s.write(memoryview(buf)[:n * 2])
            self._buffered += n      # n more frames now queued toward the ring
        except Exception as exc:  # noqa: BLE001 -- audio must never crash the loop
            print("KidCode audio tick failed:", exc)
            self._busy = False
            self.i2s = None

    def _render_native(self, buf, n):
        """LEGACY per-block feed: render `n` frames into `buf` using the native kc_audio
        kernel for the heavy per-sample loop, keeping the Python AudioEngine the single
        source of truth. Same sequence as AudioEngine.render_into, ONLY the inner sample
        loop delegated to C:
          1. advance the music phrase scheduler in Python (it may retrigger SFX),
          2. push each voice's exact state into the C mirror (voice_set),
          3. C mixes the whole block (the part that was too slow in MicroPython),
          4. read the advanced render state back into the Python voices (voice_read)
             so is_active() / the next block's scheduler see the truth.
        Because C holds no cross-block state, the output is identical to the pure-
        Python mixer -- same .kcart, same samples on host and device. (Used only in the
        legacy single-core feed; the core-1 task uses its own snapshot/mix loop.)"""
        eng = self.engine
        ka = self._kc_audio
        # 1. music scheduler (Python) -- same dt_frame math as render_into.
        eng._advance_music(n / float(eng.rate))
        voices = eng.voices
        # 2. push exact voice state into C.
        for c in range(len(voices)):
            v = voices[c]
            ka.voice_set(c, v.active, v.steps, v.step_dur, v.loop,
                         v.idx, v.t, v.phase, v.noise)
        # 3. the heavy mix, in C.
        ka.render(buf, n, eng.rate, eng.volume)
        # 4. read the advanced state back into the Python voices.
        for c in range(len(voices)):
            st = ka.voice_read(c)
            if st is not None:
                v = voices[c]
                v.active = st[0]
                v.idx = st[1]
                v.t = st[2]
                v.phase = st[3]
                v.noise = st[4]


def make_audio(engine):
    """Injected backend factory (#16): wrap an AudioEngine in the device I2S
    backend. run_desktop hands this to the shared Workstation, the mirror of the
    host's make_audio. NEEDS ON-DEVICE VERIFICATION (see module comment)."""
    return DeviceAudio(engine)


# --- WiFi service (#38) -- network.WLAN STA, the device backend -------------
# NEEDS ON-DEVICE VERIFICATION. This wraps MicroPython's network.WLAN(STA_IF) and
# is the LIVE counterpart of the host FakeWifi -- same scan/connect/status/forget/
# known surface, so the WiFi-manager cart is byte-identical on host and device.
# It is a SYSTEM service: the connection persists when the manager cart exits, so
# the web editor (#22) and the AI helper (#8) can bind to / make requests over the
# IP it reports (`status()` -> ip). Credentials persist to the kid_carts wifi.json
# store and are used by autoconnect_wifi() at boot.
#
# Radio coexistence caveat: WiFi shares the ESP32-S3 radio with BLE (#26) and is a
# different mode from LoRa / ESP-NOW (#7) -- only one radio user can be active at a
# time. WiFi STA and the display SPI bus are SEPARATE peripherals (unlike SD), so
# there is no SPI-host fight, but ALL of this is UNVERIFIED on hardware here. The
# whole class is wrapped in try/except so a board/build without WiFi degrades to a
# never-connected service instead of crashing the console.


class DeviceWifi:
    """network.WLAN(STA_IF) wrapper. `store`/`root` are the kid_carts credential
    store + carts dir; connect()/forget() persist there so the next boot can
    autoconnect. UNVERIFIED on hardware -- treat the WLAN calls as a sketch."""

    def __init__(self, store=None, root=None):
        self._store = store
        self._root = root
        # LAZY: bringing the WiFi stack up reserves a large chunk of INTERNAL RAM that
        # the LCD DMA flush (lcd_panel_io_tx_color) also needs. Doing it at boot starved
        # the panel flush -> OSError 257 (ESP_ERR_NO_MEM) and froze the desktop. So spin
        # the radio up only on first real use (scan/connect), never at boot. Whether WiFi
        # and the display can coexist at all on this RAM budget is an open #38 question.
        self.wlan = None

    def _ensure_wlan(self):
        """Bring the radio up on demand (never at boot -- see __init__)."""
        if self.wlan is not None:
            return self.wlan
        try:
            import network
            self.wlan = network.WLAN(network.STA_IF)
            self.wlan.active(True)
        except Exception as exc:  # noqa: BLE001 -- no radio / no network module -> degrade
            _diag_note("wifi", "WLAN unavailable, offline: %s" % (exc,))
            self.wlan = None
        return self.wlan

    # -- the injected `wifi` API surface (host == device) ----------------
    def scan(self):
        """Nearby networks as (ssid, signal%, locked?) -- NEEDS ON-DEVICE VERIFICATION.
        WLAN.scan() returns (ssid, bssid, channel, RSSI, security, hidden) tuples;
        map RSSI (~-100..-30 dBm) to a 0..100 bar and security!=0 to locked."""
        if self._ensure_wlan() is None:
            return []
        try:
            out = []
            for net in self.wlan.scan():
                ssid = net[0].decode() if isinstance(net[0], (bytes, bytearray)) else str(net[0])
                rssi = net[3] if len(net) > 3 else -100
                sig = max(0, min(100, 2 * (int(rssi) + 100)))   # -100->0%, -50->100%
                locked = bool(net[4]) if len(net) > 4 else False
                if ssid:
                    out.append((ssid, sig, locked))
            return out
        except Exception as exc:  # noqa: BLE001 -- a scan failure must not crash the cart
            print("KidCode wifi scan failed:", exc)
            return []

    def connect(self, ssid, password=""):
        """Associate with `ssid`, remember the creds, and report whether the link
        came up. NEEDS ON-DEVICE VERIFICATION (the connect()/isconnected() poll
        timing below is a sketch -- a real impl waits on a status callback/timeout)."""
        ssid = str(ssid)
        ok = False
        if self._ensure_wlan() is not None:
            try:
                self.wlan.connect(ssid, password)
                # Brief poll for association. The single-threaded desktop loop calls
                # this between frames, so keep the budget small; a real impl should
                # spread this across frames rather than block.
                for _ in range(40):
                    if self.wlan.isconnected():
                        ok = True
                        break
                    time.sleep_ms(100)
            except Exception as exc:  # noqa: BLE001
                print("KidCode wifi connect failed:", exc)
                ok = False
        if self._store is not None and self._root is not None:
            try:
                self._store.remember_wifi(ssid, password, self._root)
            except Exception as exc:  # noqa: BLE001 -- save failure must not crash the cart
                print("KidCode wifi remember failed:", exc)
        return ok

    def disconnect(self):
        if self.wlan is not None:
            try:
                self.wlan.disconnect()
            except Exception:  # noqa: BLE001
                pass

    def status(self):
        """(connected, ssid, ip): the live link state #22/#8 read to use the net."""
        if self.wlan is None:
            return (False, None, None)
        try:
            if self.wlan.isconnected():
                ip = self.wlan.ifconfig()[0]
                ssid = None
                try:
                    ssid = self.wlan.config("essid") or None
                except Exception:  # noqa: BLE001 -- essid not always queryable
                    ssid = None
                return (True, ssid, ip)
        except Exception as exc:  # noqa: BLE001
            print("KidCode wifi status failed:", exc)
        return (False, None, None)

    def forget(self, ssid):
        ssid = str(ssid)
        if self._store is not None and self._root is not None:
            try:
                self._store.forget_wifi(ssid, self._root)
            except Exception as exc:  # noqa: BLE001
                print("KidCode wifi forget failed:", exc)
        # If we're on that network, drop it.
        try:
            if self.wlan is not None and self.wlan.isconnected():
                self.disconnect()
        except Exception:  # noqa: BLE001
            pass
        return True

    def known(self):
        if self._store is not None and self._root is not None:
            try:
                return [n["ssid"] for n in self._store.load_wifi(self._root)]
            except Exception as exc:  # noqa: BLE001
                print("KidCode wifi known failed:", exc)
        return []


def make_wifi(store=None, root=None):
    """Injected backend factory (#38): the device network.WLAN service over the
    kid_carts store. run_desktop hands this to the shared Workstation -- the mirror
    of the host's make_wifi. NEEDS ON-DEVICE VERIFICATION (DeviceWifi is a sketch)."""
    return DeviceWifi(store, root)


def autoconnect_wifi(wifi):
    """Boot-time autoconnect (#38): try the most-recently-remembered known network
    first (kid_carts stores it at the front), so the kid joins once and the console
    is online thereafter. Best-effort + guarded: a no-WiFi build or no saved creds
    just no-ops. NEEDS ON-DEVICE VERIFICATION -- the credential store round-trip is
    host-tested, but the actual WLAN association at boot is unproven on hardware."""
    if wifi is None:
        return False
    try:
        connected, _ssid, _ip = wifi.status()
        if connected:
            return True
        nets = []
        store = getattr(wifi, "_store", None)
        root = getattr(wifi, "_root", None)
        if store is not None and root is not None:
            nets = store.load_wifi(root)
        for n in nets:                      # front-of-list = last joined
            if wifi.connect(n["ssid"], n.get("password", "")):
                _diag_note("wifi", "autoconnected: %s" % (n["ssid"],))
                return True
    except Exception as exc:  # noqa: BLE001 -- autoconnect must never block/crash boot
        _diag_note("wifi", "autoconnect failed: %s" % (exc,))
    return False


# --- Embedded cartridges (v1) -----------------------------------------------




class TrackBall:
    """T-Deck trackball: 4 direction GPIOs pulse low when rolled; GPIO0 = click.
    Falling-edge IRQs count pulses; poll() consumes them into nav moves."""

    DIRS = (("up", 3), ("down", 15), ("left", 1), ("right", 2))
    CLICK_PIN = 0

    def __init__(self):
        self.available = False
        self._counts = [0, 0, 0, 0]
        self._click = None
        self._click_prev = 1
        try:
            from machine import Pin

            self._pins = []
            for idx, (_name, gpio) in enumerate(self.DIRS):
                p = Pin(gpio, Pin.IN, Pin.PULL_UP)
                p.irq(self._handler(idx), Pin.IRQ_FALLING)
                self._pins.append(p)
            self._click = Pin(self.CLICK_PIN, Pin.IN, Pin.PULL_UP)
            self.available = True
        except Exception as exc:  # noqa: BLE001
            _diag_note("trackball", "unavailable: %s" % (exc,))

    def _handler(self, idx):
        counts = self._counts
        def _h(pin):
            counts[idx] += 1   # list item + small int: ISR-safe (no allocation)
        return _h

    def poll(self):
        # Returns per-direction pulse counts [up, down, left, right] + click edge,
        # so the cursor moves proportionally to how far the ball was rolled.
        counts = [0, 0, 0, 0]
        for idx in range(4):
            counts[idx] = self._counts[idx]
            self._counts[idx] = 0
        click = False
        if self._click is not None:
            lvl = self._click.value()
            if lvl == 0 and self._click_prev == 1:
                click = True
            self._click_prev = lvl
        return counts, click


# Touch -> canvas mapping, calibrated on hardware (RUN_TOUCH_CALIBRATE byte dump).
# This T-Deck's GT911 already reports landscape coords matching the 320x240 canvas
# (x ~0..320, y ~0..240), so no axis swap is needed -- only the Y axis is inverted
# (raw top=240, bottom=0). read_raw() handles the byte order (y in bytes 0-1, x in
# bytes 2-3); these just scale + flip into canvas space.
TOUCH_SWAP = False      # raw axes already match the landscape canvas
TOUCH_FLIP_X = False
TOUCH_FLIP_Y = True     # GT911 Y runs opposite the screen
TOUCH_RAW_W = 320       # GT911 reported max along x
TOUCH_RAW_H = 240       # GT911 reported max along y


class Touch:
    """T-Deck GT911 capacitive touch over I2C0 (the same bus as the keyboard,
    off the SPI bus -- no display contention). poll() returns an absolute
    (x, y, tap) in canvas coords, where tap is True only on the press edge."""

    ADDRS = (0x5D, 0x14)      # GT911 default / alternate I2C addresses
    REG_STATUS = 0x814E       # touch status: bit7 ready, low nibble = point count
    REG_POINT0 = 0x8150       # point 0: [track, xl, xh, yl, yh, sizel, ...]

    def __init__(self, w, h, i2c=None):
        self.w = w
        self.h = h
        self.available = False
        self.addr = None
        self._i2c = i2c
        self._down = False
        try:
            from machine import I2C, Pin

            if self._i2c is None:
                self._i2c = I2C(0, scl=Pin(8), sda=Pin(18), freq=400000)
            for a in self.ADDRS:
                try:
                    self._i2c.readfrom(a, 1)
                    self.addr = a
                    self.available = True
                    break
                except Exception:
                    pass
            if not self.available:
                _diag_note("touch", "GT911 not found on I2C0")
        except Exception as exc:  # noqa: BLE001
            _diag_note("touch", "unavailable: %s" % (exc,))

    def read_raw(self):
        """One GT911 read. Returns (rx, ry) when a finger is down, False when the
        controller reports a fresh sample with no touch (finger up), or None when
        no new sample is ready (state unknown -- keep whatever we had). Clears the
        status register after a ready read so the next sample is produced."""
        if not self.available:
            return None
        try:
            status = self._i2c.readfrom_mem(self.addr, self.REG_STATUS, 1, addrsize=16)[0]
        except Exception:
            return None
        if not (status & 0x80):
            return None  # buffer not ready yet -- do NOT clear, do NOT change state
        raw = False      # ready sample, default "finger up"
        if (status & 0x0F) >= 1:
            try:
                d = self._i2c.readfrom_mem(self.addr, self.REG_POINT0, 4, addrsize=16)
                # This GT911 lays the point out as y(lo,hi) then x(lo,hi) -- see
                # the touch calibration byte dump. Return (x_raw, y_raw) for _map.
                raw = (d[2] | (d[3] << 8), d[0] | (d[1] << 8))
            except Exception:
                raw = None
        try:
            self._i2c.writeto_mem(self.addr, self.REG_STATUS, b"\x00", addrsize=16)
        except Exception:
            pass
        return raw

    def debug_read(self):
        """Calibration only: return (status, 8 raw point bytes) and clear, or None
        when no fresh sample. Lets us see the exact GT911 byte layout."""
        if not self.available:
            return None
        try:
            status = self._i2c.readfrom_mem(self.addr, self.REG_STATUS, 1, addrsize=16)[0]
        except Exception:
            return None
        if not (status & 0x80):
            return None
        data = None
        if (status & 0x0F) >= 1:
            try:
                data = self._i2c.readfrom_mem(self.addr, self.REG_POINT0, 8, addrsize=16)
            except Exception:
                data = None
        try:
            self._i2c.writeto_mem(self.addr, self.REG_STATUS, b"\x00", addrsize=16)
        except Exception:
            pass
        return (status, data)

    def _map(self, rx, ry):
        if TOUCH_SWAP:
            rx, ry = ry, rx
        if TOUCH_FLIP_X:
            rx = TOUCH_RAW_W - 1 - rx
        if TOUCH_FLIP_Y:
            ry = TOUCH_RAW_H - 1 - ry
        x = rx * self.w // TOUCH_RAW_W
        y = ry * self.h // TOUCH_RAW_H
        return max(0, min(self.w - 1, x)), max(0, min(self.h - 1, y))

    def poll(self):
        raw = self.read_raw()
        if not raw:                 # None (no new sample) or False (finger up)
            if raw is False:        # only a confirmed "up" clears the press state
                self._down = False
            return None
        x, y = self._map(raw[0], raw[1])
        tap = not self._down        # press edge -> single tap/click
        self._down = True
        return (x, y, tap)


def _ticks_ms():
    try:
        return time.ticks_ms()
    except AttributeError:
        return int(time.time() * 1000)


def _ticks_diff(a, b):
    try:
        return time.ticks_diff(a, b)
    except AttributeError:
        return a - b


# --- offline diagnostics wiring (kidcode_diag) ------------------------------
#
# Thin guarded shims so the run_desktop loop can route prints + perf samples
# through diag without each call site needing a try/except. `diag` is the
# kidcode_diag module or None (import failed -> everything degrades to a no-op).
# All device-side: the diag SAMPLING/wiring lives here, while the shared
# runtime/console.py only EXPOSES the numbers (perf_capture + perf_sample), so it
# stays host-safe.

def _diag_note(tag, msg):
    """Module-level convenience for call sites that don't hold a `diag` handle
    (the audio/wifi/keyboard backends): lazily import kidcode_diag and persist +
    print the line (logp). Falls back to a plain print if diag is unavailable.
    Fully guarded -- a diag failure here must never affect the caller."""
    try:
        import kidcode_diag

        kidcode_diag.logp(tag, msg)
        return
    except Exception:
        pass
    try:
        print("KidCode", tag, msg)
    except Exception:
        pass


def _diag_log(tag, msg, diag):
    """Persist a line to the diag ring AND print it live (boot serial is still
    useful). logp() does both; falls back to a plain print if diag is absent.
    `diag` is the already-imported module (or None) -- this is the hot-path
    variant used inside run_desktop, avoiding a per-call import."""
    if diag is not None:
        try:
            diag.logp(tag, msg)
            return
        except Exception:
            pass
    try:
        print("KidCode", tag, msg)
    except Exception:
        pass


def _diag_flush(diag, ws):
    """Flush the diag RAM ring to /sd/kidcode/diag.log via the workstation's live
    SD session wrapper (with_sd_live). Guarded: a flush failure is a no-op so it
    can never crash the loop. Skips the write when SD management is disabled (the
    embedded-carts fallback, where carts_root is None -> no writable SD root)."""
    if diag is None:
        return
    try:
        if not getattr(ws, "can_manage", False):
            return
        with_sd = getattr(ws, "_with_sd", None)
        diag.flush_to_sd(with_sd)
    except Exception:
        pass


def _diag_perf_sample(diag, ws):
    """Log a PERF sample line from the workstation's current frame numbers, if a
    cart is actively running. Guarded -> a no-op on any failure."""
    if diag is None:
        return
    try:
        sample = ws.perf_sample()      # (name, fps, flush_ms, draw_ms) or None
        if sample is not None:
            diag.log_perf(sample[0], sample[1], sample[2], sample[3])
    except Exception:
        pass


def _diag_drawbrk(diag, ws):
    """Log a DRAWBRK line splitting the frame's draw cost into cart _update (game
    LOGIC) / cart _draw (RENDERING) / audio.tick / console chrome (the dock+cursor+
    overlays remainder) -- the breakdown that says where draw= goes (logic vs render
    vs audio vs chrome). Guarded -> a no-op on any failure (only meaningful while a
    cart runs)."""
    if diag is None:
        return
    try:
        if ws.perf_sample() is None:        # only while a cart is actively running
            return
        b = ws.perf_breakdown()             # (logic, render, audio, chrome) ms
        diag.log("DRAWBRK", "logic=%.2f render=%.2f audio=%.2f chrome=%.2f"
                 % (b[0], b[1], b[2], b[3]))
    except Exception:
        pass


def _load_carts(session=None):
    """Load cartridges from SD (seeding the built-ins on first boot). Returns
    (carts, carts_root); carts_root is None (management disabled) on fallback to
    the embedded carts if the SD card is missing/unreadable.

    `session` is the SD lifecycle wrapper to mount under. Default is the
    pre-display machine.SDCard path (used by the boot prefetch); pass
    kidcode_sd.with_sd_live for the post-display native path."""
    try:
        import kidcode_sd
        import kid_carts

        if session is None:
            session = kidcode_sd.with_sd

        def _seed_and_scan():
            kid_carts.ensure_dirs()
            kid_carts.seed_builtins(CARTS)
            return kid_carts.scan()

        # Mount only for the seed+scan, then unmount: the render loop must own
        # the shared SPI bus with no SDCard device attached, or flushes hang.
        carts = session(_seed_and_scan)
        if carts:
            print("KidCode loaded %d carts from SD" % len(carts))
            return carts, kid_carts.CARTS_DIR
    except Exception as exc:  # noqa: BLE001
        print("KidCode SD carts unavailable:", exc)
    print("KidCode using built-in carts")
    return [dict(c) for c in CARTS], None


class WebView:
    """Device web view controller (#41/#22): owns the draw-command recorder, the
    non-blocking HTTP + WebSocket server, and the browser-input injection, and is the small
    object the shared console's Settings "WEB VIEW" row toggles (it reads .enabled +
    .url() and calls .toggle()).

    Lifecycle, all driven from run_desktop's single-threaded loop:
      * It starts OFF: ws.canvas stays the RAW DeviceCanvas, so the normal (no-browser)
        path has ZERO per-draw overhead. Turning the view ON swaps a recording TeeCanvas
        in as ws.canvas (and rebinds the wallpaper/running cart to it); even then the
        recorder only records while a browser's WebSocket is connected.
      * toggle() brings WiFi up (reusing the saved-credential autoconnect), reads the
        STA IP, and starts/stops the server. It needs WiFi already joined via the WiFi
        cart; with no saved network it stays OFF and surfaces the reason.
      * Each loop iteration: begin_frame() (start a recording if a WS client is live)
        BEFORE the render, feed_input() to inject queued browser events BEFORE
        inp.begin_frame(), commit_frame() AFTER ws.frame(), and poll() once BETWEEN frames
        to accept new connections + service the persistent WebSocket (drain its input ->
        apply, push the latest committed frame down it). None of these block the render loop.

    TRANSPORT (#41): the live channel is a persistent WebSocket -- frames PUSH down, input
    pushes up, on one socket (no per-frame HTTP handshake). The page + assets still load over
    plain HTTP, and the legacy GET/POST /frame + POST /input endpoints remain as a fallback.

    NEEDS ON-DEVICE VERIFICATION: the socket server + WiFi<->LCD-DMA RAM coexistence
    (#38/#40) are unproven on hardware here."""

    def __init__(self, ws, canvas, inp, pointer, wifi, port=8080):
        self._ws = ws
        self._canvas = canvas          # the REAL DeviceCanvas (panel draws here)
        self._inp = inp
        self._pointer = pointer
        self._wifi = wifi
        self._port = port
        self.enabled = False
        self._url = ""
        self._server = None
        self._rec = None
        self._tee = None
        # Browser one-shot button presses pulsed for exactly one frame (so pressed()
        # fires once), and the held set the browser drives via {type:"hold"}. The
        # trackball-style pan accumulates between feeds.
        self._press_queue = []
        self._pulsed = []
        self._pan = [0, 0]
        self._key_queue = []
        self._held = set()         # browser-held buttons (joystick/WASD), re-asserted each
        self._held_last = set()    # frame in feed_input AFTER keyboard.poll clears them
        # Browser pointer intent, applied AFTER the physical touch read each frame so
        # it isn't clobbered. _br_active True while a browser finger is down (so the
        # cursor follows the browser drag); _br_click latches a tap edge to consume once.
        self._br_x = pointer.x
        self._br_y = pointer.y
        self._br_active = False
        self._br_click = False
        # The cart title whose bitmaps the recorder's atlas currently holds. When the open
        # cart changes the atlas must reset (a new cart's Images mustn't collide with stale
        # id()-keyed indices), mirroring the browser refetching /assets + clearing caches.
        self._atlas_cart = None
        # STREAM MODE (#41 30fps lever): True while the device is headless for a browser
        # that's actively playing (skip the panel rasterize + flush; the cart still runs
        # logic + records cheap commands). Tracked here so begin_frame can detect the
        # enter/exit EDGE: on enter, paint a one-time "playing in browser" notice + flush
        # it; on exit, force a full redraw + re-light so the device panel resumes cleanly.
        self._streaming = False
        try:
            import kc_webserver
            self._web = kc_webserver
            self._rec = kc_webserver.DrawRecorder(canvas.w, canvas.h)
            self._tee = kc_webserver.TeeCanvas(canvas, self._rec)
        except Exception as exc:  # noqa: BLE001 -- no module -> the controller stays inert
            print("KidCode web: module unavailable:", exc)
            self._web = None

    def install(self):
        """Boot wiring: the web view starts OFF, so ws.canvas stays the RAW DeviceCanvas
        and there is ZERO per-draw overhead in the normal (no-browser) path -- the Tee is
        only swapped in when Settings turns the view ON (_bind), and swapped back out when
        it's turned OFF (_unbind). Returns the canvas the loop calls sync_back() on (the
        raw DeviceCanvas, which both the Tee -- via delegation -- and the off path share).
        """
        return self._canvas

    def _bind(self):
        """Swap the TeeCanvas in as ws.canvas (panel still renders through it -> the Tee
        forwards every call to the real DeviceCanvas) and rebind the live drawers to it so
        their draws reach the recorder: recompile the wallpaper, and restart a running cart.
        Without the rebind the wallpaper/cart draw funcs stay bound to the raw canvas and the
        browser sees nothing on the home/cart screen (the same gotcha the host web console
        guards against by recompiling the wallpaper)."""
        if self._tee is None:
            return
        self._ws.canvas = self._tee
        try:
            wp = getattr(self._ws, "wallpaper_id", None)
            if wp:
                self._ws.select_wallpaper(wp, persist=False)   # rebind backdrop to the Tee
        except Exception:  # noqa: BLE001 -- a rebind hiccup must not crash the toggle
            pass
        self._rebind_running_cart()

    def _unbind(self):
        """Swap the raw DeviceCanvas back in as ws.canvas (zero per-draw overhead again)
        and rebind the wallpaper/cart to it, the mirror of _bind."""
        self._ws.canvas = self._canvas
        try:
            wp = getattr(self._ws, "wallpaper_id", None)
            if wp:
                self._ws.select_wallpaper(wp, persist=False)
        except Exception:  # noqa: BLE001
            pass
        self._rebind_running_cart()

    def _rebind_running_cart(self):
        """If a cart is open, re-run it so its namespace recompiles against the current
        ws.canvas (apply() -> _start() rebuilds make_api). No-op on the home/editor screens
        (only a running cart binds the draw API). Guarded so a cart restart can't crash the
        toggle -- if it fails the cart simply isn't mirrored until reopened."""
        try:
            if getattr(self._ws, "cart", None) is not None and self._ws.screen == "desktop":
                self._ws.apply()
        except Exception:  # noqa: BLE001
            pass

    def available(self):
        return self._web is not None

    def url(self):
        return self._url

    # -- Settings toggle -----------------------------------------------------
    def toggle(self):
        if not self.available():
            return
        if self.enabled:
            self._stop()
        else:
            self._start()

    def _start(self):
        # Bring WiFi up (reuse the saved-credential autoconnect: only joins a network
        # the kid already added via the WiFi cart). No creds -> stay OFF with a reason.
        ip = None
        try:
            connected, _ssid, ip = self._wifi.status()
            if not connected:
                if autoconnect_wifi(self._wifi):
                    _conn, _ssid, ip = self._wifi.status()
        except Exception as exc:  # noqa: BLE001
            print("KidCode web: wifi check failed:", exc)
        if not ip:
            self._url = "no wifi"
            self.enabled = False
            _diag_note("web", "start aborted: no wifi (join via WiFi cart first)")
            return
        try:
            self._server = self._web.WebServer(self._rec, _WebProvider(self), self._port)
            if self._server.start(ip):
                self.enabled = True
                self._url = self._server.url()
                self._bind()                 # swap the Tee in (records when a browser polls)
                print("KidCode web view ON:", self._url)
                _diag_note("web", "serving at %s" % self._url)
            else:
                self.enabled = False
                self._url = "bind failed"
        except Exception as exc:  # noqa: BLE001
            print("KidCode web: start failed:", exc)
            self.enabled = False
            self._url = "error"

    def _stop(self):
        if self._server is not None:
            try:
                self._server.stop()
            except Exception:  # noqa: BLE001
                pass
        self._server = None
        self.enabled = False
        if self._rec is not None:
            self._rec.enabled = False
            self._rec.record_only = False
        # If we were mid-stream when the view was turned off, resume the panel cleanly
        # (clears comp.skip_flush, forces a redraw, re-lights) -- no-op if not streaming.
        self._apply_stream_mode(False)
        self._unbind()                       # swap the raw canvas back (zero overhead again)
        print("KidCode web view OFF")
        _diag_note("web", "stopped")

    # -- loop hooks (guarded; never block the render loop) -------------------
    def begin_frame(self):
        if self._server is None:
            return
        was = self._rec.enabled
        self._server.begin_frame()
        # STREAM MODE edge (#41): the server set recorder.record_only for this frame
        # (True only when a browser is live + this frame is recorded). Drive the panel:
        # skip the flush this frame while streaming, and handle the enter/exit transition
        # so a glance at the device isn't a confusing frozen screen.
        self._apply_stream_mode(self._rec.record_only)
        if not self._rec.enabled:
            return
        # Reset the recorder's sprite atlas when the open cart changes: a new cart's tile
        # Images are fresh objects whose id() could coincide with a freed one's, so a
        # stale index would mis-map. The browser does the matching reset (it refetches
        # /assets + clears its caches on a cart change), so the two stay in lock-step.
        cart = getattr(self._ws, "cart", None)
        title = cart.get("title") if cart else None
        if title != self._atlas_cart:
            self._atlas_cart = title
            self._rec.reset_atlas()
        # When a browser (re)connects, force ONE redraw so it gets a full frame even on
        # an idle screen (the redraw-on-change gate #44 would otherwise record nothing
        # until something changes). A running cart redraws every frame regardless.
        if not was:
            try:
                self._ws.mark_dirty()
            except Exception:  # noqa: BLE001
                pass

    def _comp(self):
        """The device compositor (owns the panel flush). None on a host/no-comp build."""
        return getattr(self._ws, "comp", None)

    def _apply_stream_mode(self, streaming):
        """Drive the panel for STREAM MODE this frame (#41): set comp.skip_flush so the
        flush inside ws.frame() is a no-op while headless, and handle the enter/exit EDGE.
        Idempotent + fully guarded -- a transition hiccup must never crash the loop."""
        comp = self._comp()
        if comp is not None:
            try:
                comp.skip_flush = streaming
            except Exception:  # noqa: BLE001
                pass
        if streaming == self._streaming:
            return                               # no edge this frame
        self._streaming = streaming
        if streaming:
            self._enter_stream()
        else:
            self._exit_stream()

    def _enter_stream(self):
        """ENTER stream mode: the device goes headless (the panel will freeze on whatever
        it last showed). Paint a one-time notice + flush it ONCE so a glance at the device
        reads 'playing in browser', not a confusing frozen frame. The notice is drawn
        straight on the REAL canvas (not the Tee) and flushed with skip_flush forced off,
        so it's the last thing the panel shows until the browser disconnects."""
        try:
            comp = self._comp()
            cv = self._canvas
            cv.cls(NAMES["dark_blue"])
            cv.rect(0, 104, cv.w, 36, NAMES["indigo"])
            cv.print("WEB VIEW", 96, 96, NAMES["yellow"], 2)
            cv.print("playing in browser", 70, 124, NAMES["white"], 1)
            if comp is not None:
                save = getattr(comp, "skip_flush", False)
                comp.skip_flush = False          # force the notice out, once
                comp.flush()
                comp.skip_flush = save
        except Exception as exc:  # noqa: BLE001 -- the notice is cosmetic; never crash
            print("KidCode web: stream notice failed:", exc)
        _diag_note("web", "stream mode ON (device headless)")

    def _exit_stream(self):
        """EXIT stream mode: the browser disconnected, so resume the device panel cleanly.
        skip_flush is already cleared by _apply_stream_mode; force a full redraw (the cart/
        UI rasterizes again next frame) and re-light the backlight in case it was off."""
        try:
            self._ws.mark_dirty()
        except Exception:  # noqa: BLE001
            pass
        try:
            import tdeck_display
            tdeck_display.set_backlight(True)
        except Exception:  # noqa: BLE001 -- host / display-less: ignore
            pass
        _diag_note("web", "stream mode OFF (panel resumed)")

    def commit_frame(self):
        if self._server is not None:
            self._server.commit_frame()

    def poll(self):
        if self._server is not None:
            try:
                self._server.poll()
            except Exception as exc:  # noqa: BLE001 -- a bad request never bricks the loop
                print("KidCode web: poll error:", exc)

    def feed_input(self, now):
        """Apply queued browser input. Called once per loop iteration, just BEFORE
        inp.begin_frame() so a browser button press registers a clean one-frame edge:
        last frame's pulsed buttons are released first, then this frame's queued ones
        are held (begin_frame then computes pressed = held - last). The pan nudges the
        cursor like the trackball does. No-op when nothing's queued (the common path)."""
        # Release last frame's one-shot presses.
        for name in self._pulsed:
            try:
                self._inp.set_button(name, False)
            except Exception:  # noqa: BLE001
                pass
        self._pulsed = []
        # Hold this frame's queued one-shot presses (released next feed).
        if self._press_queue:
            for name in self._press_queue:
                try:
                    self._inp.set_button(name, True)
                except Exception:  # noqa: BLE001
                    pass
            self._pulsed = self._press_queue
            self._press_queue = []
        # Re-assert browser-held buttons (joystick / WASD) on top of the physical keyboard.
        # feed_input runs AFTER keyboard.poll(), which clears any button with no physical key
        # down -- so without this the web holds never reach the cart's btn(). Clear ones
        # released since last frame; assert the current held set.
        for name in self._held_last:
            if name not in self._held:
                try:
                    self._inp.set_button(name, False)
                except Exception:  # noqa: BLE001
                    pass
        for name in self._held:
            try:
                self._inp.set_button(name, True)
            except Exception:  # noqa: BLE001
                pass
        self._held_last = set(self._held)
        # Browser trackball pan -> cursor move (mirrors the device loop's _cursor_delta).
        if self._pan[0] or self._pan[1]:
            self._pointer.move(self._pan[0] * 4, self._pan[1] * 4)
            self._pan = [0, 0]
        # Browser typed key -> last_key for THIS frame. Applied here (after the loop's
        # keyboard.poll() which would otherwise reset last_key to 0) so a cart in
        # textmode()/the code editor sees it; consumed so it's one byte for one frame.
        if self._key_queue:
            try:
                self._inp.last_key = self._key_queue[-1]
            except Exception:  # noqa: BLE001
                pass
            self._key_queue = []

    def feed_pointer(self, physical_active):
        """Merge browser pointer intent into the real Pointer. Called in the loop AFTER
        the physical touch read so it isn't clobbered, and only when the physical touch
        is NOT active (a real finger on the device wins). Places the cursor at the
        browser finger and OR-s in a tap edge; returns True if a browser tap fired this
        frame (so the loop sets pointer.click)."""
        clicked = False
        if not physical_active and (self._br_active or self._br_click):
            self._pointer.place(self._br_x, self._br_y)
            self._pointer.down = self._br_active
            if self._br_click:
                self._br_click = False         # consume the tap edge once
                clicked = True
        return clicked

    # -- input event hooks handed to kc_webserver.apply_events ---------------
    def _on_press(self, name):
        self._press_queue.append(name)

    def _on_pan(self, dx, dy):
        self._pan[0] += dx
        self._pan[1] += dy

    def _on_hold(self, name, down):
        # Track a browser-held button (joystick/WASD). feed_input re-asserts the held set
        # AFTER the loop's keyboard.poll() (which clears buttons -- no physical key is down),
        # so the cart's btn() actually sees it. (Setting it here, in poll(), gets wiped by
        # the next keyboard.poll before the cart runs -- the joystick/WASD not-reacting bug.)
        if down:
            self._held.add(name)
        else:
            self._held.discard(name)

    def _on_key(self, code):
        # Queue a typed key; feed_input applies it AFTER the loop's keyboard.poll() so it
        # isn't reset to 0 before the cart reads last_key. One byte per frame, like the
        # T-Deck keyboard's own ASCII path.
        self._key_queue.append(code)

    def _on_esc(self):
        # Leave an open editor/menu panel back to the desktop (mirrors the host esc).
        try:
            if self._ws.screen == "menu":
                self._ws._leave_menu()
        except Exception:  # noqa: BLE001
            pass

    # -- data the server asks for --------------------------------------------
    def assets(self):
        ws = self._ws
        cart = getattr(ws, "cart", None)
        title = cart.get("title") if cart else None
        rate = AUDIO_RATE
        return self._web.assets_payload(self._canvas.w, self._canvas.h, PAL565,
                                        getattr(ws, "sheet", None),
                                        getattr(ws, "tilemap", None), title, rate)

    def frame(self):
        cart = getattr(self._ws, "cart", None)
        title = cart.get("title") if cart else None
        cmds = self._rec.frame() if self._rec is not None else []
        return (cmds, title)

    def apply(self, events):
        # Route pointer events through a sink (captured into browser-pointer state and
        # merged later by feed_pointer, so the per-frame physical touch read doesn't
        # clobber them); buttons/keys/pan go through the hooks. apply_events guards each
        # event, so a malformed one is skipped, never raised.
        sink = _PointerSink(self)
        self._web.apply_events(events, self._inp, sink,
                               on_press=self._on_press, on_pan=self._on_pan,
                               on_key=self._on_key, on_esc=self._on_esc,
                               on_hold=self._on_hold)


class _PointerSink:
    """A Pointer-shaped target for kc_webserver.apply_events: place()/down/click write
    into the WebView's browser-pointer intent instead of the live cursor, so the loop's
    physical touch read can't clobber a browser tap (feed_pointer merges it later)."""

    def __init__(self, view):
        self._v = view

    def place(self, x, y):
        self._v._br_x = int(x)
        self._v._br_y = int(y)

    @property
    def down(self):
        return self._v._br_active

    @down.setter
    def down(self, v):
        self._v._br_active = bool(v)

    @property
    def click(self):
        return self._v._br_click

    @click.setter
    def click(self, v):
        if v:
            self._v._br_click = True


class _WebProvider:
    """Thin adapter so kc_webserver.WebServer never holds console refs directly: it
    asks this for /assets, /frame, and to apply /input -- all delegated to the WebView."""

    def __init__(self, view):
        self._v = view

    def assets(self):
        return self._v.assets()

    def frame(self):
        return self._v.frame()

    def apply(self, events):
        self._v.apply(events)


def run_desktop(handler, prefetched=None, fps_cap=60):
    """Boot the workstation on the device: launcher + carts + keyboard.

    `prefetched` is the (carts, carts_root) tuple read from SD BEFORE display
    init (see kidcode_shell._prefetch_carts). SD shares the panel's SPI bus, so
    mounting after the panel runs hard-hangs the device -- never call _load_carts
    here once the display is live."""
    if handler is not None:
        try:
            handler.deinit()  # stop the LVGL TaskHandler; the compositor owns the bus
        except Exception as exc:
            print("KidCode desktop: takeover failed:", exc)
    try:
        from tdeck_display import get_display_bus
        from kc_compositor import make_compositor
        from kidcode.input import InputState, TDeckKeyboard
    except Exception as exc:
        print("KidCode desktop unavailable:", exc)
        return
    comp = make_compositor(get_display_bus(), 320, 240, strip_h=24)
    if comp is None:
        print("KidCode desktop: no compositor")
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
    import kidcode_sd
    # Carts are read from SD before display init; only fall back to a post-display
    # mount (now safe via the native kc_sd path) if the shell didn't prefetch.
    carts, carts_root = (prefetched if prefetched is not None
                         else _load_carts(kidcode_sd.with_sd_live))
    import kid_carts
    ws = Workstation(comp, canvas, inp, carts)
    ws.make_api = make_api        # device cart namespace (DeviceCanvas + Image + color)
    ws.make_audio = make_audio    # device I2S audio backend (#16, NEEDS HW VERIFICATION)
    ws.carts_store = kid_carts    # SD .kcart store (scan/load/save/create/dup/delete)
    ws.carts_root = carts_root
    # Writes are enabled on-device via kc_sd: it attaches the SD card to the SPI
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
        return kidcode_sd.with_sd_live(fn)
    ws._with_sd = _with_sd_synced
    # OTA firmware update (#53): the shared console's Settings -> UPDATE FW row flashes a
    # new app image from /sd/update into the inactive OTA slot (esp32.Partition) and
    # reboots. SD shares the panel SPI host, so the updater reads through the SAME
    # _with_sd_synced wrapper as cart saves (drain panel DMA -> native single-bus mount).
    # Available only on an --ota build (running slot is ota_0/ota_1); on a legacy single-
    # factory image available() is False and the row never shows.
    try:
        import kc_ota
        ws.updater = kc_ota.OtaUpdater(_with_sd_synced)
    except Exception as exc:
        print("KidCode: OTA updater unavailable:", exc)
    ws.pointer = pointer
    ws.keyboard = keyboard        # lets the code editor switch to text (ASCII) mode
    # WiFi (#38): one SYSTEM service (network.WLAN STA) shared across carts, so the
    # connection persists when the WiFi-manager cart exits and #22/#8 can use it.
    # Injected into a cart's namespace ONLY when its manifest grants "network".
    # Autoconnect from the saved creds at boot. NEEDS ON-DEVICE VERIFICATION.
    ws.wifi = make_wifi(kid_carts, carts_root)
    # OTA online update (#53, Phase 3): hand the updater the wifi service so Settings ->
    # UPDATE ONLINE can fetch a manifest + stream a new image to SD. go_online reuses the
    # saved-credential autoconnect (autoconnect_wifi) so the kid needn't re-enter wifi to
    # update -- it only connects to a network they already joined via the WiFi cart.
    if getattr(ws, "updater", None) is not None:
        try:
            ws.updater.set_wifi(ws.wifi, go_online=lambda: autoconnect_wifi(ws.wifi))
        except Exception as exc:
            print("KidCode: OTA wifi wiring failed:", exc)
    # System menu (#52): the ≡ dropdown's "Reboot" row. On device a real reboot is
    # machine.reset(); the shared console calls this injected hook (None on host -> a
    # safe go_home stub). Additive -- it never touches the render/flush path.
    try:
        import machine
        ws.reboot_hook = machine.reset
    except Exception as exc:
        print("KidCode: reboot hook unavailable:", exc)
    # Web view (#41/#22): serve the running console to a browser on the same WiFi via a
    # draw-command stream (NOT raw pixels -- WiFi is ~72KB/s, 153KB/frame is unplayable).
    # It starts OFF: ws.canvas stays the RAW DeviceCanvas so there is ZERO per-draw cost
    # in the normal (no-browser) path. Only when Settings -> WEB VIEW turns it ON does the
    # WebView swap a recording TeeCanvas in as ws.canvas (and even then it records only
    # while a browser is actively polling /frame). web is None on a build without
    # kc_webserver -> the Settings row is hidden.
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
    # icons from -- from system_icons.kgfx on SD if present, else the baked default
    # theme. Same store + with_sd_live path as system.json.
    ws.load_icon_sheet()
    # Achievements (#21): load the unlocked badges (achievements.json) so earned
    # milestones survive a reboot. Same store + with_sd_live path as system.json.
    ws.load_achievements()
    # Offline diagnostics (kidcode_diag): RAM ring now, flushed to SD every ~5s and
    # on a crash, dumped to serial at next boot. perf_capture makes ws.frame() record
    # the flush/draw split each frame WITHOUT drawing the on-screen HUD, so the perf
    # sampler below can read steady numbers. Guarded import: no diag -> plain loop.
    try:
        import kidcode_diag as diag
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
    ws.arm_splash()               # boot logo: show the moybyte mascot before the launcher
    while True:
        now = _ticks_ms()
        dt = max(0.0, min(0.1, _ticks_diff(now, last) / 1000.0))
        last = now
        try:
            keyboard.poll()
        except Exception:
            pass
        # Web view (#41): start this frame's recording (no-op unless a browser is live)
        # and inject any queued browser button/pan input BEFORE begin_frame, so a
        # browser press registers a clean one-frame edge like the keyboard's.
        web.begin_frame()
        web.feed_input(now)
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
        # DMA double-buffer (#40, default OFF): point the canvas at the compositor's
        # current BACK buffer before drawing. The previous flush() swapped it, so this
        # frame's cls/rect/spr/map must target the new back, never the buffer that's
        # mid-DMA. No-op (buffer unchanged) in single-buffer mode or on a skipped frame.
        _frames_before = getattr(ws, "_frames_drawn", 0)
        canvas.sync_back()
        try:
            ws.handle_input()                   # keyboard W/A/S/D etc.
            ws.handle_pointer()                 # cursor hover + click
            ws.frame(dt)                        # draw + composite + flush
        except Exception as exc:                # never let one bad frame brick the device:
            # Capture the crash in diag AND print it live: a crash we can't see live
            # (the takeover loop has starved USB) is the whole reason diag exists, so
            # flush it to SD immediately so next boot's dump has it.
            _diag_log("frame error", exc, diag)
            print("KidCode frame error:", exc)  # print the traceback's reason to serial
            _diag_flush(diag, ws)
            gc.collect()                        # a NO_MEM flush may recover after a collect
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
                print("KidCode backlight on failed:", _bl)
            _backlight_on = True
        # Diag perf sample (~3s): a structured "PERF cart=<name> fps=<n> flush=<ms>
        # draw=<ms>" line while a cart runs -- the payload that makes "play -> reboot
        # -> paste the serial" yield per-cart frame timings offline. No SD touch here
        # (just the RAM ring); the 5s flush below is what writes it out.
        _tnow = _ticks_ms()
        if diag is not None and _ticks_diff(_tnow, _diag_perf_at) >= 0:
            _diag_perf_at = _tnow + 3000
            _diag_perf_sample(diag, ws)
            _diag_drawbrk(diag, ws)
        # Diag SD flush (~5s): overwrite /sd/kidcode/diag.log with the current ring.
        # Runs between frames on the native single-bus path (with_sd_live), never
        # during a panel flush. Guarded -> a flush failure degrades to a no-op.
        if diag is not None and _ticks_diff(_tnow, _diag_flush_at) >= 0:
            _diag_flush_at = _tnow + 5000
            _diag_flush(diag, ws)
        # Web view (#41): service the server BETWEEN frames, fully non-blocking -- accept
        # new connections + drain the persistent WebSocket's queued input and push the
        # latest committed frame down it (WiFi STA is a separate peripheral from the display
        # SPI, so this never touches the SD/panel bus -- it only competes for CPU here).
        # No-op when the server is off; a slow client is dropped, never waited on.
        web.poll()
        elapsed = _ticks_diff(_ticks_ms(), now)
        if elapsed < frame_ms:
            time.sleep_ms(frame_ms - elapsed)


def run_touch_calibrate(handler):
    """Touch bring-up aid (kidcode_shell.RUN_TOUCH_CALIBRATE). Draws corner
    targets and prints each GT911 sample (raw + current mapping) over serial.

    It flushes the panel only ONCE up front and then just polls + prints, so USB
    serial keeps draining -- the normal desktop loop's continuous flush starves
    USB and you'd see nothing. Touch each yellow corner, read the raw coords over
    serial, then set TOUCH_SWAP / TOUCH_FLIP_X / TOUCH_FLIP_Y / TOUCH_RAW_* above
    so the mapped value lands on that corner, and rebuild."""
    if handler is not None:
        try:
            handler.deinit()
        except Exception as exc:  # noqa: BLE001
            print("KidCode touch-cal: takeover failed:", exc)
    try:
        from tdeck_display import get_display_bus
        from kc_compositor import make_compositor
        from kidcode.input import InputState, TDeckKeyboard
    except Exception as exc:  # noqa: BLE001
        print("KidCode touch-cal unavailable:", exc)
        return
    comp = make_compositor(get_display_bus(), 320, 240, strip_h=40)
    if comp is None:
        print("KidCode touch-cal: no compositor")
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
    print("KidCode touch-cal start avail=%d addr=%s"
          % (1 if touch.available else 0, hex(touch.addr) if touch.addr else "?"))
    while True:
        r = touch.debug_read()
        if r and r[1]:  # (status, 8 raw bytes) on a real touch
            status, d = r
            print("KidCode touch-cal status=0x%02x bytes=%s"
                  % (status, " ".join("%02x" % b for b in d)))
        time.sleep_ms(50)


def run_keyboard_probe(handler):
    """Keyboard bring-up aid (kidcode_shell.RUN_KEYBOARD_PROBE): read the T-Deck
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
            print("KidCode kb-probe: takeover failed:", exc)
    try:
        from machine import I2C, Pin
    except Exception as exc:  # noqa: BLE001
        print("KidCode kb-probe unavailable:", exc)
        return
    addr = 0x55
    try:
        i2c = I2C(0, scl=Pin(8), sda=Pin(18), freq=400000)
    except Exception as exc:  # noqa: BLE001
        print("KidCode kb-probe i2c failed:", exc)
        return
    found = []
    try:
        found = i2c.scan()
    except Exception:  # noqa: BLE001
        pass
    print("KidCode keyboard probe start; i2c scan=%s addr=0x%02x"
          % ([hex(a) for a in found], addr))
    print("KidCode kb-probe: tap keys L->R, T->B. lines = KEY <n> 0x<hex> <dec> '<char>'")
    prev = 0
    n = 0
    beat = 0
    while True:
        try:
            d = i2c.readfrom(addr, 1)
            k = d[0] if d else 0
        except Exception as exc:  # noqa: BLE001
            print("KidCode kb-probe read err:", exc)
            time.sleep_ms(300)
            continue
        if k and k != prev:
            n += 1
            ch = chr(k) if 0x20 <= k <= 0x7E else "."
            print("KEY %d 0x%02x %d '%s'" % (n, k, k, ch))
        prev = k
        beat += 1
        if beat % 250 == 0:        # ~5s heartbeat so you know it's alive
            print("KidCode kb-probe alive (keys so far: %d)" % n)
        time.sleep_ms(20)
