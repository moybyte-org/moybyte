"""A service on a task of its own runs on through an S3's VM stop, whoever
wrote it (docs/os_2026-11/processes.md section 5; runtime/system/
scheduler.py, ACROSS A VM STOP): a Lua, a WASI and an ELF service keep their
pid and their timers, a message between processes on their own tasks is
answered during the stop, a call that needs Python waits and is answered
once the VM is back, and the next VM's scheduler takes each back from the
kernel's tables with no second start. A Python service still pauses: it is
TERMed before the stop and started again after it, a new process.

The host stands the stop in as the board's teardown and return start do it:
the resume record written and the pause run (the Player's `_stop_for`), the
VM's queues closed and its door gone, the VM down for the queue of calls
(moy_kernel_vm_down), the kernel's frame serving the hops meanwhile; then
the VM up, the app ABI's run grants ended but the kept services'
(console._app_abi), the door back and a new scheduler that reads the record
and takes back at its first frame (scheduler.boot). The stepped Lua tier has
no task to run on, so the stop's frame steps it in its place."""

import json
import os
import shutil
import time

import pytest

from runtime import moy_evq, moy_loop, moybyte, moycore, native_build, os_log, scheduler as S
from runtime import moy_log as L
from test_scheduler import _clear
from ws_helpers import build_ws

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

LUA_SVC = '''
n = 0
moybyte.every(5, function()
    n = n + 1
    if n == 20 then
        print("noted " .. tostring(moybyte.notify("Ticker", "twenty")))
    end
end)
moybyte.on("count", function(who) moybyte.send(who, "counted", n) end)
'''

PY_SVC = '''
def moy_start():
    moybyte.every(1000, lambda: None)
    print("started")
'''


def _cart(root, cid, entry, src, perms=()):
    d = root / ("%s.moy" % cid)
    d.mkdir(parents=True)
    (d / "manifest.json").write_text(json.dumps({
        "title": cid, "id": cid, "canvas": "320x240",
        "moybyte": {"type": "app", "permissions": list(perms),
                    "entries": {"service": entry}}}))
    (d / "main.py").write_text("def _init():\n    pass\n")
    (d / "config.json").write_text("{}")
    (d / entry).write_text(src)


def _shelf(ws, cid):
    return next(c for c in ws.carts.all if c.get("id") == cid)


def _texts(since, pid):
    return [t for _s, _t, _l, q, t in os_log.records(since, 256) if q == pid]


@pytest.fixture
def fresh(monkeypatch):
    """The scheduler a test's console starts on, and the VM's state put back
    after it."""
    wakes = moybyte.WAKES
    _clear(wakes)
    s = S.Scheduler()
    monkeypatch.setattr(S, "SCHED", s)
    door = moy_loop._ROLE[0]
    yield s
    moy_evq.vm(True)
    moy_loop.role(door)
    S.SCHED.reset()
    _clear(wakes)


def _stop(ws, s):
    """The VM stops: what the board's Player and teardown do, the record
    answered."""
    rec = json.loads(ws.resume_record())
    s.pause()
    moy_evq.vm_stop()
    door = moy_loop._ROLE[0]
    moy_loop.role(None)
    moy_evq.vm(False)
    return rec, door


def _during(ms, step=None):
    """The kernel's frame with no VM for `ms`: its jobs run (the hops
    served, the timers posted), and the stepped Lua tier stepped in its
    tasks' place."""
    t0 = time.monotonic()
    while (time.monotonic() - t0) * 1000 < ms:
        moy_evq.serve()
        moy_evq.timers_run()
        if step is not None:
            step()
        time.sleep(0.005)


def _back(ws, rec, door, monkeypatch):
    """The return start: the next VM's scheduler, after its first frame."""
    moy_evq.vm(True)
    ws.app_abi.stop_end([r[0] for r in S.kept_of(rec)])
    moy_loop.role(door)
    s = S.Scheduler()
    monkeypatch.setattr(S, "SCHED", s)
    ws.resume_launcher(json.dumps(rec))
    started = S.boot(ws)
    return s, started


