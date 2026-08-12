"""Indexed software canvas for the v0.4 workstation (host reference impl).

A `Canvas` is a `width x height` buffer of palette indices (default 480x270, the
v0.4 logical workstation surface) with a TIC-80-style drawing API
(cls / pix / line / rect / rectb / circ / circb / spr / print) -- note rect/circ
are FILLED and rectb/circb are the outlines, per TIC-80. `to_rgb888()` resolves
indices through the palette for display (pygame) or export (GIF). The same
index-based API is what the device backend maps onto the native `moy_compositor`
RGB565 framebuffer.

Draw STATE (TIC-80 cluster 2, #11): every primitive respects a `camera` offset
(subtracted from all coords), a `clip` rectangle (pixels outside are dropped), a
`pal` index remap (draw-time colour swap), and `palt` per-index sprite
transparency. These four are kept byte-identical on the device backend
(`DeviceCanvas`) so a `.moy` draws the same pixels everywhere.
"""

from array import array

try:
    from . import font as _font
    from . import palette as _pal
    from .editors import SpriteSheet  # noqa: F401  (canonical home; re-exported here)
except ImportError:  # pragma: no cover - the staged device tree: bare names, no
    # package, and font.py stages as moy_font (the Wallpaper preview runner
    # imports this Canvas on-device to render the Appearance monitor's frame).
    import moy_font as _font
    import palette as _pal
    from editors import SpriteSheet  # noqa: F401

# #75: immutable templates the per-frame reset_state restores the pal tables from
# IN PLACE (no per-frame bytearray allocation; identity map / all-opaque).
_PAL_IDENTITY = bytes(range(64))
_PALT_OPAQUE = bytes(64)


def tri_spans(x1, y1, x2, y2, x3, y3):
    """The horizontal spans covering a filled triangle, packed flat as
    (x, y, w, 1, 0) quints for fill_rects (#167). Pure integer scanline walk --
    sort the vertices by y, then for each row take the long edge a->c against
    whichever short edge (a->b above the middle vertex, b->c below) is active.

    Shared shape with the device twin (device_canvas.tri_spans); the colour slot
    is left 0 because tri() passes the colour as fill_rects' `c` override."""
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


try:
    from runtime import raster_binding as _native_raster
except ImportError:                     # pragma: no cover - package-relative
    try:
        import raster_binding as _native_raster
    except ImportError:                 # a tree without it (device staging)
        _native_raster = None
if _native_raster is not None and not _native_raster.NativeRaster.available():
    _native_raster = None               # no compiler: the Python raster stays


