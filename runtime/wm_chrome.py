# Map (grep -n a name to jump there):
#   WindowChrome                       the mixin WindowedWM draws its chrome through
#   WindowChrome._win_chrome           a window's frame: strip, buttons, border, shadow
#   WindowChrome._icon_slot            a window's line icon (an app cart's own)
#   WindowChrome._win_title            a window's title ("Notes · Story ideas")
#   WindowChrome._full_chrome          a full-screen window's row on the glass
#   WindowChrome._paint_row            the row's head and its corner's controls
#   WindowChrome._draw_unders          the shadows the desk takes under the windows
#   WindowChrome._cast_onto            the shadows a window behind takes
#   WindowChrome._strip_button_hit     the strip's and the corner's fat-finger hit
#   WindowChrome._grip_hit_rect        the resize grip's touch target
#   WindowChrome._chip_rects           the taskbar chips in the bar
#   WindowChrome._draw_taskbar_chips   the desk bar's window chips
"""The windowed WM's CHROME: the per-window title strip (title + min/max/X),
border, drop shadow and resize grip, the fat-finger resolution of the strip
buttons and the grip, the rubber-band resize preview, and the taskbar chips in
the desktop bar. A mixin over `WindowedWM` -- it draws on the WM's root canvas
and reads its window table, focus and freeze streaks, and owns no state of its
own.
"""

try:
    from chrome import NAMES, MIN_TAP_MM   # not palette: chrome is the device-safe home
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.chrome import NAMES, MIN_TAP_MM

try:
    import layout_base as _layout_base
    import ui_draw as _ui_draw
    import iconsets as _iconsets
    from app_decls import APPS as _APP_DECLS
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime import layout_base as _layout_base
    from runtime import ui_draw as _ui_draw
    from runtime import iconsets as _iconsets
    from runtime.app_decls import APPS as _APP_DECLS

# The frame itself (ring, strip, title, buttons, shadow) is the theme's frame
# style, drawn in C (ui.frame); the letterbox bezel keeps the shadow's black.
_SHADOW = NAMES["black"]

# The edges a resize moves, as the bits of a zone's mask.
RZ_L, RZ_R, RZ_T, RZ_B = 1, 2, 4, 8

# The pointer's shape over a resize zone, by its mask.
RZ_CURSOR = {RZ_L: "ew", RZ_R: "ew", RZ_T: "ns", RZ_B: "ns",
             RZ_L | RZ_T: "nwse", RZ_R | RZ_B: "nwse",
             RZ_R | RZ_T: "nesw", RZ_L | RZ_B: "nesw"}

# Shell-process title strips (registered apps contribute TITLE through the app
# registry instead; the player shows the live cart title). A theme without a
# depth (the older seeds) draws an app's and these in capitals, the Editor's
# as written.
_TITLES = {"menu": "Editor", "projects": "Projects",
           "settings": "Settings", "update": "Update"}
# The shell's own apps (runtime/app_decls.py), whose titles those seeds
# capitalise; an installed app's title is drawn as its app names it.
_SHELL_APPS = tuple(d["id"] for d in _APP_DECLS)
# The title of an Editor or a playtest, "Editor · Star Catcher": the app, the
# separator and the cart; the separator is a hyphen in a face without the dot
# (petme128). A chip under the 2026-10 look shows the app alone.
_SEP_DOT, _SEP_ASCII = " \u00b7 ", " - "
_PLAY = "Play"

# The title icon of a window no app cart draws (`_win_icon`).
_ICON_GLYPHS = {"menu": "code", "projects": "projects", "settings": "gear",
                "update": "get", "desktop": "run"}
# ...and the slot of an icon set (runtime/iconsets.py) each draws.
_ICON_SLOTS = {"menu": "code", "projects": "projects", "settings": "settings",
               "update": "getcarts", "desktop": "run"}
# `win.header`'s `fill` (style.json's theme.look enum: plain, fill, bar, grad),
# whose focused header is the `value` colour and its title and icon `value_ink`.
_HEAD_FILL = 1
# A look chip's room beside its label: the 12-pixel margin, the 18-pixel icon
# and 8 to the label, 24 after it (the 2026-10 look's desk bar).
_CHIP_PAD = 18 + 8 + 24