def _polled(s, cond, secs=5.0):
    t0 = time.monotonic()
    while not cond():
        if time.monotonic() - t0 >= secs:
            return False
        s.poll()
        time.sleep(0.005)
    return True


def _lua_step():
    moycore.svc_step(int(time.monotonic() * 1000) & 0x3FFFFFFF)


@pytest.mark.skipif(S._svc() is None, reason="no Lua runtime on this host")
def test_a_lua_service_keeps_its_pid_and_timers_and_its_call_waits_for_the_vm(
        tmp_path, fresh, monkeypatch):
    _cart(tmp_path / "carts", "t.tick", "service.lua", LUA_SVC, ("notifications", "autostart"))
    ws = build_ws(tmp_path)
    since = L.next()
    pid = S.boot(ws)[0]
    p = fresh.procs[pid]
    h = p.native
    assert h and fresh.find("t.tick") is p
    peer = L.spawn("peer")
    assert moy_evq.open(peer, "t.tick", "", False) == moy_evq.OK
    assert moy_evq.listen(peer, "counted", True) == moy_evq.OK
    rec, door = _stop(ws, fresh)
    path = _shelf(ws, "t.tick")["path"]
    assert rec["keep"] == [[pid, path, h]] and path not in rec["svc"]
    assert p.state == S.BACKGROUND, "the pause leaves it running"
    _during(30, _lua_step)
    # Its timers run on, and a message between processes on their own tasks
    # is answered meanwhile.
    assert moy_evq.send(peer, "t.tick", "count", "[]")[0] == 1
    _during(20, _lua_step)
    got = moy_evq.take(peer)
    assert got is not None and got[2] == "counted" and json.loads(got[3])[0] >= 4
    # Its twentieth tick's notify waits for the VM.
    _during(150, _lua_step)
    assert moycore.svc_info(h)[:2] == (2, 0)            # RUNNING (moycore_svc.h)
    assert moy_evq.next_call() is not None
    assert not any(t.startswith("noted") for t in _texts(since, pid))
    s, started = _back(ws, rec, door, monkeypatch)
    q = s.procs[pid]
    assert started[0] == pid and q.native == h and q.state == S.BACKGROUND
    assert [r.pid for r in s.services() if r.name == "t.tick"] == [pid], \
        "the autostart started no second one"
    assert _polled(s, lambda: any(t.startswith("noted ") for t in _texts(since, pid)))
    assert "taken back t.tick" in _texts(since, pid)
    assert s.stop(pid) and q.state == S.EXITED
    moy_evq.close(peer)


def test_a_python_service_is_stopped_and_started_again(tmp_path, fresh, monkeypatch):
    _cart(tmp_path / "carts", "t.py", "service.py", PY_SVC)
    ws = build_ws(tmp_path)
    since = L.next()
    pid = S.start_cart(ws, _shelf(ws, "t.py"), fresh)
    rec, door = _stop(ws, fresh)
    assert _shelf(ws, "t.py")["path"] in rec["svc"] and rec["keep"] == []
    assert fresh.procs[pid].state == S.EXITED
    s, started = _back(ws, rec, door, monkeypatch)
    again = s.find("t.py")
    assert again is not None and again.pid != pid and again.pid in started
    assert "start t.py" in _texts(since, again.pid)


def test_the_record_and_the_kept_grants_read_back():
    assert S.kept_of({"keep": [[5, "/c/a.moy", 0], ["x", 1, 2], [6, "/c/b.moy"]]}) == (
        (5, "/c/a.moy", 0),)
    assert S.kept_of({}) == () and S.kept_of(None) == ()


# -- the compiled services ----------------------------------------------------------


