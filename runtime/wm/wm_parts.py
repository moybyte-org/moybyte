# Map (grep -n a name to jump there):
#   _LayoutCtx                    every layout object a relayout rebuilds
#   _Win                          one open window
#   _Parked                       the desk's window records while console mode runs
#   _WindowStackLayer             the one layer that presents the window stack
#   _RepeatLayer                  a frame that repeats the last painted one
#   _PlayerWindowLayer            the quiet-frame partial repaint of a cart window
#   _PopoverUnderLayer            a popover frame's first half: the pixels put back
#   _PopoverOverLayer             a popover frame's second half: the popover painted
#   _bands / _grow                the rect arithmetic the shown ring and damage use
"""The windowed WM's PARTS: the records and layers `WindowedWM` (wm_windowed.py)
is built from -- a window's record and layout context, the parked desk, the
layers its draw stacks hold -- and the rect helpers its paint and routing
share. Each layer is a thin face that calls back into the WM; none owns state
of the WM's.
"""

try:
    from layers import Layer
    from widgets import _ticks_ms
    import ui_draw as _ui_draw
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.layers import Layer
    from runtime.widgets import _ticks_ms
    from runtime import ui_draw as _ui_draw


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
        self.saved = None             # full screen: the rect a restore puts
                                      # back (x, y, w, h); None while restored
        self._content = None          # content_rect's last answer
        self.docked = None            # the rect it stands in, docked in a host's
                                      # pane (the desk Editor's Run slot): no
                                      # frame and no strip; None: a window
        self.undock = None            # where it goes back to (x, y, w, h)
        self.dock_off = None          # docked: its corner from its host's
                                      # content corner, which it follows
        self.own = False              # a game moved out by the pause card's
                                      # Play in a window: never docked, and
                                      # no Editor's playtest (it outlives one)
        self.corner = 0               # full screen: where its controls start
        self.row = 0                  # full screen: the rows the WM's row takes
                                      # above the content (0 where the app's
                                      # own band is the row)
        self._buf_stale = True        # buf is behind the content's truth (fresh
                                      # buffer, or a gesture's _direct_render
                                      # painted past it) -> the next paint must
                                      # render live before the content freeze
                                      # (_content_static) may reuse it
        self._look_gen = -1           # the look buf's content was drawn under
        self._marked = False          # buf was drawn focused: it holds a focus mark
        # While buf is stale: [x0, y0, x1, y1, known], the part of it the
        # content changed since buf last held its page (known 0: unknown).
        self._lag = [0, 0, 0, 0, 0]
        # the full-screen row last painted over it: w, h, title, buttons,
        # look gen, font scale (`_row_note`)
        self._row_sig = [None, None, None, None, None, None]
        self._row_moved = False       # ...and it differs from the one before

    def full(self):
        """Whether the window is full screen (maximised, docs/media/look_2026-10/
        MAXIMISE_VARIANTS.md's V2): the whole glass, no frame and no strip."""
        return self.saved is not None

    def content_rect(self):
        """The inner rect the content occupies: below the WM title strip and
        inside the frame, its `edge` off the sides, and above the foot (the
        frame's bottom line where it has no foot); full screen, the glass
        under the WM's row."""
        if self.docked is not None:
            return self.docked
        if self.saved is not None:
            x = self.x
            y = self.y + self.row
            w = self.w
            h = self.h - self.row
        else:
            e = self.edge
            x = self.x + 1 + e
            y = self.y + 1 + self.title_h
            w = self.w - 2 - 2 * e
            h = self.h - 2 - self.title_h - max(self.foot, e)
        # The rect it answered last, while it holds: asked every frame, it
        # allocates nothing until the window moves.
        r = self._content
        if r is None or r[0] != x or r[1] != y or r[2] != w or r[3] != h:
            r = self._content = (x, y, w, h)
        return r

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