class Canvas:
    # The game canvas always renders petme128 text at the native 8px cell (the
    # device can't scale text). The SYSTEM canvas (SystemCanvas) overrides this to
    # the user's settings-chosen scale; _blit_glyph reads it so icon glyphs follow
    # the text size. Kept on the base so every backend exposes it uniformly.
    font_scale = 1
    # PARTIAL-repaint capability (the Library shelf's drag fast path): how many
    # frames back this backend's pixels persist. The host buffer is one
    # persistent bytearray (every past frame's pixels are still there -> 1);
    # the device's ping-pong double buffer holds the frame before last (-> 2 on
    # DeviceCanvas); a RECORDING canvas (web) retains nothing and leaves the
    # attribute absent, so getattr(..., 0) keeps it on full frames.
    RETAINED_FRAMES = 1

    def __init__(self, width=480, height=270, palette=None):
        self.w = width
        self.h = height
        self.palette = palette or _pal.MOY64
        self.buf = bytearray(width * height)
        # VIEWPORT (#155): w/h are the LOGICAL surface a caller draws on (0,0
        # based); _stride/_ox/_oy say where that surface lives inside `buf`. They
        # are equal to the full buffer here, so a plain Canvas is unchanged --
        # set_viewport() is what makes a canvas a window onto a bigger one, which
        # is how a window's content draws straight into the framebuffer instead
        # of into a private buffer that then has to be copied.
        #
        # The translation rides the EXISTING camera: _cam_x is kept as the
        # EFFECTIVE offset (user camera minus the origin), so every verb's
        # `x -= self._cam_x` already lands in buffer space and the hot paths cost
        # exactly what they did before. camera() remembers the user's own value
        # separately so it can still report and restore it.
        self._stride = width
        self._ox = 0
        self._oy = 0
        self._user_cam_x = 0
        self._user_cam_y = 0
        # Pending sprite batch (Fold 1, #63): spr_tile() queues 1x1 sheet-tile blits
        # here instead of drawing them immediately, and flush_batch() emits the whole
        # run in one go (spr_batch -> one native blit_batch on device). Initialised
        # BEFORE reset_state() so its flush is a safe no-op on the very first call.
        self._batch_sheet = None
        self._batch_items = []
        self._batch_colorkey = -1
        self._batch_scale = 1
        # Auto-batch profiling counters (#63, perf_capture): per frame, how many runs
        # flush_batch emitted, total sprites drawn via the batch, and the largest single
        # run -- so a profile can PROVE N sprites coalesced into ONE blit_batch
        # (flushes=1, maxrun=N) vs were drawn one-by-one (flushes=N, maxrun=1). Reset per
        # frame by the Workstation when perf capture is on; free otherwise.
        self._batch_flushes = 0
        self._batch_sprites = 0
        self._batch_maxrun = 0
        # Auto-cache for map() (Fold 2, #63): the rasterized tilemap region is cached in a
        # hidden layer so a camera-only change spr()-composites it instead of re-rastering
        # every cell -- the make_layer/draw_layer win, made automatic for a naive
        # camera()+map() cart. _mapcache is [key, layer, lw, lh, image]; it is kept ACROSS
        # frames (NOT cleared in reset_state, or it could never hit) and rebuilt when the key
        # -- (tilemap.gen, sheet.gen, region, colorkey, scale) -- changes. Profiling counters
        # prove it: a re-raster bumps _map_raster_count, a re-use bumps _map_hits (see
        # map_cache_reset). Initialised BEFORE reset_state so it's live before the first draw.
        self._mapcache = None
        self._map_raster_count = 0
        self._map_hits = 0
        # A hidden layer (new_layer) sets this True: a layer is a draw-ONCE scratch buffer
        # (the escape hatch's make_layer, or this cache's own hidden layer), so its own map()
        # must raster DIRECTLY -- never build a nested cache (which would double the layer's
        # RAM and add a redundant composite). The main canvas keeps it False and caches.
        self._nocache = False
        # RUNG 5: libmoy's own raster, over THIS buffer.
        #
        # The verbs below were a Python transcription of the C that moy-spec
        # publishes and both boards compile, kept in agreement by the spec's
        # conformance goldens -- a pin over two implementations, which is the
        # shape the zero-duplication directive exists to end. When the binding
        # is available (a C compiler; see runtime/raster_binding.py) the pixel
        # work is that C instead, drawing straight into `self.buf`: an indexed
        # libmoy pixel IS a palette index, so there is no conversion and every
        # reader of `.buf` is untouched.
        #
        # PYTHON STAYS AUTHORITATIVE for draw state. camera/clip/pal/palt live
        # here, in the fields ~28 sites outside the verbs read (layers,
        # blit_strip, scroll_rect, fill_rects, the sprite caches), and are
        # PUSHED downstream on change. One authority with a downstream copy is
        # the device's shipped _gate_state shape; two authorities is the disease.
        self._nr = None
        self._nr_sheet = None
        self._nr_map = None
        if _native_raster is not None:
            try:
                self._nr = _native_raster.NativeRaster(
                    self._stride, len(self.buf) // self._stride if self._stride else 0,
                    buf=self.buf)
            except Exception:  # noqa: BLE001 -- no binding: the Python raster stays
                self._nr = None
        # Draw state (TIC-80 cluster 2). reset_state() initialises camera/clip/pal/palt.
        self.reset_state()

    # -- the native lane ----------------------------------------------------

    def _nr_sync(self):
        """Push this canvas's draw state into the C canvas.

        Called from every setter rather than from every verb: state changes a
        few times a frame where verbs run hundreds of times, and the fields are
        already in BUFFER space here (`_cam_x` is the effective offset, the clip
        includes the viewport origin), which is exactly the space libmoy's
        canvas works in. So this is four calls and a palette walk, not a
        translation layer."""
        nr = self._nr
        if nr is None:
            return
        nr.camera(self._cam_x, self._cam_y)
        nr.clip(self._clip_x0, self._clip_y0,
                self._clip_x1 - self._clip_x0, self._clip_y1 - self._clip_y0)
        nr.pal()
        pm = self._pal_map
        for i in range(64):
            if pm[i] != i:
                nr.pal(i, pm[i])
        nr.palt()
        pt = self._palt
        for i in range(64):
            if pt[i]:
                nr.palt(i, True)

    def _nr_assets(self, sheet=None, tilemap=None):
        """Register the sheet/tilemap a verb is about to draw from. libmoy's
        console HOLDS its assets where these verbs take them per call, so this
        is the join -- keyed by identity, because re-registering the same
        buffer every call would be the per-verb cost this lane exists to
        avoid."""
        nr = self._nr
        if sheet is not None and sheet is not self._nr_sheet:
            nr.set_sheet(sheet)
            self._nr_sheet = sheet
        if tilemap is not None and tilemap is not self._nr_map:
            nr.set_map(tilemap)
            self._nr_map = tilemap

    # -- draw state (camera / clip / pal / palt, #11) ------------------------

    def set_viewport(self, x, y, w, h):
        """Point this canvas at the (x, y, w, h) sub-rect of its own buffer:
        callers keep drawing 0,0-based in a w x h surface, the pixels land at the
        offset, and everything clips to the rect (#155).

        The point is to delete a copy. A window's content used to render into a
        private buffer that was then blitted 1:1 onto the framebuffer -- ~900KB
        of bus traffic per frame on the P4, where a full-screen copy costs 27ms
        against a 91MB/s ceiling. Through a viewport the same draw calls write
        the framebuffer once, in place."""
        # CLAMP to the buffer. A window may hang off the screen edge, so the
        # requested rect is not always fully on it -- and an unclamped row write
        # runs past the end, where a bytearray slice-assign silently GROWS the
        # buffer instead of failing (caught by the direct-render equivalence
        # test, which saw a 1024x600 canvas become 1024x603).
        rows = len(self.buf) // self._stride if self._stride else 0
        ox = max(0, int(x))
        oy = max(0, int(y))
        self._ox = ox
        self._oy = oy
        self.w = max(0, min(int(w), self._stride - ox))
        self.h = max(0, min(int(h), rows - oy))
        self.reset_state()

    def clear_viewport(self):
        """Back to owning the whole buffer."""
        self._ox = 0
        self._oy = 0
        self.w = self._stride
        self.h = len(self.buf) // self._stride if self._stride else 0
        self.reset_state()

    def reset_state(self):
        """Restore camera (0,0), clip (full screen), pal (identity), palt (all
        opaque). The console calls this before each cart frame so draw state never
        leaks between carts or between a cart and the UI."""
        # Draw any queued sprites FIRST: they were spr_tile()'d under the current
        # camera/clip/pal/palt, so they must be emitted before that state is wiped.
        self.flush_batch()
        # _cam_* is the EFFECTIVE offset (user camera - viewport origin), and the
        # clip rect lives in BUFFER coordinates; both are identities on a
        # full-surface canvas.
        self._user_cam_x = 0
        self._user_cam_y = 0
        self._cam_x = -self._ox
        self._cam_y = -self._oy
        self._clip_x0 = self._ox
        self._clip_y0 = self._oy
        self._clip_x1 = self._ox + self.w
        self._clip_y1 = self._oy + self.h
        # pal remap: index i draws as _pal_map[i]. Identity by default. palt: per-index
        # sprite transparency. TIC-80 defaults index 0 transparent, but v0.4's spr()
        # has always used an explicit colorkey (default -1 = none), so to keep existing
        # carts pixel-identical the default here is ALL OPAQUE; a cart opts in via
        # palt(c, True). #75 (mirrors device_canvas): this runs EVERY cart frame, so
        # the tables are built once and afterwards restored IN PLACE, only when a
        # pal()/palt() actually dirtied them -- no per-frame allocation.
        if getattr(self, "_pal_dirty", True):
            self._pal_dirty = False
            if getattr(self, "_pal_map", None) is None:
                self._pal_map = bytearray(_PAL_IDENTITY)
                self._palt = bytearray(64)    # 0 = opaque, 1 = transparent
            else:
                self._pal_map[:] = _PAL_IDENTITY
                self._palt[:] = _PALT_OPAQUE
            # Mirrors DeviceCanvas._palgen (0 == identity). The map() auto-cache
            # (Fold 2, #63) applies pal/palt at COMPOSITE via spr(), so it only caches
            # under an identity palette (_palgen == 0) and rasters directly otherwise --
            # keeping the cached region pal-independent and byte-identical to a direct
            # raster (correctness first).
            self._palgen = 0
            self._pal_delta = 0           # #63: back to identity content (state id 0)
            self._palt_delta = 0
            self._pal_single = -1
            self._palt_single = -1
        if getattr(self, "_nr", None) is not None:
            self._nr_sync()

    def camera(self, x=0, y=0):
        """TIC-80 camera(x, y): subtract (x, y) from all subsequent draw coords so a
        world-space cart scrolls. camera() with no args resets to (0, 0). Returns the
        previous offset (TIC-80 returns the prior camera)."""
        self.flush_batch()             # queued sprites belong to the OLD camera (#63)
        prev = (self._user_cam_x, self._user_cam_y)
        self._user_cam_x = int(x)
        self._user_cam_y = int(y)
        self._cam_x = self._user_cam_x - self._ox
        self._cam_y = self._user_cam_y - self._oy
        if self._nr is not None:
            self._nr.camera(self._cam_x, self._cam_y)
        return prev

    def clip(self, x=None, y=None, w=None, h=None):
        """TIC-80 clip(x, y, w, h): restrict drawing to a rectangle (screen space,
        i.e. AFTER the camera offset, like TIC-80). clip() with no args resets to the
        full screen. The rect is clamped to the canvas."""
        self.flush_batch()             # queued sprites belong to the OLD clip (#63)
        if x is None:
            self._clip_x0 = self._ox
            self._clip_y0 = self._oy
            self._clip_x1 = self._ox + self.w
            self._clip_y1 = self._oy + self.h
            if self._nr is not None:
                self._nr.clip(self._clip_x0, self._clip_y0, self.w, self.h)
            return
        x = int(x)
        y = int(y)
        w = int(w)
        h = int(h)
        # The caller's rect is surface-local; the stored clip is buffer-space.
        self._clip_x0 = self._ox + max(0, x)
        self._clip_y0 = self._oy + max(0, y)
        self._clip_x1 = self._ox + min(self.w, x + w)
        self._clip_y1 = self._oy + min(self.h, y + h)
        if self._nr is not None:
            self._nr.clip(self._clip_x0, self._clip_y0,
                          self._clip_x1 - self._clip_x0,
                          self._clip_y1 - self._clip_y0)

    def _pal_state_id(self):
        # The stable id of the CURRENT (pal map, palt) content (mirrors
        # DeviceCanvas._pal_state_id exactly): identity is 0, any other state gets a
        # small int the first time and the SAME int thereafter, so pal-gated caches
        # (the Fold-2 map cache here; the device's sprite bakes) hit when a cart
        # returns to a tint it used before. The common single-entry remap keys as a
        # smallint (alloc-free -- the tint sandwich runs dozens of pal calls per
        # frame); only multi-entry states build the 128-byte content key.
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
        ids = getattr(self, "_pal_state_ids", None)
        if ids is None:
            ids = self._pal_state_ids = {}
            self._pal_state_next = 1
        i = ids.get(key)
        if i is None:
            if len(ids) > 64:
                ids.clear()
            i = self._pal_state_next
            self._pal_state_next += 1
            ids[key] = i
        return i

    def pal(self, c0=None, c1=None):
        """TIC-80 pal(c0, c1): remap draw-time index c0 -> c1 (recolour idiom). pal()
        with no args resets the table to identity. Applies to every primitive AND to
        sprite pixels (so a recoloured sprite draws with swapped palette entries)."""
        self.flush_batch()             # queued sprites belong to the OLD pal map (#63)
        self._pal_dirty = True         # #75: the next reset_state must restore
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
        self._palgen = self._pal_state_id()
        if self._nr is not None:
            self._nr_sync()   # #63: content id gates the map cache

    def palt(self, c=None, on=None):
        """TIC-80 palt(c, on): mark index c transparent (on=True) or opaque for spr().
        palt() with no args resets to the default (all opaque). This is consulted in
        addition to the per-call colorkey / Image.transparent."""
        self.flush_batch()             # queued sprites belong to the OLD palt (#63)
        self._pal_dirty = True         # #75: the next reset_state must restore
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
        self._palgen = self._pal_state_id()
        if self._nr is not None:
            self._nr_sync()   # #63: content id gates the map cache

    # -- primitives ----------------------------------------------------------

    def cls(self, c=0):
        # cls ignores camera/clip (it's a full-SURFACE reset, like TIC-80) but DOES
        # honour the pal remap so a recoloured palette clears consistently.
        # #155: "surface" means the VIEWPORT, not the whole buffer -- a windowed
        # layer that clears itself must not wipe the desktop it is drawing on.
        self.flush_batch()             # #63: a non-spr primitive breaks the batch
        ci = self._pal_map[c & 63]
        # NB: cls is NOT delegated. libmoy's clears the whole canvas; here a
        # "surface" is the VIEWPORT (#155), so a windowed layer clearing itself
        # must not wipe the desktop under it. The Python fill is already one
        # slice-assign per row and the difference is not worth a second meaning
        # for the verb.
        if self._ox == 0 and self._oy == 0 and self.w == self._stride:
            self.buf[:] = bytes((ci,)) * (self.w * self.h)
            return
        row = bytes((ci,)) * self.w
        buf = self.buf
        stride = self._stride
        for yy in range(self._oy, self._oy + self.h):
            base = yy * stride + self._ox
            buf[base:base + self.w] = row

    def _put(self, x, y, ci):
        # Single clipped, camera-offset, pal-remapped pixel write. `ci` is a raw
        # 0-63 index; pal remap + clip + camera are applied here so every primitive
        # that funnels through _put inherits all four pieces of draw state.
        x = x - self._cam_x
        y = y - self._cam_y
        if not (self._clip_x0 <= x < self._clip_x1 and self._clip_y0 <= y < self._clip_y1):
            return
        self.buf[y * self._stride + x] = self._pal_map[ci & 63]

    def pix(self, x, y, c=None):
        # TIC-80 pix: read the index at (x, y) with two args, set it with three
        # (replaces the old pset/pget pair). Reads are camera-relative too.
        # Flush the pending sprite batch first so a WRITE keeps draw order and a READ
        # never samples a stale pixel under a queued-but-unblitted sprite (#63).
        self.flush_batch()
        x = int(x) - self._cam_x
        y = int(y) - self._cam_y
        stride = self._stride
        if not (0 <= x < stride and 0 <= y * stride < len(self.buf)):
            return 0
        if c is None:
            return self.buf[y * stride + x]
        if not (self._clip_x0 <= x < self._clip_x1 and self._clip_y0 <= y < self._clip_y1):
            return
        self.buf[y * stride + x] = self._pal_map[c & 63]

    def line(self, x0, y0, x1, y1, c):
        if self._nr is not None:
            self.flush_batch()
            self._nr.line(int(x0), int(y0), int(x1), int(y1), c & 63)
            return
        self.flush_batch()             # #63: a non-spr primitive breaks the batch
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
        self.flush_batch()             # #63: a non-spr primitive breaks the batch
        if self._nr is not None:
            self._nr.rect(int(x), int(y), int(w), int(h), c & 63)
            return
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
        width = self._stride
        for yy in range(y0, y1):
            base = yy * width + x0
            buf[base:base + (x1 - x0)] = row

    def fill_rects(self, arr, n=-1, ox=0, oy=0, c=-1):
        # #163 span-batch: draw n packed quads (x, y, w, h, ci -- int16, 5 slots
        # each) in one call. ox/oy shift every quad (relative span lists, e.g.
        # chrome glyph runs); c >= 0 overrides every quad's ci. The device twin
        # runs this loop in C (moy_gfx DrawCtx.fill_rects); routing through
        # self.rect keeps camera/clip/pal semantics identical on both.
        if n < 0:
            n = len(arr) // 5
        rect = self.rect
        for i in range(0, n * 5, 5):
            rect(arr[i] + ox, arr[i + 1] + oy, arr[i + 2], arr[i + 3],
                 c if c >= 0 else arr[i + 4])

    def rectb(self, x, y, w, h, c):
        if self._nr is not None:
            self.flush_batch()
            self._nr.rectb(int(x), int(y), int(w), int(h), c & 63)
            return
        # TIC-80 rectb = rectangle border/outline (the old rect).
        self.flush_batch()             # #63: a non-spr primitive breaks the batch
        x = int(x)
        y = int(y)
        w = int(w)
        h = int(h)
        self.rect(x, y, w, 1, c)
        self.rect(x, y + h - 1, w, 1, c)
        self.rect(x, y, 1, h, c)
        self.rect(x + w - 1, y, 1, h, c)

    def circ(self, cx, cy, r, c):
        if self._nr is not None:
            self.flush_batch()
            self._nr.circ(int(cx), int(cy), int(r), c & 63)
            return
        # TIC-80 circ = FILLED circle (the old circfill). Each scanline is a rect(),
        # so camera/clip/pal apply through rect().
        self.flush_batch()             # #63: a non-spr primitive breaks the batch
        cx = int(cx)
        cy = int(cy)
        r = int(r)
        # Half-width by an integer walk rather than a sqrt per row -- libmoy's
        # moy_circ, which the native kernel now runs too (#97). `span` is the
        # largest s with s*s <= r*r - dy*dy, which is exactly what truncating
        # the correctly-rounded root of an exact integer gives, so the pixels
        # do not move; it just stops being a float op per scanline.
        span = 0
        for dy in range(-r, r + 1):
            t = r * r - dy * dy
            while (span + 1) * (span + 1) <= t:
                span += 1
            while span > 0 and span * span > t:
                span -= 1
            self.rect(cx - span, cy + dy, 2 * span + 1, 1, c)

    def circb(self, cx, cy, r, c):
        if self._nr is not None:
            self.flush_batch()
            self._nr.circb(int(cx), int(cy), int(r), c & 63)
            return
        # TIC-80 circb = circle border/outline (the old circ).
        self.flush_batch()             # #63: a non-spr primitive breaks the batch
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

    def tri(self, x1, y1, x2, y2, x3, y3, c):
        if self._nr is not None:
            self.flush_batch()
            self._nr.tri(int(x1), int(y1), int(x2), int(y2), int(x3), int(y3),
                         c & 63)
            return
        # TIC-80 tri = FILLED triangle (#167). Rasterized to horizontal spans and
        # emitted as ONE fill_rects (#163) instead of a rect() per scanline -- on
        # device that is a single MP->C crossing per triangle, which is what makes
        # software 3D affordable. camera/clip/pal apply through fill_rects -> rect.
        self.flush_batch()             # #63: a non-spr primitive breaks the batch
        spans = tri_spans(x1, y1, x2, y2, x3, y3)
        if spans:
            self.fill_rects(array("h", spans), len(spans) // 5, 0, 0, int(c) & 63)

    def trib(self, x1, y1, x2, y2, x3, y3, c):
        if self._nr is not None:
            self.flush_batch()
            self._nr.trib(int(x1), int(y1), int(x2), int(y2), int(x3), int(y3),
                          c & 63)
            return
        # TIC-80 trib = triangle outline (three lines, like rectb's four fills).
        self.flush_batch()             # #63: a non-spr primitive breaks the batch
        self.line(x1, y1, x2, y2, c)
        self.line(x2, y2, x3, y3, c)
        self.line(x3, y3, x1, y1, c)

    def sspr(self, sheet, sx, sy, sw, sh, dx, dy, dw=None, dh=None,
             colorkey=-1, flip=0):
        # Stretch a sw x sh PIXEL region of the sheet (top-left sx, sy) into a
        # dw x dh destination rect at dx, dy -- ARBITRARY scale, unlike spr()'s
        # integer `scale` (#167). Nearest-neighbour. This is the textured
        # wall-slice verb for software 3D, and plain non-integer sprite scaling.
        # Per-destination-pixel by nature (every pixel is a different texel), so
        # this Python path is the host lane + the no-moy_gfx fallback; the device
        # wants a native kernel before a cart leans on it in a frame loop.
        self.flush_batch()             # #63: a non-spr primitive breaks the batch
        if self._nr is not None and sheet is not None:
            self._nr_assets(sheet=sheet)
            self._nr.sspr(int(sx), int(sy), int(sw), int(sh), int(dx), int(dy),
                          None if dw is None else int(dw),
                          None if dh is None else int(dh),
                          int(colorkey), int(flip))
            return
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
        palt = self._palt
        pget = sheet.pget
        put = self._put
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
                if p == ck or palt[p & 63]:
                    continue
                put(dx + i, ty, p)

    def tline(self, tilemap, sheet, x0, y0, x1, y1, u, v, du, dv, colorkey=-1):
        if self._nr is not None and sheet is not None and tilemap is not None:
            self.flush_batch()
            self._nr_assets(sheet=sheet, tilemap=tilemap)
            self._nr.tline(int(x0), int(y0), int(x1), int(y1),
                           int(u), int(v), int(du), int(dv), int(colorkey))
            return
        # SPEC.md 6.1 tline (#167): exactly line()'s Bresenham pixels, sampling
        # the MAP as a virtual texture in 16.16 fixed point -- the Mode 7 verb.
        # u/v/du/dv are ints (the cart multiplies its floats by 65536). Before
        # each pixel the texel (u>>16, v>>16) is sampled; afterwards u += du,
        # v += dv, for EVERY walked pixel, drawn, clipped or empty alike, so
        # nothing can desynchronise the texture walk from the screen walk.
        # Coordinates wrap modulo the map's pixel size; empty cells draw
        # nothing; camera/clip/pal/palt apply through _put exactly as line()'s
        # do. Reference: moy-spec moycore/canvas.py tline; the start and step
        # are reduced once so no per-pixel modulo survives ((a + n*b) mod T ==
        # ((a mod T) + n*(b mod T)) mod T). Device twin: DeviceCanvas.tline.
        self.flush_batch()             # #63: a non-spr primitive breaks the batch
        x0 = int(x0); y0 = int(y0); x1 = int(x1); y1 = int(y1)
        u = int(u); v = int(v); du = int(du); dv = int(dv)
        ck = int(colorkey)
        tw = tilemap.w * 8
        th = tilemap.h * 8
        if tw <= 0 or th <= 0:
            return
        tu = tw << 16
        tv = th << 16
        uu = u % tu
        vv = v % tv
        du %= tu
        dv %= tv
        cells = tilemap.cells
        mw = tilemap.w
        scols = sheet.cols
        pget = sheet.pget
        palt = self._palt
        put = self._put
        dxx = x1 - x0 if x1 > x0 else x0 - x1
        dyy = y0 - y1 if y1 > y0 else y1 - y0
        stx = 1 if x0 < x1 else -1
        sty = 1 if y0 < y1 else -1
        err = dxx + dyy
        while True:
            px = uu >> 16
            py = vv >> 16
            cell = cells[(py >> 3) * mw + (px >> 3)]
            if cell:                   # 0 = empty (id+1 storage)
                tid = cell - 1
                p = pget((tid % scols) * 8 + (px & 7),
                         (tid // scols) * 8 + (py & 7))
                if p != ck and not palt[p & 63]:
                    put(x0, y0, p)
            uu += du
            if uu >= tu:
                uu -= tu
            vv += dv
            if vv >= tv:
                vv -= tv
            if x0 == x1 and y0 == y1:
                break
            e2 = 2 * err
            if e2 >= dyy:
                err += dyy
                x0 += stx
            if e2 <= dxx:
                err += dxx
                y0 += sty

    def spr(self, img, x, y, scale=1, flip=0):
        # TIC-80 flip: 0=none, 1=horizontal, 2=vertical, 3=both. The source pixel
        # read is mirrored per `flip`; camera/clip/pal/palt all apply through _put.
        # An Image blit is NOT part of an auto-batch (#63): flush any pending sheet-tile
        # run first (so it lands underneath), then draw this one immediately.
        self.flush_batch()
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

    def spr_tile(self, sheet, tile, x, y, colorkey=-1, scale=1, flip=0):
        """Queue a 1x1 sheet-tile sprite into the pending batch instead of blitting it
        now (Fold 1, #63). A contiguous run of these coalesces into ONE spr_batch (one
        native blit_batch on device) at flush -- so a kid's naive `for e in enemies:
        spr(e.tile, ...)` loop is as fast as a hand-rolled spr_batch, automatically.

        `blit_batch` bakes colorkey + scale once per call, so a change in either breaks
        the run (flush here, start a new batch); a change in camera/clip/pal/palt breaks
        it via those methods; every non-spr primitive breaks it via its own flush_batch.
        flip is per-item (blit_batch supports it), so it does NOT break the run."""
        if self._batch_items and (
                sheet is not self._batch_sheet
                or colorkey != self._batch_colorkey
                or scale != self._batch_scale):
            self.flush_batch()
        if not self._batch_items:
            self._batch_sheet = sheet
            self._batch_colorkey = colorkey
            self._batch_scale = scale
        self._batch_items.append((int(tile), int(x), int(y), int(flip)))

    def flush_batch(self):
        """Emit the pending spr_tile() batch in queue order, then clear it. A lone item
        falls back to a direct blit (no batch overhead -- no regression for a single
        sprite between primitives); a run goes through spr_batch (one native blit_batch
        on device). The list is cleared BEFORE drawing so the re-entrant flush_batch()
        inside spr()/spr_batch() is a harmless no-op."""
        items = self._batch_items
        if not items:
            return
        sheet = self._batch_sheet
        colorkey = self._batch_colorkey
        scale = self._batch_scale
        self._batch_items = []
        self._batch_sheet = None
        n = len(items)                 # profiling: count the run (see batch_reset, #63)
        self._batch_flushes += 1
        self._batch_sprites += n
        if n > self._batch_maxrun:
            self._batch_maxrun = n
        if n == 1:
            tile, x, y, flip = items[0]
            img = sheet.tile_image(tile, colorkey)
            if img is not None:
                self.spr(img, x, y, scale, flip)
        else:
            self.spr_batch(sheet, items, colorkey, scale)

    def batch_reset(self):
        """Zero the auto-batch profiling counters (#63) at the top of a frame when perf
        capture is on. Read afterward via the Workstation's perf_batch()."""
        self._batch_flushes = 0
        self._batch_sprites = 0
        self._batch_maxrun = 0

    def spr_batch(self, sheet, items, colorkey=-1, scale=1):
        # Draw many sheet tiles in one call (#43) -- the sprite analogue of map(). The
        # device collapses this to a single moy_gfx.blit_batch C call (the draw-call
        # count is its FPS bottleneck); on the host this readable per-item loop is the
        # reference, and must match the device pixel-for-pixel. `items` is a sequence of
        # (tile, x, y) or (tile, x, y, flip) tuples; tiles resolve through `sheet` like
        # map(). camera/clip/pal/palt all apply inside self.spr().
        self.flush_batch()             # #63: emit any auto-batched run first (z-order)
        for it in items:
            tile = it[0]
            x = it[1]
            y = it[2]
            flip = it[3] if len(it) > 3 else 0
            img = sheet.tile_image(int(tile), colorkey)
            if img is None:
                continue
            self.spr(img, x, y, scale, flip)

    def _map_raster(self, tilemap, sheet, mx, my, w, h, sx, sy, colorkey, scale):
        # The direct per-tile rasterizer (the pre-Fold-2 map() body): blit each non-empty
        # cell's 8x8 sheet tile via spr() at `scale` (so scale=2 => 16px world tiles), which
        # carries camera/clip/pal/palt. Draws into THIS canvas -- map() calls it on `self` for
        # the uncached path and on a hidden layer to FILL the Fold-2 cache. Tile images are
        # cached by id so a repeated tile is built once per call, not once per cell.
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
                self.spr(img, sx + cx * step, py, scale)

    def map(self, tilemap, sheet, mx=0, my=0, w=None, h=None,
            sx=0, sy=0, colorkey=-1, scale=1):
        # TIC-80 map(): blit a w x h cell region of `tilemap` (top-left cell mx,my) over
        # `sheet` to screen (sx, sy). Fold 2 (#63): the rasterized region is CACHED in a
        # hidden layer, so a subsequent call where only the camera moved re-uses it (a cheap
        # spr() composite) instead of re-rastering every cell -- the make_layer win, made
        # automatic. The cache keys on (tilemap.gen, sheet.gen, region, colorkey, scale); an
        # mset / sheet paint edit / scale change bumps the key and the region re-rasters.
        # Camera/clip/sx/sy are COMPOSITE-time (not in the key), so a scroll is a cache HIT.
        # The device (DeviceCanvas.map -> moy_gfx.blit_map/blit565) mirrors this in RGB565.
        #
        # pal/palt apply at COMPOSITE (spr carries them), so caching is gated to an identity
        # palette (_palgen == 0): under an active pal/palt map() rasters directly, keeping the
        # cache pal-independent AND byte-identical to a direct raster (correctness over
        # cleverness -- an active palette on a scrolling map is rare).
        self.flush_batch()             # #63: map() is a non-spr primitive -> break batch
        mx = int(mx)
        my = int(my)
        scale = int(scale)
        if scale < 1:
            scale = 1
        if w is None:
            w = tilemap.w - mx
        if h is None:
            h = tilemap.h - my
        w = int(w)
        h = int(h)
        if self._nocache or self._palgen != 0:   # layer / active palette -> direct raster
            self._map_raster(tilemap, sheet, mx, my, w, h, int(sx), int(sy), colorkey, scale)
            return
        step = sheet.TILE * scale
        lw = w * step
        lh = h * step
        if lw <= 0 or lh <= 0:
            return
        key = (id(tilemap), tilemap.gen, id(sheet), getattr(sheet, "gen", 0),
               mx, my, w, h, int(colorkey), scale)
        mc = self._mapcache
        if mc is None or mc[0] != key:
            # MISS -> (re)raster the region into a hidden layer at local (0,0), transparent
            # cells left as the 255 sentinel (never a valid 0..63 index). Re-use the layer
            # buffer when the pixel dims are unchanged (only the content/key changed) so a
            # live-editing cart doesn't re-allocate every rebuild.
            if mc is not None and mc[2] == lw and mc[3] == lh:
                layer = mc[1]
                image = mc[4]
            else:
                layer = self.new_layer(lw, lh)
                image = Image(lw, lh, layer.buf, transparent=255)
            layer.buf[:] = b"\xff" * (lw * lh)
            layer._map_raster(tilemap, sheet, mx, my, w, h, 0, 0, colorkey, scale)
            self._mapcache = mc = (key, layer, lw, lh, image)
            self._map_raster_count += 1
        else:
            self._map_hits += 1
        # COMPOSITE: place the cached region (spr skips the 255 sentinel) at (sx, sy) with
        # camera + clip applied by spr(). Under the identity gate spr's pal/palt are no-ops,
        # so the raw indices land unchanged. Overdrawing the region each frame still erases
        # last frame's actors for free, exactly like a direct map().
        self.spr(mc[4], int(sx), int(sy))

    def map_cache_reset(self):
        """Zero the Fold-2 map-cache profiling counters (#63). After a run of same-key map()
        calls, _map_raster_count == 1 / _map_hits == (n-1) PROVES the region rasterized ONCE
        and every later frame re-used the cache (what pixel-parity can't see) -- the map()
        analogue of batch_reset."""
        self._map_raster_count = 0
        self._map_hits = 0

    def print(self, s, x, y, c, scale=1):
        # Render with the shared petme128 8x8 font so host text is pixel-identical
        # to the device's framebuf.text. Fixed 8px like the device -- `scale` is
        # accepted for call-compatibility but ignored (the device can't scale text).
        self.flush_batch()             # #63: print() is a non-spr primitive -> break batch
        if self._nr is not None:
            # Scale-1 only: SystemCanvas.print at font_scale > 1 draws each glyph
            # pixel as a block of rects, which libmoy's print has no notion of.
            self._nr.print(s, int(x), int(y), c & 63)
            return
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
        lay = Canvas(int(w), int(h), self.palette)
        lay._nocache = True            # #63: a layer's own map() rasters directly (no nesting)
        return lay

    def blit_window_from(self, layer, cam_x=0, cam_y=0):
        """Copy the visible self.w x self.h window of `layer` (a wider pre-rendered
        Canvas) into this canvas at offset (cam_x, cam_y) -- a flat per-row index copy,
        the host parity of the device moy_gfx.blit_window (which copies RGB565). Clamped
        to the source bounds exactly like the C kernel; draw_layer keeps cam in range so
        the full window always lands. Overwrites (no transparency): it's the background,
        drawn first each frame, and it erases last frame's sprites for free.

        NOT viewport-aware (#155), deliberately: when the window is clamped to a
        narrower source this writes rows packed at the CLAMPED width, matching
        moy_gfx.blit_window -- the destination is a contiguous dw-wide buffer,
        not a sub-rect of a wider one. It is a CART verb (draw_layer) and only
        ever runs on a full-surface canvas; a viewport canvas must not call
        it."""
        # #63: flush BOTH sides -- this canvas's queued sprites (drawn, then overwritten
        # by the opaque copy, exactly as immediate mode would) and the source layer's,
        # so its pixels are complete before we read them.
        self.flush_batch()
        _fb = getattr(layer, "flush_batch", None)
        if _fb is not None:
            _fb()
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

    def blit_indices(self, indices, iw, ih, x, y):
        """Place an iw x ih palette-INDEX bitmap (1 byte/pixel) at (x, y) -- the host parity
        of the device moy_gfx.blit_indices kernel. The host is index-space, so this copies
        indices straight into the buffer (the device converts each index -> RGB565 via its
        PAL565 table); an index past the palette is skipped. The "images are data, not draw
        calls" bake (#63 Fold 3): a paint-app image is data + one placement, not thousands of
        rect() calls. Opaque; clamped to the canvas."""
        self.flush_batch()             # #63: a non-spr primitive breaks the batch
        iw = int(iw)
        ih = int(ih)
        x = int(x)
        y = int(y)
        if iw <= 0 or ih <= 0:
            return
        buf = self.buf
        w = self.w
        h = self.h
        pn = len(self.palette)
        n = len(indices)
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
                if v >= pn:                # index past palette -> skip (match the kernel)
                    continue
                buf[drow + tx] = v

    def blit_strip(self, layer, dst_x=0, dst_y=0):
        """Copy ALL of `layer` (its full layer.w x layer.h) into this canvas with its
        top-left at (dst_x, dst_y), opaquely (no transparency / colorkey). The positioned,
        partial-height sibling of blit_window_from: where blit_window_from window-copies a
        full-screen slice of a WIDER source, blit_strip stamps a SMALLER source (e.g. a
        cached W x 18px top-bar strip) at a fixed offset. Host copies palette indices; the
        device (DeviceCanvas.blit_strip) copies RGB565 via the same moy_gfx.blit565 the
        sprite path uses (key=-1 -> fully opaque). Out-of-bounds rows/cols are clamped to
        the destination, exactly like the C kernel, so an over-tall/over-wide strip is
        safe. Ignores camera/clip/pal (it's a chrome blit over a finished frame)."""
        self.flush_batch()             # #63: emit this canvas's queued sprites first
        _fb = getattr(layer, "flush_batch", None)
        if _fb is not None:
            _fb()                      # ... and complete the source strip's pixels
        dst_x = int(dst_x)
        dst_y = int(dst_y)
        dst = self.buf
        src = layer.buf
        dw = self.w
        dh = self.h
        ox, oy, stride = self._ox, self._oy, self._stride   # #155 viewport
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
            d0 = (oy + ty) * stride + ox + tx0
            dst[d0:d0 + cw] = src[s0 + sx0:s0 + sx0 + cw]

    def blit_strip_rect(self, layer, dst_x, dst_y, rx, ry, rw, rh):
        """blit_strip restricted to the destination rect (rx, ry, rw, rh): stamp
        `layer` at (dst_x, dst_y) but only write pixels inside the rect. The #58
        dirty-union drag/resize restore primitive -- the WM re-stamps only the
        region a moving window recently occupied instead of the whole cached
        backdrop (a full-screen copy is the drag path's dominant cost on device).
        Device twin: DeviceCanvas.blit_strip_rect (moy_gfx.blit565's dest-space
        clip rect). The WM probes for this method, so a canvas without it (the
        web RecordingLayer) just keeps the full-restore path."""
        self.flush_batch()             # #63: emit this canvas's queued sprites first
        _fb = getattr(layer, "flush_batch", None)
        if _fb is not None:
            _fb()
        dst_x = int(dst_x)
        dst_y = int(dst_y)
        dst = self.buf
        src = layer.buf
        dw = self.w
        dh = self.h
        ox, oy, stride = self._ox, self._oy, self._stride   # #155 viewport
        sw = layer.w
        if sw <= 0 or dw <= 0 or dh <= 0 or rw <= 0 or rh <= 0:
            return
        # Intersect the clip rect with the destination bounds.
        cx0 = max(0, int(rx))
        cy0 = max(0, int(ry))
        cx1 = min(dw, int(rx) + int(rw))
        cy1 = min(dh, int(ry) + int(rh))
        if cx0 >= cx1 or cy0 >= cy1:
            return
        sh = len(src) // sw
        for row in range(sh):
            ty = dst_y + row
            if ty < cy0 or ty >= cy1:
                continue
            # Source span whose destination lands inside [cx0, cx1).
            sx0 = max(0, cx0 - dst_x)
            sx1 = min(sw, cx1 - dst_x)
            if sx0 >= sx1:
                continue
            s0 = row * sw
            d0 = (oy + ty) * stride + ox + (dst_x + sx0)
            dst[d0:d0 + (sx1 - sx0)] = src[s0 + sx0:s0 + sx1]

    def scroll_rect(self, rx, ry, rw, rh, dx, dy):
        """Shift the pixels inside rect (rx, ry, rw, rh) by (dx, dy) IN PLACE --
        the #113 scroll-as-blit primitive: a scrolled view keeps its already-
        correct pixels and the caller repaints only the exposed band. Pixels
        that would leave the rect are dropped; the strip shifted in from
        outside the rect keeps its stale content (the caller's band repaint
        covers it). Ignores camera/clip/pal (a system verb over a finished
        frame, like blit_strip). Row copies are overlap-safe: the vertical
        iteration order follows dy, and a bytearray slice read makes a copy,
        so horizontal overlap within a row can't smear. Backends without this
        verb simply never take the blit path (callers probe, the
        blit_strip_rect pattern)."""
        self.flush_batch()             # #63: emit queued sprites into buf first
        dx = int(dx)
        dy = int(dy)
        if dx == 0 and dy == 0:
            return
        # Clamp the rect to the canvas.
        x0 = max(0, int(rx))
        y0 = max(0, int(ry))
        x1 = min(self.w, int(rx) + int(rw))
        y1 = min(self.h, int(ry) + int(rh))
        # Destination span: the part of the rect whose source is also inside it.
        tx0 = x0 + max(0, dx)
        tx1 = x1 + min(0, dx)
        ty0 = y0 + max(0, dy)
        ty1 = y1 + min(0, dy)
        if tx0 >= tx1 or ty0 >= ty1:
            return
        buf = self.buf
        ox, oy, stride = self._ox, self._oy, self._stride   # #155 viewport
        cw = tx1 - tx0
        rows = range(ty1 - 1, ty0 - 1, -1) if dy > 0 else range(ty0, ty1)
        for ty in rows:
            s0 = (oy + ty - dy) * stride + ox + (tx0 - dx)
            d0 = (oy + ty) * stride + ox + tx0
            buf[d0:d0 + cw] = buf[s0:s0 + cw]

    # -- output --------------------------------------------------------------

    def to_rgb888(self):
        self.flush_batch()             # #63: complete any queued sprites before readout
        pal3 = [bytes(rgb) for rgb in self.palette]
        return b"".join(pal3[i] for i in self.buf)


class SystemCanvas(Canvas):
    """The SYSTEM canvas (the panel/window surface): identical to `Canvas` except
    its `print` honours a settings-chosen `font_scale` (petme128 nearest-neighbor
    x1/x2/x3). The two-domain seam (#39): the desktop/launcher/settings + status
    strip + dock + EVERY editor tab (step 3: cards/code/blocks/paint/map/music)
    draw here at native resolution and reflow with the size; the running cart
    draws on the fixed 320x240 GAME canvas (a plain `Canvas`, text always 8px)
    and is composited in as a viewport.

    At font_scale == 1 every drawn pixel is byte-identical to Canvas.print -- the
    graceful-degradation guarantee (a 320x240 system canvas at scale 1 is exactly
    today). Scaling is contained entirely here, so a cart that ever calls print on
    its game canvas is never affected."""

    def __init__(self, width=320, height=240, palette=None, font_scale=1):
        Canvas.__init__(self, width, height, palette)
        self.font_scale = max(1, int(font_scale))

    def set_font_scale(self, scale):
        self.font_scale = max(1, int(scale))

    def new_layer(self, w, h):
        """An off-screen layer that keeps THIS canvas's font scale, so chrome
        rendered through a cached strip (the #43 top bar) scales its text like
        everything else drawn on the system canvas (#39). At font_scale 1 a
        SystemCanvas layer prints byte-identically to the plain Canvas layer the
        base method returns, so the 320x240 tiers can't drift."""
        lay = SystemCanvas(int(w), int(h), self.palette, font_scale=self.font_scale)
        lay._nocache = True            # #63: match Canvas.new_layer (no nested map cache)
        return lay

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
