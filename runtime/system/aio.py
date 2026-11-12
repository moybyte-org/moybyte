"""The shell's one asyncio loop, run on the cooperative scheduler
(docs/os_2026-11/processes.md section 3; runtime/system/scheduler.py).

MicroPython's `asyncio` is the API and there is one loop, the shell's. No
process runs a loop of its own: `asyncio.run(coro)`, `create_task(coro)` and a
Loop's `run_until_complete(coro)` put the task on the shell's loop and answer
its Task at once, and `run_forever()` returns at once, so a service's main
ends with `asyncio.run(main())` and the console goes on. `asyncio.sleep(s)`
and `asyncio.sleep_ms(ms)` are timers, and a stream (`open_connection`,
`start_server`, `StreamReader` over a socket) waits on the poller.

A TASK IS ITS PROCESS'S. A task belongs to the process current when it was
made (the OS log's `current`), so a task a task makes is its maker's. Each
step of it (one resume of its coroutine, up to its next await) is that
process's callback: `Scheduler.step` runs it as the process, under the guard,
its cost in the process's CPU time and every overrun rung counted. An aborted
step ends its task (CancelledError to whatever awaits it). A step while the
process is suspended is put off. A process holds its live tasks as it holds
its wakes (`moybyte.WAKES.count`), so a service runs while it has a task; its
end (`moybyte.end`) cancels them: each gets its CancelledError once to clean
up in, and is closed after it. The exception of a task nothing awaits is its
process's crash; a SystemExit in a step is its exit.

THE LOOP'S WAKE is the shell's: one `every` wake the scheduler runs plainly
(each step carries its own guard), registered while any task lives and gone
with the last, so a console with no task pays nothing. It is due when the
earliest timer is, every frame while a stream waits on the poller, and at
least every POLL_MS (a task woken from outside a step, a lock or a cancel);
an Event's set and a new task make it due at once. A pass runs the due steps
while the tick's budget lasts, the first whatever the budget.

THE TWO FACES. On MicroPython (the boards, the unix builds) this drives
MicroPython's own loop (`asyncio.core.run_until_complete`) a pass at a time
through its IO queue's wait: a pass that steps nothing allocates nothing,
and one that steps a task allocates the loop's own besides the task's (two
tuples, and the exception that ends the pass).
On CPython (the host simulator, the tests) it drives CPython's asyncio one
iteration a pass, the whole iteration under one budget, and adds
`asyncio.sleep_ms`; a task's exception counts as unawaited when nothing has
awaited it by the step that raised it. The browser's VM has no asyncio:
`install` answers False.

  install(sched)   the loop on scheduler `sched`; False where the VM has no
                   asyncio. The scheduler installs it at the first start of a
                   service whose source names asyncio
  count(pid)       the live tasks of process `pid`
  TASKS            every live task's owner, oldest first
"""

import sys as _sys

from ticks import _ticks_ms, _ticks_diff, _ticks_add
import moybyte as _moybyte

try:
    import moy_log as _L
except ImportError:  # pragma: no cover - an image without the kernel
    _L = None

try:
    import asyncio
except ImportError:  # the browser's VM
    asyncio = None

try:
    from collections.abc import Coroutine as _Base   # CPython's Task takes a coroutine
except ImportError:  # MicroPython's takes anything with send
    _Base = object

SHELL = 1
POLL_MS = 50                    # the loop's wake, at least this often while a task lives
_MP = _sys.implementation.name == "micropython"
_AT = _moybyte._AT

TASKS = []


class _Owned(_Base):
    """A task's coroutine as process `pid`'s: the loop resumes this, and each
    resume is a step under the scheduler."""

    def __init__(self, coro, pid):
        self.coro = coro
        self.pid = pid
        self.task = None
        self.dead = False

    def send(self, _v):
        return LOOP.step(self, None)

    def throw(self, *exc):
        return LOOP.step(self, exc)

    def close(self):
        self.dead = True
        try:
            self.coro.close()
        except (RuntimeError, ValueError):  # a step the guard aborted
            pass

    def __await__(self):
        return self.coro.__await__()


def count(pid):
    """The live tasks of process `pid`. Allocates nothing."""
    n = 0
    for o in TASKS:
        if o.pid == pid:
            n += 1
    return n


def _gone(o):
    if o in TASKS:
        TASKS.remove(o)


def _end(pid):
    """Process `pid` ended (moybyte.ENDS): its tasks cancelled."""
    if not TASKS:
        return
    mine = [o for o in TASKS if o.pid == pid]
    for o in mine:
        TASKS.remove(o)
        o.dead = True
        t = o.task
        if t is not None and not LOOP.running(t):
            try:
                t.cancel()
            except RuntimeError:  # the task stepping now: its step ends it
                pass
    if mine:
        LOOP.nudge()


