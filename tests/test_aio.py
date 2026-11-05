"""The shell's asyncio loop on the cooperative scheduler (runtime/system/aio.py,
docs/os_2026-11/processes.md section 3): a task's step is its process's
callback (run as it, its cost counted, its rungs), `sleep`/`sleep_ms` are
timers, a stream over a socket waits on the poller, a service runs while it
holds a task and its end cancels them, and a task's unawaited exception is
its process's crash. CPython's asyncio here with a fake guard; the last test
runs MicroPython's own asyncio under the real guard on the desktop
MicroPython, both builds, and holds an idle asyncio service's tick to no
allocation."""

import socket
import subprocess
import time

import pytest

from runtime import aio, moybyte, os_log, scheduler as S
from runtime import moy_log as L
from test_scheduler import Guard, _clear
from unix_mp import find_unix_mp, require_unix_mp, runtime_tree


@pytest.fixture
def sched():
    wakes = moybyte.WAKES
    _clear(wakes)
    s = S.Scheduler(wakes, Guard())
    yield s
    s.reset()
    for _ in range(5):              # the cancelled tasks' last steps
        s.tick(10000)
    _clear(wakes)
    wakes.ended = S.SCHED._ended


def _cart(src, cid="t.aio"):
    return {"id": cid, "title": cid, "type": "service", "src": src,
            "runtime": "python", "permissions": [], "required": [],
            "path": "/carts/%s.moy" % cid}


def _ticks(s, n, ms=3, slack=0):
    for _ in range(n):
        time.sleep(ms / 1000)
        s.tick(slack)


COUNTER = '''
import asyncio
import moy_log
seen = []
async def main():
    for _ in range(4):
        seen.append(moy_log.current())
        await asyncio.sleep_ms(1)
asyncio.run(main())
'''


def test_a_tasks_step_is_its_processs_callback(sched):
    pid = sched.start(_cart(COUNTER.replace("import moy_log", "from runtime import moy_log")))
    p = sched.procs[pid]
    assert p.state == S.BACKGROUND and sched.wakes.count(pid) == 1
    calls = p.calls                         # its start
    _ticks(sched, 20)
    assert p.state == S.EXITED and p.code == S.EXIT_OK   # its last task ended
    assert p.calls - calls == 5             # four resumes and the last
    assert sched.wakes.count(pid) == 0 and aio.count(pid) == 0
    assert aio.LOOP.w is None               # no task: the loop holds no wake


def test_each_step_runs_as_its_process_under_the_rungs(sched):
    src = '''
import asyncio
from runtime import moy_log
seen = []
async def main():
    while True:
        seen.append(moy_log.current())
        spend(30000)
        await asyncio.sleep(0)
asyncio.run(main())
'''
    since = L.next()
    pid = sched.start(_cart(src), env={"spend": sched.guard.spend})
    p = sched.procs[pid]
    _ticks(sched, 6)
    assert set(p.ns["seen"]) == {pid}
    assert p.strikes == S.MOY_BG_STRIKES and p.state == S.SUSPENDED
    n = len(p.ns["seen"])
    _ticks(sched, 4)
    assert len(p.ns["seen"]) == n           # a suspended process's steps wait
    p.ns["spend"] = lambda us: None
    assert sched.resume(pid)
    _ticks(sched, 3)
    assert len(p.ns["seen"]) > n and p.state == S.BACKGROUND
    texts = [t for _s, _t, _l, q, t in os_log.records(since, 64) if q == pid]
    assert "suspended" in texts and "resumed" in texts


def test_sleep_is_a_timer_no_earlier_than_asked(sched):
    src = '''
import asyncio, time
woke = []
async def main():
    t0 = time.monotonic()
    await asyncio.sleep(0.02)
    woke.append(time.monotonic() - t0)
asyncio.run(main())
'''
    pid = sched.start(_cart(src))
    p = sched.procs[pid]
    ns = p.ns
    _ticks(sched, 3, ms=2)
    assert ns["woke"] == []
    _ticks(sched, 15, ms=3)
    assert ns["woke"] and ns["woke"][0] >= 0.02
    assert p.state == S.EXITED


