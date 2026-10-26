# Map (grep -n a name to jump there):
#   _zoom_word                    a zoom as the bar and the seg say it
#   PaintAppLayout                   the rects: a handheld's with keys, one under touch, the desk's
#   Viewer                        a desk's drawing on a handheld: read by rows, never drawn on
#   PaintAppLayer                 the Paint app: the canvas, its tools and its pages
#   PaintAppLayer.handle_input    the keys: the tools, the cursor, the verbs
#   PaintAppLayer.handle_pointer  a tap or a drag: on the drawing, else the verbs'
#   PaintAppLayer.draw            the frame: the page in hand
#   PaintAppLayer._canvas         the drawing in its view, the cursor and the rail
#   PaintAppLayer._stroke_frame   a frame only a mark changed: the view's changed part alone
#   PaintAppLayer.retained        the WM's word on a window draw's target (and `changed`)
#   PaintAppLayer._more           More: the drawing's verbs
"""Paint: drawing with the public UI API's verbs (docs/ui_api_2026-10.md; the
look's docs/media/look_2026-10/APPS_RESTYLE.md, Paint).

The canvas, in the order a kid meets it:

  THE CANVAS   eight tools as toggles, the one in use current -- Pencil,
               Brush, Eraser, Fill, Shape (a line, a box or an oval, cycling
               on a repeat tap), Spray, Pick (a dropper whose drip is the
               colour under the cursor) and Move -- the drawing in its view,
               and the colours: on a handheld a rail of the colour in use,
               the page of sixteen that holds it, the brush (its dot the
               brush's own width) and More; on the desk all 64 and the
               sizes beside the drawing. The arrows move the ring cursor,
               Enter or `a` draws; Space held moves the drawing. On the
               handheld the bar carries undo, redo and the zoom (Z: 1x, 2x,
               4x, Fit); on the desk the window's tool row does, with the
               drawing's verbs.
  MORE         New drawing (N, the board's size: 1024 x 600 on a desk,
               320 x 240 on a handheld; the desk adds a game-size one), Open...
               (O: My Art), Use as wallpaper (W), Put in a game... (G, a
               320 x 240 drawing only), Put in a story... (T) and Show (V).
  A DESK'S DRAWING ON A HANDHELD  opens to look at (the bar's title, the
               zoom, More): Fit is a quarter, Z steps through a half, 1x and
               2x, the arrows pan and a sixteenth of the whole shows where
               the view is. Drawing on it answers with a toast.

Autosave (#108): a drawing persists on an idle debounce, a kid never saves;
the first save of a new drawing says its name in a toast.
"""

try:
    import ui
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime import ui

try:
    from layout_base import bar_rows, content_of, metric_class, window_foot, DESK
except ImportError:  # pragma: no cover - direct host import
    from runtime.layout_base import bar_rows, content_of, metric_class, window_foot, DESK

try:
    from artwork import (ArtworkService, PaintDocument, colour_name,
                         read_cover, DESK_W, DESK_H, GAME_W, GAME_H)
    from file_widgets import Bitmap
    from ticks import _ticks_ms, _ticks_diff
except ImportError:  # pragma: no cover - direct host import
    from runtime.artwork import (ArtworkService, PaintDocument, colour_name,
                                 read_cover, DESK_W, DESK_H, GAME_W, GAME_H)
    from runtime.file_widgets import Bitmap
    from runtime.ticks import _ticks_ms, _ticks_diff

try:
    import moybuf as _moybuf
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    try:
        from runtime import moybuf as _moybuf
    except ImportError:
        _moybuf = None

try:
    import moy_loop as _loop
except ImportError:  # pragma: no cover - direct host import
    try:
        from runtime import moy_loop as _loop
    except ImportError:
        _loop = None

try:
    from drawing_pages import GamePage, StoryPage, shown, dot as _dot, stories_here
except ImportError:  # pragma: no cover - direct host import
    from runtime.drawing_pages import GamePage, StoryPage, shown, dot as _dot, stories_here


# The tools, in their order: (icon, key, word). Shape's icon is the shape in use.
TOOLS = (("pencil", "P", "Pencil"), ("paint", "B", "Brush"), ("eraser", "E", "Eraser"),
         ("bucket", "F", "Fill"), ("rect", "S", "Shape"), ("spray", "A", "Spray"),
         ("picker", "I", "Pick"), ("pan", "Space", "Move"))
PENCIL, BRUSH, ERASER, FILL, SHAPE, SPRAY, PICK, MOVE = range(8)
SHAPES = (("line", "Line"), ("rect", "Rect"), ("oval", "Oval"))
# A tool's key, typed: the tool (a repeat on the tool in use opens the brush).
_TOOL_KEYS = {ord("p"): PENCIL, ord("b"): BRUSH, ord("e"): ERASER, ord("f"): FILL,
              ord("s"): SHAPE, ord("a"): SPRAY, ord("i"): PICK}
# The verbs' keys, typed.
KEYS = {ord("c"): "colours", ord("m"): "more", ord("z"): "zoom", ord("n"): "new",
        ord("o"): "open", ord("w"): "wallpaper", ord("g"): "game", ord("v"): "show",
        ord("t"): "story", 0x1A: "undo", 0x19: "redo", 0x04: "dup"}
SPACE = 0x20
# The brush's widths: a desk drawing's and a game-size one's.
SIZES_DESK = (2, 4, 8, 16)
SIZES_GAME = (1, 2, 4)
# The zooms, as quarters of a pixel a drawing pixel takes: 1/4, 1/2, 1, 2, 4.
FIT = 0
ZOOM_EDIT = (4, 8, 16, FIT)              # Z on a drawing one draws on
ZOOM_VIEW = (FIT, 2, 4, 8)               # Z on a desk's drawing a handheld views
ZOOM_SEG = (FIT, 4, 8, 16)               # the desk's segments: Fit, 1x, 2x, 4x
TOAST_MS = 2000
AUTOSAVE_S = 2.5
RETRY_S = 30.0                           # a failing store retries slowly, not hot


