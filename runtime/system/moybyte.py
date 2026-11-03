"""moybyte: what a Python app asks the OS for that has no standard name
(docs/os_2026-11/processes.md sections 1 and 3). The clock is the standard
one, `time.ticks_us()`.

  events(into)       the input records waiting for this process: into
                     `into`, an array('i') of EV_FIELDS ints a record (EV_T,
                     EV_SRC, EV_KIND, EV_PROV, EV_A, EV_ID, EV_B; the kinds and
                     fields are native/moy_input/moy_event.h's), oldest first,
                     a LOST record where some were dropped; answers how many.
                     EV_T is on time.ticks_us's clock. Allocates nothing.
  on(source, fn)     wake `fn()` when `source` has records: "keys", "touch",
                     "pad" or "input" (all three); inside `fn`, events() reads
                     that source's records
  every(ms, fn)      wake `fn()` every `ms`, no earlier
  after(ms, fn)      wake `fn()` once, no earlier than `ms` from now
  cancel(wid)        a wake undone
  notify(summary, body="", icon=None, actions=(), urgency=1, replaces_id=0)
                     post a notification (processes.md section 7) under the
                     `notifications` permission: its id, or None where the
                     process does not hold it (runtime/system/notifications.py)

on/every/after answer the wake's id. A timer means "no earlier than": a wake is
served in the shell loop's slack, so up to a frame late, and a late `every`
keeps its period from the deadline, never bursting to catch up. Precise timing
is C's (hardware.md section 2).

THE REGISTRY (`WAKES`) is what the cooperative scheduler drives: once a frame
it asks `due(now_ms, out)` for the wakes due (allocation-free, into a
preallocated list) and runs each with `call(wid)`, which points events() at the
wake's reader for the call; `count(pid)` answers how many wakes a process
holds; `end(pid)` drops a process's wakes and readers and tells `ended`, the
scheduler's hook. Each wake belongs to the process that made it (the OS log's
`current`). This module registers and queues; running a callback under a
slice is the scheduler's (runtime/system/scheduler.py).

The rings are the input table the shell binds (`bind`, the Workstation's
input), so a host and a board read the same records.
"""

try:
    from ticks import _ticks_ms, _ticks_diff, _ticks_add
except ImportError:  # host: the runtime package
    from runtime.ticks import _ticks_ms, _ticks_diff, _ticks_add

try:
    import moy_input as _I
except ImportError:  # host: the runtime package
    from runtime import moy_input as _I

try:
    import moy_log as _L
except ImportError:  # pragma: no cover - an image without the kernel
    _L = None

EV_FIELDS = _I.EV_FIELDS
EV_T, EV_SRC, EV_KIND, EV_PROV, EV_A, EV_ID, EV_B = (
    _I.EV_T, _I.EV_SRC, _I.EV_KIND, _I.EV_PROV, _I.EV_A, _I.EV_ID, _I.EV_B)
KEY_DOWN, KEY_UP, TEXT, BTN = _I.EV_KEY_DOWN, _I.EV_KEY_UP, _I.EV_TEXT, _I.EV_BTN
TOUCH_DOWN, TOUCH_MOVE, TOUCH_UP = _I.EV_TOUCH_DOWN, _I.EV_TOUCH_MOVE, _I.EV_TOUCH_UP
EDGE, LOST = _I.EV_EDGE, _I.EV_LOST
PROV_ISR, PROV_FIFO, PROV_POLL = _I.PROV_ISR, _I.PROV_FIFO, _I.PROV_POLL
EVENT_VERSION = _I.EV_VERSION

SOURCES = {"keys": _I.EV_KEYS, "touch": _I.EV_TOUCH, "pad": _I.EV_PAD,
           "input": _I.EV_KEYS | _I.EV_TOUCH | _I.EV_PAD}

SHELL = 1
_ON, _EVERY, _AFTER = 0, 1, 2
# A wake: [wid, owner, how, ms, deadline, fn, reader]
_WID, _OWNER, _HOW, _MS, _AT, _FN, _READER = range(7)


def _pid():
    return _L.current() if _L is not None else SHELL