class _Idle(BaseException):
    """A pass of MicroPython's loop is over: the budget is spent or nothing
    is due."""


_IDLE = _Idle()


class _Loop:
    def __init__(self):
        self.sched = None
        self.w = None           # the loop's wake, while registered
        self.ran = False        # a step ran in this pass
        self.patched = False
        self._o = None
        self._exc = None
        self._go = self._resume
        self._drive = self.drive

    # -- the wake -------------------------------------------------------------

    def nudge(self):
        """The loop's wake due now, made when it is not registered."""
        s = self.sched
        if s is None:
            return
        w = self.w
        if w is None or s.wakes.by_id.get(w[0]) is not w:
            wid = s.plain_every(1, self._drive)
            w = self.w = s.wakes.by_id[wid]
        w[_AT] = _ticks_ms()

    def unwake(self):
        w = self.w
        self.w = None
        if w is not None and self.sched is not None:
            self.sched.wakes.cancel(w[0])

    def arm(self):
        """After a pass: the wake set for the next one, or gone with the last
        task."""
        w = self.w
        if w is None or self.sched.wakes.by_id.get(w[0]) is not w:
            self.w = None
            if not (TASKS or self.pending()):
                return
            self.nudge()
            w = self.w
        now = _ticks_ms()
        at = self.due(now)
        if at is None:
            self.unwake()
            return
        w[_AT] = at

    # -- one step -------------------------------------------------------------

    def _resume(self):
        o = self._o
        e = self._exc
        if e is None:
            return o.coro.send(None)
        return o.coro.throw(*e)

    def step(self, o, exc):
        """Task owner `o`'s coroutine resumed (`exc`: what to throw into it)
        as its process's callback. What the loop sees: the coroutine's
        yield, its end or its exception."""
        s = self.sched
        if o.dead:
            return self._last(o, exc)
        if exc is None and s.held(o.pid):
            return self.defer()
        self.ran = True
        self._o = o
        self._exc = exc
        try:
            r = s.step(o.pid, self._go)
        except StopIteration:
            self._done(o)
            raise
        except asyncio.CancelledError:
            self._done(o)
            raise
        except SystemExit as e:
            _gone(o)
            s.crash(o.pid, e)
            self._done(o)
            raise asyncio.CancelledError()
        except Exception as e:  # noqa: BLE001 -- the loop's to deliver, or a crash
            _gone(o)
            if not self.awaited(o):
                s.crash(o.pid, e)
            self._done(o)
            raise
        finally:
            self._o = self._exc = None
        if r is s.ABORTED:
            o.close()
            self._done(o)
            raise asyncio.CancelledError()
        return r

    def _last(self, o, exc):
        """A task of an ended process: its CancelledError delivered once, under
        the guard, to clean up in, and then closed."""
        if exc is None:             # it ended in this task's own step
            exc = (asyncio.CancelledError(),)
        self._o = o
        self._exc = exc
        try:
            self.sched.step(o.pid, self._go)
        except BaseException:  # noqa: BLE001 -- its end, whatever it raised
            pass
        finally:
            self._o = self._exc = None
        o.close()
        raise asyncio.CancelledError()

    def _done(self, o):
        _gone(o)
        self.sched.settle(o.pid)

    # -- the faces ----------------------------------------------------------

    def create_task(self, coro):
        pid = _L.current() if _L is not None else SHELL
        o = _Owned(coro, pid)
        TASKS.append(o)
        try:
            t = self._create(o)
        except BaseException:
            _gone(o)
            raise
        o.task = t
        self.nudge()
        return t

    def run(self, coro):
        return self.create_task(coro)


