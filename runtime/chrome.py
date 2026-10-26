# Map (grep -n a name to jump there):
#   color                             a colour name or palette index -> index
#   -- Pointer UI layout              the 320x240 pointer-UI constants
#   -- Desktop shell                  chrome_scale_floor, text_scale_floor, Layout, CodeLayout
#   Layout                            responsive desktop-shell geometry
#   CodeLayout                        responsive code-editor geometry
#   -- the unified top bar's icon theme  the bar's icon sheet
#   -- Panel THEMES                   THEME_NAMES, the seed packages' names
#   theme_colors                      the full token dict for a theme and variant
#   Live                              the live theme's colours, RGB placed in the block (live_colors)
"""The console's stateless base layer -- the chrome + geometry the shared Moybyte
console draws with, extracted from console.py so the Workstation kernel is alone in
that file.

Everything here is PURE: the MOY64 palette (NAMES/color), the responsive Layout /
CodeLayout geometry (#39), the themeable top-bar IconSheet defaults (_ICON / _ICON_ART /
_default_icon_sheet), and the small helpers (_in / _clamp_scroll / _cursor_delta /
_ticks_* / _err_text / _from_ascii). Nothing touches a Workstation -- these are the
functions/constants shared by console.py AND the surface modules (launcher_layer
imports Layout; every surface gets NAMES and `ui.glyph` injected). console.py imports it
all back and re-exports under the pre-extraction `console.X` names, so every caller +
test is unchanged.

Canonical home is runtime/; build.sh stages a copy into the firmware modules/ tree so
the device freezes it (same pattern as editors.py). It depends only on leaf modules --
editors/widgets + the surface geometry constants from bar_layer/settings_layer/
code_layer, none of which import console/chrome -- so there is no cycle. Same
bare-or-package fallback as those modules.
"""

from array import array

try:
    from editors_code import CodeEditor
    from editors_sheet import IconSheet
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.editors_code import CodeEditor
    from runtime.editors_sheet import IconSheet

try:
    from widgets import _Blit, _in, _err_text
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.widgets import _Blit, _in, _err_text

# The toolkit's face: the display type's widths go through its text path.
try:
    import ui_draw as _ui_draw
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime import ui_draw as _ui_draw

# Surface geometry constants the Layout classes derive their responsive rects from.
# bar_layer/settings_layer/code_layer OWN these (see their modules); imported here the
# same way console.py imports them, so Layout/CodeLayout reflow off the same numbers.
try:
    from bar_layer import _STATUS_H, _BAR_ICON, _BAR_GAP, _BAR_Y, _bar_text_w
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.bar_layer import _STATUS_H, _BAR_ICON, _BAR_GAP, _BAR_Y, _bar_text_w

try:
    from settings_layer import _SET_ROW_H
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.settings_layer import _SET_ROW_H

try:
    from code_layer import (_CODE_LEAD, _CODE_STATUS_H, _SYM_CELL, _SYM_H,
                            _SYM_MIN, _SYM_MARGIN, _CODE_SYMBOLS, _CODE_INSET,
                            _CODE_MARK)
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.code_layer import (_CODE_LEAD, _CODE_STATUS_H, _SYM_CELL, _SYM_H,
                                    _SYM_MIN, _SYM_MARGIN, _CODE_SYMBOLS, _CODE_INSET,
                                    _CODE_MARK)

try:
    import shell_layout as _shell_layout
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime import shell_layout as _shell_layout

try:
    from layout_base import LayoutBase, BASE_W as _BASE_W, BASE_H as _BASE_H
    from layout_base import CLASSES as _LB_CLASSES, class_of as _lb_class_of, bar_rows as _bar_rows
    from layout_base import sys_tile as _sys_tile, metric as _lb_metric, bars as _lb_bars
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.layout_base import (LayoutBase, BASE_W as _BASE_W,
                                     BASE_H as _BASE_H)
    from runtime.layout_base import (CLASSES as _LB_CLASSES, class_of as _lb_class_of,
                                     bar_rows as _bar_rows, sys_tile as _sys_tile,
                                     metric as _lb_metric, bars as _lb_bars)


try:                                    # device: ticks is frozen flat
    from ticks import _ticks_ms, _ticks_us, _ticks_diff
except ImportError:                     # host: the runtime package
    from runtime.ticks import _ticks_ms, _ticks_us, _ticks_diff


def _from_ascii(rows, mapping, transparent="."):
    h = len(rows)
    w = max(len(r) for r in rows) if rows else 0
    pix = []
    for y in range(h):
        row = rows[y]
        for x in range(w):
            ch = row[x] if x < len(row) else transparent
            pix.append(-1 if ch == transparent else (mapping[ch] & 63))
    return _Blit(w, h, pix, -1)


# Mouse-style pointer sprite (O=black outline, F=white fill), hotspot at top-left.
CURSOR = _from_ascii([
    "O.......", "OO......", "OFO.....", "OFFO....", "OFFFO...", "OFFFFO..",
    "OFFFFFO.", "OFFFFFFO", "OFFFOOO.", "OFOOFO..", "OO..OFO.", "O...OFO.", "....OO..",
], {"O": 0, "F": 7}, ".")



def _arrow_sprite(cells, w, h):
    """A double-headed arrow from its fill cells: white, outlined black on
    every side and corner, so it reads on a pixel of any colour."""
    fill = set(cells)
    pix = []
    for y in range(h):
        for x in range(w):
            if (x, y) in fill:
                pix.append(7)
            elif any((x + i, y + j) in fill for i in (-1, 0, 1) for j in (-1, 0, 1)):
                pix.append(0)
            else:
                pix.append(-1)
    return _Blit(w, h, pix, -1)


def _resize_cursors():
    n = 13
    m = n // 2
    e = n - 2                        # the far tip's row and column
    ew = [(x, m) for x in range(1, n - 1)]
    for d in range(4):               # a solid head at each end
        for j in range(-d, d + 1):
            ew += [(1 + d, m + j), (e - d, m + j)]
    ns = [(y, x) for x, y in ew]
    nwse = [(i, i) for i in range(1, n - 1)]
    for x in range(1, n - 1):
        for y in range(1, n - 1):
            if x + y <= 6 or x + y >= 2 * e - 4:
                nwse.append((x, y))
    nesw = [(n - 1 - x, y) for x, y in nwse]
    return {k: _arrow_sprite(c, n, n)
            for k, c in (("ew", ew), ("ns", ns), ("nwse", nwse), ("nesw", nesw))}


# The pointer over a window's resize zone (WindowedWM.cursor_shape), hotspot at
# its centre.
RESIZE_CURSORS = _resize_cursors()

NAMES = {
    "black": 0, "dark_blue": 1, "dark_purple": 2, "dark_green": 3, "brown": 4,
    "dark_grey": 5, "light_grey": 6, "white": 7, "red": 8, "orange": 9,
    "yellow": 10, "green": 11, "blue": 12, "indigo": 13, "pink": 14, "peach": 15,
}


def color(name_or_index):
    if isinstance(name_or_index, str):
        return NAMES.get(name_or_index, 7)
    return int(name_or_index) & 63


