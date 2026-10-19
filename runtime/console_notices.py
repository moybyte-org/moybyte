# Map (grep -n a name to jump there):
#   Notices                     the achievements wiring, the notice banner, the toast
#   PauseMenu                   the card over a held game: Resume, Options, Quit
#   SystemMenuUI                the system menu's rows and their actions
#   SystemMenuUI.card_rows      the home's selected card as a menu group
#   SystemMenuUI.open_card      that group alone, hung at a card (long press, pad X)
#   SystemMenuUI._draw_look     the rows as the look's popover card
"""What the console SAYS on its own: the achievements wiring (the tracker,
the unlock's toast deadline + beep), the timed system notice banner and the
firmware-update verdict that rides it, and the two overlay draws. A mixin of
`Workstation`; the deadlines it arms are the flat kernel fields `_animating`
and both WMs read.
"""

try:
    from chrome import (_ticks_ms, _ticks_diff, NAMES)
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.chrome import (_ticks_ms, _ticks_diff, NAMES)
try:
    from widgets import (Achievements, TOAST_MS, _POPUP_W)
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.widgets import (Achievements, TOAST_MS, _POPUP_W)
try:
    import chrome_ops as _ch
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime import chrome_ops as _ch
try:
    import ui_draw as _ui_draw
    import ui as _ui
    import layout_base as _lb
    import shell_layout as _shell_layout
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime import ui_draw as _ui_draw
    from runtime import ui as _ui
    from runtime import layout_base as _lb
    from runtime import shell_layout as _shell_layout
try:
    from moy_store_base import builtin_name as _builtin_name
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.moy_store_base import builtin_name as _builtin_name


# The 2026-10 look's words and icons for the system menu's rows, by string id
# (docs/look_2026-10.md step 25a); a theme without a depth draws the kernel's
# chrome menu in the string table's capitals.
_LOOK_TEXT = {"menu.cart": "Cart", "menu.restart_cart": "Restart cart",
              "menu.delete_cart": "Delete cart", "menu.favourite_add": "Add favourite",
              "menu.favourite_remove": "Remove favourite", "menu.system": "System",
              "menu.clear_search": "Clear search", "menu.search": "Search",
              "menu.settings": "Settings", "menu.about": "About", "menu.reboot": "Restart"}
_LOOK_ICON = {"menu.restart_cart": "rotate", "menu.delete_cart": "trash",
              "menu.favourite_add": "star", "menu.favourite_remove": "star_on",
              "menu.clear_search": "search", "menu.search": "search",
              "menu.settings": "settings", "menu.about": "doc", "menu.reboot": "rotate"}
# The selected card's verbs (LauncherHomeLayer.card_verbs) as menu rows:
# (the chrome's label, icon, the look's text); the star's are the string ids'.
_CARD_ROW = {"change": ("CHANGE", "pencil", "Change"), "copy": ("COPY", "dup", "Copy"),
             "delete": ("DELETE", "trash", "Delete"), "sure": ("DELETE", "trash", "Delete")}
_DANGER_ICON = "trash"      # a row that wears it destroys: the danger rows' ink


def _row(kind, sid, action=None):
    """A Popup row: (kind, the chrome's label, action, icon, the look's text)."""
    return (kind, _ui_draw.s(sid), action, _LOOK_ICON.get(sid, ""), _LOOK_TEXT[sid])


def _in_box(px, py, b):
    return b is not None and b[0] <= px < b[0] + b[2] and b[1] <= py < b[1] + b[3]


def _desk_class():
    return _lb.metric_class() == _lb.DESK


class _CardVerb:
    """A card group row's action: the home's verb on its selected card.
    Its Delete deletes at once (to the Trash): the row is the choice."""

    def __init__(self, home, verb):
        self.home = home
        self.verb = "sure" if verb == "delete" else verb

    def __call__(self):
        self.home._do_verb(self.verb)


