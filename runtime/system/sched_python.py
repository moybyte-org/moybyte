"""The scheduler's Python processes (`SCHED.python`; the process table and
the tick are runtime/system/scheduler.py's): each wake of a Python process
the tick runs, each message `mail()` serves it and each asyncio step, run as
its process under the guard and the rungs; a Python service's start (its
main, `moy_start`), its TERM (`moy_stop`) and its hooks.

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

A callback's exception is a crash: its traceback goes to the OS log at crit
and a service exits with EXIT_CRASHED; a wake of any other process is
cancelled. A SystemExit from a service's callback is its exit, with its code.
"""

from ticks import _ticks_us, _ticks_diff

import moybyte as _moybyte
import moy_evq as _E
import sched_proc as _P
from sched_proc import (SHELL, SERVICE, APP, SUSPENDED, STOPPING, EXITED, EXIT_OK,
                        EXIT_CRASHED, _WRAP, Proc, allocated)

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

MOY_BG_CALLBACK_US = _E.SLICE_US   # a callback over this is an overrun (the supervisor's)
MOY_BG_ABORT_US = _E.ABORT_US      # the guard's timer: past a collection's pause
MOY_BG_STRIKES = _E.STRIKES        # strikes to suspension
MAIL_SERVE = 32                 # messages served a mail() at most

ABORTED = _guard.ABORTED if _guard is not None else object()

_OWNER, _HOW, _ARG, _TIMER = _moybyte._OWNER, _moybyte._HOW, _moybyte._ARG, _moybyte._TIMER