# --- Pointer UI layout (320x240) -------------------------------------------
# Per-surface geometry lives with its surface (bar_layer/cards_layer/
# settings_layer/code_layer/paint_layer/...) and is imported back at the top of
# this file so console._X still resolves for Layout + the golden harness/tests.
# --- Desktop shell (#28): home = wallpaper + cart icon grid -----------------
# The home screen is now a Picotron/TIC-80-style desktop: a wallpaper backdrop, a
# grid of tappable cart icons and the theme layout's bars (clock + wifi/batt/≡
# + NEW/DUP/DEL management icons). The top bar's icons are 16x16 sprites from the
# editable IconSheet (Stage 1); the rest of the chrome uses the indexed API +
# petme128 font + the _glyph vocabulary, so host == device. (_STATUS_H lives in
# bar_layer.py, imported back at the top of this file.)
# Cart icon grid: a page of COLS x ROWS tiles below the status strip.
_ICON_COLS = 4
_ICON_ROWS = 2
_ICON_W = 70            # tile footprint (icon box + label)
_ICON_H = 84
_ICON_GAP_X = 6
_ICON_GAP_Y = 6
_ICON_X0 = 8            # left margin so the COLS tiles + gaps center in 320px
_ICON_Y0 = _STATUS_H + 8
_ICON_BOX = 40          # the inner art box of a tile (the tappable icon proper)
# Page chevrons (when more carts than one page): tap to flip pages.
_PAGE_PREV = (2, 110, 14, 24)
_PAGE_NEXT = (304, 110, 14, 24)
# The band the retired bottom dock used to occupy (2026-08-21: a pre-2026-07
# six-slot tool switcher, see bar_layer.py) is now the Settings panel's bottom
# inset: 22px of dock + its 2px gap. The panel KEEPS that spacing rather than
# reclaiming it, deliberately, for two reasons. (1) Settings is an inset DIALOG
# over the wallpaper, so 24*fs at the bottom simply reads against the 20*fs at the
# top -- removing the dock moved only the dock's own pixels, and not one row of the
# panel. (2) Every tier's Settings keeps one proportion, the T-Deck's included.
# (The launcher grid
# is the other half of the story: it reclaimed its share back in #46, when the
# launcher stopped drawing the dock, and still runs to grid_bottom.)
_PANEL_FLOOR = 24       # Settings panel bottom inset, in fs-scaled px
# (_CODE_AREA is re-exported from code_layer -- test_responsive_editors pins
# lay.code_area() against it.)
# Trackball cursor sensitivity (#2). _CURSOR_BASE is the per-pulse step; the
# quadratic _CURSOR_ACCEL term adds light acceleration so a fast roll crosses the
# 320px screen in far fewer pulses while a slow, single-pulse roll stays precise.
# (1 pulse -> 9px; a 6-pulse flick -> 6*7 + 2*36 = 114px, so ~3 brisk rolls cross).
_CURSOR_BASE = 7
_CURSOR_ACCEL = 2

# (_BASE_W/_BASE_H -- the T-Deck's canvas, every layout's default size -- come
# from the layout_base leaf above; console.py still imports them back from here.)
# (The letterbox/bezel fill _VIEWPORT_BEZEL (#39) moved to wm.py with the viewport
# composite it belongs to -- FullscreenStackWM.composite_game is its only user.)

# The tap-target floor (#203): the smallest a bar icon may be ON GLASS, whatever
# the font scale. A CONFIGURATION number, not a measurement -- deliberately under
# the usual phone-button guidance, because a console icon sits in a ROW of its
# neighbours on a handheld held in two hands, and because the scale ladder is
# integers: it is the number that lifts the 3.5" Guition (165 PPI, a 2.5mm icon
# at 16px) one rung and leaves every other panel in the lineup where it is.
MIN_TAP_MM = 4.5


def chrome_scale_floor(w, h, diagonal_in=None):
    """The smallest chrome SIZE that keeps a bar icon at least MIN_TAP_MM wide
    on a `diagonal_in`-inch panel of `w` x `h` pixels: a `_BAR_ICON`-px icon
    times the unit of the panel's metric class (`layout_base.class_of`), so
    the desk's 32-pixel icons are what is measured there.

    1 when no diagonal is declared, which is the OPT-IN: a panel's physical size
    is a board fact that nothing else in the tree knows, so a board that does not
    state it keeps chrome on the font scale exactly as before. Every silence is a
    decision, and each board.toml carries its own; the state of the lineup:

      Guition S3   3.5" 480x320, DECLARED -- the board the floor exists for.
                   FONT_SCALE 2 was built, shipped and reverted first (#202).
                   A handheld: its floor is 2.
      Guition P4  10.1" 1280x800, DECLARED 2026-09-06 from the desk: 2.7mm icons
                   on touch-only glass, "too tiny", never "hard to read". A
                   desk: its 32-pixel icons clear the floor at 1.
      P4           7.0" 1024x600, DECLARED 2026-09-06. A desk, floor 1, as the
                   Guition P4: the desk metrics are what lifted it from the
                   2 it answered on the handheld's 16-pixel icons (the 2026-10
                   look, step 9).
      T-Deck       2.8" 320x240, DECLINED 2026-09-06 -- built, rendered, declined
                   on the pictures. Its floor is 2 as well, and at 320px wide
                   that leaves the bar's lent zone too narrow for the Editor's
                   tab ladder or the launcher's verbs. The board.toml says what
                   would have to change before it is worth re-proposing.
      Browser      its two arrangements are those boards': the desk declares
                   the P4's 7.0" (`web_boot.DESK_DIAGONAL_IN`), the 320x240
                   handheld declares none, as the T-Deck.

    A floor of 3 is not reachable by any panel in the lineup; a board that
    started answering 3 is a red test in tests/test_board_toml.py."""
    if not diagonal_in:
        return 1
    diag_px = (float(w) * float(w) + float(h) * float(h)) ** 0.5
    if diag_px <= 0:
        return 1
    icon = _BAR_ICON * _LB_CLASSES[_lb_class_of(diagonal_in)]["unit"]
    icon_mm = icon * 25.4 * float(diagonal_in) / diag_px
    if icon_mm <= 0:
        return 1
    need = MIN_TAP_MM / icon_mm
    n = int(need)
    if need > n:
        n += 1
    return max(1, n)


# The chrome scales a user may pick in Appearance (CHROME SIZE), and the narrowest
# logical shell a scale may leave: 240px is the Guition S3's 480px at 2x, the
# narrowest chrome-2 shell in the lineup. The T-Deck's 320px at 2x (160) is the
# arrangement its board.toml declined.
CHROME_SCALES = (1, 2)
MIN_CHROME_W = 240


