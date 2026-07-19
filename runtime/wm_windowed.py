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
    from wm import FullscreenStackWM
    from layers import Layer
    from chrome import NAMES          # not palette: chrome is the device-safe home
    from widgets import _Blit, _in    # (runtime/palette.py needs colorsys -- host-only)
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.wm import FullscreenStackWM
    from runtime.layers import Layer
    from runtime.chrome import NAMES
    from runtime.widgets import _Blit, _in


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
_BORDER_TOP = NAMES["white"]          # focused window border (cream ring, all themes)
_BTN_X_FG = NAMES["red"]
_CHIP_FG = NAMES["white"]
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
        if (wm._drag is None and wm._resize is None) or wm._backdrop_disabled:
            wm._backdrop_valid = False        # live: no drag, always re-render
            if wm._gesture_hist:
                wm._gesture_hist = []         # gesture over: drop the damage trail
            self._draw_desktop(dt)
            return
        if wm._backdrop_valid:
            _perf = getattr(self.ws, "perf_capture", False)
            _t0 = _wt() if _perf else 0
            wm._blit_backdrop_cache()
            if _perf:
                self.ws._pf_wm_restore = _wt() - _t0
            return
        self._draw_desktop(dt)                # first drag frame: render + snapshot
        wm._capture_backdrop()

    def _draw_desktop(self, dt):
        self.ws.wallpaper.draw(dt)
        self.ws.bar_layer._draw_status_strip("home")

    def handle_input(self, i):
        return True

    def handle_pointer(self, px, py, click):
        if click:
            self.ws.bar_layer.handle_home_tap(px, py)
        return True


