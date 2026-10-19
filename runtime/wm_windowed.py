# Map (grep -n a name to jump there):
#   _LayoutCtx                    every layout object a relayout rebuilds
#   _Win                          one open window
#   _WindowStackLayer             the one layer that presents the window stack
#   _PlayerWindowLayer            the quiet-frame partial repaint of a cart window
#   WindowedWM                    the windowed tier's presentation of the back-stack
#   WindowedWM.draw_stack         the frame router's draw list
#   WindowedWM.keys_to_cart       focus-aware key routing
#   WindowedWM.surface_skip       the skip-draw probe
#   WindowedWM.close_window_kind  close one window by its kind
#   WindowedWM.enter_console      the desk's PLAY: park the windows, the home fullscreen
#   WindowedWM.leave_console      the home's X: the route's restore op, the windows back
#   WindowedWM.release_cache      the fit check's release: parked stamps, then the home's
#   WindowedWM.show_desk          the HOME key on the desk: minimise all, or restore all
"""The WINDOWED window manager -- the big-screen / P4 "One" presentation tier
(#73 / #58 "Desktop look"; spec docs/shell_ux_v1.md §3's tier table).

`WindowedWM` presents the SAME everything-is-a-process shell as an OS desktop,
Picotron-style (owner direction, 2026-07-08):

  * two modes, `mode` (docs/shell_layout_2026-10.md §3): DESKTOP mode is the
    DESKTOP root (live wallpaper + the ONE full-width OS bar) beneath the
    process windows; CONSOLE mode is the fullscreen tier's presentation over
    the home, the desk's windows parked outside the back-stack until the
    home's X brings them back;
  * every process pushed above it (the home window / Editor / Settings / update /
    a running cart) is a floating WINDOW: a WM-drawn TITLE STRIP (title + minimize /
    maximize / close), a border + drop shadow, draggable by the strip and
    resizable by the bottom-right grip;
  * a window's body is PURELY the app: in windowed mode the zoned bar suppresses
    its OS widgets (ws.windowed_chrome -- see bar_layer.py), so
    an app window's own bar row is just its toolbar (the Editor's tab ladder),
    never a copied taskbar;
  * a RUNNING CART composites integer-scaled and centered in its window -- the
    game itself never draws chrome, exactly as on the fullscreen tiers, and the
    editor stays VISIBLE beneath a playtest (spec §3's canonical picture);
  * the desk's PROJECTS is the home in a window ("projects", always its grid
    arrangement, opened on Make); a Make pick opens the Editor in a window of
    its own above it, and the Editor's X closes back to it -- one window per
    back-stack kind;
  * INPUT FOCUS is decoupled from the back-stack: clicking a window (or its
    taskbar chip) moves the keyboard + highlight there WITHOUT popping anything
    -- a playtest keeps ticking (it stays the stack top, the running process)
    while the editor beside it is being typed in, and its pointer feed is
    click-stripped so the background cart never eats the editor's taps. Only an
    explicit exit ends a run: the strip X, the pause menu's Quit while focused, or an
    app verb. Chips: click to focus, click the focused chip to minimize, click
    a minimized chip to restore. The keyboard's TEXT MODE follows focus: a
    window keeps the mode it had when focus left it and gets it back when
    focus returns (or a pop reveals it), so typing in one window never leaves
    another -- a game's -- in text mode.

  ONE cart still runs at a time -- true multi-cart execution (N games ticking
  concurrently) stays out of scope per #73 (per-cart VM state / canvases / audio
  mixing / scheduling, revisited once the P4 is proven).

It subclasses `FullscreenStackWM`, so the back-stack, the memoized stack rebuild
and the overlay gating are inherited; only presentation differs. With a single
stack entry (just the launcher) every path defers to the parent, and the WM is
only ever installed on a console with a DISTINCT big system canvas -- the S3 /
320x240 tiers never see this class (it is deliberately NOT staged into the
device build until the P4 port lands).

The one mechanism of note is the LAYOUT CONTEXT: the responsive surfaces read
their geometry from ws.layout / ws.code_layout / the per-editor layouts (#39),
all derived from ws.sys_canvas. A window's content renders at the WINDOW's size,
so the WM keeps one `_LayoutCtx` per window (captured by running ws._relayout()
with the window's buffer installed) plus the ROOT context (the real canvas), and
installs the right one around every content draw / input dispatch. The ambient
context between dispatches is always the ROOT's, so the desktop root, the
overlays and the cursor always draw full-canvas. A resize/maximize rebuilds the
window's buffer + context (apply-on-release, so the drag itself allocates
nothing -- a rubber-band outline previews the new size).

Two siblings hold what is not the WM's state machine: `wm_desk.py` is the desk
root layer (wallpaper + bar + icon column, the retained-backdrop cache) and
`wm_chrome.py` the `WindowChrome` mixin (title strips, grip, taskbar chips).
"""

try:
    from wm import FullscreenStackWM, _VIEWPORT_BEZEL
    from layers import Layer
    # (from widgets, not palette: runtime/palette.py needs colorsys -- host-only)
    from widgets import _Blit, _in, _ticks_ms, _ticks_diff
    import moy_glass as _glass        # the surface table (docs/surface_model_v1.md §15)
    from wm_desk import _BackdropLayer
    from wm_chrome import WindowChrome, _SHADOW
    from moy_spine import EDITOR
    from shell_layout import WIDGETS as _LAYOUT_WIDGETS
    import layout_base as _layout_base
    import ui_draw as _ui_draw
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.wm import FullscreenStackWM, _VIEWPORT_BEZEL
    from runtime.layers import Layer
    from runtime.widgets import _Blit, _in, _ticks_ms, _ticks_diff
    from runtime import glass_binding as _glass
    from runtime.wm_desk import _BackdropLayer
    from runtime.wm_chrome import WindowChrome, _SHADOW
    from runtime.moy_spine import EDITOR
    from runtime.shell_layout import WIDGETS as _LAYOUT_WIDGETS
    from runtime import layout_base as _layout_base
    from runtime import ui_draw as _ui_draw

import time as _time                  # the bar clock's minute (localtime)

_DRAG_MIN = 4                         # px of travel before a press becomes a drag


# The desk's default OUTER sizes of the apps the 2026-10 look sizes to their
# content (DESIGN.md "The sizes"; the `*desktop*` targets), in glass pixels
# at chrome size 1. Every other window takes `_win_size`'s formulas.
DESK_SIZES = {"files": (548, 534), "calc": (344, 488)}


class _LayoutCtx:
    """A snapshot of every layout object the responsive surfaces read, plus the
    system canvas they draw on -- captured once per window (or for the root) and
    re-installed around each dispatch. Install is a handful of attribute stores
    (no allocation), so swapping contexts twice per frame costs nothing."""

    @classmethod
    def capture(cls, ws):
        ctx = cls()
        ctx.sys_canvas = ws._sys_canvas
        ctx.layout = ws.layout
        ctx.code_layout = ws.code_layout
        ctx.block_layout = ws.block_ui.block_layout
        ctx.paint_layout = ws.paint_layer.layout
        ctx.map_layout = ws.map_ui.layout
        ctx.scene_layout = ws.scene_ui.layout
        ctx.music_layout = ws.music_ui.layout
        ctx.cards_layout = ws.cards_layer.layout
        # Registered SYSTEM APPS (docs/app_api_v1.md): capture each app's layout
        # generically, so a new register_app'd app reflows per window with no WM
        # edits. (The legacy per-app attrs remain as views for the pinned tests.)
        ctx.app_layouts = {}
        for _app, _t in getattr(ws, "_apps", ()):
            _lay = getattr(_app, "layout", None)
            if _lay is not None:
                ctx.app_layouts[_app.id] = _lay
        ctx.artwork_layout = ctx.app_layouts.get("artwork")
        ctx.appearance_layout = ctx.app_layouts.get("appearance")
        ctx.storybook_layout = ctx.app_layouts.get("storybook")
        return ctx

    def install(self, ws, origin=(0, 0)):
        """Swap this context in, and write it into every surface grant's row
        (native/moy_app) with `origin`, where the window's content sits on the
        glass: an app reads its own row, which is the installing window's."""
        ws._sys_canvas = self.sys_canvas
        ws.layout = self.layout
        ws.code_layout = self.code_layout
        ws.block_ui.block_layout = self.block_layout
        ws.paint_layer.layout = self.paint_layout
        ws.map_ui.layout = self.map_layout
        ws.scene_ui.layout = self.scene_layout
        ws.music_ui.layout = self.music_layout
        ws.cards_layer.layout = self.cards_layout
        for _app, _t in getattr(ws, "_apps", ()):
            _lay = self.app_layouts.get(_app.id)
            if _lay is not None:
                _app.layout = _lay
        ws.launcher.set_layout(self.layout)
        if ws.projects_grid is not None:
            ws.projects_grid.set_layout(self.layout)
        ws._surface_write(origin[0], origin[1])


# A dev playtest's scale where the desk holds it (D-03.4).
_DEV_SCALE = 2


class _Win:
    """One open window: the back-stack kind it presents, its outer rect on the
    desktop, the WM title strip, and -- for app windows -- the retained content
    buffer + layout context its surfaces render with. The player window has no
    buffer: its content IS the fixed 320x240 game canvas, blitted integer-scaled
    and centered (and since nothing but the Player draws on the game canvas any
    more, a NON-top player window keeps showing its frozen last frame for free)."""

    def __init__(self, kind, x, y, w, h, title_h, buf=None, ctx=None):
        self.kind = kind
        self.x = x
        self.y = y
        self.w = w                    # outer size (incl. border + title strip)
        self.h = h
        self.title_h = title_h        # the WM strip (title + min/max/X)
        self.foot = 0                 # rows under the content: the depth's
                                      # rounded bottom corners (ui.frame_geom)
        self.edge = 0                 # columns each side the frame takes past
                                      # its first (WindowChrome._frame_edge)
        self.buf = buf                # SystemCanvas (apps) | None (player)
        self.ctx = ctx                # _LayoutCtx (apps) | None (player)
        self.minimized = False        # hidden; lives on as a taskbar chip
        self.held = False             # its run held by the minimise (a game's)
        self.saved = None             # pre-maximize rect (x, y, w, h) | None
        self._buf_stale = True        # buf is behind the content's truth (fresh
                                      # buffer, or a gesture's _direct_render
                                      # painted past it) -> the next paint must
                                      # render live before the content freeze
                                      # (_content_static) may reuse it
        self._look_gen = -1           # the look buf's content was drawn under
        self._marked = False          # buf was drawn focused: it holds a focus mark

    def content_rect(self):
        """The inner rect the content occupies: below the WM title strip and
        inside the frame, its `edge` off the sides, and above the foot (the
        frame's bottom line where it has no foot)."""
        e = self.edge
        return (self.x + 1 + e, self.y + 1 + self.title_h,
                self.w - 2 - 2 * e, self.h - 2 - self.title_h - max(self.foot, e))

    def inner_foot(self):
        """The rows under the content the window's ground fills inside its
        frame: the foot, less the frame's bottom line past its first row.
        What a layout's bottom inset counts (`layout_base.window_foot`), so
        its content stands as far from the frame's inside edge at the
        bottom as at the sides."""
        return max(self.foot, self.edge) - self.edge


class _Parked:
    """The desk's window records while console mode runs (shell_layout §3):
    the kinds that stood above the desk, the window slots bottom -> top, the
    records with their stamps, the focus, and the slots whose content moved
    while parked (re-opened fullscreen, or an Editor whose project went)."""

    def __init__(self, kinds, order, wins, focus):
        self.kinds = kinds
        self.order = order
        self.wins = wins
        self.focus = focus
        self.stale = {}


class _WindowStackLayer(Layer):
    """The single Layer that presents the whole window stack: draws every open
    window (retained buffers + the live top) + the taskbar chips, owns pointer
    routing (drag / resize / raise / min-max-close / content dispatch with
    layout-context swaps) and forwards keyboard to the focused window's content.
    Sits between the desktop root and the overlays in the WM's z-order."""

    id = "windows"
    domain = "system"

    def __init__(self, wm):
        self.ws = wm.ws
        self.wm = wm

    def draw(self, dt):
        _perf = getattr(self.ws, "perf_capture", False)
        _t0 = _ticks_ms() if _perf else 0
        wm = self.wm
        wm._in_draw = True
        try:
            wm._draw_windows(dt)
        finally:
            wm._in_draw = False
        if wm._relayout_owed:
            # An app on the verbs answers in its draw: a relayout it caused
            # (a theme, a chrome size) runs here, after the windows' draw,
            # never under it.
            wm._relayout_owed = False
            self.ws._relayout()
            self.ws._dirty = True
        if _perf:
            self.ws._pf_wm_windows = _ticks_ms() - _t0

    def handle_input(self, i):
        return self.wm._route_key(i)

    def handle_pointer(self, px, py, click):
        return self.wm._route_pointer(px, py, click)


class _PlayerWindowLayer(Layer):
    """The QUIET-FRAME partial repaint (#58 P4 perf): while a game window is the
    only thing animating, this stand-in draw stack repaints JUST the player
    window (Player.tick + the scaled game blit + its chrome) into the back
    buffer -- the rest of that buffer already holds pixel-identical desktop
    content from two frames ago. The original Library-backed root measured
    ~124ms/frame around a 7ms game on P4 glass; the real desktop removes the
    hidden grid tax, and this path also skips its remaining static wallpaper,
    bar, and other windows. DRAW ONLY: input routing keeps using the full
    memoized visible stack."""

    id = "player_window"
    domain = "system"

    def __init__(self, wm):
        self.wm = wm

    def draw(self, dt):
        wm = self.wm
        win = wm._wins.get("desktop")
        if win is not None and not win.minimized:
            # full=False: the letterbox bezel + strip/shadow chrome are static and
            # already present in BOTH ping-pong buffers (the two full paints any
            # change costs); repainting them measured ~half the quiet frame.
            wm._draw_player_window(win, True, wm._focus == "desktop", dt, full=False)


def _overlap(a, b):
    return (a[0] < b[0] + b[2] and b[0] < a[0] + a[2]
            and a[1] < b[1] + b[3] and b[1] < a[1] + a[3])


class _PopoverUnderLayer(Layer):
    """A popover frame's first half (docs/shell_layout_2026-10.md §8): put
    back the pixels a closed (or moved) popover covered, from the snapshot
    taken when it opened -- before the player window repaints over them."""

    id = "popover_under"
    domain = "system"

    def __init__(self, wm):
        self.wm = wm

    def draw(self, dt):
        self.wm._pop_restore(True)


class _PopoverOverLayer(Layer):
    """A popover frame's second half: the open popover painted over the
    retained frame (its snapshot taken first), its rect the frame's damage."""

    id = "popover_over"
    domain = "system"

    def __init__(self, wm):
        self.wm = wm

    def draw(self, dt):
        self.wm._pop_paint(True)


