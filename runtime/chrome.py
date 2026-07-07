"""The console's stateless base layer -- the chrome + geometry the shared Moybyte
console draws with, extracted from console.py so the Workstation kernel is alone in
that file.

Everything here is PURE: the MOY64 palette (NAMES/color), the responsive Layout /
CodeLayout geometry (#39), the pre-literate icon-glyph vocabulary (_GLYPHS /
_blit_glyph), the themeable top-bar IconSheet defaults (_ICON / _ICON_ART /
_default_icon_sheet), and the small helpers (_in / _clamp_scroll / _cursor_delta /
_ticks_* / _err_text / _from_ascii). Nothing touches a Workstation -- these are the
functions/constants shared by console.py AND the surface modules (launcher_layer
imports Layout; every surface gets NAMES/_blit_glyph injected). console.py imports it
all back and re-exports under the pre-extraction `console.X` names, so every caller +
test is unchanged.

Canonical home is runtime/; build.sh stages a copy into the firmware modules/ tree so
the device freezes it (same pattern as editors.py). It depends only on leaf modules --
editors/widgets + the surface geometry constants from bar_layer/settings_layer/
code_layer, none of which import console/chrome -- so there is no cycle. Same
bare-or-package fallback as those modules.
"""

import time

try:
    from editors import CodeEditor, IconSheet
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.editors import CodeEditor, IconSheet

try:
    from widgets import _Blit
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.widgets import _Blit

# Surface geometry constants the Layout classes derive their responsive rects from.
# bar_layer/settings_layer/code_layer OWN these (see their modules); imported here the
# same way console.py imports them, so Layout/CodeLayout reflow off the same numbers.
try:
    from bar_layer import _STATUS_H, _DOCK_SLOTS, _BAR_ICON, _BAR_GAP, _BAR_Y
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.bar_layer import _STATUS_H, _DOCK_SLOTS, _BAR_ICON, _BAR_GAP, _BAR_Y

try:
    from settings_layer import (_SET_ROW_H, _SET_X, _SET_W, _SET_ROW_Y0,
                                 _SET_BACK, _SET_ACH, _SET_TITLE_HIT)
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.settings_layer import (_SET_ROW_H, _SET_X, _SET_W, _SET_ROW_Y0,
                                         _SET_BACK, _SET_ACH, _SET_TITLE_HIT)

try:
    from code_layer import (_CODE_LH, _CODE_X0, _CODE_Y0,
                            _SYM_CELL, _SYM_H, _CODE_SYMBOLS)
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.code_layer import (_CODE_LH, _CODE_X0, _CODE_Y0,
                                    _SYM_CELL, _SYM_H, _CODE_SYMBOLS)


def _ticks_ms():
    try:
        return time.ticks_ms()
    except AttributeError:
        return int(time.time() * 1000)


def _ticks_diff(a, b):
    try:
        return time.ticks_diff(a, b)
    except AttributeError:
        return a - b


def _err_text(exc):
    """A short, kid-readable one-liner for an exception (type: message). Robust
    on MicroPython, whose exceptions sometimes stringify oddly."""
    try:
        name = type(exc).__name__
    except Exception:  # noqa: BLE001
        name = "Error"
    try:
        msg = str(exc)
    except Exception:  # noqa: BLE001
        msg = ""
    return (name + ": " + msg) if msg else name


# (_wrap + _exc_cart_line -- word-wrap the crash text + find a cart traceback line --
# moved to player.py (Stage 2) with the crash panel + Player.start, their only users.)


# (_Blit -- the minimal cursor/composite blittable -- moved to widgets.py, imported
# back above; _from_ascii below + the #39 composite build it.)


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

NAMES = {
    "black": 0, "dark_blue": 1, "dark_purple": 2, "dark_green": 3, "brown": 4,
    "dark_grey": 5, "light_grey": 6, "white": 7, "red": 8, "orange": 9,
    "yellow": 10, "green": 11, "blue": 12, "indigo": 13, "pink": 14, "peach": 15,
}


def color(name_or_index):
    if isinstance(name_or_index, str):
        return NAMES.get(name_or_index, 7)
    return int(name_or_index) & 63


# (The code-editor syntax highlighter -- _highlight + _HL_* -- moved to code_layer.py
# with the rest of the code editor; it was code-only. The Pointer cursor +
# CURSOR_IDLE_MS moved to widgets.py, imported back at the top of this file.)


