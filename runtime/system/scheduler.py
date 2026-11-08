# Map (grep -n a name to jump there):
#   Proc                     a process the scheduler has met: its state and counts
#   Scheduler                the wakes' runner, the services and their policies
#   -- the tick              Scheduler.tick, mail, the plain wakes, step
#   -- the rungs             overrun, strike, suspend, resume, a crash
#   -- services              start, hook, stop, kill, exit, pause, reset
#   -- restart policies      policy, set_policy, forgive, the back-off
#   start_cart               a shelf cart started under its consent
#   boot                     the autostart services at the first frame
"""The cooperative scheduler (docs/os_2026-11/processes.md sections 1, 3, 4
and 5): the wakes of `moybyte.WAKES` run once a frame after present, each
callback under a slice and the abort guard, and the services the shell
starts, stops and autostarts.

THE TICK. `SCHED.tick(slack_us)` runs once a frame after the console's frame
(the boards' service upcall, device/desktop_spine.py; the host's and the
browser's ConsoleDriver.frame). It runs the wakes due, oldest first, while its
budget lasts: the frame's `slack_us` (what the loop would otherwise sleep),
and at least MOY_BG_SLICE_US every MOY_BG_SLICE_EVERY frames, so an unpaced
game (no slack) still leaves background work its slice. A callback is never
split: the first due one runs whatever the budget, and the wakes left over
run first on the next tick. With no wakes registered a tick returns at once,
and on a board the service upcall is not made at all; a message wake with
no message queued (moybyte's `mail`) counts as none. The tick allocates
nothing (tests/test_frame_alloc.py).

THE RUNGS. Each callback runs with its process current in the OS log (its
prints, its new wakes and its events() are its own) and under
`moy_guard.call` (native/moy_kernel/modmoy_guard.c): a one-shot timer of
MOY_BG_ABORT_US aborts it if it never returns. Its cost is measured less the
collections inside it. A callback over MOY_BG_CALLBACK_US is an OVERRUN:

  the first overrun          a warning in the OS log
  every later one, and
  every aborted callback     a strike, logged at err
  MOY_BG_STRIKES strikes     the process is SUSPENDED: its wakes stay
                             registered and are not served until `resume`

The shell's own wakes (pid 1) are never struck. The rungs count over a run;
a new start is a new process. An abort skips the callback's `finally` and
`__exit__` (the guard's header says what it puts back).

ASYNCIO. The shell's one asyncio loop (runtime/system/aio.py) is a wake of
the shell's that the tick runs plainly (`plain_every`), each task step under
`step`: the guard and the rungs as the task's process, its exception the
loop's. A process's live tasks count as its wakes. The first start of a
service whose source names asyncio installs the loop.

MESSAGES (moybyte's `mail`) are served by `mail()`, which the console calls
at the start of its frame while one is queued, so a message sent by the
frame's input is served before the frame draws, and by a tick that runs
first, as any wake is. A send asks a board for no upcall of its own.

A callback's exception is a crash: its traceback goes to the OS log at crit
and a service exits with EXIT_CRASHED; a wake of any other process is
cancelled. A SystemExit from a service's callback is its exit, with its code.

SERVICES (`"type": "service"`). `start(cart)` checks the launch under the
cart's consent (runtime/shell/app_perms.py), spawns its pid, runs its main
with `moybyte` and its app's role names in its namespace and then its
`moy_start()`; it runs until it
has no wakes left (exit 0), crashes, or is stopped, and a suspended service
holds its wakes unserved. `stop(pid)` is the TERM: its `moy_stop()` hook runs
under a guard of MOY_SERVICE_STOP_MS, and a service whose hook is still
running then gets the KILL (EXIT_KILLED); either way its wakes and readers
end. A start that runs past the same limit is killed too. Every start, stop,
kill, exit and rung is one line of the OS log under the service's pid.

ITS APP'S ROLES (owner, 2026-11-08; docs/os_2026-11/processes.md section 5).
A service started on a console holds the roles a foreground run of its app
is granted, under its granted permissions, on a run grant of its own whose
owner is its pid (system_api.service_api), held on its row and ended at its
end: a Python service's namespace holds the names a Python app's does, a Lua
service's state opens the names a Lua app's does. `tell` routes
`moybyte.tell`: message "app" to the other processes of the caller's app
(`app_of`, the console's, bound at `attach`).

LUA SERVICES (`"runtime": "lua"`). The start hands the cart's source to
moycore's services (native/moycore/moycore_svc.h): a state of its own, on a
task of its own on a P4's core 0, refused where the chip runs none (the S3s,
logged and exited 1), with what the console hands a system process (its
`env`: the values as globals, the functions as console calls) and its app's
run, whose role names its state opens. `poll()`, a
wake of the shell's every POLL_MS while one lives, steps them where they
have no task (the host, the desktop MicroPython), answers the console calls
they wait on (`moybyte.notify`, `tell` and the `env` functions, run here on
the VM's task as the service's process, at most CALLS_MAX a service a poll,
and, on a P4, the role row its task posted), reads
each one's heap (exact) and its calls' time onto its row, and hands an end
to the same exit, log line and restart policy; a stop is its TERM there, the
C side's KILL past MOY_SERVICE_STOP_MS. `send` routes a message to a Lua
service's row (a radio's change reaches every row through `_routed`, the
radios' router's hook, kept while a Lua service lives), and the console's frame start (`mail`) and a tick that runs
first serve it with a poll, as they serve a Python service's.

AUTOSTART. `boot(ws)`, at the console's first drawn frame, starts every
service cart whose manifest asks for `autostart` and whose consent holds it
(Settings turns it off per app), and every service the resume record says
was running when the kernel stopped the VM.

RESTART POLICIES. A service's policy says what follows an end nobody asked
for (an exit, a crash, a kill at its start; a `stop`, the pause before a VM
stop and `reset` are asked): `never` (the default) leaves it down,
`on-crash` starts it again after an end with a code other than 0, `always`
after any. The restart waits a back-off, MOY_RESTART_BACKOFF_MS doubled for
each end in a row, and the RESTART_CAP'th end in a row (the strike ledger's
cap, CrashGuard.STRIKES) leaves it down; a run of MOY_RESTART_HEAL_MS or more
before its end starts the count afresh, and so does a start by hand
(`forgive`). The restart is the cart's launch again, under its consent as it
stands then, as a new process whose start forgets the old row (its log
stays). The policies are `system/settings`' RESTART_KEY, {service id:
policy}, read at the end: `service policy` sets and shows them
(runtime/shell/sh_roles.py), and every start, end, restart and the cap are
lines of the OS log.

ACROSS A VM STOP (the S3s). The stop ends the VM's heap, so a Python service
cannot be held through it: `pause()`, just before the stop, TERMs every
running service (its `moy_stop` persists what it needs) and the resume
record names them; the console that starts after the stop starts them again
(`moy_start`). Records their sources took during the stop are not delivered.
"""