class PythonProcs:
    """The Python processes of scheduler `sched`: their callbacks, under its
    guard, and its Python services' starts, stops and hooks."""

    ABORTED = ABORTED

    def __init__(self, sched):
        self.sched = sched
        self._wid = 0
        self._one = self._call_wid      # bound once: the guard's callable
        self._fns = []                  # the handlers of the message being served

    def _call_wid(self):
        self.sched.wakes.call(self._wid)

    def run(self, w, wid):
        """Wake `wid` (its row `w`) of a Python process run as it, under the
        guard and the rungs; a service left with no wakes has exited (0)."""
        s = self.sched
        pid = w[_OWNER]
        p = s.procs.get(pid)
        if p is not None and (p.state == SUSPENDED or p.state == EXITED):
            return
        self._wid = wid
        self._guarded(pid, self._one, w)
        if p is None:
            p = s.procs.get(pid)
        if p is not None and p.kind == SERVICE and p.state != EXITED \
                and not s.wakes_of(pid):
            s._exit(p, EXIT_OK)

    def serve(self):
        """The VM processes' messages off their queues, MAIL_SERVE at most,
        each handed to every wake of its process on its name as
        `fn(sender, *args)` under the guard and the rungs. Answers whether a
        serve's worth was taken, so more may wait."""
        s = self.sched
        w = s.wakes
        fns = self._fns
        for _ in range(MAIL_SERVE):
            pid = _E.ready()            # a suspended process's queue is never ready
            if not pid:
                return False
            p = s.procs.get(pid)
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
                p = s.procs.get(pid)
            if p is not None and p.kind == SERVICE and p.state != EXITED \
                    and p.state != STOPPING and not w.count(pid):
                s._exit(p, EXIT_OK)
        return True

    def step(self, pid, fn):
        """An asyncio task's step (runtime/system/aio.py): `fn()` as process
        `pid` under the guard and the rungs, counted as its callback. Answers
        ABORTED or fn's result; fn's exception, its end or its error, is the
        caller's once it is counted."""
        return self._guarded(pid, fn, None, None, True, True)

    def settle(self, pid):
        """A live service holding no wake and no task has exited (0)."""
        s = self.sched
        p = s.procs.get(pid)
        if p is not None and p.kind == SERVICE and p.state != EXITED \
                and p.state != STOPPING and not s.wakes.count(pid):
            s._exit(p, EXIT_OK)

    def crash(self, pid, exc):
        """Process `pid` crashed with `exc` outside a wake (an asyncio task):
        logged, and a service exits with its code."""
        self._crash(pid, exc, None)

    def hook(self, pid, name, *args):
        """Run live service `pid`'s hook `name(*args)` (`moy_action`, a
        notification acted on) as a callback: under the guard and the rungs.
        False where the service is gone or defines no such hook."""
        s = self.sched
        p = s.procs.get(pid)
        if p is None or p.kind != SERVICE or p.state == EXITED or p.ns is None:
            return False
        fn = p.ns.get(name)
        if fn is None:
            return False
        self._guarded(pid, lambda: fn(*args))
        if p.state != EXITED and not s.wakes.count(pid):
            s._exit(p, EXIT_OK)
        return True

    def start(self, p, cart, rec, env, names, absent):
        """Python service `p` started: its main run in a namespace of
        `moybyte`, `env`, its roles' `names` and None for each `absent` name,
        then its `moy_start()`, both under a guard of MOY_SERVICE_STOP_MS.
        Answers its pid, or None where it ended in its start."""
        import app_perms
        s = self.sched
        if "asyncio" in (cart.get("src") or ""):
            import aio
            aio.install(s)
        ns = {"__name__": "__main__", "moybyte": _moybyte}
        if env:
            ns.update(env)
        ns.update(names)
        for n in app_perms.absent_names(absent, rec):
            ns[n] = None
        p.ns = ns
        src = cart.get("src") or ""

        def body():
            code = compile(src, "<%s>" % p.name, "exec")
            a0 = allocated()
            exec(code, ns)
            hook = ns.get("moy_start")
            if hook is not None:
                hook()
            a1 = allocated()
            if a0 is not None and a1 is not None:
                p.mem = (a1 - a0) & _WRAP
        r = self._guarded(p.pid, body, None, _P.MOY_SERVICE_STOP_MS * 1000, False)
        if p.state == EXITED:
            return None
        if r is ABORTED:
            s._kill(p, "its start ran past %d ms" % _P.MOY_SERVICE_STOP_MS)
            return None
        if not s.wakes.count(p.pid):
            s._exit(p, EXIT_OK)
            return None
        return p.pid

    def stop(self, p):
        """Python service `p`'s TERM: its `moy_stop()` under a guard of
        MOY_SERVICE_STOP_MS, the KILL if it is still running then."""
        s = self.sched
        hook = p.ns.get("moy_stop") if p.ns is not None else None
        r = None
        if hook is not None:
            r = self._guarded(p.pid, hook, None, _P.MOY_SERVICE_STOP_MS * 1000, False)
        if p.state == EXITED:
            return
        if r is ABORTED:
            s._kill(p, "its stop ran past %d ms" % _P.MOY_SERVICE_STOP_MS)
        else:
            s._exit(p, EXIT_OK)

    def _guarded(self, pid, fn, w=None, budget=None, ladder=True, raises=False):
        """`fn()` as process `pid` under a guard of `budget` microseconds,
        the rungs counted where `ladder`. Answers ABORTED, None for a crash,
        else fn's result; where `raises`, fn's exception is re-raised once
        counted instead."""
        if budget is None:
            budget = MOY_BG_ABORT_US
        s = self.sched
        wakes = s.wakes
        was_reader = wakes.cur[0]
        was_pid = _L.current() if _L is not None else SHELL
        if _L is not None:
            _L.current(pid)
        g = s.guard
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
        p = s.procs.get(pid)
        if p is not None:
            p.calls = (p.calls + 1) & _WRAP
            p.cost_us = (p.cost_us + cost) & _WRAP
        if r is ABORTED:
            wakes.cur[0] = was_reader       # the abort skipped call()'s finally
            if w is not None and w[_HOW] == _TIMER and w[_ARG]:
                wakes.cancel(w[0])
            if ladder:
                self._charge(pid, cost, True)
        elif ladder and cost > MOY_BG_CALLBACK_US:
            self._charge(pid, cost, False)
        if err is not None:
            raise err
        return r

    def _charge(self, pid, cost, aborted):
        """A callback of process `pid` over its slice, or aborted: charged
        to the one supervisor (moy_super.h), which logs the rung; its
        suspension holds the process's wakes unserved."""
        if pid == SHELL:
            return
        rung = _E.charge(pid, cost, aborted)
        procs = self.sched.procs
        p = procs.get(pid)
        if p is None:
            name = _L.name(pid) if _L is not None else "pid %d" % pid
            p = procs[pid] = Proc(pid, APP, name)
        p.overruns, p.strikes = _E.rungs(pid)
        if rung == _E.SUSPENDED and p.state != EXITED:
            p.state = SUSPENDED

    def _crash(self, pid, exc, w):
        s = self.sched
        p = s.procs.get(pid)
        if isinstance(exc, SystemExit):
            code = exc.args[0] if exc.args else 0
            code = code if isinstance(code, int) else EXIT_CRASHED
            if p is not None and p.kind == SERVICE:
                s._exit(p, code)
            elif w is not None:
                s.wakes.cancel(w[0])
            return
        from os_log import crashed
        crashed(pid, exc)
        if p is not None and p.kind == SERVICE:
            s._exit(p, EXIT_CRASHED)
        elif w is not None:
            s.wakes.cancel(w[0])