class WindowedWM(WindowChrome, FullscreenStackWM):
    """The TWO-MODE presentation of the back-stack (docs/shell_layout_2026-10.md
    §3): in DESKTOP mode the DESK (stack kind "desk") is the floor --
    wallpaper + system icons + taskbar, every process above it a floating
    window; in CONSOLE mode this tier presents FULLSCREEN exactly like the
    small tiers (the home and everything opened from it own the whole
    screen) while the desk's windows wait in `_parked`. Boot lands on the
    desk; the desk's PLAY enters console mode (`enter_console`), the
    home's X leaves it (`leave_console`). Install with
    `ws.wm = WindowedWM(ws)` right after construction
    (host_app.build_workstation(windowed=True)); requires a DISTINCT system
    canvas bigger than the 320x240 game canvas."""

    has_desk = True
    # `chips` (the taskbar's window chips) has a value in desktop mode only.
    LAYOUT_CATALOGUE = {"desktop": _LAYOUT_WIDGETS,
                        "console": FullscreenStackWM.LAYOUT_CATALOGUE["console"]}

    def __init__(self, ws):
        FullscreenStackWM.__init__(self, ws)
        if ws._sys_canvas is None:
            raise ValueError("WindowedWM needs a distinct (big) system canvas")
        # THE MODE (docs/shell_layout_2026-10.md §3): "desktop" -- the desk
        # and its windows -- or "console", FullscreenStackWM's presentation
        # over the home. Every tier-sensitive branch reads it, never the stack
        # shape. Console until the boot's open_desk; the desk's PLAY enters
        # console mode and the home's X leaves it. ws.windowed_chrome follows
        # it through desk_open().
        self.mode = "console"
        self._mode_was = "console"    # mode-switch edge detector (_on_nav)
        self._mode_switch = False     # on_relayout: this relayout is a switch
        # The desk's window records while console mode runs: outside the
        # back-stack, so the kernel route can truncate to its root, and put
        # back by its restore op on the way out (None in desktop mode).
        self._parked = None
        self._shown_focus = None      # the focus show_desk minimised away
        self._root_canvas = ws._sys_canvas
        self._root_ctx = _LayoutCtx.capture(ws)
        self._win_layer = _WindowStackLayer(self)
        self._wins = {}               # slot key -> _Win
        # Two orders of the same slots: `_kinds` is the back-stack's above the
        # desk (the order the windows opened in, which the chips keep), and
        # `_order` is the Z-ORDER, bottom -> top, which the focus raises
        # through: every draw and hit-test walks `_order`.
        self._kinds = []
        self._order = []
        self._front = None            # the kind a goto() asked to bring forward
        self._player_under = False    # a window above the game window overlaps it
        # Surface model v1 (docs/surface_model_v1.md): the registry and the
        # one monotonic gen mint are the kernel's surface table (§15, the
        # glass). Window records mirror _wins ("win:<slot key>" -- REGISTRY
        # keys, never content kinds, §2); _surface_signals bumps the epoch /
        # attributes gestures; skip-draw (§4) consults the gens on the
        # RECORDING tier only -- the P4/host raster paths see zero new
        # per-frame work (_recording False -> signals early-return, and their
        # canvases have no skip_surface so the console probe stays None).
        self._recording = hasattr(self._root_canvas, "begin_surface")
        self._drawn_gens = {}         # sid -> content gen last actually DRAWN
        self.surface_keyframe = False  # armed by the serve loop (§5.4)
        self._kf_active = False        # this frame's latched keyframe state
        self._clock_min = -1           # bar HH:MM minute, for the clock touch
        # INPUT FOCUS -- decoupled from the back-stack (the owner call that fixed
        # "clicking anything else kills my playtest"): the stack still owns what's
        # OPEN and who TICKS (the Player ticks while it's the stack top), but the
        # keyboard and window highlight follow _focus, which moves on click without
        # popping anything. A running game now keeps running while the editor
        # beside it is being typed in; only its X / the pause menu's Quit (when
        # focused) / an exit verb ends it. None = the desktop root has focus.
        self._focus = None
        # TEXT MODE follows _focus: the keyboard is in text mode only while
        # the focused window wants it. `_text_wish` keeps, per window key (None
        # the desk), the mode the window had when focus left it, and focus
        # coming back restores it; `_text_left` is set once a pending stack
        # change has recorded the window it takes focus from.
        self._text_wish = {}
        self._text_left = False
        # PLAY INTENT (#178): the same run() verb serves two jobs on a desktop --
        # playing a game (open it as big as fits) and PLAYTESTING the cart you are
        # editing (open it small, so the editor beside it stays readable). The
        # entry point already carries that meaning, so the caller stamps it here
        # rather than the kid choosing a scale: Editor PLAY -> "dev", everything
        # else -> "play". Read ONCE, when the player window is created; a window
        # the kid has since resized is never re-sized under them.
        self._play_intent = "play"
        self._drag = None             # (kind, grab_dx, grab_dy) while dragging
        self._restored = False        # what the last cache restore covered: a
                                      # rect, None (all of it) or False (nothing)
        self._foot_gen = None         # the look the windows' feet were sized under
        self._root_marks = None       # ui.marks' active before a window's install
        self._drag_armed = None       # (kind, ox, oy, wx, wy) press-in-strip origin
        self._resize = None           # (kind, ox, oy, ow, oh, cur_w, cur_h) in-flight
        self._ctx_switching = False   # reentrancy guard for internal _relayout runs
        self._in_draw = False         # the windows draw: a relayout waits for its end
        self._relayout_owed = False
        # Quiet-frame fast path (#58): the stand-in stacks + the ping-pong
        # full-paint debt. After ANY full repaint the NEXT drawn frame must also be
        # full -- a double-buffered panel alternates physical buffers, so one full
        # paint only updates ONE of them; a partial frame on the other would swap
        # in its pre-change desktop (visible flicker between old/new chrome).
        # The FPS chip (sig bit 2, show_fps default ON) draws on the GAME canvas
        # before the window blit, so it rides the quiet path too -- mirroring the
        # full stack's [.., perf, windows] order.
        _pw = _PlayerWindowLayer(self)
        self._quiet_stack = [_pw]
        self._full_debt = 0
        # THE POPOVER ON THE QUIET STACK (docs/shell_layout_2026-10.md §8): a
        # frame whose only change is the popover's -- it opened, a row moved,
        # it closed -- draws the popover's rect alone over the retained frame
        # and hands that rect as the frame's damage; with a playtest the
        # player window repaints between the two halves. Two snapshot slots,
        # [layer, rect, debt, captured]: the open popover's (the pixels under
        # it, taken before its first paint) and a closing one's, whose rect is
        # put back into each retained buffer (`debt` frames) -- two, because
        # a popover may open while the last one's restore is still owed.
        _pu = _PopoverUnderLayer(self)
        _po = _PopoverOverLayer(self)
        self._pop_stack = [_pu, _po]
        self._quiet_pop_stack = [_pu, _pw, _po]
        self._pop_slots = [[None, None, 0, False], [None, None, 0, False]]
        self._pop_cur = None          # the open popover's slot, or None
        self._pop_over_game = False   # ...and does it overlap the playtest's window
        # Its face, copied once painted while it overlaps a playtest: the
        # quiet frames put it back over the game instead of painting it.
        self._pop_face = None
        self._pop_face_ok = False
        # Retained desktop-backdrop cache (#58 drag perf): during a DRAG/RESIZE the
        # wallpaper + OS bar are invariant, so the first such frame captures
        # their composite and every later one blits it (see _BackdropLayer). Its
        # own full-screen off-screen buffer, allocated lazily on the first drag and
        # dropped on relayout (the size can change with the font scale).
        self._backdrop_layer = _BackdropLayer(self)
        self._backdrop = None
        self._backdrop_valid = False
        self._backdrop_disabled = False   # A/B measurement knob (P4 remote `cache`)
        self._backdrop_unsupported = False  # the root canvas can't snapshot pixels
                                            # (the web CommandCanvas, #113): stop
                                            # re-trying the capture every drag frame
                                            # (each retry minted+leaked a layer)
        self._content_gesture = False     # a finger is dragging inside a window's
                                          # CONTENT (a list scroll, not the window
                                          # itself). The desk can't change under it,
                                          # so _BackdropLayer serves the cache --
                                          # see its draw() note.
        self._desk_sig = None             # #155: the desk cache is keyed on this
        self._desk_streak = 0             # consecutive content-gesture frames that
                                          # stamped the cached desk; at 2 BOTH
                                          # ping-pong buffers hold it and the
                                          # restore can be skipped outright.
        self._desk_painted = True         # did the backdrop paint THIS frame? (it
                                          # wipes the buffer, so every window above
                                          # must then repaint -- see
                                          # _lowest_dirty_window)
        self._desk_win_sig = None         # shape sig as the DESK pass last saw it
                                          # (its own holder: it advances a pass
                                          # earlier in the frame than _win_sig)
        self._win_sig = None              # window-shape signature; a change voids
                                          # every retained window stamp
        self._sig_stable = False          # ...was it unchanged THIS frame?
        # Chrome freeze (#155): a window strip/border and the taskbar chips are
        # disjoint from every content stamp, so a quiet frame can leave them
        # alone once both ping-pong buffers hold them.
        self._chrome_quiet = False
        self._chip_sig = None
        self._chip_streak = 0
        # Dirty-union gesture restore (#58 "smooth like a real OS"): during a
        # drag/resize only the moving window's recent footprint needs the backdrop
        # re-stamped -- a full-screen 1.2MB restore per frame is the drag path's
        # dominant cost on the P4 (PSRAM-bandwidth-bound, no accelerator helps a
        # 1:1 copy). _gesture_hist holds the last _retained_n() frames' damage
        # extents (a back buffer on an N-buffer root is N frames stale; a
        # single-buffer host needs one -- the floored N covers all). Seeded
        # with the window's extent at gesture START (every physical buffer
        # holds its pre-gesture stamp).
        self._gesture_hist = []
        self._union_disabled = False      # A/B measurement knob (P4 remote `union`)
        # DAMAGE (the rotated-compositor lever, Guition P4 2026-09-08): on a
        # painted frame the WM can DESCRIBE -- a gesture's union, the windows
        # whose pixels it changed -- it hands those root rects to a canvas
        # that asks for them (`note_damage`; only the rotated DSI compositor
        # has one), and the compositor presents the rects instead of the whole
        # frame. The backdrop layer opens the description (None = it painted
        # the desk live, undescribable), each window's draw marks itself
        # touched when its pixels changed, and _hand_damage closes it once
        # per frame. Same trust as the backdrop restore places in its union.
        self._damage_rects = None
        self._win_touched = False
        self._chrome_drew = False     # this window's chrome painted (_cast_onto)

    # -- layout-context plumbing ----------------------------------------------

    def on_relayout(self):
        """Hook called by ws._relayout() (a font-scale change / future resize,
        or a mode switch). Re-anchors the relayout to the ROOT canvas if it ran
        while a window context was ambient and recaptures the root context. A
        mode switch stops there: it relayouts the bars and the root context and
        never drops a window. Anything else drops every window so it rebuilds
        at the new scale on the next frame, and releases the parked stamps,
        which re-render at it on the way back to the desk."""
        if self._ctx_switching:
            return                    # an internal (window-building) relayout
        if self._in_draw:
            self._relayout_owed = True    # after the windows' draw (the layer's)
            return
        ws = self.ws
        if ws._sys_canvas is not self._root_canvas:
            self._ctx_switching = True
            ws._sys_canvas = self._root_canvas
            ws._relayout()
            self._ctx_switching = False
        self._root_ctx = _LayoutCtx.capture(ws)
        self._pop_reset()             # a relayout repaints what a popover covered
        if self._mode_switch:
            self.content_gen += 1
            self._backdrop_valid = False
            return
        if self._parked is not None:
            for win in self._parked.wins.values():
                self._drop_win(win)
        self._drop_wins()
        self._kinds = []
        self._order = []
        self._focus = None
        self.content_gen += 1
        # The drag cache stays minted (a mode switch does not change the root's
        # size; _ensure_backdrop re-makes it if one ever does) -- re-minting
        # it here cost a 2MB layer per PLAY/CHANGE round. Its pixels are stale.
        self._backdrop_valid = False

    def on_app_registered(self, app):
        """Extend the root layout snapshot for an app registered after the
        WindowedWM was installed. Built-ins register before installation, but
        the public seam explicitly permits later shell registrations."""
        layout = getattr(app, "layout", None)
        if layout is not None:
            self._root_ctx.app_layouts[app.id] = layout

    def _install(self, ctx, win=None):
        # The focus mark draws only in the focused window (ui.marks), and in
        # none while a popover above the windows holds the keys: one a screen.
        # The root context takes back the marks it had before the windows
        # drew -- off while the console holds them under a menu -- so what
        # the root draws after them marks only where it may.
        if win is None:
            ctx.install(self.ws)
            if self._root_marks is not None:
                _ui_draw.marks(self._root_marks)
                self._root_marks = None
        else:
            if self._root_marks is None:
                self._root_marks = _ui_draw.marks()[1]
            cx, cy, _cw, _ch = win.content_rect()
            ctx.install(self.ws, (cx, cy))
            win._marked = (self._wins.get(self._focus) is win
                           and not self.popover_takes_keys())
            _ui_draw.marks(win._marked)

    def _make_ctx(self, buf, foot=0):
        """Build the layout set for a window buffer: run the console's own
        _relayout with the buffer installed (so every responsive surface derives
        its geometry from the WINDOW size, and its bottom inset from the
        window's `inner_foot`), capture it, then restore the root."""
        ws = self.ws
        self._ctx_switching = True
        ws._sys_canvas = buf
        _layout_base.in_window(True, foot)    # a window's layouts take its band
        try:
            ws._relayout()
        finally:
            _layout_base.in_window(False)
        ctx = _LayoutCtx.capture(ws)
        self._root_ctx.install(ws)
        self._ctx_switching = False
        return ctx

    # -- window records --------------------------------------------------------

    def _fs(self):
        return self.ws.look.effective_font_scale()

    def _desk_rect(self):
        """The desk's content rect, (x, y, w, h) on the root canvas: the glass
        less the OS bar. Windows size, cascade, maximise and move inside it and
        never overlap the bar (the taskbar row is OS-owned, chips included)."""
        lay = self._root_ctx.layout
        return (lay.cx, lay.cy, lay.cw, lay.ch)

    def _work_rect(self, fs):
        """The desk rect right of its icon column, 8 pixels clear at the font
        scale: where a new window opens, as macOS and GNOME open one in the
        free work area, never over the launchers."""
        dx, dy, dw, dh = self._desk_rect()
        right = dx
        for _key, box, band, _label, _cart in self._backdrop_layer._icon_rects():
            right = max(right, box[0] + box[2], band[0] + band[2])
        if right > dx:
            right = min(dx + dw, right + 8 * fs)
        return (right, dy, dx + dw - right, dh)

    def desk_open(self):
        return self.mode == "desktop"

    def _on_nav(self):
        FullscreenStackWM._on_nav(self)
        p = self._parked
        if p is not None:
            # A parked kind re-opened fullscreen is the one process: its
            # window shows what that process holds now when the desk returns.
            top = self.stack.top()
            if top in p.wins:
                p.stale[top] = True
        if self.mode != self._mode_was:
            # MODE SWITCH: windowed_chrome just changed, and every app layout
            # bakes it into its content rect at construction. Rebuild all layouts +
            # recapture the root ctx NOW, so the visible mode is never
            # presented through the other mode's chrome (repro without this: a
            # font-scale change on the desk would strip console mode's
            # fullscreen app bars). on_relayout keeps every window record.
            self._mode_was = self.mode
            self._mode_switch = True
            try:
                # The catalogue is the mode's: bars that move relayout there.
                if not self.ws.look.apply_layout():
                    self.ws._relayout()
            finally:
                self._mode_switch = False
            self.ws.bar_layer.invalidate()      # the home's X follows the floor

    # -- the two modes (docs/shell_layout_2026-10.md §3) ----------------------

    def floor(self):
        """Console mode's home has the desk beneath it (the X shows there);
        desktop mode's floor is the desk itself."""
        return 0 if self.mode == "console" else 2

    def root_kind(self):
        return "launcher" if self.mode == "console" else "desk"

    def is_parked(self, kind):
        p = self._parked
        return p is not None and kind in p.kinds

    def enter_console(self):
        """The desk's PLAY: end a running playtest as its window's X would,
        move the window records -- rect, focus, the retained stamp -- off the
        stack into `_parked`, truncate the route to its root (the home) and
        present it fullscreen. The processes behind the records keep running:
        a kind has one process, and re-opening it here shows that process."""
        if self.mode == "console":
            return
        ws = self.ws
        if self.stack.has("desktop"):
            ws._exit_to_caller()
        self._sync_windows()
        self._text_wish[self._focus] = bool(ws.input.text_mode)
        st = self.stack.kinds()
        at = self.stack.index("desk")
        self._parked = _Parked(st[at + 1:] if at >= 0 else [], self._order,
                               self._wins, self._focus)
        self._wins = {}
        self._kinds = []
        self._order = []
        self._focus = None
        self._drag = self._drag_armed = self._resize = None
        self._content_gesture = False
        self._gesture_hist = []
        if self._recording:
            _glass.sync([])
        self.mode = "console"
        self.stack.goto("launcher")
        self._on_nav()
        ws._dirty = True

    def leave_console(self):
        """The home's X: put the parked kinds back above the desk with the
        route's restore op (no process re-opens) and the window records with
        them. A record whose stamp was released re-renders once, the path a
        resize takes; one whose kind was re-opened in console mode re-renders
        once to show what that process holds now. The Editor's project is the
        workspace, and a run in console mode swaps it: a parked Editor whose
        project went that way does not come back."""
        if self.mode != "console":
            return
        ws = self.ws
        if self.stack.depth() > 1:
            ws.go_home()                  # what stood on the home leaves first
        p = self._parked or _Parked([], [], {}, None)
        self._parked = None
        kinds = list(p.kinds)
        if ("menu" in kinds and not ws._editing_icons
                and ws.editor_app.project is not ws.project):
            kinds.remove("menu")
            win = p.wins.pop("menu", None)
            if win is not None:
                if "menu" in p.order:
                    p.order.remove("menu")
                self._drop_win(win)
                if p.focus == "menu":
                    p.focus = p.order[-1] if p.order else None
        self.stack.restore(["desk"] + kinds)
        self._wins = p.wins
        self._kinds = kinds
        self._order = p.order
        self._focus = p.focus
        self.mode = "desktop"
        self._on_nav()
        self._sync_windows()
        self._apply_text(self._focus)
        for g in self._order:
            win = self._wins[g]
            win._stamp_streak = 0
            if win.kind == "desktop":
                continue
            if win.buf is None:
                self._build_content(win)
                self._prewarm(win)
            elif p.stale.get(g):
                self._prewarm(win)
        self._win_sig = None
        self._desk_sig = None
        ws._dirty = True

    def show_desk(self):
        """The HOME key in desktop mode (owner, 2026-10-13), Win+D's shape:
        minimise every window to its chip, kept with its stamp; when every
        window already is, restore them and the focus they had. A running
        game's window stays up: the desk is shown beside the game, never by
        holding it."""
        wins = [self._wins[g] for g in self._order
                if self._wins[g].kind != "desktop"]
        if not wins:
            return
        if all(w.minimized for w in wins):
            for w in wins:
                w.minimized = False
            focus = self._shown_focus
            self._move_focus(focus if focus in self._wins else self._order[-1])
            return
        self._shown_focus = self._focus
        for w in wins:
            w.minimized = True
        if self._focus is not None and self._wins[self._focus].minimized:
            self._move_focus("desktop" if "desktop" in self._wins else None)

    def release_cache(self):
        """The fit check's release: the parked stamps oldest-first, one per
        call, then the popover's snapshots when none is live, then the home's
        retained frame."""
        p = self._parked
        if p is not None:
            for g in p.order:
                win = p.wins[g]
                if win.buf is not None:
                    self._drop_win(win)
                    return True
        if not self._pop_live():
            held = False
            for s in self._pop_slots:
                if s[0] is not None:
                    self._release_layer(s[0])
                    s[0] = None
                    held = True
            if self._pop_face is not None:
                self._release_layer(self._pop_face)
                self._pop_face = None
                held = True
            if held:
                return True
        return FullscreenStackWM.release_cache(self)

    def _slots(self):
        """The back-stack's kinds ABOVE THE DESK, one window each, bottom ->
        top. In console mode there are NO windows, ever: the desk's records
        are parked and every kind presents fullscreen."""
        if self.mode == "console":
            return []
        base = self.stack.index("desk") + 1
        if not base:
            return []
        st = self.stack.kinds()
        return st[base:]

    def _sync_windows(self):
        """Mirror the back-stack into window records: drop windows whose kind
        was popped, create records (buffer + layout context + cascade
        position) for newly-pushed ones."""
        keys = self._slots()
        front, self._front = self._front, None
        if keys != self._kinds:
            alive = set(keys)
            old = self._focus
            if not self._text_left and (old is None or old in alive):
                self._text_wish[old] = bool(self.ws.input.text_mode)
            for g in list(self._wins):
                if g not in alive:
                    self._drop_win(self._wins.pop(g))
                    self._text_wish.pop(g, None)
            pushed = keys[-1] if keys and keys[-1] not in self._wins else None
            had = set(self._wins)
            for depth, g in enumerate(keys):
                if g not in self._wins:
                    self._wins[g] = self._make_window(g, g, depth)
            # The z-order keeps its windows where they were; a new one opens
            # on top.
            order = [g for g in self._order if g in alive]
            for g in keys:
                if g not in order:
                    order.append(g)
            if ("desktop" in self._kinds) != ("desktop" in alive):
                # the Editor's Play reads Stop while a playtest runs (D-23)
                bar = getattr(self.ws, "bar_layer", None)
                if bar is not None:
                    bar.invalidate()
            self._kinds = keys
            self._order = order
            # The focus after a stack change: the kind a navigation brought
            # forward (a spawn, or a return to a window below), else a window
            # just opened; a close leaves it where it was, or on the window
            # now in front. Click moves it after that. A window opened set its
            # own text mode; one a pop revealed gets back the mode it had.
            if front in alive:
                new = front
            elif pushed is not None:
                new = pushed
            elif old in alive:
                new = old
            else:
                new = order[-1] if order else None
            self._focus = new
            self._raise(new)
            if new in had and new != old:
                self._apply_text(new)
        elif front is not None and front in self._wins and self.mode == "desktop":
            self._bring_forward(front)
        self._text_left = False
        # Surface model: window records mirror the slot set. A dropped slot's
        # surface goes with it; a re-opened one is REBORN with fresh gens from
        # the monotonic mint, so no client cache can alias it (§2).
        if self._recording:
            _glass.sync(["win:" + g for g in self._order])

    def set_play_intent(self, intent):
        """Record WHY the next cart run is starting -- "dev" (a playtest launched
        from the Editor) or "play" (the desk/home). Only the player window's
        DEFAULT size reads it; see _win_size."""
        self._play_intent = intent if intent == "dev" else "play"

    def _win_size(self, key, full, fs):
        """Default window OUTER size per window slot: on a desk, the apps the
        2026-10 look sizes to their content take that size (`DESK_SIZES`)."""
        th = _layout_base.metric("title", fs)
        if _layout_base.metric_class() == _layout_base.DESK and key in DESK_SIZES:
            n = _layout_base.chrome_size(fs)
            r = _layout_base.render_scale()
            return (DESK_SIZES[key][0] * n // r, DESK_SIZES[key][1] * n // r)
        if key == "desktop":
            # A dev playtest (#178) opens at 2x where the desk holds it, else
            # at native size (the owner's D-03.4, 2026-10-19): the game is the
            # point of the window. A desk play takes the largest whole scale
            # that fits. The frame fits the game exactly: its border (and the
            # edge a wider frame takes), its title strip and the rows its
            # rounded bottom corners take (the foot), which the content rect
            # leaves out.
            foot = (_ui_draw.frame_geom(full.w, full.h, th)[1]
                    if _ui_draw.depth()[13] else 0)
            fit = max(1, min((full.w - 24) // self.ws.canvas.w,
                             (self._desk_rect()[3] - 24 - th - foot)
                             // self.ws.canvas.h))
            s = min(fit, _DEV_SCALE) if self._play_intent == "dev" else fit
            e = self._frame_edge()
            return (self.ws.canvas.w * s + 2 + 2 * e,
                    self.ws.canvas.h * s + 2 + th + max(foot, e))
        if key in ("menu", "projects"):
            return (full.w - full.w // 8, full.h - full.h // 10)
        if key in ("artwork", "appearance", "storybook"):
            return (full.w - full.w // 8, full.h - full.h // 10)
        if key == "update":
            return (full.w // 2, full.h // 2)
        # Settings + default: a compact floating panel (the Picotron proportion),
        # never a near-fullscreen sheet.
        return (max(340 * fs, full.w * 2 // 5), max(300 * fs, full.h * 3 // 5))

    def _make_window(self, key, kind, depth):
        full = self._root_canvas
        fs = self._fs()
        w, h = self._win_size(key, full, fs)
        title_h = _layout_base.metric("title", fs)
        dx, dy, dw, dh = self._desk_rect()
        # A default can exceed a small desktop (e.g. the fs-scaled Settings minimum
        # at font 2x on 600px) -- clamp so the whole window, grip included, fits
        # the desk beside the OS bar.
        w = min(w, dw - 4 * fs)
        h = min(h, dh - 8 * fs)
        if key == "desktop" and self._play_intent == "dev":
            x, y = self._dev_place(w, h, fs)
        else:
            # Cascade: centred in the work area right of the icon column (a
            # window wider than it as far right as the desk allows), each
            # deeper window stepping down-right, clamped so the title strip
            # always stays reachable on the desk.
            wx, _wy, ww, _wh = self._work_rect(fs)
            if w > ww:
                wx, ww = max(dx, dx + dw - w), w
            x = wx + (ww - w) // 2 + depth * 12 * fs
            y = dy + (dh - h) * 2 // 5 + depth * 10 * fs
            x = max(wx, min(x, dx + dw - (w if w <= dw - (wx - dx) else w // 2)))
            y = max(dy, min(y, dy + dh - 24 * fs))
        x, y = self._clear_of_subject(x, y, w, h, fs)
        win = _Win(kind, x, y, w, h, title_h)
        self._build_content(win)
        # Prewarm: render the content ONCE into the fresh buffer, so a window
        # created BEHIND another (e.g. the home window when it and the Editor
        # spawn in one navigation) shows its app instead of a black retained
        # buffer.
        self._prewarm(win)
        return win

    def _dev_place(self, w, h, fs):
        """Where a dev playtest of outer size (w, h) opens (D-03.4): at 2x,
        centred over the Editor's window (the desk's middle with none); at
        native size, at the Editor's top right, clear of the symbol keys
        along its foot. Clamped to the desk."""
        dx, dy, dw, dh = self._desk_rect()
        ed = self._wins.get("menu")
        native = w < 2 * self.ws.canvas.w
        if ed is None or getattr(ed, "minimized", False):
            ex, ey, ew, eh = dx, dy, dw, dh
        else:
            ex, ey, ew, eh = ed.x, ed.y, ed.w, ed.h
        if native:
            x = ex + ew - w - 8 * fs
            y = ey + (ed.title_h if ed is not None else 0) + 8 * fs
        else:
            x = ex + (ew - w) // 2
            y = ey + (eh - h) // 2
        x = max(dx, min(x, dx + dw - w))
        y = max(dy, min(y, dy + dh - h))
        return x, y

    def _clear_of_subject(self, x, y, w, h, fs):
        """A new window's place kept clear of the wallpaper's subject
        (`Wallpaper.subject`, docs/theming_2026-09.md §4.9): where the
        window at (x, y) would cover it, the nearest place beside, above or
        under it, 8 pixels clear at the font scale, that holds the whole
        window in the work area (`_work_rect`, clear of the icon column);
        where none does, (x, y) as it was."""
        s = getattr(getattr(self.ws, "wallpaper", None), "subject", None)
        if s is None:
            return x, y
        sx, sy, sw, sh = s
        m = 8 * fs
        if x + w <= sx - m or x >= sx + sw + m or y + h <= sy - m \
                or y >= sy + sh + m:
            return x, y
        dx, dy, dw, dh = self._desk_rect()
        dw -= self._work_rect(fs)[0] - dx
        dx = self._work_rect(fs)[0]
        best = None
        for cx, cy in ((sx - m - w, y), (sx + sw + m, y),
                       (x, sy - m - h), (x, sy + sh + m)):
            if cx < dx or cy < dy or cx + w > dx + dw or cy + h > dy + dh:
                continue
            d = abs(cx - x) + abs(cy - y)
            if best is None or d < best[0]:
                best = (d, cx, cy)
        return (x, y) if best is None else (best[1], best[2])

    def _prewarm(self, win):
        """One-shot render of an app window's content into its buffer (defensive:
        a content error just leaves the buffer blank -- the live path will report
        it when the window is focused)."""
        if win.buf is None or win.ctx is None:
            return
        content = self._content_for(win.kind)
        if content is None:
            return
        self._install(win.ctx, win)
        try:
            content.draw(0)
            win._buf_stale = False
            win._look_gen = self.ws.look.look_gen
        except Exception:  # noqa: BLE001
            pass
        finally:
            self._install(self._root_ctx)

    def _build_content(self, win):
        """(Re)build a window's content backing for its CURRENT size: the buffer +
        layout context for an app window; nothing for the player (its content is
        the game canvas, scaled/centered per draw).

        The buffer is the ROOT canvas's own new_layer(), so every tier gets its
        native window surface from one call: the host SystemCanvas returns a
        font-scale-carrying SystemCanvas layer (what this method used to build by
        hand), a RECORDING root (the web console's CommandCanvas) returns a
        RecordingLayer that rasterizes AND records its own stream -- blit_strip
        then ships the window to the browser as a cached off-screen layer (the
        #54/#43 deflayer mechanism: a retained background window blits by
        reference, only a live window re-ships) -- and the P4's device system
        canvas (#58) returns an RGB565 layer sharing its native moy_gfx kernel."""
        win.foot = self._win_foot(win)
        win.edge = self._frame_edge()
        if win.kind == "desktop":
            win.buf = None
            win.ctx = None
            return
        full = self._root_canvas
        cw = max(64, win.w - 2 - 2 * win.edge)
        ch = max(40, win.h - 2 - win.title_h - max(win.foot, win.edge))
        self._drop_win(win)             # a resize's old buffer, freed first
        win.buf = full.new_layer(cw, ch)
        win.ctx = self._make_ctx(win.buf, win.inner_foot())
        win._buf_stale = True         # blank until a live render/_prewarm fills it

    def _resize_window(self, win, w, h):
        """Apply a new OUTER size (resize grip release / maximize): clamp to a
        usable minimum, then rebuild the content buffer + layout context so the
        app reflows to the new window size."""
        fs = self._fs()
        dx, dy, max_w, max_h = self._desk_rect()
        full = self._root_canvas
        # A right or bottom bar is a wall the grip stops at.
        if dx + max_w < full.w:
            max_w = max(1, min(max_w, dx + max_w - win.x))
        if dy + max_h < full.h:
            max_h = max(1, min(max_h, dy + max_h - win.y))
        min_w = 160 * fs
        min_h = 90 * fs + win.title_h
        # Min-size convention (ui.py + the app API): a registered app declares
        # its resize minimum (fs-scaled) at register_app time -- the minimums
        # live with the app, not in a WM if-ladder. The cap keeps the same apps
        # usable on a 320x240 windowed host.
        ms = self.ws.app_min_size(win.kind) \
            if hasattr(self.ws, "app_min_size") else None
        if ms is not None:
            min_w = min(max_w, ms[0] * fs)
            min_h = min(max_h, ms[1] * fs)
        win.w = max(min_w, min(w, max_w))
        win.h = max(min_h, min(h, max_h))
        self._build_content(win)
        self.ws._dirty = True

    def _toggle_max(self, win):
        """Maximize <-> restore: fill the desk's content rect (the taskbar
        stays visible), remembering the rect to restore to."""
        dx, dy, dw, dh = self._desk_rect()
        if win.saved is None:
            win.saved = (win.x, win.y, win.w, win.h)
            win.x, win.y = dx, dy
            self._resize_window(win, dw, dh)
        else:
            x, y, w, h = win.saved
            win.saved = None
            win.x, win.y = x, y
            self._resize_window(win, w, h)

    def _top_win(self):
        return self._wins.get(self._order[-1]) if self._order else None

    def _focus_win(self):
        """The window holding INPUT focus, or None (the desktop root)."""
        return self._wins.get(self._focus) if self._focus else None

    def input_holder(self):
        """The process the keyboard reaches: the focused window's key on the
        desk (None: the desk itself), the stack top in console mode."""
        if self.mode == "console":
            return self.stack.top()
        return self._focus

    def _move_focus(self, key):
        """Focus moves to window `key` (None: the desk) with no stack change,
        and the window comes to the front, as on every desktop: the window it
        leaves keeps its text mode, and `key` gets back its own."""
        old = self._focus
        if key == old:
            self._raise(key)
            return
        self._text_wish[old] = bool(self.ws.input.text_mode)
        self._focus = key
        self._raise(key)
        self._apply_text(key)

    def _raise(self, key):
        """Window `key` to the top of the z-order (None, or one on top: no-op)."""
        order = self._order
        if key is None or not order or order[-1] == key or key not in order:
            return
        order.remove(key)
        order.append(key)
        self.ws._dirty = True

    def _bring_forward(self, key):
        """Window `key` shown, focused and in front: what opening a process
        that already has a window does on the desk."""
        win = self._wins.get(key)
        if win is None:
            return
        if win.minimized:
            self._minimize(win, False)
            self.ws._dirty = True
        self._move_focus(key)

    def _minimize(self, win, on):
        """Hide `win` to its chip (`on`) or show it again. A running game's
        window holds its run while hidden, as the pause menu does
        (`Player.pause`), and gives it back on restore -- unless the run was
        already held (the pause menu open), which stays its holder's."""
        if win.minimized == bool(on):
            return
        win.minimized = bool(on)
        if win.kind != "desktop":
            return
        player = getattr(self.ws, "player", None)
        if player is None:
            return
        if on:
            win.held = not player.paused
            if win.held:
                player.pause()
        elif getattr(win, "held", False):
            win.held = False
            player.resume()

    def goto(self, kind):
        """The back-stack's goto, and on the desk the window it names comes
        forward: a spawn opens on top, a return to a window below raises it,
        and a goto to the process already on the stack's top raises its
        window over any the focus had put in front of it."""
        FullscreenStackWM.goto(self, kind)
        if self.mode == "desktop":
            self._front = kind

    def bring_forward(self, kind):
        """On the desk, a process that already has a window is opened by
        bringing that window forward (shown, focused, raised), every other
        window left open, as a desktop's launcher does."""
        if (self.mode != "desktop" or kind not in self._wins
                or self._slots() != self._kinds):
            return False
        self._bring_forward(kind)
        self.ws._dirty = True
        return True

    def _apply_text(self, key):
        """Put the keyboard in the text mode window `key` had (button mode
        for one that never set it)."""
        ws = self.ws
        on = self._text_wish.get(key, False)
        if on != bool(ws.input.text_mode):
            ws._set_text_mode(on)

    def note_text_mode(self, prev):
        """Console hook, before `ws._set_text_mode` applies a mode: while a
        stack change is not yet mirrored into windows the mode is the window
        about to take focus, so the focused one keeps `prev`, its own."""
        if self.mode == "console" or self._text_left:
            return
        if self._slots() != self._kinds:
            self._text_wish[self._focus] = bool(prev)
            self._text_left = True

    def player_has_pointer(self):
        """Console hook: whether the game-space pointer publication should carry
        live click/down state. False while a window OTHER than the playtest holds
        focus, so a background running cart never eats the taps meant for the
        editor beside it."""
        if self.mode == "console":
            return True
        win = self._focus_win()
        return win is not None and win.kind == "desktop"

    def hand_content_damage(self, cv, x, y, w, h):
        """Console mode presents fullscreen and describes like the fullscreen
        tier; a desktop-mode frame is described by its windows
        (`_hand_damage`), whose content draws in window coordinates."""
        if self.mode == "console":
            FullscreenStackWM.hand_content_damage(self, cv, x, y, w, h)

    def keys_to_cart(self):
        """Console hook (#44 redraw gate), focus-aware like player_has_pointer:
        keyboard follows _focus (decoupled from the back-stack), so keys reach
        the cart only while the PLAYER window holds focus. This is the gate that
        stops a held/typed key from forcing the FULL desktop repaint every frame
        -- a BLE keyboard reports last_key as LEVEL state (unlike the T-Deck's
        press edge), and an unconditional dirty-mark collapsed play to ~10fps on
        any key (the P4 keyboard slowdown). An editor focused beside a playtest
        keeps repainting on typing exactly as before."""
        ws = self.ws
        if ws.cart_error is not None or (ws._update is None and ws._draw is None):
            return False
        if self.mode == "console":             # the fullscreen shape
            return FullscreenStackWM.keys_to_cart(self)
        win = self._focus_win()
        return win is not None and win.kind == "desktop"

    def keeps_animating(self, dt):
        """Console hook (the #44 redraw gate): keep frames flowing while a RUNNING
        cart's window is open anywhere on the desktop -- not just while it's the
        stack top -- so a game visibly keeps playing under Settings / beside a
        focused editor. Mirrors _animating's own desktop branch conditions.

        Deliberately NO gesture leg here: a WM gesture's repaint is guaranteed
        by the POINTER leg (window movement implies pointer movement; a
        held-still pointer moves nothing, so skipping is correct), and
        _animating feeds the backdrop cache's "desk pixels are moving" test --
        a gesture leg would force a live desk re-render every drag frame,
        which is the exact #155 regression the cache exists to prevent.

        A LIVE WALLPAPER does belong here (owner report 2026-07-31: "they only
        move when I move a window... but it doesn't happen while Settings is on
        top"). _animating's own wallpaper leg keys on the TOP process kind --
        `kind in ("launcher", "settings", "desk")` -- a list written before apps
        were windows, so Settings kept the desk alive and Appearance (or Files,
        Paint, Storybook...) silently froze it. On THIS tier the desk backdrop is
        visible behind every window, so its liveness cannot depend on who is on
        top. Unlike the gesture leg, re-rendering here is CORRECT: a live
        wallpaper's desk pixels really do change every frame, which is exactly
        the case the backdrop cache is not for."""
        ws = self.ws
        if self.mode == "desktop" and ws.wallpaper.is_animating(dt):
            return True                 # a parked desk's wallpaper is not drawn
        return (self.stack.has("desktop") and ws.cart_error is None
                and (ws._update is not None or ws._draw is not None))

    def _content_for(self, kind):
        """The content Layer a window presents (the router's registry lookup,
        window-kind-aware): the active Editor tab for "menu", the registry entry
        otherwise -- same resolution ws._content_layer() does for the stack top."""
        ws = self.ws
        if kind == "menu":
            return ws.layer_of(ws.menu_view) or ws._content_layers["cards"]
        return ws.layer_of(kind)

    # -- the memoized stacks (parent memo, windowed shape) ---------------------

    def _rebuild(self, content, sig):
        if self.mode == "console":
            # Console mode: byte-identical to the fullscreen tier for the
            # whole stack (the home, fullscreen games, Settings, apps). The
            # desk's windows are parked, not dropped.
            FullscreenStackWM._rebuild(self, content, sig)
            return
        self._sync_windows()
        ws = self.ws
        overlays = self._overlay_layers(sig)   # the shared sig ladder (wm.py)
        # The GAME-domain perf HUD is deliberately NOT in this stack. It draws on
        # the 320x240 game canvas, and on this tier that canvas reaches the screen
        # two different ways: a raster backend composites its pixels, but a
        # RECORDING one (the web console) has no pixels -- the cart's commands are
        # placed by the window's `view` bracket (#175). Anything game-domain drawn
        # outside that bracket lands unplaced at the DESKTOP origin, which is
        # exactly where the FPS chip appeared: floating over the editor window at
        # its game-canvas coordinates (owner report 2026-07-31). So the chip is
        # drawn inside _draw_player_window, between the cart's draw and the
        # composite -- same pixels on a raster tier, correctly placed on a
        # recording one.
        base = [self._backdrop_layer, self._win_layer]
        draw_base = [ws._splash_layer] if (sig & 1) else base
        self._cache_overlay = overlays
        self._cache_visible = base + overlays
        self._cache_draw = draw_base + overlays
        rev = list(self._cache_visible)
        rev.reverse()
        self._cache_visible_rev = rev
        self._cache_content = content
        self._cache_gen = self.content_gen
        self._cache_sig = sig

    # -- the popover on the quiet stack (docs/shell_layout_2026-10.md §8) -------

    def _pop_rides(self):
        """True where a popover change paints its rect alone: desktop mode on
        a raster root (the recording tier's surfaces replay by layer)."""
        return self.mode == "desktop" and not self._recording

    def _pop_n(self):
        """The root's real buffer count: what a popover's rect is owed to."""
        n = getattr(self._root_canvas, "RETAINED_FRAMES", 1)
        return n if n > 1 else 1

    def popover_changed(self):
        """The popover opened, moved its cursor or closed. Riding: owe its
        rect to every retained buffer -- a paint while it is open, the
        snapshot put back once it is closed. Otherwise the frame repaints."""
        if not self._pop_rides():
            self.ws._dirty = True
            return
        m = self.ws.sysmenu
        n = self._pop_n()
        self._pop_face_ok = False
        slots = self._pop_slots
        cur = self._pop_cur
        rect = self.ws.menu_ui.panel_rect() if m.open else None
        if cur is not None and slots[cur][1] != rect:
            slots[cur][2] = n             # closing (or moved): restore owed
            cur = self._pop_cur = None
        if rect is None:
            return
        if cur is None:
            j = 0 if slots[0][1] is None or slots[0][2] <= 0 else 1
            if slots[j][1] is not None and slots[j][2] > 0:
                self.ws._dirty = True     # both slots owed: repaint whole
                self._pop_reset()
                j = 0
            s = slots[j]
            s[1] = rect
            s[3] = False
            cur = self._pop_cur = j
        slots[cur][2] = n

    def popover_owed(self):
        """True while a retained buffer is owed the popover's rect."""
        for s in self._pop_slots:
            if s[2] > 0:
                return True
        return False

    def popover_takes_keys(self):
        """The open popover owns the keyboard and paints its own change, so
        a key press does not mark the frame dirty (console.handle_input)."""
        return self.ws.sysmenu.open and self._pop_rides()

    def _pop_live(self):
        """True while a popover is open or owed to a retained buffer."""
        if self._pop_cur is not None:
            return True
        for s in self._pop_slots:
            if s[1] is not None:
                return True
        return False

    def _pop_reset(self):
        self._pop_face_ok = False
        for s in self._pop_slots:
            s[1] = None
            s[2] = 0
            s[3] = False
        self._pop_cur = None

    def _pop_note(self, r):
        nd = getattr(self._root_canvas, "note_damage", None)
        if nd is not None:
            nd(r[0], r[1], r[2], r[3])

    def _pop_restore(self, ride):
        """Put back each closing popover's rect (and, on a full frame, the
        open one's, so the frame starts as if no popover were drawn)."""
        root = self._root_canvas
        cur = self._pop_cur
        for i in range(2):
            s = self._pop_slots[i]
            r = s[1]
            if r is None:
                continue
            if i == cur:
                if not ride and s[3]:
                    root.blit_strip_rect(s[0], r[0], r[1], r[0], r[1], r[2], r[3])
                continue
            if s[3]:
                root.blit_strip_rect(s[0], r[0], r[1], r[0], r[1], r[2], r[3])
                if ride:
                    self._pop_note(r)
            s[2] -= 1
            if s[2] <= 0:
                s[1] = None
                s[2] = 0
                s[3] = False

    def _pop_capture(self, s):
        """Snapshot the pixels under slot `s`'s rect from the root canvas."""
        x, y, w, h = s[1]
        lay = s[0]
        if lay is None or lay.w < w or lay.h < h:
            root = self._root_canvas
            ow, oh = (lay.w, lay.h) if lay is not None else (0, 0)
            self._release_layer(lay)
            lay = s[0] = root.new_layer(max(w, ow), max(h, oh))
        lay.blit_strip(self._root_canvas, -x, -y)
        s[3] = True
        # Whether the playtest's window repaints under it, read once here:
        # nothing moves a window while a popover is open (a tap outside
        # closes it), and a play frame allocates nothing.
        win = self._wins.get("desktop")
        self._pop_over_game = (win is not None and not win.minimized
                               and _overlap(s[1], self._win_extent(win)))

    def _pop_paint(self, ride):
        """Paint the open popover: snapshot what it covers first (always on
        a full frame, whose stack just repainted it), then the panel. A ride
        paints it while a buffer is owed it, or every frame it overlaps the
        playtest's window, which repaints beneath it."""
        cur = self._pop_cur
        m = self.ws.sysmenu
        if cur is None or not m.open:
            if not ride:
                self.ws.menu_ui._draw_sysmenu()
            return
        s = self._pop_slots[cur]
        fresh = not ride or not s[3]
        if fresh:
            self._pop_capture(s)
        r = s[1]
        if ride and s[2] <= 0:
            if "desktop" not in self._wins:
                self._pop_note(r)         # a still frame: say so, not "all"
                return
            if not self._pop_over_game:
                return
            if self._pop_face_ok:
                # The playtest repainted under it: put its face back, one
                # copy, rather than paint the component again every frame.
                root = self._root_canvas
                root.blit_strip_rect(self._pop_face, r[0], r[1], r[0], r[1], r[2], r[3])
                self._pop_note(r)
                return
        if not fresh and self.ws.menu_ui.looked():
            # the look's card casts a shadow: paint it over what it covers,
            # never over its own last paint
            self._root_canvas.blit_strip_rect(s[0], r[0], r[1], r[0], r[1], r[2], r[3])
        self.ws.menu_ui._draw_sysmenu()
        if self._pop_over_game and not self._pop_face_ok:
            face = self._pop_face
            if face is None or face.w < r[2] or face.h < r[3]:
                ow, oh = (face.w, face.h) if face is not None else (0, 0)
                self._release_layer(face)
                face = self._pop_face = self._root_canvas.new_layer(
                    max(r[2], ow), max(r[3], oh))
            face.blit_strip(self._root_canvas, -r[0], -r[1])
            self._pop_face_ok = True
        if ride:
            self._pop_note(r)
        if s[2] > 0:
            s[2] -= 1

    # -- the quiet-frame draw stack (#58 P4 perf) -------------------------------

    def _retained_n(self):
        """How many physical buffers the root canvas rotates through -- the
        staleness horizon every partial-paint mechanism must respect: retained
        pixels may be trusted only after N consecutive identical paints, a
        change must be painted into all N buffers before partials resume
        (debt = N-1), and a gesture's damage trail must union the last N
        frames' extents. Parameterized for the P4 triple-framebuffer render
        overlap (#58); FLOORED at 2 so the host's single persistent buffer
        keeps today's conservative gates (behavior-identical at N<=2)."""
        n = getattr(self._root_canvas, "RETAINED_FRAMES", 1)
        return n if n > 2 else 2

    def draw_stack(self):
        """The frame router's draw list. QUIET frames -- a running game window is
        the only animation: no dirty flag, no pointer change, no cursor, no
        overlays/splash, no drag/resize in flight -- return the one-layer partial
        stack (just the player window; see _PlayerWindowLayer), with the
        popover's two halves around it while one is open or owed. A still
        frame whose only change is the popover's takes the popover's layers
        alone (docs/shell_layout_2026-10.md §8). Everything else falls through
        to the full memoized stack, and every full paint leaves a debt so every
        retained buffer carries the change before partial frames resume."""
        ws = self.ws
        self._ensure_stack()
        # Surface-model signals run BEFORE any layer draws or skip-decides
        # (recording tier only; a raster tier returns immediately). The dt is
        # nominal -- the one dt-sensitive predicate (wallpaper.is_animating)
        # only tests dt > 0.
        self._surface_signals(1 / 60)
        # NOTE deliberately NO pointer-state condition: a finger playing a touch
        # cart moves the (hidden) pointer every frame, and the desktop shows no
        # hover feedback while the cursor is hidden -- real UI reactions all set
        # ws._dirty. Requiring a still pointer would forfeit the fast path for
        # exactly the games that need it.
        # The popover (bit 64) rides the quiet stack too: its rect is the
        # frame's damage, never a reason to repaint the desk.
        pop = self._pop_live()
        still = (not ws._dirty
                 and (self._cache_sig & ~66) == 0   # FPS chip (bit 2), popover (64)
                 and ws.cart_error is None
                 and self._drag is None and self._drag_armed is None
                 and self._resize is None
                 and not getattr(ws.pointer, "visible", False))
        # A window over the game's casts its shadow on it and must stay over
        # it: the quiet stack repaints the game window alone, so not then.
        quiet = still and "desktop" in self._wins and not self._player_under
        if quiet and self._full_debt <= 0:
            if pop:
                return self._quiet_pop_stack
            return self._quiet_stack        # the FPS chip rides the player window
        if (still and pop and not quiet and self.mode == "desktop"
                and not self._recording and not ws._animating(1 / 60)):
            # A persistent root (one buffer) owes no other buffer anything.
            if self._full_debt <= 0 or self._pop_n() == 1:
                return self._pop_stack
            quiet = True                    # a full paint that pays the debt
        if quiet:
            self._full_debt -= 1      # paying the other-buffer debt: full paint
        else:
            # A change painted: every OTHER physical buffer owes a full paint
            # before partial frames may resume (N-1 debts on an N-buffer root).
            self._full_debt = self._retained_n() - 1
        return self._cache_draw

    # -- the drag backdrop cache (#58 drag perf) -------------------------------

    # -- window buffer lifetime ------------------------------------------------
    #
    # A window's content buffer is a root-canvas layer, and on a board a layer
    # lives OUTSIDE the gc heap (heap_caps PSRAM, device_canvas._LayerComp),
    # where nothing collects it: the WM has to say when it is done. It did not,
    # and every window open leaked its buffer -- ~3.6MB per Library -> CHANGE
    # -> home round on the Guition P4 (window + re-minted backdrop + the
    # strip cache's canvases), measured 2026-09-09: 15.9MB of PSRAM free at
    # boot, 0.8MB after four rounds, after which every layer fell back onto
    # the gc heap and a collect cost 430ms. `release` is probed, so a
    # recording tier's layer (no release) and the host's are unaffected.

    @staticmethod
    def _release_layer(lay):
        rel = getattr(lay, "release", None)
        if rel is not None:
            rel()

    def _drop_win(self, win):
        buf = win.buf
        if buf is None:
            return
        win.buf = None
        win.ctx = None
        self._release_layer(buf)

    def _drop_wins(self):
        for win in self._wins.values():
            self._drop_win(win)
        self._wins.clear()

    def _drop_backdrop(self):
        cache = self._backdrop
        self._backdrop = None
        if cache is not None:
            self._release_layer(cache)

    def _ensure_backdrop(self):
        """The full-screen off-screen cache buffer (lazily allocated, re-made if
        the root canvas size changed under it)."""
        sc = self._root_canvas
        cache = self._backdrop
        if cache is None or cache.w != sc.w or cache.h != sc.h:
            self._drop_backdrop()
            cache = self._backdrop = sc.new_layer(sc.w, sc.h)
            self._backdrop_valid = False
        return cache

    def _capture_backdrop(self):
        """Snapshot the just-rendered desktop backdrop -- the root canvas with NO
        windows on it yet (_BackdropLayer precedes _win_layer) -- into the cache:
        one opaque full-frame copy via the cache canvas's own blit_strip reading
        the root canvas AS a source layer (native moy_gfx.blit565 on the device,
        host index slice otherwise -- the same kernel both directions)."""
        # UNSUPPORTED IS A HARD STOP, CHECKED HERE (2026-07-31). The callers gate
        # on _backdrop_unsupported only to choose the LIVE render path -- they
        # still called this afterwards, and _ensure_backdrop mints a fresh
        # full-screen layer BEFORE the blit_strip fails again. On a recording
        # root (web/wasm) that leaked one 1024x600 RecordingLayer per painted
        # desk frame: the recorder's _layers list is append-only (dense deflayer
        # ids, dropped only on a cart change), so the console's own dropped
        # reference frees nothing. Measured: ~614KB retained per leak, and since
        # every GC collect then scans a bigger heap, collect cost climbed
        # 13ms -> 380ms over one browsing session -- the owner's "the more the
        # kinetic scroll activates the slower it gets".
        if self._backdrop_unsupported or self._backdrop_disabled:
            return
        try:
            cache = self._ensure_backdrop()
            self.ws.note_cost("desk.backdrop")
            cache.blit_strip(self._root_canvas, 0, 0)
            self._backdrop_valid = True
        except Exception:  # noqa: BLE001 -- a failed capture (OOM, or a
            self._backdrop_valid = False   # recording root with no pixels to
            self._drop_backdrop()          # snapshot) forfeits the cache; the
            # drag re-renders live. Mark the mechanism unsupported so the next
            # frames don't retry (#113: each retry allocated a fresh layer --
            # on the web root that leaked one RecordingLayer per drag frame).
            self._backdrop_unsupported = True

    def _gesture_extent(self):
        """The moving window's CURRENT damage bbox (x, y, w, h) -- everything the
        gesture draws this frame: the window body (at the resize rubber size when
        larger OR smaller -- max covers the no-live-resize outline mode too), its
        border and the 3px drop shadow, padded a couple px for safety. None when
        no gesture is in flight."""
        g = self._drag if self._drag is not None else self._resize
        if g is None:
            return None
        win = self._wins.get(g[0])
        if win is None:
            return None
        w, h = win.w, win.h
        # Gesture tuples carry the window REGISTRY key (g[0]); every
        # gesture-vs-window test below resolves through _wins identity.
        if g is self._resize:
            w = max(w, self._resize[5])
            h = max(h, self._resize[6])
        return self._pad_extent(win, win.x, win.y, w, h)

    def _seed_gesture_hist(self):
        """Prime the damage history at gesture START: every physical buffer
        holds the window's pre-gesture stamp, so the first N restores must
        cover it."""
        ext = self._gesture_extent()
        self._gesture_hist = [ext] * self._retained_n() if ext is not None else []

    def _blit_backdrop_cache(self):
        """Stamp the cached backdrop into the current (ping-pong) back buffer --
        restricted to the DIRTY UNION of the gesture's recent damage extents when
        the canvas supports a rect-clipped stamp (#58: a full-screen 1:1 copy is
        ~26ms PSRAM-bandwidth-bound on the P4 and no accelerator helps it; the
        union is window-sized, so the restore cost follows the WINDOW, not the
        screen). Everything outside the union is either untouched since that
        buffer's last frame or opaquely redrawn every frame (the other windows,
        the bar strip, the chips), so the partial restore is pixel-safe. Falls
        back to the full copy when the canvas lacks blit_strip_rect (the web
        RecordingLayer) or via the `union` A/B knob."""
        self._restored = False
        if self._backdrop is None:
            return
        rc = self._root_canvas
        stamp = getattr(rc, "blit_strip_rect", None)
        ext = self._gesture_extent()
        n = self._retained_n()
        if (stamp is None or self._union_disabled or ext is None
                or not self._gesture_hist):
            rc.blit_strip(self._backdrop, 0, 0)
            self._restored = None
            if ext is not None:
                self._gesture_hist = (self._gesture_hist + [ext])[-n:] \
                    if self._gesture_hist else [ext] * n
            # A whole-cache copy still CHANGES only the gesture's footprint:
            # outside it the cache is the desk that was already there, and the
            # other windows re-stamp over it identically.
            return self._union_bbox(self._gesture_hist)
        rects = self._gesture_hist + [ext]
        x0 = min(r[0] for r in rects)
        y0 = min(r[1] for r in rects)
        x1 = max(r[0] + r[2] for r in rects)
        y1 = max(r[1] + r[3] for r in rects)
        # Subtract the window's CURRENT opaque body from the union (measured on
        # glass 2026-07-10: the union alone bought ~nothing for a near-full-screen
        # window -- the default windows are ~60% of the desktop, so "window-sized"
        # ~= "screen-sized"). The body rect gets fully covered THIS frame anyway
        # (content stamp + chrome fills + shadow are all opaque), so only the
        # EXPOSED margin -- the thin trail strips the window just left -- needs
        # the backdrop back: for a real finger drag that's a few px of perimeter,
        # ~KBs instead of the window's ~700KB, making restore cost independent of
        # window size. Decomposed as up to 4 strips (top/bottom/left/right of the
        # union minus the body); the resize body is the rubber rect (live-body
        # covers it opaquely too).
        g = self._drag if self._drag is not None else self._resize
        win = self._wins.get(g[0]) if g is not None else None
        bx0 = by0 = bx1 = by1 = 0
        if win is not None:
            bw, bh = win.w, win.h
            if g is self._resize \
                    and self._live_resize_ok() and win.kind != "desktop":
                bw, bh = self._resize[5], self._resize[6]
            # The rows the depth's rounded corners leave partly uncovered are
            # restored with the margin: only the square middle is opaque.
            r = self._win_radius(win)
            bx0 = max(x0, win.x)
            by0 = max(y0, win.y + r)
            bx1 = min(x1, win.x + bw)
            by1 = min(y1, win.y + bh - r)
        if win is None or bx0 >= bx1 or by0 >= by1:
            stamp(self._backdrop, 0, 0, x0, y0, x1 - x0, y1 - y0)
        else:
            if by0 > y0:                                       # top strip
                stamp(self._backdrop, 0, 0, x0, y0, x1 - x0, by0 - y0)
            if by1 < y1:                                       # bottom strip
                stamp(self._backdrop, 0, 0, x0, by1, x1 - x0, y1 - by1)
            if bx0 > x0:                                       # left strip
                stamp(self._backdrop, 0, 0, x0, by0, bx0 - x0, by1 - by0)
            if bx1 < x1:                                       # right strip
                stamp(self._backdrop, 0, 0, bx1, by0, x1 - bx1, by1 - by0)
        self._gesture_hist = (self._gesture_hist + [ext])[-n:]
        self._restored = (x0, y0, x1 - x0, y1 - y0)
        return self._restored

    @staticmethod
    def _union_bbox(rects):
        if not rects:
            return None
        x0 = min(r[0] for r in rects)
        y0 = min(r[1] for r in rects)
        x1 = max(r[0] + r[2] for r in rects)
        y1 = max(r[1] + r[3] for r in rects)
        return (x0, y0, x1 - x0, y1 - y0)

    def _win_extent(self, win):
        """Everything a window's draw touches: body, border and shadow, padded
        like _gesture_extent."""
        return self._pad_extent(win, win.x, win.y, win.w, win.h)

    def _hand_damage(self, touched):
        """Close this frame's damage description: hand the backdrop's rects and
        the touched windows' extents to a root canvas that takes them. Nothing
        is handed when the frame cannot be described -- the backdrop painted
        live, an overlay other than the popover (toast/notice/About/splash) is
        in the stack, or the cursor sprite is visible (it moves every frame and
        is drawn by a layer above this one) -- and the compositor then presents
        the whole frame. The popover's rects, open or closing, ride along."""
        rects = self._damage_rects
        self._damage_rects = None
        if rects is None:
            return
        nd = getattr(self._root_canvas, "note_damage", None)
        if nd is None:
            return
        if (self._cache_sig & ~66) != 0:
            return                        # the popover (64) is described below
        if getattr(self.ws.pointer, "visible", False):
            return
        pops = False
        for s in self._pop_slots:
            if s[1] is not None:
                pops = True
        if not rects and not touched and not pops:
            return                        # nothing to say: the safe full frame
        for r in rects:
            nd(*r)
        for r in touched:
            nd(*r)
        for s in self._pop_slots:
            if s[1] is not None:
                nd(*s[1])

    # -- game viewport == the player window (#39 mapping) ----------------------

    def _player_view(self, win):
        """(ox, oy, scale) of the game canvas centered in the player window's
        content rect -- the windowed viewport. A cart-declared view (`view(w,
        h)`) scales ITS region here exactly like the fullscreen composite, so
        celeste's 128x128 fills the window at 4x instead of the container's 2x."""
        gc = self.ws.canvas
        view = self._view_src()
        gw, gh = (view[2], view[3]) if view else (gc.w, gc.h)
        cx, cy, cw, ch = win.content_rect()
        scale = max(1, min(cw // gw, ch // gh))
        ox = cx + (cw - gw * scale) // 2
        oy = cy + (ch - gh * scale) // 2
        return (ox, oy, scale)

    def game_is_fullscreen(self):
        """Only in console mode. In desktop mode the game lives in a
        WINDOW -- _draw_player_window emits its own placement bracket around the
        cart's draw -- so the router must NOT also letterbox the whole desktop.
        It briefly did, and because the FPS overlay ("perf") is a GAME-domain
        layer, a windowed playtest blacked out the desk and re-placed the game:
        the owner saw "the project launcher games also go fullscreen"."""
        return self.mode == "console"

    def viewport(self):
        win = self._wins.get("desktop")
        if win is None:
            return FullscreenStackWM.viewport(self)
        return self._player_view(win)


    def present_frame(self, cf, view):
        # Desktop mode: the player WINDOW shows the frame itself
        # (_draw_player_window), so a frame shown full-viewport here would
        # land on the desktop. Console mode shows it where composite_game
        # would have composited the canvas, over the same bezel.
        if self.mode == "desktop":
            return False
        ws = self.ws
        sc = ws.sys_canvas
        if sc is not self._pf_for:
            self._pf_for = sc
            self._pf_fn = getattr(sc, "present_frame", None)
        pf = self._pf_fn
        if pf is None:
            return False
        ox, oy, scale = FullscreenStackWM.viewport(self)
        src = self._view_src()
        self._play_bezel(sc, ox, oy, scale, src)
        return pf(cf, view, ws.canvas, ox, oy, scale, src)

    def _window_frame(self, gc, ox, oy, scale, defer, src):
        """The player window's composite of a compiled cart's frame: shown
        from the cart's memory, or -- on a composite the cart drew no new
        frame for -- from the copy of the last one shown. True when the root
        canvas took it; otherwise the frame is settled into the game canvas,
        which the window composites as ever (Workstation._composite_game's
        rule)."""
        cf = self.ws.cart_frame
        if cf is None:
            return False
        view = cf.take()
        if view is not None:
            sc = self._root_canvas
            if sc is not self._pf_for:
                self._pf_for = sc
                self._pf_fn = getattr(sc, "present_frame", None)
            pf = self._pf_fn
            if pf is not None and pf(cf, view, gc, ox, oy, scale, src, defer):
                return True
            cf.settle(gc)
        cf.nrects = 0
        return False

    def composite_game(self):
        # Desktop mode: a no-op -- the window layer blits the game canvas into
        # the player WINDOW itself, and stamping it full-viewport would paint
        # over the desktop. Console mode: the fullscreen composite, routed
        # through the polymorphic _blit_game so the P4's native RGB565 path and
        # the web's b64-spr path both work (the parent's index loops need an
        # indexed system buffer this tier's canvases may not have).
        if self.mode == "desktop":
            return
        ws = self.ws
        gc = ws.canvas
        sc = ws.sys_canvas
        if gc is not self._fb_for:     # the parent's cached flush_batch probe
            self._fb_for = gc
            self._fb_fn = getattr(gc, "flush_batch", None)
        if self._fb_fn is not None:
            self._fb_fn()
        if sc is gc:
            return
        if getattr(gc, "buf", None) is None \
                and getattr(sc, "blit_game", None) is None:
            # Command-only game canvas (#175): the cart's frame is ALREADY in
            # the stream; the letterbox cls() below would wipe it, so bail.
            # Ask the question _blit_game asks -- a system canvas with a native
            # blit_game can composite, buffer or no buffer. A `.buf`-only probe
            # silently never composited the fullscreen game on the device
            # raster canvas, whose framebuffer is `_buf` (P4, 2026-08-01).
            return
        ox, oy, scale = FullscreenStackWM.viewport(self)
        src = self._view_src()
        self._play_bezel(sc, ox, oy, scale, src)
        self._blit_game(sc, gc, ox, oy, scale, src=src)

    def play_viewport(self):
        """Console mode's fullscreen game rect, (ox, oy, scale): what a run
        the kernel drives in front composes (native/moy_play's front). None in
        the desk world and for a cart-declared view, which the console
        composes."""
        if self._order or self._view_src() is not None:
            return None
        return FullscreenStackWM.viewport(self)

    def _play_bezel(self, sc, ox, oy, scale, src):
        """Console mode's letterbox bezel, at most once a frame.

        It is STATIC: fill it only until every framebuffer holds it (the
        stale-by-N rule) -- it was a full-screen fill EVERY play frame
        (chrome=8ms of a 15ms celeste frame). Retention must end BOTH on a
        geometry change AND on a gap in the drawn-frame counter: anything
        larger than one frame means another stack painted the screen and the
        bezel pixels are no longer ours -- without the gap rule, N stale
        Library frames rotate/flicker behind the game (P4, 2026-08-01,
        measured as 0/32/24 bytes differing between the three
        framebuffers)."""
        drawn = self.ws._frames_drawn
        if drawn == getattr(self, "_bezel_frame", -2):
            return              # a declined frame's composite, same frame
        bkey = (ox, oy, scale, sc.w, sc.h, src)
        if bkey != getattr(self, "_bezel_key", None) \
                or drawn != getattr(self, "_bezel_frame", -2) + 1:
            self._bezel_key = bkey
            self._bezel_paints = 0
        self._bezel_frame = drawn
        if self._bezel_paints < self._retained_n():
            self._bezel_paints += 1
            sc.cls(_VIEWPORT_BEZEL)    # letterbox fill

    # -- drawing ---------------------------------------------------------------

    def _lowest_dirty_window(self, dt):
        """Index of the LOWEST window that must repaint this frame; everything
        below it is already correct in the target framebuffer and is skipped.

        Why (measured on glass 2026-07-26): every open window was re-stamped and
        re-chromed EVERY frame even when nothing about it changed -- an unfocused
        window isn't even re-rendered, just copied. Each extra open window cost
        ~44ms a frame (1 window 56ms, 2 windows 99ms, 3 windows 144ms), which is
        what made a desktop with a few things open feel far worse than the
        fullscreen home.

        The z-order rule that makes skipping SOUND: windows are painted bottom to
        top, so a repainting window overwrites the overlap of everything below
        it. Skipping is therefore only safe for a CONTIGUOUS run at the BOTTOM --
        find the lowest window that must paint and draw from there up.

        A window may be skipped only once its pixels sit in BOTH ping-pong
        buffers (`_stamp_streak >= _retained_n()`, the same rule _BackdropLayer
        uses), it is
        not the focused window (whose content re-renders live), and nothing
        global invalidated the frame -- the desk backdrop repainting under it, a
        live animation, or a visible cursor whose old stamp would ghost."""
        n = len(self._order)
        # Any change to the window SHAPE of the frame (which windows, their
        # z-order, geometry, minimised state, or which one has focus) voids every
        # retained stamp. One signature comparison beats hunting each mutation
        # site (open/close/move/resize/maximise/focus) and can't miss one.
        sig = self._shape_sig()
        if sig != self._win_sig:
            self._win_sig = sig
            self._player_under = self._covers_player()
            for k in self._order:
                self._wins[k]._stamp_streak = 0
            self._sig_stable = False
            return 0
        self._sig_stable = True
        if not self._frame_is_quiet(dt):
            return 0                      # something under/over everything moved
        horizon = self._retained_n()
        for i in range(n):
            win = self._wins[self._order[i]]
            if win.minimized:
                continue                  # nothing painted -> nothing to skip past
            if (self._order[i] == self._focus or win.kind == "desktop"
                    or getattr(win, "_stamp_streak", 0) < horizon):
                return i
        return n                          # every window is settled: draw none

    def _covers_player(self):
        """Whether a shown window above the game window reaches it, its
        shadow included (read once a shape change, by draw_stack's quiet
        path)."""
        order = self._order
        if "desktop" not in order:
            return False
        win = self._wins["desktop"]
        if win.minimized:
            return False
        for k in order[order.index("desktop") + 1:]:
            up = self._wins[k]
            if not up.minimized and _overlap(self._pad_extent(up, up.x, up.y, up.w, up.h),
                                             (win.x, win.y, win.w, win.h)):
                return True
        return False

    def _frame_is_quiet(self, dt):
        """True when nothing GLOBAL invalidated the retained ping-pong pixels
        this frame: the desk backdrop did not repaint under the windows, no
        window is being dragged or resized, nothing is animating, and no cursor
        stamp needs erasing. Shared by the window-skip and the chrome freeze."""
        if (self._desk_painted or self._drag is not None
                or self._resize is not None or self.ws._animating(dt)):
            return False
        p = self.ws.pointer
        if p is not None and getattr(p, "visible", False):
            return False
        return True

    def _surface_signals(self, dt):
        """Per-frame surface-model signal bookkeeping (§3). Runs at the TOP of
        draw_stack -- BEFORE any layer's skip decision -- or a dirty frame's
        epoch would land after the desk already declared itself clean.

        Signals: a ws._dirty write (Class A un-attributed) or a transient
        overlay bumps the set EPOCH -- everything reads changed, today's
        full-repaint behavior expressed as gens. A WM GESTURE in flight is the
        first AUDITED flow (§3 Class C -> A): the change is the moving window
        (+ the chips outline on a resize), so only those sids are touched --
        which is exactly what lets the desk/bar/other windows SKIP during a
        drag. Pointer activity outside a WM gesture stays the conservative
        epoch (content handlers mutate without marking dirty -- the pre-#44
        debt). Class B declarations follow _animating's by-name list
        (pure-read predicates, verified before mirroring)."""
        if not self._recording:
            return                    # raster tiers: zero new per-frame work
        ws = self.ws
        self._kf_active = self.surface_keyframe
        self.surface_keyframe = False
        cur = ws._ptr_state()
        last = ws._last_ptr
        ptr = ((cur is not None and (cur[3] or cur[4]))
               or (last is not None and (last[3] or last[4])))
        now = _ticks_ms()
        overlay = (ws._splash_until is not None
                   or (ws._confetti_until
                       and _ticks_diff(ws._confetti_until, now) > 0)
                   or (ws._egg_until and _ticks_diff(ws._egg_until, now) > 0)
                   or (ws._toast_until and _ticks_diff(ws._toast_until, now) > 0))
        gesture = self._drag if self._drag is not None else self._resize
        if ws._dirty or overlay:
            _glass.epoch()
        elif gesture is not None:
            key = gesture[0]
            if self._wins.get(key) is not None:
                _glass.touch(_glass.surface("win:" + key))   # pre-L4 streams carry the position
                if gesture is self._resize:
                    _glass.touch(_glass.surface("chips"))    # the rubber-band outline rides there
        elif ptr and self._content_gesture and self._focus is not None:
            # A CONTENT gesture (a finger scrolling/selecting INSIDE the
            # focused window): the desk cannot change under it (the backdrop
            # cache's own invariant), so attribute the change to that window
            # alone. Without this the pointer leg epoch'd everything and the
            # desk -- whose pixel cache the web root cannot use -- re-rendered
            # LIVE at ~627KB of garbage per frame (measured, the code-editor
            # drag), which is what marched the GC into mid-gesture collects.
            _glass.touch(_glass.surface("win:" + self._focus))
        elif ptr or cur != last:
            # `cur != last` is a POSITION-only change: a hovering mouse. The
            # shell's hover feedback (desk icon highlight, cards msel) is drawn
            # by surfaces that do not all mark dirty for it, so a gen-clean
            # skip would swallow exactly the repaint hover needs. Epoch is the
            # honest cost of hover -- it lasts only while the mouse MOVES (a
            # still pointer is inert again, and the idle desktop stays free).
            _glass.epoch()
        # The desk clock (bar HH:MM renders inside the "launcher" backdrop
        # surface): a minute flip is a content change nobody marks -- attribute
        # it here so a skipped desk can never show a stale clock.
        try:
            mm = _time.localtime()[4]
        except Exception:  # noqa: BLE001 -- no RTC: the bar shows mm:ss uptime
            mm = None
        if mm != self._clock_min:
            self._clock_min = mm
            _glass.touch(_glass.surface("launcher"))
        # Class B for the non-window surfaces: the desk under a live wallpaper,
        # the visible cursor (its stream follows the pointer position) -- and
        # console mode's home while its row coasts on a kinetic fling or glide.
        # Fling frames paint via request_frame() (no dirty, no pointer), so
        # without this declaration every gen read clean and skip-draw FROZE the
        # coasting grid until the next real signal -- the owner's "fast drags
        # lag" (a fast drag flings; a slow one doesn't). In the desk world the
        # home's row isn't visible, so launcher.flinging is False there and
        # the desk backdrop keeps skipping.
        _glass.animating(_glass.surface("launcher"), (
            ws.wallpaper.is_animating(dt)
            or bool(getattr(ws.launcher, "flinging", False))))
        _glass.animating(_glass.surface("cursor"),
                         bool(getattr(ws.pointer, "visible", False)))

    def _surface_windows(self):
        """Window-record bookkeeping, AFTER _sync_windows (live slot set):
        placement mirrored by compare (the WM owns x/y/w/h -- diffing its own
        writes covers every write site from one place: drag, maximize, resize
        apply, relayout clamp) and the §3 Class B declarations (the
        _content_static by-name list, becoming its registration seed)."""
        if not self._recording:
            return
        ws = self.ws
        for z, key in enumerate(self._order):
            win = self._wins[key]
            s = _glass.surface("win:" + key)
            _glass.place(s, win.x, win.y, win.w, win.h, z)
            _glass.animating(s, win.kind in ("desktop", "update")
                           or (win.kind == "settings"
                               and ws.settings_layer.bluetooth_animating())
                           or (win.kind == "menu" and ws.menu_view == "music"
                               and ws.music_ui.music_preview is not None))

    def surface_skip(self, layer_id):
        """The console draw-stack probe (§4 skip-draw): True = do not draw this
        layer this frame; the recorder keeps its z-slot and the wire ships a
        {"same":1} stub. Recording tier only; only the desk backdrop is layer-
        eligible for now (windows skip individually inside _draw_windows --
        their layer carries the sync/bookkeeping and must always run)."""
        if not self._recording or self._kf_active or layer_id != "launcher":
            return False
        h = _glass.surface_find(layer_id)
        if _glass.animating(h):
            return False
        gen = _glass.content_gen(h)
        if gen == self._drawn_gens.get(layer_id):
            self.ws.note_cost("surface.skip")
            return True
        self._drawn_gens[layer_id] = gen      # it WILL draw this frame
        return False

    def _draw_windows(self, dt):
        self._sync_windows()
        self._resize_feet()
        self._surface_windows()
        # Per-window surface marks (§2: this is the Stage 9 re-partition -- the
        # one "windows" span becomes one span per window + a "chips" residual).
        # Probed like console.draw_stack's _surf: None on a raw canvas -> no
        # call, no allocation, pixels byte-identical.
        _surf = getattr(self._root_canvas, "begin_surface", None)
        _skip = getattr(self._root_canvas, "skip_surface", None)
        n = len(self._order)
        first = self._lowest_dirty_window(dt)
        # Chrome freeze (#155): a window's strip/border/shadow is DISJOINT from
        # its content stamp, so on a quiet frame -- a content scroll, a fling,
        # typing -- those pixels are already correct in this ping-pong buffer and
        # redrawing them is pure waste. Measured on P4 glass during a picker
        # scroll: 8.2ms of a 70ms frame, every frame, for an unchanged title bar.
        # _lowest_dirty_window sets _sig_stable (window shape/order/focus).
        self._chrome_quiet = self._sig_stable and self._frame_is_quiet(dt)
        touched = []
        for i in range(n):
            key = self._order[i]
            win = self._wins[key]
            focused = (key == self._focus)
            sid = "win:" + key
            if win.minimized:
                continue                  # hidden: no z-slot, not replayed
            if i < first:
                # The raster partial path settled this window. On the recording
                # tier its z-slot must still reach the wire (the page replays
                # surfaces in listed order -- an omitted window would vanish
                # under the desk's cls), so keep it as a skip stub.
                if _skip is not None:
                    _skip(sid, "system")
                continue
            if _skip is not None and not self._kf_active:
                # §4 skip-draw: gen-clean + non-animating -> don't draw at all;
                # the client replays its retained stream. The gesture target
                # can never match (its touch in _surface_signals moved the gen)
                # and a reborn window's fresh mint always differs (§2).
                sh = _glass.surface_find(sid)
                gen = _glass.content_gen(sh)
                if (gen == self._drawn_gens.get(sid)
                        and not _glass.animating(sh)):
                    _skip(sid, "system")
                    self.ws.note_cost("surface.skip")
                    continue
                self._drawn_gens[sid] = gen
            if _surf is not None:
                _surf(sid, "system")
            win._stamp_streak = getattr(win, "_stamp_streak", 0) + 1
            self._win_touched = False
            self._chrome_drew = False
            if win.kind == "desktop":
                # The Player TICKS whenever its window is open, independent of
                # input focus AND of what sits above it on the back-stack -- a
                # game keeps running while the editor beside it is typed in or
                # while Settings floats over it (wifi setup mid-game, #38). The
                # one exception: the crash-editor flow keeps the frozen frame
                # (cart_error -> tick just repaints the error panel, harmless).
                # Its composite is the canvas's word (mark_game), not this
                # loop's: only a moved/re-chromed player window is touched.
                self._draw_player_window(win, True, focused, dt)
            else:
                self._draw_app_window(win, focused, dt)
            self._cast_onto(i)
            if self._win_touched:
                touched.append(self._win_extent(win))
        # The chips live on the OS bar, whose strip the compositor carries
        # when the chip draw says it moved; the resize outline lies inside the
        # gesture union. Neither is a rect of its own.
        self._hand_damage(touched)
        if _skip is not None and not self._kf_active:
            # The chips residual skips like a window: its content moves on
            # focus/order/minimize (dirty -> epoch) and on a resize outline
            # (attributed in _surface_signals).
            gen = _glass.content_gen(_glass.surface_find("chips"))
            if gen == self._drawn_gens.get("chips"):
                _skip("chips", "system")
                return
            self._drawn_gens["chips"] = gen
        if _surf is not None:
            # The residual span: chips + the resize outline. Its OWN sid --
            # SurfaceDelta's cache is sid-keyed, so a second "windows" span in
            # one frame would corrupt the {"same":1} replay.
            _surf("chips", "system")
        self._draw_taskbar_chips(quiet=self._chrome_quiet)
        if self._resize is not None:               # rubber-band resize preview
            kind, _ox, _oy, _ow, _oh, cw, chh = self._resize
            win = self._wins.get(kind)
            # App windows resize LIVE-BODY (drawn in _draw_app_window, #58 "real
            # OS" feel); the outline preview remains for the game window (its
            # content is the scaled composite -- cropping it mid-gesture reads as
            # glitch, and the scale recomputes on release anyway) and for
            # canvases without the rect stamp (the web RecordingLayer).
            if win is not None and (win.kind == "desktop"
                                    or not self._live_resize_ok()):
                self._root_canvas.rectb(win.x, win.y, cw, chh,
                                        self.ws.theme_colors["accent"])

    def _direct_render(self, win, dt):
        """Draw the window's content into the framebuffer IN PLACE, through a
        viewport at its content rect (#155). Returns False if this backend or
        this window can't take the path, and the caller falls back to the
        render-into-buffer-then-stamp route.

        The layout context still comes from the window (every responsive surface
        must keep deriving its geometry from the WINDOW size); only the canvas is
        swapped for the root, masked to the window's rect. That is why the
        viewport has to match win.buf exactly -- the layouts were built for that
        size, and a mismatch would lay the content out for the wrong surface."""
        root = self._root_canvas
        sv = getattr(root, "set_viewport", None)
        if sv is None:
            return False                      # web RecordingLayer: keep the stamp
        cx, cy, cw, ch = win.content_rect()
        if cw <= 0 or ch <= 0 or cw != win.buf.w or ch != win.buf.h:
            return False
        # A window may hang off the screen edge; a clamped viewport would then be
        # a different surface from the one its layouts were built for. Stamp
        # instead (the buffer path crops for free).
        if cx < 0 or cy < 0 or cx + cw > root.w or cy + ch > root.h:
            return False
        ws = self.ws
        self._install(win.ctx, win)
        ws._sys_canvas = root                 # ...but paint on the framebuffer
        ws._surface_write(cx, cy)
        sv(cx, cy, cw, ch)
        try:
            self._content_for(win.kind).draw(dt)
        finally:
            root.clear_viewport()
            self._install(self._root_ctx)
        win._buf_stale = True                 # buf skipped: it now lags the truth
        return True

    def _content_static(self, win):
        """True when this PAINTED frame provably did not change WIN's content,
        so the focused window's draw may be skipped and the stamp reuse the
        retained win.buf (the surface-granularity damage model,
        docs/surface_model_v1.md §14.1 -- this is its first slice).

        "Provably" is three facts together:

        - Nothing marked the UI dirty. ws._dirty is still readable during the
          draw -- frame() clears it AFTER -- so a False here means no input or
          state handler reported a visible change this frame.
        - No pointer DOWN or CLICK was live this frame or last. The drag-driven
          handlers (paint strokes, map pans, block drags, kinetic scrolls)
          mutate content WITHOUT marking dirty -- they predate the #44 gate and
          rely on the pointer-state change arming the repaint -- so any button
          activity forces a live render. The click check reads BOTH frames'
          states because handlers consume p.click in place (a tap that opened a
          prompt zeroes it before the draw sees it); the previous frame's edge
          is still visible in ws._last_ptr. A position/visibility-only change
          -- the moving cursor, its auto-hide -- is inert: no content surface
          draws hover feedback (audited 2026-07-27; the cursor is an overlay,
          and anything that appears without input must already be an
          _animating source or it could never paint under the redraw gate).
        - The content is not one of the self-animating window surfaces: the
          _animating sources that draw INSIDE an app window (the music
          preview's playhead + PLAY/STOP, the bluetooth panel's async
          scan/pair states, the update screen's progress) repaint on
          animation-armed frames precisely because nothing marks dirty, so
          they are excluded by name. Sources OUTSIDE app windows (running
          cart, live wallpaper, splash, confetti/toast/egg overlays) draw on
          other layers and need no exclusion.

        win._buf_stale guards the buffer itself: a fresh/rebuilt buffer or a
        gesture's _direct_render (which paints past the buffer straight into
        the framebuffer) leaves buf behind the truth, so the next paint
        renders live to refill it before the freeze may resume, and
        win._look_gen against `look_gen` re-renders a buffer drawn under an
        older look."""
        ws = self.ws
        gen = ws.look.look_gen
        if win._look_gen != gen:
            win._look_gen = gen
            return False
        if ws._dirty or win.buf is None or win._buf_stale:
            return False
        cur = ws._ptr_state()
        last = ws._last_ptr
        if cur is not None and (cur[3] or cur[4]):
            return False
        if last is not None and (last[3] or last[4]):
            return False
        if (cur is None) != (last is None):
            return False                  # pointer appeared/vanished: be safe
        k = win.kind
        if k == "update":
            return False
        if k == "settings" and ws.settings_layer.bluetooth_animating():
            return False
        if k == "menu" and ws.menu_view == "music" \
                and ws.music_ui.music_preview is not None:
            return False
        return True

    def _draw_app_window(self, win, focused, dt):
        # Freeze the content render while THIS window is being dragged/resized
        # (#58 drag perf): its buffer can't change under a drag (no input reaches
        # the content, and a resize only rubber-bands until release), so re-running
        # the editor-tab layout every drag frame is pure waste -- blit the retained
        # buffer at the new position instead. A drag of ANOTHER window still lets
        # this one render live (it's not the one moving).
        # Gesture keys are the window REGISTRY key -- resolve through _wins
        # identity (see _gesture_extent's note).
        if self._resize is not None \
                and self._wins.get(self._resize[0]) is win \
                and self._live_resize_ok():
            # Live-body resize: the frame follows the grip, content crops.
            self._win_touched = True
            self._draw_resizing_window(win, focused,
                                       self._resize[5], self._resize[6])
            return
        moving = ((self._drag is not None
                   and self._wins.get(self._drag[0]) is win)
                  or (self._resize is not None
                      and self._wins.get(self._resize[0]) is win))
        if moving:
            self._win_touched = True
        elif not focused and (win._look_gen != self.ws.look.look_gen
                              or (win._marked and _ui_draw.marks_on())):
            # A background window's retained buffer under an older look, or
            # drawn while it had the focus mark.
            win._look_gen = self.ws.look.look_gen
            self._win_touched = True
            self._prewarm(win)
        if focused and not moving:
            # DIRECT RENDER (#155): while this window's content is being scrolled
            # or flung, draw it STRAIGHT into the framebuffer through a viewport
            # and skip the 1:1 stamp entirely -- that copy is ~900KB of bus
            # traffic per frame on the P4, against a measured 91MB/s ceiling.
            #
            # Gated to the gesture on purpose. Outside one, the render still goes
            # into win.buf, because other paths stamp FROM that buffer without
            # re-rendering (the drag freeze, a settled background window). Only
            # during a gesture is nobody reading it -- the window repaints every
            # frame anyway -- so letting it go stale for the duration is free,
            # and the first ordinary frame after the release refills it.
            if (self._chrome_quiet
                    and (self._content_gesture or self._content_flinging())
                    and self._order and self._wins.get(self._order[-1]) is win
                    and self._direct_render(win, dt)):
                self._win_touched = True
                self._win_chrome(win, focused, quiet=self._chrome_quiet)
                return
            # CONTENT FREEZE (docs/surface_model_v1.md §14.1, first slice): on a
            # painted frame that provably did not change this window's content
            # (see _content_static), skip the re-render and let the retained-
            # buffer stamp below present it -- the map tab's draw is ~70ms on P4
            # glass, the stamp ~14ms, and the stamp runs either way.
            if not self._content_static(win):
                # Live: render the focused app into its buffer at the window's
                # layout.
                self._win_touched = True
                self._install(win.ctx, win)
                try:
                    self._content_for(win.kind).draw(dt)
                finally:
                    self._install(self._root_ctx)
                win._buf_stale = False
        # Stamp-defer (#58 drag; extended to CONTENT gestures 2026-07-26): on a
        # device with an async 1:1 blitter (P4SystemCanvas.blit_strip_async ->
        # PPA DMA) draw the chrome FIRST -- strip/borders/shadow/title are all
        # DISJOINT from the content rect -- and register the content stamp so the
        # compositor kicks it as the frame's LAST framebuffer write. The deferred
        # present then overlaps the ~25ms DMA with the next loop's input poll,
        # the same machinery as the quiet-game-frame composite.
        #
        # Why content scrolls qualify now: the stamp is a 1:1 copy, so it is a
        # wall-time WASH against the CPU (PSRAM-bound both ways, measured 91MB/s
        # either engine) -- ALL of its value is the overlap, and the overlap is
        # only sound when nothing draws over the region afterwards. The #155
        # chrome freeze made that true on a quiet gesture frame: the strip,
        # border and taskbar chips no longer repaint at all, so the deferred DMA
        # really is the last write. Same accepted trade as the drag path -- the
        # resize grip sits inside the content rect and hides for the duration.
        #
        # Host/web canvases have no hook, so their path is byte-identical.
        moving_this = (self._drag is not None
                       and self._wins.get(self._drag[0]) is win)
        scrolling_this = (focused and self._chrome_quiet
                          and (self._content_gesture
                               or self._content_flinging()))
        if ((moving_this or scrolling_this)
                and self._order and self._wins.get(self._order[-1]) is win):
            asb = getattr(self._root_canvas, "blit_strip_async", None)
            if asb is not None:
                self._win_chrome(win, focused, quiet=self._chrome_quiet)
                cx, cy, _cw, _ch = win.content_rect()
                if asb(win.buf, cx, cy):
                    return
                # non-fit / refusal: fall through to the sync stamp (the chrome
                # is already down; the second draw below is harmless overdraw).
        # Retained (or just-rendered) buffer -> desktop, then chrome.
        _perf = getattr(self.ws, "perf_capture", False)
        _t0 = _ticks_ms() if _perf else 0
        cx, cy, _cw, _ch = win.content_rect()
        self._root_canvas.blit_strip(win.buf, cx, cy)
        if _perf:
            self.ws._pf_wm_stamp = _ticks_ms() - _t0
        self._win_chrome(win, focused, quiet=self._chrome_quiet)

    def _fps_chip_on(self):
        """The parent's overlay-sig bit 2, re-read where the chip is now drawn:
        show_fps AND the player is the process on top (a game under Settings
        keeps its chip hidden, as before)."""
        return (self.ws.show_fps and self.stack.top() == "desktop")

    def _draw_player_window(self, win, running, focused, dt, full=True):
        ws = self.ws
        gc = ws.canvas
        # Geometry FIRST (it does not depend on the cart's draw): on a recording
        # target with a command-only game canvas we place the cart's draw span with
        # a WM view bracket (#175) instead of compositing its pixels afterwards --
        # so the bezel and the bracket must both precede the draw, or the bezel
        # would wipe the frame the cart just recorded.
        cx, cy, cw, ch = win.content_rect()
        ox, oy, scale = self._player_view(win)
        _view = getattr(self._root_canvas, "view", None)
        # A command-only game canvas (no `buf`) has NO pixels to composite, so the
        # bracket is not optional -- without it the cart's commands would land
        # unplaced at the desktop origin, unclipped. (That is exactly the bug the
        # first cut of this shipped.) `scale` is the same integer magnification the
        # pixel path would have used, so the game FILLS its window.
        use_view = _view is not None and getattr(gc, "buf", None) is None
        if full:
            self._root_canvas.rect(cx, cy, cw, ch, _SHADOW)   # letterbox bezel
        if running:
            # The Player ticks while it's the STACK top (the running process),
            # focused or not -- editing beside a live playtest keeps it alive.
            if use_view:
                # (ox, oy, scale) is the centered game rect the pixel composite
                # used; vCW/vCH bound cls() and every draw to the cart surface, so
                # a cart cannot paint over the desk around it.
                _view(ox, oy, scale, gc.w, gc.h)
            self._content_for("desktop").draw(dt)  # Player.tick -> the game canvas
            if self._fps_chip_on():
                ws._perf_layer.draw(dt)   # game domain: inside the bracket (above)
            if use_view:
                _view()
        fb = getattr(gc, "flush_batch", None)
        if fb is not None:
            fb()
        if use_view:
            if full:
                self._win_chrome(win, focused)
            return
        # On a quiet game frame (full=False) the composite is this frame's LAST
        # framebuffer write, so a device backend may run it async and defer the
        # present (the #58 composite-overlap budget lever). A full paint draws
        # chrome AFTER it, so it must stay synchronous -- defer=not full. A
        # compiled cart's frame is shown from where it is when the root canvas
        # can (_window_frame), the FPS chip over it a rect its painter declared.
        src = self._view_src()
        if not self._window_frame(gc, ox, oy, scale, not full, src):
            self._blit_game(self._root_canvas, gc, ox, oy, scale,
                            defer=not full, src=src)
        if full:
            self._win_chrome(win, focused)

    def _blit_game(self, sc, gc, ox, oy, scale, defer=False, src=None):
        """Integer-scale the 320x240 game canvas into the desktop at (ox, oy) --
        the windowed sibling of the parent's centered composite_game. A device
        system canvas (the P4, #58) exposes a native blit_game (RGB565 scaled
        blit in one moy_gfx call) -- the Python index loops below can't run there
        (no index buffer) and would be far too slow anyway. On a RECORDING
        desktop (the web console) there is no framebuffer to copy into: ship the
        game frame as ONE scaled self-contained spr instead (the same move as
        FullscreenStackWM._composite_via_spr)."""
        bg = getattr(sc, "blit_game", None)
        if bg is not None:
            bg(gc, ox, oy, scale, defer, src)
            return
        gbuf = getattr(gc, "buf", None)
        sbuf = getattr(sc, "buf", None)
        if gbuf is None:
            return
        if sbuf is None:
            if src is not None:            # ship only the declared view region
                sx, sy, vw, vh = src
                rows = bytearray(vw * vh)
                for gy in range(vh):
                    b = (sy + gy) * gc.w + sx
                    rows[gy * vw:(gy + 1) * vw] = gbuf[b:b + vw]
                img = _Blit(vw, vh, bytes(rows), -1)
            else:
                img = _Blit(gc.w, gc.h, bytes(gbuf), -1)
            img._paint = True              # -> the compact b64 wire form (~2.4x lighter)
            sc.spr(img, ox, oy, scale)
            return
        stride = gc.w
        sx, sy, gw, gh = src if src is not None else (0, 0, gc.w, gc.h)
        sw, sh = sc.w, sc.h
        for gy in range(gh):
            grow = (sy + gy) * stride + sx
            for s in range(scale):
                dy = oy + gy * scale + s
                if dy < 0 or dy >= sh:
                    continue
                base = dy * sw + ox
                if scale == 1:
                    end = min(gw, sw - ox)
                    if end > 0:
                        sbuf[base:base + end] = gbuf[grow:grow + end]
                else:
                    out = base
                    for gx in range(gw):
                        if out + scale <= (dy + 1) * sw:
                            sbuf[out:out + scale] = bytes((gbuf[grow + gx],)) * scale
                        out += scale

    # -- input routing ----------------------------------------------------------

    def _route_key(self, i):
        """Keyboard goes to the FOCUSED window's content -- focus moves on click,
        independent of the back-stack, so typing lands in the editor while a
        playtest keeps running beside it. No focused window (the desktop root) ->
        return False and the launcher layer takes the keys. The HOME key is the
        desk's, whatever holds focus but a game (`ws.home_key`: show the
        desktop, or bring the windows back). B and Esc are the window's back
        chevron while it shows one (`_win_back`)."""
        if not self._order:
            return False
        win = self._focus_win()
        if i.pressed("home") and (win is None or win.kind != "desktop"):
            self.ws.home_key()
            return True
        if win is None or win.minimized:
            return False
        content = self._content_for(win.kind)
        if content is None:
            return False
        if (i.pressed("b") or i.pressed("stop")) and self._win_back(win):
            return True               # the window's back chevron (`_win_back`)
        if win.ctx is not None:
            self._install(win.ctx, win)
            try:
                return bool(content.handle_input(i))
            finally:
                self._install(self._root_ctx)
        return bool(content.handle_input(i))

    def _shape_sig(self):
        """The frame's window SHAPE: which windows exist, z-order, geometry,
        minimised state, focus. The ONE signature both retained-pixel voiders
        key on (window stamps AND the desk-restore streak) -- a change means
        pixels somewhere stopped being covered by what covered them last frame."""
        return (tuple(self._order), self._focus,
                tuple((w.x, w.y, w.w, w.h, w.minimized, w.kind)
                      for w in (self._wins[k] for k in self._order)))

    def _win_at(self, px, py):
        """The slot key of the topmost VISIBLE window under the pointer (incl.
        its border), or None."""
        for k in reversed(self._order):
            win = self._wins[k]
            if win.minimized:
                continue
            if win.x <= px < win.x + win.w and win.y <= py < win.y + win.h:
                return k
        return None

    def _content_flinging(self):
        """True while a window's CONTENT is coasting on a kinetic fling (#113) --
        the finger is up but the view is still moving, so the desk is as static
        as it is mid-drag. Without this the release dropped straight back onto
        the live desk render and the fling frames measured ~180ms against the
        drag's ~100ms (on glass, 2026-07-26)."""
        ws = self.ws
        for grid in (ws.projects_grid, ws.launcher):
            if grid is not None and getattr(grid, "flinging", False):
                return True
        return False

    def _route_pointer(self, px, py, click):
        ws = self.ws
        self._sync_windows()
        p = ws.pointer
        if p is None or not p.down:
            self._content_gesture = False   # finger up: the desk renders live again
        # An in-flight RESIZE follows the pointer; released -> apply the new size.
        if self._resize is not None:
            kind, ox, oy, ow, oh, _cw, _ch = self._resize
            win = self._wins.get(kind)
            if win is None:
                self._resize = None
            elif p.down:
                fs = self._fs()
                nw = max(160 * fs, ow + (px - ox))
                nh = max(90 * fs + win.title_h, oh + (py - oy))
                self._resize = (kind, ox, oy, ow, oh, nw, nh)
                # Attributed like _move_window (§3): keeps_animating's gesture
                # leg owns the repaint; _surface_signals touches the sids.
                return True
            else:
                self._resize = None
                win.saved = None                   # a manual size clears "maximized"
                self._resize_window(win, _cw, _ch)
                return True
        # An in-flight DRAG follows the pointer until release.
        if self._drag is not None:
            kind, gdx, gdy = self._drag
            win = self._wins.get(kind)
            if win is None or not p.down:
                self._drag = None
                # Restart the trail restore on RELEASE (owner report 2026-08-02:
                # "drag a window very fast and let go" draws the window twice;
                # dwell before letting go and it does not).
                #
                # _BackdropLayer.draw skips the restore once _desk_streak
                # reaches _retained_n(), and a drag saturates it. Geometry is in
                # the desk signature, and that signature is tracked SILENTLY
                # while a gesture is live (see the comment there), so release
                # finds it equal and nothing restarts the streak. The next
                # painted frame -- whenever it comes: a cursor move, the clock
                # -- then rotates onto a buffer still holding the window's old
                # position and SKIPS the restore that would erase it. Dwelling
                # works because the extra frames paint the settled window into
                # every buffer before the gesture ends.
                #
                # Caught by capturing all three framebuffers mid-symptom: each
                # held the window at a different point in the drag. The STREAK,
                # not the cache -- invalidating _backdrop_valid here is the
                # 120ms live desk re-render #155 removed. Bisected on glass:
                # this line alone fixes it; also forcing _full_debt (the first
                # attempt) measured as no difference at all and was dropped.
                self._desk_streak = 0
            else:
                self._move_window(win, px - gdx, py - gdy)
                return True
        # A pressed-but-not-yet-moved strip grab becomes a drag after _DRAG_MIN px.
        if self._drag_armed is not None:
            kind, ox, oy, wx, wy = self._drag_armed
            win = self._wins.get(kind)
            if win is None or not p.down:
                self._drag_armed = None
            elif abs(px - ox) >= _DRAG_MIN or abs(py - oy) >= _DRAG_MIN:
                self._drag_armed = None
                self._drag = (kind, ox - wx, oy - wy)
                self._seed_gesture_hist()   # #58: pre-move footprint, both buffers
                self._move_window(win, px - (ox - wx), py - (oy - wy))
                return True
        if not self._order:
            return False
        # Taskbar chips live in the OS bar row, above every window.
        if click:
            for key, rect, _label in self._chip_rects():
                if _in(px, py, rect):
                    self._chip_tap(key)
                    return True
        key = self._win_at(px, py)
        if key is None:
            # Clicking the desktop moves focus to it; a bar is not the desk,
            # so a widget's tap (a popover over the focused window) keeps it.
            # Only its OS bar is interactive; the home is not invisibly
            # present underneath.
            if (click and self._focus is not None
                    and not ws.bar_layer.bar_at("desk", px, py)):
                self._move_focus(None)
                ws._dirty = True
            return bool(self._backdrop_layer.handle_pointer(px, py, click))
        win = self._wins[key]
        # A click FOCUSES the window it lands in -- and never pops anything (the
        # owner call: looking at the editor must not end the playtest beside it).
        # Closing is explicit: the strip X, the pause menu's Quit in a focused game,
        # or an app's own exit verb.
        if click and key != self._focus:
            self._move_focus(key)
            ws._dirty = True
        focused = (key == self._focus)
        if click:
            name = self._strip_button_hit(win, px, py)
            if name is not None:
                ws._dirty = True
                if name == "close":
                    self._close_window(win.kind)
                elif name == "back":
                    self._win_back(win)
                elif name == "max":
                    self._toggle_max(win)
                elif name == "min":
                    self._minimize(win, True)
                    if focused:
                        self._move_focus(None)
                return True
            if _in(px, py, self._grip_hit_rect(win)):
                self._resize = (key, px, py, win.w, win.h, win.w, win.h)
                self._seed_gesture_hist()   # #58: pre-resize footprint, both buffers
                return True
            if py < win.y + 1 + win.title_h:
                self._drag_armed = (key, px, py, win.x, win.y)
                return True
        elif not focused:
            return True         # hovers/drags only reach the focused window
        if win.kind == "desktop":
            # Content: the Player translates system->game coords itself via
            # ws._game_xy, which this WM maps onto the window's viewport.
            return bool(self._content_for("desktop").handle_pointer(px, py, click))
        # App window: dispatch the content in WINDOW-LOCAL coords under the
        # window's layout context (the app's own bar row is its toolbar).
        cx, cy, _cw, _ch = win.content_rect()
        lx, ly = px - cx, py - cy
        content = self._content_for(win.kind)
        if content is None:
            return True
        if p.down:
            self._content_gesture = True    # scrolling INSIDE a window: the desk
                                            # is static -> _BackdropLayer caches
        self._install(win.ctx, win)
        try:
            content.handle_pointer(lx, ly, click)
        finally:
            self._install(self._root_ctx)
        return True

    def _remove_kind(self, kind):
        """SURGICALLY remove one process from the back-stack -- the windowed
        close verb. The fullscreen tiers can only pop from the top (goto
        truncates everything above), but on a desktop closing one window must
        never take unrelated windows with it (the owner-reported bug: closing
        Make also closed Settings). The launcher root is never removable."""
        if self.stack.remove(kind):
            self._on_nav()

    def close_player(self):
        """Close the playtest window (its X, or the pause menu's Quit routed through
        ws._exit_to_caller): remove ONLY the player from the stack, then hand
        focus back to the run CALLER's window when it's open (the Editor for a
        PLAY run) -- the launch-and-return contract, without collateral pops."""
        ws = self.ws
        self._remove_kind("desktop")
        self._sync_windows()
        if (ws.returns.caller() == EDITOR
                and "menu" in self._order):
            self._move_focus("menu")
        ws._dirty = True

    def close_window_kind(self, kind):
        """Close one window by its process kind -- the console's windowed exit
        hook (_exit_to_caller / _exit_settings route here) and the title-strip
        X's dispatch: that window alone closes.

        (#111) autosave-only: the strip X is an exit path like any other, so
        every persistent surface hard-commits here BEFORE its window/level pops
        -- otherwise a kid mid-idle-debounce who drags-closes the window (rather
        than using the app's own CLOSE/context-X) would silently lose the last
        edit."""
        ws = self.ws
        ws._dirty = True
        if kind == "storybook":
            ws.storybook_app._commit_deck()   # same rule for an open story
        if kind == "artwork":
            ws.artwork_app._save()            # same rule for the open drawing
        if kind == "appearance":
            ws.look.commit()                  # and for a change still settling
        if kind == "settings":
            ws.settings_layer.commit_name()   # and for a Name still being typed
        if kind == "menu":
            # The "menu" WM kind is shared by the Editor AND the icon-theme editor
            # (Settings -> EDIT ICONS, paint_layer.ThemeLayer -- it spawns on the
            # same back-stack slot); each has its own commit verb.
            if ws._editing_icons:
                ws.look.save_icons()
            else:
                ws.editor_app.save_current()      # the Editor's active tab
        if kind == "desktop":
            self.close_player()
        else:
            self._remove_kind(kind)

    def _close_window(self, kind):
        self.close_window_kind(kind)

    def _move_window(self, win, nx, ny):
        # An ATTRIBUTED placement write (surface model §3): no un-attributed
        # ws._dirty here -- the repaint is guaranteed by keeps_animating's
        # gesture leg + the pointer leg, and _surface_signals attributes the
        # gesture to the moving window's sid. This is what lets the desk/bar/
        # other windows SKIP during a drag (the 70ms->3ms wasm frame class).
        # The content rect clamps it: a window may hang off the glass's left
        # or right edge with 40 columns still on the desk, and never crosses
        # a bar -- a side bar is a wall, as the top and bottom ones are.
        full = self._root_canvas
        fs = self._fs()
        dx, dy, dw, dh = self._desk_rect()
        lo = dx if dx > 0 else -win.w + 40 * fs
        hi = dx + dw - win.w if dx + dw < full.w else full.w - 40 * fs
        win.x = max(lo, min(nx, max(lo, hi)))
        win.y = max(dy, min(ny, dy + dh - 24 * fs))