def _zoom_word(q):
    """A zoom in quarters as the bar and the seg say it."""
    if q == 1:
        return "¼" if ui.has_symbols() else "1/4"
    if q == 2:
        return "½" if ui.has_symbols() else "1/2"
    return "%d%s" % (q // 4, "×" if ui.has_symbols() else "x")


class PaintAppLayout:
    """Paint's rects over the content rect. `touch` lays the handheld out for
    a finger (the tools two by four at 28 px, one colour target, no hint
    strip); else a handheld with keys (the T-Deck: the tools down the left at
    22, the rail at the right); `desk` the window's (or the desk's whole
    surface): the tool row and its rule, the tools down the left at 40, the
    drawing on its pasteboard, the colours beside it and the foot."""

    MIN_W = 310
    MIN_H = 230
    FOOT = 44

    def __init__(self, w, h, fs=1, windowed=False, cs=None, touch=False):
        self.w = int(w)
        self.h = int(h)
        self.fs = fs = max(1, int(fs))
        self.cs = max(fs, int(cs)) if cs else fs
        self.windowed = bool(windowed)
        self.desk = metric_class() == DESK
        self.touch = bool(touch) and not self.desk
        self.cx, self.cy, self.cw, self.ch = content_of(
            self.w, self.h, 0 if windowed else bar_rows(self.cs))
        cx, cy, cw, ch = self.cx, self.cy, self.cw, self.ch
        self.full = (cx, cy, cw, ch)
        self.hints = None
        self.swatch_page = None
        if self.desk:
            self._desk(cx, cy, cw, ch)
        elif self.touch:
            u = fs
            self.tools = [(cx + 4 * u + (i % 2) * 32 * u, cy + 2 * u + (i // 2) * 32 * u,
                           28 * u, 28 * u) for i in range(8)]
            self.colour = (cx + 4 * u, cy + 132 * u, 28 * u, 28 * u)
            self.brush = (cx + 36 * u, cy + 132 * u, 28 * u, 28 * u)
            self.more = (cx + 4 * u, cy + 168 * u, 28 * u, 28 * u)
            self.view = (cx + 68 * u, cy + 2 * u, cw - 72 * u, ch - 6 * u)
            self.radius = 6 * u
        else:
            u = fs
            self.hints = (cx, cy + ch - 22 * u, cw, 22 * u)
            self.tools = [(cx + 4 * u, cy + 3 * u + i * 23 * u, 22 * u, 22 * u)
                          for i in range(8)]
            self.view = (cx + 32 * u, cy + 3 * u, cw - 64 * u, ch - 28 * u)
            rx = cx + cw - 28 * u
            vy = self.view[1]
            self.colour = (rx, vy, 24 * u, 22 * u)
            self.swatch_page = (rx, vy + 27 * u, 12 * u)
            self.brush = (rx, vy + 129 * u, 24 * u, 22 * u)
            self.more = (rx, vy + 155 * u, 24 * u, 22 * u)
            self.radius = 6 * u
        self.page = (cx, cy, cw, ch)

    def _desk(self, cx, cy, cw, ch):
        under = window_foot() if self.windowed else 0
        self.foot, body = ui.cut_bottom((cx, cy, cw, ch), self.FOOT - under)
        self.row = (cx + 16, cy + 8, cw - 32, 36)
        self.rule = (cx + 16, cy + 52, cw - 32, 1)
        self.tools = [(cx + 16, cy + 66 + i * 44, 40, 40) for i in range(8)]
        self.panel = (cx + cw - 248, cy + 62, 208, body[1] + body[3] - cy - 62)
        px = self.panel[0]
        self.board = (cx + 68, cy + 62, px - 16 - cx - 68,
                      self.foot[1] - 2 - cy - 62)
        self.view = self.board
        self.radius = 0
        self.colour = (px, cy + 62 + 26, 44, 28)
        self.swatch_page = (px, cy + 62 + 64, 26)


class Viewer:
    """A drawing larger than this handheld's own, to look at (owner,
    2026-10-21): never decoded whole. Each zoom reads the drawing's file a row
    at a time into the screen it shows (`read_cover`): Fit a screen of its
    own size at a quarter, a half and up a source of that scale (the half,
    else the whole), off the gc heap; what the view shows of it is cut into a
    window the canvas bakes. A sixteenth of the whole is the overview."""

    OWNER = "artwork"

    def __init__(self, name, w, h, blob, view):
        self.name = name
        self.W = w
        self.H = h
        self.blob = blob
        self.view = view
        self.zoom = FIT
        self.fit_q = self._fit()
        self.pan = [0, 0]                 # the view's top left in the source's pixels
        self._fit_bmp = None
        self._mini = None
        self._src = None                  # (scale in quarters, w, h, pixels)
        self._win = None                  # (key, Bitmap) the view's window of it
        self._show = None

    def _fit(self):
        vw, vh = self.view[2], self.view[3]
        for q in (4, 2, 1):
            if self.W * q // 4 <= vw and self.H * q // 4 <= vh:
                return q
        return 1

    def quarters(self):
        return self.fit_q if self.zoom == FIT else self.zoom

    def _alloc(self, n):
        if _moybuf is not None:
            return _moybuf.alloc(n)
        return bytearray(n)

    def _free(self, pix):
        if pix is not None and _moybuf is not None and isinstance(pix, memoryview):
            try:
                _moybuf.free(pix)
            except Exception:  # noqa: BLE001 -- already returned
                pass

    def fit_bitmap(self):
        if self._fit_bmp is None:
            q = self.fit_q
            w, h = self.W * q // 4, self.H * q // 4
            pix = bytearray(w * h)
            if read_cover(self.blob, pix, w, h) is None:
                return None
            self._fit_bmp = Bitmap(w, h, pix, self.OWNER)
        return self._fit_bmp

    def mini(self):
        """The whole at a sixteenth: the overview a zoomed view shows."""
        if self._mini is None:
            w, h = max(1, self.W // 16), max(1, (self.H + 8) // 16)
            pix = bytearray(w * h)
            if read_cover(self.blob, pix, w, h) is None:
                return None
            self._mini = Bitmap(w, h, pix)
        return self._mini

    def show_bitmap(self, w, h):
        if self._show is None or self._show.w != w or self._show.h != h:
            pix = bytearray(w * h)
            if read_cover(self.blob, pix, w, h) is None:
                return None
            self._show = Bitmap(w, h, pix, self.OWNER)
        return self._show

    def _source(self):
        """The raster the zoom reads from: the drawing at a half for a half,
        whole for 1x and up."""
        q = min(self.quarters(), 4)
        src = self._src
        if src is not None and src[0] == q:
            return src
        self.drop_source()
        w, h = self.W * q // 4, self.H * q // 4
        pix = self._alloc(w * h)
        if read_cover(self.blob, pix, w, h) is None:
            self._free(pix)
            return None
        self._src = (q, w, h, pix)
        return self._src

    def drop_source(self):
        if self._src is not None:
            self._free(self._src[3])
        self._src = None
        self._win = None

    def window(self):
        """(Bitmap, k): the view's window of the zoom's source and the
        whole pixels each of its pixels takes on the glass."""
        src = self._source()
        if src is None:
            return None, 1
        q, sw, sh, pix = src
        k = max(1, self.quarters() // q)
        vw, vh = self.view[2], self.view[3]
        ww, wh = min(sw, vw // k), min(sh, vh // k)
        self.pan[0] = max(0, min(self.pan[0], sw - ww))
        self.pan[1] = max(0, min(self.pan[1], sh - wh))
        key = (q, self.pan[0], self.pan[1], ww, wh)
        if self._win is not None and self._win[0] == key:
            return self._win[1], k
        win = bytearray(ww * wh)
        x0, y0 = self.pan
        for r in range(wh):
            o = (y0 + r) * sw + x0
            win[r * ww:(r + 1) * ww] = pix[o:o + ww]
        bmp = Bitmap(ww, wh, win, self.OWNER)
        self._win = (key, bmp)
        return bmp, k

    def step(self, zooms):
        """The next zoom of `zooms`, the view kept about its middle."""
        q0 = self.quarters()
        i = zooms.index(self.zoom) if self.zoom in zooms else 0
        self.zoom = zooms[(i + 1) % len(zooms)]
        q1 = self.quarters()
        s0 = min(q0, 4)
        s1 = min(q1, 4)
        vw, vh = self.view[2], self.view[3]
        k0 = max(1, q0 // s0)
        mx = (self.pan[0] + vw // (2 * k0)) * 4 // s0 if q0 != self.fit_q else self.W // 2
        my = (self.pan[1] + vh // (2 * k0)) * 4 // s0 if q0 != self.fit_q else self.H // 2
        k1 = max(1, q1 // s1)
        self.pan[0] = mx * s1 // 4 - vw // (2 * k1)
        self.pan[1] = my * s1 // 4 - vh // (2 * k1)
        if self.zoom == FIT:
            self.drop_source()

    def nudge(self, dx, dy):
        k = max(1, self.quarters() // min(self.quarters(), 4))
        step = 16 // k
        self.pan[0] += dx * step
        self.pan[1] += dy * step

    def frame(self):
        """Where the view stands in the whole, in the whole's pixels:
        (x, y, w, h), what the overview frames."""
        q = self.quarters()
        s = min(q, 4)
        vw, vh = self.view[2], self.view[3]
        return (self.pan[0] * 4 // s, self.pan[1] * 4 // s,
                min(self.W, vw * 4 // q), min(self.H, vh * 4 // q))

    def close(self):
        self.drop_source()
        self._fit_bmp = None
        self._mini = None
        self._show = None
        self.blob = None


def _grow(c, x0, y0, x1, y1):
    """Rect `c` [x0, y0, x1, y1) grown over another; an empty one adds
    nothing."""
    if x1 <= x0 or y1 <= y0:
        return
    if x0 < c[0]:
        c[0] = x0
    if y0 < c[1]:
        c[1] = y0
    if x1 > c[2]:
        c[2] = x1
    if y1 > c[3]:
        c[3] = y1


class _Damage:
    """Paint's damage role: `all` and `again` as the role's, each counted in
    `gen`; `mark` asks for a frame whose only change is a mark's -- the doc's
    pixels and a shape's outline -- which the stroke frame paints alone;
    `settle` asks, from a draw, for one more frame of the same page."""

    def __init__(self, role):
        self._role = role
        self.gen = 0

    def all(self):
        self.gen += 1
        self._role.all()

    def again(self):
        self.gen += 1
        self._role.again()

    def mark(self):
        self._role.all()

    def settle(self):
        self._role.again()


# The most physical buffers a canvas rotates through (the scroll's paint ring
# says the same of its own): a stroke frame needs that many paints of the
# same page behind it, and the ring keeps one more, the frame in hand.
_RING = 3


class PaintAppLayer:
    """The Paint app: the canvas, its tools and colours, More's verbs, and a
    desk's drawing viewed on a handheld."""

    id = "artwork"
    domain = "system"
    TITLE = "Paint"
    # The shell roles this app uses (runtime/app_context.py): its document
    # model's (`ArtworkService`, built here), its copies' and My Art's door.
    NEEDS = ("surface", "theme", "damage", "files", "carts", "wallpaper",
             "prefs", "notify", "nav")
    # The open picture's rows, `paint_doc` and `paint_doc_kind`, which the
    # artwork role's C rows read (native/moy_app's MOY_APP_ARTWORK_NS).
    PREFS_NS = "paint"
    UI = True
    # The WM's shown protocol (docs/surface_model_v1.md §14.3): `retained`
    # before a window draw, `changed` after it.
    HELD = True
    AUTOSAVE_S = AUTOSAVE_S
    RETRY_S = RETRY_S

    def __init__(self, ctx, names):
        self.ctx = ctx
        self._surf = ctx.surface
        self._theme = ctx.theme
        self._damage = _Damage(ctx.damage)
        self._nav = ctx.nav
        self._ui = ctx.ui
        self._art = self.service = ArtworkService(ctx)
        self._art.on_leave = self._leaving
        self.names = names
        cv = ctx.surface.canvas()
        self.layout = PaintAppLayout(cv.w, cv.h, self._surf.font_scale(),
                                  self._surf.windowed(), self._surf.chrome_scale())
        self._touch = None            # the input at open: None until the first frame
        # The document is built at the app's first use of it (`doc`), not at
        # the console's start: a session that never opens Paint pays nothing.
        self._doc = None
        self._starter_pending = self.layout.desk
        self.tool = PENCIL
        self.shape = 1                # SHAPES: a box until a repeat tap moves it on
        self.shape_fill = False
        self.color = names["blue"]
        self.size_i = 1
        self.zoom = 4                 # quarters, FIT the largest of 1x, 1/2, 1/4 that fits
        self.pan = [0, 0]             # the view's top left, in drawing pixels
        self.cur = [0, 0]             # the cursor, in drawing pixels
        self.mark = None              # where the last mark was made
        self.pop = None               # the popover open: colours | brush | more
        self.swatch = 0               # the colour the colours' ring is on
        self.page = None              # a page over the canvas: game | story | show
        self.viewer = None            # a desk's drawing, looked at
        self.toast = None             # (text, icon, until ms, undo or None)
        self._pending_toast = None
        self.game = None
        self.story = None
        self._stroke = None           # the last drawing pixel a stroke reached
        self._shape0 = None           # a shape's first corner, while it is dragged
        self._shape1 = None
        self._down = False            # the pointer pressed on the drawing
        self._panning = None          # (x, y) a Move drag last stood at
        self._held = 0                # frames an arrow has been held
        self._key = 0
        self._space = False
        self._drawing_key = False     # `a` held, drawing at the cursor
        self._rng = 0x5EED123
        self._unsaved = False
        self._idle = 0.0
        self._first_save = False      # the drawing has never been written
        self._focus = None
        self._pop_rect = None
        self._opened = None           # the doc pointer the open drawing was opened at
        # The stroke frame (`_stroke_frame`): the paints behind it, each
        # [frame, epoch, x0, y0, x1, y1) the glass its marks changed; the
        # canvas they went to; the page's signature and its epoch; and last
        # frame's shape outline and pointer sprite, which a frame repaints
        # where they stood.
        self._ring = [[-1, -1, 0, 0, 0, 0] for _i in range(_RING + 1)]
        self._ring_cv = None
        self._sig = [None] * 20
        self._epoch = 0
        self._gen = -1
        self._chg = [0, 0, 0, 0]
        self._spec_l = [0] * 9
        self._src = [0, 0, 0, 0]
        self._hole = [0, 0, 0, 0, 0]
        self._own = [0, 0, 0, 0]
        self._clip_r = None
        self._pv = [0, 0, 0, 0]
        self._sp = [0, 0, 0, 0]
        self._sp_out = False
        self._cu = [0, 0, 0, 0]
        self._drip_c = 0
        # The window's word on the target of the next draw (`retained`):
        # [known, x0, y0, x1, y1), whether the WM said it, and whether the
        # draw before was one it said it of; and this draw's change, read by
        # `changed`.
        self._word = [0, 0, 0, 0, 0]
        self._held_on = False
        self._held_last = False
        self._moved = True
        self._said = False            # this draw's target is the one the WM said
        self._sf_ran = False          # the last draw weighed a stroke frame
        self._changed = False

    # -- the document ------------------------------------------------------------

    @property
    def doc(self):
        d = self._doc
        if d is None:
            d = self._doc = PaintDocument(*self._board_size())
        return d

    @doc.setter
    def doc(self, d):
        self._doc = d

    def _board_size(self):
        """The drawings this board makes: a desk board's, else a handheld's."""
        return (DESK_W, DESK_H) if metric_class() == DESK else (GAME_W, GAME_H)

    def sizes(self):
        """The brush's widths for the open drawing's size."""
        d = self._doc
        if d is not None and (d.W > GAME_W or d.H > GAME_H):
            return SIZES_DESK
        return SIZES_GAME

    def size(self):
        s = self.sizes()
        return s[min(self.size_i, len(s) - 1)]

    def is_app(self, cart):
        """The app-API matcher (docs/app_api_v1.md)."""
        return self._art.is_paint_app(cart)

    def relayout(self, w, h, fs, cs=None):
        self.layout = PaintAppLayout(w, h, fs, self._surf.windowed(), cs, bool(self._touch))
        if self.viewer is not None:
            self.viewer.view = self.layout.view

    def open(self):
        art = self._art
        self._return_bakes()           # #186: load() may re-mint the document
        self._close_viewer()
        self.pop = None
        self.page = None
        self.toast = None
        self._touch = None
        self._ui.reset()
        peek = None if self.layout.desk else art.peek()
        if peek is not None and (peek[0] > GAME_W or peek[1] > GAME_H):
            # A desk's drawing on a handheld: to look at, never decoded whole.
            self.viewer = Viewer(art.doc_name(), peek[0], peek[1], peek[2], self.layout.view)
            self._opened = self._pointer()
            self._damage.all()
            return
        peek = None
        loaded = art.load()
        self._first_save = False
        if self.doc.load(loaded):
            if not art.editable():
                self._say(art.why_read_only(), "warn")
        elif not art.editable():
            # A picture that would not decode. It still opens HERE, saying so:
            # the blank canvas is not a new drawing and is never written back.
            self._say(art.why_read_only(), "warn")
        elif self._starter_pending:
            # The editable starter garden, built on the first Paint launch.
            self.doc = PaintDocument(*self._board_size(), seed=True)
            self._first_save = True
            self._mark_changed()
        else:
            if (self.doc.W, self.doc.H) != self._board_size():
                self.doc = PaintDocument(*self._board_size())
            self._first_save = True
        self._starter_pending = False
        self._unsaved = self._unsaved and art.editable()
        self.size_i = min(self.size_i, len(self.sizes()) - 1)
        self._open_view()
        self._opened = self._pointer()
        self._damage.all()

    def _pointer(self):
        """Where the doc pointer stands, as the count of its moves: what a
        frame compares, with no prefs read."""
        return self._art.moves

    def _open_view(self):
        """At 1x centred on the last mark on a handheld; the desk at Fit."""
        d = self.doc
        m = self._art_mark()
        self.cur = [m[0], m[1]] if m else [d.W // 2, d.H // 2]
        self.zoom = FIT if self.layout.desk else 4
        self._centre(self.cur)

    def _art_mark(self):
        got = self._art._prefs.get("mark")
        try:
            x, y = [int(v) for v in str(got).split(",")]
        except Exception:  # noqa: BLE001 -- no mark yet
            return None
        d = self.doc
        if 0 <= x < d.W and 0 <= y < d.H:
            return (x, y)
        return None

    def _close_viewer(self):
        if self.viewer is not None:
            self.viewer.close()
            self.viewer = None

    def _return_bakes(self):
        """Hand this document's off-heap RGB565 bakes back (#186): the
        document is left (close) or replaced (a new drawing), and nothing is
        mid-draw there. The canvas nulls the bake as it frees, so the next
        frame rebuilds rather than reading returned RAM."""
        rel = getattr(self._surf.canvas(), "release_bakes", None)
        if rel is not None:
            rel(PaintDocument.OWNER)

    def close(self):
        """The app-API LEAVING hook: the drawing is saved where it changed,
        its bakes and a viewed drawing's screens go back. A document never
        built (Paint never opened) has nothing to write and lent no bake."""
        self._close_viewer()
        if self._doc is None:
            return
        self._save()
        self._return_bakes()

    commit = close

    def _leaving(self):
        """The service is about to point at another picture: the open one
        is written first, while its name is still the pointer's."""
        if self._doc is not None and self.viewer is None and self._opened is not None:
            self._save()

    def has_page(self):
        return self.pop is not None or self.page is not None

    def back(self):
        """Back one level (B, Esc, the bar's chevron): a popover closes, a
        page goes back to the canvas. False on the canvas: the host leaves."""
        if self.pop is not None:
            self.pop = None
            self._ui.reset()
        elif self.page is not None:
            self.page = None
            self._ui.reset()
        else:
            return False
        self._damage.all()
        return True

    def doc_title(self):
        if self.viewer is not None:
            return shown(self.viewer.name)
        name = self._art.doc_name()
        return shown(name) if name else "New drawing"

    # -- saving -----------------------------------------------------------------

    def _mark_changed(self):
        self._unsaved = True
        self._idle = 0.0

    def _save(self):
        art = self._art
        if self.viewer is not None or not art.editable():
            return True                # show-only: there is nothing to write back
        if not self._unsaved and art.doc_name() is not None and not self._first_save:
            return True
        if not self._unsaved and self._first_save:
            return True                # a new drawing nobody marked: nothing lands
        d = self.doc
        if art.save(d.pix, d.W, d.H):
            self._opened = self._pointer()
            if self._first_save:
                self._say("Saved " + shown(art.doc_name()))
            self._first_save = False
            self._unsaved = False
            self._idle = 0.0
            if self.mark is not None:
                art._prefs.set("mark", "%d,%d" % self.mark)
            return True
        self._say(art.last_error or "Can't save", "warn", True)
        self._idle = self.AUTOSAVE_S - self.RETRY_S
        return False

    def _fresh_doc(self, w, h):
        """A new auto-named drawing: the open one keeps its file."""
        self._save()
        self._return_bakes()
        self._close_viewer()
        self._art.new_doc(w, h)
        self.doc = PaintDocument(w, h)
        self._unsaved = False
        self._first_save = True
        self.mark = None
        self.size_i = min(self.size_i, len(self.sizes()) - 1)
        self.cur = [w // 2, h // 2]
        self.zoom = FIT if self.layout.desk else 4
        self._centre(self.cur)
        self._opened = self._pointer()

    # -- toasts -------------------------------------------------------------------

    def _say(self, text, icon="check", danger=False, undo=None):
        if not text:
            return
        self.toast = (text, "warn" if danger else icon, _ticks_ms() + TOAST_MS, undo)
        self._damage.all()

    # -- the view ---------------------------------------------------------------

    def _quarters(self):
        """The zoom in quarters: Fit is the largest of 1x, 1/2 and 1/4 that
        shows the whole drawing."""
        if self.zoom != FIT:
            return self.zoom
        d = self.doc
        vw, vh = self.layout.view[2], self.layout.view[3]
        for q in (4, 2, 1):
            if d.W * q // 4 <= vw and d.H * q // 4 <= vh:
                return q
        return 1

    def _spec(self):
        """[x, y, w, h, sx, sy, sw, sh, q]: where the drawing's (sx, sy, sw,
        sh) part stands on the glass at q quarters a pixel. One list, filled
        afresh each call (a frame asks several times and keeps nothing)."""
        d = self.doc
        q = self._quarters()
        v = self.layout.view
        vx = v[0]
        vy = v[1]
        vw = v[2]
        vh = v[3]
        o = self._spec_l
        dw = d.W * q // 4
        dh = d.H * q // 4
        if dw <= vw:
            o[0] = vx + (vw - dw) // 2
            o[4] = 0
            o[6] = d.W
            o[2] = dw
            self.pan[0] = 0
        else:
            sw = vw * 4 // q
            self.pan[0] = max(0, min(self.pan[0], d.W - sw))
            o[0] = vx
            o[4] = self.pan[0]
            o[6] = sw
            o[2] = sw * q // 4
        if dh <= vh:
            o[1] = vy + (vh - dh) // 2
            o[5] = 0
            o[7] = d.H
            o[3] = dh
            self.pan[1] = 0
        else:
            sh = vh * 4 // q
            self.pan[1] = max(0, min(self.pan[1], d.H - sh))
            o[1] = vy
            o[5] = self.pan[1]
            o[7] = sh
            o[3] = sh * q // 4
        o[8] = q
        return o

    def _centre(self, p):
        q = self._quarters()
        vw, vh = self.layout.view[2], self.layout.view[3]
        self.pan[0] = p[0] - vw * 2 // q
        self.pan[1] = p[1] - vh * 2 // q

    def _follow(self):
        """Keep the cursor in the view, a sixteenth of it from the edge."""
        x, y, w, h, sx, sy, sw, sh, q = self._spec()
        mx, my = max(1, sw // 16), max(1, sh // 16)
        c = self.cur
        if c[0] < sx + mx:
            self.pan[0] = c[0] - mx
        elif c[0] >= sx + sw - mx:
            self.pan[0] = c[0] - sw + mx + 1
        if c[1] < sy + my:
            self.pan[1] = c[1] - my
        elif c[1] >= sy + sh - my:
            self.pan[1] = c[1] - sh + my + 1

    def _to_doc(self, px, py, clamp=False):
        x, y, w, h, sx, sy, sw, sh, q = self._spec()
        dx = sx + (px - x) * 4 // q
        dy = sy + (py - y) * 4 // q
        d = self.doc
        if clamp:
            return (max(0, min(d.W - 1, dx)), max(0, min(d.H - 1, dy)))
        if px < x or py < y or px >= x + w or py >= y + h:
            return None
        if dx < 0 or dy < 0 or dx >= d.W or dy >= d.H:
            return None
        return (dx, dy)

    def _to_glass(self, p):
        x, y, w, h, sx, sy, sw, sh, q = self._spec()
        return (x + (p[0] - sx) * q // 4, y + (p[1] - sy) * q // 4)

    def _zoom_to(self, z):
        mid = self._view_middle()
        self.zoom = z
        self._centre(mid)
        self._damage.all()

    def _view_middle(self):
        x, y, w, h, sx, sy, sw, sh, q = self._spec()
        return (sx + sw // 2, sy + sh // 2)

    def _step_zoom(self):
        if self.viewer is not None:
            self.viewer.step(ZOOM_VIEW)
            self._damage.all()
            return
        z = ZOOM_EDIT
        i = z.index(self.zoom) if self.zoom in z else len(z) - 1
        self._zoom_to(z[(i + 1) % len(z)])

    # -- input --------------------------------------------------------------------

    def ball_steps(self):
        """The trackball steps an open popover (colours, brush, more) and
        points at the drawing otherwise (Workstation.ball_steps)."""
        return self.pop is not None

    def handle_input(self, inp):
        k = getattr(inp, "last_key", 0) or 0
        if not isinstance(k, int):
            k = ord(k[0]) if k else 0
        typed = k != 0 and k != self._key
        self._key = k
        if self.toast is not None and (typed or inp.any_pressed()):
            if typed and k == 0x1A and self.toast[3] is not None:
                self.toast[3]()                 # Ctrl+Z on "Added as slide 6 ... Undo"
                self.toast = None
                self._say("Taken out again")
                return True
            self.toast = None
            self._damage.all()
        if self.page == "show":
            if typed or inp.any_pressed():
                self.page = None
                self._damage.all()
            return True
        if inp.pressed("b"):
            self.back()
            return True
        if self.pop == "colours" and not self.layout.touch:
            self._swatch_keys(inp)
            return True
        if self.pop is not None or self.page is not None:
            if self._ui.keys(inp):
                self._damage.all()
            return True
        self._space = k == SPACE
        if typed and k != SPACE:
            lk = k + 32 if 65 <= k <= 90 else k
            if self._verb_key(lk):
                return True
        dx = (1 if inp.held("right") else 0) - (1 if inp.held("left") else 0)
        dy = (1 if inp.held("down") else 0) - (1 if inp.held("up") else 0)
        if dx or dy:
            self._held += 1
        else:
            self._held = 0
        step = dx or dy
        moved = step and (self._held == 1 or self._held > 8)
        if self.viewer is not None:
            if moved and self.viewer.zoom != FIT:
                self.viewer.nudge(dx, dy)
                self._damage.all()
            if inp.pressed("a"):
                self._say("From a desk: you can look, not draw")
            return True
        if moved:
            if self._space or self.tool == MOVE:
                q = self._quarters()
                n = max(1, 16 * 4 // q)
                self.pan[0] += dx * n
                self.pan[1] += dy * n
            else:
                n = 1 if self._held <= 24 else max(1, 4 * 4 // self._quarters())
                d = self.doc
                self.cur[0] = max(0, min(d.W - 1, self.cur[0] + dx * n))
                self.cur[1] = max(0, min(d.H - 1, self.cur[1] + dy * n))
                self._follow()
                if self._drawing_key:
                    self._drag((self.cur[0], self.cur[1]))
            if self._space or self.tool == MOVE:
                self._damage.all()
            else:
                self._damage.mark()
        down = inp.held("a") and not self._space
        if down and not self._drawing_key:
            self._drawing_key = True
            self._press((self.cur[0], self.cur[1]))
            self._damage.mark()
        elif not down and self._drawing_key:
            self._drawing_key = False
            self._release()
            self._damage.mark()
        return True

    def _swatch_keys(self, inp):
        """The colours popover under keys: the arrows move its ring over the
        64, Enter or `a` takes the colour it is on."""
        dx = (1 if inp.pressed("right") else 0) - (1 if inp.pressed("left") else 0)
        dy = (1 if inp.pressed("down") else 0) - (1 if inp.pressed("up") else 0)
        if dx or dy:
            c = self.swatch
            x = max(0, min(7, c % 8 + dx))
            y = max(0, min(7, c // 8 + dy))
            self.swatch = y * 8 + x
            self._damage.all()
        if inp.pressed("a") or inp.pressed("run") or self._key == 0x0D:
            self.color = self.swatch
            self.pop = None
            self._ui.reset()
            self._damage.all()

    def _verb_key(self, k):
        """A typed key on the canvas: a tool, a verb. Whether it was one."""
        if self.viewer is not None:
            verb = KEYS.get(k)
            if verb in ("zoom", "more", "open", "wallpaper", "show", "story"):
                self._verb(verb)
                return True
            if k in _TOOL_KEYS or verb in ("colours", "new", "game") or k == 0x0D:
                self._say("From a desk: you can look, not draw")
                return True
            return False
        t = _TOOL_KEYS.get(k)
        if t is not None:
            self._pick_tool(t, True)
            return True
        verb = KEYS.get(k)
        if verb is not None:
            self._verb(verb)
            return True
        return False

    def _pick_tool(self, t, keyed=False):
        """Tool `t` in use: a repeat on Shape moves its shape on, a repeat key
        on a tool with a brush opens the brush."""
        if t == self.tool and t == SHAPE:
            self.shape = (self.shape + 1) % len(SHAPES)
        elif t == self.tool and keyed and t in (PENCIL, BRUSH, ERASER, SPRAY):
            self._open("brush")
        self.tool = t
        self._damage.all()

    def _verb(self, verb):
        art = self._art
        if verb == "undo":
            if self.viewer is None and self.doc.undo():
                self._mark_changed()
        elif verb == "redo":
            if self.viewer is None and self.doc.redo():
                self._mark_changed()
        elif verb == "zoom":
            self._step_zoom()
        elif verb in ("colours", "brush", "more"):
            self._open(verb)
        elif verb == "new":
            self._fresh_doc(*self._board_size())
        elif verb == "new_game":
            self._fresh_doc(GAME_W, GAME_H)
        elif verb == "open":
            self._save()
            self._nav.open_app("myart")
        elif verb == "wallpaper":
            name = self._source_name()
            if name and self._save() and art.set_wallpaper(name):
                self._say(shown(name) + " is the wallpaper")
            elif name:
                self._say(art.last_error or "Can't use it", "warn", True)
        elif verb == "game":
            if self.viewer is None and (self.doc.W, self.doc.H) == (GAME_W, GAME_H) \
                    and self._save() and art.doc_name():
                self.game = GamePage(art.copies, self._nav, art.doc_name())
                self.page = "game"
                self._ui.reset()
                self._focus = "Games"
        elif verb == "story":
            name = self._source_name()
            if name and self._save() and stories_here():
                self.story = StoryPage(art.ctx.carts, art.ctx.files, name)
                self.page = "story"
                self._ui.reset()
        elif verb == "show":
            self._save()
            self.page = "show"
        elif verb == "copy":
            self._copy_to_my_art()
        elif verb == "dup":
            name = self._source_name()
            if name and self._save():
                got, err = art.ctx.files.duplicate("drawings", name)
                if err is None and got:
                    self._say("Duplicated as " + shown(got))
        self._damage.all()

    def _source_name(self):
        """The gallery drawing the verbs act on: the one open or viewed."""
        if self.viewer is not None:
            return self.viewer.name
        return self._art._open_drawing()

    def _copy_to_my_art(self):
        """A show-only picture's pixels as a new drawing of My Art's."""
        art = self._art
        d = self.doc
        name = art.new_doc(d.W, d.H)
        if name and art.save(d.pix, d.W, d.H):
            self._say("Copied to My Art as " + shown(name))
        else:
            self._say(art.last_error or "Can't copy it", "warn", True)

    def _open(self, which):
        if self.pop == which:
            self.pop = None
        else:
            self.pop = which
            self.swatch = self.color
        self._ui.reset()
        self._damage.all()

    # -- the pointer ----------------------------------------------------------------

    def handle_pointer(self, px, py, click):
        down = bool(getattr(self._surf.pointer(), "down", False))
        if self.page == "show":
            if click:
                self.page = None
                self._damage.all()
            return True
        if self.toast is not None and click:
            if self.toast[3] is not None and self._toast_hit(px, py):
                self.toast[3]()
                self.toast = None
                self._damage.all()
                return True
        if self.pop == "colours" and click and self._swatch_tap(px, py):
            return True
        if self.pop is None and self.page is None and self.viewer is None:
            if self._canvas_pointer(px, py, click, down):
                return True
        if self._ui.point(px, py, click, down):
            self._damage.all()
        return True

    def _canvas_pointer(self, px, py, click, down):
        lay = self.layout
        if click:
            p = self._to_doc(px, py)
            if p is not None:
                self._down = True
                if self.tool == MOVE:
                    self._panning = (px, py)
                    self._damage.all()
                else:
                    self.cur = [p[0], p[1]]
                    self._press(p)
                    self._damage.mark()
                return True
            if lay.swatch_page is not None and not lay.desk:
                c = self._page_swatch(px, py)
                if c is not None:
                    self.color = c
                    self._damage.all()
                    return True
            if lay.desk:
                c = self._desk_swatch(px, py)
                if c is not None:
                    self.color = c
                    self._damage.all()
                    return True
            return False
        if not self._down:
            return False
        if self._panning is not None:
            q = self._quarters()
            self.pan[0] -= (px - self._panning[0]) * 4 // q
            self.pan[1] -= (py - self._panning[1]) * 4 // q
            self._panning = (px, py)
            self._damage.all()
        else:
            p = self._to_doc(px, py, True)
            self.cur[0] = p[0]
            self.cur[1] = p[1]
            self._drag(p)
            self._damage.mark()
        if not down:
            self._down = False
            self._panning = None
            self._release()
            self._damage.mark()
        return True

    def _page_swatch(self, px, py):
        x0, y0, s = self.layout.swatch_page
        i = (px - x0) // s if px >= x0 else -1
        j = (py - y0) // s if py >= y0 else -1
        if 0 <= i < 2 and 0 <= j < 8:
            return (self.color // 16) * 16 + j * 2 + i
        return None

    def _desk_swatch(self, px, py):
        x0, y0, s = self.layout.swatch_page
        i = (px - x0) // s if px >= x0 else -1
        j = (py - y0) // s if py >= y0 else -1
        if 0 <= i < 8 and 0 <= j < 8 and (px - x0) % s < s - 2 and (py - y0) % s < s - 2:
            return j * 8 + i
        return None

    def _swatch_tap(self, px, py):
        r = self._pop_rect
        if r is None:
            return False
        x0, y0, cols, rows, s, first = r
        i = (px - x0) // s if px >= x0 else -1
        j = (py - y0) // s if py >= y0 else -1
        if 0 <= i < cols and 0 <= j < rows and first + j * cols + i < 64:
            self.color = first + j * cols + i
            self.pop = None
            self._ui.reset()
            self._damage.all()
            return True
        return False

    def _toast_hit(self, px, py):
        """A tap on the toast's band, the foot of the view."""
        vx, vy, vw, vh = self.layout.view
        return vx <= px < vx + vw and vy + vh - 40 * self.layout.fs <= py < vy + vh

    # -- marks --------------------------------------------------------------------

    def _press(self, p):
        art = self._art
        if not art.editable():
            self._say(art.why_read_only() or "Only to look at", "warn")
            return
        d = self.doc
        t = self.tool
        if t == PICK:
            self.color = d.get(p[0], p[1])
            return
        if t == MOVE:
            return
        d.snapshot()
        if t == FILL:
            d.flood(p[0], p[1], self.color)
            d.finish()
            self.mark = p
            self._mark_changed()
            return
        if t == SHAPE:
            self._shape0 = p
            self._shape1 = p
            return
        self._stroke = p
        self._paint_at(p, p)

    def _paint_at(self, a, b):
        d = self.doc
        t = self.tool
        s = self.size()
        if t == SPRAY:
            self._rng = d.spray(b[0], b[1], self.color, s, self._rng)
        elif t == ERASER:
            d.line(a[0], a[1], b[0], b[1], PaintDocument.PAPER, s * 2, False)
        else:
            d.line(a[0], a[1], b[0], b[1], self.color, s, t == BRUSH)

    def _drag(self, p):
        if self._shape0 is not None:
            self._shape1 = p
            return
        if self._stroke is None or not self.doc.action_live:
            return
        self._paint_at(self._stroke, p)
        self._stroke = p

    def _release(self):
        d = self.doc
        if not d.action_live:
            self._stroke = self._shape0 = self._shape1 = None
            return
        a, b = self._shape0, self._shape1
        if a is not None and b is not None:
            s = self.size()
            if self.shape == 0:
                d.line(a[0], a[1], b[0], b[1], self.color, s, True)
            elif self.shape == 1:
                d.box(a, b, self.color, self.shape_fill, s)
            else:
                d.oval(a, b, self.color, self.shape_fill, s)
            self.mark = b
        else:
            self.mark = self._stroke
        d.finish()
        self._stroke = self._shape0 = self._shape1 = None
        self._mark_changed()

    # -- draw ---------------------------------------------------------------------

    # -- the WM's shown protocol (docs/surface_model_v1.md §14.3) ------------------

    def retained(self, known, x0, y0, x1, y1):
        """The window's word on the target of the draw that follows: when
        `known`, it holds the page as this layer's last draw left it but for
        [x0, y0, x1, y1)."""
        h = self._word
        h[0] = known
        h[1] = x0
        h[2] = y0
        h[3] = x1
        h[4] = y1
        self._held_on = True

    def changed(self):
        """What the last draw changed of the page, [x0, y0, x1, y1), or None
        when it was a whole frame."""
        return self._own if self._changed else None

    def draw(self, dt):
        # The window's word counts only when the draw before was one it said
        # it of too, and weighed a stroke frame: anything else may have drawn
        # the page since, or drawn one the page's signature never saw.
        said = self._held_on
        self._said = said and self._held_last and self._word[0] and self._sf_ran
        self._sf_ran = False
        self._held_on = False
        self._held_last = said
        self._changed = False
        cv = self._surf.canvas()
        if self._opened is not None and self._pointer() != self._opened \
                and (self._doc is None or not self._doc.action_live):
            # another app pointed Paint at a drawing (My Art's Open, Files'),
            # and the window came forward as it was: open that one (the
            # one open was written as the pointer moved, `on_leave`)
            self.open()
        if (self._unsaved and self.page is None and self._doc is not None
                and not self._doc.action_live):
            self._idle += dt
            if self._idle >= self.AUTOSAVE_S:
                self._save()
        if self.toast is not None:
            if _ticks_diff(_ticks_ms(), self.toast[2]) >= 0:
                self.toast = None
            else:
                self._damage.again()
        th = self._theme.colors()
        lay = self.layout
        if (self.page is None and self.viewer is None and self._doc is not None
                and self._stroke_frame(cv, th)):
            self._changed = True
            return
        self._ui.begin(cv, th, (0, 0, cv.w, cv.h), lay.desk, lay.windowed)
        if self._touch is None:
            self._touch = ui.hints_width(("back", "Back")) == 0
            # the page this frame paints is the first a stroke frame may
            # follow: one more frame begins bringing every buffer to it
            self._damage.again()
            if self._touch != lay.touch and not lay.desk:
                self.relayout(lay.w, lay.h, lay.fs, lay.cs)
                lay = self.layout
        if self.page == "show":
            self._show(cv, th)
            return
        ui.page("")
        was = (self.pop, self.page)
        if self._focus is not None:
            ui.focus(self._focus)
            self._focus = None
        if self.page == "game":
            self._game_page(cv)
        elif self.page == "story":
            self._story_page(cv)
        elif self.viewer is not None:
            self._view_page(cv, th)
        else:
            self._canvas(cv, th)
        if self.toast is not None:
            self._draw_toast()
        ui.end()
        if (self.pop, self.page) != was:
            self._damage.again()

    def _lend(self, title, tools=(), off=(), back=False):
        got = ui.bar(title, tools, off, back)
        if got == ui.UP:
            self.back()
        return got

    def _canvas(self, cv, th):
        lay = self.layout
        d = self.doc
        zw = _zoom_word(self._quarters())
        if lay.desk:
            got = self._lend(self.doc_title())
        else:
            got = self._lend("Paint", ("undo", "redo", zw),
                             (not d.history, not d.future, False))
        if got == 0:
            self._verb("undo")
        elif got == 1:
            self._verb("redo")
        elif got == 2:
            self._verb("zoom")
        if self.pop is None:
            ui.focus("nowhere")       # the arrows are the cursor's
        if lay.desk:
            self._desk_row(cv, th)
        self._tools(cv, th)
        self._drawing(cv, th)
        if lay.desk:
            self._desk_panel(cv, th)
            self._desk_foot(cv, th)
        else:
            self._rail(cv, th)
            self._hints()
        if self.pop == "colours":
            self._colours_pop(cv, th)
        elif self.pop == "brush":
            self._brush_pop()
        elif self.pop == "more":
            self._more()

    def _tools(self, cv, th):
        lay = self.layout
        for i, (icon, key, word) in enumerate(TOOLS):
            if i == SHAPE:
                icon = SHAPES[self.shape][0]
            ui.at(*lay.tools[i])
            label = word + "\t" + key if lay.desk else ""
            if ui.tool(icon, label, {"id": "Tool " + word, "current": i == self.tool}):
                self._pick_tool(i)
            if i == PICK:
                self._drip(cv, lay.tools[i])

    def _drip(self, cv, r):
        """Pick's drip, in the colour under the cursor."""
        x, y, w, h = r
        side = 24 if self.layout.desk else 16 * self.layout.fs
        k = side / 24
        o = (w - side) // 2
        dd = max(3, side // 6)
        c = self._drip_colour()
        cx = x + o + int(5.8 * k)
        cy = y + (h - side) // 2 + int(20 * k)
        cv.rrect(cx - dd // 2, cy - dd // 2, dd, dd, dd // 2, c)

    def _drip_colour(self):
        """The colour under the cursor; while a stroke is drawn, the one it
        was under when the stroke began."""
        if self._down or self._drawing_key:
            return self._drip_c
        d = self.doc
        c = self._drip_c = d.get(min(d.W - 1, self.cur[0]), min(d.H - 1, self.cur[1]))
        return c

    def _drawing(self, cv, th):
        lay = self.layout
        d = self.doc
        vx, vy, vw, vh = lay.view
        dirty = d.take_dirty()
        if dirty is not None:
            cv.rebake(d.image, *dirty)
        spec = self._spec()
        self._view(cv, th, spec)
        g = self._cursor_at()
        if g is not None:
            cv.clip(vx, vy, vw, vh)
            ui.cursor(g[0], g[1], g[2], g[2])
            cv.clip()

    def _cursor_at(self):
        """(x, y, side) of the ring cursor's rect on the glass, or None where
        it is not drawn."""
        if not self._cursor_shown():
            return None
        s = self.size()
        gx, gy = self._to_glass((self.cur[0] - s // 2, self.cur[1] - s // 2))
        vx, vy, vw, vh = self.layout.view
        if not (vx <= gx < vx + vw and vy <= gy < vy + vh):
            return None
        return (gx, gy, max(2, s * self._quarters() // 4))

    def _cursor_shown(self):
        """The ring cursor: the keys', so not under touch, under a popover,
        or while the pointer draws (the pointer is where it draws)."""
        return not self.layout.touch and self.pop is None and not self._down

    def _view(self, cv, th, spec):
        """The view: the drawing on its ground, its frame and thumbs, and a
        shape's outline while it is dragged."""
        lay = self.layout
        d = self.doc
        vx, vy, vw, vh = lay.view
        x, y, w, h, sx, sy, sw, sh, q = spec
        r = lay.radius
        src = self._src
        src[0] = sx
        src[1] = sy
        src[2] = sw
        src[3] = sh
        full = w == vw and h == vh
        if lay.desk:
            cv.rrect(vx, vy, vw, vh, 10, th["panel"])
            hole = self._hole
            hole[0] = x
            hole[1] = y
            hole[2] = w
            hole[3] = h
            cv.shadow(x, y, w, h, 2, 0, 60, 6, 0, 2, hole)
            cv.picture(d.image, x, y, w, h, 0, src)
            cv.rrect(x - 1, y - 1, w + 2, h + 2, 0, None, th["border"])
        else:
            if w < vw or h < vh:
                cv.rrect(vx, vy, vw, vh, r, th["surface_alt"], th["border"])
            cv.picture(d.image, x, y, w, h, r if full else 0, src)
            if full:
                cv.rrect(vx, vy, vw, vh, r, None, th["border"])
            self._thumbs(cv, th, x, y, w, h, sx, sy, sw, sh)
        self._preview(cv)

    # -- the stroke frame ----------------------------------------------------------
    #
    # A frame whose only change is a mark's -- the doc's pixels, a shape's
    # outline, the ring cursor the keys move, the pointer's sprite over them
    # -- repaints the part of the view
    # those changed and nothing else: the page around it, the toolkit's pass
    # included, is the pixels already there. That holds when the canvas keeps
    # them: the last RETAINED_FRAMES frames all painted this page, the same
    # page (`_epoch`, moved by any change but a mark's and by anything the page
    # reads), into this canvas. The view is then painted again inside the
    # union of what those frames and this one changed, by the same calls a
    # whole frame makes, so the pixels are a whole frame's. A recording canvas
    # keeps nothing, and the loop's drawn-frame count is what says the frames
    # were consecutive. In a desk window the WM says instead what the target
    # holds (`retained`, docs/surface_model_v1.md §14.3), and is told what
    # each frame changed (`changed`).

    def _stroke_frame(self, cv, th):
        """Paint this frame as a stroke frame where it may: True when it did
        (the frame is done), False when the whole page must be painted. Either
        way the doc's marks are baked and this frame's change is recorded."""
        self._sf_ran = True
        d = self.doc
        spec = self._spec()
        c = self._chg
        c[0] = c[1] = 1 << 29
        c[2] = c[3] = -(1 << 29)
        dirty = d.take_dirty()
        if dirty is not None:
            cv.rebake(d.image, dirty[0], dirty[1], dirty[2], dirty[3])
            x, y, w, h, sx, sy, sw, sh, q = spec
            dx, dy, dw, dh = dirty
            _grow(c, x + (dx - sx) * q // 4 - 1, y + (dy - sy) * q // 4 - 1,
                  x + ((dx + dw - sx) * q + 3) // 4 + 1, y + ((dy + dh - sy) * q + 3) // 4 + 1)
        pv = self._pv
        _grow(c, pv[0], pv[1], pv[2], pv[3])
        a, b = self._shape0, self._shape1
        if a is not None and b is not None:
            ga = self._to_glass(a)
            gb = self._to_glass(b)
            pv[0] = min(ga[0], gb[0]) - 2
            pv[1] = min(ga[1], gb[1]) - 2
            pv[2] = max(ga[0], gb[0]) + 3
            pv[3] = max(ga[1], gb[1]) + 3
            _grow(c, pv[0], pv[1], pv[2], pv[3])
        else:
            pv[2] = pv[0]
        cu = self._cu
        _grow(c, cu[0], cu[1], cu[2], cu[3])
        g = self._cursor_at()
        if g is not None:
            # the ring reaches five pixels past the rect it is round
            cu[0] = g[0] - 6
            cu[1] = g[1] - 6
            cu[2] = g[0] + g[2] + 6
            cu[3] = g[1] + g[2] + 6
            _grow(c, cu[0], cu[1], cu[2], cu[3])
        else:
            cu[2] = cu[0]
        # The pointer's sprite, drawn over the page after it: where it was
        # and where it is, when it moved. One that reaches past the view is
        # a whole frame.
        sp = self._sp
        x0, y0, x1, y1 = sp[0], sp[1], sp[2], sp[3]
        p = self._surf.pointer()
        if p is not None and p[4]:
            u = self.layout.fs
            sp[0] = p[0] - 1
            sp[1] = p[1] - 1
            sp[2] = p[0] + 8 * u + 1
            sp[3] = p[1] + 13 * u + 1
        else:
            sp[2] = sp[0]
        if sp[0] != x0 or sp[1] != y0 or sp[2] != x1 or sp[3] != y1:
            _grow(c, x0, y0, x1, y1)
            _grow(c, sp[0], sp[1], sp[2], sp[3])
            self._sp_out = (not self._in_view(x0, y0, x1, y1)
                            or not self._in_view(sp[0], sp[1], sp[2], sp[3]))
        else:
            self._sp_out = False
        epoch = self._page_epoch(cv, th, d, spec)
        f = _loop.drawn() if _loop is not None else -1
        ok = self._region(cv, f, epoch, self._said)
        if f >= 0:
            e = self._ring[f % (_RING + 1)]
            e[0] = f
            e[1] = epoch
            e[2] = self._own[0]
            e[3] = self._own[1]
            e[4] = self._own[2]
            e[5] = self._own[3]
            self._ring_cv = cv
        if not ok:
            # A whole frame: until the canvas's every buffer holds this page,
            # the frames after it are asked for now, while nothing is drawn,
            # so the next press is a stroke frame from its first.
            if f >= 0 and self._may(cv) and not self._kept(f + 1, epoch, None):
                self._damage.settle()
            return False
        if c[2] > c[0] and c[3] > c[1]:
            self._clip_r = c
            cv.clip(c[0], c[1], c[2] - c[0], c[3] - c[1])
            self._view(cv, th, spec)
            if g is not None:
                # the toolkit's verbs on this page's context, its frame not
                # begun again: the ring is drawn as a whole frame draws it
                self._ui.resume(cv, th)
                ui.cursor(g[0], g[1], g[2], g[2])
            cv.clip()
            self._clip_r = None
        return True

    def _region(self, cv, f, epoch, held=False):
        """Whether this frame may be a stroke frame; when it may, `_chg`
        becomes the part of the view it repaints: this frame's change and
        those of the frames since the canvas's buffer was last painted -- or,
        where the window said what the target holds (`held`), the part it
        said lags. The frame's own change stays in `_own`, which the ring
        records."""
        o = self._own
        c = self._chg
        o[0] = c[0]
        o[1] = c[1]
        o[2] = c[2]
        o[3] = c[3]
        if f < 0 or self._sp_out or not self._may(cv):
            return False
        if held:
            if self._moved:
                return False
            h = self._word
            _grow(c, h[1], h[2], h[3], h[4])
        elif cv is not self._ring_cv or not self._kept(f, epoch, c):
            return False
        lay = self.layout
        vx, vy, vw, vh = lay.view
        if c[0] < vx:
            c[0] = vx
        if c[1] < vy:
            c[1] = vy
        if c[2] > vx + vw:
            c[2] = vx + vw
        if c[3] > vy + vh:
            c[3] = vy + vh
        # The view's rounded corners blend into what is under them, so a
        # second paint there would not be the first's.
        k = 10 if lay.desk else lay.radius
        if k > 0 and c[2] > c[0] and c[3] > c[1]:
            if ((c[0] < vx + k or c[2] > vx + vw - k)
                    and (c[1] < vy + k or c[3] > vy + vh - k)):
                return False
        return True

    def _in_view(self, x0, y0, x1, y1):
        """Whether rect [x0, y0, x1, y1) is empty or inside the view."""
        if x1 <= x0 or y1 <= y0:
            return True
        vx, vy, vw, vh = self.layout.view
        return vx <= x0 and vy <= y0 and x1 <= vx + vw and y1 <= vy + vh

    def _may(self, cv):
        """Whether the page and the canvas are ones a stroke frame paints."""
        if (self.page is not None or self.pop is not None or self.viewer is not None
                or self.toast is not None or self._touch is None
                or getattr(cv, "begin_surface", None) is not None
                or getattr(cv, "covered", False)):
            return False
        n = getattr(cv, "RETAINED_FRAMES", 0)
        return 1 <= n <= _RING

    def _kept(self, f, epoch, c):
        """Whether the RETAINED_FRAMES frames before frame `f` all painted
        page `epoch`, so the buffer `f` draws into holds it; `c`, when given,
        grows over what those frames changed since that buffer was painted."""
        n = self._ring_cv.RETAINED_FRAMES
        ring = self._ring
        for i in range(1, n + 1):
            e = ring[(f - i) % (_RING + 1)]
            if e[0] != f - i or e[1] != epoch:
                return False
            if c is not None and i < n:
                _grow(c, e[2], e[3], e[4], e[5])
        return True

    def _page_epoch(self, cv, th, d, spec):
        """The page's epoch: moved by any change a mark is not (the damage's
        own count) and by anything the page outside the view reads."""
        g = self._damage.gen
        moved = g != self._gen
        self._gen = g
        cvm = self._sig_is(0, cv)
        moved = self._sig_is(1, self.layout) | moved
        moved = self._sig_is(2, th) | moved
        moved = self._sig_is(3, d) | moved
        moved = self._sig_is(4, self.page) | moved
        moved = self._sig_is(5, self.pop) | moved
        moved = self._sig_is(6, self.viewer) | moved
        moved = self._sig_is(7, self.toast) | moved
        h = (self.tool | self.shape << 4 | (1 if self.shape_fill else 0) << 6
             | self.size_i << 7 | self.color << 10 | (1 if self._unsaved else 0) << 16
             | (1 if d.history else 0) << 17 | (1 if d.future else 0) << 18
             | (1 if self._touch else 0) << 20
             | (1 if self._art.editable() else 0) << 21)
        moved = self._sig_eq(8, h) | moved
        moved = self._sig_eq(9, self.zoom) | moved
        for i in range(9):
            moved = self._sig_eq(10 + i, spec[i]) | moved
        moved = self._sig_eq(19, self._drip_colour()) | moved
        # The page moved; a new canvas is a new epoch too, but where the
        # window said what the target holds, it is only a new target.
        self._moved = moved
        if moved or cvm:
            self._epoch += 1
        return self._epoch

    def _sig_is(self, i, v):
        if self._sig[i] is v:
            return False
        self._sig[i] = v
        return True

    def _sig_eq(self, i, v):
        if self._sig[i] == v:
            return False
        self._sig[i] = v
        return True

    def _thumbs(self, cv, th, x, y, w, h, sx, sy, sw, sh):
        """A panned view's thumbs: where the view stands in the drawing."""
        d = self.doc
        u = self.layout.fs
        ink = th["ink_dim"]
        if sw < d.W:
            tw = w - 18 * u
            cv.rrect(x + 6 * u + sx * tw // d.W, y + h - 6 * u, max(6, sw * tw // d.W), 3 * u,
                     1 * u, ink)
        if sh < d.H:
            tw = h - 18 * u
            cv.rrect(x + w - 6 * u, y + 6 * u + sy * tw // d.H, 3 * u, max(6, sh * tw // d.H),
                     1 * u, ink)

    def _preview(self, cv):
        a, b = self._shape0, self._shape1
        if a is None or b is None:
            return
        ga = self._to_glass(a)
        gb = self._to_glass(b)
        vx, vy, vw, vh = self.layout.view
        k = self._clip_r
        if k is not None:
            x0, y0 = max(vx, k[0]), max(vy, k[1])
            cv.clip(x0, y0, min(vx + vw, k[2]) - x0, min(vy + vh, k[3]) - y0)
        else:
            cv.clip(vx, vy, vw, vh)
        c = self.color
        if self.shape == 0:
            cv.line(ga[0], ga[1], gb[0], gb[1], c)
        else:
            x, y = min(ga[0], gb[0]), min(ga[1], gb[1])
            w, h = abs(ga[0] - gb[0]) + 1, abs(ga[1] - gb[1]) + 1
            if self.shape == 1:
                (cv.rect if self.shape_fill else cv.rectb)(x, y, w, h, c)
            else:
                (cv.oval if self.shape_fill else cv.ovalb)(x, y, w, h, c)
        if k is not None:
            cv.clip(k[0], k[1], k[2] - k[0], k[3] - k[1])
        else:
            cv.clip()

    def _two_tone(self, cv, th, x, y, w, h):
        """The colour in use's edge: a line of ink, then one of surface
        within, never a palette colour."""
        cv.rectb(x, y, w, h, th["ink"])
        cv.rectb(x + 1, y + 1, w - 2, h - 2, th["surface"])

    def _rail(self, cv, th):
        lay = self.layout
        u = lay.fs
        ui.at(*lay.colour)
        if ui.tool("", "", {"id": "Colour in use", "current": lay.touch and self.pop == "colours"}):
            self._open("colours")
        x, y, w, h = lay.colour
        cv.rect(x, y, w, h, self.color)
        self._two_tone(cv, th, x, y, w, h)
        if lay.swatch_page is not None:
            x0, y0, s = lay.swatch_page
            first = (self.color // 16) * 16
            for i in range(16):
                sx, sy = x0 + (i % 2) * s, y0 + (i // 2) * s
                cv.rect(sx, sy, s, s, first + i)
                if first + i == self.color:
                    self._two_tone(cv, th, sx, sy, s, s)
            cv.rectb(x0, y0, 2 * s, 8 * s, th["border"])
        ui.at(*lay.brush)
        if ui.tool("", "", {"id": "Brush", "current": self.pop == "brush"}):
            self._open("brush")
        x, y, w, h = lay.brush
        if lay.touch:
            cv.rrect(x, y, w, h, 6 * u, None, th["border"])
        ds = min(self.size(), 8) * u
        cv.rrect(x + (w - ds) // 2, y + (h - ds) // 2, ds, ds, ds // 2, th["ink"])
        ui.at(*lay.more)
        if ui.tool("more", "", {"id": "More", "current": self.pop == "more"}):
            self._open("more")

    def _hints(self):
        lay = self.layout
        if lay.hints is None or lay.touch:
            return
        x, y, w, h = lay.hints
        u = lay.fs
        if self.pop is not None:
            ui.at(x, y, w, h)
            ui.hints(("back", "Close"))
            return
        lead = TOOLS[self.tool][2] + (" ·" if ui.has_symbols() else " -")
        ui.face("caption")
        lw = ui.text_size(lead)[0]
        ui.face()
        ui.at(x + 8 * u, y, lw + 2 * u, h)
        ui.label(lead, {"face": "caption"})
        ui.at(x + 8 * u + lw, y, w - 8 * u - lw, h)
        ui.hints(("confirm", "Draw", "C", "Colours", "M", "More"))

    # -- the desk's row, panel and foot ----------------------------------------------

    def _desk_row(self, cv, th):
        lay = self.layout
        x, y, w, h = lay.row
        d = self.doc
        for bx, icon, verb, word, key, off in (
                (0, "new", "new", "New drawing", "N", False),
                (40, "folder_open", "open", "Open", "O", False),
                (96, "undo", "undo", "Undo", "Ctrl Z", not d.history),
                (136, "redo", "redo", "Redo", "Ctrl Y", not d.future)):
            ui.at(x + bx, y, h, h)
            if ui.tool(icon, word + "\t" + key, {"id": "Row " + verb, "disabled": off}):
                self._verb(verb)
        ui.at(x + 86, y + 6, 1, h - 12)
        ui.rule()
        xr = x + w - h
        ui.at(xr, y, h, h)
        if ui.tool("more", "More\tM", {"id": "Row more", "current": self.pop == "more"}):
            self._open("more")
        xr -= 8
        ui.face("caption")
        verbs = (("Show", "maximize", "show"), ("Wallpaper", "wallpaper", "wallpaper"),
                 ("Put in a story", "storybook", "story"))
        widths = [ui.text_size(v[0])[0] + 48 for v in verbs]
        ui.face()
        for i in range(len(verbs) - 1, -1, -1):
            label, icon, verb = verbs[i]
            xr -= widths[i]
            ui.at(xr, y + (h - 32) // 2, widths[i], 32)
            ui.icon(icon)
            if ui.button(label, "normal", {"id": "Row " + verb}):
                self._verb(verb)
            xr -= 8
        segs = [_zoom_word(z) if z else "Fit" for z in ZOOM_SEG]
        cur = ZOOM_SEG.index(self.zoom) if self.zoom in ZOOM_SEG else 0
        sx = x + 180
        ui.at(sx, y, xr - 8 - sx, h)
        got = ui.seg("", segs, cur, {"id": "Zoom"})
        if got != cur and 0 <= got < len(ZOOM_SEG):
            self._zoom_to(ZOOM_SEG[got])
        ui.at(*lay.rule)
        ui.rule()

    def _desk_panel(self, cv, th):
        lay = self.layout
        px, py, pw, ph = lay.panel
        ui.at(px, py, pw, 20)
        ui.label("Colour", {"tone": "dim", "face": "caption"})
        x, y, w, h = lay.colour
        cv.rect(x, y, w, h, self.color)
        self._two_tone(cv, th, x, y, w, h)
        ui.at(x + w + 12, y, pw - w - 12, h)
        ui.label(colour_name(self.color, _dot()))
        x0, y0, s = lay.swatch_page
        for i in range(64):
            sx, sy = x0 + (i % 8) * s, y0 + (i // 8) * s
            cv.rect(sx, sy, s - 2, s - 2, i)
            if i == 7 or i == 48:
                cv.rectb(sx, sy, s - 2, s - 2, th["ink_dim"])
            if i == self.color:
                self._two_tone(cv, th, sx, sy, s - 2, s - 2)
        ty = y0 + 8 * s + 6
        ui.at(px, ty, pw, 20)
        word = TOOLS[self.tool][2]
        ui.label(word, {"tone": "dim", "face": "caption"})
        sizes = self.sizes()
        ui.at(px - 10, ty + 22, pw + 30, 30)
        got = ui.seg("Size", [str(v) for v in sizes], self.size_i, {"id": "Size"})
        if got != self.size_i and 0 <= got < len(sizes):
            self.size_i = got
        if self.tool == SHAPE:
            ui.at(px - 10, ty + 56, pw + 30, 30)
            got = ui.seg("Shape", [s[1] for s in SHAPES], self.shape, {"id": "Shape"})
            if got != self.shape and 0 <= got < len(SHAPES):
                self.shape = got
            ui.at(px - 10, ty + 90, pw + 30, 30)
            f = 1 if self.shape_fill else 0
            got = ui.seg("Fill", ("Outline", "Filled"), f, {"id": "Fill"})
            if got != f and got in (0, 1):
                self.shape_fill = got == 1

    def _desk_foot(self, cv, th):
        lay = self.layout
        fx, fy, fw, fh = lay.foot
        ui.at(fx + 16, fy, fw - 32, 1)
        ui.rule()
        hints = ("Z", "Zoom", "C", "Colours", "Ctrl Z", "Undo")
        hw = ui.hints_width(hints)
        ui.at(fx + 26, fy + 8, max(0, fw - 52 - hw), 30)
        ui.label(self._status(), {"tone": "dim", "face": "caption"})
        if hw:
            ui.at(fx + fw - 16 - hw, fy + 8, hw, 30)
            ui.hints(hints)

    def _status(self):
        """The desk foot's sentence: the tool, its size, the zoom, the save."""
        dot = _dot()
        q = self._quarters()
        z = ("Fit, " + _zoom_word(q)) if self.zoom == FIT else _zoom_word(q)
        state = "saved" if not self._unsaved else "changed"
        if not self._art.editable():
            state = "only to look at"
        return TOOLS[self.tool][2] + dot + "size %d" % self.size() + dot + z + dot + state

    # -- popovers ---------------------------------------------------------------------

    def _hang(self, w):
        """Hang the rail's popover inside the drawing's frame: its right 2 px
        in from the frame's, its bottom on the frame's bottom."""
        vx, vy, vw, vh = self.layout.view
        u = self.layout.fs
        ui.at(vx + vw - 2 * u - w, vy + vh + 4 * u, w, 1)

    def _colours_pop(self, cv, th):
        lay = self.layout
        u = lay.fs
        if lay.touch:
            vx, vy, vw, vh = lay.view
            cols, s, first = 7, 32 * u, (self.color // 28) * 28 if self.color < 56 else 56
            w = vw
            ui.at(vx, vy + vh + 4 * u, vw, 1)
        else:
            cols, s, first = 8, 18 * u, 0
            w = 162 * u
            self._hang(w)
        shown_ = ui.popover("Colours", w)
        n = 64 - first if not lay.touch else min(28, 64 - first)
        rows = (n + cols - 1) // cols
        head = (14 if lay.touch else 20) * u
        foot = 10 * u if lay.touch else 0
        x, y, pw, ph, _st = ui.part("Swatches", w, head + rows * s + foot, {"disabled": True})
        if lay.touch:
            pages = ("1\u201328", "29\u201356", "57\u201364") if ui.has_symbols() \
                else ("1-28", "29-56", "57-64")
            cur = first // 28
            got = ui.seg("", pages, cur, {"id": "Colour page"})
            if got != cur and 0 <= got < 3:
                self.color = got * 28
        ui.popover()
        if not shown_:
            self.pop = None
            self._damage.all()
            return
        # the head and the grid over the part's well, on the card's ground
        cv.rect(x, y, pw, ph, th["surface"])
        gx = x + (pw - cols * s) // 2
        gy = y + head
        ui.at(gx, y, pw // 2, head - 4 * u)
        ui.label("Colour", {"face": "title"})
        for i in range(n):
            c = first + i
            sx, sy = gx + (i % cols) * s, gy + (i // cols) * s
            cv.rect(sx, sy, s - 2 * u, s - 2 * u, c)
            if c == self.color:
                self._two_tone(cv, th, sx, sy, s - 2 * u, s - 2 * u)
        cv.rectb(gx - u, gy - u, cols * s, rows * s, th["border"])
        self._pop_rect = (gx, gy, cols, rows, s, first)
        on = self.color
        if not lay.touch:
            on = self.swatch
            i = on - first
            if 0 <= i < n:
                ui.cursor(gx + (i % cols) * s, gy + (i // cols) * s, s - 2 * u, s - 2 * u)
        word = colour_name(on, _dot())
        ui.face("caption")
        tw = ui.text_size(word)[0]
        ui.face()
        ui.at(gx + cols * s - 2 * u - tw, y, tw, head - 4 * u)
        ui.label(word, {"tone": "dim", "face": "caption"})

    def _brush_pop(self):
        lay = self.layout
        u = lay.fs
        w = 194 * u
        self._hang(w)
        shown_ = ui.popover("Brush", w)
        word = TOOLS[self.tool][2]
        ui.label(word, {"face": "title"})
        sizes = self.sizes()
        got = ui.seg("Size", [str(v) for v in sizes], self.size_i, {"id": "Size"})
        if got != self.size_i and 0 <= got < len(sizes):
            self.size_i = got
        if self.tool == SHAPE:
            got = ui.seg("Shape", [s[1] for s in SHAPES], self.shape, {"id": "Shape"})
            if got != self.shape and 0 <= got < len(SHAPES):
                self.shape = got
            f = 1 if self.shape_fill else 0
            got = ui.seg("Fill", ("Outline", "Filled"), f, {"id": "Fill"})
            if got != f and got in (0, 1):
                self.shape_fill = got == 1
        ui.popover()
        if not shown_:
            self.pop = None
            self._damage.all()

    def _more_rows(self):
        """More's rows: (verb, label, icon, key), "-" a rule between parts."""
        d = self._doc
        rows = []
        if self.viewer is None:
            rows.append(("new", "New drawing", "new", "N"))
            if self.layout.desk:
                rows.append(("new_game", "New game-size drawing (320×240)"
                             if ui.has_symbols() else "New game-size drawing (320x240)",
                             "new", ""))
        rows.append(("open", "Open…" if ui.has_symbols() else "Open...",
                     "folder_open", "O"))
        rows.append("-")
        if self.viewer is None and not self._art.editable():
            rows.append(("copy", "Copy to My Art", "dup", ""))
        rows.append(("wallpaper", "Use as wallpaper", "wallpaper", "W"))
        if self.viewer is None and d is not None and (d.W, d.H) == (GAME_W, GAME_H):
            rows.append(("game", "Put in a game…" if ui.has_symbols() else "Put in a game...",
                         "export", "G"))
        if self.viewer is not None or self.layout.desk:
            rows.append(("story", "Put in a story…" if ui.has_symbols()
                         else "Put in a story...", "storybook", "T"))
        rows.append(("show", "Show", "maximize", "V"))
        return rows

    def _more(self):
        lay = self.layout
        u = lay.fs
        rows = self._more_rows()
        if lay.desk:
            x, y, w, h = lay.row
            ui.at(x + w - h, y, h, h)
            width = 300
        elif self.viewer is not None:
            width = 194 * u
            ui.at(lay.cx + lay.cw - 6 * u - width, lay.cy - 4 * u, width, 1)
        else:
            width = 182 * u
            self._hang(width)
        shown_ = ui.popover("More", width)
        part = []
        parts = [part]
        for r in rows:
            if r == "-":
                part = []
                parts.append(part)
            else:
                part.append(r)
        pick = None
        keys = ui.hints_width(("back", "Back")) > 0
        for p in parts:
            if not p:
                continue
            got = ui.menu("", [r[1] + ("\t" + r[3] if r[3] and keys else "") for r in p],
                          [r[2] for r in p])
            if got >= 0:
                pick = p[got][0]
        ui.popover()
        if pick is not None:
            self.pop = None
            self._ui.reset()
            self._verb(pick)
        elif not shown_:
            self.pop = None
            self._damage.all()

    # -- the pages ----------------------------------------------------------------------

    def _game_page(self, cv):
        lay = self.layout
        self._lend("Put in a game", (), (), True)
        if self.game is None:
            self.page = None
            return
        got = self.game.draw(cv, lay.page, lay.desk, lay.fs)
        if got is not None:
            self.page = None
            if got:
                self._say(shown(self.game.name) + " is the background of " + got)
            else:
                self._say(self._art.copies.last_error or "Can't put it there", "warn", True)
            self.game = None

    def _story_page(self, cv):
        lay = self.layout
        self._lend("Put in a story", (), (), True)
        if self.story is None:
            self.page = None
            return
        got = self.story.draw(cv, lay.page, lay.desk, lay.fs)
        if got is not None:
            self.page = None
            title, slide, undo = got
            dot = _dot()
            if slide:
                self._say("Added as slide %d of %s%sUndo" % (slide, title, dot),
                          undo=undo)
            else:
                self._say("Can't add it to " + title, "warn", True)
            self.story = None

    def _view_page(self, cv, th):
        lay = self.layout
        v = self.viewer
        q = v.quarters()
        got = self._lend(shown(v.name), (_zoom_word(q), "more"), (False, False))
        if got == 0:
            self._verb("zoom")
        elif got == 1:
            self._open("more")
        if self.pop is None:
            ui.focus("nowhere")
        u = lay.fs
        cx, cy, cw, ch = lay.full
        hint_h = 22 * u if not lay.touch else 0
        if v.zoom == FIT:
            bmp = v.fit_bitmap()
            if bmp is not None:
                x = cx + (cw - bmp.w) // 2
                y = cy + 6 * u
                cv.shadow(x, y, bmp.w, bmp.h, 2, 0, 60, 6, 0, 2, (x, y, bmp.w, bmp.h, 0))
                cv.picture(bmp, x, y, bmp.w, bmp.h)
                cv.rrect(x - 1, y - 1, bmp.w + 2, bmp.h + 2, 0, None, th["border"])
                cap = "%d × %d, made on a desk%sshown at %s" % (
                    v.W, v.H, _dot(), _zoom_word(q)) if ui.has_symbols() else \
                    "%dx%d, made on a desk - shown at %s" % (v.W, v.H, _zoom_word(q))
                ui.at(cx, y + bmp.h + 6 * u, cw, 18 * u)
                ui.label(cap, {"tone": "dim", "face": "caption", "align": "center"})
        else:
            v.view = (cx, cy, cw, ch - hint_h)
            bmp, k = v.window()
            if bmp is not None:
                cv.picture(bmp, cx, cy, bmp.w * k, bmp.h * k)
            mini = v.mini()
            if mini is not None:
                mx = cx + cw - 10 * u - mini.w
                my = cy + 6 * u
                cv.rect(mx - 2 * u, my - 2 * u, mini.w + 4 * u, mini.h + 4 * u, th["surface"])
                cv.picture(mini, mx, my, mini.w, mini.h)
                fx, fy, fw, fh = v.frame()
                cv.rectb(mx + fx // 16, my + fy // 16, max(2, fw // 16), max(2, fh // 16),
                         th["accent"])
                cv.rectb(mx - 2 * u, my - 2 * u, mini.w + 4 * u, mini.h + 4 * u, th["border"])
        if not lay.touch and lay.hints is not None:
            x, y, w, h = lay.hints
            if self.pop is not None:
                ui.at(x, y, w, h)
                ui.hints(("confirm", "Use", "back", "Close"))
            else:
                lead = "View only"
                ui.face("caption")
                lw = ui.text_size(lead)[0]
                ui.face()
                ui.at(x + 8 * u, y, lw + 2 * u, h)
                ui.label(lead, {"face": "caption"})
                ui.at(x + 8 * u + lw + 8 * u, y, w - 16 * u - lw, h)
                if v.zoom == FIT:
                    ui.hints(("Z", "Zoom", "M", "More", "back", "Back"))
                else:
                    ui.hints(("move", "Pan", "Z", "Zoom", "M", "More"))
        if self.pop == "more":
            self._more()

    def _show(self, cv, th):
        """Show: the drawing alone on the whole surface, black around it;
        any key or tap returns."""
        cv.cls(self.names["black"])
        v = self.viewer
        if v is not None:
            w = cv.w
            h = v.H * w // v.W
            if h > cv.h:
                h = cv.h
                w = v.W * h // v.H
            bmp = v.show_bitmap(w, h)
            if bmp is not None:
                cv.picture(bmp, (cv.w - w) // 2, (cv.h - h) // 2, w, h)
            return
        d = self.doc
        dirty = d.take_dirty()
        if dirty is not None:
            cv.rebake(d.image, *dirty)
        w = cv.w
        h = d.H * w // d.W
        if h > cv.h:
            h = cv.h
            w = d.W * h // d.H
        cv.picture(d.image, (cv.w - w) // 2, (cv.h - h) // 2, w, h)

    def _draw_toast(self):
        lay = self.layout
        text, icon, _until, _undo = self.toast
        if self.viewer is not None and self.viewer.zoom == FIT:
            bmp = self.viewer.fit_bitmap()
            u = lay.fs
            cx, cy, cw, ch = lay.full
            h = bmp.h if bmp is not None else ch // 2
            ui.at(cx, cy + 6 * u, cw, h - 10 * u)
        elif lay.desk:
            ui.at(*lay.view)
        else:
            vx, vy, vw, vh = lay.view
            ui.at(vx, vy, vw, vh - 9 * lay.fs)
        ui.toast(text, icon)