class Wakes:
    """What every process asked to be woken by, and each process's events()
    reader."""

    def __init__(self):
        self.table = None
        self.list = []              # the wakes, in the order they were made
        self.by_id = {}
        self.readers = {}           # pid -> its events() reader
        self.cur = [-1]             # the reader events() reads; -1: the current process's
        self.next_id = 1
        self.ended = None           # fn(pid) told when a process's wakes end
        self.added = None           # fn() told when a wake is made (a board's
                                    # loop then makes the scheduler's upcall)

    def bind(self, table):
        """The input table whose rings the readers read. A new table starts
        every reader again on it."""
        if table is self.table:
            return
        self.table = table
        self.readers.clear()
        for w in self.list:
            if w[_HOW] == _ON:
                w[_READER] = table.reader(SOURCES[w[_MS]])

    def _add(self, how, ms, at, fn, reader):
        wid = self.next_id
        self.next_id += 1
        w = [wid, _pid(), how, ms, at, fn, reader]
        self.list.append(w)
        self.by_id[wid] = w
        if self.added is not None:
            self.added()
        return wid

    def on(self, source, fn):
        kinds = SOURCES.get(source)
        if kinds is None:
            raise ValueError("no wake source %r" % (source,))
        if self.table is None:
            raise OSError("no input table bound")
        return self._add(_ON, source, 0, fn, self.table.reader(kinds))

    def every(self, ms, fn):
        ms = max(1, int(ms))
        return self._add(_EVERY, ms, _ticks_add(_ticks_ms(), ms), fn, -1)

    def after(self, ms, fn):
        ms = max(0, int(ms))
        return self._add(_AFTER, ms, _ticks_add(_ticks_ms(), ms), fn, -1)

    def cancel(self, wid):
        w = self.by_id.pop(wid, None)
        if w is None:
            return False
        self.list.remove(w)
        if w[_READER] >= 0 and self.table is not None:
            self.table.reader_free(w[_READER])
        return True

    def end(self, pid):
        """Process `pid` ended: its wakes and its reader go."""
        if pid is None:
            return
        for w in [w for w in self.list if w[_OWNER] == pid]:
            self.cancel(w[_WID])
        r = self.readers.pop(pid, None)
        if r is not None and self.table is not None:
            self.table.reader_free(r)
        if self.ended is not None:
            self.ended(pid)

    def count(self, pid):
        """How many wakes process `pid` holds. Allocates nothing."""
        n = 0
        for w in self.list:
            if w[_OWNER] == pid:
                n += 1
        return n

    def due(self, now, out):
        """The wakes due at `now` (ticks_ms) into `out`, a list of at least
        one slot a wake; answers how many. A timer due is re-armed (`every`)
        or spent (`after`, cancelled by its `call`). Allocates nothing."""
        n = 0
        cap = len(out)
        t = self.table
        for w in self.list:
            if n == cap:
                break
            how = w[_HOW]
            if how == _ON:
                if t is None or not t.pending(w[_READER]):
                    continue
            else:
                at = w[_AT]
                if at is None or _ticks_diff(now, at) < 0:
                    continue
                if how == _EVERY:
                    at = _ticks_add(at, w[_MS])
                    if _ticks_diff(now, at) >= 0:
                        at = _ticks_add(now, w[_MS])
                    w[_AT] = at
                else:
                    w[_AT] = None
            out[n] = w[_WID]
            n += 1
        return n

    def call(self, wid):
        """Run wake `wid`'s function, events() reading its reader (an `on`)
        or its process's. Its exception is the caller's. An `after` is spent
        by the call."""
        w = self.by_id.get(wid)
        if w is None:
            return
        cur = self.cur
        was = cur[0]
        cur[0] = w[_READER] if w[_READER] >= 0 else self._reader_of(w[_OWNER])
        try:
            w[_FN]()
        finally:
            cur[0] = was
            if w[_HOW] == _AFTER:
                self.cancel(wid)

    def _reader_of(self, pid):
        r = self.readers.get(pid)
        if r is None:
            if self.table is None:
                return -1
            r = self.readers[pid] = self.table.reader(SOURCES["input"])
        return r

    def events(self, into):
        t = self.table
        if t is None:
            return 0
        r = self.cur[0]
        if r < 0:
            r = self._reader_of(_pid())
        return t.events(into, r)


WAKES = Wakes()


def bind(table):
    WAKES.bind(table)


def events(into):
    return WAKES.events(into)


def on(source, fn):
    return WAKES.on(source, fn)


def every(ms, fn):
    return WAKES.every(ms, fn)


def after(ms, fn):
    return WAKES.after(ms, fn)


def cancel(wid):
    return WAKES.cancel(wid)


def end(pid):
    WAKES.end(pid)


def notify(summary, body="", icon=None, actions=(), urgency=1, replaces_id=0):
    try:
        from notifications import NOTES
    except ImportError:  # host: the runtime package
        from runtime.notifications import NOTES
    return NOTES.post(_pid(), summary, body, icon, actions, urgency, replaces_id)
