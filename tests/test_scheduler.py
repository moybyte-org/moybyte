"""The cooperative scheduler (runtime/system/scheduler.py,
docs/os_2026-11/processes.md sections 1, 3-5): the tick's budget, each
overrun rung, a crash, a service's start and its TERM then KILL, autostart at
the console's first frame, and the pause across a VM stop. On CPython a fake
guard stands in for moy_guard's abort; the last tests run the real guard on
the desktop MicroPython."""

import json
import subprocess
import time

import pytest

from runtime import moybyte, os_log, scheduler as S
from runtime import moy_log as L
from unix_mp import find_unix_mp, require_unix_mp, runtime_tree


class Guard:
    """moy_guard's face: `cost` is what the callback says it spent
    (`spend`), and a call is aborted while `abort` is set."""

    def __init__(self):
        self.abort = False
        self._cost = 0
        self._next = 0
        self.budgets = []

    def spend(self, us):
        self._next = us

    def call(self, fn, budget):
        self.budgets.append(budget)
        self._next = 0
        if self.abort:
            self._cost = budget
            return S.ABORTED
        r = fn()
        self._cost = self._next
        return r

    def cost(self):
        return self._cost


def _clear(wakes):
    for pid in {w[1] for w in wakes.list + wakes.mail}:
        wakes.end(pid)


@pytest.fixture
def sched():
    """A scheduler over the one registry (a service's `moybyte` is the
    module's), with the fake guard; the registry handed back empty."""
    wakes = moybyte.WAKES
    _clear(wakes)
    s = S.Scheduler(wakes, Guard())
    yield s
    s.reset()
    _clear(wakes)
    wakes.ended = S.SCHED._ended
    wakes.opener = S.SCHED._open


def _since():
    return L.next()


def _lines(since, pid=None):
    return [(lvl, text) for _seq, _t, lvl, p, text in os_log.records(since, 256)
            if pid is None or p == pid]


def _app(name):
    """A process the Player would run: a pid of the log, current."""
    return L.spawn(name)


def _as(pid, fn):
    was = L.current()
    L.current(pid)
    try:
        return fn()
    finally:
        L.current(was)


# -- the tick -----------------------------------------------------------------


def test_a_tick_with_no_wakes_spends_nothing(sched):
    assert sched.tick(0) == 0 and sched.spent_us == 0


def test_a_message_left_by_an_ended_process_costs_no_frame(sched):
    """A message queued on a process that ends before it is served goes
    with its queue, and leaves no frame a look at the queues."""
    pid = _app("t.gone")
    _as(pid, lambda: sched.wakes.on("ping", lambda who: None))
    assert sched.send(pid, "ping", ()) and sched.wakes.queued
    sched.wakes.end(pid)
    assert not sched.wakes.queued
    assert sched.tick(0) == 0 and sched.spent_us == 0


def test_a_suspended_processs_messages_wait_and_cost_no_frame(sched):
    pid = _app("t.held")
    got = []
    _as(pid, lambda: sched.wakes.on("ping", lambda who, n: got.append(n)))
    for _ in range(S.MOY_BG_STRIKES):
        sched._charge(pid, 0, True)
    assert sched.held(pid)
    sched.send(pid, "ping", (1,))
    sched.mail()
    assert got == [] and not sched.wakes.queued and sched.mail_of(pid) == 1
    assert sched.resume(pid)
    sched.mail()
    assert got == [1]
    sched.wakes.end(pid)


def test_a_wake_runs_as_the_process_that_made_it(sched):
    pid = _app("t.owner")
    seen = []
    _as(pid, lambda: sched.wakes.after(0, lambda: seen.append(L.current())))
    sched.tick(0)
    assert seen == [pid]
    assert L.current() != pid                  # put back after the call
    assert sched.wakes.count(pid) == 0         # an `after` is spent


def _busy(us):
    t0 = time.perf_counter()
    while (time.perf_counter() - t0) * 1e6 < us:
        pass


