"""The shared KidCode v0.4 console UI -- launcher + desktop + cards/code/paint
editors + the trackball/touch Pointer. Backend-agnostic: it draws through an
injected `canvas` (host Canvas or device DeviceCanvas -- identical TIC-80 API +
petme128 font) and persists through an injected cart store + make_api, so the
host sim and the T-Deck render the SAME pixels from this one file.

Canonical home is runtime/; build.sh stages a copy into the firmware modules/
tree so the device freezes it (same pattern as editors.py). Keep it dependency-
free apart from the shared editor cores below.
"""

import time

from audio import AudioBank, AudioEngine, MusicTrack, SFX
from editors import (BlockEditor, CodeEditor, IconSheet, MapEditor, MusicEditor,
                     PaintEditor, SpriteSheet, TileMap)

# The block vocabulary/compiler (#29). Imported under whichever name it's known by:
# bare `blocks` on the device (frozen top-level) and on the host once host_app has
# aliased it, or `runtime.blocks` when a test loads console/kid_runtime directly
# without that alias (the device path is plain `import blocks`). Mirrors
# kid_carts._import_blocks so neither module hard-depends on import order.
try:
    import blocks as _blocks_mod
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime import blocks as _blocks_mod


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


def _wrap(text, cols):
    """Word-wrap `text` into a list of lines no wider than `cols` chars. A single
    word longer than `cols` is hard-split so it still fits the panel."""
    if cols < 1:
        cols = 1
    out = []
    for para in str(text).split("\n"):
        line = ""
        for word in para.split(" "):
            while len(word) > cols:                 # hard-split an over-long token
                if line:
                    out.append(line)
                    line = ""
                out.append(word[:cols])
                word = word[cols:]
            if not line:
                line = word
            elif len(line) + 1 + len(word) <= cols:
                line = line + " " + word
            else:
                out.append(line)
                line = word
        out.append(line)
    return out


def _exc_cart_line(exc, fname="<cart>"):
    """Best-effort: the 1-based source line INSIDE the cart where `exc` was
    raised, or None -- so a runtime crash can drop the kid on the offending line
    (#24), like a syntax error does. Both backends rely on the cart being
    compiled with the filename `fname` (see _start). Host (CPython) walks the
    traceback objects; the device (MicroPython, which exposes no tb objects)
    parses sys.print_exception's rendered output. The DEEPEST cart frame wins."""
    tb = getattr(exc, "__traceback__", None)
    line = None
    while tb is not None:
        try:
            if tb.tb_frame.f_code.co_filename == fname:
                line = tb.tb_lineno
        except AttributeError:
            pass
        tb = tb.tb_next
    if line is not None:
        return line
    try:
        import sys
        import io
        buf = io.StringIO()
        sys.print_exception(exc, buf)              # MicroPython only
        for ln in buf.getvalue().split("\n"):
            if fname in ln:
                p = ln.find("line ")
                if p >= 0:
                    num = ""
                    for ch in ln[p + 5:]:
                        if "0" <= ch <= "9":
                            num += ch
                        elif num:
                            break
                    if num:
                        line = int(num)            # keep the last (deepest) match
    except Exception:  # noqa: BLE001
        pass
    if line is not None:
        return line
    return getattr(exc, "lineno", None)            # SyntaxError caught at compile


class _Blit:
    """Minimal blittable for the cursor sprite (canvas.spr reads only these)."""
    def __init__(self, w, h, pix, transparent=-1):
        self.w = w
        self.h = h
        self.pix = pix
        self.transparent = transparent


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
_TYPE_COLOR = {"wallpaper": 12, "game": 8, "app": 11, "tool": 9}  # index by type


def color(name_or_index):
    if isinstance(name_or_index, str):
        return NAMES.get(name_or_index, 7)
    return int(name_or_index) & 63


# --- code-editor syntax highlighting (#24) ---------------------------------
# A tiny, MicroPython-safe tokenizer: scans one source line char-by-char and
# returns a per-character list of KID64 palette indices, so the code view draws
# colored runs without any re/tokenize dependency (those are heavy/absent on the
# device). Token classes map to:
_HL_TEXT = 6        # light_grey -- identifiers, operators, punctuation (default)
_HL_KEYWORD = 12    # blue
_HL_STRING = 11     # green
_HL_NUMBER = 9      # orange
_HL_COMMENT = 5     # dark_grey
_HL_BUILTIN = 14    # pink -- the cart drawing verbs stand out

_HL_KEYWORDS = (
    "False", "None", "True", "and", "as", "assert", "break", "class",
    "continue", "def", "del", "elif", "else", "except", "finally", "for",
    "from", "global", "if", "import", "in", "is", "lambda", "nonlocal", "not",
    "or", "pass", "raise", "return", "try", "while", "with", "yield",
)
# Cart-API verbs + the common builtins a kid actually types. Keep roughly in
# sync with make_api (host_app / kid_runtime); an extra name here is harmless.
_HL_BUILTINS = (
    "cls", "pix", "pset", "line", "rect", "rectb", "circ", "circb", "spr",
    "map", "mget", "mset",
    "print", "btn", "btnp", "touch", "mouse", "key", "keyp", "time", "pmem",
    "cfg", "col", "rnd", "flr", "abs", "min",
    "max", "sin", "cos", "range", "len", "int", "str", "float", "round", "sqrt",
)


def _is_alpha(ch):
    return ch == "_" or ("a" <= ch <= "z") or ("A" <= ch <= "Z")


def _highlight(line):
    """Return a list of palette indices, one per character of `line` (#24).
    Hand-rolled scanner -- no regex/tokenize, so it runs under MicroPython."""
    n = len(line)
    out = [_HL_TEXT] * n
    i = 0
    while i < n:
        ch = line[i]
        if ch == "#":                          # comment to end of line
            while i < n:
                out[i] = _HL_COMMENT
                i += 1
            break
        if ch == '"' or ch == "'":             # string literal (single line)
            q = ch
            out[i] = _HL_STRING
            i += 1
            while i < n:
                out[i] = _HL_STRING
                if line[i] == "\\" and i + 1 < n:   # escape: consume next char too
                    i += 1
                    out[i] = _HL_STRING
                    i += 1
                    continue
                if line[i] == q:
                    i += 1
                    break
                i += 1
            continue
        if "0" <= ch <= "9":                   # number literal
            while i < n and (("0" <= line[i] <= "9") or line[i] == "." or line[i] == "x"):
                out[i] = _HL_NUMBER
                i += 1
            continue
        if _is_alpha(ch):                      # identifier / keyword / builtin
            j = i
            while j < n and (_is_alpha(line[j]) or "0" <= line[j] <= "9"):
                j += 1
            word = line[i:j]
            if word in _HL_KEYWORDS:
                cl = _HL_KEYWORD
            elif word in _HL_BUILTINS:
                cl = _HL_BUILTIN
            else:
                cl = _HL_TEXT
            while i < j:
                out[i] = cl
                i += 1
            continue
        i += 1                                 # operator / punctuation / space
    return out


CURSOR_IDLE_MS = 2000  # hide the trackball cursor after this long with no movement


class Pointer:
    """A screen-space cursor. The trackball drives it relatively (and shows it);
    touch places it absolutely (finger is the pointer, so it stays hidden). The
    cursor auto-hides after CURSOR_IDLE_MS without trackball movement."""

    def __init__(self, w, h, idle_ms=CURSOR_IDLE_MS):
        self.w = w
        self.h = h
        self.x = w // 2
        self.y = h // 2
        self.click = False
        self.down = False         # touch/button currently held (for drag gestures)
        self.visible = True
        self.idle_ms = idle_ms
        self._last_move = _ticks_ms()

    def move(self, dx, dy):
        # Relative move from the trackball: clamp, and wake the cursor.
        self.x = max(0, min(self.w - 1, self.x + dx))
        self.y = max(0, min(self.h - 1, self.y + dy))
        self.visible = True
        self._last_move = _ticks_ms()

    def place(self, x, y):
        # Absolute position from touch: hit-test there, but keep the cursor
        # hidden (the finger already shows where you are).
        self.x = max(0, min(self.w - 1, x))
        self.y = max(0, min(self.h - 1, y))
        self.visible = False

    def tick(self, now):
        # Auto-hide once the trackball has been idle long enough.
        if self.visible and _ticks_diff(now, self._last_move) >= self.idle_ms:
            self.visible = False



# --- Pointer UI layout (320x240) -------------------------------------------
# The unified top bar (Stage 1): an 18px black strip on BOTH the launcher and the
# running-cart screen, where every chrome control is a 16x16 sprite from an editable
# IconSheet (not the old labeled glyph buttons). On the running-cart screen the LEFT
# cluster is the tool switcher -- HOME, EDIT/CODE, PAINT, MAP, BLOCKS -- a row of
# icon-only buttons. 16px icons in an 18px bar -> 1px top margin (y=1); icons stride
# _BAR_STRIDE (16 + 2px gap). These rects are GAME-canvas coords (the running-cart
# screen hit-tests in the 320x240 viewport); the launcher's NEW/DUP/DEL/gear live in
# Layout (system-canvas coords) since the home screen hit-tests there.
_BAR_ICON = 16              # icon sprite side (16x16, from the IconSheet)
_BAR_GAP = 2               # px between adjacent bar icons
_BAR_STRIDE = _BAR_ICON + _BAR_GAP        # 18: left-edge step between icons
_BAR_Y = 1                 # icons sit 1px down in the 18px bar (1px top/bottom margin)
# System menu (#52): the hamburger (≡) is the LEFT-MOST icon (Picotron logo-top-left);
# tapping it toggles the dropdown. It claims the old _HOME_BTN slot (x=2) and the tool
# switchers HOME/EDIT/PAINT/MAP/BLOCKS/MUSIC shift one stride right so the bar stays a
# single uninterrupted row. The hamburger is drawn via the _glyph BITMAP (not the
# themeable IconSheet) so an already-themed device -- whose saved system_icons.kgfx has
# no art for a new slot -- never shows a blank tile here. The rightmost switcher is now
# MUSIC (#50) at slot 6 -> ends at x=126; the right cluster (clock at x=224..) is clear.
_SYSMENU_BTN = (2, _BAR_Y, _BAR_ICON, _BAR_ICON)                 # ≡ dropdown toggle (slot 0)
_HOME_BTN = (2 + _BAR_STRIDE, _BAR_Y, _BAR_ICON, _BAR_ICON)      # back to launcher
_MENU_BTN = (2 + 2 * _BAR_STRIDE, _BAR_Y, _BAR_ICON, _BAR_ICON)  # Make-it-mine / code
_PAINT_BTN = (2 + 3 * _BAR_STRIDE, _BAR_Y, _BAR_ICON, _BAR_ICON)  # paint editor
_MAP_BTN = (2 + 4 * _BAR_STRIDE, _BAR_Y, _BAR_ICON, _BAR_ICON)   # map (tilemap) editor
_BLOCKS_BTN = (2 + 5 * _BAR_STRIDE, _BAR_Y, _BAR_ICON, _BAR_ICON)  # block editor (#29)
_MUSIC_BTN = (2 + 6 * _BAR_STRIDE, _BAR_Y, _BAR_ICON, _BAR_ICON)  # music/sound editor (#50)
# Right cluster on the running-cart bar (GAME canvas, always 320 wide @ fs 1): batt
# hard-right, then wifi, then the clock text. (Settings moved off the bar into the ≡
# system menu, OS-style, so there's no gear here any more.) Mirrors the launcher
# Layout's right cluster so both bars read identically. The clock egg hit-test uses
# _BAR_CLOCK. Literal 320 width / 18px bar / 8px font here (the game canvas is fixed).
_BAR_BATT = (320 - 2 - _BAR_ICON, _BAR_Y, _BAR_ICON, _BAR_ICON)
_BAR_WIFI = (_BAR_BATT[0] - _BAR_STRIDE, _BAR_Y, _BAR_ICON, _BAR_ICON)
_BAR_CLOCK = (_BAR_WIFI[0] - 2 - 5 * 8, 0, 5 * 8, 18)
_RUN_BTN = (28, 188, 70, 24)
_CODE_BTN = (104, 188, 84, 24)
_CLOSE_BTN = (194, 188, 96, 24)
_CARD_X = 24
_CARD_W = 272
_CARD_Y0 = 52
_CARD_DY = 22
_CARD_H = 20
# Cards-menu scroll window (#3): cards lay out from _CARD_Y0 down; rows whose
# bottom would pass _CARD_VIEW_BOTTOM are scrolled off rather than drawn over the
# RUN/CODE/CLOSE bar (y=188). A small up/down chevron strip on the right scrolls.
_CARD_VIEW_BOTTOM = 186
_CARD_SCROLL_UP = (300, 38, 16, 14)     # tap to scroll cards up (toward the top)
_CARD_SCROLL_DN = (300, 168, 16, 14)    # tap to scroll cards down
# --- Desktop shell (#28): home = wallpaper + cart icon grid + dock ----------
# The home screen is now a Picotron/TIC-80-style desktop: a wallpaper backdrop, a
# grid of tappable cart icons, the unified 18px top bar (clock + wifi/batt/gear +
# NEW/DUP/DEL management icons), and (in Settings) the bottom dock. The top bar's
# icons are 16x16 sprites from the editable IconSheet (Stage 1); the rest of the
# chrome uses the indexed API + petme128 font + the _glyph vocabulary, so host ==
# device.
_STATUS_H = 18          # unified top bar height (16px icons + 1px top/bottom margin)
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
# Home management actions (create / duplicate / delete) -- 16x16 icon buttons on the
# LEFT of the top bar's right cluster (the cluster is, L->R: NEW DUP DEL ... wifi batt
# gear, with the gear hard against the right edge). Only drawn/hit-tested when
# can_manage. 320x240 baseline; Layout reflows them on a larger system canvas.
_NEW_BTN = (2, _BAR_Y, _BAR_ICON, _BAR_ICON)        # placeholder; Layout sets the real x
_DUP_BTN = (2 + _BAR_STRIDE, _BAR_Y, _BAR_ICON, _BAR_ICON)
_DEL_BTN = (2 + 2 * _BAR_STRIDE, _BAR_Y, _BAR_ICON, _BAR_ICON)
# Page chevrons (when more carts than one page): tap to flip pages.
_PAGE_PREV = (2, 110, 14, 24)
_PAGE_NEXT = (304, 110, 14, 24)
# Bottom dock (persistent tool switcher, TIC-80 style): one tap to jump between
# home / code / draw / map / run / settings. On the home screen HOME is the
# active highlight; while a cart is open the dock stays so a kid hops tools
# without back-tracking. Six evenly-spaced slots across 320px.
_DOCK_SLOTS = ("home", "code", "paint", "map", "run", "settings")
_DOCK_GLYPH = {"home": "home", "code": "code", "paint": "paint",
               "map": "map", "run": "run", "settings": "gear"}
_DOCK_LABEL = {"home": "HOME", "code": "CODE", "paint": "DRAW",
               "map": "MAP", "run": "RUN", "settings": "SET"}
