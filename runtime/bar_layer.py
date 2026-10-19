# Map (grep -n a name to jump there):
#   -- bar geometry           the bar's constants (console.py imports them back)
#   BarLayer                  the theme layout's bars: cells, strips, clock, taps
#   BarLayer.invalidate       repaint the cached bar strips
#   BarLayer.bar_rects        where the bars are on a surface; content_rect, what they leave
#   BarLayer.cell             where a widget is: its bar and its cell
#   BarLayer._draw_bar        one bar's cached strip at its rect
#   BarLayer.redraw_clock     repaint just the clock cell
#   BarLayer.handle_bar_tap   the bars' tap slice, shared by every surface
#   BarLayer.handle_cart_tap  the running-cart bar's tap slice
"""The shell's bars (#46; docs/shell_layout_2026-10.md §4, §7): every bar the
theme's layout resolves to, on any edge, each a cached strip of widget cells
(`shell_layout.pack`), extracted from Workstation (runtime/console.py) as its
own surface -- docs/history/shell_layers_refactor_v1.md.

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
navigates by the zoned bar, the home's tabs and the context-X, and the launcher had
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
    from layout_base import (BAR_ROWS, DEFAULT_BARS, bar_rects_of, bars_in_force,
                             content_of)
    from shell_layout import pack as _pack
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.layout_base import (BAR_ROWS, DEFAULT_BARS, bar_rects_of, bars_in_force,
                                     content_of)
    from runtime.shell_layout import pack as _pack


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

# -- today's bar on the fixed 320x240 GAME canvas: the cells
# `shell_layout.pack` gives the default layout there (a running tool/app's
# strip, `BarLayer._zone_is_game`; tests/test_bars.py holds the packing to
# them). A SEPARATE cluster from _SYSMENU_BTN/_HOME_BTN/.../_BAR_WIFI/_BAR_CLOCK
# -- the crash bar's own fixed rects.
_ZONE_BATT = _BAR_BATT                                          # same rightmost slot
# Wi-Fi a status gap from the battery (shell_layout.pack's at font scale 1).
_ZONE_WIFI = (_ZONE_BATT[0] - _BAR_ICON - 4, _BAR_Y, _BAR_ICON, _BAR_ICON)
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
    import ui_draw as _ui_draw
    import layout_base as _layout_base
    import iconsets as _iconsets
    from shell_layout import STATUS as _STATUS
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime import ui_draw as _ui_draw
    from runtime import layout_base as _layout_base
    from runtime import iconsets as _iconsets
    from runtime.shell_layout import STATUS as _STATUS


def _bar_text_w(s):
    """A bar `text` widget's width at scale 1, in the print font it draws in."""
    return _ui_draw.print_width(s, 1)


# The band's edge line faces the content (ui.frame_band's `side`).
_BAND_SIDE = {"top": _ui_draw.BAND_BOTTOM, "bottom": _ui_draw.BAND_TOP,
              "left": _ui_draw.BAND_RIGHT, "right": _ui_draw.BAND_LEFT}
# The OS widgets a bar paints itself (the zone is its owner's, the chips the
# WM's, a spacer nothing).
_PAINTED = ("menu", "close", "wifi", "battery", "clock", "title", "text", "separator")
# The widgets whose tap is the OS's (§4): the system menu, the X, and the
# three that open a component (§8).
_TAPPED = ("clock", "menu", "wifi", "battery", "close")
# A bar's titles where no running cart names one.
_TITLES = {"projects": "PROJECTS", "settings": "SETTINGS", "desk": "DESK",
           "calendar": "CALENDAR"}
# _cart_bar_key's fields every bar folds, and the ones a widget adds. Field 2
# (the EDIT|CODE slot) is drawn by the crash chrome alone, which folds all.
_KEY_ALWAYS = (0, 3, 4, 10)
_KEY_OF = {"clock": (1,), "zone": (5, 6, 8, 9), "title": (6, 8), "wifi": (7,),
           "menu": (11,)}
_KEY_N = 12
_KEY_ALL = (1 << _KEY_N) - 1
_KEY_UNDO = 1 << 9          # drawn only by the Editor's zone ("menu")


def _battery(cv, x, y, side, u, ink):
    """The look's battery in a `side`-square cell at unit `u`: an outlined
    body with its cap, filled to the level (full: the host reads no level)."""
    bw, bh = 11 * u, 7 * u
    bx = x + (side - bw - u) // 2
    by = y + (side - bh) // 2
    for k in range(u):
        cv.rectb(bx + k, by + k, bw - 2 * k, bh - 2 * k, ink)
    cv.rect(bx + bw, by + 2 * u, u, bh - 4 * u, ink)
    cv.rect(bx + 2 * u, by + 2 * u, bw - 4 * u, bh - 4 * u, ink)


# A look leaf no theme sets (moy_theme.h's MOY_THEME_NONE).
_NONE = -2147483648


