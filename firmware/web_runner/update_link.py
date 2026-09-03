"""The browser's half of "update the board that served this page" (#41/#53).

The console this page runs IS the console every board runs, so the update
SCREEN is already here -- `runtime/update_ui.py` is frozen into this bundle and
was dead code only because `ws.updater` was None. This module is what makes it
live: a backend behind the SAME injected seam a board fills with
`moy_ota.OtaUpdater`, so Settings -> UPDATE ONLINE in a browser is the screen a
kid sees on glass, with the same two confirms, the same error text and the same
pixels. A second, thinner update UI in the page was built first and deleted;
one console on every tier is the thesis, and this is what it costs to keep.

SHAPE: `gpio_link.py`'s, deliberately. A board capability reaches this VM by
QUEUEING on the Python side while the worker does the I/O and hands the answer
back -- one request in flight, coalesced, answered from the LAST ANSWER. That
is the established shape here (the #9 pins, the 3.4 sync push) and a third
shape for the third capability is the divergence the pattern exists to prevent.

THE IMPEDANCE MISMATCH, stated plainly because it is where a bug will hide.
`UpdateUI` was written for a backend it DRIVES: `download_step()` and `step()`
mean "move one chunk", called once per painted frame, and `dl_done`/`done` are
the counters that advance because of it. Nothing here moves a byte. The BOARD
downloads over its own WiFi and writes its own flash; these methods report the
last polled state and the progress numbers are the board's own, at worst one
poll old. So:

  * they never fake progress. A phase with no `progress` in the document
    leaves the counters where they were, and a board that has said nothing yet
    reads 0/0 -- which the screen draws as an empty bar, because that is what
    is true.
  * `download_step()` returns True while more REMAINS, exactly as the contract
    says, but "remains" is read off the board's state word rather than off a
    file position. It goes False when the board says `ready`, and `step()`
    goes False when the board says `reboot`.
  * a request is not instantaneous, which a local backend never has to admit.
    `pending` is that admission, and `update_ui`'s checking phase honours it --
    without it the first frame's "no manifest" would be reported as an error
    against a board that simply had not answered yet.

THE TWO BOARD SHAPES (`screen` in the status document, moy_webhost):

  * HEADLESS (the Zero): full parity. This screen runs the whole arc -- check,
    confirm the download, confirm the install, watch both -- because the
    browser is that board's only screen.
  * WITH GLASS: the board HANDS THE GLASS BACK instead, and this screen says
    so and stops. It must, and the reason is hardware: a console advances the
    flash one chunk per painted frame OF ITS UPDATE SCREEN, and while a browser
    is driving, that board's glass is parked on the WEB CONSOLE connection
    screen -- so an install driven from here would sit at 0% forever unless
    the chunk work moved into `poll_webhost`, which on the T-Deck is the frame
    tail where an sdspi transaction is the documented panic.

NO UNATTENDED INSTALL, on either shape. Every request here is a tap.
"""

import json

PROTOCOL_V = 1

# How many polls may fail back-to-back before this link gives up for good.
# Deliberately far above gpio_link's MAX_FAILS=5: a board WRITING AN APP
# PARTITION legitimately stops answering for seconds at a time, where a pin
# link that stalls at all is already broken. At the worker's one-second poll
# this is half a minute of silence, which is a board that has gone.
MAX_FAILS = 30

# The board states that mean "the request I sent is still being worked on".
# Keyed by the action, because the answer differs: a check that is still
# checking is pending, a check that reached `downloading` is not (it is not
# even the same request).
_BUSY = {
    "check": ("checking",),
    "download": ("checking", "downloading"),
    "install": ("installing",),
    "cancel": (),
}


