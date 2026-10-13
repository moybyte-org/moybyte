"""The windowed WM's CHROME: the per-window title strip (title + min/max/X),
border, drop shadow and resize grip, the fat-finger resolution of the strip
buttons and the grip, the rubber-band resize preview, and the taskbar chips in
the desktop bar. A mixin over `WindowedWM` -- it draws on the WM's root canvas
and reads its window table, focus and freeze streaks, and owns no state of its
own.
"""

try:
    from chrome import NAMES          # not palette: chrome is the device-safe home
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.chrome import NAMES

try:
    import ui as _ui
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime import ui as _ui

# The frame itself (ring, strip, title, buttons, shadow) is the theme's frame
# style, drawn in C (ui.frame); the letterbox bezel keeps the shadow's black.
_SHADOW = NAMES["black"]

# Shell-process title strips (registered apps contribute TITLE through the app
# registry instead; the player shows the live cart title).
_TITLES = {"menu": "EDITOR", "picker": "PROJECTS",
           "settings": "SETTINGS", "update": "UPDATE"}


class WindowChrome:
    """The window chrome + taskbar chips of `WindowedWM` (its first base)."""

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
               self._win_title(win), self.ws.look.theme_name,
               self.ws.look.theme_variant,
               self._fs())
        if sig != getattr(win, "_chrome_sig", None):
            self._win_touched = True      # the chrome's pixels differ from last frame's
        if not quiet or sig != getattr(win, "_chrome_sig", None):
            win._chrome_sig = sig
            win._chrome_streak = 0
        elif win._chrome_streak >= self._retained_n():
            self._win_grip(win, focused)
            return
        win._chrome_streak += 1
        _ui.frame(self._root_canvas, self.ws.theme_colors, (win.x, win.y, win.w, win.h),
                  win.title_h, self._win_title(win), focused, win.kind != "desktop",
                  self._fs())
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

    def _frame_buttons(self, win):
        """The title-strip buttons as the frame style lays them, left to right:
        [(name, rect, right)]. Close, maximize, and -- app windows only --
        minimize (a running game can't minimize: hiding it would mean silently
        pausing it)."""
        return _ui.frame_buttons((win.x, win.y, win.w, win.h), win.title_h,
                                 self._win_title(win), win.kind != "desktop", self._fs())

    def _strip_buttons(self, win):
        """The title-strip buttons as (name, rect), RIGHT to LEFT."""
        return [(n, r) for n, r, _side in reversed(self._frame_buttons(win))]

    def _strip_button_hit(self, win, px, py):
        """Fat-finger resolution for the strip buttons (owner report 2026-07-27:
        'when I exit a window it stays on the desktop'). At font scale 1 the
        visual buttons are 16px (~1.7mm on the 7\" glass) at 18px pitch, so a
        finger tap missed the exact rect and fell through to the drag-arm --
        the window moved a little and never closed. A tap anywhere in a button
        GROUP's block (the group's span out to the window's edge on its side,
        plus a small overhang below the strip) resolves to the group's NEAREST
        button center. Per-button padding can't work at this pitch; same fix
        class as _grip_hit_rect (2026-07-10)."""
        btns = self._frame_buttons(win)
        if not btns:
            return None
        fs = self._fs()
        pad = 6 * fs
        if not (win.y <= py < win.y + 1 + win.title_h + pad):
            return None
        best = None
        bd = None
        for right in (False, True):
            grp = [(n, r) for n, r, side in btns if side == right]
            if not grp:
                continue
            if right:
                left, end = min(r[0] for _, r in grp) - pad, win.x + win.w
            else:
                left, end = win.x, max(r[0] + r[2] for _, r in grp) + pad
            if not (left <= px < end):
                continue
            for name, (bx, by, bw, bh) in grp:
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
            widths.append(_ui.text_width(label, fs, "ui") + 2 * 4 * fs)
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
        if sig != self._chip_sig:
            # New chip pixels on the OS bar: the compositor's strip carries them.
            self.ws.bar_layer._strip_moved(sc, 0, 0, sc.w, self._root_ctx.layout.status_h)
        if not quiet or sig != self._chip_sig:
            self._chip_sig = sig
            self._chip_streak = 0
        elif self._chip_streak >= self._retained_n():
            return
        self._chip_streak += 1
        line_h = self._root_ctx.layout.line_h
        for key, (x, y, w, h), label in self._chip_rects():
            win = self._wins[key]
            focused = (key == self._focus and not win.minimized)
            bg = th["accent"] if focused else th["panel"]
            if focused:
                fg = _ui.state_colors(th, "chip", _ui.ON)[1]
            else:
                fg = th["edge"] if win.minimized else th["chrome_ink"]
            state = _ui.ON if focused else (_ui.DISABLED if win.minimized else _ui.REST)
            if not _ui.paint(sc, th, (x, y, w, h), "bar.item", state):
                sc.rect(x, y, w, h, bg)
                sc.rectb(x, y, w, h, th["dim"] if win.minimized else th["chrome_ink"])
            sc.print(label, x + 4 * fs, y + (h - line_h) // 2, fg, 1)

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
