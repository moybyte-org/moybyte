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
    from widgets import (Achievements, TOAST_MS)
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.widgets import (Achievements, TOAST_MS)


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
        """The system banner: a wide strip under the top bar, title + one small line.

        Sized off `layout` rather than the frozen 320x240 numbers the achievement
        toast uses, because this one has to look deliberate on a 1024x600 desktop
        too."""
        cv = self.sys_canvas
        lay = self.layout
        fs = lay.fs
        title, sub, kind = self._notice
        th = self.theme_colors
        accent = th["play"] if kind == "ok" else NAMES["orange"]
        w = min(lay.w - 16 * fs, max(180 * fs, (len(title) + 2) * 8 * fs * 2))
        h = 34 * fs
        x = (lay.w - w) // 2
        y = lay.status_h + 6 * fs
        cv.rect(x, y, w, h, th["surface"])
        cv.rectb(x, y, w, h, accent)
        cv.rect(x, y, w, 3 * fs, accent)          # a lit edge, not a full title bar
        self._glyph("gear", (x + 5 * fs, y + 8 * fs, 14 * fs, 14 * fs), accent, cv)
        cv.print(title[:22], x + 22 * fs, y + 7 * fs, th["ink"], 2 * fs)
        if sub:
            cv.print(sub[:26], x + 22 * fs, y + 22 * fs, th["ink_dim"], 1 * fs)

    def _draw_toast(self):
        """A small celebratory banner near the top: a trophy + "ACHIEVEMENT!" + the
        achievement name + its glyph. Drawn last each frame over whatever screen is
        up, so it never disturbs the content beneath and expires on its own. Indexed
        API only (host == device)."""
        cv = self.sys_canvas
        ach_id, title, glyph = self.ach.toast
        x, y, w, h = 36, 26, 248, 38
        cv.rect(x, y, w, h, NAMES["dark_purple"])
        cv.rectb(x, y, w, h, NAMES["yellow"])
        cv.rect(x, y, w, 12, NAMES["yellow"])
        self._glyph("trophy", (x + 2, y - 1, 12, 12), NAMES["black"], cv)
        cv.print("ACHIEVEMENT UNLOCKED!", x + 16, y + 2, NAMES["black"], 1)
        self._glyph(glyph, (x + 6, y + 16, 16, 16), NAMES["yellow"], cv)
        cv.print(title[:24], x + 28, y + 20, NAMES["white"], 2)

    # _draw_egg / _draw_confetti / _draw_achievements (the egg popup, Konami
    # confetti, and achievements-list overlay) now live on self.ach_ui
    # (achievements_ui.py, AchievementsUI). frame() calls self.ach_ui._draw_*.