# --- Pointer UI layout (320x240) -------------------------------------------
# The unified top bar's geometry (Stage 1) -- _STATUS_H, _BAR_ICON/_BAR_GAP/
# _BAR_STRIDE/_BAR_Y, the fixed 320x240 tool-switcher button rects (_SYSMENU_BTN /
# _HOME_BTN / _MENU_BTN / _PAINT_BTN / _MAP_BTN / _BLOCKS_BTN / _MUSIC_BTN),
# _BAR_BATT / _BAR_WIFI / _BAR_CLOCK, and _DOCK_SLOTS / _DOCK_GLYPH / _DOCK_LABEL --
# now lives in bar_layer.py (its own surface, #46) and is imported back at the top of
# this file, so console._X still resolves for Layout + the golden harness/tests.
# (The #71 pause-screen button geometry was retired in Stage 5 along with the pause
# machinery -- the Player exits on hold-BACKSPACE now.)
# The cards-menu geometry (_CARD_*) lives in cards_layer.py (its own surface, #3/#15)
# and is imported back at the top of this file, so console._X still resolves for tests.
# (GO/CODE/CLOSE dissolved into the unified bar in fix B -- PLAY/Code-tab/context X.)
# --- Desktop shell (#28): home = wallpaper + cart icon grid + dock ----------
# The home screen is now a Picotron/TIC-80-style desktop: a wallpaper backdrop, a
# grid of tappable cart icons, the unified 18px top bar (clock + wifi/batt/gear +
# NEW/DUP/DEL management icons), and (in Settings) the bottom dock. The top bar's
# icons are 16x16 sprites from the editable IconSheet (Stage 1); the rest of the
# chrome uses the indexed API + petme128 font + the _glyph vocabulary, so host ==
# device. (_STATUS_H lives in bar_layer.py, imported back at the top of this file.)
_DOCK_Y = 218           # bottom dock strip top
_DOCK_H = 22
# Cart icon grid: a page of COLS x ROWS tiles between the status strip and dock.
_ICON_COLS = 4
_ICON_ROWS = 2
_ICON_W = 70            # tile footprint (icon box + label)
_ICON_H = 84
_ICON_GAP_X = 6
_ICON_GAP_Y = 6
_ICON_X0 = 8            # left margin so the COLS tiles + gaps center in 320px
_ICON_Y0 = _STATUS_H + 8
_ICON_BOX = 40          # the inner art box of a tile (the tappable icon proper)
# (The home NEW/DUP/DEL management icons are hit-tested via Layout's responsive
# lay.new_btn/dup_btn/del_btn -- the old fixed _NEW_BTN/_DUP_BTN/_DEL_BTN placeholders
# were dead, so they were dropped with the bar-geometry move to bar_layer.py.)
# Page chevrons (when more carts than one page): tap to flip pages.
_PAGE_PREV = (2, 110, 14, 24)
_PAGE_NEXT = (304, 110, 14, 24)
# Bottom dock (persistent tool switcher, TIC-80 style): one tap to jump between
# home / code / draw / map / run / settings. Six evenly-spaced slots across 320px.
# (_DOCK_SLOTS/_DOCK_GLYPH/_DOCK_LABEL live in bar_layer.py, imported back above;
# the per-slot width/gap/geometry below stays here for Layout.)
_DOCK_W = 52
_DOCK_GAP = 1
_DOCK_X0 = 2
# Settings screen (#28) geometry (_SET_*) lives in settings_layer.py (its own surface)
# and is imported back at the top of this file, so console._X still resolves for the
# Layout class + tests.
# Code editor (#24) geometry (_CODE_*/_ED_*/_SYM_*/_CODE_SYMBOLS) lives in code_layer.py
# (its own surface) and is imported back at the top of this file, so console._X resolves
# for the CodeLayout class + the crash panel (_CODE_LH) + tests. (_CODE_AREA is
# re-exported too -- test_responsive_editors pins lay.code_area() against it.)
# Paint editor (#4/#30) geometry (_PG_*/_SW*/_SPR_*/_PAINT_*) lives in paint_layer.py
# (its own surface) and is imported back at the top of this file, so console._X still
# resolves for tests + tools. (_PAINT_BTN -- the desktop overlay -- is in bar_layer.py.)
# Map (tilemap) editor (#32) constants + MapEditorUI now live in map_editor_ui.py
# (imported above) -- this used to be ~80 lines of module-level constants right here.
# Block editor (#29 Part 2) constants, BlockLayout, and BlockEditorUI now live in
# block_editor_ui.py (imported above) -- this used to be ~120 lines of module-level
# constants + a class right here.
# Music/sound editor (#50) constants + MusicEditorUI now live in music_editor_ui.py
# (imported above) -- this used to be ~60 lines of module-level constants right here.
# Trackball cursor sensitivity (#2). _CURSOR_BASE is the per-pulse step; the
# quadratic _CURSOR_ACCEL term adds light acceleration so a fast roll crosses the
# 320px screen in far fewer pulses while a slow, single-pulse roll stays precise.
# These are a FEEL tweak meant to be finalized on real hardware (the trackball's
# pulses-per-revolution sets the true "rolls to cross").  Before: BASE=4, ACCEL=1
# (1 pulse -> 5px, ~64 px/s at a steady 1 pulse/frame). After: BASE=7, ACCEL=2
# (1 pulse -> 9px; a 6-pulse flick -> 6*7 + 2*36 = 114px, so ~3 brisk rolls cross).
_CURSOR_BASE = 7
_CURSOR_ACCEL = 2

