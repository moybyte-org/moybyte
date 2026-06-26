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
from editors import CodeEditor, MapEditor, PaintEditor, SpriteSheet, TileMap


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
    "print", "btn", "btnp", "touch", "cfg", "col", "rnd", "flr", "abs", "min",
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
# Launcher action bar (pointer): create / duplicate / delete a cartridge.
_NEW_BTN = (12, 206, 92, 28)
_DUP_BTN = (114, 206, 92, 28)
_DEL_BTN = (216, 206, 92, 28)
# Launcher scrolling (#1). The tile strip spans TILE_Y0..(below the last tile);
# a finger held in the top/bottom EDGE band autoscrolls toward the off-screen
# rows, and dragging anywhere in the strip pans it by whole tiles.
_LIST_Y0 = 36           # == Launcher.TILE_Y0 (defined below; kept in sync)
_LIST_BOTTOM = 200      # tiles end above the action bar (y=206)
_LIST_EDGE = 28         # px band at top/bottom of the strip that autoscrolls
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
# Paint editor (#4): zoomed 8x8 grid + 16-color palette (2x8) + sprite selector.
# (_PAINT_BTN -- the desktop overlay button -- lives in the button row above.)
_PG_X0 = 14
_PG_Y0 = 32
_PG_CELL = 18
_PG_AREA = (_PG_X0, _PG_Y0, 8 * _PG_CELL, 8 * _PG_CELL)
_SW_X0 = 170
_SW_Y0 = 32
_SW = 18
_SW_COLS = 2
_SW_AREA = (_SW_X0, _SW_Y0, _SW_COLS * _SW, (16 // _SW_COLS) * _SW)
_SPR_PREV = (214, 40, 40, 24)
_SPR_NEXT = (262, 40, 40, 24)
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
# Trackball cursor sensitivity (#2). _CURSOR_BASE is the per-pulse step; the
# quadratic _CURSOR_ACCEL term adds light acceleration so a fast roll crosses the
# 320px screen in far fewer pulses while a slow, single-pulse roll stays precise.
# These are a FEEL tweak meant to be finalized on real hardware (the trackball's
# pulses-per-revolution sets the true "rolls to cross").  Before: BASE=4, ACCEL=1
# (1 pulse -> 5px, ~64 px/s at a steady 1 pulse/frame). After: BASE=7, ACCEL=2
# (1 pulse -> 9px; a 6-pulse flick -> 6*7 + 2*36 = 114px, so ~3 brisk rolls cross).
_CURSOR_BASE = 7
_CURSOR_ACCEL = 2

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
}


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



class Launcher:
    TILE_Y0 = 36
    TILE_H = 34
    TILE_PITCH = 40
    VISIBLE = 4

    def __init__(self, items):
        self.items = items
        self.sel = 0
        self.top = 0

    def move(self, d):
        n = len(self.items)
        if n:
            self.sel = (self.sel + d) % n
            self._scroll()

    def _scroll(self):
        if self.sel < self.top:
            self.top = self.sel
        elif self.sel >= self.top + self.VISIBLE:
            self.top = self.sel - self.VISIBLE + 1
        self._clamp_top()

    def max_top(self):
        # Topmost index that still fills the visible window (0 when everything fits).
        return max(0, len(self.items) - self.VISIBLE)

    def _clamp_top(self):
        self.top = max(0, min(self.max_top(), self.top))

    def scroll(self, d):
        # Pan the visible window by d rows (touch drag / autoscroll), clamped so the
        # last row never scrolls past the bottom. Independent of `sel` -- this just
        # moves which slice of the list is on screen.
        self.top = max(0, min(self.max_top(), self.top + d))

    def selected(self):
        return self.items[self.sel] if self.items else None

    def _visible(self):
        return range(self.top, min(len(self.items), self.top + self.VISIBLE))

    def tile_rect(self, i):
        if i < self.top or i >= self.top + self.VISIBLE:
            return None
        return (10, self.TILE_Y0 + (i - self.top) * self.TILE_PITCH, 300, self.TILE_H)

    def tile_at(self, px, py):
        for i in self._visible():
            r = self.tile_rect(i)
            if r and _in(px, py, r):
                return i
        return None

    def draw(self, cv):
        cv.cls(NAMES["dark_blue"])
        cv.print("CARTRIDGES", 12, 8, NAMES["white"], 2)
        for i in self._visible():
            x, y, w, h = self.tile_rect(i)
            it = self.items[i]
            sel = (i == self.sel)
            cv.rect(x, y, w, h, NAMES["dark_purple"] if sel else NAMES["black"])
            cv.rectb(x, y, w, h, NAMES["yellow"] if sel else NAMES["dark_grey"])
            cv.rect(x + 6, y + 6, 10, h - 12, _TYPE_COLOR.get(it["type"], NAMES["indigo"]))
            cv.print(it["title"], x + 24, y + 5, NAMES["white"], 2)
            cv.print(it["type"].upper(), x + 24, y + 19, NAMES["peach"], 2)
        if self.top > 0:
            cv.print("^", 300, self.TILE_Y0, NAMES["light_grey"], 2)
        if self.top + self.VISIBLE < len(self.items):
            cv.print("v", 300, self.TILE_Y0 + (self.VISIBLE - 1) * self.TILE_PITCH, NAMES["light_grey"], 2)


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
    def __init__(self, comp, canvas, input, carts=None):
        self.comp = comp
        self.canvas = canvas
        self.input = input
        self.make_api = None       # injected: make_api(canvas, input, cfg, sheet, audio, tilemap)->ns
        self.make_audio = None      # injected: make_audio(engine)->audio backend (host/device)
        self.audio = None           # the per-cart audio backend (built on open, #16)
        self.carts_store = None     # injected: cart store module (kid_carts API)
        self.launcher = Launcher(carts if carts else [])
        self.screen = "launcher"      # "launcher" | "desktop" | "menu"
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
        self.paint = None             # PaintEditor while menu_view == "paint"
        self.mapedit = None           # MapEditor while menu_view == "map" (#32)
        self.map_erase = False        # map editor: tap-to-erase instead of stamp
        self.map_page = 0             # map editor: first tile id shown in the palette
        self.keyboard = None          # set by run_desktop (for raw/text mode toggle)
        self._ekey_prev = 0           # last consumed keyboard byte (edge detect)
        self._drag = None             # last pointer pos during a code-view drag-scroll
        self._ldrag = None            # launcher drag state [press_y, last_y, moved?]
        self._autoscroll = 0          # frames a finger has dwelled in a launcher edge
        self._lhover = (-1, -1)       # last cursor pos used for launcher hover-highlight
        self.pointer = None           # set by run_desktop
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

    def _start(self):
        self._build_audio()
        ns = self.make_api(self.canvas, self.input, self.config, self.sheet,
                           self.audio, self.tilemap)
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
        self.cart_error = None
        self.save_status = None
        self.sheet = self._build_sheet()
        self.tilemap = self._build_tilemap()
        self.menu_view = "cards"
        self._set_text_mode(False)
        # Open to the desktop even if the cart failed to start: frame() shows the
        # error panel there and the EDIT/CODE button stays reachable so the kid can
        # fix it (a silent stay-on-launcher would be a dead end on the device).
        self._start()
        self.screen = "desktop"

    def _build_sheet(self):
        hexs = self.cart.get("sprites") if self.cart else None
        if hexs:
            try:
                return SpriteSheet.from_hex(hexs)
            except Exception:  # noqa: BLE001
                pass
        return SpriteSheet()

    def _build_tilemap(self):
        """Build the open cart's TileMap from its map.kmap blob (#32), or an empty
        map when the cart has none -- the mirror of _build_sheet, so map()/mget()/
        mset() are always callable (an empty map just blits nothing)."""
        blob = self.cart.get("map") if self.cart else None
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
        self._set_text_mode(view == "code")

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
        self.screen = "launcher"
        self.cart = None
        self.ns = None
        self.cart_error = None
        self.save_status = None

    # -- cart management (SD) ------------------------------------------------
    #
    # Each action mounts the SD card, mutates, and re-scans within a single
    # _with_sd session, then the card is unmounted before the next flush.

    def _apply_items(self, items):
        if items:
            self.launcher.items = items
            if self.launcher.sel >= len(items):
                self.launcher.sel = len(items) - 1
            self.launcher._scroll()

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
            if i.pressed("up") or i.pressed("left"):
                self.launcher.move(-1)
            if i.pressed("down") or i.pressed("right"):
                self.launcher.move(1)
            if i.pressed("a") or i.pressed("run"):
                self.open()
        elif self.screen == "desktop":
            if i.pressed("home") or i.pressed("stop"):
                self.go_home()
            elif i.pressed("b"):
                self._open_menu()
        elif self.screen == "menu":
            if self.menu_view == "code":
                self._editor_input()           # keyboard is in text mode here
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

    def _launcher_pointer(self, px, py, click):
        # The launcher cart list scrolls by touch (#1): drag the strip to pan it,
        # or dwell a held finger in the top/bottom edge band to autoscroll. A plain
        # tap (press + release with no drag) opens the tile under the finger.
        down = self.pointer.down
        in_strip = _LIST_Y0 <= py < _LIST_BOTTOM and 10 <= px < 310

        # Action-bar buttons fire on the press edge (they sit below the strip).
        if click:
            if self.can_manage and _in(px, py, _NEW_BTN):
                self.new_cart(); self._end_launcher_drag(); return
            if self.can_manage and _in(px, py, _DUP_BTN):
                self.dup_cart(); self._end_launcher_drag(); return
            if self.can_manage and _in(px, py, _DEL_BTN):
                self.del_cart(); self._end_launcher_drag(); return
            # A trackball click (cursor click, no finger down) opens the tile under
            # it. Touch taps open on release instead (so a drag can scroll first).
            if not down:
                i = self.launcher.tile_at(px, py)
                if i is not None:
                    self.launcher.sel = i
                    self._end_launcher_drag()
                    self.open()
                    return

        if down:
            if self._ldrag is None:                 # finger just went down in/at the strip
                self._ldrag = [py, py, False]       # [press_y, last_y, moved?]
                self._autoscroll = 0
            press_y, last_y, moved = self._ldrag
            # Drag: pan by whole tiles as the finger crosses each tile pitch.
            # Truncate toward zero (NOT floor) so up and down are symmetric -- a
            # plain floor would turn a 1px DOWN move into a whole-row scroll
            # (-1 // 40 == -1) and mis-open the wrong cart (#2).
            d = last_y - py
            pitch = self.launcher.TILE_PITCH
            steps = d // pitch if d >= 0 else -((-d) // pitch)
            if steps:
                self.launcher.scroll(steps)
                last_y = last_y - steps * pitch
            if abs(py - press_y) > 4:
                moved = True
            self._ldrag = [press_y, last_y, moved]
            # Autoscroll while dwelling in an edge band -- but ONLY once the gesture
            # is an actual drag (`moved`). The bands overlap the first/last tile, so
            # autoscrolling on a HELD-still tap would slide a different row under the
            # finger and open the wrong cart on release (#1).
            if moved and in_strip and py < _LIST_Y0 + _LIST_EDGE:
                self._autoscroll += 1
                if self._autoscroll % 6 == 0:
                    self.launcher.scroll(-1)
            elif moved and in_strip and py >= _LIST_BOTTOM - _LIST_EDGE:
                self._autoscroll += 1
                if self._autoscroll % 6 == 0:
                    self.launcher.scroll(1)
            else:
                self._autoscroll = 0
            # Hover-highlight the tile under a still finger (suppressed once dragging).
            if not moved:
                i = self.launcher.tile_at(px, py)
                if i is not None:
                    self.launcher.sel = i
        else:
            # Finger lifted: a tap that never became a drag opens the tile it was on.
            if self._ldrag is not None and not self._ldrag[2]:
                i = self.launcher.tile_at(px, py)
                if i is not None:
                    self.launcher.sel = i
                    self.open()
                self._end_launcher_drag()
            else:
                self._end_launcher_drag()
                # Trackball cursor hover (no touch): highlight the tile the cursor
                # MOVED onto. Only re-highlight when the cursor actually moved, so a
                # parked cursor sitting on a tile doesn't fight keyboard up/down nav.
                if (px, py) != self._lhover:
                    self._lhover = (px, py)
                    i = self.launcher.tile_at(px, py)
                    if i is not None:
                        self.launcher.sel = i

    def _end_launcher_drag(self):
        self._ldrag = None
        self._autoscroll = 0

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
        if self.screen == "launcher":
            self._launcher_pointer(px, py, click)
        elif self.screen == "desktop":
            if click:
                if _in(px, py, _MENU_BTN):
                    self._open_menu()
                elif _in(px, py, _PAINT_BTN):
                    self._open_paint()
                elif _in(px, py, _MAP_BTN):
                    self._open_map()
                elif _in(px, py, _HOME_BTN):
                    self.go_home()
        elif self.screen == "menu":
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
                if click:
                    self._paint_click(px, py)
                return
            if self.menu_view == "map":
                if click:
                    self._map_click(px, py)
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

    def _paint_click(self, px, py):
        pe = self.paint
        if pe is None:
            return
        if _in(px, py, _PG_AREA):              # paint a pixel in the zoomed grid
            lx = (px - _PG_X0) // _PG_CELL
            ly = (py - _PG_Y0) // _PG_CELL
            if 0 <= lx < 8 and 0 <= ly < 8:
                pe.paint(lx, ly)
        elif _in(px, py, _SW_AREA):            # pick a palette color
            idx = ((py - _SW_Y0) // _SW) * _SW_COLS + ((px - _SW_X0) // _SW)
            if 0 <= idx < 16:
                pe.color = idx
        elif _in(px, py, _SPR_PREV):
            pe.select(-1)
        elif _in(px, py, _SPR_NEXT):
            pe.select(1)
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

    def _map_click(self, px, py):
        me = self.mapedit
        if me is None:
            return
        if _in(px, py, _MV_AREA):              # stamp/erase a cell in the map view
            cx = me.cam_x + (px - _MV_X0) // _MV_CELL
            cy = me.cam_y + (py - _MV_Y0) // _MV_CELL
            if self.map_erase:
                me.erase(cx, cy)
            else:
                me.place(cx, cy)
        elif _in(px, py, _TP_AREA):            # pick the brush tile from the palette
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

    # -- frame + drawing -----------------------------------------------------

    def frame(self, dt):
        if dt > 0:
            inst = 1.0 / dt
            # EMA so the readout reflects sustained rate, not single-frame jitter.
            self._fps = inst if self._fps <= 0 else self._fps + (inst - self._fps) * 0.15
        if self.screen == "launcher":
            self.launcher.draw(self.canvas)
            if self.can_manage:
                self._btn("NEW", _NEW_BTN, NAMES["green"])
                self._btn("DUP", _DUP_BTN, NAMES["blue"])
                self._btn("DEL", _DEL_BTN, NAMES["red"])
        elif self.screen == "desktop":
            if self.cart_error is None:
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
            if self.cart_error is not None:
                self._draw_error_panel()
            self._draw_desktop_buttons()
        elif self.menu_view == "code":
            self._draw_code()              # full-screen editor (covers the cart)
        else:  # cards / paint / map: a panel over the frozen cart
            try:
                if self._draw:
                    self._draw()
            except Exception:
                pass
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
        self._draw_cursor()
        self.comp.flush()

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
        if self.pointer is not None and self.pointer.visible:
            self.canvas.spr(CURSOR, self.pointer.x, self.pointer.y, 1)

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

    def _glyph(self, kind, rect, c):
        """Draw an icon glyph (no background) centered in `rect`, in color `c`.
        The shared pre-literate icon vocabulary -- a 12x12 1-bit pixel bitmap
        (see _GLYPHS) blitted via the indexed primitives only (rect spans), so it
        renders identically on host and device. Unknown kinds draw NOTHING, so
        every caller can keep a text label as the guaranteed fallback."""
        bits = _GLYPHS.get(kind)
        if bits is None:                                # unknown -> nothing (fallback contract)
            return
        cv = self.canvas
        x, y, w, h = rect
        n = _GLYPH_SIZE
        # Center the 12x12 mask in the rect (centers match the old cx/cy glyphs).
        ox = x + (w - n) // 2
        oy = y + (h - n) // 2
        for r in range(n):
            row = bits[r]
            if not row:
                continue
            yy = oy + r
            run = 0                                     # length of the current on-run
            for col in range(n):                        # walk L->R, coalescing runs
                if row & (1 << (n - 1 - col)):
                    run += 1
                elif run:
                    cv.rect(ox + col - run, yy, run, 1, c)
                    run = 0
            if run:
                cv.rect(ox + n - run, yy, run, 1, c)

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
        # Zoomed 8x8 pixel grid (filled cells + grid lines).
        for ly in range(8):
            for lx in range(8):
                x = _PG_X0 + lx * _PG_CELL
                y = _PG_Y0 + ly * _PG_CELL
                cv.rect(x, y, _PG_CELL, _PG_CELL, sheet.tget(pe.n, lx, ly))
                cv.rectb(x, y, _PG_CELL, _PG_CELL, NAMES["dark_grey"])
        # 16-color palette (2x8), the selected swatch outlined white.
        for idx in range(16):
            x = _SW_X0 + (idx % _SW_COLS) * _SW
            y = _SW_Y0 + (idx // _SW_COLS) * _SW
            cv.rect(x, y, _SW, _SW, idx)
            cv.rectb(x, y, _SW, _SW,
                     NAMES["white"] if idx == pe.color else NAMES["dark_grey"])
        # Sprite selector + a 4x preview of the current sprite.
        self._btn("<", _SPR_PREV, NAMES["blue"])
        self._btn(">", _SPR_NEXT, NAMES["blue"])
        ppx, ppy, ps = 240, 92, 4
        for ly in range(8):
            for lx in range(8):
                cv.rect(ppx + lx * ps, ppy + ly * ps, ps, ps, sheet.tget(pe.n, lx, ly))
        cv.rectb(ppx, ppy, 8 * ps, 8 * ps, NAMES["dark_grey"])
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
