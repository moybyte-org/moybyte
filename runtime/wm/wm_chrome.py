# Map (grep -n a name to jump there):
#   WindowChrome                       the mixin WindowedWM draws its chrome through
#   WindowChrome._win_chrome           a window's frame: strip, buttons, border, shadow
#   WindowChrome._icon_slot            a window's line icon (an app cart's own)
#   WindowChrome._win_title            a window's title ("Notes · Story ideas")
#   WindowChrome._full_chrome          a full-screen window's row on the glass
#   WindowChrome._paint_row            the row's head and its corner's controls
#   WindowChrome.draw_unders           the shadows the desk takes under the windows
#   WindowChrome._cast_onto            the shadows a window behind takes
#   WindowChrome._strip_button_hit     the strip's and the corner's fat-finger hit
#   WindowChrome._grip_hit_rect        the resize grip's touch target
#   WindowChrome.chip_rects           the taskbar chips in the bar
#   WindowChrome._draw_taskbar_chips   the desk bar's window chips
"""The windowed WM's CHROME: the per-window title strip (title + min/max/X),
border, drop shadow and resize grip, the fat-finger resolution of the strip
buttons and the grip, the rubber-band resize preview, and the taskbar chips in
the desktop bar. A mixin over `WindowedWM` -- it draws on the WM's root canvas
and reads its window table, focus and freeze streaks, and owns no state of its
own.
"""

from chrome import NAMES, MIN_TAP_MM   # not palette: chrome is the device-safe home
import layout_base as _layout_base
import ui_draw as _ui_draw
from bar_layer import glyph as _bar_glyph
import iconsets as _iconsets
from wm_parts import _set_rect
import wm_strip as _strip
import wm_strip_ui as _strip_ui

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
# registry instead; the player shows the live cart title).
_TITLES = {"menu": "Editor", "projects": "Projects",
           "settings": "Settings", "update": "Update", "tasks": "Tasks"}
# The Editor's title in its theme mode (runtime/editors/theme_editor.py).
THEME_TITLE = "Theme"
# The title of an Editor or a playtest, "Editor · Star Catcher": the app, the
# separator and the cart; the separator is a hyphen in a face without the dot
# (petme128). A chip under the 2026-10 look shows the app alone.
_SEP_DOT, _SEP_ASCII = " \u00b7 ", " - "
_SEPS = (_SEP_DOT, _SEP_ASCII)
_PLAY = "Play"

# The title icon of a window no app cart draws (`_win_icon`).
_ICON_GLYPHS = {"menu": "code", "projects": "projects", "settings": "gear",
                "update": "get", "desktop": "run", "tasks": "run"}