# Baseline the responsive layout reproduces EXACTLY (#39 graceful degradation).
_BASE_W = 320
_BASE_H = 240
_FONT_W = 8                 # petme128 cell width at scale 1 (one char advance)
# (The letterbox/bezel fill _VIEWPORT_BEZEL (#39) moved to wm.py with the viewport
# composite it belongs to -- FullscreenStackWM.composite_game is its only user.)


class Layout:
    """Responsive desktop-shell geometry (#39): the status strip, cart icon grid,
    page chevrons, management buttons, bottom dock, and Settings rows derived from
    the SYSTEM canvas size (w, h) + the system font scale (1/2/3) -- instead of the
    hand-placed 320x240 constants. The desktop reflows to fill a larger panel and
    the chrome scales with the font.

    The single hard contract: at (w, h, fs) == (320, 240, 1) every field equals the
    frozen module constant, byte-for-byte -- the T-Deck path is unchanged. The exact
    baseline is reproduced VERBATIM (the `_base` branch) rather than re-derived, so
    no reflow formula's integer-floor can drift a pixel at the default size; the
    responsive formulas only run on a larger canvas / bigger font. The editors stay
    a fixed 320x240 viewport in step 1, so their constants are NOT routed here.

    `font_w` is the on-screen char-cell width (8 * fs) so callers center/space text
    that the SystemCanvas renders at `fs`; chrome heights/margins scale with fs too."""

    def __init__(self, w=_BASE_W, h=_BASE_H, font_scale=1):
        self.w = int(w)
        self.h = int(h)
        self.fs = max(1, int(font_scale))
        fs = self.fs
        self.font_w = _FONT_W * fs
        self._base = (self.w == _BASE_W and self.h == _BASE_H and fs == 1)

        # -- status strip + dock (heights/positions scale with the font) --------
        self.status_h = _STATUS_H * fs
        self.dock_h = _DOCK_H * fs
        self.dock_y = self.h - self.dock_h
        n = len(_DOCK_SLOTS)
        if self._base:
            self.dock_w, self.dock_gap, self.dock_x0 = _DOCK_W, _DOCK_GAP, _DOCK_X0
        else:
            self.dock_gap = _DOCK_GAP * fs
            # Fill the width with n evenly-spaced slots, snug to the edges.
            self.dock_w = max(_FONT_W,
                              (self.w - 2 * _DOCK_X0 - (n - 1) * self.dock_gap) // n)
            span = n * self.dock_w + (n - 1) * self.dock_gap
            self.dock_x0 = max(0, (self.w - span) // 2)

        # -- cart icon grid (reflows COLS x ROWS to fill the band) ---------------
        # The launcher/home screen no longer draws the bottom dock (#46), so the cart
        # grid reclaims the height below the status strip down to the canvas floor
        # (grid_bottom), not just down to the dock. (Settings keeps the dock, so its
        # panel still stops at dock_y -- that bound is unchanged.)
        self.icon_w = _ICON_W * fs
        self.icon_h = _ICON_H * fs
        self.icon_gap_x = _ICON_GAP_X * fs
        self.icon_gap_y = _ICON_GAP_Y * fs
        self.icon_box = _ICON_BOX * fs
        self.icon_y0 = self.status_h + 8 * fs
        self.grid_bottom = self.h - 4 * fs           # launcher grid floor (no dock)
        if self._base:
            self.cols, self.rows = _ICON_COLS, _ICON_ROWS
            self.icon_x0 = _ICON_X0
        else:
            self.cols = max(1, (self.w + self.icon_gap_x) //
                            (self.icon_w + self.icon_gap_x))
            band = self.grid_bottom - self.icon_y0
            self.rows = max(1, (band + self.icon_gap_y) //
                            (self.icon_h + self.icon_gap_y))
            grid_w = self.cols * self.icon_w + (self.cols - 1) * self.icon_gap_x
            self.icon_x0 = max(0, (self.w - grid_w) // 2)
        self.page = self.cols * self.rows

        # -- unified top bar: icon size + clusters (Stage 1) -------------------
        # Every bar control is a 16x16 IconSheet sprite (16px icons, 1px margin in the
        # 18px bar -> y = _BAR_Y). Icons scale with the font (24px at fs=2, etc.) so
        # the bar grows on a larger system canvas. status_gh stays the 12*fs glyph box
        # the non-bar chrome (dock/settings/toasts) still uses via _glyph.
        self.status_gh = 12 * fs                      # legacy glyph box (dock/settings)
        ic = _BAR_ICON * fs                           # bar icon side, scaled
        stride = ic + _BAR_GAP * fs                   # left-edge step between bar icons
        self.bar_icon = ic
        self.bar_stride = stride
        edge = 2 * fs                                 # margin from the canvas edges

        # -- right zone (OS-owned, Stage 4 #46 zoned bar -- the macOS-menu-bar
        # model): batt hard against the right edge, then wifi, then the ≡ system-
        # menu toggle (moved off the left edge so every OS control lives on ONE
        # side), then a slot RESERVED for the Stage-5 context X (not drawn/tapped
        # yet -- carved out now so its arrival doesn't reflow the rest of this
        # cluster again), then the clock text filling the remaining space to
        # their left.
        self.batt_btn = (self.w - edge - ic, _BAR_Y, ic, ic)
        self.wifi_btn = (self.batt_btn[0] - stride, _BAR_Y, ic, ic)
        self.sysmenu_btn = (self.wifi_btn[0] - stride, _BAR_Y, ic, ic)
        self.context_x_btn = (self.sysmenu_btn[0] - stride, _BAR_Y, ic, ic)  # reserved
        self.clock_w = 5 * self.font_w                # "HH:MM" (5 chars)
        self.clock_x = max(edge, self.context_x_btn[0] - edge - self.clock_w)

        # -- left zone (Stage 4: fully LENT to the active app's draw_zone/zone_tap
        # -- launcher/Settings/the Editor): a row of icon slots starting right at the
        # left edge now that ≡ isn't there any more. Cart management (create/copy/
        # delete) now lives in the Editor picker's zone, not the launcher's -- DUP/DEL
        # are drawn at dup_btn/del_btn there (new_btn's slot is unused: "+ New" is a
        # pinned grid tile, not a bar icon).
        self.new_btn = (edge, _BAR_Y, ic, ic)
        self.dup_btn = (self.new_btn[0] + stride, _BAR_Y, ic, ic)
        self.del_btn = (self.dup_btn[0] + stride, _BAR_Y, ic, ic)

        # -- title/selected-name slot: between the (picker's) DUP/DEL cluster and the
        # clock. Used by EditorPickerLayer's zone title ("PICK A PROJECT"); the
        # launcher's zone (just the selected cart's name, no icons) starts at
        # zone_left's own edge instead (see LauncherHomeLayer.draw_zone).
        self.status_name_x = self.del_btn[0] + self.del_btn[2] + edge
        self.status_name_maxc = max(
            4, (self.clock_x - edge - self.status_name_x) // self.font_w)
        # The full lent left zone (Stage 4): from the left edge to just before the
        # right zone's clock text -- the rect BarLayer hands to draw_zone/zone_tap.
        self.zone_left = (edge, _BAR_Y, max(0, self.clock_x - 2 * edge), ic)

        # -- page chevrons (centered vertically in the icon band) ----------------
        if self._base:
            self.page_prev, self.page_next = _PAGE_PREV, _PAGE_NEXT
        else:
            cy = (self.icon_y0 + self.grid_bottom) // 2 - 12 * fs
            self.page_prev = (2, cy, 14 * fs, 24 * fs)
            self.page_next = (self.w - 2 - 14 * fs, cy, 14 * fs, 24 * fs)

        # -- Settings rows + panel (scale row height with the font) --------------
        self.set_row_h = _SET_ROW_H * fs
        if self._base:
            self.set_x = _SET_X
            self.set_w = _SET_W
            self.set_row_y0 = _SET_ROW_Y0
            self.settings_panel = (8, 16, 304, 198)         # frozen baseline
            self.set_back = _SET_BACK
            self.set_ach = _SET_ACH
            self.set_title_hit = _SET_TITLE_HIT
        else:
            # The Settings panel fills the band between the status strip and dock.
            py0 = self.status_h + 2 * fs
            ph = self.dock_y - py0 - 2 * fs
            self.settings_panel = (8 * fs, py0, self.w - 16 * fs, ph)
            self.set_x = self.settings_panel[0] + 10 * fs
            self.set_w = self.settings_panel[2] - 20 * fs
            self.set_row_y0 = py0 + 24 * fs
            pr = self.settings_panel[0] + self.settings_panel[2]   # panel right edge
            self.set_back = (pr - 20 * fs, py0 + 2 * fs, 18 * fs, 14 * fs)
            self.set_ach = (pr - 46 * fs, py0 + 2 * fs, 22 * fs, 14 * fs)
            self.set_title_hit = (self.settings_panel[0] + 14 * fs, py0 + 2 * fs,
                                  10 * self.font_w, 16 * fs)

    # -- derived rects (mirror the old module-constant arithmetic) ----------
    def dock_slot_rect(self, k):
        x = self.dock_x0 + k * (self.dock_w + self.dock_gap)
        return (x, self.dock_y + 1, self.dock_w, self.dock_h - 2)

    def settings_row_rect(self, i):
        return (self.set_x, self.set_row_y0 + i * self.set_row_h,
                self.set_w, self.set_row_h - 2)

    def tile_rect(self, i, page):
        """Grid-cell rect for cart index `i` on `page`, or None if off that page."""
        start = page * self.page
        if i < start or i >= start + self.page:
            return None
        k = i - start
        col = k % self.cols
        row = k // self.cols
        x = self.icon_x0 + col * (self.icon_w + self.icon_gap_x)
        y = self.icon_y0 + row * (self.icon_h + self.icon_gap_y)
        return (x, y, self.icon_w, self.icon_h)

    def clock_hit(self):
        # The clock-text region in the top bar's right cluster (Time Traveler egg #21).
        return (self.clock_x, 0, self.clock_w, self.status_h)


class CodeLayout:
    """Responsive code-editor geometry (#39 step 2): the top bar (title + run/save/
    close icons), the COLS x ROWS text grid, the caret/gutter, and the bottom symbol
    palette -- all derived from the SYSTEM canvas size (w, h) + font scale, instead
    of the hand-placed 320x240 constants. On a larger panel the editor shows MORE
    visible lines + WIDER columns; at a bigger font everything (cell, bar, palette)
    grows with the text.

    The single hard contract (mirrors `Layout`): at (w, h, fs) == (320, 240, 1)
    every field equals the frozen `_CODE_*`/`_SYM_*`/`_ED_*` module constant, byte
    for byte -- so the degradation path is exactly today. That baseline is
    reproduced VERBATIM (the `_base` branch); the responsive formulas only run on a
    larger canvas / bigger font.

    `cell` is the on-screen char-cell width (8 * fs); `lh` the line height. `cols` /
    `rows` are how many fit -- the CodeEditor's view window adopts them so it scrolls
    the right span."""

    def __init__(self, w=_BASE_W, h=_BASE_H, font_scale=1):
        self.w = int(w)
        self.h = int(h)
        self.fs = max(1, int(font_scale))
        fs = self.fs
        self.cell = _FONT_W * fs                  # char-cell width (8*fs)
        self.lh = _CODE_LH * fs                   # line height
        self._base = (self.w == _BASE_W and self.h == _BASE_H and fs == 1)
        # -- symbol palette (bottom strip): one cell per coding symbol -----------
        self.sym_cell = _SYM_CELL * fs
        self.sym_h = _SYM_H * fs
        self.sym_y = self.h - self.sym_h
        self.sym_area = (0, self.sym_y, self.sym_cell * len(_CODE_SYMBOLS), self.sym_h)
        # -- code area origin (below the unified 18px bar) -----------------------
        # The old RUN/SAVE/CLOSE top-band icons are gone (Stage-4 bar rollout): the
        # unified zoned bar owns the top 18px, so CodeLayout no longer carries their
        # rects. y0 stays 18 (the text begins right under the bar), so the body is
        # fullscreen text + the symbol palette with no chrome of its own.
        self.x0 = _CODE_X0 * fs
        self.y0 = _CODE_Y0 * fs
        # -- the COLS x ROWS text grid (fills between the bar + palette) ---------
        if self._base:
            self.cols = CodeEditor.COLS          # 38
            self.rows = CodeEditor.ROWS          # 20
        else:
            avail_w = self.w - self.x0
            self.cols = max(8, avail_w // self.cell)
            avail_h = self.sym_y - self.y0
            self.rows = max(4, avail_h // self.lh)

    def code_area(self):
        return (self.x0, self.y0, self.cols * self.cell, self.rows * self.lh)


# BlockLayout (#39 step 2) now lives in block_editor_ui.py (imported above).


# --- Button icon glyphs (the pre-literate icon vocabulary) ------------------
# 1-bit, recolorable pixel bitmaps designed on a 12x12 grid at the native button
# size (boxes are 14-16px), then centered in each button's rect and blitted in
# the requested palette color via the indexed primitives only -- so they render
# identically on host (runtime/canvas.py) and the frozen device console. Each
# glyph is a tuple of 12 ints: row r, bit (11 - col) set => pixel on. Constant
# (no per-frame allocation; freezes into firmware at ~15*12 ints).
#
# Hand-authored at this grid, adapted from the Pixelarticons set
# (https://pixelarticons.com, MIT License (c) Gerrit Halfmann) -- a purpose-built
# pixel-icon vocabulary; shapes traced down to 12x12 and hand-cleaned for
# legibility at button size. MIT permits this use; this comment is the notice.
_GLYPH_SIZE = 12
_GLYPHS = {
    "run":    (0x000, 0x180, 0x1C0, 0x1E0, 0x1F0, 0x1F8, 0x1F8, 0x1F0, 0x1E0, 0x1C0, 0x180, 0x000),
    "save":   (0x000, 0x7FE, 0x402, 0x5FA, 0x402, 0x402, 0x4F2, 0x492, 0x492, 0x492, 0x7FE, 0x000),
    "close":  (0x000, 0x204, 0x30C, 0x198, 0x0F0, 0x060, 0x060, 0x0F0, 0x198, 0x30C, 0x204, 0x000),
    "edit":   (0x01E, 0x03E, 0x07C, 0x0F8, 0x0F0, 0x1E0, 0x3C0, 0x780, 0x700, 0x600, 0x400, 0x000),
    "paint":  (0x006, 0x00C, 0x018, 0x030, 0x060, 0x0E0, 0x1F0, 0x1F0, 0x1F0, 0x0E0, 0x000, 0x000),
    "home":   (0x000, 0x060, 0x0F0, 0x1F8, 0x3FC, 0x7FE, 0x204, 0x264, 0x264, 0x264, 0x3FC, 0x000),
    "minus":  (0x000, 0x000, 0x000, 0x000, 0x000, 0x7FE, 0x7FE, 0x000, 0x000, 0x000, 0x000, 0x000),
    "plus":   (0x000, 0x000, 0x060, 0x060, 0x060, 0x7FE, 0x7FE, 0x060, 0x060, 0x060, 0x000, 0x000),
    "turtle": (0x000, 0x0F8, 0x1FC, 0x3FE, 0x3FE, 0x3FE, 0x3FE, 0x2ED, 0x653, 0x000, 0x000, 0x000),
    "rabbit": (0x220, 0x220, 0x220, 0x360, 0x1C0, 0x3E0, 0x7F4, 0x7F0, 0x3E0, 0x000, 0x000, 0x000),
    "star":   (0x000, 0x060, 0x060, 0x0F0, 0xFFC, 0x7F8, 0x3F0, 0x3F0, 0x618, 0x618, 0x000, 0x000),
    "dot":    (0x000, 0x000, 0x000, 0x0F0, 0x1F8, 0x1F8, 0x1F8, 0x0F0, 0x000, 0x000, 0x000, 0x000),
    "get":    (0x000, 0x060, 0x060, 0x060, 0x264, 0x1F0, 0x0E0, 0x040, 0x7FE, 0x402, 0x7FE, 0x000),
    "put":    (0x000, 0x040, 0x0E0, 0x1F0, 0x264, 0x060, 0x060, 0x060, 0x7FE, 0x402, 0x7FE, 0x000),
    "heart":  (0x000, 0x30C, 0x79E, 0x7FE, 0x7FE, 0x7FE, 0x3FC, 0x1F8, 0x0F0, 0x060, 0x000, 0x000),
    # "map": a 3x3 tile grid (the tilemap editor's nav/open icon, #32) -- full
    # h-lines at rows 1/5/9, v-lines at cols 1/5/9, so it reads as a placed grid.
    "map":    (0x000, 0x7FE, 0x444, 0x444, 0x444, 0x7FE, 0x444, 0x444, 0x444, 0x7FE, 0x000, 0x000),
    # "blocks": two stacked Scratch-style notched bricks (the #29 block-editor icon).
    "blocks": (0x000, 0x3F8, 0x7FC, 0x7FC, 0x3F8, 0x000, 0x1FC, 0x3FE, 0x3FE, 0x1FC, 0x000, 0x000),
    # Desktop-shell dock icons (#28). "code" = angle brackets </>; "gear" = a
    # settings cog; "note" = a music note (the #16 music slot, greyed until it
    # lands); "app" = a generic window (default cart icon).
    "code":   (0x000, 0x048, 0x08C, 0x118, 0x230, 0x230, 0x118, 0x08C, 0x048, 0x000, 0x000, 0x000),
    "gear":   (0x000, 0x060, 0x276, 0x3FC, 0x1F8, 0x18C, 0x18C, 0x1F8, 0x3FC, 0x276, 0x060, 0x000),
    "note":   (0x000, 0x07E, 0x042, 0x042, 0x042, 0x042, 0x0C6, 0x1CE, 0x1CE, 0x0C4, 0x000, 0x000),
    # "music": a beamed pair of eighth notes -- the #50 sound-editor switcher's
    # fallback glyph (the bar normally blits the 16x16 IconSheet "music" sprite).
    "music":  (0x000, 0x07E, 0x042, 0x042, 0x042, 0x0C2, 0x1C2, 0x1CE, 0x00E, 0x00C, 0x000, 0x000),
    "app":    (0x000, 0x7FE, 0x402, 0x7FE, 0x402, 0x402, 0x402, 0x402, 0x402, 0x402, 0x7FE, 0x000),
    "wifi":   (0x000, 0x000, 0x1F8, 0x204, 0x0F0, 0x108, 0x060, 0x000, 0x060, 0x000, 0x000, 0x000),
    "batt":   (0x000, 0x000, 0x180, 0x7FE, 0x7FE, 0x7FE, 0x7FE, 0x7FE, 0x7FE, 0x000, 0x000, 0x000),
    # Achievements (#21): "trophy" (the unlocked-badge cue), "lock" (a locked/secret
    # entry), "smile" (the "Oh! You found me!" Easter-egg character), "key" (the
    # explorer reward), and "spark" (the celebratory confetti pip).
    "trophy": (0x000, 0x7FE, 0x7FE, 0x3FC, 0x3FC, 0x1F8, 0x0F0, 0x060, 0x060, 0x1F8, 0x1F8, 0x000),
    "lock":   (0x000, 0x0F0, 0x108, 0x108, 0x108, 0x7FE, 0x7FE, 0x792, 0x792, 0x7FE, 0x7FE, 0x000),
    "smile":  (0x0F0, 0x308, 0x404, 0x492, 0x492, 0x404, 0x444, 0x438, 0x404, 0x308, 0x0F0, 0x000),
    "key":    (0x000, 0x1C0, 0x220, 0x220, 0x1C0, 0x080, 0x080, 0x0E0, 0x080, 0x0E0, 0x000, 0x000),
    "spark":  (0x000, 0x060, 0x060, 0x060, 0x366, 0x1FC, 0x060, 0x1FC, 0x366, 0x060, 0x060, 0x000),
    # "menu": the hamburger (≡) -- three full-width bars. The system-menu (#52) toggle;
    # always a _glyph bitmap (NOT a themeable IconSheet slot) so it can't go blank on a
    # device whose saved theme predates this icon.
    "menu":   (0x000, 0x000, 0x7FE, 0x7FE, 0x000, 0x7FE, 0x7FE, 0x000, 0x7FE, 0x7FE, 0x000, 0x000),
    # "projects": a 2x2 grid of tiles -- the Editor's "back to the project-picker"
    # affordance (spec shell_ux_v1.md). Not an IconSheet slot, so it renders via the
    # _glyph fallback (ws._icon falls back to this 12x12 bitmap for kinds not in _ICON).
    "projects": (0x000, 0x7BC, 0x7BC, 0x7BC, 0x7BC, 0x000, 0x000, 0x7BC, 0x7BC, 0x7BC, 0x7BC, 0x000),
}


def _blit_glyph(cv, kind, rect, c):
    """Draw an icon glyph (no background) centered in `rect`, in color `c`, onto
    canvas `cv`. The shared pre-literate icon vocabulary -- a 12x12 1-bit pixel
    bitmap (see _GLYPHS) blitted via the indexed primitives only (rect spans), so
    it renders identically on host and device. Unknown kinds draw NOTHING, so
    every caller can keep a text label as the guaranteed fallback. Module-level so
    both Workstation._glyph and Launcher (which only holds a canvas) share one
    implementation -- the glyph encoding lives in exactly one loop."""
    bits = _GLYPHS.get(kind)
    if bits is None:                                # unknown -> nothing (fallback contract)
        return
    x, y, w, h = rect
    n = _GLYPH_SIZE
    # Scale the icon mask with the canvas's system font scale (#39) so glyphs grow
    # alongside text on a larger system canvas. A plain (game) Canvas has font_scale
    # 1, so this is byte-identical to the original 1x path everywhere else.
    fs = getattr(cv, "font_scale", 1)
    if fs < 1:
        fs = 1
    span = n * fs
    ox = x + (w - span) // 2                          # center the (scaled) mask in the rect
    oy = y + (h - span) // 2
    for r in range(n):
        row = bits[r]
        if not row:
            continue
        yy = oy + r * fs
        run = 0                                     # length of the current on-run
        for col in range(n):                        # walk L->R, coalescing runs
            if row & (1 << (n - 1 - col)):
                run += 1
            elif run:
                cv.rect(ox + (col - run) * fs, yy, run * fs, fs, c)
                run = 0
        if run:
            cv.rect(ox + (n - run) * fs, yy, run * fs, fs, c)


# --- the unified top bar's icon theme (Stage 1) -----------------------------
#
# The top bar's chrome controls are 16x16 sprites drawn from an EDITABLE IconSheet
# (so the bar is themeable), not the hardcoded _GLYPHS bitmaps -- which collapses the
# ~120 glyph rect-spans/frame the labeled button rows cost into ~12 cached sprite
# blits (a measured ~15ms/frame device win). `_ICON` is the slot map: a chrome kind ->
# its sprite id in the 8x4 IconSheet (row-major). The IconSheet is loaded from
# system_icons.moygfx when present, else baked from `_ICON_ART` below. The _glyph
# vocabulary stays for NON-chrome uses (the cards/paint/blocks editors).
_ICON = {
    "home": 0, "edit": 1, "code": 2, "paint": 3, "map": 4, "blocks": 5,
    "gear": 6, "wifi": 7, "batt": 8, "new": 9, "dup": 10, "del": 11,
    "close": 12, "run": 13, "save": 14, "music": 15,
    "moy": 16,          # the moybyte mascot (boot logo); not a bar control
    "wifi_off": 17,     # wifi-with-a-red-slash: the right-zone status glyph when
                        # there's NO connection (ws._wifi_icon_kind picks it, #Part3)
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

# Bump whenever the baked _ICON_ART above changes: a saved system_icons.moygfx theme
# written at an OLDER version is treated as stale and re-seeded to these new defaults
# at load (mirrors cart versioning, #47), so an already-themed device/desktop picks up
# new icons without a manual wipe. A bump discards a user's custom icon edits, exactly
# like a built-in cart re-seed. (v1 = the first full restyle; v2 = added the "moy"
# mascot slot for the boot logo; v3 = added the "wifi_off" no-connection status slot.)
_ICON_VERSION = 3


def _nibble(ch):
    """One _ICON_ART char -> a palette index, or -1 for transparent ('.')."""
    if ch == ".":
        return -1
    try:
        return int(ch, 16) & 15
    except ValueError:
        return -1


def _default_icon_sheet():
    """Bake `_ICON_ART` into a fresh IconSheet at the `_ICON` slots -- the theme used
    when no system_icons.moygfx exists. Each art entry is painted into its 16x16 tile
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
    clamp to the valid range. Shared by the settings list (_settings_scroll) and
    the block-editor outline (_blk_reveal) -- verified identical clamp math at both
    call sites. NOT used by the music editor's _mu_visible_top, which re-centers the
    window on the cursor every call instead of nudging a persistent offset -- a
    different (and intentionally different) scrolling feel, not a third copy of
    this."""
    if cur < top:
        top = cur
    elif cur >= top + visible:
        top = cur - visible + 1
    return max(0, min(top, max(0, count - visible)))


def _in(px, py, rect):
    x, y, w, h = rect
    return x <= px < x + w and y <= py < y + h


# (_line_cells -- the drag-to-draw Bresenham helper -- moved to paint_layer.py with
# the rest of the paint editor; it was paint-only.)


# (_TYPE_GLYPH / _TYPE_COLOR -- the launcher tile-type icon/color maps -- moved to
# launcher_layer.py with the Launcher grid.)


# (The Achievements milestone tracker (#21) + its ACHIEVEMENTS catalog moved to
# widgets.py; ACHIEVEMENTS is imported back at the top of this file for the
# AchievementsUI construction + tests.)


# (The Launcher grid class moved to launcher_layer.py alongside LauncherHomeLayer;
# its instance is still ws.launcher, built in __init__ -- the single source.)


# (Pmem (cart persistent RAM), the _SilentAudio no-op backend, and the reusable
# Popup dropdown primitive (#52) moved to widgets.py, imported back at the top of
# this file.)


# Boot logo: how long the moybyte splash (Moy + wordmark) holds before the launcher
# is revealed. Armed only by the real boot entries (device run_desktop, interactive
# host), never by unit construction, so tests see the launcher on the first frame().
_SPLASH_MS = 1500