if _MP and asyncio is not None:
    from asyncio import core as _core

    class _IO(_core.IOQueue):
        """The loop's IO queue: its wait never blocks, and it ends a pass."""

        def wait_io_event(self, dt):
            if self.map:
                _core.IOQueue.wait_io_event(self, 0)
            if dt == 0:
                if LOOP.ran and LOOP.sched.left_us() <= 0:
                    raise _IDLE
                return
            t = _core._task_queue.peek()
            if t is None or _ticks_diff(t.ph_key, _ticks_ms()) > 0:
                raise _IDLE

    class _MPLoop(_Loop):
        def _create(self, o):
            return _CREATE(o)

        def running(self, t):
            return t is _core.cur_task

        def awaited(self, o):
            return _core.cur_task.state is not True

        def defer(self):
            _core._task_queue.push(_core.cur_task, _ticks_add(_ticks_ms(), POLL_MS))

        def pending(self):
            return _core._task_queue.peek() is not None or bool(_core._io_queue.map)

        def due(self, now):
            if _core._io_queue.map:
                return now
            t = _core._task_queue.peek()
            if t is None:
                return _ticks_add(now, POLL_MS) if TASKS else None
            if _ticks_diff(t.ph_key, now) < POLL_MS:
                return t.ph_key
            return _ticks_add(now, POLL_MS)

        def drive(self):
            io = _core._io_queue
            if io.map:
                _core.IOQueue.wait_io_event(io, 0)
            t = _core._task_queue.peek()
            if t is not None and _ticks_diff(t.ph_key, _ticks_ms()) <= 0:
                self.ran = False
                try:
                    _core.run_until_complete(None)
                except _Idle:
                    pass
                except Exception as e:  # noqa: BLE001 -- the loop never breaks a frame
                    _log_err(e)
                _core.cur_task = None
                _IDLE.__traceback__ = None
            self.arm()

        def patch(self):
            global _CREATE
            _CREATE = _core.create_task
            create, run = self.create_task, self.run
            for m in (asyncio, _core):
                m.create_task = create
                m.run = run
            _core.Loop.create_task = create
            _core.Loop.run_until_complete = run
            _core.Loop.run_forever = _nothing
            asyncio.new_event_loop = _core.new_event_loop = _same_loop
            _core.Loop.set_exception_handler(_unawaited)
            if not _core._io_queue.map:
                _core._io_queue = _IO()
            ev = asyncio.Event
            was = ev.set

            def set_(e):
                was(e)
                self.nudge()
            ev.set = set_

    _CREATE = None
    LOOP = _MPLoop()

elif asyncio is not None:

    class _PyLoop(_Loop):
        def __init__(self):
            _Loop.__init__(self)
            self.loop = None

        def _create(self, o):
            return self.loop.create_task(o)

        def running(self, t):
            return False

        def awaited(self, o):
            return bool(getattr(o.task, "_callbacks", None))

        def defer(self):
            return None             # a bare yield: the next iteration

        def pending(self):
            return bool(self.loop is not None and getattr(self.loop, "_ready", None))

        def due(self, now):
            return now if TASKS or self.pending() else None

        def drive(self):
            loop = self.loop
            self.ran = False
            loop.call_soon(loop.stop)
            try:
                loop.run_forever()
            except Exception as e:  # noqa: BLE001 -- the loop never breaks a frame
                _log_err(e)
            self.arm()

        def patch(self):
            self.loop = loop = asyncio.new_event_loop()
            loop.set_exception_handler(_unawaited)
            create, run = asyncio.create_task, asyncio.run

            def factory(lp, coro, **kw):
                if not isinstance(coro, _Owned):
                    coro = _Owned(coro, _L.current() if _L is not None else SHELL)
                    TASKS.append(coro)
                t = asyncio.Task(coro, loop=lp, **kw)
                coro.task = t
                return t
            loop.set_task_factory(factory)

            def create_task(coro, **kw):
                if asyncio._get_running_loop() is None:
                    return self.create_task(coro)
                return create(coro, **kw)

            def run_(coro, **kw):
                if asyncio._get_running_loop() is loop or (
                        _L is not None and _L.current() != SHELL):
                    return self.create_task(coro)
                return run(coro, **kw)
            asyncio.create_task = create_task
            asyncio.run = run_
            asyncio.sleep_ms = _sleep_ms

    def _sleep_ms(ms):
        return asyncio.sleep(max(0, ms) / 1000)

    LOOP = _PyLoop()

else:
    LOOP = _Loop()


def _nothing(*_a):
    return None


def _same_loop(*_a):
    return asyncio.get_event_loop()


def _unawaited(_loop, _context):
    """A task's unawaited exception was its process's crash at its step."""


def _log_err(e):
    if _L is not None:
        _L.write(getattr(_L, "ERR", 3), SHELL, "asyncio: %r" % (e,))


def install(sched):
    """The shell's loop on scheduler `sched`. False where the VM has none."""
    if asyncio is None:
        return False
    lp = LOOP
    if lp.sched is sched:
        return True
    lp.unwake()
    lp.sched = sched
    if not lp.patched:
        lp.patch()
        lp.patched = True
    sched.wakes.holds = count
    if _end not in _moybyte.ENDS:
        _moybyte.ENDS.append(_end)
    if TASKS or lp.pending():
        lp.nudge()
    return True
