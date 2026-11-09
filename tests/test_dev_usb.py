"""The `usb` device API (runtime/system/dev_usb.py over
native/p4/moy_usb/moy_usb.h; docs/os_2026-11/hardware.md section 1, #83) on
the host's stand-in bus (runtime/host/usb_sim.py): devices listed as they
enumerate and go, a HID keyboard's reports, a CDC-ACM serial line both ways,
every change an event on the process's wake; one holder and its end, the
permission, and the API's absence on a console with no USB host."""

import errno

import pytest

from runtime import app_perms as P
from runtime import devices, moybyte
from runtime import moy_input as mi
from runtime import moy_log as L
from runtime import moy_usb as N
from runtime.host import usb_sim

KBD, TTY = 1, 2


@pytest.fixture(autouse=True)
def bus():
    devices.grants = devices.console = None     # a console an earlier test built
    usb_sim.install()
    was = moybyte.WAKES.table
    moybyte.bind(mi.kernel())
    yield
    while N.owner():
        moybyte.end(N.owner())
    devices.grants = None
    usb_sim.remove()
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


def test_devices_come_and_go_and_a_keyboard_reports():
    pid = L.spawn("t.usb")
    usb = moybyte.usb
    ev = []
    assert _as(pid, usb.devices) == []
    _as(pid, lambda: usb.on(lambda *e: ev.append(e)))
    usb_sim.plug(KBD, usb.HID, usb.KEYBOARD, 0x046d, 0xc31c, "USB Keyboard")
    usb_sim.plug(TTY, usb.CDC, 0, 0x2341, 0x0043, "Uno")
    _wake(pid)
    assert ev == [(usb.EV_ATTACH, KBD, usb.HID), (usb.EV_ATTACH, TTY, usb.CDC)]
    assert _as(pid, usb.devices) == [
        (KBD, usb.HID, usb.KEYBOARD, 0x046d, 0xc31c, "USB Keyboard"),
        (TTY, usb.CDC, 0, 0x2341, 0x0043, "Uno")]
    buf = bytearray(8)
    assert _as(pid, lambda: usb.report(KBD, buf)) == 0
    usb_sim.send(KBD, b"\x02\x00\x04\x00\x00\x00\x00\x00")     # shift + a
    _wake(pid)
    assert ev[-1] == (usb.EV_REPORT, KBD, 8)
    assert _as(pid, lambda: usb.report(KBD, buf)) == 8 and buf[0] == 2 and buf[2] == 4
    assert _errno(lambda: _as(pid, lambda: usb.serial(KBD))) == errno.EINVAL
    usb_sim.unplug(KBD)
    _wake(pid)
    assert ev[-1] == (usb.EV_DETACH, KBD, 0)
    assert [d[0] for d in _as(pid, usb.devices)] == [TTY]
    assert _errno(lambda: _as(pid, lambda: usb.report(KBD, buf))) == errno.ENODEV


def test_a_serial_line_both_ways():
    pid = L.spawn("t.usb")
    usb = moybyte.usb
    usb_sim.plug(TTY, usb.CDC, 0, 0x2341, 0x0043, "Uno")
    ev = []
    _as(pid, lambda: usb.on(lambda *e: ev.append(e)))
    _as(pid, lambda: usb.serial(TTY, 31250))
    _as(pid, lambda: usb.write(TTY, b"hello\n"))
    _wake(pid)
    assert ev[-1] == (usb.EV_SENT, TTY, 0)
    assert usb_sim.sent(TTY) == (b"hello\n", 31250)
    usb_sim.send(TTY, b"ok ")
    usb_sim.send(TTY, b"42\n")
    _wake(pid)
    assert ev[-1] == (usb.EV_DATA, TTY, 6)
    buf = bytearray(4)
    assert _as(pid, lambda: usb.read(TTY, buf)) == 4 and bytes(buf) == b"ok 4"
    assert _as(pid, lambda: usb.read(TTY, buf)) == 2 and bytes(buf[:2]) == b"2\n"
    assert _errno(lambda: _as(pid, lambda: usb.serial(TTY, 7))) == errno.EINVAL
    assert _errno(lambda: _as(pid, lambda: usb.write(TTY, b""))) == errno.EINVAL


def test_one_holder_and_its_end():
    a, b = L.spawn("t.a"), L.spawn("t.b")
    _as(a, moybyte.usb.devices)
    assert N.owner() == a
    assert _errno(lambda: _as(b, moybyte.usb.devices)) == errno.EBUSY
    moybyte.end(a)
    assert N.owner() == 0
    _as(b, moybyte.usb.devices)
    assert N.owner() == b


def test_the_next_holder_rides_the_ring_the_last_one_had():
    """The bus keeps its one ring source on the kernel's table across
    holders: eight opens do not take its eight ring slots."""
    rings = set()
    for _ in range(9):
        pid = L.spawn("t.usb")
        _as(pid, moybyte.usb.devices)
        rings.add(N.source())
        moybyte.end(pid)
    assert len(rings) == 1


def test_usb_needs_the_permission_and_is_absent_without_a_host():
    pid = L.spawn("t.usb")
    devices.grants = lambda p: ("ble",) if p == pid else None
    assert _errno(lambda: _as(pid, moybyte.usb.devices)) == errno.EPERM
    devices.grants = lambda p: ("usb",) if p == pid else None
    _as(pid, moybyte.usb.devices)
    assert P.line("usb") == "Use USB devices plugged into the console"

    class Ws:
        wifi = net = gpio = None
    assert "usb" in P.services(Ws())
    moybyte.end(pid)
    usb_sim.remove()
    assert moybyte.usb is None and "usb" not in P.services(Ws())
