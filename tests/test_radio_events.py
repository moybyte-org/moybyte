"""Connectivity events (docs/os_2026-11/between_apps.md section 4): WiFi and
BLE coming up and going down are NET and BLE records on the input table's
ring source "radio" (native/moy_input/moy_input.h, the radios), posted by
the drivers on a change and only on one; one router of the shell's reads
that ring and routes each change as the message "net" or "ble" from the
system, `(up, why)`, to every process that listens on it, whatever its tier
(runtime/system/moybyte.py, THE RADIOS; native/moy_kernel/moy_evq.h). The
host's stand-ins post as the boards' drivers do: FakeWifi's link, and the BLE
central's machine (moy_hid.c) as it starts and stops."""

import subprocess
from array import array

import pytest

from runtime import moybyte, os_log, scheduler as S
from runtime import moy_input as mi
from runtime import moy_log as L
from runtime.host_api import FakeWifi
from test_scheduler import Guard, _clear
from unix_mp import require_unix_mp, runtime_tree

NET, BLE = mi.EV_NET, mi.EV_BLE
DEPTH = 8                                           # MOY_INPUT_RADIO_DEPTH


@pytest.fixture
def table():
    t = mi.InputTable()
    was = moybyte.WAKES.table
    _clear(moybyte.WAKES)
    moybyte.WAKES.table = None
    moybyte.bind(t)
    yield t
    _clear(moybyte.WAKES)
    moybyte.WAKES.table = None
    if was is not None:
        moybyte.bind(was)


@pytest.fixture
def sched(table):
    wakes = moybyte.WAKES
    s = S.Scheduler(wakes, Guard())
    yield s
    s.reset()
    _clear(wakes)
    wakes.ended = S.SCHED._ended
    wakes.opener = S.SCHED._open


def _records(t, r):
    buf = array("i", [0] * (moybyte.EV_FIELDS * 16))
    n = t.events(buf, r)
    F = moybyte.EV_FIELDS
    return [(buf[i * F + moybyte.EV_KIND], buf[i * F + moybyte.EV_A], buf[i * F + moybyte.EV_B])
            for i in range(n)]


def _as(pid, fn):
    was = L.current()
    L.current(pid)
    try:
        return fn()
    finally:
        L.current(was)


def _frame(s):
    """A frame's background half: the tick (the router), then the next
    frame's start (the messages it queued)."""
    s.tick(10000)
    s.mail()


# -- the ring --------------------------------------------------------------------------------

def test_a_change_is_one_record_and_only_a_change_is_one():
    t = mi.InputTable()
    src = t.radio_source()
    assert t.radio_source() == src
    r = t.reader(1 << NET | 1 << BLE, src)
    assert t.radio(NET, True) is True
    assert t.radio(NET, True) is False             # already up: nothing posted
    assert t.radio(BLE, True) is True
    assert t.radio(NET, False, 8) is True
    assert t.radio(NET, False, 2) is False
    buf = array("i", [0] * moybyte.EV_FIELDS)
    assert t.events(buf, t.reader(1 << BLE, src)) == 0
    t.radio(BLE, False)
    t.events(buf, r)
    assert t.src_name(buf[moybyte.EV_SRC]) == "radio"
    assert _records(t, r) == [(BLE, 1, 0), (NET, 0, 8), (BLE, 0, 0)]
    assert not t.radio_up(BLE) and not t.radio_up(NET)
    t.radio(BLE, True)
    assert t.radio_up(BLE)
    assert not t.radio_up(mi.EV_LORA)
    with pytest.raises(ValueError):
        t.radio(mi.EV_LORA, True)


def test_a_change_before_the_first_ask_is_kept_and_not_posted():
    t = mi.InputTable()
    assert t.radio(NET, True) is True
    src = t.radio_source()
    r = t.reader(1 << NET, src)
    assert t.radio_up(NET) and _records(t, r) == []
    t.radio(NET, False, 201)
    assert _records(t, r) == [(NET, 0, 201)]


def test_a_reader_that_fell_behind_reads_a_gap_then_the_latest():
    t = mi.InputTable()
    src = t.radio_source()
    r = t.reader(1 << NET | 1 << mi.EV_LOST, src)
    for i in range(DEPTH + 2):
        t.radio(NET, i % 2 == 0, i)
    got = _records(t, r)
    assert got[0][0] == mi.EV_LOST and len(got) == DEPTH + 1
    assert got[-1] == (NET, 0, DEPTH + 1)


# -- Python wakes ----------------------------------------------------------------------------

def test_every_process_on_net_hears_each_change_through_one_reader(table, sched):
    a, b = L.spawn("t.a"), L.spawn("t.b")
    heard = {a: [], b: []}
    _as(a, lambda: moybyte.on("net", lambda who, up, why: heard[a].append((who, up, why))))
    _as(b, lambda: moybyte.on("net", lambda who, up, why: heard[b].append((up, why))))
    router = moybyte.WAKES.radio
    assert router is not None and router[1] == moybyte.SHELL
    assert sum(1 for w in moybyte.WAKES.list if w is router) == 1
    table.radio(NET, True)
    table.radio(BLE, True)                          # nobody waits on "ble"
    _frame(sched)
    table.radio(NET, False, 8)
    _frame(sched)
    assert heard[a] == [("system", True, 0), ("system", False, 8)]
    assert heard[b] == [(True, 0), (False, 8)]


def test_ble_is_its_own_name(table, sched):
    pid = L.spawn("t.ble")
    heard = []
    _as(pid, lambda: moybyte.on("ble", lambda who, up, why: heard.append((up, why))))
    table.radio(NET, True)
    table.radio(BLE, True)
    _frame(sched)
    assert heard == [(True, 0)]


