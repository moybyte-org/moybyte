"""moybyte: what a Python app asks the OS for that has no standard name
(docs/os_2026-11/processes.md sections 1 and 3). The clock is the standard
one, `time.ticks_us()`.

  events(into)       the input records waiting for this process: into
                     `into`, an array('i') of EV_FIELDS ints a record (EV_T,
                     EV_SRC, EV_KIND, EV_PROV, EV_A, EV_ID, EV_B; the kinds and
                     fields are native/moy_input/moy_event.h's), oldest first,
                     a LOST record where some were dropped; answers how many.
                     EV_T is on time.ticks_us's clock. Allocates nothing.
  source(src)        where a record came from: the name of the source whose
                     records carry EV_SRC `src` ("kbd", "touch", "ble",
                     "gpio5"), None once that source has ended; its EV_PROV
                     says how good the stamp is (PROV_ISR, PROV_FIFO,
                     PROV_POLL)
  on(source, fn)     wake `fn()` when `source` has records: "keys", "touch",
                     "pad" or "input" (all three); inside `fn`, events() reads
                     that source's records. Any other name is a MESSAGE: wake
                     `fn(sender, *args)` for each message of that name on
                     this process's event queue, `sender` the app id the OS
                     stamped on it ("system" for the OS's own: "net" and
                     "ble", the radios, below)
  send(app, name, *args)
                     message `name` with `args` (JSON values) to every
                     process of app `app` that listens on it, the caller
                     apart: always within the caller's own app, from another
                     app only where the receiver's manifest lists `name`
                     under `receives`, always from the system. How many took
                     it (native/moy_kernel/moy_evq.h, THE RULE)
  award(id)          unlock badge `id` of this app's manifest's
                     `achievements`: `send("moybyte.achievements", "award",
                     id)` (docs/app_api_v1.md section 9)
  progress(id, value)
                     badge `id`'s goal stands at `value`, the game's own
                     count: the badge is found once it reaches the goal.
                     `send("moybyte.achievements", "progress", id, value)`
  every(ms, fn)      wake `fn()` every `ms`, no earlier
  after(ms, fn)      wake `fn()` once, no earlier than `ms` from now
  at(t, fn)          an alarm: wake `fn()` once, no earlier than `t`, Unix
                     seconds on the wall clock (runtime/system/wallclock.py's
                     `now`, from 1970 on every tier), and light the screen
                     from the idle ladder's sleep. Under the `alarms`
                     permission (OSError(EPERM) without it; the shell is not
                     gated). An alarm set while the clock is not synced is on
                     the clock as it reads then
  cancel(wid)        a wake undone
  lease(name, **cfg) a resource the board leases, held by this process until
                     it ends: "gpio<n>" a machine.Pin, "uart<n>" a
                     machine.UART, "spi<n>" a machine.SPI, each
                     MicroPython's own API; refused (OSError) where the board
                     offers no such row, another process holds it, or this
                     one lacks `lease:<row>` (runtime/system/leases.py)
  pins()             what the board leases and who holds what
  http, mic, lora, ble, usb, midi, camera, media
                     the device APIs (runtime/system/devices.py): each the
                     API's face where this console has its class, else None;
                     a verb without the API's permission raises
                     OSError(EPERM)
  snd_open(frames=0) the render-ahead audio ring (hardware.md section 2), this
                     process's until snd_close or its end: `frames` deep at
                     22,050 Hz mono (0: SND_DEPTH, 2,048; SND_MIN..SND_MAX),
                     deeper outlasting a longer stall (a collection), shallower
                     reaching the speaker sooner. True when it plays; False
                     where nothing would play it or the depth is out of range;
                     OSError(ENOSPC) where the board's RINGS row cannot hold
                     it, OSError(EBUSY) while another process or a compiled
                     cart holds the ring
  snd(buf=None)      queue `buf` (signed 16-bit mono, little-endian: a
                     bytearray or array('h')): how many frames it took, the
                     rest being the caller's to offer again; with no buffer,
                     the room. From the loop or a `_thread`; allocates nothing
  snd_close()        the ring given back; snd_counts() its (queued, played,
                     starved, room, open, depth) counters, None before any open
  notify(summary, body="", icon=None, actions=(), urgency=1, replaces_id=0)
                     post a notification (processes.md section 7) under the
                     `notifications` permission: its id, or None where the
                     process does not hold it (runtime/system/notifications.py)
  status(icon, section, order=0)
                     a status item in the bar for this process, its section
                     drawn in the item's menu (the desk) or the control
                     center (a handheld): an item with show(state),
                     changed() and remove(), None where the bar is full
                     (runtime/system/status_items.py). The process's end
                     takes it out
  open_with(item, choose=False)
                     open `item` (a URL, a user file by its path,
                     "Documents/todo.md", or a grant this process was
                     handed) in the app that handles its MIME type: the one
                     match, else the last one chosen, else the "open with"
                     menu, which `choose` asks always; False where no app
                     handles it (runtime/system/intents.py). From the
                     background without the `front` permission it, `share`
                     and `pick` become a notification that does it when
                     opened (runtime/system/front.py)
  share(data, mime)  hand `data` (str or bytes) of type `mime` to the app
                     that handles it, routed as open_with routes
  pick(mime, mode="r")
                     the console's file picker over the user's files of
                     type `mime`: the one picked reaches this process's
                     `moy_open(h)` as a grant of `mode` ("r" or "rw") for
                     its run, None where the picker was cancelled; False
                     where no file has the type
  log(level, text)   a record of the OS log (native/moy_kernel/moy_log.h) as
                     this process, at syslog severity `level` (0 emerg to 7
                     debug; ERR, WARNING and INFO name three)
  clock_synced()     whether the wall clock holds real time: NTP has answered
                     on a board since power-on (runtime/system/wallclock.py);
                     until then `time.time()` counts from power-on

on/every/after/at answer the wake's id. A timer means "no earlier than": a wake is
served in the shell loop's slack, so up to a frame late, and a late `every`
keeps its period from the deadline, never bursting to catch up. Precise timing
is C's (hardware.md section 2).

THE REGISTRY (`WAKES`) is what the cooperative scheduler drives: once a frame
it asks `due(now_ms, out)` for the wakes due (allocation-free, into a
preallocated list) and runs each with `call(wid)`, which points events() at the
wake's reader for the call; `count(pid)` answers how many wakes a process
holds; `end(pid)` drops a process's wakes and readers and tells `ended`, the
scheduler's hook; `alarm(t, fn, wake)` is `at` with the screen's waking a
choice (cron's and at's jobs, runtime/system/cron.py, wake nothing), and
`woke(pid)` the console's hook an alarm that wakes calls. Each wake belongs
to the process that made it (the OS log's `current`). This module registers and queues; running a callback under a
slice is the scheduler's (runtime/system/scheduler.py).

The rings are the input table the shell binds (`bind`, the Workstation's
input), so a host and a board read the same records.

THE RADIOS (docs/os_2026-11/between_apps.md section 4). WiFi having an
address and the BLE host running, each coming up or going down, are NET and
BLE records on the input table's ring source "radio" (moy_input.h, the
radios), which the drivers post in C on every change and only on one.
`on("net", fn)` and `on("ble", fn)` are message wakes: `fn("system", up,
why)` for each change, `up` a bool and `why` what took it down (the
station's disconnect reason, NimBLE's reset reason; 0 when it was put down or
came up). One ROUTER reads that ring for everybody, a wake of the shell's
that lives while any process waits on "net" or "ble" or anything else asks
for it (`radio_keep`: the scheduler's, while a service on a task of its own
listens on one), and routes each change
from the system to every process that listens on it, whatever its tier
(`route`, moy_evq_route), so one reader serves every process.

MESSAGES are not ring records: they are the kernel's per-process event
queues (native/moy_kernel/moy_evq.h, the module `moy_evq`), every tier's
alike. A message wake waits in `mail`, apart from `list`; its process
LISTENS on its name. The queue is opened at a process's first `on` or
`send` (`opener`, the scheduler's: its app id and its manifest's
`receives`) and closed at its end. `post(pid, name, args)` is the OS's own
message to one process (from the system), `route` an OS event to every
listener. While `queued` is 0 a message costs a frame nothing, so the
scheduler's tick and a board's upcall look at `list` and `queued` alone; a
send from Python sets it, and the scheduler's `woken` sets it when a
message reached the VM's processes from another task (the kernel's wake,
moy_evq.h). A send does not ask a board
for the service upcall: the console serves what is queued at the start of
its next frame (scheduler.Scheduler.mail), and a tick that runs first serves
it too, so a run the kernel drives makes no crossing for a message its
launch sent.
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

try:
    import moy_evq as _E
except ImportError:  # host: the runtime package
    from runtime import moy_evq as _E

try:
    import json as _json
except ImportError:  # pragma: no cover - every VM has it
    _json = None

EV_FIELDS = _I.EV_FIELDS
EV_T, EV_SRC, EV_KIND, EV_PROV, EV_A, EV_ID, EV_B = (
    _I.EV_T, _I.EV_SRC, _I.EV_KIND, _I.EV_PROV, _I.EV_A, _I.EV_ID, _I.EV_B)
KEY_DOWN, KEY_UP, TEXT, BTN = _I.EV_KEY_DOWN, _I.EV_KEY_UP, _I.EV_TEXT, _I.EV_BTN
TOUCH_DOWN, TOUCH_MOVE, TOUCH_UP = _I.EV_TOUCH_DOWN, _I.EV_TOUCH_MOVE, _I.EV_TOUCH_UP
EDGE, LOST = _I.EV_EDGE, _I.EV_LOST
NET, BLE, LORA = _I.EV_NET, _I.EV_BLE, _I.EV_LORA
USB, MIDI = _I.EV_USB, _I.EV_MIDI
PROV_ISR, PROV_FIFO, PROV_POLL = _I.PROV_ISR, _I.PROV_FIFO, _I.PROV_POLL
EVENT_VERSION = _I.EV_VERSION

SOURCES = {"keys": _I.EV_KEYS, "touch": _I.EV_TOUCH, "pad": _I.EV_PAD,
           "input": _I.EV_KEYS | _I.EV_TOUCH | _I.EV_PAD}

SYSTEM = "system"           # the sender of the OS's own messages

# The radios' messages, by the kind of their records.
RADIO = {NET: "net", BLE: "ble"}
_RADIO_KINDS = (1 << NET) | (1 << BLE)

SHELL = 1
ALARMS = "alarms"           # the permission `at` needs
EPERM = 1
ERR, WARNING, INFO = 3, 4, 6    # the OS log's severities `log` names most (syslog's)
_ON, _EVERY, _AFTER, _MAIL, _RING, _ALARM = 0, 1, 2, 3, 4, 5
# A wake: [wid, owner, how, ms, deadline, fn, reader]; a message wake's
# `ms` is its name (its messages wait on its process's event queue), a ring
# wake's `ms` the ring source it reads alone and its `deadline` the kinds,
# an alarm's `ms` whether it wakes the screen and its `deadline` its Unix
# seconds.
_WID, _OWNER, _HOW, _MS, _AT, _FN, _READER = range(7)


def _pid():
    return _L.current() if _L is not None else SHELL


class Wakes:
    """What every process asked to be woken by, and each process's events()
    reader."""

    def __init__(self):
        self.table = None
        self.list = []              # the wakes, in the order they were made
        self.mail = []              # the message wakes, likewise
        self.queued = 0             # 1: a message may wait on a VM process's queue
        self.by_id = {}
        self.readers = {}           # pid -> its events() reader
        self.cur = [-1]             # the reader events() reads; -1: the current process's
        self.next_id = 1
        self.ended = None           # fn(pid) told when a process's wakes end
        self.added = None           # fn() told when a wake is made (a board's
                                    # loop then makes the scheduler's upcall)
        self.holds = None           # fn(pid) -> what else keeps a process running:
                                    # its asyncio tasks (runtime/system/aio.py)
        self.woke = None            # fn(pid) told when an alarm of process pid's that
                                    # wakes goes off: the console lights the screen
        self.radio = None           # the radios' router, a wake of the shell's
        self.radio_keep = False     # something beside a wake wants the router
        self.opener = None          # fn(pid): process pid's event queue opened
                                    # (the scheduler's: its app and `receives`)
        self._rbuf = None

    def bind(self, table):
        """The input table whose rings the readers read. A new table starts
        every reader again on it."""
        if table is self.table:
            return
        self._radio_drop()
        self.table = table
        self.readers.clear()
        for w in self.list:
            if w[_HOW] == _ON:
                w[_READER] = table.reader(SOURCES[w[_MS]])
            elif w[_HOW] == _RING:
                try:
                    w[_READER] = table.reader(w[_AT], w[_MS])
                except (OSError, ValueError):  # the source is not this table's
                    w[_READER] = -1
        self.radio_sync()

    def _add(self, how, ms, at, fn, reader):
        wid = self.next_id
        self.next_id += 1
        w = [wid, _pid(), how, ms, at, fn, reader]
        self.by_id[wid] = w
        if how == _MAIL:
            self.mail.append(w)
            return wid
        self.list.append(w)
        if self.added is not None:
            self.added()
        return wid

    def queue(self, pid):
        """Process `pid`'s event queue, opened where it has none."""
        if not _E.has(pid):
            if self.opener is not None:
                self.opener(pid)
            else:
                _E.open(pid, SYSTEM if pid <= SHELL else "pid%d" % pid, "", True)

    def on(self, source, fn):
        kinds = SOURCES.get(source)
        if kinds is None:
            if not isinstance(source, str) or not source:
                raise ValueError("no wake source %r" % (source,))
            pid = _pid()
            self.queue(pid)
            if _E.listen(pid, source, True) != _E.OK:
                raise OSError("process %d listens on too many names" % pid)
            wid = self._add(_MAIL, source, None, fn, -1)
            if source == "net" or source == "ble":
                self.radio_sync()
            return wid
        if self.table is None:
            raise OSError("no input table bound")
        return self._add(_ON, source, 0, fn, self.table.reader(kinds))

    def on_ring(self, src, fn, kinds=0):
        """Wake `fn()` when ring source `src` (a lease's edges, a device
        API's records) has records of `kinds` (a mask of 1 << kind; EDGE's
        by default); inside `fn`, events() reads that source alone."""
        if self.table is None:
            raise OSError("no input table bound")
        kinds = kinds or 1 << EDGE
        return self._add(_RING, src, kinds, fn, self.table.reader(kinds, src))

    def every(self, ms, fn):
        ms = max(1, int(ms))
        return self._add(_EVERY, ms, _ticks_add(_ticks_ms(), ms), fn, -1)

    def after(self, ms, fn):
        ms = max(0, int(ms))
        return self._add(_AFTER, ms, _ticks_add(_ticks_ms(), ms), fn, -1)

    def alarm(self, t, fn, wake=True):
        """Wake `fn()` once at wall time `t` (Unix seconds, rounded up),
        `woke` told as it goes off when `wake`."""
        t = int(-(-t // 1))
        return self._add(_ALARM, bool(wake), t, fn, -1)

    def post(self, pid, name, args, frm=SHELL):
        """Message `name` with `args` (a tuple of JSON values) from process
        `frm` (the system by default) to process `pid`, under the rule: True
        where its queue took it. It makes no upcall wanted (`added` is not
        told)."""
        if frm > SHELL:
            self.queue(frm)
        ok = _E.post(pid, frm, name, _json.dumps(list(args))) == _E.OK
        if ok:
            self.queued = 1
        return ok

    def send(self, frm, app, name, args):
        """`moybyte.send` from process `frm`: message `name` to every
        process of app `app` that listens on it, under the rule. How many
        took it."""
        if frm > SHELL:
            self.queue(frm)
        took = _E.send(frm, app, name, _json.dumps(list(args)))[0]
        if took > 0:
            self.queued = 1
        return max(0, took)

    def route(self, name, args):
        """An OS event: message `name` with `args` from the system to every
        process that listens on it, whatever its tier. How many took it."""
        took = _E.route(name, _json.dumps(list(args)))
        if took:
            self.queued = 1
        return took

    def handlers(self, pid, name, out):
        """The functions of process `pid`'s wakes on message `name`, into
        list `out` (cleared first)."""
        del out[:]
        for w in self.mail:
            if w[_OWNER] == pid and w[_MS] == name:
                out.append(w[_FN])
        return out

    def radio_sync(self):
        """The router made while a process waits on "net" or "ble" or
        `radio_keep` is set, and dropped once neither holds."""
        want = self.radio_keep
        if not want:
            for w in self.mail:
                if w[_MS] == "net" or w[_MS] == "ble":
                    want = True
                    break
        if not want:
            self._radio_drop()
            return
        t = self.table
        if self.radio is not None or t is None:
            return
        try:
            src = t.radio_source()
            r = t.reader(_RADIO_KINDS, src)
        except (AttributeError, OSError, ValueError):  # a table with no radios, or full
            return
        if self._rbuf is None:
            from array import array
            self._rbuf = array("i", [0] * (EV_FIELDS * 4))
        wid = self.next_id
        self.next_id += 1
        w = [wid, SHELL, _RING, src, _RADIO_KINDS, self._route, r]
        self.by_id[wid] = w
        self.list.append(w)
        self.radio = w
        if self.added is not None:
            self.added()

    def _radio_drop(self):
        w, self.radio = self.radio, None
        if w is not None:
            self.cancel(w[_WID])

    def _route(self):
        w, t, buf = self.radio, self.table, self._rbuf
        if w is None or t is None:
            return
        r = w[_READER]
        cap = len(buf) // EV_FIELDS
        n = t.events(buf, r)
        while n:
            for i in range(n):
                k = i * EV_FIELDS
                name = RADIO.get(buf[k + EV_KIND])
                if name is not None:
                    self.route(name, (buf[k + EV_A] != 0, buf[k + EV_B]))
            n = t.events(buf, r) if n == cap else 0

    def mail_of(self, pid):
        """How many messages wait on process `pid`'s event queue."""
        return _E.queued(pid)

    def cancel(self, wid):
        w = self.by_id.pop(wid, None)
        if w is None:
            return False
        if w[_HOW] == _MAIL:
            self.mail.remove(w)
            _E.listen(w[_OWNER], w[_MS], False)
            if w[_MS] == "net" or w[_MS] == "ble":
                self.radio_sync()
            return True
        self.list.remove(w)
        if w is self.radio:
            self.radio = None
        if w[_READER] >= 0 and self.table is not None:
            self.table.reader_free(w[_READER])
        return True

    def end(self, pid):
        """Process `pid` ended: what ENDS sweeps (its leases) first, then its
        wakes and its reader go."""
        if pid is None:
            return
        for fn in ENDS:
            fn(pid)
        for w in [w for w in self.list if w[_OWNER] == pid] + \
                [w for w in self.mail if w[_OWNER] == pid]:
            self.cancel(w[_WID])
        r = self.readers.pop(pid, None)
        if r is not None and self.table is not None:
            self.table.reader_free(r)
        if pid > SHELL:
            _E.close(pid)
            _E.drop(pid)
            _E.forget(pid)
            if self.queued and not _E.ready():
                self.queued = 0         # what was queued was the ended process's
        if self.ended is not None:
            self.ended(pid)

    def count(self, pid):
        """How many wakes process `pid` holds, its asyncio tasks counted
        (`holds`). Allocates nothing."""
        n = self.holds(pid) if self.holds is not None else 0
        for w in self.list:
            if w[_OWNER] == pid:
                n += 1
        for w in self.mail:
            if w[_OWNER] == pid:
                n += 1
        return n

    def due(self, now, out):
        """The wakes due at `now` (ticks_ms) into `out`, a list of at least
        one slot a wake; answers how many. A timer due is re-armed (`every`)
        or spent (`after`, an alarm: cancelled by its `call`). Allocates
        nothing."""
        n = 0
        cap = len(out)
        t = self.table
        wall = None
        for w in self.list:
            if n == cap:
                break
            how = w[_HOW]
            if how == _ON or how == _RING:
                if t is None or w[_READER] < 0 or not t.pending(w[_READER]):
                    continue
            elif how == _ALARM:
                at = w[_AT]
                if at is None:
                    continue
                if wall is None:
                    wall = _wall()
                if wall < at:
                    continue
                w[_AT] = None
                if w[_MS] and self.woke is not None:
                    self.woke(w[_OWNER])
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
        by the call, and so is an alarm."""
        w = self.by_id.get(wid)
        if w is None or w[_HOW] == _MAIL:
            return
        cur = self.cur
        was = cur[0]
        cur[0] = w[_READER] if w[_READER] >= 0 else self._reader_of(w[_OWNER])
        try:
            w[_FN]()
        finally:
            cur[0] = was
            if w[_HOW] == _AFTER or w[_HOW] == _ALARM:
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


_WC = [None]


def _wall():
    """The wall clock's Unix seconds (wallclock.now)."""
    wc = _WC[0]
    if wc is None:
        try:
            import wallclock as wc
        except ImportError:  # host: the runtime package
            from runtime import wallclock as wc
        _WC[0] = wc
    return wc.now()


# fn(pid) told when a process ends, before its wakes go: the lease sweep
# (runtime/system/leases.py) registers here.
ENDS = []

WAKES = Wakes()


def bind(table):
    WAKES.bind(table)


def events(into):
    return WAKES.events(into)


def source(src):
    t = WAKES.table
    return None if t is None else t.src_name(src)


def on(source, fn):
    return WAKES.on(source, fn)


def every(ms, fn):
    return WAKES.every(ms, fn)


def after(ms, fn):
    return WAKES.after(ms, fn)


def _may_alarm(pid):
    try:
        import devices
    except ImportError:  # host: the runtime package
        from runtime import devices
    if not devices.may(pid, ALARMS):
        raise OSError(EPERM, 'needs "%s"' % ALARMS)


def at(t, fn):
    _may_alarm(_pid())
    return WAKES.alarm(t, fn, True)


AT = "moybyte.at"           # the message an alarm of at_message posts


def at_message(t):
    """`at` for a process whose callbacks are not the VM's (a Lua service,
    native/moycore/moycore_svc.c's ALARMS): the alarm posts it message AT
    from the system, its one argument the alarm's id, which it answers."""
    pid = _pid()
    _may_alarm(pid)
    w = [0]
    w[0] = WAKES.alarm(t, lambda: WAKES.post(pid, AT, (w[0],)), True)
    return w[0]


def cancel(wid):
    return WAKES.cancel(wid)


def end(pid):
    WAKES.end(pid)


def lease(name, **cfg):
    return _leases().lease(name, **cfg)


def pins():
    return _leases().pins()


def _leases():
    try:
        import leases
    except ImportError:  # host: the runtime package
        from runtime import leases
    return leases


# The render-ahead ring's holder: its pid, 0 for none.
SND = [0]
SND_DEPTH, SND_MIN, SND_MAX = 2048, 64, 65536
ENOSPC, EBUSY = 28, 16


_AUD = [None]


def _aud():
    if _AUD[0] is None:
        try:
            import moy_audio
            _AUD[0] = moy_audio
        except ImportError:  # host: the runtime package's ctypes build of the same C
            try:
                from runtime import audio_binding
            except ImportError:  # pragma: no cover - a host without its audio
                return None
            _AUD[0] = audio_binding.get()
    return _AUD[0]


def snd_open(frames=0):
    a = _aud()
    if a is None:
        return False
    p = _pid()
    if SND[0] not in (0, p):
        raise OSError(EBUSY, "the audio ring is another process's")
    c = a.snd_counts()
    if SND[0] == 0 and c is not None and c[4]:
        raise OSError(EBUSY, "a compiled cart holds the audio ring")
    rc = a.snd_open(int(frames))
    if rc < 0:
        SND[0] = 0
        raise OSError(ENOSPC, "the audio ring does not fit")
    SND[0] = p if rc else 0
    return rc > 0


def snd(buf=None):
    if not SND[0]:
        return 0
    return _AUD[0].snd(buf)


def snd_close():
    if SND[0]:
        SND[0] = 0
        _AUD[0].snd_close()


def snd_counts():
    a = _aud()
    return None if a is None else a.snd_counts()


def _snd_end(pid):
    if SND[0] == pid:
        snd_close()


ENDS.append(_snd_end)


def notify(summary, body="", icon=None, actions=(), urgency=1, replaces_id=0):
    try:
        from notifications import NOTES
    except ImportError:  # host: the runtime package
        from runtime.notifications import NOTES
    return NOTES.post(_pid(), summary, body, icon, actions, urgency, replaces_id)


def _items():
    try:
        from status_items import ITEMS
    except ImportError:  # host: the runtime package
        from runtime.status_items import ITEMS
    return ITEMS


def status(icon, section, order=0):
    return _items().add(_pid(), icon, section, order)


def _status_end(pid):
    _items().sweep(pid)


ENDS.append(_status_end)


def _intents():
    try:
        from intents import INTENTS
    except ImportError:  # host: the runtime package
        from runtime.intents import INTENTS
    return INTENTS


def open_with(item, choose=False):
    return _intents().open_with(_pid(), item, bool(choose))


def send(app, name, *args):
    return WAKES.send(_pid(), app, name, args)


ACHIEVEMENTS = "moybyte.achievements"


def award(ach_id):
    return send(ACHIEVEMENTS, "award", ach_id)


def progress(ach_id, value):
    return send(ACHIEVEMENTS, "progress", ach_id, value)


def share(data, mime):
    return _intents().share(_pid(), data, mime)


def pick(mime, mode="r"):
    return _intents().pick(_pid(), mime, mode)


def _intents_end(pid):
    _intents().end(pid)


ENDS.append(_intents_end)


def log(level, text):
    if _L is not None:
        _L.write(max(0, min(7, int(level))), _pid(), str(text))


def clock_synced():
    try:
        import wallclock
    except ImportError:  # host: the runtime package
        from runtime import wallclock
    return wallclock.synced()


def __getattr__(name):
    if name in ("http", "mic", "lora", "ble", "usb", "midi", "camera", "media"):
        try:
            import devices
        except ImportError:  # host: the runtime package
            from runtime import devices
        return devices.api(name)
    raise AttributeError(name)