class WindowedWM(FullscreenStackWM):
    """The windowed presentation of the back-stack (spec shell_ux_v1.md §3):
    Library = the launch surface; every pushed process = a floating window over
    the wallpaper desktop, focus = top of stack. Install with
    `ws.wm = WindowedWM(ws)` right after construction
    (host_app.build_workstation(windowed=True)); requires a DISTINCT system
    canvas bigger than the 320x240 game canvas."""

    def __init__(self, ws):
        FullscreenStackWM.__init__(self, ws)
        if ws._sys_canvas is None:
            raise ValueError("WindowedWM needs a distinct (big) system canvas")
        ws.windowed_chrome = True     # bar/dock: suppress OS chrome inside windows
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
        # Dirty-union gesture restore (#58 "smooth like a real OS"): during a
        # drag/resize only the moving window's recent footprint needs the backdrop
        # re-stamped -- a full-screen 1.2MB restore per frame is the drag path's
        # dominant cost on the P4 (PSRAM-bandwidth-bound, no accelerator helps a
        # 1:1 copy). _gesture_hist holds the last TWO frames' damage extents (two:
        # a ping-pong back buffer is two frames stale; a single-buffer host needs
        # one -- the superset covers both). Seeded with the window's extent at
        # gesture START (both physical buffers hold its pre-gesture stamp).
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

    def _slots(self):
        """Collapse the back-stack (above the root) into window SLOTS: consecutive
        kinds in the same _GROUP share one slot, and the slot's content is the
        TOPMOST of its kinds (picker+menu -> one "make" window showing whichever
        is up). Returns [[slot_key, kind], ...] bottom -> top."""
        slots = []
        for k in self._stack[1:]:
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
        if len(self._stack) == 1:
            # Just the launcher root: byte-identical to the fullscreen tier.
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
            self._full_debt -= 1      # paying the second-buffer debt: full paint
        else:
            self._full_debt = 1       # a change painted: the OTHER buffer owes one
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
            cache.blit_strip(self._root_canvas, 0, 0)
            self._backdrop_valid = True
        except Exception:  # noqa: BLE001 -- a failed capture (OOM) forfeits the
            self._backdrop_valid = False   # cache; the drag just re-renders live
            self._backdrop = None

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
        if self._resize is not None and self._resize[0] == win.kind:
            w = max(w, self._resize[5])
            h = max(h, self._resize[6])
        return (win.x - 2, win.y - 2, w + 7, h + 7)

    def _seed_gesture_hist(self):
        """Prime the damage history at gesture START: both physical buffers hold
        the window's pre-gesture stamp, so the first two restores must cover it."""
        ext = self._gesture_extent()
        self._gesture_hist = [ext, ext] if ext is not None else []

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
        if (stamp is None or self._union_disabled or ext is None
                or not self._gesture_hist):
            rc.blit_strip(self._backdrop, 0, 0)
            if ext is not None:
                self._gesture_hist = [self._gesture_hist[-1], ext] \
                    if self._gesture_hist else [ext, ext]
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
            if self._resize is not None and self._resize[0] == win.kind \
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
        self._gesture_hist = [self._gesture_hist[-1], ext]

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
        # The frame router's game->system boundary composite: a no-op here -- the
        # window layer blits the game canvas into the player WINDOW itself, and
        # stamping it full-viewport would paint over the desktop.
        if not self._order:
            FullscreenStackWM.composite_game(self)

    # -- drawing ---------------------------------------------------------------

    def _draw_windows(self, dt):
        self._sync_windows()
        n = len(self._order)
        for i in range(n):
            key = self._order[i]
            win = self._wins[key]
            focused = (key == self._focus)
            if win.minimized:
                continue
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
        self._draw_taskbar_chips()
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

    def _win_chrome(self, win, focused):
        """Title strip (title + min/max/X) + border + drop shadow + resize grip.
        The highlight follows INPUT FOCUS (which moves on click), not the stack."""
        sc = self._root_canvas
        ws = self.ws
        th = ws.theme_colors
        fs = self._fs()
        sh = 3
        sc.rect(win.x + sh, win.y + win.h, win.w, sh, _SHADOW)     # bottom shadow
        sc.rect(win.x + win.w, win.y + sh, sh, win.h, _SHADOW)     # right shadow
        sc.rectb(win.x, win.y, win.w, win.h,
                 _BORDER_TOP if focused else th["dim"])
        # Title strip: label left, [minimize][maximize][close] right. Focused =
        # the theme's title tint with its ink (the active-title cue, Picotron-
        # style); unfocused = the theme's panel field with dim ink.
        strip_bg = th["title"] if focused else th["panel"]
        strip_fg = th["title_ink"] if focused else NAMES["light_grey"]
        sc.rect(win.x + 1, win.y + 1, win.w - 2, win.title_h, strip_bg)
        sc.rect(win.x + 1, win.y + win.title_h, win.w - 2, 1,
                _BORDER_TOP if focused else th["dim"])
        title = self._win_title(win)
        btns = self._strip_buttons(win)
        first_btn_x = btns[-1][1][0] if btns else win.x + win.w
        maxc = max(0, (first_btn_x - (win.x + 4 * fs)) // (8 * fs))
        if maxc > 0:
            sc.print(title[:maxc], win.x + 4 * fs, win.y + 1 + 5 * fs, strip_fg, 1)
        for name, rect in btns:
            glyph = {"close": "close", "max": "app", "min": "minus"}[name]
            ws._glyph(glyph, rect, _BTN_X_FG if name == "close" else strip_fg, sc)
        # Resize grip (focused window only): three diagonal steps in the corner.
        if focused:
            gx, gy, gw, gh = self._grip_rect(win)
            for i in range(3):
                d = (i + 1) * (gw // 4)
                sc.rect(gx + gw - d, gy + gh - 2 * fs, d, fs, _BORDER_TOP)

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

    def _draw_app_window(self, win, focused, dt):
        # Freeze the content render while THIS window is being dragged/resized
        # (#58 drag perf): its buffer can't change under a drag (no input reaches
        # the content, and a resize only rubber-bands until release), so re-running
        # the editor-tab layout every drag frame is pure waste -- blit the retained
        # buffer at the new position instead. A drag of ANOTHER window still lets
        # this one render live (it's not the one moving).
        if self._resize is not None and self._resize[0] == win.kind \
                and self._live_resize_ok():
            # Live-body resize: the frame follows the grip, content crops.
            self._draw_resizing_window(win, focused,
                                       self._resize[5], self._resize[6])
            return
        moving = ((self._drag is not None and self._drag[0] == win.kind)
                  or (self._resize is not None and self._resize[0] == win.kind))
        if focused and not moving:
            # Live: render the focused app into its buffer at the window's layout.
            self._install(win.ctx)
            try:
                self._content_for(win.kind).draw(dt)
            finally:
                self._install(self._root_ctx)
        # Drag stamp-defer (#58): while THIS window drags on a device with an
        # async 1:1 blitter (P4SystemCanvas.blit_strip_async -> PPA DMA), draw
        # the chrome FIRST -- strip/borders/shadow/title are all DISJOINT from
        # the content rect -- and kick the content stamp as the frame's LAST
        # framebuffer write; the deferred present (present_pending) then overlaps
        # the ~24ms DMA with the next loop's input poll, the same machinery as
        # the quiet-game-frame composite. Only the TOP-drawn window during a
        # DRAG (a resize redraws live-body above; the grip drawn under the stamp
        # simply hides while dragging, which is fine). Host/web canvases lack
        # the hook, so their path is byte-identical.
        if (self._drag is not None and self._drag[0] == win.kind
                and self._order and self._order[-1] == win.kind):
            asb = getattr(self._root_canvas, "blit_strip_async", None)
            if asb is not None:
                self._win_chrome(win, focused)
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
        self._win_chrome(win, focused)

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

    def _draw_taskbar_chips(self):
        sc = self._root_canvas
        fs = self._fs()
        th = self.ws.theme_colors
        for key, (x, y, w, h), label in self._chip_rects():
            win = self._wins[key]
            focused = (key == self._focus and not win.minimized)
            bg = th["accent"] if focused else th["panel"]
            if focused:
                fg = NAMES["black"]                # ink on the accent CTA chip
            else:
                fg = th["edge"] if win.minimized else _CHIP_FG
            sc.rect(x, y, w, h, bg)
            sc.rectb(x, y, w, h, th["dim"] if win.minimized else _BORDER_TOP)
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

    def _route_pointer(self, px, py, click):
        ws = self.ws
        self._sync_windows()
        p = ws.pointer
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
            for name, rect in self._strip_buttons(win):
                if _in(px, py, rect):
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
        window flips back to the picker); everything else just closes."""
        ws = self.ws
        ws._dirty = True
        if kind == "writer":
            ws.writer_app.flush(force=True)   # the strip X must never lose typed notes
        if kind == "storybook":
            ws.storybook_app._commit_deck()   # same rule for an open story
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
