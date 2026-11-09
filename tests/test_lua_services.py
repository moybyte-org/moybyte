"""The Lua tier's services (native/moycore/moycore_svc.h, docs/os_2026-11/
processes.md sections 1, 5 and 6): a Lua service cart started by the
scheduler runs its timers in a state of its own, ends with its exit code, is
struck out of a callback that will not return (a `pcall` cannot keep it),
gets its `moy_stop` on a stop and the KILL past its limit, lists in the task
manager with the exact heap of its state, and runs on through an app switch.
It takes the OS's messages on its message wakes and makes console calls (the
names its start was handed, `moybyte.notify`), which the VM's task answers.
The host steps the services at the scheduler's poll; a P4 runs each on a task
of its own on core 0, and an S3 refuses one (on glass)."""

import time

import pytest

from runtime import moy_evq, moybyte, moycore, notifications, os_log, scheduler as S
from runtime import moy_log as L
from runtime import tasks_ui
from test_scheduler import Guard, _clear
from ws_helpers import boot_on_fixture, build_desktop_ws, make_drv, open_cart

_DT = 1 / 30


@pytest.fixture
def sched():
    wakes = moybyte.WAKES
    _clear(wakes)
    s = S.Scheduler(wakes, Guard())
    yield s
    s.reset()
    _clear(wakes)
    wakes.ended = S.SCHED._ended
    wakes.opener = S.SCHED._open
    wakes.goal = S.SCHED._goal


def _cart(src, cid="t.lua"):
    return {"id": cid, "title": cid, "type": "service", "runtime": "lua", "src": src,
            "permissions": [], "required": [], "path": "/carts/%s.moy" % cid}


def _run(s, ms):
    t0 = time.monotonic()
    while (time.monotonic() - t0) * 1000 < ms:
        time.sleep(0.002)
        s.tick(0)


def _texts(since, pid):
    return [t for _s, _t, _l, q, t in os_log.records(since, 128) if q == pid]


COUNTER = """
n = 0
moybyte.every(5, function()
    n = n + 1
    print("tick " .. n)
    if n == 3 then moybyte.exit(7) end
end)
"""


def test_a_lua_service_runs_its_timers_and_ends_with_its_code(sched):
    since = L.next()
    pid = sched.start(_cart(COUNTER))
    p = sched.procs[pid]
    assert p.native and p.state == S.BACKGROUND
    _run(sched, 80)
    assert p.state == S.EXITED and p.code == 7 and not p.native
    assert _texts(since, pid) == ["start t.lua", "tick 1", "tick 2", "tick 3", "exit 7"]
    assert not sched._poll                      # the poll went with the last service


CRYPTO = """
moybyte.after(1, function()
    local d = moybyte.sha256("abc"):sub(1, 4)
    print("sha256 " .. d:gsub(".", function(c) return string.format("%02x", c:byte()) end))
    print("random " .. #moybyte.random(8) .. " " .. type(moybyte.every))
    local sk = moybyte.sha256("seed")
    local sig = moybyte.ed25519_sign(sk, "msg")
    print("ed25519 " .. #sig .. " " .. tostring(moybyte.ed25519_verify(moybyte.ed25519_public(sk), "msg", sig)))
    moybyte.exit(0)
end)
"""


def test_a_service_with_no_timer_after_its_start_has_exited(sched):
    pid = sched.start(_cart("x = 1"))
    assert pid is None
    assert [p.code for p in sched.services()] == [0]


def test_an_error_is_a_crash_with_its_line(sched):
    since = L.next()
    sched.start(_cart('moybyte.after(1, function() error("bad") end)'))
    _run(sched, 30)
    p = sched.services()[0]
    assert p.code == S.EXIT_CRASHED
    assert any("bad" in t for t in _texts(since, p.pid))


BYTECODE = """
local blob = string.dump(function() return 1 end)
local ok = pcall(function() return load(blob, "x", "b") end)
print("load " .. type(load) .. " " .. tostring(ok) .. " " .. type(dofile) .. " " .. type(loadfile))
moybyte.after(1, function() moybyte.exit(0) end)
"""


def test_a_service_cannot_load_a_binary_chunk(sched):
    since = L.next()
    pid = sched.start(_cart(BYTECODE))
    _run(sched, 30)
    assert "load nil false nil nil" in _texts(since, pid)