def _band_part():
    """The look's band for the metric class in force: the desk bar's or the
    handheld bar's (ui.bar_part)."""
    if _layout_base.metric_class() == _layout_base.DESK:
        return _ui_draw.BAR_BAND
    return _ui_draw.BAR_HH_BAND


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
    """The shell's bars (#46), migrated out of Workstation as its own surface
    (docs/history/shell_layers_refactor_v1.md Phase 2): every bar of the
    resolved layout (docs/shell_layout_2026-10.md §4), each a row of widget
    cells packed at relayout (`shell_layout.pack`) -- the OS widgets (`menu`,
    `close`, `clock`, `wifi`, `battery`, `title`, `text`, `separator`) drawn by
    the bar itself through the kernel's cell painter, the taskbar `chips` by the
    WM over the strip, and the `zone` LENT to whichever app is active --
    `launcher_layer`/`settings_layer`/`editor_app` each implement
    `draw_zone(cv, rect)` (paint the lent rect) + `zone_tap(px, py)` (claim a tap
    inside it) + `zone_gen` (an int bumped whenever their zone's pixels change).
    Today's bar is the default seed's layout: the zone left, the chips over the
    middle, the clock, X, ≡, wifi and battery right.

    `where` still selects which screen is asking (`"home"` / `"settings"` /
    `"menu"` / `"desktop"` / `"tool"`), but it's no longer a set of hardcoded draw
    bodies: `"desktop"` stays the running-cart CRASH chrome (Stage 5 retired the
    #71 pause frame -- for a GAME the bar shows only on a crash, never during play);
    `"tool"` (Part 4) is the minimal bar a running TOOL/APP shows so it's exitable
    (back + title + status, NO tab ladder). BOTH the `"desktop"` and `"tool"`
    branches return before any `owner.draw_zone` call, so `draw_zone` stays
    structurally unreachable while a Player is top-of-stack (the perf guardrail); the
    home/settings/menu three route through the SAME generalized cache (below) into
    whichever app owns `where`.

    The #43 strip cache is KEPT AND GENERALIZED, not duplicated: `_cart_bar_key`
    takes `where` (default `"desktop"`, so the pinned zero-arg tests are
    unchanged) and folds in the active app's `zone_gen` + `ws.can_manage`, so ONE
    offscreen strip per bar serves every `where` and re-renders WHOLE only when a
    key field that bar shows changes (app switch / clock tick / zone-content change
    / theme edit) -- never a bare frame, and never a bar for a widget it does not
    hold. `_render_cart_bar(cv, key, i)` branches on `key[0]` (`where`) to render
    either the crash body or bar `i` of the zoned layout.

    Boundary decisions (deliberate, see the doc):
      * The button-rect CONSTANTS are the module-level single source above --
        the golden harness + tests reference console._HOME_BTN / _MENU_BTN / ...
        (re-exported by console.py), and Layout also uses them.
      * The shared draw toolkit (ws._glyph / ws._icon / ws._mini_btn / ws._bar_image)
        stays on Workstation per the doc; the bar is a CONSUMER of it (via self.ws).
      * `_bar_img_cache` (per-kind icon Image cache backing ws._icon) stays on ws;
        `_bar_cache_gen` (the strip cache generation) lives here, bumped
        through invalidate(); the look's changes reach the strips through
        `ws.look.look_gen` in their key.

    The bar is CHROME the content composes, not a standalone stack layer: it's
    SYSTEM-domain on the launcher, Settings and every Editor tab (drawn on
    sys_canvas) and GAME-domain on the running cart's crash chrome and a running
    tool's strip (drawn on the fixed game canvas, `_zone_is_game`), and it draws
    at their rects (`bar_rects`), LAST inside each content's paint, which lays
    out in what the bars leave (`content_rect`). So the content draws call
    _draw_status_strip(where) and the pointer methods delegate their bar slice to
    handle_bar_tap(where, ...) / handle_cart_tap, which take
    canvas coordinates and hit-test in the coordinates of the bar they land on.
    An app's zone_tap takes the lent rect as a parameter: the zone's cell, in
    the game canvas's packing or the responsive layout's. Inside a window the
    layout packs the window's band (`layout_base.BAND`, today's bar at the
    window's top whatever the theme's layout) and only its zone draws.

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
        # (invalidate()), and `ws.look.look_gen` in the key repaints a look change. Keyed per
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
        self._date_cache = (0, 0, 0)
        # The root canvas the bar strips are published to (publish_strips),
        # and the clock text redraw_clock last painted there.
        self._strip_cv = None
        self._clock_drawn = None
        self._ink_dx = None           # {status widget: dx} the strip last laid out
        # The game canvas's packing (_packed) and what it was packed from;
        # each packing's per-bar key fields (_key_fields).
        self._game_of = None
        self._game_size = None
        self._game_pack = None
        self._fields_of = None
        self._fields = ()
        self._need = _KEY_ALL
        # Each `where`'s frame key, filled in place (_fill_key).
        self._keys = {}

    def invalidate(self):
        """Repaint every cached bar strip on the next frame: something its key
        does not carry moved its pixels (a bar placement, a journal flip)."""
        self._bar_cache_gen += 1

    # -- the strips a rotated compositor presents ------------------------------

    def publish_strips(self, cv=None):
        """Hand the bar strips to a root canvas that presents them as rects of
        their own (the rotated compositor's `set_strips`; absent elsewhere):
        every bar in force, at the root's size. `cv` is the root, kept for the
        relayouts that publish again; every repaint that changes a strip's
        pixels on it says so through `_strip_moved`."""
        if cv is not None:
            self._strip_cv = cv
        cv = self._strip_cv
        ss = getattr(cv, "set_strips", None)
        if ss is None:
            return
        r = getattr(self.ws, "render_scale", 1)
        ss([(x * r, y * r, w * r, h * r) for x, y, w, h in
            bar_rects_of(cv.w // r, cv.h // r, self.ws.layout.status_h, bars_in_force())])

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
        if where == "projects":
            return ws.projects_layer
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
            return ws.sys_canvas is ws.canvas and not _ui_draw.depth()[0]
        if where != "tool" or getattr(ws, "app_full_canvas", False):
            return False
        # Under a theme's look a tool's strip on a canvas that IS the system
        # one is the look's bar, the one every app shows (docs/look_2026-10.md
        # step 27a).
        return not (ws.sys_canvas is ws.canvas and _ui_draw.depth()[0])

    def _bar_canvas(self, where, game=None):
        """`where`'s canvas; `game` is `_zone_is_game(where)` where the caller
        has it already (each read of `ws.sys_canvas` is a property call)."""
        if game is None:
            if where == "app":
                return self.ws.sys_canvas
            game = self._zone_is_game(where)
        # an "app" strip on the game canvas: that canvas IS the system one
        if where == "desktop" or game:
            return self.ws.canvas
        return self.ws.sys_canvas

    def _bar_h(self, where):
        """The rows of a BAR_ROWS-row bar on `where`'s canvas: every bar's
        size is in those."""
        if where == "desktop" or self._zone_is_game(where):
            return _STATUS_H
        return self.ws.layout.status_h

    # -- where the bars are: the one author for draws and taps -----------------
    #
    # A bar's pixels and its cells (shell_layout.pack) are in BAR coordinates,
    # row 0 its first; the bar itself sits at its rect on `where`'s canvas. The
    # responsive surfaces read the packing their layout made at relayout (a
    # window's layout packs its band); a strip on the fixed game canvas packs
    # the bars in force once per placement, at that canvas's 320x240 and
    # scale 1. The running cart's crash chrome ("desktop") is the Player's
    # crash panel, which lays out below a top strip, so it stays one bar at
    # row 0.

    def _packed(self, where, game=None):
        if where == "desktop":
            # The crash chrome: one strip across the top of the game canvas.
            return (("top", (0, 0, self.ws.canvas.w, _STATUS_H), 0, ()),)
        if game is None:
            game = self._zone_is_game(where)
        if game:
            of = bars_in_force()
            cv = self.ws.canvas
            gs = self._game_size
            if (self._game_of is not of or gs is None
                    or gs[0] != cv.w or gs[1] != cv.h):
                self._game_of = of
                self._game_size = (cv.w, cv.h)
                self._game_pack = _pack(cv.w, cv.h, _STATUS_H, 1, 1, 5 * 8, _bar_text_w, of)
            return self._game_pack
        return self.ws.layout.bars

    def bar_rects(self, where):
        """Every bar `where` shows, as (x, y, w, h) on its canvas."""
        return tuple(b[1] for b in self._packed(where))

    def bar_rect(self, i, where):
        return self._packed(where)[i][1]

    def content_rect(self, where):
        """`where`'s canvas less its bars: what the surface under them lays
        out in, (x, y, w, h)."""
        cv = self._bar_canvas(where)
        if where == "desktop":
            return content_of(cv.w, cv.h, _STATUS_H, DEFAULT_BARS)
        if self._zone_is_game(where):
            return content_of(cv.w, cv.h, _STATUS_H, bars_in_force())
        L = self.ws.layout
        return (L.cx, L.cy, L.cw, L.ch)

    def bar_at(self, where, px, py):
        """True when (px, py) on `where`'s canvas is on one of its bars."""
        for b in self._packed(where):
            if _in(px, py, b[1]):
                return True
        return False

    def cell(self, where, name):
        """(bar, cell rect) of `where`'s first `name` widget, the cell in its
        bar's coordinates and the bar as `shell_layout.pack` gives it; None
        where no bar places one."""
        for b in self._packed(where):
            for c in b[3]:
                if c[0] == name:
                    return b, c[1]
        return None

    def _zone_rect(self, where):
        """The rect LENT to `where`'s app for its draw_zone/zone_tap (never asked
        for "desktop", whose branch returns before any zone dispatch), in its
        bar's coordinates."""
        got = self.cell(where, "zone")
        if got is None:
            return (0, 0, 0, 0)
        if self._in_window(where):
            return self._window_zone(got[0][1], got[1])
        return got[1]

    @staticmethod
    def _window_zone(rect, zone):
        """A window's strip paints none of the OS widgets, so its zone runs on
        to the strip's end, its own left margin kept at the right."""
        return (zone[0], zone[1], max(zone[2], rect[2] - 2 * zone[0]), zone[3])

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
        window bar then suppresses the OS widgets (no copied taskbar) -- the
        WM's title strip carries min/max/close instead. Always False on the
        fullscreen-stack tiers (ws.windowed_chrome stays False there)."""
        return getattr(self.ws, "windowed_chrome", False) and where not in (
            "home", "desk", "desktop", "tool", "app")

    # -- draw + cache (the #43 strip, generalized to every `where`) -----------

    def _draw_top_bar_cart(self, where="desktop"):
        """The ONE cached bar draw for every `where` (Stage 4 generalizes what was
        the running-cart-only strip cache): each bar's pixels rendered ONCE into
        an offscreen strip and blit_strip'd each frame (one flat copy a bar)
        instead of re-rendering ~9 16x16 sprites + a glyph + text every frame --
        ~6ms of wasted draw the #43 cache exists to avoid. When a field of the
        key that the bar shows changes (app switch / clock tick / zone-content
        change / theme edit / font-size change) that bar's strip is re-rendered
        WHOLE, then reused; a bar without the clock never re-renders on its
        tick. The strips are purely the DRAW; hit-testing goes through the
        packed cells / zone_tap, so caching can't desync taps."""
        game = self._zone_is_game(where)
        cv = self._bar_canvas(where, game)
        packed = self._packed(where, game)
        fields = self._key_fields(packed)
        key = self._keys.get(where)
        if key is None:
            key = self._keys[where] = [None] * _KEY_N
        self._fill_key(key, where, cv, self._shown(where))
        if hasattr(cv, "_lr"):
            # The canvas is itself a RecordingLayer (a windowed-WM window buffer on
            # the web console): a strip cache NESTED in a recorded layer can't ship
            # (blit_strip of a raw strip isn't a recordable op), so render the bars
            # directly -- a live window re-records its whole stream anyway, and the
            # dozen icon/text commands are far lighter than an inlined pixel strip.
            for i in range(len(packed)):
                r = packed[i][1]
                if r[0] or r[1]:
                    cv.camera(-r[0], -r[1])
                self._render_cart_bar(cv, key, i)
                if r[0] or r[1]:
                    cv.camera(0, 0)
            return
        bars = self._bar_strips.get(where)
        if bars is None:
            bars = []
            self._bar_strips[where] = bars
        while len(bars) < len(packed):
            bars.append([])
        for i in range(len(packed)):
            self._draw_bar(cv, bars[i], packed[i][1], key, fields[i], i)

    def _key_fields(self, packed):
        """For each bar of `packed`, the `_cart_bar_key` fields its pixels show:
        the shared ones, then each of its widgets' values, and their union as
        a bit per field (`_need`). Once per packing."""
        if self._fields_of is not packed:
            out = []
            need = 0
            for b in packed:
                f = list(_KEY_ALWAYS)
                for c in b[3]:
                    for k in _KEY_OF.get(c[0], ()):
                        if k not in f:
                            f.append(k)
                if not b[3]:                # the crash chrome: everything
                    f = list(range(_KEY_N))
                out.append(tuple(f))
                for k in f:
                    need |= 1 << k
            self._need = need
            self._fields_of = packed
            self._fields = tuple(out)
        return self._fields

    def _shown(self, where):
        """The key fields `where`'s bars draw, as `_need`'s bits (after
        `_key_fields` of its packing): the undo/redo dims only in the
        Editor's zone."""
        return self._need if where == "menu" else self._need & ~_KEY_UNDO

    def shown_key(self, where):
        """`_cart_bar_key` narrowed to what `where`'s bars draw, the fields
        a skipped one leaves None: the key a cache over a whole frame that
        includes the bar folds."""
        game = self._zone_is_game(where)
        cv = self._bar_canvas(where, game)
        self._key_fields(self._packed(where, game))
        key = [None] * _KEY_N
        self._fill_key(key, where, cv, self._shown(where))
        return tuple(key)

    def _draw_bar(self, cv, slots, rect, key, fields, i):
        """Bar `i`'s strip at `rect` on `cv`, from the slot that holds it for
        `cv`, re-rendered when a field of `key` it shows moved."""
        bx, by, bw, bh = rect
        # One slot PER CANVAS, most-recently-used first. The windowed WM draws the
        # same `where` into DIFFERENT canvases from frame to frame -- straight into
        # the root canvas through a viewport on a quiet frame (wm_windowed
        # _direct_render), into the window's own buffer otherwise -- so a slot that
        # remembers only one canvas reads every switch as a canvas swap and rebuilds.
        # That cost 72ms of an 86ms Settings frame on P4 glass, twice per gesture
        # (press edge + release edge), because the rebuild calls new_layer and the
        # P4's new_layer pre-collects. Same failure the per-`where` keying above
        # already fixed once, one axis over.
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
            for j in range(len(slots)):
                if slots[j] is slot:
                    if j:
                        slots.insert(0, slots.pop(j))
                    break
        strip = slot[0]
        # The active canvas can SWAP at runtime (the device web view binds a recording
        # TeeCanvas in place of the raw DeviceCanvas, #41) OR change identity/size as
        # `where` moves between the fixed game canvas and the responsive system canvas
        # (Stage 4) or between the root canvas and a window buffer (wm_windowed.py).
        # A strip allocated on the OLD canvas/size is the wrong layer for the new
        # one (a raw DeviceCanvas strip blitted through the Tee has no RecordingLayer
        # hooks -- '_end_batch' AttributeError -- and it wouldn't be recorded for the
        # browser anyway), so rebuild whenever either changes.
        canvas_changed = slot[2] is not cv
        size_changed = strip is None or strip.w != bw or strip.h != bh
        old = slot[1]
        fresh = size_changed or canvas_changed or old is None
        if not fresh:
            for k in fields:
                if old[k] != key[k]:
                    fresh = True
                    break
        if fresh:
            # (Re)build the cached strip. new_layer gives a same-type/-palette canvas the
            # bar body draws into in BAR coordinates, so the cached pixels are
            # byte-identical to drawing straight onto cv at the bar's rect. Reuse the
            # buffer across re-renders when the size is unchanged; allocate a fresh
            # layer on first build / a resize / a canvas swap.
            if size_changed or canvas_changed:
                self.ws.note_cost("bar.strip.alloc")
                _release_layer(strip)              # the old size's / canvas's
                strip = cv.new_layer(bw, bh)
                slot[0] = strip
                slot[2] = cv
            self.ws.note_cost("bar.strip.render")
            self._render_cart_bar(strip, key, i)
            slot[1] = tuple(key)
        cv.blit_strip(strip, bx, by)
        if fresh:
            self._strip_moved(cv, bx, by, bw, bh)

    def _cart_bar_key(self, where="desktop"):
        """The cache key for the bar: every piece of state that changes ANY `where`
        variant's PIXELS. A different key forces a strip re-render; an unchanged key
        reuses the cached strip. `where` is key[0] (so a screen switch always
        invalidates); then the clock text (ticks ~once/min), whether the open cart
        has an edit schema (EDIT vs CODE icon, "desktop" only), the look's
        generation + the glyph font scale (a theme or icon edit / resize must
        repaint),
        whether writes are enabled (the launcher's NEW/DUP/DEL visibility), the
        active app's OWN `zone_gen` (Stage 4: an int the app bumps whenever ITS
        lent zone's content changes -- e.g. EditorApp.set_tab, Launcher.sel/
        set_items -- so a real zone-content change is the ONLY thing that forces a
        re-render beyond the shared dimensions here), and a generation counter the
        explicit invalidators bump (a bar placement, a journal flip)."""
        key = [None] * _KEY_N
        self._fill_key(key, where, self._bar_canvas(where), _KEY_ALL)
        return tuple(key)

    def _fill_key(self, key, where, cv, need):
        """Write `_cart_bar_key`'s fields into `key` in place: the ones `need`
        names (a bit per field), plus the shared ones every bar folds. The
        frame's draw fills `where`'s one list with only what the bars on show
        draw, so a painted frame allocates no key and reads no state it does
        not show; a field it skips keeps the value no bar compares."""
        ws = self.ws
        key[0] = where
        if need & 2:
            key[1] = self._clock_text()
        if need & 4:
            key[2] = _edit_kind(ws.cart)
        key[3] = ws.look.look_gen
        key[4] = getattr(cv, "font_scale", 1)
        if need & 32:
            key[5] = bool(ws.can_manage)
        if need & 64:
            owner = self._zone_owner(where)
            key[6] = owner.zone_gen if owner is not None else 0
        if need & 128:
            key[7] = ws._wifi_icon_kind()      # the wifi status glyph
        if need & 256:
            cart = ws.cart
            key[8] = cart.get("title") if cart else None   # the tool bar's title
        if need & _KEY_UNDO:
            # #111: the local op-history dim state (RAM-only; journal flips
            # invalidate explicitly)
            key[9] = ws.history.bar_undo_bits()
        key[10] = self._bar_cache_gen
        if need & 2048:
            m = getattr(ws, "sysmenu", None)       # the system button's open plate
            key[11] = bool(_ui_draw.depth()[0]) and m is not None and m.open \
                and m.widget == "menu"

    def _render_cart_bar(self, cv, key, i=0):
        """Render bar `i`'s pixels onto `cv` (the offscreen strip, or any canvas)
        for `key[0]` (`where`), in bar coordinates. The pieces are the kernel's
        strip (native/moy_play/moy_chrome.c: the crash bar, a widget's cell)
        and the toolkit's band, the one body every bar draws with; the lent
        zone is its app's. Factored out of _draw_top_bar_cart so the SAME
        drawing serves the cache build and the direct path. `key` carries the
        already computed has_edit (index 2) so the icon choice can't drift
        from it."""
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
        edge, rect, dy, cells = self._packed(where)[i]
        game = self._zone_is_game(where)
        # Part 4: a TOOL/APP runs WITH a minimal bar so it's EXITABLE: the OS
        # widgets, its TITLE in the zone (no tab ladder, no draw_zone: the
        # play-frame guardrail holds). Every other bar is the zoned bar: the
        # band (a window's light toolbar under a light theme), the OS widgets
        # -- suppressed inside a window (#73), whose WM title strip carries
        # min/max/close -- then the active app's LENT zone.
        tool = where in ("tool", "app")
        light = False if tool else self.zone_band_light(where)
        inside = self._in_window(where)
        looked = not game and not inside and _ui_draw.depth()[0]
        bar = (0, 0, rect[2], rect[3])
        if looked and self._home_ground(cv, where, -rect[0], -rect[1]):
            pass
        elif not (looked and _ui_draw.bar_part(cv, ws.theme_colors, _band_part(), bar,
                                               _BAND_SIDE[edge])):
            looked = False
            _ui_draw.frame_band(cv, ws.theme_colors, bar, light, _BAND_SIDE[edge])
        zone = None
        if not inside:
            cs = 0 if game else ws.layout.cs
            across = edge in ("left", "right")
            show_x = self._show_x(where)
            self._ink_dx = self._status_dx(cells) if looked and not across else None
            for name, r, text, _slot in cells:
                if name == "zone":
                    zone = r
                elif name in _PAINTED and (show_x or name != "close"):
                    if looked:
                        self._paint_look_cell(cv, where, name, r, text, bar, across)
                    else:
                        self._paint_cell(cv, mp, where, name, r, text, cs, dy, across, game)
        else:
            for c in cells:
                if c[0] == "zone":
                    zone = self._window_zone(rect, c[1])
        if zone is None:
            return
        back = self.draws_back(where)
        if tool:
            if back:
                self._paint_back_title(cv, zone, self._title(where), bar, game)
                return
            if looked:
                self._paint_look_cell(cv, where, "title", zone, None, bar, False)
                return
            _ch.replay(mp.chrome_strip_cell("title", zone, 0, dy, self._title(where), False),
                       cv, ws)
            return
        owner = self._zone_owner(where)
        if owner is not None:
            title = getattr(owner, "back_title", None) if back else None
            if title:
                self._paint_back_title(cv, zone, title, bar, game)
                return
            if back:
                self._paint_back_title(cv, zone, None, bar, game)
                zone = self.past_back(zone, game)
            owner.draw_zone(cv, zone)

    # -- the way back (docs/look_2026-10.md step 27a) --------------------------
    #
    # Back goes one level; a window's X closes it. A bar shows the way back
    # wherever there is somewhere to go (`_way_back`): as the back chevron at
    # the head of its zone, or as the `close` widget's X where its layout
    # places one (the themes without a look). The chevron, Esc and B do the
    # same thing.

    def _way_back(self, where):
        """Whether `where` has somewhere to go back to. The desk is desktop
        mode's floor and never has; the home has where the back-stack is
        deeper than its mode's floor -- console mode on a P4, whose desk waits
        beneath it -- and not where it is the floor."""
        if where == "desk":
            return False
        if where == "home":
            wm = self.ws.wm
            return wm.stack.depth() > wm.floor()
        return True

    def draws_back(self, where):
        """Whether `where`'s bar draws the back chevron at the head of its
        zone: where it has a way back and its bar places no `close`, outside
        a window (whose title strip carries close)."""
        return (not self._in_window(where) and self._way_back(where)
                and self.cell(where, "close") is None)

    def _back_w(self, game):
        """The width the chevron takes at the head of a zone: its square and
        the bar's 2-pixel pitch, what the X it replaces took."""
        u = 1 if game else _layout_base.unit()
        return 18 * u

    def past_back(self, zone, game=False):
        """`zone` less the chevron at its head: the rect its owner draws and
        hit-tests in when the bar draws the way back."""
        bw = self._back_w(game)
        return (zone[0] + bw, zone[1], max(0, zone[2] - bw), zone[3])

    def back_rect(self, where):
        """The chevron's tap target on `where`'s canvas, (x, y, w, h): the head
        of its zone; None where the bar draws no chevron."""
        if not self.draws_back(where):
            return None
        got = self.cell(where, "zone")
        if got is None:
            return None
        bar, (x, y, w, h) = got
        return (bar[1][0] + x, bar[1][1] + y,
                min(w, self._back_w(self._zone_is_game(where))), h)

    def go_back(self, where):
        """The chevron's tap: back one level from `where` -- what Esc and B
        do there."""
        ws = self.ws
        if where == "home":
            ws.open_desk()
        elif where == "app":
            ws.app_back()
        elif where == "tool":
            ws._exit_to_caller()
        elif where == "settings":
            ws.settings_layer.back()
        else:
            ws.exit()
        ws._dirty = True

    def _paint_back_title(self, cv, r, title, bar, game=False):
        """A page's place in the bar (DESIGN.md, Bar): the back chevron in
        the zone's first square, then the page's title (None: none) in the
        `title` face, in the chrome ink."""
        ws = self.ws
        ink = ws.theme_colors["chrome_ink"]
        x, _y, w, _h = r
        thick = bar[3]
        u = 1 if game else _layout_base.unit()
        side = 16 * u
        gy = (thick - side) // 2
        if not ws.draw_icon("back", (x, gy, side, side), ink, cv):
            ws._glyph("arr_l", (x, gy, side, side), ink, cv, u)
        if not title:
            return
        tf = _ui_draw.font("title")
        fs = 1 if game else ws.layout.fs
        sc = 1 if game else max(fs, (16 * fs) // max(1, tf[2]))   # a heading, 16 rows
        was = _ui_draw.print_font(tf[0])
        tx = x + side + 6 * u
        s = _ui_draw.print_cut(title, max(0, x + w - tx), sc)
        if game:                       # the fixed 320x240 strip: the face's own size
            cv.print(s, tx, (thick - tf[2]) // 2, ink, 1)
        else:
            cv.print_scaled(s, tx, (thick - tf[2] * sc) // 2, ink, sc)
        if was is not None:
            _ui_draw.print_font(was)

    def _paint_cell(self, cv, mp, where, name, r, text, cs, dy, across, game):
        """One OS widget's cell, through the kernel's painter."""
        ws = self.ws
        s = None
        if name == "clock":
            s = self._clock_text()
            if across:
                L = ws.layout
                fw = 8 if game else L.font_w
                lh = 8 if game else L.line_h
                r = (r[0] + (r[2] - 2 * fw) // 2, r[1] + (r[3] - 2 * lh) // 2,
                     2 * fw, 2 * lh)
        elif name == "wifi":
            s = ws._wifi_icon_kind()
            if s != "wifi":            # off is the quiet state: no glyph
                return
        elif name == "title":
            s = self._title(where)
        elif name == "text":
            s = text
            if across:
                tw = _bar_text_w(s) * (1 if game else ws.layout.fs)
                r = (r[0] + (r[2] - tw) // 2, r[1], tw, r[3])
        _ch.replay(mp.chrome_strip_cell(name, r, cs, dy, s, across), cv, ws)

    def _paint_look_cell(self, cv, where, name, r, text, bar, across):
        """One OS widget's cell under the theme's look (docs/look_2026-10.md
        step 14): the system button is the pixel Moy on its tile, open while
        the system menu shows; the status glyphs, the clock and the strings
        draw in the bar's ink, centred across the bar."""
        ws = self.ws
        th = ws.theme_colors
        ink = th["chrome_ink"]
        x, y, w, h = r
        thick = bar[2] if across else bar[3]
        u = _layout_base.unit()
        if name == "menu":
            tx, ty, tw, tht = self.sys_tile(r, bar, across)
            m = ws.sysmenu
            part = _ui_draw.BAR_HH_SYS if _band_part() == _ui_draw.BAR_HH_BAND \
                else _ui_draw.BAR_SYS
            _ui_draw.bar_part(cv, th, part, (tx, ty, tw, tht), m.open and m.widget == "menu")
            sc = max(1, tht // 16)
            if ws.draw_icon("moy", (tx, ty, tw, tht), ink, cv):   # outlined, its set's
                return
            img = ws._icon_image_keyed("moy")          # its plate keyed on the tile
            if img is not None:
                cv.spr(img, tx + (tw - 16 * sc) // 2, ty + (tht - 16 * sc) // 2, sc)
            return
        if name in ("wifi", "battery", "close"):
            kind = name
            if name == "wifi" and ws._wifi_icon_kind() != "wifi":
                return             # off is the quiet state, as a phone's: no glyph
            dx = self._ink_dx.get(name, 0) if self._ink_dx else 0
            x += dx                                    # its ink evened out (_status_dx)
            side = 16 * u                              # the theme's icon set, centred
            gx = x + (w - side) // 2 if not across else (thick - side) // 2
            gy = (thick - side) // 2 if not across else y + (h - side) // 2
            if ws.draw_icon(kind, (gx, gy, side, side), ink, cv):
                return
            side = 12 * u
            gx = x + (w - side) // 2 if not across else (thick - side) // 2
            gy = (thick - side) // 2 if not across else y + (h - side) // 2
            if name == "battery":
                _battery(cv, gx, gy, side, u, ink)
            else:
                ws._glyph(name, (gx, gy, side, side), ink, cv, u)
            return
        if name == "separator":
            if across:
                cv.rect(x + 2 * u, y + h // 2, w - 4 * u, 1, th["chrome_ink_dim"])
            else:
                cv.rect(x + w // 2, (thick - 16 * u) // 2, 1, 16 * u, th["chrome_ink_dim"])
            return
        s = self._clock_text() if name == "clock" else \
            self._title(where) if name == "title" else (text or "")
        L = ws.layout
        if name == "clock" and across:
            for i, part in enumerate((s[:2], s[3:5])):
                cv.print(part, (thick - 2 * L.font_w) // 2, y + i * (h // 2), ink, 1)
            return
        if name == "title":
            s = s[:max(0, w // max(1, L.font_w))]
        cv.print(s, x, (thick - L.line_h) // 2, ink, 1)

    def _ink(self, name, r):
        """(first, last) column of status widget `name`'s ink in its cell `r`
        of a horizontal bar, as `_paint_look_cell` draws it: an icon's from
        its set's mask (the wider of Wi-Fi's two states), the fallback
        battery's from its outline, the clock's its cell."""
        x, _y, w, _h = r
        if name == "clock":
            return (x, x + w - 1)
        u = _layout_base.unit()
        side = 16 * u
        gx = x + (w - side) // 2
        sp = self.ws.icon_spec()
        kinds = ("wifi", "wifi_off") if name == "wifi" else (name,)
        a = b = None
        for k in kinds:
            got = _iconsets.ink_span(k, side, side, sp)
            if got is not None:
                a = got[0] if a is None else min(a, got[0])
                b = got[1] if b is None else max(b, got[1])
        if a is not None:
            return (gx + a, gx + b)
        side = 12 * u
        gx = x + (w - side) // 2
        if name == "battery":
            bx = gx + (side - 12 * u) // 2
            return (bx, bx + 12 * u - 1)
        return (gx, gx + side - 1)

    def _status_dx(self, cells):
        """{name: dx} for the status widgets (`shell_layout.STATUS`) of a
        horizontal bar's runs: where two or more sit side by side, the
        middle ones move so the ink of each is as far from the next as the
        last gap is from the first -- one gap between Wi-Fi, the battery and
        the clock whatever room each icon's art leaves in its cell. The ends
        stay and a clock never moves (a run with one inside is left as
        packed), so the clock's own repaint (`redraw_clock`) lands where the
        strip drew it."""
        out = None
        run = []
        for c in cells + (("", (0, 0, 0, 0), None, -1),):
            if c[0] in _STATUS and (not run or run[-1][3] == c[3]):
                run.append(c)
                continue
            if len(run) > 2 and all(n != "clock" for n, _r, _t, _s in run[1:-1]):
                inks = [self._ink(n, r) for n, r, _t, _s in run]
                inner = 0
                for a, b in inks[1:-1]:
                    inner += b - a + 1
                free = inks[-1][0] - inks[0][1] - 1 - inner
                at = inks[0][1] + 1
                for i in range(1, len(run) - 1):
                    gap = free // (len(run) - i)
                    free -= gap
                    at += gap
                    if out is None:
                        out = {}
                    out[run[i][0]] = at - inks[i][0]
                    at += inks[i][1] - inks[i][0] + 1
            run = [c] if c[0] in _STATUS else []
        return out

    def sys_tile(self, r, bar, across):
        """The system button's tile in its cell `r` of bar rect `bar`: 36x34 on
        the desk's 40-row bar, 22x20 on the handheld's 28, in proportion on
        another (`layout_base.sys_tile`), centred in its cell and across the
        bar."""
        thick = bar[2] if across else bar[3]
        tw, th, _len = _layout_base.sys_tile(thick)
        if across:
            return ((thick - tw) // 2, r[1] + (r[3] - th) // 2, tw, th)
        return (r[0] + (r[2] - tw) // 2, (thick - th) // 2, tw, th)

    def _title(self, where):
        """The `title` widget's string: the running cart's or the open
        project's, else the surface's name."""
        ws = self.ws
        if where in ("tool", "app", "menu") and ws.cart:
            return ws.cart.get("title") or ""
        if where == "home":
            return ws.home_title()
        return _TITLES.get(where, "")

    def _show_x(self, where):
        """The `close` widget's X, where a layout places one: wherever there
        is a way back (`_way_back`)."""
        return self._way_back(where)

    def _home_ground(self, cv, where, x, y):
        """Paint the handheld home's backdrop under its bar where the look
        draws the bar with no band (`hhbar.style` none) and the home's
        ground is a backdrop of its own, not the `desktop` colour a band
        would fill (Blocks' sunflower; docs/look_2026-10.md step 26b): the
        screen's surface at (x, y) of `cv`, inside its clip. True when
        painted; no wallpaper is under a bar here."""
        ws = self.ws
        if where != "home" or _layout_base.metric_class() == _layout_base.DESK \
                or ws.look.wallpaper_id is not None:
            return False
        style = ws.look.live.look[0].get("hhbar.style")
        if style not in (None, 0, _NONE):
            return False
        rec = ws.look.backdrop("home")
        if not rec:
            return False
        sc = ws.sys_canvas
        cv.backdrop(rec, ws.theme_colors["desktop"], surface=(x, y, sc.w, sc.h))
        return True

    def repaint_cell(self, where, name):
        """Paint the ground of cell `name` of an already-drawn bar again: the
        band the strip itself draws (the light-theme in-window band is a
        different token), minus its edge line so the repaint can never eat
        it. What draws in the cell next lands on fresh pixels -- the clock
        over its old digits, the taskbar chips' labels and corners, which
        blend. (canvas, bar, cell rect on the canvas, whether the look drew
        the band), or None where no bar places the cell."""
        cv = self._bar_canvas(where)
        if cv is None:
            return None
        got = self.cell(where, name)
        if got is None:
            return None
        bar, (x, y, w, h) = got
        edge, (bx, by, bw, bh), _dy, _cells = bar
        th = self.ws.theme_colors
        x += bx
        y += by
        cx, cy, cw, ch = x, y, w, h
        if edge == "top":
            ch = min(h, max(0, by + bh - 1 - y))
        elif edge == "bottom":
            if cy == by:
                cy, ch = by + 1, h - 1
        elif edge == "left":
            cw = min(w, max(0, bx + bw - 1 - x))
        elif cx == bx:
            cx, cw = bx + 1, w - 1
        cv.clip(cx, cy, cw, ch)
        looked = not self._zone_is_game(where) and not self._in_window(where) \
            and (self._home_ground(cv, where, 0, 0)
                 or _ui_draw.bar_part(cv, th, _band_part(), (bx, by, bw, bh), _BAND_SIDE[edge]))
        if not looked:
            _ui_draw.frame_band(cv, th, (bx, by, bw, bh), self.zone_band_light(where),
                                _BAND_SIDE[edge])
        cv.clip()
        return cv, bar, (x, y, w, h), looked

    def redraw_clock(self, where):
        """Repaint JUST the clock cell of an already-drawn bar (#155).

        Lets the desk backdrop cache survive a clock tick. The desk render is
        wallpaper cover-crop + icon column + this bar, ~77ms on the P4, and the
        clock is the only part of it that changes while nothing else does -- so
        without this, ticking the minute invalidated the whole cached desk once a
        second. The cell is the clock's own (no other widget's cell overlaps
        it, the taskbar chips stop short of it), so repainting it cannot erase
        them. A layout with no clock has nothing to repaint."""
        ws = self.ws
        got = self.repaint_cell(where, "clock")
        if got is None:
            return
        cv, bar, (x, y, w, h), looked = got
        edge, (bx, by, bw, bh), dy, _cells = bar
        th = ws.theme_colors
        text = self._clock_text()
        across = edge in ("left", "right")
        if looked:
            cv.camera(-bx, -by)
            self._paint_look_cell(cv, where, "clock", (x - bx, y - by, w, h), None,
                                  (0, 0, bw, bh), across)
            cv.camera(0, 0)
        elif across:
            cv.camera(-bx, -by)
            mp = _ch.chrome_inks(th, self._NAMES)
            self._paint_cell(cv, mp, where, "clock", (x - bx, y - by, w, h), None,
                             ws.layout.cs, dy, True, False)
            cv.camera(0, 0)
        else:
            cv.print(text, x, by + 3 + dy, th["chrome_ink"], 1)
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
            self._date_cache = (lt[0], lt[1], lt[2])
        except Exception:  # noqa: BLE001
            secs = _ticks_diff(_ticks_ms(), 0) // 1000
            s = "%02d:%02d" % ((secs // 60) % 100, secs % 60)
        self._clock_at = now_s
        self._clock_cache = s
        return s

    def _today(self):
        """(year, month, day) from the clock's per-second read, (0, 0, 0)
        where there is no wall clock."""
        self._clock_text()
        return self._date_cache

    def handle_bar_tap(self, where, px, py):
        """The zoned bars' tap slice (Stage 4), shared by every screen they draw
        on: the clock Easter egg, the ≡ system menu, the wifi status icon, and
        the X (where a layout places one) are OS-owned, IDENTICAL wherever a bar shows them (same as
        the draw), checked first so a tap on any never falls through to the lent
        zone. Anything else on a bar routes to the active app's zone_tap over its
        lent zone. Returns True iff a bar (or the app's zone) consumed the tap.
        The clock-run reset runs for ANY non-clock tap (byte-identical to the
        pre-Stage-4 launcher pointer), so taps that fall through to the content
        still reset it. (px, py) are on `where`'s canvas; every cell is in its
        bar's coordinates."""
        ws = self.ws
        for bar in self._packed(where):
            r = bar[1]
            if _in(px, py, r):
                return self._tap_bar(where, bar, px - r[0], py - r[1])
        ws.ach_ui._clock_taps = 0
        return False

    def _tap_bar(self, where, bar, px, py):
        ws = self.ws
        cells = bar[3]
        zone = None
        for c in cells:
            if c[0] == "zone":
                zone = c[1]
        if self._in_window(where):
            # Inside a WM window (#73) the bar has NO OS widgets (suppressed in
            # the draw), so there is nothing OS-owned to hit -- the tap goes
            # straight to the app's lent zone; closing lives on the WM title strip.
            owner = self._zone_owner(where)
            return bool(owner.zone_tap(px, py, zone)) \
                if owner is not None and zone is not None else False
        hit = None
        for c in cells:
            if c[0] in _TAPPED and _in(px, py, c[1]):
                hit = c
                break
        name = hit[0] if hit is not None else None
        # Clock Easter egg (#21): tapping the bar's clock _CLOCK_TAP_GOAL times
        # wakes the Time Traveler; every tap also opens (or closes) its calendar.
        # Checked before the ≡/X/zone so a tap on the clock never falls through.
        if name == "clock":
            ws.ach_ui._tap_clock()
        else:
            ws.ach_ui._clock_taps = 0            # any other bar tap resets the run
        # The ≡, the clock, wifi and battery open their component
        # (docs/shell_layout_2026-10.md §8): a popover in desktop mode, the
        # system menu as the popup it is and the rest fullscreen in console
        # mode -- wifi the Settings WIFI panel either way, the one wifi
        # implementation. Consumed, so a tap never leaks to the lent zone.
        if name in ("clock", "menu", "wifi", "battery"):
            ws.widget_tap(name)
            return True
        # The X, where a layout places `close` (spec Section 9): tap to EXIT the active surface toward
        # its mode's root. On the home it leaves console mode for the desk beneath
        # (docs/shell_layout_2026-10.md §3); where the home is the floor it draws no X
        # and is not tested. A running TOOL (Part 4) pops to its RUN caller (spec
        # Section 6's test-play round trip -- go_home for a launcher-launched tool, but
        # the Editor tab if a tool is ever PLAYed from the editor); every other taskbar
        # app (Editor/Settings) uses the screen-string exit.
        if name == "close" and self._show_x(where):
            if where == "home":
                ws.open_desk()
            elif where in ("tool", "app"):
                ws._exit_to_caller()
            else:
                ws.exit()
            return True
        owner = self._zone_owner(where)
        if zone is not None and self.draws_back(where):
            game = self._zone_is_game(where)
            if zone[0] <= px < zone[0] + self._back_w(game):
                self.go_back(where)
                return True
            if owner is not None and not getattr(owner, "back_title", None):
                zone = self.past_back(zone, game)
        # Pass the SAME lent rect the draw used, in the bar coordinates (px, py) are
        # in now, so an app's zone_tap hit-tests its icons at the exact positions
        # draw_zone drew them wherever the bar sits.
        if owner is None or zone is None:
            return False
        return bool(owner.zone_tap(px, py, zone))

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
