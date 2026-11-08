# Map (grep -n a name to jump there):
#   WindowOverlays                     the mixin WindowedWM's overlays live on
#   WindowOverlays.popover_changed     the popover opened, moved or closed: its debts
#   WindowOverlays.pause_changed       the pause menu came or went: its debts
#   WindowOverlays._pop_wash_changed   the riding scrim's debts
#   WindowOverlays._pop_restore        put back what a closed popover covered
#   WindowOverlays._pop_paint          the open popover over the retained frame
#   WindowOverlays._pop_wash_paint     the riding scrim over its snapshot
#   WindowOverlays._pause_paint        the riding pause menu's scrim and card
#   WindowOverlays._scrim_game         wash the game's fresh frame under the scrim
#   WindowOverlays.prealloc            the riding scrim's snapshot, minted when idle
#   WindowOverlays._ride_refused       a snapshot the glass has no room for: nothing rides
"""The windowed WM's OVERLAYS on a retained desk (docs/theme_and_shell_2026-11.md
§8.6): the popover (the system menu and a card's menu) painted in its rect
alone, the pause menu riding over a paused playtest's window, the system
menu's scrim washed once over a still desk, and each one's debts to the
root's other buffers -- the snapshots they put back and the rects they owe.
A mixin over `WindowedWM`, after `WindowChrome` among its bases: these run
on the frames an overlay is up or changes. The predicates the draw stack
reads on every frame (`_pop_live`, `_scrim_up`, `_wash_rides` and the rest)
stay on the class. It owns no state of its own: the slots and seqs it reads
are the WM's, made in `WindowedWM.__init__`.
"""

try:
    from wm_parts import _overlap, _inside, _bands, _grow
    import ui_draw as _ui_draw
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.wm_parts import _overlap, _inside, _bands, _grow
    from runtime import ui_draw as _ui_draw