class _RepeatLayer(Layer):
    """A frame that repeats the last painted one (surface model §14.6):
    nothing changed since it, so the buffer it presented holds this frame's
    whole picture, and a copy of it brings the buffer this frame paints up
    to it in place of a repaint. A refused copy paints the full stack."""

    id = "repeat"
    domain = "system"

    def __init__(self, wm, lead=False):
        self.wm = wm
        self.lead = lead

    def draw(self, dt):
        wm = self.wm
        root = wm._root_canvas
        # A debt frame copies the rows the frames since its buffer was
        # painted changed (`_debt_rows`); a lead copy, under a playtest's
        # frame, the whole glass.
        r = None if self.lead else wm._debt_rows()
        if r is None or r[2]:
            ok = root.copy_last(0, 0, root.w, root.h)
            px = root.w * root.h
        elif r[1] > r[0]:
            ok = root.copy_last(0, r[0], root.w, r[1] - r[0])
            px = root.w * (r[1] - r[0])
        else:
            ok = True
            px = 0
        if ok:
            wm._repeats_n += 1
            wm._debt_px += px
            wm._repeat_note()
            if not self.lead:
                _ui_draw.repeat_frame()
            return
        # A lead copy (a quiet frame paying a buffer's debt, the quiet
        # stack's layers after it): refused, the layers after it stand down.
        wm._lead_refused = self.lead
        for layer in wm._cache_draw:
            layer.draw(dt)


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
        if win is not None and not win.minimized and not wm._lead_refused:
            # full=False: the letterbox bezel + strip/shadow chrome are static and
            # already present in BOTH ping-pong buffers (the two full paints any
            # change costs); repainting them measured ~half the quiet frame.
            # Under the system menu's scrim the frame is washed after its
            # composite, which therefore lands now, not at the present.
            up = wm._scrim_up()
            wm._draw_player_window(win, True, wm._focus == "desktop", dt,
                                   full=False, defer=not up)
            if up and wm._wash_all:
                # The scrim's first frame: every rect it covers washed once,
                # the system button's tile as the menu now stands.
                wm._wash(wm.ws.menu_ui.scrim_rects())
                wm.ws.bar_layer.redraw_tile("desk")
            elif up:
                wm._scrim_game(win)


def _overlap(a, b):
    return (a[0] < b[0] + b[2] and b[0] < a[0] + a[2]
            and a[1] < b[1] + b[3] and b[1] < a[1] + a[3])


def _inside(a, b):
    """Rect `a` lies within rect `b` (None: nothing does)."""
    return (b is not None and b[0] <= a[0] and b[1] <= a[1]
            and a[0] + a[2] <= b[0] + b[2] and a[1] + a[3] <= b[1] + b[3])


# The dirty mark the home's X leaves (`leave_console`): the desk comes back
# whole, and no window's content changed with it, so §14.1's freeze holds
# the focused one as it holds the others. Any handler's mark after it is
# True, which the freeze reads as a change.
MODE_MARK = 2

# The most physical buffers a root canvas rotates through (the Waveshare P4's
# triple framebuffer): the shown ring keeps one entry more.
_SHOWN = 3


def _bands(r, hole):
    """Rect `r` (x, y, w, h) less `hole`: itself, or the bands above, below,
    left and right of the hole's part in it."""
    if hole is None:
        return (r,)
    x0, y0 = max(r[0], hole[0]), max(r[1], hole[1])
    x1 = min(r[0] + r[2], hole[0] + hole[2])
    y1 = min(r[1] + r[3], hole[1] + hole[3])
    if x0 >= x1 or y0 >= y1:
        return (r,)
    bands = ((r[0], r[1], r[2], y0 - r[1]), (r[0], y1, r[2], r[1] + r[3] - y1),
             (r[0], y0, x0 - r[0], y1 - y0), (x1, y0, r[0] + r[2] - x1, y1 - y0))
    return tuple(b for b in bands if b[2] > 0 and b[3] > 0)


def _grow(c, x0, y0, x1, y1):
    """Grow `c`, a rect [x0, y0, x1, y1), over another; an empty one (no
    area) grows nothing, and an empty `c` becomes the other."""
    if x1 <= x0 or y1 <= y0:
        return
    if c[2] <= c[0] or c[3] <= c[1]:
        c[0] = x0
        c[1] = y0
        c[2] = x1
        c[3] = y1
        return
    if x0 < c[0]:
        c[0] = x0
    if y0 < c[1]:
        c[1] = y0
    if x1 > c[2]:
        c[2] = x1
    if y1 > c[3]:
        c[3] = y1


class _PopoverUnderLayer(Layer):
    """A popover frame's first half (docs/theme_and_shell_2026-11.md §8.6): put
    back the pixels a closed (or moved) popover covered, from the snapshot
    taken when it opened -- before the player window repaints over them."""

    id = "popover_under"
    domain = "system"

    def __init__(self, wm):
        self.wm = wm

    def draw(self, dt):
        if not self.wm._lead_refused:
            self.wm._pop_restore(True)
            self.wm._pop_marks_paint()


class _PopoverOverLayer(Layer):
    """A popover frame's second half: the open popover painted over the
    retained frame (its snapshot taken first), its rect the frame's damage."""

    id = "popover_over"
    domain = "system"

    def __init__(self, wm):
        self.wm = wm

    def draw(self, dt):
        if not self.wm._lead_refused:
            self.wm._pop_paint(True)
