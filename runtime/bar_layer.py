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

# -- the ZONED bar's fixed GAME-canvas right cluster (Stage 4 of
# docs/shell_ux_technical_plan_v1.md, #46 macOS-menu-bar model): reused by the
# game-domain Editor tabs (cards/paint/map -- see EditorApp/cards_layer.py/
# paint_layer.py/layers.py's _MapLayer) that draw on the SAME fixed 320x240 canvas
# as the running-cart pause/crash bar above. This is a SEPARATE cluster from
# _SYSMENU_BTN/_HOME_BTN/.../_BAR_WIFI/_BAR_CLOCK -- those stay the pause bar's own
# fixed rects, untouched until Stage 5 retires the pause screen -- so the two
# mechanisms can never collide or desync.
_ZONE_BATT = _BAR_BATT                                          # same rightmost slot
_ZONE_WIFI = _BAR_WIFI                                           # same slot
_ZONE_GEAR = (_ZONE_WIFI[0] - _BAR_STRIDE, _BAR_Y, _BAR_ICON, _BAR_ICON)
# Reserved for the Stage-5 context X (tap to exit the active app) -- NOT drawn or
# tapped yet (that's Stage 5's job); the slot is carved out of the right cluster
# now so the X's arrival doesn't reflow the clock/gear/wifi/batt again.
_ZONE_CONTEXT_X = (_ZONE_GEAR[0] - _BAR_STRIDE, _BAR_Y, _BAR_ICON, _BAR_ICON)
_ZONE_CLOCK = (_ZONE_CONTEXT_X[0] - 2 - 5 * 8, 0, 5 * 8, 18)
_ZONE_LEFT_GAME = (2, _BAR_Y, _ZONE_CLOCK[0] - 4, _BAR_ICON)     # the app-lent rect

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
    its own surface (docs/shell_layers_refactor_v1.md Phase 2) and reshaped into a
    ZONED bar (Stage 4 of docs/shell_ux_technical_plan_v1.md, the macOS-menu-bar
    model): a RIGHT zone (OS-owned: clock/wifi/batt/gear + a slot reserved for the
    Stage-5 context X) drawn by the bar itself, and a LEFT zone LENT to whichever
    app is active -- `launcher_layer`/`settings_layer`/`editor_app` each implement
    `draw_zone(cv, rect)` (paint the lent rect) + `zone_tap(px, py)` (claim a tap
    inside it) + `zone_gen` (an int bumped whenever their zone's pixels change).

    `where` still selects which screen is asking (`"home"` / `"settings"` /
    `"menu"` / `"desktop"`), but it's no longer three-plus-one hardcoded draw
    bodies: `"desktop"` stays the running-cart CRASH chrome (Stage 5 retired the
    #71 pause frame -- the bar shows only on a crash now, never during play -- see
    `_render_cart_bar`'s early branch, which returns before any zone dispatch, so
    `draw_zone` is structurally unreachable while a Player is top-of-stack); the
    other three route through the SAME generalized cache (below) into whichever
    app owns `where`.

    The #43 strip cache is KEPT AND GENERALIZED, not duplicated: `_cart_bar_key`
    now takes `where` (default `"desktop"`, so the pinned zero-arg tests are
    unchanged) and folds in the active app's `zone_gen` + `ws.can_manage`, so ONE
    offscreen strip serves all four `where` values and re-renders only on a real
    change (app switch / clock tick / zone-content change / theme edit) -- never a
    bare frame. `_render_cart_bar(cv, key)` branches on `key[0]` (`where`) to
    render either the untouched desktop body or the new zoned-bar body.

    Boundary decisions (deliberate, see the doc):
      * The button-rect + dock CONSTANTS are the module-level single source above --
        the golden harness + tests reference console._HOME_BTN / _MENU_BTN /
        _DOCK_SLOTS / ... (re-exported by console.py), and Layout also uses them.
      * The shared draw toolkit (ws._glyph / ws._icon / ws._mini_btn / ws._bar_image)
        stays on Workstation per the doc; the bar is a CONSUMER of it (via self.ws).
      * `_bar_img_cache` (per-kind icon Image cache backing ws._icon) stays on ws;
        `_bar_cache_gen` (the strip cache generation) lives here and
        ws.set_icon_sheet bumps it via invalidate().

    The bar is CHROME the content composes, not a standalone stack layer: it's
    SYSTEM-domain on launcher/settings/the code+blocks editor tabs (drawn on
    sys_canvas) but GAME-domain on the running cart AND the cards/paint/map editor
    tabs (drawn on the fixed game canvas), and it draws at a fixed point inside each
    content's paint. So the content draws call _draw_status_strip(where)/
    _draw_dock(where) and the pointer methods delegate their bar slice to
    handle_bar_tap(where, ...) / handle_home_tap / handle_cart_tap /
    _dock_slot_at + _activate_dock.

    (Stage-4 ROLLOUT: CODE now draws the SAME zoned bar too -- its title + RUN/SAVE/
    CLOSE band was dissolved into it (BLOCKS/MUSIC fold in next). CODE is on the SYSTEM
    canvas like the launcher/Settings bar, so `_zone_is_game` stays False for it and the
    app's zone_tap takes the lent rect as a parameter; the game-canvas tabs
    (cards/paint/map, MUSIC next) keep the fixed _ZONE_LEFT_GAME rect.)

    `NAMES` (palette) and `_in` (rect hit-test) are injected at construction (the same
    circular-import dodge the other extracted UIs use)."""

    def __init__(self, ws, names, in_rect):
        self.ws = ws
        self._NAMES = names
        self._in = in_rect
        # Cached top bar (#43, generalized in Stage 4 to every `where`): rendered
        # ONCE into an offscreen strip and blitted each frame (one flat copy)
        # instead of re-rendering ~9 sprites + glyph + text every frame.
        # `_cart_bar_strip` is the layer; `_cart_bar_key_cur` is the state key it
        # holds (None = stale); `_cart_bar_canvas` is the canvas it was built on (a
        # web-view swap, OR switching between the game canvas and the system canvas
        # as the active `where` changes, forces a rebuild); `_bar_cache_gen` is
        # bumped by the explicit invalidators (invalidate(), from ws.set_icon_sheet)
        # so a theme swap repaints. ONE strip/key slot serves every `where` -- only
        # one screen is ever on top at a time, so sharing it is exactly as safe as
        # the single-slot cache already was, and a `where` change is just another
        # key change the existing compare already catches.
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
        """Entry point every content Layer calls; `where` is "home" / "settings" /
        "menu" / "desktop". All four route through the ONE generalized cache
        mechanism below (_draw_top_bar_cart) -- there is no more per-`where` draw
        body here; the zoning happens inside _render_cart_bar."""
        self._draw_top_bar_cart(where)

    # -- zone plumbing (Stage 4, #46 zoned bar) -------------------------------
    #
    # "where" identifies which screen is asking; these helpers answer the THREE
    # questions that differ per `where`: who owns the lent left zone, which canvas
    # + rect it draws into, and (for "menu") whether the CURRENT tab draws on the
    # fixed 320x240 GAME canvas (cards/paint/map, MUSIC next) or the responsive SYSTEM
    # canvas (code, like the launcher/Settings) -- both show the SAME bar now.

    def _zone_owner(self, where):
        """The app that owns `where`'s lent left zone, or None ("desktop" -- the
        Player's crash chrome never lends a zone, see _render_cart_bar)."""
        ws = self.ws
        if where == "home":
            return ws.launcher_layer
        if where == "settings":
            return ws.settings_layer
        if where == "menu":
            return ws.editor_app
        return None

    def _zone_is_game(self, where):
        """True for the "menu" tabs that draw on the fixed 320x240 GAME canvas
        (cards/paint/map/music, like the running cart) rather than the responsive
        SYSTEM canvas (code/blocks, like the launcher/Settings)."""
        return where == "menu" and self.ws.menu_view in ("cards", "paint", "map", "music")

    def _bar_canvas(self, where):
        if where == "desktop" or self._zone_is_game(where):
            return self.ws.canvas
        return self.ws.sys_canvas

    def _bar_h(self, where):
        if where == "desktop" or self._zone_is_game(where):
            return _STATUS_H
        return self.ws.layout.status_h

    def _zone_rect(self, where):
        """The rect LENT to `where`'s app for its draw_zone/zone_tap (never asked
        for "desktop", whose branch returns before any zone dispatch)."""
        if self._zone_is_game(where):
            return _ZONE_LEFT_GAME
        return self.ws.layout.zone_left

    # -- draw + cache (the #43 strip, generalized to every `where`) -----------

    def _draw_top_bar_cart(self, where="desktop"):
        """The ONE cached bar draw for every `where` (Stage 4 generalizes what was
        the running-cart-only strip cache): render the pixels ONCE into an
        offscreen strip and blit_strip it each frame (one flat copy) instead of
        re-rendering ~9 16x16 sprites + a glyph + text every frame -- ~6ms of
        wasted draw the #43 cache exists to avoid, now paid by every zoned screen,
        not just the running cart. When the key changes (app switch / clock tick /
        zone-content change / theme edit / font-size change) the strip is
        re-rendered, then reused. The strip is purely the DRAW; hit-testing goes
        through the independent _*_BTN rects / zone_tap, so caching can't desync
        taps."""
        cv = self._bar_canvas(where)
        bar_h = self._bar_h(where)
        key = self._cart_bar_key(where)
        strip = self._cart_bar_strip
        # The active canvas can SWAP at runtime (the device web view binds a recording
        # TeeCanvas in place of the raw DeviceCanvas, #41) OR change identity/height as
        # `where` moves between the fixed game canvas and the responsive system canvas
        # (Stage 4). A strip allocated on the OLD canvas/height is the wrong layer for
        # the new one (a raw DeviceCanvas strip blitted through the Tee has no
        # RecordingLayer hooks -- '_end_batch' AttributeError -- and it wouldn't be
        # recorded for the browser anyway), so rebuild whenever either changes, not
        # just on a width resize.
        canvas_changed = self._cart_bar_canvas is not cv
        size_changed = strip is None or strip.w != cv.w or strip.h != bar_h
        if size_changed or canvas_changed or self._cart_bar_key_cur != key:
            # (Re)build the cached strip. new_layer gives a same-type/-palette canvas the
            # bar body draws into at the SAME coords (the bar lives at y in [0, bar_h),
            # which maps 1:1 onto the strip's rows), so the cached pixels are byte-identical
            # to drawing straight onto cv. Reuse the buffer across re-renders when the size
            # is unchanged; allocate a fresh layer on first build / a resize / a canvas swap.
            if size_changed or canvas_changed:
                strip = cv.new_layer(cv.w, bar_h)
                self._cart_bar_strip = strip
                self._cart_bar_canvas = cv
            self._render_cart_bar(strip, key)
            self._cart_bar_key_cur = key
        cv.blit_strip(strip, 0, 0)

    def _cart_bar_key(self, where="desktop"):
        """The cache key for the bar: every piece of state that changes ANY `where`
        variant's PIXELS. A different key forces a strip re-render; an unchanged key
        reuses the cached strip. `where` is key[0] (so a screen switch always
        invalidates); then the clock text (ticks ~once/min), whether the open cart
        has an edit schema (EDIT vs CODE icon, "desktop" only), the icon theme
        identity + the glyph font scale (a theme edit / resize must repaint),
        whether writes are enabled (the launcher's NEW/DUP/DEL visibility), the
        active app's OWN `zone_gen` (Stage 4: an int the app bumps whenever ITS
        lent zone's content changes -- e.g. EditorApp.set_tab, Launcher.sel/
        set_items -- so a real zone-content change is the ONLY thing that forces a
        re-render beyond the shared dimensions here), and a generation counter the
        explicit invalidators bump (set_icon_sheet, etc.)."""
        ws = self.ws
        owner = self._zone_owner(where)
        has_edit = bool(ws.cart.get("edit")) if ws.cart else False
        return (where, self._clock_text(), has_edit, id(ws.icon_sheet),
                getattr(self._bar_canvas(where), "font_scale", 1),
                bool(ws.can_manage),
                owner.zone_gen if owner is not None else 0,
                ws._wifi_icon_kind(),      # Part 3: wifi status glyph (connect/disconnect repaints)
                self._bar_cache_gen)

    def _render_cart_bar(self, cv, key):
        """Render the bar's pixels onto `cv` (the offscreen strip, or any canvas)
        for `key[0]` (`where`). Factored out of _draw_top_bar_cart so the SAME
        drawing serves both the cache build and the (test/fallback) direct path,
        which is what makes the cached strip pixel-identical to a direct render.
        `key` carries the already-computed has_edit (index 2) so the icon choice
        can't drift from the key."""
        NAMES = self._NAMES
        ws = self.ws
        where = key[0]
        has_edit = key[2]
        if where == "desktop":
            # The running-cart CRASH chrome: the Player's own top bar, drawn only on a
            # crash (Stage 5 retired the #71 pause frame -- never during play), never a
            # "zone" (no draw_zone call below this branch's `return`, which is exactly
            # what keeps the zoned-bar dispatch off the play frame -- see the Stage-4
            # guardrail test, now driven through the crash state).
            cv.rect(0, 0, cv.w, _STATUS_H, NAMES["black"])
            cv.rect(0, _STATUS_H - 1, cv.w, 1, NAMES["dark_grey"])   # shelf edge line
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
            ws._icon(ws._wifi_icon_kind(), _BAR_WIFI[0], _BAR_WIFI[1], cv)
            ws._icon("batt", _BAR_BATT[0], _BAR_BATT[1], cv)
            return
        # -- the zoned bar (Stage 4): a black backing band (with a thin shelf edge
        # line below), the OS-owned RIGHT zone, then the active app's LENT left zone.
        bar_h = self._bar_h(where)
        cv.rect(0, 0, cv.w, bar_h, NAMES["black"])
        cv.rect(0, bar_h - 1, cv.w, 1, NAMES["dark_grey"])           # shelf edge line
        self._render_right_zone(cv, where)
        owner = self._zone_owner(where)
        if owner is not None:
            owner.draw_zone(cv, self._zone_rect(where))

    def _render_right_zone(self, cv, where):
        """The OS-owned right zone (Stage 4): clock, wifi, batt, the ≡ system-menu
        toggle (moved off the left edge -- the macOS-menu-bar model keeps every OS
        control on one side), and -- Stage 5 -- the context X (tap to EXIT the active
        app back toward the launcher root). Two geometries: the fixed game-canvas
        cluster (cards/paint/map, mirrors the crash bar's right cluster) or the
        responsive Layout-driven one (home/settings/code/blocks). The launcher IS the
        back-stack root, so it draws NO X (spec Section 9) -- only where != "home"."""
        NAMES = self._NAMES
        ws = self.ws
        show_x = where != "home"          # the launcher root never exits -> no X
        if self._zone_is_game(where):
            cv.print(self._clock_text(), _ZONE_CLOCK[0], 3, NAMES["light_grey"], 1)
            ws._icon(ws._wifi_icon_kind(), _ZONE_WIFI[0], _ZONE_WIFI[1], cv)
            ws._icon("batt", _ZONE_BATT[0], _ZONE_BATT[1], cv)
            ws._glyph("menu", _ZONE_GEAR, NAMES["white"], cv)
            if show_x:                    # context X (Stage 5): tap to exit the app
                ws._icon("close", _ZONE_CONTEXT_X[0], _ZONE_CONTEXT_X[1], cv)
        else:
            lay = ws.layout
            cv.print(self._clock_text(), lay.clock_x, 3, NAMES["light_grey"], 1)
            ws._icon(ws._wifi_icon_kind(), lay.wifi_btn[0], lay.wifi_btn[1], cv)
            ws._icon("batt", lay.batt_btn[0], lay.batt_btn[1], cv)
            ws._glyph("menu", lay.sysmenu_btn, NAMES["white"], cv)
            if show_x:                    # context X (Stage 5): tap to exit the app
                ws._icon("close", lay.context_x_btn[0], lay.context_x_btn[1], cv)

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
            ws.launch_selected()                 # home run = open selected (tap-mode, #55)

    def handle_bar_tap(self, where, px, py):
        """The zoned bar's tap slice (Stage 4), shared by every screen it draws on
        (home/settings/menu): the clock Easter egg, the ≡ system menu, the wifi status
        icon (Part 3: taps LAUNCH the wifi tool), and -- Stage 5 -- the context X are
        OS/right-zone-owned, IDENTICAL wherever the bar shows (same as the draw), checked
        first so a tap on any never falls through to the lent zone.
        Anything else routes to the active app's zone_tap over its lent left zone.
        Returns True iff the bar (or the app's zone) consumed the tap. The clock-
        run reset runs for ANY non-clock tap (byte-identical to the pre-Stage-4
        launcher pointer), so taps that fall through to the content still reset it."""
        ws = self.ws
        if self._zone_is_game(where):
            clock_hit, gear_hit, x_hit = _ZONE_CLOCK, _ZONE_GEAR, _ZONE_CONTEXT_X
            wifi_hit = _ZONE_WIFI
        else:
            lay = ws.layout
            clock_hit, gear_hit, x_hit = lay.clock_hit(), lay.sysmenu_btn, lay.context_x_btn
            wifi_hit = lay.wifi_btn
        # Clock Easter egg (#21): tapping the bar's clock _CLOCK_TAP_GOAL times
        # wakes the Time Traveler. Checked before the ≡/X/zone so a tap on the clock
        # never falls through to a button.
        if self._in(px, py, clock_hit):
            ws.ach_ui._tap_clock()
            return True
        ws.ach_ui._clock_taps = 0                # any other bar tap resets the run
        if self._in(px, py, gear_hit):           # ≡ -> system menu (Settings/About/Reboot, #52)
            ws.toggle_sysmenu()
            return True
        # WiFi status icon (Part 3): tap -> LAUNCH the wifi.moy tool (you run it, you don't
        # edit it). Consumes the tap even if the tool isn't installed, so it never leaks to
        # the lent zone underneath the OS-owned right cluster.
        if self._in(px, py, wifi_hit):
            ws.launch_wifi_tool()
            return True
        # Context X (Stage 5, spec Section 9): tap to EXIT the active app back toward the
        # launcher root. The launcher draws no X (where == "home") so it's not tested
        # there -- it is the root and never exits.
        if where != "home" and self._in(px, py, x_hit):
            ws.exit()
            return True
        owner = self._zone_owner(where)
        # Pass the SAME lent rect the draw used (game-canvas _ZONE_LEFT_GAME for
        # cards/paint/map/music, the responsive layout.zone_left for the system-canvas
        # code/blocks tabs + launcher/Settings), so an app's zone_tap hit-tests its
        # icons at the exact positions draw_zone drew them regardless of canvas.
        return bool(owner.zone_tap(px, py, self._zone_rect(where))) if owner is not None else False

    def handle_home_tap(self, px, py):
        """Backward-compatible name for the launcher's tap slice (launcher_layer.py
        is the one caller outside this file) -- the launcher's own zone_tap (NEW/
        DUP/DEL) now lives on LauncherHomeLayer, reached through handle_bar_tap."""
        return self.handle_bar_tap("home", px, py)

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
