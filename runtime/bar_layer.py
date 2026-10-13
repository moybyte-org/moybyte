# Map (grep -n a name to jump there):
#   -- bar geometry           the bar's constants (console.py imports them back)
#   BarLayer                  the unified 18px top bar: zones, clock, status, taps
#   BarLayer.invalidate       repaint the cached running-cart bar
#   BarLayer.bar_rects        where the bar is on a surface; content_rect, what it leaves
#   BarLayer.redraw_clock     repaint just the clock cell
#   BarLayer.handle_bar_tap   the zoned bar's tap slice, shared by every surface
#   BarLayer.handle_cart_tap  the running-cart bar's tap slice
"""The unified 18px top bar (#46), extracted from Workstation
(runtime/console.py) as its own surface -- docs/history/shell_layers_refactor_v1.md.

This module is the SINGLE SOURCE of the bar geometry constants (`_STATUS_H`,
`_BAR_*`, the fixed 320x240 tool-switcher button rects `_HOME_BTN`/`_MENU_BTN`/...).
They're consumed BOTH here (BarLayer's draw + hit-testing) AND by console.py's own
chrome (the `Layout` class + a few derived constants + the golden harness/tests that
read `console._HOME_BTN`), so rather than duplicate them (drift), console.py imports
them back from here (re-exported under the same names) -- the same pattern
block_editor_ui.py uses for its `_BLK_*`.

The BOTTOM DOCK this module used to own is GONE (2026-08-21): a six-slot
HOME/CODE/DRAW/MAP/RUN/SET tool switcher left over from the pre-2026-07 shell,
whose slots still routed into that shell's entry points. The 2026-07 shell
navigates by the zoned bar, the Make tile and the context-X, and the launcher had
already stopped drawing the dock in #46 -- Settings was its last caller, on the
fullscreen tiers only (the windowed WM suppressed it). Do not re-grow it;
`tests/test_desktop_shell.py::test_the_pre_2026_07_bottom_dock_is_gone` is the
ratchet.

`BarLayer` reaches everything else through its `self.ws` back-ref (the shared draw
toolkit ws._glyph/_icon/_mini_btn stays on Workstation; the bar is a consumer). Only
`NAMES` (palette) is injected at construction -- the same circular-import dodge the
other extracted UIs use, since console.py builds the one BarLayer instance a
Workstation holds; the rect hit-test `_in` is widgets', imported directly. The trivial time helpers `_ticks_ms`/
`_ticks_diff` are duplicated here (time-only, like achievements_ui.py) for the clock.
"""
import time

try:
    from layout_base import BAR_ROWS, bar_y, content_of
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.layout_base import BAR_ROWS, bar_y, content_of


# -- bar geometry (single source; console.py imports these back) --------------
# Every rect here is in BAR coordinates (row 0 is the bar's first row, wherever
# BarLayer places the bar). The bars on the fixed 320x240 GAME canvas take these
# fixed constants; the launcher/Settings bar reflows via Layout on the SYSTEM
# canvas. The hamburger (≡) is the LEFT-MOST icon (slot 0); the tool switchers
# HOME/EDIT/PAINT/MAP/BLOCKS/MUSIC follow one stride apart.
_BAR_ICON = 16              # icon sprite side (16x16, from the IconSheet)
_BAR_GAP = 2               # px between adjacent bar icons
_BAR_STRIDE = _BAR_ICON + _BAR_GAP        # 18: left-edge step between icons
_BAR_Y = 1                 # icons sit 1px down in the 18px bar (1px top/bottom margin)
# Cached bar strips kept per `where`, one per destination canvas. 2 covers the
# windowed WM's root-canvas / window-buffer alternation; each costs cv.w * bar_h * 2
# bytes (~36KB at 1024 wide), so this is a small, bounded PSRAM trade for not
# rebuilding the strip on every switch.
_BAR_STRIP_SLOTS = 2