def test_a_callback_that_never_returns_is_struck_out_through_its_pcall(sched, monkeypatch):
    spin = """
moybyte.every(1, function()
    while true do pcall(function() while true do end end) end
end)
"""
    since = L.next()
    pid = sched.start(_cart(spin))
    _run(sched, 1500)
    p = sched.procs[pid]
    assert p.state == S.SUSPENDED and p.strikes == 3      # the one supervisor's rungs
    texts = _texts(since, pid)
    assert [t for t in texts if "strike" in t] == [
        "strike %d of 3: aborted after 300 ms" % i for i in (1, 2, 3)]
    assert "suspended" in texts
    assert sched.resume(pid) and sched.procs[pid].state == S.BACKGROUND
    sched.stop(pid)


def test_a_stop_runs_moy_stop_and_a_stop_past_its_limit_is_the_kill(sched):
    keeper = """
moybyte.every(1000, function() end)
function moy_stop() print("saved") end
"""
    since = L.next()
    pid = sched.start(_cart(keeper))
    assert sched.stop(pid)
    p = sched.procs[pid]
    assert p.state == S.EXITED and p.code == 0
    assert _texts(since, pid)[-3:] == ["stop", "saved", "exit 0"]
    stuck = keeper.replace('print("saved")', "while true do end")
    pid = sched.start(_cart(stuck, "t.stuck"))
    t0 = time.monotonic()
    assert sched.stop(pid)
    assert time.monotonic() - t0 < 3
    assert sched.procs[pid].code == S.EXIT_KILLED


def test_the_pause_before_a_vm_stop_leaves_each_lua_service_running(sched, monkeypatch):
    """A Lua service runs on its own task, so the pause before a VM stop
    sends it no TERM and names it among the services the next VM takes back
    (scheduler.py, ACROSS A VM STOP); tests/test_services_vm_stop.py runs one
    through the stop."""
    keeper = """
moybyte.every(1000, function() end)
function moy_stop() print("saved") end
"""
    since = L.next()
    pid = sched.start(_cart(keeper))
    stopped = []
    monkeypatch.setattr(sched, "stop", lambda q: stopped.append(q))
    assert sched.pause() == []
    p = sched.procs[pid]
    assert not stopped and p.state == S.BACKGROUND
    assert sched.kept_rows() == [[pid, "/carts/t.lua.moy", p.native]]
    assert "saved" not in _texts(since, pid)


def test_the_c_side_refuses_what_it_does_not_run():
    with pytest.raises(OSError):
        moycore.svc_stop(12345)
    assert moycore.svc_info(12345) is None
    assert moycore.svc_tasks() is False         # the host steps them


def test_a_message_reaches_its_handler_with_its_arguments_in_place(sched):
    since = L.next()
    pid = sched.start(_cart(
        'moybyte.on("ping", function(who, a, b, c) print(who .. ":" .. tostring(a) .. "|"'
        ' .. tostring(b) .. "|" .. tostring(c)) end)'))
    assert sched.wakes_of(pid) == 1
    assert sched.send(pid, "ping", ("x", None, 5)) is True
    assert sched.send(pid, "pong", ("konami",)) is False        # no wake on it
    assert sched.mail_of(pid) == 1 and sched.svc_mail
    sched.mail()                                # the frame's start serves it
    assert sched.mail_of(pid) == 0
    assert _texts(since, pid)[-1] == "system:x|nil|5"         # the OS's own: from the system


def test_a_message_name_is_any_name_that_fits(sched):
    pid = sched.start(_cart('moybyte.on("lora", function() end)', "t.lora"))
    assert sched.procs[pid].state == S.BACKGROUND
    sched.stop(pid)
    sched.start(_cart('moybyte.on("%s", function() end)' % ("x" * 40), "t.long"))
    p = [q for q in sched.services() if q.name == "t.long"][0]
    assert p.code == S.EXIT_CRASHED


def test_the_systems_flood_drops_its_own_oldest_and_a_run_serves_a_batch(sched):
    since = L.next()
    pid = sched.start(_cart('moybyte.on("ping", function(who, k, i) print(i) end)'))
    for i in range(moy_evq.SENDER + 3):
        sched.send(pid, "ping", ("open", i))
    assert sched.mail_of(pid) == moy_evq.SENDER
    sched.mail()
    assert _texts(since, pid)[1:] == [str(i) for i in range(3, 3 + moy_evq.BATCH)]
    assert sched.svc_mail                       # the rest: the next frames'
    for _ in range(moy_evq.SENDER):
        sched.mail()
    assert _texts(since, pid)[1:] == [str(i) for i in range(3, moy_evq.SENDER + 3)]