def test_the_slice_bounds_a_tick_and_the_rest_run_next(sched):
    ran = []
    for i in range(6):
        sched.wakes.after(0, lambda i=i: (_busy(600), ran.append(i)))
    sched.tick(0)                               # no slack: the guaranteed slice
    first = len(ran)
    assert 1 <= first <= 2, ran                 # the slice is 1 ms, each 0.6
    for _ in range(10):
        sched.tick(0)
    assert ran == list(range(6))                # every one, in order, none twice


def test_slack_runs_more_than_the_slice(sched):
    ran = []
    for i in range(6):
        sched.wakes.after(0, lambda i=i: (_busy(600), ran.append(i)))
    sched.tick(50000)
    assert ran == list(range(6))


def test_one_callback_runs_whatever_the_budget(sched, monkeypatch):
    monkeypatch.setattr(S, "MOY_BG_SLICE_US", 0)
    ran = []
    sched.wakes.after(0, lambda: ran.append(1))
    sched.tick(0)
    assert ran == []                            # a budget of nothing runs nothing
    monkeypatch.setattr(S, "MOY_BG_SLICE_US", 1)
    sched.tick(0)
    assert ran == [1]


# -- the rungs ----------------------------------------------------------------


def test_each_overrun_rung_warning_strike_suspend_and_resume(sched):
    g = sched.guard
    pid = _app("t.slow")
    calls = []

    def slow():
        calls.append(1)
        g.spend(S.MOY_BG_CALLBACK_US + 5000)
    _as(pid, lambda: sched.wakes.every(1, slow))
    since = _since()

    def step():
        time.sleep(0.003)
        sched.tick(0)

    step()                                      # overrun 1: a warning
    p = sched.procs[pid]
    assert (p.overruns, p.strikes, p.state) == (1, 0, S.RUNNING)
    got = _lines(since, pid)
    assert got[-1][0] == L.WARNING and "over its 20 ms slice" in got[-1][1], got
    step()                                      # overrun 2: strike 1
    assert p.strikes == 1 and "strike 1 of 3" in _lines(since, pid)[-1][1]
    g.abort = True
    step()                                      # an abort: strike 2
    assert p.strikes == 2 and "aborted after 300 ms" in _lines(since, pid)[-1][1]
    assert g.budgets[-1] == S.MOY_BG_ABORT_US
    g.abort = False
    step()                                      # strike 3: suspended
    assert p.state == S.SUSPENDED
    assert [t for _l, t in _lines(since, pid)][-2:] == ["strike 3 of 3: callback ran 25 ms",
                                                        "suspended"]
    n = len(calls)
    for _ in range(3):
        step()
    assert len(calls) == n                      # its wakes held, not served
    assert sched.wakes.count(pid) == 1
    assert sched.resume(pid)
    assert p.state == S.RUNNING and p.strikes == 0
    step()
    assert len(calls) == n + 1


def test_a_callback_within_its_slice_is_no_overrun(sched):
    g = sched.guard
    pid = _app("t.fine")
    _as(pid, lambda: sched.wakes.every(1, lambda: g.spend(S.MOY_BG_CALLBACK_US)))
    for _ in range(4):
        time.sleep(0.003)
        sched.tick(0)
    assert pid not in sched.procs               # never met a rung


def test_the_shell_is_never_struck(sched):
    g = sched.guard
    g.abort = True
    _as(S.SHELL, lambda: sched.wakes.every(1, lambda: None))
    for _ in range(5):
        time.sleep(0.003)
        sched.tick(0)
    assert S.SHELL not in sched.procs and sched.wakes.count(S.SHELL) == 1


def test_an_aborted_after_is_spent_and_the_reader_put_back(sched):
    sched.guard.abort = True
    pid = _app("t.abort")
    _as(pid, lambda: sched.wakes.after(0, lambda: None))
    sched.tick(0)
    assert sched.wakes.count(pid) == 0 and sched.wakes.cur[0] == -1


