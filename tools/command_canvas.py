"""CommandCanvas -- a recording canvas backend for the web console (#22, cutoff #2).

The v0.4 console (runtime/console.py `Workstation`) draws every frame through an
*injected* canvas object: it calls `self.canvas.cls/pix/line/rect/rectb/circ/circb/
spr/map/print` and never inspects the result. The host backend (runtime/canvas.py
`Canvas`) rasterizes those into a 320x240 buffer of palette indices; the device
backend (`DeviceCanvas`) maps them onto the native RGB565 compositor.

This module adds a THIRD backend: instead of rasterizing, it **records each draw
call as a compact, JSON-serializable command** into a per-frame list. The web
server ships that list to the browser; a JS replayer redraws the commands on an
HTML <canvas> (crisp, scalable). The console/device stays authoritative and the
wire is a few calls per frame instead of 76,800 pixels.

CommandCanvas exposes the SAME public surface as runtime.canvas.Canvas
(`w`, `h`, `cls`, `pix`, `line`, `rect`, `rectb`, `circ`, `circb`, `spr`, `map`,
`print`, `to_rgb888`) so it can be dropped in as `ws.canvas` with no change to the
shared console. The recorded commands are deliberately *literal* -- they mirror the
Canvas method calls 1:1 -- so a tiny reference replayer (replay_to_canvas below,
and the JS one in web_console.html) can reproduce pixel-identical output.

Command formats (each a short list; the first element is the op name):
  ["cls",  c]                        # clear to palette index c
  ["pix",  x, y, c]                  # set one pixel
  ["line", x0, y0, x1, y1, c]
  ["rect", x, y, w, h, c]            # FILLED (TIC-80 rect)
  ["rectb",x, y, w, h, c]            # outline (TIC-80 rectb)
  ["circ", cx, cy, r, c]            # FILLED
  ["circb",cx, cy, r, c]           # outline
  ["spr",  x, y, scale, w, h, t, pix]   # raw indexed sprite blit (see below)
  ["print",s, x, y, c]              # petme128 text (scale ignored, like Canvas)
  ["deflayer", id, w, h, cmds]      # define an off-screen layer (its recorded stream)
  ["blit_layer", id, cam_x, cam_y]          # window-copy the W x H screen slice
  ["blit_layer", id, dst_x, dst_y, "full"]  # full-copy the whole layer at (dst_x,dst_y)

`spr` always carries the sprite's raw pixels. At the `canvas.spr` boundary the
console/carts have ALREADY resolved a tile id to an Image/_SheetSprite (make_api
does sheet.tile_image(n) before calling canvas.spr), so the canvas only ever sees
a blittable with .w/.h/.pix/.transparent. Emitting the raw pixels (8x8..32x32 =
tens of ints) makes the stream self-contained and guarantees the replay matches
the rasterizer exactly -- no sheet lookup needed on the browser to be correct.
`t` is the transparent index (or a value < 0 meaning "no transparent index").

`map` is expanded into a sequence of `spr` commands (one per non-empty cell) by
reusing this same recorder, exactly mirroring Canvas.map's per-tile spr() calls --
so the replayer needs no separate map op and stays pixel-identical to the host.

OFF-SCREEN LAYERS (#54 scroll + #43 cached top bar) -- ONE recorded-layer mechanism:
a layer (make_layer / the cached top-bar strip) is an off-screen surface the cart/
console pre-renders into ONCE, then window-copies (draw_layer -> blit_window_from) or
full-copies (blit_strip) to the screen each frame. Rather than ship the layer's
finished PIXELS every frame (a wide scroll layer is ~150KB -- unplayable over WiFi),
new_layer() returns a RecordingLayer that FORWARDS draws to a real rasterizing host
Canvas (so the host stays pixel-identical) AND RECORDS the indexed draw commands into
a per-layer command stream with a stable `id` + a `gen` bumped on each redraw. The
stream ships ONCE per browser session as ["deflayer", id, w, h, cmds] (re-shipped if
`gen` changed); each frame then references it with a tiny op:
  ["blit_layer", id, cam_x, cam_y]          windowed blit (draw_layer / scroll)
  ["blit_layer", id, dst_x, dst_y, "full"]  full blit (blit_strip / cached top bar)
The browser REPLAYS the deflayer command stream into an off-screen index buffer once
(reusing the SAME replayer), then copies the window/full into the main buffer -- so
scroll AND bar share ONE mechanism, index-based, pixel-identical on host + device.
This REPLACES the old bespoke ["blit_strip", x, y, w, h, pix] op (which shipped the
finished layer pixels every frame and which web_console.html never even replayed).

The browser still receives the palette + petme128 font + the open cart's sheet via
GET /assets: the palette resolves indices to RGB, the font renders `print`, and the
sheet is sent so a future id-based `spr` (a bandwidth optimization) can resolve --
but correctness here never depends on the sheet, only the per-command pixels.
"""

