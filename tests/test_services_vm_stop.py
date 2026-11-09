"""A service on a task of its own runs on through an S3's VM stop, whoever
wrote it (docs/os_2026-11/processes.md section 5; runtime/system/
scheduler.py, ACROSS A VM STOP): a Lua, a WASI and an ELF service keep their
pid and their timers (one set again from its own tick, and a Lua alarm,
the kernel's), a message between processes on their own tasks is
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
moybyte.every(5, function() n = n + 1 end)
moybyte.on("note", function()
    print("noted " .. tostring(moybyte.notify("Ticker", "twenty")))
end)
moybyte.on("count", function(who) moybyte.send(who, "counted", n) end)
moybyte.on("alarm", function()
    moybyte.at(os.time() + 1, function() print("rang") end)
    print("alarm set")
end)
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
    _cart(tmp_path / "carts", "t.tick", "service.lua", LUA_SVC,
          ("notifications", "autostart", "alarms"))
    ws = build_ws(tmp_path)
    since = L.next()
    pid = S.boot(ws)[0]
    p = fresh.procs[pid]
    h = p.native
    assert h and fresh.find("t.tick") is p
    peer = L.spawn("peer")
    assert moy_evq.open(peer, "t.tick", "", False) == moy_evq.OK
    assert moy_evq.listen(peer, "counted", True) == moy_evq.OK
    assert moy_evq.send(peer, "t.tick", "alarm", "[]")[0] == 1
    assert _polled(fresh, lambda: "alarm set" in _texts(since, pid))
    woke = []
    monkeypatch.setattr(moybyte.WAKES, "woke", woke.append)
    rec, door = _stop(ws, fresh)
    path = _shelf(ws, "t.tick")["path"]
    assert rec["keep"] == [[pid, path, h]] and path not in rec["svc"]
    assert p.state == S.BACKGROUND, "the pause leaves it running"
    _during(30, _lua_step)
    # Its timers run on, and a message between processes on their own tasks
    # is answered meanwhile.
    assert moy_evq.send(peer, "t.tick", "count", "[]")[0] == 1
    got, t0 = None, time.monotonic()
    while got is None:
        assert time.monotonic() - t0 < 5, "a message during the stop is answered"
        _during(10, _lua_step)
        got = moy_evq.take(peer)
    assert got[2] == "counted" and json.loads(got[3])[0] >= 2
    # Its alarm, the kernel's, rings during the stop; its notify waits for
    # the VM.
    t0 = time.monotonic()
    while "rang" not in _texts(since, pid):
        assert time.monotonic() - t0 < 3, "its alarm rings during the stop"
        _during(20, _lua_step)
    assert moy_evq.send(peer, "t.tick", "note", "[]")[0] == 1
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
    s.tick()
    assert woke == [pid], "the alarm's screen wake reaches the console's next frame"
    assert s.stop(pid) and q.state == S.EXITED
    moy_evq.close(peer)


ALARM_SVC = '''
local ids = {}
moybyte.on("set", function(_, secs, tag)
    ids[tag] = moybyte.at(os.time() + secs, function() print("rang " .. tag) end)
    print("set " .. tag)
end)
moybyte.on("unset", function(_, tag) moybyte.cancel(ids[tag]); print("unset " .. tag) end)
'''


@pytest.mark.skipif(S._svc() is None, reason="no Lua runtime on this host")
def test_a_kernel_alarm_keeps_its_wall_time_through_a_clock_step_and_a_cancel_frees_it(
        tmp_path, fresh):
    """The wall clock steps (the board's NTP answer, moy_wifi.c's
    sntp_sync_time, tells the kernel): an alarm keeps its wall time, rung
    soon after a step forward past most of its wait, held through a step
    back. A Lua service's cancel cancels the kernel's alarm."""
    _cart(tmp_path / "carts", "t.alarm", "service.lua", ALARM_SVC, ("autostart", "alarms"))
    ws = build_ws(tmp_path)
    since = L.next()
    pid = S.boot(ws)[0]
    peer = L.spawn("peer")
    assert moy_evq.open(peer, "t.alarm", "", False) == moy_evq.OK

    def ask(name, *args):
        assert moy_evq.send(peer, "t.alarm", name, json.dumps(list(args)))[0] == 1

    def until(text, secs):
        t0 = time.monotonic()
        while text not in _texts(since, pid):
            if time.monotonic() - t0 >= secs:
                return False
            fresh.poll()
            moy_evq.timers_run()
            _lua_step()
            time.sleep(0.005)
        return True

    try:
        ask("set", 600, "far")
        assert until("set far", 5) and moy_evq.timers(pid) == 1
        assert not until("rang far", 0.3)
        moy_evq.clock(598_500)      # ten minutes on, but 1.5 to 2.5 s
        t0 = time.monotonic()
        assert until("rang far", 5), "the alarm keeps its wall time"
        assert time.monotonic() - t0 > 0.5, "the step moved it by the step, not past it"
        assert moy_evq.timers(pid) == 0
        ask("set", 1, "near")
        assert until("set near", 5)
        moy_evq.clock(-60_000)      # the clock set back a minute
        assert not until("rang near", 2.5), "its wall time is a minute further off"
        moy_evq.clock(60_000)
        assert until("rang near", 3)
        ask("set", 600, "gone")
        assert until("set gone", 5) and moy_evq.timers(pid) == 1
        ask("unset", "gone")
        assert until("unset gone", 5)
        assert moy_evq.timers(pid) == 0, "the cancel gave the kernel's slot back"
        moy_evq.clock(600_000)
        assert not until("rang gone", 0.3)
    finally:
        moy_evq.close(peer)
        fresh.stop(pid)


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


def _through_a_stop(ws, s, monkeypatch, cid, tier, noted, rearm, ticks):
    """A compiled service started, a VM stop and back: it keeps its pid, a
    ping from a process on its own task is answered during the stop, a timer
    it sets again from its own timer's message (`rearm`, the message that
    starts it) keeps going off meanwhile (`ticks(texts, got)` counts them),
    and its notify, which needs the console, is answered once the VM is
    back."""
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
    assert moy_evq.send(peer, cid, rearm[0], rearm[1])[0] == 1
    assert _polled(s, lambda: s.tick() >= 0 and (heard() or ticks(_texts(since, pid), got) > 0))
    rec, door = _stop(ws, s)
    assert rec["keep"] == [[pid, cart["path"], 0]]
    assert moy_evq.send(peer, cid, "ping", "[3]")[0] == 1
    t0 = time.monotonic()
    while not (heard() or "pong" in got):
        assert time.monotonic() - t0 < 5, "a ping during the stop is answered"
        _during(10)
    n = ticks(_texts(since, pid), got)
    t0 = time.monotonic()
    while not (heard() or ticks(_texts(since, pid), got) >= n + 3):
        assert time.monotonic() - t0 < 5, "its timer, set again from its tick, runs on"
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
                            lambda texts, got: "noted" in got, ("rearm", "[]"),
                            lambda texts, got: got.count("timed"))
    assert "taken back elf.svc" in texts and "ping 3" in texts, "|".join(texts)


def test_a_wasi_service_runs_on_through_a_vm_stop(tmp_path, wasi_built, fresh, monkeypatch):
    ws = _compiled_ws(tmp_path, wasi_built, "wasi_svc.moy")
    if "wasm" not in ws.runtimes:
        pytest.skip("no wasm tier on this host")
    texts = _through_a_stop(ws, fresh, monkeypatch, "wasi.svc", "wasm",
                            lambda texts, got: any(t.startswith("service_running ")
                                                   for t in texts),
                            ("wake", '["rearm"]'),
                            lambda texts, got: texts.count("rearmed"))
    note = next(i for i, t in enumerate(texts) if t.startswith("note "))
    assert texts.index("taken back wasi.svc") < note, "its notify was answered after the stop"
    assert "stopped after 1 pings, ticking" in texts


def test_an_elf_and_a_wasi_service_message_each_other_and_log_through_a_vm_stop(
        tmp_path, elf_built, wasi_built, fresh, monkeypatch):
    """`send` and `log` are the kernel's on each program's own task
    (native/os_calls.json, how "here"): with the VM down, a WASI service's
    ping reaches an ELF service of another app that receives it, the ELF
    service's pong reaches a process of the WASI service's app, and each
    service's log lines are written meanwhile."""
    from runtime.host_app import build_workstation
    carts = tmp_path / "carts"
    carts.mkdir()
    for built, name, hears in ((elf_built, "elf_svc.moy", "ping"),
                               (wasi_built, "wasi_svc.moy", "pong")):
        shutil.copytree(built, str(carts / name))
        man_path = carts / name / "manifest.json"
        man = json.loads(man_path.read_text())
        man["moybyte"]["receives"] = [hears]
        man_path.write_text(json.dumps(man))
    ws = build_workstation(str(carts))
    if "elf" not in ws.runtimes or "wasm" not in ws.runtimes:
        pytest.skip("no ELF or wasm tier on this host")
    s = fresh
    since = L.next()
    elf = S.start_cart(ws, _shelf(ws, "elf.svc"), s)
    wasi = S.start_cart(ws, _shelf(ws, "wasi.svc"), s)
    peer = L.spawn("peer")
    assert moy_evq.open(peer, "wasi.svc", "pong", False) == moy_evq.OK
    assert moy_evq.listen(peer, "pong", True) == moy_evq.OK
    assert _polled(s, lambda: all(any(t in ("listening", "started") for t in _texts(since, p))
                                  for p in (elf, wasi)))
    rec, door = _stop(ws, s)
    assert sorted(k[0] for k in rec["keep"]) == sorted((elf, wasi))
    assert moy_evq.send(peer, "wasi.svc", "wake", '["pass","elf.svc"]')[0] == 1
    got = []
    t0 = time.monotonic()
    while not got:
        assert time.monotonic() - t0 < 5, "the ELF service's pong reaches the WASI app"
        _during(10)
        m = moy_evq.take(peer)
        if m is not None:
            got.append(m)
    assert got[0][1:3] == ("elf.svc", "pong") and got[0][3] == '["wasi"]', got
    assert "passed 1" in _texts(since, wasi), _texts(since, wasi)
    assert "ping 8" in _texts(since, elf), _texts(since, elf)
    s2, _started = _back(ws, rec, door, monkeypatch)
    for p in (elf, wasi):
        assert s2.stop(p) and s2.procs[p].code == 0
    moy_evq.close(peer)


