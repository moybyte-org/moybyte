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

MESSAGES (moybyte's `mail`) are served by `mail()`, which the console calls
at the start of its frame while one is queued, so a message sent by the
frame's input is served before the frame draws, and by a tick that runs
first, as any wake is. A send asks a board for no upcall of its own.

A callback's exception is a crash: its traceback goes to the OS log at crit
and a service exits with EXIT_CRASHED; a wake of any other process is
cancelled. A SystemExit from a service's callback is its exit, with its code.

SERVICES (`"type": "service"`). `start(cart)` checks the launch under the
cart's consent (runtime/shell/app_perms.py), spawns its pid, runs its main
with `moybyte` in its namespace and then its `moy_start()`; it runs until it
has no wakes left (exit 0), crashes, or is stopped, and a suspended service
holds its wakes unserved. `stop(pid)` is the TERM: its `moy_stop()` hook runs
under a guard of MOY_SERVICE_STOP_MS, and a service whose hook is still
running then gets the KILL (EXIT_KILLED); either way its wakes and readers
end. A start that runs past the same limit is killed too. Every start, stop,
kill, exit and rung is one line of the OS log under the service's pid.

AUTOSTART. `boot(ws)`, at the console's first drawn frame, starts every
service cart whose manifest asks for `autostart` and whose consent holds it
(Settings turns it off per app), and every service the resume record says
was running when the kernel stopped the VM.

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
DUE_MAX = 16                    # wakes taken from the registry at a time
_WRAP = 0x3FFFFFFF              # a counter wraps as ticks do: a small int on a board

EXIT_OK, EXIT_CRASHED, EXIT_KILLED = 0, 1, 137

RUNNING = "running"             # an app's row (its wakes; the Player runs it)
BACKGROUND = "background"       # a service, running
SUSPENDED = "suspended"
STOPPING = "stopping"
EXITED = "exited"

SHELL = 1
SERVICE, APP, COMMAND = "service", "app", "command"

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
        self.mem = None         # what its start allocated (`allocated`), bytes
        self.cpu = None         # its last sample's busy us a second (Procs.account)
        self.seen_us = 0        # cost_us at that sample


def _log(level, pid, *parts):
    if _L is not None:
        _L.write(level, pid, *parts)


def _lvl(name, default):
    return getattr(_L, name, default) if _L is not None else default


INFO = _lvl("INFO", 6)
WARNING = _lvl("WARNING", 4)
ERR = _lvl("ERR", 3)


class Scheduler:
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
        self.wakes.ended = self._ended

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
        budget = slack_us
        if f == 0 and budget < MOY_BG_SLICE_US:
            budget = MOY_BG_SLICE_US
        if budget <= 0:
            self.spent_us = 0
            return 0
        t0 = _ticks_us()
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
        if not w.queued:
            return 0
        t0 = _ticks_us()
        for wake in list(w.mail):
            if wake[_AT]:
                self._run(wake[0])
        return _ticks_diff(_ticks_us(), t0)

    def _call_wid(self):
        self.wakes.call(self._wid)

    def _run(self, wid):
        w = self.wakes.by_id.get(wid)
        if w is None:
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

    def _guarded(self, pid, fn, w=None, budget=None, ladder=True):
        """`fn()` as process `pid` under a guard of `budget` microseconds,
        the rungs counted where `ladder`. Answers ABORTED, None for a crash,
        else fn's result."""
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
        try:
            if g is not None:
                r = g.call(fn, budget)
                cost = g.cost()
            else:
                r = fn()
                cost = _ticks_diff(_ticks_us(), t0)
        except SystemExit as exc:
            cost = _ticks_diff(_ticks_us(), t0)
            self._crash(pid, exc, w)
            r = None
        except Exception as exc:  # noqa: BLE001 -- a callback never breaks a frame
            cost = _ticks_diff(_ticks_us(), t0)
            self._crash(pid, exc, w)
            r = None
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

    def start(self, cart, rec=None, have=(), env=None):
        """Start service `cart` (a cart dict carrying `src`), under consent
        record `rec` on a console with the services `have`, its namespace
        holding the names of `env` (what the console hands a system
        process). A cart already running answers its pid. Answers its pid,
        or None when the launch was refused or it ended in its start."""
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
        if refusal is not None:
            _log(ERR, pid, refusal)
            p.state, p.code = EXITED, EXIT_CRASHED
            return None
        _log(INFO, pid, "start", name)
        ns = {"__name__": "__main__", "moybyte": _moybyte}
        if env:
            ns.update(env)
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
        p.state = EXITED
        p.code = code
        p.ns = None
        self.wakes.end(p.pid)
        _log(INFO, p.pid, "exit %d" % code)

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
        for p in list(self.procs.values()):
            if p.kind == SERVICE and p.state != EXITED:
                self._exit(p, EXIT_KILLED)
        self.procs.clear()
        self._n = self._i = 0
        self.resumed = ()


SCHED = Scheduler()


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


def start_cart(ws, cart):
    """Start service `cart` of console `ws` under its consent (`prepared`)."""
    got = prepared(ws, cart)
    if got is None:
        _log(ERR, None, "service %s: no source" % (cart.get("id") or cart.get("title")))
        return None
    return SCHED.start(*got)


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
    carts = getattr(ws, "carts", None)
    for cart in (carts.all if carts is not None else ()):
        if wants_autostart(ws, cart) or cart.get("path") in SCHED.resumed:
            pid = start_cart(ws, cart)
            if pid is not None:
                out.append(pid)
    SCHED.resumed = ()
    return out