class Notices:

    def load_achievements(self):
        """Wire a fresh Achievements over the badges the store remembers (#21).

        The read is `prefs`'; the WIRING is kernel -- persistence goes back to
        the store, the unlock effects (the toast deadline + the beep) are the
        kernel's own. Call after the store + carts_root are injected (host
        build_workstation / device run_desktop)."""
        self.ach = Achievements(self.prefs.load_achievements(),
                                on_save=self.prefs.save_achievements,
                                on_unlock=self._achievement_unlocked)

    def _achievement_unlocked(self, ach_id):
        """A fresh unlock's EFFECTS: arm the toast overlay, then celebrate with a
        short rising beep when audio is wired (#21, rev-3 event push).

        `Achievements` generates the effect and the kernel executes it. The
        deadline is written HERE, at the unlock, rather than polled per frame off
        the object -- `_animating` and the WM's overlay signature read the flat
        field and never call into `ach` on the frame path. There is no toast
        QUEUE to preserve: `award()` overwrites its payload, so a second unlock
        inside the window replaces the banner and extends the deadline, which is
        exactly what a later write to this field does.

        The deadline is armed BEFORE the beep so a silent (or broken) backend
        cannot cost the kid the banner; the beep itself is best-effort."""
        self._toast_until = _ticks_ms() + TOAST_MS
        # ...and the kernel's copy, which a run it drives draws over its frames.
        t = getattr(self.ach, "toast", None)
        if t:
            _ch._moy_play().chrome_toast_arm(t[1], t[2], self._toast_until)
        au = self.audio
        if au is not None:
            try:
                au.beep(880, 0.08)
                au.beep(1320, 0.12)
            except Exception:  # noqa: BLE001
                pass

    # The hidden Easter eggs (#21) are self.ach_ui's (achievements_ui.py,
    # AchievementsUI): the 3 eggs + their trigger state + the popup payload +
    # _show_egg + _draw_egg/_draw_confetti/_draw_achievements. The achievement
    # core is the two methods above; the overlay deadlines those objects arm
    # are created in Workstation._init_overlays.

    def notice(self, title, sub="", kind="ok", ms=6000):
        """Say something on whatever screen is up, briefly, and then stop.

        For things the MACHINE did on its own -- the achievement toast next door
        is for things the kid did. It expires on a timer with no input, because a
        notice that needs dismissing is a modal, and a modal in front of a kid who
        just wanted to play is worse than the message is worth."""
        self._notice = (str(title), str(sub), kind)
        self._notice_until = _ticks_ms() + int(ms)
        self._dirty = True
        # The kernel's copy, which a run it drives draws over its frames.
        _ch._moy_play().chrome_notice(self._notice[0], self._notice[1], kind == "ok",
                                      self._notice_until)

    def notice_active(self, now=None):
        if self._notice is None:
            return False
        if _ticks_diff(self._notice_until, now if now is not None else _ticks_ms()) <= 0:
            self._notice = None
            return False
        return True

    def announce_update(self):
        """Put the firmware-update verdict on the desktop (#53).

        An update lands during a REBOOT: the screen that asked for it is gone by
        the time there is an answer, so unless the machine volunteers it the kid
        learns nothing -- a successful update looks like a slow reboot, and a
        rolled-back one looks exactly the same. Reading it here does NOT clear it;
        Settings -> UPDATE still has it for anyone who missed the banner."""
        u = getattr(self, "updater", None)
        verdict = getattr(u, "boot_verdict", None)
        if not verdict:
            return False
        if verdict[0] == "ok":
            self.notice(_ui_draw.s("notice.updated"), _ui_draw.s("notice.now") % u.version_label(), "ok")
        else:
            self.notice(_ui_draw.s("notice.undone"), _ui_draw.s("notice.still_on") % u.version_label(), "warn")
        return True

    def _draw_notice(self):
        """The system banner: a wide strip atop the content rect, title + one small
        line, sized off `layout` so it looks deliberate on a 1024x600 desktop
        too. The kernel's one body (moy_chrome_banner)."""
        cv = self.sys_canvas
        lay = self.layout
        title, sub, kind = self._notice
        mp = _ch.chrome_inks(self.theme_colors, NAMES)
        _ch.replay(mp.chrome_banner(lay.w, lay.fs, lay.cy, title, sub, kind == "ok"),
                   cv, self)

    def _draw_toast(self):
        """A small celebratory banner near the top: a trophy, "ACHIEVEMENT
        UNLOCKED!", the achievement's name and its glyph, drawn last over
        whatever screen is up. The kernel's one body (moy_chrome_toast)."""
        cv = self.sys_canvas
        _ach_id, title, glyph = self.ach.toast
        mp = _ch.chrome_inks(self.theme_colors, NAMES)
        _ch.replay(mp.chrome_toast(title, glyph), cv, self)

    # _draw_egg / _draw_confetti / _draw_achievements (the egg popup, Konami
    # confetti, and achievements-list overlay) now live on self.ach_ui
    # (achievements_ui.py, AchievementsUI). frame() calls self.ach_ui._draw_*.


