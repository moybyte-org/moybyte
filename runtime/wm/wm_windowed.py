# Map (grep -n a name to jump there):
#   WindowedWM                    the windowed tier's presentation of the back-stack
#   WindowedWM.draw_stack         the frame router's draw list
#   WindowedWM.keys_to_cart       focus-aware key routing
#   WindowedWM.close_window_kind  close one window by its kind
#   WindowedWM.enter_console      the desk's PLAY: park the windows, the home fullscreen
#   WindowedWM.leave_console      the home's X: the route's restore op, the windows back
#   WindowedWM.release_cache      the fit check's release: parked stamps, then the home's
#   WindowedWM.show_desk          the HOME key on the desk: minimise all, or restore all
"""The WINDOWED window manager -- the big-screen / P4 "One" presentation tier
(#73 / #58 "Desktop look"; spec docs/shell_ux_v1.md §3's tier table).

`WindowedWM` presents the SAME everything-is-a-process shell as an OS desktop,
Picotron-style (owner direction, 2026-07-08):

  * two modes, `mode` (docs/theme_and_shell_2026-11.md §8.1): DESKTOP mode is the
    DESKTOP root (live wallpaper + the ONE full-width OS bar) beneath the
    process windows; CONSOLE mode is the fullscreen tier's presentation over
    the home, the desk's windows parked outside the back-stack until the
    home's X brings them back;
  * every process pushed above it (the home window / Editor / Settings / update /
    a running cart) is a floating WINDOW: a WM-drawn TITLE STRIP (title + minimize /
    maximize / close), a border + drop shadow, draggable by the strip (a
    full-screen one by its row, which restores it) and resizable from every
    edge and corner;
  * a window's body is PURELY the app: in windowed mode the zoned bar suppresses
    its OS widgets (ws.windowed_chrome -- see bar_layer.py), so
    an app window's own bar row is just its toolbar (the Editor's tab ladder),
    never a copied taskbar;
  * a RUNNING CART composites integer-scaled and centered in its window -- the
    game itself never draws chrome, exactly as on the fullscreen tiers, and the
    editor stays VISIBLE beneath a playtest (spec §3's canonical picture);
  * the desk's PROJECTS is the home in a window ("projects", the console
    home's design, opened on Make); a Make pick opens the Editor in a window
    of its own above it, and the Editor's X closes back to it -- one window
    per back-stack kind; the Editor opened from the console-mode home leaves
    console mode for the desk and opens there as a window too;
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

Siblings hold what is not the WM's state machine: `wm_desk.py` is the desk
root layer (wallpaper + bar + icon column, the retained-backdrop cache),
`wm_parts.py` the window record, its layout context and the layers the draw
stacks hold, and four mixins, its bases in this order:
`wm_paint.py`'s `WindowPaint` (the window buffers, the game's composite, the
windows' draw, the shown ring), `wm_chrome.py`'s `WindowChrome` (title
strips, grip, taskbar chips), `wm_overlays.py`'s `WindowOverlays` (the
popover, pause and wash overlays) and `wm_route.py`'s `WindowRoute` (the
keys and the pointer). The draw stack's choice of what a frame paints stays
here, with the window table, the modes and the window verbs.
"""

try:
    from wm import FullscreenStackWM
    from widgets import _ticks_ms, _ticks_diff, _err_text
    from wm_desk import _BackdropLayer
    from wm_parts import (_LayoutCtx, _Win, _Parked, _WindowStackLayer, _RepeatLayer,
                          _PlayerWindowLayer, _PopoverUnderLayer, _PopoverOverLayer,
                          MODE_MARK, _SHOWN)
    from wm_chrome import WindowChrome, RZ_L, RZ_R, RZ_T, RZ_B, RZ_CURSOR
    from wm_overlays import WindowOverlays
    from wm_paint import WindowPaint
    from wm_route import WindowRoute
    from moy_spine import EDITOR
    from shell_layout import WIDGETS as _LAYOUT_WIDGETS
    from notifications import NOTES as _NOTES
    import layout_base as _layout_base
    import ui_draw as _ui_draw
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.wm import FullscreenStackWM
    from runtime.widgets import _ticks_ms, _ticks_diff, _err_text
    from runtime.wm_desk import _BackdropLayer
    from runtime.wm_parts import (_LayoutCtx, _Win, _Parked, _WindowStackLayer, _RepeatLayer,
                                  _PlayerWindowLayer, _PopoverUnderLayer, _PopoverOverLayer,
                                  MODE_MARK, _SHOWN)
    from runtime.wm_chrome import (WindowChrome, RZ_L, RZ_R, RZ_T, RZ_B,
                                   RZ_CURSOR)
    from runtime.wm_overlays import WindowOverlays
    from runtime.wm_paint import WindowPaint
    from runtime.wm_route import WindowRoute
    from runtime.moy_spine import EDITOR
    from runtime.shell_layout import WIDGETS as _LAYOUT_WIDGETS
    from runtime.notifications import NOTES as _NOTES
    from runtime import layout_base as _layout_base
    from runtime import ui_draw as _ui_draw


# The desk's default OUTER sizes of the apps the 2026-10 look sizes to their
# content (DESIGN.md "The sizes"; the `*desktop*` targets), in glass pixels
# at chrome size 1. Every other window takes `_win_size`'s formulas.
DESK_SIZES = {"files": (548, 534), "calc": (344, 488), "getcarts": (900, 536),
              "notes": (900, 536), "artwork": (900, 536), "myart": (900, 536),
              "storybook": (900, 536)}

# A dev playtest's scale where the desk holds it (D-03.4).
_DEV_SCALE = 2