def test_a_flood_stops_at_its_sender_and_another_app_is_answered_in_one_run(sched):
    """A pinger's flood at a Lua service: FULL past MOY_EVQ_SENDER, the
    service never struck, a second app's message the take behind the
    flood's oldest, and a run handing MOY_EVQ_BATCH (moy_evq.h)."""
    since = L.next()
    src = 'moybyte.on("ping", function(who, n) print(who .. ":" .. n) end)'
    cart = dict(_cart(src, "t.svc"), receives=["ping"])
    pid = sched.start(cart)
    p = sched.procs[pid]
    pinger, other = L.spawn("t.pinger"), L.spawn("t.other")
    moy_evq.open(pinger, "t.pinger", "", True)
    moy_evq.open(other, "t.other", "", True)
    took = [moy_evq.send(pinger, "t.svc", "ping", "[%d]" % i)[0] for i in range(100)]
    assert took == [1] * moy_evq.SENDER + [-moy_evq.FULL] * (100 - moy_evq.SENDER)
    assert moy_evq.send(other, "t.svc", "ping", "[-1]")[0] == 1
    sched.poll()                                # one run
    assert _texts(since, pid)[1:] == ["t.pinger:0", "t.other:-1", "t.pinger:1", "t.pinger:2"]
    assert sched.svc_mail                       # the rest: the next frames'
    for _ in range(moy_evq.SENDER):
        sched.mail()
    assert sched.mail_of(pid) == 0 and len(_texts(since, pid)) == 2 + moy_evq.SENDER
    assert (p.state, p.overruns, p.strikes) == (S.BACKGROUND, 0, 0)
    assert moy_evq.send(pinger, "t.svc", "ping", "[0]")[0] == 1    # served: it may send again
    moy_evq.close(pinger)
    moy_evq.close(other)
    sched.stop(pid)

CALLS = """
moybyte.on("go", function()
    print(double(base))
    local v, why = boom()
    print(tostring(v) .. " " .. why)
    print(slow())
    local ok, err = pcall(coroutine.wrap(function() return double(1) end))
    print(tostring(ok) .. " " .. err)
    moybyte.notify("Hi", "there", "star")
end)
"""


def test_console_calls_are_answered_on_the_vms_task(sched, monkeypatch):
    """The names the start was handed answer from the console, a refusal is
    nil and why, the time the console takes is not the callback's, a call
    from a coroutine the service made is an error, and `moybyte.notify`
    posts as the service."""
    notifications.NOTES.reset()
    monkeypatch.setattr(notifications.NOTES, "who", lambda pid: ("t.lua", None, True))
    since = L.next()

    def boom():
        raise ValueError("no")

    env = {"base": 21, "double": lambda x: 2 * x, "boom": boom,
           "slow": lambda: time.sleep(0.4) or "slept"}
    pid = sched.start(_cart(CALLS), env=env)
    sched.send(pid, "go", ())
    sched.mail()
    texts = _texts(since, pid)
    assert texts[1:5] == ["42", "boom: ValueError: no", "nil the console refused boom", "slept"]
    assert texts[5].startswith("false ") and "outside the service's own call" in texts[5]
    assert not [t for t in texts if "strike" in t]
    n = notifications.NOTES.list[0]
    assert (n.summary, n.body, n.icon, n.pid) == ("Hi", "there", "star", pid)
    notifications.NOTES.reset()


ALARMS = """
local late = moybyte.at(os.time() + 3600, function() print("late") end)
moybyte.at(os.time(), function() print("rang") end)
moybyte.cancel(late)
print(late < 0)
"""


def test_an_alarm_rings_once_and_its_service_then_has_no_wakes(sched):
    """`moybyte.at` is the console's alarm (under `alarms`), heard as a
    message; a cancelled one never rings, and once the last has rung the
    service holds no wake and has exited."""
    since = L.next()
    pid = sched.start(_cart(ALARMS))
    for _ in range(5):
        sched.tick(0)
        sched.mail()
        sched.poll()
    assert _texts(since, pid) == ["start t.lua", "true", "rang", "exit 0"]


def test_an_alarm_without_its_permission_is_refused(sched, monkeypatch):
    from runtime import devices
    monkeypatch.setattr(devices, "grants", lambda p: ())
    since = L.next()
    sched.start(_cart('moybyte.at(os.time(), function() end)'))
    sched.poll()
    texts = [t for _s, _t, _l, _q, t in os_log.records(since, 128)]
    assert any("the console refused moybyte.at" in t for t in texts), texts


@pytest.fixture
def desk(tmp_path, monkeypatch):
    boot_on_fixture(monkeypatch)
    ws = build_desktop_ws(tmp_path)
    drv = make_drv(ws)
    ws.open_desk()
    drv.frame(_DT)
    yield ws, drv
    S.SCHED.reset()


