# Map (grep -n a name to jump there):
#   Proc                     a process the scheduler has met: its state and counts
#   Scheduler                the wakes' runner, the services and their policies
#   -- the tick              Scheduler.tick, mail, the plain wakes, step
#   -- the rungs             overrun, strike, suspend, resume, a crash
#   -- services              start, hook, stop, kill, exit, pause, reset
#   -- the Lua services      _start_lua, poll, the VM's queue answered
#   -- the compiled services  _start_compiled, a native service at the poll
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

THE RUNGS are the one supervisor's (native/moy_kernel/moy_super.h), every
tier's: this module charges it a Python callback's cost, moycore a Lua
service's, and it logs each rung. Each callback runs with its process current
in the OS log (its prints, its new wakes and its events() are its own) and
under `moy_guard.call` (native/moy_kernel/modmoy_guard.c): a one-shot timer of
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

MESSAGES are the kernel's per-process event queues (native/moy_kernel/
moy_evq.h, the module `moy_evq`). `mail()` serves the queues of the VM's
processes (moy_evq.ready), each message handed to every wake of its process
on its name as `fn(sender, *args)` under the guard and the rungs, at most
MAIL_SERVE a call and a queue's MOY_EVQ_BATCH (a call is each queue's STEP,
moy_evq.h: its senders in turn, so a flood from one leaves another's message
the next served). What is left waits for the next frame's. A stepped Lua
service's run hands a batch too, and a poll that leaves it more serves it at
the next frame's start. The console calls it at the start of its frame while
`queued` is set, so a message sent by the frame's input is served before the
frame draws, and a tick that runs first serves it too. A send asks a board
for no upcall of its own. `_open` opens a process's queue at its first
`on` or `send` (moybyte's `opener`) and at a Lua service's start: its app id
(`app_of`, else its cart's) and its manifest's `receives`.

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
service's state opens the names a Lua app's does. An app talks to its
service with `moybyte.send(its own app id, name, ...)`, which the rule always
lets through within one app.

LUA SERVICES (`"runtime": "lua"`). The start hands the cart's source to
moycore's services (native/moycore/moycore_svc.h): a state of its own, on a
task of its own on a board (a P4's core 0, an S3's core 1), with what the
console hands a system process (its
`env`: the values as globals, the functions as console calls) and its app's
run, whose role names its state opens. `poll()` (THE POLL, below) steps
them where they have no task (the host, the desktop MicroPython), answers
the console calls
they wait on the VM's queue (native/moy_kernel/moy_vmq.h: `moybyte.notify`
and the `env` functions, run here on the VM's task as the service's
process, and, on a board, the role rows its task hopped), reads
each one's heap (exact) and its calls' time onto its row, and hands an end
to the same exit, log line and restart policy; a stop is its TERM there, the
C side's KILL past MOY_SERVICE_STOP_MS. A message reaches a Lua service's
event queue as any process's (a radio's change too: the radios' router is
kept while a service on a task of its own listens on "net" or "ble"), and
wakes its task; a stepped one is stepped at the next poll, or at once by the
frame start (`mail`) where the OS's own `send` posted it.

AN APP'S OWN SERVICE (`"moybyte": {"entries": {"service": file}}`,
docs/app_api_v1.md section 7). An app cart names its service's program
beside its main, and its service is the cart's own: one id, one consent,
one storage. `as_service` makes the cart a start takes from it (its type
SERVICE, its main the entry, its tier the entry's extension's: .py, .lua, a
WASI .wasm, an ELF .elf, which a native console runs as `<stem>.<chip>.elf`),
and `start_cart`, `wants_autostart` and `boot` take the app cart as they
take a service cart. `own(pid, verb)` is moybyte's `service_start`,
`service_stop` and `service_running`: the caller's own app's service,
never another's, its start or stop ASKED and run at the next tick by the
shell (`_asked`, a plain wake), so it never runs inside the caller's
callback, the VM's queue or a compiled call: the last ask wins.

