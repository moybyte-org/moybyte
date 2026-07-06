"""The unified 18px top bar + bottom dock (#46), extracted from Workstation
(runtime/console.py) as its own surface -- docs/shell_layers_refactor_v1.md.

This module is the SINGLE SOURCE of the bar/dock geometry constants (`_STATUS_H`,
`_BAR_*`, the fixed 320x240 tool-switcher button rects `_HOME_BTN`/`_MENU_BTN`/...,
and `_DOCK_SLOTS`/`_DOCK_GLYPH`/`_DOCK_LABEL`). They're consumed BOTH here (BarLayer's
draw + hit-testing) AND by console.py's own chrome (the `Layout` class + a few derived
constants + the golden harness/tests that read `console._HOME_BTN`), so rather than
duplicate them (drift), console.py imports them back from here (re-exported under the
same names) -- the same pattern block_editor_ui.py uses for its `_BLK_*`.

`BarLayer` reaches everything else through its `self.ws` back-ref (the shared draw
toolkit ws._glyph/_icon/_mini_btn stays on Workstation; the bar is a consumer). Only
`NAMES` (palette) and `_in` (rect hit-test) are injected at construction -- the same
circular-import dodge the other extracted UIs use, since console.py builds the one
BarLayer instance a Workstation holds. The trivial time helpers `_ticks_ms`/
`_ticks_diff` are duplicated here (time-only, like achievements_ui.py) for the clock.
"""
import time


# -- bar/dock geometry (single source; console.py imports these back) ---------
# The running-cart top bar draws on the fixed 320x240 GAME canvas, so its icon/button
# rects are fixed constants here; the launcher/Settings bar reflows via Layout on the
# SYSTEM canvas. The hamburger (≡) is the LEFT-MOST icon (slot 0); the tool switchers
# HOME/EDIT/PAINT/MAP/BLOCKS/MUSIC follow one stride apart.
_BAR_ICON = 16              # icon sprite side (16x16, from the IconSheet)
_BAR_GAP = 2               # px between adjacent bar icons
_BAR_STRIDE = _BAR_ICON + _BAR_GAP        # 18: left-edge step between icons
_BAR_Y = 1                 # icons sit 1px down in the 18px bar (1px top/bottom margin)
_SYSMENU_BTN = (2, _BAR_Y, _BAR_ICON, _BAR_ICON)                 # ≡ dropdown toggle (slot 0)
_HOME_BTN = (2 + _BAR_STRIDE, _BAR_Y, _BAR_ICON, _BAR_ICON)      # back to launcher
_MENU_BTN = (2 + 2 * _BAR_STRIDE, _BAR_Y, _BAR_ICON, _BAR_ICON)  # Make-it-mine / code
_PAINT_BTN = (2 + 3 * _BAR_STRIDE, _BAR_Y, _BAR_ICON, _BAR_ICON)  # paint editor
_MAP_BTN = (2 + 4 * _BAR_STRIDE, _BAR_Y, _BAR_ICON, _BAR_ICON)   # map (tilemap) editor
_BLOCKS_BTN = (2 + 5 * _BAR_STRIDE, _BAR_Y, _BAR_ICON, _BAR_ICON)  # block editor (#29)
_MUSIC_BTN = (2 + 6 * _BAR_STRIDE, _BAR_Y, _BAR_ICON, _BAR_ICON)  # music/sound editor (#50)
# Right cluster on the running-cart bar (GAME canvas, always 320 wide @ fs 1): batt
# hard-right, then wifi, then the clock text. Mirrors the launcher Layout's right
# cluster so both bars read identically. The clock egg hit-test uses _BAR_CLOCK.
_BAR_BATT = (320 - 2 - _BAR_ICON, _BAR_Y, _BAR_ICON, _BAR_ICON)
_BAR_WIFI = (_BAR_BATT[0] - _BAR_STRIDE, _BAR_Y, _BAR_ICON, _BAR_ICON)
_BAR_CLOCK = (_BAR_WIFI[0] - 2 - 5 * 8, 0, 5 * 8, 18)
_STATUS_H = 18          # unified top bar height (16px icons + 1px top/bottom margin)
# Bottom dock (persistent tool switcher, TIC-80 style): six evenly-spaced slots. The
# per-slot rects come from Layout (responsive); these are the slot vocabulary + labels.
_DOCK_SLOTS = ("home", "code", "paint", "map", "run", "settings")
_DOCK_GLYPH = {"home": "home", "code": "code", "paint": "paint",
               "map": "map", "run": "run", "settings": "gear"}
_DOCK_LABEL = {"home": "HOME", "code": "CODE", "paint": "DRAW",
               "map": "MAP", "run": "RUN", "settings": "SET"}


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