def test_an_apps_rows_go_when_its_run_ends(sched):
    g = sched.guard
    pid = _app("t.app")
    _as(pid, lambda: sched.wakes.every(1, lambda: g.spend(10 ** 6)))
    time.sleep(0.003)
    sched.tick(0)
    assert pid in sched.procs
    sched.wakes.end(pid)                        # the Player's moybyte.end
    assert pid not in sched.procs


# -- services -----------------------------------------------------------------

TICKER = '''
import moybyte
n = 0
stopped = []
def tick():
    global n
    n += 1
def moy_start():
    moybyte.every(1, tick)
def moy_stop():
    stopped.append(n)
'''


def _cart(src, perms=(), cid="t.svc", required=()):
    return {"id": cid, "title": cid, "type": "service", "src": src,
            "runtime": "python", "permissions": list(perms),
            "required": list(required), "path": "/carts/%s.moy" % cid}


def test_a_service_starts_runs_and_stops_on_its_term(sched):
    since = _since()
    pid = sched.start(_cart(TICKER))
    assert pid is not None
    p = sched.procs[pid]
    assert p.state == S.BACKGROUND and sched.wakes.count(pid) == 1
    for _ in range(3):
        time.sleep(0.003)
        sched.tick(0)
    assert p.ns["n"] == 3
    assert sched.stop(pid)
    assert p.ns is None and p.state == S.EXITED and p.code == S.EXIT_OK
    assert sched.wakes.count(pid) == 0
    assert sched.guard.budgets[-1] == S.MOY_SERVICE_STOP_MS * 1000
    assert [t for _l, t in _lines(since, pid)] == ["start t.svc", "stop", "exit 0"]


def test_a_stop_still_running_after_its_limit_is_killed(sched):
    since = _since()
    pid = sched.start(_cart(TICKER))
    sched.guard.abort = True                    # moy_stop never returns
    sched.stop(pid)
    p = sched.procs[pid]
    assert p.state == S.EXITED and p.code == S.EXIT_KILLED
    assert sched.wakes.count(pid) == 0
    assert [t for _l, t in _lines(since, pid)][-3:] == [
        "stop", "killed: its stop ran past 2000 ms", "exit 137"]


def test_a_service_with_no_stop_hook_ends_at_its_term(sched):
    pid = sched.start(_cart("import moybyte\nmoybyte.every(5, lambda: None)\n"))
    assert sched.stop(pid) and sched.procs[pid].code == S.EXIT_OK


def test_a_service_whose_wakes_run_out_exits(sched):
    pid = sched.start(_cart("import moybyte\nmoybyte.after(0, lambda: None)\n"))
    p = sched.procs[pid]
    sched.tick(0)
    assert p.state == S.EXITED and p.code == S.EXIT_OK
    assert sched.start(_cart("x = 1\n", cid="t.idle")) is None   # nothing to wake it


def test_a_service_that_raises_is_a_crash(sched):
    since = _since()
    pid = sched.start(_cart("import moybyte\n"
                            "def boom():\n    raise ValueError('bad')\n"
                            "moybyte.every(1, boom)\nmoybyte.every(50, boom)\n"))
    time.sleep(0.003)
    sched.tick(0)
    p = sched.procs[pid]
    assert p.state == S.EXITED and p.code == S.EXIT_CRASHED
    assert sched.wakes.count(pid) == 0
    crit = [t for lvl, t in _lines(since, pid) if lvl == L.CRIT]
    assert crit and "ValueError: bad" in crit[0]


def test_a_service_exits_with_its_systemexit_code(sched):
    pid = sched.start(_cart("import moybyte, sys\n"
                            "moybyte.after(0, lambda: sys.exit(3))\n"
                            "moybyte.every(50, lambda: None)\n"))
    sched.tick(0)
    assert sched.procs[pid].code == 3