class PauseMenu:
    """The pause menu (docs/look_2026-10.md step 25a): over a fullscreen game
    Super holds the run (`Player.pause`: no tick, no draw, its clock and its
    music held) and a card centred on the glass names the game and offers
    Resume, Restart, Options and Quit; Esc, B or Super again resumes, and
    the cart's next frame comes as if no time passed. Options is the card's
    second page, the console's Volume (Esc goes back). The card is a
    popover of the public UI API's with nothing to hang from, holding a
    `ui.menu`, drawn on the system canvas over the frozen frame; the menu's
    keys and taps are this object's, so an action runs in the input phase,
    and Options' slider takes the engine's."""

    ITEMS = ("Resume", "Restart", "Options", "Quit")
    ICONS = ("play", "rotate", "settings", "close")

    def __init__(self, ws):
        self.ws = ws
        self.open = False
        self.sel = 0
        self.page = 0               # 0 the menu, 1 Options
        self._host = None
        self._key = 0               # the key typed last frame (a press is its change)
        self._under = None          # what the card's band covered: (layer, x)
        self._capture = False       # take that band before the card first draws

    def _width(self, desk):
        return 300 if desk else 188 * self.ws.layout.fs

    def _keep_under(self, cv, desk):
        """Where the game's frame IS the system canvas (the handheld), the
        card draws over the frozen frame itself: keep the whole frame, which
        the scrim dims under the card, so each frame starts from it, and a
        smaller card (Options) and the resumed game find it whole. Where the
        game is composited, its frame repaints under the card."""
        self._capture = False
        ws = self.ws
        new = getattr(cv, "new_layer", None)
        if ws.canvas is not cv or new is None:
            return
        try:
            lay = new(cv.w, cv.h)
        except Exception:  # noqa: BLE001 -- no room: the card draws without it
            return
        lay.blit_strip(cv, 0, 0)
        self._under = (lay, 0)

    def _put_under(self, cv):
        u = self._under
        if u is not None:
            lay, x = u
            cv.blit_strip_rect(lay, x, 0, x, 0, lay.w, lay.h)

    def _drop_under(self):
        u, self._under = self._under, None
        if u is not None:
            rel = getattr(u[0], "release", None)
            if rel is not None:
                rel()

    def show(self):
        ws = self.ws
        ws.player.pause()
        self.open = True
        self.sel = 0
        self.page = 0
        self._key = getattr(ws.input, "last_key", 0)
        self._drop_under()
        self._capture = True
        if self._host is None:
            self._host = _ui_draw.AppHost()
        else:
            self._host.reset()
        ws._dirty = True

    def close(self):
        """Resume: the run is given back."""
        if not self.open:
            return
        self.open = False
        self._put_under(self.ws.sys_canvas)   # the frame as the game left it
        self._drop_under()
        self.ws.player.resume()
        self.ws._dirty = True

    def back(self):
        """Esc and B: Options back to the menu, the menu back to the game."""
        if self.page:
            self.page = 0
            self._host.reset()
            self.ws._dirty = True
        else:
            self.close()

    def choose(self, i):
        ws = self.ws
        if i == 0:
            self.close()
        elif i == 1:
            self.close()
            ws._reset_canvas_state()
            ws._start()                   # the same cart from the top, same caller
        elif i == 2:
            self.page = 1
            self._host.reset()
        elif i == 3:
            self.close()
            ws._reset_canvas_state()
            ws._exit_to_caller()
        ws._dirty = True

    def handle_input(self, i):
        k = getattr(i, "last_key", 0) or 0
        esc = k == 27 and self._key != 27
        self._key = k
        if i.pressed("b") or i.pressed("stop") or esc:
            self.back()
            return
        if self.page:
            if self._host.keys(i):
                self.ws._dirty = True
            return
        n = len(self.ITEMS)
        if i.pressed("up") and self.sel > 0:
            self.sel -= 1
            self.ws._dirty = True
        elif i.pressed("down") and self.sel < n - 1:
            self.sel += 1
            self.ws._dirty = True
        elif i.pressed("a") or i.pressed("run"):
            self.choose(self.sel)

    def handle_pointer(self, px, py, click):
        host = self._host
        ws = self.ws
        if self.page:
            if click and not _in_box(px, py, host.box("Pause")):
                self.back()
            elif host.point(px, py, click, bool(getattr(ws.pointer, "down", False))):
                ws._dirty = True
            return
        if not click:
            return
        for j, t in enumerate(self.ITEMS):
            if _in_box(px, py, host.box(t)):
                self.sel = j
                self.choose(j)
                return
        if not _in_box(px, py, host.box("Pause")):
            self.close()                  # a press outside the card resumes

    def draw(self):
        # The card is the shell's, not the held cart's: the shell's fonts and
        # icons, whatever the run left the app-cart boundary at.
        was = _ui_draw.cart_text(False)
        try:
            self._draw()
        finally:
            _ui_draw.cart_text(was)

    def _draw(self):
        ws = self.ws
        cv = ws.sys_canvas
        host = self._host
        desk = _desk_class()
        if self._capture:
            self._keep_under(cv, desk)
        else:
            self._put_under(cv)
        ws.icon_spec()                    # the theme's set in the engine, as the bar has it
        # the paused game dims under the card (D-01): over the frame just
        # put back, or the composite just made, never over the last scrim;
        # the whole glass, or on the desk the content under its bar
        if self._under is not None or ws.canvas is not cv:
            if getattr(ws.wm, "mode", None) == "desktop":
                lay = ws.layout
                _ui_draw.scrim(cv, ws.theme_colors, (lay.cx, lay.cy, lay.cw, lay.ch))
            else:
                _ui_draw.scrim(cv, ws.theme_colors, (0, 0, cv.w, cv.h))
        if not self.page:
            host.focus(self.ITEMS[self.sel])
        settled = host.begin(cv, ws.theme_colors, (0, 0, cv.w, cv.h), desk)
        _ui.popover("Pause", self._width(desk))
        cart = ws.cart
        _ui.icon("moy")
        _ui.info(str((cart.get("title") if cart else "") or "Game"), "Paused")
        if self.page:
            sl = ws.settings_layer
            cur, lo, hi = sl.level("volume")
            if cur == lo:
                _ui.icon("mute")
            got = _ui.slider("Volume", cur, lo, hi, {"steps": True})
            if got != cur:
                sl.set_level("volume", got)
            _ui.hints(("change", "Change", "back", "Back"))
        else:
            _ui.menu("", self.ITEMS, self.ICONS)
            _ui.hints(("confirm", "Choose", "back", "Resume"))
        _ui.popover()
        if not settled:
            ws.request_frame()