from runtime import font as _font
from runtime import palette as _pal


class CommandCanvas:
    """A drop-in canvas that records draw calls instead of rasterizing them.

    Same public API as runtime.canvas.Canvas. `take_commands()` returns the frame's
    recorded command list and clears it for the next frame. `pix()` with two args
    (a read) returns 0 -- a recording canvas has no buffer to read back; the shared
    console never reads pixels during a draw, only writes them, so this is safe
    (and matches how a pure command stream must behave)."""

    def __init__(self, width=320, height=240, palette=None, font_scale=1):
        self.w = width
        self.h = height
        self.palette = palette or _pal.KID64
        # System-UI font scale (#39): when this recorder stands in for the SYSTEM
        # canvas, the console calls set_font_scale on it and reads font_scale (in
        # _blit_glyph). Scaled text is recorded as rect commands (the replayer already
        # handles rect), so the browser renders the bigger font with no new op.
        self.font_scale = max(1, int(font_scale))
        self._cmds = []
        # OFF-SCREEN LAYERS (#54 scroll + #43 cached top bar). Each RecordingLayer this
        # canvas mints gets a dense, stable id; the recorder remembers them so a
        # blit_layer can prepend the layer's ["deflayer", ...] stream the first time the
        # browser would see it (and re-ship if the layer's gen changed). _served_layers
        # maps id -> the gen last shipped; reset_served_layers() (a page reload / cart
        # change clears the browser's layer cache) drops it so every layer re-ships.
        self._layers = []            # RecordingLayer objects, indexed by their id
        self._served_layers = {}     # layer id -> gen last emitted as a deflayer

    def set_font_scale(self, scale):
        self.font_scale = max(1, int(scale))

    # -- frame handoff -------------------------------------------------------

    def take_commands(self):
        """Return this frame's command list and start a fresh one.

        SHIP-ONCE LAYERS (#54/#43): for every ["blit_layer", id, ...] this frame
        references, PREPEND the layer's ["deflayer", id, w, h, cmds] stream the first
        time the browser would see this id (or after its `gen` changed -- a redraw),
        then mark it served. So a wide scroll layer's command stream travels ONCE per
        browser session (a few KB), then a tiny blit_layer per frame -- mirroring the
        sprite atlas's serve-time defspr. reset_served_layers() (a page reload / cart
        change) makes the next take_commands re-ship every referenced layer."""
        cmds = self._cmds
        self._cmds = []
        prefix = None
        for c in cmds:
            if c and c[0] == "blit_layer":
                lid = c[1]
                layer = self._layers[lid] if 0 <= lid < len(self._layers) else None
                if layer is None:
                    continue
                gen = layer.gen
                if self._served_layers.get(lid) != gen:
                    self._served_layers[lid] = gen
                    if prefix is None:
                        prefix = []
                    prefix.append(layer.deflayer_cmd())
        if prefix is not None:
            return prefix + cmds
        return cmds

    def reset_served_layers(self):
        """Forget which layer streams the browser has -- so the next take_commands
        re-ships every referenced layer's ["deflayer", ...]. Called when /assets is
        (re)served (a page load / cart change clears the browser's layer cache), the
        layer twin of the sprite atlas's reset on a cart change."""
        self._served_layers = {}

    # -- draw state (camera / clip / pal / palt, #11) ------------------------
    # Recorded as literal commands so the replayer applies the SAME draw state, in
    # order; the stream stays self-contained and replays pixel-identically.

    def reset_state(self):
        self._cmds.append(["reset_state"])

    def camera(self, x=0, y=0):
        self._cmds.append(["camera", int(x), int(y)])
        return (0, 0)

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

    # -- primitives (each just records; see module docstring for formats) ----

    def cls(self, c=0):
        self._cmds.append(["cls", c & 63])

    def pix(self, x, y, c=None):
        if c is None:
            # A read. A command stream has no framebuffer to read back; the console
            # never reads during draw (only the rasterizing Canvas does, internally).
            return 0
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
        # `img` is an Image / _SheetSprite (.w/.h/.pix/.transparent) -- the id has
        # already been resolved to pixels by the time the canvas sees it. Emit the
        # raw pixels so the stream is self-contained and replays identically. `flip`
        # (0=none, 1=h, 2=v, 3=both, #11) is carried so the replay mirrors too.
        t = img.transparent
        if t is None:
            t = -1
        # Coerce pix to a plain list of ints (it may be a bytearray or a list with
        # -1 transparent markers); JSON handles ints, and the replayer reads them.
        pix = list(img.pix)
        self._cmds.append(["spr", int(x), int(y), int(scale),
                           int(img.w), int(img.h), int(t), pix, int(flip)])

    def spr_batch(self, sheet, items, colorkey=-1, scale=1):
        # Mirror Canvas.spr_batch (#43): emit one spr command per item, resolving each
        # tile through the sheet like map() does. Reusing the spr() op keeps the replay
        # pixel-identical with no dedicated batch op; tile Images are cached per id
        # within the batch so a repeated tile is built once.
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
            self.spr(img, it[1], it[2], scale, flip)

    def map(self, tilemap, sheet, mx=0, my=0, w=None, h=None,
            sx=0, sy=0, colorkey=-1, scale=1):
        # Mirror Canvas.map EXACTLY: walk the w x h cell region and emit one spr
        # command per non-empty cell, at the same screen position and scale. Reusing
        # spr() keeps the replay pixel-identical to the host rasterizer with no
        # dedicated map op. Tile Images are cached per id within the draw, like
        # Canvas.map, so a repeated tile is built once.
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
        # The per-call `scale` arg is ignored (like Canvas.print). At font_scale 1 we
        # record a `print` op the replayer renders with the petme128 font from
        # GET /assets, pixel-identical to font.draw(). At font_scale > 1 (the
        # resizable system font, #39) we record the scaled glyph as rect blocks --
        # exactly what SystemCanvas.print rasterizes -- so the browser shows the bigger
        # font without a new op (the replayer already handles rect).
        fs = self.font_scale
        if fs <= 1:
            self._cmds.append(["print", str(s), int(x), int(y), c & 63])
            return
        ci = c & 63
        cmds = self._cmds

        def block(bx, by, n):
            cmds.append(["rect", bx, by, n, n, ci])

        _font.draw_scaled(block, s, x, y, fs)

    # -- offscreen layers (#54 scroll engine + #43 cached top bar) -----------
    # ONE recorded-layer mechanism for BOTH the scroll layer (make_layer/draw_layer)
    # AND the cached top bar (blit_strip). new_layer() returns a RecordingLayer: a real
    # rasterizing host Canvas (so the host stays pixel-identical) wrapped so every draw
    # call into it is ALSO recorded as an indexed command (its per-layer stream + a
    # `gen` bumped on each redraw). The copy emits a tiny REFERENCE op (blit_layer); the
    # layer's command stream is shipped ONCE as a deflayer at serve time (take_commands).
    # This is index-based (no finished pixels on the wire) so it works identically on
    # host + device and over the ~72KB/s WiFi.

    def new_layer(self, w, h):
        from runtime.canvas import Canvas
        lid = len(self._layers)
        layer = RecordingLayer(Canvas(int(w), int(h), self.palette), lid)
        self._layers.append(layer)
        return layer

    def blit_window_from(self, layer, cam_x=0, cam_y=0):
        # draw_layer's window-copy: reference the layer's W x H screen window at the
        # camera offset (clamped >= 0 exactly like Canvas.blit_window_from). The browser
        # replays the layer's stream into an off-screen buffer once (deflayer) then
        # copies this window from it -- opaque + full-screen, so it also clears last
        # frame (fixing the scroll cart's trails).
        layer._end_batch()                 # the redraw (if any) is done -> next draw rebumps gen
        self._cmds.append(["blit_layer", int(layer.id),
                           max(0, int(cam_x)), max(0, int(cam_y))])

    def blit_strip(self, layer, dst_x=0, dst_y=0):
        # The cached top bar: reference the WHOLE layer at (dst_x, dst_y) via the SAME
        # blit_layer op with a trailing "full" marker, so the bar + the scroll layer
        # share one mechanism. No layer pixels travel; the deflayer ships the stream once.
        layer._end_batch()
        self._cmds.append(["blit_layer", int(layer.id),
                           int(dst_x), int(dst_y), "full"])

    # -- output (parity with Canvas; not used by the web path) ---------------

    def to_rgb888(self):
        """A command canvas has no buffer; rasterize via replay_to_canvas to get
        pixels (used only by the cross-check). Returns empty bytes otherwise so the
        attribute exists for API parity with Canvas.to_rgb888."""
        return b""


