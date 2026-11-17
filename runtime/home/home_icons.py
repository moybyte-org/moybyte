# Map (grep -n a name to jump there):
#   IconHome                     the icons arrangement: tiles that scroll down, a caption band
#   IconHome._lay_cells          its geometry on a glass
#   IconHome.nav2d               tile navigation
#   IconHome._draw_caption_band  the handheld's caption: the focused cart and its line
"""The home's ICONS arrangement (docs/history/look_2026-10.md step 33), the
layout's `home: icons`: each cart's icon -- its cover's reduction -- in a
tile, as many columns as the glass holds across, scrolling down. On the
handheld the focused cart is named in one caption band at the grid's foot;
on the desk every tile is named under it. A `Launcher` (home_grid.py): the
same selection model over a grid that scrolls down.
"""

import ui as _ui
import ui_draw as _ui_draw
import layout_base as _lb
from home_grid import (_Cells, _cover_div_for, _COVER_HALF, _dot_top, _GROW, _hint_strip,
                       _ICONS_DK, _ICONS_HH, Launcher, _NO_KEYS, PSEUDO_TILE_TYPES, _WIDE)
_in = _ui.rect_in   # one hit-test (ui.rect_in)


class IconHome(Launcher):
    """The home's icons arrangement (docs/history/look_2026-10.md step 33): each
    cart's icon -- its cover's reduction -- in a tile with the card radius,
    as many columns as the glass holds across, scrolling down; focus grows
    a tile and rings it flat, as the shelf's does. On the handheld no tile
    carries a label: the focused cart's name and its line sit in one
    caption band at the grid's foot, over the hint strip, which clips the
    top of the next row so the grid reads as scrolling. On the desk every
    tile is named in full under it, the focused one bold."""

    arrangement = "icons"
    horizontal = False

    def _lay_cells(self, lay, keys, desk, depth):
        fs = lay.fs
        hints = _hint_strip(lay, keys, desk)
        if desk:
            u = _lb.render_scale()
            t, gap, room, top, base = (v // u for v in _ICONS_DK)
            margin = 16 // u
            caption = None
            band = (lay.cx, lay.cy, lay.cw, hints[1] - lay.cy)
            title_dy = t + base - _ui_draw.font()[3] * fs
        else:
            narrow, wide, gap, room, top, cap = (v * fs for v in _ICONS_HH)
            t = wide if lay.cw >= _WIDE * fs else narrow
            margin = 16 * fs
            caption = (lay.cx, hints[1] - cap, lay.cw, cap)
            band = (lay.cx, lay.cy, lay.cw, caption[1] - lay.cy)
            title_dy = 0
        p = t + gap
        cols = max(1, (lay.cw - margin) // p)
        py = t + room
        return _Cells(t, depth[1],
                      lay.cx + (lay.cw - cols * p) // 2 + gap // 2, lay.cy + top, p, py,
                      max(1, (band[3] - top - 1) // py + 1), cols, band, hints, caption,
                      title_dy, p, _GROW[0], desk)

    def cell_of(self, i):
        cols = self._cells().cols
        return i // cols, i % cols

    def index_of(self, row, col):
        return row * self._cells().cols + col

    def nav2d(self, dx, dy):
        """Left and right step a tile, up and down a row, clamped within the
        list; the scroll follows."""
        n = len(self.items)
        if not n:
            return
        cols = self._cells().cols
        if dx:
            row, col = self.cell_of(self.sel)
            self.sel = self.index_of(row, max(0, min(cols - 1, col + dx)))
        elif dy:
            self.sel += dy * cols
        self.sel = max(0, min(n - 1, self.sel))
        self._scroll_to_sel()

    def _extent(self):
        c = self._cells()
        n = len(self.items)
        if n <= 0:
            return 0
        top = c.y0 - c.band[1]
        return 2 * top + self.cell_of(n - 1)[0] * c.py + c.side + (c.py - c.side
                                                                     if c.desk else 0)

    def _span(self, i):
        c = self._cells()
        top = c.y0 - c.band[1]
        return (self.cell_of(i)[0] * c.py,
                2 * top + c.side + (c.py - c.side if c.desk else 0))

    def tile_at(self, px, py):
        c = self._cells()
        if not _in(px, py, c.band):
            return None
        for i, r in self._visible():
            if _in(px, py, (r[0] - (c.px - c.side) // 2, r[1], c.px,
                            c.py if c.desk else c.side)):
                return i
        return None

    def _fade(self, cv, c):
        return

    def _face_div(self, n):
        """A handheld tile's picture is its cover's half decode."""
        return _cover_div_for(n) if self._cells().desk else _COVER_HALF

    def _titled(self, c):
        """On the desk every tile is named under it; on the handheld the
        focused one's name is the caption band's."""
        return c.desk

    def cover_specs(self):
        return [(it, _COVER_HALF) for it in self.items
                if it.get("path") and it.get("type") not in PSEUDO_TILE_TYPES]

    def draw(self, cv, sheet_for=None, key=_NO_KEYS, over=None, beside=None):
        end = self.band_pass(cv, sheet_for, None, key, True, over, beside)
        c = self._cells()
        if c.caption is not None:
            self._draw_caption_band(cv, c)
        return end

    def _draw_caption_band(self, cv, c):
        """The handheld's caption band: on the home's ground under a
        hairline, the focused cart's name in the `title` face from the first
        column's left, its metadata after it in the dim ink, the items apart
        by a dot."""
        th = self.theme or {}
        fs = self.layout.fs
        x, y, w, h = c.caption
        cv.rect(x, y, w, h, self._ground())
        cv.rect(x, y, w, fs, th.get("edge", 0))
        it = self.selected()
        if it is None:
            return
        base = y + 22 * fs
        left = c.x0
        right = x + w - (c.x0 - x)
        fid, _w, _lh, asc = _ui_draw.font("title")
        name = "New" if it.get("type") in PSEUDO_TILE_TYPES else (it.get("title") or "")
        was = _ui_draw.print_font(fid)
        name = _ui_draw.print_ellipsis(name, right - left, fs)
        tw = _ui_draw.print_width(name, fs)
        cv.print(name, left, base - asc * fs, th.get("ink", 7), 1)
        if was is not None:
            _ui_draw.print_font(was)
        f = _ui_draw.font()
        sep = 12 * fs
        mx = left + tw + 10 * fs
        my = base - f[3] * fs
        d = 2 * fs if _ui_draw.font_v2(f[0]) else fs
        dim = th.get("ink_dim", 6)
        for k, t in enumerate(self._meta(it)):
            t = _ui_draw.print_fit(t, right - mx, fs)
            if not t:
                break
            if k:
                cv.rect(mx - (sep + d) // 2, _dot_top(my, fs, d), d, d, dim)
            cv.print(t, mx, my, dim, 1)
            mx += _ui_draw.print_width(t, fs) + sep
