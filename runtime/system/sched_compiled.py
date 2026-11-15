"""The scheduler's compiled services (`SCHED.compiled`; the process table,
the tick and THE POLL are runtime/system/scheduler.py's).

COMPILED SERVICES (a native cart, `"moybyte": {"native": [chips]}`, and a
wasm cart with no source, `"runtime": "wasm"`). The start hands the cart to
its tier's runtime (`ws.runtimes["elf"]`, runtime/run/elf_run.py;
native/moy_elf/moy_os.h, A SERVICE; `ws.runtimes["wasm"]`,
native/moy_wasm/moy_wasi.h, A SERVICE): its module's `main` or `_start` on
a task of its own, a loop blocked in poll on its event descriptor, the
queue the scheduler opens with its app id and `receives`. A native one's
app calls run for a run grant of its own (`compiled_run`, a command's too)
as hops on the VM's queue, which the scheduler's `poll` serves; a WASI
program imports none. The poll also reaps the ones that ended (its
runtime's `reap`: main returned, a trap, or killed by its watchdog, 137)
into the same exit, log line and restart policy, reads its memory and its
wakes' time onto its row and its rungs from the one supervisor, and flags a
message it sent the VM's processes (`queued`) for the frame's start. On a
board the watchdog runs in C, a loop job of its tier's once a frame while
one lives (modmoy_wasm.c, modmoy_elf.c). A stop hangs its descriptor up,
the KILL (137) past MOY_SERVICE_STOP_MS. A console without the tier refuses
one (logged, exit 1).
"""

import moy_evq as _E
import sched_proc as _P
from sched_proc import (BACKGROUND, SUSPENDED, EXITED, EXIT_OK, EXIT_CRASHED, EXIT_KILLED,
                        ALLOC_WRAP, _end_grant, _log, ERR)

COMPILED = {"elf": "native", "wasm": "wasm"}    # the tiers whose services start on their runtime, by their names


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
    import moy_app
    roles, kind = moy_app.policy(p.granted)
    app_id = moy_app.id_for(cart.get("id"), cart.get("title"))
    p.ctx = factory(app_id, roles, app_id, run=True, kind=kind, owner=p.pid)
    devs = getattr(app, "grant_devs", None)
    if devs is not None and p.granted:
        import devices
        # its device handles' (moy_dev.h), and the folder they read its files in
        devs(p.ctx.grant, tuple(p.granted), devices.folder(cart))
    return app.run(p.ctx.grant, roles)


class CompiledServices:
    """The compiled services of scheduler `sched`, each a row whose `rt`
    is its runtime."""

    def __init__(self, sched):
        self.sched = sched

    def start(self, p, ws, cart, tier):
        """Compiled service `p` (native/moy_elf/moy_os.h, A SERVICE): its
        module's main on a task of its own, blocked in poll on its event
        descriptor, its app calls answered for a run grant of its own
        (`compiled_run`) as hops on the VM's queue at the poll. Refused where
        the console carries no such tier or no module for its chip."""
        s = self.sched
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
            s._open(p.pid, False)
            mem = getattr(rt, "memory", None)
            free = mem()[0] if mem is not None else None
            why = rt.service(cart, p.pid, run)
            if why is None:
                if free is not None:
                    p.held = max(0, free - mem()[0])
                why = s._no_room()
                if why is not None:
                    rt.stop(p.pid)
        if why is not None:
            p.held = 0
            _log(ERR, p.pid, why)
            p.state, p.code = EXITED, EXIT_CRASHED
            _end_grant(p)
            s.wakes.end(p.pid)
            return None
        p.rt = rt
        s._polling()
        return p.pid

    def row(self, p, ends):
        """Compiled service `p` at the poll: its end (in `ends`, {runtime id:
        {pid: code}}, each runtime reaped once a poll; None: none reaped)
        handed to the scheduler's exit, else its figures and its rungs read
        onto its row. Whether it lives."""
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
            self.sched._exit(p, code if code is not None else EXIT_OK)
        return False

    def stop(self, p):
        """Compiled service `p`'s TERM: its descriptor hung up, the KILL past
        MOY_SERVICE_STOP_MS; its end now."""
        rt, p.rt = p.rt, None
        code = rt.stop(p.pid)
        if code == EXIT_KILLED:
            _log(ERR, p.pid, "killed: its stop ran past %d ms" % _P.MOY_SERVICE_STOP_MS)
        self.sched._exit(p, code if code is not None and code >= 0 else EXIT_OK)
