"""What the console SAYS on its own: the achievements wiring (the tracker,
the unlock's toast deadline + beep), the timed system notice banner and the
firmware-update verdict that rides it, and the two overlay draws. A mixin of
`Workstation`; the deadlines it arms are the flat kernel fields `_animating`
and both WMs read.
"""

try:
    from chrome import (_ticks_ms, _ticks_diff, NAMES, _GLYPHS)
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.chrome import (_ticks_ms, _ticks_diff, NAMES, _GLYPHS)

# The glyphs the kernel draws over a run it drives (the toast's trophy and the
# badges', the banner's gear, the crash bar's menu), handed to its raster once.
KERNEL_GLYPHS = ("trophy", "gear", "menu", "app", "run", "paint", "map", "code",
                 "star", "heart", "spark", "smile", "key")


def register_glyphs():
    mp = _ch._moy_play()
    for k in KERNEL_GLYPHS:
        rows = _GLYPHS.get(k)
        if rows is not None:
            mp.chrome_glyph(k, rows)
try:
    from widgets import (Achievements, TOAST_MS)
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.widgets import (Achievements, TOAST_MS)
try:
    import chrome_ops as _ch
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime import chrome_ops as _ch


class Notices:

    def load_achievements(self):
        """Wire a fresh Achievements over the badges the store remembers (#21).

        The read is `prefs`'; the WIRING is kernel -- persistence goes back to
        the store, the unlock effects (the toast deadline + the beep) are the
        kernel's own. Call after the store + carts_root are injected (host
        build_workstation / device run_desktop)."""
        register_glyphs()
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
            self.notice("MOYBYTE UPDATED", "now %s" % u.version_label(), "ok")
        else:
            self.notice("UPDATE UNDONE", "still on %s" % u.version_label(), "warn")
        return True

    def _draw_notice(self):
        """The system banner: a wide strip under the top bar, title + one small
        line, sized off `layout` so it looks deliberate on a 1024x600 desktop
        too. The kernel's one body (moy_chrome_banner)."""
        cv = self.sys_canvas
        lay = self.layout
        title, sub, kind = self._notice
        mp = _ch.chrome_inks(self.theme_colors, NAMES)
        _ch.replay(mp.chrome_banner(lay.w, lay.fs, lay.status_h, title, sub, kind == "ok"),
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


class SystemMenuUI:
    """The ≡ system menu's rows and their actions (#52). Its drawing, and the
    ABOUT box's, is the kernel's one body (moy_chrome_menu, moy_chrome_about),
    replayed on the SYSTEM canvas. The `sysmenu` Popup, the `_about` flag,
    `reboot_hook` and `toggle_sysmenu()` stay on the Workstation (its tested
    surface); the privileged verbs (REBOOT, DELETE) are reached through it."""

    def __init__(self, ws, names):
        self.ws = ws
        self._NAMES = names

    def _sysmenu_items(self):
        """The rows for this open of the ≡ menu (see class note for the tuple form).
        The cart group is OMITTED entirely (not greyed) when no cart is open."""
        rows = []
        if self.ws.cart is not None:
            rows.append(("header", "CART"))
            rows.append(("item", "RESTART CART", self._menu_restart_cart))
            rows.append(("item", "DELETE CART", self._menu_delete_cart))
            rows.append(("sep",))
        rows.append(("header", "SYSTEM"))
        # SEARCH (#105): only meaningful over the run-grid, so it's offered ONLY
        # from the launcher home -- the label flips to CLEAR SEARCH once a query
        # is active/typing, so the same row both opens and dismisses it.
        if self.ws.screen == "launcher":
            label = ("CLEAR SEARCH"
                      if (self.ws.search_typing or self.ws.search_query)
                      else "SEARCH")
            rows.append(("item", label, self.ws.toggle_search))
        rows.append(("item", "SETTINGS", self.ws.open_settings))
        rows.append(("item", "ABOUT", self._menu_about))
        rows.append(("item", "REBOOT", self._menu_reboot))
        return rows

    def _menu_restart_cart(self):
        # Re-run the open cart from its current config (TIC-80 restart), landing back
        # on the running-cart screen -- exactly what GO/apply does.
        if self.ws.cart is not None:
            self.ws.apply()

    def _menu_delete_cart(self):
        # Delete the OPEN cart (carts.delete() targets ws.cart when a cart is open --
        # which a picker-opened cart is, even if it's not the launcher selection),
        # then go home.
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
        hook = self.ws.reboot_hook
        if hook is not None:
            try:
                hook()
                return
            except Exception as exc:  # noqa: BLE001
                print("Moybyte reboot failed:", exc)
        self.ws.go_home()                 # safe stub when no reboot hook is wired

    def _draw_sysmenu(self):
        """The ≡ dropdown: a panel flush under the bar, one row per item, the
        selected one on the accent fill, headers dim, a 1px line between
        groups. `fs` (#39/#58) scales the text and `cs` (#203) the rows."""
        ws = self.ws
        m = ws.sysmenu
        x, y, w, h = m.panel_rect()
        rows = []
        for it in m.items:
            kind = it[0]
            if kind == "sep":
                rows.append((2, ""))
            else:
                rows.append((1 if kind == "header" else 0, it[1]))
        mp = _ch.chrome_inks(ws.theme_colors, self._NAMES)
        _ch.replay(mp.chrome_menu(rows, m.sel, x, y, w, h, m.fs, m.cs), ws.sys_canvas, ws)

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