class RecordingLayer:
    """An off-screen layer that is BOTH a real rasterizing host Canvas (so the host
    panel + the pixel cross-check stay byte-identical) AND a recorder of its own
    indexed draw-command stream (so the browser can replay it into an off-screen index
    buffer instead of receiving finished pixels every frame). The ONE mechanism behind
    both the #54 scroll layer (make_layer/draw_layer) and the #43 cached top bar
    (blit_strip).

    Every draw verb forwards to the real Canvas AND to an inner CommandCanvas recorder.
    A stable `id` (assigned by the parent CommandCanvas) keys the deflayer; `gen` bumps
    once at the start of each REDRAW batch -- detected as the first draw after the layer
    was last referenced by a blit (_end_batch). So a layer that's pre-rendered once and
    then blitted every frame ships its deflayer ONCE; a periodically-rebuilt layer (the
    top bar, repainted on a clock tick / theme change) re-ships only on the rebuild.

    RECORDED VERBS: primitives + print + spr (the recorder's spr already ships raw pixels,
    so a layer's sprites are self-contained). map()/spr_batch() INTO a layer are not in
    _VERBS -- they fall through __getattr__ to the real Canvas (so the host panel is still
    correct) but are NOT recorded into the layer stream; no shipped cart draws a tilemap
    into a layer (Sky Run uses primitives only), so this is a documented follow-up."""

    # The Canvas draw surface make_api's _Layer + the console's bar renderer call.
    _VERBS = ("cls", "pix", "line", "rect", "rectb", "circ", "circb",
              "spr", "spr_batch", "map", "print",
              "camera", "clip", "pal", "palt", "reset_state")

    def __init__(self, canvas, layer_id):
        self._canvas = canvas              # the real rasterizing host Canvas
        self._rec = CommandCanvas(canvas.w, canvas.h, palette=canvas.palette)
        self.id = layer_id
        self.w = canvas.w
        self.h = canvas.h
        self.gen = 0                       # bumped at each redraw batch start
        self._in_batch = False             # currently accumulating a redraw?
        for name in RecordingLayer._VERBS:
            self._bind(name)

    def _bind(self, name):
        real = getattr(self._canvas, name)
        rec = getattr(self._rec, name)

        def fn(*args, **kwargs):
            self._begin_batch()
            r = real(*args, **kwargs)
            rec(*args, **kwargs)
            return r

        setattr(self, name, fn)

    def _begin_batch(self):
        # The first draw after the layer was last blitted starts a fresh redraw: bump
        # gen (so the server re-ships the deflayer) and clear the recorded stream.
        if not self._in_batch:
            self._in_batch = True
            self.gen += 1
            self._rec.take_commands()      # drop the previous redraw's stream

    def _end_batch(self):
        # The main canvas referenced this layer (a blit): the redraw (if any) is done,
        # so the next draw starts a new batch. Called by CommandCanvas.blit_*.
        self._in_batch = False

    def __getattr__(self, name):
        # Attribute READS not set on the layer (font_scale, palette, to_rgb888, ...) go
        # to the real Canvas -- so the console's bar renderer (_blit_glyph reads
        # cv.font_scale) + any sizing reads see the real surface. Never reached for the
        # bound draw verbs (those are instance attributes set in __init__).
        return getattr(self._canvas, name)

    @property
    def buf(self):
        return self._canvas.buf

    def deflayer_cmd(self):
        """The ["deflayer", id, w, h, cmds] that ships this layer's recorded stream to
        the browser. The cmds are the layer's CURRENT redraw stream (peeked, not taken,
        so a re-emit on a gen bump re-reads the fresh stream)."""
        return ["deflayer", int(self.id), int(self.w), int(self.h),
                list(self._rec._cmds)]