COMPILED SERVICES (a native cart, `"moybyte": {"native": [chips]}`, and a
wasm cart with no source, `"runtime": "wasm"`). The start hands the cart to
its tier's runtime (`ws.runtimes["elf"]`, runtime/run/elf_run.py;
native/moy_elf/moy_os.h, A SERVICE; `ws.runtimes["wasm"]`,
native/moy_wasm/moy_wasi.h, A SERVICE): its module's `main` or `_start` on
a task of its own, a loop blocked in poll on its event descriptor, the
queue this scheduler opens with its app id and `receives`. A native one's
app calls run for a run grant of its own (`compiled_run`, a command's too)
as hops on the VM's queue, which `poll` serves; a WASI program imports
none. The poll also reaps the ones that ended (its runtime's `reap`: main
returned, a trap, or killed by its watchdog, 137) into the same exit, log
line and restart policy, reads its memory and its wakes' time onto its row
and its rungs from the one supervisor, and flags a message it sent the VM's
processes (`queued`) for the frame's start. On a board the watchdog runs
in C, a loop job of its tier's once a frame while one lives
(modmoy_wasm.c, modmoy_elf.c). A stop hangs its descriptor up, the KILL
(137) past MOY_SERVICE_STOP_MS. A console without the tier refuses one
(logged, exit 1).

THE POLL. On a board nothing polls while every service sleeps: a service's
task WANTS the VM (native/moy_kernel/moy_evq.h, THE VM'S WAKE) -- a message
it queued for a VM process, a call or hop it posted on the VM's queue, its
end, a rung the supervisor moved, a name it listens on taken or given up --
and the board's loop then runs `woken` at the frame's tail
(device/desktop_spine.py), which serves a message at once (`mail`) and runs
the poll for the rest. A console with no such loop (`kernel_wake` False:
the host, the desktop MicroPython), or a stepped Lua service, keeps the
poll a wake of the shell's every POLL_MS while one lives. Either way the
task manager's accounting reads the rows' figures (`figures`) each
METER_MS (runtime/shell/procs.py).

THE SHARES. What a service holds beside the VM's heap counts in THE SHARES'
PROCS row (native/moy_play/moy_play.h) with the running set's apps, as
`held` on its row: a compiled one's what its start took (its runtime's free
PSRAM before and after) or its linear memory or mappings once they pass it,
a Lua one's stack and heap (LUA_SVC_NEED before its start); a Python one's
world is the VM heap's own. A start that does not fit beside the running set
(`room`, the console's) is refused, logged, exit 1, and a game's admission
counts the services and names them among what to close
(runtime/run/player_fit.py's `admit`).

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

ACROSS A VM STOP (the S3s). A service on a task of its own runs on through
the stop, whoever wrote it; a Python one pauses. `pause()`, just before the
stop, TERMs every running Python service (its `moy_stop` persists what it
needs), whose paths the resume record names (`running_paths`), and closes
the device handles of the others (`_handles_end`: Python objects in the
heap the stop ends); the record also names the others as rows the kernel
keeps (`kept_rows`: pid, cart path, Lua handle). Their grants outlive the
stop (console._app_abi, moy_app_stop_end's `keep`); their timers and alarms
are the kernel's (moy_evq.h, TIMERS) and go off meanwhile, an alarm's
screen wake told at the next VM's first tick (`_E.woke`), and a message between
processes on their own tasks is delivered; a call or a role row that needs
Python waits on the VM's queue until the VM is back (moy_vmq.h, WITH NO
VM), and a message to a Python process meanwhile is not taken (its queue
closed with the VM). The console that starts after the stop takes the kept
rows back at its first frame (`take_back`, from `boot`): the same pid, its
row made again from the tier's table, its grant re-held, its waits then
answered; a row whose service ended meanwhile is reaped by that poll into
the ordinary exit and restart policy, and the autostart and the record's
Python services start after it, never a second copy (`start` finds the
running one). The Python services start again (`moy_start`), records their
sources took during the stop not delivered. `adopt` is the same row for a
service the kernel starts on demand (a C one: its runtime answers
`figures`, `reap` and `stop`), so it shows, stops and ends as any other.
A soft reset has no record: the kernel stops those services in its place
(moy_os_services_end, moy_wasm_services_end, moycore_svc_vm_stop).
"""

try:
    from ticks import _ticks_ms, _ticks_us, _ticks_diff
except ImportError:  # host: the runtime package
    from runtime.ticks import _ticks_ms, _ticks_us, _ticks_diff

try:
    from time import sleep_ms as _sleep_ms
except ImportError:  # CPython
    import time as _time

    def _sleep_ms(ms):
        _time.sleep(ms / 1000)

try:
    import moybyte as _moybyte
except ImportError:  # host: the runtime package, `import moybyte` for a service
    import sys as _sys
    from runtime import moybyte as _moybyte
    _sys.modules.setdefault("moybyte", _moybyte)

# The console calls every Lua service holds (native/moycore/moycore_svc.c's
# `moybyte` table), answered by the console's own verbs.
# GENERATED by tools/gen_os_table.py from native/os_calls.json. Do not edit.
_CALLS = {
    "moybyte.notify": _moybyte.notify,
    "moybyte.open_with": _moybyte.open_with,
    "moybyte.share": _moybyte.share,
    "moybyte.award": _moybyte.award,
    "moybyte.progress": _moybyte.progress,
    "moybyte.pick": _moybyte.pick,
    "moybyte.service_start": _moybyte.service_start,
    "moybyte.service_stop": _moybyte.service_stop,
    "moybyte.service_running": _moybyte.service_running,
}
# END GENERATED

try:
    import json as _json
except ImportError:  # pragma: no cover - every VM has it
    _json = None

try:
    import moy_evq as _E
except ImportError:  # host: the runtime package
    from runtime import moy_evq as _E

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
MOY_BG_CALLBACK_US = _E.SLICE_US   # a callback over this is an overrun (the supervisor's)
MOY_BG_ABORT_US = _E.ABORT_US      # the guard's timer: past a collection's pause
MOY_BG_STRIKES = _E.STRIKES        # strikes to suspension
MAIL_SERVE = 32                 # messages served a mail() at most
MOY_SERVICE_STOP_MS = 2000      # TERM to KILL: a start or stop hook's limit
MOY_RESTART_BACKOFF_MS = 1000  # the first restart's wait, doubled for each end in a row
MOY_RESTART_HEAL_MS = 10000     # a run this long before its end starts the count afresh
DUE_MAX = 16                    # wakes taken from the registry at a time
POLL_MS = 20                    # the Lua services' poll (a stepped tier's timer grain)
CALLS_MAX = 16                  # console calls answered a Lua service a poll
LUA_SVC_STACK = 12288           # moycore_svc.h's MOYCORE_SVC_STACK, in PSRAM on a board
LUA_SVC_NEED = 40 << 10         # a Lua service at its start: its stack and its state
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
NOTIFY = "notifications"        # the permission a notification needs

SHELL = 1
SERVICE, APP, COMMAND = "service", "app", "command"
COMPILED = {"elf": "native", "wasm": "wasm"}    # the tiers whose services start on their runtime, by their names

NEVER, ON_CRASH, ALWAYS = "never", "on-crash", "always"
POLICIES = (NEVER, ON_CRASH, ALWAYS)
RESTART_KEY = "service_restart"     # system/settings: {service id: policy}

ENTRY = "service"                   # the entry `entries` names a service by
ENTRY_TIERS = {"py": "python", "lua": "lua", "wasm": "wasm", "elf": "elf"}
ABSENT = -8                         # `own`'s answer where the app has no service

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
        self.held = 0           # PSRAM it holds beside the VM's heap (THE SHARES, below)
        self.rt = None          # a compiled service's runtime (ws.runtimes), None: none
        self.ctx = None         # a service's context: its run grant over its app's roles
        self.kernel = False     # a kernel service's row (`adopt`): the kernel logs its
                                # start and its end, and no policy restarts it


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
        self.resumed = ()           # the Python services the resume record restarts
        self.kept = ()              # what it says ran on through the stop: (pid, path, handle)
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
        self._poll = 0              # the services' poll, a wake of the shell's
        self.kernel_wake = False    # a board's loop runs `woken` (THE POLL)
        self._tasks = False         # a Lua or compiled service lives
        self.svc_mail = False       # a message sent to a stepped Lua service, not yet polled
        self.app_of = None          # fn(pid) -> the app id it runs, the console's (`attach`)
        self.cart_of = None         # fn(pid) -> the cart a run runs, the console's (`attach`)
        self.room = None            # fn(held) -> why services holding `held` do not
                                    # fit beside the running set, or None (`attach`)
        self._fns = []              # the handlers of the message being served
        self.ws = None              # the console an app's own service starts on (`attach`)
        self.asked = {}             # service id -> [its wake, "start" or "stop", its app cart]
        self.wakes.ended = self._ended
        self.wakes.opener = self._open
        self.wakes.service = self.own

    # -- the tick ---------------------------------------------------------

    def tick(self, slack_us=0):
        """The frame's background work; answers the microseconds spent."""
        f = self.frame + 1
        self.frame = f = 0 if f >= MOY_BG_SLICE_EVERY else f
        _E.timers_run()                 # the kernel's frame runs them too (moy_evq.h, TIMERS)
        _E.notes_run()                  # ...and the badges' sends that wait (moy_notes.h)
        if not self.kernel_wake and not self.wakes.queued and _E.wants() & _E.WANT_MAIL:
            self.wakes.queued = 1       # one of them reached a VM process
        woke = _E.woke()
        while woke:                     # a kernel alarm went off: the screen's wake
            if self.wakes.woke is not None:
                self.wakes.woke(woke)
            woke = _E.woke()
        w = self.wakes
        if not w.list and not w.queued:
            self._n = self._i = 0
            self.spent_us = 0
            return 0
        if w.queued or self.svc_mail:
            self.mail()                 # the frame start's work, not the wakes' budget
        if not w.list:
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
        _E.wanted(_E.WANT_MAIL)         # what this serve finds needs no wake of its own
        if self.svc_mail or (w.queued and self._poll):
            self.svc_mail = False       # a Lua service may have taken one too
            self.poll()
        w.queued = 0
        _E.step(0)                      # each VM queue hands MOY_EVQ_BATCH at most
        fns = self._fns
        for _ in range(MAIL_SERVE):
            pid = _E.ready()            # a suspended process's queue is never ready
            if not pid:
                break
            p = self.procs.get(pid)
            ev = _E.take(pid)
            if ev is None:
                continue
            app, name = ev[1], ev[2]
            try:
                args = _json.loads(ev[3])
            except ValueError:
                continue
            w.handlers(pid, name, fns)
            for fn in fns:
                self._guarded(pid, lambda: fn(app, *args))
            del fns[:]
            if p is None:
                p = self.procs.get(pid)
            if p is not None and p.kind == SERVICE and p.state != EXITED \
                    and p.state != STOPPING and not self.wakes.count(pid):
                self._exit(p, EXIT_OK)
        else:
            w.queued = 1                # more than one serve's worth: the next frame's
        if _E.step(0):
            w.queued = 1                # past a queue's batch: the next frame's
        return _ticks_diff(_ticks_us(), t0)

    def send(self, pid, name, args):
        """The OS's own message `name` with `args` to process `pid`, from
        the system, whatever its tier: False where it does not listen on
        `name`."""
        took = self.wakes.post(pid, name, args)
        p = self.procs.get(pid)
        if took and p is not None and p.native:
            self.svc_mail = True
        return took

    def _open(self, pid, vm=True):
        """Process `pid`'s event queue opened: its app id (`app_of`, else
        its cart's), its manifest's `receives`, whether it holds `alarms`
        and `notifications` (moy_evq.h's MOY_EVQ_ALARMS and MOY_EVQ_NOTIFY:
        what the kernel's alarms and notifications check) and its badges'
        goals; `vm` where the VM's task serves it (a Python process)."""
        p = self.procs.get(pid)
        cart = p.cart if p is not None else None
        if cart is None and self.cart_of is not None:
            cart = self.cart_of(pid)
        app = self.app_of(pid) if self.app_of is not None else None
        if app is None and cart is not None:
            try:
                import app_perms
            except ImportError:  # host: the runtime package
                from runtime import app_perms
            app = app_perms.app_id(cart)
        if app is None:
            app = (_L.name(pid) if _L is not None else "") or "pid%d" % pid
        recv = cart.get("receives") if cart is not None else None
        recv = "\n".join(r for r in recv if isinstance(r, str)) if isinstance(recv, list) else ""
        dev = _devices()
        _E.open(pid, app, recv, vm, dev.may(pid, _moybyte.ALARMS), dev.may(pid, NOTIFY))
        self._goals(pid, cart)

    def _goals(self, pid, cart):
        """The goals of the badges cart `cart` declares, handed to the
        kernel's queue for process `pid` (native/moy_kernel/moy_notes.h:
        a progress that reaches its goal is sent at once)."""
        for a in (cart.get("achievements") if cart is not None else None) or ():
            if isinstance(a, dict) and isinstance(a.get("id"), str):
                goal = a.get("goal")
                if type(goal) is int and goal >= 1:
                    _E.notes_goal(pid, a["id"], goal)

    def wakes_of(self, pid):
        """How many wakes process `pid` holds: its timers, readers and
        message wakes."""
        p = self.procs.get(pid)
        if p is not None and p.native:
            got = _svc().svc_info(p.native)
            return got[6] if got is not None else 0
        if p is not None and p.rt is not None:
            return 1                    # the one wait on its event descriptor
        return self.wakes.count(pid)

    def mail_of(self, pid):
        """How many messages wait on process `pid`'s event queue."""
        return _E.queued(pid)

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
                and not self.wakes_of(pid):
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
                self._charge(pid, cost, True)
        elif ladder and cost > MOY_BG_CALLBACK_US:
            self._charge(pid, cost, False)
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

    def _charge(self, pid, cost, aborted):
        """A callback of process `pid` over its slice, or aborted: charged
        to the one supervisor (moy_super.h), which logs the rung; its
        suspension holds the process's wakes unserved."""
        if pid == SHELL:
            return
        rung = _E.charge(pid, cost, aborted)
        p = self._row(pid)
        p.overruns, p.strikes = _E.rungs(pid)
        if rung == _E.SUSPENDED and p.state != EXITED:
            p.state = SUSPENDED

    def resume(self, pid):
        """A suspended process served again, its rungs from the start."""
        p = self.procs.get(pid)
        if p is None or p.state != SUSPENDED:
            return False
        _E.resume(pid)
        p.state = BACKGROUND if p.kind == SERVICE else RUNNING
        p.overruns = p.strikes = 0
        self.wakes.queued = 1           # what waited on its queue meanwhile
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
        name = _svc_name(cart)
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
        tier = app_perms.tier_of(cart)
        if tier == "elf" or (tier in COMPILED and not cart.get("src")):
            return self._start_compiled(p, ws, cart, tier)
        try:
            roles, names = self._grant(p, ws, cart)
        except (OSError, ValueError) as e:     # the grant table full, a refused kind
            _log(ERR, pid, "its grant: %s" % e)
            p.state, p.code = EXITED, EXIT_CRASHED
            return None
        if tier == "lua":
            return self._start_lua(p, lua_chunks(cart), env, roles)
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

    def _start_lua(self, p, chunks, env=None, roles=()):
        """Lua service `p` (native/moycore/moycore_svc.h) running `chunks`
        (`lua_chunks`): its own state, on a task of its own on a board and
        at this scheduler's poll elsewhere,
        handed `env` (its values as globals, its functions as console calls)
        and its app's run over `roles` (its role names, moy_app_lua.h);
        refused with no Lua runtime."""
        svc = _svc()
        why = self._no_room(LUA_SVC_NEED)
        if why is not None:
            _log(ERR, p.pid, why)
            p.state, p.code = EXITED, EXIT_CRASHED
            _end_grant(p)
            return None
        p.held = LUA_SVC_NEED
        run = None
        if p.ctx is not None:
            run = p.ws.app_abi.run(p.ctx.grant, roles)
        try:
            if svc is None:
                raise OSError(EINVAL, "no Lua runtime")
            self._open(p.pid, False)
            p.native = svc.svc_start(p.pid, p.name, chunks, _env_text(env), run)
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
        self._polling()
        self.poll()
        return p.pid if p.state != EXITED else None

    def _polling(self):
        """A Lua or compiled service lives: the poll's wake kept where no
        kernel wake runs `woken`, or a Lua service is stepped (THE POLL)."""
        self._tasks = True
        if not self._poll and (not self.kernel_wake or self._steps()):
            self._poll = self.plain_every(POLL_MS, self.poll)

    def _steps(self):
        """A Lua service lives that runs only at the poll."""
        if not _stepped():
            return False
        for p in self.procs.values():
            if p.native:
                return True
        return False

    def kernel_woken(self):
        """A board's loop runs `woken` from now on (THE POLL): the poll's
        wake goes where no service needs stepping."""
        self.kernel_wake = True
        if self._poll and not self._steps():
            self._drop_poll()

    def _drop_poll(self):
        self.wakes.cancel(self._poll)
        self._plain.pop(self._poll, None)
        self._poll = 0

    def mail_owed(self):
        """Whether the frame's start serves messages: one queued from
        Python, a Lua service's, or (where no board loop runs `woken`) one
        the kernel queued from C for a VM process, a compiled app's send on
        its own task (moy_evq.h, THE VM'S WAKE). There, too, a poll another
        task wanted with no poll's wake kept runs here: a compiled app's
        listen on a radio, which the radios' router follows."""
        w = self.wakes
        if not self.kernel_wake and not w.queued and _E.wants() & _E.WANT_MAIL:
            w.queued = 1
        if not self.kernel_wake and not self._poll and _E.wanted(_E.WANT_POLL):
            self.poll()
        return w.queued or self.svc_mail

    def woken(self):
        """THE VM'S WAKE (native/moy_kernel/moy_evq.h), at a board's frame
        tail: a message queued for a VM process served at once (`queued`),
        and the poll run for the rest a service's task wanted."""
        got = _E.wanted(_E.WANT_MAIL | _E.WANT_POLL)
        if got & _E.WANT_MAIL:
            self.wakes.queued = 1
        if got & _E.WANT_POLL:
            self.poll()

    def holding(self):
        """The PSRAM the live services hold beside the VM's heap, as
        [(pid, bytes)], those that hold none left out (THE SHARES)."""
        return [(p.pid, p.held) for p in self.procs.values()
                if p.kind == SERVICE and p.state != EXITED and p.held]

    def _no_room(self, need=0):
        """Why the live services and `need` more bytes do not fit THE
        SHARES' PROCS row beside the running set (`room`, the console's),
        or None where they fit or there is no share to count against."""
        if self.room is None:
            return None
        total = need
        for q in self.procs.values():
            if q.kind == SERVICE and q.state != EXITED:
                total += q.held
        return self.room(total)

    def figures(self):
        """Each Lua and compiled service's figures read onto its row (its
        heap or mappings, its time, its rungs), nothing reaped: the task
        manager's accounting, each METER_MS (runtime/shell/procs.py)."""
        if self._tasks:
            self._rows(_svc(), None)

    # -- the compiled services ---------------------------------------------

    def _start_compiled(self, p, ws, cart, tier):
        """Compiled service `p` (native/moy_elf/moy_os.h, A SERVICE): its
        module's main on a task of its own, blocked in poll on its event
        descriptor, its app calls answered for a run grant of its own
        (`compiled_run`) as hops on the VM's queue at the poll. Refused where
        the console carries no such tier or no module for its chip."""
        rt = (getattr(ws, "runtimes", None) or {}).get(tier)
        why = None
        if rt is None or not hasattr(rt, "service"):
            why = "a %s service does not run on this console" % COMPILED[tier]
        else:
            try:
                run = compiled_run(ws, p, cart)
            except (OSError, ValueError) as e:     # the grant table full, a refused kind
                why = "its grant: %s" % e
        if why is None:
            self._open(p.pid, False)
            mem = getattr(rt, "memory", None)
            free = mem()[0] if mem is not None else None
            why = rt.service(cart, p.pid, run)
            if why is None:
                if free is not None:
                    p.held = max(0, free - mem()[0])
                why = self._no_room()
                if why is not None:
                    rt.stop(p.pid)
        if why is not None:
            p.held = 0
            _log(ERR, p.pid, why)
            p.state, p.code = EXITED, EXIT_CRASHED
            _end_grant(p)
            self.wakes.end(p.pid)
            return None
        p.rt = rt
        self._polling()
        return p.pid

    def _compiled(self, p, ends):
        """Compiled service `p` at the poll: its end (in `ends`, {runtime id:
        {pid: code}}, each runtime reaped once a poll; None: none reaped)
        handed to `_exit`, else its figures and its rungs read onto its row.
        Whether it lives."""
        rt = p.rt
        code = None
        if ends is not None:
            got = ends.get(id(rt))
            if got is None:
                got = ends[id(rt)] = dict(rt.reap())
            code = got.get(p.pid)
        fig = rt.figures(p.pid) if code is None else None
        if fig is not None:
            p.mem = fig[0]
            p.cost_us = fig[1] & ALLOC_WRAP
            if fig[0] > p.held:
                p.held = fig[0]
            held = _E.held(p.pid)
            if p.state == BACKGROUND and held:
                p.state = SUSPENDED
            elif p.state == SUSPENDED and not held:
                p.state = BACKGROUND
            p.overruns, p.strikes = _E.rungs(p.pid)
            return True
        if ends is None:
            return True                 # its end is the poll's to reap
        p.rt = None
        if p.state != EXITED:
            self._exit(p, code if code is not None else EXIT_OK)
        return False

    def poll(self):
        """The services on tasks or steps of their own (THE POLL): the Lua
        services stepped where they have no task of their own, the VM's
        queue served (`_answer`), each one's figures read onto its row (a
        Lua heap exact, its calls' time; a compiled one's mappings and its
        wakes' time) and an end handed to `_exit`, which its restart policy
        follows. What a service sent to the VM's processes is served at the
        next frame's start. The poll's wake goes with the last service, and
        the radios' router is kept while a queue not the VM's listens on a
        radio."""
        svc = _svc()
        stepped = svc is not None and not svc.svc_tasks()
        if stepped and svc.svc_step(_ticks_ms()) == 0:
            self.svc_mail = True        # a service with more than a run's batch: the next frame's
        self._answer(svc, stepped)
        self._termed()
        live = self._rows(svc, {})
        if _E.ready():
            self.wakes.queued = 1
        self._tasks = live
        if not live and self._poll:
            self._drop_poll()
        self._radios(_E.listening("net") > 0 or _E.listening("ble") > 0)

    def _termed(self):
        """THE STOPS ASKED (native/moy_kernel/moy_evq.h): each service a
        compiled program's `service_stop` hung up on its own task, maybe
        through a VM stop, stopped as `stop` stops one, so its end is a stop
        asked and its KILL comes MOY_SERVICE_STOP_MS after."""
        pid = _E.termed()
        while pid:
            p = self.procs.get(pid)
            if p is not None and p.kind == SERVICE and p.state not in (STOPPING, EXITED):
                self.stop(pid)
            pid = _E.termed()

    def _rows(self, svc, ends):
        """Each Lua and compiled service's row read: its figures, and where
        `ends` is a dict (the poll's, {runtime id: {pid: code}}) its end
        reaped into `_exit`. Whether one lives."""
        live = False
        for pid in list(self.procs):
            p = self.procs.get(pid)
            if p is None:
                continue
            if p.rt is not None:
                live = self._compiled(p, ends) or live
                continue
            if not p.native:
                continue
            got = svc.svc_info(p.native)
            if got is None:
                p.native = 0
                continue
            state, code, heap, cpu_us = got[0], got[1], got[2], got[3]
            why = got[5]
            p.mem = heap
            p.cost_us = cpu_us & ALLOC_WRAP
            p.held = LUA_SVC_STACK + heap
            if state != SVC_EXITED or ends is None:
                live = True
                if p.state == BACKGROUND and _E.held(pid):
                    p.state = SUSPENDED
                p.overruns, p.strikes = _E.rungs(pid)
                continue
            h, p.native = p.native, 0
            svc.svc_reap(h)
            if p.state != EXITED:
                if why:
                    _log(ERR if code else INFO, p.pid, why)
                self._exit(p, code)
        return live

    def _radios(self, on):
        """The radios' router (moybyte's THE RADIOS) kept while a process
        whose queue is not the VM's (a service on a task of its own, a
        compiled app) listens on "net" or "ble"."""
        if self.wakes.radio_keep != on:
            self.wakes.radio_keep = on
            self.wakes.radio_sync()

    def _answer(self, svc, stepped):
        """The VM's queue (moy_vmq.h): the role rows the services' tasks
        hopped with run, then the console calls they wait on answered on
        this task as their processes: a console call of `_CALLS` (the
        moybyte module's own verbs), or a
        function of the `env` a start was handed. A stepped service runs
        on to its next call at once."""
        for _ in range(CALLS_MAX * 4):
            if not _E.serve():
                return
            req = _E.next_call()
            if req is None:
                return
            ticket, pid, name, args = req
            p = self.procs.get(pid)
            env = p.args[2] if p is not None and len(p.args) > 2 else None
            was = _L.current() if _L is not None else SHELL
            if _L is not None:
                _L.current(pid)
            ans = None
            try:
                fn = _CALLS.get(name)
                if fn is None and env:
                    fn = env.get(name)
                if fn is not None:
                    ans = _json.dumps(fn(*_json.loads(args)))
            except Exception as exc:  # noqa: BLE001 -- refused, the service told
                _log(ERR, pid, "%s: %s: %s" % (name, type(exc).__name__, exc))
            finally:
                if _L is not None:
                    _L.current(was)
            _E.answer(ticket, ans)
            if stepped and svc.svc_step(_ticks_ms()) == 0:
                self.svc_mail = True

    def calls(self):
        """The VM's queue served now (`_answer`): what a foreground compiled
        command posted, between its job's steps, whether or not a service's
        poll runs."""
        self._answer(None, False)

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

    def stop(self, pid, said=False):
        """The TERM: the service's `moy_stop()` under a guard of
        MOY_SERVICE_STOP_MS, the KILL if it is still running then. Either
        way the service ends. Answers whether `pid` was a live service."""
        p = self.procs.get(pid)
        if p is None or p.kind != SERVICE or p.state == EXITED:
            return False
        if not said:
            _log(INFO, pid, "stop")
        p.state = STOPPING
        if p.native:
            _svc().svc_stop(p.native)
            self.poll()
            return True
        if p.rt is not None:
            rt, p.rt = p.rt, None
            code = rt.stop(pid)
            if code == EXIT_KILLED:
                _log(ERR, pid, "killed: its stop ran past %d ms" % MOY_SERVICE_STOP_MS)
            self._exit(p, code if code is not None and code >= 0 else EXIT_OK)
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
        if not p.kernel:
            _log(INFO, p.pid, "exit %d" % code)
        if asked or p.kind != SERVICE or p.kernel or not self._unasked(p, code):
            _let_go(p)

    # -- an app's own service ----------------------------------------------

    def own(self, pid, verb):
        """moybyte's `service_<verb>` as process `pid` (AN APP'S OWN
        SERVICE): its app's service, from any process of the app. "start"
        answers its pid where it runs, else 0 with its start asked; "stop"
        1 with its stop asked where it runs or its start was asked, else 0;
        "running" its pid while it lives, else 0. ABSENT where the caller's
        app declares no service or no console is attached."""
        app = self._own_cart(pid)
        if app is None:
            return ABSENT
        name = _svc_name(app)
        p = self.find(name)
        if p is not None and p.state == STOPPING:
            p = None
        got = self.asked.get(name)
        if verb == "running":
            return p.pid if p is not None else 0
        if verb == "start":
            if p is None or got is not None:
                self._ask(name, "start", app)
            return p.pid if p is not None else 0
        if p is None and (got is None or got[1] != "start"):
            return 0
        self._ask(name, "stop", app)
        return 1

    def _own_cart(self, pid):
        """The app cart, as the shelf holds it, whose service process `pid`
        may start: its own app's, declaring one; None else."""
        p = self.procs.get(pid)
        cart = None
        if p is not None:
            cart = p.origin if p.origin is not None else p.cart
        if cart is None and self.cart_of is not None and pid != SHELL:
            cart = self.cart_of(pid)
        if cart is None or self.ws is None:
            return None
        path = cart.get("path")
        carts = getattr(self.ws, "carts", None)
        for c in (carts.all if carts is not None and path else ()):
            if c.get("path") == path:
                cart = c
                break
        if cart.get("type") == SERVICE or entry_of(cart) is None:
            return None
        return cart

    def _ask(self, name, verb, app):
        got = self.asked.get(name)
        if got is not None:
            got[1] = verb
            return
        self.asked[name] = [self._plain_wake(self.wakes.after, 0,
                                             lambda: self._asked(name)), verb, app]

    def _asked(self, name):
        """The last start or stop asked of service `name`, run as the shell."""
        got = self.asked.pop(name, None)
        if got is None:
            return
        verb, app = got[1], got[2]
        p = self.find(name)
        if verb == "stop":
            if p is not None and p.state != STOPPING:
                self.stop(p.pid)
            return
        if p is None and self.ws is not None:
            self.forgive(name)
            start_cart(self.ws, app, self)

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
        """Before a VM stop (ACROSS A VM STOP): every live Python service
        TERMed, its wakes ended; a service on a task of its own runs on, the
        device handles it holds closed (they are the VM's, dev_handles.py).
        Answers the Python services' carts' paths, which the resume record
        carries for the next VM to start again."""
        out = []
        for p in list(self.procs.values()):
            if p.kind != SERVICE or p.state == EXITED:
                continue
            if _off_vm(p):
                _handles_end(p.pid)
                continue
            path = p.cart.get("path") if p.cart else None
            if path:
                out.append(path)
            self.stop(p.pid)
        return out

    def running_paths(self):
        """The live Python services' carts' paths: the resume record's
        services to start again after a stop."""
        return [p.cart.get("path") for p in self.procs.values()
                if p.kind == SERVICE and p.state not in (EXITED, STOPPING)
                and not _off_vm(p) and p.cart and p.cart.get("path")]

    def kept_rows(self):
        """The live services on tasks of their own a VM stop leaves running,
        as the resume record carries them: [pid, its cart's path, its Lua
        handle (0 for a compiled one)]. A kernel service (`adopt` with no
        cart) is its own to find again, and is not among them."""
        return [[p.pid, p.cart.get("path"), p.native] for p in self.procs.values()
                if p.kind == SERVICE and p.state not in (EXITED, STOPPING) and _off_vm(p)
                and p.cart and p.cart.get("path")]

    def adopt(self, pid, name, rt=None, cart=None, native=0):
        """A row for service `pid`, running on a task of its own, that this
        VM's scheduler did not start: one a VM stop left running (`take_back`,
        its cart and its Lua `native` handle or compiled runtime `rt`), or a
        kernel service with no cart (its runtime `rt`: `figures(pid)` while it
        lives, `reap()` its end, `stop(pid)`), which starts on its first
        request and ends when its work is done, and logs both itself. Its
        figures are read and its end reaped at the poll as a started one's
        are; a kernel service has no restart policy. Answers its Proc, or the
        live row it already has."""
        p = self.procs.get(pid)
        if p is not None and p.state != EXITED:
            return p
        p = self.procs[pid] = Proc(pid, SERVICE, name, cart)
        p.started = _ticks_ms()
        p.native = native
        p.rt = rt
        p.kernel = cart is None
        if cart is not None:
            _log(INFO, pid, "taken back", name)
        self._polling()
        return p

    def take_back(self, ws):
        """The services the resume record says ran on through the VM stop,
        each found again in its tier's table (the kernel's, which outlived
        the stop) and given its row, its grant (the same row of the app ABI,
        which the start kept for it) and what its console calls are answered
        with. One that ended during the stop gets its row and its end, which
        its restart policy follows; one a compiled program stopped during it
        (THE STOPS ASKED) is a stop asked, ended now or by `stop`. Answers
        the pids taken back."""
        try:
            import app_perms
        except ImportError:  # host: the runtime package
            from runtime import app_perms
        out = []
        kept, self.kept = self.kept, ()
        carts = getattr(ws, "carts", None)
        shelf = {c.get("path"): c for c in (carts.all if carts is not None else ())}
        attach(ws, self)
        ends = {}
        asked = []
        pid = _E.termed()
        while pid:
            asked.append(pid)
            pid = _E.termed()
        for pid, path, native in kept:
            cart = shelf.get(path)
            got = prepared(ws, cart, True) if cart is not None else None
            if got is None:
                _log(ERR, pid, "taken back: its cart is gone")
                continue
            run, rec, have, env = got
            tier = app_perms.tier_of(run)
            rt = None if native else (getattr(ws, "runtimes", None) or {}).get(tier)
            if not native and rt is None:
                _log(ERR, pid, "taken back: a %s service does not run here" % tier)
                continue
            p = self.adopt(pid, _svc_name(run), rt, run, native)
            if pid in asked:
                _log(INFO, pid, "stop")
                p.state = STOPPING
            p.granted = tuple(app_perms.check(run, rec, have)[0])
            p.ws, p.origin, p.args = ws, cart, (rec, have, env)
            try:
                if native:
                    self._grant(p, ws, run)
                else:
                    compiled_run(ws, p, run)
            except (OSError, ValueError) as e:     # the grant table full, a refused kind
                _log(ERR, pid, "its grant: %s" % e)
            p.held = LUA_SVC_NEED if native else 0
            if native:
                self._rows(_svc(), ends)
                if not p.native and p.state != EXITED:
                    _log(ERR, pid, "taken back: its tier has no such service")
                    self._exit(p, EXIT_CRASHED)
            elif not self._compiled(p, ends):
                continue
            if p.state != EXITED:
                out.append(pid)
        for pid in asked:
            p = self.procs.get(pid)
            if p is not None and p.state == STOPPING:
                p.state = BACKGROUND
                self.stop(pid, said=True)
        return out

    def reset(self):
        """Every service ended and every row forgotten (a test's teardown)."""
        for name in list(self.pending):
            self.forgive(name)
        svc = _svc()
        if svc is not None and not svc.svc_tasks():
            svc.svc_reset()             # its states close under their grants
        for p in list(self.procs.values()):
            if p.rt is not None:
                p.rt.stop(p.pid)
                p.rt = None
            if p.kind == SERVICE and p.state != EXITED:
                p.state = STOPPING
                self._exit(p, EXIT_KILLED)
        if self._poll:
            self._drop_poll()
        self._tasks = False
        self._radios(False)
        self.procs.clear()
        self.down.clear()
        self.svc_mail = False
        self.store = None
        self.ws = None
        for got in self.asked.values():
            self.plain_cancel(got[0])
        self.asked.clear()
        self._n = self._i = 0
        self.resumed = ()
        self.kept = ()


def kept_of(rec):
    """The resume record's (a dict) services the stop kept running, as
    (pid, path, Lua handle) rows; a malformed one is left out."""
    out = []
    for row in (rec.get("keep") or ()) if isinstance(rec, dict) else ():
        if (isinstance(row, list) and len(row) == 3 and isinstance(row[0], int)
                and isinstance(row[1], str) and isinstance(row[2], int)):
            out.append((row[0], row[1], row[2]))
    return tuple(out)


def kept_pids():
    """The pids the kernel's resume record says a VM stop kept running,
    read at a return start before the console takes them back (its app
    ABI's start keeps their grants); () off a board or with no record."""
    try:
        import moy_kernel
    except ImportError:
        return ()
    get = getattr(moy_kernel, "resume", None)
    text = get() if get is not None else None
    if not text or _json is None:
        return ()
    try:
        rec = _json.loads(text)
    except ValueError:
        return ()
    return tuple(r[0] for r in kept_of(rec))


def _off_vm(p):
    """A service on a task of its own (a Lua one, a compiled one, a kernel
    one): the VM's heap holds only its row."""
    return bool(p.native) or p.rt is not None


def _handles_end(pid):
    """The device handles process `pid` holds closed (dev_handles.py: they
    are Python objects in the VM's heap)."""
    try:
        import dev_handles
    except ImportError:  # host: the runtime package
        try:
            from runtime import dev_handles
        except ImportError:
            return
    dev_handles.sweep(pid)


def _devices():
    try:
        import devices
    except ImportError:  # host: the runtime package
        from runtime import devices
    return devices


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


def _stepped():
    """Whether the Lua services run at the poll, with no task of their own."""
    svc = _svc()
    return svc is not None and not svc.svc_tasks()


def src_of(cart):
    return cart.get("src") or ""


def lua_chunks(cart):
    """Lua service `cart`'s scripts as moycore's svc_start takes them, in
    load order: (text, chunkname) pairs, its main named after the service
    (None) and the others after their files, as a window's are
    (`lua_ext.cart_chunks`)."""
    return ([(s, "@" + n) for n, s in cart.get("src_before") or ()]
            + [(cart.get("src") or "", None)]
            + [(s, "@" + n) for n, s in cart.get("src_after") or ()])


def _svc_name(cart):
    """The name a service row of `cart` runs under: its cart's id."""
    return cart.get("id") or cart.get("title") or "service"


def entry_of(cart):
    """The service entry app `cart` declares (`"moybyte": {"entries":
    {"service": file}}`): (its file, its tier), None where it declares
    none or one that does not read: a file in the cart's folder, never
    above it, of a tier ENTRY_TIERS names."""
    got = cart.get("entries") if cart else None
    f = got.get(ENTRY) if isinstance(got, dict) else None
    if not isinstance(f, str) or not f or f[0] == "/" or ".." in f.split("/"):
        return None
    tier = ENTRY_TIERS.get(f.rsplit(".", 1)[-1]) if "." in f else None
    return (f, tier) if tier is not None else None


def _elf_chips(path, stem):
    """The chips app folder `path` carries an ELF module `<stem>.<chip>.elf` for."""
    import os
    head = stem + "."
    try:
        names = os.listdir(path)
    except OSError:
        return []
    return [n[len(head):-4] for n in sorted(names)
            if n.startswith(head) and n.endswith(".elf") and "." not in n[len(head):-4]]


def as_service(cart):
    """App `cart`'s service as a start takes it (AN APP'S OWN SERVICE): the
    cart with its type SERVICE, its main the entry and its tier the entry's,
    its source the entry's text (a Python or Lua one, read from its folder),
    `entry` ENTRY, and its id, title, permissions, `receives` and path the
    app's. A Lua app's Lua entry runs after the app's SHARED scripts, the
    ones its `sources` lists before its main (`src_before`, the entry itself
    not among them), as its window does. None where it declares no service
    or a Python or Lua entry does not read."""
    got = entry_of(cart)
    if got is None:
        return None
    f, tier = got
    run = dict(cart)
    run["type"] = SERVICE
    run["entry"] = ENTRY                # an app's own service, not a service cart
    run["main"] = f
    shared = cart.get("src_before") or ()
    run["src_before"] = ([(n, s) for n, s in shared if n != f]
                         if tier == "lua" and cart.get("runtime") == "lua" else [])
    run["src_after"] = []
    run["native"] = None
    if tier == "elf":
        run["native"] = _elf_chips(cart.get("path") or "", f[:-4]) or None
        run["src"] = ""
        return run if run["native"] else None
    run["runtime"] = tier
    if tier in COMPILED:
        run["src"] = ""
        return run
    try:
        with open("%s/%s" % (cart.get("path"), f)) as fh:
            run["src"] = fh.read()
    except (OSError, TypeError):
        return None
    return run


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


def tick(slack_us=0):
    return SCHED.tick(slack_us)


def prepared(ws, cart, service=False):
    """What a launch of `cart` on console `ws` runs under: (the cart with
    its source, read from the store when the shelf holds it slim, its
    consent record as a launch reads it (`Consent.for_run`), the console's
    services, its namespace's names: a
    system process's, `ws.service_env`), or None where it has no source
    (a compiled cart's is its module, which its runtime reads). Where
    `service`, an app cart's is its own service's (`as_service`)."""
    carts = getattr(ws, "carts", None)
    full = carts.rehydrate(cart) if carts is not None else cart
    run = dict(full)
    if carts is not None:
        carts.reslim(cart)
    if service and run.get("type") != SERVICE:
        run = as_service(run)
        if run is None:
            return None
    try:
        import app_perms
    except ImportError:  # host: the runtime package
        from runtime import app_perms
    if run.get("src") is None and app_perms.tier_of(run) not in COMPILED:
        return None
    consent = getattr(ws, "consent", None)
    if consent is not None:
        rec = consent.for_run(run)
        have = app_perms.services(ws)
    else:
        rec, have = None, ()
    env_of = getattr(ws, "service_env", None)
    return run, rec, have, env_of(run) if env_of is not None else None


def compiled_run(ws, p, cart):
    """Compiled process `p`'s app run (native/moy_app/moy_app_run.h): a run
    grant of its own, owned by its pid, over the roles its granted
    permissions name, held on its row (`p.ctx`) until its end, so its app
    calls (docs/app_api_v1.md's call table) are its app's, a command's
    (runtime/shell/sh_door.py) and a service's alike; the run's bytes for
    its runtime, or None on a console with no app ABI."""
    factory = getattr(ws, "app_context", None)
    app = getattr(ws, "app_abi", None)
    if factory is None or app is None:
        return None
    try:
        import moy_app
    except ImportError:  # host: the runtime package
        from runtime import moy_app
    roles, kind = moy_app.policy(p.granted)
    app_id = moy_app.id_for(cart.get("id"), cart.get("title"))
    p.ctx = factory(app_id, roles, app_id, run=True, kind=kind, owner=p.pid)
    devs = getattr(app, "grant_devs", None)
    if devs is not None and p.granted:
        try:
            import devices
        except ImportError:  # host: the runtime package
            from runtime import devices
        # its device handles' (moy_dev.h), and the folder they read its files in
        devs(p.ctx.grant, tuple(p.granted), devices.folder(cart))
    return app.run(p.ctx.grant, roles)


def attach(ws, sched=None):
    """The console whose settings hold the restart policies and whose
    processes' app ids and carts stamp their event queues (`_open`)."""
    sched = sched or SCHED
    sched.ws = ws
    sched.store = getattr(ws, "system", None)
    procs = getattr(ws, "procs", None)
    sched.app_of = getattr(procs, "app_id", None)
    sched.room = getattr(procs, "room", None)
    who = getattr(procs, "who", None)
    sched.cart_of = (lambda pid: who(pid)[1]) if who is not None else None


def start_cart(ws, cart, sched=None):
    """Start service `cart` of console `ws` under its consent (`prepared`)
    on `sched` (SCHED)."""
    sched = sched or SCHED
    attach(ws, sched)
    got = prepared(ws, cart, True)
    if got is None:
        _log(ERR, None, "service %s: no source" % (cart.get("id") or cart.get("title")))
        return None
    run, rec, have, env = got
    return sched.start(run, rec, have, env, ws, cart)


def wants_autostart(ws, cart):
    """A service cart, or an app cart declaring its own service, asking for
    `autostart`, its consent not turned off."""
    if cart.get("type") != SERVICE and entry_of(cart) is None:
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
    """The console's services at its first frame: those a VM stop left
    running taken back first (`take_back`), then the autostart ones and the
    Python ones the resume record names started; a service taken back is
    not started again. Answers the pids started and taken back."""
    attach(ws)
    out = SCHED.take_back(ws) if SCHED.kept else []
    carts = getattr(ws, "carts", None)
    for cart in (carts.all if carts is not None else ()):
        if wants_autostart(ws, cart) or cart.get("path") in SCHED.resumed:
            pid = start_cart(ws, cart)
            if pid is not None:
                out.append(pid)
    SCHED.resumed = ()
    return out
