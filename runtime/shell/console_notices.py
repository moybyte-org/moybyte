# Map (grep -n a name to jump there):
#   Notices                     the achievements process's start, the notice banner, the unlock banner
#   Notices._draw_note          the notification banner (notifications.py) and its input
"""What the console SAYS on its own: the achievements process's start and
its unlock banner (deadline + beep), the timed system notice and the
firmware-update verdict that rides it, the banners -- the notice, the unlock
and an app's notification (runtime/system/notifications.py), each
native/moy_ui's `moy_ui_unlock` -- and the notification's input. A mixin
of `Workstation`; the deadlines it arms are the flat kernel fields
`_animating` and both WMs read. The cards the console puts up over what is
running are runtime/shell/shell_cards.py's; the intents' hooks are
runtime/shell/console_intents.py's.
"""

from chrome import (_ticks_ms, _ticks_diff)
from widgets import TOAST_MS
import chrome_ops as _ch
import ui_draw as _ui_draw
import layout_base as _lb
import app_perms as _perms
from notifications import NOTES as _NOTES
import devices as _devices
import scheduler as _scheduler
from status_items import ITEMS as _ITEMS
import achievements_ui as _ach_ui
import achievements as _ach


# The two questions a notice asks: stop the music, open a cart.
_STOP_PLAY = ("Cancel", "Stop & play")
STOP_POLL_MS = 50               # how often Stop & play looks for the music's end
STOP_QUIET_MS = 3000            # and how long it waits for it
_OPEN = ("No", "Open")


def _in_box(px, py, b):
    return b is not None and b[0] <= px < b[0] + b[2] and b[1] <= py < b[1] + b[3]


def _desk_class():
    return _lb.metric_class() == _lb.DESK