def test_the_router_lives_while_somebody_waits(table, sched):
    pid = L.spawn("t.net")
    wid = _as(pid, lambda: moybyte.on("net", lambda who, up, why: None))
    w = moybyte.WAKES.radio
    assert w is not None
    readers = [table.reader() for _ in range(7)]    # the router holds the eighth
    with pytest.raises(OSError):
        table.reader()
    assert moybyte.cancel(wid)
    assert moybyte.WAKES.radio is None and w not in moybyte.WAKES.list
    readers.append(table.reader())                  # its reader went back
    for r in readers:
        table.reader_free(r)
    _as(pid, lambda: moybyte.on("ble", lambda who, up, why: None))
    assert moybyte.WAKES.radio is not None
    moybyte.end(pid)
    assert moybyte.WAKES.radio is None


def test_a_queue_that_listens_keeps_the_router_and_hears_the_change(table, sched):
    """A process with no Python wake that listens on "net" on its own queue
    (a compiled app's moybyte_on, runtime/shell/shell_servers.py's
    ConsoleCalls) keeps the router while it listens and hears each change as
    the system's message; its end drops the router."""
    from runtime import moy_evq
    pid = L.spawn("t.compiled")
    assert moy_evq.open(pid, "t.compiled", "", False) == moy_evq.OK
    assert moy_evq.listen(pid, "net", True) == moy_evq.OK
    moybyte.WAKES.radio_sync()
    assert moybyte.WAKES.radio is not None
    table.radio(NET, True)
    _frame(sched)
    frm, _app, name, args = moy_evq.take(pid)
    assert (name, args) == ("net", "[true, 0]") and frm <= moybyte.SHELL
    moybyte.end(pid)
    assert moybyte.WAKES.radio is None


def test_a_new_table_takes_the_router_along(table, sched):
    pid = L.spawn("t.net")
    heard = []
    _as(pid, lambda: moybyte.on("net", lambda who, up, why: heard.append(up)))
    other = mi.InputTable()
    moybyte.bind(other)
    table.radio(NET, True)                          # the old table: nobody reads it
    other.radio(NET, True)
    _frame(sched)
    assert heard == [True]


def test_the_host_link_posts_as_the_driver_does(table, sched):
    pid = L.spawn("t.net")
    heard = []
    _as(pid, lambda: moybyte.on("net", lambda who, up, why: heard.append((up, why))))
    wifi = FakeWifi()
    wifi.connect("Home WiFi", "pw")
    _frame(sched)
    wifi.radio_off()                                # the last holder let go
    wifi.disconnect()
    _frame(sched)
    assert heard == [(True, 0), (False, 0)]
    assert not table.radio_up(NET)


class _Stack:
    def __getattr__(self, name):
        return lambda *a: 0

    def ms(self):
        return 1000


def test_the_ble_central_posts_its_start_and_stop(table, sched):
    pid = L.spawn("t.ble")
    heard = []
    _as(pid, lambda: moybyte.on("ble", lambda who, up, why: heard.append(up)))
    m = mi.HidMachine(table, _Stack())
    m.started(True)
    _frame(sched)
    m.stopped()
    m.started(False, "NimBLE did not start")       # down already: nothing
    _frame(sched)
    assert heard == [True, False]


# -- Lua services ----------------------------------------------------------------------------

def _lua(src, cid="t.radio"):
    return {"id": cid, "title": cid, "type": "service", "runtime": "lua", "src": src,
            "permissions": [], "required": [], "path": "/carts/%s.moy" % cid}


def test_a_lua_service_hears_the_same_names_and_arguments(table, sched):
    since = L.next()
    pid = sched.start(_lua(
        'moybyte.on("net", function(who, up, why) print("net " .. tostring(up) .. " " .. why) end)\n'
        'moybyte.on("ble", function(who, up, why) print(who .. " ble " .. tostring(up) .. " " .. why) end)'))
    assert moybyte.WAKES.radio is not None          # kept while a Lua service lives
    table.radio(NET, True)
    _frame(sched)
    table.radio(BLE, True)
    table.radio(NET, False, 15)
    _frame(sched)
    texts = [t for _s, _t, _l, q, t in os_log.records(since, 64) if q == pid]
    assert texts[1:] == ["net true 0", "system ble true 0", "net false 15"]
    sched.stop(pid)
    sched.poll()
    assert moybyte.WAKES.radio is None



# -- the VM a board runs -------------------------------------------------------------------

VM_SCRIPT = r'''
import sys
sys.path[:0] = [@TREE@]
import moy_input as I
import moybyte
import scheduler
t = I.HostInputTable()
moybyte.bind(t)
heard = []
wid = moybyte.on("net", lambda who, up, why: heard.append((up, why)))
print("ROUTER", moybyte.WAKES.radio is not None)
print("CHANGED", t.radio(I.EV_NET, True), t.radio(I.EV_NET, True), t.radio_up(I.EV_NET))
t.radio(I.EV_NET, False, 8)
out = [0] * 4
w = moybyte.WAKES
for i in range(w.due(0, out)):
    w.call(out[i])
scheduler.SCHED.mail()
for i in range(w.due(0, out)):
    w.call(out[i])
scheduler.SCHED.mail()
print("HEARD", heard)
moybyte.cancel(wid)
print("GONE", w.radio is None)
'''


def test_the_radios_route_alike_on_the_vm_a_board_runs(tmp_path):
    exe = require_unix_mp("moy_input", why="the radios on the VM a board runs")
    script = tmp_path / "radio.py"
    script.write_text(VM_SCRIPT.replace("@TREE@", repr(runtime_tree())))
    done = subprocess.run([str(exe), str(script)], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stdout + done.stderr
    assert done.stdout.splitlines() == [
        "ROUTER True", "CHANGED True False True", "HEARD [(True, 0), (False, 8)]", "GONE True"]