def _release_layer(lay):
    """A strip is a root-canvas layer, off the gc heap on a board (nothing
    collects it): an evicted or outgrown strip is given back here, or every
    window the desk opens leaks its bar strip (~80KB a round on the Guition
    P4, 2026-09-09). Probed: a recording tier's layer has no release."""
    rel = getattr(lay, "release", None)
    if rel is not None:
        rel()
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
_STATUS_H = BAR_ROWS    # the bar's rows (16px icons + 1px top/bottom margin)

# -- the ZONED bar's fixed GAME-canvas right cluster (Stage 4 of
# docs/history/shell_ux_technical_plan_v1.md, #46 macOS-menu-bar model): the
# running tool/app's strip where it draws on the fixed 320x240 game canvas
# (`BarLayer._zone_is_game`). This is a SEPARATE cluster from
# _SYSMENU_BTN/_HOME_BTN/.../_BAR_WIFI/_BAR_CLOCK -- the crash bar's own fixed
# rects -- so the two mechanisms can never collide or desync.
_ZONE_BATT = _BAR_BATT                                          # same rightmost slot
_ZONE_WIFI = _BAR_WIFI                                           # same slot
_ZONE_GEAR = (_ZONE_WIFI[0] - _BAR_STRIDE, _BAR_Y, _BAR_ICON, _BAR_ICON)
# The context X (tap to exit the active app).
_ZONE_CONTEXT_X = (_ZONE_GEAR[0] - _BAR_STRIDE, _BAR_Y, _BAR_ICON, _BAR_ICON)
_ZONE_CLOCK = (_ZONE_CONTEXT_X[0] - 2 - 5 * 8, 0, 5 * 8, 18)
_ZONE_LEFT_GAME = (2, _BAR_Y, _ZONE_CLOCK[0] - 4, _BAR_ICON)     # the app-lent rect


try:                                    # device: ticks is frozen flat
    from ticks import _ticks_ms, _ticks_diff
except ImportError:                     # host: the runtime package
    from runtime.ticks import _ticks_ms, _ticks_diff

try:
    from widgets import _in
except ImportError:                     # host: the runtime package
    from runtime.widgets import _in

try:
    import chrome_ops as _ch
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime import chrome_ops as _ch
try:
    import ui as _ui
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime import ui as _ui



def _edit_kind(cart):
    """The crash bar's EDIT|CODE slot for `cart`: True for a cart with a
    Make-it-mine schema (pencil), False for code (the < > glyph), None for a
    compiled ("runtime": "wasm") cart, whose trap offers no EDIT action at
    all (docs/wasm_tier_plan_2026-09.md)."""
    if not cart:
        return False
    if cart.get("runtime") == "wasm":
        return None
    return bool(cart.get("edit"))

