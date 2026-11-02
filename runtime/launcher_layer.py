# Map (grep -n a name to jump there):
#   LauncherHomeLayer           the home: wallpaper, the cells, the bar's tabs, the hints
#   LauncherHomeLayer.draw      the home frame
#   LauncherHomeLayer.zone_tap  the tabs in the bar's zone
#   HomeWindowLayer             the home in a desktop-mode window (PROJECTS)
"""The home (#28), extracted from Workstation (runtime/console.py) as a Layer
-- docs/history/shell_layers_refactor_v1.md (Move 1b).

  * `Launcher`     -- (home_grid.py) the selection model (items/sel/scroll/nav)
                      and the `grid` arrangement's draw: a shelf of cover
                      cards in the rows the glass holds, scrolling sideways.
                      The arrangements below are its subclasses.
  * `Carousel`     -- (home_carousel.py) the home's row
                      (docs/theme_and_shell_2026-11.md §8.10): the
                      same selection model over one row of square cover cards,
                      the centre card larger by a function of its distance
                      from the centre; on the desk's metrics the selected
                      card is the hero beside its details over a shelf. Its
                      instances are ws.launcher in the `carousel`
                      arrangement, the default (the layout's `home`), and
                      ws.projects_grid, the home window's, always.
  * `IconHome`     -- (home_icons.py) the `icons` arrangement: the same model over tiles of
                      the covers' reduction that scroll down, the focused
                      cart named in a caption band on the handheld and
                      every tile named under it on the desk.
  * `LauncherHomeLayer` -- the "launcher" content Layer: the home, console mode's
                      root on every board (wallpaper -> cells -> hint strip ->
                      bar, its tabs in the bar's zone), its four tabs (Games / Mine / Apps / Make, the lists
                      ws._launcher_items builds) and their input. A tap on the
                      centre card is the active tab's (ws.launch_selected).
                      Its bases: `HomeSearch` (home_search.py, the search
                      line).
  * `HomeWindowLayer` -- the "projects" content Layer: the SAME home in a
                      desktop-mode window (docs/theme_and_shell_2026-11.md §8.10, the
                      desk's PROJECTS): the console home's design over
                      ws.projects_grid, laid out in the window's content
                      rect, opened on its Make tab.

Boundary (single source of truth): ws.launcher (the instance) + ws.open() (open the
selected cart -- lifecycle, pinned) + the cart store stay on Workstation. The card
draw needs the palette -- `NAMES` is INJECTED at construction. The rect
hit-test is `ui.rect_in`. No circular import: this module and the home_*
modules it takes its arrangements and band frame from import no console
module (the only console touch is Launcher's lazy Layout fallback for a bare
Launcher() that no caller ever constructs).

The lent bar zone (#46): each home layer draws and taps it (`draw_zone`/`zone_tap`)
and answers its `zone_gen` -- one int of what the zone shows, the home's tabs (the
open one and the tab row's focus), so BarLayer's strip cache trusts one int. The
grid's own `zone_gen` (bumped whenever its `sel` or `items` changes) is the
grid's.
"""


from array import array

try:
    import ui as _ui
    import ui_draw as _ui_draw
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime import ui as _ui
    from runtime import ui_draw as _ui_draw
try:
    from moy_input import SHIFT as _SHIFT
except ImportError:  # pragma: no cover - host fallback
    from runtime.moy_input import SHIFT as _SHIFT
_in = _ui.rect_in   # one hit-test (ui.rect_in)


# The tick helpers feed the perf_capture-gated home-frame split (see draw below).
try:
    from chrome import _ticks_ms, _ticks_diff
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.chrome import _ticks_ms, _ticks_diff

try:
    import layout_base as _lb
    import shell_layout as _shell_layout
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime import layout_base as _lb
    from runtime import shell_layout as _shell_layout

try:
    from moy_store_base import builtin_name as _builtin_name
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.moy_store_base import builtin_name as _builtin_name

try:
    from home_band import (_wall_ink, _overlaps_any, _ground_of, _dim_of, _cursor_stamp,
                           _statics_base, _Keyed, _band_key, _band_frame)
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.home_band import (_wall_ink, _overlaps_any, _ground_of, _dim_of, _cursor_stamp,
                                   _statics_base, _Keyed, _band_key, _band_frame)
try:
    from home_search import HomeSearch
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.home_search import HomeSearch
try:
    from home_icons import IconHome
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.home_icons import IconHome
try:
    from home_carousel import Carousel
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.home_carousel import Carousel
try:
    from home_grid import (day_words, desk_button_layout, DESK_ROW_GAP, _dot_top, HOME_SCALE_HH,
                           HOME_TABS, NEW_TILE_TYPE, PSEUDO_TILE_TYPES, text_scale, _VERB_HINT)
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.home_grid import (day_words, desk_button_layout, DESK_ROW_GAP, _dot_top,
                                   HOME_SCALE_HH, HOME_TABS, NEW_TILE_TYPE, PSEUDO_TILE_TYPES,
                                   text_scale, _VERB_HINT)


# The home layer's own words and keys: the tab row's order, the verbs'
# letters and the strips' keys.
_TAB_ORDER = {tid: i for i, (tid, _label) in enumerate(HOME_TABS)}
_UNFAVOURITE = "Remove favourite"   # the star's words on a card that wears it
_VERB_KEY = {"c": "change", "/": "search", "n": "new", "y": "copy", "x": "delete",
             "p": "window"}
_FAVOURITE_KEY = "f"     # the selected card's star, on every tab that draws one
# The inputs (ui_draw.INPUTS) that type those letters: a gamepad has no C.
_LETTER_INPUTS = ("keys", "tdeck")
# A press held this long on a card without travel opens the card's verbs (a
# phone home screen's long press), in frame milliseconds.
LONG_PRESS_MS = 500
_TAB_KEYS = ("tabs", "Tabs")
_HINT_LEAD = 5       # the handheld's margin ui.hints leads with, at the font
                     # scale: the top bar's (MOY_UI_HINT_LEAD)
_HINT_LEAD_DESK = 8  # the desk's
BACK_TIP = "Desk"    # the tooltip on the bar's chevron back to the desk
DESC_LINES = 3       # the desk home's description: at most this many lines,
DESC_EMS = 48        # ... as wide as this many n's: about 60 characters of
                     # running text, the width of the buttons' row
_MENU_KEYS = ("back", "Close")   # the strip under the system menu
# The strip while the keys are on the tab row: left and right move the focus
# along it, confirm opens the tab it is on, back comes off the row.
_TAB_ROW_KEYS = ("change", "Move", "confirm", "Open", "back", "Back")
_CONFIRM_KEYS = ("confirm", "Choose", "back", "Cancel")   # under the delete confirm


def modal_keys(ws):
    """The keys a home's hint strip names while a system modal holds them:
    the delete confirm's dialog's, the system menu's card's, the bar's while
    the keys are on it, else None (the home's own verbs)."""
    if ws.confirm.open:
        return _CONFIRM_KEYS
    if ws.menu_ui.looked():
        return _MENU_KEYS
    if _ui_draw.on_bar():
        return _ui_draw.BAR_KEYS
    return None


def times_words(n):
    """A play count as the home says it."""
    return "Not yet" if not n else ("Once" if n == 1 else "%d times" % n)


