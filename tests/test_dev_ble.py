"""The `ble` device API (runtime/system/dev_ble.py over
native/moy_input/moy_gatt.h; docs/os_2026-11/hardware.md section 1) on the
host's stand-in peripherals (runtime/host/gatt_sim.py): a scan with RSSI and
names, a connection, a characteristic found by a 16-bit and a 128-bit UUID,
read, written and subscribed to, a beacon advertised, every step an event on
the process's wake; one holder, the sweep dropping its connection, the
permission, and its absence where the console's BLE host is down."""

import errno

import pytest

from runtime import app_perms as P
from runtime import devices, moybyte
from runtime import moy_gatt as N
from runtime import moy_input as mi
from runtime import moy_log as L
from runtime.host import gatt_sim

HRM = "c0:ff:ee:00:00:01"
LAMP = "c0:ff:ee:00:00:02"
NUS_RX = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"


@pytest.fixture(autouse=True)
def air():
    devices.grants = devices.console = None     # a console an earlier test built
    gatt_sim.install()
    gatt_sim.device(HRM, "Pulse", rssi=-48)
    gatt_sim.chr(HRM, 0x2A37, 0x0010, b"\x00\x48")             # heart rate measurement
    gatt_sim.chr(HRM, 0x2A19, 0x0020, b"\x5a")                 # battery level
    gatt_sim.device(LAMP, "Lamp", rssi=-71)
    gatt_sim.chr(LAMP, NUS_RX, 0x0030, b"")
    was = moybyte.WAKES.table
    moybyte.bind(mi.kernel())
    yield
    while N.owner():
        moybyte.end(N.owner())
    devices.grants = None
    gatt_sim.remove()
    moybyte.WAKES.table = None
    if was is not None:
        moybyte.bind(was)


def _as(pid, fn):
    was = L.current()
    L.current(pid)
    try:
        return fn()
    finally:
        L.current(was)


def _wake(pid):
    out = [0] * 8
    for wid in out[:moybyte.WAKES.due(0, out)]:
        _as(pid, lambda: moybyte.WAKES.call(wid))


def _errno(fn):
    with pytest.raises(OSError) as e:
        fn()
    return e.value.errno


def test_a_scan_connect_find_read_and_notify_round_trip():
    pid = L.spawn("t.ble")
    ev = []
    ble = moybyte.ble
    _as(pid, lambda: ble.on(lambda *e: ev.append(e)))
    _as(pid, ble.scan)
    _wake(pid)
    assert [e[0] for e in ev] == [ble.EV_ADV, ble.EV_ADV, ble.EV_SCAN_DONE]
    assert _as(pid, ble.devices) == [(HRM, -48, "Pulse"), (LAMP, -71, "Lamp")]

    _as(pid, lambda: ble.connect(HRM))
    _as(pid, lambda: ble.find(0x2A19))
    _wake(pid)
    assert ev[-2] == (ble.EV_CONNECTED, 1, 0) and ev[-1] == (ble.EV_FOUND, 0x20, 0)
    assert _as(pid, ble.connected)
    _as(pid, lambda: ble.read(0x20))
    _wake(pid)
    buf = bytearray(8)
    assert ev[-1] == (ble.EV_READ, 1, 0) and _as(pid, lambda: ble.value(buf)) == 1
    assert buf[0] == 90

    _as(pid, lambda: ble.subscribe(0x10))
    assert gatt_sim.notify(HRM, 0x10, b"\x00\x4c")
    _wake(pid)
    assert ev[-1][0] == ble.EV_NOTIFY and ev[-1][1] == 0x10
    assert _as(pid, lambda: ble.notified(buf)) == (2, 0x10) and buf[1] == 0x4c
    assert _as(pid, lambda: ble.notified(buf)) is None

    _as(pid, lambda: ble.find(0x9999))
    _wake(pid)
    assert ev[-1] == (ble.EV_FOUND, 0, -errno.ENOENT)
    _as(pid, ble.disconnect)
    _wake(pid)
    assert ev[-1][0] == ble.EV_DISCONNECTED and not _as(pid, ble.connected)


def test_a_write_by_128_bit_uuid_reaches_the_peripheral():
    pid = L.spawn("t.ble")
    ble = moybyte.ble
    _as(pid, lambda: ble.connect(LAMP))
    ev = []
    _as(pid, lambda: ble.on(lambda *e: ev.append(e)))
    _as(pid, lambda: ble.find(NUS_RX))
    _wake(pid)
    assert ev == [(ble.EV_FOUND, 0x30, 0)]
    _as(pid, lambda: ble.write(0x30, b"on"))
    _as(pid, lambda: ble.write(0x30, b"dim", response=False))
    _wake(pid)
    assert ev[-1] == (ble.EV_WRITTEN, 0, 0) and len(ev) == 2
    assert gatt_sim.written(LAMP, 0x30) == b"dim"


def test_a_connection_to_nobody_times_out_and_verbs_need_one():
    pid = L.spawn("t.ble")
    ble = moybyte.ble
    ev = []
    _as(pid, lambda: ble.on(lambda *e: ev.append(e)))
    assert _errno(lambda: _as(pid, lambda: ble.read(1))) == errno.ENOTCONN
    _as(pid, lambda: ble.connect("de:ad:00:00:00:00"))
    _wake(pid)
    assert ev[-1] == (ble.EV_CONNECTED, 0, -errno.ETIMEDOUT)


def test_a_beacon_advertises_its_name_and_data():
    pid = L.spawn("t.ble")
    _as(pid, lambda: moybyte.ble.advertise("moy", b"\xff\xff\x01\x02"))
    ad = gatt_sim.advertised()
    assert ad == bytes([2, 1, 6, 4, 9]) + b"moy" + bytes([5, 0xff]) + b"\xff\xff\x01\x02"
    assert _errno(lambda: _as(pid, lambda: moybyte.ble.advertise("x" * 40))) == errno.EINVAL
    moybyte.end(pid)
    assert gatt_sim.advertised() == b""


def test_one_holder_its_end_drops_the_connection():
    a, b = L.spawn("t.a"), L.spawn("t.b")
    _as(a, lambda: moybyte.ble.connect(HRM))
    assert N.state() == N.CONNECTED
    assert _errno(lambda: _as(b, moybyte.ble.scan)) == errno.EBUSY
    moybyte.end(a)
    assert N.owner() == 0 and N.state() == 0
    _as(b, moybyte.ble.scan)


def test_ble_needs_the_permission_and_is_absent_with_the_host_down():
    pid = L.spawn("t.ble")
    devices.grants = lambda p: ("lora",) if p == pid else None
    assert _errno(lambda: _as(pid, moybyte.ble.scan)) == errno.EPERM
    devices.grants = lambda p: ("ble",) if p == pid else None
    _as(pid, moybyte.ble.scan)
    assert P.line("ble") == "Use Bluetooth devices"

    class Ws:
        wifi = net = gpio = None
    assert "ble" in P.services(Ws())
    moybyte.end(pid)
    gatt_sim.remove()
    assert moybyte.ble is None and "ble" not in P.services(Ws())
