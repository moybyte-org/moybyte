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
        self.cart_error = None        # last cart failure text -> on-canvas error panel
        self.save_status = None       # last save_code result text (e.g. a syntax error)
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
            # The device's native run loop starves USB, so a print() never reaches
            # serial -- stash the failure so frame() can paint an on-canvas panel.
            # Print only the _err_text-guarded string, never the raw `exc`: a cart
            # exception whose __str__ itself raises would otherwise escape here and
            # become the exact silent device hang the panel exists to prevent.
            self.cart_error = _err_text(exc)
            print("KidCode cart error:", self.cart_error)
            return False
        self.cart_error = None
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
        self.cart_error = None
        self.save_status = None
        self.sheet = self._build_sheet()
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
            self.editor.key(k)
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
            return False
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
        tiles = f.get("tiles")
        if tiles:
            return [int(t) for t in tiles]
        out = []
        for c in f.get("choices", []):
            try:
                out.append(int(c))
            except (TypeError, ValueError):
                out.append(0)
        return out

    def _card_height(self, f):
        d = self._card_display(f)
        if d in ("sprite-tiles", "bg-thumbs"):
            return 44
        if d in ("gauge", "count", "choice-icons"):
            return 32
        return _CARD_H

    def _card_layout(self):
        """Pure (no-draw) per-card geometry so draw and hit-test agree. Returns a
        list of dicts: {i, f, display, x, y, w, h}, laid out top-down from
        _CARD_Y0 with a per-card height that depends on its display type."""
        rows = []
        y = _CARD_Y0
        for i, f in enumerate(self.cart["edit"]):
            h = self._card_height(f)
            rows.append({"i": i, "f": f, "display": self._card_display(f),
                         "x": _CARD_X, "y": y, "w": _CARD_W, "h": h})
            y += h + 2
        return rows

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
            if self.cart_error is None:
                try:
                    if self._update:
                        self._update(dt)
                    if self._draw:
                        self._draw()
                except Exception as exc:  # noqa: BLE001
                    # A cart that raises mid-frame must NOT escape the loop (the
                    # device would hang silently). Capture it, stop running the
                    # broken cart, and fall through to paint the error panel; the
                    # desktop buttons stay so the kid can EDIT/CODE the fix.
                    self.cart_error = _err_text(exc)
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
        # N repeated icons == the value, so a count reads at a glance. Capped so a
        # big number stays one tidy row; the number itself is the label cue above.
        f = row["f"]
        x, y, w = row["x"], row["y"], row["w"]
        cur = self.config.get(f["key"], f.get("default", 0))
        try:
            n = int(cur)
        except (TypeError, ValueError):
            n = 0
        glyph = f.get("icon", "star")
        cap = int(f.get("count_max", min(f.get("max", 12), 14)))
        shown = max(0, min(n, cap))
        step = 16
        per_row = max(1, (w - 4) // step)
        for k in range(shown):
            gx = x + 2 + (k % per_row) * step
            gy = y + 14 + (k // per_row) * 14
            self._glyph(glyph, (gx, gy, 14, 14), NAMES["yellow"])

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

    # A few named background presets, each a tiny "what the screen will look like"
    # thumbnail. A cart reads the chosen name in cfg("bg") and paints to match
    # (e.g. _bg(name) at the top of _draw). New presets just add a clause here.
    _BG_PRESETS = ("black", "dark_blue", "night", "stripes")

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
        # A glyph on its own colored button background -- the code-editor top bar
        # (run/save/close). The pure glyph vocabulary lives in _glyph(); this just
        # paints a backing box of a sensible color, then the glyph on top.
        bg = {"run": "green", "save": "blue", "close": "red"}.get(kind, "dark_grey")
        x, y, w, h = rect
        self.canvas.rect(x, y, w, h, NAMES[bg])
        self._glyph(kind, rect, NAMES["black"] if kind == "run" else NAMES["white"])

    def _glyph(self, kind, rect, c):
        """Draw an icon glyph (no background) centered in `rect`, in color `c`.
        The shared pre-literate icon vocabulary -- composed from the indexed
        primitives only (pix/line/rect/rectb/circ/circb), so it renders identically
        on host and device. Unknown kinds draw NOTHING, so every caller can keep a
        text label as the guaranteed fallback."""
        cv = self.canvas
        x, y, w, h = rect
        cx, cy = x + w // 2, y + h // 2
        if kind == "run":                               # play triangle
            for i in range(6):
                hh = 10 - 2 * i
                if hh > 0:
                    cv.rect(x + (w - 8) // 2 + i, cy - 5 + i, 1, hh, c)
        elif kind == "save":                            # down-into-tray arrow
            cv.rect(cx, y + 2, 1, 6, c)
            cv.line(x + 3, y + 6, cx, y + 10, c)
            cv.line(x + w - 4, y + 6, cx, y + 10, c)
        elif kind == "close":                           # X
            cv.line(x + 3, y + 3, x + w - 4, y + h - 4, c)
            cv.line(x + w - 4, y + 3, x + 3, y + h - 4, c)
        elif kind == "edit":                            # pencil (diagonal + nib)
            cv.line(x + 4, y + h - 5, x + w - 5, y + 4, c)
            cv.line(x + 5, y + h - 5, x + w - 4, y + 5, c)
            cv.rect(x + 2, y + h - 6, 3, 3, c)          # nib block
        elif kind == "paint":                           # brush: handle + bristle
            cv.line(cx + 5, y + 3, cx - 3, y + h - 6, c)
            cv.rect(cx - 6, y + h - 7, 7, 5, c)         # bristle block
        elif kind == "home":                            # house: roof + walls + door
            for i in range(5):
                cv.line(cx - i, cy - 4 + i, cx + i, cy - 4 + i, c)   # roof
            cv.rectb(cx - 4, cy, 9, 6, c)               # walls
            cv.rect(cx - 1, cy + 2, 3, 4, c)            # door
        elif kind == "minus":
            cv.rect(cx - 4, cy - 1, 9, 2, c)
        elif kind == "plus":
            cv.rect(cx - 4, cy - 1, 9, 2, c)
            cv.rect(cx - 1, cy - 4, 2, 9, c)
        elif kind == "turtle":                          # gauge low end (slow)
            cv.circ(cx - 1, cy + 1, 3, c)               # shell
            cv.rect(cx + 2, cy, 3, 2, c)                # head
            cv.rect(cx - 5, cy + 3, 2, 2, c)            # foot
        elif kind == "rabbit":                          # gauge high end (fast)
            cv.circ(cx, cy + 1, 3, c)                   # body
            cv.rect(cx - 1, cy - 5, 1, 4, c)            # ears
            cv.rect(cx + 1, cy - 5, 1, 4, c)
        elif kind == "star":                            # generic count token
            cv.rect(cx - 3, cy, 7, 1, c)
            cv.rect(cx, cy - 3, 1, 7, c)
            cv.pix(cx - 2, cy - 2, c)
            cv.pix(cx + 2, cy - 2, c)
            cv.pix(cx - 2, cy + 2, c)
            cv.pix(cx + 2, cy + 2, c)
        elif kind == "dot":                             # generic count/choice token
            cv.circ(cx, cy, 3, c)
        elif kind == "heart":
            cv.circ(cx - 2, cy - 1, 2, c)
            cv.circ(cx + 2, cy - 1, 2, c)
            for i in range(4):
                cv.line(cx - 3 + i, cy + i, cx + 3 - i, cy + i, c)

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
