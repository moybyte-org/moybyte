"""The console's end of the achievements process (docs/os_2026-11/processes.md
section 8): `ws.ach`, and the notify role's server.

The badges are the process's (system_carts/moybyte.achievements.moy, PROC):
a service the console starts when it loads its store (`Link.start`),
handing it the document the store holds, the write and the day
(`Link.env`). The notify role's rows name it (`"server":
"proc:achievements"`, native/os_calls.json):

  achieve, award    MESSAGES to the process (the scheduler's `send`): it
                    wakes on them in the scheduler's slice, records, and
                    posts a notification on a fresh find, which the console
                    shows as the unlock banner
                    (console_notices.Notices._note_shown)
  count, has, found_on, progress, today
                    a Python process's functions of those names, called as
                    reads; a process of another tier (a Lua service, whose
                    state is its own task's) is read from the document it
                    last handed back, which is the whole of its state

The process hands the document back and the console writes it at its next
frame (`flush`), outside any slice: a store write on a card can outlast a
callback's slice and its abort.

While the process is not running (stopped in the task manager, crashed, a
build without its cart) an event goes nowhere, and the reads answer the last
document the console holds.
"""

try:
    import scheduler as _sched
except ImportError:  # host: the runtime package
    from runtime import scheduler as _sched
try:
    from widgets import Achievements, ACH_TITLE, ACH_GLYPH, ACH_HIDDEN
except ImportError:  # host: the runtime package
    from runtime.widgets import Achievements, ACH_TITLE, ACH_GLYPH, ACH_HIDDEN

PROC = "moybyte.achievements"           # the cart the notify role's rows name
TITLE = "Achievements"
_BY_TITLE = {t: i for i, t in ACH_TITLE.items()}


def is_proc(cart):
    """Whether `cart` is the achievements process's: by its id, or, for an
    embedded seed entry that carries none, by its title and type."""
    cid = cart.get("id")
    if cid is not None:
        return cid == PROC
    return cart.get("type") == "service" and cart.get("title") == TITLE


def _err_text(exc):
    return "%s: %s" % (type(exc).__name__, exc)