def _title_sep():
    """The title separator the `title` face draws: the middle dot, else a
    hyphen (petme128 draws ASCII alone)."""
    info = _ui_draw.font_info(_ui_draw.font("title")[0]) if _ui_draw.font("title")[0] else None
    return _SEP_DOT if info is not None and info[4] == 0 else _SEP_ASCII


class WindowChrome:
    """The window chrome + taskbar chips of `WindowedWM` (its first base)."""

    def _win_grip(self, win, focused):
        """The resize grip: three diagonal steps in the bottom-right corner.
        Drawn SEPARATELY from the rest of the chrome because it sits INSIDE the
        content rect -- the window's content stamp overwrites it every frame, so
        it is the one piece the chrome freeze can never skip. A depth's
        rounded frame draws none (its corner still resizes,
        `_grip_hit_rect`)."""
        if not focused or _ui_draw.depth()[13] or win.saved is not None:
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
        resumes. A full-screen window's is its row (`_full_chrome`)."""
        if win.saved is not None:
            self._full_chrome(win, focused, quiet)
            return
        btns = self._win_btns(win)
        sig = (win.x, win.y, win.w, win.h, win.title_h, focused,
               self._win_title(win), btns, self.ws.look.look_gen, self._fs())
        if sig != getattr(win, "_chrome_sig", None):
            self._win_touched = True      # the chrome's pixels differ from last frame's
        if not quiet or sig != getattr(win, "_chrome_sig", None):
            win._chrome_sig = sig
            win._chrome_streak = 0
        elif win._chrome_streak >= self._retained_n():
            self._win_grip(win, focused)
            return
        win._chrome_streak += 1
        self._chrome_drew = True
        self.ws.icon_spec()               # the look's icon set, for the title buttons
        # The game window's content stands in further than the frame takes
        # (_win_edge): the frame's body paints that matte, as it does the foot.
        matte = _ui_draw.FRAME_MATTE if win.edge > self._frame_edge() else 0
        _ui_draw.frame(self._root_canvas, self.ws.theme_colors, (win.x, win.y, win.w, win.h),
                  win.title_h, self._win_title(win), focused, int(btns) | matte, self._fs())
        slot = _ui_draw.frame_icon((win.x, win.y, win.w, win.h), win.title_h, btns)
        if slot is not None:
            self._win_icon(win, slot, focused)
        self._win_grip(win, focused)

    def _full_chrome(self, win, focused, quiet):
        """A full-screen window's row on the root canvas: the band's ground
        and the row (`_paint_row`) where the row lies above the content, the
        corner's controls alone over a game's black; nothing where the app's
        own band is the row, which its content draw carries (`_paint_row`
        into the window's buffer). Frozen on quiet frames as a strip is."""
        if win.kind != "desktop" and not win.row:
            return
        sig = (win.w, win.h, win.row, self._full_title(win), self._win_btns(win),
               self.ws.look.look_gen, self._fs())
        if sig != getattr(win, "_chrome_sig", None):
            self._win_touched = True
        if not quiet or sig != getattr(win, "_chrome_sig", None):
            win._chrome_sig = sig
            win._chrome_streak = 0
        elif win._chrome_streak >= self._retained_n():
            return
        win._chrome_streak += 1
        self._chrome_drew = True
        if win.row:
            self.ws.bar_layer.paint_band(self._root_canvas, (win.x, win.y, win.w, win.row))
        self._paint_row(win, self._root_canvas)

    def _paint_row(self, win, cv):
        """A full-screen row's head and controls on `cv` (the glass's
        coordinates): the back chevron where its app has a page open, the
        window's icon and title, then minimise, restore and close at the
        right, drawn by the frame as on every strip (`FRAME_FULL`). A game's
        row is its controls alone."""
        self.ws.icon_spec()               # the look's icon set, for the buttons
        btns = self._win_btns(win) | _ui_draw.FRAME_FULL
        rect = self._row_rect(win)
        game = win.kind == "desktop"
        _ui_draw.frame(cv, self.ws.theme_colors, rect, win.title_h,
                       "" if game else self._full_title(win), True, btns, self._fs())
        if not game:
            slot = _ui_draw.frame_icon(rect, win.title_h, btns)
            if slot is not None:
                self._win_icon(win, slot, True, cv, row=True)

    def _full_title(self, win):
        """A full-screen row's title: the Editor's names its cart alone
        (EDITOR_LAYOUT.md's frame), cut to the title slot where the app's band
        follows it."""
        t = self._win_title(win)
        if win.kind == "menu":
            cart = self.ws.cart.get("title") if self.ws.cart else None
            if cart:
                t = str(cart)
        if win.kind == "desktop" or win.row:
            return t
        rect = self._row_rect(win)
        btns = self._win_btns(win) | _ui_draw.FRAME_FULL
        x = _ui_draw.frame_icon(rect, win.title_h, btns)
        n = _layout_base.chrome_size(self._fs()) * 8 // _layout_base.render_scale()
        x = (x[0] + x[2] + n + 2) if x is not None else win.x + 2 * n
        room = self._full_slot(win) - n - x
        fid = _ui_draw.font("title")[0]
        return _ui_draw._C.text_cut(t, max(0, room), self._fs(), fid)

    def _win_icon(self, win, slot, focused, cv=None, row=False):
        """The window's icon in the slot its frame leaves left of the title:
        its kind's icon of the theme's icon set where the set draws one (a
        shell app's by its id), else its app cart's icon at the largest whole
        scale that fits, else its glyph, in the title's ink (on a full-screen
        `row`, the band's)."""
        ws = self.ws
        sc = self._root_canvas if cv is None else cv
        x, y, w, h = slot
        role = "chrome_ink" if focused else "chrome_ink_dim"
        if focused and not row and _ui_draw.depth()[13] \
                and ws.look.live.look[0].get("win.header") == _HEAD_FILL:
            role = "value_ink"            # on the `value` header (moy_ui_depth_title_ink)
        ink = ws.theme_colors[role]
        if ws.draw_icon(self._icon_slot(win), slot, ink, sc):
            return
        img = self._win_icon_img(win)
        if img is not None:
            iw = getattr(img, "w", 16) or 16
            s = max(1, w // iw)
            sc.spr(img, x + (w - iw * s) // 2, y + (h - iw * s) // 2, s)
            return
        if not ws.draw_icon("app", slot, ink, sc):
            ws._glyph(_ICON_GLYPHS.get(win.kind, "app"), slot, ink, sc)

    def _icon_slot(self, win):
        """The icon set's slot a window's line icon and its chip draw: its
        kind's, or a window-bound app cart's own (Notes' `notes`)."""
        ws = self.ws
        if win.kind == "desktop" and ws.app_window_canvas and ws.cart:
            name = str(ws.cart.get("id") or "")
            name = name[name.rfind(".") + 1:]
            if name in _iconsets.SLOT:
                return name
        return _ICON_SLOTS.get(win.kind, win.kind)

    def _win_icon_img(self, win):
        """The icon sheet of the app cart `win` runs, found once per window."""
        got = getattr(win, "_icon_img", False)
        if got is not False:
            return got
        ws = self.ws
        img = None
        for app, _text in getattr(ws, "_apps", ()):
            if app.id != win.kind:
                continue
            for c in ws.carts.all:
                if app.is_app(c):
                    img = ws.covers.icon_sheet_for(c)
                    break
            break
        win._icon_img = img
        return img

    # -- depth: rounded frames and shadows (ui.use_depth) ---------------------

    def _win_radius(self, win):
        """The depth's corner radius for `win` (0 with depth off)."""
        if not _ui_draw.depth()[13]:
            return 0
        return _ui_draw.frame_geom(win.w, win.h, win.title_h)[0]

    def _win_foot(self, win):
        """The rows under `win`'s content its rounded bottom corners take
        (none full screen)."""
        if not _ui_draw.depth()[13] or win.saved is not None:
            return 0
        return _ui_draw.frame_geom(win.w, win.h, win.title_h)[1]

    def _frame_edge(self):
        """The columns the depth's window frame takes past its first on each
        side (a 2 px frame: 1), focused or not: the content rect leaves them
        out (and the bottom line's, where no foot holds it), so no content
        pixel lies under the frame and a row's mark stands off it (design
        review R3-25)."""
        if not _ui_draw.depth()[13]:
            return 0
        lk = self.ws.look.live.look[0]
        w = max(lk.get("win.frame.w", 0), lk.get("win.frame_back.w", 0))
        return max(0, (w + 50) // 100 - 1)

    def _win_edge(self, win):
        """The columns `win`'s content stands in from its frame each side:
        the frame's edge, and in the player's window its foot too, so the
        game sits as far from the frame at its sides as above the foot."""
        if win.saved is not None:
            return 0                      # full screen: no frame
        e = self._frame_edge()
        if win.kind == "desktop":
            return max(e, self._win_foot(win))
        return e

    def _pad_extent(self, win, x, y, w, h):
        """(x, y, w, h) grown by what its draw touches beyond it: the border,
        the classic 3px shadow, or the depth's shadow at its full size; a
        full-screen window draws nothing past the glass it covers."""
        if win.saved is not None:
            return (x, y, w, h)
        if _ui_draw.depth()[13]:
            g = _ui_draw.frame_geom(w, h, win.title_h)
            return (x - g[2] - 2, y - g[3] - 2, w + g[2] + g[4] + 4, h + g[3] + g[5] + 4)
        return (x - 2, y - 2, w + 7, h + 7)

    def _draw_unders(self, clip=None):
        """Every visible window's shadow and outline fringe, bottom to top,
        over the desk the backdrop layer just painted: the whole desk, or only
        `clip`, the rect a cache restore covered -- and never over the bar,
        which the windows stay off too. Over a window, the casts of the windows
        above it are its own repaint's (`_cast_onto`), so each lands once on
        freshly painted pixels. A window being dragged or resized casts the
        same shadow as a still one: a moving thing keeps its shadow."""
        if not _ui_draw.depth()[13]:
            return
        rc = self._root_canvas
        th = self.ws.theme_colors
        x0, y0, w, h = self._desk_rect()
        x1, y1 = x0 + w, y0 + h
        if clip is not None:
            x0, y0 = max(x0, clip[0]), max(y0, clip[1])
            x1, y1 = min(x1, clip[0] + clip[2]), min(y1, clip[1] + clip[3])
        if x1 <= x0 or y1 <= y0:
            return
        rc.clip(x0, y0, x1 - x0, y1 - y0)
        try:
            for key in self._order:
                win = self._wins.get(key)
                if win is None or win.minimized or win.saved is not None:
                    continue
                _ui_draw.frame_under(rc, th, (win.x, win.y, win.w, win.h), win.title_h,
                                key == self._focus)
        finally:
            rc.clip()

    def _cast_onto(self, i):
        """The windows above z-slot `i` cast their shadows and outline
        fringes over what window `i` has just painted -- its whole frame when
        its chrome drew this frame, else its content rect -- bottom to top.
        Window `i` painted those pixels afresh, so each blend lands once; what
        it left alone keeps the casts it took when it was painted."""
        order = self._order
        n = len(order)
        if i >= n - 1 or not _ui_draw.depth()[13]:
            return
        key = order[i]
        win = self._wins[key]
        chrome = self._chrome_drew
        if chrome:
            rx, ry, rw, rh = win.x, win.y, win.w, win.h
        else:
            rx, ry, rw, rh = win.content_rect()
        dx, dy, dw, dh = (win.x, win.y, win.w, win.h) if win.saved is not None \
            else self._desk_rect()
        x0, y0 = max(dx, rx), max(dy, ry)
        x1, y1 = min(dx + dw, rx + rw), min(dy + dh, ry + rh)
        if x1 <= x0 or y1 <= y0:
            return
        rc = self._root_canvas
        th = self.ws.theme_colors
        focus = self._focus
        onto = (win.x, win.y, win.w, win.h)
        clipped = False
        try:
            for j in range(i + 1, n):
                k = order[j]
                up = self._wins.get(k)
                if up is None or up.minimized or up.saved is not None:
                    continue
                ex = self._pad_extent(up, up.x, up.y, up.w, up.h)
                if (ex[0] >= x1 or ex[1] >= y1 or ex[0] + ex[2] <= x0
                        or ex[1] + ex[3] <= y0):
                    continue
                if not clipped:
                    rc.clip(x0, y0, x1 - x0, y1 - y0)
                    clipped = True
                rect = (up.x, up.y, up.w, up.h)
                if chrome:
                    _ui_draw.frame_under_onto(rc, th, rect, up.title_h, k == focus,
                                              onto, win.title_h, key == focus)
                else:
                    _ui_draw.frame_under(rc, th, rect, up.title_h, k == focus)
        finally:
            if clipped:
                rc.clip()

    def _resize_feet(self):
        """A look that moves the depth re-sizes every window's foot and
        frame edge (its content rect, and so its buffer)."""
        gen = self.ws.look.look_gen
        if gen == self._foot_gen:
            return
        self._foot_gen = gen
        for key in self._order:
            win = self._wins.get(key)
            if win is not None and (win.foot != self._win_foot(win)
                                    or win.edge != self._win_edge(win)):
                self._resize_window(win, win.w, win.h)

    def _page_named(self, win):
        """The page a shell app names in its window's title (a story's), or
        None."""
        pt = getattr(self.ws._apps_by_id.get(win.kind), "page_title", None)
        return pt() if pt is not None else None

    def _win_title(self, win):
        ws = self.ws
        if win.kind == "desktop":
            t = str((ws.cart.get("title") if ws.cart else "") or "GAME")
            if getattr(self, "_play_intent", None) == "dev":
                return _PLAY + _title_sep() + t       # a playtest from the Editor
            lent = ws.bar_layer._lent("tool") if ws.app_window_canvas else None
            if lent is not None and lent[0]:
                return t + _title_sep() + lent[0]     # the page an app lent ("Notes · Story ideas")
            return t
        shell = win.kind in _TITLES or win.kind in _SHELL_APPS
        named = self._page_named(win)
        if named:
            return named                      # the page a shell app names (a story's)
        base = ws.app_title(win.kind) or _TITLES.get(win.kind, win.kind.upper())
        if shell and win.kind != "menu" and not _ui_draw.depth()[0]:
            base = base.upper()
        app = ws._apps_by_id.get(win.kind)
        host = getattr(app, "_ui", None) if getattr(app, "UI", False) else None
        if host is not None:
            lent = _ui_draw.bar_lent(host.ctx)
            if lent is not None and lent[0] and lent[0] != base:
                return base + _title_sep() + lent[0]   # the document it lent ("Paint · Garden")
        if win.kind == "menu" and ws.cart:
            t = ws.cart.get("title")
            if t:
                return base + _title_sep() + str(t)
        return base

    def _chip_title(self, win, looked):
        """A window's chip label: its title, or under the 2026-10 look the app
        alone where the title names the app and a cart ("Editor"), and a
        playtest's the cart alone ("Star Catcher", D-23), never a second
        "Play" beside the Editor's."""
        t = self._win_title(win)
        named = self._page_named(win)
        if named and t == named:
            t = self.ws.app_title(win.kind) or t  # the chip names the app
        if looked:
            for sep in (_SEP_DOT, _SEP_ASCII):
                at = t.find(sep)
                if at > 0 and win.kind == "desktop" and self.ws.app_window_canvas:
                    return t[:at]                 # an app's chip names the app
                if at > 0 and win.kind == "desktop":
                    return t[at + len(sep):]      # a playtest's chip names the cart
                if at > 0 and (win.kind == "menu" or win.kind in self.ws._apps_by_id):
                    return t[:at]                 # the Editor's, an app's: the app
        return t

    def _win_btns(self, win):
        """The frame's optional strip buttons for `win` (ui_draw.FRAME_*):
        minimize on every window, a running game's too (`_minimize` holds
        its run while it is hidden), so every frame has one control set; and
        the back chevron while its app has a page of it open
        (docs/look_2026-10.md step 27a), which the chrome's freeze signature
        carries."""
        if win.kind == "desktop":
            return _ui_draw.FRAME_MIN
        if self.ws.app_has_page(win.kind):
            return _ui_draw.FRAME_MIN | _ui_draw.FRAME_BACK
        return _ui_draw.FRAME_MIN

    def _frame_buttons(self, win):
        """The title-strip buttons as the frame style lays them, left to right:
        [(name, rect, right)]. Close, maximize, and `_win_btns`' minimize and
        back; a full-screen window's on its row (`_row_rect`)."""
        if win.saved is not None:
            return _ui_draw.frame_buttons(self._row_rect(win), win.title_h, "",
                                          self._win_btns(win) | _ui_draw.FRAME_FULL,
                                          self._fs())
        return _ui_draw.frame_buttons((win.x, win.y, win.w, win.h), win.title_h,
                                      self._win_title(win), self._win_btns(win), self._fs())

    def _win_back(self, win):
        """The window's back -- its chevron, and Esc and B while it holds the
        focus: its app steps out of the page it has open, under the window's
        layout context. False where the app has none open (no chevron shows;
        the keys stay the app's own)."""
        if not self.ws.app_has_page(win.kind):
            return False
        app = self.ws.page_app(win.kind)
        if win.ctx is not None:
            self._install(win.ctx, win)
        try:
            self.ws.app_back(app)
        finally:
            if win.ctx is not None:
                self._install(self._root_ctx)
        self.ws._dirty = True
        return True

    def focus_has_back(self):
        """Whether the focused window shows the back chevron: what Esc does
        on the host's desk (host_api.ConsoleDriver.escape)."""
        win = self._focus_win()
        return win is not None and not win.minimized and self.ws.app_has_page(win.kind)

    def focus_back(self):
        """The focused window's back (`_win_back`); False where it shows none."""
        win = self._focus_win()
        return win is not None and not win.minimized and self._win_back(win)

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
        strip = self._row_h() if win.saved is not None else 1 + win.title_h
        if not (win.y <= py < win.y + strip + pad):
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

    def _rz_band(self):
        """(outside, inside, corner) of a window's resize zones, in root
        pixels: a few of the chrome's unit inside the frame, outside it what
        takes the zone to MIN_TAP_MM across on this glass, and how far each
        corner's zone reaches along its two edges."""
        n = _layout_base.chrome_size(self._fs()) * _layout_base.unit()
        inside = 2 * n
        total = 5 * n
        diag = getattr(self.ws, "panel_diagonal_in", None)
        if diag:
            full = self._root_canvas
            r = _layout_base.render_scale()
            gw, gh = full.w * r, full.h * r
            per_mm = (gw * gw + gh * gh) ** 0.5 / (float(diag) * 25.4) / r
            tap = int(MIN_TAP_MM * per_mm + 0.999)
            if tap > total:
                total = tap
        return (total - inside, inside, max(total, 12 * n))

    def _resize_edges(self, win, px, py):
        """The edges (a mask of RZ_*) a press at (px, py) resizes `win` by:
        within its zones, which straddle the frame (`_rz_band`) but for the
        top's, which lies above it (the strip is the drag's), or on its
        grip; 0 elsewhere, and for a full-screen, docked or minimised one."""
        if win.saved is not None or win.docked is not None or win.minimized:
            return 0
        o, i, c = self._rz_band()
        x0, y0 = win.x, win.y
        x1, y1 = x0 + win.w, y0 + win.h
        if not (x0 - o <= px < x1 + o and y0 - o <= py < y1 + o):
            return 0
        g = self._grip_hit_rect(win)
        if g[0] <= px < g[0] + g[2] and g[1] <= py < g[1] + g[3]:
            return RZ_R | RZ_B
        m = 0
        if px < x0 + i:
            m = RZ_L
        elif px >= x1 - i:
            m = RZ_R
        if py < y0:                       # the strip itself is the drag's
            m |= RZ_T
        elif py >= y1 - i:
            m |= RZ_B
        if m and not m & (RZ_T | RZ_B):
            if py < y0 + c:
                m |= RZ_T
            elif py >= y1 - c:
                m |= RZ_B
        elif m and not m & (RZ_L | RZ_R):
            if px < x0 + c:
                m |= RZ_L
            elif px >= x1 - c:
                m |= RZ_R
        return m

    def _live_resize_ok(self):
        """Live-body resize needs the rect-clipped stamp (see _blit_backdrop_cache);
        without it (the web RecordingLayer) the rubber-band outline preview stays."""
        return getattr(self._root_canvas, "blit_strip_rect", None) is not None

    def _draw_resizing_window(self, win, focused, x, y, cw, ch):
        """The 'real OS' resize feel (#58): during the gesture the window BODY
        follows the edge -- frame + title strip + grip at the rubber rect, the
        RETAINED content cropped into the new content rect (anchored top-left;
        grow reveals the panel field -- no re-layout mid-gesture, the real reflow
        still lands on release via _resize_window). Draws via a temporary rect
        swap so _win_chrome/content_rect need no size plumbing."""
        sc = self._root_canvas
        ox, oy, ow, oh = win.x, win.y, win.w, win.h
        win.x, win.y, win.w, win.h = x, y, cw, ch
        try:
            cx, cy, cwid, chei = win.content_rect()
            if cwid > 0 and chei > 0:
                sc.rect(cx, cy, cwid, chei, self.ws.theme_colors["panel"])
                sc.blit_strip_rect(win.buf, cx, cy, cx, cy, cwid, chei)
            self._win_chrome(win, focused)
        finally:
            win.x, win.y, win.w, win.h = ox, oy, ow, oh

    # -- the taskbar chips (open windows in the desktop bar) --------------------

    def _chips_cell(self):
        """(bar, cell) of the desk's `chips` widget in the root layout's
        packing (shell_layout.pack), or None where no bar places it."""
        for bar in self._root_ctx.layout.bars:
            for c in bar[3]:
                if c[0] == "chips":
                    return bar, c
        return None

    def _chip_rects(self):
        """One chip per open window, in the `chips` widget's cell: across a
        top or bottom bar a labelled chip each, packed from the cell's start
        in a left slot, its end in a right one, and centred on the bar in the
        centre slot (today's taskbar); down a side bar a square chip each
        with the label's first letter. They stop short of the cell's end.
        Returns [(kind, rect, label)] in the order the windows opened (the
        back-stack's, never the z-order, so a chip never moves when the focus
        does), on the root canvas; deterministic, so draw + hit-test share it
        without stored state."""
        kinds = [k for k in self._kinds if k in self._wins]
        if not kinds:
            return []
        got = self._chips_cell()
        if got is None:
            return []
        (edge, (bx, by, bw, bh), _dy, _cells), (_n, (cx, cy, cw, ch), _t, slot) = got
        lay = self._root_ctx.layout
        fs = lay.fs
        out = []
        if edge in ("left", "right"):
            side = 16 * lay.cs
            x = bx + (bw - side) // 2
            y = by + cy
            end = by + cy + ch - 4 * fs
            for k in kinds:
                if y + side > end:
                    break                              # out of bar space -- stop
                out.append((k, (x, y, side, side), self._win_title(self._wins[k])[:1]))
                y += side + 2 * fs
            return out
        looked = bool(_ui_draw.depth()[0])
        titles = [self._chip_title(self._wins[k], looked) for k in kinds]
        pad = _CHIP_PAD if looked else 2 * 4 * fs   # the look's: icon slot and margins
        gap = 6 if looked else 2 * fs
        room = cw - 4 * fs - (len(titles) - 1) * gap
        cap = max(len(t) for t in titles)
        while True:
            labels = [t[:cap] for t in titles]
            widths = [_ui_draw.text_width(lb, fs, "ui") + pad for lb in labels]
            if sum(widths) <= room or cap <= 4:
                break
            cap -= 1                     # shorten the longest titles first
        total = sum(widths) + (len(widths) - 1) * gap
        if slot == 1:
            x = max(cx, (bw - total) // 2)
        elif slot == 2:
            x = max(cx, cx + cw - total)
        else:
            x = cx
        x += bx
        y = by + 1 * fs
        h = bh - 2 * fs
        if looked:
            h = min(bh * 28 // 40, bh - 8)
            y = by + (bh - h) // 2
        end = bx + cx + cw - 4 * fs
        for i, k in enumerate(kinds):
            if x + widths[i] > end:
                break                                  # out of bar space -- stop
            out.append((k, (x, y, widths[i], h), labels[i]))
            x += widths[i] + gap
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
        sig = tuple((k, r, lb, k == self._focus, self._wins[k].minimized,
                     getattr(self._wins[k], "held", False))
                    for k, r, lb in self._chip_rects())
        if sig != self._chip_sig:
            # New chip pixels on the OS bar: the compositor's strip carries them.
            got = self._chips_cell()
            if got is not None:
                bx, by, bw, bh = got[0][1]
                self.ws.bar_layer._strip_moved(sc, bx, by, bw, bh)
        if not quiet or sig != self._chip_sig:
            self._chip_sig = sig
            self._chip_streak = 0
        elif self._chip_streak >= self._retained_n():
            return
        self._chip_streak += 1
        # The chips blend (their labels' and plates' edges) over the bar's
        # band, which the bar painted once: its ground goes down again first
        # in the root's bar, so a chip repaint is a full repaint's pixels.
        self.ws.bar_layer.repaint_cell("desk", "chips")
        line_h = self._root_ctx.layout.line_h
        if _ui_draw.depth()[0]:
            self._draw_look_chips(sc, th, fs, line_h)
            return
        for key, (x, y, w, h), label in self._chip_rects():
            win = self._wins[key]
            focused = (key == self._focus and not win.minimized)
            bg = th["accent"] if focused else th["panel"]
            if focused:
                fg = _ui_draw.state_colors(th, "chip", _ui_draw.ON)[1]
            else:
                fg = th["chrome_ink"]       # a minimized chip is not dimmed (D-25)
            state = _ui_draw.ON if focused else _ui_draw.REST
            if not _ui_draw.paint(sc, th, (x, y, w, h), "bar.item", state):
                sc.rect(x, y, w, h, bg)
                sc.rectb(x, y, w, h, th["chrome_ink"])
            tx = x + (w - _ui_draw.text_width(label, fs, "ui")) // 2
            sc.print(label, tx, y + (h - line_h) // 2, fg, 1)

    def _draw_look_chips(self, sc, th, fs, line_h):
        """The taskbar chips as the look's tabs (docs/look_2026-10.md step 14):
        the window's icon (in `_win_icon`'s order) then its title, the
        focused window's chip raised under the current mark, another open
        window's in the dim ink. The focused chip's content takes the ink the
        look gives current content (Moy's accent), the plate's second cue. A
        minimized window's chip keeps the chrome's ink, and a held game's
        icon is the pause glyph: the state is the icon (D-25)."""
        current = _ui_draw.current_ink(sc, th) or th["chrome_ink"]
        for key, (x, y, w, h), label in self._chip_rects():
            win = self._wins[key]
            focused = key == self._focus and not win.minimized
            _ui_draw.bar_part(sc, th, _ui_draw.BAR_CHIP, (x, y, w, h), focused)
            ink = (current if focused else
                   th["chrome_ink"] if win.minimized else th["chrome_ink_dim"])
            slot = (x + 12, y + (h - 18) // 2, 18, 18)
            if win.minimized and getattr(win, "held", False):
                self._pause_glyph(sc, slot, ink)
            elif not self.ws.draw_icon(self._icon_slot(win), slot, ink, sc):
                img = self._win_icon_img(win)
                if img is not None:
                    iw = getattr(img, "w", 16) or 16
                    s = max(1, 18 // iw)
                    sc.spr(img, slot[0] + (18 - iw * s) // 2, slot[1] + (18 - iw * s) // 2, s)
                else:
                    self.ws._glyph(_ICON_GLYPHS.get(win.kind, "app"), slot, ink, sc)
            sc.print(label, x + 38, y + (h - line_h) // 2, ink, 1)

    @staticmethod
    def _pause_glyph(sc, slot, ink):
        """A held game's icon: the pause glyph, two bars in `ink` centred
        in the icon's slot, as a media player shows a held track."""
        x, y, w, h = slot
        bw, bh, gap = w * 2 // 9, h * 2 // 3, w // 6
        bx = x + (w - 2 * bw - gap) // 2
        by = y + (h - bh) // 2
        sc.rect(bx, by, bw, bh, ink)
        sc.rect(bx + bw + gap, by, bw, bh, ink)

    def _chip_tap(self, key):
        """Taskbar chip click -- pure FOCUS verbs, never a pop: restore a
        minimized window (and focus it), minimize the focused one (apps only;
        the focus leaves it, as the title strip's minimize leaves it), or just
        move focus to it. Nothing closes from the taskbar."""
        ws = self.ws
        win = self._wins.get(key)
        if win is None:
            return
        ws._dirty = True
        if win.minimized:
            self._minimize(win, False)
            self._move_focus(key)
        elif key == self._focus:
            self._minimize(win, True)
            self._move_focus(None)
        else:
            self._move_focus(key)