class BarLayer:
    """The unified 18px top bar + bottom dock (#46), migrated out of Workstation as
    its own surface (docs/shell_layers_refactor_v1.md Phase 2). Owns the bar's DRAW
    (three variants: launcher 'home', 'settings', running-cart 'desktop'), the dock,
    the running-cart bar's offscreen strip cache (#43), the per-second clock cache
    (#66), the dock geometry/activation, and the bar/dock TAP slices.

    Boundary decisions (deliberate, see the doc):
      * The button-rect + dock CONSTANTS are the module-level single source above --
        the golden harness + tests reference console._HOME_BTN / _MENU_BTN /
        _DOCK_SLOTS / ... (re-exported by console.py), and Layout also uses them.
      * The shared draw toolkit (ws._glyph / ws._icon / ws._mini_btn / ws._bar_image)
        stays on Workstation per the doc; the bar is a CONSUMER of it (via self.ws).
      * `_bar_img_cache` (per-kind icon Image cache backing ws._icon) stays on ws;
        `_bar_cache_gen` (the running-cart STRIP cache generation) lives here and
        ws.set_icon_sheet bumps it via invalidate().

    The bar is CHROME the content composes, not a standalone stack layer: it's
    SYSTEM-domain on launcher/settings (drawn on sys_canvas) but GAME-domain on the
    running cart (drawn on the game canvas inside the viewport), and it draws at a
    fixed point inside each content's paint. So the content draws call
    _draw_status_strip(where)/_draw_dock(where) and the pointer methods delegate their
    bar slice to handle_home_tap / handle_cart_tap / _dock_slot_at + _activate_dock.

    `NAMES` (palette) and `_in` (rect hit-test) are injected at construction (the same
    circular-import dodge the other extracted UIs use)."""

    def __init__(self, ws, names, in_rect):
        self.ws = ws
        self._NAMES = names
        self._in = in_rect
        # Cached running-cart top bar (#43): rendered ONCE into an offscreen strip and
        # blitted each frame (one flat copy) instead of re-rendering ~9 sprites + glyph
        # + text every frame. `_cart_bar_strip` is the layer; `_cart_bar_key_cur` is the
        # state key it holds (None = stale); `_cart_bar_canvas` is the canvas it was
        # built on (a web-view swap forces a rebuild); `_bar_cache_gen` is bumped by the
        # explicit invalidators (invalidate(), from ws.set_icon_sheet) so a theme swap
        # repaints.
        self._cart_bar_strip = None
        self._cart_bar_key_cur = None
        self._cart_bar_canvas = None
        self._bar_cache_gen = 0
        # Clock-text cache (#66 CHROMEBRK): (second, string) -- see _clock_text.
        self._clock_at = -1
        self._clock_cache = ""

    def invalidate(self):
        """Repaint the cached running-cart bar on the next frame (a theme/IconSheet
        swap changed its pixels). Called by ws.set_icon_sheet."""
        self._bar_cache_gen += 1

    # -- draw ----------------------------------------------------------------

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
        NAMES = self._NAMES
        ws = self.ws
        if where == "desktop":
            self._draw_top_bar_cart()
            return
        cv = ws.sys_canvas
        lay = ws.layout
        cv.rect(0, 0, cv.w, lay.status_h, NAMES["black"])
        cv.rect(0, lay.status_h - 1, cv.w, 1, NAMES["dark_grey"])   # shelf edge line
        # Right cluster: clock + wifi/batt (Settings now lives in the ≡ menu, #52).
        cv.print(self._clock_text(), lay.clock_x, 3, NAMES["light_grey"], 1)
        ws._icon("wifi", lay.wifi_btn[0], lay.wifi_btn[1], cv)
        ws._icon("batt", lay.batt_btn[0], lay.batt_btn[1], cv)
        # ≡ system-menu toggle (leftmost, always) -- the launcher's Settings entry now,
        # a _glyph bitmap like the in-cart bar so an older saved theme can't blank it.
        ws._glyph("menu", lay.sysmenu_btn, NAMES["white"], cv)
        # Left cluster: management icons (when writable) + the selected cart's name.
        if where == "home":
            if ws.can_manage:
                ws._icon("new", lay.new_btn[0], lay.new_btn[1], cv)
                ws._icon("dup", lay.dup_btn[0], lay.dup_btn[1], cv)
                ws._icon("del", lay.del_btn[0], lay.del_btn[1], cv)
            sel = ws.launcher.selected()
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
        ws = self.ws
        cv = ws.canvas
        key = self._cart_bar_key()
        strip = self._cart_bar_strip
        # The active canvas can SWAP at runtime (the device web view binds a recording
        # TeeCanvas in place of the raw DeviceCanvas, #41). A strip allocated on the OLD
        # canvas is the wrong layer type for the new one -- a raw DeviceCanvas strip blitted
        # through the Tee has no RecordingLayer hooks ('_end_batch' AttributeError), and it
        # wouldn't be recorded for the browser anyway. So rebuild the layer when the canvas
        # identity changes, not just on a resize.
        canvas_changed = self._cart_bar_canvas is not cv
        if strip is None or strip.w != cv.w or canvas_changed or self._cart_bar_key_cur != key:
            # (Re)build the cached strip. new_layer gives a same-type/-palette canvas the
            # bar body draws into at the SAME coords (the bar lives at y in [0, _STATUS_H),
            # which maps 1:1 onto the strip's rows), so the cached pixels are byte-identical
            # to drawing straight onto cv. Reuse the buffer across re-renders when the size
            # is unchanged; allocate a fresh layer on first build / a resize / a canvas swap.
            if strip is None or strip.w != cv.w or canvas_changed:
                strip = cv.new_layer(cv.w, _STATUS_H)
                self._cart_bar_strip = strip
                self._cart_bar_canvas = cv
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
        ws = self.ws
        has_edit = bool(ws.cart.get("edit")) if ws.cart else False
        return (self._clock_text(), has_edit, id(ws.icon_sheet),
                getattr(ws.canvas, "font_scale", 1), self._bar_cache_gen)

    def _render_cart_bar(self, cv, key):
        """Render the running-cart bar's pixels onto `cv` (the offscreen strip, or any
        canvas) at the fixed 320x240 bar coords. Factored out of _draw_top_bar_cart so the
        SAME drawing serves both the cache build and the (test/fallback) direct path, which
        is what makes the cached strip pixel-identical to a direct render. `key` carries the
        already-computed has_edit (index 1) so the icon choice can't drift from the key."""
        NAMES = self._NAMES
        ws = self.ws
        has_edit = key[1]
        cv.rect(0, 0, cv.w, _STATUS_H, NAMES["black"])
        cv.rect(0, _STATUS_H - 1, cv.w, 1, NAMES["dark_grey"])      # shelf edge line
        # Left cluster: the TIC-80 one-tap tool switcher. Carts with a Make-it-mine
        # schema open the cards menu (pencil = EDIT); the rest jump straight to code
        # (the < > glyph = CODE). cart may be None defensively (error panel, no cart).
        # ≡ system-menu toggle (#52), leftmost. A _glyph bitmap (not a themeable
        # IconSheet slot) so it never goes blank on a device with an older saved theme.
        ws._glyph("menu", _SYSMENU_BTN, NAMES["white"], cv)
        ws._icon("home", _HOME_BTN[0], _HOME_BTN[1], cv)
        ws._icon("edit" if has_edit else "code", _MENU_BTN[0], _MENU_BTN[1], cv)
        ws._icon("paint", _PAINT_BTN[0], _PAINT_BTN[1], cv)
        ws._icon("map", _MAP_BTN[0], _MAP_BTN[1], cv)
        ws._icon("blocks", _BLOCKS_BTN[0], _BLOCKS_BTN[1], cv)
        ws._icon("music", _MUSIC_BTN[0], _MUSIC_BTN[1], cv)
        # Right cluster: clock + wifi/batt (Settings now lives in the ≡ menu, not a gear).
        cv.print(self._clock_text(), _BAR_CLOCK[0], 3, NAMES["light_grey"], 1)
        ws._icon("wifi", _BAR_WIFI[0], _BAR_WIFI[1], cv)
        ws._icon("batt", _BAR_BATT[0], _BAR_BATT[1], cv)

    def _clock_text(self):
        """A wall-clock HH:MM from time.localtime when available, else a mm:ss
        uptime so the strip always shows a live clock (host == device). Cached
        per second (#66 CHROMEBRK): the cart bar's cache KEY calls this every
        frame, and re-running localtime + %-format 30x/s was a measurable slice
        of the ~2.3ms bar cost -- the string can only change once a second."""
        now_s = _ticks_ms() // 1000
        if now_s == self._clock_at:
            return self._clock_cache
        try:
            lt = time.localtime()
            s = "%02d:%02d" % (lt[3], lt[4])
        except Exception:  # noqa: BLE001
            secs = _ticks_diff(_ticks_ms(), 0) // 1000
            s = "%02d:%02d" % ((secs // 60) % 100, secs % 60)
        self._clock_at = now_s
        self._clock_cache = s
        return s

    def _draw_dock(self, where):
        """The persistent bottom dock: home / code / draw / map / run / settings.
        The active slot (home on the desktop, settings in Settings) is highlighted;
        the music slot is greyed (its editor is #16, not yet here). Tool slots that
        need an open cart read dimmed on the home desktop."""
        NAMES = self._NAMES
        ws = self.ws
        cv = ws.sys_canvas
        lay = ws.layout
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
            enabled = slot in ("home", "settings", "run") or ws.cart is not None
            if is_active:
                cv.rect(x, y, w, h, NAMES["indigo"])
            gc = NAMES["white"] if enabled else NAMES["dark_blue"]
            ws._glyph(_DOCK_GLYPH[slot], (x, y, w, gh), gc, cv)
            label = _DOCK_LABEL[slot]
            cv.print(label, x + (w - len(label) * fw) // 2, y + gh, gc, 1)

    # -- dock geometry + taps ------------------------------------------------

    def _dock_slot_rect(self, k):
        return self.ws.layout.dock_slot_rect(k)

    def _dock_slot_at(self, px, py):
        """Which dock slot ("home"/"code"/.../"settings") was tapped, or None."""
        if py < self.ws.layout.dock_y:
            return None
        for k in range(len(_DOCK_SLOTS)):
            if self._in(px, py, self._dock_slot_rect(k)):
                return _DOCK_SLOTS[k]
        return None

    def _activate_dock(self, slot):
        """Run a dock action. The dock is drawn on home + settings; from the home
        desktop only home/settings/run apply (no open cart for the editors), but if
        a cart is still open behind Settings the tool slots switch its active editor
        (TIC-80 style). run = open the selected cart from home, or re-run the open
        one from Settings."""
        ws = self.ws
        if slot == "home":
            ws.go_home()
        elif slot == "settings":
            ws.open_settings()
        elif ws.cart is not None:                # tool slots need an open cart
            if slot == "code":
                ws._open_menu()
            elif slot == "paint":
                ws._open_paint()
            elif slot == "map":
                ws._open_map()
            elif slot == "run":
                ws.run_code() if ws.editor is not None else ws.apply()
        elif slot == "run" and ws.launcher.selected() is not None:
            ws.open()                            # on the home desktop, run = open selected

    def handle_home_tap(self, px, py):
        """The launcher/home top-bar tap slice: the clock Easter egg, the ≡ system
        menu, and the NEW/DUP/DEL management icons. Returns True if the bar consumed
        the tap. The clock-run reset runs for ANY non-clock tap (byte-identical to the
        pre-migration launcher pointer), so page/tile taps that fall through still
        reset it."""
        ws = self.ws
        lay = ws.layout
        # Clock Easter egg (#21): tapping the status-strip clock _CLOCK_TAP_GOAL
        # times wakes the Time Traveler. Checked before the management row so a
        # tap on the clock never falls through to a button.
        if self._in(px, py, lay.clock_hit()):
            ws.ach_ui._tap_clock()
            return True
        ws.ach_ui._clock_taps = 0                # any other desktop tap resets the run
        if self._in(px, py, lay.sysmenu_btn):    # ≡ -> system menu (Settings/About/Reboot, #52)
            ws.toggle_sysmenu()
            return True
        if ws.can_manage and self._in(px, py, lay.new_btn):
            ws.new_cart(); return True
        if ws.can_manage and self._in(px, py, lay.dup_btn):
            ws.dup_cart(); return True
        if ws.can_manage and self._in(px, py, lay.del_btn):
            ws.del_cart(); return True
        return False

    def handle_cart_tap(self, px, py):
        """The running-cart top-bar tap slice (px, py in GAME coords): the TIC-80
        one-tap tool switcher (≡ / HOME / EDIT|CODE / PAINT / MAP / BLOCKS / MUSIC).
        Returns True if a tool switch consumed the tap (so the pause QUIT/CONTINUE
        handling in the desktop pointer is skipped)."""
        ws = self.ws
        if self._in(px, py, _SYSMENU_BTN):
            ws.toggle_sysmenu()      # ≡ -> open the dropdown system menu (#52)
        elif self._in(px, py, _HOME_BTN):
            ws.go_home()
        elif self._in(px, py, _MENU_BTN):
            ws._open_menu()
        elif self._in(px, py, _PAINT_BTN):
            ws._open_paint()
        elif self._in(px, py, _MAP_BTN):
            ws._open_map()
        elif self._in(px, py, _BLOCKS_BTN):
            ws._open_blocks()
        elif self._in(px, py, _MUSIC_BTN):
            ws._open_music()
        else:
            return False
        return True
