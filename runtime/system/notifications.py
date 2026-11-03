"""Notifications (docs/os_2026-11/processes.md §7): what an app posts with
`moybyte.notify`, the banner the OS draws over whatever is running, and the
list the task manager shows.

A notification carries the freedesktop fields: a `summary` (one line), a
`body`, an `icon` (a name of the console's icon set; "bubble" where it names
none), `actions` (the freedesktop flat list, key then label, `"default"` the
action the notification itself stands for), an `urgency` (LOW, NORMAL,
CRITICAL) and `replaces_id` (a notification of the same process it updates in
place, keeping its id). Beside them, who posted it: the process, its title
and its cart, read when it posts.

THE LIST holds every notification not yet acted on or cleared, newest first,
KEEP at most (the oldest goes). THE BANNER is the newest one posted at NORMAL
or CRITICAL, up until its deadline (BANNER_MS, CRITICAL_MS) and then only in
the list: a LOW one goes to the list alone, and a new banner replaces the one
up. Nothing reaches the app in front: the shell draws the banner over it (its
`banner` layer; the kernel's copy, `moy_chrome_note`, over a run the kernel
drives) and reads the input that acts on it.

  act(nid, key)     the user opened it: it leaves the list, and the shell
                    focuses its process and calls the process's
                    `moy_action(id, key)` (`acted`, the shell's hook)
  dismiss()         the banner goes; the notification stays in the list
  clear(nid)        it leaves the list unopened

A process posts only while it holds the `notifications` permission, which
the shell's `who(pid)` answers at the post from the grant its run or its
service was bound with; the shell itself (pid 1) always may. Without it the
post answers None and nothing shows.

`gen` moves with every change, the task manager's cue to read the list again.
"""

try:
    from ticks import _ticks_ms, _ticks_diff, _ticks_add
except ImportError:  # host: the runtime package
    from runtime.ticks import _ticks_ms, _ticks_diff, _ticks_add

LOW, NORMAL, CRITICAL = 0, 1, 2
BANNER_MS = 5000                # a NORMAL banner's time up
CRITICAL_MS = 10000             # ...a CRITICAL one's
KEEP = 16                       # the list's length, at most
SUMMARY_MAX = 47                # the kernel copy's line (moy_chrome.c's 48 bytes)
BODY_MAX = 200
ICON = "bubble"                 # the icon a notification naming none shows
DEFAULT = "default"
SHELL = 1


class Note:
    """One notification as posted, and who posted it."""

    def __init__(self, nid, pid, app, cart):
        self.id = nid
        self.pid = pid
        self.app = app              # the poster's title, as it posted
        self.cart = cart            # the poster's cart dict, or None (the shell)
        self.summary = ""
        self.body = ""
        self.icon = ICON
        self.actions = ()           # ((key, label), ...)
        self.urgency = NORMAL
        self.at = 0                 # ticks_ms it was posted

    def labels(self):
        """The actions a user picks from by name: every one but `default`."""
        return [a for a in self.actions if a[0] != DEFAULT]


def _text(v, cap):
    s = "" if v is None else str(v)
    s = s.replace("\n", " ")
    return s[:cap]


def _pairs(actions):
    """The freedesktop flat list (key, label, key, label, ...) as pairs; a
    sequence of pairs is taken as it is."""
    if not actions:
        return ()
    out = []
    flat = list(actions)
    if all(isinstance(a, (tuple, list)) and len(a) == 2 for a in flat):
        for k, label in flat:
            out.append((str(k), str(label)))
        return tuple(out)
    if len(flat) % 2:
        raise ValueError("actions: a key and a label each")
    for i in range(0, len(flat), 2):
        out.append((str(flat[i]), str(flat[i + 1])))
    return tuple(out)


class Notifications:
    def __init__(self):
        self.list = []              # newest first
        self.banner = None          # the Note up, or None
        self.until = 0              # ticks_ms the banner goes at
        self.gen = 0
        self.next_id = 1
        self.who = None             # fn(pid) -> (title, cart, may_post), the shell's
        self.shown = None           # fn(note|None, until) the banner went up or down
        self.acted = None           # fn(note, key) a notification was acted on

    def find(self, nid):
        for n in self.list:
            if n.id == nid:
                return n
        return None

    def post(self, pid, summary, body="", icon=None, actions=(), urgency=NORMAL,
             replaces_id=0):
        """Process `pid` posts: its id, or None where it may not."""
        title, cart, may = self._who(pid)
        if not may:
            return None
        acts = _pairs(actions)
        try:
            urgency = int(urgency)
        except (TypeError, ValueError):
            urgency = NORMAL
        urgency = LOW if urgency < LOW else CRITICAL if urgency > CRITICAL else urgency
        n = self.find(replaces_id) if replaces_id else None
        if n is not None and n.pid != pid:
            n = None
        if n is None:
            n = Note(self.next_id, pid, title, cart)
            self.next_id += 1
            self.list.insert(0, n)
            if len(self.list) > KEEP:
                old = self.list.pop()
                if old is self.banner:
                    self._down()
        n.summary = _text(summary, SUMMARY_MAX)
        n.body = _text(body, BODY_MAX)
        n.icon = str(icon) if icon else ICON
        n.actions = acts
        n.urgency = urgency
        n.at = _ticks_ms()
        if urgency != LOW:
            self.banner = n
            self.until = _ticks_add(n.at, CRITICAL_MS if urgency == CRITICAL else BANNER_MS)
            if self.shown is not None:
                self.shown(n, self.until)
        elif n is self.banner:
            self._down()
        self.gen += 1
        return n.id

    def _who(self, pid):
        if self.who is not None:
            return self.who(pid)
        return ("Moybyte", None, pid == SHELL)

    def up(self, now=None):
        """The notification the banner shows at `now`, or None (a banner
        past its deadline goes down here). Allocates nothing."""
        n = self.banner
        if n is None:
            return None
        if _ticks_diff(self.until, _ticks_ms() if now is None else now) <= 0:
            self.banner = None
            self.until = 0
            self.gen += 1
            return None
        return n

    def _down(self):
        if self.banner is None:
            return
        self.banner = None
        self.until = 0
        if self.shown is not None:
            self.shown(None, 0)

    def dismiss(self):
        """The banner goes without being acted on; its notification stays in
        the list. False where none was up."""
        if self.banner is None:
            return False
        self._down()
        self.gen += 1
        return True

    def clear(self, nid):
        """Notification `nid` leaves the list unopened."""
        n = self.find(nid)
        if n is None:
            return False
        if n is self.banner:
            self._down()
        self.list.remove(n)
        self.gen += 1
        return True

    def clear_all(self):
        self._down()
        if self.list:
            self.list = []
            self.gen += 1

    def act(self, nid, key=None):
        """The user opened notification `nid` (by `key`, else its `default`
        or first action): it leaves the list and the shell's `acted` takes
        it to its process. False where there is no such notification."""
        n = self.find(nid)
        if n is None:
            return False
        if key is None:
            key = DEFAULT
            for a in n.actions:
                if a[0] == DEFAULT:
                    break
            else:
                if n.actions:
                    key = n.actions[0][0]
        self.clear(nid)
        if self.acted is not None:
            self.acted(n, key)
        return True

    def reset(self):
        self._down()
        self.list = []
        self.gen += 1


NOTES = Notifications()