class BarLayer:
    """The unified 18px top bar (#46), migrated out of Workstation as
    its own surface (docs/history/shell_layers_refactor_v1.md Phase 2) and reshaped into a
    ZONED bar (Stage 4 of docs/history/shell_ux_technical_plan_v1.md, the macOS-menu-bar
    model): a RIGHT zone (OS-owned: clock/wifi/batt/gear + a slot reserved for the
    Stage-5 context X) drawn by the bar itself, and a LEFT zone LENT to whichever
    app is active -- `launcher_layer`/`settings_layer`/`editor_app` each implement
    `draw_zone(cv, rect)` (paint the lent rect) + `zone_tap(px, py)` (claim a tap
    inside it) + `zone_gen` (an int bumped whenever their zone's pixels change).

    `where` still selects which screen is asking (`"home"` / `"settings"` /
    `"menu"` / `"desktop"` / `"tool"`), but it's no longer a set of hardcoded draw
    bodies: `"desktop"` stays the running-cart CRASH chrome (Stage 5 retired the
    #71 pause frame -- for a GAME the bar shows only on a crash, never during play);
    `"tool"` (Part 4) is the minimal bar a running TOOL/APP shows so it's exitable
    (title + status + context-X, NO tab ladder). BOTH the `"desktop"` and `"tool"`
    branches return before any `owner.draw_zone` call, so `draw_zone` stays
    structurally unreachable while a Player is top-of-stack (the perf guardrail); the
    home/settings/menu three route through the SAME generalized cache (below) into
    whichever app owns `where`.

    The #43 strip cache is KEPT AND GENERALIZED, not duplicated: `_cart_bar_key`
    now takes `where` (default `"desktop"`, so the pinned zero-arg tests are
    unchanged) and folds in the active app's `zone_gen` + `ws.can_manage`, so ONE
    offscreen strip serves all four `where` values and re-renders only on a real
    change (app switch / clock tick / zone-content change / theme edit) -- never a
    bare frame. `_render_cart_bar(cv, key)` branches on `key[0]` (`where`) to
    render either the untouched desktop body or the new zoned-bar body.

    Boundary decisions (deliberate, see the doc):
      * The button-rect CONSTANTS are the module-level single source above --
        the golden harness + tests reference console._HOME_BTN / _MENU_BTN / ...
        (re-exported by console.py), and Layout also uses them.
      * The shared draw toolkit (ws._glyph / ws._icon / ws._mini_btn / ws._bar_image)
        stays on Workstation per the doc; the bar is a CONSUMER of it (via self.ws).
      * `_bar_img_cache` (per-kind icon Image cache backing ws._icon) stays on ws;
        `_bar_cache_gen` (the strip cache generation) lives here and
        ws.look.set_icon_sheet bumps it via invalidate().

    The bar is CHROME the content composes, not a standalone stack layer: it's
    SYSTEM-domain on the launcher, Settings and every Editor tab (drawn on
    sys_canvas) and GAME-domain on the running cart's crash chrome and a running
    tool's strip (drawn on the fixed game canvas, `_zone_is_game`), and it draws
    at its rect (`bar_rects`) inside each content's paint, which lays out in what
    the bar leaves (`content_rect`). So the content draws call
    _draw_status_strip(where) and the pointer methods delegate their bar slice to
    handle_bar_tap(where, ...) / handle_home_tap / handle_cart_tap, which take
    canvas coordinates and hit-test in bar coordinates. An app's zone_tap takes
    the lent rect as a parameter: the fixed _ZONE_LEFT_GAME on the game canvas,
    the responsive layout.zone_left elsewhere.

    `NAMES` (palette) is injected at construction (the same circular-import dodge
    the other extracted UIs use)."""

    def __init__(self, ws, names):
        self.ws = ws
        self._NAMES = names
        # Cached top bar (#43, generalized in Stage 4 to every `where`): rendered
        # ONCE into an offscreen strip and blitted each frame (one flat copy)
        # instead of re-rendering ~9 sprites + glyph + text every frame.
        # `_bar_strips` maps `where` -> a small MRU list of [strip, key, canvas]:
        # the offscreen layer,
        # the state key it holds (None = stale) and the canvas it was built on (a
        # web-view swap, OR switching between the game canvas and the system canvas,
        # forces a rebuild); `_bar_cache_gen` is bumped by the explicit invalidators
        # (invalidate(), from ws.look.set_icon_sheet) so a theme swap repaints. Keyed per
        # `where` (was one shared slot) because the windowed WM (wm_windowed.py)
        # draws TWO bars per frame -- the desktop root's "home" bar and the focused
        # window's own -- and a shared slot would rebuild both every frame. On the
        # fullscreen-stack tiers only one `where` is ever active, so the dict holds
        # one live entry, exactly as cheap as the old single slot.
        #
        # Each `where` keeps up to _BAR_STRIP_SLOTS of them, one per destination
        # canvas (see _draw_top_bar_cart). Two is the measured alternation -- root
        # canvas vs window buffer; a third destination would simply thrash as
        # before, which is a perf floor, not a correctness problem.
        self._bar_strips = {}
        self._bar_cache_gen = 0
        # Clock-text cache (#66): (second, string) -- see _clock_text.
        self._clock_at = -1
        self._clock_cache = ""
        # The root canvas the bar strips are published to (publish_strips),
        # and the clock text redraw_clock last painted there.
        self._strip_cv = None
        self._clock_drawn = None

    def invalidate(self):
        """Repaint the cached running-cart bar on the next frame (a theme/IconSheet
        swap changed its pixels). Called by ws.look.set_icon_sheet."""
        self._bar_cache_gen += 1

    # -- the strips a rotated compositor presents ------------------------------

    def publish_strips(self, cv=None):
        """Hand the bar strips to a root canvas that presents them as rects of
        their own (the rotated compositor's `set_strips`; absent elsewhere):
        the tallest top bar the layout draws. `cv` is the root, kept for the
        relayouts that publish again; every repaint that changes a strip's
        pixels on it says so through `_strip_moved`."""
        if cv is not None:
            self._strip_cv = cv
        cv = self._strip_cv
        ss = getattr(cv, "set_strips", None)
        if ss is None:
            return
        bh = max(self.ws.layout.status_h, self._bar_h("desktop"))
        ss([(0, bar_y(cv.h, bh), cv.w, bh)])

    def _strip_moved(self, cv, x, y, w, h):
        """New pixels landed in (x, y, w, h) of the bar on `cv`: when that is
        the published root, the strips there ride its next presented frame."""
        if cv is self._strip_cv:
            sm = getattr(cv, "strip_moved", None)
            if sm is not None:
                sm(x, y, w, h)

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
    # + rect it draws into, and whether it draws on the fixed 320x240 GAME canvas
    # (a running tool's strip) or the responsive SYSTEM canvas -- both show the
    # SAME bar.

    def _zone_owner(self, where):
        """The app that owns `where`'s lent left zone, or None ("desktop" -- the
        Player's crash chrome never lends a zone, see _render_cart_bar)."""
        ws = self.ws
        if where == "home":
            return ws.launcher_layer
        if where == "picker":
            return ws.editor_picker
        if where == "settings":
            return ws.settings_layer
        if where == "menu":
            return ws.editor_app
        return None

    def _zone_is_game(self, where):
        """True for the surfaces that draw on the fixed 320x240 GAME canvas -- since
        the #39 step-3 conversions moved every Editor tab onto the responsive SYSTEM
        canvas, that's only the Part-4 "tool" bar (a running tool/app is on the game
        canvas, like the running cart's crash chrome).

        The one exception is a RESPONSIVE app cart (#181): it drew on the SYSTEM
        canvas, so its exitable strip has to be the responsive geometry too --
        the fixed cluster would leave the context-X stranded 700px left of the
        right edge on the P4. `app_full_canvas` is False for every game, every
        fixed app cart and every shipped system app, so nothing else moves.

        "app" is a REGISTERED system app's strip (console.py's host guarantee):
        the app draws on the SYSTEM canvas, so its strip does too, and takes
        the fixed cluster only where that canvas IS the 320x240 game canvas.
        Drawn on the game canvas instead, a board with a separate system
        canvas (the Guition S3's 480x320) showed the app's bar band empty and
        had no context-X under the finger."""
        ws = self.ws
        if where == "app":
            return ws.sys_canvas is ws.canvas
        return where == "tool" and not getattr(ws, "app_full_canvas", False)

    def _bar_canvas(self, where):
        if where == "app":
            return self.ws.sys_canvas
        if where == "desktop" or self._zone_is_game(where):
            return self.ws.canvas
        return self.ws.sys_canvas

    def _bar_h(self, where):
        if where == "desktop" or self._zone_is_game(where):
            return _STATUS_H
        return self.ws.layout.status_h

    # -- where the bar is: the one author for draws and taps -------------------
    #
    # A bar's pixels and its hit rects (the right zone's, the lent zone's) are
    # in BAR coordinates, row 0 its first; the bar itself sits where
    # layout_base.place_bar put it on `where`'s canvas. The running cart's crash
    # chrome ("desktop") is the Player's crash panel, which lays out below a
    # top strip, so it stays at row 0.

    def _bar_y(self, where, cv, bh):
        return 0 if where == "desktop" else bar_y(cv.h, bh)

    def bar_rects(self, where):
        """Every bar `where` shows, as (x, y, w, h) on its canvas."""
        cv = self._bar_canvas(where)
        bh = self._bar_h(where)
        return ((0, self._bar_y(where, cv, bh), cv.w, bh),)

    def bar_rect(self, i, where):
        return self.bar_rects(where)[i]

    def content_rect(self, where):
        """`where`'s canvas less its bars: what the surface under them lays
        out in, (x, y, w, h)."""
        cv = self._bar_canvas(where)
        return content_of(cv.w, cv.h, self._bar_h(where))

    def bar_at(self, where, px, py):
        """True when (px, py) on `where`'s canvas is on one of its bars."""
        for r in self.bar_rects(where):
            if _in(px, py, r):
                return True
        return False

    def _zone_rect(self, where):
        """The rect LENT to `where`'s app for its draw_zone/zone_tap (never asked
        for "desktop", whose branch returns before any zone dispatch)."""
        if self._zone_is_game(where):
            return _ZONE_LEFT_GAME
        return self.ws.layout.zone_left

    def zone_band_light(self, where):
        """True when `where`'s bar band is LIGHT -- an in-window band under a
        theme whose window bands are light (`band_light`), or ANY band when the
        theme's OS bar itself is light (`bar_light`) -- zone owners pick their band
        ink with this so lent-zone text stays readable on either band."""
        th = self.ws.theme_colors
        if th.get("bar_light", False):
            return True
        return self._in_window(where) and th.get("band_light", False)

    def _in_window(self, where):
        """True when `where`'s bar is being drawn INSIDE a window of the windowed
        WM (#73): every zoned screen except the desktop root's "home" bar. The
        window bar then suppresses the OS right zone (no copied taskbar) -- the
        WM's title strip carries min/max/close instead. Always False on the
        fullscreen-stack tiers (ws.windowed_chrome stays False there)."""
        return getattr(self.ws, "windowed_chrome", False) and where not in (
            "home", "desk", "desktop", "tool", "app")

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
        if hasattr(cv, "_lr"):
            # The canvas is itself a RecordingLayer (a windowed-WM window buffer on
            # the web console): a strip cache NESTED in a recorded layer can't ship
            # (blit_strip of a raw strip isn't a recordable op), so render the bar
            # directly -- a live window re-records its whole stream anyway, and the
            # dozen icon/text commands are far lighter than an inlined pixel strip.
            by = self._bar_y(where, cv, bar_h)
            if by:
                cv.camera(0, -by)
            self._render_cart_bar(cv, key)
            if by:
                cv.camera(0, 0)
            return
        # One slot PER CANVAS, most-recently-used first. The windowed WM draws the
        # same `where` into DIFFERENT canvases from frame to frame -- straight into
        # the root canvas through a viewport on a quiet frame (wm_windowed
        # _direct_render), into the window's own buffer otherwise -- so a slot that
        # remembers only one canvas reads every switch as a canvas swap and rebuilds.
        # That cost 72ms of an 86ms Settings frame on P4 glass, twice per gesture
        # (press edge + release edge), because the rebuild calls new_layer and the
        # P4's new_layer pre-collects. Same failure the per-`where` keying above
        # already fixed once, one axis over.
        slots = self._bar_strips.get(where)
        if slots is None:
            slots = []
            self._bar_strips[where] = slots
        slot = None
        for s in slots:
            if s[2] is cv:
                slot = s
                break
        if slot is None:
            if len(slots) >= _BAR_STRIP_SLOTS:
                slot = slots.pop()                 # evict the least-recently-used
                _release_layer(slot[0])            # ...its layer belongs to a
                slot[0] = None                     # canvas we are no longer drawing
                slot[1] = None                     # into, so it is rebuilt below
                slot[2] = None
            else:
                slot = [None, None, None]          # [strip, key, canvas]
            slots.insert(0, slot)
        else:
            # Move to front. By IDENTITY, not list.remove(), whose == would happily
            # match a different all-None slot.
            for i in range(len(slots)):
                if slots[i] is slot:
                    if i:
                        slots.insert(0, slots.pop(i))
                    break
        strip = slot[0]
        # The active canvas can SWAP at runtime (the device web view binds a recording
        # TeeCanvas in place of the raw DeviceCanvas, #41) OR change identity/height as
        # `where` moves between the fixed game canvas and the responsive system canvas
        # (Stage 4) or between the root canvas and a window buffer (wm_windowed.py).
        # A strip allocated on the OLD canvas/height is the wrong layer for the new
        # one (a raw DeviceCanvas strip blitted through the Tee has no RecordingLayer
        # hooks -- '_end_batch' AttributeError -- and it wouldn't be recorded for the
        # browser anyway), so rebuild whenever either changes, not just on a width
        # resize.
        canvas_changed = slot[2] is not cv
        size_changed = strip is None or strip.w != cv.w or strip.h != bar_h
        fresh = size_changed or canvas_changed or slot[1] != key
        if fresh:
            # (Re)build the cached strip. new_layer gives a same-type/-palette canvas the
            # bar body draws into at the SAME coords (the bar lives at y in [0, bar_h),
            # which maps 1:1 onto the strip's rows), so the cached pixels are byte-identical
            # to drawing straight onto cv. Reuse the buffer across re-renders when the size
            # is unchanged; allocate a fresh layer on first build / a resize / a canvas swap.
            if size_changed or canvas_changed:
                self.ws.note_cost("bar.strip.alloc")
                _release_layer(strip)              # the old size's / canvas's
                strip = cv.new_layer(cv.w, bar_h)
                slot[0] = strip
                slot[2] = cv
            self.ws.note_cost("bar.strip.render")
            self._render_cart_bar(strip, key)
            slot[1] = key
        by = self._bar_y(where, cv, bar_h)
        cv.blit_strip(strip, 0, by)
        if fresh:
            self._strip_moved(cv, 0, by, cv.w, bar_h)

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
        explicit invalidators bump (look.set_icon_sheet, etc.)."""
        ws = self.ws
        owner = self._zone_owner(where)
        has_edit = _edit_kind(ws.cart)
        return (where, self._clock_text(), has_edit, id(ws.look.icon_sheet),
                getattr(self._bar_canvas(where), "font_scale", 1),
                bool(ws.can_manage),
                owner.zone_gen if owner is not None else 0,
                ws._wifi_icon_kind(),      # Part 3: wifi status glyph (connect/disconnect repaints)
                (ws.cart.get("title") if ws.cart else None),   # Part 4: the tool bar's left-zone title
                ws.history.bar_undo_bits(),   # #111: local op-history dim state
                                              # (RAM-only; journal flips
                                              # invalidate explicitly)
                self._bar_cache_gen)

    def _render_cart_bar(self, cv, key):
        """Render the bar's pixels onto `cv` (the offscreen strip, or any canvas)
        for `key[0]` (`where`). The pieces are the kernel's strip
        (native/moy_play/moy_chrome.c: the crash bar, the band, the right zone,
        a tool's title), the one body every bar draws with; the lent left zone
        is its app's. Factored out of _draw_top_bar_cart so the SAME drawing
        serves the cache build and the direct path. `key` carries the already
        computed has_edit (index 2) so the icon choice can't drift from it."""
        ws = self.ws
        where = key[0]
        mp = _ch.chrome_inks(ws.theme_colors, self._NAMES)
        if where == "desktop":
            # The running-cart CRASH chrome: drawn only on a crash, never a
            # "zone" (no draw_zone below this return, which keeps the zoned-bar
            # dispatch off the play frame -- the Stage-4 guardrail).
            has_edit = key[2]
            edit = -1 if has_edit is None else (1 if has_edit else 0)
            _ch.replay(mp.chrome_strip_crash(cv.w, edit, self._clock_text(),
                                             ws._wifi_icon_kind()), cv, ws)
            return
        if where in ("tool", "app"):
            # Part 4: a TOOL/APP runs WITH a minimal bar so it's EXITABLE: the
            # OS status cluster + the context-X on the right, its TITLE alone
            # on the left (no tab ladder, no draw_zone: the play-frame
            # guardrail holds).
            bar_h = self._bar_h(where)
            zone = self._zone_rect(where)
            _ui.frame_band(cv, ws.theme_colors, (0, 0, cv.w, bar_h))
            self._render_right_zone(cv, where, mp)
            title = (ws.cart.get("title") if ws.cart else "") or ""
            dy = 0 if self._zone_is_game(where) else ws.layout.bar_text_dy
            _ch.replay(mp.chrome_strip_title(title, zone[0], zone[2], dy), cv, ws)
            return
        # The zoned bar (Stage 4): the band (a window's light toolbar under a
        # light theme), the OS-owned RIGHT zone -- suppressed inside a window
        # (#73), whose WM title strip carries min/max/close -- then the active
        # app's LENT left zone.
        bar_h = self._bar_h(where)
        _ui.frame_band(cv, ws.theme_colors, (0, 0, cv.w, bar_h), self.zone_band_light(where))
        if not self._in_window(where):
            self._render_right_zone(cv, where, mp)
        owner = self._zone_owner(where)
        if owner is not None:
            owner.draw_zone(cv, self._zone_rect(where))

    def _show_x(self, where):
        """The context X: wherever something is beneath the surface. The desk
        is desktop mode's floor and never has one; the home has one when the
        back-stack is deeper than its mode's floor -- console mode on a P4,
        whose desk waits beneath it -- and none where it is the floor."""
        if where == "desk":
            return False
        if where == "home":
            wm = self.ws.wm
            return wm.stack.depth() > wm.floor()
        return True

    def _render_right_zone(self, cv, where, mp=None):
        """The OS-owned right zone: clock, wifi, batt, the ≡ toggle and the
        context X (`_show_x`). The fixed game-canvas cluster, or the responsive
        Layout's rects at its chrome scale (#203)."""
        ws = self.ws
        if mp is None:
            mp = _ch.chrome_inks(ws.theme_colors, self._NAMES)
        show_x = self._show_x(where)
        lay = None
        if not self._zone_is_game(where):
            L = ws.layout
            lay = (L.clock_x, L.bar_text_dy, L.cs, L.wifi_btn, L.batt_btn,
                   L.sysmenu_btn, L.context_x_btn)
        _ch.replay(mp.chrome_strip_right(lay, self._clock_text(), ws._wifi_icon_kind(),
                                         show_x), cv, ws)

    def redraw_clock(self, where):
        """Repaint JUST the clock cell of an already-drawn bar (#155).

        Lets the desk backdrop cache survive a clock tick. The desk render is
        wallpaper cover-crop + icon column + this bar, ~77ms on the P4, and the
        clock is the only part of it that changes while nothing else does -- so
        without this, ticking the minute invalidated the whole cached desk once a
        second. The clock cell is right of the taskbar chips (they stop at
        lay.clock_x - 4*fs), so repainting it cannot erase them."""
        ws = self.ws
        cv = self._bar_canvas(where)
        if cv is None:
            return
        lay = ws.layout
        th = ws.theme_colors
        x, y, w, h = lay.clock_hit()
        # Same background the strip itself uses (the light-theme in-window band
        # is a different token), minus the bottom edge line so the repaint can
        # never eat it.
        bar_h = self._bar_h(where)
        by = self._bar_y(where, cv, bar_h)
        y += by
        cv.clip(x, y, w, min(h, max(0, bar_h - 1)))
        _ui.frame_band(cv, th, (0, by, cv.w, bar_h), self.zone_band_light(where))
        cv.clip()
        text = self._clock_text()
        cv.print(text, lay.clock_x, by + 3 + lay.bar_text_dy, th["chrome_ink_dim"], 1)
        if text != self._clock_drawn:
            self._clock_drawn = text
            self._strip_moved(cv, x, y, w, h)

    def _clock_text(self):
        """A wall-clock HH:MM from time.localtime when available, else a mm:ss
        uptime so the strip always shows a live clock (host == device). Cached
        per second (#66): the cart bar's cache KEY calls this every
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

    def handle_bar_tap(self, where, px, py):
        """The zoned bar's tap slice (Stage 4), shared by every screen it draws on
        (home/settings/menu): the clock Easter egg, the ≡ system menu, the wifi status
        icon (Part 3: taps LAUNCH the wifi tool), and -- Stage 5 -- the context X are
        OS/right-zone-owned, IDENTICAL wherever the bar shows (same as the draw), checked
        first so a tap on any never falls through to the lent zone.
        Anything else routes to the active app's zone_tap over its lent left zone.
        Returns True iff the bar (or the app's zone) consumed the tap. The clock-
        run reset runs for ANY non-clock tap (byte-identical to the pre-Stage-4
        launcher pointer), so taps that fall through to the content still reset it.
        (px, py) are on `where`'s canvas; every rect below is in bar coordinates."""
        ws = self.ws
        py -= self._bar_y(where, self._bar_canvas(where), self._bar_h(where))
        if self._in_window(where):
            # Inside a WM window (#73) the bar has NO right zone (suppressed in the
            # draw), so there is nothing OS-owned to hit -- the tap goes straight to
            # the app's lent zone; closing lives on the WM title strip.
            owner = self._zone_owner(where)
            return bool(owner.zone_tap(px, py, self._zone_rect(where))) \
                if owner is not None else False
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
        if _in(px, py, clock_hit):
            ws.ach_ui._tap_clock()
            return True
        ws.ach_ui._clock_taps = 0                # any other bar tap resets the run
        if _in(px, py, gear_hit):           # ≡ -> system menu (Settings/About/Reboot, #52)
            ws.toggle_sysmenu()
            return True
        # WiFi status icon: on the WINDOWED desktop it deep-links into Settings ->
        # WIFI (spec Section 5's "status icons shortcut into Settings" -- Settings is
        # a system APP, so wifi setup coexists with a running cart, #38); on the
        # fullscreen tiers it launches the moybyte.wifi.moy tool (Part 3, unchanged device
        # behavior). Consumes the tap either way so it never leaks to the lent zone.
        if _in(px, py, wifi_hit):
            if getattr(ws, "windowed_chrome", False):
                ws.open_settings()
                ws.settings_layer.open_wifi()
            else:
                ws.launch_wifi_tool()
            return True
        # Context X (Stage 5, spec Section 9): tap to EXIT the active surface back toward
        # its mode's root. On the home it leaves console mode for the desk beneath
        # (docs/shell_layout_2026-10.md §3); where the home is the floor it draws no X
        # and is not tested. A running TOOL (Part 4) pops to its RUN caller (spec
        # Section 6's test-play round trip -- go_home for a launcher-launched tool, but
        # the Editor tab if a tool is ever PLAYed from the editor); every other taskbar
        # app (Editor/Settings) uses the screen-string exit.
        if self._show_x(where) and _in(px, py, x_hit):
            if where == "home":
                ws.open_desk()
            elif where in ("tool", "app"):
                ws._exit_to_caller()
            else:
                ws.exit()
            return True
        owner = self._zone_owner(where)
        # Pass the SAME lent rect the draw used (the game canvas's _ZONE_LEFT_GAME,
        # or the responsive layout.zone_left), in the bar coordinates (px, py) are
        # in now, so an app's zone_tap hit-tests its icons at the exact positions
        # draw_zone drew them wherever the bar sits.
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
        if _in(px, py, _SYSMENU_BTN):
            ws.toggle_sysmenu()      # ≡ -> open the dropdown system menu (#52)
        elif _in(px, py, _HOME_BTN):
            ws.go_home()
        elif _in(px, py, _MENU_BTN):
            if _edit_kind(ws.cart) is None:
                return False
            ws._open_menu()
        elif _in(px, py, _PAINT_BTN):
            ws._open_paint()
        elif _in(px, py, _MAP_BTN):
            ws._open_map()
        elif _in(px, py, _BLOCKS_BTN):
            ws._open_blocks()
        elif _in(px, py, _MUSIC_BTN):
            ws._open_music()
        else:
            return False
        return True
