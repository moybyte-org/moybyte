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
    local nonce = string.rep("n", 13)
    local sealed = moybyte.aes_ccm_seal(sk, nonce .. "mesh", 8)
    print("ccm " .. #sealed .. " " .. moybyte.aes_ccm_open(sk, nonce .. sealed, 8)
          .. " " .. moybyte.aes_ctr(sk, string.rep("c", 16) .. moybyte.aes_ctr(sk, string.rep("c", 16) .. "back")))
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


def test_a_service_runs_its_scripts_in_order_each_its_own_chunk(sched):
    """`src_before`, its main, `src_after` (scheduler.lua_chunks): globals
    cross from one script to the next, a `local` does not, and an error
    names the file it is in."""
    since = L.next()
    cart = _cart('print("main " .. SHARED .. " " .. tostring(hidden))\n'
                 'moybyte.after(1, function() moybyte.exit(TAIL) end)')
    cart["src_before"] = [("shared.lua", "SHARED = 41\nlocal hidden = 1")]
    cart["src_after"] = [("tail.lua", "TAIL = 5")]
    pid = sched.start(cart)
    _run(sched, 30)
    p = sched.procs[pid]
    assert p.state == S.EXITED and p.code == 5
    assert "main 41 nil" in _texts(since, pid)

    since = L.next()
    cart["src_before"] = [("shared.lua", "SHARED = 41\nerror('shared broke')")]
    sched.start(cart)
    _run(sched, 30)
    p = sched.services()[-1]
    assert p.code == S.EXIT_CRASHED
    assert any("shared.lua:2: shared broke" in t for t in _texts(since, p.pid))


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
    assert any('moybyte.at needs "alarms"' in t for t in texts), texts


@pytest.fixture
def desk(tmp_path, monkeypatch):
    # The one scheduler starts clean: a service another test left on it keeps
    # a poll wake the sched fixture's _clear has ended, and none is armed again.
    S.SCHED.reset()
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
    crypto rows (native/moy_app/moy_app_lua.c) at once, with no permission:
    the rows need no grant."""
    ws, drv = desk
    since = L.next()
    pid = S.SCHED.start(_cart(CRYPTO), ws=ws)
    _frames(drv, 40)
    assert _texts(since, pid)[1:5] == ["sha256 ba7816bf", "random 8 function", "ed25519 64 true",
                                       "ccm 12 mesh back"]


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


VM_CHUNKS = """
import moycore
h = moycore.svc_start(9, "t.vm", [("A = 2\\nlocal b = 1", "@a.lua"),
                                  ("moybyte.every(1, function() moybyte.exit(A + (b or 0)) end)", None)])
for t in (0, 1, 2):
    moycore.svc_step(t)
