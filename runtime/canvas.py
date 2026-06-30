"""Indexed software canvas for the v0.4 workstation (host reference impl).

A `Canvas` is a `width x height` buffer of palette indices (default 480x270, the
v0.4 logical workstation surface) with a TIC-80-style drawing API
(cls / pix / line / rect / rectb / circ / circb / spr / print) -- note rect/circ
are FILLED and rectb/circb are the outlines, per TIC-80. `to_rgb888()` resolves
indices through the palette for display (pygame) or export (GIF). The same
index-based API is what the device backend maps onto the native `kc_compositor`
RGB565 framebuffer.

Draw STATE (TIC-80 cluster 2, #11): every primitive respects a `camera` offset
(subtracted from all coords), a `clip` rectangle (pixels outside are dropped), a
`pal` index remap (draw-time colour swap), and `palt` per-index sprite
transparency. These four are kept byte-identical on the device backend
(`DeviceCanvas`) so a `.kcart` draws the same pixels everywhere.
"""

from . import font as _font
from . import palette as _pal
from .editors import SpriteSheet  # noqa: F401  (canonical home; re-exported here)


class Image:
    """A small indexed sprite. `pix` is a flat list/bytes of palette indices."""

    def __init__(self, width, height, pix, transparent=None):
        self.w = width
        self.h = height
        self.pix = pix
        self.transparent = transparent

    @classmethod
    def from_ascii(cls, rows, mapping, transparent="."):
        """Build from ['..##..', ...] using {char: index}; `transparent` char skipped."""
        h = len(rows)
        w = max(len(r) for r in rows) if rows else 0
        t_index = -1
        pix = []
        for y in range(h):
            row = rows[y]
            for x in range(w):
                ch = row[x] if x < len(row) else transparent
                if ch == transparent:
                    pix.append(t_index)
                else:
                    pix.append(mapping[ch] & 63)
        return cls(w, h, pix, transparent=t_index)