def test_its_stop_cancels_its_tasks_each_once_to_clean_up(sched):
    src = '''
import asyncio
cleaned = []
async def forever(i):
    try:
        while True:
            await asyncio.sleep(1)
    finally:
        cleaned.append(i)
async def main():
    asyncio.create_task(forever(1))
    await forever(0)
asyncio.run(main())
'''
    pid = sched.start(_cart(src))
    p = sched.procs[pid]
    ns = p.ns
    _ticks(sched, 3)
    assert aio.count(pid) == 2
    assert sched.stop(pid) and p.code == S.EXIT_OK
    assert aio.count(pid) == 0
    _ticks(sched, 3)
    assert sorted(ns["cleaned"]) == [0, 1]
    assert aio.LOOP.w is None


def test_an_unawaited_exception_is_its_processs_crash(sched):
    since = L.next()
    src = '''
import asyncio
async def boom():
    raise ValueError("bad")
async def main():
    asyncio.create_task(boom())
    await asyncio.sleep(5)
asyncio.run(main())
'''
    pid = sched.start(_cart(src))
    _ticks(sched, 4)
    p = sched.procs[pid]
    assert p.state == S.EXITED and p.code == S.EXIT_CRASHED
    crit = [t for _s, _t, lvl, q, t in os_log.records(since, 64)
            if q == pid and lvl == L.CRIT]
    assert crit and "ValueError: bad" in crit[0]
    assert aio.count(pid) == 0


def test_an_awaited_exception_is_the_awaiters(sched):
    src = '''
import asyncio
caught = []
async def boom():
    raise ValueError("bad")
async def main():
    try:
        await asyncio.gather(boom())
    except ValueError as e:
        caught.append(str(e))
    await asyncio.sleep(5)
asyncio.run(main())
'''
    pid = sched.start(_cart(src))
    p = sched.procs[pid]
    _ticks(sched, 4)
    assert p.state == S.BACKGROUND and p.ns["caught"] == ["bad"]


def test_an_aborted_step_ends_its_task_and_strikes(sched):
    src = '''
import asyncio
ended = []
async def spin():
    await asyncio.sleep(0)
async def main():
    try:
        await asyncio.create_task(spin())
    except asyncio.CancelledError:
        ended.append("cancelled")
    await asyncio.sleep(5)
asyncio.run(main())
'''
    pid = sched.start(_cart(src))
    p = sched.procs[pid]
    _ticks(sched, 1)
    sched.guard.abort = True
    _ticks(sched, 1)
    sched.guard.abort = False
    _ticks(sched, 3)
    assert p.strikes == 1 and p.state == S.BACKGROUND
    assert p.ns["ended"] == ["cancelled"]


def test_a_systemexit_in_a_step_is_its_exit(sched):
    src = '''
import asyncio, sys
async def main():
    await asyncio.sleep(0)
    sys.exit(4)
asyncio.run(main())
'''
    pid = sched.start(_cart(src))
    _ticks(sched, 4)
    p = sched.procs[pid]
    assert p.state == S.EXITED and p.code == 4


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


STREAM = '''
import asyncio
got = []
async def echo(r, w):
    line = await r.readline()
    w.write(line.upper())
    await w.drain()
    w.close()
async def main():
    srv = await asyncio.start_server(echo, "127.0.0.1", PORT)
    r, w = await asyncio.open_connection("127.0.0.1", PORT)
    w.write(b"hello\\n")
    await w.drain()
    got.append(await r.readline())
    w.close()
    await w.wait_closed()
    srv.close()
    await srv.wait_closed()
asyncio.run(main())
'''


def test_a_stream_over_a_socket_waits_on_the_poller(sched):
    pid = sched.start(_cart(STREAM.replace("PORT", str(_free_port()))))
    p = sched.procs[pid]
    ns = p.ns
    for _ in range(200):
        if p.state == S.EXITED:
            break
        _ticks(sched, 1, ms=1)
    assert ns["got"] == [b"HELLO\n"]
    assert p.state == S.EXITED and p.code == S.EXIT_OK


# -- MicroPython's asyncio, under the real guard ------------------------------

