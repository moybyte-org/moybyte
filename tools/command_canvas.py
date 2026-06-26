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

    def set_font_scale(self, scale):
        self.font_scale = max(1, int(scale))

    # -- frame handoff -------------------------------------------------------

    def take_commands(self):
        """Return this frame's command list and start a fresh one."""
        cmds = self._cmds
        self._cmds = []
        return cmds

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

    # -- output (parity with Canvas; not used by the web path) ---------------

    def to_rgb888(self):
        """A command canvas has no buffer; rasterize via replay_to_canvas to get
        pixels (used only by the cross-check). Returns empty bytes otherwise so the
        attribute exists for API parity with Canvas.to_rgb888."""
        return b""


def replay_to_canvas(commands, canvas):
    """Reference replayer: execute a CommandCanvas command list onto a real
    runtime.canvas.Canvas (the rasterizing host backend). This is the Python twin
    of the browser's JS replayer -- it proves the command stream captures everything
    the renderer needs, by reproducing the host frame pixel-for-pixel.

    `canvas` is a runtime.canvas.Canvas of the same size. Sprite commands rebuild a
    minimal Image-like blittable from the raw pixels and feed it through Canvas.spr,
    so the rasterization path (transparency, scaling, clipping) is the very same code
    the original frame used."""
    from runtime.canvas import Image

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
