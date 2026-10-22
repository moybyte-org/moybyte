# Map (grep -n a name to jump there):
#   MapPage              the handheld Map tab on the verbs
#   MapPage.draw         the view, the brushes and the tile, and the foot's tools
#   MapPage.palette_tap  a tap on a brush
#   MapPage.point        a pointer sample: the verbs' taps
"""The handheld Editor's Map tab on the public UI API
(docs/media/look_2026-10/EDITOR_LAYOUT.md's `map` pictures): the map at its
zoom from the content rect's top left, beside it the brushes a page at a
time (‹ 1 / 4 ›), the brush's tile, the cell last touched and that a hold
picks, and under both the tools -- pen, box, fill, select, erase -- then
the brush's size, the zoom and resize. While Select is the tool, Copy, Cut
and Paste stand where the brushes were.

The view is `MapEditorUI.draw_view`'s and its gestures `MapEditorUI`'s (a
tap paints, a drag pans, a still hold picks); the keys stay the tab's own
(the arrows pan, A steps the zoom). The frame begins with no stop holding
focus, and the verbs take the taps. Resize opens a popover whose keys are
its own while it is open."""

from array import array

try:
    import ui
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime import ui

# The foot's tools: (icon, word, the editor's tool, erasing).
TOOLS = (("pencil", "Pen", "stamp", False), ("rect", "Box", "rect", False),
         ("bucket", "Fill", "flood", False), ("select", "Select", "select", False),
         ("eraser", "Erase", "stamp", True))
CELL = 22                        # a brush's pitch; the tile 20 inside it
TOOL = 24                        # a foot tool's cell


def times(a, b=None):
    """`a×b`, or `a×` alone, in the print font's sign where it has one."""
    x = "×" if ui.has_symbols() else "x"
    return "%d%s%s" % (a, x, "" if b is None else str(b))


_CHECKERS = {}                   # (w, h, s) packed in one int -> the dark squares


