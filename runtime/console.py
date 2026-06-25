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

from editors import CodeEditor, PaintEditor, SpriteSheet


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
_MENU_BTN = (4, 4, 76, 18)        # desktop overlay: open Make-it-mine
_HOME_BTN = (240, 4, 76, 18)      # desktop overlay: back to launcher
_RUN_BTN = (28, 188, 70, 24)
_CODE_BTN = (104, 188, 84, 24)
_CLOSE_BTN = (194, 188, 96, 24)
_CARD_X = 24
_CARD_W = 272
_CARD_Y0 = 52
_CARD_DY = 22
_CARD_H = 20
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
_PAINT_BTN = (122, 4, 76, 18)     # desktop overlay: open the paint editor
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
# Trackball cursor sensitivity (#2). _CURSOR_BASE is the per-pulse step; the
# quadratic _CURSOR_ACCEL term adds light acceleration so a fast roll crosses the
# 320px screen in far fewer pulses while a slow, single-pulse roll stays precise.
# These are a FEEL tweak meant to be finalized on real hardware (the trackball's
# pulses-per-revolution sets the true "rolls to cross").  Before: BASE=4, ACCEL=1
# (1 pulse -> 5px, ~64 px/s at a steady 1 pulse/frame). After: BASE=7, ACCEL=2
# (1 pulse -> 9px; a 6-pulse flick -> 6*7 + 2*36 = 114px, so ~3 brisk rolls cross).
_CURSOR_BASE = 7
_CURSOR_ACCEL = 2


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