@pytest.fixture(scope="module")
def elf_built(tmp_path_factory):
    if native_build.cc() is None:
        pytest.skip("no C compiler")
    from tools import elf_cart
    dst = str(tmp_path_factory.mktemp("elfsvc") / "elf_svc.moy")
    elf_cart.build(os.path.join(ROOT, "tests", "fixtures", "elf", "elf_svc.moy"), dst,
                   ["host"], signed=False)
    return dst


@pytest.fixture(scope="module")
def wasi_built(tmp_path_factory):
    if native_build.cc() is None:
        pytest.skip("no C compiler")
    from runtime import wasm_binding, wasm_host
    from tools import jet_cart, wasm_cart
    if not wasm_host.available():
        pytest.skip("no host wasm binding: %s" % (wasm_binding.why_unavailable() or "?"))
    try:
        jet_cart.wasi_sdk(fetch=True)
    except Exception as exc:  # noqa: BLE001
        pytest.skip("no wasi-sdk: %s" % exc)
    dst = str(tmp_path_factory.mktemp("wasisvc") / "wasi_svc.moy")
    wasm_cart.build(os.path.join(ROOT, "tests", "fixtures", "wasm", "wasi_svc.moy"), dst)
    return dst


def _compiled_ws(tmp_path, built, name):
    from runtime.host_app import build_workstation
    carts = tmp_path / "carts"
    carts.mkdir()
    shutil.copytree(built, str(carts / name))
    return build_workstation(str(carts))


def _through_a_stop(ws, s, monkeypatch, cid, tier, noted, timed):
    """A compiled service started, a VM stop and back: it keeps its pid, a
    ping from a process on its own task is answered during the stop, a
    timer of the kernel's goes off meanwhile (`timed(texts, got, id)`), and
    its notify, which needs the console, is answered once the VM is back."""
    since = L.next()
    cart = _shelf(ws, cid)
    pid = S.start_cart(ws, cart, s)
    rt = ws.runtimes[tier]
    peer = L.spawn("peer")
    assert moy_evq.open(peer, cid, "", False) == moy_evq.OK
    assert moy_evq.listen(peer, "pong", True) == moy_evq.OK
    assert moy_evq.listen(peer, "noted", True) == moy_evq.OK
    assert moy_evq.listen(peer, "timed", True) == moy_evq.OK
    got = []

    def heard():
        m = moy_evq.take(peer)
        while m is not None:
            got.append(m[2])
            m = moy_evq.take(peer)
        return noted(_texts(since, pid), got)
    assert _polled(s, lambda: any(t in ("listening", "started") for t in _texts(since, pid)))
    if cid == "elf.svc":                         # its "wake" names who hears its timers
        assert moy_evq.send(peer, cid, "wake", "[]")[0] == 1
        assert _polled(s, lambda: s.tick() >= 0 and (heard() or "timed" in got))
    rec, door = _stop(ws, s)
    assert rec["keep"] == [[pid, cart["path"], 0]]
    assert moy_evq.send(peer, cid, "ping", "[3]")[0] == 1
    t0 = time.monotonic()
    while not (heard() or "pong" in got):
        assert time.monotonic() - t0 < 5, "a ping during the stop is answered"
        _during(10)
    rc, tid = moy_evq.timer(pid, 20)
    assert rc == moy_evq.OK
    n = len(got)
    t0 = time.monotonic()
    while not (heard() or timed(_texts(since, pid), got[n:], tid)):
        assert time.monotonic() - t0 < 5, "its timer goes off during the stop"
        _during(10)
    assert moy_evq.send(peer, cid, "note", '["x"]')[0] == 1
    _during(150)
    assert rt.figures(pid) is not None, "it runs on"
    assert not heard(), "its notify waits for the VM"
    s2, started = _back(ws, rec, door, monkeypatch)
    q = s2.procs[pid]
    assert started[0] == pid and q.rt is rt and q.state == S.BACKGROUND
    assert _polled(s2, heard)
    assert s2.stop(pid) and q.state == S.EXITED and q.code == 0, "|".join(_texts(since, pid))
    moy_evq.close(peer)
    return _texts(since, pid)