class WindowOverlays:
    """The popover, pause and wash overlays of `WindowedWM` (a later base)."""

    def _game_rect(self, win):
        """The rect a quiet frame composites the game into, on the glass."""
        ox, oy, scale = self._player_view(win)
        gc = self.ws.run.canvas
        view = None if self.ws.run.app_window_canvas else self._view_src()
        gw, gh = (view[2], view[3]) if view else (gc.w, gc.h)
        return (ox, oy, gw * scale, gh * scale)

    def _scrim_game(self, win):
        """Wash the game's fresh frame where the system menu's scrim lies:
        the pixels this frame painted, so they darken once (D-01)."""
        x, y, w, h = self._game_rect(win)
        root = self._root_canvas
        th = self.ws.theme_colors
        for r in self.ws.menu_ui.scrim_rects():
            x0 = x if x > r[0] else r[0]
            y0 = y if y > r[1] else r[1]
            x1 = min(x + w, r[0] + r[2])
            y1 = min(y + h, r[1] + r[3])
            if x0 < x1 and y0 < y1:
                _ui_draw.scrim(root, th, (x0, y0, x1 - x0, y1 - y0))

    def popover_changed(self):
        """The popover opened, moved its cursor or closed. Riding: owe its
        rect to every retained buffer -- a paint while it is open, the
        snapshot put back once it is closed. Otherwise the frame repaints."""
        if not self._pop_rides():
            self.ws._dirty = True
            return
        self._pop_seq += 1
        m = self.ws.sysmenu
        n = self._pop_n()
        self._pop_face_ok = False
        if not self._pop_wash_changed(n):
            self.ws._dirty = True
        slots = self._pop_slots
        cur = self._pop_cur
        rect = self.ws.menu_ui.panel_rect() if m.open else None
        if cur is not None and slots[cur][1] != rect:
            slots[cur][2] = n             # closing (or moved): restore owed
            cur = self._pop_cur = None
        self._pop_marks(n)
        if rect is None:
            return
        if cur is None:
            j = 0 if slots[0][1] is None or slots[0][2] <= 0 else 1
            if slots[j][1] is not None and slots[j][2] > 0:
                self.ws._dirty = True     # both slots owed: repaint whole
                self._pop_reset()
                j = 0
            s = slots[j]
            if not self._room(s, rect):
                self.ws._dirty = True     # no room for its snapshot: repaint whole
                self._pop_reset()
                return
            s[1] = rect
            s[3] = False
            cur = self._pop_cur = j
        slots[cur][2] = n

    def popover_owed(self):
        """True while a retained buffer is owed the popover's rect."""
        for s in self._pop_slots:
            if s[2] > 0:
                return True
        return self._pop_wash[2] > 0 or self._pause_owed > 0 or self._mark_owed > 0

    def _pop_marks(self, n):
        """The popover took the keys from the focused window or gave them
        back, and the window draws its focus mark accordingly (one a
        screen): owe its stamp, as it now draws, to every buffer. The
        system menu's wash keeps its own (`_wash_focus`); a window under
        another repaints whole."""
        win = self._wins.get(self._focus) if self._focus is not None else None
        if (win is None or win.minimized or win.kind == "desktop" or win.buf is None
                or win._marked == self._marks_for(win)):
            return
        if self._wash_win[0] is win and not self._mark_kept:
            return
        if self._order[-1] != self._focus:
            self.ws._dirty = True
            return
        self._mark_owed = n

    def _pop_marks_paint(self):
        """A ride frame's owed stamp of the focused window (`_pop_marks`):
        its buffer drawn as it shows now first -- kept as it was and drawn
        without its mark, or put back -- then stamped, before the popover
        paints over it."""
        if self._mark_owed <= 0:
            return
        win = self._wins.get(self._focus) if self._focus is not None else None
        if win is None or win.minimized or win.buf is None:
            self._mark_owed = 0
            return
        want = self._marks_for(win)
        if win._marked != want:
            if not want:
                self._wash_focus()
                self._mark_kept = self._wash_win[0] is win
            elif self._mark_kept and self._wash_win[0] is win:
                self._mark_kept = False
                self._wash_focus_back()
            else:
                self._prewarm(win)
        else:
            self._fresh(win)              # the stamp reads its page
        cx, cy, cw, ch = win.content_rect()
        self._root_canvas.blit_strip(win.buf, cx, cy)
        self._win_grip(win, True)         # the one piece of chrome inside the content
        self._pop_note((cx, cy, cw, ch))
        self._mark_owed -= 1

    def pause_changed(self):
        """The pause menu opened, moved or closed. Riding (`_wash_rides`):
        its card is owed to every retained buffer, its scrim as the system
        menu's is; otherwise the frame repaints."""
        if not self._pop_rides():
            self.ws._dirty = True
            return
        self._pop_seq += 1
        w = self._pop_wash
        was = w[4]
        n = self._pop_n()
        if not self._pop_wash_changed(n):
            self.ws._dirty = True
        elif self._pause_rides():
            if w[3] or w[2] > 0:
                self._pause_owed = n      # its card, in every buffer
            else:
                self.ws._dirty = True     # a whole frame since: no snapshot
        elif not (was and w[2] > 0):
            self.ws._dirty = True         # not its riding close

    def _wash_rects(self):
        """The rects the riding scrim washes: the pause menu's, or the
        system menu's bands."""
        ws = self.ws
        if ws.pause.open:
            return (ws.pause.scrim_rect(),)
        return ws.menu_ui.scrim_rects()

    def _pop_wash_changed(self, n):
        """Owe the scrim's change to every retained buffer: its wash while it
        is up, its snapshot put back once it is down. False when the change
        cannot ride and the frame repaints whole: the scrim came back while
        a buffer is still owed the last one's restore, or it goes with no
        snapshot to put back, or another modal's scrim stays. A scrim whose
        snapshot the glass has no room for comes up whole, as one that came
        up while the frame repainted does, and stays so."""
        w = self._pop_wash
        up = self._wash_rides()
        if up and not w[4] and self._scrim_was:
            up = False                    # the scrim came up whole: it stays so
        rect = self._wash_bbox() if up and not w[4] else None
        if rect is not None and not self._room(w, rect):
            up = False
        if up == w[4]:
            return True
        self._wash_seq += 1
        ride = True
        if up:
            ride = w[1] is None
            w[1] = rect
            w[3] = False
            sp = self._pause_span
            sp[0] = sp[1] = sp[2] = sp[3] = 0
        elif self._scrim_up() or not w[3]:
            ride = False
            w[1] = None
            w[3] = False
        w[4] = up
        w[2] = n if ride else 0
        return ride

    def _wash_bbox(self):
        """The rect the riding scrim's snapshot takes: its rects' bounds."""
        rects = self._wash_rects()
        x0 = min(r[0] for r in rects)
        y0 = min(r[1] for r in rects)
        x1 = max(r[0] + r[2] for r in rects)
        y1 = max(r[1] + r[3] for r in rects)
        return (x0, y0, x1 - x0, y1 - y0)

    def _room(self, s, rect):
        """Slot `s`'s snapshot layer minted at `rect`'s size before a ride
        counts on it: False where the glass has no room, and the change
        repaints whole instead. Asked once a change, never a frame."""
        try:
            self._grown(s, 0, rect[2], rect[3])
        except MemoryError:
            return False
        return True

    def popover_takes_keys(self):
        """The open popover owns the keyboard and paints its own change, so
        a key press does not mark the frame dirty (console.handle_input);
        so does the pause menu while it rides."""
        return (self.ws.sysmenu.open or self._pause_rides()) and self._pop_rides()

    def _pop_reset(self):
        self._pop_seq += 1
        self._wash_seq += 1
        self._pop_face_ok = False
        for s in self._pop_slots:
            s[1] = None
            s[2] = 0
            s[3] = False
        self._pop_cur = None
        w = self._pop_wash
        w[1] = None
        w[2] = 0
        w[3] = False
        w[4] = False
        self._pause_owed = 0
        self._mark_owed = 0
        self._mark_kept = False
        self._wash_focus_drop()

    def _pop_note(self, r):
        nd = getattr(self._root_canvas, "note_damage", None)
        if nd is not None:
            nd(r[0], r[1], r[2], r[3])

    def _pop_restore(self, ride):
        """Put back each closing popover's rect (and, on a full frame, the
        open one's, so the frame starts as if no popover were drawn), then
        the scrim's: a full frame repaints what it washed, and it is spent."""
        root = self._root_canvas
        cur = self._pop_cur
        self._pop_restore_slots(root, cur, ride)
        if not ride:
            # A whole frame draws the focused window as it shows now: one
            # buffer's owed stamp, and its kept buffer, are spent.
            if self._mark_owed > 0:
                self._mark_owed -= 1
            if self._mark_kept:
                self._mark_kept = False
                self._wash_win[0] = None
        w = self._pop_wash
        if w[1] is None:
            return
        if not ride:
            if not w[4]:
                w[1] = None
                w[3] = False
                self._wash_focus_drop()
            w[2] = 0
            return
        if w[2] <= 0:
            return
        if not w[4]:
            r = w[1]
            hole = None
            if self._pop_game_next:
                win = self._wins.get("desktop")
                if win is not None and not win.minimized:
                    hole = self._game_rect(win)   # the game's frame comes next, opaque
            lay = w[0]
            # Put back whole, it is the frame's last write there: the engine
            # copies it after the frame has drawn (`blit_strip_async`).
            if not (hole is None and lay.w == r[2] and lay.h == r[3]
                    and hasattr(root, "blit_strip_async")
                    and root.blit_strip_async(lay, r[0], r[1])):
                for b in _bands(r, hole):
                    root.blit_strip_rect(lay, r[0], r[1], b[0], b[1], b[2], b[3])
            self._pop_note(r)
            self._wash_focus_back()
            w[2] -= 1
            if w[2] <= 0:
                w[1] = None
                w[3] = False
        if self.full_cover() < 0:               # a full-screen window covers the bar
            self.ws.bar_layer.redraw_tile("desk")   # the system button, as it now stands

    def _pop_restore_slots(self, root, cur, ride):
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

    def _grown(self, s, i, w, h):
        """Layer `s[i]` of the root's, at least `w` by `h`: a new one at the
        larger of both sizes where it is smaller, the old one released."""
        lay = s[i]
        if lay is not None and lay.w >= w and lay.h >= h:
            return lay
        ow, oh = (lay.w, lay.h) if lay is not None else (0, 0)
        s[i] = None
        self._release_layer(lay)
        lay = s[i] = self._root_canvas.new_layer(max(w, ow), max(h, oh))
        return lay

    def _snap(self, s):
        """Snapshot the pixels under slot `s`'s rect from the root canvas
        (a popover's, or the riding scrim's), in the layer `_room` minted
        when its ride began. False where a rect that grew since finds no
        room: nothing rides, and the frame after repaints whole
        (`_ride_refused`)."""
        x, y, w, h = s[1]
        try:
            lay = self._grown(s, 0, w, h)
        except MemoryError:
            self._ride_refused()
            return False
        lay.blit_strip(self._root_canvas, -x, -y)
        s[3] = True
        return True

    def _ride_refused(self):
        """A snapshot found no room: every overlay's ride is dropped and the
        next frame repaints whole, as a WM with no retained root paints
        them. An overlay's next change asks again: a failed ask costs that
        change, never every frame after it."""
        self._pop_reset()
        self.ws._dirty = True
        self.ws._frame_requested = True   # asked from within a draw (request_frame)

    def _pop_capture(self, s):
        """Snapshot slot `s` (`_snap`), and note whether it lies over the
        playtest's window. False where it found no room."""
        if not self._snap(s):
            return False
        # Whether the playtest's window repaints under it, read once here:
        # nothing moves a window while a popover is open (a tap outside
        # closes it), and a play frame allocates nothing.
        win = self._wins.get("desktop")
        self._pop_over_game = (win is not None and not win.minimized
                               and _overlap(s[1], self._win_extent(win)))
        return True

    def _pop_paint(self, ride):
        """Paint the open popover: snapshot what it covers first (always on
        a full frame, whose stack just repainted it), then the panel. A ride
        paints it while a buffer is owed it, or every frame it overlaps the
        playtest's window, which repaints beneath it."""
        cur = self._pop_cur
        m = self.ws.sysmenu
        self._pop_wash_paint(ride)
        if cur is None or not m.open:
            if not ride:
                self.ws.menu_ui._draw_sysmenu()
            return
        s = self._pop_slots[cur]
        fresh = not ride or not s[3]
        if fresh:
            # Taken at its rect as it is now: a card that grew (the networks
            # a scan found) repainted the frame whole at its new size.
            s[1] = self.ws.menu_ui.panel_rect()
            if not self._pop_capture(s):
                self.ws.menu_ui._draw_sysmenu()
                return
        r = s[1]
        if (ride and not fresh and self._pop_over_game and self._pop_face_ok
                and "desktop" in self._wins):
            # The playtest repainted under it and it has settled: its face
            # put back, one copy rather than a paint of the component, in
            # every buffer it is still owed to as well.
            self._root_canvas.blit_strip_rect(self._pop_face, r[0], r[1], r[0], r[1], r[2], r[3])
            self._pop_note(r)
            if s[2] > 0:
                s[2] -= 1
            return
        if ride and s[2] <= 0:
            if "desktop" not in self._wins:
                self._pop_note(r)         # a still frame: say so, not "all"
                return
            if not self._pop_over_game:
                return
        if self._repeating and not fresh and s[2] > 0:
            # The last frame's card, as it was: inside the wash this frame
            # copied, or copied itself.
            w = self._pop_wash[1]
            if ((self._pop_wash_f == self.ws.frame_state.frames_drawn and _inside(r, w))
                    or self._root_canvas.copy_last(r[0], r[1], r[2], r[3])):
                self._pop_note(r)
                s[2] -= 1
                return
        if not fresh:
            # the look's card (the menu's or a component's) has rounded,
            # shadowed edges: paint it over what it covers, never over its
            # own last paint
            self._root_canvas.blit_strip_rect(s[0], r[0], r[1], r[0], r[1], r[2], r[3])
        ws = self.ws
        asked = ws._frame_requested
        ws._frame_requested = False
        ws.menu_ui._draw_sysmenu()
        moving = ws._frame_requested
        ws._frame_requested = asked
        if moving and ws.menu_ui.panel_rect() != r:
            # It sized itself again (the networks a scan found): the next
            # frame repaints whole, its snapshot taken at the new rect.
            ws._frame_requested = True
            self._pop_face_ok = False
        elif moving:
            # The card has not settled (it opens, its focus moves): its rect
            # is owed to every buffer from the next frame on, not the desk,
            # and no face is kept of it.
            self._pop_face_ok = False
            s[2] = self._pop_n() + 1
            self._pop_seq += 1
        elif self._pop_over_game and not self._pop_face_ok:
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

    def _pop_wash_paint(self, ride):
        """The scrim, before the card: on a full frame always; on a ride
        while a buffer is owed it, the snapshot taken before the first."""
        w = self._pop_wash
        if not ride:
            # A full frame washes what it painted. Its snapshot would cost
            # the frame a copy of the glass: the next close repaints whole.
            w[2] = 0
            w[3] = False
            self.ws.menu_ui.scrim()
            return
        if not w[4]:
            return
        if w[2] <= 0:
            if self._pause_owed > 0:
                self._pause_paint(False)
            return
        if self._wash_repeats and w[3] and self._root_canvas.copy_last(*w[1]):
            # The last frame washed this same scrim: its buffer's wash, with
            # what that frame's card left over it, which the card's own
            # paint puts back first where it changed.
            self._pop_note(w[1])
            self._pop_wash_f = self.ws.frame_state.frames_drawn
            w[2] -= 1
            if self._pause_owed > 0:
                self._pause_paint(self._repeating)
            return
        if self._pause_rides():
            # Its game's frame, held, composited where a quiet frame deferred
            # it past this buffer: what the wash goes over.
            game = self._wins["desktop"]
            self._draw_player_window(game, False, self._focus == "desktop", 0,
                                     full=False, defer=False)
        if not w[3]:
            if not self._snap(w):
                return
            self._wash_focus()
        win = self._wash_win[0]
        if win is not None:
            cx, cy = win.content_rect()[:2]
            self._root_canvas.blit_strip(win.buf, cx, cy)
            self._win_chrome(win, True, quiet=False)
        self._wash(self._wash_rects())
        self._pop_note(w[1])
        w[2] -= 1
        if self._pause_owed > 0:
            self._pause_paint(True)

    def _wash(self, rects, clip=None):
        """The scrim over `rects`, within `clip` where given."""
        root = self._root_canvas
        th = self.ws.theme_colors
        for r in rects:
            if clip is not None:
                x0, y0 = max(r[0], clip[0]), max(r[1], clip[1])
                x1 = min(r[0] + r[2], clip[0] + clip[2])
                y1 = min(r[1] + r[3], clip[1] + clip[3])
                if x0 >= x1 or y0 >= y1:
                    continue
                r = (x0, y0, x1 - x0, y1 - y0)
            _ui_draw.scrim(root, th, r)

    def _pause_paint(self, washed):
        """The pause menu's card over the riding scrim: where the buffer
        was not washed this frame, what its paints covered put back from
        the snapshot and washed again first, since its shadow blends. A
        card that has not settled owes every buffer its next paint."""
        ws = self.ws
        sp = self._pause_span
        if self._repeating:
            # The last frame's card over its scrim, as this one would paint
            # it: copied where its paints have covered, the card's rect too.
            r = ws.pause.card_rect()
            if r is not None:
                _grow(sp, r[0], r[1], r[0] + r[2], r[1] + r[3])
            s = (sp[0], sp[1], sp[2] - sp[0], sp[3] - sp[1])
            if s[2] > 0 and ((self._pop_wash_f == ws.frame_state.frames_drawn
                              and _inside(s, self._pop_wash[1]))
                             or self._root_canvas.copy_last(s[0], s[1], s[2], s[3])):
                self._pop_note(s)
                self._pause_owed -= 1
                if self._pause_owed <= 0 and r is not None:
                    sp[0], sp[1], sp[2], sp[3] = r[0], r[1], r[0] + r[2], r[1] + r[3]
                return
        if not washed and sp[2] > sp[0]:
            w = self._pop_wash
            o = w[1]
            r = (sp[0], sp[1], sp[2] - sp[0], sp[3] - sp[1])
            self._root_canvas.blit_strip_rect(w[0], o[0], o[1], r[0], r[1], r[2], r[3])
            self._wash(self._wash_rects(), r)
        ws.pause.draw_card()
        r = ws.pause.card_rect()
        if r is not None:
            _grow(sp, r[0], r[1], r[0] + r[2], r[1] + r[3])
            self._pop_note((sp[0], sp[1], sp[2] - sp[0], sp[3] - sp[1]))
        self._pause_owed -= 1
        if ws._frame_requested:
            ws._frame_requested = False
            self._pause_owed = self._pop_n()
            self._pop_seq += 1
        elif self._pause_owed <= 0 and r is not None:
            # every buffer holds this paint now: the span is its rect
            sp[0], sp[1], sp[2], sp[3] = r[0], r[1], r[0] + r[2], r[1] + r[3]

    def _pause_full(self, drawn=False):
        """A full frame under the riding pause menu, before its scrim and
        card draw: what the scrim covers is snapshotted again, so its next
        change still rides; `drawn`, after: the span takes the card."""
        w = self._pop_wash
        if not w[4] or w[1] is None:
            self._pause_owed = 0
            return
        if drawn:
            r = self.ws.pause.card_rect()
            if r is not None:
                _grow(self._pause_span, r[0], r[1], r[0] + r[2], r[1] + r[3])
            return
        if not self._snap(w):
            return
        w[2] = 0
        self._pause_owed = 0

    def _wash_focus(self):
        """Keep the focused window's buffer as it is, then render it as it
        shows under the menu: the wash stamps it into each buffer."""
        ww = self._wash_win
        win = self._wins.get(self._focus) if self._focus is not None else None
        if (win is None or win.minimized or win.kind == "desktop"
                or win._marked == self._marks_for(win)):
            ww[0] = None                  # it shows under the menu as it is
            return
        buf = win.buf
        try:
            lay = self._grown(ww, 1, buf.w, buf.h)
        except MemoryError:
            ww[0] = None                  # no room: it shows as it is
            return
        lay.blit_strip(buf, 0, 0)
        ww[0] = win
        ww[2] = win._buf_stale
        ww[3][:] = win._lag
        ww[4] = win._marked
        self._prewarm(win)

    def _wash_focus_back(self):
        """The close: the focused window's buffer as the menu found it."""
        ww = self._wash_win
        win = ww[0]
        if win is None:
            return
        ww[0] = None
        if win.buf is None or win not in self._wins.values():
            return
        win.buf.blit_strip_rect(ww[1], 0, 0, 0, 0, win.buf.w, win.buf.h)
        win._buf_stale = ww[2]
        win._lag[:] = ww[3]
        win._marked = ww[4]

    def _wash_focus_drop(self):
        """A close that repaints whole: the window renders again as it is."""
        win = self._wash_win[0]
        self._wash_win[0] = None
        if win is not None:
            win._buf_stale = True
            win._lag[4] = 0

    def prealloc(self):
        """The riding scrim's snapshot layer at the layout's content rect,
        minted on an idle frame (the device new_layer pre-collects), so a
        menu's first open over the desk copies into it instead. Its size
        allocates nothing once minted; release_cache gives it back. No
        room is asked about once: a refused mint waits for the WM to give a
        layer back (`_release_layer`), because each ask that the glass
        cannot serve collects the whole heap first."""
        if self._prealloc_refused or not self._pop_rides() or self._pop_live():
            return
        try:
            self._grown(self._pop_wash, 0, self.ws.layout.cw, self.ws.layout.ch)
        except Exception:  # noqa: BLE001 -- no room: the open mints it, or repaints
            self._prealloc_refused = True