def test_a_start_that_never_returns_is_killed(sched):
    sched.guard.abort = True
    since = _since()
    assert sched.start(_cart(TICKER)) is None
    texts = [t for _l, t in _lines(since)]
    assert "killed: its start ran past 2000 ms" in texts


def test_a_refused_launch_starts_nothing(sched):
    since = _since()
    cart = _cart(TICKER, perms=["pins"], required=["pins"])
    assert sched.start(cart, None, ()) is None          # no gpio on this console
    assert any(lvl == L.ERR and 'needs "pins"' in t for lvl, t in _lines(since))
    assert not sched.wakes.list


def test_a_turned_off_permission_reads_none(sched):
    src = "import moybyte\nseen = [pin_write, pin_read]\nmoybyte.every(5, lambda: None)\n"
    rec = {"perms": ["pins"], "off": ["pins"]}
    pid = sched.start(_cart(src, perms=["pins"]), rec, ("gpio",))
    assert sched.procs[pid].ns["seen"] == [None, None]


def test_pause_terms_every_service_and_names_them(sched):
    a = sched.start(_cart(TICKER, cid="t.a"))
    b = sched.start(_cart(TICKER, cid="t.b"))
    assert sorted(sched.running_paths()) == ["/carts/t.a.moy", "/carts/t.b.moy"]
    paused = sched.pause()
    assert sorted(paused) == ["/carts/t.a.moy", "/carts/t.b.moy"]
    assert sched.procs[a].code == sched.procs[b].code == S.EXIT_OK
    assert sched.running_paths() == [] and not sched.wakes.list


# -- the console --------------------------------------------------------------


def _write_service(root, cid, perms):
    d = root / ("%s.moy" % cid)
    d.mkdir(parents=True)
    (d / "manifest.json").write_text(json.dumps({
        "format": "moy-1", "title": cid, "id": cid, "runtime": "python",
        "main": "main.py", "moybyte": {"type": "service", "permissions": perms}}))
    (d / "main.py").write_text(TICKER)


@pytest.fixture
def console(tmp_path):
    from runtime.host_app import build_workstation, ConsoleDriver
    root = tmp_path / "carts"
    _write_service(root, "t.auto", ["autostart"])
    _write_service(root, "t.manual", [])
    _write_service(root, "t.off", ["autostart"])

    def make():
        ws = build_workstation(str(root))
        return ws, ConsoleDriver(ws)
    yield make
    S.SCHED.reset()


def _running():
    """The services running but the achievements process, which every
    console starts with its store (tests/test_achievements_proc.py)."""
    return sorted(p.name for p in S.SCHED.services()
                  if p.state != S.EXITED and p.name != "moybyte.achievements")


def test_autostart_starts_what_holds_it_at_the_first_frame(console):
    S.SCHED.reset()
    ws, drv = console()
    from runtime import app_perms
    off = [c for c in ws.carts.all if c.get("id") == "t.off"][0]
    ws.consent.turn(app_perms.app_id(off), "autostart", False)
    assert _running() == []
    drv.frame(1 / 60)
    drv.frame(1 / 60)
    assert _running() == ["t.auto"]
    p = S.SCHED.find("t.auto")
    time.sleep(0.003)
    drv.frame(1 / 60)
    assert p.ns["n"] >= 1                       # ticked by the host's frame
    for tab in ("games", "mine"):               # no service on a home tab
        assert not [c for c in ws._launcher_items(ws.carts.all, tab)
                    if c.get("type") == "service"]