class WindowedWM(WindowPaint, WindowChrome, WindowOverlays, WindowRoute,
                 FullscreenStackWM):
    """The TWO-MODE presentation of the back-stack (docs/theme_and_shell_2026-11.md
    §8.1): in DESKTOP mode the DESK (stack kind "desk") is the floor --
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
    # One voucher per region: the shown ring (`_shown`) speaks for a
    # window's content here, never the content's own ring.
    CONTENT_RING = False
    # `chips` (the taskbar's window chips) has a value in desktop mode only.
    LAYOUT_CATALOGUE = {"desktop": _LAYOUT_WIDGETS,
                        "console": FullscreenStackWM.LAYOUT_CATALOGUE["console"]}
    # The base's verbs the frame reads every frame, bound on the class: on
    # MicroPython a method the class lacks is looked up through the bases in
    # order, and the mixins stand before the base.
    _ensure_stack = FullscreenStackWM._ensure_stack
    _overlay_sig = FullscreenStackWM._overlay_sig
    overlay_stack = FullscreenStackWM.overlay_stack
    visible_stack_rev = FullscreenStackWM.visible_stack_rev
    top_kind = FullscreenStackWM.top_kind
    top_is = FullscreenStackWM.top_is
    top_is_player = FullscreenStackWM.top_is_player

    def __init__(self, ws):
        FullscreenStackWM.__init__(self, ws)
        if ws._sys_canvas is None:
            raise ValueError("WindowedWM needs a distinct (big) system canvas")
        # THE MODE (docs/theme_and_shell_2026-11.md §8.1): "desktop" -- the desk
        # and its windows -- or "console", FullscreenStackWM's presentation
        # over the home. Every tier-sensitive branch reads it, never the stack
        # shape. Console until the boot's open_desk; the desk's PLAY enters
        # console mode and the home's X leaves it. ws.windowed_chrome follows
        # it through desk_open().
        self.mode = "console"
        self._app_src = None          # a window-bound app's view of its canvas (_view_src)
        self._app_wc = None           # app_window_content's last (rect, desk w, h, answer)
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
        self._prewarm_failed = set()  # window kinds whose prewarm draw has been reported
        # Two orders of the same slots: `_kinds` is the back-stack's above the
        # desk (the order the windows opened in, which the chips keep), and
        # `_order` is the Z-ORDER, bottom -> top, which the focus raises
        # through: every draw and hit-test walks `_order`.
        self._kinds = []
        self._order = []
        self._front = None            # the kind a goto() asked to bring forward
        self._player_under = False    # a window above the game window overlaps it
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
        self._rest = None             # a released drag's tuple, and the painted
        self._rest_end = 0            # frame it ends before (`_gesture`)
        self._grab = 0                # the last press was the WM's: 1 while it
                                      # is held, 2 once released; no content
                                      # has had it (_content_static)
        self._held_in = None          # the window a held press went down in
        self._restored = False        # what the last cache restore covered: a
                                      # rect, None (all of it) or False (nothing)
        self._restored_f = -1         # ...and the frame it was this frame's in
        self._laid = []               # the rects of it the desk was laid in
        self._clock_held = [None, -1, 0]  # the desk clock: text, frame, in a row
        self._foot_gen = None         # the look the windows' feet were sized under
        self._root_marks = None       # ui.marks' active before a window's install
        self._installed = None        # the window whose layouts are installed
        self._drag_armed = None       # (kind, ox, oy, wx, wy) press-in-strip origin
        self._resize = None           # in flight: (kind, press x, press y, start w,
                                      # start h, rubber w, rubber h, edges (RZ_*),
                                      # start x, start y, rubber x, rubber y)
        self._ctx_switching = False   # reentrancy guard for internal _relayout runs
        self._in_draw = False         # the windows draw: a relayout waits for its end
        self._relayout_owed = False
        self._direct_root = False     # a window's content draws into the root now
        self._scale_owed = False      # ...and changed the font scale the root takes after
        # Quiet-frame fast path (#58): the stand-in stacks + the ping-pong
        # full-paint debt. After ANY full repaint the NEXT drawn frame must also
        # bring its buffer to it -- a double-buffered panel alternates physical
        # buffers, so one full paint only updates ONE of them; a partial frame on
        # the other would swap in its pre-change desktop (visible flicker between
        # old/new chrome). A still desk's debt frame copies the last painted
        # frame's buffer (`_repeat_stack`, surface model §14.6).
        # The FPS chip (sig bit 2, show_fps, default OFF) draws on the GAME canvas
        # before the window blit, so it rides the quiet path too -- mirroring the
        # full stack's [.., perf, windows] order.
        _pw = _PlayerWindowLayer(self)
        self._quiet_stack = [_pw]
        self._full_debt = 0
        # A debt frame of a still desk repeats the last painted frame: its
        # buffer copied, not the desk painted again (§14.6). `_painted_f` is
        # the frame the last draw_stack routed.
        self._repeat_stack = [_RepeatLayer(self)]
        self._painted_f = -2
        self._repeats_n = 0
        # A quiet frame still owing a buffer the last change (a full frame
        # came before it) copies the last painted frame whole, which holds
        # this frame's picture but for the game, then draws the quiet stack
        # over it: the stacks set up with the popover's layers.
        self._lead_refused = False
        self._wash_all = False        # this frame is the riding scrim's first (draw_stack)
        # ...and a popover's ride frame, while its state is as the last
        # painted frame left it: `_pop_seq` counts the popover's changes,
        # `_pop_seq_f` is the count the last draw_stack saw, and
        # `_repeating` says this frame repeats the last one. The scrim
        # counts its own (`_wash_seq`): a card still settling under a
        # scrim that is not changes the card's rect alone (`_wash_repeats`).
        self._pop_seq = 0
        self._pop_seq_f = 0
        self._repeating = False
        self._wash_seq = 0
        self._wash_seq_f = 0
        self._wash_repeats = False
        self._pop_wash_f = -1         # the frame whose wash was a copy
        # THE POPOVER ON THE QUIET STACK (docs/theme_and_shell_2026-11.md §8.6): a
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
        _lead = _RepeatLayer(self, lead=True)
        self._lead_quiet_stack = [_lead, _pw]
        self._lead_pop_stack = [_lead, _pu, _pw, _po]
        self._pop_slots = [[None, None, 0, False], [None, None, 0, False]]
        self._pop_cur = None          # the open popover's slot, or None
        # The system menu's scrim on a still desk rides as one more slot,
        # [layer, rect, debt, captured, up]: the pixels the wash covers,
        # taken before its first wash; while `up` each retained buffer is
        # owed the wash (`debt` frames), once down the snapshot put back.
        self._pop_wash = [None, None, 0, False, False]
        # ...and the focused window under it, which the menu taking the keys
        # shows without its focus (ui.marks): [window, its buffer as it was,
        # that buffer's _buf_stale, _lag, _marked], put back at the close.
        self._wash_win = [None, None, False, [0, 0, 0, 0, 0], False]
        # A component popover (a bar widget's card) takes the keys from the
        # focused window too: its stamp, drawn without its focus mark, owed
        # to every buffer while it opens, and with it again once it closes
        # (`_pop_marks`); `_mark_kept`: the wash slot above holds its buffer
        # as the popover found it.
        self._mark_owed = 0
        self._mark_kept = False
        # The pause menu's card over its riding scrim: the frames still owed
        # its paint, and the rect its paints have covered since it opened.
        self._pause_owed = 0
        self._pause_span = [0, 0, 0, 0]
        self._pop_game_next = False   # this frame's popover stack composites the game
        self._pop_over_game = False   # ...and does it overlap the playtest's window
        # Its face, copied once painted while it overlaps a playtest: the
        # quiet frames put it back over the game instead of painting it.
        self._pop_face = None
        self._pop_face_ok = False
        # The idle frame's scrim snapshot found no room (`prealloc`): it is
        # not asked for again until the WM gives a layer back.
        self._prealloc_refused = False
        # Retained desktop-backdrop cache (#58 drag perf): during a DRAG/RESIZE the
        # wallpaper + OS bar are invariant, so the first such frame captures
        # their composite and every later one blits it (see _BackdropLayer). Its
        # own full-screen off-screen buffer, allocated lazily on the first drag and
        # dropped on relayout (the size can change with the font scale).
        self._backdrop_layer = _BackdropLayer(self)
        self._backdrop = None
        self._backdrop_valid = False
        self._desk_tile = False           # the system button's state the cache holds
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
        self._scrim_was = False           # a modal's scrim was up last frame
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
        # THE SHOWN RING (docs/surface_model_v1.md §14.3): what each physical
        # buffer of the root holds of the focused window's content. One entry
        # a painted frame, [frame, win, known, x0, y0, x1, y1): `win` when the
        # frame left that buffer's content rect holding the window's page
        # and nothing over it (None when it could not say), and the part of
        # the page that frame changed (known 0: unknown). A buffer N frames
        # back then holds the page but for what the N-1 frames since changed.
        # Then where the page lay, [cx, cy, cw, ch, look_gen), and whether
        # the frame changed nothing on the glass outside it (`_alone_f`):
        # the debt a buffer owes is then the rows those frames changed.
        self._shown = [[-1, None, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]
                       for _i in range(_SHOWN + 1)]
        self._outside_f = -1          # the frame that drew outside the focused window
        self._rows = [0, 0, 0]        # the debt's rows [y0, y1, whole]
        self._refills = 0             # window buffers refilled from the glass
        self._debt_px = 0             # pixels the debt frames copied
        self._own = [0, 0, 0, 0, 0]   # this frame's change, [x0, y0, x1, y1, known]
        self._u = [0, 0, 0, 0]        # a target's lag behind the page
        self._sc = [0, 0, 0, 0]       # scratch for the next frame's chain
        self._part = [0, 0, 0, 0, 0]  # [x0, y0, x1, y1, set]: the root rect the
                                      # window changed, where narrower than it
        self._under_hit = False       # a window under the top one drew into it
        self._settle_f = -1           # the frame a settle asked for

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
            # The desk's cache stays good: its signature carries the layout's
            # content rect, so a switch that moves the desk's bars renders it
            # again, and leave_console restarts its streak.
            self.content_gen += 1
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

    def _install(self, ctx, win=None, draws=True):
        # The focus mark draws only in the focused window (ui.marks), and in
        # none while a popover or the delete confirm above the windows holds
        # the keys: one a screen. `draws`: the window's buffer is drawn
        # under it, so it holds the mark as set (`_marked`); input handled
        # in its context leaves the buffer as it is.
        # The root context takes back the marks it had before the windows
        # drew -- off while the console holds them under a menu -- so what
        # the root draws after them marks only where it may.
        self._installed = win
        if win is None:
            ctx.install(self.ws)
            if self._root_marks is not None:
                _ui_draw.marks(self._root_marks)
                self._root_marks = None
        else:
            if self._root_marks is None:
                self._root_marks = _ui_draw.marks()[0]
            cx, cy, _cw, _ch = win.content_rect()
            ctx.install(self.ws, (cx, cy))
            word = self._mark_word(win)
            if draws:
                win._marked = word and _ui_draw.input_kind() != "touch"
            _ui_draw.marks(word)

    def _mark_word(self, win):
        """The shell's word on `win`'s focus mark (ui.marks): the focused
        window's, while no menu or confirm holds the keys (one a screen)."""
        return (self._wins.get(self._focus) is win
                and not self.popover_takes_keys()
                and not self.ws.confirm.open)

    def _marks_for(self, win):
        """Whether `win`'s content shows its focus mark now: the shell's
        word, under an input that shows focus (none does under touch)."""
        return self._mark_word(win) and _ui_draw.input_kind() != "touch"

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

    # -- the two modes (docs/theme_and_shell_2026-11.md §8.1) ----------------------

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
        self._rest = None
        self._grab = 0
        self._held_in = None
        self._content_gesture = False
        self._gesture_hist = []
        self.mode = "console"
        self.stack.goto("launcher")
        self._on_nav()
        ws._dirty = True

    def leave_console(self, game=False):
        """The home's X: put the parked kinds back above the desk with the
        route's restore op (no process re-opens) and the window records with
        them. A record whose stamp was released re-renders once, the path a
        resize takes; one whose kind was re-opened in console mode re-renders
        once to show what that process holds now. The Editor's project is the
        workspace, and a run in console mode swaps it: a parked Editor whose
        project went that way does not come back. `game`: the pause card's
        Play in a window, the game on the home going on above them, its
        window made as a desk play's."""
        if self.mode != "console":
            return
        ws = self.ws
        if self.stack.depth() > 1 and not game:
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
        self.stack.restore(["desk"] + kinds + (["desktop"] if game else []))
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
        self._desk_streak = 0             # every buffer holds the home
        ws._dirty = MODE_MARK

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
        retained frame and the layer a dialog's page is held in."""
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
            if self._pop_wash[0] is not None:
                self._release_layer(self._pop_wash[0])
                self._pop_wash[0] = None
                held = True
            if self._wash_win[1] is not None:
                self._release_layer(self._wash_win[1])
                self._wash_win[1] = None
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

    def window_kinds(self):
        """The kinds open as windows on the desk, bottom -> top (none in
        console mode)."""
        return self._slots()

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
                    self.ws.procs.dropped(g)    # a running set's game ends with it
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

    def set_play_intent(self, intent):
        """Record WHY the next cart run is starting -- "dev" (a playtest launched
        from the Editor) or "play" (the desk/home). Only the player window's
        DEFAULT size reads it; see _win_size."""
        self._play_intent = intent if intent == "dev" else "play"
        win = self._wins.get("desktop")
        if win is not None:
            win.own = False               # a new run is its caller's again

    def _win_size(self, key, full, fs):
        """Default window OUTER size per window slot: on a desk, the apps the
        2026-10 look sizes to their content take that size (`DESK_SIZES`)."""
        th = _layout_base.metric("title", fs)
        if _layout_base.metric_class() == _layout_base.DESK and key in DESK_SIZES:
            n = _layout_base.chrome_size(fs)
            r = _layout_base.render_scale()
            return (DESK_SIZES[key][0] * n // r, DESK_SIZES[key][1] * n // r)
        if key == "desktop" and self.ws.app_window_canvas:
            # a window-bound app cart (Notes): its designed size, by the
            # last part of its id, else the default panel below
            cid = str((self.ws.cart or {}).get("id") or "")
            key = cid[cid.rfind(".") + 1:] or key
            if _layout_base.metric_class() == _layout_base.DESK and key in DESK_SIZES:
                n = _layout_base.chrome_size(fs)
                r = _layout_base.render_scale()
                return (DESK_SIZES[key][0] * n // r, DESK_SIZES[key][1] * n // r)
            key = "app"
        if key == "desktop":
            # A dev playtest (#178) opens at 2x where the desk holds it, else
            # at native size (the owner's D-03.4, 2026-10-19): the game is the
            # point of the window. A desk play takes the largest whole scale
            # that fits. The frame fits the game exactly: its border, its
            # title strip, and the rows its rounded bottom corners take (the
            # foot) under the game and at each side of it (`_win_edge`), which
            # the content rect leaves out.
            foot = (_ui_draw.frame_geom(full.w, full.h, th)[1]
                    if _ui_draw.depth()[12] else 0)
            e = max(foot, self._frame_edge())
            fit = max(1, min((full.w - 24 - 2 * e) // self.ws.canvas.w,
                             (self._desk_rect()[3] - 24 - th - e)
                             // self.ws.canvas.h))
            s = min(fit, _DEV_SCALE) if self._play_intent == "dev" else fit
            return (self.ws.canvas.w * s + 2 + 2 * e,
                    self.ws.canvas.h * s + 2 + th + e)
        if key in ("menu", "projects"):
            return (full.w - full.w // 8, full.h - full.h // 10)
        if key in ("artwork", "appearance"):
            return (full.w - full.w // 8, full.h - full.h // 10)
        if key == "update":
            return (full.w // 2, full.h // 2)
        # Settings + default: a compact floating panel (the Picotron proportion),
        # never a near-fullscreen sheet.
        return (max(340 * fs, full.w * 2 // 5), max(300 * fs, full.h * 3 // 5))

    def _open_size(self, key, fs):
        """(w, h) a window of slot `key` opens at: its default size, clamped
        so the whole window, grip included, fits the desk beside the OS bar
        (a default can exceed a small desktop, e.g. the fs-scaled Settings
        minimum at font 2x on 600px)."""
        w, h = self._win_size(key, self._root_canvas, fs)
        _dx, _dy, dw, dh = self._desk_rect()
        return min(w, dw - 4 * fs), min(h, dh - 8 * fs)

    def _make_window(self, key, kind, depth):
        fs = self._fs()
        w, h = self._open_size(key, fs)
        title_h = _layout_base.metric("title", fs)
        dx, dy, dw, dh = self._desk_rect()
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
        if self._opens_full(kind):
            win.saved = (x, y, w, h)
            self._set_full(win)
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
        (`Wallpaper.subject`, docs/theme_and_shell_2026-11.md §3.9): where the
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
        a content error leaves the buffer blank and stale, reported once per
        window kind)."""
        if win.buf is None or win.ctx is None:
            return
        content = self._content_for(win.kind)
        if content is None:
            return
        self._install(win.ctx, win)
        try:
            content.draw(0)
            self._row_over(win, win.buf)
            win._buf_stale = False
            win._look_gen = self.ws.look.look_gen
        except Exception as exc:  # noqa: BLE001 -- a prewarm never ends a session
            if win.kind not in self._prewarm_failed:
                self._prewarm_failed.add(win.kind)
                print("Moybyte window draw failed:", win.kind, _err_text(exc))
        finally:
            self._install(self._root_ctx)

    def in_window(self, kind, fn):
        """Run `fn()` in window `kind`'s layout context -- its canvas and its
        layouts, as its content draws -- drawing nothing: work a content does
        ahead of its next draw (`EditorDesk.warm_step`). False where no such
        window has a buffer."""
        win = self._wins.get(kind)
        if win is None or win.buf is None or win.ctx is None or self.mode == "console":
            return False
        self._install(win.ctx, win, draws=False)
        try:
            fn()
        finally:
            self._install(self._root_ctx)
        return True

    def _row_over(self, win, cv, held=False, kept=False):
        """Where `win` is full screen and its app's band is its row: the
        row's head and controls over the band its content just drew on `cv`,
        so they ride the content's stamp and its freeze. For a content that
        speaks the shown protocol (`held`), whether the row's pixels moved;
        `kept`: its draw went into the window's buffer as that content's
        last draw left it, so where the row is as it was and the draw changed
        none of the row's band (`changed`), the row is the buffer's already
        (painted again, its controls would blend over themselves)."""
        if win.saved is not None and not win.row and win.kind != "desktop":
            t = self._full_title(win)
            if not held:
                # painted over a whole draw: a held frame after it finds the
                # row as this one painted it
                self._row_note(win, t)
                self._paint_row(win, cv, t)
                return
            if self._row_note(win, t):
                win._row_moved = True     # the row's pixels are not the ones before
            elif kept and self._row_clear(win):
                return
            self._paint_row(win, cv, t)

    def _row_note(self, win, t):
        """Record the full-screen row about to be painted over `win`, its
        title `t`, in `win._row_sig`: whether it differs from the one last
        painted."""
        s = win._row_sig
        w, h, b = win.w, win.h, self._win_btns(win)
        g, fs = self.ws.look.look_gen, self._fs()
        if s[0] == w and s[1] == h and s[2] == t and s[3] == b and s[4] == g and s[5] == fs:
            return False
        s[0] = w
        s[1] = h
        s[2] = t
        s[3] = b
        s[4] = g
        s[5] = fs
        return True

    def _row_clear(self, win, r=None):
        """Whether what the focused content's last draw changed (`changed`),
        or the rect `r` in its coordinates, is known and reaches none of
        `win`'s full-screen row."""
        if r is None:
            c = self._content_for(win.kind)
            r = c.changed() if c is not None else None
        if r is None:
            return False
        if r[2] <= r[0] or r[3] <= r[1]:
            return True
        cx, cy, _cw, _ch = win.content_rect()
        rx, ry, rw, rh = self._row_rect(win)
        rx -= cx
        ry -= cy
        return r[1] >= ry + rh or r[3] <= ry or r[0] >= rx + rw or r[2] <= rx

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
        win.edge = self._win_edge(win)
        if win.saved is not None:
            # where the corner's controls start, and the game stops short of
            right = [r[0] for _n, r, side in self._frame_buttons(win) if side]
            win.corner = (min(right) if right else win.x + win.w) - 8 * self._fs()
        if win.kind == "desktop":
            win.buf = None
            win.ctx = None
            if self.ws.app_window_canvas:
                self.ws._surface_write()  # the app's content rect is its window's
            return
        full = self._root_canvas
        _x, _y, cw, ch = win.content_rect()
        cw, ch = max(64, cw), max(40, ch)
        self._drop_win(win)             # a resize's old buffer, freed first
        win.buf = full.new_layer(cw, ch)
        win.ctx = self._make_ctx(win.buf, win.inner_foot())
        win._buf_stale = True         # blank until a live render/_prewarm fills it
        win._lag[4] = 0

    def _resize_window(self, win, w, h):
        """Apply a new OUTER size (a resize's release / a restore): clamp to a
        usable minimum, then rebuild the content buffer + layout context so the
        app reflows to the new window size."""
        dx, dy, max_w, max_h = self._desk_rect()
        full = self._root_canvas
        # A right or bottom bar is a wall a resize stops at.
        if dx + max_w < full.w:
            max_w = max(1, min(max_w, dx + max_w - win.x))
        if dy + max_h < full.h:
            max_h = max(1, min(max_h, dy + max_h - win.y))
        min_w, min_h = self._min_size(win, max_w, max_h)
        win.w = max(min_w, min(w, max_w))
        win.h = max(min_h, min(h, max_h))
        self._build_content(win)
        self.ws._dirty = True

    def _min_size(self, win, max_w, max_h):
        """The smallest outer size `win` takes. Min-size convention (ui.py +
        the app API): a registered app declares its resize minimum
        (fs-scaled) at register_app time -- the minimums live with the app,
        not in a WM if-ladder. The cap at (max_w, max_h) keeps the same apps
        usable on a 320x240 windowed host."""
        fs = self._fs()
        ms = self.ws.app_min_size(win.kind) \
            if hasattr(self.ws, "app_min_size") else None
        if ms is not None:
            return (min(max_w, ms[0] * fs), min(max_h, ms[1] * fs))
        return (160 * fs, 90 * fs + win.title_h)

    def _rubber(self, win, edges, wx, wy, ow, oh, ddx, ddy):
        """The rect (x, y, w, h) a resize by (ddx, ddy) of the window that
        stood at (wx, wy, ow, oh) gives it: each moving edge stops at the
        desk's edge and where the window would be smaller than its minimum."""
        dx, dy, dw, dh = self._desk_rect()
        min_w, min_h = self._min_size(win, dw, dh)
        x0, y0, x1, y1 = wx, wy, wx + ow, wy + oh
        if edges & RZ_L:
            x0 = min(x1 - min_w, max(dx, x0 + ddx))
        elif edges & RZ_R:
            x1 = max(x0 + min_w, min(dx + dw, x1 + ddx))
        if edges & RZ_T:
            y0 = min(y1 - min_h, max(dy, y0 + ddy))
        elif edges & RZ_B:
            y1 = max(y0 + min_h, min(dy + dh, y1 + ddy))
        return (x0, y0, x1 - x0, y1 - y0)

    def _resize_zone_at(self, px, py):
        """(key, edges) of the window whose resize zone holds (px, py), the
        top one first; None where none does, a window above holds the point
        (its strip buttons included), or it is off the desk (a bar's)."""
        dx, dy, dw, dh = self._desk_rect()
        if not (dx <= px < dx + dw and dy <= py < dy + dh):
            return None
        order = self._order
        for i in range(len(order) - 1, -1, -1):
            key = order[i]
            win = self._wins.get(key)
            if win is None or win.minimized or win.docked is not None:
                continue
            inside = (win.x <= px < win.x + win.w
                      and win.y <= py < win.y + win.h)
            if inside and (win.saved is not None
                           or self._strip_button_hit(win, px, py) is not None):
                return None
            m = self._resize_edges(win, px, py)
            if m:
                return (key, m)
            if inside:
                return None
        return None

    def cursor_shape(self):
        """The pointer's shape: a resize's arrows over a window's resize
        zone and while one is in flight (`RZ_CURSOR`), else None."""
        g = self._resize
        if g is not None:
            return RZ_CURSOR.get(g[7])
        p = self.ws.pointer
        if self.mode != "desktop" or p is None:
            return None
        hit = self._resize_zone_at(p.x, p.y)
        return RZ_CURSOR.get(hit[1]) if hit is not None else None

    def _restore_under(self, win, px, py):
        """A full-screen window restored for a drag from its row at
        (px, py): its last normal size (the size it opened at, where it
        opened full), the press at the same point across its title and as
        far down its strip. Returns where it stands, before the desk
        clamps it."""
        _sx, _sy, w, h = win.saved
        row = self._row_h()
        fx = (px - win.x) * w // max(1, win.w)
        fy = (py - win.y) * win.title_h // max(1, row)
        win.saved = None
        win.row = 0
        x, y = px - fx, py - fy
        win.x, win.y = x, y
        self._resize_window(win, w, h)
        self._prewarm(win)
        self._remember_full(win)
        self._move_window(win, x, y)
        return x, y

    # -- full screen (docs/media/look_2026-10/MAXIMISE_VARIANTS.md, V2) -------
    #
    # Maximise is full screen: the window takes the whole glass, the desk's
    # bar, its chips and every window under it covered; no frame, no strip.
    # Its row is the top of the glass: the app's own band where its content
    # draws one (the Editor's, Settings', the home window's), the band's
    # ground under the WM's row otherwise, with the window's icon and title
    # at its head and minimise, restore and close at its right, drawn and
    # answered as every strip's. A game's window is the game alone, at the
    # largest whole scale the glass holds, on black, with no buttons (owner,
    # 2026-10-31: play is full screen, as Big Picture's); the player's
    # window running a window-bound app cart (Notes) is an app's, under the
    # WM's row (`_is_game`).

    def _row_h(self):
        """The full-screen row's height: the band's (a window's bar)."""
        return _layout_base.bar_rows(self._root_ctx.layout.cs)

    def _band_is_row(self, kind):
        """Whether `kind`'s content draws the band a window's layouts take
        (`BarLayer.lends_band`), which full screen makes its row."""
        return kind != "desktop" and self.ws.bar_layer.lends_band(kind)

    def _set_full(self, win):
        """`win` on the whole glass, its row above the content where its band
        is not the row (none for a game)."""
        full = self._root_canvas
        win.x, win.y, win.w, win.h = 0, 0, full.w, full.h
        win.row = (0 if self._is_game(win) or self._band_is_row(win.kind)
                   else self._row_h())

    def _on_row_head(self, win, px, py):
        """Whether (px, py) is on a full-screen app window's row where the
        WM draws it: the whole row above the content, or, where the app's
        band is the row, its head -- the icon and title left of the zone
        the band is lent (`band_span`)."""
        if win.saved is None or self._is_game(win) \
                or not (win.y <= py < win.y + self._row_h()):
            return False
        return bool(win.row) or px < self._full_slot(win)

    def _toggle_max(self, win):
        """Full screen <-> restored: the whole glass, remembering the rect a
        restore puts back unchanged; the state is remembered per board."""
        if win.saved is None:
            win.saved = (win.x, win.y, win.w, win.h)
            self._set_full(win)
            self._build_content(win)
        else:
            x, y, w, h = win.saved
            win.saved = None
            win.row = 0
            win.x, win.y = x, y
            self._resize_window(win, w, h)
        self._remember_full(win)
        self.ws._dirty = True

    def full_key(self):
        """F11 (a keyboard's `full` button, the host's and the page's F11):
        the focused desk window full screen, or back. Esc never leaves full
        screen -- a game and the Editor's panes take it. False where no
        window holds the focus."""
        win = self._focus_win()
        if self.mode != "desktop" or win is None or win.minimized:
            return False
        if win.docked is not None:
            win = self._wins.get("menu")    # a docked game: its host's
            if win is None:
                return False
        self._toggle_max(win)
        return True

    def _remember_full(self, win):
        """An app window's full screen, per board (`win_full`, the owner's
        E-04): the next time it opens it opens so."""
        if win.kind == "desktop":
            return
        got = self.ws.system.get("win_full", None)
        got = dict(got) if isinstance(got, dict) else {}
        if got.get(win.kind) != win.full():
            got[win.kind] = win.full()
            self.ws.system.set("win_full", got)

    def _opens_full(self, kind):
        """Whether a window of `kind` opens full screen: as it was last left
        on this board, and the Editor so the first time, on a desk (E-04)."""
        if kind == "desktop":
            # Play is full screen on the desk (owner, 2026-10-31): a desk
            # play opens so, with no buttons; an Editor's playtest and a
            # window-bound app cart open as windows.
            return self._play_intent == "play" and not self.ws.app_window_canvas
        if _layout_base.metric_class() != _layout_base.DESK:
            return False
        got = self.ws.system.get("win_full", None)
        if isinstance(got, dict) and kind in got:
            return bool(got[kind])
        return kind == "menu"

    def full_cover(self):
        """The z-slot of the topmost shown full-screen window, -1 for none:
        it and the windows above it are all the glass shows."""
        order = self._order
        for i in range(len(order) - 1, -1, -1):
            win = self._wins.get(order[i])
            if win is not None and not win.minimized and win.saved is not None:
                return i
        return -1

    def _full_slot(self, win):
        """Where a full-screen row's title slot ends (EDITOR_LAYOUT.md's
        frame: 144 pixels from the icon at 1024 wide, 112 narrower), past a
        back chevron the row shows."""
        r = _layout_base.render_scale()
        n = _layout_base.chrome_size(self._fs())
        slot = (144 if win.w * r >= 1024 else 112) * n // r
        btns = self._win_btns(win) | _ui_draw.FRAME_FULL
        icon = _ui_draw.frame_icon(self._row_rect(win), win.title_h, btns)
        head = icon[0] if icon is not None else win.x + 16 * n // r
        return head + slot

    def _row_rect(self, win):
        """The rect a full-screen row's title and buttons lay out on: the
        window strip's height, centred on the row, so its controls are the
        size they are on every desk window."""
        rh = self._row_h()
        return (win.x, win.y + (rh - win.title_h) // 2, win.w, win.title_h + 2)

    def band_span(self):
        """(x0, x1) the band's lent zone runs between in the window whose
        layouts are installed, where it is a full-screen row: from the title
        slot to the corner's controls. None anywhere else."""
        win = self._installed
        if win is None or win.saved is None or win.row:
            return None
        n = _layout_base.chrome_size(self._fs()) * 16 // _layout_base.render_scale()
        return (self._full_slot(win), win.corner - n)

    def dock(self, kind, rect):
        """Stand window `kind` in `rect` (glass coordinates) with no frame and
        no strip, above the window it docks in: the desk Editor's Run slot
        and Game tab hold the playtest so (runtime/editors/editor_desk.py). `rect`
        None gives the window back its place; False hides it with its run
        held (`_dock_hide`), the Editor's playtest waiting for its slot. Whether there is such a window: a game moved
        out to a window of its own (`play_in_window`) never docks."""
        win = self._wins.get(kind)
        if win is None or win.own:
            return False
        if rect is None or rect is False:
            if win.docked is not None:
                win.docked = None
                win.dock_off = None
                if win.undock is not None:
                    win.x, win.y, win.w, win.h = win.undock
                    win.undock = None
                self._build_content(win)
                self.ws._dirty = True
            if rect is False and not win.minimized:
                self._dock_hide(win, True)
            elif rect is None and win.minimized:
                self._dock_hide(win, False)
            return True
        if win.minimized:
            self._dock_hide(win, False)
        rect = tuple(rect)
        host = self._wins.get("menu")
        if host is not None:
            hx, hy = host.content_rect()[:2]
            win.dock_off = (rect[0] - hx, rect[1] - hy)
        if win.docked != rect:
            if win.docked is None:
                win.undock = (win.x, win.y, win.w, win.h)
            win.docked = rect
            win.saved = None
            win.x, win.y, win.w, win.h = rect
            self._build_content(win)
            self.ws._dirty = True
        order = self._order
        if order and order[-1] != kind and kind in order:
            order.remove(kind)
            order.append(kind)
        return True

    def offers_window(self):
        """Whether the pause card offers Play in a window (owner,
        2026-10-31): on a board with a desk, which this WM is, in both modes
        -- over a game full screen (console mode's, standing on the home, or
        a desk window's) or docked in the Editor's Run slot; never over one
        already in a window. A handheld's WM has no desk and no such row."""
        ws = self.ws
        if self.mode == "console":
            return (self.stack.depth() == 2 and self.stack.top() == "desktop"
                    and not ws.app_window_canvas and ws.cart_error is None
                    and not ws.player._is_tool)
        win = self._wins.get("desktop")
        return (win is not None and self._is_game(win)
                and (win.saved is not None or win.docked is not None))

    def play_in_window(self):
        """The pause card's Play in a window: the running game, its run
        untouched, in a desk window of its own with its strip -- a full-screen
        one restored to the rect it opened at, a docked one given back its
        window as an orphaned one is (`dock`); from console mode the desk
        comes back first, its windows as they were, the game's window on
        top. It is no Editor's playtest from then on: closing the Editor
        leaves it, the Editor's workspaces drop its Game tab while it lasts,
        and its maximize is the way back to full screen. False where nothing
        moved."""
        if self.mode == "desktop":
            self._sync_windows()          # a run spawned this frame has its window
        if not self.offers_window():
            return False
        if self.mode == "console":
            self.leave_console(game=True)
        win = self._wins["desktop"]
        docked = win.docked is not None
        if docked:
            self.dock("desktop", None)
        else:
            self._toggle_max(win)
        win.own = True
        self._editor_again()              # its workspaces without the game
        self._bring_forward("desktop")
        self.ws._dirty = True
        return True

    def game_full(self):
        """Whether the focused desk window is a game full screen: a play
        the console's key pauses, as in console mode."""
        win = self._focus_win()
        return (self.mode == "desktop" and win is not None and not win.minimized
                and win.saved is not None and self._is_game(win))

    def game_pauses(self):
        """Whether the console's key opens the pause card on the desk: over
        a game full screen, or docked in the Editor's Run slot, holding the
        keys."""
        if self.game_full():
            return True
        win = self._focus_win()
        return (self.mode == "desktop" and win is not None and not win.minimized
                and win.docked is not None and self._is_game(win))

    def _editor_again(self):
        """The Editor's window drawn again where a game left its workspaces
        or came back to them: its bar's Play or Stop, its Game tab."""
        host = self._wins.get("menu")
        if host is not None and not self.ws._editing_icons:
            self.ws.editor_app.zone_gen += 1
            self._prewarm(host)

    def _end_playtest(self):
        """Closing the Editor ends its playtest (owner, 2026-10-31), the
        way the pause card's Quit ends a run: the world released, its pmem
        flushed, its window closed. A game moved to a window of its own, or
        one the Editor did not run, goes on."""
        ws = self.ws
        win = self._wins.get("desktop")
        if win is None or win.own or ws.returns.caller() != EDITOR:
            return
        pause = ws.pause
        if pause.open and not pause.stopped:
            pause.close()
        ws._reset_canvas_state()
        ws._exit_to_caller()

    def _undock_orphans(self):
        """A docked game whose host is gone, hidden, or no longer shows the
        desk's workspaces gets its window back; one whose host moved moves
        with it. The host's content is stamped, not drawn, while it moves,
        so the game follows its host here rather than from the host's draw."""
        win = self._wins.get("desktop")
        if win is None or win.docked is None:
            return
        host = self._wins.get("menu")
        if host is not None and not host.minimized \
                and self.ws.editor_layer() is self.ws.editor_desk:
            off = win.dock_off
            if off is not None:
                hx, hy = host.content_rect()[:2]
                d = win.docked
                if d[0] != hx + off[0] or d[1] != hy + off[1]:
                    self.dock("desktop", (hx + off[0], hy + off[1], d[2], d[3]))
            return
        self.dock("desktop", None)

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
        p = self.ws.procs.by_key.get(key)
        if p is not None:
            self.ws.procs.promote(p)    # the focused game is the front one
            key = self._focus
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
        """Hide `win` to its chip (`on`) or show it again. A game's window,
        or a window-bound app cart's, keeps its run while hidden: the run is
        minimised -- its update at the minimised rate, no draw -- and hears
        `_minimise()` and `_restore()` (docs/os_2026-11/processes.md §1,
        runtime/shell/procs.py), so a game written from the template holds
        itself."""
        if win.minimized == bool(on):
            return
        win.minimized = bool(on)

    def _dock_hide(self, win, on):
        """A docked playtest hidden (`on`) or shown again: minimised, and
        its run held by the Editor while hidden unless something already
        holds it (the pause menu), which stays its holder's."""
        self._minimize(win, on)
        player = self.ws.player
        if on:
            win.held = not player.paused
            if win.held:
                player.pause()
        elif win.held:
            win.held = False
            player.resume()

    # -- the running set's windows (runtime/shell/procs.py) --------------------

    def rekey(self, old, new):
        """Window `old` goes on under key `new`, where it is: its record, its
        z-slot, its focus, its back-stack slot."""
        win = self._wins.pop(old)
        self._wins[new] = win
        order = self._order
        if old in order:
            order[order.index(old)] = new
        if self._focus == old:
            self._focus = new
        if old in self._text_wish:
            self._text_wish[new] = self._text_wish.pop(old)
        st = self.stack.kinds()
        if old in st:
            st[st.index(old)] = new
            self.stack.restore(st[1:])      # above the root
        self._kinds = self._slots()
        win.proc = self.ws.procs.by_key.get(new)
        self._win_sig = None
        self._on_nav()

    def swap_keys(self, a, b):
        """Windows `a` and `b` trade keys (a promotion: the world each shows
        moved), each staying where it is."""
        wa, wb = self._wins[a], self._wins[b]
        self._wins[a], self._wins[b] = wb, wa
        order = self._order
        ia, ib = order.index(a), order.index(b)
        order[ia], order[ib] = b, a
        if self._focus == b:
            self._focus = a
        elif self._focus == a:
            self._focus = b
        procs = self.ws.procs
        wa.proc = procs.by_key.get(b)
        wb.proc = procs.by_key.get(a)
        self._win_sig = None
        self.ws._dirty = True

    def game_shown(self, key):
        """A running set's game window shows: on the desk, not hidden to
        its chip, not under a full-screen window."""
        win = self._wins.get(key)
        if win is None or win.minimized or self.mode != "desktop":
            return False
        return self._order.index(key) >= self.full_cover()

    def _proc_paint(self, win, key, focused, dt, full=True):
        """A running set's game window: its world installed, its canvas's
        last frame composited and its chrome drawn, the cart not run (its
        frames are `Procs.frame`'s)."""
        procs = self.ws.procs
        p = procs.by_key.get(key)
        if p is None:
            return
        if not full and p.owed <= 0:
            return
        if p.owed > 0:
            p.owed -= 1
        was = procs._install(p)
        try:
            self._draw_player_window(win, False, focused, dt, full=full)
        finally:
            procs._uninstall(p, was)
        self._win_touched = True

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

    def content_alone(self):
        """Console mode's content alone on the glass, as the fullscreen
        tier's; a desktop-mode frame is its windows'."""
        return self.mode == "console" and FullscreenStackWM.content_alone(self)

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

    def desk_moves(self, dt):
        """Whether the desk's own pixels change this frame with nothing
        marking them: a live wallpaper, the boot logo, the unlock banner, a
        notice or a notification over it. A running game, a music preview or any other
        content animates inside its window, over the desk's cache."""
        ws = self.ws
        if ws._splash_until is not None or ws.wallpaper.is_animating(dt):
            return True
        if ws._toast_until and _ticks_diff(ws._toast_until, _ticks_ms()) > 0:
            return True
        if _NOTES.banner is not None and _NOTES.up() is not None:
            return True
        return ws.notice_active()

    def keeps_animating(self, dt, pages=True):
        """Console hook (the #44 redraw gate): keep frames flowing while a RUNNING
        cart's window is open anywhere on the desktop -- not just while it's the
        stack top -- so a game visibly keeps playing under Settings / beside a
        focused editor. Mirrors _animating's own desktop branch conditions.

        Deliberately NO gesture leg here: a WM gesture's repaint is guaranteed
        by the POINTER leg (window movement implies pointer movement; a
        held-still pointer moves nothing, so skipping is correct), and
        _animating feeds the window freeze's "nothing is animating" test
        (`_frame_is_quiet`) -- a gesture leg would void it on every drag
        frame. The desk's own cache asks `desk_moves`, which no window's
        content answers.

        A LIVE WALLPAPER does belong here (owner report 2026-07-31: "they only
        move when I move a window... but it doesn't happen while Settings is on
        top"). _animating's own wallpaper leg keys on the TOP process kind --
        `kind in ("launcher", "settings", "desk")` -- a list written before apps
        were windows, so Settings kept the desk alive and Appearance (or Files,
        Paint, Storybook...) silently froze it. On THIS tier the desk backdrop is
        visible behind every window, so its liveness cannot depend on who is on
        top. Unlike the gesture leg, re-rendering here is CORRECT: a live
        wallpaper's desk pixels really do change every frame, which is exactly
        the case the backdrop cache is not for. `pages` False: an app on the
        verbs (drawn on change) changes its own window's content alone."""
        ws = self.ws
        if self.mode == "desktop" and ws.wallpaper.is_animating(dt):
            return True                 # a parked desk's wallpaper is not drawn
        if ws.procs.list:
            return True                 # a running set's game steps every frame
        if "tasks" in self._wins and ws.tasks_layer.moved():
            return True                 # the task manager's rows moved
        return (self.stack.has("desktop") and ws.cart_error is None
                and not ws.player.paused
                and (ws._update is not None or ws._draw is not None)
                and (pages or not ws.player._on_change)
                and ws.player.animates())

    def _content_for(self, kind):
        """The content Layer a window presents (the router's registry lookup,
        window-kind-aware): the active Editor tab for "menu", the registry entry
        otherwise -- same resolution ws._content_layer() does for the stack top."""
        ws = self.ws
        if kind == "menu":
            return ws.editor_layer()
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

    # -- the overlays' state (docs/theme_and_shell_2026-11.md §8.6) ------------
    # The draw stack, the quiet frame and the debt check read these on every
    # frame, so they stay on the class; the overlays' paints and verbs are
    # WindowOverlays' (wm_overlays.py).

    def _pop_rides(self):
        """True where a popover change paints its rect alone: desktop mode on
        a raster root."""
        return self.mode == "desktop"

    def _scrim_up(self):
        """Whether a system modal's scrim is up (D-01): the system menu or a
        card's menu, ABOUT, the pause menu, the delete confirm
        (`SystemMenuUI.scrims`)."""
        return self.ws.menu_ui.scrims()

    def _scrim_rides(self):
        """Whether the scrim that is up is the system menu's, riding as a
        popover: a playtest's quiet frames carry it, the game's frame washed
        in its own rect (`_scrim_game`) and the menu's face put back over
        it, while every other pixel keeps the wash its full frame gave it."""
        ws = self.ws
        m = ws.sysmenu
        return (m.open and m.comp is None and self._pop_cur is not None
                and not ws._about and not ws.pause.open and not ws.confirm.open
                and self._pop_rides())

    def _pop_n(self):
        """The root's real buffer count: what a popover's rect is owed to."""
        n = getattr(self._root_canvas, "RETAINED_FRAMES", 1)
        return n if n > 1 else 1

    def _pause_rides(self):
        """Whether the pause menu rides as the system menu does: its scrim
        and card painted alone over a playtest's window paused on the desk."""
        return self._pop_wash[4] and self.ws.pause.open

    def _wash_rides(self):
        """Whether the system menu's scrim rides with it: the menu's own
        (not a card's, ABOUT's, the pause menu's or the delete confirm's)
        over a desk with no playtest, whose quiet frames wash the game
        instead (`_scrim_rides`). A focused window must be the top one: the
        menu taking the keys changes what it shows (ui.marks), so the wash
        renders it again over the snapshot (`_wash_focus`)."""
        ws = self.ws
        m = ws.sysmenu
        if ws._about or ws.confirm.open:
            return False
        win = self._wins.get(self._focus) if self._focus is not None else None
        if ws.pause.open:
            # The pause menu (or a stopped run's card) over its playtest's
            # window, the game composited (not drawn on the glass itself) and
            # held.
            game = self._wins.get("desktop")
            return (not m.open and game is not None and not game.minimized
                    and ws.canvas is not ws.sys_canvas and self._game_held()
                    and (win is None or win is game))
        if not (m.open and m.comp is None and "desktop" not in self._wins):
            return False
        return (win is None or win.minimized
                or (self._order[-1] == self._focus and win.buf is not None
                    and win.ctx is not None))

    def _game_held(self):
        """The playtest's game holds its frame: paused, or a stopped run
        under its card."""
        ws = self.ws
        return ws.player.paused or (ws.pause.open and ws.pause.stopped)

    def _pop_live(self):
        """True while a popover is open or owed to a retained buffer."""
        if self._pop_cur is not None:
            return True
        for s in self._pop_slots:
            if s[1] is not None:
                return True
        return self._pop_wash[1] is not None

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
        alone (docs/theme_and_shell_2026-11.md §8.6). Everything else falls through
        to the full memoized stack, and every full paint leaves a debt so every
        retained buffer carries the change before partial frames resume."""
        ws = self.ws
        self._ensure_stack()
        follows = self._painted_f == ws._frames_drawn - 1
        same_pop = self._pop_seq == self._pop_seq_f
        same_wash = self._wash_seq == self._wash_seq_f
        self._painted_f = ws._frames_drawn
        self._pop_seq_f = self._pop_seq
        self._wash_seq_f = self._wash_seq
        self._repeating = self._wash_repeats = False
        self._lead_refused = False
        # A system modal's scrim (D-01) dims every retained pixel once: the
        # frame it comes or goes repaints the desk whole, every window's
        # stamp and the desk's own streak voided, and while it is up no
        # window or desk frame is retained (_frame_is_quiet). The system
        # menu's over a still desk rides with it instead (`_pop_wash`). A
        # playtest under the system menu keeps its quiet frames: each
        # washes the game's own fresh pixels (_scrim_rides), and the frame
        # it comes on is the last one copied and washed whole (`_wash_all`).
        up = self._scrim_up()
        w = self._pop_wash
        self._wash_all = False
        if w[4] and not self._wash_rides():
            self._pop_reset()             # what it rides over changed (a playtest came)
            ws._dirty = True
        if up != self._scrim_was:
            self._scrim_was = up
            if w[1] is None or up != w[4]:
                self._win_sig = None
                self._desk_streak = 0
                if (up and not ws._dirty and follows and self._scrim_rides()
                        and hasattr(self._root_canvas, "copy_last")):
                    self._wash_all = True
                else:
                    ws._dirty = True
        # NOTE deliberately NO pointer-state condition: a finger playing a touch
        # cart moves the (hidden) pointer every frame, and the desktop shows no
        # hover feedback while the cursor is hidden -- real UI reactions all set
        # ws._dirty. Requiring a still pointer would forfeit the fast path for
        # exactly the games that need it.
        # The popover (bit 64) rides the quiet stack too: its rect is the
        # frame's damage, never a reason to repaint the desk.
        pop = self._pop_live()
        still = self._still()
        # A window over the game's casts its shadow on it and must stay over
        # it: the quiet stack repaints the game window alone, so not then.
        quiet = (still and "desktop" in self._wins and not self._player_under
                 and (not up or self._scrim_rides()))
        if self._wash_all:
            if quiet and pop:
                # The frame before held all but the game and the wash: its
                # copy, the game's frame, the scrim over both, the card.
                self._pop_game_next = True
                self._full_debt = self._retained_n() - 1
                return self._lead_pop_stack
            self._wash_all = False
            ws._dirty = True
            still = quiet = False
        self._pop_game_next = quiet and pop
        if quiet and self._full_debt <= 0:
            if pop:
                return self._quiet_pop_stack
            return self._quiet_stack        # the FPS chip rides the player window
        if (quiet and follows
                and hasattr(self._root_canvas, "copy_last")):
            # The buffer this frame paints is owed the last change: the last
            # painted frame holds it, and every pixel but the game's is this
            # frame's. Copied whole (whole rows go to the GDMA; round the
            # game's rect they would not), the quiet stack goes over it, the
            # popover's debts paid from the copy (`_repeating`).
            self._full_debt -= 1
            if pop:
                self._repeating = same_pop
                self._wash_repeats = same_wash
                return self._lead_pop_stack
            return self._lead_quiet_stack
        self._pop_game_next = False
        settled = not quiet and self._settled(still)
        if (follows and settled and (self._calm() or self._pop_holds_input())
                and ("desktop" not in self._wins or self._game_held())
                and hasattr(self._root_canvas, "copy_last")):
            self._repeating = same_pop
            self._wash_repeats = same_wash
        if pop and settled:
            # A persistent root (one buffer) owes no other buffer anything.
            if self._full_debt <= 0 or self._pop_n() == 1:
                return self._pop_stack
        if quiet or (settled and (pop or self._calm())):
            # Paying the other-buffer debt: the last painted frame again. A
            # still desk with no playtest and no popover copies its buffer.
            rep = self._repeating and not pop and self._full_debt > 0
            self._full_debt -= 1
            if rep:
                return self._repeat_stack
        else:
            # A change painted: every OTHER physical buffer owes a full paint
            # before partial frames may resume (N-1 debts on an N-buffer root).
            self._full_debt = self._retained_n() - 1
        return self._cache_draw

    def _still(self):
        """No input, overlay, gesture or cursor asks this frame to change
        anything but a running game and the popover (the riding pause menu
        one, or a stopped run's riding card)."""
        ws = self.ws
        return (not ws._dirty
                and (self._cache_sig & ~(578 if self._pause_rides() else 66)) == 0
                # FPS chip (bit 2), popover (64), the riding pause menu (512)
                and (ws.cart_error is None or self._pause_rides())
                and self._drag is None and self._drag_armed is None
                and self._resize is None
                and not getattr(ws.pointer, "visible", False))

    def _settled(self, still):
        """A still frame on the desk with nothing animating: what changes
        is the popover's, if anything."""
        return (still and self.mode == "desktop"
                and not self.ws._animating(1 / 60))

    def _calm(self):
        """...and the pointer neither moved nor is down (a press reaches
        content that marks nothing, §14.1): a full paint of such a frame
        changes nothing, so it brings one more retained buffer up to date."""
        ws = self.ws
        return not ws._ptr_changed() and not getattr(ws.pointer, "down", False)

    def _pop_holds_input(self):
        """The open system menu or riding pause menu takes every press: a
        tap outside closes it and reaches nothing, so a press or a release
        under it changes no content."""
        return ((self.ws.sysmenu.open and self._pop_cur is not None)
                or self._pause_rides())

    def debt_owed(self):
        """True while a retained buffer is behind the last change and the
        desk is calm: the console brings it now, while nothing waits on
        it, so the next change finds every buffer current and its popover
        or menu rides (a single-buffer root owes nothing)."""
        if not (self._full_debt > 0 and self._pop_n() > 1
                and self._settled(self._still()) and self._calm()):
            return False
        # ...and its frame would draw no window's content: it copies the
        # rows the last frame changed (`_RepeatLayer`), whatever the focused
        # window's buffer holds, or the focused one's buffer holds its page
        # under the look in force (§14.1's freeze).
        win = self._wins.get(self._focus) if self._focus is not None else None
        if win is None or win.minimized or win.kind == "desktop":
            return True
        if win._look_gen != self.ws.look.look_gen and not win._buf_stale:
            return False
        if win.buf is not None and not win._buf_stale:
            return True
        return (hasattr(self._root_canvas, "copy_last")
                and ("desktop" not in self._wins or self._game_held()))

    # -- closing and moving windows ---------------------------------------------

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
        win = self._wins.get("desktop")
        own = win is not None and win.own
        self._remove_kind("desktop")
        self._sync_windows()
        if own:
            self._editor_again()          # its Game tab back
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
            ws.storybook_app.commit()         # same rule for an open story
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
        if kind == "menu" and not ws._editing_icons:
            self._end_playtest()              # its playtest goes with it
        if kind == "desktop":
            self.close_player()
        elif kind in ws.procs.by_key:
            ws.procs.close(ws.procs.by_key[kind])
        else:
            self._remove_kind(kind)
        if kind == "menu" and not ws._editing_icons:
            ws.editor_closed()                # back where it was opened from

    def _close_window(self, kind):
        self.close_window_kind(kind)

    def _move_window(self, win, nx, ny):
        # No ws._dirty here: the repaint is guaranteed by keeps_animating's
        # gesture leg + the pointer leg.
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