# ...and the slot of an icon set (runtime/toolkit/iconsets.py) each draws.
_ICON_SLOTS = {"menu": "code", "projects": "projects", "settings": "settings",
               "update": "getcarts", "desktop": "run", "tasks": "layout"}
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
    """The window chrome + taskbar chips of `WindowedWM` (its second base,
    after `WindowPaint`)."""

    def _win_grip(self, win, focused):
        """The resize grip: three diagonal steps in the bottom-right corner.
        Drawn SEPARATELY from the rest of the chrome because it sits INSIDE the
        content rect -- the window's content stamp overwrites it every frame, so
        it is the one piece the chrome freeze can never skip. A depth's
        rounded frame draws none (its corner still resizes,
        `_grip_hit_rect`)."""
        if not focused or _ui_draw.depth()[12] or win.saved is not None:
            return
        sc = self.root_canvas
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
        title = self._win_title(win)
        look = self.ws.look.look_gen
        fs = self._fs()
        k = win._chrome_keys
        if (k[0] != win.x or k[1] != win.y or k[2] != win.w or k[3] != win.h
                or k[4] != win.title_h or k[5] != focused or k[6] != title
                or k[7] != btns or k[8] != look or k[9] != fs):
            k[0] = win.x
            k[1] = win.y
            k[2] = win.w
            k[3] = win.h
            k[4] = win.title_h
            k[5] = focused
            k[6] = title
            k[7] = btns
            k[8] = look
            k[9] = fs
            self._chrome_count += 1       # one count for every window's, so a
            win._chrome_gen = self._chrome_count  # new window never repeats one
        sig = win._chrome_gen
        if sig != getattr(win, "_chrome_sig", None):
            self._win_touched = True      # the chrome's pixels differ from last frame's
            self._outside_f = self.ws.frame_state.frames_drawn
        if not quiet or sig != getattr(win, "_chrome_sig", None):
            win._chrome_sig = sig
            win._chrome_streak = 0
        elif win._chrome_streak >= self.retained_n():
            self._win_grip(win, focused)
            return
        win._chrome_streak += 1
        self._chrome_drew = True
        self.ws.icon_spec()               # the look's icon set, for the title buttons
        # The game window's content stands in further than the frame takes
        # (_win_edge): the frame's body paints that matte, as it does the foot.
        matte = _ui_draw.FRAME_MATTE if win.edge > self._frame_edge() else 0
        rect = _set_rect(win._frame_rect, win.x, win.y, win.w, win.h)
        th = self.ws.theme_colors
        bare = int(btns) | matte | _ui_draw.FRAME_BARE
        cv = self.root_canvas
        _ui_draw.frame(cv, th, rect, win.title_h, title, focused, bare, fs)
        _strip.stamp(cv, th, win.x, win.y, win.w, win.h, win.title_h, title, focused, bare, fs,
                     self._strip_unit(fs), self._strip_icon(win), self._strip_tone(focused),
                     bool(btns & _ui_draw.FRAME_BACK), look, self._win_radius(win))
        self._win_grip(win, focused)

    def _strip_unit(self, fs):
        """The strip page's unit: the chrome's unit at the window's font
        scale (2 on a desk at chrome size 1, the font scale on a handheld)."""
        return _layout_base.unit() * _layout_base.chrome_size(fs)

    def _strip_icon(self, win):
        """The icon a window's strip and its chip show: its icon set slot,
        else its kind's glyph."""
        icon = self._icon_slot(win)
        return icon if icon in _iconsets.SLOT else _ICON_GLYPHS.get(win.kind, "app")

    def _strip_tone(self, focused):
        """The strip's ink as a role: the title's ink on the focused
        window's strip (the depth's on a filled header, `value_ink`), the
        dimmed chrome ink behind."""
        if not focused:
            return "chrome_ink_dim"
        if _ui_draw.depth()[12]:
            return "value_ink" if self.ws.look.live.look[0].get("win.header") == _HEAD_FILL \
                else "chrome_ink"
        return "title_ink"

    def _strip_args(self, win):
        """The strip's rect and what it shows, for `wm_strip`."""
        btns = self._win_btns(win)
        return (_set_rect(win._strip_rect, win.x + 1, win.y + 1, win.w - 2, win.title_h),
                self._strip_unit(self._fs()), self._win_title(win), self._strip_icon(win),
                self._strip_tone(win.kind == self.focus_key), bool(btns & _ui_draw.FRAME_BACK),
                self._win_radius(win))

    def strip_run(self, win, name, focused):
        """Strip button `name` of `win`, tapped: a framed window's runs its
        strip page's action (`wm_strip_ui`, which calls `_strip_do`); a
        full-screen row's runs at once."""
        if win.saved is not None:
            self._strip_do(win, name, focused)
            return
        self._strip_at = (win, focused)
        _strip_ui.TAP[0] = self._strip_tapped
        try:
            _strip.fire(name)
        finally:
            self._strip_at = None

    def _strip_tapped(self, name):
        at = self._strip_at
        if at is not None:
            self._strip_do(at[0], name, at[1])

    def _strip_do(self, win, name, focused):
        if name == "close":
            self._close_window(win.kind)
        elif name == "back":
            self._win_back(win)
        elif name == "max":
            self.toggle_max(win)
        elif name == "min":
            self.minimize(win, True)
            if focused:
                self.move_focus(None)

    def _is_game(self, win):
        """Whether `win` is a game's window: the player's, running a game.
        The player's window running a window-bound app cart (Notes) is an
        app window, full screen as every app's is: its row, its title."""
        return win.kind == "desktop" and not self.ws.run.app_window_canvas

    def _full_chrome(self, win, focused, quiet):
        """A full-screen window's row on the root canvas: the band's ground
        and the row (`_paint_row`) where the row lies above the content;
        nothing where the app's own band is the row, which its content draw
        carries (`_paint_row` into the window's buffer), and nothing over a
        game: play shows no window buttons (owner, 2026-10-31). Frozen on
        quiet frames as a strip is."""
        if (win.kind != "desktop" and not win.row) or self._is_game(win):
            return
        sig = (win.w, win.h, win.row, self._full_title(win), self._win_btns(win),
               self.ws.look.look_gen, self._fs())
        if sig != getattr(win, "_chrome_sig", None):
            self._win_touched = True
            self._outside_f = self.ws.frame_state.frames_drawn
        if not quiet or sig != getattr(win, "_chrome_sig", None):
            win._chrome_sig = sig
            win._chrome_streak = 0
        elif win._chrome_streak >= self.retained_n():
            return
        win._chrome_streak += 1
        self._chrome_drew = True
        if win.row:
            self.ws.bar_layer.paint_band(self.root_canvas, (win.x, win.y, win.w, win.row))
        self._paint_row(win, self.root_canvas)

    def _paint_row(self, win, cv, title=None):
        """A full-screen row's head and controls on `cv` (the glass's
        coordinates): the back chevron where its app has a page open, the
        window's icon and title, then minimise, restore and close at the
        right, drawn by the frame as on every strip (`FRAME_FULL`)."""
        self.ws.icon_spec()               # the look's icon set, for the buttons
        btns = self._win_btns(win) | _ui_draw.FRAME_FULL
        rect = self._row_rect(win)
        if title is None:
            title = self._full_title(win)
        _ui_draw.frame(cv, self.ws.theme_colors, rect, win.title_h,
                       title, True, btns, self._fs())
        slot = _ui_draw.frame_icon(rect, win.title_h, btns)
        if slot is not None:
            self._win_icon(win, slot, True, cv, row=True)

    def _full_title(self, win):
        """A full-screen row's title: the Editor's names its cart alone
        (EDITOR_LAYOUT.md's frame), cut to the title slot where the app's band
        follows it."""
        t = self._win_title(win)
        doc = self._theme_doc(win)
        if doc is not None:
            t = doc.name                        # the theme mode names its theme alone
        elif win.kind == "menu":
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
        sc = self.root_canvas if cv is None else cv
        x, y, w, h = slot
        role = "chrome_ink" if focused else "chrome_ink_dim"
        if focused and not row and _ui_draw.depth()[12] \
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
            _bar_glyph(_ICON_GLYPHS.get(win.kind, "app"), slot, ink, sc)

    def _theme_doc(self, win):
        """The theme the Editor's window holds in its theme mode, or None."""
        if win.kind != "menu":
            return None
        app = getattr(self.ws, "editor_app", None)
        return getattr(app, "theme", None)

    def _icon_slot(self, win):
        """The icon set's slot a window's line icon and its chip draw: its
        kind's, the Editor's theme mode's palette, or a window-bound app
        cart's own (Notes' `notes`)."""
        ws = self.ws
        if self._theme_doc(win) is not None:
            return "palette"
        if win.kind == "desktop" and ws.run.app_window_canvas and ws.cart:
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
        for app in getattr(ws, "app_layers", ()):
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
        """The depth's corner radius for `win` (0 under a frame style's frame)."""
        if not _ui_draw.depth()[12]:
            return 0
        return _ui_draw.frame_geom(win.w, win.h, win.title_h)[0]

    def _win_foot(self, win):
        """The rows under `win`'s content its rounded bottom corners take
        (none full screen)."""
        if not _ui_draw.depth()[12] or win.saved is not None:
            return 0
        return _ui_draw.frame_geom(win.w, win.h, win.title_h)[1]

    def _frame_edge(self):
        """The columns the depth's window frame takes past its first on each
        side (a 2 px frame: 1), focused or not: the content rect leaves them
        out (and the bottom line's, where no foot holds it), so no content
        pixel lies under the frame and a row's mark stands off it (design
        review R3-25)."""
        if not _ui_draw.depth()[12]:
            return 0
        look = self.ws.look
        if self._edge_gen != look.look_gen:   # the look's, read once a publish
            lk = look.live.look[0]
            w = max(lk.get("win.frame.w", 0), lk.get("win.frame_back.w", 0))
            self._edge = max(0, (w + 50) // 100 - 1)
            self._edge_gen = look.look_gen
        return self._edge

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

    def _pad_extent(self, win, x, y, w, h, out=None):
        """(x, y, w, h) grown by what its draw touches beyond it: the border,
        the classic 3px shadow, or the depth's shadow at its full size; a
        full-screen window draws nothing past the glass it covers. Written
        into the list `out` when one is given."""
        if win.saved is not None:
            if out is not None:
                return _set_rect(out, x, y, w, h)
            return (x, y, w, h)
        if _ui_draw.depth()[12]:
            g = _ui_draw.frame_geom(w, h, win.title_h)
            if out is not None:
                return _set_rect(out, x - g[2] - 2, y - g[3] - 2, w + g[2] + g[4] + 4,
                                 h + g[3] + g[5] + 4)
            return (x - g[2] - 2, y - g[3] - 2, w + g[2] + g[4] + 4, h + g[3] + g[5] + 4)
        if out is not None:
            return _set_rect(out, x - 2, y - 2, w + 7, h + 7)
        return (x - 2, y - 2, w + 7, h + 7)

    def draw_unders(self, clip=None):
        """Every visible window's shadow and outline fringe, bottom to top,
        over the desk the backdrop layer just painted: the whole desk, or only
        `clip`, the rect a cache restore covered -- and never over the bar,
        which the windows stay off too. Over a window, the casts of the windows
        above it are its own repaint's (`_cast_onto`), so each lands once on
        freshly painted pixels. A window being dragged or resized casts the
        same shadow as a still one: a moving thing keeps its shadow. A cache
        restore `_blit_backdrop_cache` left to it (`_fill`) is laid with the
        first shadow, in one pass."""
        fill = self._fill
        self._fill = None
        try:
            if not _ui_draw.depth()[12]:
                return
            rc = self.root_canvas
            th = self.ws.theme_colors
            x0, y0, w, h = self.desk_rect()
            x1, y1 = x0 + w, y0 + h
            if clip is not None:
                x0, y0 = max(x0, clip[0]), max(y0, clip[1])
                x1, y1 = min(x1, clip[0] + clip[2]), min(y1, clip[1] + clip[3])
            if x1 <= x0 or y1 <= y0:
                return
            rc.clip(x0, y0, x1 - x0, y1 - y0)
            try:
                for key in self.z_order:
                    win = self.windows.get(key)
                    if win is None or win.minimized or win.saved is not None:
                        continue
                    if fill is None:
                        _ui_draw.frame_under(rc, th, self._shown_rect(win), win.title_h,
                                             key == self.focus_key)
                    else:
                        cache = fill[0]
                        ba = getattr(cache, "_batch_arr", None)
                        if ba is not None and ba[0] > 4:
                            cache.flush_batch()
                        _ui_draw.frame_under_fill(rc, th, self._shown_rect(win), win.title_h,
                                                  key == self.focus_key, cache._buf, fill[1],
                                                  fill[2])
                        fill = None
            finally:
                rc.clip()
        finally:
            if fill is not None:
                self._lay(self._laid)

    def _shown_rect(self, win):
        """Where `win` stands this frame: a live resize's rubber rect, else
        its own (the window's kept list, written in place: read it, never
        keep it) -- what its shadow and outline fringe follow."""
        g = self._resize
        if (g is not None and self.windows.get(g[0]) is win
                and win.kind != "desktop" and self._live_resize_ok()):
            return (g[10], g[11], g[5], g[6])
        return _set_rect(win._shown, win.x, win.y, win.w, win.h)

    def _cast_onto(self, i, within=None):
        """The windows above z-slot `i` cast their shadows and outline
        fringes over what window `i` has just painted -- its whole frame when
        its chrome drew this frame, else its content rect, and of that only
        rect `within` where given -- bottom to top. Window `i` painted those
        pixels afresh, so each blend lands once; what it left alone keeps the
        casts it took when it was painted."""
        order = self.z_order
        n = len(order)
        if i >= n - 1 or not _ui_draw.depth()[12]:
            return
        key = order[i]
        win = self.windows[key]
        chrome = self._chrome_drew
        if chrome:
            rx, ry, rw, rh = win.x, win.y, win.w, win.h
        else:
            rx, ry, rw, rh = win.content_rect()
        dx, dy, dw, dh = (win.x, win.y, win.w, win.h) if win.saved is not None \
            else self.desk_rect()
        x0, y0 = max(dx, rx), max(dy, ry)
        x1, y1 = min(dx + dw, rx + rw), min(dy + dh, ry + rh)
        if within is not None:
            x0, y0 = max(x0, within[0]), max(y0, within[1])
            x1 = min(x1, within[0] + within[2])
            y1 = min(y1, within[1] + within[3])
        if x1 <= x0 or y1 <= y0:
            return
        rc = self.root_canvas
        th = self.ws.theme_colors
        focus = self.focus_key
        onto = (win.x, win.y, win.w, win.h)
        clipped = False
        try:
            for j in range(i + 1, n):
                k = order[j]
                up = self.windows.get(k)
                if up is None or up.minimized or up.saved is not None:
                    continue
                rect = self._shown_rect(up)
                ex = self._pad_extent(up, rect[0], rect[1], rect[2], rect[3])
                if (ex[0] >= x1 or ex[1] >= y1 or ex[0] + ex[2] <= x0
                        or ex[1] + ex[3] <= y0):
                    continue
                if not clipped:
                    rc.clip(x0, y0, x1 - x0, y1 - y0)
                    clipped = True
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
        for key in self.z_order:
            win = self.windows.get(key)
            if win is not None and (win.foot != self._win_foot(win)
                                    or win.edge != self._win_edge(win)):
                self.resize_window(win, win.w, win.h)

    def _page_named(self, win):
        """The page a shell app names in its window's title (a story's,
        Settings' Achievements), or None."""
        app = self.ws.app(win.kind)
        return app.page_title() if app is not None else None

    def _win_title(self, win):
        ws = self.ws
        p = getattr(win, "proc", None)
        if p is not None:
            return p.title()                  # a running set's game
        if win.kind == "desktop":
            t = str((ws.cart.get("title") if ws.cart else "") or "GAME")
            if getattr(self, "play_intent", None) == "dev":
                return _PLAY + _title_sep() + t       # a playtest from the Editor
            lent = ws.bar_layer.lent("tool") if ws.run.app_window_canvas else None
            if lent is not None and lent[0]:
                return t + _title_sep() + lent[0]     # the page an app lent ("Notes · Story ideas")
            return t
        named = self._page_named(win)
        if named:
            return named                      # the page a shell app names (a story's)
        base = ws.spine.app_title(win.kind)
        if not base:
            base = _TITLES.get(win.kind)
            if base is None:
                base = win.kind.upper()
        app = ws.app(win.kind)
        host = getattr(app, "_ui", None) if getattr(app, "UI", False) else None
        if host is not None:
            lent = _ui_draw.bar_lent(host.ctx)
            if lent is not None and lent[0] and lent[0] != base:
                return base + _title_sep() + lent[0]   # the document it lent ("Paint · Garden")
        doc = self._theme_doc(win)
        if doc is not None:
            return THEME_TITLE + _title_sep() + doc.name     # "Theme · custom"
        if win.kind == "menu" and ws.cart:
            t = ws.cart.get("title")
            if t:
                return base + _title_sep() + str(t)
        return base

    def _chip_title(self, win):
        """A window's chip label: its title, or under the 2026-10 look the app
        alone where the title names the app and a cart ("Editor"), and a
        playtest's the cart alone ("Star Catcher", D-23), never a second
        "Play" beside the Editor's."""
        t = self._win_title(win)
        named = self._page_named(win)
        if named and t == named:
            t = self.ws.spine.app_title(win.kind) or t  # the chip names the app
        for sep in _SEPS:
            at = t.find(sep)
            if at > 0 and win.kind == "desktop" and self.ws.run.app_window_canvas:
                return t[:at]                     # an app's chip names the app
            if at > 0 and win.kind == "desktop":
                return t[at + len(sep):]          # a playtest's chip names the cart
            if at > 0 and (win.kind == "menu" or self.ws.app(win.kind) is not None):
                return t[:at]                     # the Editor's, an app's: the app
        return t

    def _win_btns(self, win):
        """The frame's optional strip buttons for `win` (ui_draw.FRAME_*):
        minimize on every window, a running game's too (`minimize`
        minimises its run while it is hidden), so every frame has one control set; and
        the back chevron while its app has a page of it open
        (docs/history/look_2026-10.md step 27a), which the chrome's freeze signature
        carries."""
        if self.ws.app_has_page(win.kind):
            return _ui_draw.FRAME_MIN | _ui_draw.FRAME_BACK
        return _ui_draw.FRAME_MIN

    def _frame_buttons(self, win):
        """The title-strip buttons as the frame style lays them, left to right:
        [(name, rect, right)]. Close, maximize, and `_win_btns`' minimize and
        back; a full-screen window's on its row (`_row_rect`), and none on a
        full-screen game's."""
        if win.saved is not None:
            if self._is_game(win):
                return []
            return _ui_draw.frame_buttons(self._row_rect(win), win.title_h, "",
                                          self._win_btns(win) | _ui_draw.FRAME_FULL,
                                          self._fs())
        a = self._strip_args(win)
        return _strip.buttons(a[0], a[1], a[2], a[3], a[4], a[5], a[6], [])

    def _win_back(self, win):
        """The window's back -- its chevron, and Esc and B while it holds the
        focus: its surface steps out of the page it has open
        (`InputPolicy.step`), under the window's layout context. False where
        it has none open (no chevron shows; the keys stay its own)."""
        ws = self.ws
        policy = ws.input_policy
        s = ws.surface_of(win.kind)
        if s is None or not policy.has_page(s):
            return False
        if win.ctx is not None:
            self.install_window(win.ctx, win, draws=False)
        try:
            policy.step(s)
        finally:
            if win.ctx is not None:
                self.install_window(self.root_ctx)
        ws.mark_dirty()
        return True

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
        class as _grip_hit_rect."""
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
            full = self.root_canvas
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
        return hasattr(self.root_canvas, "blit_strip_rect")

    def _draw_resizing_window(self, win, focused, x, y, cw, ch):
        """The 'real OS' resize feel (#58): during the gesture the window BODY
        follows the edge -- frame + title strip + grip at the rubber rect, the
        RETAINED content cropped into the new content rect (anchored top-left;
        grow reveals the panel field -- no re-layout mid-gesture, the real reflow
        still lands on release via _resize_window). Draws via a temporary rect
        swap so _win_chrome/content_rect need no size plumbing."""
        sc = self.root_canvas
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
        packing (shell_layout.pack), or None where no bar places it. Kept
        while the packing stands: a new packing is a new list."""
        bars = self.root_ctx.layout.bars
        m = self._cell_memo
        if m is not None and m[0] is bars:
            return m[1]
        got = None
        for bar in bars:
            for c in bar[3]:
                if c[0] == "chips" and got is None:
                    got = (bar, c)
        self._cell_memo = (bars, got)
        return got

    def _chips_same(self, m, side):
        """Whether the open windows and their chip titles are memo `m`'s,
        compared in place (`chip_rects`)."""
        kinds, titles = m[0], m[1]
        j = 0
        for k in self._kinds:
            win = self.windows.get(k)
            if win is None:
                continue
            if j >= len(kinds) or kinds[j] != k:
                return False
            t = self._win_title(win) if side else self._chip_title(win)
            if titles[j] != t:
                return False
            j += 1
        return j == len(kinds)

    def chip_rects(self):
        """One chip per open window, in the `chips` widget's cell: across a
        top or bottom bar a labelled chip each, packed from the cell's start
        in a left slot, its end in a right one, and centred on the bar in the
        centre slot (today's taskbar); down a side bar a square chip each
        with the label's first letter. They stop short of the cell's end.
        Returns [(kind, rect, label)] in the order the windows opened (the
        back-stack's, never the z-order, so a chip never moves when the focus
        does), on the root canvas; deterministic, so draw + hit-test share it
        without stored state. The answer is kept while the windows, their
        titles, the cell and the scales stand (`_chip_memo`, compared in place
        so that a frame that asks again allocates nothing): the list is
        shared, never changed."""
        got = self._chips_cell()
        if got is None:
            return []
        (edge, (bx, by, bw, bh), _dy, _cells), (_n, (cx, cy, cw, ch), _t, slot) = got
        lay = self.root_ctx.layout
        fs = lay.fs
        side = edge == "left" or edge == "right"
        m = self._chip_memo
        if (m is not None and m[2] is got and m[3] == fs and m[4] == lay.cs
                and self._chips_same(m, side)):
            return m[5]
        return self._chips_made(got, lay, fs, side)

    def chip_model(self):
        """The bar page's `wins` (bar_ui): a row per open window in the order
        they opened -- its kind, its chip title, its icon, whether it holds
        the focus (its chip marked) and its ink: a minimised window's the
        chrome ink, another's the dim chrome ink, the focused one's its
        mark's. The list is kept while all of that stands, compared in
        place, so the page's state is written only on a change."""
        k = self._wins_keys
        i = 0
        same = True
        for kind in self._kinds:
            win = self.windows.get(kind)
            if win is None:
                continue
            on = kind == self.focus_key and not win.minimized
            t = self._chip_title(win)
            if i + 4 > len(k):
                k.extend((None, None, None, None))
                same = False
            if k[i] != kind or k[i + 1] != t or k[i + 2] != on or k[i + 3] != win.minimized:
                k[i], k[i + 1], k[i + 2], k[i + 3] = kind, t, on, win.minimized
                same = False
            i += 4
        if i != len(k):
            del k[i:]
            same = False
        if same:
            return self._wins_rows
        rows = []
        for j in range(0, i, 4):
            win = self.windows[k[j]]
            icon = self._icon_slot(win)
            if icon not in _iconsets.SLOT:
                icon = _ICON_GLYPHS.get(win.kind, "app")
            rows.append({"k": k[j], "title": k[j + 1], "icon": icon, "on": k[j + 2],
                         "tone": "" if k[j + 2] else "chrome_ink" if k[j + 3] else "chrome_ink_dim"})
        self._wins_rows = rows
        return rows

    def _chips_made(self, got, lay, fs, side):
        """`chip_rects`' answer made again (its comprehensions' cells are
        made on every call of the function that holds them, so they live
        here, where only a change runs): the bar page's where it draws the
        bar (`BarLayer.page_chip_rects`), else the look's arithmetic below."""
        (edge, (bx, by, bw, bh), _dy, _cells), (_n, (cx, cy, cw, ch), _t, slot) = got
        kinds = tuple(k for k in self._kinds if k in self.windows)
        if not kinds:
            return []
        if side:
            titles = tuple(self._win_title(self.windows[k]) for k in kinds)
        else:
            titles = tuple(self._chip_title(self.windows[k]) for k in kinds)
        out = []
        self._chip_memo = (kinds, titles, got, fs, lay.cs, out)
        paged = None if side else self.ws.bar_layer.page_chip_rects(got[0], [])
        self.chips_paged = paged is not None
        if paged is not None:
            for k, r in paged:
                out.append((k, r, titles[kinds.index(k)]))
            return out
        if side:
            side = 16 * lay.cs
            x = bx + (bw - side) // 2
            y = by + cy
            end = by + cy + ch - 4 * fs
            for k, t in zip(kinds, titles):
                if y + side > end:
                    break                              # out of bar space -- stop
                out.append((k, (x, y, side, side), t[:1]))
                y += side + 2 * fs
            return out
        pad = _CHIP_PAD                    # the look's: icon slot and margins
        gap = 6
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
        sc = self.root_canvas
        fs = self._fs()
        th = self.ws.theme_colors
        sig = self._chips_gen()
        if sig != self._chip_sig:
            # New chip pixels on the OS bar: the compositor's strip carries them.
            self._outside_f = self.ws.frame_state.frames_drawn
            got = self._chips_cell()
            if got is not None:
                bx, by, bw, bh = got[0][1]
                self.ws.bar_layer.strip_moved(sc, bx, by, bw, bh)
        if not quiet or sig != self._chip_sig:
            self._chip_sig = sig
            self._chip_streak = 0
        elif self._chip_streak >= self.retained_n():
            return
        self._chip_streak += 1
        # The chips blend (their labels' and plates' edges) over the bar's
        # band, which the bar painted once: its ground goes down again first
        # in the root's bar, so a chip repaint is a full repaint's pixels.
        if self.chips_paged and self.ws.bar_layer.redraw_chips("desk"):
            return
        self.ws.bar_layer.repaint_cell("desk", "chips")
        line_h = self.root_ctx.layout.line_h
        self._draw_look_chips(sc, th, fs, line_h)

    def _chips_gen(self):
        """A count that moves whenever the chips' pixels would: each chip's
        window, rect and label, whether it has the focus and whether its
        window is minimised, compared in place in one kept list."""
        k = self._chip_keys
        chips = self.chip_rects()
        moved = len(k) != 5 * len(chips)
        if moved:
            k[:] = [None] * (5 * len(chips))
        i = 0
        for key, r, lb in chips:
            foc = key == self.focus_key
            mini = self.windows[key].minimized
            if (k[i] != key or k[i + 1] != r or k[i + 2] != lb or k[i + 3] != foc
                    or k[i + 4] != mini):
                k[i] = key
                k[i + 1] = r
                k[i + 2] = lb
                k[i + 3] = foc
                k[i + 4] = mini
                moved = True
            i += 5
        if moved:
            self._chip_gen += 1
        return self._chip_gen

    def _draw_look_chips(self, sc, th, fs, line_h):
        """The taskbar chips of a bar the bar page does not draw (a side
        bar's, a centred slot's) as the look's tabs (docs/history/look_2026-10.md step 14):
        the window's icon (in `_win_icon`'s order) then its title, the
        focused window's chip raised under the current mark, another open
        window's in the dim ink. The focused chip's content takes the ink the
        look gives current content (Moy's accent), the plate's second cue. A
        minimized window's chip keeps the chrome's ink."""
        current = _ui_draw.current_ink(sc, th) or th["chrome_ink"]
        for key, (x, y, w, h), label in self.chip_rects():
            win = self.windows[key]
            focused = key == self.focus_key and not win.minimized
            _ui_draw.bar_part(sc, th, _ui_draw.BAR_CHIP, (x, y, w, h), focused)
            ink = (current if focused else
                   th["chrome_ink"] if win.minimized else th["chrome_ink_dim"])
            slot = (x + 12, y + (h - 18) // 2, 18, 18)
            if not self.ws.draw_icon(self._icon_slot(win), slot, ink, sc):
                img = self._win_icon_img(win)
                if img is not None:
                    iw = getattr(img, "w", 16) or 16
                    s = max(1, 18 // iw)
                    sc.spr(img, slot[0] + (18 - iw * s) // 2, slot[1] + (18 - iw * s) // 2, s)
                else:
                    _bar_glyph(_ICON_GLYPHS.get(win.kind, "app"), slot, ink, sc)
            sc.print(label, x + 38, y + (h - line_h) // 2, ink, 1)

    def chip_tap(self, key):
        """Taskbar chip click -- pure FOCUS verbs, never a pop: restore a
        minimized window (and focus it), minimize the focused one (apps only;
        the focus leaves it, as the title strip's minimize leaves it), or just
        move focus to it. Nothing closes from the taskbar."""
        ws = self.ws
        win = self.windows.get(key)
        if win is None:
            return
        ws.mark_dirty()
        if win.minimized:
            self.minimize(win, False)
            self.move_focus(key)
        elif key == self.focus_key:
            self.minimize(win, True)
            self.move_focus(None)
        else:
            self.move_focus(key)