try:
    from ticks import _ticks_ms, _ticks_us, _ticks_diff
except ImportError:  # host: the runtime package
    from runtime.ticks import _ticks_ms, _ticks_us, _ticks_diff

try:
    import moybyte as _moybyte
except ImportError:  # host: the runtime package, `import moybyte` for a service
    import sys as _sys
    from runtime import moybyte as _moybyte
    _sys.modules.setdefault("moybyte", _moybyte)

# The console calls every Lua service holds (native/moycore/moycore_svc.c's
# `moybyte` table), answered by the console's own verbs.
_CALLS = {"moybyte.notify": _moybyte.notify, "moybyte.open_with": _moybyte.open_with,
          "moybyte.share": _moybyte.share, "moybyte.tell": _moybyte.tell}

try:
    import json as _json
except ImportError:  # pragma: no cover - every VM has it
    _json = None

try:
    import moy_log as _L
except ImportError:  # pragma: no cover - an image without the kernel
    _L = None

try:
    import moy_guard as _guard
except ImportError:  # CPython and the browser: no VM abort
    _guard = None

MOY_BG_SLICE_US = 1000          # the background slice a frame always gets
MOY_BG_SLICE_EVERY = 1          # ... every this many frames
MOY_BG_CALLBACK_US = 20000      # a callback over this is an overrun
MOY_BG_ABORT_US = 300000        # the guard's timer: past a collection's pause
MOY_BG_STRIKES = 3              # strikes to suspension
MOY_SERVICE_STOP_MS = 2000      # TERM to KILL: a start or stop hook's limit
MOY_RESTART_BACKOFF_MS = 1000  # the first restart's wait, doubled for each end in a row
MOY_RESTART_HEAL_MS = 10000     # a run this long before its end starts the count afresh
DUE_MAX = 16                    # wakes taken from the registry at a time
POLL_MS = 20                    # the Lua services' poll (a stepped tier's timer grain)
CALLS_MAX = 16                  # console calls answered a Lua service a poll
SVC_EXITED = 4                  # moycore_svc.h's MOYCORE_SVC_EXITED
EINVAL = 22
_WRAP = 0x3FFFFFFF              # a counter wraps as ticks do: a small int on a board

EXIT_OK, EXIT_CRASHED = 0, 1
# A kill's status is the process table's (native/moy_kernel/moy_proc_exit.h),
# read through moy_log's binding.
EXIT_KILLED = _L.KILLED if _L is not None else None

RUNNING = "running"             # an app's row (its wakes; the Player runs it)
BACKGROUND = "background"       # a service, running
SUSPENDED = "suspended"
STOPPING = "stopping"
EXITED = "exited"

SHELL = 1
SERVICE, APP, COMMAND = "service", "app", "command"

NEVER, ON_CRASH, ALWAYS = "never", "on-crash", "always"
POLICIES = (NEVER, ON_CRASH, ALWAYS)
RESTART_KEY = "service_restart"     # system/settings: {service id: policy}

try:
    from crash_guard import CrashGuard as _CG
except ImportError:  # host: the runtime package
    from runtime.crash_guard import CrashGuard as _CG
RESTART_CAP = _CG.STRIKES           # ends in a row that leave a service down

ALLOC_WRAP = _WRAP              # `allocated()` wraps here: a difference is masked by it

try:
    import tracemalloc as _tm
except ImportError:  # a board: the guard counts
    _tm = None
_ALLOCATED = getattr(_guard, "allocated", None) if _guard is not None else None


