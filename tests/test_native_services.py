"""A native service run by the scheduler (runtime/system/scheduler.py,
COMPILED SERVICES; docs/os_2026-11/processes.md section 1, the service
model): started under its consent on a task of its own, listed with the
other services, its app calls (`moy_notify`) answered on the VM's task as
hops at the scheduler's poll, its `moy_send` to the VM's processes served at
the next frame's start, its end by itself reaped into the restart policy, its
stop the descriptor's end of file, and a wake that never polls again struck
by the watchdog and killed (137) once it strikes out."""

import json
import os
import shutil
import time

import pytest

from runtime import native_build

if native_build.cc() is None:
    pytest.skip("no C compiler", allow_module_level=True)

from runtime import moy_evq, moybyte, notifications, os_log, scheduler as S  # noqa: E402
from runtime import moy_log as L  # noqa: E402
from test_scheduler import _clear  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURE = os.path.join(ROOT, "tests", "fixtures", "elf", "elf_svc.moy")


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    from tools import elf_cart
    dst = str(tmp_path_factory.mktemp("elfsvc") / "elf_svc.moy")
    elf_cart.build(FIXTURE, dst, ["host"], signed=False)
    return dst


@pytest.fixture
def svc(tmp_path, built):
    """(ws, the scheduler, the service cart as the shelf holds it, a peer
    process of the service's own app that the VM serves)."""
    from runtime.host_app import build_workstation
    carts = tmp_path / "carts"
    carts.mkdir()
    shutil.copytree(built, str(carts / "elf_svc.moy"))
    ws = build_workstation(str(carts))
    assert "elf" in ws.runtimes
    cart = [c for c in ws.carts.all if c.get("id") == "elf.svc"][0]
    wakes = moybyte.WAKES
    _clear(wakes)
    s = S.SCHED                     # the one the console's door reads (procs.who)
    s.reset()
    peer = L.spawn("peer")
    assert moy_evq.open(peer, "elf.svc", "", True) == moy_evq.OK
    for name in ("pong", "noted", "timed"):
        assert moy_evq.listen(peer, name, True) == moy_evq.OK
    yield ws, s, cart, peer
    moy_evq.close(peer)
    s.reset()
    _clear(wakes)


def _polled(s, cond, secs=5.0):
    """The scheduler's poll run until cond() holds or `secs` pass."""
    t0 = time.monotonic()
    while not cond():
        if time.monotonic() - t0 >= secs:
            return False
        s.poll()
        time.sleep(0.01)
    return True


def _texts(since, pid):
    return [t for _s, _t, _l, q, t in os_log.records(since, 128) if q == pid]


def test_a_native_service_is_started_listed_and_stopped_as_any_service(svc):
    ws, s, cart, peer = svc
    since = L.next()
    pid = S.start_cart(ws, cart, s)
    assert pid is not None
    p = s.procs[pid]
    assert p.kind == S.SERVICE and p.state == S.BACKGROUND and p.rt is ws.runtimes["elf"]
    assert s.services() == [p] and s.find("elf.svc") is p
    assert s._poll                                  # the poll's wake, while it lives
    assert s.wakes_of(pid) == 1                     # its one wait
    assert moy_evq.app(pid) == "elf.svc"            # its queue, the scheduler's
    assert _polled(s, lambda: moy_evq.send(peer, "elf.svc", "ping", "[1]")[0] == 1)
    s.wakes.queued = 0
    assert _polled(s, lambda: s.wakes.queued == 1)  # its moy_send to a VM process
    assert moy_evq.take(peer) == (pid, "elf.svc", "pong", "[1]")
    assert _polled(s, lambda: p.cost_us > 0)        # its wakes' time, onto its row
    assert s.stop(pid)
    assert p.state == S.EXITED and p.code == 0 and p.rt is None
    assert not moy_evq.has(pid)
    texts = _texts(since, pid)
    assert texts[0] == "start elf.svc" and "stop" in texts and texts[-1] == "exit 0"
    s.poll()
    assert not s._poll                              # gone with the last service


def test_its_app_calls_are_answered_on_the_vms_task(svc):
    """moy_notify from the service's own task hops to the VM's queue, which
    the scheduler's poll serves: the notification posts as the service."""
    ws, s, cart, peer = svc
    notifications.NOTES.reset()
    pid = S.start_cart(ws, cart, s)
    assert _polled(s, lambda: moy_evq.send(peer, "elf.svc", "note", "[]")[0] == 1)
    assert _polled(s, lambda: moy_evq.queued(peer) == 1)
    assert moy_evq.take(peer) == (pid, "elf.svc", "noted", "[1]")
    n = [n for n in notifications.NOTES.list if n.summary == "Hello"]
    assert [(x.body, x.pid) for x in n] == [("from a service", pid)]
    notifications.NOTES.reset()


def test_a_native_service_s_timer_is_the_system_s_message(svc):
    """moy_after answers its timer's id, which goes off as the system's
    message "moybyte.timer" on the service's event descriptor."""
    ws, s, cart, peer = svc
    pid = S.start_cart(ws, cart, s)
    assert _polled(s, lambda: moy_evq.send(peer, "elf.svc", "wake", "[]")[0] == 1)
    assert _polled(s, lambda: s.tick() >= 0 and moy_evq.queued(peer) == 1)
    frm, app, name, args = moy_evq.take(peer)
    assert (frm, app, name) == (pid, "elf.svc", "timed")
    assert int(json.loads(args)[0]) > 0
    assert s.stop(pid)


def test_an_end_by_itself_is_reaped_into_its_restart_policy(svc):
    ws, s, cart, peer = svc
    store = {}

    class Store:
        def get(self, k, d=None):
            return store.get(k, d)

        def set(self, k, v):
            store[k] = v
    pid = S.start_cart(ws, cart, s)
    s.store = Store()
    s.set_policy("elf.svc", S.ON_CRASH)
    p = s.procs[pid]
    assert _polled(s, lambda: moy_evq.send(peer, "elf.svc", "quit", "[]")[0] == 1)
    assert _polled(s, lambda: p.state == S.EXITED)
    assert p.code == 7 and p.rt is None
    assert "elf.svc" in s.pending                   # on-crash: its restart waits


def test_a_wake_that_never_polls_again_is_struck_out_and_killed(svc):
    """Native code is not aborted where it stands: the watchdog at the poll
    strikes a wake once for each abort budget it runs on, and the third
    strike, which suspension cannot hold, kills it (137)."""
    ws, s, cart, peer = svc
    since = L.next()
    pid = S.start_cart(ws, cart, s)
    p = s.procs[pid]
    assert _polled(s, lambda: moy_evq.send(peer, "elf.svc", "spin", "[]")[0] == 1)
    assert _polled(s, lambda: p.state == S.EXITED, 10)
    assert p.code == L.KILLED
    texts = _texts(since, pid)
    assert "killed: a wake ran past its strikes without a poll" in texts
    assert sum("strike" in t for t in texts) >= 3


def test_a_console_without_the_native_tier_refuses_one(svc):
    ws, s, cart, _peer = svc
    since = L.next()
    del ws.runtimes["elf"]
    assert S.start_cart(ws, cart, s) is None
    p = s.services()[0]
    assert p.state == S.EXITED and p.code == S.EXIT_CRASHED
    assert "a native service does not run on this console" in _texts(since, p.pid)
    assert not moy_evq.has(p.pid)


def test_the_fixture_asks_for_what_it_calls():
    with open(os.path.join(FIXTURE, "manifest.json")) as f:
        man = json.load(f)
    assert man["moybyte"]["type"] == "service"
    assert "notifications" in man["moybyte"]["permissions"]