class RemoteUpdater:
    """`ws.updater` over a board's pin-gated /update, for the shared UpdateUI."""

    def __init__(self, status, log=print):
        self._log = log
        self._doc = status if isinstance(status, dict) else {}
        self.dead = False
        # -- the request queue (gpio_link's shape) --------------------------
        self._queued = None       # the body the worker should POST next
        self._sent = None         # the body the in-flight POST is carrying
        self._asked = None        # the action this screen visit is waiting on
        self._fails = 0
        self._want_poll = False   # does the console need fresh status?
        # -- the fields UpdateUI reads directly -----------------------------
        self.error = None
        self.absent = False
        self.handed_off = False   # a board WITH GLASS took this onto its screen
        self.boot_verdict = None
        self.dl_done = 0
        self.dl_total = 0
        self.done = 0
        self.total = 0
        self._verdict_seen = False
        self._absorb_doc(self._doc)

    # -- capability ---------------------------------------------------------

    def available(self):
        """False, ALWAYS, and this inverts the local invariant on purpose.

        On a board `available()` means "there are two app slots", and it gates
        Settings -> UPDATE FW, which installs a `.bin` a human put on the
        card. There is no such verb across this seam -- the wire vocabulary is
        check/download/install and none of them names a local file -- so the
        row is expressed as ABSENT rather than as a row that always fails.
        """
        return False

    def online_available(self):
        """True while a board is answering. There is no separate wifi question
        here: the board's own radio does the downloading, and a board that
        could not reach the internet says so in the status it returns."""
        return not self.dead

    # -- what the screen displays -------------------------------------------

    def _running(self, key, default):
        run = self._doc.get("running")
        if isinstance(run, dict) and run.get(key) is not None:
            return run[key]
        return default

    def version(self):
        return int(self._running("version", 0) or 0)

    def version_label(self):
        return str(self._running("label", "?"))

    def channel(self):
        return str(self._running("channel", "stable"))

    def slot(self):
        return str(self._running("slot", "?"))

    def screen(self):
        """Does the board have glass of its own -- the hardware fact that
        decides whether this screen runs the update or hands it back."""
        return bool(self._doc.get("screen"))

    @property
    def pending(self):
        """The board has not answered the request this screen is waiting on.

        A local backend never has to say this, which is exactly why it is a
        separate probe rather than a value smuggled through a return: the
        screen must be able to tell "not yet" from "nothing".
        """
        if self._queued is not None or self._sent is not None:
            return True
        return self._state() in _BUSY.get(self._asked, ())

    # -- the flow, as UpdateUI drives it ------------------------------------

    def find_bin(self):
        """No local image can exist here -- see available()."""
        return None

    def check_online(self, channel=None):
        """Ask the board to look, and answer with what it found.

        Returns the manifest the board fetched (offered or not -- `offers()`
        is what decides), or None with `absent` / `error` / `handed_off` set.
        Called every frame while the screen is on `checking`, so the ASKING
        happens once and every later call reads the answer.
        """
        if self._asked != "check":
            self._ask("check", channel=channel)
            return None
        if self.pending:
            return None
        st = self._state()
        if st == "glass":
            # A board with a screen. Nothing installs here; say so and stop.
            self.handed_off = True
            return None
        if st == "error":
            self.error = self._doc.get("error") or "check failed"
            return None
        if st == "none" and self._doc.get("absent"):
            self.absent = True
            return None
        # `available` is present for BOTH verdicts -- a manifest that was
        # fetched and judged not newer is still what the board found, and it
        # is what "UP TO DATE" is drawn from.
        return self._doc.get("available")

    def offers(self, manifest, channel=None):
        """The BOARD already decided, against ITS firmware version and channel.

        Re-deciding here would mean the browser holding a second opinion about
        a number it read off the board -- and getting it wrong the first time
        a board runs a build this bundle predates.
        """
        return self._state() == "offer"

    def begin_download(self, manifest):
        self._ask("download")

    def download_step(self, max_bytes=None):
        """True while more REMAINS. Nothing is stepped: this reports the last
        polled state, and the bytes are moving on the board."""
        return self._more("download", "ready")

    def download_finish(self):
        """The staged image's path on the BOARD, or None when it is not there.

        Named by the board rather than guessed, because the screen prints its
        basename and the two boards stage in different places.
        """
        if self._state() != "ready":
            return None
        self._asked = None
        return self._doc.get("staged") or "firmware.bin"

    def begin(self, path):
        self._ask("install")

    def step(self, max_blocks=None):
        """True while more REMAINS -- the flash is the board's own."""
        return self._more("install", "reboot")

    def finish(self):
        """True once the board says it pointed the bootloader at the new slot."""
        return self._state() == "reboot"

    def reset(self):
        """A NO-OP, and it has to be: the board resets ITSELF a moment after
        `finish()` (the grace its own state machine keeps so the request that
        asked for this can still read `reboot`). Rebooting is not something a
        page can do to a board, and pretending otherwise would be the one
        place this adapter lied."""
        return None

    def cancel(self):
        self._cancel()

    def download_cancel(self):
        self._cancel()

    def _cancel(self):
        """Drop whatever this screen started. Silent when it started nothing --
        `_exit_update` calls both cancels on EVERY exit, including a B press on
        a screen that never asked the board for anything."""
        if self._asked is None:
            return
        self._ask("cancel")
        self._asked = None
        self.error = None
        self.absent = False

    # -- the queue ----------------------------------------------------------

    def _state(self):
        return self._doc.get("state") or "idle"

    def _more(self, action, done_state):
        if self._asked != action:
            # The screen reached this phase without the request landing (a
            # cancel raced it, a link that died). Nothing remains to wait for.
            return False
        if self.pending:
            return True
        st = self._state()
        if st == "error":
            self.error = self._doc.get("error") or "the board reported a failure"
            return False
        return st != done_state

    def _ask(self, action, channel=None):
        """Queue one request. Last one wins -- a screen that changed its mind
        while a POST was away must not send both."""
        if self.dead:
            self.error = "the console stopped answering"
            return
        doc = {"v": PROTOCOL_V, "action": action}
        if channel:
            # The CHANNEL row travels with the request. It is the browser's
            # choice of what to ask the board to look for -- which on a
            # headless board is the only place that choice can be made at all,
            # since it has no Settings of its own.
            doc["channel"] = channel
        self._queued = doc
        self._asked = None if action == "cancel" else action
        self.error = None
        self.absent = False
        self._want_poll = True

    # -- the pump's end (worker.js) -----------------------------------------

    def take_json(self, pin=None):
        """The next POST body, or "" -- nothing queued, or one in flight."""
        if self.dead or self._sent is not None or self._queued is None:
            return ""
        doc = self._queued
        self._queued = None
        self._sent = doc
        if pin:
            doc["pin"] = pin
        return json.dumps(doc)

    def wants_poll(self):
        """"1" while the console is watching. A GET a second costs a board
        nothing while a kid is on the update screen and would be a request a
        second forever if it never turned off."""
        if self.dead or not self._want_poll:
            return ""
        return "1"

    def ack(self, ok, text=""):
        """Settle a POST or absorb a GET. `text` is the board's status."""
        sent, self._sent = self._sent, None
        if not ok:
            self._fails += 1
            if self._fails >= MAX_FAILS:
                self.stop()
                return False
            # REQUEUE rather than drop. A request nobody answered is still
            # outstanding, and `pending` is the only thing keeping the screen on
            # "checking" instead of concluding "no manifest" about a board that
            # has simply not replied yet. Dropping it made a board that went
            # quiet look like a board with nothing published -- and then the
            # link's own death, MAX_FAILS polls later, arrived at a screen that
            # had already settled on the wrong sentence and stopped listening.
            if sent is not None and self._queued is None:
                self._queued = sent
            return False
        self._fails = 0
        if text:
            self._absorb(text)
        return True

    def _absorb(self, text):
        try:
            doc = json.loads(text)
        except Exception:                # noqa: BLE001 -- a truncated answer
            return
        if not isinstance(doc, dict) or "running" not in doc:
            return
        self._doc = doc
        self._absorb_doc(doc)

    def _absorb_doc(self, doc):
        # Progress is STICKY. The board omits it in phases where nothing is
        # moving (a still 0/0 is what a broken transfer looks like too), and
        # the confirm screen after a download still has to print how big the
        # thing it is about to install was.
        st = doc.get("state")
        p = doc.get("progress")
        if isinstance(p, dict):
            if st == "downloading":
                self.dl_done = int(p.get("done") or 0)
                self.dl_total = int(p.get("total") or 0)
            elif st in ("installing", "reboot"):
                self.done = int(p.get("done") or 0)
                self.total = int(p.get("total") or 0)
        # A download the board has FINISHED is full, whatever the last sample
        # happened to catch. `ready` is the board saying it downloaded AND
        # VERIFIED the image, so this is not inventing progress -- it is
        # refusing to leave the bar at a number the transfer has already gone
        # past. Polling can only ever sample the middle of a transfer, so
        # without this a finished download shows stuck at whatever fraction the
        # last poll before the transition caught.
        if st in ("ready", "installing", "reboot") and self.dl_total:
            self.dl_done = self.dl_total
        av = doc.get("available")
        if isinstance(av, dict) and not self.dl_total:
            # Before a byte has moved, the manifest's own size is the honest
            # total -- it is what the board is about to fetch, not a guess.
            self.dl_total = int(av.get("size") or 0)
        last = doc.get("last")
        if isinstance(last, dict) and not self._verdict_seen:
            # Read ONCE. `_boot_verdict_phase` clears this by assignment after
            # showing it, and the board keeps reporting it until its next
            # install -- so re-absorbing would put the banner back every poll.
            self._verdict_seen = True
            self.boot_verdict = (str(last.get("result") or "ok"),
                                 str(last.get("detail") or ""))
        if doc.get("state") == "glass":
            self.handed_off = True

    def stop(self):
        """Go inert for good. The SCREEN keeps working -- a kid may still be
        looking at it -- and what it reads from here is an error."""
        self.dead = True
        self._queued = None
        self._sent = None
        self._want_poll = False
        # OVERWRITES rather than defers to whatever was already there. A stale
        # error describes a request that finished before the board went away
        # ("no manifest"); this describes the board being GONE, which is the
        # only one of the two a person can still act on. Once the link is dead
        # every other error is history.
        self.error = "the console stopped answering"
        try:
            self._log("update: the board stopped answering")
        except Exception:                # noqa: BLE001
            pass