def test_a_vm_stop_starts_its_services_again_after_it(console):
    S.SCHED.reset()
    ws, drv = console()
    drv.frame(1 / 60)
    drv.frame(1 / 60)
    manual = [c for c in ws.carts.all if c.get("id") == "t.manual"][0]
    assert S.start_cart(ws, manual) is not None
    rec = json.loads(ws.resume_record())
    assert sorted(p.rsplit("/", 1)[-1] for p in rec["svc"]) == [
        "moybyte.achievements.moy", "t.auto.moy", "t.manual.moy", "t.off.moy"]
    S.SCHED.pause()                             # the Player's, before the stop
    assert _running() == []
    S.SCHED.reset()
    ws2, drv2 = console()                       # the console after the stop
    ws2.resume_launcher(json.dumps(rec))
    drv2.frame(1 / 60)
    drv2.frame(1 / 60)
    assert _running() == ["t.auto", "t.manual", "t.off"]


# -- the real guard, on the desktop MicroPython ---------------------------------

VM_SCRIPT = r'''
import sys
sys.path[:0] = [@TREE@]
import gc, time
import moy_input as I
import moy_guard
import moybyte
import scheduler as S
moybyte.bind(I.HostInputTable())
s = S.SCHED
print("CAN", moy_guard.can_abort())
S.MOY_BG_ABORT_US = 30000
SPIN = """
import moybyte
def spin():
    while True:
        try:
            pass
        except BaseException:
            pass
def moy_start():
    moybyte.every(1, spin)
def moy_stop():
    while True:
        pass
"""
pid = s.start({"id": "t.spin", "title": "spin", "src": SPIN, "permissions": []})
p = s.procs[pid]
for _ in range(4):
    time.sleep_ms(2)
    s.tick(0)
print("STRIKES", p.strikes, p.state)
S.MOY_SERVICE_STOP_MS = 40
t0 = time.ticks_ms()
s.stop(pid)
print("KILLED", p.code, time.ticks_diff(time.ticks_ms(), t0) < 1000, len(moybyte.WAKES.list))
QUIET = """
import moybyte
n = [0]
def tick():
    n[0] += 1
moybyte.every(1, tick)
"""
pid = s.start({"id": "t.quiet", "title": "quiet", "src": QUIET, "permissions": []})
q = s.procs[pid]
for _ in range(5):
    time.sleep_ms(2)
    s.tick(0)
gc.collect()
before = gc.mem_alloc()
deltas = []
for _ in range(200):
    time.sleep_ms(1)
    a = gc.mem_alloc()
    s.tick(0)
    deltas.append(gc.mem_alloc() - a)
print("ALLOC", sum(deltas), q.ns["n"][0] > 100)
print("DELTAS", [d for d in deltas if d][:10])
'''


def test_on_the_vm_the_guard_aborts_kills_and_a_tick_allocates_nothing(tmp_path):
    exe = require_unix_mp("moy_input", "moy_log",
                          why="the scheduler under the real VM abort")
    exes = [("desktop", exe)]
    board = find_unix_mp("moy_input", "moy_log", board_model=True)
    if board is not None:
        exes.append(("board", board))
    for tag, x in exes:
        script = tmp_path / ("sched_%s.py" % tag)
        script.write_text(VM_SCRIPT.replace("@TREE@", repr(runtime_tree())))
        done = subprocess.run([str(x), str(script)], capture_output=True, text=True,
                              timeout=120)
        out = done.stdout
        assert done.returncode == 0, (tag, out + done.stderr)
        assert "CAN True\n" in out, (tag, out)
        assert "STRIKES 3 suspended\n" in out, (tag, out)
        assert "KILLED 137 True 0\n" in out, (tag, out)
        assert "ALLOC 0 True\n" in out, (tag, out)


def test_a_vm_stop_closes_the_vms_event_queues_and_no_other():
    """The kernel's queues are PSRAM that outlives the VM: its teardown
    (moy_kernel.c's soft_reset_exit, a stop and a soft reset alike) closes
    every queue the VM serves, whose processes ended with it, and leaves a
    service's on a task of its own. Without it every VM start leaked the
    queue of each process listening at its stop (340 bytes a soft reset on
    glass)."""
    from runtime import moy_evq as E
    base = E.live()
    py, svc = L.spawn("t.vm"), L.spawn("t.task")
    assert E.open(py, "t.vm", "", True) == E.OK
    assert E.open(svc, "t.task", "", False) == E.OK
    assert E.live() == base + 2
    assert E.vm_stop() >= 1 and not E.has(py) and E.has(svc)
    after = E.live()
    for _ in range(20):                     # twenty VM starts, each opening its own
        p = L.spawn("t.again")
        assert E.open(p, "t.again", "", True) == E.OK
        assert E.vm_stop() == 1
    assert E.live() == after
    E.close(svc)