class Notices:

    def load_achievements(self):
        """(Re)start the achievements process (runtime/system/achievements.py,
        docs/os_2026-11/processes.md section 8), whose prefs hold the badges.
        Call after the store + carts_root are injected (host
        build_workstation / device run_desktop)."""
        self.ach.start()

    def service_env(self, cart):
        """What the console hands service `cart` in its namespace beside
        its app's roles: the achievements process's `game`, `clock_day` and
        `row_stamp`; None for any other."""
        if _ach.is_proc(cart):
            return self.ach.env()
        return None

    def _today_ymd(self):
        """Today as YYYYMMDD off the bar's wall clock, 0 without one (a board
        that never set its clock reads a year before 2020)."""
        y, m, d = self.bar_layer._today()
        return y * 10000 + m * 100 + d if y >= 2020 else 0

    def _achievement_unlocked(self, badge):
        """A fresh unlock's EFFECTS: arm the banner over `badge`, (head,
        title, icon, secret), then celebrate with a short rising beep when
        audio is wired (#21, rev-3 event push). The achievements process's
        notification brings it (`_note_shown`).

        The deadline is written HERE, at the unlock, rather than polled per frame off
        the object -- `_animating` and the WM's overlay signature read the flat
        field and never call into `ach` on the frame path. There is no banner
        QUEUE to preserve: each unlock overwrites the payload, so a second unlock
        inside the window replaces the banner and extends the deadline, which is
        exactly what a later write to this field does. A badge found again
        awards nothing, so it reaches neither the banner nor the beep.

        The deadline is armed BEFORE the beep so a silent (or broken) backend
        cannot cost the kid the banner; the beep itself is best-effort."""
        t = self.ach.toast = badge
        now = _ticks_ms()
        self._toast_at = now
        self._toast_until = now + TOAST_MS
        # ...and the kernel's copy, which a run it drives draws over its frames.
        if t:
            _ch._moy_play().chrome_toast_arm(t[0], t[1], t[2], bool(t[3]), self._toast_until)
        au = self.run.audio
        if au is not None:
            try:
                au.beep(880, 0.08)
                au.beep(1320, 0.12)
            except Exception:  # noqa: BLE001
                pass

    def notice(self, title, sub="", kind="ok", ms=6000):
        """Say something on whatever screen is up, briefly, and then stop.

        For things the MACHINE did on its own -- the achievement toast next door
        is for things the kid did. It expires on a timer with no input, because a
        notice that needs dismissing is a modal, and a modal in front of a kid who
        just wanted to play is worse than the message is worth."""
        self._notice = (str(title), str(sub), kind)
        self._notice_until = _ticks_ms() + int(ms)
        self.frame_state.dirty = True
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
        """The system notice: the unlock banner's card at the top centre of
        the content, its tile a check (a warning's, the warning sign), the
        title in `value` over the line under it. The card's rect."""
        notice = self._notice
        if notice is None:      # expired between the frame's check and its draw
            return None
        cv = self.sys_canvas
        lay = self.layout
        title, sub, kind = notice
        desk = _desk_class()
        return _ui_draw.unlock(cv, self.theme_colors, cv.w // 2, lay.cy + (12 if desk else 6 * lay.fs),
                        title, sub or "", "check" if kind == "ok" else "warn", 0, desk)

    def _draw_toast(self):
        """The unlock banner over whatever is up (`ach.toast`), at the top
        centre of the glass: under the bar where there is one, at the glass's
        top over a fullscreen game. A secret's bursts confetti from its
        edges (achievements_ui names the heads and the burst)."""
        t = self.ach.toast
        if t is None:
            return
        cv = self.sys_canvas
        lay = self.layout
        desk = _desk_class()
        wm = self.wm
        game = (wm.top_is_player() and getattr(wm, "mode", None) != "desktop"
                and not self._running_cart_shows_bar())
        top = (8 if desk else 6 * lay.fs) if game else lay.cy + (12 if desk else 6 * lay.fs)
        burst = 0
        if t[3]:
            el = _ticks_diff(_ticks_ms(), self._toast_at)
            burst = 255 if el >= _ach_ui.BURST_MS else max(1, el * 255 // _ach_ui.BURST_MS)
        _ui_draw.unlock(cv, self.theme_colors, cv.w // 2, top, t[0], t[1], t[2], burst, desk)

    # -- notifications (docs/os_2026-11/processes.md §7) ----------------------

    def _init_notes(self):
        """The notification service's hooks are this console's: who a pid
        is, which cart an app id names, the banner's kernel copy, and acting
        on one."""
        _NOTES.who = self._note_who
        _NOTES.titled = self._note_titled
        _NOTES.shown = self._note_shown
        _NOTES.acted = self._note_acted
        self._note_rect = None          # the banner's card where it last drew
        self._note_y0 = None            # where a press on it went down
    # -- the apps' status items (docs/theme_and_shell_2026-11.md §8.6) --------

    def _init_status(self):
        """The status items' hooks are this console's: an item's caption is
        its process's title, and its menu closes with it."""
        _ITEMS.title_of = self._status_title
        _ITEMS.closed = self._status_closed
        self.status_open = None         # the item whose desk menu is open
        self.status_open_k = 0          # ...its place in the bar

    def _status_title(self, pid):
        try:
            return self.procs.who(pid)[0]
        except Exception:  # noqa: BLE001 -- a caption is never worth a crash
            return ""

    def _status_closed(self, item):
        m = self.sysmenu
        if m.open and ((m.widget == "status" and self.status_open is item)
                       or (m.widget == "battery" and not self.wm.desk_open())):
            self.close_popover()
        if self.status_open is item:
            self.status_open = None
        self.frame_state.dirty = True

    def ask_stop_music(self, cart):
        """A compiled game that fits with the VM stopped only without the
        music (the Player's `_music_keeps_the_vm`): the running set's
        dialog asks first, Cancel focused; Stop & play stops the stream as
        the shell, then opens the game again once the media service has
        given its memory back (its run ends on the frame's job), now free
        to stop the VM."""
        title = str((cart or {}).get("title") or "this game")[:18]

        def then():
            media = _devices.api("media")
            if media is None:
                self.open(cart)
                return
            media.stop()
            self._open_when_quiet(media, cart)
        self.confirm.ask_close("Stop the music to play %s?" % title,
                               "%s needs the music's memory. You can start the music "
                               "again after." % title, then, _STOP_PLAY)

    def _open_when_quiet(self, media, cart, waited=None):
        """`cart` opened once the media service's run has ended (a frame or
        two after its stop), STOP_QUIET_MS at most."""
        if not media.running() or (waited is not None and waited[0] >= STOP_QUIET_MS):
            if waited is not None:
                _scheduler.SCHED.plain_cancel(waited[1])
            self.open(cart)
            return
        if waited is None:
            waited = [0, 0]
            waited[1] = _scheduler.SCHED.plain_every(
                STOP_POLL_MS, lambda: self._open_when_quiet(media, cart, waited))
        waited[0] += STOP_POLL_MS

    def ask_first(self, cart, then):
        """A cart that is asked before its first run (`app_perms.asks_first`:
        it arrived as files, with no consent record): the install prompt's
        lines in the running set's dialog, "Open <title>?", No focused;
        Open records the consent, as an install does, then `then()`. No
        records nothing and the cart stays unrun. True when it asked."""
        consent = getattr(self, "consent", None)
        if consent is None or not cart:
            return False
        aid = _perms.app_id(cart)
        if not _perms.asks_first(cart, consent.get(aid)):
            return False
        title = str(cart.get("title") or "this cart")[:18]
        perms = list(cart.get("permissions") or ())

        def allow():
            consent.record(aid, perms)
            then()
        self.confirm.ask_lines("Open %s?" % title, _perms.prompt(cart), allow, _OPEN)
        return True

    def open_status_menu(self, item):
        """Status item `item`'s menu on the desk, hung from its cell; the
        same item again closes it (`toggle_popover`)."""
        if item is None:
            return
        if _ITEMS.bare(item):
            self.open_status_app(item)
            return
        m = self.sysmenu
        same = m.open and m.widget == "status" and self.status_open is item
        if m.open and m.widget == "status" and not same:
            self.close_popover()
        self.status_open = item
        sh = _ITEMS.shown
        for k in range(len(sh)):
            if sh[k] is item:
                self.status_open_k = k
        self.toggle_popover("status")
        self.frame_state.dirty = True

    def _note_who(self, pid):
        return self.procs.who(pid)

    def _note_titled(self, app):
        """The cart app id `app` names, None where none on the shelf does."""
        for c in self.carts.all:
            if _perms.app_id(c) == app:
                return c
        return None

    def _note_shown(self, n, until):
        """The banner went up (`n`) or down: the kernel's copy, which a run
        it drives draws over its frames, follows. The achievements process's
        banner is the unlock banner: its card with the kernel's words and a
        secret's burst, and the beep; the notification stays in the list."""
        self.frame_state.dirty = True
        badge = self.ach.badge_of(n)
        if badge is not None:
            self._achievement_unlocked(badge)
            _NOTES.dismiss()
            return
        if n is None:
            self._note_rect = None
            _ch._moy_play().chrome_note("", "", "", 0)
        else:
            _ch._moy_play().chrome_note(n.app, n.summary, n.icon, until or 1)

    def _note_acted(self, n, key):
        """Notification `n` was opened by `key`: its process comes to the
        front -- a run the front's, as the task manager's Switch to brings
        it (the game in front then throttled on the desk, minimised on a
        handheld), a cart no longer running opened again -- and its
        `moy_action(id, key)` runs; a service, which has no window, gets
        the call alone, and an app's own service's notification opens its
        app too (scheduler.as_service)."""
        self.frame_state.dirty = True
        brought = self._bring(n.pid)
        if brought is not None:
            if brought:
                self.run.player.window_event("moy_action", n.id, key)
            return
        import scheduler as _sched
        hooked = _sched.SCHED.hook(n.pid, "moy_action", n.id, key)
        c = n.cart
        if hooked and not (c and c.get("entry") and c.get("path")):
            return
        self._open_cart_of(c)

    def _open_cart_of(self, c):
        """Process cart `c` opened again: an app's own service's its app's
        cart, a service with no app nothing."""
        if c and c.get("entry") and c.get("path"):
            c = next((a for a in self.carts.all if a.get("path") == c["path"]), None)
        if c and c.get("path") and c.get("type") != "service":
            if self.wm.top_is("tasks") and self.procs.handheld():
                self.close_tasks()
            self.open(c)

    def open_status_app(self, item):
        """Status item `item`, which has no menu, was tapped: its process's
        run comes to the front, as a notification's does, else its cart
        opens (an app's own service's, its app)."""
        if item is None or not item.pid:
            return
        self.frame_state.dirty = True
        self.close_popover()
        if self._bring(item.pid) is not None:
            return
        self._open_cart_of(self.procs.who(item.pid)[1])

    def _bring(self, pid):
        """Process `pid`'s run comes to the front, as the task manager's
        Switch to brings it: True where it is the front run after, False
        where it could not come, None where no run is `pid`'s."""
        if pid is None:
            return None
        procs = self.procs
        pl = self.run.player
        if pl is not None and pl.pid == pid:
            if not procs.handheld():
                self.wm.bring_forward("desktop")
            elif self.wm.top_is("tasks"):
                self.close_tasks()
            return True
        for p in procs.list:
            if p.player.pid == pid:
                self.tasks_switch(p.key)
                return self.run.player.pid == pid
        return None

    def _note_top(self):
        """The banner's top: under the bar where there is one, at the
        glass's top over a fullscreen game (the unlock banner's)."""
        lay = self.layout
        desk = _desk_class()
        wm = self.wm
        game = (wm.top_is_player() and getattr(wm, "mode", None) != "desktop"
                and not self._running_cart_shows_bar())
        return (8 if desk else 6 * lay.fs) if game else lay.cy + (12 if desk else 6 * lay.fs)

    def _draw_note(self):
        """The notification banner over whatever is up: the posting app's
        title over the summary, on its icon's tile, at the top centre."""
        n = _NOTES.up()
        if n is None:
            return
        cv = self.sys_canvas
        self._note_rect = _ui_draw.unlock(cv, self.theme_colors, cv.w // 2, self._note_top(),
                                          n.app, n.summary, n.icon, 0, _desk_class())

    def _draw_rec(self):
        """The recording indicator while a process holds the microphone
        (`devices.MIC`): kernel chrome at the glass's top right, over
        everything but the cursor, which nothing turns off."""
        cv = self.sys_canvas
        _ui_draw.rec(cv, self.theme_colors, cv.w - 2, 2, _desk_class())

    def _note_pointer(self, px, py, click):
        """A tap on the banner opens its notification; a press on it dragged
        up dismisses it, the app in front playing on. While a press on it is
        held, the pointer is the banner's."""
        n = _NOTES.up()
        y0 = self._note_y0
        if n is None:
            self._note_y0 = None
            return False
        down = bool(getattr(self.pointer, "down", False))
        if y0 is None:
            r = self._note_rect
            if r is None or not (down or click) or not _in_box(px, py, r):
                return False
            if click and not down:
                _NOTES.act(n.id)
                return True
            self._note_y0 = py
            return True
        if down and not click:
            return True
        self._note_y0 = None
        if y0 - py >= 8 * self.layout.fs:
            _NOTES.dismiss()
        else:
            r = self._note_rect
            if r is not None and _in_box(px, py, r):
                _NOTES.act(n.id)
        return True