VM_SCRIPT = r'''
import sys
sys.path[:0] = [@TREE@]
import gc, time
import moy_input as I
import moybyte, aio
import scheduler as S
moybyte.bind(I.HostInputTable())
s = S.SCHED

def ticks(n, ms=2):
    for _ in range(n):
        time.sleep_ms(ms)
        s.tick(0)

COUNT = """
import asyncio, moy_log
seen = []
async def main():
    for _ in range(4):
        seen.append(moy_log.current())
        await asyncio.sleep_ms(1)
asyncio.run(main())
"""
pid = s.start({"id": "t.count", "title": "count", "src": COUNT, "permissions": []})
p = s.procs[pid]
ns = p.ns
c0 = p.calls
ticks(20)
print("COUNT", p.state, p.code, p.calls - c0, set(ns["seen"]) == {pid}, p.cost_us > 0,
      aio.LOOP.w is None)

STREAM = @STREAM@
pid = s.start({"id": "t.stream", "title": "stream", "src": STREAM, "permissions": []})
p = s.procs[pid]
ns = p.ns
for _ in range(300):
    if p.state == "exited":
        break
    ticks(1, 1)
print("STREAM", ns["got"], p.state, p.code)

CRASH = """
import asyncio
async def boom():
    raise ValueError("bad")
async def main():
    asyncio.create_task(boom())
    await asyncio.sleep(5)
asyncio.run(main())
"""
pid = s.start({"id": "t.crash", "title": "crash", "src": CRASH, "permissions": []})
p = s.procs[pid]
ticks(4)
print("CRASH", p.state, p.code, aio.count(pid))

S.MOY_BG_ABORT_US = 30000
SPIN = """
import asyncio
async def spin():
    while True:
        pass
async def main():
    try:
        await asyncio.create_task(spin())
    except asyncio.CancelledError:
        ended.append(1)
    await asyncio.sleep(5)
ended = []
asyncio.run(main())
"""
pid = s.start({"id": "t.spin", "title": "spin", "src": SPIN, "permissions": []})
p = s.procs[pid]
ns = p.ns
ticks(4)
print("ABORT", p.strikes, ns["ended"], p.state)
s.stop(pid)

CLEAN = """
import asyncio
cleaned = []
async def forever():
    try:
        while True:
            await asyncio.sleep(1)
    finally:
        cleaned.append(1)
asyncio.run(forever())
"""
pid = s.start({"id": "t.clean", "title": "clean", "src": CLEAN, "permissions": []})
p = s.procs[pid]
ns = p.ns
ticks(3)
s.stop(pid)
ticks(3)
print("CLEAN", p.code, ns["cleaned"], aio.count(pid), aio.LOOP.w is None)

IDLE = """
import asyncio
n = [0]
async def main():
    while True:
        n[0] += 1
        await asyncio.sleep_ms(400)
asyncio.run(main())
"""
pid = s.start({"id": "t.idle", "title": "idle", "src": IDLE, "permissions": []})
p = s.procs[pid]
ticks(5)
gc.collect()
deltas = []
t0 = time.ticks_ms()
while time.ticks_diff(time.ticks_ms(), t0) < 300:
    time.sleep_ms(1)
    a = gc.mem_alloc()
    s.tick(0)
    deltas.append(gc.mem_alloc() - a)
print("IDLE", sum(deltas), len(deltas) > 50, p.state)
'''


def test_on_micropythons_asyncio_under_the_real_guard(tmp_path):
    exe = require_unix_mp("moy_input", "moy_log",
                          why="MicroPython's asyncio on the scheduler")
    exes = [("desktop", exe)]
    board = find_unix_mp("moy_input", "moy_log", board_model=True)
    if board is not None:
        exes.append(("board", board))
    for tag, x in exes:
        stream = STREAM.replace("PORT", str(_free_port()))
        script = tmp_path / ("aio_%s.py" % tag)
        script.write_text(VM_SCRIPT.replace("@TREE@", repr(runtime_tree()))
                          .replace("@STREAM@", repr(stream)))
        done = subprocess.run([str(x), str(script)], capture_output=True, text=True,
                              timeout=120)
        out = done.stdout
        assert done.returncode == 0, (tag, out + done.stderr)
        assert "COUNT exited 0 5 True True True\n" in out, (tag, out)
        assert "STREAM [b'HELLO\\n'] exited 0\n" in out, (tag, out)
        assert "CRASH exited 1 0\n" in out, (tag, out)
        assert "ABORT 1 [1] background\n" in out, (tag, out)
        assert "CLEAN 0 [1] 0 True\n" in out, (tag, out)
        assert "IDLE 0 True background\n" in out, (tag, out)
