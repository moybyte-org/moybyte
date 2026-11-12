# Map (grep -n a name to jump there):
#   _zoom_word                    a zoom as the bar and the seg say it
#   PaintAppLayout                   the rects: a handheld's with keys, one under touch, the desk's
#   PaintAppLayer                 the Paint app: the canvas, its tools and its pages
#   PaintAppLayer.handle_input    the keys: the tools, the cursor, the verbs
#   PaintAppLayer.handle_pointer  a tap or a drag: on the drawing, else the verbs'
#   PaintAppLayer.draw            the frame: the page in hand
#   PaintAppLayer.retained        the WM's word on a window draw's target (and `changed`)
#   -- Viewer and the draw half   paint_viewer's Viewer; paint_draw's PaintDraw, the first base
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

import ui
from layout_base import bar_rows, content_of, metric_class, window_foot, DESK
from artwork import ArtworkService, PaintDocument, DESK_W, DESK_H, GAME_W, GAME_H
from ticks import _ticks_ms, _ticks_diff
from drawing_pages import GamePage, StoryPage, shown, stories_here


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


# Viewer and the draw half live in modules of their own (paint_viewer,
# paint_draw), which take this module's constants at their foot: imported
# here, after those are bound.
from paint_viewer import Viewer
from paint_draw import PaintDraw


class PaintAppLayer(PaintDraw):
    """The Paint app: the canvas, its tools and colours, More's verbs, and a
    desk's drawing viewed on a handheld."""

    id = "artwork"
    domain = "system"
    TITLE = "Paint"
    # The shell roles this app uses (runtime/shell/app_context.py): its document
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
        old = self._doc
        self._doc = d
        if old is not None and old is not d:
            old.release()             # its bakes went back first (_return_bakes)

    def _pixels_for(self, w, h):
        """The document's own pixels at w x h, minted at that size where it
        is another: what the open drawing is read straight into."""
        d = self._doc
        if d is None or d.W != w or d.H != h:
            self.doc = d = PaintDocument(w, h)
        return d.pix

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
        loaded = art.load(self._pixels_for)
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
        dx = inp.steps("right") - inp.steps("left")
        dy = inp.steps("down") - inp.steps("up")
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
