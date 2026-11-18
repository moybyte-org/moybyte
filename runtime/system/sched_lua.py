"""The scheduler's Lua services (`SCHED.lua`; the process table, the
tick and THE POLL are runtime/system/scheduler.py's).

LUA SERVICES (`"runtime": "lua"`). The start hands the cart's source to
moycore's services (native/moycore/moycore_svc.h): a state of its own, on a
task of its own on a board (a P4's core 0, an S3's core 1), with what the
console hands a system process (its
`env`: the values as globals, the functions as console calls) and its app's
run, whose role names its state opens. The scheduler's `poll()` steps
them where they have no task (the host, the desktop MicroPython), answers
the console calls
they wait on the VM's queue (native/moy_kernel/moy_vmq.h: `moybyte.notify`
and the `env` functions, run there on the VM's task as the service's
process, and, on a board, the role rows its task hopped), reads
each one's heap (exact) and its calls' time onto its row, and hands an end
to the same exit, log line and restart policy; a stop is its TERM there, the
C side's KILL past MOY_SERVICE_STOP_MS. A message reaches a Lua service's
event queue as any process's (a radio's change too: the radios' router is
kept while a service on a task of its own listens on "net" or "ble"), and
wakes its task; a stepped one is stepped at the next poll, or at once by the
frame start (`mail`) where the OS's own `send` posted it.
"""

import moy_evq as _E
from sched_proc import (BACKGROUND, SUSPENDED, EXITED, EXIT_CRASHED, ALLOC_WRAP,
                        _end_grant, _log, INFO, ERR)

try:
    import json as _json
except ImportError:  # pragma: no cover - every VM has it
    _json = None

LUA_SVC_STACK = 12288           # moycore_svc.h's MOYCORE_SVC_STACK, in PSRAM on a board
LUA_SVC_NEED = 40 << 10         # a Lua service at its start: its stack and its state
SVC_EXITED = 4                  # moycore_svc.h's MOYCORE_SVC_EXITED
EINVAL = 22


def runtime():
    """The Lua tier's services (moycore's svc_* names), or None."""
    try:
        import moycore
    except ImportError:
        return None
    return moycore if hasattr(moycore, "svc_start") else None


def lua_chunks(cart):
    """Lua service `cart`'s scripts as moycore's svc_start takes them, in
    load order: (text, chunkname) pairs, its main named after the service
    (None) and the others after their files, as a window's are
    (`lua_ext.cart_chunks`)."""
    return ([(s, "@" + n) for n, s in cart.get("src_before") or ()]
            + [(cart.get("src") or "", None)]
            + [(s, "@" + n) for n, s in cart.get("src_after") or ()])


def env_text(env):
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


class LuaServices:
    """The Lua services of scheduler `sched`, each a row whose `native` is
    its moycore handle."""

    def __init__(self, sched):
        self.sched = sched

    def start(self, p, chunks, env=None, roles=()):
        """Lua service `p` (native/moycore/moycore_svc.h) running `chunks`
        (`lua_chunks`): its own state, on a task of its own on a board and
        at the scheduler's poll elsewhere,
        handed `env` (its values as globals, its functions as console calls)
        and its app's run over `roles` (its role names, moy_app_lua.h);
        refused with no Lua runtime."""
        s = self.sched
        svc = runtime()
        why = s._no_room(LUA_SVC_NEED)
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
            s._open(p.pid, False)
            p.native = svc.svc_start(p.pid, p.name, chunks, env_text(env), run)
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
        s._polling()
        s.poll()
        return p.pid if p.state != EXITED else None

    def stepping(self):
        """moycore where the Lua services run at the poll, with no task of
        their own; None else."""
        svc = runtime()
        return svc if svc is not None and not svc.svc_tasks() else None

    def steps(self):
        """A Lua service lives that runs only at the poll."""
        if self.stepping() is None:
            return False
        for p in self.sched.procs.values():
            if p.native:
                return True
        return False

    def step(self, svc):
        """The stepped services run on to their next call; one left more
        than a run's batch of messages is the next frame's."""
        if svc.svc_step() == 0:
            self.sched.svc_mail = True

    def wakes(self, p):
        """How many wakes Lua service `p` holds."""
        got = runtime().svc_info(p.native)
        return got[6] if got is not None else 0

    def row(self, p, ends):
        """Lua service `p` at the poll: its heap, its calls' time and its
        rungs read onto its row, and where `ends` is not None its end
        reaped into the scheduler's exit. Whether it lives."""
        svc = runtime()
        got = svc.svc_info(p.native)
        if got is None:
            p.native = 0
            return False
        state, code, heap, cpu_us = got[0], got[1], got[2], got[3]
        why = got[5]
        p.mem = heap
        p.cost_us = cpu_us & ALLOC_WRAP
        p.held = LUA_SVC_STACK + heap
        if state != SVC_EXITED or ends is None:
            if p.state == BACKGROUND and _E.held(p.pid):
                p.state = SUSPENDED
            p.overruns, p.strikes = _E.rungs(p.pid)
            return True
        h, p.native = p.native, 0
        svc.svc_reap(h)
        if p.state != EXITED:
            if why:
                _log(ERR if code else INFO, p.pid, why)
            self.sched._exit(p, code)
        return False

    def stop(self, p):
        """Lua service `p`'s TERM, handed to its state; its end is the
        poll's."""
        runtime().svc_stop(p.native)
        self.sched.poll()

    def reset(self):
        """Every stepped state closed under its grant (a test's teardown)."""
        svc = self.stepping()
        if svc is not None:
            svc.svc_reset()