class Workstation:
    def __init__(self, comp, canvas, input, carts=None):
        self.comp = comp
        self.canvas = canvas
        self.input = input
        self.make_api = None       # injected: make_api(canvas, input, cfg, sheet)->ns
        self.carts_store = None     # injected: cart store module (kid_carts API)
        self.launcher = Launcher(carts if carts else [])
        self.screen = "launcher"      # "launcher" | "desktop" | "menu"
        self.cart = None
        self.config = None
        self.ns = None
        self._update = None
        self._draw = None
        self.msel = 0                 # selected card in the menu
        self.menu_view = "cards"      # menu sub-view: "cards" | "code" | "paint"
        self.editor = None            # CodeEditor while menu_view == "code"
        self.sheet = None             # SpriteSheet for the open cart (built on open)
        self.paint = None             # PaintEditor while menu_view == "paint"
        self.keyboard = None          # set by run_desktop (for raw/text mode toggle)
        self._ekey_prev = 0           # last consumed keyboard byte (edge detect)
        self._drag = None             # last pointer pos during a code-view drag-scroll
        self._ldrag = None            # launcher drag state [press_y, last_y, moved?]
        self._autoscroll = 0          # frames a finger has dwelled in a launcher edge
        self._lhover = (-1, -1)       # last cursor pos used for launcher hover-highlight
        self.pointer = None           # set by run_desktop
        self.carts_root = None        # SD carts dir (reads); set by run_desktop
        self.can_manage = True        # writes enabled? run_desktop sets this from
                                      # whether SD is the cart source (carts_root)
        # SD session wrapper: mounts SD for the duration of fn(), then releases it
        # so the render loop's flushes never collide on the shared bus. On device
        # run_desktop swaps in kidcode_sd.with_sd_live (native kc_sd attach). The
        # default is a host passthrough.
        self._with_sd = lambda fn: fn()

    def _start(self):
        ns = self.make_api(self.canvas, self.input, self.config, self.sheet)
        try:
            exec(self.cart["src"], ns)
            if ns.get("_init"):
                ns["_init"]()
        except Exception as exc:  # noqa: BLE001
            print("KidCode cart error:", exc)
            return False
        self.ns = ns
        self._update = ns.get("_update")
        self._draw = ns.get("_draw")
        return True

    def open(self):
        self.cart = self.launcher.selected()
        self.config = dict(self.cart["cfg"])
        self.msel = 0
        self.editor = None
        self.paint = None
        self.sheet = self._build_sheet()
        self.menu_view = "cards"
        self._set_text_mode(False)
        if self._start():
            self.screen = "desktop"

    def _build_sheet(self):
        hexs = self.cart.get("sprites") if self.cart else None
        if hexs:
            try:
                return SpriteSheet.from_hex(hexs)
            except Exception:  # noqa: BLE001
                pass
        return SpriteSheet()

    # -- code / paint editors (#3, #4) ---------------------------------------

    def set_menu_view(self, view):
        """Switch the menu sub-view, building the matching editor and toggling
        the keyboard between game (raw) and text (ASCII) modes."""
        self.menu_view = view
        if view == "code":
            if self.editor is None and self.cart is not None:
                self.editor = CodeEditor(self.cart["src"])
                self._ekey_prev = 0
        elif view == "paint":
            if self.paint is None and self.sheet is not None:
                self.paint = PaintEditor(self.sheet)
        self._set_text_mode(view == "code")

    def _set_text_mode(self, on):
        # The T-Deck keyboard always returns clean 1-byte ASCII now, so there is no
        # mode to flip -- the editor reads last_key directly and nav/menus read the
        # mapped buttons. Kept as a hook (and to keep the keyboard out of raw mode).
        kb = self.keyboard
        if kb is not None:
            kb.raw_mode = False

    def _open_menu(self):
        self.screen = "menu"
        # Carts with a Make-it-mine schema open to cards; others go straight to
        # the code editor (there are no cards to show).
        self.set_menu_view("cards" if self.cart.get("edit") else "code")

    def _open_paint(self):
        self.screen = "menu"
        self.set_menu_view("paint")

    def _leave_menu(self):
        self._set_text_mode(False)
        self.screen = "desktop"

    def _editor_input(self):
        # Feed the typed key to the editor, one insert per physical press: the
        # keyboard reports the byte for the frame it is down then 0, so acting on
        # the 0->key edge (key != previous) avoids autorepeat.
        if self.editor is None:
            return
        k = self.input.last_key
        if k and k != self._ekey_prev:
            self.editor.key(k)
        self._ekey_prev = k

    def save_code(self):
        if not (self.editor and self.cart and self.cart.get("path") and self.can_manage):
            return
        src = self.editor.text()
        try:
            self._with_sd(lambda: self.carts_store.save_code(self.cart, src))
            self.editor.dirty = False
        except Exception as exc:  # noqa: BLE001
            print("KidCode save code failed:", exc)

    def run_code(self):
        if self.editor is not None:
            self.cart["src"] = self.editor.text()   # in-RAM apply (always)
            self.save_code()                         # persist if SD-backed
        if self._start():
            self._set_text_mode(False)
            self.screen = "desktop"

    def save_sprites(self):
        if not (self.sheet and self.cart and self.cart.get("path") and self.can_manage):
            return
        hexs = self.sheet.to_hex()
        try:
            self._with_sd(lambda: self.carts_store.save_sprites(self.cart, hexs))
            self.sheet.dirty = False
        except Exception as exc:  # noqa: BLE001
            print("KidCode save sprites failed:", exc)

    def apply(self):
        if self._start():
            self.screen = "desktop"
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
        self.screen = "launcher"
        self.cart = None
        self.ns = None

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
            v = str(v).replace("_", " ").upper()
        t = f.get("card")
        return t.replace("{value}", str(v)) if t else "%s: %s" % (f["key"].upper(), v)

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
            if i.pressed("down"):
                self.msel = (self.msel + 1) % len(ed)
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
                    self.open()
                    return

        if down:
            if self._ldrag is None:                 # finger just went down in/at the strip
                self._ldrag = [py, py, False]       # [press_y, last_y, moved?]
                self._autoscroll = 0
            press_y, last_y, moved = self._ldrag
            # Drag: pan by whole tiles as the finger crosses each tile pitch.
            steps = (last_y - py) // self.launcher.TILE_PITCH
            if steps:
                self.launcher.scroll(steps)
                last_y = last_y - steps * self.launcher.TILE_PITCH
            if abs(py - press_y) > 4:
                moved = True
            self._ldrag = [press_y, last_y, moved]
            # Autoscroll while dwelling in an edge band (held finger, not just a flick).
            if in_strip and py < _LIST_Y0 + _LIST_EDGE:
                self._autoscroll += 1
                if self._autoscroll % 6 == 0:
                    self.launcher.scroll(-1)
            elif in_strip and py >= _LIST_BOTTOM - _LIST_EDGE:
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
        for i in range(len(self.cart["edit"])):
            y = _CARD_Y0 + i * _CARD_DY
            if _CARD_X <= px < _CARD_X + _CARD_W and y <= py < y + _CARD_H:
                return i
        return None

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
                elif ci is not None:
                    self.adjust(-1 if px < _CARD_X + _CARD_W // 2 else 1)

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
        elif _in(px, py, _PAINT_SAVE):
            self.save_sprites()
        elif _in(px, py, _PAINT_CLOSE):
            self._leave_menu()

    # -- frame + drawing -----------------------------------------------------

    def frame(self, dt):
        if self.screen == "launcher":
            self.launcher.draw(self.canvas)
            if self.can_manage:
                self._btn("NEW", _NEW_BTN, NAMES["green"])
                self._btn("DUP", _DUP_BTN, NAMES["blue"])
                self._btn("DEL", _DEL_BTN, NAMES["red"])
        elif self.screen == "desktop":
            try:
                if self._update:
                    self._update(dt)
                if self._draw:
                    self._draw()
            except Exception as exc:  # noqa: BLE001
                print("KidCode frame error:", exc)
                self.go_home()
            else:
                self._draw_desktop_buttons()
        elif self.menu_view == "code":
            self._draw_code()              # full-screen editor (covers the cart)
        else:  # cards / paint: a panel over the frozen cart
            try:
                if self._draw:
                    self._draw()
            except Exception:
                pass
            if self.menu_view == "paint":
                self._draw_paint()
            else:
                self._draw_cards()
        self._draw_cursor()
        self.comp.flush()

    def _btn(self, label, rect, fill):
        x, y, w, h = rect
        cv = self.canvas
        cv.rect(x, y, w, h, fill)
        cv.rectb(x, y, w, h, NAMES["white"])
        cv.print(label, x + 6, y + (h - 8) // 2, NAMES["black"], 2)

    def _draw_desktop_buttons(self):
        # Carts with a Make-it-mine schema open the cards menu; the rest jump
        # straight to the code editor -- label the button to match.
        self._btn("EDIT" if self.cart.get("edit") else "CODE",
                  _MENU_BTN, NAMES["dark_purple"])
        self._btn("PAINT", _PAINT_BTN, NAMES["orange"])
        self._btn("HOME", _HOME_BTN, NAMES["dark_grey"])

    def _draw_cursor(self):
        if self.pointer is not None and self.pointer.visible:
            self.canvas.spr(CURSOR, self.pointer.x, self.pointer.y, 1)

    def _draw_cards(self):
        cv = self.canvas
        cv.rect(20, 16, 280, 206, NAMES["dark_purple"])
        cv.rectb(20, 16, 280, 206, NAMES["pink"])
        cv.print("MAKE IT MINE", 30, 22, NAMES["white"], 2)
        for i in range(len(self.cart["edit"])):
            y = _CARD_Y0 + i * _CARD_DY
            if i == self.msel:
                cv.rect(_CARD_X, y - 1, _CARD_W, _CARD_H, NAMES["indigo"])
            cv.print("-", _CARD_X + 4, y, NAMES["yellow"], 2)
            cv.print(self.card_text(i), _CARD_X + 22, y,
                     NAMES["white"] if i == self.msel else NAMES["light_grey"], 2)
            cv.print("+", _CARD_X + _CARD_W - 12, y, NAMES["yellow"], 2)
        self._btn("RUN", _RUN_BTN, NAMES["green"])
        self._btn("CODE", _CODE_BTN, NAMES["blue"])
        self._btn("CLOSE", _CLOSE_BTN, NAMES["red"])

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
            for idx in range(len(vis)):
                y = _CODE_Y0 + idx * _CODE_LH
                cv.print(vis[idx][ed.left:ed.left + CodeEditor.COLS], _CODE_X0, y,
                         NAMES["light_grey"], 1)
                if ed.top + idx == ed.row:      # caret on the cursor's line
                    vcol = ed.col - ed.left
                    if 0 <= vcol <= CodeEditor.COLS:
                        cv.rect(_CODE_X0 + vcol * 8, y, 1, 8, NAMES["yellow"])
        self._draw_symbols()

    def _draw_symbols(self):
        # Tappable coding-symbol palette (supplies what the keyboard can't type).
        cv = self.canvas
        for i in range(len(_CODE_SYMBOLS)):
            x = _SYM_AREA[0] + i * _SYM_CELL
            cv.rect(x, _SYM_Y, _SYM_CELL - 1, _SYM_H - 1, NAMES["dark_grey"])
            cv.rectb(x, _SYM_Y, _SYM_CELL - 1, _SYM_H - 1, NAMES["indigo"])
            cv.print(_CODE_SYMBOLS[i], x + 6, _SYM_Y + 6, NAMES["white"], 1)

    def _draw_icon(self, kind, rect):
        cv = self.canvas
        x, y, w, h = rect
        if kind == "run":
            cv.rect(x, y, w, h, NAMES["green"])         # play triangle
            for i in range(6):
                hh = 10 - 2 * i
                if hh > 0:
                    cv.rect(x + 4 + i, y + 2 + i, 1, hh, NAMES["black"])
        elif kind == "save":
            cv.rect(x, y, w, h, NAMES["blue"])          # down-arrow ("save")
            cx = x + w // 2
            cv.rect(cx, y + 2, 1, 6, NAMES["white"])
            cv.line(x + 3, y + 6, cx, y + 10, NAMES["white"])
            cv.line(x + w - 4, y + 6, cx, y + 10, NAMES["white"])
        else:  # close
            cv.rect(x, y, w, h, NAMES["red"])           # X
            cv.line(x + 3, y + 3, x + w - 4, y + h - 4, NAMES["black"])
            cv.line(x + w - 4, y + 3, x + 3, y + h - 4, NAMES["black"])

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
        self._btn("SAVE", _PAINT_SAVE, NAMES["green"])
        self._btn("CLOSE", _PAINT_CLOSE, NAMES["red"])