print(moycore.svc_info(h)[:2])
moycore.svc_reap(h)
h = moycore.svc_start(9, "t.vm", [("error('no')", "@a.lua"), ("x = 1", None)])
moycore.svc_step(0)
i = moycore.svc_info(h)
print(i[1], i[5])
moycore.svc_reap(h)
"""


def test_the_vm_a_board_runs_takes_a_services_scripts(tmp_path):
    assert _vm(tmp_path, VM_CHUNKS) == ["(4, 2)", "1 a.lua:1: no"]


RADIO = """
local h = moybyte.dev_open("lora", "freq=869525000 sf=11 bw=250000 sync=0x2b preamble=16")
print("open " .. tostring(h & 0x40000000 ~= 0) .. " " .. tostring(select(2, moybyte.dev_open("lora"))))
moybyte.on("lora", function(who, ev, n)
    if ev ~= 1 then return end
    local p = moybyte.dev_read(h)
    print(who .. " heard " .. p .. " " .. n .. " " .. moybyte.dev_get(h, "rssi") .. " "
          .. moybyte.dev_get(h, "snr"))
    print("wrote " .. moybyte.dev_write(h, "pong") .. " " .. #moybyte.dev_read(h))
end)
"""


@pytest.fixture
def radio():
    from runtime import moy_input as mi
    from runtime import moy_lora as N
    from runtime.host import lora_sim
    lora_sim.install()
    yield N, lora_sim
    while N.owner():
        moybyte.end(N.owner())
    lora_sim.remove()


def test_a_lua_service_holds_the_radio_by_handle_and_wakes_on_it(desk, radio):
    """A Lua service under `lora` opens the radio by handle on its own task
    (the kernel's table, native/moy_kernel/moy_dev.h), hears the system's
    message "lora" for each packet the radio heard, reads it through the
    handle with its RSSI and SNR, and sends back."""
    N, sim = radio
    ws, drv = desk
    since = L.next()
    cart = dict(_cart(RADIO), permissions=["lora"])
    pid = S.SCHED.start(cart, have=("lora",), ws=ws)
    _frames(drv, 20)
    assert N.owner() == pid
    assert sim.air(b"ping", 869525000, 11, rssi=-97, snr=-26, bw=250000, sync=0x2B)
    N.step(0)
    _frames(drv, 20)
    assert _texts(since, pid)[1:] == ["open true BUSY",
                                      "system heard ping 4 -97 -26", "wrote 4 0"]
    assert sim.sent()[0] == b"pong"


def test_a_lua_service_without_the_permission_is_denied_the_radio(desk, radio):
    """Under a grant without `lora` the row answers DENIED, and so it does
    under no permission at all (a Lua service's run grant is always made)."""
    N, sim = radio
    ws, drv = desk
    since = L.next()
    src = ('print(moybyte.dev_open and select(2, moybyte.dev_open("lora")))\n'
           'moybyte.every(1000, function() end)')
    a = S.SCHED.start(dict(_cart(src, "t.a"), permissions=["prefs"]), have=("lora",), ws=ws)
    b = S.SCHED.start(_cart(src, "t.b"), have=("lora",), ws=ws)
    _frames(drv, 20)
    assert N.owner() == 0
    assert (_texts(since, a)[1:], _texts(since, b)[1:]) == (["DENIED"], ["DENIED"])


def test_a_lua_app_holds_the_radio_by_handle(tmp_path, radio):
    """A Lua app's run holds the same rows (its role binding, moy_app_lua.c),
    called on the VM's task."""
    import json
    from ws_helpers import build_ws, select_on_home
    N, sim = radio
    d = tmp_path / "carts" / "Radio.moy"
    d.mkdir(parents=True)
    (d / "manifest.json").write_text(json.dumps(
        {"title": "Radio", "canvas": "320x240", "runtime": "lua", "main": "main.lua",
         "moybyte": {"type": "app", "permissions": ["graphics", "lora"]}}))
    (d / "main.lua").write_text(
        'H = moybyte.dev_open("lora", "sf=7")\n'
        'function _update() if H then GOT = moybyte.dev_read(H) end end\n'
        'function _draw() end\n')
    (d / "config.json").write_text("{}")
    ws = build_ws(tmp_path)
    assert select_on_home(ws, "Radio") is not None
    ws.open()
    ws.input.begin_frame()
    ws.frame(_DT)
    pl = ws.run.player
    assert pl.cart_error is None, pl.cart_error
    assert N.owner() == pl.pid
    assert sim.air(b"hi", 868100000, 7)
    N.step(0)
    ws.input.begin_frame()
    ws.frame(_DT)
    assert pl._lua.get_global("GOT") == "hi"


STATUS = """
moybyte.after(1, function()
    H = moybyte.status_show("wifi", 1, 5)
    print("show " .. tostring(H > 0) .. " " .. moybyte.status_show("", 0))
    print("panel " .. moybyte.status_panel(H, "text Uplink\\tup\\nbutton sync Sync now"))
    print("bad " .. moybyte.status_state(H, 9) .. " none " .. moybyte.status_state(99, 0))
end)
moybyte.on("moybyte.status", function(who, item, id, v)
    print(who .. " " .. tostring(item == H) .. " " .. id .. " " .. v)
    print("state " .. moybyte.status_state(H, 2) .. " bare " .. moybyte.status_panel(H))
    print("remove " .. moybyte.status_remove(H) .. " " .. moybyte.status_remove(H))
    moybyte.exit(0)
end)
"""


def test_a_lua_service_shows_a_status_item_and_hears_its_panel(desk):
    """A Lua service's status item (runtime/system/status_items.py), with no
    permission: the floor rows on its own task (show, state, remove, each the
    row's answer: BAD for an empty icon or a state out of range, ABSENT for
    a handle it never had) and its panel through the console call; a press
    reaches it as the system's message "moybyte.status" [item, id, value]."""
    from runtime.status_items import ITEMS, PAUSED
    ws, drv = desk
    since = L.next()
    pid = S.SCHED.start(_cart(STATUS), ws=ws)
    _frames(drv, 60)
    assert _texts(since, pid)[1:] == ["show true -7", "panel 0", "bad -7 none -8"]
    h = next(it.k + 1 for it in ITEMS.slots if it.pid == pid)
    it = ITEMS.of(pid, h)
    assert it is not None and it.state == PAUSED and it.order == 5 and it.section.n == 2
    assert (it.section.label[0], it.section.value[0], it.section.rid[1]) == \
        ("Uplink", "up", "sync")
    it.section._press(1, 1)
    _frames(drv, 60)
    assert _texts(since, pid)[4:] == ["system true sync 1", "state 0 bare 0", "remove 0 -8",
                                       "exit 0"]
    assert ITEMS.of(pid, h) is None and ITEMS.count() == 0
    assert S.SCHED.procs[pid].state == S.EXITED


STATUS_APP = """
H = moybyte.status_show("wifi")
P = moybyte.status_panel(H, "toggle live Live 0")
moybyte.on("moybyte.status", function(who, item, id, v)
    GOT = who .. " " .. tostring(item == H) .. " " .. id .. " " .. v
    S = moybyte.status_state(H, 2)
end)
function _update() if GOT and not R then R = moybyte.status_remove(H) end end
function _draw() end
"""


def test_a_lua_app_shows_a_status_item_and_hears_its_panel(tmp_path):
    """A Lua app's run holds the same names (its role binding on the VM's
    task, status_panel through the run's console calls), and its panel's
    press reaches its `moybyte.on`."""
    import json
    from runtime.status_items import ITEMS, ON
    from ws_helpers import build_ws, select_on_home
    d = tmp_path / "carts" / "Bar.moy"
    d.mkdir(parents=True)
    (d / "manifest.json").write_text(json.dumps(
        {"title": "Bar", "canvas": "320x240", "runtime": "lua", "main": "main.lua",
         "moybyte": {"type": "app", "permissions": ["graphics"]}}))
    (d / "main.lua").write_text(STATUS_APP)
    (d / "config.json").write_text("{}")
    ws = build_ws(tmp_path)
    assert select_on_home(ws, "Bar") is not None
    ws.open()
    ws.input.begin_frame()
    ws.frame(_DT)
    pl = ws.run.player
    assert pl.cart_error is None, pl.cart_error
    lua = pl._lua
    h = lua.get_global("H")
    assert h > 0 and lua.get_global("P") == 0
    it = ITEMS.of(pl.pid, h)
    assert it is not None and it.state == ON and it.section.rid[0] == "live"
    it.section._press(0, 1)
    for _ in range(3):
        ws.input.begin_frame()
        ws.frame(_DT)
    assert lua.get_global("GOT") == "system true live 1"
    assert (lua.get_global("S"), lua.get_global("R")) == (0, 0)
    assert ITEMS.of(pl.pid, h) is None


VM_STATUS = r'''
import sys
sys.path[:0] = [@TREE@]
import json
from array import array
import moy_spine, moy_app, moycore, moy_evq
import moy_log as L
import moybyte
from status_items import ITEMS

app = moy_app.App(moy_spine.Settings(lambda b: None))


def panel(pid, *args):
    was = L.current()
    L.current(pid)
    try:
        return moybyte.status_panel(*args)
    finally:
        L.current(was)


def item(pid):
    ITEMS.sync()
    for it in ITEMS.slots:
        if it.pid == pid:
            return it.k + 1, it.state, it.order, it.section.n
    return None


# a Lua service: the rows on its own task, the panel a console call
SVC = """
moybyte.after(1, function()
    H = moybyte.status_show("wifi", 1, 5)
    print("show " .. tostring(H > 0) .. " " .. moybyte.status_show("", 0))
    print("panel " .. moybyte.status_panel(H, "button sync Sync now"))
end)
moybyte.on("moybyte.status", function(who, item, id, v)
    print(who .. " " .. tostring(item == H) .. " " .. id .. " " .. v)
    print("state " .. moybyte.status_state(H, 2) .. " remove " .. moybyte.status_remove(H)
          .. " " .. moybyte.status_remove(H))
    moybyte.exit(0)
end)
"""
since = L.next()
g = app.grant("t.st", (), run=True, owner=9)
h = moycore.svc_start(9, "t.st", SVC, None, app.run(g, ()))
for t in range(4):
    moycore.svc_step(t * 10)
    req = moy_evq.next_call()
    if req is not None:
        print("call", req[1], req[2])
        moy_evq.answer(req[0], json.dumps(panel(req[1], *json.loads(req[3]))))
print("svc", item(9))
ITEMS.of(9, item(9)[0]).section._press(0, 1)
for t in range(4, 8):
    moycore.svc_step(t * 10)
print("svc", item(9), moycore.svc_info(h)[:2])
for r in L.read(since, 32):
    if r[3] == 9:
        print("log", r[4])
moycore.svc_reap(h)

# a Lua app: the rows on the VM's task, the panel the run's console call
APP = """
H = moybyte.status_show("wifi")
BAD = moybyte.status_state(H, 9)
local n = 0
function _update()
    n = n + 1
    if n == 2 then S = moybyte.status_state(H, 2) end
    if n == 3 then R = moybyte.status_remove(H); R2 = moybyte.status_remove(H) end
end
function _draw() end
"""
W, H = 96, 64
fb = bytearray(W * H * 2)
snap = array("i", bytearray(4 * moycore.SNAP_LEN))
aq = array("i", bytearray(4 * (1 + moycore.AQ_SLOTS * moycore.AQ_MAX)))
sheet = bytearray(128 * 256)
g = app.grant("t.app", (), run=True, owner=10)
moycore.run_begin(fb, W, H, None, sheet, None, 0, 0, snap, aq, None, None, None, True,
                  app.run(g, ()))
print("load", moycore.load(((APP, "@cart"),)))
moycore.tick(0.03125)
h = moycore.get_global("H")
print("app", h == item(10)[0], moycore.get_global("BAD"), item(10))
print("panel", panel(10, h, "toggle live Live 0"), panel(10, h, "nonsense"),
      panel(10, 99, ""), panel(9, h, ""))
ITEMS.of(10, h).section._press(0, 1)
print("heard", moy_evq.take(10)[2:] == ("moybyte.status", json.dumps([h, "live", 1])))
moycore.tick(0.03125)
print("app", item(10), moycore.get_global("S"))
moycore.tick(0.03125)
print("app", moycore.get_global("R"), moycore.get_global("R2"), item(10))
moycore.close()
'''


def test_on_the_vm_a_board_runs_lua_status_items(tmp_path):
    """The status rows of a Lua service and a Lua app on the board's
    bindings: the C rows answer as on the host, the panel is the console
    call's (moybyte.status_panel, as the process), and a press posts the
    item's process "moybyte.status" [item, id, value]."""
    import subprocess
    from unix_mp import require_unix_mp, runtime_tree
    exe = require_unix_mp("moycore", "moy_app", "moy_evq", "moy_log",
                          why="the Lua status items on the VM a board runs")
    script = tmp_path / "status.py"
    script.write_text(VM_STATUS.replace("@TREE@", repr(runtime_tree())))
    done = subprocess.run([str(exe), str(script)], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stdout + done.stderr
    assert done.stdout.splitlines() == [
        "call 9 moybyte.status_panel", "svc (1, 1, 5, 1)", "svc None (4, 0)",
        "log show true -7", "log panel 0", "log system true sync 1",
        "log state 0 remove 0 -8",
        "load None", "app True -7 (1, 0, 0, 0)", "panel 0 -7 -8 -8", "heard True",
        "app (1, 2, 0, 1) 0", "app 0 -8 None"], done.stdout