def test_an_elf_service_runs_on_through_a_vm_stop(tmp_path, elf_built, fresh, monkeypatch):
    ws = _compiled_ws(tmp_path, elf_built, "elf_svc.moy")
    if "elf" not in ws.runtimes:
        pytest.skip("no ELF tier on this host")
    texts = _through_a_stop(ws, fresh, monkeypatch, "elf.svc", "elf",
                            lambda texts, got: "noted" in got,
                            lambda texts, got, tid: "timed" in got)
    assert "taken back elf.svc" in texts and "ping 3" in texts, "|".join(texts)


def test_a_wasi_service_runs_on_through_a_vm_stop(tmp_path, wasi_built, fresh, monkeypatch):
    ws = _compiled_ws(tmp_path, wasi_built, "wasi_svc.moy")
    if "wasm" not in ws.runtimes:
        pytest.skip("no wasm tier on this host")
    texts = _through_a_stop(ws, fresh, monkeypatch, "wasi.svc", "wasm",
                            lambda texts, got: any(t.startswith("service_running ")
                                                   for t in texts),
                            lambda texts, got, tid: "moybyte.timer from system [%d]" % tid
                            in texts)
    note = next(i for i, t in enumerate(texts) if t.startswith("note "))
    assert texts.index("taken back wasi.svc") < note, "its notify was answered after the stop"
    assert "stopped after 1 pings, ticking" in texts


# -- on the VM a board runs ---------------------------------------------------------

VM_STOP = """
import moycore, moy_evq
src = '''
n = 0
moybyte.every(5, function() n = n + 1 end)
moybyte.on("count", function(who) moybyte.send(who, "counted", n) end)
moybyte.on("ask", function() moybyte.exit(day()) end)
'''
h = moycore.svc_start(9, "t.tick", src, '[{}, ["day"]]')
print(moy_evq.open(8, "t.tick", "", False), moy_evq.listen(8, "counted", True))
moy_evq.vm(False)
rc, tid = moy_evq.timer(8, 0)
print(moy_evq.vm(), rc, tid & moy_evq.TIMER_ID != 0, moy_evq.timers(8))
moy_evq.timers_run()
m = moy_evq.take(8)
print(m[2], m[3] == "[%d]" % tid, moy_evq.timers(8))
for t in range(0, 50, 5):
    moycore.svc_step(t)
print(moy_evq.send(8, "t.tick", "count", "[]")[0])
moycore.svc_step(55)
m = moy_evq.take(8)
print(m[2], int(m[3][1:-1]) >= 9)
moy_evq.send(8, "t.tick", "ask", "[]")
moycore.svc_step(60)
t = moy_evq.next_call()
moycore.svc_step(65)
print(t[1:], moycore.svc_info(h)[:2])
moy_evq.vm(True)
moy_evq.answer(t[0], "7")
moycore.svc_step(70)
print(moycore.svc_info(h)[:2], moy_evq.vm())
moycore.svc_reap(h)
"""


def test_on_the_vm_a_board_runs_a_lua_service_and_a_timer_run_on_with_no_vm(tmp_path):
    """The board's bindings with the VM down (moy_evq.vm): a kernel timer
    posts, a Lua service steps and answers a message, and its console call
    waits, answered once the VM is up."""
    import subprocess
    from unix_mp import require_unix_mp
    exe = require_unix_mp("moycore", why="the services on the VM a board runs, with no VM")
    script = tmp_path / "stop.py"
    script.write_text(VM_STOP)
    done = subprocess.run([str(exe), str(script)], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stdout + done.stderr
    assert done.stdout.splitlines() == [
        "0 0", "False 0 True 1", "moybyte.timer True 0", "1", "counted True",
        "(9, 'day', '[]') (2, 0)", "(4, 7) True"], done.stdout