def test_a_process_on_a_task_wants_the_vm_and_nothing_else_does():
    """THE VM'S WAKE (moy_evq.h): a message for a VM queue wants MAIL; a
    call or hop on the VM's queue, a rung the supervisor moves and a name a
    task's queue listens on taken or given up want POLL; a message to a
    task's own queue and a callback within its slice want nothing. The VM
    takes what it asks for and leaves the rest."""
    from runtime import moy_evq as E
    E.wanted(E.WANT_MAIL | E.WANT_POLL)
    py, svc = L.spawn("t.vm"), L.spawn("t.task")
    try:
        assert E.open(py, "t.app", "", True) == E.OK
        assert E.open(svc, "t.app", "", False) == E.OK
        assert E.listen(py, "ping", True) == E.OK
        assert E.wants() == 0                       # a VM queue's listen
        assert E.listen(svc, "pong", True) == E.OK
        assert E.wanted(E.WANT_POLL | E.WANT_MAIL) == E.WANT_POLL
        assert E.post(svc, py, "pong", "[]") == E.OK
        assert E.wants() == 0                       # the task's own wake
        assert E.post(py, svc, "ping", "[]") == E.OK
        assert E.wants() == E.WANT_MAIL
        assert E.charge(svc, 1000, False) == E.FINE
        assert E.wanted(E.WANT_MAIL) == E.WANT_MAIL and E.wants() == 0
        assert E.charge(svc, S.MOY_BG_CALLBACK_US + 1, False) == E.WARNED
        assert E.wanted(E.WANT_MAIL) == 0 and E.wants() == E.WANT_POLL
        assert E.wanted(E.WANT_POLL) == E.WANT_POLL and E.wants() == 0
        assert E.listening("pong") == 1 and E.listening("ping") == 0
    finally:
        E.close(py)
        E.close(svc)
        E.forget(svc)
        E.wanted(E.WANT_MAIL | E.WANT_POLL)


def test_a_board_runs_no_poll_and_woken_serves_what_was_wanted(sched):
    """THE POLL on a board (`kernel_woken`): a compiled service is started
    with no poll's wake, the radios' router is not kept for a service that
    listens on no radio, and `woken` turns a MAIL want into `queued` and runs
    the poll for a POLL one."""
    from runtime import moy_evq as E

    class Rt:
        def __init__(self):
            self.ended = []

        def service(self, cart, pid, run):
            return None

        def reap(self):
            got, self.ended = self.ended, []
            return got

        def figures(self, pid):
            return None if any(p == pid for p, _c in self.ended) else (4096, 0)

        def stop(self, pid):
            return 0
    rt = Rt()

    class Ws:
        runtimes = {"wasm": rt}
    sched.kernel_woken()
    E.wanted(E.WANT_MAIL | E.WANT_POLL)
    pid = sched.start({"id": "t.wsvc", "runtime": "wasm", "type": "service"}, ws=Ws())
    assert pid is not None and not sched._poll and sched._tasks
    assert not sched.wakes.radio_keep
    p = sched.procs[pid]
    E.want(E.WANT_MAIL)
    sched.wakes.queued = 0
    sched.woken()
    assert sched.wakes.queued == 1 and p.state == S.BACKGROUND
    rt.ended = [(pid, 3)]
    E.want(E.WANT_POLL)
    sched.woken()
    assert p.state == S.EXITED and p.code == 3 and not sched._tasks
    assert E.wants() == 0