def chrome_scale_choices(w, floor):
    """The chrome scales a `w`-pixel-wide panel whose tap floor is `floor` can
    take: never under the floor, never leaving the shell narrower than
    MIN_CHROME_W. Fewer than two means there is nothing to choose and Appearance
    shows no CHROME SIZE row."""
    return tuple(s for s in CHROME_SCALES
                 if s >= floor and int(w) // s >= MIN_CHROME_W)


# The text-size floor (docs/theming_2026-09.md §4.7), #203's twin for text: the
# smallest a theme font's ascent may be ON GLASS. A CONFIGURATION number, read
# off the same declared diagonal as the tap floor and with the same opt-in. It
# is the smallest text the owner has kept at 1x: Tiny5's six rows on the 7"
# 1024x600 P4 (font scale 1, owner call 2026-07-12), under its six rows on the
# 3.5" Guition S3 (whose 1x text "reads fine", #203). It lifts nothing in the
# lineup and guards denser glass; petme128 clears it on every panel.
MIN_TEXT_MM = 0.89


def text_scale_floor(w, h, diagonal_in, ascent):
    """The smallest font scale at which a font whose ascent is `ascent` rows
    reaches MIN_TEXT_MM on a `diagonal_in`-inch panel of `w` x `h` pixels.
    1 when no diagonal is declared (the T-Deck, a host window)."""
    if not diagonal_in or ascent <= 0:
        return 1
    diag_px = (float(w) * float(w) + float(h) * float(h)) ** 0.5
    if diag_px <= 0:
        return 1
    mm = ascent * 25.4 * float(diagonal_in) / diag_px
    need = MIN_TEXT_MM / mm
    n = int(need)
    if need > n:
        n += 1
    return max(1, n)


class Layout(LayoutBase):
    """Responsive desktop-shell geometry (#39): the status strip, cart icon grid,
    page chevrons, management buttons, and Settings rows derived from
    the SYSTEM canvas size (w, h) + the system font scale (1/2/3) -- instead of the
    hand-placed 320x240 constants. The desktop reflows to fill a larger panel and
    the chrome scales with the font.

    The T-Deck's 320x240 is laid out by the same formulas as every canvas. The editors' geometry is their own `*Layout`'s.

    `font_w` and `line_h` are the `ui` font's glyph-box width and line advance
    at `fs` (docs/theming_2026-09.md §4.7: 8 and 8 for petme128), so callers
    center and space text that the SystemCanvas renders at `fs`.

    TWO scales, since #203: `fs` sizes TEXT and `cs` (>= fs, equal by default)
    sizes what a finger lands on -- the bar and its buttons, the Settings rows,
    the ≡ menu's rows. Where a hand-tuned text offset sits inside one of those
    elements, the `*_text_dy` fields below re-center the fs-tall band inside the
    cs-tall box; each is 0 at cs == fs, which is what keeps every existing tier
    byte-identical."""

    def __init__(self, w=_BASE_W, h=_BASE_H, font_scale=1, chrome_scale=None):
        LayoutBase.__init__(self, w, h, font_scale, chrome_scale=chrome_scale)
        fs = self.fs
        cs = self.cs
        _fid, fw, line, _asc = _ui_draw.font("ui")
        self.font_w = fw * fs
        self.line_h = line * fs

        # -- status strip (its rows scale with the CHROME scale; where it sits
        # is the content rect's business: everything below lays out in
        # cx/cy/cw/ch, never under the bar) ------------------------------------
        self.status_h = _bar_rows(cs)
        bottom = self.cy + self.ch
        # The bar's clock/title print at a hand-tuned y=3 inside an fs-tall bar;
        # this re-centers that band when the bar is taller than the font asked for.
        self.bar_text_dy = (self.status_h - _STATUS_H * fs) // 2

        # -- cart icon grid (reflows COLS x ROWS to fill the band) ---------------
        # The launcher/home screen stopped drawing the bottom dock in #46, so the
        # cart grid reclaims the height below the status strip down to the canvas
        # floor (grid_bottom). (The dock itself is gone entirely since 2026-08-21;
        # the Settings panel keeps its _PANEL_FLOOR inset -- see above.)
        self.icon_w = _ICON_W * fs
        self.icon_h = _ICON_H * fs
        self.icon_gap_x = _ICON_GAP_X * fs
        self.icon_gap_y = _ICON_GAP_Y * fs
        self.icon_box = _ICON_BOX * fs
        self.icon_y0 = self.cy + 8 * fs
        self.grid_bottom = bottom - 4 * fs           # launcher grid floor
        self.icon_x0 = self.cx + _ICON_X0 * fs
        # The home's grid arrangement, the SHELF (visual identity v1's library
        # concept mockup), on EVERY tier -- the T-Deck's 320x240 baseline included (the home's
        # carousel lays itself out in launcher_layer.Carousel). A card grid that
        # SCROLLS continuously LEFT-RIGHT (owner call, 2026-07-13: a shelf slides
        # sideways; no pages): slot 0 is the ONE tall
        # FEATURED card (the pinned "+ New", column 0 spanning both visible
        # rows at the head of the list); every other cartridge card stacks the
        # columns marching right, `rows` per column.
        # RESOLUTION-driven, not font-scale-driven (owner call, 2026-07-12:
        # "everything is 1x; two resolutions"): the shelf's proportions come
        # from the canvas size -- the mockup's 4 big columns at 1024x600 --
        # with fs-multiples only as floors so a kid who picks a bigger
        # Settings font never gets clipped chrome. At 1024x600 the geometry
        # is near-identical at fs 1 and 2; fs 1 just fits crisper text.
        cx, cw = self.cx, self.cw
        mx = max(16 * fs, cw // 20)
        pt = self.cy + max(8 * fs, self.h // 24)
        pb = max(16 * fs, self.h // 14)
        self.lib_panel = (cx + mx, pt, cw - 2 * mx, bottom - pt - pb)
        self.lib_header_h = max(26 * fs, self.h // 12)
        self.lib_footer_h = max(20 * fs, self.h // 15)
        self.lib_gap = max(6 * fs, cw // 64)
        # Display-type multiplier: the shelf's headings hold ~32px at desktop
        # widths regardless of the body font scale (petme128 x4 at fs1, x2 at
        # fs2), and never wider than the tier can hold -- the 320-wide baseline
        # renders them at body size.
        self.lib_mult = max(1, min(4 // fs, cw // 256))
        inset = max(10 * fs, cw // 42)
        px_, py_, pw_, ph_ = self.lib_panel
        gx = px_ + inset
        gy = py_ + self.lib_header_h
        gw = pw_ - 2 * inset
        gh = ph_ - self.lib_header_h - self.lib_footer_h - 4 * fs
        self.cols = max(3, min(6, (gw + self.lib_gap) //
                               (cw // 5 + self.lib_gap)))
        self.rows = 2                                # VISIBLE rows (grid look)
        self.lib_card_w = (gw - (self.cols - 1) * self.lib_gap) // self.cols
        self.lib_card_h = (gh - self.lib_gap) // 2
        self.lib_step = self.lib_card_w + self.lib_gap   # one-column scroll step
        self.lib_grid = (gx, gy, gw, gh)
        # Whether a card is big enough to carry its own PLAY/CHANGE button row
        # (visual identity v1 Section 1.2): the row + title band (the exact
        # heights the card draw uses) must leave the cover art at least half the
        # card. Small-card tiers (the 320x240 baseline's ~69px-tall cards) keep
        # the verbs as lent-bar-zone chips instead (Section 7: on the small tier
        # selected actions use the zoned bar).
        band_h = max(14 * fs, 20)
        btn_area = max(13 * fs, 22) + 2 * max(2 * fs, 3)
        self.lib_card_actions = (
            self.lib_card_h - band_h - btn_area >= self.lib_card_h // 2)

        # -- the bars: icon size + stride (Stage 1) ----------------------------
        # Every bar control is a 16x16 IconSheet sprite (16px icons, 1px margin in the
        # 18px bar -> y = _BAR_Y). Icons scale with the CHROME scale (#203) -- with the
        # font by default, and past it on a panel dense enough that 16px is under
        # MIN_TAP_MM.
        ic = _BAR_ICON * cs                           # bar icon side, scaled
        stride = ic + _BAR_GAP * cs                   # left-edge step between bar icons
        self.bar_icon = ic
        self.bar_stride = stride
        edge = 2 * cs                                 # margin from the canvas edges

        # -- the bars (docs/shell_layout_2026-10.md §4): every bar this surface
        # takes, packed into cells once here (shell_layout.pack) -- the rects
        # the bar layer draws and hit-tests, each in its own bar's coordinates.
        # The named rects below are views of the first bar that places each
        # widget (None where none does): batt hard right, then wifi, the ≡,
        # the context X and the clock in today's bar; its lent left zone runs
        # from the left edge to just before the clock.
        # "HH:MM": five cells, or a version 2 face's measure of it no
        # narrower than its metric class's clock (`layout_base.CLASSES`), so
        # a theme whose modes set different faces keeps one bar
        self.clock_w = (max(_ui_draw.print_width("00:00", fs), _lb_metric("clock", fs))
                        if _ui_draw.font_v2(_ui_draw.font()[0]) else 5 * self.font_w)
        sys_len = _sys_tile(self.status_h)[2] if _ui_draw.depth()[0] else 0
        of = _lb_bars()
        self.bars = _shell_layout.pack(self.w, self.h, self.status_h, cs, fs,
                                       self.clock_w, _bar_text_w, of=of, sys_len=sys_len)
        self._pack_of = (of, cs, fs, sys_len)
        # {where: the packing its bar takes}, the bar layer's to fill
        # (`BarLayer._make_room`): a surface's bars less the status widgets
        # its zone owner took the room of.
        self.room = {}
        self.batt_btn = self._cell("battery")
        self.wifi_btn = self._cell("wifi")
        self.sysmenu_btn = self._cell("menu")
        self.context_x_btn = self._cell("close")
        clock = self._cell("clock")
        self.clock_x = clock[0] if clock is not None else None
        zone = self._cell("zone") or (edge, _BAR_Y, 0, ic)
        self.zone_left = zone

        # -- scroll nudge arrows (the pager's successors) ----------------------
        # Boxed left/right arrow buttons in the Library panel footer's corners:
        # each tap slides the card grid by one column (drag + scrollbar +
        # keyboard nav are the primary scroll affordances; these are the big
        # tap targets).
        px_, py_, pw_, ph_ = self.lib_panel
        ah = max(16 * fs, self.lib_footer_h * 2 // 3)   # touch-target floor at fs1
        aw = max(22 * fs, 30)
        am = max(10 * fs, 20)
        ay = py_ + ph_ - self.lib_footer_h + (self.lib_footer_h - ah) // 2
        self.scroll_lt = (px_ + am, ay, aw, ah)
        self.scroll_rt = (px_ + pw_ - am - aw, ay, aw, ah)

        # -- Settings rows + panel (a row is a TAP TARGET -> the chrome scale) ---
        # The row list already scrolls (_settings_visible derives its window from
        # the panel height), so a taller row costs rows on SCREEN, never rows.
        self.set_row_h = _SET_ROW_H * cs
        self.row_text_dy = (self.set_row_h - _SET_ROW_H * fs) // 2
        # The Settings panel fills the band between the status strip and the
        # bottom inset (_PANEL_FLOOR -- the retired dock's band, kept).
        py0 = self.cy + 2 * fs
        ph = bottom - _PANEL_FLOOR * fs - py0
        self.settings_panel = (cx + 8 * fs, py0, cw - 16 * fs, ph)
        self.set_x = self.settings_panel[0] + 10 * fs
        self.set_w = self.settings_panel[2] - 20 * fs
        # The title band holds the X / trophy buttons, so it takes the chrome
        # scale with them; its gear + "SETTINGS" text re-center via set_head_dy.
        self.set_row_y0 = py0 + 24 * cs
        self.set_head_dy = 12 * (cs - fs)
        pr = self.settings_panel[0] + self.settings_panel[2]   # panel right edge
        self.set_back = (pr - 20 * cs, py0 + 2 * cs, 18 * cs, 14 * cs)
        self.set_ach = (pr - 46 * cs, py0 + 2 * cs, 22 * cs, 14 * cs)
        self.set_title_hit = (self.settings_panel[0] + 14 * fs, py0 + 2 * cs,
                              10 * self.font_w, 16 * cs)

    # -- derived rects (mirror the old module-constant arithmetic) ----------
    def settings_row_rect(self, i):
        return (self.set_x, self.set_row_y0 + i * self.set_row_h,
                self.set_w, self.set_row_h - 2)

    def row_band(self, rect):
        """The fs-tall band inside a chrome-scaled row (#203) -- where the row's
        own hand-tuned content sits, so a bottom-anchored mark (the wifi list's
        signal bars) still lands beside the label instead of at the floor of a
        box the tap-target floor made taller. The IDENTITY at cs == fs."""
        d = self.row_text_dy
        return (rect[0], rect[1] + d, rect[2], rect[3] - 2 * d)

    def tap_box(self, rect, base_w, base_h):
        """The fs-sized DRAWING box centred inside a cs-sized TAP TARGET (#203).

        `rect` is a button the chrome scale grew from a `base_w` x `base_h` design
        box; what goes inside it -- a 12px glyph, a `mini_btn` label, a badge
        count -- is still font-sized, and drawn at the button's origin it lands in
        the corner rather than under the finger. This is `row_band` for a BUTTON:
        the same content, moved to the middle. The IDENTITY at cs == fs, where the
        rect already is exactly this box."""
        bw, bh = base_w * self.fs, base_h * self.fs
        return (rect[0] + (rect[2] - bw) // 2, rect[1] + (rect[3] - bh) // 2,
                bw, bh)

    def tile_cell(self, i, featured=True):
        """(row, col) of grid slot `i` in the shelf packing. With `featured`,
        slot 0 is the ONE tall featured card -- column 0 spanning BOTH visible
        rows (its row reads 0) -- and slots 1.. stack the columns marching
        right; without it every slot stacks, `rows` cards per column, top to
        bottom."""
        if not featured:
            return (i % self.rows, i // self.rows)
        if i <= 0:
            return (0, 0)
        j = i - 1
        return (j % self.rows, 1 + j // self.rows)

    def tile_index(self, row, col, featured=True):
        """Inverse of tile_cell: the grid slot at (row, col) -- with
        `featured`, column 0 is the tall slot 0 at any row. No bounds check
        (callers clamp to n)."""
        if not featured:
            return col * self.rows + row
        if col <= 0:
            return 0
        return 1 + (col - 1) * self.rows + row

    def tile_rect(self, i, scroll=0, featured=True):
        """Grid-cell rect for cart index `i` at pixel scroll offset `scroll`, or
        None when the cell lies fully outside the grid viewport (the card list
        SCROLLS continuously left-right -- there are no pages). Partially
        visible cells DO return their rect; the draw clips them."""
        gx, gy, gw, gh = self.lib_grid
        cw, ch, gap = self.lib_card_w, self.lib_card_h, self.lib_gap
        row, col = self.tile_cell(i, featured)
        hh = 2 * ch + gap if featured and i == 0 else ch
        x = gx + col * (cw + gap) - scroll
        if x + cw <= gx or x >= gx + gw:
            return None
        return (x, gy + row * (ch + gap), cw, hh)

    def grid_content_w(self, n, featured=True):
        """Total scrollable content width for n grid items (at least the tall
        slot 0's own column)."""
        if n <= 0:
            return 0
        cw, gap = self.lib_card_w, self.lib_gap
        _row, last_col = self.tile_cell(n - 1, featured)
        return last_col * (cw + gap) + cw

    def _cell(self, name):
        got = _shell_layout.cell_of(self.bars, name)
        return got[1] if got is not None else None

    def bars_without(self, names):
        """`bars` packed again with the widgets `names` left out, their room
        the flex widgets' (`BarLayer._make_room`): the same bars in force,
        glass and scales, so every other cell is where `bars` has it."""
        of, cs, fs, sys_len = self._pack_of
        return _shell_layout.pack(self.w, self.h, self.status_h, cs, fs, self.clock_w,
                                  _bar_text_w, of=_shell_layout.without(of, names),
                                  sys_len=sys_len)

    def clock_hit(self):
        # The clock's cell (Time Traveler egg #21), in its bar's coordinates
        # like every bar rect here; None where no bar places a clock.
        return self._cell("clock")


class CodeLayout(LayoutBase):
    """Responsive code-editor geometry (#39 step 2): the top bar (title + run/save/
    close icons), the COLS x ROWS text grid, the caret/gutter, and the bottom symbol
    palette -- all derived from the SYSTEM canvas size (w, h) + font scale, instead
    of the hand-placed 320x240 constants. On a larger panel the editor shows MORE
    visible lines + WIDER columns; at a bigger font everything (cell, bar, palette)
    grows with the text.

    The T-Deck's 320x240 is laid out by the same formulas as every canvas: the text grid, the status band and the symbol palette.

    `cell` is the `mono` font's glyph-box width at fs; `lh` its line advance and
    `_CODE_LEAD` rows (10 at fs 1 with petme128), or a version 2 face's glyph
    box (14 with JetBrains Mono 11). `cols` /
    `rows` are how many fit -- the CodeEditor's view window adopts them so it scrolls
    the right span."""

    def __init__(self, w=_BASE_W, h=_BASE_H, font_scale=1, chrome_scale=None,
                 symbols=True):
        LayoutBase.__init__(self, w, h, font_scale, chrome_scale=chrome_scale)
        fs = self.fs
        cs = self.cs
        # The symbol keys show where the last input was touch, and a keyboard
        # hides them (the owner's D-17, 2026-10-19): their rows go to the code.
        self.symbols = bool(symbols)
        fid, fw, line, _asc = _ui_draw.font("mono")
        self.cell = fw * fs
        # A row: petme128's 8-pixel line and the code lead; a version 2
        # face's glyph box, which holds its ascenders and descenders (14 for
        # the Code tab's JetBrains Mono 11). `text_h` is the box the caret
        # and a selection span.
        info = _ui_draw.font_info(fid) if fid else None
        if info is not None and info[4] == 0:
            self.text_h = info[1] * fs
            self.lh = self.text_h
        else:
            self.text_h = 8 * fs
            self.lh = (line + _CODE_LEAD) * fs
        # One content inset (design review R3-10): the code, the status line
        # and the symbol keys all start `inset` in from the content's left
        # edge, and the current line's mark stands `mark_x`, inside it.
        ins = self.inset = _CODE_INSET * fs
        self.mark_x = self.cx + _CODE_MARK * fs
        # -- symbol palette (bottom strip): one cell per coding symbol -----------
        # A key is TAPPED (on a board with no keyboard this strip IS the keyboard),
        # so it is as tall as the chrome scale makes a tap target, and the keys
        # share out the content width less the inset each side -- a key is never
        # narrower than _SYM_MIN at the font scale (font scale 3 overflows 800px
        # either way). Each key draws fs inside its cell (`draw_symbol_keys`),
        # so keys stand 2fs apart, the first one's edge on the inset, and the
        # strip sits a margin above the content's bottom edge. The cells share
        # the span out whole (`sym_key_x`), so its two ends are the inset.
        n = len(_CODE_SYMBOLS)
        m = _SYM_MARGIN * fs
        span = self.cw - 2 * (ins - fs)
        self.sym_cell = max(_SYM_MIN * fs, span // n)
        self.sym_h = _SYM_H * cs if self.symbols else 0
        self.sym_text_dx = max(0, (self.sym_cell - _SYM_CELL * fs) // 2)
        self.sym_text_dy = (self.sym_h - _SYM_H * fs) // 2
        if self.symbols:
            self.sym_y = self.cy + self.ch - self.sym_h - (m - fs)
            self.sym_area = (self.cx + ins - fs, self.sym_y,
                             max(span, self.sym_cell * n), self.sym_h)
        else:
            self.sym_y = self.cy + self.ch
            self.sym_area = (self.cx + ins - fs, self.sym_y, 0, 0)
        # -- code area origin (the content rect's top) --------------------------
        # The zoned bar carries the editor's controls, so CodeLayout has no
        # chrome of its own: the body is text + the symbol palette, filling the
        # content rect.
        self.x0 = self.cx + ins
        self.y0 = self.cy
        # -- the COLS x ROWS text grid (fills between the bar + palette) ---------
        avail_w = self.cx + self.cw - self.x0
        self.cols = max(8, avail_w // self.cell)
        # Status band (visual identity v1 Phase 3, the Studio mockup's
        # "Ln 13, Col 1" strip) between the text grid and the symbol palette.
        # Its height is _CODE_STATUS_H under a theme's own `ui` face (a
        # version 2 face), one height in every theme and mode, and 12 under
        # petme128; never under the face's line and 1px above and below. The
        # band spans the content; its text (`status_text`, the band less the
        # status pad before the inset) starts on the code's column.
        ufid, _uw, uline, _ua = _ui_draw.font("ui")
        band = max(_CODE_STATUS_H if _ui_draw.font_v2(ufid) else 12, uline + 2) * fs
        self.status_band = (self.cx, self.sym_y - band, self.cw, band)
        tx = self.x0 - _ui_draw.metrics("status")[_ui_draw.ST_PAD] * fs
        self.status_text = (tx, self.sym_y - band, self.cx + self.cw - tx, band)
        avail_h = self.status_band[1] - self.y0
        self.rows = max(4, avail_h // self.lh)

    def sym_key_x(self, i):
        """Where symbol key cell `i` starts: the strip's width shared out
        whole, so the cells differ by at most a pixel."""
        x, _y, w, _h = self.sym_area
        return x + i * w // len(_CODE_SYMBOLS)

    def sym_key_at(self, px):
        """The symbol key cell under x `px` (sym_key_x's inverse), or -1."""
        x, _y, w, _h = self.sym_area
        n = len(_CODE_SYMBOLS)
        if not x <= px < x + w:
            return -1
        return ((px - x + 1) * n - 1) // w

    def code_area(self):
        return (self.x0, self.y0, self.cols * self.cell, self.rows * self.lh)


# BlockLayout (#39 step 2) now lives in block_editor_ui.py (imported above).


def _print_scaled(cv, s, x, y, c, mult=2):
    """System DISPLAY type (visual identity v1): print `s` at `mult` x the canvas's
    system font scale -- the Library shelf's headings ("LIBRARY", the MAKE card).
    cv.print ignores its legacy per-call scale arg (one system size), which is why
    this helper exists: above an effective scale of 1 it is one `print_scaled`, the
    one UI text path at that scale (native/moy_gfx/moy_text.h); at 1 it is
    cv.print."""
    fs = getattr(cv, "font_scale", 1)
    if fs < 1:
        fs = 1
    sc = fs * max(1, int(mult))
    if sc <= 1:
        cv.print(s, x, y, c, 1)
        return
    cv.print_scaled(s, x, y, c, sc)


def _text_w(cv, s, mult=1):
    """The on-screen width of `s` printed at `mult` x the canvas font scale, as
    the text path measures it in the print font (`ui.print_width`)."""
    fs = getattr(cv, "font_scale", 1)
    if fs < 1:
        fs = 1
    return _ui_draw.print_width(s, fs * max(1, int(mult)))


# --- the unified top bar's icon theme (Stage 1) -----------------------------
#
# Under a theme that names no icon set the top bar's chrome controls are 16x16
# sprites of the shell's IconSheet, not the fixed 12x12 glyphs -- which collapses the
# ~120 glyph rect-spans/frame the labeled button rows cost into ~12 cached sprite
# blits (a measured ~15ms/frame device win). `_ICON` is the slot map: a chrome kind ->
# its sprite id in the 8x4 IconSheet (row-major), baked from `_ICON_ART` below. The
# _glyph vocabulary stays for NON-chrome uses (the cards/paint/blocks editors).
_ICON = {
    "home": 0, "edit": 1, "code": 2, "paint": 3, "map": 4, "blocks": 5,
    "gear": 6, "wifi": 7, "batt": 8, "new": 9, "dup": 10, "del": 11,
    "close": 12, "run": 13, "save": 14, "music": 15,
    "moy": 16,          # the moybyte mascot (boot logo); not a bar control
    "wifi_off": 17,     # wifi-with-a-red-slash: the right-zone status glyph when
                        # there's NO connection (ws._wifi_icon_kind picks it, #Part3)
    "scene": 18,        # the scene placement editor tab (#85 Stage 2)
    "undo": 19, "redo": 20,   # the shared Editor bar's undo/redo icons (#88)
}

# The baked default theme: each icon is 16 row-strings of 16 chars over the 16-color
# base palette. A char is a palette nibble (hex), or "." for transparent. Authored
# high-contrast (mostly white 7 outlines + a couple of accents) so they read at 16px
# on the black bar. Kept readable here so the theme is hand-editable; _default_icon_
# sheet() bakes it into an IconSheet's pixels at the _ICON slots.
_ICON_ART = {
    "home": (
        "................", ".......77.......", "......8888......", ".....888888.....",
        "....88888888....", "...8888888888...", "..888888888888..", ".77777777777777.",
        ".7ffffffffffff7.", ".7f77ff11ff77f7.", ".7f77ff11ff77f7.", ".7ffffff11ffff7.",
        ".7ffffff11ffff7.", ".7ffffff11ffff7.", ".77777777777777.", "................",
    ),
    "edit": (
        ".............77.", "............7ee7", "...........7ee7.", "..........7aa7..",
        ".........7aa7...", "........7aa7....", ".......7aa7.....", "......7aa7......",
        ".....7aa7.......", "....7aa7........", "...7ff7.........", "..7ff7..........",
        ".700f...........", "700.............", "................", "................",
    ),
    "code": (
        "................", "................", ".....c....c.....", "....cc....cc....",
        "...cc......cc...", "..cc........cc..", ".cc..........cc.", "cc............cc",
        ".cc..........cc.", "..cc........cc..", "...cc......cc...", "....cc....cc....",
        ".....c....c.....", "................", "................", "................",
    ),
    "paint": (
        "..............77", ".............799", "............7997", "...........7997.",
        "..........7997..", ".........7997...", "........7997....", ".......7667.....",
        "......76667.....", ".....7eeee7.....", "....7eeeee7.....", "....7eeeee7.....",
        ".....7eeee7.....", "......7ee7......", ".......77.......", "................",
    ),
    "map": (
        "................", ".77777777777777.", ".7bbb7ccc7bbb77.", ".7bbb7ccc7bbb77.",
        ".77777777777777.", ".7ccc7bbb7ccc77.", ".7ccc7bbb7ccc77.", ".77777777777777.",
        ".7bbb7ccc7bbb77.", ".7bbb7ccc7bbb77.", ".77777777777777.", "................",
        "................", "................", "................", "................",
    ),
    "blocks": (
        "................", "..bbbbb.........", ".bb...bbbbbb....", ".bbbbbb....b....",
        ".bccccccccccb...", ".cc........cc...", ".cccccc....cc...", ".ccaaaccccccc...",
        ".caaaaaaaaaac...", ".aa........aa...", ".aaaaaa....aa...", ".aaaaaaaaaaaa...",
        "................", "................", "................", "................",
    ),
    "gear": (
        "......6..6......", ".....66..66.....", "..6..666666..6..", "..66666666666...",
        "..6677777766....", ".66777777776666.", ".667700007766...", "66677000007766..",
        "66677000007766..", ".667700007766...", ".66777777776666.", "..6677777766....",
        "..66666666666...", "..6..666666..6..", ".....66..66.....", "......6..6......",
    ),
    "wifi": (
        "................", "....77777777....", "..77........77..", ".7....7777....7.",
        "....77....77....", "...7........7...", "......7777......", ".....7....7.....",
        "........7.......", "................", ".......77.......", "......7887......",
        ".......77.......", "................", "................", "................",
    ),
    # "wifi_off": the wifi signal with a bold red (8) diagonal slash through it -- the
    # right-zone STATUS glyph shown when there is NO connection (ws._wifi_icon_kind).
    "wifi_off": (
        "................", "....77777777....", ".887........77..", ".788..7777....7.",
        "...887....77....", "...788......7...", ".....88777......", ".....788..7.....",
        ".......88.......", "........88......", ".......7788.....", "......788788....",
        ".......77..88...", "............88..", "................", "................",
    ),
    "batt": (
        "................", "................", "....77777777.7..", "...7........7.7.",
        "...7.bbbbbb.7.7.", "...7.bbbbbb.7.7.", "...7.bbbbbb.7.7.", "...7.bbbbbb.7.7.",
        "...7........7.7.", "....77777777.7..", "................", "................",
        "................", "................", "................", "................",
    ),
    "new": (
        "..7777777777....", "..7........7....", "..7...bb...7....", "..7...bb...7....",
        "..7.bbbbbb.7....", "..7.bbbbbb.7....", "..7...bb...7....", "..7...bb...7....",
        "..7........7....", "..7........7....", "..7........7....", "..7777777777....",
        "................", "................", "................", "................",
    ),
    "dup": (
        "....7777777.....", "....7......7....", "..7777777..7....", "..7......7.7....",
        "..7......777....", "..7........7....", "..7........7....", "..7........7....",
        "..7........7....", "..7........7....", "..77777777777...", "................",
        "................", "................", "................", "................",
    ),
    "del": (
        "................", ".....88888......", "...888888888....", ".88888888888888.",
        "................", ".7777777777777..", ".7.7.7.7.7.7.7..", ".7.7.7.7.7.7.7..",
        ".7.7.7.7.7.7.7..", ".7.7.7.7.7.7.7..", ".7.7.7.7.7.7.7..", "..77777777777...",
        "..777777777.....", "................", "................", "................",
    ),
    "close": (
        "................", ".88..........88.", "..88........88..", "...88......88...",
        "....88....88....", ".....88..88.....", "......8888......", "......8888......",
        ".....88..88.....", "....88....88....", "...88......88...", "..88........88..",
        ".88..........88.", "................", "................", "................",
    ),
    "run": (
        "................", "...bb...........", "...bbbb.........", "...bbbbbb.......",
        "...bbbbbbbb.....", "...bbbbbbbbbb...", "...bbbbbbbbbbbb.", "...bbbbbbbbbb...",
        "...bbbbbbbb.....", "...bbbbbb.......", "...bbbb.........", "...bb...........",
        "................", "................", "................", "................",
    ),
    "save": (
        "................", ".7777777777777..", ".7cc7777777cc7..", ".7cc7777777cc7..",
        ".7cc7777777cc7..", ".7ccccccccccc7..", ".7c777777777c7..", ".7c7bbbbbbb7c7..",
        ".7c7bbbbbbb7c7..", ".7c7777777b7c7..", ".7c7777777b7c7..", ".7ccccccccccc7..",
        ".77777777777....", "................", "................", "................",
    ),
    "music": (
        ".....77777777...", "....7cccccccc7..", "...7cc......cc..", "...cc.......cc..",
        "...cc.......cc..", "...cc.......cc..", "...cc.......cc..", "...cc.......cc..",
        ".7ccc.....7ccc..", "7cccc....7cccc..", "7cccc....7cccc..", ".7cc......7cc...",
        "................", "................", "................", "................",
    ),
    # "scene": the placement editor (#85 Stage 2) -- a white viewport frame with
    # two placed actors inside (a green block low-left, an orange one up-right),
    # matching the 12px _glyph's "things on a stage" motif.
    "scene": (
        "................", ".77777777777777.", ".7............7.", ".7........aa..7.",
        ".7........aa..7.", ".7............7.", ".7............7.", ".7...bbb......7.",
        ".7...bbb......7.", ".7...bbb......7.", ".7............7.", ".77777777777777.",
        "................", "................", "................", "................",
    ),
    # "undo"/"redo" (#88): the SAME curl the 12x12 glyph vocabulary already draws
    # (undo left, redo its horizontal mirror -- kept pixel-consistent with the
    # code editor's existing tool-palette icon so the two affordances read as one
    # visual language), just re-authored at 16x16 for the themeable IconSheet.
    "undo": (
        "................", "................", "................", "................",
        ".....7..........", "....77..........", "...77777........", "..777777........",
        "...77777..7.....", "....77...77.....", "........777.....", "......7777......",
        "................", "................", "................", "................",
    ),
    "redo": (
        "................", "................", "................", "................",
        "..........7.....", "..........77....", "........77777...", "........777777..",
        ".....7..77777...", ".....77...77....", ".....777........", "......7777......",
        "................", "................", "................", "................",
    ),
    # "Moy", the moybyte mascot: one big pixel (a byte) with a square bite chomped
    # from the top-right corner, two eyes + a smile + stubby feet. "Grape" skin --
    # body = indigo (d/13), shadow = dark-purple (2), sheen = light-grey (6), eyes
    # white (7), outlined in black (0). The boot logo (see _draw_splash); not a bar
    # control, so it has no _glyph fallback (the splash simply omits it if absent).
    "moy": (
        "................", "...0000000......", "..0ddddddd0.....", ".0d66ddddd0.....",
        ".0dddddddd0.....", ".0dddddddd0000..", ".0dd77d77ddddd0.", ".0dd70d70ddddd0.",
        ".0dddddddddddd0.", ".0dd0ddd0ddddd0.", ".0ddd000dddddd0.", ".0dddddddddddd0.",
        ".0dddddddddddd0.", "..022222222220..", "..02220002220...", "...000...000....",
    ),
}


# --- Panel THEMES ----------------------------------------------------------
# A theme is a data package (docs/theming_2026-09.md): the seeds are
# native/moy_ui/themes/*.json, embedded in the image and read by the C loader
# (native/moy_ui/moy_theme.c), which validates them, walks `inherits` and
# fills every role a package leaves unset from style.json's derivation rules.
# The roles are style.json's `tokens`; their jobs are
# docs/visual_identity_v1.md Section 4.3's. A colour role is a MOY64 index or
# RGB (`RGB | 0xrrggbb`, docs/look_2026-10.md step 5): `theme_colors` answers
# the theme's own values, and `live_colors` the live theme's as the shell
# draws them, each RGB role placed in the block at indices 64..127;
# `surface_light`, `bar_light` and `band_light` are presentation flags
# (booleans here).
# The default is the first seed, Moy, in its light mode (Day). The
# store's theme packages are themes too (`adopt_packages`, which the look
# calls at every store change), each resolved from its bytes without
# installing it (`moy_ui.theme_resolve`), as is the one installed package
# beside the seeds (`theme_known`); a name no package provides resolves as the
# default.
THEME_NAMES = _ui_draw._C.themes()
DEFAULT_THEME = THEME_NAMES[0]
THEME_VARIANTS = ("dark", "light")
DEFAULT_VARIANT = "light"
_THEME_FLAGS = ("surface_light", "bar_light", "band_light")
_THEME_CACHE = {}
_CONTENT_CACHE = {}
_LIVE_CACHE = {}
_LOOK_CACHE = {}
RGB = 0x1000000             # a colour value at or above: RGB | 0xrrggbb
PAINT = 0x2000000           # a look colour at or above is a paint, no colour
LOOK_NONE = -2147483648     # a look leaf the theme leaves unset
# The store's packages: name -> theme_resolve's answer, None until first read.
_PACKAGES = {}
_PKG_READ = [None]          # name -> its theme.json's bytes, or None


def adopt_packages(names, read):
    """The store's theme packages are `names` now, each read through
    `read(name)` (its theme.json's bytes, or None) the first time it is
    asked for: the look calls this at every store change."""
    _PACKAGES.clear()
    for n in names:
        _PACKAGES[n] = None
    _PKG_READ[0] = read
    forget_themes()


def package(name):
    """`moy_ui.theme_resolve`'s answer for store package `name`, read once
    per store change: (rc, notes, name, dark, light, fonts, layout, sheets,
    looks, backdrops, modes). None
    when `name` is no store package. A package whose `name` is not its
    folder's is refused (rc 4, the loader's NAME)."""
    if name not in _PACKAGES:
        return None
    got = _PACKAGES[name]
    if got is None:
        read = _PKG_READ[0]
        src = read(name) if read is not None else None
        if src is None:
            got = (_ui_draw._C.theme_resolve(b"")[0], 0) + (None,) * 9
        else:
            got = _ui_draw._C.theme_resolve(src)
            if got[0] == 0 and got[2] != name:
                got = (4, got[1]) + (None,) * 9
        _PACKAGES[name] = got
    return got


def package_ok(name):
    """True for a store package that resolves."""
    got = package(name)
    return got is not None and got[0] == 0


def theme_names():
    """The seeds, then the store's packages that resolve: every list of
    themes reads this (THEME_NAMES stays the seeds)."""
    return THEME_NAMES + tuple(n for n in sorted(_PACKAGES) if package_ok(n))


def theme_known(name):
    """True for a seed's name, a store package that resolves, or the
    installed package's."""
    return (name in THEME_NAMES or name == _ui_draw._C.theme_installed()
            or package_ok(name))


def _row(name, light):
    """`name`'s role row: the installed slot's or a seed's from the loader,
    a store package's from its resolved bytes."""
    if name not in THEME_NAMES and name != _ui_draw._C.theme_installed():
        got = package(name)
        if got is not None and got[0] == 0:
            return got[4 if light else 3][0]
    return _ui_draw._C.theme(name, light)[0]


def theme_modes(name):
    """The modes theme `name` has, of THEME_VARIANTS: the ones its package
    declares (`"modes"`, docs/theming_2026-09.md §3), both where it is
    silent."""
    if not theme_known(name):
        name = DEFAULT_THEME
    bits = None
    if name not in THEME_NAMES and name != _ui_draw._C.theme_installed():
        got = package(name)
        if got is not None and got[0] == 0:
            bits = got[10]
    if bits is None:
        bits = _ui_draw._C.theme_modes(name)
    return tuple(v for v in THEME_VARIANTS if bits & (1 << (v == "light")))


def theme_colors(name, variant=DEFAULT_VARIANT):
    """The full token dict for theme `name` in `variant` ("dark"/"light"),
    flattened by the C loader: every role set, an unknown name or variant
    resolving as the default's. Returns a shared cached dict (treat as
    read-only)."""
    if not theme_known(name):
        name = DEFAULT_THEME
    if variant not in THEME_VARIANTS:
        variant = DEFAULT_VARIANT
    key = (name, variant)
    cached = _THEME_CACHE.get(key)
    if cached is None:
        row = _row(name, variant == "light")
        cached = {}
        for role, v in zip(_ui_draw._C.tokens(), row):
            cached[role] = bool(v) if role in _THEME_FLAGS else v
        _THEME_CACHE[key] = cached
    return cached


def forget_package(name):
    """Store package `name`'s bytes moved (a change to `custom`): its answer
    is read again at the next ask, and the shared dicts go."""
    if name in _PACKAGES:
        _PACKAGES[name] = None
    forget_themes()


def forget_themes():
    """Drop every shared token and content dict: an install or uninstall
    moved what the names resolve to."""
    _THEME_CACHE.clear()
    _CONTENT_CACHE.clear()
    _LIVE_CACHE.clear()
    _LOOK_CACHE.clear()


def _look_raw(name, light):
    if name not in THEME_NAMES and name != _ui_draw._C.theme_installed():
        got = package(name)
        if got is not None and got[0] == 0:
            return got[8][1 if light else 0]
    return _ui_draw._C.theme_look(name, light)


def theme_look(name, variant=DEFAULT_VARIANT):
    """Theme `name`'s look leaves in `variant` as {leaf name: value}, a
    colour a palette index or RGB | 0xrrggbb, a width or alpha in
    hundredths, an unset leaf `LOOK_NONE`: what a picture of another theme
    (Appearance's cards) reads its ground, bar and frame from. Returns a
    shared cached dict (treat as read-only)."""
    if not theme_known(name):
        name = DEFAULT_THEME
    key = (name, variant == "light")
    got = _LOOK_CACHE.get(key)
    if got is None:
        got = _LOOK_CACHE[key] = dict(zip(look_fields()[0], _look_raw(name, key[1])[0]))
    return got


def backdrop(name, variant, surface):
    """Theme `name`'s backdrop for `surface` ("home" or "desk") in `variant`:
    the records `moy_gfx.backdrop` draws (`moy_ui.theme_backdrop`), b"" where
    the surface has none and is its `desktop` fill."""
    if not theme_known(name):
        name = DEFAULT_THEME
    light = variant == "light"
    if name not in THEME_NAMES and name != _ui_draw._C.theme_installed():
        got = package(name)
        if got is not None and got[0] == 0:
            return got[9][1 if light else 0][0 if surface == "home" else 1]
    return _ui_draw._C.theme_backdrop(name, light, surface)


_LOOK_FIELDS = []


def look_fields():
    """The look's leaves (`moy_ui.theme_look_fields`): (names, kinds, a
    button's leaf names)."""
    if not _LOOK_FIELDS:
        _LOOK_FIELDS.append(_ui_draw._C.theme_look_fields())
    return _LOOK_FIELDS[0]


class Live:
    """The live theme's colours as the shell draws them (docs/look_2026-10.md
    step 5): `tokens` and `content` with each RGB role placed in the block
    (`block`, `used` of its 64 RGB565 words, indices 64..), `near` each
    token's nearest MOY64 index (what theme(), a compiled app and a cart
    canvas are given), `look` the look's leaves with its colours placed in
    the same block, and `buttons` its button leaves as `ui.use_buttons`
    installs them: (app or None, kind, leaves), a paint fill its first stop's
    colour, every colour placed; `fixed` the colours no theme changes
    (FIXED), placed in the block too."""

    __slots__ = ("tokens", "content", "near", "block", "used", "look", "buttons", "fixed")


# The colours no theme changes (docs/media/look_2026-10/DESIGN.md, Components:
# Editors): transparency's grey checker, dark then light, under the sprite
# editor's clear pixels and its colour 0; the black keyline its cursor's ring
# goes round; the white that strokes colour 0 and rings the colour in use.
# The black and the white are also a desk label's over a wallpaper, its
# shadow and its ink (docs/look_2026-10.md step 25).
FIXED = (RGB | 0x6e6e78, RGB | 0x9a9aa4, RGB | 0x000000, RGB | 0xffffff)


_PAINT, _VIBRANCY = PAINT, 0x3000000
_NONE = LOOK_NONE


def _button_rows(look, names, kinds, btn):
    """The look's button leaves, (app or None, kind, [leaves]), and the
    (row, leaf) of each colour among them: the theme's kinds, then its
    app_buttons."""
    leaves, paints = look[0], look[1]
    at = {n: i for i, n in enumerate(names)}
    order = []
    for n in names:
        if n.startswith("buttons.") and n.split(".")[1] not in order:
            order.append(n.split(".")[1])
    rows = [(None, k, [leaves[at["buttons.%s.%s" % (k, f)]] for f in btn]) for k in order]
    rows += [(app, k, list(vals)) for app, k, vals in look[3]]
    colourish = [j for j, f in enumerate(btn)
                 if kinds[at["buttons.normal." + f]] in ("colour", "paint")]
    spots = []
    for r, (_app, _k, vals) in enumerate(rows):
        for j in colourish:
            v = vals[j]
            if _PAINT <= v < _VIBRANCY:
                p = paints[v - _PAINT] if v - _PAINT < len(paints) else None
                v = vals[j] = p[2][0][1] if p and p[2] else _NONE
            elif v >= _VIBRANCY:
                v = vals[j] = _NONE
            if v >= 0:
                spots.append((r, j))
    return rows, spots


def live_colors(name, variant=DEFAULT_VARIANT):
    """The `Live` record of theme `name` in `variant`, cached as
    `theme_colors` is."""
    if not theme_known(name):
        name = DEFAULT_THEME
    if variant not in THEME_VARIANTS:
        variant = DEFAULT_VARIANT
    key = (name, variant)
    got = _LIVE_CACHE.get(key)
    if got is not None:
        return got
    light = variant == "light"
    row = _row(name, light)
    look = _look_raw(name, light)
    names, kinds, btn = look_fields()
    leaves = look[0]
    at = [i for i in range(len(leaves)) if kinds[i] == "colour"]
    rows, spots = _button_rows(look, names, kinds, btn)
    placed, near, block, used = _ui_draw._C.theme_place(
        list(row) + [leaves[i] for i in at] + [rows[r][2][j] for r, j in spots]
        + list(FIXED))
    toks = _ui_draw._C.tokens()
    roles = _ui_draw._C.theme_roles()
    rec = Live()
    rec.tokens, rec.content, near_tok = {}, {}, {}
    for i in range(len(roles)):
        v = placed[i]
        if i < len(toks):
            flag = roles[i] in _THEME_FLAGS
            rec.tokens[roles[i]] = bool(v) if flag else v
            near_tok[roles[i]] = bool(v) if flag else near[i]
        else:
            rec.content[roles[i]] = v
    rec.near = near_tok
    lv = list(leaves)
    for k, i in enumerate(at):
        lv[i] = placed[len(row) + k]
    rec.look = (dict(zip(names, lv)),) + tuple(look[1:5])
    base = len(row) + len(at)
    for k, (r, j) in enumerate(spots):
        rows[r][2][j] = placed[base + k]
    rec.buttons = rows
    at = base + len(spots)
    rec.fixed = tuple(placed[at:at + len(FIXED)])
    rec.block, rec.used = block, used
    _LIVE_CACHE[key] = rec
    return rec


def rgb565_of(v):
    """A colour value's RGB565 word: an index's, a block slot's or RGB's."""
    return _ui_draw._C.theme_rgb(v)[0]


def nearest_of(v):
    """A colour value's nearest MOY64 index."""
    return _ui_draw._C.theme_rgb(v)[2]


def theme_accents():
    """((name, A, deep), ...): the sixteen accents a theme change takes
    (style.json's), for a picker."""
    return _ui_draw.theme_accents()


def theme_presets():
    """((name, dark, light), ...): the accent presets a theme change takes,
    an accent in each mode's value (style.json's), for a picker."""
    return _ui_draw.theme_presets()


def theme_tints():
    """((name, ramp), ...): the eight tints a theme change takes
    (style.json's), for a picker."""
    return _ui_draw.theme_tints()


def theme_content(name, variant=DEFAULT_VARIANT):
    """The CONTENT roles of theme `name` in `variant` -- code syntax and block
    colours (style.json's `theme.content`), which are not in the live token
    row apps read. A shared cached dict, resolved as `theme_colors` is."""
    if not theme_known(name):
        name = DEFAULT_THEME
    if variant not in THEME_VARIANTS:
        variant = DEFAULT_VARIANT
    key = (name, variant)
    cached = _CONTENT_CACHE.get(key)
    if cached is None:
        row = _row(name, variant == "light")
        roles = _ui_draw._C.theme_roles()
        n = len(_ui_draw._C.tokens())
        cached = {}
        for i in range(n, len(roles)):
            cached[roles[i]] = row[i]
        _CONTENT_CACHE[key] = cached
    return cached


def _nibble(ch):
    """One _ICON_ART char -> a palette index, or -1 for transparent ('.')."""
    if ch == ".":
        return -1
    try:
        return int(ch, 16) & 15
    except ValueError:
        return -1


def _default_icon_sheet():
    """Bake `_ICON_ART` into a fresh IconSheet at the `_ICON` slots -- the bar's
    icons under a theme that names no icon set. Each art entry is painted into its 16x16 tile
    via tset, so the result serializes/loads through the same .moygfx hex as any sheet.
    Unmapped/short rows just leave that tile blank (transparent)."""
    sheet = IconSheet()
    t = sheet.TILE
    for kind, rows in _ICON_ART.items():
        n = _ICON.get(kind)
        if n is None or n >= sheet.count:
            continue
        for ly in range(t):
            row = rows[ly] if ly < len(rows) else ""
            for lx in range(t):
                ch = row[lx] if lx < len(row) else "."
                c = _nibble(ch)
                if c >= 0:
                    sheet.tset(n, lx, ly, c)
    sheet.dirty = False
    return sheet


def _cursor_delta(n):
    # n = net pulses this frame on one axis. Precise on a slow roll
    # (1 pulse -> _CURSOR_BASE + _CURSOR_ACCEL px), accelerates super-linearly on a
    # fast roll (the a*a term dominates as pulses-per-frame climbs).
    a = n if n >= 0 else -n
    if a == 0:
        return 0
    d = a * _CURSOR_BASE + _CURSOR_ACCEL * a * a
    return d if n > 0 else -d


def _clamp_scroll(top, cur, visible, count):
    """Nudge a persistent scroll offset `top` the minimum amount needed so `cur`
    stays inside a window of `visible` rows out of `count` total -- move only when
    the cursor exits the current window (a stable scrolloff, not a re-center), then
    clamp to the valid range. The block-editor outline's (_blk_reveal). NOT used by the music editor's _mu_visible_top, which re-centers the
    window on the cursor every call instead of nudging a persistent offset -- a
    different (and intentionally different) scrolling feel, not a third copy of
    this."""
    if cur < top:
        top = cur
    elif cur >= top + visible:
        top = cur - visible + 1
    return max(0, min(top, max(0, count - visible)))


# (_in -- the rect hit-test -- lives in widgets.py, the one shared definition;
# imported above and re-exported so console.py's `from chrome import _in` holds.)


# (Extracted helpers -- ACHIEVEMENTS, Popup, Pmem -- live in
# widgets.py/launcher_layer.py/paint_layer.py and are imported back at the top
# of this file where console._X must still resolve for tests.)


# Boot logo: how long the moybyte splash (Moy + wordmark) holds before the launcher
# is revealed. Armed only by the real boot entries (device run_desktop, interactive
# host), never by unit construction, so tests see the launcher on the first frame().
_SPLASH_MS = 1500