def allocated():
    """The bytes the VM has allocated, a count that wraps at ALLOC_WRAP:
    a difference across a call is what the call allocated, its frees not
    subtracted (one shared heap cannot say whose an object is), so a
    process's memory is approximate. The boards and the unix builds count
    in the guard (`moy_guard.allocated`); CPython reads tracemalloc's
    current figure while it traces (the tests), and None otherwise."""
    if _ALLOCATED is not None:
        return _ALLOCATED()
    if _tm is not None and _tm.is_tracing():
        return _tm.get_traced_memory()[0] & _WRAP
    return None

_W = _moybyte._OWNER, _moybyte._HOW, _moybyte._AFTER, _moybyte._AT
_OWNER, _HOW, _AFTER, _AT = _W
_MS = _moybyte._MS

ABORTED = _guard.ABORTED if _guard is not None else object()


class Proc:
    """A process the scheduler has met: a service it started, or another
    process whose wakes reached a rung."""

    def __init__(self, pid, kind, name, cart=None):
        self.pid = pid
        self.kind = kind
        self.name = name
        self.cart = cart
        self.state = BACKGROUND if kind == SERVICE else RUNNING
        self.ns = None
        self.granted = ()       # the permissions its start was granted (a service's)
        self.overruns = 0
        self.strikes = 0
        self.code = None
        self.calls = 0
        self.cost_us = 0        # its callbacks' cost, summed (wrapping at 2**30)
        self.started = 0        # ticks_ms at its start
        self.ws = None          # the console a service was started on, its cart
        self.origin = None      # as the shelf holds it, and its consent: what a
        self.args = ()          # restart launches again
        self.mem = None         # what its start allocated (`allocated`), bytes
        self.cpu = None         # its last sample's busy us a second (Procs.account)
        self.seen_us = 0        # cost_us at that sample
        self.native = 0         # a Lua service's handle (moycore_svc.h), 0: Python
        self.ctx = None         # a service's context: its run grant over its app's roles


def _end_grant(p):
    """A service's end: its run grant ends with it."""
    ctx, p.ctx = p.ctx, None
    if ctx is not None:
        ctx.end()


def _let_go(p):
    """An exited row that will not restart holds no console."""
    p.ws = p.origin = None
    p.args = ()


def _log(level, pid, *parts):
    if _L is not None:
        _L.write(level, pid, *parts)


def _lvl(name, default):
    return getattr(_L, name, default) if _L is not None else default


INFO = _lvl("INFO", 6)
WARNING = _lvl("WARNING", 4)
ERR = _lvl("ERR", 3)


