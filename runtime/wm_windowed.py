"""The WINDOWED window manager -- the big-screen / P4 "One" presentation tier
(#73 / #58 "Desktop look"; spec docs/shell_ux_v1.md §3's tier table).

`WindowedWM` presents the SAME everything-is-a-process shell as an OS desktop,
Picotron-style (owner direction, 2026-07-08):

  * with no processes open, the LIBRARY is the full-screen launch surface. Once
    PLAY/CHANGE opens a process, it gives way to the DESKTOP root (live wallpaper
    + the ONE full-width OS bar) beneath the process windows;
  * every process pushed above it (picker / Editor / Settings / update / a running
    cart) is a floating WINDOW: a WM-drawn TITLE STRIP (title + minimize /
    maximize / close), a border + drop shadow, draggable by the strip and
    resizable by the bottom-right grip;
  * a window's body is PURELY the app: in windowed mode the zoned bar suppresses
    its OS right zone and the dock (ws.windowed_chrome -- see bar_layer.py), so
    an app window's own bar row is just its toolbar (the Editor's tab ladder),
    never a copied taskbar;
  * a RUNNING CART composites integer-scaled and centered in its window -- the
    game itself never draws chrome, exactly as on the fullscreen tiers, and the
    editor stays VISIBLE beneath a playtest (spec §3's canonical picture);
  * the PICKER and the EDITOR share one "Make" window (_GROUP): picking a
    project swaps that window's content to the Editor, PROJECTS / its X swap it
    back -- the back-stack keeps both kinds, only the presentation merges;
  * INPUT FOCUS is decoupled from the back-stack: clicking a window (or its
    taskbar chip) moves the keyboard + highlight there WITHOUT popping anything
    -- a playtest keeps ticking (it stays the stack top, the running process)
    while the editor beside it is being typed in, and its pointer feed is
    click-stripped so the background cart never eats the editor's taps. Only an
    explicit exit ends a run: the strip X, hold-BACKSPACE while focused, or an
    app verb. Chips: click to focus, click the focused chip to minimize, click
    a minimized chip to restore.

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
"""

try:
    from wm import FullscreenStackWM, _VIEWPORT_BEZEL
    from layers import Layer
    from chrome import NAMES          # not palette: chrome is the device-safe home
    from widgets import _Blit, _in    # (runtime/palette.py needs colorsys -- host-only)
    import ui as _ui                  # desk icon label pills (ui.chip)
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.wm import FullscreenStackWM, _VIEWPORT_BEZEL
    from runtime.layers import Layer
    from runtime.chrome import NAMES
    from runtime.widgets import _Blit, _in
    from runtime import ui as _ui


# Window-chrome colors: the fixed ones live here; everything THEMEABLE (panel /
# title strip / accents / dim texture) reads the ws.theme_colors tokens per draw
# (chrome.THEMES, Settings -> THEME; the "night" default is the moybyte site
# colorway -- midnight navy panels, lavender strips, the yellow CTA accent).
import time as _time


def _wt():
    # ms tick for the WM's drag-path perf split (gated on ws.perf_capture)
    try:
        return _time.ticks_ms()
    except AttributeError:
        return int(_time.time() * 1000)


_SHADOW = NAMES["black"]
_BTN_X_FG = NAMES["red"]
_DRAG_MIN = 4                         # px of travel before a press becomes a drag

# Shell-process title strips (registered apps contribute TITLE through the app
# registry instead; the player shows the live cart title).
_TITLES = {"menu": "EDITOR", "picker": "PROJECTS",
           "settings": "SETTINGS", "update": "UPDATE"}

# Window GROUPS: back-stack kinds that share ONE window slot. The project picker
# and the Editor are one "Make" flow (spec shell_ux_v1.md §4/§6 -- the picker IS
# the Editor's entry state), so picking a project swaps the window's CONTENT to
# the Editor instead of stacking a second window, and PROJECTS / the editor's X
# swap it back. The back-stack itself is untouched (launcher -> picker -> menu
# still pops one level at a time); only the presentation merges.
_GROUP = {"picker": "make", "menu": "make"}


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
        ctx.writer_layout = ctx.app_layouts.get("writer")
        ctx.storybook_layout = ctx.app_layouts.get("storybook")
        return ctx

    def install(self, ws):
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
        ws.picker.set_layout(self.layout)


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
        self.buf = buf                # SystemCanvas (apps) | None (player)
        self.ctx = ctx                # _LayoutCtx (apps) | None (player)
        self.minimized = False        # hidden; lives on as a taskbar chip
        self.saved = None             # pre-maximize rect (x, y, w, h) | None
        self._buf_stale = True        # buf is behind the content's truth (fresh
                                      # buffer, or a gesture's _direct_render
                                      # painted past it) -> the next paint must
                                      # render live before the content freeze
                                      # (_content_static) may reuse it

    def content_rect(self):
        """The inner rect the content occupies (below the WM title strip)."""
        return (self.x + 1, self.y + 1 + self.title_h,
                self.w - 2, self.h - 2 - self.title_h)


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
        _t0 = _wt() if _perf else 0
        self.wm._draw_windows(dt)
        if _perf:
            self.ws._pf_wm_windows = _wt() - _t0

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