def replay_to_canvas(commands, canvas, layers=None):
    """Reference replayer: execute a CommandCanvas command list onto a real
    runtime.canvas.Canvas (the rasterizing host backend). This is the Python twin
    of the browser's JS replayer -- it proves the command stream captures everything
    the renderer needs, by reproducing the host frame pixel-for-pixel.

    `canvas` is a runtime.canvas.Canvas of the same size. Sprite commands rebuild a
    minimal Image-like blittable from the raw pixels and feed it through Canvas.spr,
    so the rasterization path (transparency, scaling, clipping) is the very same code
    the original frame used.

    `layers` is the off-screen layer cache (id -> rasterized Canvas), the twin of the
    browser's per-id off-screen canvases. A deflayer (re)builds the entry by REPLAYING
    its command stream into a fresh off-screen Canvas (recursing through this very
    function); a blit_layer copies a window (or the full layer) from it. Pass a dict
    persisted across frames so a deflayer shipped ONCE keeps serving later frames'
    blit_layers (ship-once); omit it for a single self-contained frame."""
    from runtime.canvas import Image, Canvas

    if layers is None:
        layers = {}

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
        elif op == "spr":
            _x, _y, scale, w, h, t, pix = cmd[1:8]
            flip = cmd[8] if len(cmd) > 8 else 0    # #11: optional flip field
            img = Image(w, h, pix, transparent=t)
            canvas.spr(img, _x, _y, scale, flip)
        elif op == "print":
            canvas.print(cmd[1], cmd[2], cmd[3], cmd[4])
        elif op == "deflayer":
            # Define / redraw an off-screen layer (#54 scroll + #43 cached top bar):
            # rebuild its index Canvas by REPLAYING its recorded stream into it (the
            # same draw verbs the source layer recorded), so it's pixel-identical to the
            # device/host layer pre-render. Cached by id for later blit_layer ops.
            lid, lw, lh, lcmds = cmd[1:5]
            layer = Canvas(int(lw), int(lh), canvas.palette)
            replay_to_canvas(lcmds, layer, layers)
            layers[lid] = layer
        elif op == "blit_layer":
            # Reference an off-screen layer: a windowed blit (draw_layer / scroll, 4
            # fields) or a full blit (blit_strip / cached top bar, a trailing "full").
            # Runs the SAME Canvas.blit_window_from / blit_strip the source frame used,
            # so the copy rasterizes identically -- and opaque + full-screen, so the
            # windowed blit also clears last frame (fixing the scroll cart's trails).
            lid = cmd[1]
            layer = layers.get(lid)
            if layer is not None:
                if len(cmd) > 4 and cmd[4] == "full":
                    canvas.blit_strip(layer, cmd[2], cmd[3])
                else:
                    canvas.blit_window_from(layer, cmd[2], cmd[3])
        # -- draw state (#11): apply in order so the replay tracks clip/camera/pal --
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