class Canvas:
    # The game canvas always renders petme128 text at the native 8px cell (the
    # device can't scale text). The SYSTEM canvas (SystemCanvas) overrides this to
    # the user's settings-chosen scale; _blit_glyph reads it so icon glyphs follow
    # the text size. Kept on the base so every backend exposes it uniformly.
    font_scale = 1

    def __init__(self, width=480, height=270, palette=None):
        self.w = width
        self.h = height
        self.palette = palette or _pal.KID64
        self.buf = bytearray(width * height)
        # Draw state (TIC-80 cluster 2). reset_state() initialises camera/clip/pal/palt.
        self.reset_state()

    # -- draw state (camera / clip / pal / palt, #11) ------------------------

    def reset_state(self):
        """Restore camera (0,0), clip (full screen), pal (identity), palt (all
        opaque). The console calls this before each cart frame so draw state never
        leaks between carts or between a cart and the UI."""
        self._cam_x = 0
        self._cam_y = 0
        self._clip_x0 = 0
        self._clip_y0 = 0
        self._clip_x1 = self.w
        self._clip_y1 = self.h
        # pal remap: index i draws as _pal_map[i]. Identity by default.
        self._pal_map = bytearray(range(64))
        # palt: per-index sprite transparency. TIC-80 defaults index 0 transparent,
        # but v0.4's spr() has always used an explicit colorkey (default -1 = none),
        # so to keep existing carts pixel-identical the default here is ALL OPAQUE.
        # A cart opts in via palt(c, True).
        self._palt = bytearray(64)        # 0 = opaque, 1 = transparent

    def camera(self, x=0, y=0):
        """TIC-80 camera(x, y): subtract (x, y) from all subsequent draw coords so a
        world-space cart scrolls. camera() with no args resets to (0, 0). Returns the
        previous offset (TIC-80 returns the prior camera)."""
        prev = (self._cam_x, self._cam_y)
        self._cam_x = int(x)
        self._cam_y = int(y)
        return prev

    def clip(self, x=None, y=None, w=None, h=None):
        """TIC-80 clip(x, y, w, h): restrict drawing to a rectangle (screen space,
        i.e. AFTER the camera offset, like TIC-80). clip() with no args resets to the
        full screen. The rect is clamped to the canvas."""
        if x is None:
            self._clip_x0 = 0
            self._clip_y0 = 0
            self._clip_x1 = self.w
            self._clip_y1 = self.h
            return
        x = int(x)
        y = int(y)
        w = int(w)
        h = int(h)
        self._clip_x0 = max(0, x)
        self._clip_y0 = max(0, y)
        self._clip_x1 = min(self.w, x + w)
        self._clip_y1 = min(self.h, y + h)

    def pal(self, c0=None, c1=None):
        """TIC-80 pal(c0, c1): remap draw-time index c0 -> c1 (recolour idiom). pal()
        with no args resets the table to identity. Applies to every primitive AND to
        sprite pixels (so a recoloured sprite draws with swapped palette entries)."""
        if c0 is None:
            for i in range(64):
                self._pal_map[i] = i
            return
        self._pal_map[int(c0) & 63] = int(c1) & 63

    def palt(self, c=None, on=None):
        """TIC-80 palt(c, on): mark index c transparent (on=True) or opaque for spr().
        palt() with no args resets to the default (all opaque). This is consulted in
        addition to the per-call colorkey / Image.transparent."""
        if c is None:
            for i in range(64):
                self._palt[i] = 0
            return
        self._palt[int(c) & 63] = 1 if on else 0

    # -- primitives ----------------------------------------------------------

    def cls(self, c=0):
        # cls ignores camera/clip (it's a full-surface reset, like TIC-80) but DOES
        # honour the pal remap so a recoloured palette clears consistently.
        self.buf[:] = bytes((self._pal_map[c & 63],)) * (self.w * self.h)

    def _put(self, x, y, ci):
        # Single clipped, camera-offset, pal-remapped pixel write. `ci` is a raw
        # 0-63 index; pal remap + clip + camera are applied here so every primitive
        # that funnels through _put inherits all four pieces of draw state.
        x = x - self._cam_x
        y = y - self._cam_y
        if not (self._clip_x0 <= x < self._clip_x1 and self._clip_y0 <= y < self._clip_y1):
            return
        self.buf[y * self.w + x] = self._pal_map[ci & 63]

    def pix(self, x, y, c=None):
        # TIC-80 pix: read the index at (x, y) with two args, set it with three
        # (replaces the old pset/pget pair). Reads are camera-relative too.
        x = int(x) - self._cam_x
        y = int(y) - self._cam_y
        if not (0 <= x < self.w and 0 <= y < self.h):
            return 0
        if c is None:
            return self.buf[y * self.w + x]
        if not (self._clip_x0 <= x < self._clip_x1 and self._clip_y0 <= y < self._clip_y1):
            return
        self.buf[y * self.w + x] = self._pal_map[c & 63]

    def line(self, x0, y0, x1, y1, c):
        x0 = int(x0)
        y0 = int(y0)
        x1 = int(x1)
        y1 = int(y1)
        ci = c & 63
        dx = abs(x1 - x0)
        dy = -abs(y1 - y0)
        sx = 1 if x0 < x1 else -1
        sy = 1 if y0 < y1 else -1
        err = dx + dy
        while True:
            self._put(x0, y0, ci)
            if x0 == x1 and y0 == y1:
                break
            e2 = 2 * err
            if e2 >= dy:
                err += dy
                x0 += sx
            if e2 <= dx:
                err += dx
                y0 += sy

    def rect(self, x, y, w, h, c):
        # TIC-80 rect = FILLED rectangle (the old rectfill). Camera-offset the corner,
        # then intersect the span with the clip rect so out-of-clip pixels are dropped.
        x = int(x) - self._cam_x
        y = int(y) - self._cam_y
        x0 = max(self._clip_x0, x)
        y0 = max(self._clip_y0, y)
        x1 = min(self._clip_x1, x + int(w))
        y1 = min(self._clip_y1, y + int(h))
        if x1 <= x0 or y1 <= y0:
            return
        ci = self._pal_map[c & 63]
        row = bytes((ci,)) * (x1 - x0)
        buf = self.buf
        width = self.w
        for yy in range(y0, y1):
            base = yy * width + x0
            buf[base:base + (x1 - x0)] = row

    def rectb(self, x, y, w, h, c):
        # TIC-80 rectb = rectangle border/outline (the old rect).
        x = int(x)
        y = int(y)
        w = int(w)
        h = int(h)
        self.rect(x, y, w, 1, c)
        self.rect(x, y + h - 1, w, 1, c)
        self.rect(x, y, 1, h, c)
        self.rect(x + w - 1, y, 1, h, c)

    def circ(self, cx, cy, r, c):
        # TIC-80 circ = FILLED circle (the old circfill). Each scanline is a rect(),
        # so camera/clip/pal apply through rect().
        cx = int(cx)
        cy = int(cy)
        r = int(r)
        for dy in range(-r, r + 1):
            span = int((r * r - dy * dy) ** 0.5)
            self.rect(cx - span, cy + dy, 2 * span + 1, 1, c)

    def circb(self, cx, cy, r, c):
        # TIC-80 circb = circle border/outline (the old circ).
        cx = int(cx)
        cy = int(cy)
        r = int(r)
        ci = c & 63
        x = r
        y = 0
        err = 0
        while x >= y:
            for px, py in ((x, y), (y, x), (-y, x), (-x, y), (-x, -y), (-y, -x), (y, -x), (x, -y)):
                self._put(cx + px, cy + py, ci)
            y += 1
            if err <= 0:
                err += 2 * y + 1
            else:
                x -= 1
                err -= 2 * x + 1

    def spr(self, img, x, y, scale=1, flip=0):
        # TIC-80 flip: 0=none, 1=horizontal, 2=vertical, 3=both. The source pixel
        # read is mirrored per `flip`; camera/clip/pal/palt all apply through _put.
        x = int(x)
        y = int(y)
        scale = int(scale)
        flip = int(flip)
        fx = flip & 1
        fy = (flip >> 1) & 1
        t = img.transparent
        iw = img.w
        ih = img.h
        pix = img.pix
        palt = self._palt
        if scale <= 1:
            for sy in range(ih):
                ssy = (ih - 1 - sy) if fy else sy
                base_s = ssy * iw
                ty = y + sy
                for sx in range(iw):
                    ssx = (iw - 1 - sx) if fx else sx
                    p = pix[base_s + ssx]
                    if p == t or p < 0 or palt[p & 63]:
                        continue
                    self._put(x + sx, ty, p)
            return
        # Scaled blit: each source pixel becomes a scale x scale block.
        for sy in range(ih):
            ssy = (ih - 1 - sy) if fy else sy
            base_s = ssy * iw
            for sx in range(iw):
                ssx = (iw - 1 - sx) if fx else sx
                p = pix[base_s + ssx]
                if p == t or p < 0 or palt[p & 63]:
                    continue
                self.rect(x + sx * scale, y + sy * scale, scale, scale, p)

    def spr_batch(self, sheet, items, colorkey=-1, scale=1):
        # Draw many sheet tiles in one call (#43) -- the sprite analogue of map(). The
        # device collapses this to a single kc_gfx.blit_batch C call (the draw-call
        # count is its FPS bottleneck); on the host this readable per-item loop is the
        # reference, and must match the device pixel-for-pixel. `items` is a sequence of
        # (tile, x, y) or (tile, x, y, flip) tuples; tiles resolve through `sheet` like
        # map(). camera/clip/pal/palt all apply inside self.spr().
        for it in items:
            tile = it[0]
            x = it[1]
            y = it[2]
            flip = it[3] if len(it) > 3 else 0
            img = sheet.tile_image(int(tile), colorkey)
            if img is None:
                continue
            self.spr(img, x, y, scale, flip)

    def map(self, tilemap, sheet, mx=0, my=0, w=None, h=None,
            sx=0, sy=0, colorkey=-1, scale=1):
        # TIC-80 map(): blit a w x h cell region of `tilemap` (top-left cell mx,my)
        # over `sheet` to screen (sx, sy). Each non-empty cell draws its 8x8 sheet
        # tile via spr() at `scale` (so scale=2 => 16px world tiles). The native
        # device path (DeviceCanvas.map -> kc_gfx.blit_map) does this in one C call;
        # here it's the readable per-tile reference. Tile images are cached by id so
        # a repeated tile is built once per draw, not once per cell. spr() carries
        # camera/clip/pal/palt, so map inherits the draw state too.
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
                self.spr(img, sx + cx * step, py, scale)

    def print(self, s, x, y, c, scale=1):
        # Render with the shared petme128 8x8 font so host text is pixel-identical
        # to the device's framebuf.text. Fixed 8px like the device -- `scale` is
        # accepted for call-compatibility but ignored (the device can't scale text).
        ci = c & 63
        put = self._put

        def emit(px, py):
            put(px, py, ci)

        _font.draw(emit, s, x, y)

    # -- scroll layers (#54) -------------------------------------------------

    def new_layer(self, w, h):
        """A blank, wider off-screen canvas the cart pre-renders a level into ONCE,
        then window-copies to the screen per frame (draw_layer -> blit_window_from).
        Same Canvas type + palette, so every draw verb (map/spr/rect/circ/...) works on
        it pixel-identically -- the whole point of the scroll engine: replace a
        per-frame full-background re-render with a flat memory copy."""
        return Canvas(int(w), int(h), self.palette)

    def blit_window_from(self, layer, cam_x=0, cam_y=0):
        """Copy the visible self.w x self.h window of `layer` (a wider pre-rendered
        Canvas) into this canvas at offset (cam_x, cam_y) -- a flat per-row index copy,
        the host parity of the device kc_gfx.blit_window (which copies RGB565). Clamped
        to the source bounds exactly like the C kernel; draw_layer keeps cam in range so
        the full window always lands. Overwrites (no transparency): it's the background,
        drawn first each frame, and it erases last frame's sprites for free."""
        cam_x = int(cam_x)
        cam_y = int(cam_y)
        if cam_x < 0:
            cam_x = 0
        if cam_y < 0:
            cam_y = 0
        dst = self.buf
        src = layer.buf
        dw = self.w
        dh = self.h
        src_w = layer.w
        if src_w <= 0 or dw <= 0 or dh <= 0:
            return
        if cam_x + dw > src_w:            # clamp window to source width
            dw = src_w - cam_x
        if dw <= 0:
            return
        src_rows = len(src) // src_w
        if cam_y + dh > src_rows:         # clamp window to source height
            dh = src_rows - cam_y
        if dh <= 0:
            return
        for row in range(dh):
            d0 = row * dw
            s0 = (cam_y + row) * src_w + cam_x
            dst[d0:d0 + dw] = src[s0:s0 + dw]

    def blit_strip(self, layer, dst_x=0, dst_y=0):
        """Copy ALL of `layer` (its full layer.w x layer.h) into this canvas with its
        top-left at (dst_x, dst_y), opaquely (no transparency / colorkey). The positioned,
        partial-height sibling of blit_window_from: where blit_window_from window-copies a
        full-screen slice of a WIDER source, blit_strip stamps a SMALLER source (e.g. a
        cached W x 18px top-bar strip) at a fixed offset. Host copies palette indices; the
        device (DeviceCanvas.blit_strip) copies RGB565 via the same kc_gfx.blit565 the
        sprite path uses (key=-1 -> fully opaque). Out-of-bounds rows/cols are clamped to
        the destination, exactly like the C kernel, so an over-tall/over-wide strip is
        safe. Ignores camera/clip/pal (it's a chrome blit over a finished frame)."""
        dst_x = int(dst_x)
        dst_y = int(dst_y)
        dst = self.buf
        src = layer.buf
        dw = self.w
        dh = self.h
        sw = layer.w
        if sw <= 0 or dw <= 0 or dh <= 0:
            return
        sh = len(src) // sw
        for row in range(sh):
            ty = dst_y + row
            if ty < 0 or ty >= dh:
                continue
            s0 = row * sw
            # Clamp the source row's horizontal span to the destination.
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
            dst[d0:d0 + cw] = src[s0 + sx0:s0 + sx0 + cw]

    # -- output --------------------------------------------------------------

    def to_rgb888(self):
        pal3 = [bytes(rgb) for rgb in self.palette]
        return b"".join(pal3[i] for i in self.buf)


