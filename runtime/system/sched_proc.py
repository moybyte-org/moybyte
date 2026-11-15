"""The process table's row and the words the scheduler's modules share:
`Proc`, its states, kinds and exit codes, the VM's allocation count and the
OS log's lines. runtime/system/scheduler.py holds the table and the tick;
each tier's half is an object it holds, in a module of its own:
sched_python.py (Python processes), sched_lua.py (Lua services) and
sched_compiled.py (compiled services). Each imports this module, never the
scheduler's."""

try:
    import moy_log as _L
except ImportError:  # pragma: no cover - an image without the kernel
    _L = None

try:
    import moy_guard as _guard
except ImportError:  # CPython and the browser: no VM abort
    _guard = None

MOY_SERVICE_STOP_MS = 2000      # TERM to KILL: a start or stop hook's limit
_WRAP = 0x3FFFFFFF              # a counter wraps as ticks do: a small int on a board
ALLOC_WRAP = _WRAP              # `allocated()` wraps here: a difference is masked by it

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
        self.held = 0           # PSRAM it holds beside the VM's heap (scheduler.py, THE SHARES)
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