_DOCK_W = 52
_DOCK_GAP = 1
_DOCK_X0 = 2
# Settings screen (#28): wallpaper picker (FUNCTIONAL) + mocked rows. A simple
# vertical list of rows, each with a < label > stepper; the wallpaper row applies
# + persists immediately, the rest are clearly-marked no-ops ("soon").
_SET_X = 18
_SET_W = 284
_SET_ROW_Y0 = 40
_SET_ROW_H = 26
_SET_BACK = (288, 18, 18, 14)       # close Settings (X), in the panel title row
_SET_ACH = (262, 18, 22, 14)        # open the achievements view (trophy), title row (#21)
_SET_TITLE_HIT = (30, 18, 130, 16)  # the "SETTINGS" panel title (secret door, #21)
# Code editor: FULL-SCREEN (320x240). Top bar = title + run/save/close icons;
# the code area fills the middle; a tappable symbol palette runs along the bottom
# (the T-Deck keyboard has no `=`/`[]`/`{}`/`<>`/`%`, so the palette supplies them).
_CODE_X0 = 4
_CODE_Y0 = 18
_CODE_LH = 10
_CODE_AREA = (_CODE_X0, _CODE_Y0, CodeEditor.COLS * 8, CodeEditor.ROWS * _CODE_LH)
_ED_RUN = (266, 1, 16, 14)        # top-bar action icons (play / save / close)
_ED_SAVE = (285, 1, 16, 14)
_ED_CLOSE = (304, 1, 15, 14)
# Tappable coding-symbol palette along the bottom edge.
_CODE_SYMBOLS = "=()[]{}<>:;,.\"_%"
_SYM_Y = 220
_SYM_H = 20
_SYM_CELL = 20
_SYM_AREA = (0, _SYM_Y, _SYM_CELL * len(_CODE_SYMBOLS), _SYM_H)
# Paint editor (#4): zoomed pixel grid + 16-color palette (2x8) + sprite selector.
# (_PAINT_BTN -- the desktop overlay button -- lives in the button row above.)
# The grid is a fixed _PG_SPAN x _PG_SPAN square; the per-pixel cell shrinks as the
# sprite size grows (1x1 -> 18px cells over 8 px; 2x2 -> 9px over 16; 3x3 -> 6px
# over 24), so a bigger sprite (#30) edits in the same on-screen footprint.
_PG_X0 = 14
_PG_Y0 = 32
_PG_CELL = 18                      # cell size at size 1 (8x8); _PG_SPAN derives from it
_PG_SPAN = 8 * _PG_CELL            # fixed grid footprint in px (144), all sizes
_PG_AREA = (_PG_X0, _PG_Y0, _PG_SPAN, _PG_SPAN)
_SW_X0 = 170
_SW_Y0 = 32
_SW = 18
_SW_COLS = 2
_SW_AREA = (_SW_X0, _SW_Y0, _SW_COLS * _SW, (16 // _SW_COLS) * _SW)
_SPR_PREV = (214, 40, 40, 24)
_SPR_NEXT = (262, 40, 40, 24)
_PAINT_SIZE = (214, 68, 88, 20)    # cycle sprite size 1x1 / 2x2 / 3x3 (#30)
_PAINT_SAVE = (14, 190, 88, 26)
_PAINT_CLOSE = (200, 190, 102, 26)
# Cross-cart sprite reuse (#18): two buttons in the paint editor move the CURRENT
# tile between this cart's sheet and the well-known shared sheet, so a kid can
# carry a painted sprite from one cart to another without repainting.
#   GET  -- import the current tile FROM the shared sheet into this cart's sheet.
#   PUT  -- save the current tile TO the shared sheet (persisted to disk).
_PAINT_GET = (210, 130, 92, 20)
_PAINT_PUT = (210, 154, 92, 20)
# Map (tilemap) editor (#32): a panned view of the map on the left where each cell
# is the scaled sprite tile placed there, and a paged tile palette on the right to
# pick the brush tile. Tap a map cell to stamp the brush (or erase, when the ERASE
# toggle is on); tap a palette cell to select that tile id. Mirrors the paint
# editor's structure (grid + picker + save/close), with pan controls for maps
# larger than the on-screen window.
_MV_X0 = 14            # map view top-left (cells drawn scaled here)
_MV_Y0 = 32
# The map-view rectangle is sized dynamically from the current ZOOM level (#37
# follow-up): the cell size in px drives how many cells fit (and how big each tile
# is drawn). The available rectangle is x 14..~206, y 32..~196 -- clear of the tile
# palette (x >= 210) / pan d-pad / bottom button row (y = 198). All hit-testing,
# panning and drawing route through Workstation._mv_metrics() so they share one
# live cell size; there is no fixed _MV_CELL/_MV_COLS/_MV_ROWS/_MV_AREA any more.
_MV_AVAIL_W = 192      # usable map-view width  (14 .. 206)
_MV_AVAIL_H = 164      # usable map-view height (32 .. 196)
# TIC-80-style zoom: a small ascending list of cell sizes in px. Zoom only goes IN
# from the default (bigger cells -> fewer cells -> more detail), so a tile is only
# ever UPscaled (no sub-8px downscaling). The DEFAULT (index 0, most zoomed-OUT) is
# computed -- not hardcoded -- as the largest cell that still shows the whole map of
# both shipped games with NO panning: battle_city is 15x15 and platformer is 20x13,
# so the default must fit >= 20 columns AND >= 15 rows in the available rectangle.
_MV_FIT_COLS = 20      # widest shipped map (platformer)
_MV_FIT_ROWS = 15      # tallest shipped map (battle_city)


def _mv_default_cell():
    """The largest cell size (px) that still fits the whole shipped maps with no
    panning: >= _MV_FIT_COLS columns AND >= _MV_FIT_ROWS rows in the available
    rectangle. Computed rather than hardcoded so the fit guarantee is provable.
    With a 192x164 area this is 9px (192//9 = 21 cols, 164//9 = 18 rows)."""
    cell = 4
    best = cell
    while cell <= 40:
        if _MV_AVAIL_W // cell >= _MV_FIT_COLS and _MV_AVAIL_H // cell >= _MV_FIT_ROWS:
            best = cell
        cell += 1
    return best


# Zoom levels, ascending: index 0 is the fit-both default; the rest zoom IN. The
# zoomed-in sizes are multiples of the 8px tile (16/24/32) so each tile UPSCALES to
# fill its cell exactly (scale = cell // 8 = 2/3/4) -- crisp pixel art, no floating
# 8px tile in a big box.
_MV_ZOOMS = [_mv_default_cell(), 16, 24, 32]
# ZOOM control: a small button in the map editor that cycles the zoom level. Sits
# in the empty CENTER of the pan d-pad (between UP/DOWN/LEFT/RIGHT) -- a natural,
# TIC-80-ish spot that overlaps nothing (palette PREV/NEXT end at y 140; the d-pad
# arrows surround x 244,y 164 without filling its center).
_MAP_ZOOM = (244, 164, 24, 16)
# Tile palette (right): a paged grid of sheet tiles; tap to pick the brush.
_TP_X0 = 210
_TP_Y0 = 32
_TP_CELL = 22
_TP_COLS = 4
_TP_ROWS = 4
_TP_PAGE = _TP_COLS * _TP_ROWS          # tiles shown per palette page
_TP_AREA = (_TP_X0, _TP_Y0, _TP_COLS * _TP_CELL, _TP_ROWS * _TP_CELL)
_TP_PREV = (_TP_X0, _TP_Y0 + _TP_ROWS * _TP_CELL + 2, 42, 18)        # page back
_TP_NEXT = (_TP_X0 + 46, _TP_Y0 + _TP_ROWS * _TP_CELL + 2, 42, 18)   # page forward
# Empty/"sky" swatch (#37): a first-class, selectable palette entry on the bottom
# button row (right of CLOSE). Picking it sets the brush to EMPTY so a tap paints
# "nothing" -- the transparent/background cell map() skips -- like any other tile.
_TP_SKY = (206, 198, 100, 20)
# Pan d-pad (right column, under the palette): 4 arrow buttons that scroll the
# view. Kept clear of the map view (x < 196) and the bottom button row (y = 198).
_PAN_UP = (244, 146, 24, 16)
_PAN_LF = (218, 164, 24, 16)
_PAN_RT = (270, 164, 24, 16)
_PAN_DN = (244, 182, 24, 16)
# ERASE toggle + SAVE + CLOSE along the bottom-left (clear of the d-pad column).
_MAP_ERASE = (14, 198, 40, 20)
_MAP_SAVE = (58, 198, 64, 20)
_MAP_CLOSE = (126, 198, 76, 20)
# Map editor gesture threshold (#37): a pointer drag farther than this many pixels
# from its press origin pans the visible window (drag = pan); a shorter press +
# release taps one cell (tap = paint). Touch-drag panning is the primary way to
# navigate a map larger than the 320x240 view, so it wins over drag-to-stamp.
_MAP_PAN_THRESH = 6
# Block editor (#29 Part 2): the structured outline. A title bar + a vertical
# scrolling list of Scratch-style colored block rows (the flattened script) over a
# bottom action bar. Built 320x240-first (the responsive pass is #39 step 2), drawn
# on the GAME canvas through the same primitives as the other editors so host ==
# device. Pressing A on an insert `+` row opens the category->block insert menu.
_BLK_TITLE_Y = 2
_BLK_X0 = 6                # left edge of the outline
_BLK_W = 308              # outline width
_BLK_Y0 = 16             # first row's top
_BLK_ROW_H = 16          # one block row's height (8px text + padding)
_BLK_INDENT = 12         # px of indent per nesting depth
_BLK_ROWS = 11           # visible rows (Y0 .. just above the action bar)
_BLK_AREA = (_BLK_X0, _BLK_Y0, _BLK_W, _BLK_ROWS * _BLK_ROW_H)
# Bottom action bar: ADD / DEL / up / down on the left, SAVE / CODE / CLOSE right.
_BLK_ADD = (6, 196, 40, 22)
_BLK_DEL = (48, 196, 34, 22)
_BLK_UP = (84, 196, 22, 22)
_BLK_DN = (108, 196, 22, 22)
_BLK_SAVE = (138, 196, 50, 22)
_BLK_CODE = (190, 196, 56, 22)   # graduate to code
_BLK_CLOSE = (248, 196, 66, 22)
# The insert menu: a modal list overlay (category list, then the block list for the
# chosen category, then for some slots a small option picker). Drawn over a frozen
# outline; navigated with up/down + A, B backs out one level.
_BLK_MENU = (40, 24, 240, 192)        # the modal panel
_BLK_MENU_ROW_H = 16
_BLK_MENU_ROWS = 10                   # visible menu rows (scrolls if more)
# The variable name-entry prompt: a small modal with the live name + touch buttons
# (the kid types on the keyboard; OK/DEL/X are for touch). #29 Bug 2.
_BLK_KBD = (40, 78, 240, 84)          # the prompt panel
_BLK_KBD_DEL = (52, 124, 50, 24)      # backspace
_BLK_KBD_OK = (188, 124, 40, 24)      # confirm
_BLK_KBD_X = (232, 124, 36, 24)       # cancel
# The number-entry prompt (#29): a taller modal with an on-screen DIGIT GRID so a
# kid can TAP a literal in (touch-only / device sym-key-free), plus type it on the
# keyboard. The grid is 0-9 . - laid out 6-per-row; OK/DEL/X/BLOCK along the bottom.
_BLK_NUM = (24, 36, 272, 168)         # the prompt panel
_BLK_NUM_GX = 34                      # digit grid left
_BLK_NUM_GY = 78                      # digit grid top
_BLK_NUM_BW = 40                      # one key's width
_BLK_NUM_BH = 26                      # one key's height
_BLK_NUM_BPR = 6                      # keys per row
_BLK_NUM_KEYS = ["1", "2", "3", "4", "5", "6",
                 "7", "8", "9", "0", ".", "-"]
_BLK_NUM_DEL = (34, 168, 56, 26)      # backspace
_BLK_NUM_BLOCK = (96, 168, 60, 26)    # swap to a reporter block (expr slots only)
_BLK_NUM_OK = (200, 168, 40, 26)      # confirm
_BLK_NUM_X = (244, 168, 40, 26)       # cancel
# In-row slot editing: tapping/right-step on a selected block cycles to its NEXT
# editable slot; that slot is highlighted, and A opens its editor (number bump,
# variable/dropdown picker, expr -> a nested expression insert).
# Kid-facing category names for the insert menu (the catalog ids are terse keys).
_CAT_LABEL = {
    "events": "When...", "control": "Control", "draw": "Draw", "input": "Buttons",
    "variables": "Variables", "lists": "Lists", "operators": "Math", "sound": "Sound",
}

# Sentinel menu row: "make a brand-new variable + name it". It heads the Variables
# block list AND the variable-slot picker, so a kid can always create + name a
# variable with just ▲▼ + A (no dragging) and then use it everywhere (#29 Bug 2).
_NEW_VAR_ITEM = "\x00new_var"
_NEW_VAR_LABEL = "+ new variable"

# The list analogue (#48): heads the Lists block list AND the list-slot picker, so a
# kid creates + names a list the same way they make a variable.
_NEW_LIST_ITEM = "\x00new_list"
_NEW_LIST_LABEL = "+ new list"

# Sentinel menu row in the expr-slot chooser: "type a number" -- the Scratch white
# editable oval. It heads the reporter list so a typed literal (the common case:
# `set score to 0`, `> 100`) is the first, obvious choice; picking it opens the
# number keypad instead of dropping a block (#29).
_NUM_LITERAL_ITEM = "\x00num_lit"
_NUM_LITERAL_LABEL = "123 type a number"


def _blk_plain_label(label):
    """A block's display label with the {slot} placeholders stripped to bare names,
    so a menu/row reads like 'repeat times' or 'if cond'. The renderer fills the
    real slot values inline; this is the human template without the braces."""
    out = ""
    for ch in str(label):
        if ch == "{":
            out += ""              # drop the brace; keep the slot name that follows
        elif ch == "}":
            out += ""
        else:
            out += ch
    return out


# Kid-facing one-line hints for the surprising blocks (shown under the title).
_BLK_HINTS = {
    "forever": "forever = repeats fast every frame (not endless)",
    "wait": "wait = a friendly pause (each frame keeps drawing)",
    "repeat_until": "repeat until = loops fast until true (not endless)",
    "wait_until": "wait until = each frame keeps drawing till it's true",
    "stop": "stop = end this script now (this frame)",
    "break_loop": "break = jump out of the loop around it",
    "for_each": "for each = run the body once per item in the list",
}
# Music / sound editor (#50): a tracker-style step editor over the cart's AudioBank.
# Two views: SFX (a vertical column of [note, wave, vol] steps for one effect) and
# SONG (a column of SFX-id slots making the looping phrase). The cursor picks a
# step/slot; the right-hand button pad edits the value under it; a bottom bar plays/
# stops the preview + saves. Drawn 320x240 with the indexed API + petme128 font only
# (host == device); pointer/trackball/keyboard driven, mirroring the map editor's
# conventions. The step list scrolls when there are more steps than fit.
_MU_TITLE_Y = 2
_MU_VIEW = (236, 1, 80, 14)        # SFX <-> SONG view toggle (top-right)
# Step/slot list (left): a scrolling vertical column. Each row shows the index +
# the value (a note name + wave letter + a small volume bar, or an SFX id).
_MU_LIST_X = 8
_MU_LIST_Y0 = 30
_MU_ROW_H = 16
_MU_ROWS = 10                      # visible rows (Y0 .. above the bottom bar)
_MU_LIST_W = 150
_MU_LIST_AREA = (_MU_LIST_X, _MU_LIST_Y0, _MU_LIST_W, _MU_ROWS * _MU_ROW_H)
# Object selector (which SFX / track): < n > stepper under the title.
_MU_OBJ_PREV = (8, 16, 24, 12)
_MU_OBJ_NEXT = (124, 16, 24, 12)
# Edit pad (right): bump the value under the cursor. Two columns of buttons.
_MU_PAD_X = 168
_MU_PAD_Y = 30
_MU_PAD_W = 68                     # one button's width
_MU_PAD_H = 22
_MU_PAD_GAP = 4
# Buttons (filled in by _mu_pad_rect via row index):
#   row 0: NOTE- / NOTE+  (pitch down/up, or SFX-id down/up in song view)
#   row 1: WAVE  / VOL    (cycle waveform / cycle volume) -- sfx view only
#   row 2: REST  / SPEED  (toggle rest / bump tempo)
#   row 3: ADD   / DEL    (insert/remove a step or slot)
_MU_SPEED_DN = (240, 16, 16, 12)   # speed - (compact, by the title)
_MU_SPEED_UP = (300, 16, 16, 12)   # speed +
# Bottom action bar.
_MU_PLAY = (8, 198, 70, 24)
_MU_SAVE = (84, 198, 60, 24)
_MU_LOOP = (150, 198, 60, 24)
_MU_CLOSE = (216, 198, 100, 24)
# Note names for rendering a pitch index (semitone 0..95 -> e.g. "C4"). Sharps only,
# matching audio._NOTE_OFFSETS; kept here so the console renders labels without
# reaching into audio's private table.
_MU_NOTE_NAMES = ("C", "C#", "D", "D#", "E", "F",
                  "F#", "G", "G#", "A", "A#", "B")
_MU_WAVE_LABELS = ("SQ", "TRI", "SAW", "NOI")   # WAVE_SQUARE/TRIANGLE/SAW/NOISE


def _mu_note_name(pitch):
    """Render a semitone index as a note name ("C4"), or "--" for a rest (<0)."""
    if pitch is None or pitch < 0:
        return "--"
    return _MU_NOTE_NAMES[pitch % 12] + str(pitch // 12)


def _mu_pad_rect(col, row):
    """The (x, y, w, h) of edit-pad button at (col 0/1, row 0..3)."""
    x = _MU_PAD_X + col * (_MU_PAD_W + _MU_PAD_GAP)
    y = _MU_PAD_Y + row * (_MU_PAD_H + _MU_PAD_GAP)
    return (x, y, _MU_PAD_W, _MU_PAD_H)
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
# Letterbox/bezel fill (#39): the solid KID64 index the system canvas shows around
# the integer-scaled 320x240 game viewport (the borders of the fixed-aspect frame).
_VIEWPORT_BEZEL = 0         # black


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

        # -- right cluster (always): clock text, then wifi, batt, right-aligned.
        # (Settings moved into the ≡ system menu, OS-style, so there's no gear here any
        # more.) batt is hard against the right edge; wifi sits to its left, then the
        # clock text fills the space before them.
        self.batt_btn = (self.w - edge - ic, _BAR_Y, ic, ic)
        self.wifi_btn = (self.batt_btn[0] - stride, _BAR_Y, ic, ic)
        self.clock_w = 5 * self.font_w                # "HH:MM" (5 chars)
        self.clock_x = max(edge, self.wifi_btn[0] - edge - self.clock_w)

        # -- left cluster: the ≡ system-menu toggle (always, leftmost), then -- when
        # writable -- NEW / DUP / DEL. ≡ is the launcher's Settings entry now (it opens
        # the dropdown that holds Settings/About/Reboot), mirroring the in-cart bar.
        self.sysmenu_btn = (edge, _BAR_Y, ic, ic)
        self.new_btn = (self.sysmenu_btn[0] + stride, _BAR_Y, ic, ic)
        self.dup_btn = (self.new_btn[0] + stride, _BAR_Y, ic, ic)
        self.del_btn = (self.dup_btn[0] + stride, _BAR_Y, ic, ic)

        # -- selected-cart name slot: between the management cluster and the clock.
        self.status_name_x = self.del_btn[0] + self.del_btn[2] + edge
        self.status_name_maxc = max(
            4, (self.clock_x - edge - self.status_name_x) // self.font_w)

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
# system_icons.kgfx when present, else baked from `_ICON_ART` below. The _glyph
# vocabulary stays for NON-chrome uses (the cards/paint/blocks editors).
_ICON = {
    "home": 0, "edit": 1, "code": 2, "paint": 3, "map": 4, "blocks": 5,
    "gear": 6, "wifi": 7, "batt": 8, "new": 9, "dup": 10, "del": 11,
    "close": 12, "run": 13, "save": 14, "music": 15,
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
}

# Bump whenever the baked _ICON_ART above changes: a saved system_icons.kgfx theme
# written at an OLDER version is treated as stale and re-seeded to these new defaults
# at load (mirrors cart versioning, #47), so an already-themed device/desktop picks up
# new icons without a manual wipe. A bump discards a user's custom icon edits, exactly
# like a built-in cart re-seed. (v1 = the first full restyle.)
_ICON_VERSION = 1


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
    when no system_icons.kgfx exists. Each art entry is painted into its 16x16 tile
    via tset, so the result serializes/loads through the same .kgfx hex as any sheet.
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


def _in(px, py, rect):
    x, y, w, h = rect
    return x <= px < x + w and y <= py < y + h


def _line_cells(x0, y0, x1, y1):
    """Integer cells on the line from (x0, y0) to (x1, y1), inclusive (Bresenham).
    Drag-to-draw fills these so a fast pointer move paints a continuous stroke
    instead of dotting only the frames it was sampled on (#30). Integer-only so it
    runs the same under CPython (host) and MicroPython (device)."""
    cells = []
    dx = x1 - x0 if x1 >= x0 else x0 - x1
    dy = y1 - y0 if y1 >= y0 else y0 - y1
    sx = 1 if x0 < x1 else -1
    sy = 1 if y0 < y1 else -1
    err = dx - dy
    x, y = x0, y0
    while True:
        cells.append((x, y))
        if x == x1 and y == y1:
            break
        e2 = err + err
        if e2 > -dy:
            err -= dy
            x += sx
        if e2 < dx:
            err += dx
            y += sy
    return cells


# Per-type icon glyph for a cart tile on the desktop (the pre-literate cue).
_TYPE_GLYPH = {"wallpaper": "paint", "game": "run", "app": "app", "tool": "gear"}


# --- achievements + Easter eggs (#21) ---------------------------------------
#
# A small, tasteful set of fun milestones a kid hits naturally (open/run a cart,
# paint a sprite, edit a map, save code, play a few carts, visit each editor) plus
# the hidden Easter-egg rewards. Each is a tuple (id, name, glyph, hidden):
#   id     -- stable key persisted in achievements.json
#   name   -- the friendly title shown in the toast + the achievements view
#   glyph  -- the icon from _GLYPHS drawn beside it
#   hidden -- True hides the name as "???" in the view until it's unlocked, so a
#             secret stays a surprise (the Easter-egg rewards are all hidden).
# Backend-agnostic + MicroPython-safe (a plain tuple of tuples, frozen into the
# device build). The store (kid_carts.load/save_achievements) holds only the
# unlocked ids; this catalog is the single source of what each one MEANS, so host
# and device show identical badges.
ACHIEVEMENTS = (
    ("first_open",   "First Steps",     "app",    False),
    ("first_run",    "Lift Off!",       "run",    False),
    ("first_paint",  "Little Artist",   "paint",  False),
    ("first_map",    "Map Maker",       "map",    False),
    ("first_code",   "Code Wizard",     "code",   False),
    ("play_five",    "Cart Explorer",   "star",   False),
    ("toolbox",      "Toolbox Master",  "gear",   False),
    ("decorator",    "Home Decorator",  "heart",  False),
    # Hidden Easter-egg rewards (name shown as "???" until found):
    ("konami",        "Secret Coder",    "spark",  True),
    ("clock_tinker",  "Time Traveler",   "smile",  True),
    ("secret_door",   "Secret Finder",   "key",    True),
)

# Which achievement(s) a plain milestone event unlocks. Counters (play_five,
# toolbox) are tallied separately by Achievements.note; everything else maps an
# event name straight to one id. Keep this list of events in sync with the hook
# points in Workstation (open/run/save_*/editor opens).
_EVENT_ACHIEVEMENT = {
    "open": "first_open",
    "run": "first_run",
    "paint_save": "first_paint",
    "map_save": "first_map",
    "code_save": "first_code",
    "wallpaper_change": "decorator",
}

# Editors a kid can visit; opening all of them earns "toolbox".
_TOOLBOX_VIEWS = ("code", "paint", "map")
_PLAY_GOAL = 5            # distinct carts opened to earn "Cart Explorer"

ACH_TITLE = {a[0]: a[1] for a in ACHIEVEMENTS}
ACH_GLYPH = {a[0]: a[2] for a in ACHIEVEMENTS}
ACH_HIDDEN = {a[0]: a[3] for a in ACHIEVEMENTS}

TOAST_MS = 2600          # how long a celebratory unlock banner stays on screen


class Achievements:
    """Tracks which fun milestones a kid has unlocked, awards each exactly once,
    persists the unlocked set, and queues a celebratory toast on a fresh unlock.

    Backend-agnostic + MicroPython-safe. The Workstation owns one of these, calls
    note(event[, key]) at the existing flow points (open/run/paint-save/...), and
    reads `toast`/`toast_until` to draw the banner. Persistence + audio are injected
    callbacks so this class stays free of the SD wrapper and the audio backend (the
    Workstation wires those), which also makes it trivially unit-testable."""

    def __init__(self, unlocked=None, on_save=None, on_unlock=None):
        # `unlocked` is the list loaded from achievements.json (ids already valid).
        self.unlocked = {}                 # id -> True (a set; dict for MP parity)
        for i in (unlocked or ()):
            self.unlocked[i] = True
        self._on_save = on_save            # called(list_of_ids) to persist; None = volatile
        self._on_unlock = on_unlock        # called(id) on a FRESH unlock (e.g. a beep)
        self._seen_views = {}              # editor views visited this session+history
        self._played = {}                  # distinct cart keys opened (for play_five)
        self.toast = None                  # (id, title, glyph) of the live toast, or None
        self.toast_until = 0               # _ticks_ms deadline the toast hides at

    # -- queries -------------------------------------------------------------
    def has(self, ach_id):
        return ach_id in self.unlocked

    def count(self):
        return len(self.unlocked)

    # -- awarding ------------------------------------------------------------
    def award(self, ach_id):
        """Unlock `ach_id` if it isn't already and is a known achievement. Returns
        True only on the FIRST unlock (so a milestone awards exactly once), and then
        persists + raises a toast + fires the on_unlock hook. A repeat is a no-op."""
        if ach_id in self.unlocked or ach_id not in ACH_TITLE:
            return False
        self.unlocked[ach_id] = True
        if self._on_save is not None:
            try:
                self._on_save(list(self.unlocked.keys()))
            except Exception as exc:  # noqa: BLE001 -- a failed save must not crash the UI
                print("KidCode achievements save failed:", _err_text(exc))
        self.toast = (ach_id, ACH_TITLE[ach_id], ACH_GLYPH.get(ach_id, "trophy"))
        self.toast_until = _ticks_ms() + TOAST_MS
        if self._on_unlock is not None:
            try:
                self._on_unlock(ach_id)
            except Exception:  # noqa: BLE001 -- audio is best-effort celebration
                pass
        return True

    def note(self, event, key=None):
        """Record a milestone `event` and award whatever it earns. `key` is the
        per-event detail used by the counter milestones: for "open" it's the cart
        identity (distinct carts -> play_five); for "editor" it's the view name
        (visiting all editors -> toolbox). Direct-mapped events (open/run/saves)
        award their id immediately. Safe to call every time the event happens --
        award() makes the once-only guarantee."""
        if event == "open":
            if key is not None:
                self._played[key] = True
                if len(self._played) >= _PLAY_GOAL:
                    self.award("play_five")
            self.award("first_open")
        elif event == "editor":
            if key in _TOOLBOX_VIEWS:
                self._seen_views[key] = True
                if all(v in self._seen_views for v in _TOOLBOX_VIEWS):
                    self.award("toolbox")
        elif event in _EVENT_ACHIEVEMENT:
            self.award(_EVENT_ACHIEVEMENT[event])

    def toast_active(self, now=None):
        if self.toast is None:
            return False
        if now is None:
            now = _ticks_ms()
        if _ticks_diff(self.toast_until, now) <= 0:
            self.toast = None
            return False
        return True


class Launcher:
    """The desktop home (#28): carts laid out as a PAGED GRID of tappable icon
    tiles over the wallpaper backdrop, instead of a flat vertical strip. Keeps the
    selection model (items/sel/selected/move) the rest of the console relies on;
    `page`/PAGE is the grid's scroll unit (one screen of COLS x ROWS icons).

    The grid geometry comes from an injected `Layout` (#39) so it reflows with the
    system canvas size + font scale; COLS/ROWS/PAGE are instance attributes mirrored
    from the live layout (so callers reading them, and the selection/paging model,
    track the reflowed grid). A bare Launcher(items) (unit construction) falls back
    to the 320x240 / scale-1 baseline -- exactly today's 4x2/PAGE=8 grid."""

    def __init__(self, items, layout=None):
        self.items = items
        self.sel = 0
        self.page = 0
        self.set_layout(layout or Layout())

    def set_layout(self, layout):
        """Adopt a new grid layout (size/font-scale change) and re-clamp the page so
        the selection stays on a valid screen. Mirrors COLS/ROWS/PAGE as instance
        attributes for the callers/tests that read them directly."""
        self.layout = layout
        self.COLS = layout.cols
        self.ROWS = layout.rows
        self.PAGE = layout.page
        self._clamp_page()

    # -- selection ----------------------------------------------------------
    def set_items(self, items):
        """Replace the cart list (after a create/duplicate/delete) and re-clamp the
        selection + page so neither dangles past the new end. The public re-sync
        entry point -- callers must not poke the private page bookkeeping."""
        self.items = items
        if self.sel >= len(items):
            self.sel = max(0, len(items) - 1)
        self._page_to_sel()

    def nav2d(self, dx, dy):
        """Grid navigation: dx steps a column, dy steps a row. Clamped within the
        list (no wrap) so arrow nav feels like a real grid."""
        n = len(self.items)
        if not n:
            return
        step = dx + dy * self.COLS
        self.sel = max(0, min(n - 1, self.sel + step))
        self._page_to_sel()

    def _page_to_sel(self):
        self.page = self.sel // self.PAGE
        self._clamp_page()

    def max_page(self):
        n = len(self.items)
        return max(0, (n - 1) // self.PAGE) if n else 0

    def _clamp_page(self):
        self.page = max(0, min(self.max_page(), self.page))

    def flip_page(self, d):
        """Page the grid by d screens (chevron tap), moving the selection onto the
        first tile of the new page so keyboard nav continues from there."""
        self.page = max(0, min(self.max_page(), self.page + d))
        first = self.page * self.PAGE
        if self.items and not (first <= self.sel < first + self.PAGE):
            self.sel = min(len(self.items) - 1, first)

    def selected(self):
        return self.items[self.sel] if self.items else None

    def _page_range(self):
        start = self.page * self.PAGE
        return range(start, min(len(self.items), start + self.PAGE))

    def tile_rect(self, i):
        """The grid-cell rect for cart index i, or None if it's not on the current
        page. Cells lay out left-to-right, top-to-bottom in the icon area (geometry
        from the live Layout, so it reflows with the system canvas / font scale)."""
        return self.layout.tile_rect(i, self.page)

    def tile_at(self, px, py):
        for i in self._page_range():
            r = self.tile_rect(i)
            if r and _in(px, py, r):
                return i
        return None

    def draw(self, cv, sheet_for=None):
        # Icon tiles only -- the wallpaper backdrop + status strip + dock are drawn
        # by the Workstation around this (so the wallpaper shows through). For each
        # cart: a rounded art box (its sprite tile 0 if it has one, else a type
        # glyph), the selection ring, and a short name beneath. All geometry scales
        # with the layout (font scale), so a bigger panel shows bigger tiles (#39).
        lay = self.layout
        box = lay.icon_box
        fw = lay.font_w                              # on-screen char-cell width (8*fs)
        spr_scale = max(1, box // 16)                # fit the 16x16 icon sprite in the box
        for i in self._page_range():
            x, y, w, h = self.tile_rect(i)
            it = self.items[i]
            sel = (i == self.sel)
            bx = x + (w - box) // 2
            by = y + 2
            cv.rect(bx, by, box, box, NAMES["dark_purple"])
            cv.rectb(bx, by, box, box,
                     NAMES["yellow"] if sel else NAMES["dark_grey"])
            img = sheet_for(it) if sheet_for is not None else None
            if img is not None:
                cv.spr(img, bx + (box - 16 * spr_scale) // 2,
                       by + (box - 16 * spr_scale) // 2, spr_scale)
            else:
                self._tile_glyph(cv, it, (bx, by, box, box))
            # short name (one line, truncated to the tile width: fw-wide cells)
            name = it["title"]
            maxc = w // fw
            if len(name) > maxc:
                name = name[:maxc]
            cv.print(name, x + (w - len(name) * fw) // 2, by + box + 3,
                     NAMES["white"] if sel else NAMES["light_grey"], 1)

    def _tile_glyph(self, cv, it, box):
        # A type-colored art box with a centered type glyph, for carts with no
        # sprite. Uses the shared module-level glyph blitter (host == device).
        x, y, w, h = box
        cv.rect(x + 6, y + 6, w - 12, h - 12, _TYPE_COLOR.get(it["type"], NAMES["indigo"]))
        _blit_glyph(cv, _TYPE_GLYPH.get(it["type"], "app"), box, NAMES["black"])


class Pmem:
    """A cart's persistent memory: 256 x 32-bit unsigned ints, TIC-80 pmem().

    Backend-agnostic (host + device share this). The Workstation builds one per
    cart from kid_carts.load_pmem and injects its `cell` accessor into make_api as
    `pmem(i[, v])`: read pmem(i) -> int, write pmem(i, v) -> persists (when the
    cart is on a writable store). A write only persists if the value actually
    changed, so a cart calling pmem(i, v) every frame doesn't hammer the SD."""

    CELLS = 256
    MASK = 0xFFFFFFFF

    def __init__(self, cells=None, on_write=None):
        # `cells` is the loaded list (already 256 long from kid_carts.load_pmem);
        # default to all-zero so an embedded/non-SD cart still gets working RAM.
        if cells is None:
            cells = [0] * self.CELLS
        self.cells = cells
        self._on_write = on_write   # called(cells) to persist; None = volatile

    def cell(self, index, value=None):
        i = int(index)
        if i < 0 or i >= self.CELLS:
            return 0
        if value is None:
            return self.cells[i]
        v = int(value) & self.MASK
        if self.cells[i] != v:
            self.cells[i] = v
            if self._on_write is not None:
                self._on_write(self.cells)
        return v


class _SilentAudio:
    """No-op audio backend (#16): wraps an AudioEngine but never produces sound.
    The default when no make_audio backend was injected. Exposes the same control
    surface the api binds to, so make_api stays identical whether or not real
    playback is wired. (Permission-gating audio on the manifest 'sound' permission
    is future work -- the v0.4 console doesn't yet enforce any cart permissions.)"""

    def __init__(self, engine):
        self.engine = engine

    def sfx(self, n, chan=None):
        pass

    def beep(self, freq, dur=0.15):
        pass

    def music(self, track, loop=True):
        pass

    def music_stop(self):
        pass

    def sound_stop(self, chan=None):
        pass

    def volume(self, level):
        pass

    def tick(self, dt):
        pass


# --- reusable overlay popup (#52) -------------------------------------------
# A minimal left-anchored dropdown drawn ON TOP of whatever screen is up, with its
# OWN open/selected state. It's the primitive the top-bar system menu is built on,
# and is reusable for #55 (system-as-cart) / future menus. Index-only drawing (the
# existing cls/rect/rectb/print verbs -- host == device, no new native primitive),
# petme128 8x8 text, the _glyph fallback contract for an optional per-row icon.
#
# Items are tuples; the first element is a kind:
#   ("header", text)               -- a dim section title; NOT cursor-selectable
#   ("sep",)                        -- a 1px separator line between groups
#   ("item", text, action)         -- a selectable row; `action` is called on activate
# The cursor (`sel`) only ever lands on "item" rows (move()/_clamp skip the rest);
# activate() runs the selected item's action then closes (close-on-select).
_POPUP_X = 0                  # panel left edge (flush to the screen left, x = 0)
_POPUP_Y = _STATUS_H          # top edge flush under the 18px bar (y = 18)
_POPUP_W = 128                # panel width (~120-140px band; 128 keeps it clear of clock)
_POPUP_ROW_H = 12             # per-row height (selectable + header rows alike)
_POPUP_PAD_X = 4              # text inset from the panel left
_POPUP_SEP_H = 1              # separator line height


class Popup:
    """A self-contained dropdown overlay (#52): owns open/closed + the moving cursor,
    dismisses on outside-tap / ESC, and draws on top. Backend-agnostic -- the host and
    the device drive it through the same indexed canvas."""

    def __init__(self):
        self.open = False
        self.items = []               # list of row tuples (see module note above)
        self.sel = 0                  # index of the highlighted SELECTABLE row

    # -- open/close ----------------------------------------------------------
    def show(self, items):
        """Open with `items`, cursor on the first selectable row."""
        self.items = list(items)
        self.open = True
        self.sel = self._first_selectable()

    def close(self):
        self.open = False

    def toggle(self, items):
        """≡ pressed: open with `items` if closed, else close (the same control
        toggles it shut)."""
        if self.open:
            self.close()
        else:
            self.show(items)

    # -- selectable-row helpers ----------------------------------------------
    def _is_selectable(self, i):
        return 0 <= i < len(self.items) and self.items[i][0] == "item"

    def _first_selectable(self):
        for i in range(len(self.items)):
            if self.items[i][0] == "item":
                return i
        return 0

    def _clamp_sel(self):
        if not self._is_selectable(self.sel):
            self.sel = self._first_selectable()

    # -- navigation (cursor skips headers/separators; clamps at the ends) -----
    def move(self, d):
        """Step the highlight by d (+1 down / -1 up), skipping non-selectable rows.
        Clamps at the first/last selectable row (no wrap)."""
        if not self.open or d == 0:
            return
        step = 1 if d > 0 else -1
        i = self.sel + step
        while 0 <= i < len(self.items):
            if self.items[i][0] == "item":
                self.sel = i
                return
            i += step
        # no further selectable row in that direction -> stay put (clamp)

    def activate(self):
        """Fire the selected row's action, then close (close-on-select). No-op when
        closed or the cursor isn't on a selectable row."""
        if not self.open:
            return
        self._clamp_sel()
        if self._is_selectable(self.sel):
            action = self.items[self.sel][2]
            self.close()              # close BEFORE running so the action can re-open
            if action is not None:
                action()

    # -- geometry + hit-testing ----------------------------------------------
    def panel_rect(self):
        """(x, y, w, h) of the whole panel -- height grows with the row count."""
        h = 0
        for it in self.items:
            h += _POPUP_SEP_H if it[0] == "sep" else _POPUP_ROW_H
        return (_POPUP_X, _POPUP_Y, _POPUP_W, h)

    def row_at(self, px, py):
        """Index of the row under (px, py), or None when outside the panel."""
        if not self.open:
            return None
        x, y, w, h = self.panel_rect()
        if not _in(px, py, (x, y, w, h)):
            return None
        cy = _POPUP_Y
        for i in range(len(self.items)):
            rh = _POPUP_SEP_H if self.items[i][0] == "sep" else _POPUP_ROW_H
            if cy <= py < cy + rh:
                return i
            cy += rh
        return None

    def click(self, px, py):
        """Apply a tap: outside the panel -> dismiss; on a selectable row -> move the
        cursor there AND activate (tap = move+select in one gesture, #52); on a
        header/separator -> swallow (taps inside the panel never dismiss). Returns
        True when the tap was consumed (so the caller stops dispatching it)."""
        if not self.open:
            return False
        i = self.row_at(px, py)
        if i is None:
            self.close()              # tap OUTSIDE dismisses
            return True
        if self.items[i][0] == "item":
            self.sel = i
            self.activate()
        return True                    # tap inside is always consumed


class Workstation:
    def __init__(self, comp, canvas, input, carts=None, sys_canvas=None,
                 font_scale=1):
        self.comp = comp
        # Two rendering domains (#39). The GAME canvas is the fixed 320x240 indexed
        # surface the cart + cart API draw on -- carts are UNCHANGED. The SYSTEM
        # canvas is the panel/window surface the desktop/launcher/settings + status
        # strip + dock render on, responsive to its size + the system font scale; a
        # running cart (and, for step 1, the editors) draw on the game canvas and are
        # composited as a fixed-aspect, integer-scaled, centered viewport into it.
        # When sys_canvas is None (or the same size, the T-Deck default) the system
        # canvas IS the game canvas -- one object, so everything is pixel-identical
        # to today (graceful degradation), and the composite step is a no-op.
        self.canvas = canvas
        # A distinct SYSTEM canvas, or None for the degradation case where the system
        # canvas IS the game canvas. Kept as a separate field (not a hard reference to
        # `canvas`) so the property tracks `self.canvas` even if a backend swap
        # reassigns it later (the web console does `ws.canvas = CommandCanvas(...)`).
        self._sys_canvas = sys_canvas if (sys_canvas is not None
                                          and sys_canvas is not canvas) else None
        # `font_scale` is the REQUESTED system-UI scale (persisted). It only takes
        # visible effect on a distinct SYSTEM canvas that can render scaled text; in
        # the degradation case (no system canvas -- e.g. the T-Deck, whose framebuf
        # text can't scale) the effective scale is 1, so the chrome layout matches the
        # 8px text actually drawn. The requested value is still kept + persisted, so a
        # bigger panel later honours it.
        self.font_scale = max(1, int(font_scale))
        if self._sys_canvas is not None:
            self._sys_canvas.set_font_scale(self.font_scale)
        self.layout = Layout(self.sys_canvas.w, self.sys_canvas.h,
                             self._effective_font_scale())
        self.input = input
        self.make_api = None       # injected: make_api(canvas, input, cfg, sheet, audio, tilemap, pmem, wifi)->ns
        self.make_audio = None      # injected: make_audio(engine)->audio backend (host/device)
        self.audio = None           # the per-cart audio backend (built on open, #16)
        # WiFi (#38): a SYSTEM service shared across carts (the connection persists
        # when a cart exits), not per-cart. run_desktop/build_workstation injects
        # the backend here; it's exposed to a cart's namespace ONLY when the cart's
        # manifest permissions include "network" (capability-gated -- see _start).
        self.wifi = None            # injected wifi backend (host FakeWifi / device WLAN)
        self.carts_store = None     # injected: cart store module (kid_carts API)
        # OTA firmware updater (#53): injected by the device (kc_ota.OtaUpdater); None
        # on the host. When present AND the build is OTA-capable, Settings grows an
        # "UPDATE FW" row that flashes a new image from /sd/update to the inactive slot.
        self.updater = None
        self._updater_ok = None     # cached updater.available() (cheap, but not per-frame)
        self._online_ok = None      # cached updater.online_available() (#53 Phase 3)
        # update screen phases: local install -- "confirm" | "install" | "done" | "error";
        # online (#53 Phase 3) -- "checking" | "uptodate" | "confirm_online" | "downloading".
        self._upd_phase = None
        self._upd_msg = ""          # update screen: error / status text
        self._upd_bin = None        # update screen: (path, size) of the found/downloaded image
        self._upd_at = 0            # update screen: timestamp the install finished
        self._online_manifest = None  # the fetched update manifest dict
        self._check_armed = False   # one-frame gate so CHECKING... draws before the blocking fetch
        self.launcher = Launcher(carts if carts else [], self.layout)
        # Screen states (#28): "launcher" is now the DESKTOP home (wallpaper + cart
        # icon grid + dock); "desktop" is a running cart; "menu" is the cards/code/
        # paint/map editors; "settings" is the Settings app.
        self.screen = "launcher"      # "launcher" | "desktop" | "menu" | "settings" | "update"
        self.cart = None
        self.config = None
        self.ns = None
        self._update = None
        self._draw = None
        self.msel = 0                 # selected card in the menu
        self.mtop = 0                 # first card scrolled into view (#3)
        self.menu_view = "cards"      # menu sub-view: "cards" | "code" | "paint" | "map"
        self.editor = None            # CodeEditor while menu_view == "code"
        self.sheet = None             # SpriteSheet for the open cart (built on open)
        self.tilemap = None           # TileMap for the open cart (built on open, #32)
        self.pmem = None              # Pmem (persistent cart store) for the open cart
        self._cart_start_ms = 0       # _ticks_ms when the running cart last _start()ed
        self._cart_key_prev = 0       # last frame's keyboard byte (key()/keyp() edge)
        self.paint = None             # PaintEditor while menu_view == "paint"
        self.mapedit = None           # MapEditor while menu_view == "map" (#32)
        self.map_erase = False        # map editor: tap-to-erase instead of stamp
        self.map_page = 0             # map editor: first tile id shown in the palette
        self.map_zoom = 0             # map editor: zoom level index into _MV_ZOOMS (0 = fit)
        # Block editor (#29 Part 2): a BlockEditor over the cart's block program +
        # the structured-outline UI state. `blocks_ed` is built lazily on first open.
        self.blocks_ed = None         # BlockEditor while menu_view == "blocks"
        self.blk_top = 0              # first outline row scrolled into view
        self.blk_slot = 0             # which slot of the selected block is highlighted
        self.blk_menu = None          # active insert menu state dict, or None
        self.blk_status = None        # last block-editor SAVE result text
        self.blk_protect = False      # block editor opened on a hand-written-code cart
        self.blk_kbd = None           # inline name-entry prompt state dict, or None
        # Music/sound editor (#50): a MusicEditor over the open cart's AudioBank
        # (built lazily on first open of menu_view == "music"). `music_preview` tracks
        # what the live AudioEngine is previewing so the frame loop ticks the mixer
        # and shows STOP; None when nothing is playing.
        self.musicedit = None         # MusicEditor while menu_view == "music"
        self.music_preview = None     # ("sfx", n) | ("song", track) | None (preview)
        self.keyboard = None          # set by run_desktop (for raw/text mode toggle)
        self._ekey_prev = 0           # last consumed keyboard byte (edge detect)
        self._drag = None             # last pointer pos during a code-view drag-scroll
        self._paint_drag = None       # last painted grid cell during a paint drag (#30)
        self._map_drag = None         # last pointer (px,py) during a map pan drag (#37)
        self._map_press = None        # gesture origin (px,py); set on press, None on release (#37)
        self._map_panning = False     # this gesture has crossed the pan threshold (#37)
        self._map_paint_undo = None   # (cx,cy,prev_byte) painted on press; reverted if the
                                      # gesture turns out to be a pan, not a tap (#37)
        self._lhover = (-1, -1)       # last cursor pos used for desktop icon hover-highlight
        self.pointer = None           # set by run_desktop
        # Desktop wallpaper (#28): a chosen wallpaper-type cart compiled into its
        # own namespace and run (its _draw, optionally _update) as the BACKDROP each
        # home/settings frame -- the Picotron "wallpaper is a cart" model. None until
        # _select_wallpaper picks one; a solid KID64 fill is the zero-cart fallback.
        self.system = {}              # system settings dict (kid_carts system.json)
        self.wallpaper_id = None      # chosen wallpaper: cart slug or "fill:<color>"
        self._wp_ns = None            # wallpaper cart namespace
        self._wp_update = None        # wallpaper _update(dt) (live wallpapers)
        self._wp_draw = None          # wallpaper _draw() (the backdrop layer)
        self._wp_cart = None          # the wallpaper cart dict currently loaded
        self._wp_live = True          # run the wallpaper's _update too (set False to
                                      # save cost: _draw-only static backdrop)
        self._icon_cache = {}         # cart path -> desktop-icon sprite Image (or None)
        # Unified top bar (Stage 1): the editable 16x16 IconSheet the bar draws its
        # chrome icons from. Injected by build_workstation / run_desktop (loaded from
        # system_icons.kgfx, else the baked default theme); None falls back to _glyph.
        # _bar_img_cache memoises tile_image(slot) per kind so the SAME _SheetSprite is
        # reused every frame -- on the device that keeps its per-Image RGB565 blit cache
        # alive (one cached blit per icon), the whole point of moving the bar to sprites.
        self.icon_sheet = None
        self._bar_img_cache = {}      # icon kind -> cached _SheetSprite (or None)
        # Cached running-cart top bar (#43): the bar is ~static while a cart runs, so it
        # is rendered ONCE into an offscreen _STATUS_H-tall strip and blitted each frame
        # (one flat copy) instead of re-rendering ~9 sprites + glyph + text every frame
        # (the ~6ms `chrome=` cost). `_cart_bar_strip` is the layer (lazily allocated on
        # the first running-cart frame, reused across re-renders); `_cart_bar_key_cur` is
        # the state key the strip currently holds (None = stale -> rebuild). `_bar_cache_gen`
        # is bumped by the explicit invalidators (set_icon_sheet) so a theme swap repaints.
        self._cart_bar_strip = None
        self._cart_bar_key_cur = None
        self._bar_cache_gen = 0
        # Themeable top bar (Stage 2): True while the PAINT editor is repainting the
        # SYSTEM icon sheet (Settings -> EDIT ICONS) rather than a cart's sprites.
        # It changes where SAVE writes (system_icons.kgfx, not the cart) and where
        # CLOSE/back returns (Settings, not the running cart). menu_view == "theme"
        # reuses the cart PAINT renderer/input over self.icon_sheet (PaintEditor is
        # tile-size-agnostic, so the 16x16 IconSheet edits natively).
        self._editing_icons = False
        self.set_msel = 0             # selected row in the Settings screen
        self.set_top = 0              # first visible Settings row (scroll offset, #53)
        self.carts_root = None        # SD carts dir (reads); set by run_desktop
        self.cart_error = None        # last cart failure text -> on-canvas error panel
        self.save_status = None       # last save_code result text (e.g. a syntax error)
        self.code_err = None          # short inline syntax-error message (#24)
        self.code_err_row = None      # 0-based row the syntax error is on (#24)
        self.crash_line = None        # 1-based cart line of the last runtime crash (#24)
        self._hl_cache = {}           # per-line syntax-highlight memo (#24)
        self.paint_status = None      # last sprite-reuse (GET/PUT) result text (#18)
        self.can_manage = True        # writes enabled? run_desktop sets this from
                                      # whether SD is the cart source (carts_root)
        # SD session wrapper: mounts SD for the duration of fn(), then releases it
        # so the render loop's flushes never collide on the shared bus. On device
        # run_desktop swaps in kidcode_sd.with_sd_live (native kc_sd attach). The
        # default is a host passthrough.
        self._with_sd = lambda fn: fn()
        self.show_fps = True          # bottom-right FPS readout while a cart runs
        self._fps = 0.0               # smoothed frames/sec (EMA of 1/dt)
        # Frame-time breakdown HUD (#43/#44 perf): off by default; tap the FPS
        # readout (bottom-right, while a cart runs) to toggle it. When on, frame()
        # records the per-frame split in ms -- _flush_ms is the compositor's panel
        # DMA flush (comp.flush(); ~0 on the host's _NullComp), _draw_ms is the
        # rest (cart _update/_draw + the console's own draw = total minus flush).
        # All EMA-smoothed like _fps so the numbers read steady, not single-frame
        # jitter. This tells us whether the wall is the SPI flush or the per-frame
        # MicroPython draw cost on device. Measurement only -- no render-path change.
        self.perf_hud = False         # frame-time breakdown HUD shown? (tap FPS to toggle)
        # perf_capture decouples the per-frame timing MEASUREMENT from drawing the
        # HUD: when either perf_hud OR perf_capture is set, frame() records the
        # flush/draw split (the two cheap ticks calls below). The device backend
        # (kid_runtime.run_desktop) sets perf_capture=True so it can SAMPLE these
        # numbers into the offline diag log without painting the HUD on screen.
        # Default False -> host behaviour is byte-identical (no extra ticks calls).
        self.perf_capture = False     # measure flush/draw without drawing the HUD
        self._flush_ms = 0.0          # smoothed comp.flush() ms (panel DMA)
        self._draw_ms = 0.0           # smoothed draw ms (total frame - flush)
        # DRAWBRK phase split of _draw_ms (#43 follow-up): where the per-frame draw
        # cost actually goes -- cart _update, cart _draw, and the console chrome
        # (dock + cursor + overlays, the remainder). Surfaced via perf_breakdown().
        self._upd_ms = 0.0            # smoothed cart _update(dt) ms (game LOGIC)
        self._cart_ms = 0.0           # smoothed cart _draw() ms (RENDERING)
        self._audio_ms = 0.0          # smoothed audio.tick(dt) ms (mixer feed)
        self._chrome_ms = 0.0         # smoothed chrome ms (= draw - upd - cart - audio)
        # Achievements (#21): a small set of fun milestones + the hidden Easter-egg
        # rewards. Starts empty/volatile; load_achievements() wires the SD store +
        # the unlock beep. The Workstation calls ach.note(event) at the flow points
        # below (open/run/save_*/editor opens) and draws ach.toast each frame.
        self.ach = Achievements()
        # Easter-egg trigger state. Kept tiny + reset on screen changes so a stray
        # sequence never carries between contexts. None of these touch cart data.
        self._konami_pos = 0          # how far into the Konami sequence we are (desktop)
        self._clock_taps = 0          # clock taps on the status strip (Time Traveler)
        self._secret_taps = 0         # SETTINGS-title taps (Secret Finder door)
        self.egg_msg = None           # (line, glyph) of the live Easter-egg popup, or None
        self.egg_until = 0            # _ticks_ms the egg popup hides at
        self._confetti_until = 0      # _ticks_ms the Konami confetti effect ends
        self.show_achievements = False  # the locked/unlocked list overlay (Settings entry)
        # Top-bar system menu (#52): the ≡ dropdown. A reusable Popup owns its own
        # open/selected state; the SYSTEM group (Settings/About/Reboot) is always
        # present, a CART group (Restart/Delete) is prepended only while a cart is
        # open. `_about` is a tiny dismissible info modal the About row pops.
        self.sysmenu = Popup()
        self._about = False
        # Reboot hook: the device injects a callable (machine.reset via the OTA
        # updater); None on the host -> the Reboot row is a safe no-op (go_home).
        self.reboot_hook = None
        # Web view (#41/#22): the device injects a small controller exposing
        # .enabled (bool), .toggle(), and .url() so Settings can grow a "WEB VIEW"
        # ON/OFF row that serves the running console to a browser over WiFi. None on
        # the host (the host already has tools/web_console.py) -> the row is hidden.
        self.web_hook = None
        # Redraw-on-change (#44 step 1): a static UI screen costs ~0 -- frame() only
        # redraws + flushes when something visible changed. `_dirty` is the "redraw
        # this frame" flag; it starts True so the very first frame always paints, and
        # is set whenever input/state could have changed the picture (mark_dirty()).
        # `_last_ptr` snapshots the pointer state actually drawn so a cursor move/hide/
        # click triggers exactly one redraw. A running cart and a live wallpaper /
        # overlay effect animate every frame -> always dirty -> unchanged full-redraw
        # behaviour for them. `_frames_drawn` counts the frames that actually painted
        # (idle frames are skipped) -- a host-testable witness of the win.
        self._dirty = True
        self._last_ptr = None         # (x, y, visible, down, click) last drawn, or None
        self._frames_drawn = 0        # frames that actually drew+flushed (test witness)

    @property
    def sys_canvas(self):
        """The SYSTEM canvas the desktop chrome + overlays render on (#39). A distinct
        SystemCanvas when one was supplied, else the GAME canvas itself (degradation:
        one surface, pixel-identical to today). Reading through `self.canvas` keeps it
        correct even if a backend swaps the game canvas (e.g. the web CommandCanvas)."""
        return self._sys_canvas if self._sys_canvas is not None else self.canvas

    def _cart_has_perm(self, name):
        """True iff the open cart's manifest permissions include `name` (#38).
        kid_carts.load() carries the manifest "permissions" list onto the cart;
        an embedded/legacy cart with none simply never matches, so it gets no
        gated APIs."""
        perms = self.cart.get("permissions") if self.cart else None
        return bool(perms) and name in perms

    # -- desktop wallpaper (#28) ---------------------------------------------
    #
    # The home screen renders a chosen wallpaper-type cart as a live backdrop:
    # exactly the Picotron model where a wallpaper is just a fullscreen cart. We
    # reuse the cart-run machinery (compile + _init/_update/_draw) but in a SEPARATE
    # namespace so it never collides with the foreground cart. Fallback options are
    # plain solid KID64 fills ("fill:<color>"), so there's always a valid choice
    # even with zero wallpaper carts installed (and a cheap option for the device).

    _FILL_WALLPAPERS = ("fill:dark_blue", "fill:black", "fill:indigo", "fill:dark_purple")

    def wallpaper_carts(self):
        """The wallpaper-type carts available as backdrops (discovery: scan the
        launcher items by type, KidCode's equivalent of Picotron's wallpapers
        folder). Returns the cart dicts in launcher order."""
        return [c for c in self.launcher.items if c.get("type") == "wallpaper"]

    def wallpaper_options(self):
        """All selectable wallpaper ids: each wallpaper cart's slug, then the
        built-in solid fills (always present so there's a valid pick)."""
        out = []
        for c in self.wallpaper_carts():
            out.append(self._wp_id_for(c))
        out.extend(self._FILL_WALLPAPERS)
        return out

    def _wp_id_for(self, cart):
        # A stable id for a wallpaper cart: its folder name (slug) so the choice
        # survives a reboot. Embedded/path-less carts fall back to the title slug.
        path = cart.get("path")
        if path:
            name = path.rsplit("/", 1)[-1]
            if name.endswith(".kcart"):
                name = name[:-6]
            return name
        return self.carts_store.slug(cart["title"]) if self.carts_store else cart["title"]

    def _wp_cart_by_id(self, wp_id):
        for c in self.wallpaper_carts():
            if self._wp_id_for(c) == wp_id:
                return c
        return None

    def load_system(self):
        """Load the system settings (kid_carts system.json) and apply the saved
        wallpaper + font scale (#39). Safe no-op if no store/root is wired (embedded
        boot)."""
        if self.carts_store is not None and self.carts_root is not None:
            try:
                self.system = self._with_sd(
                    lambda: self.carts_store.load_system(self.carts_root)) or {}
            except Exception as exc:  # noqa: BLE001 -- a bad store must not crash boot
                print("KidCode system load failed:", _err_text(exc))
                self.system = {}
        # System font scale (#39): apply the persisted choice (1/2/3) so the desktop
        # boots at the saved text size. set_font_scale relays it into the system
        # canvas + relayouts; persist=False so loading doesn't re-write the store.
        self.set_font_scale(self.system.get("font_scale", self.font_scale),
                            persist=False)
        self.select_wallpaper(self.system.get("wallpaper"), persist=False)

    def set_icon_sheet(self, sheet):
        """Adopt the top-bar IconSheet (Stage 1) and drop the per-kind image cache so
        the next frame rebuilds its sprites (and, on the device, their RGB565 copies)
        from the new theme. None reverts the bar to the _glyph fallback."""
        self.icon_sheet = sheet
        self._bar_img_cache = {}
        self._bar_cache_gen += 1      # repaint the cached cart bar with the new theme (#43)

    def load_icon_sheet(self):
        """Build the top-bar IconSheet (Stage 1): use the saved system_icons.kgfx theme
        only if its stored version is >= the baked _ICON_VERSION; otherwise bake the
        default theme. A saved theme older than _ICON_VERSION is STALE (the shipped
        icons changed) -> re-seed it: bake the new default and overwrite the saved theme
        + version, so an already-themed device/desktop picks up new icons automatically
        (mirrors cart versioning, #47). A missing theme stays write-free (the common
        "absent = default" case). Safe on an embedded/no-store boot (baked default)."""
        hexs, saved_ver = None, 0
        store = self.carts_store
        load = getattr(store, "load_system_icons", None) if store is not None else None
        if load is not None and self.carts_root is not None:
            loadver = getattr(store, "load_system_icons_version", None)

            def _read_theme():
                return (load(self.carts_root),
                        loadver(self.carts_root) if loadver is not None else _ICON_VERSION)
            try:
                hexs, saved_ver = self._with_sd(_read_theme)
            except Exception as exc:  # noqa: BLE001 -- a bad theme falls back to default
                print("KidCode icons load failed:", _err_text(exc))
                hexs = None
        sheet = None
        if hexs and saved_ver >= _ICON_VERSION:        # current/newer saved theme -> keep it
            try:
                sheet = IconSheet.from_hex(hexs)
            except Exception:  # noqa: BLE001
                sheet = None
        if sheet is None:
            sheet = _default_icon_sheet()
            # Re-seed a STALE (or corrupt) saved theme to the new default so the new
            # icons land; skip when nothing was saved (no churn) or the store predates
            # versioning (no loadver -> _read_theme reported current, never stale).
            if hexs and self.carts_root is not None \
                    and getattr(store, "save_system_icons", None) is not None:
                try:
                    self._with_sd(lambda: store.save_system_icons(
                        sheet.to_hex(), self.carts_root, _ICON_VERSION))
                except Exception as exc:  # noqa: BLE001
                    print("KidCode icons re-seed failed:", _err_text(exc))
        self.set_icon_sheet(sheet)

    # -- system font scale (#39) ---------------------------------------------
    #
    # The system-UI font is settings-resizable (petme128 nearest-neighbor x1/x2/x3),
    # persisted in system.json (mirroring the #28 wallpaper setting) and applied live.
    # The GAME canvas keeps plain 8x8 text regardless -- scaling lives in the system
    # canvas + the responsive Layout, so a cart is never affected.

    FONT_SCALES = (1, 2, 3)

    def _effective_font_scale(self):
        """The scale actually applied to the system canvas + layout. It is the
        requested font_scale ONLY when a distinct system canvas exists (one that can
        render scaled text); in the degradation case (the T-Deck / a shared 320x240
        canvas, whose framebuf text can't scale) it is 1, so the chrome geometry
        always matches the 8px text actually drawn -- no mis-laid-out desktop."""
        return self.font_scale if self._sys_canvas is not None else 1

    def set_font_scale(self, scale, persist=True):
        """Set the system-UI font scale (clamped to FONT_SCALES), relay the effective
        scale into the system canvas + relayout the desktop, and (by default) persist
        it. The game canvas text is always 8px; the effective scale is 1 without a
        distinct system canvas (so the choice is remembered but only shows on a panel
        that can render it)."""
        try:
            scale = int(scale)
        except (TypeError, ValueError):
            scale = 1
        if scale not in self.FONT_SCALES:
            scale = self.FONT_SCALES[0]
        self.font_scale = scale
        if self._sys_canvas is not None:
            self._sys_canvas.set_font_scale(self._effective_font_scale())
        self._relayout()
        if persist:
            self._persist_font_scale()

    def cycle_font_scale(self, d):
        """Step the font scale by d through FONT_SCALES (Settings < / > stepper);
        applies + persists immediately so the desktop text resizes live."""
        scales = self.FONT_SCALES
        cur = self.font_scale if self.font_scale in scales else scales[0]
        nxt = scales[(scales.index(cur) + d) % len(scales)]
        self.set_font_scale(nxt, persist=True)

    def _relayout(self):
        """Rebuild the responsive layout from the live system-canvas size + the
        EFFECTIVE font scale and re-push it into the launcher (so its grid reflows).
        Called on a font-scale change (and could be called on a resize)."""
        self.layout = Layout(self.sys_canvas.w, self.sys_canvas.h,
                             self._effective_font_scale())
        self.launcher.set_layout(self.layout)

    def _persist_font_scale(self):
        self.system["font_scale"] = self.font_scale
        self._persist_system()

    def _persist_system(self):
        """Write self.system to system.json when a writable store is wired. Shared by the
        persisting Settings toggles (font, wallpaper, OTA channel)."""
        if not (self.carts_store is not None and self.carts_root is not None
                and self.can_manage):
            return
        try:
            self._with_sd(lambda: self.carts_store.save_system(self.system, self.carts_root))
        except Exception as exc:  # noqa: BLE001 -- a failed write just isn't remembered
            print("KidCode system save failed:", _err_text(exc))

    def _ota_channel(self):
        """The selected OTA update channel ("stable" default / "unstable" beta). Drives
        which manifest UPDATE ONLINE checks; persisted in system.json."""
        return self.system.get("ota_channel", "stable")

    def _cycle_channel(self, d):
        """Toggle the OTA channel STABLE<->UNSTABLE and persist. Two channels, so any
        step flips. This only changes what UPDATE ONLINE checks -- the running firmware
        is unchanged until a manifest is actually installed (and the bootloader's
        rollback still guards a bad beta image)."""
        self.system["ota_channel"] = (
            "stable" if self._ota_channel() == "unstable" else "unstable")
        self._persist_system()

    def load_achievements(self):
        """Load the unlocked achievements (kid_carts achievements.json) and wire the
        store + unlock-beep into a fresh Achievements (#21). Safe no-op on an
        embedded/no-store boot -- then the achievements stay in volatile RAM (still
        awarded + toasted this session, just not remembered). Call after the store +
        carts_root are injected (host build_workstation / device run_desktop)."""
        unlocked = []
        if self.carts_store is not None and self.carts_root is not None:
            try:
                unlocked = self._with_sd(
                    lambda: self.carts_store.load_achievements(self.carts_root)) or []
            except Exception as exc:  # noqa: BLE001 -- a bad store must not crash boot
                print("KidCode achievements load failed:", _err_text(exc))
                unlocked = []
        self.ach = Achievements(unlocked, on_save=self._save_achievements,
                                on_unlock=self._achievement_unlocked)

    def _save_achievements(self, ids):
        """Persist the unlocked-id list through the SD wrapper, when writes are on.
        A failed/disabled write just isn't remembered (the badge still shows this
        session) -- never fatal."""
        if not (self.carts_store is not None and self.carts_root is not None
                and self.can_manage):
            return
        self._with_sd(lambda: self.carts_store.save_achievements(ids, self.carts_root))

    def _achievement_unlocked(self, ach_id):
        """Celebrate a fresh unlock with a short rising beep, when audio is wired.
        Best-effort -- a silent backend (or none) just skips it. The toast is the
        primary, always-present feedback; the beep is the cherry on top."""
        au = self.audio
        if au is not None:
            try:
                au.beep(880, 0.08)
                au.beep(1320, 0.12)
            except Exception:  # noqa: BLE001
                pass

    # -- hidden Easter eggs (#21) --------------------------------------------
    #
    # Three playful, SAFE, reversible secrets, each gated behind a non-obvious
    # trigger and each awarding a hidden achievement. None touch persistent cart
    # data; the only state is a short on-canvas popup (egg_msg/egg_until) and a
    # confetti timer, all of which expire on their own. Triggers reset on screen
    # changes so a half-entered sequence never carries between contexts.
    #
    #   1. Konami code on the home desktop (up up down down left right left right
    #      b a) -> confetti rain + "OH! YOU FOUND ME!" -> "Secret Coder".
    #   2. Tapping the desktop clock 7 times -> a time-travel wink -> "Time
    #      Traveler".
    #   3. Tapping the SETTINGS title 5 times (the hidden "secret door") -> "knock
    #      knock... oh! you found me!" -> "Secret Finder".

    _KONAMI = ("up", "up", "down", "down", "left", "right", "left", "right", "b", "a")
    # Easter-egg hit regions (#21) are now derived from self.layout (clock_hit() /
    # set_title_hit) so they track the responsive status strip / Settings panel.
    _CLOCK_TAP_GOAL = 7
    _SECRET_TAP_GOAL = 5

    def _show_egg(self, line, glyph="smile", ms=2600):
        """Pop a non-blocking Easter-egg banner (drawn over the current screen for
        `ms`). Purely cosmetic + self-expiring."""
        self.egg_msg = (line, glyph)
        self.egg_until = _ticks_ms() + ms

    def _konami_step(self, name):
        """Advance the desktop Konami sequence on a button press; the full code in
        order fires the confetti egg + awards "Secret Coder". A wrong key restarts
        (but still counts if it's the sequence's first key, so a fresh start works)."""
        seq = self._KONAMI
        if name == seq[self._konami_pos]:
            self._konami_pos += 1
        else:
            # restart; the press may itself be the (new) first step
            self._konami_pos = 1 if name == seq[0] else 0
        if self._konami_pos >= len(seq):
            self._konami_pos = 0
            self._confetti_until = _ticks_ms() + 3000
            self._show_egg("OH! YOU FOUND ME!", "smile", ms=3000)
            self.ach.award("konami")

    def _tap_clock(self):
        """Count a clock tap; the _CLOCK_TAP_GOAL'th in a row fires the time egg +
        awards "Time Traveler". Any other desktop tap resets the run."""
        self._clock_taps += 1
        if self._clock_taps >= self._CLOCK_TAP_GOAL:
            self._clock_taps = 0
            self._show_egg("TICK TOCK... TIME TRAVELER!", "smile")
            self.ach.award("clock_tinker")

    def _tap_secret_door(self):
        """Count a SETTINGS-title tap (the hidden door); the _SECRET_TAP_GOAL'th
        knocks it open -> "Secret Finder"."""
        self._secret_taps += 1
        if self._secret_taps >= self._SECRET_TAP_GOAL:
            self._secret_taps = 0
            self._show_egg("KNOCK KNOCK... OH! YOU FOUND ME!", "key", ms=3000)
            self.ach.award("secret_door")

    def _egg_active(self, now=None):
        if self.egg_msg is None:
            return False
        if now is None:
            now = _ticks_ms()
        if _ticks_diff(self.egg_until, now) <= 0:
            self.egg_msg = None
            return False
        return True

    def select_wallpaper(self, wp_id, persist=True):
        """Choose the desktop backdrop. `wp_id` is a wallpaper cart slug or a
        "fill:<color>" built-in; an unknown/None id falls back to the first
        available option. Compiles the chosen cart into its own namespace (or sets
        a solid fill) and, when persist, writes the choice to system.json."""
        opts = self.wallpaper_options()
        if wp_id not in opts:
            wp_id = opts[0] if opts else self._FILL_WALLPAPERS[0]
        self.wallpaper_id = wp_id
        self._wp_ns = self._wp_update = self._wp_draw = None
        self._wp_cart = None
        if not (isinstance(wp_id, str) and wp_id.startswith("fill:")):
            cart = self._wp_cart_by_id(wp_id)
            if cart is not None:
                self._compile_wallpaper(cart)
        if persist:
            self._persist_wallpaper()

    def _compile_wallpaper(self, cart):
        """Compile a wallpaper cart into its own namespace + grab its _update/_draw,
        running its _init. Guarded: any failure leaves the backdrop on the solid
        fill (a broken wallpaper must never take down the desktop)."""
        try:
            sheet = self._build_sheet(cart)
            tilemap = self._build_tilemap(cart)
            ns = self.make_api(self.canvas, self.input, dict(cart.get("cfg", {})),
                               sheet, _SilentAudio(AudioEngine(AudioBank.default())),
                               tilemap, Pmem(), None)
            exec(compile(cart["src"], "<wallpaper>", "exec"), ns)
            if ns.get("_init"):
                ns["_init"]()
            self._wp_ns = ns
            self._wp_cart = cart
            self._wp_update = ns.get("_update")
            self._wp_draw = ns.get("_draw")
        except Exception as exc:  # noqa: BLE001
            print("KidCode wallpaper error:", _err_text(exc))
            self._wp_ns = self._wp_update = self._wp_draw = None

    def _persist_wallpaper(self):
        self.system["wallpaper"] = self.wallpaper_id
        if not (self.carts_store is not None and self.carts_root is not None
                and self.can_manage):
            return
        try:
            self._with_sd(lambda: self.carts_store.save_system(self.system, self.carts_root))
        except Exception as exc:  # noqa: BLE001 -- a failed write just isn't remembered
            print("KidCode system save failed:", _err_text(exc))

    def cycle_wallpaper(self, d):
        """Step the wallpaper choice by d (Settings < / > stepper); applies +
        persists immediately so the desktop updates live."""
        opts = self.wallpaper_options()
        if not opts:
            return
        cur = self.wallpaper_id if self.wallpaper_id in opts else opts[0]
        nxt = opts[(opts.index(cur) + d) % len(opts)]
        self.select_wallpaper(nxt, persist=True)
        self.ach.note("wallpaper_change")       # "Home Decorator": changed the backdrop (#21)

    def _draw_wallpaper(self, dt):
        """Paint the backdrop: run the wallpaper cart's _update/_draw, or a solid
        fill. Always fully clears the canvas so the foreground draws over a clean
        backdrop. Guarded so a misbehaving wallpaper degrades to a fill.

        Status-strip safe area (#46): on the launcher/settings the strip sits along the
        top, so a wallpaper that draws art/text near y=0 (the shipped ones print their
        title at y=10) gets sliced by the strip band. Before running the wallpaper we
        push its drawing DOWN by the strip height (camera) and clip the art to the rows
        below the strip, so the wallpaper composites into a known safe area beneath the
        strip and is never cut into. cls() ignores camera/clip (like TIC-80), so the
        backdrop FILL still covers the whole surface -- only the foreground art shifts,
        leaving a clean strip band of the wallpaper's own background colour."""
        if self._wp_draw is not None:
            try:
                if self._wp_live and self._wp_update is not None and dt > 0:
                    self._wp_update(dt)
                sh = self.layout.status_h
                safe = sh if self.screen in ("launcher", "settings") else 0
                if safe:
                    # camera(0, -sh): a draw at world y lands at screen y + sh (below
                    # the strip); clip keeps the art inside the safe rows.
                    self.canvas.camera(0, -safe)
                    self.canvas.clip(0, safe, self.canvas.w, self.canvas.h - safe)
                self._wp_draw()
                # Clear any camera/clip/pal/palt (#11) the wallpaper cart set (and the
                # safe-area camera/clip above), so the home/settings foreground (icons,
                # status strip) draws clean at full extent.
                self._reset_canvas_state()
                return
            except Exception as exc:  # noqa: BLE001 -- drop a broken wallpaper to the fill
                print("KidCode wallpaper draw error:", _err_text(exc))
                self._reset_canvas_state()
                self._wp_ns = self._wp_update = self._wp_draw = None
        # Solid fill fallback (also the "fill:<color>" built-ins).
        wp = self.wallpaper_id or "fill:dark_blue"
        name = wp[5:] if isinstance(wp, str) and wp.startswith("fill:") else "dark_blue"
        self.canvas.cls(NAMES.get(name, NAMES["dark_blue"]))

    def _icon_sheet_for(self, cart):
        """A cached sprite Image for a cart's desktop icon (its sheet tile 0), or
        None when the cart has no art (then the type glyph is drawn). Cached per
        cart path so the grid doesn't rebuild a sheet every frame."""
        key = cart.get("path") or cart.get("title")
        cache = self._icon_cache
        if key in cache:
            return cache[key]
        sheet = self._build_sheet(cart)             # shared sprite-load + fallback
        img = sheet.tile_image(0, -1) if not sheet.is_blank() else None
        cache[key] = img
        return img

    # -- Settings screen (#28) -----------------------------------------------
    #
    # Wallpaper is FUNCTIONAL (applies + persists); the rest are real-looking but
    # no-op controls clearly marked "soon", so the layout is proven without
    # committing to backends. Each row is (key, label, kind): "wallpaper" is the
    # live one; "mock" rows just step a cosmetic placeholder value.

    _SETTINGS_ROWS = (
        ("wallpaper", "WALLPAPER", "wallpaper"),
        ("font_scale", "FONT SIZE", "font"),
        ("volume", "VOLUME", "mock-gauge"),
        ("brightness", "BRIGHTNESS", "mock-gauge"),
        ("name", "NAME", "mock-name"),
        # EDIT ICONS (Stage 2): the one FUNCTIONAL "theme" control -- an action row
        # that opens the PAINT editor on the system icon sheet so a kid can repaint
        # the top-bar chrome. The dropdown menu that would otherwise host it is
        # deferred to #52, so it lives in Settings for now. "action" rows aren't
        # +/- steppers: any tap / left / right activates them (open_theme).
        ("icons", "EDIT ICONS", "action"),
    )
    _MOCK_NAMES = ("ALEX", "SAM", "KIT", "RAE")

    def _update_available(self):
        """True when an OTA updater is injected AND this build is OTA-capable (the
        running app is ota_0/ota_1, not a legacy single-`factory` image). Cached: the
        answer is fixed for a boot, and the check reads a partition (cheap, no SD)."""
        if self._updater_ok is None:
            u = self.updater
            try:
                self._updater_ok = bool(u is not None and u.available())
            except Exception:
                self._updater_ok = False
        return self._updater_ok

    def _online_update_available(self):
        """True when the updater can also fetch firmware over WiFi (#53 Phase 3):
        OTA-capable build + an injected wifi service. Cached like _update_available."""
        if self._online_ok is None:
            u = self.updater
            try:
                self._online_ok = bool(u is not None and u.online_available())
            except Exception:
                self._online_ok = False
        return self._online_ok

    def _settings_rows(self):
        """The Settings rows for this session: the static set, plus "UPDATE FW" (install
        from SD) and "UPDATE ONLINE" (WiFi download, #53 Phase 3) action rows when the
        injected updater supports them. Built on demand so the rows appear/disappear with
        the updater without re-statting per draw."""
        rows = self._SETTINGS_ROWS
        if self.web_hook is not None:           # device web view (#41): a WiFi browser feed
            rows = rows + (("web", "WEB VIEW", "web"),)
        if self._update_available():
            rows = rows + (("update", "UPDATE FW", "action"),)
        if self._online_update_available():
            rows = rows + (("ota_channel", "CHANNEL", "channel"),)
            rows = rows + (("update_online", "UPDATE ONLINE", "action"),)
        return rows

    def _activate_settings_action(self, key):
        """Fire an "action" Settings row by key: EDIT ICONS opens the theme editor,
        UPDATE FW installs a local SD image, UPDATE ONLINE checks WiFi for one (#53)."""
        if key == "update":
            self.open_update()
        elif key == "update_online":
            self.open_update_online()
        else:
            self.open_theme()       # EDIT ICONS (#52)

    def open_settings(self):
        if self.screen != "settings":
            self._settings_return = self.screen   # resume here on exit (cart vs home)
        self._dirty = True             # screen change repaints (#44)
        self.set_msel = 0
        self.set_top = 0               # reset the scroll window (#53)
        self.screen = "settings"
        self.show_achievements = False
        self._secret_taps = 0              # fresh secret-door run each visit (#21)
        self._set_text_mode(False)

    def _exit_settings(self):
        # Close Settings back to wherever it was opened from: resume the running cart
        # if we came from one (the gear on the in-cart bar), else the launcher home.
        if getattr(self, "_settings_return", "launcher") == "desktop" and self.cart is not None:
            self.screen = "desktop"
            self._dirty = True
        else:
            self.go_home()

    # -- top-bar system menu (#52) -------------------------------------------
    #
    # The ≡ dropdown built on the reusable Popup primitive. Contents are rebuilt each
    # open from the live state: a SYSTEM group always (Settings / About / Reboot), and
    # -- only when a cart is open -- a CART group PREPENDED (Restart / Delete). The
    # actions wire to the existing console flows (open_settings, apply = re-run, the SD
    # delete path). Selecting any row closes the menu (Popup.activate closes first).

    def _sysmenu_items(self):
        """The rows for this open of the ≡ menu (see class note for the tuple form).
        The cart group is OMITTED entirely (not greyed) when no cart is open."""
        rows = []
        if self.cart is not None:
            rows.append(("header", "CART"))
            rows.append(("item", "RESTART CART", self._menu_restart_cart))
            rows.append(("item", "DELETE CART", self._menu_delete_cart))
            rows.append(("sep",))
        rows.append(("header", "SYSTEM"))
        rows.append(("item", "SETTINGS", self.open_settings))
        rows.append(("item", "ABOUT", self._menu_about))
        rows.append(("item", "REBOOT", self._menu_reboot))
        return rows

    def toggle_sysmenu(self):
        """≡ tapped (or its keyboard shortcut): open the dropdown if closed, close it
        if open. Rebuilds the item list so the cart group reflects the current state."""
        self._dirty = True             # overlay open/close repaints (#44)
        self.sysmenu.toggle(self._sysmenu_items())

    def _menu_restart_cart(self):
        # Re-run the open cart from its current config (TIC-80 restart), landing back
        # on the running-cart screen -- exactly what GO/apply does.
        if self.cart is not None:
            self.apply()

    def _menu_delete_cart(self):
        # Delete the open cart (it IS the launcher selection -- open() set self.cart =
        # launcher.selected()), then go home. del_cart guards read-only / last-cart.
        before = len(self.launcher.items)
        self.del_cart()
        if len(self.launcher.items) < before:
            self.go_home()

    def _menu_about(self):
        # A tiny dismissible info modal (any tap / ESC / B closes it), drawn on top.
        self._dirty = True
        self._about = True

    def _menu_reboot(self):
        # Device: the injected reboot hook (machine.reset). Host / no hook: a safe
        # fallback to the home launcher (a hard reset would kill the sim window).
        self._dirty = True
        hook = self.reboot_hook
        if hook is not None:
            try:
                hook()
                return
            except Exception as exc:  # noqa: BLE001
                print("KidCode reboot failed:", exc)
        self.go_home()                 # safe stub when no reboot hook is wired

    def settings_adjust(self, d):
        """Step the selected Settings row by d. Wallpaper/font apply + persist; the
        mock rows just move a cosmetic value held in self.system (not acted on); an
        "action" row (EDIT ICONS) fires its action regardless of direction."""
        key, _label, kind = self._settings_rows()[self.set_msel]
        if kind == "action":                    # EDIT ICONS / UPDATE FW: open the tool
            self._activate_settings_action(key)
            return
        if key == "web":                        # device web view ON <-> OFF (#41)
            self._toggle_web_view()
            return
        if key == "ota_channel":                # OTA update channel STABLE <-> BETA
            self._cycle_channel(d)
            return
        if key == "wallpaper":
            self.cycle_wallpaper(d)
            return
        if key == "font_scale":                 # system-UI font size (#39): live + persisted
            self.cycle_font_scale(d)
            return
        if key == "name":
            cur = self.system.get("name", self._MOCK_NAMES[0])
            i = self._MOCK_NAMES.index(cur) if cur in self._MOCK_NAMES else 0
            self.system["name"] = self._MOCK_NAMES[(i + d) % len(self._MOCK_NAMES)]
        else:  # mock-gauge (volume / brightness): a 0..5 placeholder
            v = int(self.system.get(key, 3)) + d
            self.system[key] = max(0, min(5, v))

    def _toggle_web_view(self):
        """Flip the device web view on/off via the injected controller (#41). Guarded
        so a backend hiccup (e.g. WiFi not up yet -> can't bind) can never crash
        Settings; the row just stays OFF and the controller may surface a reason."""
        hook = self.web_hook
        if hook is None:
            return
        self._dirty = True
        try:
            hook.toggle()
        except Exception as exc:  # noqa: BLE001
            print("KidCode web view toggle failed:", exc)

    def _settings_wallpaper_label(self):
        """A friendly label for the current wallpaper: the cart's TITLE for a
        wallpaper cart, or the color name for a built-in solid fill."""
        wp = self.wallpaper_id or ""
        if isinstance(wp, str) and wp.startswith("fill:"):
            return wp[5:].replace("_", " ").upper()
        cart = self._wp_cart_by_id(wp)
        if cart is not None:
            return cart["title"].upper()
        return str(wp).replace("_", " ").upper()

    # -- firmware update screen (#53) -----------------------------------------
    #
    # OTA flow: Settings -> UPDATE FW finds a .bin on /sd/update, the kid confirms,
    # and the injected updater flashes it to the INACTIVE OTA slot one chunk per frame
    # (so the progress bar animates through the normal frame/flush loop), then reboots
    # into the new image. The running slot is never touched, and the bootloader rolls
    # back if the new app doesn't confirm itself healthy -- so a bad/aborted update is
    # safe. Pure UI here; all SD + flash work lives in the device-only updater backend.

    def open_update(self):
        """Open the firmware-update screen: scan SD for an image to install. Lands on
        the "confirm" phase when one is found, else "error" with a friendly reason."""
        self.screen = "update"
        self._dirty = True
        self.show_achievements = False
        self._set_text_mode(False)             # button-driven, not typing
        self._upd_bin = None
        self._upd_msg = ""
        u = self.updater
        if u is None:
            self._upd_phase = "error"
            self._upd_msg = "no updater"
            return
        found = u.find_bin()                    # SD op (between frames)
        if not found:
            self._upd_phase = "error"
            self._upd_msg = "no .bin in /sd/update"
            return
        self._upd_bin = found
        self._upd_phase = "confirm"

    def open_update_online(self):
        """Open the online-update flow (#53 Phase 3): connect WiFi + fetch the manifest,
        and if it's newer, download the image to SD. The blocking check runs in
        _pump_update one frame later so a CHECKING... screen shows first."""
        self.screen = "update"
        self._dirty = True
        self.show_achievements = False
        self._set_text_mode(False)
        self._upd_bin = None
        self._upd_msg = ""
        self._online_manifest = None
        if self.updater is None:
            self._upd_phase = "error"
            self._upd_msg = "no updater"
            return
        self._check_armed = False              # gate: draw CHECKING... before the blocking fetch
        self._upd_phase = "checking"

    def _start_download(self):
        """Open the socket + SD file and switch to the streaming download phase."""
        u = self.updater
        if u is None or not self._online_manifest:
            return
        self._dirty = True
        try:
            u.begin_download(self._online_manifest)
            self._upd_phase = "downloading"
        except Exception as exc:               # noqa: BLE001 -- shown to the kid
            self._upd_phase = "error"
            self._upd_msg = _err_text(exc)[:30]

    def _exit_update(self):
        """Leave the update screen back to Settings, dropping any in-progress install
        (the inactive slot may be half-written, but it was never set bootable) or
        download (the socket + partial SD file are closed)."""
        u = self.updater
        if u is not None:
            try:
                u.cancel()
            except Exception:
                pass
            try:
                u.download_cancel()
            except Exception:
                pass
        self.screen = "settings"
        self._dirty = True

    def _confirm_update(self):
        """Begin flashing the found image (validates header + size, opens the slot)."""
        u = self.updater
        if u is None or not self._upd_bin:
            return
        self._dirty = True
        try:
            u.begin(self._upd_bin[0])
            self._upd_phase = "install"
        except Exception as exc:               # noqa: BLE001 -- shown to the kid
            self._upd_phase = "error"
            self._upd_msg = _err_text(exc)[:30]

    def _update_input(self, i):
        ph = self._upd_phase
        if i.pressed("home") or i.pressed("stop"):
            if ph != "done":                   # "done" is past the point of no return
                self._exit_update()
                self.go_home()
            return
        if ph == "confirm":
            if i.pressed("a") or i.pressed("run"):
                self._confirm_update()
            elif i.pressed("b"):
                self._exit_update()
        elif ph == "confirm_online":
            if i.pressed("a") or i.pressed("run"):
                self._start_download()
            elif i.pressed("b"):
                self._exit_update()
        elif ph in ("install", "downloading", "checking"):
            if i.pressed("b"):                 # abort: nothing bootable was committed yet
                self._exit_update()
        elif ph in ("error", "uptodate"):
            if i.pressed("b") or i.pressed("a"):
                self._exit_update()
        # "done": ignore input -- _pump_update reboots into the new image shortly.

    def _update_pointer(self, px, py, click):
        if not click:
            return
        if _in(px, py, self.layout.set_back):  # the X in the title row
            if self._upd_phase != "done":
                self._exit_update()
            return
        ph = self._upd_phase
        if ph == "confirm":
            self._confirm_update()             # tap anywhere (besides X) = install
        elif ph == "confirm_online":
            self._start_download()             # tap anywhere (besides X) = download
        elif ph in ("error", "uptodate"):
            self._exit_update()

    def _pump_update(self, dt):
        """Advance the install one chunk (called each painted frame on the update
        screen). Drives begin->step*N->finish->reset through the updater backend."""
        u = self.updater
        ph = self._upd_phase
        if ph == "checking":
            # Run the blocking connect + manifest fetch ONE frame after entry, so the
            # CHECKING... screen paints first (this method runs before _draw each frame).
            if not self._check_armed:
                self._check_armed = True
                return
            if u is None:
                self._upd_phase = "error"
                self._upd_msg = "no updater"
                return
            ch = self._ota_channel()
            manifest = u.check_online(ch)      # connect (saved creds) + GET the manifest
            if u.error:
                self._upd_phase = "error"
                self._upd_msg = u.error
                return
            if not manifest:
                self._upd_phase = "error"
                self._upd_msg = "no manifest"
                return
            # Offer when the manifest is a different channel (a switch -- incl. beta->
            # stable) or a newer version within the selected channel (#53).
            if not u.offers(manifest, ch):
                self._upd_phase = "uptodate"
                return
            self._online_manifest = manifest
            self._upd_phase = "confirm_online"
        elif ph == "downloading":
            if u is None:
                self._upd_phase = "error"
                self._upd_msg = "no updater"
                return
            more = u.download_step()           # one chunk: socket -> SD (+ running sha256)
            if u.error:
                self._upd_phase = "error"
                self._upd_msg = u.error
                return
            if not more:
                path = u.download_finish()     # close + verify size/sha256
                if u.error or not path:
                    self._upd_phase = "error"
                    self._upd_msg = u.error or "verify failed"
                    return
                self._upd_bin = (path, u.dl_total or u.dl_done)
                self._upd_phase = "confirm"    # hand off to the Phase-2 install confirm
        elif ph == "install":
            if u is None:
                self._upd_phase = "error"
                self._upd_msg = "no updater"
                return
            more = u.step()                    # one SD session: read + flash a chunk
            if u.error:
                self._upd_phase = "error"
                self._upd_msg = u.error
                return
            if not more:
                if u.finish():                 # point the bootloader at the new slot
                    self._upd_phase = "done"
                    self._upd_at = _ticks_ms()
                else:
                    self._upd_phase = "error"
                    self._upd_msg = u.error or "set_boot failed"
        elif ph == "done":
            # Brief pause so the kid sees "UPDATED!", then reboot into the new image.
            if _ticks_diff(_ticks_ms(), self._upd_at) >= 1200:
                try:
                    u.reset()
                except Exception:
                    self._upd_phase = "error"
                    self._upd_msg = "reset failed"

    def _draw_update(self, dt):
        """The firmware-update screen: confirm / progress / done / error. On the
        SYSTEM canvas, same panel chrome as Settings (host == device)."""
        cv = self.sys_canvas
        lay = self.layout
        fs = lay.fs
        cv.rect(0, 0, lay.w, lay.h, NAMES["black"])
        px, py, pw, ph = lay.settings_panel
        cv.rect(px, py, pw, ph, NAMES["dark_purple"])
        cv.rectb(px, py, pw, ph, NAMES["pink"])
        self._glyph("gear", (px + 6, py + 2, 14 * fs, 14 * fs), NAMES["yellow"], cv)
        cv.print("UPDATE", px + 24, py + 4, NAMES["white"], 2)
        self._mini_btn("X", lay.set_back, NAMES["red"], cv)
        u = self.updater
        slot = u.slot() if u is not None else "?"
        ver = u.version() if u is not None else 0
        vlabel = u.version_label() if u is not None else "v0"
        x = px + 12 * fs
        y = py + 28 * fs
        phase = self._upd_phase
        if phase == "checking":
            cv.print("CHECKING ONLINE...", x, y, NAMES["yellow"], 1)
            y += 16 * fs
            beta = self._ota_channel() == "unstable"
            cv.print("channel: %s" % ("BETA" if beta else "STABLE"), x, y,
                     NAMES["orange"] if beta else NAMES["green"], 1)
            y += 14 * fs
            cv.print("running: %s %s" % (slot, vlabel), x, y, NAMES["light_grey"], 1)
        elif phase == "uptodate":
            cv.print("UP TO DATE", x, y, NAMES["green"], 1)
            y += 14 * fs
            cv.print("firmware %s" % vlabel, x, y, NAMES["white"], 1)
            y += 18 * fs
            cv.print("B = BACK", x, y, NAMES["yellow"], 1)
        elif phase == "confirm_online" and self._online_manifest:
            m = self._online_manifest
            newv = int(m.get("version", 0) or 0)
            kb = int(m.get("size", 0) or 0) // 1024
            run_ch = u.channel() if u is not None else "stable"
            tgt_ch = m.get("channel") or self._ota_channel()
            label = str(m.get("label") or ("v%d" % newv))
            switch = tgt_ch != run_ch
            tgt_name = "BETA" if tgt_ch == "unstable" else "STABLE"
            cv.print("SWITCH TO %s" % tgt_name if switch else "UPDATE AVAILABLE",
                     x, y, NAMES["light_grey"], 1)
            y += 12 * fs
            if switch:
                cv.print(label[:22], x, y, NAMES["orange"], 1)
            else:
                cv.print("%s -> %s" % (vlabel, label[:13]), x, y, NAMES["green"], 1)
            y += 14 * fs
            if kb:
                cv.print("%d KB download" % kb, x, y, NAMES["white"], 1)
                y += 14 * fs
            else:
                y += 2 * fs
            cv.print("A = DOWNLOAD", x, y, NAMES["yellow"], 1)
            y += 12 * fs
            cv.print("B = CANCEL", x, y, NAMES["light_grey"], 1)
        elif phase == "downloading":
            done = u.dl_done if u is not None else 0
            total = u.dl_total if (u is not None and u.dl_total) else 0
            cv.print("DOWNLOADING...", x, y, NAMES["yellow"], 1)
            y += 16 * fs
            frac = (done / total) if total else 0.0
            self._draw_progress_bar(px + 12 * fs, y, pw - 24 * fs, 10 * fs, frac)
            y += 16 * fs
            if total:
                cv.print("%d / %d KB" % (done // 1024, total // 1024), x, y, NAMES["white"], 1)
            else:
                cv.print("%d KB" % (done // 1024), x, y, NAMES["white"], 1)
            y += 16 * fs
            cv.print("B = CANCEL", x, y, NAMES["light_grey"], 1)
        elif phase == "confirm" and self._upd_bin:
            path, size = self._upd_bin
            name = path.rsplit("/", 1)[-1]
            cv.print("FOUND ON SD:", x, y, NAMES["light_grey"], 1)
            y += 12 * fs
            cv.print(name[:24], x, y, NAMES["green"], 1)
            y += 12 * fs
            cv.print("%d KB" % (size // 1024), x, y, NAMES["white"], 1)
            y += 14 * fs
            cv.print("running: %s" % slot, x, y, NAMES["light_grey"], 1)
            y += 18 * fs
            cv.print("A = INSTALL", x, y, NAMES["yellow"], 1)
            y += 12 * fs
            cv.print("B = CANCEL", x, y, NAMES["light_grey"], 1)
        elif phase == "install":
            done = u.done if u is not None else 0
            total = u.total if (u is not None and u.total) else 1
            cv.print("FLASHING...", x, y, NAMES["yellow"], 1)
            y += 16 * fs
            self._draw_progress_bar(px + 12 * fs, y, pw - 24 * fs, 10 * fs, done / total)
            y += 16 * fs
            cv.print("%d / %d KB" % (done // 1024, (u.total // 1024) if u else 0),
                     x, y, NAMES["white"], 1)
            y += 16 * fs
            cv.print("DO NOT POWER OFF", x, y, NAMES["red"], 1)
        elif phase == "done":
            cv.print("UPDATED!", x, y, NAMES["green"], 2)
            y += 20 * fs
            cv.print("rebooting...", x, y, NAMES["white"], 1)
        else:  # error
            cv.print("UPDATE FAILED", x, y, NAMES["red"], 1)
            y += 14 * fs
            cv.print((self._upd_msg or "?")[:26], x, y, NAMES["light_grey"], 1)
            y += 18 * fs
            cv.print("B = BACK", x, y, NAMES["yellow"], 1)

    def _draw_progress_bar(self, x, y, w, h, frac):
        cv = self.sys_canvas
        if frac < 0:
            frac = 0.0
        elif frac > 1:
            frac = 1.0
        cv.rectb(x, y, w, h, NAMES["light_grey"])
        fill = int((w - 2) * frac)
        if fill > 0:
            cv.rect(x + 1, y + 1, fill, h - 2, NAMES["green"])

    def _start(self):
        self._dirty = True             # a (re)started cart paints its first frame (#44)
        self._build_audio()
        # Reset the canvas draw state (camera/clip/pal/palt, #11) so a fresh cart run
        # never inherits a previous cart's clip rect or palette swap.
        rs = getattr(self.canvas, "reset_state", None)
        if rs is not None:
            rs()
        # Stamp the cart-start clock so the cart's time() reads ms since this run
        # began (re-run on apply/run_code/edit-close resets it, like TIC-80).
        self._cart_start_ms = _ticks_ms()
        self.input.cart_start_ms = self._cart_start_ms
        # Capability-permission gate (#38): hand make_api the wifi backend ONLY
        # when this cart declares the "network" permission, so a normal kid cart
        # gets NO `wifi` name (sandbox preserved). make_api injects `wifi` into the
        # cart namespace iff the backend it receives is non-None.
        wifi = self.wifi if self._cart_has_perm("network") else None
        ns = self.make_api(self.canvas, self.input, self.config, self.sheet,
                           self.audio, self.tilemap, self.pmem, wifi)
        try:
            # Compile with the "<cart>" filename so a runtime traceback carries
            # cart line numbers (_exc_cart_line reads them to mark the bad line).
            exec(compile(self.cart["src"], "<cart>", "exec"), ns)
            if ns.get("_init"):
                ns["_init"]()
        except Exception as exc:  # noqa: BLE001
            # The device's native run loop starves USB, so a print() never reaches
            # serial -- stash the failure so frame() can paint an on-canvas panel.
            # Print only the _err_text-guarded string, never the raw `exc`: a cart
            # exception whose __str__ itself raises would otherwise escape here and
            # become the exact silent device hang the panel exists to prevent.
            self.cart_error = _err_text(exc)
            self.crash_line = _exc_cart_line(exc)
            print("KidCode cart error:", self.cart_error)
            return False
        self.cart_error = None
        self.crash_line = None
        self.ns = ns
        self._update = ns.get("_update")
        self._draw = ns.get("_draw")
        return True

    def open(self):
        self.cart = self.launcher.selected()
        self.config = dict(self.cart["cfg"])
        self.msel = 0
        self.mtop = 0
        self.editor = None
        self.paint = None
        self.mapedit = None
        self.musicedit = None
        self.music_preview = None
        self.blocks_ed = None
        self.blk_menu = None
        self.blk_kbd = None
        self.blk_protect = False
        self.cart_error = None
        self.save_status = None
        self.sheet = self._build_sheet()
        self.tilemap = self._build_tilemap()
        self.pmem = self._build_pmem()
        self._cart_key_prev = 0       # fresh cart: no stale key edge
        self.input.text_mode = False  # a fresh cart starts in game mode (#38/#42);
                                      # it opts into text input via textmode(True)
        self.menu_view = "cards"
        self._set_text_mode(False)
        # Open to the desktop even if the cart failed to start: frame() shows the
        # error panel there and the EDIT/CODE button stays reachable so the kid can
        # fix it (a silent stay-on-launcher would be a dead end on the device).
        self._start()
        self.screen = "desktop"
        # Achievements (#21): opening a cart is "First Steps"; opening _PLAY_GOAL
        # distinct carts is "Cart Explorer". Key by the cart's path/title so it's
        # the SAME identity the launcher uses (distinct carts, not repeat opens).
        self.ach.note("open", self.cart.get("path") or self.cart.get("title"))

    def _build_sheet(self, cart=None):
        # Build `cart`'s sprite sheet (default: the open cart), or a blank one when
        # there's no/bad art. The wallpaper runner passes a cart explicitly.
        cart = cart if cart is not None else self.cart
        hexs = cart.get("sprites") if cart else None
        if hexs:
            try:
                return SpriteSheet.from_hex(hexs)
            except Exception:  # noqa: BLE001
                pass
        return SpriteSheet()

    def _build_pmem(self):
        """Load the open cart's persistent memory (pmem.json) into a Pmem, wiring
        its writes back through the SD store when the cart is writable. An
        embedded/non-SD cart still gets working (volatile) RAM."""
        path = self.cart.get("path") if self.cart else None
        cells = None
        if path and self.carts_store is not None:
            try:
                cells = self._with_sd(lambda: self.carts_store.load_pmem(path))
            except Exception as exc:  # noqa: BLE001
                print("KidCode pmem load failed:", exc)
                cells = None

        on_write = None
        if path and self.carts_store is not None and self.can_manage:
            def on_write(values, cart=self.cart):
                try:
                    self._with_sd(lambda: self.carts_store.save_pmem(cart, values))
                except Exception as exc:  # noqa: BLE001
                    # No serial in the device run loop, but a failed pmem write must
                    # not crash the cart -- the kid just loses that one save.
                    print("KidCode pmem save failed:", _err_text(exc))
        return Pmem(cells, on_write)

    def _build_tilemap(self, cart=None):
        """Build `cart`'s TileMap from its map.kmap blob (#32) (default: the open
        cart), or an empty map when the cart has none -- the mirror of _build_sheet,
        so map()/mget()/mset() are always callable (an empty map just blits
        nothing). The wallpaper runner passes a cart explicitly."""
        cart = cart if cart is not None else self.cart
        blob = cart.get("map") if cart else None
        if blob:
            try:
                return TileMap.from_hex(blob)
            except Exception:  # noqa: BLE001
                pass
        return TileMap()

    def _build_audio(self):
        """Build the per-cart audio backend (#16): an AudioEngine over the cart's
        sound bank (sounds.json), wrapped by the injected host/device backend. The
        mirror of _build_sheet. A cart with no bank gets the friendly default bank
        so beep()/the editor still work. Falls back to a silent backend if no
        make_audio was injected (keeps make_api callable everywhere)."""
        data = self.cart.get("sounds") if self.cart else None
        bank = AudioBank.from_dict(data) if data else AudioBank.default()
        engine = AudioEngine(bank)
        if self.make_audio is not None:
            self.audio = self.make_audio(engine)
        else:
            self.audio = _SilentAudio(engine)

    # -- code / paint editors (#3, #4) ---------------------------------------

    def set_menu_view(self, view):
        """Switch the menu sub-view, building the matching editor and toggling
        the keyboard between game (raw) and text (ASCII) modes."""
        self._dirty = True             # sub-view change always repaints (#44)
        self.menu_view = view
        if view == "code":
            if self.editor is None and self.cart is not None:
                self.editor = CodeEditor(self.cart["src"])
                self._ekey_prev = 0
                if self.crash_line is not None:
                    # Opened after a runtime crash -> land on the line that raised.
                    self._mark_code_error(self.crash_line - 1,
                                          (self.cart_error or "crashed")[:32])
                else:
                    self.code_err = None
                    self.code_err_row = None
        elif view == "paint":
            if self.paint is None and self.sheet is not None:
                self.paint = PaintEditor(self.sheet)
        elif view == "map":
            # Mirror the paint branch: build the MapEditor over the cart's TileMap
            # + sheet (both always exist after open()). Edits go straight into the
            # live tilemap, so a running cart picks them up via tilemap.gen (#32).
            if self.mapedit is None and self.tilemap is not None and self.sheet is not None:
                self.mapedit = MapEditor(self.tilemap, self.sheet)
        elif view == "blocks":
            # Build the BlockEditor over the cart's block program (#29). A cart
            # authored from blocks carries blocks.json (cart["blocks"]); a code-only
            # cart starts a fresh empty program -- saving it makes the cart
            # block-authored from then on. The Part-1 `blocks` module is injected so
            # the editor core stays dependency-free.
            if self.blocks_ed is None and self.cart is not None:
                prog = None
                if self.carts_store is not None and self.cart.get("path"):
                    try:
                        prog = self._with_sd(
                            lambda: self.carts_store.load_blocks(self.cart))
                    except Exception as exc:  # noqa: BLE001
                        print("KidCode load blocks failed:", _err_text(exc))
                if prog is None:
                    prog = self.cart.get("blocks")
                # DATA-LOSS GUARD (#29): a cart whose main.py is hand-written code
                # (no blocks.json, and main.py wasn't emitted by the block compiler)
                # must NEVER have that code clobbered by saving an empty block program.
                # When that's the case, run the block editor in PROTECTED mode: the
                # outline still opens (read-only-ish), but SAVE / graduate refuse to
                # overwrite main.py and tell the kid why. A genuinely block-authored
                # cart (has blocks.json) -- or an empty/new-template cart with no real
                # code -- is unprotected and round-trips exactly as before.
                self.blk_protect = (prog is None
                                    and self._cart_has_handwritten_code())
                self.blocks_ed = BlockEditor(_blocks_mod, prog)
                self.blk_top = 0
                self.blk_slot = 0
                self.blk_menu = None
                self.blk_status = ("CODE LOCKED -- can't blockify"
                                   if self.blk_protect else None)
        elif view == "music":
            # Build the MusicEditor over the open cart's live AudioBank (#50): the
            # SAME bank the running cart plays through, so an edit is heard immediately
            # by the preview AND by the cart on resume. The bank lives on the audio
            # backend's engine (self.audio.engine.bank); SFX/MusicTrack are injected as
            # factories so the editor core stays import-free. Edits go straight into
            # that bank; SAVE persists it to sounds.json.
            if self.musicedit is None and self.audio is not None:
                bank = self.audio.engine.bank
                self.musicedit = MusicEditor(bank, sfx_factory=SFX,
                                             track_factory=MusicTrack)
        self._set_text_mode(view == "code")
        # Achievements (#21): visiting each editor (code/paint/map) earns "Toolbox
        # Master". "cards" isn't an editor, so it's ignored by note().
        self.ach.note("editor", view)

    def _set_text_mode(self, on):
        # The code editor needs clean 1-byte ASCII (it reads last_key for typing);
        # a running cart wants the raw key matrix so a *held* direction keeps firing
        # (true hold-to-move -- the ASCII path reports each key once on the press
        # edge with no autorepeat). Flip the keyboard between the two on every screen
        # change. Raw needs keyboard fw >= 2025-06-12; without it the keyboard keeps
        # sending ASCII and TDeckKeyboard sticks on the 1-byte + hold-latch path, so
        # this is safe on any firmware. No-op on the host (no keyboard).
        # text_mode is the single source of truth for "typing, don't latch buttons":
        # the device keyboard, in ASCII, otherwise ALSO fires a typed key's game alias
        # (w/a/s/d/z/x -> up/left/down/right/a/b), so a typed name/password would
        # trigger d-pad/shortcut actions. Set it for the code editor too (on=True), and
        # clear it on every other screen (on=False) so it can never leak past the cart
        # /editor that asked for it -- the desktop frame re-derives keyboard mode from
        # input.text_mode (#38/#42). No-op on the host (no keyboard); harmless flag set.
        self.input.text_mode = bool(on)
        kb = self.keyboard
        if kb is not None:
            kb.set_game_mode(not on)

    def _sync_cart_text_mode(self):
        # Cart text input (#38/#42): a RUNNING cart opts into text-keyboard mode by
        # calling textmode(True) (make_api), which sets input.text_mode. Games leave it
        # off (the default) and keep the raw/game keyboard so a held direction keeps
        # firing btn(). When a cart asks for text mode we flip the keyboard to clean
        # 1-byte ASCII (set_game_mode(False)) so key()/keyp() yield typeable bytes;
        # when it turns text mode back off we restore game mode. Idempotent (the
        # keyboard's set_game_mode only talks to the HW on a real transition), called
        # each running-cart frame so a mid-cart textmode() toggle takes effect. No-op
        # on the host (no keyboard) -- there the same flag gates type_char routing in
        # ConsoleDriver. On older keyboard firmware set_game_mode(True) is a no-op
        # (stays ASCII) and the hold-latch fallback applies, so this is safe.
        want_text = bool(getattr(self.input, "text_mode", False))
        kb = self.keyboard
        if kb is not None:
            kb.set_game_mode(not want_text)

    def _open_menu(self):
        self.screen = "menu"
        # Carts with a Make-it-mine schema open to cards; others go straight to
        # the code editor (there are no cards to show).
        self.set_menu_view("cards" if self.cart.get("edit") else "code")

    def _open_paint(self):
        self.screen = "menu"
        self._editing_icons = False        # a CART sheet, not the system theme
        self.paint_status = None
        self.set_menu_view("paint")

    def open_theme(self):
        """Open the PAINT editor on the SYSTEM icon sheet (Settings -> EDIT ICONS,
        Stage 2 / #52). The same renderer/input as the cart PAINT flow, but pointed
        at self.icon_sheet: SAVE persists system_icons.kgfx (not a cart) and CLOSE
        returns to Settings. Starts from the current theme (the baked default if no
        system_icons.kgfx exists yet); the first SAVE creates the file."""
        self._dirty = True                 # screen change repaints (#44)
        self._editing_icons = True
        self.paint_status = None
        self.save_status = None
        self.screen = "menu"
        self.menu_view = "theme"
        # Build a PaintEditor over the icon sheet (PaintEditor is tile-size-agnostic,
        # so the 16x16 IconSheet edits natively). A fresh editor each open so the
        # brush/tile state doesn't leak in from a cart paint session.
        if self.icon_sheet is not None:
            self.paint = PaintEditor(self.icon_sheet)
        self._paint_drag = None
        self._set_text_mode(False)         # paint is pointer-driven, raw/game keyboard
        self.ach.note("editor", "paint")   # repainting the chrome counts toward Toolbox

    def _open_map(self):
        self.screen = "menu"
        self.save_status = None
        self.map_erase = False
        self.map_zoom = 0              # reset to the fit-both default zoom (#37 follow-up)
        self._map_press = None         # fresh gesture state on open (#37)
        self._map_panning = False
        self._map_drag = None
        self._map_paint_undo = None
        self.set_menu_view("map")
        # Open with the camera at the top-left so the whole map shows at the default
        # (fit-both) zoom with zero panning. set_menu_view builds the MapEditor.
        if self.mapedit is not None:
            self.mapedit.cam_x = 0
            self.mapedit.cam_y = 0

    def _open_blocks(self):
        self.screen = "menu"
        # NB: don't pre-clear blk_status here -- set_menu_view("blocks") sets the
        # "CODE LOCKED" notice when it builds the editor in protected mode, and
        # clearing it after would hide the data-loss guard's message.
        self.set_menu_view("blocks")

    def _open_music(self):
        """Open the music/sound editor (#50): a tracker-style step grid over the
        cart's AudioBank. Mirrors _open_map -- reset preview state, then build the
        editor via set_menu_view("music")."""
        self.screen = "menu"
        self.save_status = None
        self._stop_music_preview()
        self.set_menu_view("music")

    def _cart_has_handwritten_code(self):
        """True if the current cart's main.py is real, hand-written code that the
        block editor must not overwrite: there is non-trivial source AND it was NOT
        emitted by the block compiler (no BLOCK_MARKER) AND it isn't the throwaway
        new-cart template. A brand-new / template-only cart returns False, so a kid
        can freely start authoring it with blocks."""
        cart = self.cart
        if cart is None:
            return False
        src = cart.get("src") or ""
        if _blocks_mod.is_block_authored_source(src):
            return False                         # already block-authored main.py
        # The default new-cart template is fair game to blockify (it's boilerplate,
        # not the kid's own code) -- treat it as no real code.
        tmpl = getattr(self.carts_store, "NEW_TEMPLATE", None) if self.carts_store else None
        if tmpl is not None and src.strip() == str(tmpl.get("src", "")).strip():
            return False
        # Any remaining non-whitespace source is the kid's own code -> protect it.
        return bool(src.strip())

    def _leave_menu(self):
        self._dirty = True             # back to the desktop repaints (#44)
        self._set_text_mode(False)
        if self.menu_view == "music":
            self._stop_music_preview()   # don't let a preview leak into the cart resume
        # Returning to the desktop from the code editor must run whatever source is
        # in the editor now (the kid may have fixed a crash and hit SAVE, or just
        # edited and closed). Re-_start() with the editor text so the FIXED cart
        # actually runs -- otherwise a previously-set cart_error would re-paint the
        # stale "crashed" panel and _update/_draw would stay None forever.
        if self.menu_view == "code" and self.editor is not None and self.cart is not None:
            self.cart["src"] = self.editor.text()
            self._start()
        elif self.menu_view == "blocks":
            self.blk_menu = None
            self.blk_kbd = None
            # A saved block edit already recompiled cart["src"] (save_blocks); re-run
            # it so leaving the outline runs the freshest program, just like the code
            # editor does. (Unsaved edits don't touch src, so this re-runs the last
            # saved version -- the kid SAVEs to keep changes, exactly like code.)
            if self.cart is not None:
                self._start()
        self.screen = "desktop"

    def _editor_input(self):
        # Feed the typed key to the editor, one insert per physical press: the
        # keyboard reports the byte for the frame it is down then 0, so acting on
        # the 0->key edge (key != previous) avoids autorepeat.
        if self.editor is None:
            return
        k = self.input.last_key
        if k and k != self._ekey_prev:
            if self.editor.key(k):       # text changed -> drop the stale error marker
                self.code_err = None
                self.code_err_row = None
                self.crash_line = None
        self._ekey_prev = k

    def save_code(self):
        """Persist the edited source. Returns True iff it was written. A source
        that won't compile is REFUSED (the good file is left intact) and the
        syntax error is surfaced via self.save_status / cart_error rather than
        silently writing garbage. Non-SD carts (no path) just no-op True."""
        if not (self.editor and self.cart):
            return False
        src = self.editor.text()
        # Always compile-check, even for embedded/non-SD carts, so the kid sees a
        # syntax error before run_code execs it into a hard failure.
        ok, msg = self.carts_store.compile_check(src)
        if not ok:
            self.save_status = "SYNTAX " + msg
            self.cart_error = "Syntax error -- " + msg
            self._set_code_error(msg)        # mark the bad line in the editor (#24)
            return False
        self.code_err = None                 # parses now -> clear the inline marker
        self.code_err_row = None
        self.crash_line = None               # a re-run will re-detect any runtime crash
        if not (self.cart.get("path") and self.can_manage):
            self.save_status = None             # nothing to persist, but src is valid
            self.ach.note("code_save")          # "Code Wizard": valid code saved (#21)
            return True
        try:
            # kid_carts.save_code always returns a (status, message) 2-tuple.
            status, smsg = self._with_sd(lambda: self.carts_store.save_code(self.cart, src))
            if status != self.carts_store.SAVE_OK:
                self.save_status = "SAVE FAILED " + str(smsg)
                self.cart_error = "Could not save -- " + str(smsg)
                return False
            self.editor.dirty = False
            self.save_status = "SAVED"
            self.ach.note("code_save")          # "Code Wizard": code saved (#21)
            # A successful save means the source now compiles and persisted: clear
            # any stale crash text so returning to the desktop re-runs the fixed
            # cart instead of re-painting the old "crashed" panel. (run_code/the
            # _leave_menu re-_start() then actually re-exec it.)
            self.cart_error = None
            return True
        except Exception as exc:  # noqa: BLE001
            txt = _err_text(exc)
            self.save_status = "SAVE FAILED"
            self.cart_error = "Could not save -- " + txt
            print("KidCode save code failed:", txt)
            return False

    def _set_code_error(self, msg):
        """Record a syntax error so the code view can mark the offending line
        inline (#24). compile_check formats messages as "line N: <reason>"; pull
        N out for the marker, keep the short reason for the inline note, and move
        the caret onto that line so the fix is one tap away."""
        row = None
        short = msg
        if msg.startswith("line "):
            rest = msg[5:]
            p = rest.find(":")
            if p > 0 and rest[:p].strip().isdigit():
                row = int(rest[:p].strip()) - 1
                short = rest[p + 1:].strip()
        self._mark_code_error(row, short)

    def _mark_code_error(self, row, short):
        """Record an inline error marker (#24) and, if the editor is open, move
        the caret onto `row` (0-based) so the fix is one tap away."""
        self.code_err = short
        self.code_err_row = row
        if row is not None and self.editor is not None:
            ed = self.editor
            ed.row = max(0, min(len(ed.lines) - 1, row))
            ed._clamp_col()
            ed._scroll()

    def run_code(self):
        # Refuse to run un-parseable source: keep the kid in the editor with the
        # syntax error shown rather than dropping to a blank/broken desktop.
        if self.editor is not None:
            if not self.save_code():
                return                               # syntax/save error -> stay in editor
            self.cart["src"] = self.editor.text()   # in-RAM apply (validated above)
        if self._start():
            self.ach.note("run")                # "Lift Off!": a cart was RUN (#21)
            self._set_text_mode(False)
            self.screen = "desktop"
        else:
            # Compiled but raised at exec/_init: show the error panel on the desktop
            # (still reachable -> the kid can reopen the editor to fix it).
            self.screen = "desktop"

    def save_sprites(self):
        if not (self.sheet and self.cart and self.cart.get("path") and self.can_manage):
            return
        hexs = self.sheet.to_hex()
        try:
            self._with_sd(lambda: self.carts_store.save_sprites(self.cart, hexs))
            self.sheet.dirty = False
            self.save_status = "SAVED"
            self.ach.note("paint_save")         # "Little Artist": a sprite saved (#21)
        except Exception as exc:  # noqa: BLE001
            # Mirror the save_code contract: a failed sprite save must be VISIBLE on
            # device (no serial in the run loop), not silent. _err_text-guarded so a
            # weird exception's __str__ can't itself escape this handler.
            txt = _err_text(exc)
            self.save_status = "SAVE FAILED"
            self.cart_error = "Could not save sprites -- " + txt
            print("KidCode save sprites failed:", txt)

    def save_icons(self):
        """Persist the edited system icon sheet to system_icons.kgfx (Stage 2 / #52),
        the exact mirror of save_sprites/save_shared_sheet: to_hex -> the SAME SD
        wrapper the cart-sprite save uses (host: direct write; device: with_sd_live).
        Then invalidate the bar caches so the NEXT bar draw shows the new pixels live:
        set_icon_sheet drops the per-kind _SheetSprite cache (and with it the device's
        per-Image RGB565 blit cache), and the sheet's gen already bumped on each pset
        so any gen-keyed cache rebuilds too. Surfaces a save status like the cart
        paint editor. A bad store/no SD root is a no-op (writes deferred)."""
        if not (self.icon_sheet and self.carts_root and self.can_manage):
            return
        hexs = self.icon_sheet.to_hex()
        try:
            self._with_sd(lambda: self.carts_store.save_system_icons(hexs, self.carts_root, _ICON_VERSION))
            self.icon_sheet.dirty = False
            self.save_status = "SAVED"
            # Re-adopt the (same) sheet so the bar's per-kind image cache is dropped and
            # the next _draw_status_strip rebuilds its sprites from the freshest pixels.
            self.set_icon_sheet(self.icon_sheet)
            self.ach.note("paint_save")         # "Little Artist": a theme saved (#21)
        except Exception as exc:  # noqa: BLE001
            # Mirror save_sprites: a failed save must be VISIBLE on device (no serial in
            # the run loop), not silent. _err_text-guarded so a weird __str__ can't escape.
            txt = _err_text(exc)
            self.save_status = "SAVE FAILED"
            self.cart_error = "Could not save icons -- " + txt
            print("KidCode save icons failed:", txt)

    def _leave_theme(self):
        """CLOSE/back from the theme editor: return to Settings (not a cart/desktop --
        the theme editor was opened from there). Drops the editor + clears the
        editing-icons flag so the cart PAINT flow is untouched next time."""
        self._dirty = True                 # screen change repaints (#44)
        self._editing_icons = False
        self.paint = None
        self._paint_drag = None
        self.screen = "settings"

    def save_map(self):
        # Persist the cart's tilemap to map.kmap (#32) -- the exact mirror of
        # save_sprites (to_hex -> SD wrapper -> save_map). The running cart already
        # holds this same TileMap, so a save only persists what it's already using.
        if not (self.tilemap and self.cart and self.cart.get("path") and self.can_manage):
            return
        hexs = self.tilemap.to_hex()
        try:
            self._with_sd(lambda: self.carts_store.save_map(self.cart, hexs))
            self.tilemap.dirty = False
            self.save_status = "SAVED"
            self.ach.note("map_save")           # "Map Maker": a map saved (#21)
        except Exception as exc:  # noqa: BLE001
            txt = _err_text(exc)
            self.save_status = "SAVE FAILED"
            self.cart_error = "Could not save map -- " + txt
            print("KidCode save map failed:", txt)

    # -- music / sound editor (#50) ------------------------------------------

    def save_sounds(self):
        """Persist the cart's AudioBank to sounds.json (#50) -- the mirror of
        save_map. The MusicEditor edits the LIVE bank (self.audio.engine.bank), so a
        save just serializes what the cart already plays through."""
        me = self.musicedit
        if not (me and self.cart and self.cart.get("path") and self.can_manage):
            return
        bank_dict = me.bank.to_dict()
        try:
            self._with_sd(lambda: self.carts_store.save_sounds(self.cart, bank_dict))
            me.dirty = False
            self.save_status = "SAVED"
            self.ach.note("sound_save")          # "Sound Designer": a bank saved (#21)
        except Exception as exc:  # noqa: BLE001
            txt = _err_text(exc)
            self.save_status = "SAVE FAILED"
            self.cart_error = "Could not save sounds -- " + txt
            print("KidCode save sounds failed:", txt)

    def _play_music_preview(self):
        """Preview what the cursor is on: in the SFX view play the current SFX, in
        the SONG view play the current phrase (looping). Routes through the live
        AudioEngine (the same backend the cart uses), so it sounds on the host and
        the device. The frame loop ticks the mixer + redraws while a preview is up."""
        me = self.musicedit
        au = self.audio
        if me is None or au is None:
            return
        au.sound_stop()                          # cut any prior preview first
        if me.view == MusicEditor.SONG_VIEW:
            au.music(me.track_idx, True)
            self.music_preview = ("song", me.track_idx)
        else:
            au.sfx(me.sfx_idx)
            self.music_preview = ("sfx", me.sfx_idx)
        self._dirty = True

    def _stop_music_preview(self):
        """Stop any music-editor preview + clear the preview flag."""
        if self.audio is not None:
            self.audio.sound_stop()
            self.audio.music_stop()
        self.music_preview = None
        self._dirty = True

    def _music_preview_active(self):
        """True while a music-editor preview is still producing sound (so the frame
        loop keeps ticking the mixer + redrawing the PLAY/STOP button)."""
        if self.music_preview is None:
            return False
        au = self.audio
        if au is None or getattr(au, "engine", None) is None:
            return False
        return au.engine.is_active()

    # -- cross-cart sprite reuse (#18) ---------------------------------------
    #
    # The shared sheet is a single .kgfx living beside the carts dir. PUT copies
    # the tile a kid is painting INTO that shared sheet; GET copies a tile back
    # OUT of it into whatever cart they're painting next -- so a sprite travels
    # between carts without being repainted. Both go through SpriteSheet.copy_tile
    # (the import primitive) and the kid_carts shared-sheet store.

    def _load_shared_sheet(self):
        """Read the shared sheet into a SpriteSheet (empty one if never saved)."""
        try:
            hexs = self._with_sd(lambda: self.carts_store.load_shared_sheet(self.carts_root))
        except Exception as exc:  # noqa: BLE001
            print("KidCode load shared sheet failed:", exc)
            return None
        if hexs:
            try:
                return SpriteSheet.from_hex(hexs)
            except Exception:  # noqa: BLE001
                pass
        return SpriteSheet()

    def share_tile_get(self):
        """Import the current tile FROM the shared sheet into this cart's sheet
        (same tile id). The kid then SAVEs the cart sheet to keep it."""
        if not (self.paint and self.sheet):
            return False
        shared = self._load_shared_sheet()
        if shared is None:
            self.paint_status = "NO SHARED"
            return False
        if shared.is_blank():
            self.paint_status = "SHARED EMPTY"   # nothing painted there yet
            return False
        n = self.paint.n
        if self.sheet.copy_tile(shared, n, dst_n=n) is None:
            self.paint_status = "GET FAILED"
            return False
        self.paint_status = "GOT SPR " + str(n)
        return True

    def share_tile_put(self):
        """Save the current tile TO the shared sheet (persisted), so another cart
        can GET it. Loads the shared sheet, drops this tile in at the same id, and
        writes it back."""
        if not (self.paint and self.sheet):
            return False
        if not (self.carts_root and self.can_manage):
            self.paint_status = None             # writes deferred -- nothing to persist
            return False
        shared = self._load_shared_sheet()
        if shared is None:
            self.paint_status = "PUT FAILED"
            return False
        n = self.paint.n
        if shared.copy_tile(self.sheet, n, dst_n=n) is None:
            self.paint_status = "PUT FAILED"
            return False
        try:
            hexs = shared.to_hex()
            self._with_sd(lambda: self.carts_store.save_shared_sheet(hexs, self.carts_root))
        except Exception as exc:  # noqa: BLE001
            self.paint_status = "PUT FAILED"
            print("KidCode save shared sheet failed:", exc)
            return False
        self.paint_status = "PUT SPR " + str(n)
        return True

    def apply(self):
        # Re-run with the new config. Always return to the desktop: on success it
        # runs, on failure frame() paints the error panel there (still reachable).
        ok = self._start()
        self.screen = "desktop"
        if ok:
            self.ach.note("run")                # "Lift Off!": GO re-ran the cart (#21)
            self._save_config()

    def _save_config(self):
        # Persist edits to the SD cartridge (embedded fallback carts have no path).
        if self.cart and self.cart.get("path"):
            self.cart["cfg"] = dict(self.config)   # in-RAM sync (always)
            if not self.can_manage:
                return                             # writes deferred on device
            try:
                self._with_sd(lambda: self.carts_store.save_config(self.cart))
            except Exception as exc:  # noqa: BLE001
                print("KidCode save failed:", exc)

    def go_home(self):
        self._dirty = True             # screen change repaints (#44)
        self._set_text_mode(False)    # restore the game-button keyboard mode
        self.editor = None
        self.paint = None
        self._editing_icons = False    # never carry the theme-editing flag home
        self.mapedit = None
        self.blocks_ed = None
        self.blk_menu = None
        self.blk_kbd = None
        self.blk_protect = False
        self.screen = "launcher"
        self.cart = None
        self.ns = None
        self.cart_error = None
        self.save_status = None
        self.show_achievements = False
        self._konami_pos = 0          # fresh Konami run on the home desktop (#21)
        self._clock_taps = 0

    # -- cart management (SD) ------------------------------------------------
    #
    # Each action mounts the SD card, mutates, and re-scans within a single
    # _with_sd session, then the card is unmounted before the next flush.

    def _apply_items(self, items):
        if items:
            self.launcher.set_items(items)

    def new_cart(self):
        if not self.carts_root or not self.can_manage:
            return
        try:
            self._apply_items(self._with_sd(lambda: (
                self.carts_store.new_from_template(self.carts_root),
                self.carts_store.scan(self.carts_root))[1]))
        except Exception as exc:  # noqa: BLE001
            print("KidCode new cart failed:", exc)

    def dup_cart(self):
        if not self.carts_root or not self.can_manage or not self.launcher.selected():
            return
        sel = self.launcher.selected()
        try:
            self._apply_items(self._with_sd(lambda: (
                self.carts_store.duplicate(sel, self.carts_root),
                self.carts_store.scan(self.carts_root))[1]))
        except Exception as exc:  # noqa: BLE001
            print("KidCode duplicate failed:", exc)

    def del_cart(self):
        if not self.carts_root or not self.can_manage or len(self.launcher.items) <= 1:
            return  # keep at least one cartridge
        sel = self.launcher.selected()
        try:
            self._apply_items(self._with_sd(lambda: (
                self.carts_store.delete(sel),
                self.carts_store.scan(self.carts_root))[1]))
        except Exception as exc:  # noqa: BLE001
            print("KidCode delete failed:", exc)

    def adjust(self, d):
        f = self.cart["edit"][self.msel]
        key = f["key"]
        cur = self.config.get(key, f.get("default"))
        if f["type"] == "int":
            v = int(cur) + d * f.get("step", 1)
            if "min" in f:
                v = max(f["min"], v)
            if "max" in f:
                v = min(f["max"], v)
            self.config[key] = v
        elif f["type"] == "choice":
            ch = f["choices"]
            idx = ch.index(cur) if cur in ch else 0
            self.config[key] = ch[(idx + d) % len(ch)]

    def card_text(self, i):
        f = self.cart["edit"][i]
        v = self.config.get(f["key"], f.get("default"))
        if f["type"] == "choice":
            v = self._choice_label(f, v)
        t = f.get("card")
        return t.replace("{value}", str(v)) if t else "%s: %s" % (f["key"].upper(), v)

    # -- visual ("display") cards (#15) --------------------------------------
    #
    # A card field MAY carry an optional `display` hint -- "gauge" | "count" |
    # "choice-icons" | "sprite-tiles" -- that draws the VALUE as a picture a kid
    # who can't read can recognize, with the number/word kept as a small SECONDARY
    # cue. When `display` is absent the card renders exactly as before (one text
    # line), so every existing cart keeps working untouched.

    _DISPLAYS = ("gauge", "count", "choice-icons", "sprite-tiles", "bg-thumbs")
    _CELL_DISPLAYS = ("choice-icons", "sprite-tiles", "bg-thumbs")

    def _card_display(self, f):
        d = f.get("display")
        return d if d in self._DISPLAYS else None

    def _choice_label(self, f, v):
        """A short readable label for a choice value -- a kid-friendly word for a
        string choice, or just the id for tile/number choices."""
        if isinstance(v, str):
            return v.replace("_", " ").upper()
        return str(v)

    def _choice_index(self, f, cur):
        ch = f["choices"]
        return ch.index(cur) if cur in ch else 0

    def _resolve_tiles(self, f):
        """For a `sprite-tiles` field, the list of sprite tile ids its choices map
        to. `choices` may be ints (tile ids directly) or names paired with a
        parallel `tiles` list. Returns ints; non-resolvable entries become 0."""
        src = f.get("tiles")
        if not src:
            src = f.get("choices", [])
        out = []
        for c in src:                          # guard BOTH branches: a non-numeric
            try:                               # tiles/choices entry must not escape
                out.append(int(c))             # _draw_cards -> device hang (#15).
            except (TypeError, ValueError):
                out.append(0)
        return out

    def _card_height(self, f):
        d = self._card_display(f)
        if d in ("sprite-tiles", "bg-thumbs"):
            return 44
        if d == "choice-icons":
            return 36         # cells are 22px tall at y+12 -> bottom y+34 fits in 36
        if d in ("gauge", "count"):
            return 32
        return _CARD_H

    def _card_layout(self):
        """Pure (no-draw) per-card geometry for the VISIBLE cards so draw and
        hit-test agree (#3). Cards lay out top-down from _CARD_Y0 starting at the
        scrolled-in index self.mtop; a row is included only while its bottom stays
        within _CARD_VIEW_BOTTOM (so cards never overlap the RUN/CODE/CLOSE bar).
        Returns dicts: {i, f, display, x, y, w, h}."""
        rows = []
        y = _CARD_Y0
        top = self._clamp_mtop()
        for i in range(top, len(self.cart["edit"])):
            f = self.cart["edit"][i]
            h = self._card_height(f)
            if i > top and y + h > _CARD_VIEW_BOTTOM:
                break                       # next row would spill past the buttons
            rows.append({"i": i, "f": f, "display": self._card_display(f),
                         "x": _CARD_X, "y": y, "w": _CARD_W, "h": h})
            y += h + 2
        return rows

    def _card_count(self):
        return len(self.cart["edit"]) if self.cart and self.cart.get("edit") else 0

    def _max_mtop(self):
        """Topmost card index that still leaves the view full from the bottom up:
        walk heights backwards, summing until the next card would no longer fit."""
        n = self._card_count()
        if n == 0:
            return 0
        avail = _CARD_VIEW_BOTTOM - _CARD_Y0
        used = 0
        top = n
        for i in range(n - 1, -1, -1):
            h = self._card_height(self.cart["edit"][i])
            step = h if top == n else h + 2
            if used + step > avail:
                break
            used += step
            top = i
        # Never park past the last card: even a card taller than the window must
        # still be reachable (_card_layout always shows at least the top row).
        return min(top, n - 1)

    def _clamp_mtop(self):
        self.mtop = max(0, min(self._max_mtop(), self.mtop))
        return self.mtop

    def scroll_cards(self, d):
        """Scroll the cards window by d rows (clamped). Independent of msel."""
        self.mtop = max(0, min(self._max_mtop(), self.mtop + d))

    def _cards_scrollable(self):
        """True when not all cards fit at once (so the chevrons are live)."""
        return self._max_mtop() > 0

    def _reveal_card(self, i):
        """Scroll so card i is on screen (mirror Launcher._scroll): bring it down
        into view if it's above the window, or up into view if it's below."""
        if i < self.mtop:
            self.mtop = i
        else:
            # Page the window down one card at a time until i's row is included.
            guard = self._card_count()
            while guard >= 0:
                if any(r["i"] == i for r in self._card_layout()):
                    break
                if self.mtop >= self._max_mtop():
                    break
                self.mtop += 1
                guard -= 1
        self._clamp_mtop()

    def _choice_cells(self, row):
        """Tappable cells for a choice-icons / sprite-tiles card: one box per
        choice, laid out left-to-right under the label. Returns a list of
        (choice_index, cell_rect)."""
        f = row["f"]
        n = len(f.get("choices", []))
        if n <= 0:
            return []
        if row["display"] == "bg-thumbs":
            cw, ch = 40, 26                # wide thumbnails for background previews
        elif row["display"] == "sprite-tiles":
            cw = ch = 26
        else:
            cw = ch = 22
        gap = 4
        x0 = row["x"] + 4
        top = row["y"] + 12
        cells = []
        for k in range(n):
            cells.append((k, (x0 + k * (cw + gap), top, cw, ch)))
        return cells

    def handle_input(self):
        i = self.input
        # Redraw-on-change (#44): a button PRESS edge or a typed key this frame may
        # change visible state (nav, select, screen/menu switch, an edit), so request a
        # repaint. Only the press edge (not release, not a steady hold) is marked: every
        # UI handler acts on i.pressed()/the typed key, never on the release, so a press
        # draws exactly one frame and the UI is static again -- a release/hold that
        # changes nothing costs nothing. Pointer-driven changes (click/drag/cursor move)
        # are caught separately in frame() via the pointer-state snapshot. Conservative
        # but never stale: a press that's a no-op costs one redraw, not a wrong screen.
        if getattr(i, "_pressed", None) or i.last_key:
            self._dirty = True
        # System menu / About modal (#52): when either overlay is up it is MODAL --
        # it eats this frame's keys (one moving cursor) and returns, so nav never
        # leaks to the screen underneath. ESC (stop) / B dismiss; Up/Down move
        # (skipping headers); Enter/A/RUN select (close-on-select). Checked before the
        # per-screen branches so the menu owns input while open; closed = zero change.
        if self._about:
            if i.pressed("b") or i.pressed("stop") or i.pressed("a") or i.pressed("run"):
                self._about = False
            return
        if self.sysmenu.open:
            if i.pressed("b") or i.pressed("stop"):
                self.sysmenu.close()
            elif i.pressed("up"):
                self.sysmenu.move(-1)
            elif i.pressed("down"):
                self.sysmenu.move(1)
            elif i.pressed("a") or i.pressed("run"):
                self.sysmenu.activate()
            return
        if self.screen == "launcher":
            # Konami Easter egg (#21): watch every button press on the home desktop
            # for the secret sequence (the nav below still runs normally -- the egg
            # is a passive observer, so it never blocks the launcher).
            for _b in self._KONAMI:
                if i.pressed(_b):
                    self._konami_step(_b)
                    break
            # Grid nav (#28): left/right step a column, up/down a whole row.
            if i.pressed("left"):
                self.launcher.nav2d(-1, 0)
            if i.pressed("right"):
                self.launcher.nav2d(1, 0)
            if i.pressed("up"):
                self.launcher.nav2d(0, -1)
            if i.pressed("down"):
                self.launcher.nav2d(0, 1)
            if i.pressed("a") or i.pressed("run"):
                self.open()
        elif self.screen == "settings":
            rows = self._settings_rows()
            if i.pressed("up"):
                self.set_msel = (self.set_msel - 1) % len(rows)
            if i.pressed("down"):
                self.set_msel = (self.set_msel + 1) % len(rows)
            self._settings_scroll()        # keep the selection in view (#53)
            if i.pressed("left"):
                self.settings_adjust(-1)
            if i.pressed("right"):
                self.settings_adjust(1)
            if i.pressed("a") or i.pressed("run"):  # activate an action row (EDIT ICONS / UPDATE FW)
                row = rows[self.set_msel % len(rows)]
                if row[2] == "action":
                    self._activate_settings_action(row[0])
                elif row[2] == "web":               # A/run also toggles the web view (#41)
                    self._toggle_web_view()
            if i.pressed("b"):
                self._exit_settings()          # back -> resume the cart if opened from one
            elif i.pressed("home") or i.pressed("stop"):
                self.go_home()
        elif self.screen == "update":
            self._update_input(i)
        elif self.screen == "desktop":
            if i.pressed("home") or i.pressed("stop"):
                self.go_home()
            elif i.pressed("b"):
                self._open_menu()
        elif self.screen == "menu":
            if self.menu_view == "code":
                self._editor_input()           # keyboard is in text mode here
                return
            if self.menu_view == "blocks":
                self._blocks_input()           # cursor nav + insert menu (#29)
                return
            if self.menu_view == "paint":
                return                         # paint is pointer/touch-driven
            if self.menu_view == "theme":
                # EDIT ICONS: pointer/touch-driven like PAINT; B closes back to Settings.
                if i.pressed("b"):
                    self._leave_theme()
                elif i.pressed("home"):
                    self.go_home()
                return
            if self.menu_view == "map":
                # The d-pad pans the visible map window (the grid is bigger than the
                # screen); B leaves (#37). Painting stays pointer/touch-driven.
                me = self.mapedit
                if me is not None:
                    if i.pressed("up"):
                        self._map_pan(0, -1)
                    if i.pressed("down"):
                        self._map_pan(0, 1)
                    if i.pressed("left"):
                        self._map_pan(-1, 0)
                    if i.pressed("right"):
                        self._map_pan(1, 0)
                    if i.pressed("a"):          # A cycles the zoom level (#37 follow-up)
                        self._map_cycle_zoom()
                if i.pressed("b"):
                    self._leave_menu()
                elif i.pressed("home"):
                    self.go_home()
                return
            if self.menu_view == "music":
                # D-pad navigates the tracker (#50): up/down move the step/slot cursor,
                # left/right change the value under it (pitch / SFX-id), A plays/stops
                # the preview, B leaves. Tap remains the primary path; this gives the
                # trackball + keyboard parity with the other editors.
                me = self.musicedit
                if me is not None:
                    if i.pressed("up"):
                        me.move_cursor(-1)
                    if i.pressed("down"):
                        me.move_cursor(1)
                    song = me.view == MusicEditor.SONG_VIEW
                    if i.pressed("left"):
                        (me.nudge_slot if song else me.nudge_pitch)(-1)
                    if i.pressed("right"):
                        (me.nudge_slot if song else me.nudge_pitch)(1)
                    if i.pressed("a"):
                        if self.music_preview is not None:
                            self._stop_music_preview()
                        else:
                            self._play_music_preview()
                if i.pressed("b"):
                    self._leave_menu()
                elif i.pressed("home"):
                    self.go_home()
                self._dirty = True
                return
            ed = self.cart.get("edit")
            if not ed:
                return
            if i.pressed("up"):
                self.msel = (self.msel - 1) % len(ed)
                self._reveal_card(self.msel)
            if i.pressed("down"):
                self.msel = (self.msel + 1) % len(ed)
                self._reveal_card(self.msel)
            if i.pressed("left"):
                self.adjust(-1)
            if i.pressed("right"):
                self.adjust(1)
            if i.pressed("a"):
                self.set_menu_view("code")
            if i.pressed("run"):
                self.apply()
            elif i.pressed("b"):
                self._leave_menu()
            elif i.pressed("home"):
                self.go_home()

    # -- pointer (trackball-as-mouse) ----------------------------------------

    def _dock_slot_rect(self, k):
        return self.layout.dock_slot_rect(k)

    def _dock_slot_at(self, px, py):
        """Which dock slot ("home"/"code"/.../"settings") was tapped, or None."""
        if py < self.layout.dock_y:
            return None
        for k in range(len(_DOCK_SLOTS)):
            if _in(px, py, self._dock_slot_rect(k)):
                return _DOCK_SLOTS[k]
        return None

    def _activate_dock(self, slot):
        """Run a dock action. The dock is drawn on home + settings; from the home
        desktop only home/settings/run apply (no open cart for the editors), but if
        a cart is still open behind Settings the tool slots switch its active editor
        (TIC-80 style). run = open the selected cart from home, or re-run the open
        one from Settings."""
        if slot == "home":
            self.go_home()
        elif slot == "settings":
            self.open_settings()
        elif self.cart is not None:                # tool slots need an open cart
            if slot == "code":
                self._open_menu()
            elif slot == "paint":
                self._open_paint()
            elif slot == "map":
                self._open_map()
            elif slot == "run":
                self.run_code() if self.editor is not None else self.apply()
        elif slot == "run" and self.launcher.selected() is not None:
            self.open()                            # on the home desktop, run = open selected

    def _launcher_pointer(self, px, py, click):
        # Desktop home (#28): a tap on a cart icon opens it; the gear + management
        # row + page chevrons fire on the press edge. There's no list drag anymore --
        # the grid pages instead. Trackball hover still previews the icon under it.
        # The bottom in-cart dock is no longer drawn on the launcher (#46), so it's not
        # hit-tested here; Settings is reached via the gear in the status strip.
        if click:
            # Clock Easter egg (#21): tapping the status-strip clock _CLOCK_TAP_GOAL
            # times wakes the Time Traveler. Checked before the management row so a
            # tap on the clock never falls through to a button.
            lay = self.layout
            if _in(px, py, lay.clock_hit()):
                self._tap_clock()
                return
            self._clock_taps = 0                # any other desktop tap resets the run
            if _in(px, py, lay.sysmenu_btn):    # ≡ -> system menu (Settings/About/Reboot live here now, #52)
                self.toggle_sysmenu()
                return
            if self.can_manage and _in(px, py, lay.new_btn):
                self.new_cart(); return
            if self.can_manage and _in(px, py, lay.dup_btn):
                self.dup_cart(); return
            if self.can_manage and _in(px, py, lay.del_btn):
                self.del_cart(); return
            if self.launcher.max_page() > 0 and _in(px, py, lay.page_prev):
                self.launcher.flip_page(-1); return
            if self.launcher.max_page() > 0 and _in(px, py, lay.page_next):
                self.launcher.flip_page(1); return
            i = self.launcher.tile_at(px, py)
            if i is not None:
                self.launcher.sel = i
                self.open()
                return
        # Trackball cursor hover (no click): highlight the icon the cursor MOVED
        # onto. Only when the position actually changed frame-to-frame, so a
        # parked cursor doesn't fight keyboard nav. _lhover seeds to the live
        # pointer position on the first frame so the initial centered cursor isn't
        # treated as a move (which would clobber the first arrow step).
        if self._lhover == (-1, -1):
            self._lhover = (px, py)
        elif (px, py) != self._lhover:
            self._lhover = (px, py)
            i = self.launcher.tile_at(px, py)
            if i is not None:
                self.launcher.sel = i

    def _settings_visible(self):
        """How many Settings rows fit in the panel at the current font scale (#39)."""
        lay = self.layout
        _px, py, _pw, ph = lay.settings_panel
        n = (py + ph - lay.set_row_y0) // lay.set_row_h
        return max(1, int(n))

    def _settings_scroll(self):
        """Keep the selected row (set_msel) inside the visible window by moving the
        scroll offset set_top. The list scrolls once it has more rows than fit -- the
        #53 OTA rows (UPDATE FW / CHANNEL / UPDATE ONLINE) push it past one screen."""
        rows = len(self._settings_rows())
        vis = self._settings_visible()
        if self.set_msel < self.set_top:
            self.set_top = self.set_msel
        elif self.set_msel >= self.set_top + vis:
            self.set_top = self.set_msel - vis + 1
        self.set_top = max(0, min(self.set_top, max(0, rows - vis)))

    def _settings_row_visible(self, i):
        return self.set_top <= i < self.set_top + self._settings_visible()

    def _settings_row_rect(self, i):
        # Scrolled position: row i sits in on-screen slot (i - set_top). Rows outside
        # the visible window get an off-panel rect that the draw + pointer loops skip.
        return self.layout.settings_row_rect(i - self.set_top)

    def _settings_pointer(self, px, py, click):
        if not click:
            return
        # The achievements view is a modal overlay: while it's up, any tap closes it
        # (it has no controls of its own besides "tap to dismiss").
        if self.show_achievements:
            self.show_achievements = False
            return
        lay = self.layout
        if _in(px, py, lay.set_ach):           # trophy: open the achievements view (#21)
            self.show_achievements = True
            self._secret_taps = 0
            return
        if _in(px, py, lay.set_back):
            self._exit_settings()
            return
        # Secret-door Easter egg (#21): tapping the SETTINGS title (not a button)
        # _SECRET_TAP_GOAL times knocks the hidden door open. Reset on any other tap.
        if _in(px, py, lay.set_title_hit):
            self._tap_secret_door()
            return
        self._secret_taps = 0
        slot = self._dock_slot_at(px, py)
        if slot is not None:
            self._activate_dock(slot)
            return
        edge = 5 * self.layout.font_w           # the "<"/">" hit zone (40px at fs=1)
        rows = self._settings_rows()
        for i in range(len(rows)):
            if not self._settings_row_visible(i):
                continue                       # off-screen (scrolled) rows aren't tappable
            x, y, w, h = self._settings_row_rect(i)
            if _in(px, py, (x, y, w, h)):
                self.set_msel = i
                if rows[i][2] == "action":
                    self._activate_settings_action(rows[i][0])  # EDIT ICONS / UPDATE FW
                    return
                if rows[i][2] == "web":            # web view: any tap flips ON/OFF (#41)
                    self._toggle_web_view()
                    return
                # left third = "<" (decrement), right third = ">" (increment).
                if px >= x + w - edge:
                    self.settings_adjust(1)
                elif px <= x + edge:
                    self.settings_adjust(-1)
                return

    def _card_at(self, px, py):
        for row in self._card_layout():
            if _in(px, py, (row["x"], row["y"], row["w"], row["h"])):
                return row["i"]
        return None

    def _card_tap(self, px, py, ci):
        """Apply a tap inside card `ci`. For an icon/sprite picker, tapping a
        specific choice cell SETS that choice (no scrolling needed -- a kid taps
        the picture they want). Otherwise the card is a -/+ stepper: the left half
        decrements, the right half increments (matching the on-card glyphs)."""
        for row in self._card_layout():
            if row["i"] != ci:
                continue
            if row["display"] in self._CELL_DISPLAYS:
                for k, cell in self._choice_cells(row):
                    if _in(px, py, cell):
                        self.config[row["f"]["key"]] = row["f"]["choices"][k]
                        return
            self.adjust(-1 if px < _CARD_X + _CARD_W // 2 else 1)
            return

    def handle_pointer(self):
        p = self.pointer
        if p is None:
            return
        px, py, click = p.x, p.y, p.click
        # The desktop chrome (launcher/settings) hit-tests in SYSTEM coords; a running
        # cart + the editors live in the 320x240 GAME viewport, so translate the
        # pointer into game coords for those (#39). Also publish the game-space
        # pointer so the cart touch()/mouse() API reads the viewport, not the panel.
        gx, gy = self._game_xy(px, py)
        # System menu / About modal (#52): drawn on the SYSTEM canvas at fixed top-left
        # coords, so they hit-test in SYSTEM (px, py) -- NOT the game viewport. When
        # either is up it's MODAL and checked FIRST: the tap is consumed here (a row taps
        # move+select, a tap OUTSIDE dismisses) and never falls through to the screen
        # underneath. Clear the game pointer's tap too so a running cart's touch() never
        # also sees a tap the menu just swallowed.
        if self._about:
            if click:
                self._about = False        # any tap dismisses the About modal
                self._dirty = True
            self.input.game_pointer = (gx, gy, False, False)
            return
        if self.sysmenu.open:
            if click:
                self._dirty = True
                self.sysmenu.click(px, py)   # row -> move+select; outside -> dismiss
            self.input.game_pointer = (gx, gy, False, False)
            return                     # swallow non-click moves too while it's open
        self.input.game_pointer = (gx, gy, click, p.down)
        if self.screen == "launcher":
            self._launcher_pointer(px, py, click)
        elif self.screen == "settings":
            self._settings_pointer(px, py, click)
        elif self.screen == "update":
            self._update_pointer(px, py, click)
        elif self.screen == "desktop":
            px, py = gx, gy
            # While a cart runs the unified TOP BAR (HOME, EDIT/CODE, PAINT, MAP,
            # BLOCKS as 16x16 icons) is the TIC-80 one-tap tool switcher -- it occludes
            # only the 18px bar at the top so gameplay keeps the rest of the screen (a
            # bottom dock would cover the play area). The icon rects are the same
            # _HOME_BTN/_MENU_BTN/... constants the bar draws from, so a tap on an icon
            # fires its action.
            if click:
                if _in(px, py, _SYSMENU_BTN):
                    self.toggle_sysmenu()      # ≡ -> open the dropdown system menu (#52)
                elif _in(px, py, _HOME_BTN):
                    self.go_home()
                elif _in(px, py, _MENU_BTN):
                    self._open_menu()
                elif _in(px, py, _PAINT_BTN):
                    self._open_paint()
                elif _in(px, py, _MAP_BTN):
                    self._open_map()
                elif _in(px, py, _BLOCKS_BTN):
                    self._open_blocks()
                elif _in(px, py, _MUSIC_BTN):
                    self._open_music()
                elif self.show_fps and _in(px, py, self._fps_tap_rect()):
                    # Tapping the FPS readout toggles the frame-time breakdown HUD
                    # (#43/#44 perf). Deliberate, no keyboard, doesn't fight game
                    # input -- the touch lands on a small bottom-right corner box.
                    self.perf_hud = not self.perf_hud
        elif self.screen == "menu":
            px, py = gx, gy                    # editors live in the 320x240 viewport
            if self.menu_view == "code":
                self._code_drag(px, py)        # touch/mouse drag pans the viewport
                if click:
                    if _in(px, py, _ED_RUN):
                        self.run_code()
                    elif _in(px, py, _ED_SAVE):
                        self.save_code()
                    elif _in(px, py, _ED_CLOSE):
                        self._leave_menu()
                    elif _in(px, py, _SYM_AREA) and self.editor is not None:
                        i = (px - _SYM_AREA[0]) // _SYM_CELL   # tap a coding symbol
                        if 0 <= i < len(_CODE_SYMBOLS):
                            self.editor.key(ord(_CODE_SYMBOLS[i]))
                    elif self.editor is not None and _in(px, py, _CODE_AREA):
                        self.editor.place((px - _CODE_X0) // 8,
                                          (py - _CODE_Y0) // _CODE_LH)
                return
            if self.menu_view in ("paint", "theme"):
                # A tap (click) routes through _paint_click (grid OR buttons). A
                # held drag with no fresh click keeps painting the grid stroke so
                # press-and-move draws a continuous line -- the same path for a host
                # mouse drag and a device touch drag (both = pointer.down + moving
                # position). Releasing resets the stroke origin (#30). The theme editor
                # (EDIT ICONS) reuses this exact path over the icon sheet.
                if click:
                    self._paint_click(px, py)
                elif p.down:
                    self._paint_stroke(px, py)
                else:
                    self._paint_drag = None
                return
            if self.menu_view == "map":
                # Tap = paint one cell, drag = pan (#37). The map grid is bigger
                # than the on-screen window, so dragging the grid scrolls the view;
                # only a short press-and-release stamps the brush there. A palette
                # pick / button press fires on the click edge as usual; a tap on the
                # MAP VIEW is deferred to release so a drag that turns into a pan
                # never leaves a stray stamp at its origin.
                if click:
                    self._map_click(px, py)
                elif p.down:
                    self._map_pan_drag(px, py)
                else:
                    self._map_release(px, py)
                return
            if self.menu_view == "blocks":
                self._blocks_pointer(px, py, click)   # outline + insert menu (#29)
                return
            if self.menu_view == "music":
                if click:
                    self._music_click(px, py)         # step list + edit pad + actions
                return
            ci = self._card_at(px, py)
            if ci is not None:
                self.msel = ci                 # hover highlights
            if click:
                if _in(px, py, _RUN_BTN):
                    self.apply()
                elif _in(px, py, _CODE_BTN):
                    self.set_menu_view("code")
                elif _in(px, py, _CLOSE_BTN):
                    self._leave_menu()
                elif self._cards_scrollable() and _in(px, py, _CARD_SCROLL_UP):
                    self.scroll_cards(-1)
                elif self._cards_scrollable() and _in(px, py, _CARD_SCROLL_DN):
                    self.scroll_cards(1)
                elif ci is not None:
                    self._card_tap(px, py, ci)

    def nav(self, dx, dy):
        # Directional input (host arrows / device trackball). In the code editor it
        # moves the CARET (the view follows it); elsewhere the launcher/desktop are
        # pointer-driven, so this is a no-op there.
        if (self.screen == "menu" and self.menu_view == "code"
                and self.editor is not None and (dx or dy)):
            self.editor.move(dy, dx)
            self._dirty = True             # caret moved -> redraw (#44)

    def _code_drag(self, px, py):
        # Touch/mouse drag inside the code area pans the viewport (content follows
        # the finger): drag down -> see earlier lines, drag right -> see left text.
        ed = self.editor
        if ed is None or not self.pointer.down or not _in(px, py, _CODE_AREA):
            self._drag = None
            return
        if self._drag is None:
            self._drag = (px, py)
            return
        drows = (py - self._drag[1]) // _CODE_LH
        dcols = (px - self._drag[0]) // 8
        if drows or dcols:
            ed.scroll(-drows, -dcols)
            self._drag = (px, py)

    def _paint_grid_cell(self, px, py):
        """Grid-local pixel (lx, ly) under (px, py), or None when outside the grid.
        The cell size shrinks as the sprite grows so the size*8 region always fills
        the fixed _PG_SPAN footprint (#30)."""
        pe = self.paint
        if pe is None or not _in(px, py, _PG_AREA):
            return None
        cell = _PG_SPAN // pe.dim
        if cell < 1:
            cell = 1
        lx = (px - _PG_X0) // cell
        ly = (py - _PG_Y0) // cell
        if 0 <= lx < pe.dim and 0 <= ly < pe.dim:
            return (lx, ly)
        return None

    def _paint_stroke(self, px, py):
        """Drag-to-draw (#30): paint the grid cell under (px, py) AND fill the line
        from the last painted cell, so a fast drag leaves no gaps. Works the same
        for a host mouse drag and a device touch drag -- both arrive as pointer.down
        with the position updated each frame. Returns True if a cell was painted."""
        pe = self.paint
        cell = self._paint_grid_cell(px, py)
        if cell is None:
            self._paint_drag = None        # left the grid -> next entry starts fresh
            return False
        last = self._paint_drag
        if last is None:
            pe.paint(cell[0], cell[1])
        else:
            for cx, cy in _line_cells(last[0], last[1], cell[0], cell[1]):
                pe.paint(cx, cy)
        self._paint_drag = cell
        return True

    def _paint_click(self, px, py):
        # A tap (press edge). Paint the grid cell, or hit a button/palette swatch.
        pe = self.paint
        if pe is None:
            return
        if self._paint_stroke(px, py):         # paint a pixel in the zoomed grid
            return
        if _in(px, py, _SW_AREA):              # pick a palette color
            idx = ((py - _SW_Y0) // _SW) * _SW_COLS + ((px - _SW_X0) // _SW)
            if 0 <= idx < 16:
                pe.color = idx
        elif _in(px, py, _SPR_PREV):
            pe.select(-1)
        elif _in(px, py, _SPR_NEXT):
            pe.select(1)
        elif _in(px, py, _PAINT_SIZE):         # cycle 1x1 / 2x2 / 3x3 (#30)
            pe.cycle_size()
        elif _in(px, py, _PAINT_GET) and not self._editing_icons:
            self.share_tile_get()              # import the tile from the shared sheet
        elif _in(px, py, _PAINT_PUT) and not self._editing_icons:
            self.share_tile_put()              # save the tile to the shared sheet
        elif _in(px, py, _PAINT_SAVE):
            # SAVE persists the SYSTEM icon theme (EDIT ICONS) or the cart's sprites.
            self.save_icons() if self._editing_icons else self.save_sprites()
        elif _in(px, py, _PAINT_CLOSE):
            # CLOSE returns to Settings (theme editor) or runs+leaves to the cart (PAINT).
            self._leave_theme() if self._editing_icons else self._leave_menu()

    def _map_palette_ids(self):
        """The tile ids shown on the current palette page (a window into the sheet,
        clamped so the last page never runs past the sheet's tile count)."""
        if self.sheet is None:
            return []
        count = self.sheet.count
        start = self.map_page
        return list(range(start, min(start + _TP_PAGE, count)))

    def _mv_metrics(self):
        """The LIVE map-view metrics for the current zoom level (#37 follow-up):
        (x0, y0, cell, cols, rows). `cell` is the px per cell at the current zoom;
        `cols`/`rows` are how many whole cells fit the available rectangle. All map
        hit-testing, panning and drawing route through this so they share one cell
        size -- there is no fixed _MV_CELL/_MV_COLS/_MV_ROWS any more."""
        idx = self.map_zoom
        if idx < 0:
            idx = 0
        elif idx >= len(_MV_ZOOMS):
            idx = len(_MV_ZOOMS) - 1
        cell = _MV_ZOOMS[idx]
        cols = _MV_AVAIL_W // cell
        rows = _MV_AVAIL_H // cell
        return (_MV_X0, _MV_Y0, cell, cols, rows)

    def _mv_area(self):
        """The current map-view rectangle (x, y, w, h) for _in() hit-tests."""
        x0, y0, cell, cols, rows = self._mv_metrics()
        return (x0, y0, cols * cell, rows * cell)

    def _map_clamp_cam(self):
        """Clamp the camera so you can't scroll far past the map edges at the
        current zoom: the top-left visible cell stays in [0, max(0, dim - visible)],
        so a map smaller than the view always pins to (0, 0) (no panning needed)."""
        me = self.mapedit
        tm = self.tilemap
        if me is None or tm is None:
            return
        x0, y0, cell, cols, rows = self._mv_metrics()
        me.cam_x = max(0, min(max(0, tm.w - cols), me.cam_x))
        me.cam_y = max(0, min(max(0, tm.h - rows), me.cam_y))

    def _map_cycle_zoom(self):
        """Cycle to the next zoom level (wrapping back to the fit-both default),
        then re-clamp the camera so a zoom-out can't leave it scrolled off-map."""
        self.map_zoom = (self.map_zoom + 1) % len(_MV_ZOOMS)
        self._map_clamp_cam()

    def _map_pan(self, dcx, dcy):
        """Pan the camera by (dcx, dcy) cells then clamp to the map edges at the
        current zoom (the editor's clamp, which knows the visible cols/rows -- the
        MapEditor.pan clamp is zoom-agnostic so we re-clamp here)."""
        me = self.mapedit
        if me is None:
            return
        me.cam_x = me.cam_x + dcx
        me.cam_y = me.cam_y + dcy
        self._map_clamp_cam()

    def _map_cell_at(self, px, py):
        """The map cell (cx, cy) under pointer (px, py) accounting for the pan
        offset, or None when the pointer is outside the visible map view."""
        me = self.mapedit
        if me is None or not _in(px, py, self._mv_area()):
            return None
        x0, y0, cell, cols, rows = self._mv_metrics()
        cx = me.cam_x + (px - x0) // cell
        cy = me.cam_y + (py - y0) // cell
        return (cx, cy)

    def _map_paint(self, cx, cy):
        """Stamp the brush at map cell (cx, cy): the EMPTY brush (#37) clears the
        cell (paints sky/background), otherwise the brush's tile is placed. The
        ERASE toggle still forces a clear regardless of the brush."""
        me = self.mapedit
        if me is None:
            return
        if self.map_erase or me.n < 0:
            me.erase(cx, cy)
        else:
            me.place(cx, cy)

    def _map_pan_drag(self, px, py):
        """Held-drag handler for the map view (#37): once a drag that began inside
        the map view moves past _MAP_PAN_THRESH px it latches PAN mode for the rest
        of the gesture and scrolls the camera by the drag delta (in cells), so the
        content follows the finger. A drag is a pan, not a paint -- so when it
        latches it REVERTS the cell stamped on the press edge (the tap-paint), which
        means a tap paints and a drag pans without a stray stamp at the origin."""
        press = self._map_press
        if press is None:
            return
        if not self._map_panning:
            if abs(px - press[0]) < _MAP_PAN_THRESH and abs(py - press[1]) < _MAP_PAN_THRESH:
                return                         # still within the tap dead-zone
            self._map_panning = True           # crossed the threshold -> this is a pan
            self._map_drag = press
            self._map_revert_paint()           # undo the press-edge stamp (it was a pan)
        me = self.mapedit
        last = self._map_drag
        if me is None or last is None:
            return
        x0, y0, cell, cols, rows = self._mv_metrics()
        dcx = (last[0] - px) // cell           # content follows the finger: drag
        dcy = (last[1] - py) // cell           # right -> see cells to the left
        if dcx or dcy:
            self._map_pan(dcx, dcy)
            # advance the anchor by whole cells consumed (keep the sub-cell remainder
            # so a slow drag still accumulates instead of stalling).
            self._map_drag = (last[0] - dcx * cell, last[1] - dcy * cell)

    def _map_revert_paint(self):
        """Undo the cell stamped on the press edge (used when a press turns into a
        pan): restore the cell's previous byte AND the map's dirty/gen counters so a
        pure pan is side-effect-free (no false '*' dirty flag, no spurious cache
        rebuild in a running cart)."""
        u = self._map_paint_undo
        tm = self.tilemap
        if u is not None and tm is not None:
            cx, cy, prev, dirty, gen = u
            if 0 <= cx < tm.w and 0 <= cy < tm.h:
                tm.cells[cy * tm.w + cx] = prev
            tm.dirty = dirty
            tm.gen = gen
        self._map_paint_undo = None

    def _map_release(self, px, py):
        """Pointer up in the map view (#37): the tap-paint already landed on the
        press edge (and a pan would have reverted it), so release just clears the
        gesture state."""
        self._map_press = None
        self._map_panning = False
        self._map_drag = None
        self._map_paint_undo = None

    def _map_click(self, px, py):
        me = self.mapedit
        if me is None:
            return
        if _in(px, py, self._mv_area()):       # a press in the map view: start a
            self._map_press = (px, py)         # gesture (tap=paint / drag=pan).
            self._map_panning = False
            self._map_drag = None
            # Paint immediately so a tap is responsive; remember the cell + its prior
            # byte so a drag-that-becomes-a-pan can revert it (no stray stamp) (#37).
            cell = self._map_cell_at(px, py)
            tm = self.tilemap
            if cell is not None and tm is not None:
                cx, cy = cell
                if 0 <= cx < tm.w and 0 <= cy < tm.h:
                    self._map_paint_undo = (cx, cy, tm.cells[cy * tm.w + cx],
                                            tm.dirty, tm.gen)
                    self._map_paint(cx, cy)
            return
        if _in(px, py, _TP_SKY):               # the EMPTY/"sky" swatch (#37)
            me.n = self.tilemap.EMPTY if self.tilemap is not None else -1
            return
        if _in(px, py, _TP_AREA):              # pick the brush tile from the palette
            col = (px - _TP_X0) // _TP_CELL
            row = (py - _TP_Y0) // _TP_CELL
            if 0 <= col < _TP_COLS and 0 <= row < _TP_ROWS:
                k = row * _TP_COLS + col
                ids = self._map_palette_ids()
                if 0 <= k < len(ids):
                    me.n = ids[k]
        elif _in(px, py, _TP_PREV):            # page the palette back/forward
            self.map_page = max(0, self.map_page - _TP_PAGE)
        elif _in(px, py, _TP_NEXT):
            if self.sheet is not None and self.map_page + _TP_PAGE < self.sheet.count:
                self.map_page += _TP_PAGE
        elif _in(px, py, _MAP_ZOOM):           # cycle the zoom level (#37 follow-up)
            self._map_cycle_zoom()
        elif _in(px, py, _PAN_UP):
            self._map_pan(0, -1)
        elif _in(px, py, _PAN_DN):
            self._map_pan(0, 1)
        elif _in(px, py, _PAN_LF):
            self._map_pan(-1, 0)
        elif _in(px, py, _PAN_RT):
            self._map_pan(1, 0)
        elif _in(px, py, _MAP_ERASE):          # toggle stamp <-> erase
            self.map_erase = not self.map_erase
        elif _in(px, py, _MAP_SAVE):
            self.save_map()
        elif _in(px, py, _MAP_CLOSE):
            self._leave_menu()

    # -- music / sound editor input (#50) ------------------------------------

    def _music_click(self, px, py):
        """Route a tap in the music editor: the step/slot list places the cursor; the
        right-hand edit pad bumps the value under it; the title-strip steppers pick the
        SFX/track + tempo; the bottom bar plays/saves/loops/closes. Mirrors _map_click's
        button-dispatch shape."""
        me = self.musicedit
        if me is None:
            if _in(px, py, _MU_CLOSE):
                self._leave_menu()
            return
        song = me.view == MusicEditor.SONG_VIEW
        # The step/slot list: tap a row to select it.
        if _in(px, py, _MU_LIST_AREA):
            total = me.slot_count() if song else me.step_count()
            cur = me.slot if song else me.step
            top = self._mu_visible_top(cur, total)
            row = (py - _MU_LIST_Y0) // _MU_ROW_H
            me.select_cursor(top + row)
            return
        # Title-strip controls.
        if _in(px, py, _MU_OBJ_PREV):
            (me.select_track if song else me.select_sfx)(-1)
            return
        if _in(px, py, _MU_OBJ_NEXT):
            (me.select_track if song else me.select_sfx)(1)
            return
        if _in(px, py, _MU_SPEED_DN):
            me.nudge_speed(-1); return
        if _in(px, py, _MU_SPEED_UP):
            me.nudge_speed(1); return
        if _in(px, py, _MU_VIEW):
            me.toggle_view()
            self._stop_music_preview()         # don't carry a preview across views
            return
        # The bottom action bar.
        if _in(px, py, _MU_PLAY):
            if self.music_preview is not None:
                self._stop_music_preview()
            else:
                self._play_music_preview()
            return
        if _in(px, py, _MU_SAVE):
            self.save_sounds(); return
        if _in(px, py, _MU_LOOP):
            me.toggle_loop(); return
        if _in(px, py, _MU_CLOSE):
            self._leave_menu(); return
        # The right-hand edit pad (per-view button grid).
        self._music_pad_click(px, py, song)

    def _music_pad_click(self, px, py, song):
        me = self.musicedit
        if me is None:
            return
        # Find which pad button was hit (col 0/1, row 0..3).
        for row in range(4):
            for col in range(2):
                if _in(px, py, _mu_pad_rect(col, row)):
                    self._music_pad_action(row, col, song)
                    return

    def _music_pad_action(self, row, col, song):
        """Apply the edit-pad button at (row, col) for the active view (#50). The
        labels are wired in _draw_music_pad; this is their behavior."""
        me = self.musicedit
        if me is None:
            return
        if song:
            if row == 0:                       # SFX- / SFX+
                me.nudge_slot(-1 if col == 0 else 1)
            elif row == 3:                     # ADD / DEL
                me.add_slot() if col == 0 else me.del_slot()
            return
        # SFX view.
        if row == 0:                           # NOTE- / NOTE+
            me.nudge_pitch(-1 if col == 0 else 1)
        elif row == 1:                         # WAVE / VOL (both wrap: one tap cycles)
            me.cycle_wave(1) if col == 0 else me.cycle_vol(1)
        elif row == 2:                         # REST (col 0) -- col 1 unused
            if col == 0:
                me.toggle_rest()
        elif row == 3:                         # ADD / DEL
            me.add_step() if col == 0 else me.del_step()

    # -- block editor (#29 Part 2) -------------------------------------------
    #
    # The structured outline. The cursor moves over the flattened script (block rows
    # + the `+` insert points between them); A inserts (at an insert point) or steps
    # through / edits the selected block's slots. No dragging -- the decided
    # device-friendly interaction. The vocabulary/compiler is Part 1's blocks module;
    # the BlockEditor core (runtime/editors.py) owns the tree edits, this owns the UI.

    def _blk_reveal(self):
        """Keep the block cursor inside the visible outline window (scrolloff)."""
        be = self.blocks_ed
        if be is None:
            return
        if be.cur < self.blk_top:
            self.blk_top = be.cur
        elif be.cur > self.blk_top + _BLK_ROWS - 1:
            self.blk_top = be.cur - _BLK_ROWS + 1
        maxtop = len(be.rows) - _BLK_ROWS
        if maxtop < 0:
            maxtop = 0
        self.blk_top = max(0, min(maxtop, self.blk_top))

    def _blk_move_cursor(self, d):
        be = self.blocks_ed
        if be is None:
            return
        be.move(d)
        self.blk_slot = 0          # a new selection resets the slot highlight
        self._blk_reveal()

    def _blk_a(self):
        """The A/primary action in the outline: open the insert menu on a `+` row,
        or edit the highlighted slot of the selected block (a c-block with no slots
        falls back to opening the insert menu for its body's first gap)."""
        be = self.blocks_ed
        if be is None:
            return
        if be.at_insert():
            self._blk_open_categories()
            return
        b = be.selected_block()
        if b is None:
            return
        slots = be.slots(b)
        if slots:
            self._blk_edit_slot(b, slots[self.blk_slot % len(slots)])

    def _blk_next_slot(self):
        """Step the slot highlight to the selected block's next slot (wraps)."""
        be = self.blocks_ed
        if be is None:
            return
        slots = be.slots()
        if slots:
            self.blk_slot = (self.blk_slot + 1) % len(slots)

    # -- the insert menu (category -> block, plus slot pickers) --------------
    def _blk_open_categories(self):
        """Open the modal insert menu at the category level."""
        self.blk_menu = {"mode": "cat", "sel": 0, "top": 0,
                         "items": _blocks_mod.categories()}

    def _blk_open_blocks(self, category):
        ids = _blocks_mod.blocks_in_category(category)
        # Events are the lifecycle hats -- they live at the top level and the empty
        # program already has all three, so they're not insertable into a body.
        if category == _blocks_mod.CAT_EVENTS:
            ids = []
        # Variables: head the list with "+ new variable" so creating + naming one is
        # the first, obvious thing in the category (#29 Bug 2).
        if category == _blocks_mod.CAT_VARIABLES:
            ids = [_NEW_VAR_ITEM] + list(ids)
        # Lists: the same affordance -- "+ new list" heads the category (#48).
        if category == _blocks_mod.CAT_LISTS:
            ids = [_NEW_LIST_ITEM] + list(ids)
        self.blk_menu = {"mode": "blk", "cat": category, "sel": 0, "top": 0,
                         "items": ids}

    def _blk_menu_items(self):
        m = self.blk_menu
        return m["items"] if m else []

    def _blk_menu_label(self, i):
        """The display label for menu item i (a category name or a block label)."""
        m = self.blk_menu
        if not m:
            return ""
        item = m["items"][i]
        if item == _NEW_VAR_ITEM:
            return _NEW_VAR_LABEL
        if item == _NEW_LIST_ITEM:
            return _NEW_LIST_LABEL
        if item == _NUM_LITERAL_ITEM:
            return _NUM_LITERAL_LABEL
        if m["mode"] == "cat":
            return _CAT_LABEL.get(item, item).upper()
        if m["mode"] == "blk":
            d = _blocks_mod.block_def(item)
            return _blk_plain_label(d["label"]) if d else item
        if m["mode"] in ("dropdown", "variable", "list"):
            return str(item)
        return str(item)

    def _blk_menu_move(self, d):
        m = self.blk_menu
        if not m or not m["items"]:
            return
        m["sel"] = max(0, min(len(m["items"]) - 1, m["sel"] + d))
        if m["sel"] < m["top"]:
            m["top"] = m["sel"]
        elif m["sel"] > m["top"] + _BLK_MENU_ROWS - 1:
            m["top"] = m["sel"] - _BLK_MENU_ROWS + 1

    def _blk_menu_select(self):
        """Activate the highlighted menu item (drill in or commit)."""
        m = self.blk_menu
        if not m or not m["items"]:
            return
        item = m["items"][m["sel"]]
        if item == _NEW_VAR_ITEM:
            # "+ new variable": create one with a default name and immediately open
            # the on-screen-keyboard name prompt so the kid names it (#29 Bug 2).
            self._blk_new_variable()
            return
        if item == _NEW_LIST_ITEM:
            # "+ new list": same flow, for lists (#48).
            self._blk_new_list()
            return
        if item == _NUM_LITERAL_ITEM:
            # "type a number": close the chooser and open the number keypad on this
            # expr slot (the Scratch white oval -- a literal instead of a block).
            blk, name = m["block"], m["slot"]
            self.blk_menu = None
            self._blk_open_number_prompt(blk, name, None)
            return
        if m["mode"] == "cat":
            self._blk_open_blocks(item)
        elif m["mode"] == "blk":
            self._blk_insert_chosen(item)
        elif m["mode"] == "dropdown":
            self.blocks_ed.set_slot(m["slot"], item, m["block"])
            self.blk_menu = None
        elif m["mode"] in ("variable", "list"):
            self.blocks_ed.set_slot(m["slot"], item, m["block"])
            self.blk_menu = None
        elif m["mode"] == "expr":
            self._blk_insert_expr(item)

    def _blk_menu_back(self):
        """Back out one menu level (block list -> categories), or close the menu."""
        m = self.blk_menu
        if not m:
            return
        if m["mode"] == "blk":
            self._blk_open_categories()
        else:
            self.blk_menu = None

    def _blk_insert_chosen(self, type_id):
        """Insert the chosen block type at the cursor and close the menu."""
        be = self.blocks_ed
        be.insert_block(type_id)
        self.blk_slot = 0
        self.blk_menu = None
        self._blk_reveal()

    # -- slot editors --------------------------------------------------------
    def _blk_edit_slot(self, block, slot):
        """Open the right editor for a slot's type: a number slot (and an expr slot
        holding a literal) opens the on-screen number pad so the kid TYPES the value;
        an expr slot is the Scratch white oval -- type a number OR drop in a reporter
        block; variable + dropdown open a picker; text opens the keyboard."""
        be = self.blocks_ed
        t = slot["type"]
        name = slot["name"]
        if t == _blocks_mod.SLOT_DROPDOWN:
            # A dropdown opens its option list (16 colors is a lot to cycle through);
            # left/right still cycle it in place for a quick one-step tweak.
            self._blk_open_dropdown_picker(block, slot)
        elif t == _blocks_mod.SLOT_NUMBER:
            # Type the number directly (the +/- bump still lives on left/right).
            self._blk_open_number_prompt(block, name, slot)
        elif t == _blocks_mod.SLOT_TEXT:
            cur = str((block.get("p", {}) or {}).get(name, ""))
            self._blk_open_text_prompt(block, name, cur)
        elif t == _blocks_mod.SLOT_VARIABLE:
            self._blk_open_variable_picker(block, name)
        elif t == _blocks_mod.SLOT_LIST:
            self._blk_open_list_picker(block, name)
        elif t == _blocks_mod.SLOT_EXPR:
            # Scratch's editable oval: a literal opens the number pad (with an
            # "insert a block" escape hatch); a slot already holding a reporter block
            # re-opens the block chooser so the kid can swap/clear it.
            val = (block.get("p", {}) or {}).get(name)
            if _blocks_mod.is_literal_value(val):
                self._blk_open_number_prompt(block, name, slot)
            else:
                self._blk_open_expr_menu(block, name)

    def _blk_bump_number(self, block, name, d):
        # +/- nudge of a numeric slot (left/right in the outline). Works on a number
        # slot AND on an expr slot holding a numeric literal -- a quick tweak without
        # the keypad. A float keeps its fraction (e.g. 4.5 -> 5.5).
        be = self.blocks_ed
        cur = (block.get("p", {}) or {}).get(name, 0)
        if isinstance(cur, float):
            val = cur + d
        else:
            try:
                val = int(cur) + d
            except (TypeError, ValueError):
                val = d
        be.set_slot(name, val, block)

    # -- typed literal prompts (number / text), shared on-screen keypad -------
    def _blk_arm_prompt(self):
        """Neutralise the input edge that OPENED a prompt so its first frame can't
        carry the still-latched A/Enter/tap straight into commit/cancel (#29). Shared
        by every blk_kbd prompt (variable name, number, text)."""
        self._set_text_mode(True)            # ASCII keyboard for typing
        self.input.release_all()             # drop held buttons (host + device)
        try:
            self.input._pressed = set()
            self.input._released = set()
            self.input._last = set()         # device InputState edge snapshot
            self.input._prev = set()         # host InputState edge snapshot
        except AttributeError:
            pass
        # Seed the typed-key edge with the byte held RIGHT NOW so a held A/Enter byte
        # (last_key) isn't re-read as a fresh keystroke on the prompt's first frame.
        self._ekey_prev = getattr(self.input, "last_key", 0) or 0
        if self.pointer is not None:
            self.pointer.click = False       # a tap that opened the prompt != OK

    def _blk_open_number_prompt(self, block, name, slot):
        """Open the on-screen number pad to TYPE a literal into a number / expr slot
        (#29 blocking gap: you can now set `score to 0`, `x to 50`, compare `> 100`).
        `slot` is the catalog slot (or None when reopened from the expr chooser): an
        expr slot can ALSO hold a reporter, so the pad offers a "BLOCK" escape hatch
        for it. The pad starts EMPTY (default-on-OK is the slot's current value), so
        the kid types a fresh number."""
        cur = (block.get("p", {}) or {}).get(name)
        allow_block = self._blk_slot_is_expr(block, name, slot)
        self.blk_menu = None
        self.blk_kbd = {"kind": "num", "text": "", "cur": cur,
                        "block": block, "slot": name, "allow_block": allow_block,
                        "armed": False}
        self._blk_arm_prompt()

    def _blk_slot_is_expr(self, block, name, slot):
        """True if the named slot on `block` is an expr slot (so the number pad can
        offer 'BLOCK' to drop a reporter instead). Uses `slot` when given; else looks
        the slot up in the catalog by name."""
        if slot is not None:
            return slot["type"] == _blocks_mod.SLOT_EXPR
        d = _blocks_mod.block_def(block.get("t"))
        if d is None:
            return False
        for s in d["slots"]:
            if s["name"] == name:
                return s["type"] == _blocks_mod.SLOT_EXPR
        return False

    def _blk_open_text_prompt(self, block, name, cur):
        """Open the on-screen keyboard to TYPE a text literal into a text slot."""
        self.blk_menu = None
        self.blk_kbd = {"kind": "text", "text": str(cur or ""),
                        "block": block, "slot": name, "armed": False}
        self._blk_arm_prompt()

    def _blk_open_variable_picker(self, block, name):
        # The variable-slot picker: "+ new variable" first (so a kid can create +
        # name one right here and have the slot use it), then every declared variable.
        be = self.blocks_ed
        items = [_NEW_VAR_ITEM] + be.variables()
        self.blk_menu = {"mode": "variable", "sel": 0, "top": 0, "items": items,
                         "block": block, "slot": name}

    def _blk_open_list_picker(self, block, name):
        # The list-slot picker (#48): "+ new list" first, then every declared list.
        be = self.blocks_ed
        items = [_NEW_LIST_ITEM] + be.lists()
        self.blk_menu = {"mode": "list", "sel": 0, "top": 0, "items": items,
                         "block": block, "slot": name}

    # -- variable create + name (on-screen keyboard) -------------------------
    def _blk_new_variable(self):
        """Create a fresh variable (default name) and open the name-entry prompt so
        the kid types its name with the on-screen keyboard. Remembers the menu that
        was open (variable-slot picker) so that slot gets filled with the named var
        once the kid confirms (#29 Bug 2)."""
        be = self.blocks_ed
        if be is None:
            return
        m = self.blk_menu
        slot_target = None
        if m is not None and m.get("mode") == "variable":
            slot_target = (m.get("block"), m.get("slot"))
        name = be.new_var("var")
        self.blk_menu = None
        # An inline prompt: `text` is the live edit buffer (starts EMPTY so the kid
        # types a fresh name instead of appending to the default), `var` is the
        # just-created variable's CURRENT name -- confirm renames it old->typed, and a
        # blank/invalid entry keeps this default. `slot_target`, if set, is the
        # (block, slot) to fill with the final name. `kind` routes the shared keypad
        # (var / num / text); `armed` is the one-frame guard (#29): the prompt ignores
        # commit/cancel until its first input pass arms it, so the very input that
        # *selected* "+ new variable" (a held A / Enter, or the tap) can't carry into
        # the fresh prompt and instantly close it.
        self.blk_kbd = {"kind": "var", "text": "", "var": name,
                        "slot_target": slot_target, "armed": False}
        # Neutralise the triggering input so the prompt's first frame can't consume it
        # (#29): drop held buttons, wipe this frame's edges, and seed the typed-key
        # snapshot -- otherwise the still-latched A/Enter edge (or the held Enter byte
        # on the device) lands on the prompt as commit and it flashes shut.
        self._blk_arm_prompt()

    def _blk_new_list(self):
        """Create a fresh list (default name) and open the name-entry prompt (#48).
        Mirrors _blk_new_variable: if a list-slot picker was open, that slot gets
        filled with the named list on confirm; the `list` prompt kind renames it."""
        be = self.blocks_ed
        if be is None:
            return
        m = self.blk_menu
        slot_target = None
        if m is not None and m.get("mode") == "list":
            slot_target = (m.get("block"), m.get("slot"))
        name = be.new_list("list")
        self.blk_menu = None
        self.blk_kbd = {"kind": "list", "text": "", "var": name,
                        "slot_target": slot_target, "armed": False}
        self._blk_arm_prompt()

    def _blk_kbd_commit(self):
        """Confirm a prompt: a name prompt renames the var; a number prompt parses the
        typed text into a numeric literal and writes it to the slot; a text prompt
        stores the string. Falls back to the slot's current value on a blank entry."""
        be = self.blocks_ed
        k = self.blk_kbd
        if be is None or k is None:
            self.blk_kbd = None
            self._set_text_mode(False)
            return
        kind = k.get("kind", "var")
        if kind == "num":
            cur = k.get("cur")
            default = cur if _blocks_mod.is_literal_value(cur) and cur is not None else 0
            val = _blocks_mod.parse_number_literal(k["text"], default)
            be.set_slot(k["slot"], val, k["block"])
            self.blk_status = "= " + str(val)
        elif kind == "text":
            be.set_slot(k["slot"], k["text"], k["block"])
            self.blk_status = "text set"
        elif kind == "list":                   # "list": rename the freshly-created list
            old = k["var"]
            applied = be.rename_list(old, k["text"])
            final = applied if applied else old
            bt = k.get("slot_target")
            if bt is not None and bt[0] is not None:
                be.set_slot(bt[1], final, bt[0])
            self.blk_status = "list: " + final[:12]
        else:                                  # "var": rename the freshly-created var
            old = k["var"]
            applied = be.rename_var(old, k["text"])
            final = applied if applied else old   # blank/dup/invalid keeps the default
            bt = k.get("slot_target")
            if bt is not None and bt[0] is not None:
                be.set_slot(bt[1], final, bt[0])
            self.blk_status = "var: " + final[:12]
        self.blk_kbd = None
        self._set_text_mode(False)

    def _blk_kbd_cancel(self):
        """Cancel a prompt: a number/text prompt just discards the edit (the slot keeps
        its old value); a name prompt keeps the default-named variable/list (it's already
        declared and usable) and fills the slot with that default."""
        be = self.blocks_ed
        k = self.blk_kbd
        if be is not None and k is not None and k.get("kind", "var") in ("var", "list"):
            bt = k.get("slot_target")
            if bt is not None and bt[0] is not None:
                be.set_slot(bt[1], k["var"], bt[0])
        self.blk_kbd = None
        self._set_text_mode(False)

    def _blk_kbd_key(self, ch):
        """Apply one typed character to the prompt buffer: backspace deletes, Enter
        confirms, Esc cancels, and an allowed char appends. The allowed set depends on
        the prompt kind -- digits/'-'/'.' for a number, name-legal chars for a var,
        any printable for free text."""
        k = self.blk_kbd
        if k is None:
            return
        if ch in (8, 127):                    # backspace / delete
            k["text"] = k["text"][:-1]
            return
        if ch in (13, 10):                    # Enter -> confirm
            self._blk_kbd_commit()
            return
        if ch == 27:                          # Esc -> cancel
            self._blk_kbd_cancel()
            return
        if not (32 <= ch < 127):
            return
        c = chr(ch)
        if len(k["text"]) >= 16:              # cap so it always fits a row
            return
        kind = k.get("kind", "var")
        if kind == "num":
            # digits, a single leading '-', and at most one '.' (parse_number_literal
            # tolerates more, but filtering here keeps the on-screen buffer honest).
            if c.isdigit():
                k["text"] += c
            elif c == "-" and not k["text"]:
                k["text"] += c
            elif c == "." and "." not in k["text"]:
                k["text"] += c
        elif kind == "text":
            k["text"] += c                    # any printable char for a text literal
        else:                                  # "var": name-legal chars only
            if c.isalpha() or c.isdigit() or c in ("_", " ", "-"):
                k["text"] += c

    def _blk_open_dropdown_picker(self, block, slot):
        opts = _blocks_mod.slot_options(slot)
        self.blk_menu = {"mode": "dropdown", "sel": 0, "top": 0, "items": opts,
                         "block": block, "slot": slot["name"]}

    def _blk_open_expr_menu(self, block, name):
        """Open the expression chooser for an expr slot. Heads the list with "type a
        number" (the Scratch white oval -- a typed literal is the DEFAULT a kid wants),
        then every reporter block (operator / input / variable -- everything with an
        `expr` shape). Selecting "type a number" opens the number pad; selecting a
        block writes a fresh nested reporter into the slot."""
        ids = [_NUM_LITERAL_ITEM]
        for cat in _blocks_mod.categories():
            for bid in _blocks_mod.blocks_in_category(cat):
                if _blocks_mod.is_expr(bid):
                    ids.append(bid)
        self.blk_menu = {"mode": "expr", "sel": 0, "top": 0, "items": ids,
                         "block": block, "slot": name}

    def _blk_insert_expr(self, type_id):
        """Write a fresh expression block of `type_id` into the target expr slot."""
        m = self.blk_menu
        self.blocks_ed.set_slot(m["slot"], _blocks_mod.make_block(type_id), m["block"])
        self.blk_menu = None

    # -- save / graduate -----------------------------------------------------
    def save_blocks(self):
        """Compile-on-save the block program to blocks.json + main.py, surfacing
        SAVE_OK / a syntax problem like save_code does. Returns True on success.
        A non-SD/embedded cart just validates + applies in RAM."""
        be = self.blocks_ed
        if not (be and self.cart):
            return False
        if self.blk_protect:
            # DATA-LOSS GUARD (#29): this cart's main.py is hand-written code that a
            # block save would replace. Refuse and tell the kid -- their code stays.
            self.blk_status = "CART HAS CODE -- not saved"
            return False
        prog = be.program
        # Always compile-check first so the kid sees a problem before it persists.
        try:
            src = _blocks_mod.compile_blocks(prog)
        except Exception as exc:  # noqa: BLE001 -- BlockError on a corrupt tree
            self.blk_status = "BAD: " + _err_text(exc)
            return False
        ok, msg = self.carts_store.compile_check(src) if self.carts_store else (True, "")
        if not ok:
            self.blk_status = "SYNTAX " + msg
            return False
        if not (self.cart.get("path") and self.can_manage and self.carts_store):
            # nothing to persist (embedded / writes deferred): apply in RAM so RUN works
            self.cart["src"] = src
            self.cart["blocks"] = prog
            be.dirty = False
            self.blk_status = "SAVED"
            self.ach.note("code_save")
            return True
        try:
            status, smsg = self._with_sd(
                lambda: self.carts_store.save_blocks(self.cart, prog))
            if status != self.carts_store.SAVE_OK:
                self.blk_status = "SAVE BAD " + str(smsg)
                return False
            be.dirty = False
            self.blk_status = "SAVED"
            self.ach.note("code_save")          # "Code Wizard": a program saved (#21)
            self.cart_error = None
            return True
        except Exception as exc:  # noqa: BLE001
            self.blk_status = "SAVE FAILED"
            print("KidCode save blocks failed:", _err_text(exc))
            return False

    def graduate_to_code(self):
        """The one-way 'graduate to code' rung: compile the block program and open
        the generated main.py in the code editor, so a kid moves from blocks to text
        on the same cart. Saves first (so blocks.json + main.py are in lockstep), then
        switches to the code view on the freshly compiled source."""
        be = self.blocks_ed
        if not (be and self.cart):
            return
        if self.blk_protect:
            # Protected cart: don't compile the (empty) blocks over the kid's real
            # main.py. Just open the code editor on the EXISTING source -- "graduate"
            # here simply means "go edit the code you already have".
            self.editor = None
            self.blk_menu = None
            self.set_menu_view("code")
            return
        try:
            src = _blocks_mod.compile_blocks(be.program)
        except Exception as exc:  # noqa: BLE001
            self.blk_status = "BAD: " + _err_text(exc)
            return
        self.save_blocks()                       # persist (best-effort) before leaving
        self.cart["src"] = src                   # ensure the editor opens the latest
        self.editor = None                       # rebuild on the new source
        self.blk_menu = None
        self.set_menu_view("code")

    # -- block-editor input / pointer ----------------------------------------
    def _blocks_input(self):
        """Keyboard/button input for the outline + insert menu. Mirrors the other
        editors' edge-driven nav. The menu, when open, captures nav + A/B."""
        i = self.input
        if self.blk_kbd is not None:
            # The variable name-entry prompt owns input: type the name (one insert per
            # physical press, edge-detected like the code editor), Enter/A confirm, B
            # cancels. last_key carries the resolved ASCII byte (text mode is on).
            # One-frame guard (#29): the FIRST input pass after the prompt opens only
            # arms it -- never commits/cancels -- so the A/Enter/tap that *selected*
            # "+ new variable" (which can still be latched/held this frame) can't carry
            # in and instantly close the prompt before the kid types a name.
            if not self.blk_kbd.get("armed"):
                self.blk_kbd["armed"] = True
                self._ekey_prev = i.last_key   # don't read the trigger byte as a key
                return
            k = i.last_key
            if k and k != self._ekey_prev:
                self._blk_kbd_key(k)
            self._ekey_prev = k
            if i.pressed("a") or i.pressed("run"):
                self._blk_kbd_commit()
            elif i.pressed("b"):
                self._blk_kbd_cancel()
            return
        if self.blk_menu is not None:
            if i.pressed("up"):
                self._blk_menu_move(-1)
            if i.pressed("down"):
                self._blk_menu_move(1)
            if i.pressed("a") or i.pressed("run"):
                self._blk_menu_select()
            elif i.pressed("b"):
                self._blk_menu_back()
            return
        if i.pressed("up"):
            self._blk_move_cursor(-1)
        if i.pressed("down"):
            self._blk_move_cursor(1)
        if i.pressed("right"):
            self._blk_next_slot()                # step the highlighted slot
        if i.pressed("left"):
            # left on a number slot decrements it (a quick tweak without the menu)
            self._blk_left()
        if i.pressed("a"):
            self._blk_a()
        elif i.pressed("run"):
            self.save_blocks()
        elif i.pressed("b"):
            self._leave_menu()
        elif i.pressed("home"):
            self.go_home()

    def _blk_left(self):
        be = self.blocks_ed
        if be is None:
            return
        b = be.selected_block()
        slots = be.slots(b)
        if not slots:
            return
        slot = slots[self.blk_slot % len(slots)]
        if slot["type"] == _blocks_mod.SLOT_NUMBER:
            self._blk_bump_number(b, slot["name"], -1)
        elif slot["type"] == _blocks_mod.SLOT_DROPDOWN:
            be.cycle_dropdown(slot["name"], -1, b)
        elif slot["type"] == _blocks_mod.SLOT_EXPR:
            # quick -1 nudge on an expr slot holding a numeric literal (a block in the
            # slot is left alone -- you can't decrement an expression).
            cur = (b.get("p", {}) or {}).get(slot["name"])
            if _blocks_mod.is_literal_value(cur) and isinstance(cur, (int, float)) \
                    and not isinstance(cur, bool):
                self._blk_bump_number(b, slot["name"], -1)

    def _blocks_pointer(self, px, py, click):
        if not click:
            return
        if self.blk_kbd is not None:
            self._blk_kbd_click(px, py)
            return
        if self.blk_menu is not None:
            self._blk_menu_click(px, py)
            return
        be = self.blocks_ed
        if be is None:
            return
        # Action bar
        if _in(px, py, _BLK_ADD):
            self._blk_open_categories(); return
        if _in(px, py, _BLK_DEL):
            be.delete(); self.blk_slot = 0; self._blk_reveal(); return
        if _in(px, py, _BLK_UP):
            be.move_block(-1); self._blk_reveal(); return
        if _in(px, py, _BLK_DN):
            be.move_block(1); self._blk_reveal(); return
        if _in(px, py, _BLK_SAVE):
            self.save_blocks(); return
        if _in(px, py, _BLK_CODE):
            self.graduate_to_code(); return
        if _in(px, py, _BLK_CLOSE):
            self._leave_menu(); return
        # Tap a row in the outline: select it (and on a block, advance the slot
        # highlight / open the insert menu on a `+` row -- a tap == the A action).
        if _in(px, py, _BLK_AREA):
            ridx = self.blk_top + (py - _BLK_Y0) // _BLK_ROW_H
            if 0 <= ridx < len(be.rows):
                if ridx == be.cur:
                    self._blk_a()                # a second tap acts (insert / edit)
                else:
                    be.cur = ridx
                    self.blk_slot = 0
                    self._blk_reveal()

    def _blk_menu_click(self, px, py):
        m = self.blk_menu
        if not m:
            return
        mx, my, mw, mh = _BLK_MENU
        if not _in(px, py, _BLK_MENU):
            self.blk_menu = None                 # tap outside dismisses
            return
        ridx = m["top"] + (py - (my + 16)) // _BLK_MENU_ROW_H
        if 0 <= ridx < len(m["items"]):
            m["sel"] = ridx
            self._blk_menu_select()

    def _blk_kbd_click(self, px, py):
        """Touch handling for the entry prompts. One-frame guard (#29): the tap that
        OPENED the prompt must not carry into this first pass and immediately commit."""
        if self.blk_kbd is not None and not self.blk_kbd.get("armed"):
            self.blk_kbd["armed"] = True
            return
        if self.blk_kbd is not None and self.blk_kbd.get("kind") == "num":
            self._blk_num_click(px, py)
            return
        # var / text prompt: DEL backspaces, OK confirms, X cancels (typing is the
        # on-screen / T-Deck keyboard).
        if _in(px, py, _BLK_KBD_DEL):
            self._blk_kbd_key(8); return
        if _in(px, py, _BLK_KBD_OK):
            self._blk_kbd_commit(); return
        if _in(px, py, _BLK_KBD_X):
            self._blk_kbd_cancel(); return
        # taps inside the panel are ignored (no dismiss-on-tap-outside: a stray tap
        # shouldn't discard a half-typed name).

    def _blk_num_click(self, px, py):
        """Touch handling for the number pad: the on-screen digit grid types a literal
        (so it works touch-only / without sym keys), DEL backspaces, OK/X confirm/cancel,
        and BLOCK (expr slots) swaps in a reporter block instead."""
        k = self.blk_kbd
        # the digit grid
        for idx in range(len(_BLK_NUM_KEYS)):
            r = idx // _BLK_NUM_BPR
            c = idx % _BLK_NUM_BPR
            rx = _BLK_NUM_GX + c * _BLK_NUM_BW
            ry = _BLK_NUM_GY + r * _BLK_NUM_BH
            if _in(px, py, (rx, ry, _BLK_NUM_BW - 3, _BLK_NUM_BH - 3)):
                self._blk_kbd_key(ord(_BLK_NUM_KEYS[idx]))
                return
        if _in(px, py, _BLK_NUM_DEL):
            self._blk_kbd_key(8); return
        if k is not None and k.get("allow_block") and _in(px, py, _BLK_NUM_BLOCK):
            self._blk_num_to_block(); return
        if _in(px, py, _BLK_NUM_OK):
            self._blk_kbd_commit(); return
        if _in(px, py, _BLK_NUM_X):
            self._blk_kbd_cancel(); return

    def _blk_num_to_block(self):
        """From the number pad, switch to dropping a reporter BLOCK into the slot
        (the Scratch white-oval -> drop-a-block move). Discards the typed number and
        opens the expr chooser on the same slot."""
        k = self.blk_kbd
        if k is None:
            return
        block, name = k["block"], k["slot"]
        self.blk_kbd = None
        self._set_text_mode(False)
        self._blk_open_expr_menu(block, name)

    # -- frame + drawing -----------------------------------------------------

    def _reset_canvas_state(self):
        # Reset the canvas's TIC-80 draw state (camera/clip/pal/palt, #11) if the
        # backend supports it. Guarded so a backend without draw state (a test stub,
        # or a recording canvas) is a no-op.
        rs = getattr(self.canvas, "reset_state", None)
        if rs is not None:
            rs()

    # -- two-domain composite + viewport coords (#39) ------------------------

    def _viewport(self):
        """The composited game viewport as (ox, oy, scale) -- the top-left of the
        320x240 game canvas inside the system canvas, and its integer scale. (0, 0,
        1) when the two canvases are the same object (degradation)."""
        gc = self.canvas
        sc = self.sys_canvas
        if sc is gc:
            return (0, 0, 1)
        scale = min(sc.w // gc.w, sc.h // gc.h)
        if scale < 1:
            scale = 1
        ox = (sc.w - gc.w * scale) // 2
        oy = (sc.h - gc.h * scale) // 2
        return (ox, oy, scale)

    def _game_xy(self, px, py):
        """Map a SYSTEM-canvas point (where the pointer lives) into GAME-canvas
        coords, so a running cart / the editors (drawn in the 320x240 viewport) hit-
        test correctly. Identity in the degradation case."""
        ox, oy, scale = self._viewport()
        return ((px - ox) // scale, (py - oy) // scale)

    def _composite_game(self):
        """Blit the fixed 320x240 GAME canvas into the SYSTEM canvas as a
        fixed-aspect, integer-scaled, centered viewport, filling the letterbox with
        a solid bezel color. A no-op when the two canvases are the same object (the
        degradation case: 320x240 system canvas == game canvas, pixel-identical to
        today). Index-only (host == device): reads game indices, writes them scaled
        into the system buffer, so no palette resolve is needed."""
        gc = self.canvas
        sc = self.sys_canvas
        if sc is gc:
            return
        ox, oy, scale = self._viewport()
        sc.cls(_VIEWPORT_BEZEL)                     # letterbox fill
        gbuf = getattr(gc, "buf", None)
        sbuf = getattr(sc, "buf", None)
        if gbuf is None or sbuf is None:
            # A recording system canvas (the web CommandCanvas) has no framebuffer to
            # copy into -- blit the whole game frame as one scaled sprite so the draw
            # stream carries the viewport. The game canvas must expose its pixels.
            self._composite_via_spr(gc, sc, gbuf, ox, oy, scale)
            return
        gw = gc.w
        sw = sc.w
        sh = sc.h
        vw = gw * scale
        # The viewport always fits a system canvas >= the game (the supported case),
        # so take the fast row-replication path. A degenerate smaller-than-game system
        # canvas (negative offset / overflow) falls to a clipped per-pixel path that
        # can never resize the bytearray.
        fits = ox >= 0 and oy >= 0 and ox + vw <= sw and oy + gc.h * scale <= sh
        if fits:
            for gy in range(gc.h):
                grow = gy * gw
                for s in range(scale):
                    base = (oy + gy * scale + s) * sw + ox
                    if scale == 1:
                        sbuf[base:base + gw] = gbuf[grow:grow + gw]
                    else:
                        out = base
                        for gx in range(gw):
                            sbuf[out:out + scale] = bytes((gbuf[grow + gx],)) * scale
                            out += scale
            return
        for gy in range(gc.h):                      # clipped fallback (defensive)
            grow = gy * gw
            for s in range(scale):
                dy = oy + gy * scale + s
                if dy < 0 or dy >= sh:
                    continue
                dx0 = ox if ox > 0 else 0
                dx1 = min(sw, ox + vw)
                if dx1 <= dx0:
                    continue
                base = dy * sw
                for dx in range(dx0, dx1):
                    sbuf[base + dx] = gbuf[grow + (dx - ox) // scale]

    def _composite_via_spr(self, gc, sc, gbuf, ox, oy, scale):
        """Composite by blitting the game frame as ONE scaled sprite -- the path for a
        recording system canvas (the web CommandCanvas) that has no framebuffer to
        copy into. Records a single spr command per frame carrying the game pixels."""
        if gbuf is None:
            return
        img = _Blit(gc.w, gc.h, list(gbuf), -1)     # opaque (no transparent index)
        sc.spr(img, ox, oy, scale)

    # -- redraw-on-change (#44 step 1) ---------------------------------------

    def mark_dirty(self):
        """Request a redraw on the next frame(). Called whenever a visible change
        could have happened (input that mutates state, scrolls, edits, screen/menu
        switches, selection moves). Cheap + idempotent -- the actual draw is
        coalesced to one in frame()."""
        self._dirty = True

    def _ptr_state(self):
        """The pointer state that affects what's drawn: position, visibility, the
        held/click flags. A change here (cursor moved, auto-hid, tapped, drag) means
        the picture differs, so frame() must repaint. None when there's no pointer."""
        p = self.pointer
        if p is None:
            return None
        return (p.x, p.y, bool(p.visible), bool(p.down), bool(p.click))

    def _animating(self, dt):
        """True when SOMETHING on screen changes every frame on its own, so the UI
        must keep redrawing even without input:
          - a running cart (games animate -> unchanged full-redraw behaviour),
          - a live wallpaper on the home/settings backdrop (its _update advances it),
          - the achievement toast / Konami confetti / Easter-egg popup while active.
        A static launcher/editor/menu with a still wallpaper hits none of these."""
        # A running cart on the desktop draws every frame (unless it crashed, when the
        # error panel is static).
        if self.screen == "desktop" and self.cart_error is None and (
                self._update is not None or self._draw is not None):
            return True
        # A music-editor preview must keep ticking the mixer + redrawing the PLAY/STOP
        # button (and clearing the flag when the effect ends) without input (#50).
        if self.screen == "menu" and self.menu_view == "music" \
                and self.music_preview is not None:
            return True
        # A live wallpaper animates the home/settings backdrop.
        if self.screen in ("launcher", "settings") and self._wp_live \
                and self._wp_update is not None and self._wp_draw is not None and dt > 0:
            return True
        # A firmware install (#53) advances a chunk per frame; "done" runs a short
        # reboot countdown; "checking"/"downloading" (Phase 3) step the online flow.
        # All must keep redrawing so progress animates and the work proceeds without input.
        if self.screen == "update" and self._upd_phase in (
                "install", "done", "checking", "downloading"):
            return True
        # Transient overlays redraw while they're up.
        if self._confetti_until and _ticks_diff(self._confetti_until, _ticks_ms()) > 0:
            return True
        if self._egg_active():
            return True
        if self.ach.toast_active():
            return True
        return False

    def _needs_redraw(self, dt):
        """Decide whether frame() must repaint+flush this frame. True when something
        marked the UI dirty, an animation source is live, or the pointer state the
        last frame drew has changed (cursor move/hide, tap, drag)."""
        if self._dirty:
            return True
        if self._animating(dt):
            return True
        if self._ptr_state() != self._last_ptr:
            return True
        return False

    def frame(self, dt):
        if dt > 0:
            inst = 1.0 / dt
            # EMA so the readout reflects sustained rate, not single-frame jitter.
            self._fps = inst if self._fps <= 0 else self._fps + (inst - self._fps) * 0.15
        # Redraw-on-change (#44): a static UI screen (no animation, no pointer change,
        # nothing marked dirty) is skipped entirely -- no draw, no flush. The panel /
        # host window simply retains the last frame, so an idle UI costs ~0 and the
        # device saves the SPI flush + power. A running cart / live wallpaper / active
        # overlay always reports animating, so it redraws every frame as before.
        if not self._needs_redraw(dt):
            return
        # Perf HUD (#43/#44): mark the start of this frame's draw work. Cheap (one
        # ticks call); only meaningful for a frame we actually paint, so it's after
        # the redraw gate. _flush_ms is filled around comp.flush() below; _draw_ms
        # is the rest (total span - flush). Both EMA-smoothed at frame end. Also
        # fires when perf_capture is set (device diag sampling) -- not just the HUD.
        _perf = self.perf_hud or self.perf_capture
        _frame_t0 = _ticks_ms() if _perf else 0
        _upd = 0          # cart _update(dt) ms (game LOGIC); 0 off the cart path
        _cart = 0         # cart _draw() ms (RENDERING)
        _audio = 0        # audio.tick(dt) ms (mixer feed) -- split out so it doesn't hide in render
        if self.screen == "launcher":
            self._draw_desktop_home(dt)
        elif self.screen == "settings":
            self._draw_settings(dt)
        elif self.screen == "update":
            self._pump_update(dt)          # advance the install / reboot countdown
            self._draw_update(dt)
        elif self.screen == "desktop":
            if self.cart_error is None:
                # Resolve this frame's keyboard edge for the cart's key()/keyp():
                # last_key is the byte held this frame (0 when nothing is down);
                # keyp fires only on the 0->key transition. Done here (not in
                # InputState) so it's independent of whether the backend sets
                # last_key before or after begin_frame().
                k = self.input.last_key
                self.input.cart_key = k
                self.input.cart_keyp = k if (k and k != self._cart_key_prev) else 0
                self._cart_key_prev = k
                try:
                    _ts = _ticks_ms() if _perf else 0
                    if self._update:
                        self._update(dt)
                    _tm = _ticks_ms() if _perf else 0
                    if self._draw:
                        self._draw()
                    _td = _ticks_ms() if _perf else 0
                    if self.audio is not None:
                        self.audio.tick(dt)      # advance/feed playback (#16)
                    if _perf:
                        _upd = _ticks_diff(_tm, _ts)    # cart _update -> game LOGIC
                        _cart = _ticks_diff(_td, _tm)   # cart _draw -> RENDERING
                        _audio = _ticks_diff(_ticks_ms(), _td)  # audio.tick (mixer feed)
                except Exception as exc:  # noqa: BLE001
                    # A cart that raises mid-frame must NOT escape the loop (the
                    # device would hang silently). Capture it, stop running the
                    # broken cart, and fall through to paint the error panel; the
                    # desktop buttons stay so the kid can EDIT/CODE the fix.
                    self.cart_error = _err_text(exc)
                    self.crash_line = _exc_cart_line(exc)   # mark the line on EDIT (#24)
                    self._update = None
                    self._draw = None
                    # Print the _err_text-guarded string, never the raw `exc`: a
                    # cart exception whose __str__ itself raises would otherwise
                    # escape frame() here -> the silent device hang the panel
                    # exists to prevent.
                    print("KidCode frame error:", self.cart_error)
            # Cart text input (#38/#42): apply the keyboard mode the cart's _update may
            # have just requested via textmode(), so the NEXT keyboard poll yields the
            # right bytes (clean ASCII for typing, raw/game for hold-to-move). One-frame
            # latency; no-op on the host. Done every running-cart frame so a mid-cart
            # toggle (e.g. wifi entering/leaving its password screen) takes effect.
            self._sync_cart_text_mode()
            # Clear any cart-set camera/clip/pal/palt (#11) before the console paints
            # its own UI overlays, so they're never offset/clipped/recoloured.
            self._reset_canvas_state()
            if self.cart_error is not None:
                self._draw_error_panel()
            self._draw_status_strip("desktop")     # unified top bar (tool switcher)
        elif self.menu_view == "code":
            self._draw_code()              # full-screen editor (covers the cart)
        elif self.menu_view == "blocks":
            self._draw_blocks()            # full-screen structured outline (#29)
        elif self.menu_view == "music":
            # Music/sound editor (#50): full-screen tracker over the cart's bank. The
            # frozen cart isn't drawn (the editor covers it); the live AudioEngine is
            # ticked here so a PLAY preview keeps sounding, then auto-clears the preview
            # flag once the (non-looping) effect finishes so PLAY/STOP stays honest.
            self._reset_canvas_state()
            if self.audio is not None:
                self.audio.tick(dt)
            if self.music_preview is not None and not self._music_preview_active():
                self.music_preview = None
            self._draw_music()
        elif self.menu_view == "theme":
            # EDIT ICONS (Stage 2): the PAINT editor over the system icon sheet. Opened
            # from Settings, NOT a running cart, so there's no cart backdrop to draw --
            # just clear the canvas and reuse the cart PAINT renderer (over icon_sheet).
            self.canvas.cls(NAMES["black"])
            self._reset_canvas_state()
            self._draw_paint()
        else:  # cards / paint / map: a panel over the frozen cart
            try:
                if self._draw:
                    self._draw()
            except Exception:
                pass
            # Clear cart-set camera/clip/pal/palt (#11) so the editor panel over the
            # frozen cart frame draws unaffected.
            self._reset_canvas_state()
            if self.menu_view == "paint":
                self._draw_paint()
            elif self.menu_view == "map":
                self._draw_map()
            else:
                try:
                    self._draw_cards()
                except Exception as exc:  # noqa: BLE001
                    # A malformed card (e.g. a bad tiles/choices entry) must NOT
                    # escape the frame loop -- the device would hang silently with
                    # no error surface. Fall back to a readable panel + CLOSE.
                    self.cart_error = _err_text(exc)
                    print("KidCode cards error:", exc)
                    self._draw_error_panel()
                    self._icon_btn("close", "", _CLOSE_BTN, NAMES["red"])
        if self.show_fps and self.screen == "desktop":
            self._draw_fps()
            if self.perf_hud:
                self._draw_perf_hud()      # frame-time breakdown above the FPS chip
        # Two-domain seam (#39): the "desktop" (running cart) + "menu" (editors) drew
        # on the fixed 320x240 GAME canvas above; composite it into the SYSTEM canvas
        # as a centered, integer-scaled viewport. The "launcher"/"settings" screens
        # already drew straight on the system canvas, so they skip the composite. A
        # no-op when the two canvases are the same object (the 320x240 degradation
        # case -> pixel-identical to today).
        if self.screen in ("desktop", "menu"):
            self._composite_game()
        # Achievements + Easter eggs (#21) overlay on TOP of every screen, so an
        # unlock celebration / secret popup is always visible and never disturbs the
        # screen underneath (it's drawn last, then expires on its own). These are
        # SYSTEM chrome -> drawn on the system canvas (over the composited viewport).
        if self._confetti_until and _ticks_diff(self._confetti_until, _ticks_ms()) > 0:
            self._draw_confetti()
        if self.show_achievements:
            self._draw_achievements()
        if self._egg_active():
            self._draw_egg()
        if self.ach.toast_active():
            self._draw_toast()
        # System menu dropdown + About modal (#52): drawn on TOP of every screen (after
        # the cart/editor composite + the egg/achievement overlays) so the dropdown
        # sits over whatever is underneath, then the cursor goes above even that.
        if self.sysmenu.open:
            self._draw_sysmenu()
        if self._about:
            self._draw_about()
        self._draw_cursor()
        # Perf HUD (#43/#44): time the panel DMA flush in isolation, then back out
        # the draw span (everything before it this frame). On the host _NullComp the
        # flush is a near-zero no-op (no real panel), so flush reads ~0 and draw ~=
        # total -- the real flush-vs-draw split only shows on device. The flush call
        # is unconditional + identical either way; the timing is two cheap ticks
        # calls gated on perf_hud OR perf_capture (device diag sampling), so the
        # render path itself is unchanged.
        if _perf:
            _flush_t0 = _ticks_ms()
            self.comp.flush()
            _flush = _ticks_diff(_ticks_ms(), _flush_t0)
            _total = _ticks_diff(_ticks_ms(), _frame_t0)
            _draw = _total - _flush
            if _draw < 0:
                _draw = 0
            self._flush_ms = float(_flush) if self._flush_ms <= 0 \
                else self._flush_ms + (_flush - self._flush_ms) * 0.15
            self._draw_ms = float(_draw) if self._draw_ms <= 0 \
                else self._draw_ms + (_draw - self._draw_ms) * 0.15
            # DRAWBRK split: cart _update (logic) / cart _draw (render) / audio.tick /
            # console chrome (remainder = dock + cursor + overlays).
            _chrome = _draw - _upd - _cart - _audio
            if _chrome < 0:
                _chrome = 0
            self._upd_ms = float(_upd) if self._upd_ms <= 0 \
                else self._upd_ms + (_upd - self._upd_ms) * 0.15
            self._cart_ms = float(_cart) if self._cart_ms <= 0 \
                else self._cart_ms + (_cart - self._cart_ms) * 0.15
            self._audio_ms = float(_audio) if self._audio_ms <= 0 \
                else self._audio_ms + (_audio - self._audio_ms) * 0.15
            self._chrome_ms = float(_chrome) if self._chrome_ms <= 0 \
                else self._chrome_ms + (_chrome - self._chrome_ms) * 0.15
        else:
            self.comp.flush()
        # We painted this frame: clear the dirty flag and snapshot the pointer state
        # we just drew, so the NEXT frame only repaints if something changes again.
        self._dirty = False
        self._last_ptr = self._ptr_state()
        self._frames_drawn += 1

    # -- desktop shell drawing (#28) -----------------------------------------

    def _draw_desktop_home(self, dt):
        """The home desktop: wallpaper backdrop -> cart icon grid -> top status
        strip. The wallpaper is drawn first and the rest layer over it, exactly the
        Picotron model (wallpaper shows through the chrome). All on the SYSTEM canvas,
        reflowed to its size + font scale (#39).

        The bottom in-cart tool dock is NOT drawn here (#46): on the launcher the
        code/draw/map/run slots have no cart to act on, so the dock was a dead row.
        It returns the moment a cart is open (the in-cart top-bar buttons / Settings'
        dock). Settings stays reachable via the gear button in the status strip; the
        cart grid reclaims the freed bottom band (Layout.grid_bottom)."""
        self._draw_wallpaper(dt)
        cv = self.sys_canvas
        lay = self.layout
        self.launcher.draw(cv, self._icon_sheet_for)
        # page chevrons when more than one page of carts
        if self.launcher.max_page() > 0:
            if self.launcher.page > 0:
                px, py = lay.page_prev[0], lay.page_prev[1]
                cv.print("<", px + 3, py + 8, NAMES["white"], 2)
            if self.launcher.page < self.launcher.max_page():
                px, py = lay.page_next[0], lay.page_next[1]
                cv.print(">", px + 3, py + 8, NAMES["white"], 2)
        self._draw_status_strip("home")

    def _draw_status_strip(self, where):
        """The unified 18px top bar (Stage 1), drawn on BOTH the launcher/Settings and
        the running-cart screen. A black backing band (with a thin shelf edge line
        below) full of 16x16 IconSheet sprites instead of the old labeled glyph
        buttons. Layers:

          * Right cluster (always): the clock text, then wifi, batt, gear icons,
            right-aligned (wifi/batt keep their placeholder green for now).
          * Left cluster (launcher home / Settings): NEW / DUP / DEL icons when
            can_manage; the selected cart's name fills the gap before the clock.
          * Left cluster (running cart, where == "desktop"): the tool switcher --
            HOME, then EDIT (or CODE for a no-edit cart), PAINT, MAP, BLOCKS.

        The launcher/Settings bar draws on the SYSTEM canvas (reflowed by Layout #39);
        the running-cart bar draws on the GAME canvas (the fixed 320x240 viewport), so
        it uses the fixed _BAR_* / button-rect constants. Translucency isn't available
        on the indexed canvas, so the dark band is a deliberate shelf over the
        wallpaper (whose art is pushed below it, see _draw_wallpaper #46)."""
        if where == "desktop":
            self._draw_top_bar_cart()
            return
        cv = self.sys_canvas
        lay = self.layout
        cv.rect(0, 0, cv.w, lay.status_h, NAMES["black"])
        cv.rect(0, lay.status_h - 1, cv.w, 1, NAMES["dark_grey"])   # shelf edge line
        # Right cluster: clock + wifi/batt (Settings now lives in the ≡ menu, #52).
        cv.print(self._clock_text(), lay.clock_x, 3, NAMES["light_grey"], 1)
        self._icon("wifi", lay.wifi_btn[0], lay.wifi_btn[1], cv)
        self._icon("batt", lay.batt_btn[0], lay.batt_btn[1], cv)
        # ≡ system-menu toggle (leftmost, always) -- the launcher's Settings entry now,
        # a _glyph bitmap like the in-cart bar so an older saved theme can't blank it.
        self._glyph("menu", lay.sysmenu_btn, NAMES["white"], cv)
        # Left cluster: management icons (when writable) + the selected cart's name.
        if where == "home":
            if self.can_manage:
                self._icon("new", lay.new_btn[0], lay.new_btn[1], cv)
                self._icon("dup", lay.dup_btn[0], lay.dup_btn[1], cv)
                self._icon("del", lay.del_btn[0], lay.del_btn[1], cv)
            sel = self.launcher.selected()
            if sel is not None:
                name = sel["title"]
                if len(name) > lay.status_name_maxc:
                    name = name[:lay.status_name_maxc]
                cv.print(name, lay.status_name_x, 3, NAMES["white"], 1)

    def _draw_top_bar_cart(self):
        """The running-cart half of the unified top bar (where == "desktop"). Drawn on
        the GAME canvas with the fixed 320x240 rects: a tool switcher on the left
        (HOME / EDIT|CODE / PAINT / MAP / BLOCKS) + the right cluster (clock + wifi /
        batt / gear). Same icon vocabulary as the launcher bar so both read alike.

        CACHED (#43): a running cart redraws every frame, but this bar is almost entirely
        static -- the clock changes ~once/min, the icons/menu never mid-play -- so
        re-rendering ~9 16x16 sprites + a glyph + text each frame was ~6ms of wasted draw
        (the `chrome=` term in DRAWBRK). Instead the bar's pixels are rendered ONCE into an
        offscreen _STATUS_H-tall strip (a new_layer, the #54 offscreen primitive) keyed by
        the state that changes its picture, and each frame we just blit_strip the cached
        strip onto the canvas (one flat copy, ~0.5ms). When the key changes (cart switch,
        clock tick, theme edit, font/size change) the strip is re-rendered, then reused.
        The strip is purely the DRAW; hit-testing still uses the independent _*_BTN rects,
        so caching can't desync taps."""
        cv = self.canvas
        key = self._cart_bar_key()
        strip = self._cart_bar_strip
        if strip is None or strip.w != cv.w or self._cart_bar_key_cur != key:
            # (Re)build the cached strip. new_layer gives a same-type/-palette canvas the
            # bar body draws into at the SAME coords (the bar lives at y in [0, _STATUS_H),
            # which maps 1:1 onto the strip's rows), so the cached pixels are byte-identical
            # to drawing straight onto cv. Reuse the buffer across re-renders when the size
            # is unchanged; only allocate a fresh layer on first build / a resize.
            if strip is None or strip.w != cv.w:
                strip = cv.new_layer(cv.w, _STATUS_H)
                self._cart_bar_strip = strip
            self._render_cart_bar(strip, key)
            self._cart_bar_key_cur = key
        cv.blit_strip(strip, 0, 0)

    def _cart_bar_key(self):
        """The cache key for the running-cart top bar: every piece of state that changes
        the bar's PIXELS. A different key forces a strip re-render; an unchanged key reuses
        the cached strip. Includes the clock text (ticks ~once/min), whether the cart has
        an edit schema (EDIT vs CODE icon), the icon theme identity + the glyph font scale
        (a theme edit / resize must repaint), and a generation counter the explicit
        invalidators bump (set_icon_sheet, etc.). wifi/batt are static placeholder art
        today; if they gain live state, fold it in here."""
        has_edit = bool(self.cart.get("edit")) if self.cart else False
        return (self._clock_text(), has_edit, id(self.icon_sheet),
                getattr(self.canvas, "font_scale", 1), self._bar_cache_gen)

    def _render_cart_bar(self, cv, key):
        """Render the running-cart bar's pixels onto `cv` (the offscreen strip, or any
        canvas) at the fixed 320x240 bar coords. Factored out of _draw_top_bar_cart so the
        SAME drawing serves both the cache build and the (test/fallback) direct path, which
        is what makes the cached strip pixel-identical to a direct render. `key` carries the
        already-computed has_edit (index 1) so the icon choice can't drift from the key."""
        has_edit = key[1]
        cv.rect(0, 0, cv.w, _STATUS_H, NAMES["black"])
        cv.rect(0, _STATUS_H - 1, cv.w, 1, NAMES["dark_grey"])      # shelf edge line
        # Left cluster: the TIC-80 one-tap tool switcher. Carts with a Make-it-mine
        # schema open the cards menu (pencil = EDIT); the rest jump straight to code
        # (the < > glyph = CODE). cart may be None defensively (error panel, no cart).
        # ≡ system-menu toggle (#52), leftmost. A _glyph bitmap (not a themeable
        # IconSheet slot) so it never goes blank on a device with an older saved theme.
        self._glyph("menu", _SYSMENU_BTN, NAMES["white"], cv)
        self._icon("home", _HOME_BTN[0], _HOME_BTN[1], cv)
        self._icon("edit" if has_edit else "code", _MENU_BTN[0], _MENU_BTN[1], cv)
        self._icon("paint", _PAINT_BTN[0], _PAINT_BTN[1], cv)
        self._icon("map", _MAP_BTN[0], _MAP_BTN[1], cv)
        self._icon("blocks", _BLOCKS_BTN[0], _BLOCKS_BTN[1], cv)
        self._icon("music", _MUSIC_BTN[0], _MUSIC_BTN[1], cv)
        # Right cluster: clock + wifi/batt (Settings now lives in the ≡ menu, not a gear).
        cv.print(self._clock_text(), _BAR_CLOCK[0], 3, NAMES["light_grey"], 1)
        self._icon("wifi", _BAR_WIFI[0], _BAR_WIFI[1], cv)
        self._icon("batt", _BAR_BATT[0], _BAR_BATT[1], cv)

    def _clock_text(self):
        """A wall-clock HH:MM from time.localtime when available, else a mm:ss
        uptime so the strip always shows a live clock (host == device)."""
        try:
            lt = time.localtime()
            return "%02d:%02d" % (lt[3], lt[4])
        except Exception:  # noqa: BLE001
            secs = _ticks_diff(_ticks_ms(), 0) // 1000
            return "%02d:%02d" % ((secs // 60) % 100, secs % 60)

    def _mini_btn(self, label, rect, fill, cv=None):
        x, y, w, h = rect
        if cv is None:
            cv = self.canvas
        cv.rect(x, y, w, h, fill)
        cv.print(label, x + 2, y + 2, NAMES["black"], 1)

    def _draw_dock(self, where):
        """The persistent bottom dock: home / code / draw / map / run / settings.
        The active slot (home on the desktop, settings in Settings) is highlighted;
        the music slot is greyed (its editor is #16, not yet here). Tool slots that
        need an open cart read dimmed on the home desktop."""
        cv = self.sys_canvas
        lay = self.layout
        fw = lay.font_w                              # on-screen char-cell width (8*fs)
        gh = lay.status_gh                           # glyph box (12*fs)
        cv.rect(0, lay.dock_y, cv.w, cv.h - lay.dock_y, NAMES["dark_grey"])
        cv.rect(0, lay.dock_y, cv.w, 1, NAMES["black"])
        for k in range(len(_DOCK_SLOTS)):
            slot = _DOCK_SLOTS[k]
            x, y, w, h = self._dock_slot_rect(k)
            is_active = (slot == "home" and where == "home") or \
                        (slot == "settings" and where == "settings")
            # On the home desktop the editor tools have no cart -> dim them.
            enabled = slot in ("home", "settings", "run") or self.cart is not None
            if is_active:
                cv.rect(x, y, w, h, NAMES["indigo"])
            gc = NAMES["white"] if enabled else NAMES["dark_blue"]
            self._glyph(_DOCK_GLYPH[slot], (x, y, w, gh), gc, cv)
            label = _DOCK_LABEL[slot]
            cv.print(label, x + (w - len(label) * fw) // 2, y + gh, gc, 1)

    def _draw_settings(self, dt):
        """The Settings app (#28): wallpaper picker + font-size picker (both
        FUNCTIONAL, persist) plus the mocked rows, over the live wallpaper so the
        backdrop preview is honest. On the SYSTEM canvas; panel + title-row controls
        reflow with the layout/font scale (#39)."""
        self._draw_wallpaper(dt)
        cv = self.sys_canvas
        lay = self.layout
        fs = lay.fs
        px, py, pw, ph = lay.settings_panel
        cv.rect(px, py, pw, ph, NAMES["dark_purple"])
        cv.rectb(px, py, pw, ph, NAMES["pink"])
        self._glyph("gear", (px + 6, py + 2, 14 * fs, 14 * fs), NAMES["yellow"], cv)
        cv.print("SETTINGS", px + 24, py + 4, NAMES["white"], 2)
        # Achievements view button (#21): a trophy badge with the unlocked count.
        sa = lay.set_ach
        cv.rect(sa[0], sa[1], sa[2], sa[3], NAMES["indigo"])
        self._glyph("trophy", (sa[0] - 2, sa[1], 14 * fs, 14 * fs), NAMES["yellow"], cv)
        cv.print(str(self.ach.count()), sa[0] + 13 * fs, sa[1] + 4, NAMES["white"], 1)
        self._mini_btn("X", lay.set_back, NAMES["red"], cv)
        rows = self._settings_rows()
        for i in range(len(rows)):
            if self._settings_row_visible(i):
                self._draw_settings_row(i)
        self._draw_settings_more(rows)
        self._draw_status_strip("settings")
        self._draw_dock("settings")

    def _draw_settings_more(self, rows):
        """Up/down chevrons at the panel's right edge when the Settings list scrolls
        past the visible window (the #53 OTA rows can push it over one screen)."""
        cv = self.sys_canvas
        lay = self.layout
        px, py, pw, ph = lay.settings_panel
        xr = px + pw - 9 * lay.fs
        if self.set_top > 0:
            cv.print("^", xr, lay.set_row_y0, NAMES["white"], 1)
        if self.set_top + self._settings_visible() < len(rows):
            cv.print("v", xr, py + ph - 9 * lay.fs, NAMES["white"], 1)

    def _draw_settings_row(self, i):
        cv = self.sys_canvas
        lay = self.layout
        fw = lay.font_w
        key, label, kind = self._settings_rows()[i]
        x, y, w, h = self._settings_row_rect(i)
        sel = (i == self.set_msel)
        if sel:
            cv.rect(x, y, w, h, NAMES["indigo"])
        fg = NAMES["white"] if sel else NAMES["light_grey"]
        cv.print(label, x + 4, y + 5, fg, 1)
        if kind == "action":
            # An action row (EDIT ICONS / UPDATE FW / UPDATE ONLINE): no value/stepper --
            # just an OPEN affordance at the right so a tap (or A) is the obvious activate.
            # The glyph cues what it does (paint = repaint chrome; run = install; wifi =
            # online update).
            if key == "update":
                g, c = "run", NAMES["yellow"]
            elif key == "update_online":
                g, c = "wifi", NAMES["yellow"]
            else:
                g, c = "paint", NAMES["green"]
            self._glyph(g, (x + w - 18 * lay.fs, y + 2, 14 * lay.fs, 14 * lay.fs), c, cv)
            return
        # < value > stepper at the right (the chevrons print at double size = 2*fw).
        cv.print("<", x + w - 11 * fw - 2, y + 5, NAMES["yellow"], 2)
        cv.print(">", x + w - 2 * fw + 2, y + 5, NAMES["yellow"], 2)
        vx = x + w - 78 * lay.fs           # value column (baseline x+w-78)
        if kind == "wallpaper":
            cv.print(self._settings_wallpaper_label()[:9], vx, y + 5, NAMES["green"], 1)
        elif kind == "font":               # system-UI font size (#39): 1x / 2x / 3x
            cv.print("%dx" % self.font_scale, vx, y + 5, NAMES["green"], 1)
        elif kind == "mock-gauge":
            lvl = int(self.system.get(key, 3))
            for s in range(5):
                c = NAMES["green"] if s < lvl else NAMES["dark_grey"]
                cv.rect(vx + s * 8 * lay.fs, y + 6, 6 * lay.fs, 8 * lay.fs, c)
        elif kind == "mock-name":
            cv.print(str(self.system.get("name", self._MOCK_NAMES[0]))[:8], vx, y + 5,
                     NAMES["peach"], 1)
        elif kind == "channel":            # OTA update channel: STABLE / BETA (#53)
            beta = self._ota_channel() == "unstable"
            cv.print("BETA" if beta else "STABLE", vx, y + 5,
                     NAMES["orange"] if beta else NAMES["green"], 1)
        elif kind == "web":                # device web view (#41): ON/OFF + the URL
            on = False
            url = ""
            try:
                on = bool(self.web_hook.enabled)
                url = str(self.web_hook.url() or "")
            except Exception:  # noqa: BLE001 -- a backend hiccup just reads OFF
                pass
            cv.print("ON" if on else "OFF", vx, y + 5,
                     NAMES["green"] if on else NAMES["dark_grey"], 1)
            if on and url:
                # The URL to open in a phone/desktop browser, under the row label.
                cv.print(url[:34], x + 4, y + 6 + fw, NAMES["blue"], 1)
        # Mark not-yet-functional rows clearly (wallpaper + font + channel + web + actions work).
        if kind not in ("wallpaper", "font", "action", "channel", "web"):
            cv.print("soon", x + 4, y + 6 + fw, NAMES["dark_grey"], 1)

    def _draw_fps(self):
        # Tiny FPS readout in the bottom-right, over a dark chip so it stays legible
        # on any cart. The desktop overlay buttons all sit along the top, so this
        # corner is free. Drawn with the indexed API only (host == device).
        cv = self.canvas
        s = "%d" % int(self._fps + 0.5)
        tw = len(s) * 8
        x = cv.w - tw - 3
        y = cv.h - 10
        cv.rect(x - 2, y - 1, tw + 4, 10, NAMES["black"])
        cv.print(s, x, y, NAMES["yellow"], 1)

    def _fps_tap_rect(self):
        """The bottom-right corner the FPS readout lives in, used as the tap target
        that toggles the perf HUD (#43/#44). Generous (a fixed corner box, not just
        the few-pixel digit chip) so a finger on the device touchscreen lands it; it
        sits over the FPS chip in GAME-canvas coords (the desktop hit-tests in game
        space). Kept off the cart's own top-bar tools, so a kid never trips it by
        accident -- they'd have to deliberately poke the FPS number."""
        cv = self.canvas
        w, h = 40, 14
        return (cv.w - w, cv.h - h, w, h)

    def perf_sample(self):
        """Snapshot of the current per-frame perf numbers for offline sampling:
        (cart_name, fps, flush_ms, draw_ms). Used by the device backend's diag
        sampler (kid_runtime.run_desktop) to log a PERF line every few seconds
        while a cart runs. flush_ms/draw_ms are only meaningful when perf_capture
        (or perf_hud) is on -- run_desktop sets perf_capture=True at boot. Backend-
        agnostic + host-safe: pure reads, no drawing, no hardware. Returns None
        when no cart is actively running (nothing useful to sample)."""
        running = (self.screen == "desktop" and self.cart is not None
                   and self.cart_error is None)
        if not running:
            return None
        cart = self.cart
        name = cart.get("title") or cart.get("path") or "?"
        return (name, self._fps, self._flush_ms, self._draw_ms)

    def perf_breakdown(self):
        """(_upd_ms, _cart_ms, _audio_ms, _chrome_ms): the EMA phase split of draw_ms --
        cart _update (game LOGIC), cart _draw (RENDERING), audio.tick (mixer feed), and
        console chrome (dock + cursor + overlays, the remainder). Used by the device
        diag's DRAWBRK line to find where the per-frame draw cost actually goes (cart
        logic vs rendering vs audio vs chrome). Only meaningful while a cart runs with
        perf_capture/perf_hud on."""
        return (self._upd_ms, self._cart_ms, self._audio_ms, self._chrome_ms)

    def _draw_perf_hud(self):
        """Frame-time breakdown (#43/#44 perf), drawn just above the FPS chip when
        perf_hud is on: "f<flush> d<draw> t<total>" in ms (total = flush + draw).
        flush is the panel DMA flush (comp.flush(); ~0 on the host's _NullComp, real
        only on device); draw is everything else (cart _update/_draw + console draw).
        Indexed API only (host == device); compact so it doesn't overlap the cart's
        HUD where avoidable."""
        cv = self.canvas
        f = int(self._flush_ms + 0.5)
        d = int(self._draw_ms + 0.5)
        s = "f%d d%d t%d" % (f, d, f + d)
        tw = len(s) * 8
        x = cv.w - tw - 3
        if x < 1:
            x = 1
        y = cv.h - 20            # one 8px row above the FPS chip (which sits at h-10)
        cv.rect(x - 2, y - 1, tw + 4, 10, NAMES["black"])
        cv.print(s, x, y, NAMES["white"], 1)

    # -- achievements + Easter-egg drawing (#21) -----------------------------

    def _draw_toast(self):
        """A small celebratory banner near the top: a trophy + "ACHIEVEMENT!" + the
        achievement name + its glyph. Drawn last each frame over whatever screen is
        up, so it never disturbs the content beneath and expires on its own. Indexed
        API only (host == device)."""
        cv = self.sys_canvas
        ach_id, title, glyph = self.ach.toast
        x, y, w, h = 36, 26, 248, 38
        cv.rect(x, y, w, h, NAMES["dark_purple"])
        cv.rectb(x, y, w, h, NAMES["yellow"])
        cv.rect(x, y, w, 12, NAMES["yellow"])
        self._glyph("trophy", (x + 2, y - 1, 12, 12), NAMES["black"], cv)
        cv.print("ACHIEVEMENT UNLOCKED!", x + 16, y + 2, NAMES["black"], 1)
        self._glyph(glyph, (x + 6, y + 16, 16, 16), NAMES["yellow"], cv)
        cv.print(title[:24], x + 28, y + 20, NAMES["white"], 2)

    def _draw_egg(self):
        """A non-blocking Easter-egg popup: a friendly character glyph + the secret
        message, centered low so it reads as a surprise without covering the action.
        Self-expiring (egg_until); cosmetic only -- touches no cart data."""
        cv = self.sys_canvas
        line, glyph = self.egg_msg
        w = min(cv.w - 16, 24 + len(line) * 8 + 8)
        x = (cv.w - w) // 2
        y = 150
        h = 30
        cv.rect(x, y, w, h, NAMES["black"])
        cv.rectb(x, y, w, h, NAMES["pink"])
        self._glyph(glyph, (x + 4, y + 7, 16, 16), NAMES["peach"], cv)
        cv.print(line, x + 24, y + 11, NAMES["white"], 1)

    def _draw_sysmenu(self):
        """The ≡ dropdown (#52): a left-anchored panel flush under the bar, one row per
        item. The selected row gets a full-width bright accent fill + light label;
        unselected rows sit on the panel base fill; headers read dim grey and a 1px
        line separates groups. Index-only verbs + petme128 text (host == device). All
        on the SYSTEM canvas, on top of everything."""
        cv = self.sys_canvas
        m = self.sysmenu
        x, y, w, h = m.panel_rect()
        cv.rect(x, y, w, h, NAMES["dark_purple"])          # panel base fill
        cv.rectb(x, y, w, h, NAMES["indigo"])              # framed edge
        cy = _POPUP_Y
        for idx in range(len(m.items)):
            it = m.items[idx]
            kind = it[0]
            if kind == "sep":
                cv.rect(x + 1, cy, w - 2, _POPUP_SEP_H, NAMES["indigo"])
                cy += _POPUP_SEP_H
                continue
            label = it[1]
            tx = x + _POPUP_PAD_X
            ty = cy + 2
            if kind == "header":
                cv.print(label, tx, ty, NAMES["dark_grey"], 1)   # dim section title
            elif idx == m.sel:
                cv.rect(x + 1, cy, w - 2, _POPUP_ROW_H, NAMES["indigo"])  # highlight
                cv.print(label, tx, ty, NAMES["white"], 1)
            else:
                cv.print(label, tx, ty, NAMES["light_grey"], 1)
            cy += _POPUP_ROW_H

    def _draw_about(self):
        """The ABOUT info modal (#52): a small centered panel with the console name +
        firmware version, dismissed by any tap / ESC / B. Drawn on top of everything."""
        cv = self.sys_canvas
        lines = ("KIDCODE CONSOLE", "v0.4", "", "TAP TO CLOSE")
        ver = self._firmware_version_text()
        if ver:
            lines = ("KIDCODE CONSOLE", ver, "", "TAP TO CLOSE")
        w = 0
        for ln in lines:
            w = max(w, len(ln) * 8)
        w += 24
        w = min(w, cv.w - 16)
        h = 20 + len(lines) * 12
        x = (cv.w - w) // 2
        y = (cv.h - h) // 2
        cv.rect(x, y, w, h, NAMES["black"])
        cv.rectb(x, y, w, h, NAMES["pink"])
        ly = y + 10
        for ln in lines:
            cv.print(ln, x + (w - len(ln) * 8) // 2, ly, NAMES["white"], 1)
            ly += 12

    def _firmware_version_text(self):
        """A short firmware-version string for ABOUT, or "" when unknown (host). Reads
        the injected updater's version when present (device kc_ota.FIRMWARE_VERSION)."""
        u = self.updater
        if u is not None:
            v = getattr(u, "version", None)
            try:
                v = v() if callable(v) else v
            except Exception:  # noqa: BLE001
                v = None
            if v is not None:
                return "FW " + str(v)
        return ""

    def _draw_confetti(self):
        """The Konami egg's celebration: a scatter of colored spark glyphs that
        drift down with the elapsed time. Cheap + deterministic (no RNG state),
        purely cosmetic, gone when _confetti_until passes."""
        cv = self.sys_canvas
        t = (_ticks_diff(_ticks_ms(), 0) // 80) % 240
        cols = (NAMES["red"], NAMES["yellow"], NAMES["green"], NAMES["blue"],
                NAMES["pink"], NAMES["orange"])
        for k in range(18):
            sx = (k * 53 + 7) % (cv.w - 6)
            sy = (k * 37 + t + (k * k)) % (cv.h - 6)
            self._glyph("spark", (sx, sy, 8, 8), cols[k % len(cols)], cv)

    def _draw_achievements(self):
        """The achievements view (#21): a full panel listing every achievement,
        unlocked ones with their name + glyph in bright color, locked ones greyed
        with a lock + "???" (so a hidden secret stays a surprise). A two-column grid
        so all ~11 fit at 320x240. Tap anywhere to dismiss (see _settings_pointer).
        Indexed API + the shared glyph vocabulary only (host == device)."""
        cv = self.sys_canvas
        cv.rect(6, 14, 308, 212, NAMES["dark_blue"])
        cv.rectb(6, 14, 308, 212, NAMES["yellow"])
        self._glyph("trophy", (12, 16, 14, 14), NAMES["yellow"], cv)
        cv.print("ACHIEVEMENTS", 30, 18, NAMES["white"], 2)
        cv.print("%d / %d" % (self.ach.count(), len(ACHIEVEMENTS)), 240, 20,
                 NAMES["yellow"], 1)
        col_w = 150
        row_h = 18
        x0 = 12
        y0 = 36
        per_col = 6
        for k in range(len(ACHIEVEMENTS)):
            ach_id, title, glyph, hidden = ACHIEVEMENTS[k]
            col = k // per_col
            row = k % per_col
            x = x0 + col * col_w
            y = y0 + row * row_h
            got = self.ach.has(ach_id)
            if got:
                self._glyph(glyph, (x, y, 14, 14), NAMES["yellow"], cv)
                cv.print(title[:16], x + 16, y + 3, NAMES["white"], 1)
            else:
                self._glyph("lock", (x, y, 14, 14), NAMES["dark_grey"], cv)
                # A hidden (Easter-egg) achievement stays "???"; a normal locked one
                # shows its name greyed so a kid knows what's there to earn.
                label = "???" if hidden else title[:16]
                cv.print(label, x + 16, y + 3, NAMES["light_grey"], 1)
        cv.print("TAP TO CLOSE", 110, 210, NAMES["light_grey"], 1)

    def _btn(self, label, rect, fill):
        x, y, w, h = rect
        cv = self.canvas
        cv.rect(x, y, w, h, fill)
        cv.rectb(x, y, w, h, NAMES["white"])
        cv.print(label, x + 6, y + (h - 8) // 2, NAMES["black"], 2)

    def _icon_btn(self, kind, label, rect, fill):
        """A button that leads with an icon glyph (pre-literate) and keeps the
        word as a small secondary cue beside it -- so a reader still gets the
        label and a kid who can't read still gets the picture."""
        x, y, w, h = rect
        cv = self.canvas
        cv.rect(x, y, w, h, fill)
        cv.rectb(x, y, w, h, NAMES["white"])
        self._glyph(kind, (x + 2, y, 16, h), NAMES["black"])
        if label:
            cv.print(label, x + 19, y + (h - 8) // 2, NAMES["black"], 1)

    def _draw_error_panel(self):
        # A friendly on-canvas crash report (the device never reaches serial, so
        # this is the ONLY error surface). Drawn with the indexed API only: a red
        # box + a short title + the exception text, word-wrapped and truncated to
        # fit. The CODE/EDIT button below it stays live so the kid can fix the cart.
        cv = self.canvas
        x, y, w, h = 14, 40, 292, 132
        cv.rect(x, y, w, h, NAMES["dark_purple"])
        cv.rectb(x, y, w, h, NAMES["red"])
        cv.rect(x, y, w, 14, NAMES["red"])
        cv.print("OOPS! THIS CART CRASHED", x + 6, y + 4, NAMES["white"], 1)
        cols = (w - 16) // 8                       # 8px monospace cells
        lines = _wrap(self.cart_error or "Unknown error", cols)
        max_rows = (h - 30) // _CODE_LH
        for i in range(min(len(lines), max_rows)):
            cv.print(lines[i], x + 8, y + 20 + i * _CODE_LH, NAMES["peach"], 1)
        cv.print("TAP CODE TO FIX IT", x + 8, y + h - 12, NAMES["yellow"], 1)

    def _draw_cursor(self):
        # The pointer lives in SYSTEM-canvas space (it ranges over the panel size),
        # so the cursor draws on the system canvas, on TOP of the composited viewport
        # (#39). Scaled with the font so it stays visible on a big panel; at scale 1
        # / a 320x240 system canvas this is exactly today's 1x cursor on the canvas.
        if self.pointer is not None and self.pointer.visible:
            self.sys_canvas.spr(CURSOR, self.pointer.x, self.pointer.y, self.font_scale)

    def _draw_cards(self):
        cv = self.canvas
        cv.rect(20, 16, 280, 206, NAMES["dark_purple"])
        cv.rectb(20, 16, 280, 206, NAMES["pink"])
        self._glyph("edit", (28, 20, 14, 14), NAMES["yellow"])   # pencil = "make it yours"
        cv.print("MAKE IT MINE", 46, 22, NAMES["white"], 2)
        for row in self._card_layout():
            self._draw_card(row)
        if self._cards_scrollable():           # up/down chevrons when cards overflow
            if self.mtop > 0:
                cv.print("^", _CARD_SCROLL_UP[0], _CARD_SCROLL_UP[1], NAMES["yellow"], 2)
            if self.mtop < self._max_mtop():
                cv.print("v", _CARD_SCROLL_DN[0], _CARD_SCROLL_DN[1], NAMES["yellow"], 2)
        self._icon_btn("run", "GO", _RUN_BTN, NAMES["green"])
        self._icon_btn("edit", "CODE", _CODE_BTN, NAMES["blue"])
        self._icon_btn("close", "", _CLOSE_BTN, NAMES["red"])

    def _draw_card(self, row):
        cv = self.canvas
        i, f = row["i"], row["f"]
        x, y, w, h = row["x"], row["y"], row["w"], row["h"]
        sel = (i == self.msel)
        if sel:
            cv.rect(x, y - 1, w, h, NAMES["indigo"])
        fg = NAMES["white"] if sel else NAMES["light_grey"]
        disp = row["display"]
        if disp is None:                                # today's plain text card
            self._glyph("minus", (x, y, 14, 14), NAMES["yellow"])
            cv.print(self.card_text(i), x + 18, y, fg, 2)
            self._glyph("plus", (x + w - 14, y, 14, 14), NAMES["yellow"])
            return
        # Visual card: a small label line (the SECONDARY text cue) + a picture row.
        cv.print(self.card_text(i), x + 2, y, fg, 1)
        if disp == "gauge":
            self._draw_gauge(row)
        elif disp == "count":
            self._draw_count(row)
        elif disp == "bg-thumbs":
            self._draw_bg_thumbs(row)
        elif disp in ("choice-icons", "sprite-tiles"):
            self._draw_choice_icons(row)

    def _draw_gauge(self, row):
        # A slow->fast slider: a turtle at the low end, a rabbit at the high end,
        # a track filled to the value's fraction, and a knob. Tap left/right of the
        # card to step it (the -/+ contract is preserved by _card_tap).
        cv = self.canvas
        f = row["f"]
        x, y, w = row["x"], row["y"], row["w"]
        lo = f.get("min", 0)
        hi = f.get("max", lo + 1)
        cur = self.config.get(f["key"], f.get("default", lo))
        try:
            frac = (float(cur) - lo) / (hi - lo) if hi > lo else 0.0
        except (TypeError, ValueError):
            frac = 0.0
        frac = max(0.0, min(1.0, frac))
        ends = f.get("gauge", {}) if isinstance(f.get("gauge"), dict) else {}
        ty = y + 18
        tx0 = x + 18
        tx1 = x + w - 18
        tw = tx1 - tx0
        self._glyph(ends.get("low", "turtle"), (x, ty - 6, 16, 14), NAMES["green"])
        self._glyph(ends.get("high", "rabbit"), (x + w - 16, ty - 6, 16, 14), NAMES["peach"])
        cv.rect(tx0, ty, tw, 3, NAMES["dark_grey"])                 # track
        cv.rect(tx0, ty, int(tw * frac), 3, NAMES["yellow"])        # filled portion
        kx = tx0 + int(tw * frac)
        cv.rect(kx - 1, ty - 3, 3, 9, NAMES["white"])               # knob

    def _draw_count(self, row):
        # N repeated icons == the value, so a count reads at a glance. Kept to ONE
        # tidy row -- the count card is 32px tall, so a 2nd row of glyphs would
        # spill into the next card. The number itself is the label cue above, so an
        # over-cap value still reads correctly even when not every icon fits.
        f = row["f"]
        x, y, w = row["x"], row["y"], row["w"]
        cur = self.config.get(f["key"], f.get("default", 0))
        try:
            n = int(cur)
        except (TypeError, ValueError):
            n = 0
        glyph = f.get("icon", "star")
        step = 16
        per_row = max(1, (w - 4) // step)
        cap = int(f.get("count_max", min(f.get("max", 12), 14)))
        shown = max(0, min(n, cap, per_row))    # clamp to a single row
        for k in range(shown):
            gx = x + 2 + k * step
            self._glyph(glyph, (gx, y + 14, 14, 14), NAMES["yellow"])

    def _draw_choice_icons(self, row):
        # Each choice is its own tappable cell -- a glyph (choice-icons) or a real
        # sprite tile from the cart sheet (sprite-tiles). The current pick is boxed.
        cv = self.canvas
        f = row["f"]
        cur = self.config.get(f["key"], f.get("default"))
        sel_k = self._choice_index(f, cur)
        tiles = self._resolve_tiles(f) if row["display"] == "sprite-tiles" else None
        icons = f.get("icons") or []
        for k, (cx, cy, cw, ch) in self._choice_cells(row):
            chosen = (k == sel_k)
            cv.rect(cx, cy, cw, ch, NAMES["black"] if chosen else NAMES["dark_purple"])
            cv.rectb(cx, cy, cw, ch, NAMES["yellow"] if chosen else NAMES["dark_grey"])
            if tiles is not None and self.sheet is not None:
                img = self.sheet.tile_image(tiles[k] if k < len(tiles) else 0, -1)
                if img is not None:
                    self.canvas.spr(img, cx + (cw - 16) // 2, cy + (ch - 16) // 2, 2)
            else:
                glyph = icons[k] if k < len(icons) else "dot"
                self._glyph(glyph, (cx + (cw - 14) // 2, cy + (ch - 14) // 2, 14, 14),
                            NAMES["white"])

    # Each bg-thumbs choice is drawn as a tiny "what the screen will look like"
    # preview. A cart reads the chosen name in cfg("bg") and paints to match.
    # "night"/"stripes" get a patterned thumbnail; any other name renders as a
    # solid swatch via NAMES.get, so arbitrary palette colors (e.g. "indigo")
    # just work -- no preset list to keep in sync.

    def _draw_bg_thumb(self, name, rect):
        """Paint a small preview of background preset `name` inside `rect`."""
        cv = self.canvas
        x, y, w, h = rect
        if name == "night":                              # starfield
            cv.rect(x, y, w, h, NAMES["black"])
            for sx, sy in ((4, 4), (14, 9), (24, 5), (30, 15), (9, 17), (20, 12)):
                cv.pix(x + sx, y + sy, NAMES["white"])
        elif name == "stripes":
            for i in range(0, w, 6):
                cv.rect(x + i, y, 3, h, NAMES["indigo"])
                cv.rect(x + i + 3, y, 3, h, NAMES["dark_blue"])
        else:                                            # a solid color swatch
            cv.rect(x, y, w, h, NAMES.get(name, NAMES["black"]))

    def _draw_bg_thumbs(self, row):
        # Each choice is a tappable thumbnail of the resulting background (#15 P3).
        cv = self.canvas
        f = row["f"]
        cur = self.config.get(f["key"], f.get("default"))
        sel_k = self._choice_index(f, cur)
        for k, (cx, cy, cw, ch) in self._choice_cells(row):
            self._draw_bg_thumb(f["choices"][k], (cx + 1, cy + 1, cw - 2, ch - 2))
            cv.rectb(cx, cy, cw, ch,
                     NAMES["yellow"] if k == sel_k else NAMES["dark_grey"])

    def _draw_code(self):
        cv = self.canvas
        ed = self.editor
        cv.cls(NAMES["black"])                  # full-screen editor
        # top bar: cart title (+ unsaved marker) and the action icons
        title = self.cart["title"][:31]
        if ed is not None and ed.dirty:
            title = title + " *"
        cv.print(title, 2, 3, NAMES["green"], 1)
        self._draw_icon("run", _ED_RUN)
        self._draw_icon("save", _ED_SAVE)
        self._draw_icon("close", _ED_CLOSE)
        # code area (horizontal scroll: columns [left, left+COLS))
        if ed is not None:
            vis = ed.visible_lines()
            errrow = self.code_err_row
            for idx in range(len(vis)):
                y = _CODE_Y0 + idx * _CODE_LH
                full = vis[idx]
                on_err = errrow is not None and ed.top + idx == errrow
                if on_err:                      # inline error: gutter mark + underline (#24)
                    cv.rect(0, y, 3, 8, NAMES["red"])
                    cv.rect(_CODE_X0, y + 8, CodeEditor.COLS * 8, 1, NAMES["red"])
                seg = full[ed.left:ed.left + CodeEditor.COLS]
                segcols = self._hl(full)[ed.left:ed.left + CodeEditor.COLS]
                self._draw_code_runs(seg, segcols, y)
                if on_err and self.code_err:    # short reason after the code, if it fits
                    mcol = len(seg) + 1
                    if mcol < CodeEditor.COLS - 2:
                        cv.print(self.code_err[:CodeEditor.COLS - mcol],
                                 _CODE_X0 + mcol * 8, y, NAMES["red"], 1)
                if ed.top + idx == ed.row:      # caret on the cursor's line
                    vcol = ed.col - ed.left
                    if 0 <= vcol <= CodeEditor.COLS:
                        cv.rect(_CODE_X0 + vcol * 8, y, 1, 8, NAMES["yellow"])
        self._draw_symbols()

    def _hl(self, line):
        """Memoized per-line syntax highlight (#24). Lines recur every frame, so
        cache by text; bound the cache so a long edit session can't grow it."""
        cols = self._hl_cache.get(line)
        if cols is None:
            if len(self._hl_cache) > 400:
                self._hl_cache.clear()
            cols = _highlight(line)
            self._hl_cache[line] = cols
        return cols

    def _draw_code_runs(self, seg, segcols, y):
        """Draw one code line as runs of same-colored text (#24)."""
        cv = self.canvas
        n = len(seg)
        i = 0
        while i < n:
            cl = segcols[i]
            j = i + 1
            while j < n and segcols[j] == cl:
                j += 1
            cv.print(seg[i:j], _CODE_X0 + i * 8, y, cl, 1)
            i = j

    def _draw_symbols(self):
        # Tappable coding-symbol palette (supplies what the keyboard can't type).
        cv = self.canvas
        for i in range(len(_CODE_SYMBOLS)):
            x = _SYM_AREA[0] + i * _SYM_CELL
            cv.rect(x, _SYM_Y, _SYM_CELL - 1, _SYM_H - 1, NAMES["dark_grey"])
            cv.rectb(x, _SYM_Y, _SYM_CELL - 1, _SYM_H - 1, NAMES["indigo"])
            cv.print(_CODE_SYMBOLS[i], x + 6, _SYM_Y + 6, NAMES["white"], 1)

    def _draw_icon(self, kind, rect):
        # A glyph on its own colored button background -- the code-editor top bar
        # (run/save/close). The pure glyph vocabulary lives in _glyph(); this just
        # paints a backing box of a sensible color, then the glyph on top.
        bg = {"run": "green", "save": "blue", "close": "red"}.get(kind, "dark_grey")
        x, y, w, h = rect
        self.canvas.rect(x, y, w, h, NAMES[bg])
        self._glyph(kind, rect, NAMES["black"] if kind == "run" else NAMES["white"])

    def _glyph(self, kind, rect, c, cv=None):
        # Draw a centered icon glyph in color `c`. Defaults to the GAME canvas (the
        # editors/cart-overlay callers); the desktop/system callers pass cv=
        # self.sys_canvas so the glyph follows the system font scale (#39). The shared
        # blit + the glyph encoding live in the module-level _blit_glyph so Launcher
        # (canvas-only) renders the identical vocabulary.
        _blit_glyph(cv if cv is not None else self.canvas, kind, rect, c)

    def _bar_image(self, kind):
        """The cached 16x16 _SheetSprite for top-bar icon `kind`, or None when the
        icon sheet/slot is missing. Memoised per kind so the SAME image object is
        blitted every frame -- the device caches its RGB565 copy on the image, so the
        bar costs one cached blit per icon (Stage 1's perf goal)."""
        if kind in self._bar_img_cache:
            return self._bar_img_cache[kind]
        img = None
        if self.icon_sheet is not None:
            slot = _ICON.get(kind)
            if slot is not None:
                img = self.icon_sheet.tile_image(slot)   # transparent -1 (icons keyed)
        self._bar_img_cache[kind] = img
        return img

    def _icon(self, kind, x, y, cv=None):
        """Blit the top-bar icon `kind` (a 16x16 IconSheet sprite) at (x, y). The
        themeable replacement for _glyph on the bar; falls back to _glyph (the 12x12
        bitmap, centered) when the icon sheet/slot is missing, so the bar never crashes
        on a half-wired theme. cv defaults to the system canvas (the bar lives there);
        the running-cart bar passes the game canvas explicitly. The icon scales with
        the canvas's system font scale (#39) so it grows on a larger panel -- the GAME
        canvas is always scale 1, so the cart bar is byte-identical."""
        cv = cv if cv is not None else self.sys_canvas
        fs = getattr(cv, "font_scale", 1)
        if fs < 1:
            fs = 1
        img = self._bar_image(kind)
        if img is not None:
            cv.spr(img, x, y, fs)                        # 16px art upscaled by font scale
        else:
            self._glyph(kind, (x, y, _BAR_ICON * fs, _BAR_ICON * fs),
                        NAMES["light_grey"], cv)

    def _draw_paint(self):
        cv = self.canvas
        pe = self.paint
        # Edit the editor's OWN sheet -- the cart sprites for PAINT, the system icon
        # sheet for the theme editor (EDIT ICONS) -- so one renderer serves both.
        sheet = pe.sheet if pe is not None else self.sheet
        cv.rect(8, 16, 304, 204, NAMES["black"])
        cv.rectb(8, 16, 304, 204, NAMES["orange"])
        title = ("ICONS  TILE " if self._editing_icons else "PAINT  SPR ") + str(pe.n if pe else 0)
        if sheet is not None and sheet.dirty:
            title = title + " *"
        cv.print(title, 14, 18, NAMES["orange"], 1)
        if pe is None or sheet is None:
            return
        # Zoomed pixel grid: a fixed _PG_SPAN square, cells shrink as the sprite
        # grows so a 1x1/2x2/3x3 sprite (#30) all edit in the same footprint. Pixels
        # come from the sheet's flat buffer at the sprite's tile origin, so the grid
        # spans the constituent tiles for sizes > 1. Grid lines are drawn only when
        # the cell is big enough to read (skip them once cells get tiny).
        dim = pe.dim
        cell = _PG_SPAN // dim
        if cell < 1:
            cell = 1
        ox, oy = sheet.tile_origin(pe.n)
        lines = cell >= 6
        for ly in range(dim):
            for lx in range(dim):
                x = _PG_X0 + lx * cell
                y = _PG_Y0 + ly * cell
                cv.rect(x, y, cell, cell, sheet.pget(ox + lx, oy + ly))
                if lines:
                    cv.rectb(x, y, cell, cell, NAMES["dark_grey"])
        # Outline the whole grid + the tile boundaries (so a 2x2/3x3 sprite shows
        # where its constituent sheet tiles divide).
        cv.rectb(_PG_X0, _PG_Y0, _PG_SPAN, _PG_SPAN, NAMES["orange"])
        if pe.size > 1:
            tpx = _PG_SPAN // pe.size
            for t in range(1, pe.size):
                cv.line(_PG_X0 + t * tpx, _PG_Y0,
                        _PG_X0 + t * tpx, _PG_Y0 + _PG_SPAN - 1, NAMES["light_grey"])
                cv.line(_PG_X0, _PG_Y0 + t * tpx,
                        _PG_X0 + _PG_SPAN - 1, _PG_Y0 + t * tpx, NAMES["light_grey"])
        # 16-color palette (2x8), the selected swatch outlined white.
        for idx in range(16):
            x = _SW_X0 + (idx % _SW_COLS) * _SW
            y = _SW_Y0 + (idx // _SW_COLS) * _SW
            cv.rect(x, y, _SW, _SW, idx)
            cv.rectb(x, y, _SW, _SW,
                     NAMES["white"] if idx == pe.color else NAMES["dark_grey"])
        # Sprite selector + a SIZE cycle button (#30) + a preview of the sprite,
        # scaled so the whole NxN span fits a fixed ~32px box.
        self._btn("<", _SPR_PREV, NAMES["blue"])
        self._btn(">", _SPR_NEXT, NAMES["blue"])
        self._btn("SIZE %dx%d" % (pe.size, pe.size), _PAINT_SIZE, NAMES["dark_purple"])
        ppx, ppy = 240, 92
        ps = max(1, 32 // dim)
        for ly in range(dim):
            for lx in range(dim):
                cv.rect(ppx + lx * ps, ppy + ly * ps, ps, ps,
                        sheet.pget(ox + lx, oy + ly))
        cv.rectb(ppx, ppy, dim * ps, dim * ps, NAMES["dark_grey"])
        # Cross-cart sprite reuse (#18): GET pulls this tile out of the shared sheet,
        # PUT pushes it in. Hidden in the theme editor -- the shared sheet is 8x8 cart
        # sprites, not the 16x16 icon theme, so GET/PUT don't apply there.
        if not self._editing_icons:
            self._icon_btn("get", "GET", _PAINT_GET, NAMES["indigo"])
            self._icon_btn("put", "PUT", _PAINT_PUT, NAMES["dark_green"])
        if self.paint_status:
            cv.print(self.paint_status[:18], 110, 196, NAMES["yellow"], 1)
        self._btn("SAVE", _PAINT_SAVE, NAMES["green"])
        self._btn("CLOSE", _PAINT_CLOSE, NAMES["red"])

    def _draw_map(self):
        # The map (tilemap) editor (#32): a panned view of the map on the left where
        # each cell shows the placed sprite tile, and a paged tile palette on the
        # right to pick the brush. Mirrors _draw_paint's structure (grid + picker +
        # save/close), drawn with the indexed API only so host == device.
        cv = self.canvas
        me = self.mapedit
        sheet = self.sheet
        cv.rect(8, 16, 304, 204, NAMES["black"])
        cv.rectb(8, 16, 304, 204, NAMES["green"])
        # Live zoom metrics (#37 follow-up): one cell size drives the grid, the tile
        # upscale and the title's "z<level>" badge.
        x0, y0, cell, cols, rows = self._mv_metrics()
        if me is not None and me.n < 0:        # the EMPTY/"sky" brush (#37)
            title = "MAP  SKY"
        else:
            title = "MAP  TILE " + str(me.n if me else 0)
        title = title + "  z" + str(self.map_zoom + 1)
        if self.tilemap is not None and self.tilemap.dirty:
            title = title + " *"
        cv.print(title, 14, 18, NAMES["green"], 1)
        if me is None or sheet is None or self.tilemap is None:
            return
        tm = self.tilemap
        # Visible map region: each cell is the sprite tile placed there, UPSCALED to
        # fill the cell (scale = cell // TILE, crisp pixel-art) and centered, with grid
        # lines so empty cells read as empty. Tile images are cached by id within the
        # draw so a repeated tile builds once.
        cache = {}
        scale = max(1, cell // sheet.TILE)
        off = (cell - sheet.TILE * scale) // 2
        for ry in range(rows):
            cy = me.cam_y + ry
            for rx in range(cols):
                cx = me.cam_x + rx
                x = x0 + rx * cell
                y = y0 + ry * cell
                inb = (cx < tm.w and cy < tm.h)
                cv.rect(x, y, cell, cell,
                        NAMES["dark_blue"] if inb else NAMES["black"])
                if inb:
                    tid = tm.mget(cx, cy)
                    if tid >= 0:
                        img = cache.get(tid)
                        if img is None:
                            img = sheet.tile_image(tid, -1)
                            cache[tid] = img if img is not None else False
                        if img:
                            cv.spr(img, x + off, y + off, scale)
                cv.rectb(x, y, cell, cell, NAMES["dark_grey"])
        # Tile palette (right): a page of sheet tiles; the brush tile is boxed white.
        ids = self._map_palette_ids()
        for k in range(len(ids)):
            tid = ids[k]
            x = _TP_X0 + (k % _TP_COLS) * _TP_CELL
            y = _TP_Y0 + (k // _TP_COLS) * _TP_CELL
            cv.rect(x, y, _TP_CELL, _TP_CELL, NAMES["black"])
            img = sheet.tile_image(tid, -1)
            if img is not None:
                cv.spr(img, x + (_TP_CELL - sheet.TILE) // 2,
                       y + (_TP_CELL - sheet.TILE) // 2, 1)
            cv.rectb(x, y, _TP_CELL, _TP_CELL,
                     NAMES["white"] if tid == me.n else NAMES["dark_grey"])
        self._btn("<", _TP_PREV, NAMES["blue"])
        self._btn(">", _TP_NEXT, NAMES["blue"])
        # ZOOM control (#37 follow-up): cycles the zoom level (in the d-pad center);
        # the title's "z<level>" badge shows which level is active.
        self._btn("Z" + str(self.map_zoom + 1), _MAP_ZOOM, NAMES["dark_purple"])
        # Pan d-pad under the map view.
        self._btn("^", _PAN_UP, NAMES["indigo"])
        self._btn("v", _PAN_DN, NAMES["indigo"])
        self._btn("<", _PAN_LF, NAMES["indigo"])
        self._btn(">", _PAN_RT, NAMES["indigo"])
        # ERASE toggle (highlighted when active) + SAVE + CLOSE.
        self._btn("ER", _MAP_ERASE, NAMES["red"] if self.map_erase else NAMES["dark_grey"])
        self._btn("SAVE", _MAP_SAVE, NAMES["green"])
        self._btn("CLOSE", _MAP_CLOSE, NAMES["red"])
        # EMPTY/"sky" swatch (#37): a selectable brush that paints "nothing". Drawn
        # as a checkerboard (the universal transparent cue) + "SKY" label, boxed
        # white when it's the active brush so it reads like any other palette pick.
        sx, sy, sw, sh = _TP_SKY
        cb = sh // 2
        cv.rect(sx, sy, sw, sh, NAMES["dark_blue"])
        cv.rect(sx, sy, cb, cb, NAMES["light_grey"])
        cv.rect(sx + cb, sy + cb, cb, cb, NAMES["light_grey"])
        cv.print("SKY", sx + sw - 26, sy + (sh - 8) // 2, NAMES["white"], 1)
        cv.rectb(sx, sy, sw, sh,
                 NAMES["white"] if me.n < 0 else NAMES["dark_grey"])

    # -- music / sound editor drawing (#50) ----------------------------------

    def _draw_music(self):
        """The tracker-style sound editor (#50): a title row (which SFX/track + its
        tempo), a scrolling step/slot list with the cursor highlighted, a right-hand
        edit pad, and a bottom PLAY/SAVE/LOOP/CLOSE bar. Drawn with the indexed API +
        petme128 font only, so host == device."""
        cv = self.canvas
        me = self.musicedit
        cv.cls(NAMES["dark_blue"])
        cv.rect(0, 0, cv.w, 14, NAMES["black"])
        if me is None:
            cv.print("NO SOUND BANK", _MU_LIST_X, _MU_TITLE_Y, NAMES["white"], 1)
            self._btn("CLOSE", _MU_CLOSE, NAMES["red"])
            return
        song = me.view == MusicEditor.SONG_VIEW
        # Title: which object + its tempo + a dirty *.
        if song:
            obj = me.cur_track()
            title = "SONG " + str(me.track_idx)
        else:
            obj = me.cur_sfx()
            title = "SFX " + str(me.sfx_idx)
        speed = obj.speed if obj is not None else 0
        loop = bool(obj.loop) if obj is not None else False
        if me.dirty:
            title = title + " *"
        cv.print(title, 36, _MU_TITLE_Y, NAMES["white"], 1)
        cv.print("SPD " + str(speed), 258, _MU_TITLE_Y, NAMES["light_grey"], 1)
        # Object < n > stepper + tempo +/- (compact, in the title strip).
        self._btn("<", _MU_OBJ_PREV, NAMES["indigo"])
        self._btn(">", _MU_OBJ_NEXT, NAMES["indigo"])
        self._mu_tick(_MU_SPEED_DN, "-")
        self._mu_tick(_MU_SPEED_UP, "+")
        # View toggle (SFX <-> SONG).
        self._btn("SONG" if not song else "SFX", _MU_VIEW, NAMES["dark_purple"])
        # The scrolling step/slot list.
        if song:
            self._draw_music_song(me)
        else:
            self._draw_music_sfx(me)
        # Right-hand edit pad.
        self._draw_music_pad(song)
        # Bottom bar: PLAY/STOP toggles the preview; SAVE; LOOP flag; CLOSE.
        playing = self.music_preview is not None
        self._btn("STOP" if playing else "PLAY", _MU_PLAY,
                  NAMES["red"] if playing else NAMES["green"])
        self._btn("SAVE", _MU_SAVE, NAMES["green"])
        self._btn("LOOP" if loop else "1X", _MU_LOOP,
                  NAMES["orange"] if loop else NAMES["dark_grey"])
        self._btn("CLOSE", _MU_CLOSE, NAMES["red"])
        if self.save_status:
            cv.print(self.save_status[:14], 150, _MU_TITLE_Y, NAMES["yellow"], 1)

    def _mu_tick(self, rect, label):
        """A small +/- tick button (smaller text than _btn for the title-strip nudges)."""
        x, y, w, h = rect
        cv = self.canvas
        cv.rect(x, y, w, h, NAMES["blue"])
        cv.rectb(x, y, w, h, NAMES["white"])
        cv.print(label, x + (w - 8) // 2, y + (h - 8) // 2, NAMES["black"], 1)

    def _mu_visible_top(self, cur, total):
        """First list row to show so the cursor stays in view (simple scrolloff)."""
        if total <= _MU_ROWS:
            return 0
        top = cur - _MU_ROWS // 2
        if top < 0:
            top = 0
        if top > total - _MU_ROWS:
            top = total - _MU_ROWS
        return top

    def _draw_music_sfx(self, me):
        cv = self.canvas
        s = me.cur_sfx()
        if s is None:
            return
        total = len(s.steps)
        top = self._mu_visible_top(me.step, total)
        for vi in range(_MU_ROWS):
            idx = top + vi
            if idx >= total:
                break
            x = _MU_LIST_X
            y = _MU_LIST_Y0 + vi * _MU_ROW_H
            cur = (idx == me.step)
            if cur:
                cv.rect(x, y, _MU_LIST_W, _MU_ROW_H - 1, NAMES["indigo"])
            pitch, wave, vol = s.steps[idx][0], s.steps[idx][1], s.steps[idx][2]
            tc = NAMES["white"] if cur else NAMES["light_grey"]
            cv.print("%02d" % idx, x + 2, y + 4, NAMES["dark_grey"]
                     if not cur else NAMES["light_grey"], 1)
            note = _mu_note_name(pitch)
            cv.print(note, x + 24, y + 4, NAMES["peach"] if pitch >= 0 else
                     NAMES["dark_grey"], 1)
            cv.print(_MU_WAVE_LABELS[wave & 3], x + 64, y + 4, tc, 1)
            # a little volume bar (0..7) -> up to 7 ticks
            bx = x + 96
            for v in range(7):
                col = NAMES["green"] if v < vol else NAMES["dark_grey"]
                cv.rect(bx + v * 7, y + 4, 5, 8, col)

    def _draw_music_song(self, me):
        cv = self.canvas
        t = me.cur_track()
        if t is None:
            return
        total = len(t.pattern)
        top = self._mu_visible_top(me.slot, total)
        for vi in range(_MU_ROWS):
            idx = top + vi
            if idx >= total:
                break
            x = _MU_LIST_X
            y = _MU_LIST_Y0 + vi * _MU_ROW_H
            cur = (idx == me.slot)
            if cur:
                cv.rect(x, y, _MU_LIST_W, _MU_ROW_H - 1, NAMES["indigo"])
            sid = t.pattern[idx]
            cv.print("%02d" % idx, x + 2, y + 4, NAMES["dark_grey"]
                     if not cur else NAMES["light_grey"], 1)
            cv.print("SFX " + str(sid), x + 30, y + 4,
                     NAMES["white"] if cur else NAMES["light_grey"], 1)

    def _draw_music_pad(self, song):
        # Two columns x four rows of edit buttons; labels differ per view.
        if song:
            labels = (("SFX-", "SFX+"), ("", ""), ("", ""), ("ADD", "DEL"))
            cols = ((NAMES["blue"], NAMES["blue"]), (None, None), (None, None),
                    (NAMES["dark_green"], NAMES["red"]))
        else:
            labels = (("NOTE-", "NOTE+"), ("WAVE", "VOL"), ("REST", ""),
                      ("ADD", "DEL"))
            cols = ((NAMES["blue"], NAMES["blue"]),
                    (NAMES["dark_purple"], NAMES["orange"]),
                    (NAMES["brown"], None),
                    (NAMES["dark_green"], NAMES["red"]))
        for row in range(4):
            for col in range(2):
                lbl = labels[row][col]
                if not lbl:
                    continue
                self._btn(lbl, _mu_pad_rect(col, row), cols[row][col])

    # -- block editor drawing (#29 Part 2) -----------------------------------

    def _draw_blocks(self):
        """The structured outline: a title bar, a scrolling list of Scratch-style
        colored block rows (the flattened script with the cursor highlighted and the
        insert points shown as `+`), and a bottom action bar. Drawn with the indexed
        API + petme128 font only, so host == device."""
        cv = self.canvas
        be = self.blocks_ed
        cv.cls(NAMES["dark_blue"])
        title = "BLOCKS  " + (self.cart["title"][:18] if self.cart else "")
        if be is not None and be.dirty:
            title = title + " *"
        cv.print(title, _BLK_X0, _BLK_TITLE_Y, NAMES["white"], 1)
        if self.blk_status:
            cv.print(self.blk_status[:20], 198, _BLK_TITLE_Y, NAMES["yellow"], 1)
        if be is None:
            return
        # A kid-facing hint for the surprising blocks (forever-is-bounded / wait).
        hint = self._blk_hint()
        if hint:
            cv.print(hint[:50], _BLK_X0, 9, NAMES["light_grey"], 1)
        rows = be.rows
        for vi in range(_BLK_ROWS):
            ridx = self.blk_top + vi
            if ridx >= len(rows):
                break
            self._draw_blk_row(rows[ridx], vi, ridx == be.cur)
        # scroll cue
        if self.blk_top > 0:
            cv.print("^", _BLK_X0 + _BLK_W - 8, _BLK_Y0, NAMES["white"], 1)
        if self.blk_top + _BLK_ROWS < len(rows):
            cv.print("v", _BLK_X0 + _BLK_W - 8,
                     _BLK_Y0 + (_BLK_ROWS - 1) * _BLK_ROW_H, NAMES["white"], 1)
        # action bar
        self._icon_btn("plus", "ADD", _BLK_ADD, NAMES["green"])
        self._btn("DEL", _BLK_DEL, NAMES["red"])
        self._btn("^", _BLK_UP, NAMES["indigo"])
        self._btn("v", _BLK_DN, NAMES["indigo"])
        self._btn("SAVE", _BLK_SAVE, NAMES["blue"])
        self._icon_btn("code", "CODE", _BLK_CODE, NAMES["dark_purple"])
        self._btn("CLOSE", _BLK_CLOSE, NAMES["dark_grey"])
        if self.blk_menu is not None:
            self._draw_blk_menu()
        if self.blk_kbd is not None:
            self._draw_blk_kbd()

    def _draw_blk_kbd(self):
        """Render whichever entry prompt is up: the number pad (kind == 'num') or the
        variable-name / text prompt. Indexed API + petme128 only (host == device)."""
        if self.blk_kbd.get("kind") == "num":
            self._draw_blk_num()
            return
        cv = self.canvas
        x, y, w, h = _BLK_KBD
        cv.rect(x, y, w, h, NAMES["dark_purple"])
        cv.rectb(x, y, w, h, NAMES["white"])
        is_text = self.blk_kbd.get("kind") == "text"
        cv.print("TYPE SOME TEXT" if is_text else "NAME YOUR VARIABLE",
                 x + 10, y + 8, NAMES["white"], 1)
        cv.print("type, then OK", x + 10, y + 18, NAMES["light_grey"], 1)
        # the live edit buffer in a field with a blinking-ish caret bar
        fx, fy, fw = x + 10, y + 30, w - 20
        cv.rect(fx, fy, fw, 14, NAMES["black"])
        cv.rectb(fx, fy, fw, 14, NAMES["light_grey"])
        txt = (self.blk_kbd.get("text") or "")[:24]
        if txt:
            cv.print(txt, fx + 4, fy + 3, NAMES["white"], 1)
        elif not is_text:
            # empty buffer: show the default name as a dim placeholder (OK keeps it)
            cv.print(str(self.blk_kbd.get("var", ""))[:24], fx + 4, fy + 3,
                     NAMES["dark_grey"], 1)
        cv.rect(fx + 4 + len(txt) * 8, fy + 3, 6, 8, NAMES["yellow"])   # caret
        self._btn("DEL", _BLK_KBD_DEL, NAMES["red"])
        self._btn("OK", _BLK_KBD_OK, NAMES["green"])
        self._btn("X", _BLK_KBD_X, NAMES["dark_grey"])

    def _draw_blk_num(self):
        """The number-entry pad: a live value field + an on-screen digit grid (tap a
        number in, or type it) + DEL/OK/X and a BLOCK swap for expr slots (#29)."""
        cv = self.canvas
        k = self.blk_kbd
        x, y, w, h = _BLK_NUM
        cv.rect(x, y, w, h, NAMES["dark_purple"])
        cv.rectb(x, y, w, h, NAMES["white"])
        cv.print("TYPE A NUMBER", x + 10, y + 6, NAMES["white"], 1)
        # live value field; an empty buffer shows the slot's current value, dim (OK keeps it)
        fx, fy, fw = x + 10, y + 18, w - 20
        cv.rect(fx, fy, fw, 14, NAMES["black"])
        cv.rectb(fx, fy, fw, 14, NAMES["light_grey"])
        txt = (k.get("text") or "")[:30]
        if txt:
            cv.print(txt, fx + 4, fy + 3, NAMES["white"], 1)
        else:
            cur = k.get("cur")
            ph = str(cur) if _blocks_mod.is_literal_value(cur) and cur is not None else "0"
            cv.print(ph[:30], fx + 4, fy + 3, NAMES["dark_grey"], 1)
        cv.rect(fx + 4 + len(txt) * 8, fy + 3, 6, 8, NAMES["yellow"])   # caret
        # the digit grid (0-9 . -)
        for idx in range(len(_BLK_NUM_KEYS)):
            r = idx // _BLK_NUM_BPR
            c = idx % _BLK_NUM_BPR
            rx = _BLK_NUM_GX + c * _BLK_NUM_BW
            ry = _BLK_NUM_GY + r * _BLK_NUM_BH
            self._btn(_BLK_NUM_KEYS[idx],
                      (rx, ry, _BLK_NUM_BW - 3, _BLK_NUM_BH - 3), NAMES["indigo"])
        self._btn("DEL", _BLK_NUM_DEL, NAMES["red"])
        if k.get("allow_block"):
            self._btn("BLOCK", _BLK_NUM_BLOCK, NAMES["green"])
        self._btn("OK", _BLK_NUM_OK, NAMES["green"])
        self._btn("X", _BLK_NUM_X, NAMES["dark_grey"])

    def _blk_hint(self):
        be = self.blocks_ed
        b = be.selected_block() if be is not None else None
        if b is not None:
            return _BLK_HINTS.get(b.get("t"))
        return None

    def _draw_blk_row(self, row, vi, is_cursor):
        cv = self.canvas
        be = self.blocks_ed
        y = _BLK_Y0 + vi * _BLK_ROW_H
        x = _BLK_X0 + row.depth * _BLK_INDENT
        w = _BLK_W - row.depth * _BLK_INDENT
        if row.kind == "insert":
            # an empty insert point: a slim dashed-looking `+` slot
            c = NAMES["yellow"] if is_cursor else NAMES["dark_grey"]
            cv.rectb(x, y + 2, w - 2, _BLK_ROW_H - 4, c)
            cv.print("+", x + 4, y + 4, c, 1)
            if is_cursor:
                cv.print("add a block", x + 16, y + 4, NAMES["light_grey"], 1)
            return
        b = row.block
        cat = self._blk_block_cat(b)
        fill = NAMES[_blocks_mod.CATEGORY_COLOR.get(cat, "dark_grey")]
        if row.is_else:
            fill = NAMES["orange"]
        cv.rect(x, y + 1, w - 2, _BLK_ROW_H - 2, fill)
        border = NAMES["white"] if is_cursor else NAMES["black"]
        cv.rectb(x, y + 1, w - 2, _BLK_ROW_H - 2, border)
        # readable text color over the block fill (light on dark, dark on light)
        fg = NAMES["white"] if cat in ("draw", "input", "variables", "control") \
            and not row.is_else else NAMES["black"]
        if row.is_else:
            fg = NAMES["black"]
        label = self._blk_row_text(b, row.is_else)
        cv.print(label[:(w - 8) // 8], x + 4, y + 4, fg, 1)
        # highlight the selected block's active slot with a small caret under it
        if is_cursor and not row.is_else:
            self._draw_blk_slot_caret(b, x, y)

    def _draw_blk_slot_caret(self, b, x, y):
        slots = self.blocks_ed.slots(b)
        if not slots:
            return
        # underline the active slot's value within the rendered label so the kid
        # sees which one A/left/right will edit.
        cv = self.canvas
        si = self.blk_slot % len(slots)
        col0 = self._blk_slot_text_col(b, si)
        if col0 is None:
            return
        sval = self._blk_slot_display(b, slots[si])
        cv.rect(x + 4 + col0 * 8, y + 12, max(8, len(sval) * 8), 1, NAMES["yellow"])

    def _blk_block_cat(self, b):
        d = _blocks_mod.block_def(b.get("t"))
        return d["category"] if d else "control"

    def _blk_row_text(self, b, is_else=False):
        """Render a block's inline label: the catalog template with each {slot}
        replaced by its value's compact display (a literal, a color/button name, a
        variable, or a compact expression). Mirrors the Scratch-style inline look."""
        if is_else or b.get("t") == _blocks_mod.ELSE_MARKER:
            return "else"
        d = _blocks_mod.block_def(b.get("t"))
        if d is None:
            return str(b.get("t"))
        out = ""
        tmpl = d["label"]
        i = 0
        n = len(tmpl)
        slot_by_name = {}
        for s in d["slots"]:
            slot_by_name[s["name"]] = s
        while i < n:
            ch = tmpl[i]
            if ch == "{":
                j = tmpl.find("}", i)
                if j < 0:
                    out += ch
                    i += 1
                    continue
                name = tmpl[i + 1:j]
                slot = slot_by_name.get(name)
                out += self._blk_slot_display(b, slot) if slot else name
                i = j + 1
            else:
                out += ch
                i += 1
        return out

    def _blk_slot_text_col(self, b, si):
        """The character column where slot `si`'s value starts in the rendered row
        label (so the caret underlines the right run). None if it can't be found."""
        d = _blocks_mod.block_def(b.get("t"))
        if d is None or si >= len(d["slots"]):
            return None
        target = d["slots"][si]["name"]
        tmpl = d["label"]
        col = 0
        i = 0
        n = len(tmpl)
        while i < n:
            ch = tmpl[i]
            if ch == "{":
                j = tmpl.find("}", i)
                if j < 0:
                    col += 1
                    i += 1
                    continue
                name = tmpl[i + 1:j]
                slot = None
                for s in d["slots"]:
                    if s["name"] == name:
                        slot = s
                        break
                disp = self._blk_slot_display(b, slot) if slot else name
                if name == target:
                    return col
                col += len(disp)
                i = j + 1
            else:
                col += 1
                i += 1
        return None

    def _blk_slot_display(self, b, slot):
        """A compact string for one slot's current value (for the inline row)."""
        name = slot["name"]
        val = (b.get("p", {}) or {}).get(name)
        t = slot["type"]
        if t == _blocks_mod.SLOT_EXPR:
            return self._blk_expr_display(val)
        if t == _blocks_mod.SLOT_TEXT:
            return '"' + str(val) + '"'
        if val is None:
            return "0" if t == _blocks_mod.SLOT_NUMBER else "?"
        return str(val)

    def _blk_expr_display(self, val):
        """A compact, brace-free rendering of an expression slot value: a literal
        stays itself; an expression block renders its own label recursively (so a
        nested `(x + 1)` reads inline)."""
        if val is None:
            return "0"
        if isinstance(val, dict):
            return self._blk_row_text(val)
        if isinstance(val, str):
            return '"' + val + '"'
        return str(val)

    def _draw_blk_menu(self):
        """The modal insert/picker menu over the frozen outline: a titled panel with
        a scrolling list of choices (categories, blocks, dropdown options, or
        variables). Navigated up/down + A; B backs out."""
        cv = self.canvas
        m = self.blk_menu
        mx, my, mw, mh = _BLK_MENU
        cv.rect(mx, my, mw, mh, NAMES["black"])
        cv.rectb(mx, my, mw, mh, NAMES["yellow"])
        titles = {"cat": "PICK A KIND", "blk": "PICK A BLOCK",
                  "dropdown": "PICK ONE", "variable": "PICK A VARIABLE",
                  "expr": "PICK A VALUE"}
        cv.print(titles.get(m["mode"], "PICK"), mx + 6, my + 4, NAMES["yellow"], 1)
        items = m["items"]
        if not items:
            cv.print("(nothing here)", mx + 8, my + 22, NAMES["light_grey"], 1)
            cv.print("B = back", mx + 8, my + mh - 12, NAMES["light_grey"], 1)
            return
        for vi in range(_BLK_MENU_ROWS):
            ridx = m["top"] + vi
            if ridx >= len(items):
                break
            y = my + 16 + vi * _BLK_MENU_ROW_H
            sel = ridx == m["sel"]
            if sel:
                cv.rect(mx + 3, y, mw - 6, _BLK_MENU_ROW_H - 1, NAMES["indigo"])
            # color the category/block swatch chips so the look matches the outline
            chip = self._blk_menu_chip(ridx)
            if chip is not None:
                cv.rect(mx + 5, y + 2, 8, _BLK_MENU_ROW_H - 5, chip)
            label = self._blk_menu_label(ridx)
            cv.print(label[:(mw - 24) // 8], mx + 16, y + 3,
                     NAMES["white"] if sel else NAMES["light_grey"], 1)
        cv.print("B = back", mx + 6, my + mh - 12, NAMES["light_grey"], 1)

    def _blk_menu_chip(self, ridx):
        """The category-color chip for menu row `ridx` (categories + block lists);
        None for plain pickers (dropdown/variable rows have no category color)."""
        m = self.blk_menu
        item = m["items"][ridx]
        if item == _NUM_LITERAL_ITEM:
            return NAMES["white"]      # the white editable-oval look (Scratch)
        if m["mode"] == "cat":
            return NAMES[_blocks_mod.CATEGORY_COLOR.get(item, "dark_grey")]
        if m["mode"] in ("blk", "expr"):
            d = _blocks_mod.block_def(item)
            if d:
                return NAMES[_blocks_mod.CATEGORY_COLOR.get(d["category"], "dark_grey")]
        return None