class Scheduler:
    ABORTED = ABORTED

    def __init__(self, wakes=None, guard=_guard):
        self.wakes = wakes if wakes is not None else _moybyte.WAKES
        self.guard = guard
        self.procs = {}             # pid -> Proc
        self.frame = 0              # frames since the last guaranteed slice
        self.spent_us = 0           # the last tick's background time
        self.resumed = ()           # what the resume record says was running
        self._due = [0] * DUE_MAX
        self._n = 0
        self._i = 0
        self._wid = 0
        self._one = self._call_wid      # bound once: the guard's callable
        self._plain = {}            # wid -> True: the shell's wakes run unguarded
        self.store = None           # the settings the restart policies are read from
        self.down = {}              # service id -> its ends in a row
        self.pending = {}           # service id -> the wake of its restart
        self._t0 = 0                # this tick's start and budget (left_us)
        self._budget = 0
        self._poll = 0              # the Lua services' poll, a wake of the shell's
        self.svc_mail = False       # a message sent to a Lua service, not yet polled
        self.app_of = None          # fn(pid) -> the app id it runs, the console's (`attach`)
        self.wakes.ended = self._ended
        self.wakes.routed = self._routed

    # -- the tick ---------------------------------------------------------

    def tick(self, slack_us=0):
        """The frame's background work; answers the microseconds spent."""
        f = self.frame + 1
        self.frame = f = 0 if f >= MOY_BG_SLICE_EVERY else f
        w = self.wakes
        if not w.list and not w.queued:
            self._n = self._i = 0
            self.spent_us = 0
            return 0
        if self.svc_mail:
            self.svc_mail = False
            self.poll()
        budget = slack_us
        if f == 0 and budget < MOY_BG_SLICE_US:
            budget = MOY_BG_SLICE_US
        if budget <= 0:
            self.spent_us = 0
            return 0
        t0 = _ticks_us()
        self._t0 = t0
        self._budget = budget
        if self._i >= self._n:
            self._n = self.wakes.due(_ticks_ms(), self._due)
            self._i = 0
        due = self._due
        ran = False
        while self._i < self._n:
            if ran and _ticks_diff(_ticks_us(), t0) >= budget:
                break
            wid = due[self._i]
            self._i += 1
            self._run(wid)
            ran = True
        spent = _ticks_diff(_ticks_us(), t0)
        self.spent_us = spent
        return spent

    def mail(self):
        """The messages queued, served now, each wake under the guard and
        the rungs: the console calls it at the start of its frame, before
        it draws, while `queued` is not 0, so what a message raises shows
        in the frame of the input that sent it. Answers the microseconds
        spent."""
        w = self.wakes
        if not w.queued and not self.svc_mail:
            return 0
        t0 = _ticks_us()
        if self.svc_mail:
            self.svc_mail = False
            self.poll()
        for wake in list(w.mail):
            if wake[_AT]:
                self._run(wake[0])
        return _ticks_diff(_ticks_us(), t0)

    def send(self, pid, name, args):
        """The OS routes message `name` with `args` to process `pid`
        (moybyte's `send`, or a Lua service's row): False where it holds no
        wake on `name`."""
        p = self.procs.get(pid)
        if p is None or not p.native:
            return self.wakes.send(pid, name, args)
        svc = _svc()
        try:
            took = svc.svc_send(p.native, name, _json.dumps(list(args)))
        except (OSError, TypeError, ValueError):
            return False
        if took:
            self.svc_mail = True
        return took

    def tell(self, pid, args):
        """`moybyte.tell` from process `pid`: message "app" with `args` to
        every other live process of its app (`app_of`: the app id a run or
        a service runs under, the grant's key) that waits on it, a Lua
        service's row alike. Answers how many took it."""
        of = self.app_of
        aid = of(pid) if of is not None else None
        if aid is None:
            return 0
        to = []
        for w in self.wakes.mail:
            if w[_MS] == "app" and w[_OWNER] not in to:
                to.append(w[_OWNER])
        for p in self.procs.values():
            if p.native and p.state != EXITED and p.pid not in to:
                to.append(p.pid)
        n = 0
        for q in to:
            if q != pid and of(q) == aid and self.send(q, "app", tuple(args)):
                n += 1
        return n

    def wakes_of(self, pid):
        """How many wakes process `pid` holds: its timers, readers and
        message wakes."""
        p = self.procs.get(pid)
        if p is not None and p.native:
            got = _svc().svc_info(p.native)
            return got[6] if got is not None else 0
        return self.wakes.count(pid)

    def mail_of(self, pid):
        """How many messages wait for process `pid`."""
        p = self.procs.get(pid)
        if p is not None and p.native:
            got = _svc().svc_info(p.native)
            return got[7] if got is not None else 0
        return self.wakes.mail_of(pid)

    def left_us(self):
        """What is left of this tick's budget, microseconds."""
        return self._budget - _ticks_diff(_ticks_us(), self._t0)

    def plain_every(self, ms, fn):
        """A wake of the shell's, every `ms`, that the tick runs without the
        guard: a driver whose own callbacks each take one (the asyncio loop,
        runtime/system/aio.py). Answers its id."""
        return self._plain_wake(self.wakes.every, ms, fn)

    def plain_alarm(self, t, fn):
        """An alarm of the shell's at Unix time `t` that the tick runs without
        the guard and that lights nothing (cron's and at's,
        runtime/system/cron.py). Answers its id."""
        return self._plain_wake(lambda t, fn: self.wakes.alarm(t, fn, False), t, fn)

    def plain_cancel(self, wid):
        """A plain wake undone."""
        self._plain.pop(wid, None)
        return self.wakes.cancel(wid)

    def _plain_wake(self, make, ms, fn):
        was = _L.current() if _L is not None else SHELL
        if _L is not None:
            _L.current(SHELL)
        try:
            wid = make(ms, fn)
        finally:
            if _L is not None:
                _L.current(was)
        self._plain[wid] = True
        return wid

    def _call_wid(self):
        self.wakes.call(self._wid)

    def _run(self, wid):
        w = self.wakes.by_id.get(wid)
        if w is None:
            self._plain.pop(wid, None)
            return
        if wid in self._plain:
            self.wakes.call(wid)
            if wid not in self.wakes.by_id:
                self._plain.pop(wid, None)
            return
        pid = w[_OWNER]
        p = self.procs.get(pid)
        if p is not None and (p.state == SUSPENDED or p.state == EXITED):
            return
        self._wid = wid
        self._guarded(pid, self._one, w)
        if p is None:
            p = self.procs.get(pid)
        if p is not None and p.kind == SERVICE and p.state != EXITED \
                and not self.wakes.count(pid):
            self._exit(p, EXIT_OK)

    def step(self, pid, fn):
        """An asyncio task's step (runtime/system/aio.py): `fn()` as process
        `pid` under the guard and the rungs, counted as its callback. Answers
        ABORTED or fn's result; fn's exception, its end or its error, is the
        caller's once it is counted."""
        return self._guarded(pid, fn, None, None, True, True)

    def held(self, pid):
        """Whether process `pid` is suspended: its callbacks wait."""
        p = self.procs.get(pid)
        return p is not None and p.state == SUSPENDED

    def settle(self, pid):
        """A live service holding no wake and no task has exited (0)."""
        p = self.procs.get(pid)
        if p is not None and p.kind == SERVICE and p.state != EXITED \
                and p.state != STOPPING and not self.wakes.count(pid):
            self._exit(p, EXIT_OK)

    def crash(self, pid, exc):
        """Process `pid` crashed with `exc` outside a wake (an asyncio task):
        logged, and a service exits with its code."""
        self._crash(pid, exc, None)

    def _guarded(self, pid, fn, w=None, budget=None, ladder=True, raises=False):
        """`fn()` as process `pid` under a guard of `budget` microseconds,
        the rungs counted where `ladder`. Answers ABORTED, None for a crash,
        else fn's result; where `raises`, fn's exception is re-raised once
        counted instead."""
        if budget is None:
            budget = MOY_BG_ABORT_US
        wakes = self.wakes
        was_reader = wakes.cur[0]
        was_pid = _L.current() if _L is not None else SHELL
        if _L is not None:
            _L.current(pid)
        g = self.guard
        t0 = _ticks_us()
        r = None
        err = None
        try:
            if g is not None:
                r = g.call(fn, budget)
                cost = g.cost()
            else:
                r = fn()
                cost = _ticks_diff(_ticks_us(), t0)
        except BaseException as exc:  # noqa: BLE001 -- a callback never breaks a frame
            cost = _ticks_diff(_ticks_us(), t0)
            if raises:
                err = exc
            elif isinstance(exc, (SystemExit, Exception)):
                self._crash(pid, exc, w)
            else:
                raise
        finally:
            if _L is not None:
                _L.current(was_pid)
        p = self.procs.get(pid)
        if p is not None:
            p.calls = (p.calls + 1) & _WRAP
            p.cost_us = (p.cost_us + cost) & _WRAP
        if r is ABORTED:
            wakes.cur[0] = was_reader       # the abort skipped call()'s finally
            if w is not None and w[_HOW] == _AFTER:
                wakes.cancel(w[0])
            if ladder:
                self._strike(pid, "aborted after %d ms" % (budget // 1000))
        elif ladder and cost > MOY_BG_CALLBACK_US:
            self._overrun(pid, cost)
        if err is not None:
            raise err
        return r

    # -- the rungs --------------------------------------------------------

    def _row(self, pid):
        p = self.procs.get(pid)
        if p is None:
            name = _L.name(pid) if _L is not None else "pid %d" % pid
            p = self.procs[pid] = Proc(pid, APP, name)
        return p

    def _overrun(self, pid, cost):
        if pid == SHELL:
            return
        p = self._row(pid)
        p.overruns += 1
        if p.overruns == 1:
            _log(WARNING, pid, "callback ran %d ms, over its %d ms slice"
                 % (cost // 1000, MOY_BG_CALLBACK_US // 1000))
        else:
            self._strike(pid, "callback ran %d ms" % (cost // 1000))

    def _strike(self, pid, why):
        if pid == SHELL:
            return
        p = self._row(pid)
        p.overruns += 1
        p.strikes += 1
        _log(ERR, pid, "strike %d of %d: %s" % (p.strikes, MOY_BG_STRIKES, why))
        if p.strikes >= MOY_BG_STRIKES and p.state != EXITED:
            p.state = SUSPENDED
            _log(ERR, pid, "suspended")

    def resume(self, pid):
        """A suspended process served again, its rungs from the start."""
        p = self.procs.get(pid)
        if p is None or p.state != SUSPENDED:
            return False
        p.state = BACKGROUND if p.kind == SERVICE else RUNNING
        p.overruns = p.strikes = 0
        _log(INFO, pid, "resumed")
        return True

    def _crash(self, pid, exc, w):
        p = self.procs.get(pid)
        if isinstance(exc, SystemExit):
            code = exc.args[0] if exc.args else 0
            code = code if isinstance(code, int) else EXIT_CRASHED
            if p is not None and p.kind == SERVICE:
                self._exit(p, code)
            elif w is not None:
                self.wakes.cancel(w[0])
            return
        try:
            from os_log import crashed
        except ImportError:  # host: the runtime package
            from runtime.os_log import crashed
        crashed(pid, exc)
        if p is not None and p.kind == SERVICE:
            self._exit(p, EXIT_CRASHED)
        elif w is not None:
            self.wakes.cancel(w[0])

    # -- services -----------------------------------------------------------

    def start(self, cart, rec=None, have=(), env=None, ws=None, origin=None):
        """Start service `cart` (a cart dict carrying `src`), under consent
        record `rec` on a console with the services `have`, its namespace
        holding the names of `env` (what the console hands a system
        process); `ws` and `origin`, the console and the shelf's cart, are
        what a restart launches again (`start_cart`). A cart already running
        answers its pid. Answers its pid, or None when the launch was refused
        or it ended in its start."""
        try:
            import app_perms
        except ImportError:  # host: the runtime package
            from runtime import app_perms
        name = cart.get("id") or cart.get("title") or "service"
        running = self.find(name)
        if running is not None:
            return running.pid
        granted, absent, refusal = app_perms.check(cart, rec, have)
        pid = _L.spawn(name) if _L is not None else self._next_pid()
        p = self.procs[pid] = Proc(pid, SERVICE, name, cart)
        p.granted = tuple(granted)
        p.started = _ticks_ms()
        p.ws, p.origin, p.args = ws, origin, (rec, have, env)
        if refusal is not None:
            _log(ERR, pid, refusal)
            p.state, p.code = EXITED, EXIT_CRASHED
            return None
        _log(INFO, pid, "start", name)
        try:
            roles, names = self._grant(p, ws, cart)
        except (OSError, ValueError) as e:     # the grant table full, a refused kind
            _log(ERR, pid, "its grant: %s" % e)
            p.state, p.code = EXITED, EXIT_CRASHED
            return None
        if cart.get("runtime") == "lua":
            return self._start_lua(p, cart.get("src") or "", env, roles)
        if "asyncio" in src_of(cart):
            _aio().install(self)
        ns = {"__name__": "__main__", "moybyte": _moybyte}
        if env:
            ns.update(env)
        ns.update(names)
        for n in app_perms.absent_names(absent, rec):
            ns[n] = None
        p.ns = ns
        src = cart.get("src") or ""

        def body():
            code = compile(src, "<%s>" % name, "exec")
            a0 = allocated()
            exec(code, ns)
            hook = ns.get("moy_start")
            if hook is not None:
                hook()
            a1 = allocated()
            if a0 is not None and a1 is not None:
                p.mem = (a1 - a0) & _WRAP
        r = self._guarded(pid, body, None, MOY_SERVICE_STOP_MS * 1000, False)
        if p.state == EXITED:
            return None
        if r is ABORTED:
            self._kill(p, "its start ran past %d ms" % MOY_SERVICE_STOP_MS)
            return None
        if not self.wakes.count(pid):
            self._exit(p, EXIT_OK)
            return None
        return pid

    def _grant(self, p, ws, cart):
        """Service `p`'s roles on console `ws`: its app's, under the
        permissions its start was granted, on a run grant of its own owned by
        its pid (system_api.service_api), held on its row until its end.
        Answers (its roles, the names a Python service's namespace holds);
        ((), {}) on no console."""
        factory = getattr(ws, "app_context", None)
        if factory is None:
            return (), {}
        try:
            import system_api
        except ImportError:  # host: the runtime package
            from runtime import system_api
        p.ctx, roles, names = system_api.service_api(factory, cart, p.granted, p.pid)
        return roles, names

    # -- the Lua services -------------------------------------------------

    def _start_lua(self, p, src, env=None, roles=()):
        """Lua service `p` (native/moycore/moycore_svc.h): its own state, on a
        task of its own on the P4s and at this scheduler's poll elsewhere,
        handed `env` (its values as globals, its functions as console calls)
        and its app's run over `roles` (its role names, moy_app_lua.h);
        refused on a chip that runs none (the S3s) or with no Lua runtime."""
        svc = _svc()
        run = None
        if p.ctx is not None:
            run = p.ws.app_abi.run(p.ctx.grant, roles)
        try:
            if svc is None:
                raise OSError(EINVAL, "no Lua runtime")
            p.native = svc.svc_start(p.pid, p.name, src, _env_text(env), run)
        except (TypeError, ValueError) as e:
            _log(ERR, p.pid, "what the console hands it is not JSON: %s" % e)
            p.state, p.code = EXITED, EXIT_CRASHED
            _end_grant(p)
            return None
        except OSError as e:
            _log(ERR, p.pid, "a Lua service does not run on this console"
                 if e.args and e.args[0] == EINVAL else "no room for a Lua service: %s" % e)
            p.state, p.code = EXITED, EXIT_CRASHED
            _end_grant(p)
            return None
        if not self._poll:
            self._poll = self.plain_every(POLL_MS, self.poll)
            self._radios(True)
        self.poll()
        return p.pid if p.state != EXITED else None

    def poll(self):
        """The Lua services: stepped where they have no task of their own,
        then each one's figures read onto its row (its heap exact, its
        calls' time) and an end handed to `_exit`, which its restart policy
        follows. The poll's wake goes with the last service."""
        svc = _svc()
        if svc is None:
            return
        stepped = not svc.svc_tasks()
        if stepped:
            svc.svc_step(_ticks_ms())
        live = False
        for pid in list(self.procs):
            p = self.procs[pid]
            if not p.native:
                continue
            self._answer(p, svc, stepped)
            got = svc.svc_info(p.native)
            if got is None:
                p.native = 0
                continue
            state, code, heap, cpu_us = got[0], got[1], got[2], got[3]
            why = got[5]
            p.mem = heap
            p.cost_us = cpu_us & ALLOC_WRAP
            if state != SVC_EXITED:
                live = True
                continue
            h, p.native = p.native, 0
            svc.svc_reap(h)
            if p.state != EXITED:
                if why:
                    _log(ERR if code else INFO, p.pid, why)
                self._exit(p, code)
        if not live and self._poll:
            self.wakes.cancel(self._poll)
            self._plain.pop(self._poll, None)
            self._poll = 0
            self._radios(False)

    def _radios(self, on):
        """The radios' router (moybyte's THE RADIOS) kept while a Lua
        service lives, which may wait on "net" or "ble" on its own row."""
        self.wakes.radio_keep = on
        self.wakes.radio_sync()

    def _routed(self, name, args):
        """A radio's change, which the router queued on the Python wakes,
        sent on to each Lua service; a row with no wake on it takes none."""
        if not self._poll:
            return
        for p in list(self.procs.values()):
            if p.native:
                self.send(p.pid, name, args)

    def _answer(self, p, svc, stepped):
        """The console calls Lua service `p` waits on, answered on this
        task as its process: `moybyte.notify`, `open_with`, `share` or
        `tell` (`_CALLS`), or a function of the `env` its start was handed;
        a role row its own task posted runs first, in the call that asks
        (moycore_svc_call). A stepped service runs on to its next call at
        once."""
        env = p.args[2] if len(p.args) > 2 else None
        for _ in range(CALLS_MAX):
            was = _L.current() if _L is not None else SHELL
            if _L is not None:
                _L.current(p.pid)
            try:
                req = svc.svc_call(p.native)
                if req is None:
                    return
                name, args = req
                fn = _CALLS.get(name)
                if fn is None and env:
                    fn = env.get(name)
                ans = None
                try:
                    if fn is not None:
                        ans = _json.dumps(fn(*_json.loads(args)))
                except Exception as exc:  # noqa: BLE001 -- refused, the service told
                    _log(ERR, p.pid, "%s: %s: %s" % (name, type(exc).__name__, exc))
            finally:
                if _L is not None:
                    _L.current(was)
            svc.svc_answer(p.native, ans)
            if stepped:
                svc.svc_step(_ticks_ms())

    def hook(self, pid, name, *args):
        """Run live service `pid`'s hook `name(*args)` (`moy_action`, a
        notification acted on) as a callback: under the guard and the rungs.
        False where the service is gone or defines no such hook."""
        p = self.procs.get(pid)
        if p is None or p.kind != SERVICE or p.state == EXITED or p.ns is None:
            return False
        fn = p.ns.get(name)
        if fn is None:
            return False
        self._guarded(pid, lambda: fn(*args))
        if p.state != EXITED and not self.wakes.count(pid):
            self._exit(p, EXIT_OK)
        return True

    def _next_pid(self):
        n = 2
        while n in self.procs:
            n += 1
        return n

    def find(self, name):
        """The live service started as `name` (its cart id), or None."""
        for p in self.procs.values():
            if p.kind == SERVICE and p.name == name and p.state != EXITED:
                return p
        return None

    def services(self):
        return [p for p in self.procs.values() if p.kind == SERVICE]

    def stop(self, pid):
        """The TERM: the service's `moy_stop()` under a guard of
        MOY_SERVICE_STOP_MS, the KILL if it is still running then. Either
        way the service ends. Answers whether `pid` was a live service."""
        p = self.procs.get(pid)
        if p is None or p.kind != SERVICE or p.state == EXITED:
            return False
        _log(INFO, pid, "stop")
        p.state = STOPPING
        if p.native:
            _svc().svc_stop(p.native)
            self.poll()
            return True
        hook = p.ns.get("moy_stop") if p.ns is not None else None
        r = None
        if hook is not None:
            r = self._guarded(pid, hook, None, MOY_SERVICE_STOP_MS * 1000, False)
        if p.state == EXITED:
            return True
        if r is ABORTED:
            self._kill(p, "its stop ran past %d ms" % MOY_SERVICE_STOP_MS)
        else:
            self._exit(p, EXIT_OK)
        return True

    def _kill(self, p, why):
        _log(ERR, p.pid, "killed:", why)
        self._exit(p, EXIT_KILLED)

    def _exit(self, p, code):
        if p.state == EXITED:
            return
        asked = p.state == STOPPING
        p.state = EXITED
        p.code = code
        p.ns = None
        _end_grant(p)
        self.wakes.end(p.pid)
        _log(INFO, p.pid, "exit %d" % code)
        if asked or p.kind != SERVICE or not self._unasked(p, code):
            _let_go(p)

    # -- restart policies ---------------------------------------------------

    def policy(self, name):
        """Service `name`'s restart policy: NEVER where none is set."""
        rows = self.store.get(RESTART_KEY) if self.store is not None else None
        got = rows.get(name) if isinstance(rows, dict) else None
        return got if got in POLICIES else NEVER

    def set_policy(self, name, policy):
        """Service `name`'s restart policy set, persisted in the settings."""
        if policy not in POLICIES:
            raise ValueError("no restart policy %r" % (policy,))
        if self.store is None:
            raise OSError("no settings to keep it in")
        rows = self.store.get(RESTART_KEY)
        rows = dict(rows) if isinstance(rows, dict) else {}
        if policy == NEVER:
            rows.pop(name, None)
        else:
            rows[name] = policy
        self.store.set(RESTART_KEY, rows)

    def forgive(self, name):
        """A start by hand: service `name`'s ends in a row forgotten and its
        pending restart dropped. Answers whether one was pending."""
        self.down.pop(name, None)
        wid = self.pending.pop(name, None)
        if wid is None:
            return False
        for p in self.procs.values():
            if p.name == name and p.state == EXITED:
                _let_go(p)
        self.wakes.cancel(wid)
        self._plain.pop(wid, None)
        return True

    def _unasked(self, p, code):
        """What follows service `p`'s end nobody asked for; True where it
        restarts."""
        pol = self.policy(p.name)
        if pol == NEVER or (pol == ON_CRASH and code == EXIT_OK):
            return False
        n = self.down.get(p.name, 0)
        if _ticks_diff(_ticks_ms(), p.started) >= MOY_RESTART_HEAL_MS:
            n = 0
        n += 1
        self.down[p.name] = n
        if n >= RESTART_CAP:
            _log(ERR, p.pid, "%s: %d ends in a row, it stays down" % (pol, n))
            return False
        ms = MOY_RESTART_BACKOFF_MS << (n - 1)
        _log(WARNING, p.pid, "%s: restart in %d ms (end %d of %d)"
             % (pol, ms, n, RESTART_CAP))
        old = self.pending.pop(p.name, None)
        if old is not None:
            self.wakes.cancel(old)
        self.pending[p.name] = self._plain_wake(self.wakes.after, ms,
                                                lambda: self._again(p))
        return True

    def _again(self, p):
        """Service `p` launched again after its back-off."""
        self.pending.pop(p.name, None)
        if self.find(p.name) is not None:
            return
        if self.procs.get(p.pid) is p:
            del self.procs[p.pid]
        cart = p.origin if p.origin is not None else p.cart
        if p.ws is not None:
            start_cart(p.ws, cart, self)
        else:
            rec, have, env = p.args
            self.start(cart, rec, have, env)

    def _ended(self, pid):
        """`moybyte.end(pid)`: a process the Player ran ended; its row goes."""
        p = self.procs.get(pid)
        if p is not None and p.kind != SERVICE:
            del self.procs[pid]

    def pause(self):
        """Before a VM stop: every live service TERMed, its wakes ended.
        Answers the carts' paths, which the resume record carries."""
        out = []
        for p in list(self.procs.values()):
            if p.kind == SERVICE and p.state != EXITED:
                path = p.cart.get("path") if p.cart else None
                if path:
                    out.append(path)
                self.stop(p.pid)
        return out

    def running_paths(self):
        return [p.cart.get("path") for p in self.procs.values()
                if p.kind == SERVICE and p.state != EXITED
                and p.cart and p.cart.get("path")]

    def reset(self):
        """Every service ended and every row forgotten (a test's teardown)."""
        for name in list(self.pending):
            self.forgive(name)
        svc = _svc()
        if svc is not None and not svc.svc_tasks():
            svc.svc_reset()             # its states close under their grants
        for p in list(self.procs.values()):
            if p.kind == SERVICE and p.state != EXITED:
                p.state = STOPPING
                self._exit(p, EXIT_KILLED)
        if self._poll:
            self.wakes.cancel(self._poll)
            self._plain.pop(self._poll, None)
            self._poll = 0
            self._radios(False)
        self.procs.clear()
        self.down.clear()
        self.svc_mail = False
        self.store = None
        self._n = self._i = 0
        self.resumed = ()


def _svc():
    """The Lua tier's services (moycore's svc_* names), or None."""
    try:
        import moycore
    except ImportError:
        try:
            from runtime import moycore
        except ImportError:
            return None
    return moycore if hasattr(moycore, "svc_start") else None


def src_of(cart):
    return cart.get("src") or ""


def _env_text(env):
    """What the console hands a Lua service, as moycore_svc_start takes it:
    the JSON text [values, calls], or None for nothing."""
    if not env:
        return None
    vals = {}
    calls = []
    for k, v in env.items():
        if callable(v):
            calls.append(k)
        else:
            vals[k] = v
    return _json.dumps([vals, calls])


def _aio():
    try:
        import aio
    except ImportError:  # host: the runtime package
        from runtime import aio
    return aio


SCHED = Scheduler()
_moybyte.WAKES.teller = SCHED.tell      # `moybyte.tell`: the console's scheduler


def tick(slack_us=0):
    return SCHED.tick(slack_us)


def prepared(ws, cart):
    """What a launch of `cart` on console `ws` runs under: (the cart with
    its source, read from the store when the shelf holds it slim, its
    consent record, the console's services, its namespace's names: a
    system process's, `ws.service_env`), or None where it has no source."""
    carts = getattr(ws, "carts", None)
    full = carts.rehydrate(cart) if carts is not None else cart
    run = dict(full)
    if carts is not None:
        carts.reslim(cart)
    if run.get("src") is None:
        return None
    consent = getattr(ws, "consent", None)
    if consent is not None:
        try:
            import app_perms
        except ImportError:  # host: the runtime package
            from runtime import app_perms
        rec = consent.get(app_perms.app_id(run))
        have = app_perms.services(ws)
    else:
        rec, have = None, ()
    env_of = getattr(ws, "service_env", None)
    return run, rec, have, env_of(run) if env_of is not None else None


def attach(ws, sched=None):
    """The console whose settings hold the restart policies and whose
    processes `tell` reads the app ids of."""
    sched = sched or SCHED
    sched.store = getattr(ws, "system", None)
    procs = getattr(ws, "procs", None)
    sched.app_of = getattr(procs, "app_id", None)


def start_cart(ws, cart, sched=None):
    """Start service `cart` of console `ws` under its consent (`prepared`)
    on `sched` (SCHED)."""
    sched = sched or SCHED
    attach(ws, sched)
    got = prepared(ws, cart)
    if got is None:
        _log(ERR, None, "service %s: no source" % (cart.get("id") or cart.get("title")))
        return None
    run, rec, have, env = got
    return sched.start(run, rec, have, env, ws, cart)


def wants_autostart(ws, cart):
    """A service cart asking for `autostart`, its consent not turned off."""
    if cart.get("type") != "service":
        return False
    if "autostart" not in (cart.get("permissions") or ()):
        return False
    consent = getattr(ws, "consent", None)
    if consent is None:
        return True
    try:
        import app_perms
    except ImportError:  # host: the runtime package
        from runtime import app_perms
    return consent.is_on(app_perms.app_id(cart), "autostart")


def boot(ws):
    """The console's services at its first frame: the autostart ones and
    those the resume record names. Answers the pids started."""
    out = []
    attach(ws)
    carts = getattr(ws, "carts", None)
    for cart in (carts.all if carts is not None else ()):
        if wants_autostart(ws, cart) or cart.get("path") in SCHED.resumed:
            pid = start_cart(ws, cart)
            if pid is not None:
                out.append(pid)
    SCHED.resumed = ()
    return out
