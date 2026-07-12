"""Paint's narrow shell-owned artwork capability.

The Paint cartridge owns the editor, pixels and interaction. This service owns
only the operations a sandboxed cartridge cannot perform: persisting the shared
drawing, publishing it through the built-in ``My Art`` wallpaper cartridge, and
copying it into a chosen project as ``images/bg.moyimg``.

The Player injects this object only into the shipped Paint app when its manifest
requests the ``artwork`` permission. Ordinary kid cartridges keep the frozen cart
API unchanged and cannot reach the store through this object.
"""

try:
    import ui as _ui
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime import ui as _ui



class _Bitmap:
    """Duck-typed indexed image accepted by host and device system canvases."""

    def __init__(self, w, h, pix):
        self.w = int(w)
        self.h = int(h)
        self.pix = pix
        self.transparent = -1
        self._paint = True


def _invalidate_bitmap(img):
    if img is None:
        return
    for name, value in (("_rgb_i", None), ("_rgb", None), ("_variants", {})):
        try:
            setattr(img, name, value)
        except Exception:  # noqa: BLE001 -- host bitmap has no device cache
            pass


class PaintDocument:
    """An indexed document; UI/window size never changes its chosen pixel size."""

    PAPER = 7

    def __init__(self, width=320, height=240, seed=False):
        self.W = int(width)
        self.H = int(height)
        self.thumb_w = (self.W + 1) // 2
        self.thumb_h = (self.H + 1) // 2
        self.pix = bytearray(self.W * self.H)
        self.pix[:] = bytes((self.PAPER,)) * len(self.pix)
        self.thumb = bytearray(self.thumb_w * self.thumb_h)
        self.image = _Bitmap(self.W, self.H, self.pix)
        self.thumb_image = _Bitmap(self.thumb_w, self.thumb_h, self.thumb)
        self.history = []
        self.future = []
        self.action_live = False
        if seed and self.W == 512 and self.H == 300:
            self.seed_desktop()
        self.rebuild_thumb()

    def load(self, data):
        if data is None or data[0] < 1 or data[1] < 1:
            return False
        if data[0] != self.W or data[1] != self.H:
            self.__init__(data[0], data[1])
        self.pix[:] = data[2]
        self.history = []
        self.future = []
        self.invalidate()
        self.rebuild_thumb()
        return True

    def invalidate(self):
        _invalidate_bitmap(self.image)
        _invalidate_bitmap(self.thumb_image)

    def rebuild_thumb(self):
        for y in range(self.thumb_h):
            si = (y * 2) * self.W
            di = y * self.thumb_w
            for x in range(self.thumb_w):
                self.thumb[di + x] = self.pix[si + x * 2]
        _invalidate_bitmap(self.thumb_image)

    def seed_desktop(self):
        """Editable starter art: a designed 512x300 retro garden wallpaper."""
        # Four calm sky bands from the extended MOY64 atmosphere range.
        bands = ((0, 70, 22), (70, 138, 21), (138, 202, 20), (202, 240, 19))
        for y0, y1, c in bands:
            row = bytes((c,)) * self.W
            for y in range(y0, y1):
                self.pix[y * self.W:(y + 1) * self.W] = row
        # Sun, distant lavender ridges, water, and green foreground.
        self.circle((423, 70), (463, 70), 18, True)
        for x in range(self.W):
            ridge = 186 - abs((x % 210) - 105) // 3
            for y in range(max(145, ridge), 220):
                self.put(x, y, 23 if x < 300 else 13)
        for y in range(205, 260):
            self.line(0, y, self.W - 1, y, 22 if y & 4 else 21)
        for y in range(255, self.H):
            self.line(0, y, self.W - 1, y, 32 if y < 278 else 3)
        # A broad cherry tree, built from the same circle/line verbs kids use.
        self.line(102, 274, 126, 128, 28, 7)
        self.line(116, 185, 67, 115, 28, 4)
        self.line(119, 176, 180, 104, 28, 4)
        self.line(105, 205, 54, 176, 33, 3)
        clusters = ((48, 96, 32), (82, 80, 38), (122, 89, 42),
                    (166, 78, 34), (202, 105, 30), (73, 130, 36),
                    (132, 132, 40), (178, 132, 34))
        shades = (25, 14, 16, 15)
        for i in range(len(clusters)):
            cx, cy, r = clusters[i]
            self.circle((cx, cy), (cx + r, cy), shades[i & 3], True)
            self.circle((cx - r // 3, cy - r // 4),
                        (cx - r // 3 + r // 2, cy - r // 4), 7 if i & 1 else 15, True)
        # Designed dither/reflections and a few loose petals keep the scene alive.
        for x in range(260, 500, 12):
            self.line(x, 232 + (x % 5), min(511, x + 7), 232 + (x % 5), 48)
        for i in range(38):
            x = (i * 73 + 41) % self.W
            y = 34 + ((i * 47) % 210)
            self.put(x, y, 14 if i % 3 else 7)
            if i % 5 == 0:
                self.put(x + 1, y, 15)

    def snapshot(self):
        if self.action_live:
            return
        self.history.append(bytes(self.pix))
        if len(self.history) > 3:
            self.history.pop(0)
        self.future = []
        self.action_live = True

    def finish(self):
        self.action_live = False

    def restore(self, data):
        self.pix[:] = data
        self.invalidate()
        self.rebuild_thumb()

    def undo(self):
        if not self.history:
            return False
        self.future.append(bytes(self.pix))
        if len(self.future) > 3:
            self.future.pop(0)
        self.restore(self.history.pop())
        return True

    def redo(self):
        if not self.future:
            return False
        self.history.append(bytes(self.pix))
        if len(self.history) > 3:
            self.history.pop(0)
        self.restore(self.future.pop())
        return True

    def clear(self):
        self.snapshot()
        self.pix[:] = bytes((self.PAPER,)) * len(self.pix)
        self.invalidate()
        self.rebuild_thumb()
        self.finish()

    def get(self, x, y):
        return self.pix[int(y) * self.W + int(x)]

    def put(self, x, y, color):
        x = int(x)
        y = int(y)
        if x < 0 or y < 0 or x >= self.W or y >= self.H:
            return
        c = int(color) & 63
        i = y * self.W + x
        if self.pix[i] == c:
            return
        self.pix[i] = c
        self.thumb[(y // 2) * self.thumb_w + x // 2] = c

    def stamp(self, x, y, color, radius=0):
        r = max(0, int(radius))
        if r == 0:
            self.put(x, y, color)
            return
        rr = r * r
        for py in range(y - r, y + r + 1):
            dy = py - y
            for px in range(x - r, x + r + 1):
                dx = px - x
                if dx * dx + dy * dy <= rr:
                    self.put(px, py, color)

    def line(self, x0, y0, x1, y1, color, radius=0):
        dx = abs(x1 - x0)
        dy = abs(y1 - y0)
        sx = 1 if x0 < x1 else -1
        sy = 1 if y0 < y1 else -1
        err = dx - dy
        while True:
            self.stamp(x0, y0, color, radius)
            if x0 == x1 and y0 == y1:
                return
            e2 = err + err
            if e2 > -dy:
                err -= dy
                x0 += sx
            if e2 < dx:
                err += dx
                y0 += sy

    def box(self, a, b, color, filled=False, radius=0):
        x0, x1 = min(a[0], b[0]), max(a[0], b[0])
        y0, y1 = min(a[1], b[1]), max(a[1], b[1])
        if filled:
            for y in range(y0, y1 + 1):
                self.line(x0, y, x1, y, color)
            return
        self.line(x0, y0, x1, y0, color, radius)
        self.line(x1, y0, x1, y1, color, radius)
        self.line(x1, y1, x0, y1, color, radius)
        self.line(x0, y1, x0, y0, color, radius)

    def circle(self, center, edge, color, filled=False, radius=0):
        cx, cy = center
        dx, dy = edge[0] - cx, edge[1] - cy
        r = int((dx * dx + dy * dy) ** 0.5)
        if r < 1:
            self.put(cx, cy, color)
            return
        if filled:
            for y in range(-r, r + 1):
                span = int((r * r - y * y) ** 0.5)
                self.line(cx - span, cy + y, cx + span, cy + y, color)
            return
        x, y, err = r, 0, 0
        while x >= y:
            for px, py in ((x, y), (y, x), (-y, x), (-x, y),
                           (-x, -y), (-y, -x), (y, -x), (x, -y)):
                self.stamp(cx + px, cy + py, color, radius)
            y += 1
            if err <= 0:
                err += 2 * y + 1
            else:
                x -= 1
                err -= 2 * x + 1

    def flood(self, x, y, color):
        old = self.get(x, y)
        color = int(color) & 63
        if old == color:
            return
        stack = [(x, y)]
        while stack:
            sx, sy = stack.pop()
            left = sx
            while left > 0 and self.get(left - 1, sy) == old:
                left -= 1
            right = sx
            while right + 1 < self.W and self.get(right + 1, sy) == old:
                right += 1
            for px in range(left, right + 1):
                self.put(px, sy, color)
            for ny in (sy - 1, sy + 1):
                if ny < 0 or ny >= self.H:
                    continue
                px = left
                while px <= right:
                    if self.get(px, ny) == old:
                        stack.append((px, ny))
                        while px <= right and self.get(px, ny) == old:
                            px += 1
                    px += 1


class PaintAppLayout:
    """Responsive system-domain Paint chrome for one window/content size."""

    def __init__(self, w, h, fs=1, windowed=False):
        self.w = int(w)
        self.h = int(h)
        self.fs = max(1, int(fs))
        fs = self.fs
        # Physical surface threshold: a 894x502 P4 window at font-scale 2 still has
        # room for the full 512x300 document plus desktop rails, so it is WIDE.
        self.compact = self.w < 700 or self.h < 420
        self.bar_h = 0 if windowed else 18 * fs
        self.top_h = 28 * fs
        self.status_h = 18 * fs
        self.left_w = (36 if self.compact else 52) * fs
        self.right_w = (62 if self.compact else 128) * fs
        vy = self.bar_h + self.top_h + 4 * fs
        vh = max(40, self.h - vy - self.status_h - 4 * fs)
        self.view = (self.left_w + 4 * fs, vy,
                     max(40, self.w - self.left_w - self.right_w - 8 * fs), vh)
        self.status_y = self.h - self.status_h

        labels = ("N", "U", "R", "SAVE", "WALL", "GAME", "SHOW", "FIT")
        widths = (22, 22, 22, 44, 44, 44, 44, 38)
        self.actions = []
        x = 4 * fs
        for i in range(len(labels)):
            bw = widths[i] * fs
            self.actions.append((x, self.bar_h + 2 * fs, bw, self.top_h - 4 * fs))
            x += bw + 2 * fs

        self.tools = []
        bw = (self.left_w - 6 * fs) // 2
        bh = 22 * fs
        for i in range(10):
            self.tools.append((3 * fs + (i & 1) * (bw + fs),
                               vy + (i // 2) * (bh + 2 * fs), bw, bh))

        self.pal_x = self.w - self.right_w + 5 * fs
        self.pal_y = vy + 2 * fs
        if self.compact:
            self.pal_cols = 4
            self.pal_rows = 4
            self.pal_cell = max(8, (self.right_w - 10 * fs) // 4)
        else:
            self.pal_cols = 8
            self.pal_rows = 8
            by_w = (self.right_w - 10 * fs) // 8
            by_h = max(8, (self.h - self.pal_y - self.status_h - 82 * fs) // 8)
            self.pal_cell = max(8, min(16 * fs, by_w, by_h))
        py = self.pal_y + self.pal_rows * self.pal_cell + 6 * fs
        if self.compact:
            self.pal_page = (self.pal_x, py, self.right_w - 10 * fs, 20 * fs)
            py += 24 * fs
        else:
            self.pal_page = (0, 0, 0, 0)       # all 64 swatches are already visible
        sw = (self.right_w - 14 * fs) // 3
        self.sizes = tuple((self.pal_x + i * (sw + 2 * fs), py, sw, 20 * fs)
                           for i in range(3))
        py += 24 * fs
        self.fill = (self.pal_x, py, self.right_w - 10 * fs, 20 * fs)
        py += 24 * fs
        self.preset = (self.pal_x, py, self.right_w - 10 * fs, 20 * fs)


class PaintAppLayer:
    """Resizable system process behind the shipped Paint cartridge identity."""

    id = "artwork"
    domain = "system"
    TOOLS = ("PENCIL", "BRUSH", "ERASER", "FILL", "PICK",
             "LINE", "BOX", "CIRCLE", "SPRAY", "PAN")
    GLYPHS = ("P", "B", "E", "F", "I", "/", "#", "O", "*", "+")

    def __init__(self, ws, names, in_rect):
        self.ws = ws
        self.names = names
        self._in = in_rect
        desktop = ws.sys_canvas.w >= 640 and ws.sys_canvas.h >= 400
        self.doc = PaintDocument(512, 300) if desktop else PaintDocument()
        self._starter_pending = desktop
        self.layout = PaintAppLayout(ws.sys_canvas.w, ws.sys_canvas.h,
                                     ws._effective_font_scale(), ws.windowed_chrome)
        self.tool = 0
        self.color = names["blue"]
        self.pal_page = 0
        self.size = 1
        self.shape_fill = False
        self.view_mode = 0             # 0 fit, 1/2/4 explicit detail zoom
        self.pan_x = 0
        self.pan_y = 0
        self.status = "READY"
        self.mode = "paint"           # paint | show | projects
        self.new_armed = False
        self.size_armed = False
        self.stroke_last = None
        self.shape_start = None
        self.shape_now = None
        self.pan_last = None
        self.display = None            # (image,x,y,scale,source-factor)
        self.project_top = 0
        self.project_names = ()
        self._rng_state = 0x5EED123

    def relayout(self, w, h, fs):
        self.layout = PaintAppLayout(w, h, fs, self.ws.windowed_chrome)
        self.display = None

    def open(self):
        loaded = self.ws.artwork.load()
        if self.doc.load(loaded):
            self.status = "DRAWING LOADED"
        elif self._starter_pending:
            # Build the editable demo lazily on the first Paint launch, not at OS boot.
            self.doc.seed_desktop()
            self.doc.invalidate()
            self.doc.rebuild_thumb()
            self.doc.history = []
            self.status = "STARTER GARDEN"
        else:
            self.status = "NEW DRAWING"
        self._starter_pending = False
        self.mode = "paint"
        self.ws._dirty = True

    def _button(self, cv, label, r, on=False):
        # One shared implementation now (ui.chip) -- pixel-identical delegate.
        _ui.chip(cv, self.ws.theme_colors, r, label, on=on, fs=self.layout.fs)

    def _display_spec(self):
        vx, vy, vw, vh = self.layout.view
        if self.view_mode == 0:
            if vw >= self.doc.W and vh >= self.doc.H:
                img, factor = self.doc.image, 1
                scale = max(1, min(vw // self.doc.W, vh // self.doc.H))
            else:
                img, factor = self.doc.thumb_image, 2
                scale = max(1, min(vw // img.w, vh // img.h))
            dw, dh = img.w * scale, img.h * scale
            return (img, vx + (vw - dw) // 2, vy + (vh - dh) // 2, scale, factor)
        scale = self.view_mode
        dw, dh = self.doc.W * scale, self.doc.H * scale
        max_pan_x = max(0, self.doc.W - vw // scale)
        max_pan_y = max(0, self.doc.H - vh // scale)
        self.pan_x = max(0, min(self.pan_x, max_pan_x))
        self.pan_y = max(0, min(self.pan_y, max_pan_y))
        x = vx + (vw - dw) // 2 if dw <= vw else vx - self.pan_x * scale
        y = vy + (vh - dh) // 2 if dh <= vh else vy - self.pan_y * scale
        return (self.doc.image, x, y, scale, 1)

    def _screen_to_art(self, x, y):
        if self.display is None:
            self.display = self._display_spec()
        img, dx, dy, scale, factor = self.display
        sx = (x - dx) // scale
        sy = (y - dy) // scale
        if sx < 0 or sy < 0 or sx >= img.w or sy >= img.h:
            return None
        return (sx * factor, sy * factor)

    def _art_to_screen(self, p):
        img, dx, dy, scale, factor = self.display
        return (dx + (p[0] // factor) * scale, dy + (p[1] // factor) * scale)

    def _pan(self, dx, dy):
        scale = self.view_mode if self.view_mode else 1
        self.pan_x += int(dx) // scale
        self.pan_y += int(dy) // scale
        self.display = None

    def _save(self):
        if self.ws.artwork.save(self.doc.pix, self.doc.W, self.doc.H):
            self.status = "SAVED"
            return True
        self.status = self.ws.artwork.last_error or "SAVE FAILED"
        return False

    def _action(self, index):
        if index == 0:
            if self.new_armed:
                self.doc.clear()
                self.new_armed = False
                self.status = "NEW DRAWING"
            else:
                self.new_armed = True
                self.status = "TAP N AGAIN"
        else:
            self.new_armed = False
            if index == 1:
                self.status = "UNDO" if self.doc.undo() else "NOTHING TO UNDO"
            elif index == 2:
                self.status = "REDO" if self.doc.redo() else "NOTHING TO REDO"
            elif index == 3:
                self._save()
            elif index == 4:
                if self._save() and self.ws.artwork.set_wallpaper():
                    self.status = "WALLPAPER SET"
            elif index == 5:
                if self._save():
                    self.project_names = self.ws.artwork.targets()
                    self.project_top = 0
                    self.mode = "projects"
            elif index == 6:
                self.mode = "show"
            elif index == 7:
                self.view_mode = {0: 1, 1: 2, 2: 4, 4: 0}[self.view_mode]
                self.status = "FIT" if self.view_mode == 0 else str(self.view_mode) + "X"
        self.display = None
        self.ws._dirty = True

    def handle_input(self, inp):
        if inp.pressed("b"):
            if self.mode != "paint":
                self.mode = "paint"
            else:
                self._action(7)
            self.ws._dirty = True
        return True

    def handle_pointer(self, px, py, click):
        ws = self.ws
        lay = self.layout
        if click and not ws.windowed_chrome and py < lay.bar_h:
            return bool(ws.bar_layer.handle_bar_tap("tool", px, py))
        if self.mode == "show":
            if click:
                self.mode = "paint"
                ws._dirty = True
            return True
        if self.mode == "projects":
            if click:
                self._project_tap(px, py)
            return True
        if click:
            for i, r in enumerate(lay.actions):
                if self._in(px, py, r):
                    self._action(i)
                    return True
            for i, r in enumerate(lay.tools):
                if self._in(px, py, r):
                    self.tool = i
                    self.status = self.TOOLS[i]
                    ws._dirty = True
                    return True
            count = 16 if lay.compact else 64
            for i in range(count):
                x = lay.pal_x + (i % lay.pal_cols) * lay.pal_cell
                y = lay.pal_y + (i // lay.pal_cols) * lay.pal_cell
                if self._in(px, py, (x, y, lay.pal_cell, lay.pal_cell)):
                    self.color = (self.pal_page * 16 + i) if lay.compact else i
                    self.status = "COLOR " + str(self.color)
                    ws._dirty = True
                    return True
            if lay.compact and self._in(px, py, lay.pal_page):
                self.pal_page = (self.pal_page + 1) & 3
                ws._dirty = True
                return True
            for i, r in enumerate(lay.sizes):
                if self._in(px, py, r):
                    self.size = (1, 2, 4)[i]
                    ws._dirty = True
                    return True
            if self._in(px, py, lay.fill):
                self.shape_fill = not self.shape_fill
                ws._dirty = True
                return True
            if self._in(px, py, lay.preset):
                if self.size_armed:
                    if self.doc.W == 320:
                        self.doc = PaintDocument(512, 300, seed=True)
                        self.status = "DESKTOP 512X300"
                    else:
                        self.doc = PaintDocument(320, 240)
                        self.status = "GAME 320X240"
                    self.size_armed = False
                    self.view_mode = 0
                    self.display = None
                else:
                    self.size_armed = True
                    self.status = "TAP SIZE AGAIN"
                ws._dirty = True
                return True
        self._paint_pointer(px, py, click, bool(ws.pointer.down))
        return True

    def _paint_pointer(self, x, y, tapped, held):
        p = self._screen_to_art(x, y)
        if self.tool == 9:
            if tapped:
                self.pan_last = (x, y)
            elif held and self.pan_last is not None:
                self._pan(self.pan_last[0] - x, self.pan_last[1] - y)
                self.pan_last = (x, y)
                self.ws._dirty = True
            elif not held:
                self.pan_last = None
            return
        if p is None:
            if not held:
                self._release()
            return
        factor = self.display[4]
        if tapped:
            if self.tool == 4:
                self.color = self.doc.get(p[0], p[1])
                self.status = "PICKED " + str(self.color)
                return
            self.doc.snapshot()
            if self.tool == 3:
                self.doc.flood(p[0], p[1], self.color)
                self.doc.invalidate()
                self.doc.finish()
                self.status = "FILLED"
            elif self.tool in (5, 6, 7):
                self.shape_start = p
                self.shape_now = p
            else:
                self.stroke_last = p
                self._stroke(p, factor)
            self.ws._dirty = True
        elif held:
            if self.tool in (5, 6, 7) and self.shape_start is not None:
                self.shape_now = p
            elif self.stroke_last is not None:
                self._stroke(p, factor)
            self.ws._dirty = True
        else:
            self._release()

    def _stroke(self, p, factor):
        c = PaintDocument.PAPER if self.tool == 2 else self.color
        radius = 0
        if self.tool == 1:
            radius = self.size * factor
        elif self.tool == 2:
            radius = (self.size + 1) * factor
        elif self.tool == 0:
            radius = max(0, self.size * factor - 1)
        if self.tool == 8:
            spread = 4 + self.size * 3
            for _i in range(5 + self.size * 3):
                self._rng_state = (self._rng_state * 1103515245 + 12345) & 0x7FFFFFFF
                ox = self._rng_state % (spread * 2 + 1) - spread
                self._rng_state = (self._rng_state * 1103515245 + 12345) & 0x7FFFFFFF
                oy = self._rng_state % (spread * 2 + 1) - spread
                if ox * ox + oy * oy <= spread * spread:
                    self.doc.put(p[0] + ox, p[1] + oy, self.color)
        else:
            a = self.stroke_last or p
            self.doc.line(a[0], a[1], p[0], p[1], c, radius)
        self.stroke_last = p
        self.doc.invalidate()

    def _release(self):
        if not self.doc.action_live:
            self.stroke_last = self.shape_start = self.shape_now = None
            return
        if self.shape_start is not None and self.shape_now is not None:
            r = max(0, self.size - 1)
            if self.tool == 5:
                self.doc.line(self.shape_start[0], self.shape_start[1],
                              self.shape_now[0], self.shape_now[1], self.color, r)
            elif self.tool == 6:
                self.doc.box(self.shape_start, self.shape_now, self.color,
                             self.shape_fill, r)
            elif self.tool == 7:
                self.doc.circle(self.shape_start, self.shape_now, self.color,
                                self.shape_fill, r)
            self.doc.invalidate()
        self.doc.finish()
        self.stroke_last = self.shape_start = self.shape_now = None
        self.status = "DRAWING CHANGED"
        self.ws._dirty = True

    def _project_tap(self, x, y):
        lay = self.layout
        if y < lay.bar_h + lay.top_h:
            self.mode = "paint"
            self.ws._dirty = True
            return
        row_h = 26 * lay.fs
        y0 = lay.bar_h + lay.top_h + 8 * lay.fs
        rows = max(1, (lay.h - y0 - 30 * lay.fs) // row_h)
        for row in range(rows):
            idx = self.project_top + row
            if idx >= len(self.project_names):
                break
            if self._in(x, y, (10 * lay.fs, y0 + row * row_h,
                               lay.w - 20 * lay.fs, row_h - 2 * lay.fs)):
                name = self.ws.artwork.attach(idx)
                self.status = "BG ADDED TO " + name if name else self.ws.artwork.last_error
                self.mode = "paint"
                self.ws._dirty = True
                return

    def draw(self, dt):
        cv = self.ws.sys_canvas
        if self.mode == "show":
            cv.cls(self.names["black"])
            scale = max(1, min(cv.w // self.doc.W, cv.h // self.doc.H))
            cv.spr(self.doc.image, (cv.w - self.doc.W * scale) // 2,
                   (cv.h - self.doc.H * scale) // 2, scale)
        elif self.mode == "projects":
            self._draw_projects(cv)
        else:
            self._draw_paint(cv)
        if not self.ws.windowed_chrome:
            self.ws.bar_layer._draw_status_strip("tool")

    def _draw_paint(self, cv):
        lay = self.layout
        th = self.ws.theme_colors
        cv.cls(th["panel"])
        cv.rect(0, lay.bar_h, lay.w, lay.top_h, 48)
        action_labels = ("N", "U", "R", "SAVE", "WALL", "GAME", "SHOW",
                         "FIT" if self.view_mode == 0 else str(self.view_mode) + "X")
        for i, r in enumerate(lay.actions):
            self._button(cv, action_labels[i], r,
                         (i == 0 and self.new_armed) or (i == 7 and self.view_mode == 0))
        for i, r in enumerate(lay.tools):
            self._button(cv, self.GLYPHS[i], r, i == self.tool)

        cv.rect(lay.view[0], lay.view[1], lay.view[2], lay.view[3], self.names["black"])
        cv.rectb(lay.view[0], lay.view[1], lay.view[2], lay.view[3], th["edge"])
        self.display = self._display_spec()
        img, x, y, scale, _factor = self.display
        cv.clip(lay.view[0] + 1, lay.view[1] + 1, lay.view[2] - 2, lay.view[3] - 2)
        cv.spr(img, x, y, scale)
        cv.clip()
        self._draw_preview(cv)
        self._draw_palette(cv)

        cv.rect(0, lay.status_y, lay.w, lay.status_h, self.names["black"])
        left = self.TOOLS[self.tool] + " C" + str(self.color) + " S" + str(self.size)
        cv.print(left, 4 * lay.fs, lay.status_y + 5 * lay.fs, self.names["white"], 1)
        maxc = max(1, (lay.w // (8 * lay.fs)) - len(left) - 3)
        msg = self.status[:maxc]
        cv.print(msg, lay.w - (len(msg) + 1) * 8 * lay.fs,
                 lay.status_y + 5 * lay.fs, self.names["yellow"], 1)

    def _draw_palette(self, cv):
        lay = self.layout
        count = 16 if lay.compact else 64
        for i in range(count):
            c = self.pal_page * 16 + i if lay.compact else i
            x = lay.pal_x + (i % lay.pal_cols) * lay.pal_cell
            y = lay.pal_y + (i // lay.pal_cols) * lay.pal_cell
            cv.rect(x, y, lay.pal_cell - 1, lay.pal_cell - 1, c)
            if c == self.color:
                cv.rectb(x - 1, y - 1, lay.pal_cell + 1, lay.pal_cell + 1,
                         self.names["yellow"])
        if lay.compact:
            self._button(cv, "PAL" + str(self.pal_page + 1), lay.pal_page)
        for i, r in enumerate(lay.sizes):
            self._button(cv, str((1, 2, 4)[i]), r, self.size == (1, 2, 4)[i])
        self._button(cv, "SOLID" if self.shape_fill else "EDGE",
                     lay.fill, self.shape_fill)
        preset = "GAME" if self.doc.W == 320 else "DESKTOP"
        self._button(cv, preset, lay.preset, self.size_armed)

    def _draw_preview(self, cv):
        if self.shape_start is None or self.shape_now is None or self.display is None:
            return
        a = self._art_to_screen(self.shape_start)
        b = self._art_to_screen(self.shape_now)
        v = self.layout.view
        cv.clip(v[0] + 1, v[1] + 1, v[2] - 2, v[3] - 2)
        if self.tool == 5:
            cv.line(a[0], a[1], b[0], b[1], self.color)
        elif self.tool == 6:
            x, y = min(a[0], b[0]), min(a[1], b[1])
            w, h = abs(a[0] - b[0]) + 1, abs(a[1] - b[1]) + 1
            (cv.rect if self.shape_fill else cv.rectb)(x, y, w, h, self.color)
        elif self.tool == 7:
            dx, dy = b[0] - a[0], b[1] - a[1]
            r = int((dx * dx + dy * dy) ** 0.5)
            (cv.circ if self.shape_fill else cv.circb)(a[0], a[1], r, self.color)
        cv.clip()

    def _draw_projects(self, cv):
        lay = self.layout
        th = self.ws.theme_colors
        cv.cls(th["panel"])
        cv.rect(0, lay.bar_h, lay.w, lay.top_h, 48)
        self._button(cv, "< PAINT", lay.actions[0], True)
        cv.print("ADD AS BG", lay.actions[0][0] + lay.actions[0][2] + 12 * lay.fs,
                 lay.bar_h + 9 * lay.fs, self.names["black"], 1)
        row_h = 26 * lay.fs
        y0 = lay.bar_h + lay.top_h + 8 * lay.fs
        rows = max(1, (lay.h - y0 - 30 * lay.fs) // row_h)
        for row in range(rows):
            idx = self.project_top + row
            if idx >= len(self.project_names):
                break
            r = (10 * lay.fs, y0 + row * row_h,
                 lay.w - 20 * lay.fs, row_h - 2 * lay.fs)
            cv.rect(r[0], r[1], r[2], r[3], th["title"] if row & 1 else th["panel"])
            cv.rectb(r[0], r[1], r[2], r[3], th["dim"])
            cv.print(self.project_names[idx], r[0] + 6 * lay.fs,
                     r[1] + 8 * lay.fs, th["title_ink"], 1)


class ArtworkService:
    MAX_W = 512
    MAX_H = 300
    WALL_TITLE = "My Art"
    PAINT_TITLE = "Paint"

    def __init__(self, ws):
        self.ws = ws
        self.last_error = ""
        self._cached = None
        self._wall_bitmap = None
        self._wall_key = None
        self._thumb_bitmap = None
        self._thumb_key = None

    def _ready(self):
        ws = self.ws
        return bool(ws.carts_store is not None and ws.carts_root is not None
                    and ws.can_manage)

    @classmethod
    def is_paint_app(cls, cart):
        """True only for the shipped Paint identity, not a renamed/copied cart."""
        if (not cart or cart.get("title") != cls.PAINT_TITLE
                or "artwork" not in (cart.get("permissions") or ())):
            return False
        path = cart.get("path")
        if not path:                 # embedded fallback cart (no writable store)
            return int(cart.get("version", 0)) >= 1
        name = str(path).replace("\\", "/").rsplit("/", 1)[-1]
        return name == "paint.moy"

    def _wall_cart(self):
        for cart in self.ws._all_carts:
            if cart.get("type") == "wallpaper" and cart.get("title") == self.WALL_TITLE:
                return cart
        return None

    def available(self):
        return self._ready()

    def load(self):
        """Return ``(w, h, index_bytes)`` for the shared drawing, or ``None``."""
        if not self._ready():
            return None
        ws = self.ws
        try:
            blob = ws._with_sd(
                lambda: ws.carts_store.load_artwork(ws.carts_root))
            self._cached = ws.carts_store.decode_moyimg(blob) if blob else None
            if (self._cached is not None and (self._cached[0] > self.MAX_W
                                              or self._cached[1] > self.MAX_H)):
                self._cached = None
            return self._cached
        except Exception as exc:  # noqa: BLE001 -- a bad/missing drawing is non-fatal
            self.last_error = str(exc)
            return None

    def save(self, indices, width=320, height=240):
        """Persist Paint's canvas and refresh ``My Art``'s ``bg`` asset."""
        if not self._ready():
            self.last_error = "STORAGE OFF"
            return False
        w = int(width)
        h = int(height)
        if w <= 0 or h <= 0 or w > self.MAX_W or h > self.MAX_H:
            self.last_error = "BAD SIZE"
            return False
        ws = self.ws
        store = ws.carts_store
        try:
            blob = store.encode_moyimg(w, h, indices)
            wall = self._wall_cart()

            def _write():
                store.save_artwork(blob, ws.carts_root)
                if wall is not None and wall.get("path"):
                    store.save_image(wall, "bg", blob)

            ws._with_sd(_write)
            self._cached = (w, h, bytes(indices))
            self._wall_bitmap = None
            self._wall_key = None
            self._thumb_bitmap = None
            self._thumb_key = None
            self.last_error = ""
            if ws.wallpaper_id == self._wallpaper_id():
                ws.select_wallpaper(ws.wallpaper_id, persist=False)
            if getattr(ws, "ach", None) is not None:
                ws.ach.note("paint_save")
            return True
        except Exception as exc:  # noqa: BLE001 -- surface failure in the app
            self.last_error = str(exc)
            return False

    def sync_wallpaper(self):
        """Restore ``My Art/bg`` from the re-seed-proof shared artwork file."""
        if not self._ready():
            return False
        ws = self.ws
        store = ws.carts_store
        wall = self._wall_cart()
        if wall is None or not wall.get("path"):
            return False
        try:
            def _sync():
                blob = store.load_artwork(ws.carts_root)
                if not blob:
                    return False
                self._cached = store.decode_moyimg(blob)
                current = store.load_images(wall["path"]).get("bg")
                if current != blob:
                    store.save_image(wall, "bg", blob)
                return True
            return bool(ws._with_sd(_sync))
        except Exception as exc:  # noqa: BLE001
            self.last_error = str(exc)
            return False

    def _wallpaper_id(self):
        wall = self._wall_cart()
        return self.ws._wp_id_for(wall) if wall is not None else None

    def set_wallpaper(self):
        """Publish the saved drawing as the active desktop wallpaper."""
        if not self.sync_wallpaper():
            self.last_error = self.last_error or "SAVE FIRST"
            return False
        wp_id = self._wallpaper_id()
        if wp_id is None:
            self.last_error = "NO WALLPAPER"
            return False
        self.ws.select_wallpaper(wp_id, persist=True)
        self.last_error = ""
        return True

    def owns_wallpaper(self, wp_id):
        return wp_id is not None and wp_id == self._wallpaper_id()

    @staticmethod
    def _cover_indices(src, sw, sh, dw, dh):
        """Nearest-neighbor cover crop, used only for a smaller target."""
        # Crop source to the target aspect, centered, then sample to exact size.
        if sw * dh > sh * dw:
            crop_h = sh
            crop_w = sh * dw // dh
            sx0, sy0 = (sw - crop_w) // 2, 0
        else:
            crop_w = sw
            crop_h = sw * dh // dw
            sx0, sy0 = 0, (sh - crop_h) // 2
        out = bytearray(dw * dh)
        for y in range(dh):
            sy = sy0 + y * crop_h // dh
            so = sy * sw
            do = y * dw
            for x in range(dw):
                out[do + x] = src[so + sx0 + x * crop_w // dw]
        return out

    def draw_wallpaper(self, canvas):
        """Draw My Art directly in the SYSTEM domain (512x300 -> P4 exact 2x)."""
        data = self._cached or self.load()
        if data is None:
            return False
        sw, sh, src = data
        cw, ch = canvas.w, canvas.h
        if sw > cw or sh > ch:
            key = (id(src), cw, ch)
            if self._wall_key != key:
                fitted = self._cover_indices(src, sw, sh, cw, ch)
                self._wall_bitmap = _Bitmap(cw, ch, fitted)
                self._wall_key = key
            canvas.spr(self._wall_bitmap, 0, 0)
            return True
        # Integer cover is exact for the desktop preset: 512x300 * 2 = 1024x600.
        scale = max(1, (cw + sw - 1) // sw, (ch + sh - 1) // sh)
        key = (id(src), sw, sh)
        if self._wall_key != key:
            self._wall_bitmap = _Bitmap(sw, sh, src)
            self._wall_key = key
        canvas.spr(self._wall_bitmap, (cw - sw * scale) // 2,
                   (ch - sh * scale) // 2, scale)
        return True

    def thumbnail(self, width, height):
        """A current My Art cover thumbnail for the visual Appearance picker."""
        data = self._cached or self.load()
        w = max(1, int(width))
        h = max(1, int(height))
        if data is None:
            return None
        key = ("thumb", id(data[2]), w, h)
        if self._thumb_key != key:
            pix = self._cover_indices(data[2], data[0], data[1], w, h)
            self._thumb_bitmap = _Bitmap(w, h, pix)
            self._thumb_key = key
        return self._thumb_bitmap

    def _targets(self):
        out = []
        for cart in self.ws._all_carts:
            if (cart.get("type") in ("game", "app")
                    and cart.get("title") != self.PAINT_TITLE
                    and cart.get("path")):
                out.append(cart)
        return out

    def targets(self):
        """Project titles Paint can offer in its GAME background picker."""
        return tuple(cart.get("title", "PROJECT") for cart in self._targets())

    def attach(self, index):
        """Copy the shared drawing into one project as ``images/bg.moyimg``."""
        if not self._ready():
            self.last_error = "STORAGE OFF"
            return None
        targets = self._targets()
        try:
            target = targets[int(index)]
        except (IndexError, TypeError, ValueError):
            self.last_error = "NO PROJECT"
            return None
        ws = self.ws
        store = ws.carts_store
        try:
            def _attach():
                blob = store.load_artwork(ws.carts_root)
                if not blob:
                    return False
                data = store.decode_moyimg(blob)
                if data is None:
                    return False
                if data[0] != 320 or data[1] != 240:
                    game = self._cover_indices(data[2], data[0], data[1], 320, 240)
                    blob = store.encode_moyimg(320, 240, game)
                store.save_image(target, "bg", blob)
                return True
            if not ws._with_sd(_attach):
                self.last_error = "SAVE FIRST"
                return None
            self.last_error = ""
            return target.get("title", "PROJECT")
        except Exception as exc:  # noqa: BLE001
            self.last_error = str(exc)
            return None
