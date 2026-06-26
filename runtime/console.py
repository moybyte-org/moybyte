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

from audio import AudioBank, AudioEngine
from editors import (BlockEditor, CodeEditor, MapEditor, PaintEditor,
                     SpriteSheet, TileMap)

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
# Desktop overlay button row (top edge): EDIT/CODE, PAINT, MAP, then HOME at the
# right. Four buttons now share the 320px row, so they're tighter than the old
# three -- each glyph-led button keeps a short label that fits its width.
_MENU_BTN = (4, 4, 64, 18)        # desktop overlay: open Make-it-mine / code
_PAINT_BTN = (72, 4, 64, 18)      # desktop overlay: open the paint editor
_MAP_BTN = (140, 4, 64, 18)       # desktop overlay: open the map (tilemap) editor
_BLOCKS_BTN = (208, 4, 42, 18)    # desktop overlay: open the block editor (#29)
_HOME_BTN = (252, 4, 64, 18)      # desktop overlay: back to launcher
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
# grid of tappable cart icons, a thin top status strip (clock + status pips), and
# a persistent bottom dock. Drawn with the indexed API + petme128 font + the
# _glyph vocabulary only, so host == device.
_STATUS_H = 14          # top status strip height (wallpaper shows through above icons)
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
# Home management actions (create / duplicate / delete) -- a small row of icon
# buttons tucked at the right of the status strip, only drawn when can_manage.
_NEW_BTN = (236, 1, 26, 12)
_DUP_BTN = (264, 1, 26, 12)
_DEL_BTN = (292, 1, 26, 12)
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
_MV_CELL = 14          # px per cell in the view (tappable, shows the 8x8 tile)
_MV_COLS = 13          # visible map columns
_MV_ROWS = 11          # visible map rows
_MV_AREA = (_MV_X0, _MV_Y0, _MV_COLS * _MV_CELL, _MV_ROWS * _MV_CELL)
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
# In-row slot editing: tapping/right-step on a selected block cycles to its NEXT
# editable slot; that slot is highlighted, and A opens its editor (number bump,
# variable/dropdown picker, expr -> a nested expression insert).
# Kid-facing category names for the insert menu (the catalog ids are terse keys).
_CAT_LABEL = {
    "events": "When...", "control": "Control", "draw": "Draw", "input": "Buttons",
    "variables": "Variables", "operators": "Math", "sound": "Sound",
}


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
}
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
        self.icon_w = _ICON_W * fs
        self.icon_h = _ICON_H * fs
        self.icon_gap_x = _ICON_GAP_X * fs
        self.icon_gap_y = _ICON_GAP_Y * fs
        self.icon_box = _ICON_BOX * fs
        self.icon_y0 = self.status_h + 8 * fs
        if self._base:
            self.cols, self.rows = _ICON_COLS, _ICON_ROWS
            self.icon_x0 = _ICON_X0
        else:
            self.cols = max(1, (self.w + self.icon_gap_x) //
                            (self.icon_w + self.icon_gap_x))
            band = self.dock_y - self.icon_y0
            self.rows = max(1, (band + self.icon_gap_y) //
                            (self.icon_h + self.icon_gap_y))
            grid_w = self.cols * self.icon_w + (self.cols - 1) * self.icon_gap_x
            self.icon_x0 = max(0, (self.w - grid_w) // 2)
        self.page = self.cols * self.rows

        # -- status strip glyph box + the selected-cart name slot ----------------
        self.status_gh = 12 * fs                     # pip/glyph box (scaled w/ font)
        if self._base:
            self.status_name_x = 78                  # frozen baseline (not 6*fw+6)
            self.status_name_maxc = 18
        else:
            self.status_name_x = 6 * self.font_w + 6
            self.status_name_maxc = max(
                4, (self.w - 7 * self.font_w - 4 * self.status_gh) // self.font_w)

        # -- management buttons (NEW/DUP/DEL), tucked at the strip's right end ---
        if self._base:
            self.new_btn, self.dup_btn, self.del_btn = _NEW_BTN, _DUP_BTN, _DEL_BTN
        else:
            bh = max(8, self.status_h - 2)
            bw = 3 * self.font_w + 2
            gap = 2 * fs
            x0 = self.w - 3 * bw - 2 * gap - 2
            self.new_btn = (x0, 1, bw, bh)
            self.dup_btn = (x0 + bw + gap, 1, bw, bh)
            self.del_btn = (x0 + 2 * (bw + gap), 1, bw, bh)

        # -- page chevrons (centered vertically in the icon band) ----------------
        if self._base:
            self.page_prev, self.page_next = _PAGE_PREV, _PAGE_NEXT
        else:
            cy = (self.icon_y0 + self.dock_y) // 2 - 12 * fs
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
        # The clock-text region on the status strip (Time Traveler egg, #21).
        return (0, 0, 40 if self._base else 5 * self.font_w, self.status_h)

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
        self.launcher = Launcher(carts if carts else [], self.layout)
        # Screen states (#28): "launcher" is now the DESKTOP home (wallpaper + cart
        # icon grid + dock); "desktop" is a running cart; "menu" is the cards/code/
        # paint/map editors; "settings" is the Settings app.
        self.screen = "launcher"      # "launcher" | "desktop" | "menu" | "settings"
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
        # Block editor (#29 Part 2): a BlockEditor over the cart's block program +
        # the structured-outline UI state. `blocks_ed` is built lazily on first open.
        self.blocks_ed = None         # BlockEditor while menu_view == "blocks"
        self.blk_top = 0              # first outline row scrolled into view
        self.blk_slot = 0             # which slot of the selected block is highlighted
        self.blk_menu = None          # active insert menu state dict, or None
        self.blk_status = None        # last block-editor SAVE result text
        self.keyboard = None          # set by run_desktop (for raw/text mode toggle)
        self._ekey_prev = 0           # last consumed keyboard byte (edge detect)
        self._drag = None             # last pointer pos during a code-view drag-scroll
        self._paint_drag = None       # last painted grid cell during a paint drag (#30)
        self._map_drag = None         # last stamped map cell during a map drag (#30)
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
        self.set_msel = 0             # selected row in the Settings screen
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
        if not (self.carts_store is not None and self.carts_root is not None
                and self.can_manage):
            return
        try:
            self._with_sd(lambda: self.carts_store.save_system(self.system, self.carts_root))
        except Exception as exc:  # noqa: BLE001 -- a failed write just isn't remembered
            print("KidCode system save failed:", _err_text(exc))

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
        backdrop. Guarded so a misbehaving wallpaper degrades to a fill."""
        if self._wp_draw is not None:
            try:
                if self._wp_live and self._wp_update is not None and dt > 0:
                    self._wp_update(dt)
                self._wp_draw()
                # Clear any camera/clip/pal/palt (#11) the wallpaper cart set, so the
                # home/settings foreground (icons, status strip, dock) draws clean.
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
        ("theme", "THEME", "mock-choice"),
    )
    _MOCK_THEMES = ("day", "night", "candy")
    _MOCK_NAMES = ("ALEX", "SAM", "KIT", "RAE")

    def open_settings(self):
        self.set_msel = 0
        self.screen = "settings"
        self.show_achievements = False
        self._secret_taps = 0              # fresh secret-door run each visit (#21)
        self._set_text_mode(False)

    def settings_adjust(self, d):
        """Step the selected Settings row by d. Wallpaper applies + persists; the
        mock rows just move a cosmetic value held in self.system (not acted on)."""
        key = self._SETTINGS_ROWS[self.set_msel][0]
        if key == "wallpaper":
            self.cycle_wallpaper(d)
            return
        if key == "font_scale":                 # system-UI font size (#39): live + persisted
            self.cycle_font_scale(d)
            return
        if key == "theme":
            cur = self.system.get("theme", self._MOCK_THEMES[0])
            i = self._MOCK_THEMES.index(cur) if cur in self._MOCK_THEMES else 0
            self.system["theme"] = self._MOCK_THEMES[(i + d) % len(self._MOCK_THEMES)]
        elif key == "name":
            cur = self.system.get("name", self._MOCK_NAMES[0])
            i = self._MOCK_NAMES.index(cur) if cur in self._MOCK_NAMES else 0
            self.system["name"] = self._MOCK_NAMES[(i + d) % len(self._MOCK_NAMES)]
        else:  # mock-gauge (volume / brightness): a 0..5 placeholder
            v = int(self.system.get(key, 3)) + d
            self.system[key] = max(0, min(5, v))

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

    def _start(self):
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
        self.blocks_ed = None
        self.blk_menu = None
        self.cart_error = None
        self.save_status = None
        self.sheet = self._build_sheet()
        self.tilemap = self._build_tilemap()
        self.pmem = self._build_pmem()
        self._cart_key_prev = 0       # fresh cart: no stale key edge
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
                self.blocks_ed = BlockEditor(_blocks_mod, prog)
                self.blk_top = 0
                self.blk_slot = 0
                self.blk_menu = None
                self.blk_status = None
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
        kb = self.keyboard
        if kb is not None:
            kb.set_game_mode(not on)

    def _open_menu(self):
        self.screen = "menu"
        # Carts with a Make-it-mine schema open to cards; others go straight to
        # the code editor (there are no cards to show).
        self.set_menu_view("cards" if self.cart.get("edit") else "code")

    def _open_paint(self):
        self.screen = "menu"
        self.paint_status = None
        self.set_menu_view("paint")

    def _open_map(self):
        self.screen = "menu"
        self.save_status = None
        self.map_erase = False
        self.set_menu_view("map")

    def _open_blocks(self):
        self.screen = "menu"
        self.blk_status = None
        self.set_menu_view("blocks")

    def _leave_menu(self):
        self._set_text_mode(False)
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
        self._set_text_mode(False)    # restore the game-button keyboard mode
        self.editor = None
        self.paint = None
        self.mapedit = None
        self.blocks_ed = None
        self.blk_menu = None
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
            if i.pressed("up"):
                self.set_msel = (self.set_msel - 1) % len(self._SETTINGS_ROWS)
            if i.pressed("down"):
                self.set_msel = (self.set_msel + 1) % len(self._SETTINGS_ROWS)
            if i.pressed("left"):
                self.settings_adjust(-1)
            if i.pressed("right"):
                self.settings_adjust(1)
            if i.pressed("b") or i.pressed("home") or i.pressed("stop"):
                self.go_home()
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
        # Desktop home (#28): a tap on a cart icon opens it; the dock + management
        # row + page chevrons fire on the press edge. There's no list drag anymore --
        # the grid pages instead. Trackball hover still previews the icon under it.
        if click:
            # Clock Easter egg (#21): tapping the status-strip clock _CLOCK_TAP_GOAL
            # times wakes the Time Traveler. Checked before the management row so a
            # tap on the clock never falls through to a button.
            lay = self.layout
            if _in(px, py, lay.clock_hit()):
                self._tap_clock()
                return
            self._clock_taps = 0                # any other desktop tap resets the run
            slot = self._dock_slot_at(px, py)
            if slot is not None:
                self._activate_dock(slot)
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

    def _settings_row_rect(self, i):
        return self.layout.settings_row_rect(i)

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
            self.go_home()
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
        for i in range(len(self._SETTINGS_ROWS)):
            x, y, w, h = self._settings_row_rect(i)
            if _in(px, py, (x, y, w, h)):
                self.set_msel = i
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
        self.input.game_pointer = (gx, gy, click, p.down)
        if self.screen == "launcher":
            self._launcher_pointer(px, py, click)
        elif self.screen == "settings":
            self._settings_pointer(px, py, click)
        elif self.screen == "desktop":
            px, py = gx, gy
            # While a cart runs the TOP-BAR overlay (EDIT/CODE, PAINT, MAP, HOME) is
            # the TIC-80 one-tap tool switcher -- it occludes only 22px at the top so
            # gameplay keeps the rest of the screen (a bottom dock would cover the
            # play area). The bottom dock is the home/settings chrome.
            if click:
                if _in(px, py, _MENU_BTN):
                    self._open_menu()
                elif _in(px, py, _PAINT_BTN):
                    self._open_paint()
                elif _in(px, py, _MAP_BTN):
                    self._open_map()
                elif _in(px, py, _BLOCKS_BTN):
                    self._open_blocks()
                elif _in(px, py, _HOME_BTN):
                    self.go_home()
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
            if self.menu_view == "paint":
                # A tap (click) routes through _paint_click (grid OR buttons). A
                # held drag with no fresh click keeps painting the grid stroke so
                # press-and-move draws a continuous line -- the same path for a host
                # mouse drag and a device touch drag (both = pointer.down + moving
                # position). Releasing resets the stroke origin (#30).
                if click:
                    self._paint_click(px, py)
                elif p.down:
                    self._paint_stroke(px, py)
                else:
                    self._paint_drag = None
                return
            if self.menu_view == "map":
                if click:
                    self._map_click(px, py)
                elif p.down:
                    self._map_stroke(px, py)   # drag to stamp/erase tiles (#30)
                else:
                    self._map_drag = None
                return
            if self.menu_view == "blocks":
                self._blocks_pointer(px, py, click)   # outline + insert menu (#29)
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
        elif _in(px, py, _PAINT_GET):          # import the tile from the shared sheet
            self.share_tile_get()
        elif _in(px, py, _PAINT_PUT):          # save the tile to the shared sheet
            self.share_tile_put()
        elif _in(px, py, _PAINT_SAVE):
            self.save_sprites()
        elif _in(px, py, _PAINT_CLOSE):
            self._leave_menu()

    def _map_palette_ids(self):
        """The tile ids shown on the current palette page (a window into the sheet,
        clamped so the last page never runs past the sheet's tile count)."""
        if self.sheet is None:
            return []
        count = self.sheet.count
        start = self.map_page
        return list(range(start, min(start + _TP_PAGE, count)))

    def _map_stroke(self, px, py):
        """Drag-to-stamp on the map view (#30): stamp/erase the map cell under the
        pointer and fill the line from the last stamped cell, so dragging lays a
        continuous run of tiles. Returns True if a map cell was touched. Shares the
        stroke model with the paint editor (host mouse == device touch)."""
        me = self.mapedit
        if me is None or not _in(px, py, _MV_AREA):
            self._map_drag = None
            return False
        cx = me.cam_x + (px - _MV_X0) // _MV_CELL
        cy = me.cam_y + (py - _MV_Y0) // _MV_CELL
        last = self._map_drag
        cells = ([(cx, cy)] if last is None
                 else _line_cells(last[0], last[1], cx, cy))
        for mx, my in cells:
            if self.map_erase:
                me.erase(mx, my)
            else:
                me.place(mx, my)
        self._map_drag = (cx, cy)
        return True

    def _map_click(self, px, py):
        me = self.mapedit
        if me is None:
            return
        if self._map_stroke(px, py):           # stamp/erase a cell in the map view
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
        elif _in(px, py, _PAN_UP):
            me.pan(0, -1)
        elif _in(px, py, _PAN_DN):
            me.pan(0, 1)
        elif _in(px, py, _PAN_LF):
            me.pan(-1, 0)
        elif _in(px, py, _PAN_RT):
            me.pan(1, 0)
        elif _in(px, py, _MAP_ERASE):          # toggle stamp <-> erase
            self.map_erase = not self.map_erase
        elif _in(px, py, _MAP_SAVE):
            self.save_map()
        elif _in(px, py, _MAP_CLOSE):
            self._leave_menu()

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
        if m["mode"] == "cat":
            return _CAT_LABEL.get(item, item).upper()
        if m["mode"] == "blk":
            d = _blocks_mod.block_def(item)
            return _blk_plain_label(d["label"]) if d else item
        if m["mode"] in ("dropdown", "variable"):
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
        if m["mode"] == "cat":
            self._blk_open_blocks(item)
        elif m["mode"] == "blk":
            self._blk_insert_chosen(item)
        elif m["mode"] == "dropdown":
            self.blocks_ed.set_slot(m["slot"], item, m["block"])
            self.blk_menu = None
        elif m["mode"] == "variable":
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
        """Open the right editor for a slot's type: number/text bump inline (or via
        the keyboard pad), variable + dropdown open a picker, expr opens a nested
        expression-block insert menu."""
        be = self.blocks_ed
        t = slot["type"]
        name = slot["name"]
        if t == _blocks_mod.SLOT_DROPDOWN:
            # A dropdown opens its option list (16 colors is a lot to cycle through);
            # left/right still cycle it in place for a quick one-step tweak.
            self._blk_open_dropdown_picker(block, slot)
        elif t == _blocks_mod.SLOT_NUMBER:
            self._blk_bump_number(block, name, 1)
        elif t == _blocks_mod.SLOT_TEXT:
            cur = str((block.get("p", {}) or {}).get(name, ""))
            be.set_slot(name, cur + "!", block)   # placeholder bump; keyboard refines
        elif t == _blocks_mod.SLOT_VARIABLE:
            self._blk_open_variable_picker(block, name)
        elif t == _blocks_mod.SLOT_EXPR:
            self._blk_open_expr_menu(block, name)

    def _blk_bump_number(self, block, name, d):
        be = self.blocks_ed
        cur = (block.get("p", {}) or {}).get(name, 0)
        try:
            val = int(cur) + d
        except (TypeError, ValueError):
            val = d
        be.set_slot(name, val, block)

    def _blk_open_variable_picker(self, block, name):
        be = self.blocks_ed
        items = be.variables()
        if not items:
            # No variables yet: auto-declare friendly defaults so a slot is fillable
            # even before the kid names one (they can rename later).
            be.add_var("score")
            items = be.variables()
        self.blk_menu = {"mode": "variable", "sel": 0, "top": 0, "items": items,
                         "block": block, "slot": name}

    def _blk_open_dropdown_picker(self, block, slot):
        opts = _blocks_mod.slot_options(slot)
        self.blk_menu = {"mode": "dropdown", "sel": 0, "top": 0, "items": opts,
                         "block": block, "slot": slot["name"]}

    def _blk_open_expr_menu(self, block, name):
        """Open the expression chooser for an expr slot: the operator / input /
        variable reporter blocks (everything with an `expr` shape), plus a couple of
        literal choices. Selecting one writes a nested expression block into the
        slot."""
        ids = []
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

    def _blocks_pointer(self, px, py, click):
        if not click:
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

    def frame(self, dt):
        if dt > 0:
            inst = 1.0 / dt
            # EMA so the readout reflects sustained rate, not single-frame jitter.
            self._fps = inst if self._fps <= 0 else self._fps + (inst - self._fps) * 0.15
        if self.screen == "launcher":
            self._draw_desktop_home(dt)
        elif self.screen == "settings":
            self._draw_settings(dt)
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
                    if self._update:
                        self._update(dt)
                    if self._draw:
                        self._draw()
                    if self.audio is not None:
                        self.audio.tick(dt)      # advance/feed playback (#16)
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
            # Clear any cart-set camera/clip/pal/palt (#11) before the console paints
            # its own UI overlays, so they're never offset/clipped/recoloured.
            self._reset_canvas_state()
            if self.cart_error is not None:
                self._draw_error_panel()
            self._draw_desktop_buttons()
        elif self.menu_view == "code":
            self._draw_code()              # full-screen editor (covers the cart)
        elif self.menu_view == "blocks":
            self._draw_blocks()            # full-screen structured outline (#29)
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
        self._draw_cursor()
        self.comp.flush()

    # -- desktop shell drawing (#28) -----------------------------------------

    def _draw_desktop_home(self, dt):
        """The home desktop: wallpaper backdrop -> cart icon grid -> top status
        strip -> bottom dock. The wallpaper is drawn first and the rest layer over
        it, exactly the Picotron model (wallpaper shows through the chrome). All on
        the SYSTEM canvas, reflowed to its size + font scale (#39)."""
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
        self._draw_dock("home")

    def _draw_status_strip(self, where):
        """The thin top status strip: a clock, the selected cart's name (home) or
        title (settings), and battery/wifi pips. Translucency isn't available on the
        indexed canvas, so it's a slim dark bar that the wallpaper meets just below.
        On the SYSTEM canvas; height + text follow the layout/font scale (#39)."""
        cv = self.sys_canvas
        lay = self.layout
        gh = lay.status_gh                           # glyph box scaled with the font
        cv.rect(0, 0, cv.w, lay.status_h, NAMES["black"])
        cv.print(self._clock_text(), 2, 3, NAMES["light_grey"], 1)
        if where == "home":
            sel = self.launcher.selected()
            if sel is not None:
                name = sel["title"]
                if len(name) > lay.status_name_maxc:
                    name = name[:lay.status_name_maxc]
                cv.print(name, lay.status_name_x, 3, NAMES["white"], 1)
        # battery + wifi pips (placeholders): a wifi glyph + a battery glyph.
        self._glyph("wifi", (cv.w - 2 * gh, 1, gh, gh), NAMES["green"], cv)
        self._glyph("batt", (cv.w - gh, 1, gh, gh), NAMES["green"], cv)
        # Home management actions tuck into the strip (only when writes are enabled).
        if where == "home" and self.can_manage:
            self._mini_btn("NEW", lay.new_btn, NAMES["green"], cv)
            self._mini_btn("DUP", lay.dup_btn, NAMES["blue"], cv)
            self._mini_btn("DEL", lay.del_btn, NAMES["red"], cv)

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
        for i in range(len(self._SETTINGS_ROWS)):
            self._draw_settings_row(i)
        self._draw_status_strip("settings")
        self._draw_dock("settings")

    def _draw_settings_row(self, i):
        cv = self.sys_canvas
        lay = self.layout
        fw = lay.font_w
        key, label, kind = self._SETTINGS_ROWS[i]
        x, y, w, h = self._settings_row_rect(i)
        sel = (i == self.set_msel)
        if sel:
            cv.rect(x, y, w, h, NAMES["indigo"])
        fg = NAMES["white"] if sel else NAMES["light_grey"]
        cv.print(label, x + 4, y + 5, fg, 1)
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
        else:  # mock-choice (theme)
            cv.print(str(self.system.get("theme", self._MOCK_THEMES[0])).upper()[:8], vx,
                     y + 5, NAMES["peach"], 1)
        # Mark not-yet-functional rows clearly (wallpaper + font are FUNCTIONAL).
        if kind not in ("wallpaper", "font"):
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

    def _draw_desktop_buttons(self):
        # Carts with a Make-it-mine schema open the cards menu (pencil = EDIT); the
        # rest jump straight to the code editor (same glyph -- both are "change me").
        # (cart may be None defensively if an error panel is up with no open cart.)
        has_edit = bool(self.cart.get("edit")) if self.cart else False
        self._icon_btn("edit", "EDIT" if has_edit else "CODE", _MENU_BTN, NAMES["dark_purple"])
        self._icon_btn("paint", "PAINT", _PAINT_BTN, NAMES["orange"])
        self._icon_btn("map", "MAP", _MAP_BTN, NAMES["green"])
        self._icon_btn("blocks", "", _BLOCKS_BTN, NAMES["pink"])   # open the block editor (#29)
        self._icon_btn("home", "HOME", _HOME_BTN, NAMES["dark_grey"])

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

    def _draw_paint(self):
        cv = self.canvas
        pe = self.paint
        sheet = self.sheet
        cv.rect(8, 16, 304, 204, NAMES["black"])
        cv.rectb(8, 16, 304, 204, NAMES["orange"])
        title = "PAINT  SPR " + str(pe.n if pe else 0)
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
        # Cross-cart sprite reuse (#18): GET pulls this tile out of the shared
        # sheet, PUT pushes it in. A small status line shows the last result.
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
        title = "MAP  TILE " + str(me.n if me else 0)
        if self.tilemap is not None and self.tilemap.dirty:
            title = title + " *"
        cv.print(title, 14, 18, NAMES["green"], 1)
        if me is None or sheet is None or self.tilemap is None:
            return
        tm = self.tilemap
        # Visible map region: each cell is the 8x8 sprite tile placed there, centered
        # in an _MV_CELL box, with grid lines so empty cells read as empty. Tile
        # images are cached by id within the draw so a repeated tile builds once.
        cache = {}
        off = (_MV_CELL - sheet.TILE) // 2
        for ry in range(_MV_ROWS):
            cy = me.cam_y + ry
            for rx in range(_MV_COLS):
                cx = me.cam_x + rx
                x = _MV_X0 + rx * _MV_CELL
                y = _MV_Y0 + ry * _MV_CELL
                inb = (cx < tm.w and cy < tm.h)
                cv.rect(x, y, _MV_CELL, _MV_CELL,
                        NAMES["dark_blue"] if inb else NAMES["black"])
                if inb:
                    tid = tm.mget(cx, cy)
                    if tid >= 0:
                        img = cache.get(tid)
                        if img is None:
                            img = sheet.tile_image(tid, -1)
                            cache[tid] = img if img is not None else False
                        if img:
                            cv.spr(img, x + off, y + off, 1)
                cv.rectb(x, y, _MV_CELL, _MV_CELL, NAMES["dark_grey"])
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
        # Pan d-pad under the map view.
        self._btn("^", _PAN_UP, NAMES["indigo"])
        self._btn("v", _PAN_DN, NAMES["indigo"])
        self._btn("<", _PAN_LF, NAMES["indigo"])
        self._btn(">", _PAN_RT, NAMES["indigo"])
        # ERASE toggle (highlighted when active) + SAVE + CLOSE.
        self._btn("ER", _MAP_ERASE, NAMES["red"] if self.map_erase else NAMES["dark_grey"])
        self._btn("SAVE", _MAP_SAVE, NAMES["green"])
        self._btn("CLOSE", _MAP_CLOSE, NAMES["red"])

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
        if m["mode"] == "cat":
            return NAMES[_blocks_mod.CATEGORY_COLOR.get(item, "dark_grey")]
        if m["mode"] in ("blk", "expr"):
            d = _blocks_mod.block_def(item)
            if d:
                return NAMES[_blocks_mod.CATEGORY_COLOR.get(d["category"], "dark_grey")]
        return None
