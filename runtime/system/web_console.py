"""The WEB CONSOLE switch (#197) -- wasm mode as a Workstation collaborator.

`Workstation.web`. Everything the switch is made of lives here: the paired
url (its single-use pairing token), the park/unpark of the glass, the Settings row's label,
and the one funnel that starts and stops the host. The connection screen
itself is `web_console_ui.WebConsoleUI` -- this object owns the one instance
(`ws.web.ui`) and the flag that says the glass is parked on it.

Thin verbs over the injected `ws.webhost`, so settings_layer never touches a
socket and every tier without the service is untouched. The service contract
is `.serving` / `.start()` / `.stop()` / `.url()`.

The webhost is read THROUGH `ws` on every call and never captured here: it is
`None` at construction and injected later by `wire_workstation_core`, and the
settings load later still (the same ordering `make_webhost` respects by reading
the sign-in's folder at `start()` rather than at construction).
"""

from web_console_ui import WebConsoleUI
from chrome import _ticks_us


def _moy_net():
    try:
        import moy_net
        return moy_net
    except ImportError:
        return None


class WebConsole:
    """The mode, not a session.

    Turning the row on parks the glass on the connection screen and turning it
    off returns the console -- there is no heartbeat, no presence detection and
    no timeout, because the two-writer collision the mode exists to prevent is
    designed out rather than detected: while a browser is editing this store,
    nothing on the glass can be, because the glass is showing this screen.
    """

    def __init__(self, ws, names):
        self.ws = ws
        # The WEB CONSOLE connection screen (web_console_ui.py) + the flag that
        # says the glass is parked on it. `parked` is what makes wasm mode a
        # MODE and not a screen: every return-to-the-launcher path (go_home)
        # re-parks while it is set, so a cart launched from the browser comes
        # back HERE rather than dropping a kid onto a shelf the browser is
        # concurrently rewriting.
        self.ui = WebConsoleUI(ws.app_context("webconsole", WebConsoleUI.NEEDS),
                               self, names)
        self._parked = False
        self._paired = None             # (the QR's url, its ticks_us)
        self._hint = None

    @property
    def parked(self):
        """The glass given to the connection screen: the kernel's flag where
        the switch is (moy_net.wc_park, a surface epoch), else this object's."""
        return self._parked

    @parked.setter
    def parked(self, on):
        self._parked = bool(on)
        wc_park = getattr(_moy_net(), "wc_park", None)
        if wc_park is not None and getattr(self.ws.webhost, "joining", None) is not None:
            wc_park(self._parked)

    # The QR's token outlives a draw: one is minted when the link is first
    # asked for and kept until a browser spends it (on_paired) or it nears the
    # kernel's ten minutes (moy_webauth.h, MOY_WA_TOKEN_MS).
    _TOKEN_US = 540 * 1000 * 1000

    def url(self):
        """The PAIRED url -- what the QR encodes: the address and, after its
        `#`, a fresh single-use pairing token and the board's id (moy_net.h,
        moy_wc_url). The page pairs with the token and never sends it in a
        URL; the address alone is what the screen prints. Empty when nothing
        is serving -- there is no address to show then, and a placeholder
        would encode to a QR that sends a phone nowhere."""
        wh = self.ws.webhost
        if wh is None or not getattr(wh, "serving", False):
            self._paired = None
            return ""
        now = _ticks_us()
        got = self._paired
        if got is not None and 0 <= now - got[1] < self._TOKEN_US:
            return got[0]
        try:
            paired = getattr(wh, "paired_url", None)
            url = (paired() if paired is not None else wh.url()) or ""
        except Exception:  # noqa: BLE001 -- a url is not worth a crash
            return ""
        self._paired = (url, now) if url else None
        self._hint = None
        return url

    def sign_in_hint(self):
        """How a browser that cannot scan the code signs in, one sentence,
        read with the link it sits beside (the password file is a read)."""
        if self._hint is None:
            import ssh_service
            self._hint = ("Or type this console's password" if ssh_service.is_set(self.ws)
                          else "Scan to pair: set a password (passwd) to type one instead")
        return self._hint

    def on_paired(self):
        """A browser paired with the QR's token: the screen draws a fresh one,
        and Settings' list of paired browsers reads the new one."""
        self._paired = None
        self._hint = None
        sl = getattr(self.ws, "settings_layer", None)
        if sl is not None:
            sl._read_paired()
        self.ws._dirty = True

    def park(self):
        """Take the glass over with the connection screen (#197).

        WASM MODE IS A SWITCH, NOT A SESSION (owner call, 2026-08-25). While the
        toggle is on, the browser owns this store and the glass shows how to
        reach it -- so parking goes through `go_home`, which commits every open
        editor and app before the browser starts writing underneath them, and
        `go_home` then re-parks here (and on every later return: a cart launched
        by PLAY ON DEVICE exits back to THIS screen, not to a shelf the browser
        is concurrently rewriting).

        `parked` is set FIRST: `go_home` is what routes to the screen, and it
        reads this flag. The screen's own entry state (a revealed address is
        cleared) is reset here too -- nothing in the router calls a Layer's
        `on_enter`.

        On the windowed tier it enters console mode first, which is what makes
        this fullscreen there: the desk's windows wait parked, and console mode
        presents every kind full-screen with no special case."""
        self.parked = True
        self.ui.on_enter()
        self.ws.wm.enter_console()
        self.ws.go_home()

    @staticmethod
    def _stop_saying_why(wh, why):
        """`wh.stop(why=...)`, falling back to a host that cannot take one.

        SAY WHY. This is the one place a person deliberately takes the console
        back, and the page cannot tell that from an unplugged board unless the
        board says so before it goes (moy_webhost's closing window). Without it
        a kid who pressed this got the vanished-board panel and its data-loss
        warning.

        The fallback is not politeness. `toggle` wraps this in a bare except
        that turns any failure into the row's label, so a host with the older
        no-argument `stop()` -- the host simulator's, a test's fake -- would
        raise TypeError, be swallowed, and NOT STOP, leaving a row that says ON
        over a console the kid just switched off. Catching the signature
        mismatch here and retrying without the reason keeps the stop
        unconditional and makes the goodbye the optional part, which is the
        right way round.
        """
        if why is None:
            wh.stop()               # no goodbye: the caller has another signal
            return
        try:
            wh.stop(why=why)
        except TypeError:
            wh.stop()

    def stop(self, why="off"):
        """The connection screen's TURN OFF, and the update hand-off's.

        Not `toggle` directly, and the difference is the one state a toggle gets
        wrong: if the host stopped UNDERNEATH the parked screen (a socket error,
        a stop from somewhere else), toggling would read "not serving" and START
        it again -- a button labelled TURN OFF that turns it on. Ask what the
        user wants, which is out.

        `why` rides through to the browser (see toggle). The default is a person
        pressing TURN OFF; `ConsoleUpdate` passes "update", because a console
        board handing its glass back to run its own update screen is not the
        same event to whoever is watching in a tab."""
        if self.serving() or self.joining():
            self.toggle(why=why)
        else:
            self.unpark()

    def unpark(self):
        """Give the console back. The desk is home on the windowed tier, with
        the windows parked when the console was taken; the launcher root
        everywhere else."""
        ws = self.ws
        self.parked = False
        ws._dirty = True
        ws.wm.goto("launcher")          # out of the screen, not over it
        if getattr(ws.wm, "has_desk", False):
            ws.open_desk()

    def serving(self):
        wh = self.ws.webhost
        return bool(wh is not None and getattr(wh, "serving", False))

    def joining(self):
        wh = self.ws.webhost
        return bool(wh is not None and getattr(wh, "joining", False))

    def on_serving(self):
        """The switch came up after a join: the glass goes to the screen."""
        self.ws._dirty = True
        if not self.parked:
            self.park()

    def on_failed(self):
        """The join ran out or the bind was refused: the row says why, the
        radio goes, and the glass (never parked by a join) stays where it is."""
        self.ws.wifi_release("web")
        self.ws._dirty = True
        if self.parked:
            self.unpark()

    def label(self):
        """What the Settings row shows: the ADDRESS while serving, else OFF.

        The address IS the feature -- the kid has to type it into a browser --
        so a row that said only "ON" would be telling them to go and find the
        IP somewhere else. A failure shows its reason here too, for the same
        reason: this row is the only surface this feature has.
        """
        wh = self.ws.webhost
        if wh is None:
            return "OFF"
        if getattr(wh, "error", None):
            return str(wh.error)[:22]
        if getattr(wh, "joining", False):
            return "JOINING..."
        if not getattr(wh, "serving", False):
            return "OFF"
        url = ""
        try:
            url = wh.url() or ""
        except Exception:  # noqa: BLE001 -- a url is not worth a crash
            url = ""
        # Strip the scheme: the row is ~22 chars at 1x and "http://" spends 7 of
        # them on something every browser assumes anyway.
        return url.replace("http://", "").rstrip("/") or "ON"

    def toggle(self, why="off"):
        """Start or stop serving, and park or unpark the glass with it (#197).

        `why` is what the browser is told when this STOPS the host -- "off" for
        a person switching the row, "update" for the console taking its glass
        back to run its own update screen. It reaches the page through
        moy_webhost.stop's closing window and decides which sentence a reader
        gets; it is ignored when this call starts the host.

        Starting touches WiFi, which can fail slowly and in ways nobody can act
        on from a Settings screen (no AP, wrong password, DHCP). A raised
        exception here would take the console down from a toggle, so the failure
        becomes the row's own label instead.

        The park is gated on `serving` AFTER the attempt, never on "we tried to
        start": a failed start leaves the kid in Settings looking at the reason,
        which is the only place that reason is readable. This is the ONE funnel
        -- the Settings row, the dev channel's `web`, and the connection
        screen's own TURN OFF all come through here -- so the mode and the
        socket cannot disagree about which of them is on."""
        wh = self.ws.webhost
        if wh is None:
            return
        try:
            wh.error = None
            if getattr(wh, "joining", False):
                wh.stop()                    # a join nobody waits for any more
            elif getattr(wh, "serving", False):
                self._stop_saying_why(wh, why)
                # The radio lease outlives `serving` by the goodbye window: the
                # page's last round trip rides the link, so a host that is
                # still CLOSING keeps it and lets go from its own stop (the
                # `on_stop` make_webhost wires). An immediate stop lets go now.
                if not getattr(wh, "closing", None):
                    self.ws.wifi_release("web")
            else:
                self.ws.wifi_hold("web")
                wh.start()
        except Exception as exc:  # noqa: BLE001
            wh.error = "%s" % exc
            self.ws.wifi_release("web")      # a start that failed holds nothing
        self.ws._dirty = True
        if getattr(wh, "serving", False):
            if not self.parked:
                self.park()
        elif self.parked:
            self.unpark()