def _frames(drv, ms):
    t0 = time.monotonic()
    while (time.monotonic() - t0) * 1000 < ms:
        time.sleep(0.002)
        drv.frame(_DT)


def test_a_lua_service_holds_the_crypto_rows_beside_its_own_names(desk):
    """A service's `moybyte` table is its own C's (every, log, exit) and the
    crypto rows (native/moy_app/moy_app_lua.c) at once: with no permission it
    has no run grant, and the rows need none."""
    ws, drv = desk
    since = L.next()
    pid = S.SCHED.start(_cart(CRYPTO), ws=ws)
    _frames(drv, 40)
    assert _texts(since, pid)[1:4] == ["sha256 ba7816bf", "random 8 function", "ed25519 64 true"]


def test_a_service_runs_on_through_an_app_switch(desk):
    ws, drv = desk
    since = L.next()
    pid = S.SCHED.start(_cart('n = 0\nmoybyte.every(5, function() n = n + 1; print(n) end)'))
    _frames(drv, 40)
    before = len(_texts(since, pid))
    assert before >= 2
    for t in ("Star Catcher", "Brick Siege"):
        open_cart(ws, t)
        _frames(drv, 30)
        win = ws.wm._wins["desktop"]
        if win.saved is not None:
            ws.wm._toggle_max(win)
    ws.open_tasks()
    drv.frame(_DT)
    games = [r for r in ws.procs.rows() if r[2] == "game"]
    assert [r[1] for r in games] == ["Brick Siege", "Star Catcher"]
    ws.tasks_layer._do("Switch to", games[1])
    ws.close_tasks()
    _frames(drv, 40)
    assert ws.cart["title"] == "Star Catcher"
    p = S.SCHED.procs[pid]
    assert p.state == S.BACKGROUND and p.native
    assert len(_texts(since, pid)) > before + 2


def test_the_task_manager_reads_its_exact_heap(desk):
    ws, drv = desk
    pid = S.SCHED.start(_cart('keep = string.rep("x", 40000)\n'
                              'moybyte.every(1000, function() end)'))
    drv.frame(_DT)
    p = S.SCHED.procs[pid]
    heap = moycore.svc_info(p.native)[2]
    assert 40000 < heap < 80000 and p.mem == heap
    row = [r for r in ws.procs.rows() if r[6] == pid][0]
    assert row[1:4] == ("t.lua", "service", "background") and row[5] == heap
    assert tasks_ui.verbs(row) == ["Stop"]
    ws.procs.stop_service(pid)
    assert [r for r in ws.procs.rows() if r[6] == pid] == []


VM_SVC = """
import moycore
h = moycore.svc_start(9, "t.vm", "n = 0\\nmoybyte.every(10, function() n = n + 1; if n == 2 then moybyte.exit(3) end end)")
print(moycore.svc_tasks(), moycore.svc_info(h)[0])
for t in (0, 10, 20):
    moycore.svc_step(t)
i = moycore.svc_info(h)
print(i[0], i[1], i[2], i[5] == "")
moycore.svc_reap(h)
print(moycore.svc_info(h))
"""


VM_MSG = """
import moycore, moy_evq
src = "moybyte.on('go', function(who, k, key) moybyte.exit(day() + #k + #who) end)"
h = moycore.svc_start(9, "t.msg", src, '[{}, ["day"]]')
moycore.svc_step(0)
print(moy_evq.post(9, 1, "go", '["run", null]'), moy_evq.post(9, 1, "stop", '[]'))
print(moycore.svc_info(h)[6:])
moycore.svc_step(1)
t = moy_evq.next_call()
print(t[1:])
moy_evq.answer(t[0], "7")
moycore.svc_step(2)
i = moycore.svc_info(h)
print(i[0], i[1])
moycore.svc_reap(h)
print(moy_evq.has(9))
"""


def _vm(tmp_path, src):
    import subprocess
    from unix_mp import require_unix_mp
    exe = require_unix_mp("moycore", why="the Lua services on the VM a board runs")
    script = tmp_path / "svc.py"
    script.write_text(src)
    done = subprocess.run([str(exe), str(script)], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stdout + done.stderr
    return done.stdout.splitlines()


def test_the_vm_a_board_runs_steps_its_services_alike(tmp_path):
    assert _vm(tmp_path, VM_SVC) == ["False 1", "4 3 0 True", "None"]


def test_the_vm_a_board_runs_routes_messages_and_console_calls(tmp_path):
    assert _vm(tmp_path, VM_MSG) == ["0 5", "(1, 1)", "(9, 'day', '[]')", "4 16", "False"]
