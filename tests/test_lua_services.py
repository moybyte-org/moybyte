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

from runtime import moybyte, moycore, notifications, os_log, scheduler as S
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
    assert p.code == S.EXIT_KILLED
    texts = _texts(since, pid)
    assert [t for t in texts if "strike" in t] == [
        "a callback ran past 300 ms: strike %d" % i for i in (1, 2, 3)]
    assert "struck out" in texts


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


def test_the_c_side_refuses_what_it_does_not_run():
    with pytest.raises(OSError):
        moycore.svc_stop(12345)
    assert moycore.svc_info(12345) is None
    assert moycore.svc_tasks() is False         # the host steps them


def test_a_message_reaches_its_handler_with_its_arguments_in_place(sched):
    since = L.next()
    pid = sched.start(_cart(
        'moybyte.on("achieve", function(a, b, c) print(tostring(a) .. "|" .. tostring(b)'
        ' .. "|" .. tostring(c)) end)'))
    assert sched.wakes_of(pid) == 1
    assert sched.send(pid, "achieve", ("x", None, 5)) is True
    assert sched.send(pid, "award", ("konami",)) is False       # no wake on it
    assert sched.mail_of(pid) == 1 and sched.svc_mail
    sched.mail()                                # the frame's start serves it
    assert sched.mail_of(pid) == 0
    assert _texts(since, pid)[-1] == "x|nil|5"


def test_the_message_names_are_the_os_ones(sched):
    for name in moybyte.MESSAGES:
        pid = sched.start(_cart('moybyte.on("%s", function() end)' % name, "t." + name))
        assert sched.procs[pid].state == S.BACKGROUND
    sched.start(_cart('moybyte.on("lora", function() end)', "t.lora"))
    p = sched.find("t.lora") or [q for q in sched.services() if q.name == "t.lora"][0]
    assert p.code == S.EXIT_CRASHED


def test_a_full_mailbox_drops_the_oldest(sched):
    since = L.next()
    pid = sched.start(_cart('moybyte.on("achieve", function(k, i) print(i) end)'))
    for i in range(moybyte.MAIL_MAX + 3):
        sched.send(pid, "achieve", ("open", i))
    assert sched.mail_of(pid) == moybyte.MAIL_MAX
    sched.mail()
    assert _texts(since, pid)[1:] == [str(i) for i in range(3, moybyte.MAIL_MAX + 3)]


CALLS = """
moybyte.on("achieve", function()
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
    sched.send(pid, "achieve", ())
    sched.mail()
    texts = _texts(since, pid)
    assert texts[1:5] == ["42", "boom: ValueError: no", "nil the console refused boom", "slept"]
    assert texts[5].startswith("false ") and "outside the service's own call" in texts[5]
    assert not [t for t in texts if "strike" in t]
    n = notifications.NOTES.list[0]
    assert (n.summary, n.body, n.icon, n.pid) == ("Hi", "there", "star", pid)
    notifications.NOTES.reset()


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
import moycore
src = "moybyte.on('achieve', function(k, key) moybyte.exit(day() + #k) end)"
h = moycore.svc_start(9, "t.msg", src, '[{}, ["day"]]')
moycore.svc_step(0)
print(moycore.svc_send(h, "achieve", '["run", null]'), moycore.svc_send(h, "award", '[]'))
print(moycore.svc_info(h)[6:])
moycore.svc_step(1)
print(moycore.svc_call(h))
moycore.svc_answer(h, "7")
moycore.svc_step(2)
i = moycore.svc_info(h)
print(i[0], i[1])
moycore.svc_reap(h)
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
    assert _vm(tmp_path, VM_MSG) == ["True False", "(1, 1)", "('day', '[]')", "4 10"]