def checker(cv, x, y, w, h, s, dark, light):
    """Transparency's grey checker over (x, y, w, h) in squares of `s`, the
    first row's first square light (the Sprite tab's grid and swatch, the
    Map's and the Scene's brushes): the light ground, then every dark square
    in one `fill_rects` of a quad list kept per size."""
    cv.rect(x, y, w, h, light)
    if w <= 0 or h <= 0 or s <= 0:
        return
    key = (w & 0xFFF) | ((h & 0xFFF) << 12) | ((s & 0x3F) << 24)
    quads = _CHECKERS.get(key)
    if quads is None:
        q = []
        for yy in range(0, h, s):
            for xx in range(s if (yy // s) & 1 == 0 else 0, w, 2 * s):
                q += (xx, yy, min(s, w - xx), min(s, h - yy), 0)
        if len(_CHECKERS) >= 32:
            _CHECKERS.clear()
        quads = _CHECKERS[key] = array("h", q)
    cv.fill_rects(quads, -1, x, y, dark)


def _width(text):
    ui.face("ui")
    w = ui.text_size(text)[0]
    ui.face()
    return w


class MapPage:
    """The Map tab over `mu`, the workstation's `MapEditorUI`; `host` is
    the frame's `AppHost`."""

    def __init__(self, mu, host):
        self.mu = mu
        self.host = host
        self.pop = None                  # "resize" while its popover is open

    def _greys(self):
        return self.mu.ws.look.live.fixed[:2]

    # -- drawing ------------------------------------------------------------------

    def draw(self, cv, th, rect, desk):
        mu = self.mu
        host = self.host
        host.begin(cv, th, rect, desk, False, elsewhere=self.pop is None)
        ui.at(*rect)
        ui.page("")
        me = mu.mapedit
        ws = mu.ws
        if me is None or ws.project.sheet is None or ws.project.tilemap is None:
            ui.at(*rect)
            ui.empty("map", "No map", ("This cart has no sprites to paint with",))
            ui.end()
            return
        lay = mu.layout
        x, y, w, h = rect
        mu.draw_view(cv)
        px = lay.tp_x0
        if mu.map_tool == "select":
            self._region(me, px, y + 6)
        else:
            self._brushes(cv, th, me, px, y + 6)
        self._foot(me, x, y + h - 26, w)
        if self.pop == "resize":
            self._resize(ws.project.tilemap)
        ui.end()

    def _brushes(self, cv, th, me, px, y):
        mu = self.mu
        sheet = mu.ws.project.sheet
        ids = mu._map_palette_ids()
        dark, light = self._greys()
        for k in range(len(ids)):
            tx = px + (k % 4) * CELL
            ty = y + (k // 4) * CELL
            s = CELL - 2
            checker(cv, tx, ty, s, s, 4, dark, light)
            if 0 <= ids[k] < sheet.count:
                ox, oy = sheet.tile_origin(ids[k])
                d = 2 * sheet.TILE
                cv.sspr(sheet, ox, oy, sheet.TILE, sheet.TILE,
                        tx + (s - d) // 2, ty + (s - d) // 2, d, d, -1)
        n = me.n
        if n in ids:
            k = ids.index(n)
            ui.cursor(px + (k % 4) * CELL, y + (k // 4) * CELL, CELL - 2, CELL - 2)
        py = y + 4 * CELL + 4
        pages = max(1, (sheet.count + 15) // 16)
        cur = mu.map_page // 16
        ui.at(px - 2, py - 2, 20, 20)
        if ui.tool("back", "", {"id": "Previous brushes", "disabled": cur == 0}):
            mu.map_page = max(0, mu.map_page - 16)
        ui.at(px + 18, py, 52, 16)
        ui.label("%d / %d" % (cur + 1, pages), {"align": "center"})
        ui.at(px + 70, py - 2, 20, 20)
        if ui.tool("forward", "", {"id": "Next brushes", "disabled": cur + 1 >= pages}):
            mu.map_page = min((pages - 1) * 16, mu.map_page + 16)
        ui.at(px, py + 24, 98, 16)
        ui.label("Tile %d" % n if n >= 0 else "Sky")
        cell = mu._hover_cell
        ui.at(px, py + 40, 98, 16)
        ui.label("Cell %d, %d" % cell if cell is not None else "Tap to paint", {"tone": "dim"})
        ui.at(px, py + 56, 98, 16)
        ui.label("Hold: pick", {"tone": "dim"})

    def _region(self, me, px, y):
        """Select's verbs where the brushes stand."""
        mu = self.mu
        for k, (icon, word) in enumerate((("copy", "Copy"), ("cut", "Cut"),
                                          ("paste", "Paste"))):
            off = not me.has_clip if word == "Paste" else me.sel is None
            ui.at(px, y + k * 26, 88, 22)
            ui.icon(icon)
            if ui.button(word, "normal", {"disabled": off}):
                if word == "Copy":
                    me.copy_selection()
                elif word == "Cut":
                    me.cut_selection()
                else:
                    mu._sel_paste_default(me)

    def _foot(self, me, x, y, w):
        mu = self.mu
        ui.at(x, y - 1, w, 1)
        ui.rule()
        for i, (icon, word, tool, erase) in enumerate(TOOLS):
            on = mu.map_tool == tool and (mu.map_erase == erase or tool != "stamp")
            ui.at(x + 2 + i * TOOL, y + 1, TOOL, TOOL)
            if ui.tool(icon, "", {"id": "Tool " + word, "current": on}):
                mu.map_tool = tool
                mu.map_erase = erase
                mu._map_rect = None
                mu._map_sel = None
        cx = x + 2 + len(TOOLS) * TOOL + 4
        ui.at(cx, y + 6, 1, 14)
        ui.rule()
        cx += 5
        size = times(me.size, me.size)
        cw = _width(size) + 30
        ui.at(cx, y + 3, cw, 20)
        ui.icon("brush")
        if ui.button(size, "normal", {"id": "Brush size"}):
            me.cycle_size()
        cx += cw + 4
        zoom = self.zoom_word()
        zw = _width(zoom) + 30
        ui.at(cx, y + 3, zw, 20)
        ui.icon("search")
        if ui.button(zoom, "normal", {"id": "Zoom"}):
            mu._map_cycle_zoom()
        ui.at(x + w - 2 - TOOL, y + 1, TOOL, TOOL)
        if ui.tool("resize", "", {"id": "Resize", "current": self.pop == "resize"}):
            self.pop = None if self.pop == "resize" else "resize"

    def zoom_word(self):
        """The zoom chip's word: the tile's scale, or Fit for the overview."""
        mu = self.mu
        if mu._mv_overview():
            return "Fit"
        return times(max(1, mu._mv_metrics()[2] // 8))

    def _resize(self, tm):
        box = self.host.box("Resize")
        if box is not None:
            ui.at(*box)
        if not ui.popover("Resize popover", 160):
            self.pop = None
        ui.info("Size", "%d %s %d" % (tm.w, "×" if ui.has_symbols() else "x", tm.h))
        for word, dw, dh in (("Wider", 1, 0), ("Narrower", -1, 0),
                             ("Taller", 0, 1), ("Shorter", 0, -1)):
            if ui.button(word):
                self.mu._map_resize(dw, dh)
        ui.popover()

    # -- input ----------------------------------------------------------------------

    def palette_tap(self, px, py):
        """A tap on a brush picks it, the pen with it. Whether it was one."""
        mu = self.mu
        lay = mu.layout
        me = mu.mapedit
        if me is None or mu.map_tool == "select":
            return False
        x0, y0 = lay.tp_x0, lay.tp_y0
        if not (x0 <= px < x0 + 4 * CELL and y0 <= py < y0 + 4 * CELL):
            return False
        k = (py - y0) // CELL * 4 + (px - x0) // CELL
        ids = mu._map_palette_ids()
        if 0 <= k < len(ids):
            me.n = ids[k]
            if mu.map_erase:
                mu.map_erase = False
        return True

    def keys(self, i):
        """A frame's keys while the resize popover is open: the engine's.
        Whether the page owes a frame."""
        return self.host.keys(i)

    def point(self, px, py, click, down):
        """A pointer sample for the verbs. Whether the page owes a frame."""
        return self.host.point(px, py, click, down)

    def owed(self):
        """Whether a tap or a rest frame is still to be drawn."""
        h = self.host
        return h.owed or h.tap != 0
