"""The WEB CONSOLE switch (#197) -- wasm mode as a Workstation collaborator.

`Workstation.web`. Everything the switch is made of lives here: the pairing
pin, the paired url, the park/unpark of the glass, the Settings row's label,
and the one funnel that starts and stops the host. The connection screen
itself is `web_console_ui.WebConsoleUI` -- this object owns the one instance
(`ws.web.ui`) and the flag that says the glass is parked on it.

Thin verbs over the injected `ws.webhost`, so settings_layer never touches a
socket and every tier without the service is untouched. The service contract
is `.serving` / `.start()` / `.stop()` / `.url()`.

The webhost is read THROUGH `ws` on every call and never captured here: it is
`None` at construction and injected later by `wire_workstation_core`, and the
settings load later still (the same ordering `make_webhost` respects by reading
the pin at `start()` rather than at construction). The pin's own persistence
goes to the sibling collaborator that owns system.json (`ws.prefs`, #209
landing B); `ws.system` stays a plain alias of the dict it holds, so reading and
writing a key through it needs nothing from this object.
"""

try:
    from web_console_ui import WebConsoleUI
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.web_console_ui import WebConsoleUI

try:
    from chrome import _ticks_us
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.chrome import _ticks_us


class WebConsole:
    """The mode, not a session.

    Turning the row on parks the glass on the connection screen and turning it
    off returns the console -- there is no heartbeat, no presence detection and
    no timeout, because the two-writer collision the mode exists to prevent is
    designed out rather than detected: while a browser is editing this store,
    nothing on the glass can be, because the glass is showing this screen.
    """

    _PIN_DIGITS = 4

    def __init__(self, ws, names, in_rect):
        self.ws = ws
        # The WEB CONSOLE connection screen (web_console_ui.py) + the flag that
        # says the glass is parked on it. `parked` is what makes wasm mode a
        # MODE and not a screen: every return-to-the-launcher path (go_home)
        # re-parks while it is set, so a cart launched from the browser comes
        # back HERE rather than dropping a kid onto a shelf the browser is
        # concurrently rewriting.
        self.ui = WebConsoleUI(ws, names, in_rect)
        self.parked = False

    def pin(self):
        """The pairing pin the browser must carry (`?pin=NNNN`), as a string.

        MINTED ONCE, LAZILY, and then persisted in system.json beside every
        other Settings choice. Lazily because a board that never serves the web
        console should never have written a secret to its store; once because a
        pin that changed per boot would mean re-scanning the QR after every
        power cycle, and a kid's phone keeping the old url would look like the
        board had broken.

        Four digits is the strength a kid can read off a panel and a grown-up
        can type. It is not a password: it stops the OTHER machine on the
        network from writing to this store by accident, which is the threat an
        open write endpoint on a classroom LAN actually poses (moy_webhost's
        SECURITY note). The read half stays open, by the standing owner call."""
        ws = self.ws
        pin = ws.system.get("web_pin")
        if pin:
            return str(pin)
        pin = self._mint_pin()
        ws.system["web_pin"] = pin
        ws.prefs.persist()
        return pin

    def _mint_pin(self):
        """A fresh 4-digit pin. `os.urandom` where there is one (both boards and
        the host have it); the clock is the fallback, and is only ever reached
        on a build with no urandom at all."""
        n = None
        try:
            import os as _os
            n = int.from_bytes(_os.urandom(3), "big")
        except Exception:  # noqa: BLE001 -- no urandom: fall through to the clock
            n = None
        if n is None:
            n = _ticks_us()
        return "%04d" % (n % (10 ** self._PIN_DIGITS))

    def url(self):
        """The PAIRED url -- what the QR encodes and SHOW ADDRESS reveals.

        `http://<ip>/?pin=NNNN`: the page forwards its own `?pin=` into
        every sync batch, so scanning this is the whole pairing gesture. Empty
        when nothing is serving -- there is no address to show then, and a
        placeholder would encode to a QR that sends a phone nowhere."""
        wh = self.ws.webhost
        if wh is None or not getattr(wh, "serving", False):
            return ""
        try:
            paired = getattr(wh, "paired_url", None)
            return (paired() if paired is not None else wh.url()) or ""
        except Exception:  # noqa: BLE001 -- a url is not worth a crash
            return ""

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

        On the windowed tier `go_home` also leaves the desk, which is what makes
        this fullscreen there: windows exist only above the desk (#105), so the
        play world presents every kind full-screen with no special case."""
        self.parked = True
        self.ui.on_enter()
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
        if self.serving():
            self.toggle(why=why)
        else:
            self.unpark()

    def unpark(self):
        """Give the console back. The desk is home on the windowed tier; the
        launcher root everywhere else."""
        ws = self.ws
        self.parked = False
        ws._dirty = True
        if getattr(ws.wm, "has_desk", False):
            ws.open_desk()
        else:
            ws.wm.goto("launcher")

    def serving(self):
        wh = self.ws.webhost
        return bool(wh is not None and getattr(wh, "serving", False))

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
            if getattr(wh, "serving", False):
                self._stop_saying_why(wh, why)
            else:
                wh.start()
        except Exception as exc:  # noqa: BLE001
            wh.error = "%s" % exc
        self.ws._dirty = True
        if getattr(wh, "serving", False):
            if not self.parked:
                self.park()
        elif self.parked:
            self.unpark()