class SystemCanvas(Canvas):
    """The SYSTEM canvas (the panel/window surface): identical to `Canvas` except
    its `print` honours a settings-chosen `font_scale` (petme128 nearest-neighbor
    x1/x2/x3). The two-domain seam (#39): the desktop/launcher/settings + status
    strip + dock draw here at native resolution and reflow with the size; the
    running cart (and, for now, the editors) draw on the fixed 320x240 GAME canvas
    (a plain `Canvas`, text always 8px) and are composited in as a viewport.

    At font_scale == 1 every drawn pixel is byte-identical to Canvas.print -- the
    graceful-degradation guarantee (a 320x240 system canvas at scale 1 is exactly
    today). Scaling is contained entirely here, so a cart that ever calls print on
    its game canvas is never affected."""

    def __init__(self, width=320, height=240, palette=None, font_scale=1):
        Canvas.__init__(self, width, height, palette)
        self.font_scale = max(1, int(font_scale))

    def set_font_scale(self, scale):
        self.font_scale = max(1, int(scale))

    def print(self, s, x, y, c, scale=1):
        # System text: render petme128 at `font_scale` (nearest-neighbor). At scale
        # 1 each glyph pixel is one _put -- identical to Canvas.print. At >1 each
        # becomes a font_scale x font_scale filled block via rect() (which carries
        # clip/camera/pal). `scale` (the legacy per-call arg) is ignored, exactly
        # like Canvas.print, so callers that pass 1/2 keep working.
        ci = c & 63
        fs = self.font_scale
        if fs <= 1:
            put = self._put

            def emit(px, py):
                put(px, py, ci)

            _font.draw(emit, s, x, y)
            return

        def block(bx, by, n):
            self.rect(bx, by, n, n, ci)

        _font.draw_scaled(block, s, x, y, fs)
