# Map (grep -n a name to jump there):
#   WindowRoute                  the mixin WindowedWM routes input through
#   WindowRoute._route_key       the keys to the focused window's content
#   WindowRoute._route_pointer   a press, a drag, a resize, a strip button, the content
#   WindowRoute._win_at          the topmost window under the pointer
"""The windowed WM's INPUT ROUTING: the keys go to the focused window's
content (the HOME key and a window's back chevron first), and the pointer
raises and focuses a window, drags it by its strip, resizes it from an edge
or a corner, presses its strip's buttons, or reaches its content in window
coordinates under its layout context. A mixin over `WindowedWM`, after its
paint, chrome and overlays among the bases: it runs on an input event,
never on a still frame. It owns no state of its own: the drag, the resize
and the focus it moves are the WM's.
"""

try:
    from widgets import _in
    from moy_input import PB_MIDDLE as _PB_MIDDLE
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.widgets import _in
    from runtime.moy_input import PB_MIDDLE as _PB_MIDDLE

_DRAG_MIN = 4                         # px of travel before a press becomes a drag


class WindowRoute:
    """The key and pointer routing of `WindowedWM` (a later base)."""


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
        if i.pressed("full") and self.full_key():
            return True
        content = self._content_for(win.kind)
        if content is None:
            return False
        if win.docked is not None:
            host = self._content_for("menu")
            keys = getattr(host, "dock_keys", None)
            if keys is not None and keys(i):
                return True           # the host Editor's chords reach past its game
        if (i.pressed("b") or i.pressed("stop")) and self._win_back(win):
            return True               # the window's back chevron (`_win_back`)
        if win.ctx is not None:
            self._install(win.ctx, win, draws=False)
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
        if p is None or not p.down:
            self._content_gesture = False   # finger up: the desk renders live again
        if click:
            self._grab = 0                  # a new press: whose it is is settled below
        # An in-flight RESIZE follows the pointer; released -> apply the new size.
        if self._resize is not None:
            kind, ox, oy, ow, oh, cw, ch, edges, wx, wy, rx, ry = self._resize
            win = self._wins.get(kind)
            if win is None:
                self._resize = None
            elif p.down:
                rx, ry, cw, ch = self._rubber(win, edges, wx, wy, ow, oh,
                                              px - ox, py - oy)
                self._resize = (kind, ox, oy, ow, oh, cw, ch, edges, wx, wy, rx, ry)
                # keeps_animating's gesture leg owns the repaint.
                return True
            else:
                self._resize = None
                win.saved = None                   # a manual size clears "maximized"
                win.x, win.y = rx, ry
                self._resize_window(win, cw, ch)   # the one relayout
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
                if win is not None and self._gesture_hist:
                    self._rest = (kind, gdx, gdy)
                    self._rest_end = ws._frames_drawn + self._retained_n()
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
                if win.saved is not None:          # a full-screen row: restore
                    wx, wy = self._restore_under(win, ox, oy)
                    # The desk the full screen covered: rendered live and
                    # captured once (nothing drew it meanwhile), and the
                    # whole glass restored from it (_seed_gesture_hist).
                    self._backdrop_valid = False
                    self._desk_streak = 0
                self._drag = (kind, ox - wx, oy - wy)
                self._seed_gesture_hist()   # #58: pre-move footprint, every buffer
                self._move_window(win, px - (ox - wx), py - (oy - wy))
                return True
        if self._grab == 1:
            # A press on chrome reaches no content, its release included.
            if not p.down:
                self._grab = 2
            return True
        if not self._order:
            return False
        # Taskbar chips live in the OS bar row, above every window -- and
        # under a full-screen one, which covers the bar.
        if click and self.full_cover() < 0:
            for key, rect, _label in self._chip_rects():
                if _in(px, py, rect):
                    self._chip_tap(key)
                    return True
        # Every edge and corner resizes, from a zone that straddles the frame.
        # The WM holds that press (_grab): no window's content captures it.
        hit = self._resize_zone_at(px, py) if click else None
        if hit is not None:
            key, edges = hit
            win = self._wins[key]
            if key != self._focus:
                self._move_focus(key)
                ws._dirty = True
            self._grab = 1
            self._held_in = None
            self._resize = (key, px, py, win.w, win.h, win.w, win.h, edges,
                            win.x, win.y, win.x, win.y)
            self._seed_gesture_hist()   # #58: pre-resize footprint, both buffers
            return True
        # A press held since it went down in the focused window's content --
        # a finger's, the left button's or the middle button's -- stays that
        # window's wherever the pointer goes, its release included, as a
        # browser's pointer capture keeps it: a drag scrolls on past the frame.
        held = bool(p.down) or bool(getattr(ws.input, "pointer_buttons", 0) & _PB_MIDDLE)
        key = self._held_in
        if click or key is None or key != self._focus or key not in self._wins:
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
        self._held_in = key if focused and held else None
        if click and win.docked is None:
            name = self._strip_button_hit(win, px, py)
            if name is not None:
                self._grab = 1
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
            if (win.saved is None and py < win.y + 1 + win.title_h) \
                    or self._on_row_head(win, px, py):
                # A strip, or a full-screen row's head, which a drag restores.
                self._grab = 1
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
        self._install(win.ctx, win, draws=False)
        try:
            content.handle_pointer(lx, ly, click)
        finally:
            self._install(self._root_ctx)
        return True