class SystemMenuUI:
    """The ≡ system menu's rows and their actions (#52). Its drawing, and the
    ABOUT box's, is the kernel's one body (moy_chrome_menu, moy_chrome_about),
    replayed on the SYSTEM canvas. The `sysmenu` Popup, the `_about` flag,
    `reboot_hook` and `toggle_sysmenu()` stay on the Workstation (its tested
    surface); the privileged verbs (REBOOT, DELETE) are reached through it."""

    def __init__(self, ws, names):
        self.ws = ws
        self._NAMES = names
        self._host = None           # the look's card: its verbs' context
        self._card_at = (0, 0)      # where a card's menu hangs (`open_card`)

    def _sysmenu_items(self):
        """The rows for this open of the ≡ menu (see class note for the tuple form).
        The cart group is OMITTED entirely (not greyed) when no cart is open."""
        rows = []
        cart = self.ws.cart
        if cart is not None:
            # headed by the cart's own title; Delete (to the Trash, never
            # asked) last, and only for a cart the store can delete: a
            # built-in is the console's, so it offers none
            title = cart.get("title") or ""
            head = _row("header", "menu.cart")
            if title:
                head = ("header", title.upper(), None, "", title)
            rows.append(head)
            rows.append(_row("item", "menu.restart_cart", self._menu_restart_cart))
            if self._deletable(cart):
                rows.append(_row("item", "menu.delete_cart", self._menu_delete_cart))
            rows.append(("sep",))
        elif self.ws.screen == "launcher":
            # over the home, the selected card's group in the open cart's
            # place: its title over its verbs (`card_rows`)
            card = self.card_rows()
            if card:
                rows.extend(card)
                rows.append(("sep",))
        rows.append(_row("header", "menu.system"))
        # SEARCH (#105): only meaningful over the run-grid, so it's offered ONLY
        # from the launcher home -- the label flips to CLEAR SEARCH once a query
        # is active/typing, so the same row both opens and dismisses it.
        if self.ws.screen == "launcher":
            sid = ("menu.clear_search" if (self.ws.search_typing or self.ws.search_query)
                   else "menu.search")
            rows.append(_row("item", sid, self.ws.toggle_search))
        rows.append(_row("item", "menu.settings", self.ws.open_settings))
        rows.append(_row("item", "menu.about", self._menu_about))
        rows.append(_row("item", "menu.reboot", self._menu_reboot))
        return rows

    def card_rows(self, home=None):
        """A home's selected card as a menu group (the fullscreen home's,
        else `home`'s, the desk's projects window): its title over a row a
        verb of `card_verbs` (the favourite star's words follow its state;
        Delete takes it to the Trash, never asked, as the cart group's
        does). [] where the card has none."""
        home = home or self.ws.launcher_layer
        verbs = home.card_verbs()
        if not verbs:
            return []
        it = home.grid.selected()
        title = it.get("title") or ""
        rows = [("header", title.upper(), None, "", title) if title
                else _row("header", "menu.cart")]
        for v in verbs:
            if v == "favourite":
                fav = self.ws.carts.is_favorite(it)
                rows.append(_row("item", "menu.favourite_remove" if fav
                                 else "menu.favourite_add", home.toggle_favourite))
            else:
                label, icon, text = _CARD_ROW[v]
                rows.append(("item", label, _CardVerb(home, v), icon, text))
        return rows

    def open_card(self, home, at):
        """`home`'s selected card's group alone, as a menu hung at `at`
        (x, y): what a long press on the card and a gamepad's X open. False
        where the card has no verbs."""
        rows = self.card_rows(home)
        if not rows:
            return False
        ws = self.ws
        m = ws.sysmenu
        m.fs = ws.look.effective_font_scale()
        m.cs = ws.layout.cs
        m.w = 0
        cv = ws.sys_canvas
        x, y = at
        # the Popup's own panel (a theme with no depth): under the point,
        # over it where the glass has no room below
        pw = _POPUP_W * m.fs
        m.anchor_x = max(0, min(x - pw // 2, cv.w - pw))
        m.anchor_y = y
        m.up = False
        self._card_at = (x, y)
        m.show(rows, widget="card")
        x0, y0, w0, h0 = m.panel_rect()
        if y0 + h0 > cv.h:
            m.up = True
        ws.popover_touch()
        return True

    def _deletable(self, cart):
        """Whether the open cart is the store's to delete: a store that
        writes, and a cart of its own, not a built-in it seeds."""
        path = cart.get("path")
        return bool(path) and bool(getattr(self.ws, "can_manage", True)) \
            and _builtin_name(path) is None

    def _menu_restart_cart(self):
        # Re-run the open cart from its current config (TIC-80 restart), landing back
        # on the running-cart screen -- exactly what GO/apply does.
        if self.ws.cart is not None:
            self.ws.apply()

    def _menu_delete_cart(self):
        # Delete the OPEN cart (carts.delete() with no cart targets ws.cart, which
        # need not be the home's selection), then go home.
        # carts.delete() guards read-only / last-cart. Count the FULL cart list (a
        # wallpaper isn't in the launcher run-grid) to detect the deletion.
        before = len(self.ws.carts.all)
        self.ws.carts.delete()
        if len(self.ws.carts.all) < before:
            self.ws.go_home()

    def _menu_about(self):
        # A tiny dismissible info modal (any tap / ESC / B closes it), drawn on top.
        self.ws._dirty = True
        self.ws._about = True

    def _menu_reboot(self):
        # Device: the injected reboot hook (machine.reset). Host / no hook: a safe
        # fallback to the home launcher (a hard reset would kill the sim window).
        self.ws._dirty = True
        self.ws.settings_layer.commit_name()   # a Name still being typed is kept
        hook = self.ws.reboot_hook
        if hook is not None:
            try:
                hook()
                return
            except Exception as exc:  # noqa: BLE001
                print("Moybyte reboot failed:", exc)
        self.ws.go_home()                 # safe stub when no reboot hook is wired

    def _draw_sysmenu(self):
        """The popover: the ≡ dropdown's rows -- a panel flush against the
        bar, one row per item, the selected one on the accent fill, headers
        dim, a 1px line between groups; `fs` (#39/#58) scales the text and
        `cs` (#203) the rows -- or a component in the same dialog shell."""
        ws = self.ws
        m = ws.sysmenu
        if self.looked():
            self._draw_look()
            return
        x, y, w, h = m.panel_rect()
        if m.comp is not None:
            # A component's panel: the menu's dialog shell, its painter inside.
            cv = ws.sys_canvas
            th = ws.theme_colors
            _ui_draw.dialog(cv, (x, y, w, h), ring=th["edge"], fill=th["panel"])
            m.comp.draw(cv, (x, y, w, h))
            return
        rows = []
        for it in m.items:
            kind = it[0]
            if kind == "sep":
                rows.append((2, ""))
            else:
                rows.append((1 if kind == "header" else 0, it[1]))
        mp = _ch.chrome_inks(ws.theme_colors, self._NAMES)
        _ch.replay(mp.chrome_menu(rows, m.sel, x, y, w, h, m.fs, m.cs), ws.sys_canvas, ws)

    # -- the 2026-10 look's card (docs/look_2026-10.md step 25a) ----------------

    def scrims(self):
        """Whether a system modal's scrim is up: this menu or a card's menu,
        ABOUT, the pause menu. A bar widget's popover (the calendar, Wi-Fi,
        power) hangs from its widget as an app's picker does, and dims
        nothing (docs/look_2026-10.md, D-01)."""
        ws = self.ws
        m = ws.sysmenu
        return (m.open and m.comp is None) or ws._about or ws.pause.open

    def scrim(self):
        """Dim what the system menu, a card's menu or ABOUT covers: the
        content under the bars, which stay as they are (D-01). Once a full
        frame, before the modal draws; a frame that paints the popover's
        rect alone keeps it."""
        ws = self.ws
        m = ws.sysmenu
        if m.open and m.comp is not None and not ws._about:
            return
        lay = ws.layout
        _ui_draw.scrim(ws.sys_canvas, ws.theme_colors, (lay.cx, lay.cy, lay.cw, lay.ch))

    def looked(self):
        """The menu's rows show as the look's card: a theme with a depth."""
        m = self.ws.sysmenu
        return m.open and m.comp is None and bool(_ui_draw.depth()[0])

    def _look_w(self, desk):
        return 220 if desk else 150 * self.ws.layout.fs

    def look_card(self):
        """The look's card, (x, y, w, h), as the engine lays it out (a
        popover hung under the system button, `_draw_look`'s rows), known
        before it draws: what the windowed desk snapshots and paints alone.
        tests/test_system_menu.py holds it to the card the engine draws."""
        ws = self.ws
        cv = ws.sys_canvas
        desk = _desk_class()
        fs = getattr(cv, "font_scale", 1) or 1
        d = 1 if desk else 0
        row = _ui_draw.metrics("row")
        grp = _ui_draw.metrics("group")
        vr = _ui_draw.metrics("value_row")
        rh, gap = row[5 + d] * fs, row[7] * fs
        cap = grp[3 + d] * fs
        pad = vr[28 + d] * fs
        rule = (2 * row[7] + (9 if desk else 5)) * fs
        body, n, first = 0, 0, True
        for it in ws.sysmenu.items:
            if it[0] == "header":
                if n:
                    body += n * (rh + gap)
                    n = 0
                body += cap + gap
                first = False
            elif it[0] == "item":
                if first and body:
                    body += rule + gap
                first = False
                n += 1
        body += n * (rh + gap)
        w = min(self._look_w(desk), cv.w - 2 * pad)
        bh = min(body, cv.h - 4 * pad) if 2 * pad + body > cv.h - 2 * pad else body
        h = 2 * pad + bh
        ax, ay, aw, ah = self._menu_cell()
        x = max(0, min(ax, cv.w - w))
        y = ay + ah + 4 * fs
        if y + h > cv.h:
            y = ay - 4 * fs - h
            if y < 0:
                y = max(0, cv.h - h)
        return (x, y, w, h)

    def panel_rect(self):
        """The rect the open popover paints: the Popup's, or the look's card
        with its shadow's reach."""
        m = self.ws.sysmenu
        if not self.looked():
            return m.panel_rect()
        x, y, w, h = self.look_card()
        dp = _ui_draw.depth()
        fs = self.ws.layout.fs
        r = (dp[9] + abs(dp[10]) + abs(dp[11]) + 2) * fs
        cv = self.ws.sys_canvas
        x0, y0 = max(0, x - r), max(0, y - r)
        x1, y1 = min(cv.w, x + w + r), min(cv.h, y + h + r)
        return (x0, y0, x1 - x0, y1 - y0)

    def _menu_cell(self):
        """The system button's cell on the system canvas, the card's anchor,
        its left at least the popover's gap from the glass's edge (4 pixels
        at the font scale, 8 on the desk), as far as it hangs below the bar.
        A card's menu (`open_card`) hangs from its point instead, a line as
        wide as the card and centred on it, kept on the glass."""
        ws = self.ws
        if ws.sysmenu.widget == "card":
            x, y = self._card_at
            cv = ws.sys_canvas
            w = min(self._look_w(_desk_class()), cv.w)
            return (max(0, min(x - w // 2, cv.w - w)), y, w, 0)
        bars = ws.layout.bars
        got = _shell_layout.cell_of(bars, "menu")
        if got is None:
            return (0, 0, 0, 0)
        i, (bx, by, bw, bh) = got
        rx, ry = bars[i][1][0], bars[i][1][1]
        x = rx + bx
        edge = 4 * ws.layout.fs * _lb.unit()
        if x < edge:
            bw, x = bw - (edge - x), edge
        return (x, ry + by, bw, bh)

    def _draw_look(self):
        """The rows as a popover of the public UI API's hung under the system
        button: a captioned `ui.menu` a group, each row its icon, the
        Popup's row the focused one (its keys stay the Popup's, so an action
        runs in the input phase)."""
        ws = self.ws
        m = ws.sysmenu
        cv = ws.sys_canvas
        host = self._host
        if host is None:
            host = self._host = _ui_draw.AppHost()
        if 0 <= m.sel < len(m.items) and m.items[m.sel][0] == "item":
            host.focus(m.items[m.sel][4])
        desk = _desk_class()
        settled = host.begin(cv, ws.theme_colors, (0, 0, cv.w, cv.h), desk)
        _ui.at(*self._menu_cell())
        _ui.popover("System", self._look_w(desk))
        cap, texts, icons, kinds = "", [], [], []
        for it in m.items:
            if it[0] == "header":
                if texts:
                    _ui.menu(cap, texts, icons, kinds=kinds)
                cap, texts, icons, kinds = it[4], [], [], []
            elif it[0] == "item":
                texts.append(it[4])
                icons.append(it[3])
                # Delete is a verb that destroys: the danger rows' ink
                kinds.append("danger" if it[3] == _DANGER_ICON else "")
        if texts:
            _ui.menu(cap, texts, icons, kinds=kinds)
        _ui.popover()
        if not settled:
            ws.request_frame()

    def look_hit(self, px, py):
        """The Popup row index a tap at (px, py) lands on in the look's card,
        -1 inside the card on no row, None outside it."""
        host = self._host
        if host is None:
            return None
        m = self.ws.sysmenu
        for i, it in enumerate(m.items):
            if it[0] == "item" and _in_box(px, py, host.box(it[4])):
                return i
        return -1 if _in_box(px, py, host.box("System")) else None

    def _draw_about(self):
        """ABOUT: the console's name and firmware version, centred; any tap,
        ESC or B closes it."""
        ws = self.ws
        cv = ws.sys_canvas
        mp = _ch.chrome_inks(ws.theme_colors, self._NAMES)
        _ch.replay(mp.chrome_about(cv.w, cv.h, getattr(cv, "font_scale", 1),
                                   self._firmware_version_text()), cv, ws)

    def _firmware_version_text(self):
        """A short firmware-version string for ABOUT, or "" when unknown (host). Reads
        the injected updater's version when present (device moy_ota.FIRMWARE_VERSION)."""
        u = self.ws.updater
        if u is not None:
            v = getattr(u, "version", None)
            try:
                v = v() if callable(v) else v
            except Exception:  # noqa: BLE001
                v = None
            if v is not None:
                return "FW " + str(v)
        return ""