def _two_ws(tmp_path, items):
    """A console over compiled service carts: (built, folder, what to set in
    its manifest's "moybyte") each."""
    from runtime.host_app import build_workstation
    carts = tmp_path / "carts"
    carts.mkdir()
    for built, name, sets in items:
        shutil.copytree(built, str(carts / name))
        man_path = carts / name / "manifest.json"
        man = json.loads(man_path.read_text())
        man["moybyte"].update(sets)
        man_path.write_text(json.dumps(man))
    ws = build_workstation(str(carts))
    if not all(t in ws.runtimes for t in ("elf", "wasm")):
        pytest.skip("no ELF or wasm tier on this host")
    return ws


@pytest.fixture
def pins():
    """The Waveshare P4's lease rows offered by the host's stand-in."""
    import sys
    from runtime import leases
    from runtime import moy_lease as N
    from runtime.host import lease_sim
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    import board_config
    leases.stand_in(lease_sim, board_config.lease_spec(
        os.path.join(ROOT, "firmware", "esp32_p4_wifi6_touch_lcd_7b")))
    lease_sim.reset()
    yield N
    for p in list(leases.LEASES.held):
        leases.LEASES.sweep(p)
    N.reset()
    leases.stand_in(None, None)


def test_compiled_services_hold_device_handles_through_a_vm_stop(
        tmp_path, elf_built, wasi_built, fresh, monkeypatch, pins):
    """The device handles the kernel owns (native/moy_kernel/moy_dev.h) are
    served on each program's own task: with the VM down, an ELF service
    drives a gpio handle's pin and reads it back, and a WASI service reads
    the edges a pin made during the stop from its handle, whose reader the
    stop kept, and puts text on the clipboard (native/moy_kernel/moy_lock.h);
    each lease the kernel took is no VM's, and a service's end closes its
    handles."""
    from runtime import moy_input
    N = pins
    perms = ["full_access", "lease:gpio"]
    ws = _two_ws(tmp_path, ((elf_built, "elf_svc.moy", {"permissions": perms}),
                            (wasi_built, "wasi_svc.moy",
                             {"permissions": ["lease:gpio", "clipboard"]})))
    s = fresh
    since = L.next()
    elf = S.start_cart(ws, _shelf(ws, "elf.svc"), s)
    wasi = S.start_cart(ws, _shelf(ws, "wasi.svc"), s)
    peer_e, peer_w = L.spawn("peer"), L.spawn("peer")
    assert moy_evq.open(peer_e, "elf.svc", "", False) == moy_evq.OK
    assert moy_evq.listen(peer_e, "pinned", True) == moy_evq.OK
    assert moy_evq.open(peer_w, "wasi.svc", "", False) == moy_evq.OK
    assert _polled(s, lambda: all(any(t in ("listening", "started") for t in _texts(since, p))
                                  for p in (elf, wasi)))
    assert moy_evq.send(peer_w, "wasi.svc", "wake", '["edges"]')[0] == 1
    assert _polled(s, lambda: "edges 0 errno 0" in _texts(since, wasi)), _texts(since, wasi)
    assert N.held(N.VM) == 0, "the kernel's leases hold no VM stop off"
    rec, door = _stop(ws, s)
    moy_input._lib().moy_input_vm_stop()
    N.sim_level(5, 1)
    N.sim_level(5, 0)
    assert moy_evq.send(peer_w, "wasi.svc", "wake", '["read"]')[0] == 1
    assert moy_evq.send(peer_e, "elf.svc", "pin", "[]")[0] == 1
    assert moy_evq.send(peer_w, "wasi.svc", "wake", '["clip"]')[0] == 1
    got = []
    t0 = time.monotonic()
    while not (got and "clip" in "|".join(_texts(since, wasi))):
        assert time.monotonic() - t0 < 5, (_texts(since, wasi), got)
        _during(10)
        m = moy_evq.take(peer_e)
        if m is not None:
            got.append(m)
    assert got[0][2:] == ("pinned", "[1,1,0]"), got
    assert "read 2 1 0 errno 0" in _texts(since, wasi), _texts(since, wasi)
    assert "clip 0 clipped errno 0" in _texts(since, wasi), _texts(since, wasi)
    assert N.held() == 1, "the WASI service's pin, the ELF one's closed"
    s2, _started = _back(ws, rec, door, monkeypatch)
    for p in (elf, wasi):
        assert s2.stop(p)
    assert N.held() == 0, "its end closed its handle"
    moy_evq.close(peer_e)
    moy_evq.close(peer_w)


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


def test_a_soft_reset_ends_an_elf_service_with_its_exit_logged(tmp_path, elf_built, fresh):
    """A soft reset has no record to take a service back by, so the kernel
    stops each ELF service as a stop would (moy_os_services_end), its exit
    logged, nothing left running for the next VM."""
    from runtime import elf_binding
    ws = _compiled_ws(tmp_path, elf_built, "elf_svc.moy")
    if "elf" not in ws.runtimes:
        pytest.skip("no ELF tier on this host")
    since = L.next()
    pid = S.start_cart(ws, _shelf(ws, "elf.svc"), fresh)
    rt = ws.runtimes["elf"]
    assert _polled(fresh, lambda: "started" in _texts(since, pid))
    assert elf_binding.services_end() == 1
    assert rt.figures(pid) is None
    assert "exit 0 (a soft reset)" in _texts(since, pid)
    assert elf_binding.services_end() == 0