class _BackdropLayer(Layer):
    """The real desktop root (wallpaper + ONE OS bar) with a DRAG cache (#58).
    The Library is a launch surface only while the process stack has no windows;
    as soon as PLAY/CHANGE pushes one, this layer replaces it so the Library
    never reads as wallpaper behind Studio. During a drag/resize only the window
    position changes, so the first frame captures the desktop and later frames
    blit the retained backdrop.

    Correctness: this layer precedes _win_layer in the z-order, so the capture
    snapshots the desktop backdrop with NO windows on it; each drag frame blits
    that clean backdrop (erasing the dragged window's old position for free) and
    _win_layer then stamps the windows at their current spots. Double-buffer-safe
    -- the cache is its own off-screen buffer, re-blitted into whichever ping-pong
    buffer the frame targets. The desktop background routes only its OS bar;
    hidden Library cards cannot be activated through it."""

    id = "launcher"
    domain = "system"

    def __init__(self, wm):
        self.wm = wm
        self.ws = wm.ws

    def draw(self, dt):
        wm = self.wm
        # A CONTENT gesture (a finger scrolling inside a window) counts as a
        # gesture for cache purposes, exactly like a window drag/resize (#155,
        # owner "the project picker is choppy, the play launcher is smooth").
        # The desk cannot change while a window's content is being dragged, but
        # this layer used to re-render the whole desktop -- wallpaper cover-crop
        # + icon column + bar -- on EVERY such frame. Measured on glass during a
        # picker scroll: 107ms of a 181ms frame (~5fps) against the fullscreen
        # Library's 36ms, which pays no desk at all. The cached blit is ~26ms.
        # Same staleness trade the window-drag path already accepts: a clock tick
        # mid-gesture waits for the release, which re-renders live.
        content_anim = wm._content_gesture or wm._content_flinging()
        gesture = (wm._drag is not None or wm._resize is not None
                   or content_anim)
        # The cache is gated on the desk being UNCHANGED, not on being in a
        # gesture (#155). Gating it on the gesture meant every non-gesture
        # painted frame re-rendered the whole desk -- wallpaper cover-crop + icon
        # column + bar -- which on P4 glass is a 120ms frame, and one landed on
        # every gesture RELEASE. The desk's own content is a pure function of the
        # signature below; the clock is the one live part and it is repainted
        # over the cached blit instead of invalidating it.
        sig = self._desk_sig()
        stale = sig != wm._desk_sig
        # A change to the window SHAPE (open/close/minimize/move/resize) uncovers
        # desk the departed window was covering -- pixels the skip's own
        # justification ("fully covered by the window's stamp") no longer holds
        # for. The desk STATICS are unchanged then (the cache stays valid); only
        # the restore must actually run again, so reset the skip streak. Same
        # one-signature-beats-hunting-mutation-sites rule as the stamp voider.
        # (Owner report 2026-07-27: "close it and it remains as an artifact on
        # the desktop" -- the close frame skipped the restore outright.)
        # A DISCRETE shape change (open/close/minimize/maximize, or a gesture
        # settling on release) uncovers desk the departed footprint was
        # covering: invalidate the cache so that frame renders the desk LIVE
        # (erasing the ghost regardless of cache content -- on the P4 the boot
        # capture was found holding the SPLASH, #165) and re-captures fresh.
        # NEVER for gesture-driven changes: geometry is in the sig, so
        # invalidating during a drag/resize would re-render + re-capture the
        # whole desk EVERY frame (owner: "drags are slow, settings flickers" --
        # the first cut did exactly that), and doing it on RELEASE would
        # re-add the 120ms release frame #155 killed (the trail machinery
        # already restores a gesture's footprint). So the holder TRACKS the
        # sig silently while a gesture is live -- release finds it equal --
        # and only a discrete change (open/close/min/max) invalidates. Focus
        # is deliberately NOT in this sig: focus moves no desk pixels.
        s = wm._shape_sig()
        wsig = (s[0], s[2])               # order + geometry, no focus
        if wsig != wm._desk_win_sig:
            wm._desk_win_sig = wsig
            if wm._drag is None and wm._resize is None:
                wm._desk_streak = 0
                wm._backdrop_valid = False
        if (stale or wm._backdrop_disabled or wm._backdrop_unsupported
                or self.ws._animating(dt)):   # a toast/confetti moves desk pixels
            wm._desk_sig = sig
            wm._backdrop_valid = False        # live: re-render, then re-snapshot
            wm._desk_streak = 0
            wm._desk_painted = True           # wiped the buffer -> windows repaint
            if wm._gesture_hist and not gesture:
                wm._gesture_hist = []         # gesture over: drop the damage trail
            self._draw_desktop(dt)
            wm._capture_backdrop()
            return
        if wm._backdrop_valid:
            # CONTENT gesture: the window is STATIONARY, so the desk outside it
            # never changes and the desk under it is fully covered by the
            # window's own stamp. Once the cache has been laid into BOTH
            # ping-pong buffers (two consecutive gesture frames), every later
            # frame's target already holds the right pixels -- skip the restore
            # entirely. That is the last ~28ms between a windowed content scroll
            # and the fullscreen Library's (owner: "choppy vs smooth"). A window
            # DRAG still restores every frame: there the window moves, so the
            # backdrop it uncovers is genuinely damaged.
            if wm._drag is None and wm._resize is None:
                if wm._desk_streak >= wm._retained_n():
                    wm._desk_painted = False   # untouched: windows may skip too
                    self.ws.bar_layer.redraw_clock("desk")
                    return
                wm._desk_streak += 1
            wm._desk_painted = True
            _perf = getattr(self.ws, "perf_capture", False)
            _t0 = _wt() if _perf else 0
            wm._blit_backdrop_cache()
            if _perf:
                self.ws._pf_wm_restore = _wt() - _t0
            self.ws.bar_layer.redraw_clock("desk")
            return
        wm._desk_streak = 0
        wm._desk_painted = True
        self._draw_desktop(dt)                # cache lost: render + re-snapshot
        wm._capture_backdrop()

    def _desk_sig(self):
        """Everything the desk's wallpaper + icon column depends on.

        Deliberately NOT the clock (repainted over the cache instead) and NOT
        ws._cover_gen: a cover landing in the picker would otherwise invalidate
        the desk on the very frames a scroll is trying to stay cheap. Cheap to
        compute -- no per-frame scan of the cart list."""
        ws = self.ws
        cv = ws.sys_canvas
        return (cv.w, cv.h, ws.theme_name, ws.theme_variant,
                ws._effective_font_scale(), id(ws.icon_sheet),
                getattr(ws, "wallpaper_id", None),
                len(getattr(ws, "_apps", ())), len(ws._all_carts))

    def _draw_desktop(self, dt):
        self.ws.wallpaper.draw(dt)
        self._draw_desk_icons()
        self.ws.bar_layer._draw_status_strip("desk")

    # -- desk icons (#105: the make world's launch surface) --------------------
    #
    # A static v1 column: PLAY (drop to the fullscreen Library), PROJECTS (the
    # picker), then every desktop-only system app. Geometry is deterministic
    # (the _chip_rects pattern -- computed per call, no stored state), so draw
    # and hit-test can never disagree, and the icons render before the drag
    # backdrop capture, so the drag cache carries them for free.

    ICON_GLYPHS = {"play": "run", "projects": "edit"}
    HIDDEN_APPS = ("appearance",)     # reachable via Settings, not a desk tool

    def _icon_catalog(self):
        ws = self.ws
        out = [("play", "PLAY", None), ("projects", "PROJECTS", None)]
        for app, _text in getattr(ws, "_apps", ()):
            if app.id in self.HIDDEN_APPS:
                continue
            cart = None
            for c in ws._all_carts:
                if app.is_app(c):
                    cart = c
                    break
            title = ws.app_title(app.id) or app.id.upper()
            out.append((app.id, str(title).upper(), cart))
        return out

    def _icon_rects(self):
        """[(key, box_rect, label_rect, label, cart), ...] -- a left-edge column
        wrapping into further columns; recomputed per call from live geometry."""
        ws = self.ws
        fs = ws._effective_font_scale()
        bar_h = self.wm._bar_h()
        box = 40 * fs
        cell_w = 66 * fs
        cell_h = 62 * fs
        x = 14 * fs
        y0 = bar_h + 12 * fs
        y = y0
        out = []
        for key, label, cart in self._icon_catalog():
            if y + cell_h > ws.sys_canvas.h - 6 * fs:
                y = y0
                x += cell_w
            bx = x + (cell_w - 10 * fs - box) // 2
            out.append((key,
                        (bx, y, box, box),
                        (x, y + box + 3 * fs, cell_w - 10 * fs, 13 * fs),
                        label, cart))
            y += cell_h
        return out

    def _draw_desk_icons(self):
        ws = self.ws
        cv = ws.sys_canvas
        th = ws.theme_colors
        fs = ws._effective_font_scale()
        for key, box, pill, label, cart in self._icon_rects():
            cv.rect(box[0], box[1], box[2], box[3], th.get("panel", 60))
            cv.rectb(box[0], box[1], box[2], box[3], th.get("edge", 13))
            img = ws._icon_sheet_for(cart) if cart is not None else None
            if img is not None:
                sc = max(1, (box[2] - 8 * fs) // 16)
                cv.spr(img, box[0] + (box[2] - 16 * sc) // 2,
                       box[1] + (box[3] - 16 * sc) // 2, sc)
            else:
                glyph = self.ICON_GLYPHS.get(key, "app")
                ink = th.get("accent", 10) if key == "play" else th.get("title_ink", 0)
                ws._glyph(glyph, (box[0] + 6 * fs, box[1] + 6 * fs,
                                  box[2] - 12 * fs, box[3] - 12 * fs), ink, cv)
            _ui.chip(cv, th, pill, label, on=key == "play", fs=fs)

    def _open_icon(self, key):
        ws = self.ws
        if key == "play":
            ws.open_library()
        elif key == "projects":
            ws.open_picker()
        else:
            app = ws._apps_by_id.get(key)
            if app is not None:
                ws.open_app(app)

    def handle_input(self, i):
        return True

    def handle_pointer(self, px, py, click):
        if click:
            if py < self.wm._bar_h():
                self.ws.bar_layer.handle_bar_tap("desk", px, py)
                return True
            for key, box, pill, _label, _cart in self._icon_rects():
                if _in(px, py, box) or _in(px, py, pill):
                    self._open_icon(key)
                    return True
        return True


class WindowedWM(FullscreenStackWM):
    """The TWO-WORLDS presentation of the back-stack (#105, spec shell_ux_v1.md
    §3): the DESK (stack kind "desk") is the make world's floor -- wallpaper +
    system icons + taskbar, every process above it a floating window; without
    the desk on the stack this tier presents FULLSCREEN exactly like the small
    tiers (the Library and launcher-launched games own the whole screen -- the
    play world). Boot lands on the desk; the PLAY icon drops to the Library;
    the Library's Make tile / CHANGE come back to the desk. Install with
    `ws.wm = WindowedWM(ws)` right after construction
    (host_app.build_workstation(windowed=True)); requires a DISTINCT system
    canvas bigger than the 320x240 game canvas."""

    has_desk = True

    def __init__(self, ws):
        FullscreenStackWM.__init__(self, ws)
        if ws._sys_canvas is None:
            raise ValueError("WindowedWM needs a distinct (big) system canvas")
        # (ws.windowed_chrome is a world-aware PROPERTY now -- it follows
        # desk_open(); nothing to set here.)
        self._desk_was = False        # world-flip edge detector (_on_nav)
        self._root_canvas = ws._sys_canvas
        self._root_ctx = _LayoutCtx.capture(ws)
        self._win_layer = _WindowStackLayer(self)
        self._wins = {}               # slot key -> _Win
        self._order = []              # window slots, bottom -> top (stack-shaped)
        # INPUT FOCUS -- decoupled from the back-stack (the owner call that fixed
        # "clicking anything else kills my playtest"): the stack still owns what's
        # OPEN and who TICKS (the Player ticks while it's the stack top), but the
        # keyboard and window highlight follow _focus, which moves on click without
        # popping anything. A running game now keeps running while the editor
        # beside it is being typed in; only its X / hold-BACKSPACE (when focused)
        # / an exit verb ends it. None = the desktop root has focus.
        self._focus = None
        self._drag = None             # (kind, grab_dx, grab_dy) while dragging
        self._drag_armed = None       # (kind, ox, oy, wx, wy) press-in-strip origin
        self._resize = None           # (kind, ox, oy, ow, oh, cur_w, cur_h) in-flight
        self._ctx_switching = False   # reentrancy guard for internal _relayout runs
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
        self._quiet_stack_fps = [ws._perf_layer, _pw]
        self._full_debt = 0
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

    # -- layout-context plumbing ----------------------------------------------

    def on_relayout(self):
        """Hook called by ws._relayout() (a font-scale change / future resize).
        Re-anchors the relayout to the ROOT canvas if it ran while a window
        context was ambient, recaptures the root context, and drops every window
        so it rebuilds at the new scale on the next frame."""
        if self._ctx_switching:
            return                    # an internal (window-building) relayout
        ws = self.ws
        if ws._sys_canvas is not self._root_canvas:
            self._ctx_switching = True
            ws._sys_canvas = self._root_canvas
            ws._relayout()
            self._ctx_switching = False
        self._root_ctx = _LayoutCtx.capture(ws)
        self._wins.clear()
        self._order = []
        self._focus = None
        self.content_gen += 1
        self._backdrop = None             # size may have changed: drop the drag cache
        self._backdrop_valid = False

    def on_app_registered(self, app):
        """Extend the root layout snapshot for an app registered after the
        WindowedWM was installed. Built-ins register before installation, but
        the public seam explicitly permits later shell registrations."""
        layout = getattr(app, "layout", None)
        if layout is not None:
            self._root_ctx.app_layouts[app.id] = layout

    def _install(self, ctx):
        ctx.install(self.ws)

    def _make_ctx(self, buf):
        """Build the layout set for a window buffer: run the console's own
        _relayout with the buffer installed (so every responsive surface derives
        its geometry from the WINDOW size), capture it, then restore the root."""
        ws = self.ws
        self._ctx_switching = True
        ws._sys_canvas = buf
        ws._relayout()
        ctx = _LayoutCtx.capture(ws)
        self._root_ctx.install(ws)
        self._ctx_switching = False
        return ctx

    # -- window records --------------------------------------------------------

    def _fs(self):
        return self.ws._effective_font_scale()

    def _bar_h(self):
        """The desktop OS bar's height -- windows never overlap it (the taskbar
        row is OS-owned, chips included)."""
        return self._root_ctx.layout.status_h

    def desk_open(self):
        return "desk" in self._stack

    def _on_nav(self):
        FullscreenStackWM._on_nav(self)
        desk = "desk" in self._stack
        if desk != self._desk_was:
            # WORLD FLIP (#105): windowed_chrome just changed, and every app
            # layout bakes it into its bar_h at construction. Rebuild all
            # layouts + recapture the root ctx NOW, so the visible world is
            # never presented through the other world's chrome (repro without
            # this: a font-scale change inside the desk would strip the play
            # world's fullscreen app bars).
            self._desk_was = desk
            self.ws._relayout()

    def _slots(self):
        """Collapse the back-stack ABOVE THE DESK into window SLOTS: consecutive
        kinds in the same _GROUP share one slot, and the slot's content is the
        TOPMOST of its kinds (picker+menu -> one "make" window showing whichever
        is up). Returns [[slot_key, kind], ...] bottom -> top. In the play world
        (no desk on the stack) there are NO windows, ever -- every `not
        self._order` deferral then presents fullscreen (#105 two worlds)."""
        st = self._stack
        try:
            base = st.index("desk") + 1
        except ValueError:
            return []
        slots = []
        for k in st[base:]:
            g = _GROUP.get(k, k)
            if slots and slots[-1][0] == g:
                slots[-1][1] = k        # the higher kind takes the window over
            else:
                slots.append([g, k])
        return slots

    def _sync_windows(self):
        """Mirror the back-stack into window records: drop windows whose slot was
        popped, create records (buffer + layout context + cascade position) for
        newly-pushed ones, and keep each shared slot pointed at its TOP kind (so
        the Make window's content follows picker <-> editor navigation)."""
        slots = self._slots()
        keys = [g for g, _k in slots]
        if keys != self._order:
            alive = set(keys)
            for g in list(self._wins):
                if g not in alive:
                    del self._wins[g]
            for depth, (g, k) in enumerate(slots):
                if g not in self._wins:
                    self._wins[g] = self._make_window(g, k, depth)
            self._order = keys
            # A stack change (spawn/pop) moves focus to the newest top window --
            # the one navigation just revealed/opened. Click moves it after that.
            self._focus = keys[-1] if keys else None
        for g, k in slots:              # content follows the group's top kind
            self._wins[g].kind = k

    def _win_size(self, key, full, fs):
        """Default window OUTER size per window slot."""
        th = 18 * fs
        if key == "desktop":
            s = max(1, min((full.w - 24) // self.ws.canvas.w,
                           (full.h - 24 - th - self._bar_h()) // self.ws.canvas.h))
            return (self.ws.canvas.w * s + 2, self.ws.canvas.h * s + 2 + th)
        if key in ("make", "menu", "picker"):
            return (full.w - full.w // 8, full.h - full.h // 10)
        if key in ("artwork", "appearance", "writer", "storybook"):
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
        title_h = 18 * fs
        bar_h = self._bar_h()
        # A default can exceed a small desktop (e.g. the fs-scaled Settings minimum
        # at font 2x on 600px) -- clamp so the whole window, grip included, fits
        # below the OS bar.
        w = min(w, full.w - 4 * fs)
        h = min(h, full.h - bar_h - 8 * fs)
        # Cascade: centered, each deeper window stepping down-right, clamped so
        # the title strip always stays reachable below the OS bar.
        x = (full.w - w) // 2 + depth * 12 * fs
        y = bar_h + (full.h - bar_h - h) * 2 // 5 + depth * 10 * fs
        x = max(0, min(x, full.w - w // 2))
        y = max(bar_h, min(y, full.h - 24 * fs))
        win = _Win(kind, x, y, w, h, title_h)
        self._build_content(win)
        # Prewarm: render the content ONCE into the fresh buffer, so a window
        # created BEHIND another (e.g. the picker when picker+editor spawn in one
        # navigation) shows its app instead of a black retained buffer.
        self._prewarm(win)
        return win

    def _prewarm(self, win):
        """One-shot render of an app window's content into its buffer (defensive:
        a content error just leaves the buffer blank -- the live path will report
        it when the window is focused)."""
        if win.buf is None or win.ctx is None:
            return
        content = self._content_for(win.kind)
        if content is None:
            return
        self._install(win.ctx)
        try:
            content.draw(0)
            win._buf_stale = False
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
        if win.kind == "desktop":
            win.buf = None
            win.ctx = None
            return
        full = self._root_canvas
        cw = max(64, win.w - 2)
        ch = max(40, win.h - 2 - win.title_h)
        win.buf = full.new_layer(cw, ch)
        win.ctx = self._make_ctx(win.buf)
        win._buf_stale = True         # blank until a live render/_prewarm fills it

    def _resize_window(self, win, w, h):
        """Apply a new OUTER size (resize grip release / maximize): clamp to a
        usable minimum, then rebuild the content buffer + layout context so the
        app reflows to the new window size."""
        fs = self._fs()
        max_w = self._root_canvas.w
        max_h = self._root_canvas.h - self._bar_h()
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
        """Maximize <-> restore: fill the desktop below the OS bar (the taskbar
        stays visible), remembering the rect to restore to."""
        full = self._root_canvas
        bar_h = self._bar_h()
        if win.saved is None:
            win.saved = (win.x, win.y, win.w, win.h)
            win.x, win.y = 0, bar_h
            self._resize_window(win, full.w, full.h - bar_h)
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

    def player_has_pointer(self):
        """Console hook: whether the game-space pointer publication should carry
        live click/down state. False while a window OTHER than the playtest holds
        focus, so a background running cart never eats the taps meant for the
        editor beside it."""
        if not self._order:
            return True
        win = self._focus_win()
        return win is not None and win.kind == "desktop"

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
        if not self._order:                    # launcher root: fullscreen shape
            return FullscreenStackWM.keys_to_cart(self)
        win = self._focus_win()
        return win is not None and win.kind == "desktop"

    def keeps_animating(self, dt):
        """Console hook (the #44 redraw gate): keep frames flowing while a RUNNING
        cart's window is open anywhere on the desktop -- not just while it's the
        stack top -- so a game visibly keeps playing under Settings / beside a
        focused editor. Mirrors _animating's own desktop branch conditions."""
        ws = self.ws
        return ("desktop" in self._stack and ws.cart_error is None
                and (ws._update is not None or ws._draw is not None))

    def _content_for(self, kind):
        """The content Layer a window presents (the router's registry lookup,
        window-kind-aware): the active Editor tab for "menu", the registry entry
        otherwise -- same resolution ws._content_layer() does for the stack top."""
        ws = self.ws
        if kind == "menu":
            return ws._content_layers.get(ws.menu_view) or ws._content_layers["cards"]
        return ws._content_layers.get(kind)

    # -- the memoized stacks (parent memo, windowed shape) ---------------------

    def _rebuild(self, content, sig):
        if "desk" not in self._stack:
            # The PLAY world (#105): no desk -> byte-identical to the
            # fullscreen tier, for the whole stack (Library, fullscreen games,
            # play-world Settings/tools) -- not just the launcher root.
            if self._wins:
                self._wins.clear()
                self._order = []
            FullscreenStackWM._rebuild(self, content, sig)
            return
        self._sync_windows()
        ws = self.ws
        overlays = []
        # Game-domain overlays (the perf HUD) draw on the 320x240 game canvas and
        # must land BEFORE the window blit composites it into the player window.
        pre = []
        if sig & 2:
            pre.append(ws._perf_layer)
        if sig & 4:
            overlays.append(ws._confetti_layer)
        if sig & 8:
            overlays.append(ws._ach_layer)
        if sig & 16:
            overlays.append(ws._egg_layer)
        if sig & 32:
            overlays.append(ws._toast_layer)
        if sig & 64:
            overlays.append(ws._sysmenu_layer)
        if sig & 128:
            overlays.append(ws._about_layer)
        overlays.append(ws._cursor_layer)
        base = [self._backdrop_layer] + pre + [self._win_layer]
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
        stack (just the player window; see _PlayerWindowLayer). Everything else
        falls through to the full memoized stack, and every full paint leaves a
        one-frame debt so BOTH ping-pong buffers carry the change before partial
        frames resume."""
        ws = self.ws
        self._ensure_stack()
        # NOTE deliberately NO pointer-state condition: a finger playing a touch
        # cart moves the (hidden) pointer every frame, and the desktop shows no
        # hover feedback while the cursor is hidden -- real UI reactions all set
        # ws._dirty. Requiring a still pointer would forfeit the fast path for
        # exactly the games that need it.
        quiet = (not ws._dirty
                 and "desktop" in self._wins
                 and (self._cache_sig & ~2) == 0    # FPS chip (bit 2) rides along
                 and ws.cart_error is None
                 and self._drag is None and self._drag_armed is None
                 and self._resize is None
                 and not getattr(ws.pointer, "visible", False))
        if quiet and self._full_debt <= 0:
            return self._quiet_stack_fps if (self._cache_sig & 2) else self._quiet_stack
        if quiet:
            self._full_debt -= 1      # paying the other-buffer debt: full paint
        else:
            # A change painted: every OTHER physical buffer owes a full paint
            # before partial frames may resume (N-1 debts on an N-buffer root).
            self._full_debt = self._retained_n() - 1
        return self._cache_draw

    # -- the drag backdrop cache (#58 drag perf) -------------------------------

    def _ensure_backdrop(self):
        """The full-screen off-screen cache buffer (lazily allocated, re-made if
        the root canvas size changed under it)."""
        sc = self._root_canvas
        cache = self._backdrop
        if cache is None or cache.w != sc.w or cache.h != sc.h:
            cache = self._backdrop = sc.new_layer(sc.w, sc.h)
            self._backdrop_valid = False
        return cache

    def _capture_backdrop(self):
        """Snapshot the just-rendered desktop backdrop -- the root canvas with NO
        windows on it yet (_BackdropLayer precedes _win_layer) -- into the cache:
        one opaque full-frame copy via the cache canvas's own blit_strip reading
        the root canvas AS a source layer (native moy_gfx.blit565 on the device,
        host index slice otherwise -- the same kernel both directions)."""
        try:
            cache = self._ensure_backdrop()
            self.ws.note_cost("desk.backdrop")
            cache.blit_strip(self._root_canvas, 0, 0)
            self._backdrop_valid = True
        except Exception:  # noqa: BLE001 -- a failed capture (OOM, or a
            self._backdrop_valid = False   # recording root with no pixels to
            self._backdrop = None          # snapshot) forfeits the cache; the
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
        # NOTE gesture tuples carry the window REGISTRY key (g[0], e.g. "make"
        # for the shared picker/Editor group), which is NOT win.kind (the
        # CONTENT kind, e.g. "picker") -- comparing g[0] == win.kind silently
        # never matched for the make group, so its drag content-freeze and
        # stamp-defer never engaged (found via the web payload autopsy, #113).
        # Every gesture-vs-window test below resolves through _wins identity.
        if g is self._resize:
            w = max(w, self._resize[5])
            h = max(h, self._resize[6])
        return (win.x - 2, win.y - 2, w + 7, h + 7)

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
        if self._backdrop is None:
            return
        rc = self._root_canvas
        stamp = getattr(rc, "blit_strip_rect", None)
        ext = self._gesture_extent()
        n = self._retained_n()
        if (stamp is None or self._union_disabled or ext is None
                or not self._gesture_hist):
            rc.blit_strip(self._backdrop, 0, 0)
            if ext is not None:
                self._gesture_hist = (self._gesture_hist + [ext])[-n:] \
                    if self._gesture_hist else [ext] * n
            return
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
            bx0 = max(x0, win.x)
            by0 = max(y0, win.y)
            bx1 = min(x1, win.x + bw)
            by1 = min(y1, win.y + bh)
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

    # -- game viewport == the player window (#39 mapping) ----------------------

    def _player_view(self, win):
        """(ox, oy, scale) of the game canvas centered in the player window's
        content rect -- the windowed viewport."""
        gc = self.ws.canvas
        cx, cy, cw, ch = win.content_rect()
        scale = max(1, min(cw // gc.w, ch // gc.h))
        ox = cx + (cw - gc.w * scale) // 2
        oy = cy + (ch - gc.h * scale) // 2
        return (ox, oy, scale)

    def viewport(self):
        win = self._wins.get("desktop")
        if win is None:
            return FullscreenStackWM.viewport(self)
        return self._player_view(win)

    def composite_game(self):
        # Desk world: a no-op -- the window layer blits the game canvas into
        # the player WINDOW itself, and stamping it full-viewport would paint
        # over the desktop. PLAY world (#105): the fullscreen composite, routed
        # through the polymorphic _blit_game so the P4's native RGB565 path and
        # the web's b64-spr path both work (the parent's index loops need an
        # indexed system buffer this tier's canvases may not have).
        if self._order:
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
        ox, oy, scale = FullscreenStackWM.viewport(self)
        sc.cls(_VIEWPORT_BEZEL)        # letterbox fill
        self._blit_game(sc, gc, ox, oy, scale)

    # -- drawing ---------------------------------------------------------------

    def _lowest_dirty_window(self, dt):
        """Index of the LOWEST window that must repaint this frame; everything
        below it is already correct in the target framebuffer and is skipped.

        Why (measured on glass 2026-07-26): every open window was re-stamped and
        re-chromed EVERY frame even when nothing about it changed -- an unfocused
        window isn't even re-rendered, just copied. Each extra open window cost
        ~44ms a frame (1 window 56ms, 2 windows 99ms, 3 windows 144ms), which is
        what made a desktop with a few things open feel far worse than the
        fullscreen Library.

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

    def _draw_windows(self, dt):
        self._sync_windows()
        n = len(self._order)
        first = self._lowest_dirty_window(dt)
        # Chrome freeze (#155): a window's strip/border/shadow is DISJOINT from
        # its content stamp, so on a quiet frame -- a content scroll, a fling,
        # typing -- those pixels are already correct in this ping-pong buffer and
        # redrawing them is pure waste. Measured on P4 glass during a picker
        # scroll: 8.2ms of a 70ms frame, every frame, for an unchanged title bar.
        # _lowest_dirty_window sets _sig_stable (window shape/order/focus).
        self._chrome_quiet = self._sig_stable and self._frame_is_quiet(dt)
        for i in range(n):
            key = self._order[i]
            win = self._wins[key]
            focused = (key == self._focus)
            if win.minimized:
                continue
            if i < first:
                continue                  # settled + nothing below it repainted
            win._stamp_streak = getattr(win, "_stamp_streak", 0) + 1
            if win.kind == "desktop":
                # The Player TICKS whenever its window is open, independent of
                # input focus AND of what sits above it on the back-stack -- a
                # game keeps running while the editor beside it is typed in or
                # while Settings floats over it (wifi setup mid-game, #38). The
                # one exception: the crash-editor flow keeps the frozen frame
                # (cart_error -> tick just repaints the error panel, harmless).
                self._draw_player_window(win, True, focused, dt)
            else:
                self._draw_app_window(win, focused, dt)
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

    def _win_grip(self, win, focused):
        """The resize grip: three diagonal steps in the bottom-right corner.
        Drawn SEPARATELY from the rest of the chrome because it sits INSIDE the
        content rect -- the window's content stamp overwrites it every frame, so
        it is the one piece the chrome freeze can never skip."""
        if not focused:
            return
        sc = self._root_canvas
        ink = self.ws.theme_colors["chrome_ink"]
        fs = self._fs()
        gx, gy, gw, gh = self._grip_rect(win)
        for i in range(3):
            d = (i + 1) * (gw // 4)
            sc.rect(gx + gw - d, gy + gh - 2 * fs, d, fs, ink)

    def _win_chrome(self, win, focused, quiet=False):
        """Title strip (title + min/max/X) + border + drop shadow + resize grip.
        The highlight follows INPUT FOCUS (which moves on click), not the stack.

        `quiet` (see _draw_windows) allows the FREEZE: once this exact chrome has
        been painted into every physical buffer, a quiet frame skips it and
        redraws only the grip. The streak counts CONSECUTIVE quiet paints, so any
        disturbance -- desk repaint, drag, cursor, animation, a changed title or
        theme -- restarts it and all buffers are refreshed before skipping
        resumes."""
        sig = (win.x, win.y, win.w, win.h, win.title_h, focused,
               self._win_title(win), self.ws.theme_name, self.ws.theme_variant,
               self._fs())
        if not quiet or sig != getattr(win, "_chrome_sig", None):
            win._chrome_sig = sig
            win._chrome_streak = 0
        elif win._chrome_streak >= self._retained_n():
            self._win_grip(win, focused)
            return
        win._chrome_streak += 1
        sc = self._root_canvas
        ws = self.ws
        th = ws.theme_colors
        fs = self._fs()
        sh = 3
        sc.rect(win.x + sh, win.y + win.h, win.w, sh, _SHADOW)     # bottom shadow
        sc.rect(win.x + win.w, win.y + sh, sh, win.h, _SHADOW)     # right shadow
        sc.rectb(win.x, win.y, win.w, win.h,
                 th["chrome_ink"] if focused else th["dim"])
        # Title strip: label left, [minimize][maximize][close] right. Focused =
        # the theme's active-title tint with its ink (the active-title cue,
        # Picotron-style); unfocused = the inactive strip role with dim ink.
        strip_bg = th["title_active"] if focused else th["title_inactive"]
        strip_fg = th["title_ink"] if focused else th["chrome_ink_dim"]
        sc.rect(win.x + 1, win.y + 1, win.w - 2, win.title_h, strip_bg)
        sc.rect(win.x + 1, win.y + win.title_h, win.w - 2, 1,
                th["chrome_ink"] if focused else th["dim"])
        title = self._win_title(win)
        btns = self._strip_buttons(win)
        first_btn_x = btns[-1][1][0] if btns else win.x + win.w
        maxc = max(0, (first_btn_x - (win.x + 4 * fs)) // (8 * fs))
        if maxc > 0:
            sc.print(title[:maxc], win.x + 4 * fs, win.y + 1 + 5 * fs, strip_fg, 1)
        for name, rect in btns:
            glyph = {"close": "close", "max": "app", "min": "minus"}[name]
            ws._glyph(glyph, rect, _BTN_X_FG if name == "close" else strip_fg, sc)
        self._win_grip(win, focused)

    def _win_title(self, win):
        ws = self.ws
        if win.kind == "desktop":
            return str((ws.cart.get("title") if ws.cart else "") or "GAME")
        base = ws.app_title(win.kind) or _TITLES.get(win.kind, win.kind.upper())
        if win.kind == "menu" and ws.cart:
            t = ws.cart.get("title")
            if t:
                return base + " - " + str(t)
        return base

    def _strip_buttons(self, win):
        """The title-strip buttons as (name, rect), laid RIGHT to LEFT: close,
        maximize, and -- app windows only -- minimize (a running game can't
        minimize: hiding it would mean silently pausing it)."""
        fs = self._fs()
        ic = 16 * fs
        y = win.y + 1 + (win.title_h - ic) // 2
        x = win.x + win.w - 2 - ic
        out = [("close", (x, y, ic, ic))]
        x -= ic + 2 * fs
        out.append(("max", (x, y, ic, ic)))
        if win.kind != "desktop":
            x -= ic + 2 * fs
            out.append(("min", (x, y, ic, ic)))
        return out

    def _strip_button_hit(self, win, px, py):
        """Fat-finger resolution for the strip buttons (owner report 2026-07-27:
        'when I exit a window it stays on the desktop'). At font scale 1 the
        visual buttons are 16px (~1.7mm on the 7\" glass) at 18px pitch, so a
        finger tap missed the exact rect and fell through to the drag-arm --
        the window moved a little and never closed. A tap anywhere in the
        button BLOCK (the buttons' span plus the border to the window's right
        edge, plus a small overhang below the strip) now resolves to the
        NEAREST button center. Per-button padding can't work at this pitch;
        same fix class as _grip_hit_rect (2026-07-10)."""
        btns = self._strip_buttons(win)
        if not btns:
            return None
        fs = self._fs()
        pad = 6 * fs
        left = min(r[0] for _, r in btns) - pad
        right = win.x + win.w               # past X is the dead border strip
        top = win.y
        bottom = win.y + 1 + win.title_h + pad
        if not (left <= px < right and top <= py < bottom):
            return None
        best = None
        bd = None
        for name, (bx, by, bw, bh) in btns:
            cx = bx + bw // 2
            cy = by + bh // 2
            d = (px - cx) * (px - cx) + (py - cy) * (py - cy)
            if bd is None or d < bd:
                best, bd = name, d
        return best

    def _grip_rect(self, win):
        fs = self._fs()
        g = 12 * fs
        return (win.x + win.w - g, win.y + win.h - g, g, g)

    def _grip_hit_rect(self, win):
        """The grip's TOUCH target -- twice the drawn grip plus an overhang past
        the window corner (owner report 2026-07-10: the 24px visual grip is too
        small for a finger on the 7\" panel; ~48px is the usual touch minimum).
        Drawing keeps _grip_rect, only the pointer hit-test uses this."""
        fs = self._fs()
        g = 24 * fs
        over = 4 * fs
        return (win.x + win.w - g, win.y + win.h - g, g + over, g + over)

    def _live_resize_ok(self):
        """Live-body resize needs the rect-clipped stamp (see _blit_backdrop_cache);
        without it (the web RecordingLayer) the rubber-band outline preview stays."""
        return getattr(self._root_canvas, "blit_strip_rect", None) is not None

    def _draw_resizing_window(self, win, focused, cw, ch):
        """The 'real OS' resize feel (#58): during the gesture the window BODY
        follows the grip -- frame + title strip + grip at the rubber size, the
        RETAINED content cropped into the new content rect (anchored top-left;
        grow reveals the panel field -- no re-layout mid-gesture, the real reflow
        still lands on release via _resize_window). Draws via a temporary w/h
        swap so _win_chrome/content_rect need no size plumbing."""
        sc = self._root_canvas
        ow, oh = win.w, win.h
        win.w, win.h = cw, ch
        try:
            cx, cy, cwid, chei = win.content_rect()
            if cwid > 0 and chei > 0:
                sc.rect(cx, cy, cwid, chei, self.ws.theme_colors["panel"])
                sc.blit_strip_rect(win.buf, cx, cy, cx, cy, cwid, chei)
            self._win_chrome(win, focused)
        finally:
            win.w, win.h = ow, oh

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
        self._install(win.ctx)
        ws._sys_canvas = root                 # ...but paint on the framebuffer
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
        docs/ui_damage_model_v1.md §5.0 -- this is its first slice).

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
        renders live to refill it before the freeze may resume."""
        ws = self.ws
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
        # Gesture keys are the window REGISTRY key (e.g. "make"), not win.kind
        # (the CONTENT kind, e.g. "picker") -- resolve through _wins identity
        # (see _gesture_extent's note; the == win.kind form never matched the
        # shared make group, so its drag froze nothing and re-rendered the
        # picker/Editor content every frame).
        if self._resize is not None \
                and self._wins.get(self._resize[0]) is win \
                and self._live_resize_ok():
            # Live-body resize: the frame follows the grip, content crops.
            self._draw_resizing_window(win, focused,
                                       self._resize[5], self._resize[6])
            return
        moving = ((self._drag is not None
                   and self._wins.get(self._drag[0]) is win)
                  or (self._resize is not None
                      and self._wins.get(self._resize[0]) is win))
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
                self._win_chrome(win, focused, quiet=self._chrome_quiet)
                return
            # CONTENT FREEZE (docs/ui_damage_model_v1.md §5.0 first slice): on a
            # painted frame that provably did not change this window's content
            # (see _content_static), skip the re-render and let the retained-
            # buffer stamp below present it -- the map tab's draw is ~70ms on P4
            # glass, the stamp ~14ms, and the stamp runs either way.
            if not self._content_static(win):
                # Live: render the focused app into its buffer at the window's
                # layout.
                self._install(win.ctx)
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
                if asb(win.buf, win.x + 1, win.y + 1 + win.title_h):
                    return
                # non-fit / refusal: fall through to the sync stamp (the chrome
                # is already down; the second draw below is harmless overdraw).
        # Retained (or just-rendered) buffer -> desktop, then chrome.
        _perf = getattr(self.ws, "perf_capture", False)
        _t0 = _wt() if _perf else 0
        self._root_canvas.blit_strip(win.buf, win.x + 1, win.y + 1 + win.title_h)
        if _perf:
            self.ws._pf_wm_stamp = _wt() - _t0
        self._win_chrome(win, focused, quiet=self._chrome_quiet)

    def _draw_player_window(self, win, running, focused, dt, full=True):
        ws = self.ws
        if running:
            # The Player ticks while it's the STACK top (the running process),
            # focused or not -- editing beside a live playtest keeps it alive.
            self._content_for("desktop").draw(dt)  # Player.tick -> the game canvas
        gc = ws.canvas
        fb = getattr(gc, "flush_batch", None)
        if fb is not None:
            fb()
        cx, cy, cw, ch = win.content_rect()
        if full:
            self._root_canvas.rect(cx, cy, cw, ch, _SHADOW)   # letterbox bezel
        ox, oy, scale = self._player_view(win)
        # On a quiet game frame (full=False) the composite is this frame's LAST
        # framebuffer write, so a device backend may run it async and defer the
        # present (the #58 composite-overlap budget lever). A full paint draws
        # chrome AFTER it, so it must stay synchronous -- defer=not full.
        self._blit_game(self._root_canvas, gc, ox, oy, scale, defer=not full)
        if full:
            self._win_chrome(win, focused)

    def _blit_game(self, sc, gc, ox, oy, scale, defer=False):
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
            bg(gc, ox, oy, scale, defer)
            return
        gbuf = getattr(gc, "buf", None)
        sbuf = getattr(sc, "buf", None)
        if gbuf is None:
            return
        if sbuf is None:
            img = _Blit(gc.w, gc.h, bytes(gbuf), -1)
            img._paint = True              # -> the compact b64 wire form (~2.4x lighter)
            sc.spr(img, ox, oy, scale)
            return
        gw, gh = gc.w, gc.h
        sw, sh = sc.w, sc.h
        for gy in range(gh):
            grow = gy * gw
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

    # -- the taskbar chips (open windows in the desktop bar) --------------------

    def _chip_rects(self):
        """One chip per open window, centered in the OS bar between the launcher's
        selected-name zone and the right status cluster. Returns
        [(kind, rect, label)] in stack order; deterministic, so draw + hit-test
        share it without stored state."""
        if not self._order:
            return []
        lay = self._root_ctx.layout
        fs = lay.fs
        out = []
        widths = []
        labels = []
        for k in self._order:
            label = self._win_title(self._wins[k])[:8]
            labels.append(label)
            widths.append(len(label) * lay.font_w + 8 * fs)
        total = sum(widths) + (len(widths) - 1) * 2 * fs
        left_edge = self._root_canvas.w // 4          # clear of the selected name
        x = max(left_edge, (self._root_canvas.w - total) // 2)
        y = 1 * fs
        h = lay.status_h - 2 * fs
        for i, k in enumerate(self._order):
            if x + widths[i] > lay.clock_x - 4 * fs:
                break                                  # out of bar space -- stop
            out.append((k, (x, y, widths[i], h), labels[i]))
            x += widths[i] + 2 * fs
        return out

    def _draw_taskbar_chips(self, quiet=False):
        # Frozen on quiet frames like the window chrome (#155): the chips live on
        # the OS bar, which no window ever overlaps, so once painted into both
        # ping-pong buffers they stay correct until something changes them. Any
        # disturbance (desk repaint, drag, cursor, animation) makes `quiet` False
        # and restarts the streak, so both buffers refresh before skipping again.
        sc = self._root_canvas
        fs = self._fs()
        th = self.ws.theme_colors
        sig = tuple((k, r, lb, k == self._focus, self._wins[k].minimized)
                    for k, r, lb in self._chip_rects())
        if not quiet or sig != self._chip_sig:
            self._chip_sig = sig
            self._chip_streak = 0
        elif self._chip_streak >= self._retained_n():
            return
        self._chip_streak += 1
        for key, (x, y, w, h), label in self._chip_rects():
            win = self._wins[key]
            focused = (key == self._focus and not win.minimized)
            bg = th["accent"] if focused else th["panel"]
            if focused:
                fg = NAMES["black"]                # ink on the accent CTA chip
            else:
                fg = th["edge"] if win.minimized else th["chrome_ink"]
            sc.rect(x, y, w, h, bg)
            sc.rectb(x, y, w, h, th["dim"] if win.minimized else th["chrome_ink"])
            sc.print(label, x + 4 * fs, y + (h - 8 * fs) // 2, fg, 1)

    def _chip_tap(self, key):
        """Taskbar chip click -- pure FOCUS verbs, never a pop: restore a
        minimized window (and focus it), minimize the focused one (apps only),
        or just move focus to it. Nothing closes from the taskbar."""
        ws = self.ws
        win = self._wins.get(key)
        if win is None:
            return
        ws._dirty = True
        if win.minimized:
            win.minimized = False
            self._focus = key
        elif key == self._focus:
            if win.kind != "desktop":              # a running game never minimizes
                win.minimized = True
        else:
            self._focus = key

    # -- input routing ----------------------------------------------------------

    def _route_key(self, i):
        """Keyboard goes to the FOCUSED window's content -- focus moves on click,
        independent of the back-stack, so typing lands in the editor while a
        playtest keeps running beside it. No focused window (the desktop root) ->
        return False and the launcher layer takes the keys."""
        if not self._order:
            return False
        win = self._focus_win()
        if win is None or win.minimized:
            return False
        content = self._content_for(win.kind)
        if content is None:
            return False
        if win.ctx is not None:
            self._install(win.ctx)
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
        for grid in (getattr(ws, "picker", None), getattr(ws, "launcher", None)):
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
                ws._dirty = True
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
            # Clicking the desktop moves focus to it. Only its OS bar is
            # interactive; the Library is not invisibly present underneath.
            if click and self._focus is not None:
                self._focus = None
                ws._dirty = True
            return bool(self._backdrop_layer.handle_pointer(px, py, click))
        win = self._wins[key]
        # A click FOCUSES the window it lands in -- and never pops anything (the
        # owner call: looking at the editor must not end the playtest beside it).
        # Closing is explicit: the strip X, hold-BACKSPACE in a focused game, or
        # an app's own exit verb.
        if click and key != self._focus:
            self._focus = key
            ws._dirty = True
        focused = (key == self._focus)
        if click:
            name = self._strip_button_hit(win, px, py)
            if name is not None:
                ws._dirty = True
                if name == "close":
                    self._close_window(win.kind)
                elif name == "max":
                    self._toggle_max(win)
                elif name == "min" and win.kind != "desktop":
                    win.minimized = True
                    if focused:
                        self._focus = None
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
        lx, ly = px - (win.x + 1), py - (win.y + 1 + win.title_h)
        content = self._content_for(win.kind)
        if content is None:
            return True
        if p.down:
            self._content_gesture = True    # scrolling INSIDE a window: the desk
                                            # is static -> _BackdropLayer caches
        self._install(win.ctx)
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
        st = self._stack
        if kind in st and kind != "launcher":
            st.remove(kind)
            self._on_nav()

    def close_player(self):
        """Close the playtest window (its X, or hold-BACKSPACE routed through
        ws._exit_to_caller): remove ONLY the player from the stack, then hand
        focus back to the run CALLER's window when it's open (the Editor for a
        PLAY run) -- the launch-and-return contract, without collateral pops."""
        ws = self.ws
        self._remove_kind("desktop")
        self._sync_windows()
        if (getattr(ws, "_run_caller", None) is ws.editor_app
                and "make" in self._order):
            self._focus = "make"
        ws._dirty = True

    def close_window_kind(self, kind):
        """Close one window by its process kind -- the console's windowed exit
        hook (_exit_to_caller / _exit_settings route here) and the title-strip
        X's dispatch. Closing the Make window's EDITOR pops one level (the same
        window flips back to the picker); everything else just closes.

        (#111) autosave-only: the strip X is an exit path like any other, so
        every persistent surface hard-commits here BEFORE its window/level pops
        -- otherwise a kid mid-idle-debounce who drags-closes the window (rather
        than using the app's own CLOSE/context-X) would silently lose the last
        edit. `kind` is the window's RESOLVED top (e.g. "menu" for the make
        window showing the Editor, never the "make" group key), so this keys on
        the same real kinds close_window_kind always has."""
        ws = self.ws
        ws._dirty = True
        if kind == "writer":
            ws.writer_app.flush(force=True)   # the strip X must never lose typed notes
        if kind == "storybook":
            ws.storybook_app._commit_deck()   # same rule for an open story
        if kind == "sheets":
            ws.sheets_app.flush(force=True)   # same rule for an open sheet
        if kind == "artwork":
            ws.artwork_app._save()            # same rule for the open drawing
        if kind == "menu":
            # The "menu" WM kind is shared by the Editor AND the icon-theme editor
            # (Settings -> EDIT ICONS, paint_layer.ThemeLayer -- it spawns on the
            # same back-stack slot); each has its own commit verb.
            if ws._editing_icons:
                ws.save_icons()
            else:
                ws.editor_app.save_current()      # the Editor's active tab
        if kind == "desktop":
            self.close_player()
        else:
            self._remove_kind(kind)

    def _close_window(self, kind):
        self.close_window_kind(kind)

    def _move_window(self, win, nx, ny):
        full = self._root_canvas
        fs = self._fs()
        win.x = max(-win.w + 40 * fs, min(nx, full.w - 40 * fs))
        win.y = max(self._bar_h(), min(ny, full.h - 24 * fs))
        self.ws._dirty = True