class Link:
    """`ws.ach`: the achievements process as the console reaches it."""

    def __init__(self, ws):
        self.ws = ws
        self.pid = None
        self.badges = None      # the last document: the store's, or handed back
        self.pending = None     # handed back, not yet written
        # The unlock banner's payload, (id, title, glyph, hidden), set when
        # the process's notification arrives; the banner's deadline is the
        # console's own flat field (`ws._toast_until`).
        self.toast = None
        self._last = None       # the process's tracker, kept for the reads once it stops

    # -- the process --------------------------------------------------------

    def _cart(self):
        carts = getattr(self.ws, "carts", None)
        for c in (carts.all if carts is not None else ()):
            if is_proc(c):
                return c
        return None

    def start(self):
        """(Re)start the process over the document the store holds: a
        running one is stopped first (an earlier load's, or another
        console's where a host runs two: one process a host, as a board has
        one console), this console's document written. Answers its pid, or
        None."""
        self.flush()
        sched = _sched.SCHED
        for name in (PROC, TITLE):
            old = sched.find(name)
            if old is not None and old.cart is not None and is_proc(old.cart):
                sched.stop(old.pid)
        self.badges = self.ws.prefs.load_achievements()
        self._last = None
        cart = self._cart()
        self.pid = None if cart is None else _sched.start_cart(self.ws, cart)
        ns = self._ns()
        if ns is not None:
            self._last = ns.get("ACH")
        return self.pid

    def env(self):
        """What the console hands the process, in its namespace."""
        return {"badges": self.badges, "badges_save": self._handed,
                "clock_day": self.ws._today_ymd}

    def _handed(self, doc):
        self.badges = doc
        self.pending = doc
        self._last = None

    def flush(self):
        """Write the document the process handed back, where one waits."""
        doc = self.pending
        if doc is None:
            return
        self.pending = None
        try:
            self.ws.prefs.save_achievements(doc)
        except Exception as exc:  # noqa: BLE001 -- a failed save is not remembered
            print("Moybyte achievements save failed:", _err_text(exc))

    def _proc(self):
        pid = self.pid
        return None if pid is None else _sched.SCHED.procs.get(pid)

    def _ns(self):
        """A running Python process's namespace, else None."""
        p = self._proc()
        return None if p is None else p.ns

    def running(self):
        p = self._proc()
        return p is not None and p.state != _sched.EXITED

    def tracker(self):
        """A running Python process's tracker; else the one it stopped
        with, or one over the last document."""
        ns = self._ns()
        if ns is not None and ns.get("ACH") is not None:
            self._last = ns["ACH"]
        elif self._last is None:
            self._last = Achievements(self.badges)
        return self._last

    @property
    def unlocked(self):
        return self.tracker().unlocked

    def badge_of(self, n):
        """The badge notification `n` announces (its id), or None where the
        process did not post it."""
        if n is None or self.pid is None or n.pid != self.pid:
            return None
        return _BY_TITLE.get(n.summary)

    def payload(self, ach_id):
        return (ach_id, ACH_TITLE[ach_id], ACH_GLYPH.get(ach_id, "trophy"),
                ACH_HIDDEN.get(ach_id, False))

    # -- the events: messages ------------------------------------------------

    def note(self, event, key=None):
        """A milestone `event` (with its `key`) to the process."""
        if self.pid is not None:
            _sched.SCHED.send(self.pid, "achieve", (event, key))

    def award(self, ach_id, quiet=False, day=None):
        """Badge `ach_id` to the process: True where it is a badge not yet
        found and the process took it. `quiet` and `day` keep a badge found
        on another console, uncelebrated."""
        if ach_id not in ACH_TITLE or self.pid is None or self.has(ach_id):
            return False
        return _sched.SCHED.send(self.pid, "award", (ach_id, quiet, day))

    # -- the reads: the process's functions ----------------------------------

    def count(self):
        ns = self._ns()
        return ns["count"]() if ns is not None else self.tracker().count()

    def has(self, ach_id):
        ns = self._ns()
        return ns["has"](ach_id) if ns is not None else self.tracker().has(ach_id)

    def found_on(self, ach_id):
        ns = self._ns()
        return ns["found_on"](ach_id) if ns is not None else self.tracker().found_on(ach_id)

    def progress(self, ach_id):
        ns = self._ns()
        return ns["progress"](ach_id) if ns is not None else self.tracker().progress(ach_id)

    def today(self):
        ns = self._ns()
        return ns["today"]() if ns is not None else self.ws._today_ymd()

    def doc(self):
        """achievements.json's document as it stands."""
        return self.tracker().doc()


class NotifyRole:
    """The notify role's server (native/os_calls.json's `proc:achievements` rows):
    each verb takes the grant and goes to the process through `ws.ach`."""

    def __init__(self, ws):
        self.__ws = ws

    def achieve(self, g, kind, key=None):
        """A milestone event, to the process."""
        self.__ws.ach.note(kind, key)

    def award(self, g, ach_id):
        """Badge `ach_id` (a hidden one's find), to the process: True where
        it is new."""
        return bool(self.__ws.ach.award(ach_id))

    def count(self, g):
        """How many badges are found."""
        return self.__ws.ach.count()

    def has(self, g, ach_id):
        """Whether badge `ach_id` is found."""
        return self.__ws.ach.has(ach_id)

    def found_on(self, g, ach_id):
        """The day (YYYYMMDD) badge `ach_id` was found, 0 for a day nobody
        knows, None while it is not found."""
        return self.__ws.ach.found_on(ach_id)

    def progress(self, g, ach_id):
        """(done, goal) of a counted badge, None for one that is not."""
        return self.__ws.ach.progress(ach_id)

    def today(self, g):
        """Today as the badges record it (YYYYMMDD off the bar's clock), 0
        without a clock."""
        return self.__ws.ach.today()