class LauncherHomeLayer(HomeSearch):
    """The "launcher" content Layer (system domain): the home, console mode's root.
    draw composes the wallpaper backdrop -> the tab row and the cards (`grid`,
    ws.launcher) -> the top bar; input is the grid nav / selection, dispatching
    the active tab's tap (ws.launch_selected). Owns the cursor's hover state
    (_lhover), the tab-row focus; `grid` is the single source
    it reads. `HomeWindowLayer` is the same home in a window."""

    id = "launcher"
    domain = "system"
    strip = "home"            # the bar strip it draws and taps (BarLayer's `where`)
    searchable = True         # Games carries SEARCH (the query is the home's)

    def __init__(self, ws, names):
        self.ws = ws
        self._NAMES = names
        self._lhover = (-1, -1)       # the last pointer sample (a release is not a move)
        self._back_tip = False        # a mouse rests on the bar's way back to the desk
        self._hold = None             # a press on a card: [index, x, y, ms held]
        self._tab_at = None           # the tab the keys are on, on the tab row
        self._on_bar = False          # the keys are on the home's bar (BarLayer.focus_enter)
        self._tab_focus = False       # keys drive the tab row (up from the carousel)
        # The band frame's keys (`_band_frame`; #58/#66: a FULL home repaint
        # stacks megabytes of writes on a board): the band table's
        # (`_band_key`), the hint strip's and the caption band's (`_beside`),
        # and the rects `_beside` drew.
        self._sk = _Keyed()
        self._bk = _Keyed()
        self._keys = array("i", [-1, -1])
        self._keyed_gen = -1          # the cover gen `_band_key` keyed the band under
        self._hk = _Keyed()
        self._ck = _Keyed()
        self._beside_drew = []
        # what the hint strip's key was made of, and the key (`_outside_key`)
        self._ok = [None, None, None, None, None, None, None, None]
        self._search_kprev = 0        # keyboard edge detect while typing a search query
        self._kprev = 0               # ...and for the carousel home's hint keys
        self._zone_bump = 0           # the tab row's focus moved (the bar's zone shows it)
        # Retained WHOLE-frame cache (2026-07-27, the damage-model doctrine at
        # shelf granularity): the last full home frame, captured at the END of
        # a live paint (right after our own last pixel -- overlays/cursor draw
        # AFTER this layer, so the copy is exactly our output) and stamped back
        # on re-entry when NOTHING the frame reads has changed (_retained_key)
        # -- a repeat home visit becomes one full-screen blit instead of the
        # ~150ms wallpaper + panel + cards + bar rebuild. An idle frame
        # captures only what every buffer holds (`capture_due`, owed by a
        # still frame): the P4 root rotates its buffers, so after present the
        # canvas's CURRENT buffer may hold an older frame, and an idle capture
        # of it would cache another screen's pixels under this frame's key.
        self._lib_cache = None        # cv.new_layer sized to the canvas
        self._lib_key = None          # _retained_key at capture
        self._cap_owed = None         # (frame, key, parts) a still frame owes
        self._debt_owed = None        # the frame a step left a buffer behind (`debt_due`)
        self._lib_unsupported = False # web RecordingLayer: no pixels to copy
        # The wallpaper under the carousel band, captured on every full paint
        # right after the wallpaper drew: a moving frame restores the band's
        # backdrop from it (the cards lie on the wallpaper, so the band cannot
        # shift). The key is what the capture was taken under -- the canvas,
        # the theme, the wallpaper and the band rect.
        self._band_bg = None          # cv.new_layer sized to the band
        self._band_key = None
        # The desk home's still-backdrop frames (`_try_still_frame`): the
        # keys its parts are named under in the band host's paints
        # (`ui.held`: the backdrop, the selection, the shelf, the bar); the
        # backdrop `restore_key` this frame answered; and the parts the
        # retained frame was captured under (`_capture_retained` copies only
        # the ones that moved).
        self._stk = _Keyed()
        self._slk = _Keyed()
        self._shk = _Keyed()
        self._bd_now = None
        self._lib_parts = None

    @property
    def grid(self):
        """The card model this home draws: ws.launcher, whose class is the
        layout's arrangement."""
        return self.ws.launcher

    def _grid_or_none(self):
        """The card model, None while it is not minted yet (the window's
        before its first open, the home's during the console's build)."""
        try:
            return self.grid
        except AttributeError:
            return None

    def _wall(self):
        """(white, black) where this home lies on a wallpaper (`_wall_ink`),
        else None."""
        return _wall_ink(self.ws)

    @property
    def _tab_focus(self):
        return self._tab_keys

    def at_top(self):
        """Up goes on to the bar from the tab row where the row is below
        the bar (Workstation._bar_keys); where the bar holds the tabs they
        are one row with its items, and up stays."""
        return bool(self._tab_keys) and not self.tabs_in_bar()

    def tabs_in_bar(self):
        """`tab_rects` where the tabs sit in the bar's row (the handheld's
        zone), so the keys walk them and the bar's items as one row
        (`BarLayer.focus_beside_tabs`); else []."""
        return self.tab_rects()

    def tab_enter(self, k):
        """The keys onto the tab row at its `k`th tab (-1 the last), from
        the bar's items beside it."""
        self._tab_focus = True
        ids = self._tab_ids()
        self._tab_at = ids[k]
        self._zone_bump += 1
        self.ws.mark_dirty()

    def _tab_ids(self):
        """The ids of the tabs the row shows, left to right."""
        return [t[0] for t in self.tab_rects()] or [t for t, _l in HOME_TABS]

    def focused_tab(self):
        """The tab the keys are on while the tab row has them (it opens on
        confirm), else None."""
        if not self._tab_keys:
            return None
        ids = self._tab_ids()
        at = self._tab_at
        return at if at in ids else self.grid.tab

    def _tab_step(self, d):
        """The tab row's focus `d` tabs along; off its ends onto the bar's
        item beside it where the bar holds the tabs, else it stops there.
        The open tab stays until a confirm."""
        ids = self._tab_ids()
        at = self.focused_tab()
        k = ids.index(at) + d if at in ids else 0
        if not 0 <= k < len(ids) and self.ws.bar_layer.focus_beside_tabs(d):
            return
        k = max(0, min(len(ids) - 1, k))
        if ids[k] != self._tab_at:
            self._tab_at = ids[k]
            self._zone_bump += 1
            self.ws.mark_dirty()

    @_tab_focus.setter
    def _tab_focus(self, on):
        # one focus mark a screen: the tab row's takes it from the cards
        if on != getattr(self, "_tab_keys", None):
            self._zone_bump = getattr(self, "_zone_bump", 0) + 1
            g = self._grid_or_none()
            self._tab_at = g.tab if on and g is not None else None
        self._tab_keys = on
        if on:
            self._btn = None
        g = self._grid_or_none()
        if g is not None:
            g.tab_focus = (on or getattr(self, "_btn", None) is not None
                           or getattr(self, "_on_bar", False))

    def bar_focus(self, on):
        """The keys go on to the home's bar (`on`) or off it: the cards let
        their focus mark go meanwhile, one mark a screen."""
        if on != self._on_bar:
            self._on_bar = on
            self._zone_bump += 1
        g = self._grid_or_none()
        if g is not None:
            g.tab_focus = on or self._tab_keys or getattr(self, "_btn", None) is not None
        self.ws.mark_dirty()

    @property
    def _button_focus(self):
        """The desk home's button the keys are on (up from the shelf), an
        index into `_desk_buttons`, or None: the shelf's card has them."""
        return getattr(self, "_btn", None)

    @_button_focus.setter
    def _button_focus(self, k):
        # one focus mark a screen: the button's takes it from the card
        if k != getattr(self, "_btn", None):
            self._zone_bump = getattr(self, "_zone_bump", 0) + 1
        self._btn = k
        if k is not None:
            self._tab_keys = False
        g = self._grid_or_none()
        if g is not None:
            g.tab_focus = (k is not None or bool(getattr(self, "_tab_keys", False))
                           or getattr(self, "_on_bar", False))
        self.ws.mark_dirty()

    @staticmethod
    def _button_rows_apart(buttons, k):
        """(the button over `k`, the button under it) where the pane's
        buttons wrap to rows, each the nearest in its row; None for none."""
        r = buttons[k][1]
        mid = r[0] + r[2] // 2
        out = []
        for above in (True, False):
            ys = [b[1][1] for b in buttons if (b[1][1] < r[1]) == above and b[1][1] != r[1]]
            if not ys:
                out.append(None)
                continue
            y = max(ys) if above else min(ys)
            row = [j for j, b in enumerate(buttons) if b[1][1] == y]
            out.append(min(row, key=lambda j: abs(buttons[j][1][0] + buttons[j][1][2] // 2
                                                   - mid)))
        return out[0], out[1]

    def _button_keys(self, i):
        """The keys on the desk home's buttons: left and right move along
        them, confirm presses one, up and down move between their rows where
        they wrap, up from the first row goes on to the tabs, down from the
        last and B back to the shelf. False where no button has them, or for another key."""
        k = self._button_focus
        if k is None:
            return False
        ws = self.ws
        buttons = self._desk_buttons()
        if not buttons:
            self._button_focus = None
            return False
        k = min(k, len(buttons) - 1)
        if not (i.steps("left") or i.steps("right") or i.steps("up") or i.steps("down")
                or i.pressed("a") or i.pressed("run") or i.pressed("b")
                or i.pressed("home")):
            return False                     # the strip's letters still act
        up, down = self._button_rows_apart(buttons, k)
        if i.steps("left"):
            self._button_focus = max(0, k - 1)
        elif i.steps("right"):
            self._button_focus = min(len(buttons) - 1, k + 1)
        elif i.steps("up") and up is not None:
            self._button_focus = up
        elif i.steps("up"):
            self._tab_focus = True
        elif i.steps("down") and down is not None:
            self._button_focus = down
        elif i.steps("down") or (i.pressed("b") and ws.ach_ui._konami_pos == 0):
            self._button_focus = None
        elif i.pressed("a") or i.pressed("run"):
            v = buttons[k][0]
            if v != "delete":                # the confirm's Cancel comes back to DEL
                self._button_focus = None
            self._do_verb(v)
        elif i.pressed("home"):
            ws.home_key()
        return True

    def _statics_key(self, cv):
        """Everything the home frame's STATIC chrome (wallpaper backdrop +
        Library panel fill/header/footer) is a pure function of: the shared
        base plus the wallpaper, the search field and the keys a modal's hint
        strip names (`modal_keys`). A key change forces full paints until the
        streak re-arms. Where the keys are -- the tab row, the bar, a card --
        is not in it: the tabs are the bar's, which a band frame draws, the
        hint strip names the tab row's keys under its own key (`_outside_key`)
        and the selected card wears the focus as its part's gen."""
        ws = self.ws
        return _statics_base(ws, cv, self.grid) + (
            ws.look.wallpaper_id, modal_keys(ws),
            getattr(ws, "search_query", ""), getattr(ws, "search_typing", False),
            getattr(ws, "search_typing", False) and ws.can_type(),
            self.grid.tab, self._button_focus, self.grid.arrangement,
            self.grid.statics_sel(), self._back_tip)

    def _retained_key(self, cv, statics=None):
        """Everything the WHOLE home frame's pixels are a pure function of: the
        chrome statics (`statics`, where the caller made them this frame)
        plus the grid's dynamics (selection, scroll
        offset as it was set -- its clamp is a function of the statics' band
        and item count -- the cover-cache generation, trackball hover, the
        items and the titles they carry (the list, never mutated, and the
        roster's count of titles taken in place), the settings rows'
        generation for the favorites and the play records) and the bar's own
        pixel key (`shown_key` -- clock minute, wifi state, the look; reusing
        it means the bar's cache discipline IS this cache's discipline).
        Anything that draws a pixel of the home frame and is NOT derivable
        from this tuple is a staleness bug -- the silent-cache family
        surface_model_v1.md §14.2 documents -- so additions to the home draw
        must extend this key."""
        ws = self.ws
        g = self.grid
        return (self._statics_key(cv) if statics is None else statics, g.sel,
                g._scroll, ws.covers.gen, self._lhover, g.items,
                ws.carts.titled, ws.system.gen(), ws.bar_layer.shown_key("home"),
                self._tab_keys, self._tab_at, self._on_bar)

    def _try_stamp_retained(self, cv, dt):
        """Present the retained home frame when nothing it depends on changed:
        one full-screen blit (~26ms on P4 glass) instead of the full rebuild.
        Only for settled frames -- a drag/fling wants the partial path, and an
        animating overlay/wallpaper means the pixels are NOT a pure function
        of the key. Returns True when the frame was presented."""
        ws = self.ws
        cache = self._lib_cache
        if (cache is None or self._lib_unsupported
                or self.grid.dragging or self.grid.flinging
                or ws._animating(dt)):
            return False
        # A canvas that retains nothing (RETAINED_FRAMES < 1) holds no frame to
        # capture from or replay.
        if getattr(cv, "RETAINED_FRAMES", 0) < 1:
            return False
        if cache.w != cv.w or cache.h != cv.h:
            return False
        if self._retained_key(cv) != self._lib_key:
            return False
        cv.blit_strip(cache, 0, 0)
        return True

    def prealloc_retained(self):
        """Mint the retained-frame layer on an IDLE frame (called from frame()'s
        idle branch): the device new_layer pre-collects, which measured as a
        371ms first home visit when the allocation rode the capture's paint
        frame (~150ms collect + ~35ms copy on top of the ~180ms live paint).
        Allocation reads no pixels, so the ping-pong trap in __init__'s note
        does not apply -- only the CAPTURE must stay on the paint path."""
        ws = self.ws
        if self._lib_unsupported:
            return
        cv = ws.sys_canvas
        if getattr(cv, "RETAINED_FRAMES", 0) < 1:
            return
        g = self._grid_or_none()
        if g is not None and not g.moving and ws.screen == self.id:
            g.mint_faces()               # (only under the home: a face is the home's)
        cache = self._lib_cache
        try:
            if cache is None or cache.w != cv.w or cache.h != cv.h:
                self._lib_cache = cv.new_layer(cv.w, cv.h)
                self._lib_key = None      # freshly minted: no captured pixels
                self._lib_parts = None
            self._band_layer(cv)
        except Exception:  # noqa: BLE001
            self._lib_cache = None
            self._band_bg = None
            self._lib_unsupported = True

    def _band_layer(self, cv):
        """The band backdrop's layer, minted (or re-minted for a new band
        size) on demand."""
        bx, by, bw, bh = self.grid.band_rect()
        bg = self._band_bg
        if bg is None or bg.w != bw or bg.h != bh:
            if bg is not None and getattr(bg, "release", None) is not None:
                bg.release()
            self._band_bg = bg = cv.new_layer(bw, bh)
            self._band_key = None
        return bg

    def release_retained(self):
        """Free the retained-frame layer (the fit check's release, after any
        parked window stamps): True when there was one. The next settled
        visit re-mints and re-captures it. The band's backdrop and the
        cards' kept faces go with it."""
        freed = self.grid.release_faces()
        for name in ("_lib_cache", "_band_bg"):
            cache = getattr(self, name)
            if cache is None:
                continue
            setattr(self, name, None)
            rel = getattr(cache, "release", None)
            if rel is not None:
                rel()
            freed = True
        self._lib_key = None
        self._lib_parts = None
        self._band_key = None
        return freed

    def _band_key_of(self, cv):
        ws = self.ws
        return _statics_base(ws, cv, self.grid) + (ws.look.wallpaper_id,)

    def _capture_band_backdrop(self, cv, dt):
        """Copy the band's rect of the wallpaper just drawn -- before any
        chrome or card -- on every full paint, so an edited wallpaper (the
        Paint app's document keeps its id) is never restored stale. Not while
        the wallpaper animates (those frames are full paints anyway) and not
        on a canvas that retains nothing."""
        ws = self.ws
        self._band_key = None
        if (self._lib_unsupported or ws._animating(dt)
                or ws.wallpaper.restore_key(True) is not None):
            return                # (a wallpaper that restores needs no copy)
        if getattr(cv, "RETAINED_FRAMES", 0) < 1:
            return
        key = self._band_key_of(cv)
        try:
            bg = self._band_layer(cv)
            bx, by, _bw, _bh = self.grid.band_rect()
            bg.blit_strip(cv, -bx, -by)
            self._band_key = key
        except Exception:  # noqa: BLE001 -- forfeit, as the retained frame does
            self._band_bg = None
            self._band_key = None
            self._lib_unsupported = True

    def _backdrop_fill(self, cv, r):
        """The band's backdrop inside rect `r`: the wallpaper, restored from
        the captured band."""
        bx, by, _bw, _bh = self.grid.band_rect()
        cv.blit_strip_rect(self._band_bg, bx, by, r[0], r[1], r[2], r[3])

    def _capture_retained(self, cv, dt, parts=None):
        """Capture the frame just painted into the retained cache -- called at
        the END of a live draw, while cv is still the back buffer being painted
        (pre-present, so it is provably THIS frame; see __init__'s ping-pong
        note for why an idle capture cannot be trusted). Overlays and the
        cursor draw after this layer, so the copy is exactly our output. Costs
        one full-screen copy (~26ms on P4) charged only when the key CHANGED --
        once per settled state, not per frame. Skipped mid-drag/fling (the key
        carries the scroll offset, so every frame would re-capture) and while
        anything animates (those pixels are not a pure function of the key)."""
        ws = self.ws
        if (self._lib_unsupported or self.grid.dragging
                or self.grid.flinging or ws._animating(dt)):
            return
        if getattr(cv, "RETAINED_FRAMES", 0) < 1:
            return              # a canvas that retains nothing has no frame to copy
        key = self._retained_key(cv)
        if key == self._lib_key:
            return
        if parts is None and self._carousel():
            parts = self._still_parts(cv, ws.wallpaper.restore_key(True))
        bar = ws.bar_layer.shown_gen("home")
        try:
            cache = self._lib_cache
            lp = self._lib_parts
            if cache is None or cache.w != cv.w or cache.h != cv.h:
                cache = self._lib_cache = cv.new_layer(cv.w, cv.h)
                lp = None
            ws.note_cost("home.frame.capture")
            if parts is not None and lp is not None and lp[0] == parts[0]:
                # The captured frame has this one's backdrop: only the
                # parts that moved since are copied.
                for r in self._moved_rects(parts, lp, bar):
                    cache.blit_strip_rect(cv, 0, 0, r[0], r[1], r[2], r[3])
            else:
                cache.blit_strip(cv, 0, 0)
            self._lib_key = key
            self._lib_parts = None if parts is None else parts + (bar,)
        except Exception:  # noqa: BLE001 -- a recording root has no pixels to
            self._lib_cache = None     # copy; forfeit the mechanism for the
            self._lib_key = None       # session rather than retry every paint
            self._lib_parts = None
            self._lib_unsupported = True

    def _moved_rects(self, parts, was, bar):
        """The rects of the desk home's frame `parts` (and bar key `bar`)
        that differ from the frame `was` held, where the two share a
        backdrop: the selection's, the band, the bar's and the hint strip."""
        g = self.grid
        out = []
        if was[1] != parts[1]:
            out.append(self._sel_rect())
        if was[2] != parts[2]:
            out.append(g.band_rect())
        if was[3] != bar:
            out.extend(self.ws.wallpaper.bars())
        h = g.foot()[5]
        if h[2] > 0 and h[3] > 0:
            out.append(h)
        return out

    def _carousel(self):
        """Whether the home shows the carousel (else the grid arrangement)."""
        return isinstance(self.grid, Carousel)

    def _try_band_frame(self, cv, dt):
        """The home's band frame (`_band_frame`), timed for the home
        diag's HITCH split: only once the band's backdrop holds this frame's
        wallpaper, every arrangement's cards lying on it. Any frame takes it
        -- a step, a settle -- where the wallpaper puts its own pixels back
        (the hint strip lies on it) on a canvas that keeps them (not a
        recording root, `_lib_unsupported`), but on the desk's carousel, whose
        selection is the hero beside the band (`_try_still_frame`). Never
        under touch's search line, which lies in the band."""
        bd = self._bd_now
        if bd is not None:
            fill = self.ws.wallpaper.restorer(cv, bd)
        elif self._band_bg is None or self._band_key != self._band_key_of(cv):
            return False
        else:
            fill = self._backdrop_fill
        if self._search_line() is not None:
            return False
        self.grid.flat = bd is not None and bd[0] == "bg"
        steps = (bd is not None and not self._lib_unsupported
                 and not (self._carousel() and self.grid.desk()))
        return _band_frame(self, cv, dt, self.grid, self.id, fill, self.strip,
                           timed=True, steps=steps)

    def _face_key(self, bd):
        """What the cards' kept faces (`Launcher._faces_for`) are drawn
        under for a paint over backdrop `bd` (a `restore_key`): the look's
        generation, the plain ground's colour (None over a backdrop or a
        picture) and the font scale; None where the canvas keeps no
        pixels. And whether the band's backdrop goes back in one copy
        (`restore_whole`): a baked backdrop, a picture or the band's
        capture, whose cut round the cards costs more in calls than the
        bytes it saves."""
        ws = self.ws
        g = self.grid
        g.frame_no = ws._frames_drawn
        g.restore_whole = True
        if self._lib_unsupported:
            g.face_key = None
            return
        gen = ws.look.look_gen
        flat = bd[1] if bd is not None and bd[0] == "bg" else None
        fs = ws.layout.fs
        k = g.face_key
        if k is None or k[0] != gen or k[1] != flat or k[2] != fs:
            g.face_key = (gen, flat, fs)     # (kept while it holds: a frame allocates nothing)

    def _outside_key(self):
        """What the home's parts outside the band show of the selection:
        the hint strip its verbs and its star (`_hint_groups`), or the tab
        row's keys while the tab row has them (`_draw_hints`), the handheld
        icons' caption band its cart and the day its line counts from. The
        hint strip's is made again only where what it is made of moved: the
        grid's selection or items (its `zone_gen`), its tab, the favourites
        (the settings rows' generation), the tab row's keys and the store's
        `can_manage`."""
        g = self.grid
        ws = self.ws
        m = self._ok
        zg = g.zone_gen
        sg = ws.system.gen()
        tk = bool(self._tab_keys)
        cm = getattr(ws, "can_manage", True)
        if not (m[1] == zg and m[0] is g and m[2] == g.tab and m[3] == sg and m[4] == tk
                and m[5] == cm and m[6] is g.favorite_for):
            it = self.favourable()
            m[7] = (self.zone_verbs(), it is not None and ws.carts.is_favorite(it), tk)
            m[0] = g
            m[1] = zg
            m[2] = g.tab
            m[3] = sg
            m[4] = tk
            m[5] = cm
            m[6] = g.favorite_for
        hint = m[7]
        if isinstance(g, IconHome) and g._cells().caption is not None:
            return (hint, (g.sel, g.today()))
        return (hint, None)

    def _beside(self, cv, whole, draw=True):
        """The parts outside the band a selection changes (the hint strip,
        the handheld icons' caption band), inside the band's frame: each
        named under its key (`ui.held`) and drawn where the buffer this
        frame paints holds another selection's, or `whole`; their rects in
        `_beside_drew`. With `draw` False they are only named (a frame
        that drew them by other means)."""
        drew = self._beside_drew
        del drew[:]
        g = self.grid
        now = self._outside_key()
        if now is None:
            return
        kh = self._hk.of(now[0])
        held = _ui.held("hint", kh)
        # where the last frame's buffer holds the frame round the band and
        # the part as this frame draws them, the part is copied out of it
        cl = (getattr(cv, "copy_last", None)
              if draw and not whole and _ui.held_last("band", self._keys[0]) else None)
        if draw and (whole or not held):
            h = g.foot()[5]
            if h[2] > 0 and h[3] > 0:
                if not (cl is not None and _ui.held_last("hint", kh)
                        and cl(h[0], h[1], h[2], h[3])):
                    self.ws.wallpaper.restorer(cv, self._bd_now)(cv, h)
                    self._draw_home_chrome(cv)
                drew.append(h)
        if now[1] is not None:
            kc = self._ck.of(now[1])
            held = _ui.held("caption", kc)
            if draw and (whole or not held):
                c = g._cells()
                r = c.caption
                if not (cl is not None and _ui.held_last("caption", kc)
                        and cl(r[0], r[1], r[2], r[3])):
                    g._draw_caption_band(cv, c)
                drew.append(r)

    def _name_beside(self, cv, whole):
        """`_beside` naming the parts a whole paint drew."""
        self._beside(cv, whole, False)

    def _band_settle(self, end, whole=True):
        """After a band frame with nothing moving, by its floor's end
        (`ui.table_floor`): where every buffer holds this frame, the
        retained frame is owed to an idle frame (`capture_due`); where one
        lags behind a frame that drew the band `whole` (more than half of
        it: a scroll's, a jump's, a cover's landing), the frame that brings
        one more buffer to it is asked for, while nothing moves and no
        press waits (`_still_debt`). Behind a step that drew its cells
        alone, that frame is owed to an idle frame (`debt_due`): a frame
        between two steps of a rolling ball would only delay the second,
        and the next step's floor takes what that buffer lacks."""
        if end == 0:
            # (the statics this frame's band key was made of: `_band_key`)
            key = self._retained_key(self.ws.sys_canvas, self._sk.v)
            if key != self._lib_key:
                self._cap_owed = (self.ws._frames_drawn, key, None)
        elif end == -1:
            if whole:
                self._still_debt()
            else:
                self._debt_owed = self.ws._frames_drawn

    def _band_note(self, cv, dt, vouch=True):
        """A frame that put the band there by other means (the retained
        frame's stamp, a still frame that drew no band): the band's kept
        table and the parts beside it noted as this frame holds them."""
        g = self.grid
        return g.band_pass(cv, self.ws.covers.sheet_icon, None, _band_key(self, cv, dt, g, vouch),
                           True, _cursor_stamp(self.ws), self._name_beside if vouch else None,
                           note=True)

    def _band_landed(self, cv, dt, vouch=True):
        """After a pass that drew the band: where a cover landed while it
        drew (the cover gen moved from the one its key holds), the cards
        drew it, so the band is noted again under the key the new gen is
        in: the buffer then holds the band whole under that key. Answers
        whether it noted."""
        if self.ws.covers.gen != self._keyed_gen:
            self._band_note(cv, dt, vouch)
            return True
        return False

    # -- the desk home's still-backdrop frame ------------------------------
    #
    # On the desk the home is a still backdrop under three parts that
    # change: the hero with its details pane (the selection), the shelf's
    # band and the bar. A frame repaints only the parts the buffer it paints
    # lacks, where the wallpaper can put its own pixels back
    # (`Wallpaper.restore_key`): every buffer holds this backdrop once the
    # RETAINED_FRAMES frames before this one were the home's alone, on it,
    # and each of those frames left its buffer holding its own parts. A
    # part that differs from any of theirs lags in this frame's buffer.

    def _still_parts(self, cv, bd):
        """(backdrop, selection, band) keys of the desk home's frame: what
        everything outside the selection's rect and the band is a pure
        function of (the wallpaper's `restore_key` `bd` among it), what the
        hero and its details are, and what the shelf's band is. None off
        the desk, or where the backdrop cannot be put back."""
        g = self.grid
        if bd is None or not g.desk():
            return None
        ws = self.ws
        typing = getattr(ws, "search_typing", False)
        base = (bd, cv.w, cv.h, ws.layout.fs, ws.look.look_gen, g._geom(),
                ws.look.wallpaper_id, modal_keys(ws), getattr(ws, "search_query", ""),
                typing, typing and ws.can_type(), self._tab_focus, self._tab_at,
                self._on_bar, self._back_tip)
        items = g.items
        it = items[g.nearest()] if items else None
        rows = ws.system.gen()
        gen = ws.covers.gen
        sel = g.statics_sel()
        return (base,
                (g.tab, sel, it, it and it.get("title"), it and it.get("description"),
                 gen, rows, self._button_focus, g.today()),
                (g.tab, sel, gen, self._lhover, tuple(i.get("title") for i in items),
                 rows, g.scroll, g.moving))

    def _sel_rect(self):
        """The desk home's hero and details pane with all they cast (the
        hero's shadow, a button's focus mark),
        between the bar and the shelf's band: what a change of the
        selection repaints."""
        g = self.grid
        lay = self.ws.layout
        q = g.quiet()[0]
        d = _ui_draw.depth()
        m = _ui_draw.mark_reach("part") + 1
        if d[5]:
            m += d[8] + max(abs(d[9]), abs(d[10]))    # the hero's shadow
        x0 = max(lay.cx, q[0] - m)
        y0 = max(lay.cy, q[1] - m)
        x1 = min(lay.cx + lay.cw, q[0] + q[2] + m)
        y1 = min(g.band_rect()[1], q[1] + q[3] + m)
        return (x0, y0, x1 - x0, y1 - y0)

    def _fill_around_hero(self, cv, r, fill):
        """Put the backdrop back in the selection's rect `r` but under the
        hero, which paints its square opaque but for its corners' rows (as
        a shelf card does, `Carousel._fill_around_cards`); all of it in one
        copy where the band's goes back so (`restore_whole`)."""
        hx, hy, hs, _h = self.grid.hero_rect()
        x0, y0, x1, y1 = r[0], r[1], r[0] + r[2], r[1] + r[3]
        if self.grid.restore_whole or not (x0 <= hx and y0 <= hy and hx + hs <= x1
                                           and hy + hs <= y1):
            fill(cv, r)
            return
        k = self.grid._corner_r(hs)
        k = min(k + _ui_draw.mark_reach("card") + 1 if k else 0, hs // 2)
        fill(cv, (x0, y0, r[2], hy - y0))
        fill(cv, (x0, hy + hs, r[2], y1 - hy - hs))
        fill(cv, (x0, hy, hx - x0, hs))
        fill(cv, (hx + hs, hy, x1 - hx - hs, hs))
        if k:
            fill(cv, (hx, hy, hs, k))
            fill(cv, (hx, hy + hs - k, hs, k))

    def _content_alone(self, dt):
        """Whether this frame's pixels are the home's alone: on top, nothing
        animating, nothing drawn over it (`content_alone`)."""
        ws = self.ws
        wm = ws.wm
        if ws._animating(dt):
            return False
        top = getattr(wm, "top_kind", None)
        if top is not None and top() != self.id:
            return False
        alone = getattr(wm, "content_alone", None)
        return alone is not None and alone()

    def _note_still(self, cv, parts, bar=None):
        """Name this painted frame's parts in the band host's paint
        (`ui.held`, the paint the band's pass began this frame): the
        backdrop, the selection, the shelf, the bar's key (`shown_gen`) its
        strip was drawn under and the hint strip's. Answers whether every
        buffer now holds every part named (`ui.held_all`)."""
        if bar is None:
            bar = self.ws.bar_layer.shown_gen("home")
        self.grid.resume_band_host(cv)
        _ui.held("still", self._stk.of(parts[0]))
        _ui.held("sel", self._slk.of(parts[1]))
        _ui.held("shelf", self._shk.of(parts[2]))
        _ui.held("bar", bar)
        now = self._outside_key()
        if now is not None:
            _ui.held("hint", self._hk.of(now[0]))
        return _ui.held_all()

    def _try_still_frame(self, cv, dt):
        """The desk home's frame over its still backdrop: the parts this
        frame's buffer lacks (`ui.held` in the band host's paint) put back
        over the wallpaper's own pixels, and the bar. False when the frame
        must take another path."""
        ws = self.ws
        if (self._lib_unsupported or self._back_tip
                or self._search_line() is not None):
            return False
        parts = self._still_parts(cv, self._bd_now)
        if parts is None or not self._content_alone(dt):
            return False
        bar = ws.bar_layer.shown_gen("home")
        now = self._outside_key()
        kb = self._stk.of(parts[0])
        ks = self._slk.of(parts[1])
        kd = self._shk.of(parts[2])
        kr = bar
        kh = self._hk.of(now[0])
        g = self.grid
        g.begin_band_host(cv)
        if not _ui.held("still", kb):
            return False                 # (the buffer holds another backdrop)
        # what the buffer this frame paints lacks, and what the buffer the
        # last frame painted holds as this frame would draw it (§14.6),
        # asked before anything draws through another host
        sel_lag = not _ui.held("sel", ks)
        band_lag = not _ui.held("shelf", kd)
        bar_lag = not _ui.held("bar", kr)
        hint_lag = not _ui.held("hint", kh)
        last = _ui.held_last("still", kb)
        rows = last and _ui.held_last("bar", kr)
        sel_last = last and _ui.held_last("sel", ks)
        band_last = last and _ui.held_last("shelf", kd)
        hint_last = last and _ui.held_last("hint", kh)
        wm = ws.wm
        # A part the last frame painted as this one would is copied out of
        # its buffer (§14.6): whole rows where the bar there is this one's.
        lay_sel = sel_lag and not (sel_last and self._copy_part(cv, self._sel_rect(), rows))
        lay_band = band_lag and not (band_last and self._copy_part(cv, g.band_rect(), rows))
        if bar_lag and rows and not (lay_sel or lay_band):
            for r in ws.wallpaper.bars():
                if not self._copy_part(cv, r, True):
                    break
            else:
                bar_lag = False
        laid = self._lay_parts(cv, dt, lay_sel, lay_band, sel_lag)
        h = g.foot()[5]
        # the hint strip, where the buffer shows another selection's verbs:
        # copied where the last frame drew these, else drawn
        if (h[2] > 0 and h[3] > 0 and (hint_lag or _overlaps_any(laid, (h,)))
                and not (hint_last and not _overlaps_any(laid, (h,))
                         and self._copy_part(cv, h, rows))):
            laid.append(h)
            ws.wallpaper.restorer(cv, self._bd_now)(cv, h)
            self._draw_home_chrome(cv)
            wm.hand_content_damage(cv, h[0], h[1], h[2], h[3])
        if not lay_band:
            self._band_note(cv, dt)      # (the buffer holds the band as it is)
        # A strip this buffer holds under this key stays, unless a part
        # above was put back over it.
        if bar_lag or _overlaps_any(laid, ws.wallpaper.bars()):
            ws.bar_layer._draw_status_strip("home")
        if self._note_still(cv, parts, bar):
            # every buffer holds this frame: the retained frame takes it once
            # nothing waits on the board (`capture_due`)
            key = self._retained_key(cv)
            if key != self._lib_key:
                self._cap_owed = (ws._frames_drawn, key, parts)
        else:
            self._still_debt()
        return True

    def debt_due(self):
        """Ask, on an idle frame, for the frame a step owed (`_band_settle`)
        where no frame has painted since it and nothing moves."""
        owed = self._debt_owed
        self._debt_owed = None
        ws = self.ws
        if owed is not None and owed + 1 == ws._frames_drawn and not self.grid.moving:
            ws.mark_dirty()

    def capture_due(self):
        """Take the retained frame a still frame owed (`_cap_owed`) on an
        idle frame: every buffer held that frame when it was owed and still
        does while no frame has painted since and its key holds. Dropped
        otherwise -- a painted frame never pays it, so a run of taps does
        not; the next still frame that finds every buffer holding it owes
        it again."""
        owed = self._cap_owed
        self._cap_owed = None
        ws = self.ws
        if owed is None or ws._frames_drawn != owed[0] + 1:
            return
        cv = ws.sys_canvas
        if self._retained_key(cv) == owed[1]:
            self._capture_retained(cv, 1 / 60, owed[2])

    def _copy_part(self, cv, r, rows):
        """Part rect `r` copied out of the buffer the last frame drew into,
        which holds it as this frame would draw it (§14.6) -- its whole rows
        with `rows`: what else they hold, the backdrop and the bar, is this
        frame's there too. False where the canvas cannot copy."""
        if rows:
            r = (0, r[1], cv.w, r[3])
        if not hasattr(cv, "copy_last") or not cv.copy_last(r[0], r[1], r[2], r[3]):
            return False
        self.ws.wm.hand_content_damage(cv, r[0], r[1], r[2], r[3])
        return True

    def _lay_parts(self, cv, dt, sel_lag, band_lag, marks):
        """Put the lagging parts back over the backdrop and draw them; the
        rects laid. `marks`: the buffer may hold the focus mark on another
        card."""
        ws = self.ws
        fill = ws.wallpaper.restorer(cv, self._bd_now)
        g = self.grid
        wm = ws.wm
        laid = []
        if sel_lag:
            r = self._sel_rect()
            laid.append(r)
            self._fill_around_hero(cv, r, fill)
            cv.clip(r[0], r[1], r[2], r[3])
            g._paint_details(cv)
            self._draw_details(cv)
            cv.clip()
            wm.hand_content_damage(cv, r[0], r[1], r[2], r[3])
        if band_lag:
            g.repaint_band(cv, ws.covers.sheet_icon, fill, _band_key(self, cv, dt, g, True),
                           _cursor_stamp(ws), None, False, marks, True)
            bx, by, bw, bh = g.band_rect()
            laid.append((bx, by, bw, bh))
            wm.hand_content_damage(cv, bx, by, bw, bh)
        return laid

    def _still_debt(self):
        """Ask for the frame that brings one more buffer to this frame,
        while nothing moves and no press waits on it (as the windowed WM
        pays its own, `debt_owed`)."""
        ws = self.ws
        if (not self.grid.moving and not ws._ptr_changed()
                and not getattr(ws.pointer, "down", False)):
            ws.request_frame()

    def draw(self, dt):
        """The home desktop: wallpaper backdrop -> cart icon grid -> top status
        strip. The wallpaper is drawn first and the rest layer over it, exactly the
        Picotron model (wallpaper shows through the chrome). All on the SYSTEM canvas,
        reflowed to its size + font scale (#39).

        There is no bottom tool dock: the launcher dropped it in #46 (its
        code/draw/map/run slots had no cart to act on, so it was a dead row) and it
        was deleted outright on 2026-08-21 -- see bar_layer.py. Settings is reached
        via the gear in the status strip; the cart grid keeps the freed bottom band
        (Layout.grid_bottom)."""
        ws = self.ws
        cv = ws.sys_canvas
        if ws.menu_ui.put_home(cv, dt):
            # the system menu's frame of the home, washed: the buffers hold
            # the scrim, no frame of the home's own (no band paint names
            # the home's parts in it)
            return
        # The board's motion verdict (a WM constant, §12's degradation order).
        wm = ws.wm
        carousel = self._carousel()
        if carousel:
            self.grid.motion = getattr(wm, "HOME_MOTION", "full")
            self.grid.scale = getattr(wm, "HOME_SCALE", HOME_SCALE_HH)
            self.grid.dim = _dim_of(ws)
        self.grid.wall_ink = self._wall()
        self.grid.ground = _ground_of(ws)
        # Kinetic fling (#113) and the glide to the centre card: advance the
        # row BEFORE painting, and keep the redraw gate open until it rests.
        # Both ride the partial path (its gate includes flinging).
        self.grid.anim_frame(dt)
        if self.grid.flinging:
            ws.request_frame()
        self._bd_now = ws.wallpaper.restore_key(True)
        if carousel:
            self.grid.restore_whole = True
        else:
            self._face_key(self._bd_now)
        if carousel and self._try_still_frame(cv, dt):
            return
        if self._try_band_frame(cv, dt):    # the band's frame (#58/#66): it names
            return                          # the band and the hint strip alone
        # Retained-frame stamp (2026-07-27): a re-entry whose pixels are provably
        # identical to the captured frame presents it in one blit. Bookkeeping
        # matches a full paint exactly -- pixel-wise it IS one -- so the
        # band's record stays honest.
        # (under the menu's hold the retained frame is the one before it)
        if not ws.menu_ui.holds_home() and self._try_stamp_retained(cv, dt):
            if self._band_note(cv, dt) == -1 and self._bd_now is not None:
                self._still_debt()
            parts = self._still_now(cv, dt)
            if parts is not None and not self._note_still(cv, parts):
                self._still_debt()
            return
        # perf_capture (#66 instrument-before-cutting): time the home frame's
        # three sections so a slow launcher frame names its eater (PERF's
        # home= field carries ws._pf_home -- wallpaper vs grid vs bar).
        _t0 = _ticks_ms() if getattr(ws, "perf_capture", False) else None
        ws.wallpaper.draw(dt, home=True, quiet=self._quiet(ws))
        self._capture_band_backdrop(cv, dt)
        _t1 = _ticks_ms() if _t0 is not None else None
        # The hint strip and the cells' band (docs/theme_and_shell_2026-11.md
        # §8.10) over the wallpaper; the cards draw inside the band.
        self._draw_home_chrome(cv)
        if not carousel:                      # (a carousel's cards draw: no faces)
            self._face_key(ws.wallpaper.restore_key(True))
        # (the band's kept table records this paint: the cursor's stamp about
        # to land on top in it, and the parts beside the band named)
        end = self.grid.draw(cv, ws.covers.sheet_icon, _band_key(self, cv, dt, self.grid, True),
                             _cursor_stamp(ws), self._name_beside)
        self._band_landed(cv, dt)
        if carousel and self.grid.desk():
            self._draw_details(cv)
        _t2 = _ticks_ms() if _t0 is not None else None
        ws.bar_layer._draw_status_strip("home")
        if self._back_tip:
            self._draw_back_tip(cv)
        if _t0 is not None:
            _t3 = _ticks_ms()
            ws._pf_home = (_ticks_diff(_t1, _t0), _ticks_diff(_t2, _t1),
                           _ticks_diff(_t3, _t2))
        if end == -1 and self._bd_now is not None:
            self._still_debt()       # every buffer to this frame: the next step is a band frame
        parts = self._still_now(cv, dt)
        owed = parts is not None and not self._note_still(cv, parts)
        if not ws.menu_ui.holds_home():
            # (under the menu the retained frame stays the one its close stamps)
            self._capture_retained(cv, dt, parts)   # retain this frame for re-entry stamps
        if owed:
            self._still_debt()

    def _still_now(self, cv, dt):
        """This whole frame's parts where it is the home's alone, else None."""
        if not self._carousel() or not self._content_alone(dt):
            return None
        return self._still_parts(cv, self.ws.wallpaper.restore_key(True))

    def _hover_back(self, px, py, click, down):
        """Whether a mouse rests on the bar's chevron back to the desk (the
        home in console mode over a desk, `BarLayer.back_rect`): a pointer
        that moves with nothing held, never a touch, which has no hover."""
        if click or down or _ui_draw.input_kind() == "touch":
            return False
        r = self.ws.bar_layer.back_rect("home")
        return r is not None and _in(px, py, r)

    def _draw_back_tip(self, cv):
        """The chevron's tooltip, `BACK_TIP` on a plate under it: where it
        goes, which the bare chevron does not say (owner, 2026-10-19)."""
        ws = self.ws
        r = ws.bar_layer.back_rect("home")
        if r is None:
            return
        th = ws.theme_colors
        fs = ws.layout.fs
        u = _lb.unit()
        pad = 6 * u
        tw = _ui_draw.print_width(BACK_TIP, fs)
        h = ws.layout.line_h + pad
        x, y = r[0], ws.layout.cy + 4 * u      # under the bar
        cv.rrect(x, y, tw + 2 * pad, h, 4 * u, th["ink"])
        cv.print(BACK_TIP, x + pad, y + (h - ws.layout.line_h) // 2, th["surface"], 1)

    def tab_rects(self):
        """[(tab id, rect, labels_on)] of the home's tabs in the bar's zone
        (`zone_tabs`), on the glass."""
        got = _shell_layout.cell_of(self.ws.layout.bars, "zone")
        if got is None:
            return []
        rx, ry = self.ws.layout.bars[got[0]][1][:2]
        zx, zy, zw, zh = got[1]
        return [(tid, (rx + r[0], ry + r[1], r[2], r[3]), True)
                for tid, _l, r in self.zone_tabs((zx, zy, zw, zh))]

    def _draw_home_chrome(self, cv):
        """The hint strip along the home's foot (its tabs are the bar's)."""
        self._draw_hints(cv)

    def _search_open(self):
        """Whether the home's search is open: a query held or being typed."""
        ws = self.ws
        return bool(self.searchable and (getattr(ws, "search_query", "")
                                         or getattr(ws, "search_typing", False)))

    def _search_line(self):
        """(x, y, w, h) of the handheld home's search line while search is
        open: the hint strip, or under touch, which has none, a line on the
        home's ground 4 units over the glass's foot (design review R5-04);
        None while search is closed."""
        if not self._search_open():
            return None
        rect = self.grid.foot()[5]
        if rect[3] > 0:
            return rect
        lay = self.ws.layout
        h = lay.line_h
        return (rect[0], lay.cy + lay.ch - 4 * lay.fs - h, rect[2], h)

    def card_verbs(self):
        """The selected card's verbs beyond its tap, the ONE list every
        input reaches them by: the system menu's card group, a long press
        on the card, a gamepad's X, the desk's buttons. Its favourite star,
        then CHANGE with COPY beside it, then DEL, where
        the active tab has them (`zone_verbs`)."""
        zone = self.zone_verbs()
        out = []
        if self.favourable() is not None:
            out.append("favourite")
        if "change" in zone:
            out.append("change")
            out.append("copy")
        for v in zone:
            if v == "delete":
                out.append(v)
        return out

    def _desk_verbs(self):
        """The desk home's buttons (step 18): the active tab's tap verb,
        then the card's verbs but its star (the hero wears that). On Make
        a real card's are its own, CHANGE first: NEW makes another cart,
        which the shelf's + tile and N keep. None off the desk."""
        if not (self._carousel() and self.grid.desk()):
            return ()
        zone = self.zone_verbs()
        out = [] if "change" in zone and self.grid.tab == "make" else \
            [v for v in zone[:1] if v in ("play", "open", "new")]
        out.extend(v for v in self.card_verbs() if v != "favourite")
        return out

    def _desk_button_top(self):
        """The y of the details pane's first row of buttons."""
        dk = self.grid._geom()[9]
        h, block, u = dk[5][4], dk[5][5], dk[5][6]
        return dk[6] + block - 6 // u - h - dk[8]

    def _desk_buttons(self):
        """[(verb, rect, kind)] of the details pane's buttons over the
        pane's foot (`desk_button_layout`): rows from the first the pane
        keeps (`_desk_button_top`), every one of the card's verbs."""
        verbs = self._desk_verbs()
        if not verbs:
            return []                        # off the desk
        dk = self.grid._geom()[9]
        dx, dw = dk[1], dk[2]
        h, u = dk[5][4], dk[5][6]
        y = self._desk_button_top()
        return [(v, (dx + x, y + r * (h + DESK_ROW_GAP // u) + dy, w, h), kind)
                for v, x, r, w, kind, dy in desk_button_layout(verbs, dw,
                                                               self.ws.layout.fs, u)]

    def _details_tap(self, px, py):
        """A press on the desk home's hero (the active tab's tap) or one of
        its buttons."""
        g = self.grid
        if not g.items:
            return False
        if _in(px, py, g.hero_rect()):
            self._tab_focus = False
            self.ws.defer(self._launch)          # #184: start behind the paint
            return True
        for v, r, _k in self._desk_buttons():
            if _in(px, py, r):
                self._do_verb(v)
                return True
        return False

    def _quiet(self, ws):
        """The home's quiet rectangles for its wallpaper: the bars and the
        arrangement's own (`quiet`); kept while both are the same objects,
        so a live wallpaper's frame builds nothing."""
        bars = ws.wallpaper.bars()
        own = self.grid.quiet()
        hit = getattr(self, "_quiet_key", None)
        if hit is not None and hit[0] is bars and hit[1] is own:
            return hit[2]
        q = bars + own
        self._quiet_key = (bars, own, q)
        return q

    def _draw_details(self, cv):
        """The desk home's details pane beside the hero (step 18): the
        selected card's title in the `title` face, its metadata, its facts
        (how often and when it was played, the play record's; who made it,
        its id's), then its buttons. On the home's ground under a theme's
        depth, on a plate in the bar's colours without one."""
        ws = self.ws
        g = self.grid
        if not g.items:
            return
        it = g.items[g.nearest()]
        th = ws.theme_colors
        dk = g._geom()[9]
        dx, dw, hy = dk[1], dk[2], dk[6]
        ts, ms, ks, vs, bh, block, u = dk[5]
        ink, dim = th["ink"], th["ink_dim"]
        name = it.get("title") or ""
        if it.get("type") == NEW_TILE_TYPE:
            name = "New"
        ty = hy + 4 // u
        was = _ui_draw.print_font(_ui_draw.font("title")[0])
        cv.print_scaled(_ui_draw.print_ellipsis(name, dw, ts), dx, ty, ink, ts)
        if was is not None:
            _ui_draw.print_font(was)
        lh = _ui_draw.font()[2]
        facts_on, meta_on = dk[7]
        my = ty + _ui_draw.font("title")[2] * ts + 14 // u
        sep = 8 // u
        x = dx
        for k, t in enumerate(g._meta(it, played=False) if meta_on else ()):
            if k:                                            # the facts' Last
                if x + ms + sep >= dx + dw:
                    break
                cv.rect(x, _dot_top(my, ms, ms), ms, ms, dim)
                x += ms + sep
            cv.print_scaled(_ui_draw.print_fit(t, dx + dw - x, ms), x, my, dim, ms)
            x += _ui_draw.print_width(t, ms) + sep
        if not meta_on:
            my -= lh * ms + 14 // u
        buttons = self._desk_buttons()
        by = self._desk_button_top()
        if it.get("type") not in PSEUDO_TILE_TYPES:
            vy = by - 16 // u - lh * vs
            ky = vy - 6 // u - lh * ks
            if not facts_on:
                ky = by - 6 // u
            # the manifest's description (SPEC.md 3.7) in the room between
            # the metadata and the facts: a measure of DESC_EMS, DESC_LINES
            # lines at most, the last cut with an ellipsis (owner, 2026-10-19)
            gap = 10 // u
            room = min(DESC_LINES, (ky - gap - (my + lh * ms + gap)) // (lh * ms))
            text = it.get("description")
            if text and room > 0:
                tw = min(dw, _ui_draw.print_width("n" * DESC_EMS, ms))
                lines = list(_ui_draw.wrap_px(text, tw, ms))
                if len(lines) > room:
                    lines = lines[:room]
                    lines[-1] = _ui_draw.print_fit(lines[-1], tw - _ui_draw.print_width("...", ms), ms) + "..."
                dy = my + lh * ms + gap
                for line in lines:
                    cv.print_scaled(line, dx, dy, dim, ms)
                    dy += lh * ms
            # the facts' columns stand on the buttons' left edges under them
            # (design review R4-31); a fact past the last button a column on
            col = 150 // u
            facts = self._facts(it) if facts_on else ()
            xs = [r[0] for _v, r, _k in buttons if r[1] == by][:len(facts)]
            while len(xs) < len(facts):
                xs.append(xs[-1] + col if xs else dx)
            for k, (key, val) in enumerate(facts):
                fx = xs[k]
                w = (xs[k + 1] - fx if k + 1 < len(xs) else col) - 10 // u
                w = min(w, dx + dw - fx)
                if w <= 0:
                    break                    # a column past the pane's edge
                cv.print_scaled(key, fx, ky, dim, ks)
                cv.print_scaled(_ui_draw.print_fit(val, w, vs), fx, vy, ink, vs)
        on = self._button_focus
        for k, (v, r, kind) in enumerate(buttons):
            _ui_draw.button(cv, th, r, _VERB_HINT[v][1], kind=kind, app="home",
                            glyph="play" if v in ("play", "open") else None,
                            glyph_draw=self._play_glyph, focus=k == on)

    def _play_glyph(self, kind, rect, c, cv):
        """A details-pane button's glyph: the theme's icon set's (the dock's,
        Files' and the Editor's Play), else the toolkit's own raster."""
        if not self.ws.draw_icon(kind, rect, c, cv):
            _ui_draw.glyph(cv, "run" if kind == "play" else kind, rect, c)

    def _facts(self, it):
        """The details pane's facts of a cart: (caption, value) pairs."""
        g = self.grid
        rec = g.played(it)
        out = [("Played", times_words(rec[0] if rec is not None else 0))]
        if rec is not None and rec[1]:
            out.append(("Last", day_words(rec[1], g.today())))
        cid = it.get("id") or ""
        if g.own_for is not None and g.own_for(it):
            by = "You"
        elif "." in cid:
            by = cid.split(".", 1)[0]
            by = "Moybyte" if by == "moybyte" else by
        else:
            by = None
        if by:
            out.append(("By", by))
        return out

    def _hint_groups(self):
        """The hint strip's two groups: the active tab's verbs on the
        selected card (on the desk, its buttons' and SEARCH), then the tab
        keys; and the verb of each pair. A keyboard types each verb's
        letter, the star last, its words following its state; a gamepad
        shows the verb its confirm does (`launch_selected`), on that key,
        and X for the card's other verbs (`card_verbs`). With the keys on a
        desk button, confirm names that button's verb."""
        items = []
        verbs = []
        zone = self.zone_verbs()
        on = self._button_focus
        if on is not None:
            # the keys on the desk's buttons: confirm presses the one they are on
            buttons = self._desk_buttons()
            on = buttons[min(on, len(buttons) - 1)][0] if buttons else None
        if _ui_draw.hint_kind() not in _LETTER_INPUTS:
            tap = on or self._confirm_verb(zone)
            if tap:
                items.append("confirm")
                items.append(_VERB_HINT[tap][1])
                verbs.append(tap)
            if self.card_verbs():
                items.extend(_VERB_HINT["options"])
                verbs.append("options")
            return items, verbs
        desk = tuple(self._desk_verbs())
        if desk and "new" in zone and "new" not in desk:
            desk = ("new",) + desk           # Make's N, off the card's buttons
        for v in (desk + tuple(v for v in zone if v == "search")) if desk else zone:
            k, words = _VERB_HINT[v]
            if on is not None and (v == on or k == "confirm"):
                if v != on:
                    continue                 # the shelf's tap: no key on the buttons
                k = "confirm"
            items.append(k)
            items.append(words)
            verbs.append(v)
        it = self.favourable()
        if it is not None:
            # the star last, where the strip has room for it (`_hint_spans`)
            items.append(_VERB_HINT["favourite"][0])
            items.append(_UNFAVOURITE if self.ws.carts.is_favorite(it)
                         else _VERB_HINT["favourite"][1])
            verbs.append("favourite")
        return items, verbs

    def _confirm_verb(self, zone):
        """Which of the verbs `zone` the selected card's confirm does: the
        "+ New" card's NEW, a Make card's CHANGE (it opens in the Editor),
        else PLAY or OPEN; "" for none."""
        if self.grid.tab == "make" and "change" in zone:
            return "change"
        return zone[0] if zone[:1] in (("play",), ("open",), ("new",)) else ""

    def _hint_spans(self, cv):
        """[(verb or "tabs", x0, x1)] across the hint strip, and the strip's
        rect: what `_draw_hints` draws and a tap hits."""
        out, rect, _items = self._hint_fit(cv)
        return out, rect

    def _hint_fit(self, cv):
        """(spans, rect, items) of the hint strip: the spans `_hint_spans`
        answers and the verbs' keycap items they hold. Where the verbs run
        into the tab keys the star goes first, then Duplicate (it stays on
        X and in the card's menu); the tab keys go last (design review R4-22)."""
        th = self.ws.theme_colors
        rect = self.grid.foot()[5]
        desk = self.grid.desk()
        items, verbs = self._hint_groups()
        items, verbs = list(items), list(verbs)
        if self.searchable and (getattr(self.ws, "search_query", "")
                                or getattr(self.ws, "search_typing", False)):
            items, verbs = [], []
        inner = self._hint_inner(rect)
        sp = _ui_draw.hints(cv, th, inner, _TAB_KEYS, desk, draw=False)
        dx = rect[0] + rect[2] - self._hint_pad() - sp[0][1] if sp else 0
        while True:
            out = []
            if items:
                for v, (x0, x1) in zip(verbs, _ui_draw.hints(cv, th, inner, items, desk,
                                                              draw=False)):
                    out.append((v, x0, x1))
            if not sp or not out or sp[0][0] + dx > out[-1][2]:
                break
            drop = "favourite" if verbs[-1] == "favourite" else \
                "copy" if "copy" in verbs else None
            if drop is None:
                break
            k = verbs.index(drop)
            del verbs[k]
            del items[2 * k:2 * k + 2]
        if sp and (not out or sp[0][0] + dx > out[-1][2]):
            out.append(("tabs", sp[0][0] + dx, sp[0][1] + dx))
        return out, rect, items

    def _draw_hints(self, cv):
        """The home's hint strip (steps 17 and 18): keycaps naming the
        active tab's verbs on the selected card, from the left, and the tab
        keys at the right (the theme's `keycap`, `ui.hints`'s part). While
        a search query is held it shows in the verbs' place; while the
        system menu or the delete confirm is up, their keys (`modal_keys`)."""
        th = self.ws.theme_colors
        wall = self._wall()
        if wall is not None:
            th = self._wall_theme(th, wall[0])
        keys = modal_keys(self.ws)
        if keys is None and self._tab_keys:
            keys = _TAB_ROW_KEYS
        if keys is not None:
            # the system menu's card or the delete confirm is up over the
            # home: the keys it takes, never the home's verbs
            rect = self.grid.foot()[5]
            _ui_draw.hints(cv, th, self._hint_inner(rect), keys, self.grid.desk())
            return
        spans, rect, items = self._hint_fit(cv)
        n = sum(1 for v, _a, _b in spans if v != "tabs")
        query = getattr(self.ws, "search_query", "")
        typing = getattr(self.ws, "search_typing", False)
        if self.searchable and (query or typing):
            n = 0
        desk = self.grid.desk()
        if n:
            _ui_draw.hints(cv, th, self._hint_inner(rect), items[:2 * n], desk)
        elif self.searchable and (query or typing):
            self._draw_search(cv, th, th["ink"])
        for v, x0, _x1 in spans:
            if v == "tabs":
                left = x0 - (rect[0] + self._hint_lead())
                _ui_draw.hints(cv, th, (rect[0] + left, rect[1], rect[2] - left, rect[3]),
                               _TAB_KEYS, desk)

    def _wall_theme(self, th, white):
        """`th` with its hint labels' roles white, for the strip over a
        wallpaper: one dict per theme load, so the toolkit's look ring keeps
        its slot."""
        k = getattr(self, "_wall_th", None)
        if k is not None and k[0] is th and k[1] == white:
            return k[2]
        wt = dict(th)
        wt["chrome_ink_dim"] = wt["ink_dim"] = white
        self._wall_th = (th, white, wt)
        return wt

    def _hint_lead(self):
        """The margin `ui.hints` leads with here, at the font scale."""
        return (_HINT_LEAD_DESK if self.grid.desk() else _HINT_LEAD) * self.ws.layout.fs

    def _hint_pad(self):
        """The hint strip's margin at either end: the handheld's is the one
        `ui.hints` leads with, the top bar's, the desk's wider."""
        if self.grid.desk():
            return max(24 // _lb.render_scale(), self._hint_lead())
        return self._hint_lead()

    def _hint_inner(self, rect):
        """The strip `ui.hints` draws in for the margin `_hint_pad`."""
        e = self._hint_pad() - self._hint_lead()
        return (rect[0] + e, rect[1], rect[2] - 2 * e, rect[3])

    def _hint_tap(self, px, py):
        """A tap on the hint strip: its verb, as the bar's chip was."""
        spans, rect = self._hint_spans(self.ws.sys_canvas)
        if not _in(px, py, rect):
            return False
        for v, x0, x1 in spans:
            if x0 <= px < x1:
                if v == "tabs":
                    self.ws.step_home_tab(1, self.grid)
                else:
                    self._do_verb(v)
                return True
        return True

    def _hint_keys(self, i):
        """The hint strip's keys, typed (an edge: a held key acts once):
        C, /, N, Y, X and P the verbs it names, Q and E the tabs, F the
        selected card's favourite star."""
        k = self.ws.input.last_key
        prev = self._kprev
        self._kprev = k
        if not k or k == prev or not (32 < k < 127):
            return False
        ch = chr(k).lower()
        if ch in ("q", "e"):
            self.ws.step_home_tab(-1 if ch == "q" else 1, self.grid)
            if self._tab_keys:
                self._tab_at = self.grid.tab
                self._zone_bump += 1
            return True
        if ch == _FAVOURITE_KEY:
            return self.toggle_favourite()
        v = _VERB_KEY.get(ch)
        if v is None or not (v in self.zone_verbs() or v in self.card_verbs()
                             or (v == "window" and v in self._desk_verbs())):
            return False
        self._do_verb(v)
        return True

    def handle_input(self, i):
        ws = self.ws
        if getattr(ws, "search_typing", False):
            return self._handle_search_typing(i)
        ws.ach_ui.konami_watch(i)
        if self._tab_focus:
            # The tab row (up from the carousel): left and right move the
            # focus along it (`_tab_step`), confirm opens the tab it is on
            # and takes the keys to its cards, down returns to them.
            if i.steps("left"):
                self._tab_step(-1)
            elif i.steps("right"):
                self._tab_step(1)
            if i.pressed("a") or i.pressed("run"):
                at = self.focused_tab()
                self._tab_focus = False
                if at != self.grid.tab:
                    ws.set_home_tab(at, self.grid)
                ws.mark_dirty()
            elif i.steps("down"):
                self._tab_focus = False
                ws.mark_dirty()
            if i.pressed("b") and ws.ach_ui._konami_pos == 0:
                self._tab_focus = False
                ws.mark_dirty()
            elif i.pressed("home"):
                ws.home_key()
            return True
        if self._button_keys(i):
            return True
        if self._hint_keys(i):
            return True
        # The carousel (§12): left/right glide by a card, a held key one
        # motion; up takes the keys to the tab row (on the desk, to the
        # details pane's buttons first). The grid: the arrows step its
        # cells, and up from its top row takes them to the tabs.
        grid = self.grid
        wy, wx = getattr(i, "wheel_y", 0), getattr(i, "wheel_x", 0)
        if wy or wx:
            # The wheel: the carousel glides a card a notch, the grid steps a
            # row, Shift's wheel a column.
            if getattr(i, "mods", 0) & _SHIFT:
                wx, wy = wx + wy, 0
            if self._carousel():
                grid.nav2d(wx + wy, 0)
            else:
                grid.nav2d(wx, wy)
            ws.mark_dirty()
        # a step is a cell (a card on the carousel): a frame's steps, a held
        # key's repeats and a roll's, move as many
        dx = i.steps("right") - i.steps("left")
        if dx:
            grid.nav2d(dx, 0)
            ws.mark_dirty()
        up = i.steps("up")
        if up:
            if self._desk_buttons():
                self._button_focus = 0
            elif self._carousel() or self._grid_top_row():
                self._tab_focus = True
            else:
                grid.nav2d(0, -up)
            ws.mark_dirty()
        down = i.steps("down")
        if down and not self._carousel():
            grid.nav2d(0, down)
            ws.mark_dirty()
        if i.pressed("a") or i.pressed("run"):
            # #184: DEFERRED -- the cart start runs behind the next painted
            # frame (LOADING toast), never inside this input dispatch.
            ws.defer(self._launch)           # the active tab's tap
        if i.pressed("x"):
            self.open_card_menu()            # a gamepad's X: the card's verbs
        if i.pressed("b") and ws.ach_ui._konami_pos == 0:
            self._back()                     # (a B inside the Konami code is the code's)
        elif i.pressed("home"):
            ws.home_key()
        return True

    def favourable(self):
        """The selected card where it takes the favourite star (a cart of
        the store's on a home that keeps favourites), else None."""
        grid = self.grid
        it = grid.selected()
        if grid.favorite_for is None or it is None or not it.get("path") \
                or it.get("type") in PSEUDO_TILE_TYPES:
            return None
        return it

    def toggle_favourite(self):
        """Star or unstar the selected card (the badge's tap, F, the card
        menu's row); False where it takes no star."""
        it = self.favourable()
        if it is None:
            return False
        self.ws.carts.toggle_favorite(it)
        return True

    def _back(self):
        """B: closes a held search, cleared; else the home's X, from a
        keyboard -- where the X shows."""
        if self._search_open():
            self.ws.close_search(clear=True)
        elif self.ws.bar_layer._show_x(self.strip):
            self.ws.open_desk()

    # The doors out of the home.
    def _launch(self):
        self.ws.launch_selected(self.grid)

    def _change(self):
        self.ws.change_selected(self.grid)

    def _new(self):
        self.ws.new_cart_and_edit(self.grid)

    def _grid_top_row(self):
        grid = self.grid
        if not grid.items:
            return True
        return grid.cell_of(grid.sel)[0] == 0

    def _grid_pointer(self, px, py, click):
        """The grid and icons arrangements' pointer: a card fires the active
        tab's tap on a clean RELEASE (ui.DragTap), so a drag never launches;
        a mouse's cursor selects the card it moves onto."""
        ws = self.ws
        grid = self.grid
        down = ws.pointer.down
        i = grid.pointer_frame(px, py, click, down, dt_ms=ws._pointer_dt_ms)
        if grid.moving:
            ws.mark_dirty()
        if i is not None:
            grid.sel = i
            ws.defer(self._launch)           # #184: start behind the paint
            return True
        if click or down:
            self._lhover = (px, py)
            return True
        if self._lhover == (-1, -1):
            self._lhover = (px, py)
        elif (px, py) != self._lhover:
            self._lhover = (px, py)
            i = grid.tile_at(px, py)
            if i is not None and i != grid.sel:
                grid.sel = i
                # A selection move MARKS DIRTY (#177): in a window the content
                # freeze reuses the retained buffer on position-only frames.
                ws._dirty = True
        return True

    def handle_pointer(self, px, py, click):
        # The home (§12): the bar and the tab row fire on the press edge; the
        # carousel rides ui.DragTap, so a tap fires on a clean RELEASE and a
        # drag can never launch a cart. A tap on the centre card is the
        # active tab's tap; on any other card it glides that card to the
        # centre. The favorite star on the centre card fires on the press.
        ws = self.ws
        down = ws.pointer.down
        grid = self.grid
        tip = self._hover_back(px, py, click, down)
        if tip != self._back_tip:
            self._back_tip = tip
            ws.mark_dirty()
        if click:
            self._hold = None
            if self._button_focus is not None:
                self._button_focus = None    # a press takes the keys' mark away
            if ws.bar_layer.handle_bar_tap(self.strip, px, py):
                return True
            if self._search_tap(px, py) or self._hint_tap(px, py):
                return True
            for tid, r, _on in self.tab_rects():
                if _in(px, py, r):
                    self._tab_focus = False
                    ws.set_home_tab(tid, grid)
                    return True
            frect = grid.favorite_rect(grid.sel)
            if frect is not None and _in(px, py, frect):
                self.toggle_favourite()
                return True
            if self._carousel() and grid.desk() and self._details_tap(px, py):
                return True
        if self._long_press(px, py, click, down):
            return True
        if not self._carousel():
            return self._grid_pointer(px, py, click)
        i = grid.pointer_frame(px, py, click, down, dt_ms=ws._pointer_dt_ms)
        if grid.moving:
            ws.mark_dirty()
        if i is not None:
            self._tab_focus = False
            if i == grid.sel and not grid.moving:
                # #184: the tap frame paints first; the start runs behind it.
                ws.defer(self._launch)
            else:
                grid.glide_to(i)
                ws.mark_dirty()
            return True
        if click or down:
            self._lhover = (px, py)
        return True

    def _long_press(self, px, py, click, down):
        """A press held `LONG_PRESS_MS` on a card without travel selects
        that card and opens its verbs (`open_card_menu`), the phone home
        screens' long press; the gesture ends there, so its release is no
        tap. True the frame it opens."""
        grid = self.grid
        if click:
            i = grid.tile_at(px, py)
            self._hold = None if i is None else [i, px, py, 0.0]
            return False
        h = self._hold
        if h is None:
            return False
        slop = 4 * self.ws.layout.fs + 2              # the DragTap's own
        if not down or grid.dragging or abs(px - h[1]) > slop or abs(py - h[2]) > slop:
            self._hold = None
            return False
        h[3] += self.ws._frame_dt_ms
        if h[3] < LONG_PRESS_MS:
            return False
        self._hold = None
        grid.pointer_frame(px, py, False, False)      # the gesture ends untapped
        if h[0] != grid.sel and 0 <= h[0] < len(grid.items):
            grid.sel = h[0]
        self._tab_focus = False
        self.open_card_menu((h[1], h[2]))
        self.ws.mark_dirty()
        return True

    def open_card_menu(self, at=None):
        """The selected card's verbs (`card_verbs`) as a menu hung at `at`,
        (x, y) on the system canvas -- a long press's point, else the
        card's middle (the desk's hero's): a long press, a gamepad's X, the
        strip's Options. False where the card has none."""
        if not self.card_verbs():
            return False
        if at is None:
            grid = self.grid
            r = grid.hero_rect() if self._carousel() and grid.desk() else None
            r = r or grid.tile_rect(grid.sel)
            if r is None:
                c = self.ws.layout
                at = (c.cx + c.cw // 2, c.cy + c.ch // 2)
            else:
                at = (r[0] + r[2] // 2, r[1] + r[3] // 2)
        return self.ws.menu_ui.open_card(self, at)

    # -- the lent left zone (Stage 4, #46 zoned bar) --------------------------

    @property
    def zone_gen(self):
        """What the zone's pixels follow, as one int for the bar's strip key:
        the open tab and the tab row's focus (`_zone_bump`). The zone draws
        the tabs alone, so a selection moving along the row (a held key's
        glide) leaves the bar's strip as it is."""
        return self._zone_bump * 8 + _TAB_ORDER.get(self.grid.tab, 7)

    def zone_verbs(self):
        """The active tab's verbs on the selected card (§12's table): Games
        PLAY, CHANGE, SEARCH; Mine PLAY, CHANGE; Apps OPEN; Make NEW, CHANGE,
        COPY, DEL. CHANGE, COPY and DEL need a real cart and a store that
        manages projects, and DEL one the kid's (a built-in is copied, never
        deleted: `builtin_name`, the open-cart menu's own test); the "+ New"
        card's verb is NEW. SEARCH is the
        fullscreen home's alone."""
        ws = self.ws
        grid = self.grid
        tab = grid.tab
        sel = grid.selected()
        real = sel is not None and sel.get("path") \
            and sel.get("type") not in PSEUDO_TILE_TYPES
        change = bool(real) and bool(getattr(ws, "can_manage", True))
        if tab == "apps":
            return ("open",) if real else ()
        if tab == "make":
            if not getattr(ws, "can_manage", True):
                return ()                        # a read-only store makes nothing
            if not change:
                return ("new",)
            if _builtin_name(sel["path"]) is not None:
                return ("new", "change", "copy")
            return ("new", "change", "copy", "delete")
        if sel is not None and sel.get("type") == NEW_TILE_TYPE:
            return ("new",)
        out = ("play", "change") if change else (("play",) if real else ())
        if tab == "games" and self.searchable:
            out = out + ("search",)
        return out

    def zone_tabs(self, rect):
        """[(tab id, label, rect)] of the home's tabs in the bar's zone
        `rect` (steps 17 and 18), flush left, as many as fit. A plate may
        reach a chrome unit past the zone above and below, so the label's
        line and a mark under it both fit (B-15)."""
        cs = self.ws.layout.cs
        sc, h, pad, gap = self._zone_tab_metrics()
        h = min(rect[3] + 2 * cs, h)
        y = rect[1] + (rect[3] - h) // 2
        x = rect[0] + 2 * cs
        out = []
        for tid, label in HOME_TABS:
            w = _ui_draw.print_width(label, sc) + 2 * pad
            if x + w > rect[0] + rect[2]:
                break
            out.append((tid, label, (x, y, w, h)))
            x += w + gap
        return out

    def _zone_tab_metrics(self):
        """(label scale, plate height, label pad, gap) of the tabs in the bar:
        the handheld's at the font scale on a plate a unit taller than the
        line either side (the targets'), the desk's a line about 15 glass
        pixels tall on a 28-pixel plate."""
        lay = self.ws.layout
        fs = lay.fs
        if self.grid.desk():
            u = _lb.render_scale()
            sc = text_scale(15 // u, fs)
            return (sc, max(28 // u, _ui_draw.font()[2] * sc + 6 * fs),
                    max(12 // u, 4 * fs), max(6 // u, 2 * fs))
        return fs, lay.line_h + 2 * fs, 6 * fs, 2 * fs

    def _draw_zone_tabs(self, cv, rect):
        """The tabs in the bar (step 17): the active one under the current
        mark, the rest in the dim
        chrome ink; while the keys are the tab row's, the focus pill on the
        one they are on (`focused_tab`), its plate under the label and its
        ring round the plate, so the focused tab and the open one read
        apart."""
        ws = self.ws
        th = ws.theme_colors
        fs = ws.layout.fs
        sc, _h, pad, _gap = self._zone_tab_metrics()
        at = self.focused_tab()
        u = _lb.unit()
        for tid, label, r in self.zone_tabs(rect):
            on = tid == self.grid.tab
            ink = th["chrome_ink_dim"]
            if on:
                got = _ui_draw.current(cv, th, r, r=5 * fs, fs=fs)
                ink = th["chrome_ink"] if got is None else got
            if tid == at:
                _ui_draw.focus(cv, th, r, r=5 * fs, fs=u, pill=True)
                if not on:
                    ink = th["chrome_ink"]
            if sc == fs:
                # the line centred on the plate, as the bar's clock is on the bar,
                # so the two share a baseline
                cv.print(label, r[0] + pad, r[1] + (r[3] - ws.layout.line_h) // 2, ink, 1)
            else:
                lh = _ui_draw.font()[2] * sc
                cv.print_scaled(label, r[0] + pad, r[1] + (r[3] - lh) // 2, ink, sc)
            if tid == at:
                _ui_draw.focus(cv, th, r, r=5 * fs, fs=u, after=True, pill=True)

    def _zone_action_rects(self, rect):
        """{verb: rect} of the hint strip's pairs, where the verbs are
        (`rect`, the zone, unread)."""
        spans, hr = self._hint_spans(self.ws.sys_canvas)
        return {v: (x0, hr[1], x1 - x0, hr[3]) for v, x0, x1 in spans if v != "tabs"}

    def draw_zone(self, cv, rect):
        """The home's lent left zone: the tabs."""
        return self._draw_zone_tabs(cv, rect)

    def zone_tap(self, px, py, rect=None):
        """A tab in the zone, else a verb's hint; anything else falls
        through."""
        ws = self.ws
        for tid, _label, r in self.zone_tabs(
                rect if rect is not None else ws.layout.zone_left):
            if _in(px, py, r):
                self._tab_focus = False
                ws.set_home_tab(tid, self.grid)
                return True
        chips = self._zone_action_rects(
            rect if rect is not None else ws.layout.zone_left)
        for verb, r in chips.items():
            if _in(px, py, r):
                self._do_verb(verb)
                return True
        return False

    def _do_verb(self, verb):
        """One of the active tab's verbs on the selected card (§12's table),
        from a zone chip, a hint or its key."""
        ws = self.ws
        if verb in ("play", "open"):
            ws.defer(self._launch)   # #184: start behind the paint
        elif verb == "change":
            ws.defer(self._change)
        elif verb == "new":
            ws.defer(self._new)
        elif verb == "copy":
            ws.carts.dup(self.grid.selected())
        elif verb == "delete":
            ws.confirm.ask(self.grid.selected())   # never on one press: it asks
        elif verb == "favourite":
            self.toggle_favourite()
        elif verb == "options":
            self.open_card_menu()
        elif verb == "window":
            ws.defer(self._play_window)
        else:
            ws.toggle_search()
        ws.mark_dirty()

    def _play_window(self):
        """Make's Play on the desk: the selected cart run in a desk window
        with its strip, not full screen (`WindowedWM.play_in_window`)."""
        sel = self.grid.selected()
        if sel is None:
            return
        self.ws.open(sel)
        self.ws.wm.play_in_window()


class HomeWindowLayer(LauncherHomeLayer):
    """The "projects" content Layer: the home in a desktop-mode window, the
    desk's PROJECTS (docs/theme_and_shell_2026-11.md §8.10). The same tabs,
    sources, taps and verbs over its own card model, ws.projects_grid, a
    `Carousel` whatever the fullscreen home's arrangement, opened on Make:
    the console home's design (step 18) laid out in the window's content
    rect -- the hero, its details and buttons, the shelf under them, the
    hint strip along the foot and the tabs in the window's band. A narrow
    window stands the hero over its details (`Carousel._geom_desk`).

    A window is a tool space: the panel field under the cards, never the
    wallpaper (the desk shows its own around the window), so nothing in it
    animates and an idle window draws nothing. The WM retains the window's
    buffer, so it keeps no retained frame of its own; a drag rides the
    shared `_band_frame` and the WM's content-gesture path."""

    id = "projects"
    strip = "projects"
    searchable = False

    @property
    def grid(self):
        return self.ws.projects_grid

    def _back(self):
        """B closes this window alone, as its title strip's X does."""
        self.ws.wm.close_window_kind(self.id)

    def _desk_verbs(self):
        """The home's buttons, and on Make a real card's Play third (owner,
        2026-10-31): the game in a desk window, where a desk play is full
        screen."""
        out = LauncherHomeLayer._desk_verbs(self)
        if self.grid.tab == "make" and "change" in out:
            out = list(out)
            out.insert(min(2, len(out)), "window")
        return out

    def _wall(self):
        return None                           # the window lies on its panel

    def _hover_back(self, px, py, click, down):
        return False                          # the window's X is the way out

    def prealloc_retained(self):
        return

    def release_retained(self):
        return False

    def _panel_fill(self, cv, r):
        """The band's backdrop: the window's panel."""
        cv.rect(r[0], r[1], r[2], r[3], self.ws.theme_colors["panel"])

    def _try_band_frame(self, cv, dt):
        return _band_frame(self, cv, dt, self.grid, self.id, self._panel_fill,
                           self.strip)

    def _outside_key(self):
        return None

    def draw(self, dt):
        ws = self.ws
        cv = ws.sys_canvas
        grid = self.grid
        th = ws.theme_colors
        grid.motion = getattr(ws.wm, "HOME_MOTION", "full")
        grid.scale = getattr(ws.wm, "HOME_SCALE", HOME_SCALE_HH)
        dim = _dim_of(ws)                     # the neighbours dim toward the panel
        grid.dim = None if dim is None else (th["panel"], dim[1])
        grid.wall_ink = None
        grid.anim_frame(dt)
        if grid.flinging:
            ws.request_frame()
        if self._try_band_frame(cv, dt):    # drag-scroll fast path (#113)
            return
        cv.cls(th["panel"])
        self._draw_home_chrome(cv)
        grid.draw(cv, ws.covers.sheet_icon, _band_key(self, cv, dt, grid, False),
                  _cursor_stamp(ws))
        self._band_landed(cv, dt, False)
        if grid.desk():
            self._draw_details(cv)
        ws.bar_layer._draw_status_strip(self.strip)
