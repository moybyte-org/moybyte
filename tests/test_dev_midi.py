"""The `midi` device API (runtime/system/dev_midi.py over
native/moy_input/moy_midi.h; docs/os_2026-11/hardware.md section 1): MIDI
over each transport on the host's stand-ins -- a leased UART's bytes (the
Waveshare P4's uart1 row, runtime/host/lease_sim.py) parsed with running
status, realtime bytes through whole and system exclusive read past; a
USB-MIDI device's event packets (runtime/host/usb_sim.py); a BLE-MIDI
peripheral connected, found, subscribed and its packets decoded
(runtime/host/gatt_sim.py) -- each message a MIDI record on the process's
wake, and what an app sends packed for its transport; one holder, the sweep
closing every port (a UART's lease with it), the permission and absence."""

import errno
import sys

import pytest

from runtime import app_perms as P
from runtime import devices, leases, moybyte
from runtime import moy_input as mi
from runtime import moy_lease as LN
from runtime import moy_log as L
from runtime import moy_midi as N
from runtime.host import gatt_sim, lease_sim, midi_sim, usb_sim
from unix_mp import ROOT

sys.path.insert(0, str(ROOT / "tools"))
import board_config  # noqa: E402

SPEC = board_config.lease_spec(str(ROOT / "firmware/esp32_p4_wifi6_touch_lcd_7b"))
KEYS = "c0:ff:ee:00:00:09"
IO = "7772e5db-3868-4112-a1a9-f2669d106bf3"


@pytest.fixture(autouse=True)
def wire():
    devices.grants = devices.console = None     # a console an earlier test built
    leases.stand_in(lease_sim, SPEC)
    lease_sim.reset()
    usb_sim.install()
    gatt_sim.install()
    gatt_sim.device(KEYS, "Keys", rssi=-40)
    gatt_sim.chr(KEYS, IO, 0x0040, b"")
    was = moybyte.WAKES.table
    moybyte.bind(mi.kernel())
    yield
    while N.owner():
        moybyte.end(N.owner())
    for pid in list(leases.LEASES.held):
        leases.LEASES.sweep(pid)
    devices.grants = None
    leases.LEASES.grants = None
    LN.reset()
    leases.stand_in(None, None)
    usb_sim.remove()
    gatt_sim.remove()
    midi_sim.remove()
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


def _listen(pid):
    got = []
    _as(pid, lambda: moybyte.midi.on(lambda *m: got.append(m)))
    return got


def test_a_uart_port_parses_running_status_realtime_and_sysex():
    pid = L.spawn("t.midi")
    midi = moybyte.midi
    got = _listen(pid)
    port = _as(pid, midi.uart)
    assert _as(pid, midi.ports) == [(port, midi.UART, 1)]
    assert leases.LEASES.of(pid, "uart1") is not None        # the lease is the caller's
    # note on, then two more in running status, a clock tick inside the
    # second, system exclusive read past, a program change, a song position
    midi_sim.wire(port, b"\x90\x3c\x64\x3e\xf8\x50\x40\x00")
    midi_sim.wire(port, b"\xf0\x7e\x01\x02\xf7\xc1\x05\xf2\x10\x20")
    _wake(pid)
    assert got == [(port, 0x90, 0x3c, 0x64), (port, 0xf8, 0, 0), (port, 0x90, 0x3e, 0x50),
                   (port, 0x90, 0x40, 0x00), (port, 0xc1, 0x05, 0),
                   (port, 0xf2, 0x10, 0x20)]
    _as(pid, lambda: midi.send(port, b"\x80\x3c\x00"))
    assert lease_sim.sent(1) == b"\x80\x3c\x00"
    assert _errno(lambda: _as(pid, lambda: midi.send(port, b"\x3c\x00"))) == errno.EINVAL


def test_a_usb_midi_device_both_ways():
    pid = L.spawn("t.midi")
    midi = moybyte.midi
    usb_sim.plug(3, 3, 0, 0x0499, 0x1503, "Keys 49")       # a USB-MIDI device
    got = _listen(pid)
    port = _as(pid, lambda: midi.usb(3))
    usb_sim.send(3, b"\x09\x91\x40\x7f" b"\x0f\xfe\x00\x00" b"\x04\xf0\x01\x02")
    _wake(pid)
    assert got == [(port, 0x91, 0x40, 0x7f), (port, 0xfe, 0, 0)]
    _as(pid, lambda: midi.send(port, b"\xb0\x07\x64\xf8"))
    assert usb_sim.sent(3)[0] == b"\x0b\xb0\x07\x64\x0f\xf8\x00\x00"
    assert _errno(lambda: _as(pid, lambda: midi.usb(9))) == errno.ENODEV


def test_a_ble_midi_peripheral_connects_subscribes_and_decodes():
    pid = L.spawn("t.midi")
    midi = moybyte.midi
    got = _listen(pid)
    port = _as(pid, lambda: midi.ble(KEYS))
    for _ in range(3):
        _wake(pid)                      # connected, found, subscribed
    # header, timestamp + note on, running status after a timestamp, then a
    # timestamp + control change
    assert gatt_sim.notify(KEYS, 0x0040, b"\x80\x81\x90\x3c\x64\x82\x3e\x50\x83\xb0\x07\x10")
    _wake(pid)                          # the packet decoded onto the MIDI ring
    _wake(pid)                          # ...and its messages heard
    assert got == [(port, 0x90, 0x3c, 0x64), (port, 0x90, 0x3e, 0x50), (port, 0xb0, 0x07, 0x10)]
    _as(pid, lambda: midi.send(port, b"\x80\x3c\x00"))
    w = gatt_sim.written(KEYS, 0x0040)
    assert w[0] & 0x80 and w[1] & 0x80 and w[2:] == b"\x80\x3c\x00"


def test_one_holder_and_its_end_closes_every_port():
    a, b = L.spawn("t.a"), L.spawn("t.b")
    _as(a, moybyte.midi.uart)
    assert N.owner() == a
    assert _errno(lambda: _as(b, moybyte.midi.uart)) == errno.EBUSY
    moybyte.end(a)
    assert N.owner() == 0 and leases.LEASES.of(a, "uart1") is None
    _as(b, moybyte.midi.uart)


def test_midi_needs_the_permission_and_is_absent_without_a_transport():
    pid = L.spawn("t.midi")
    devices.grants = lambda p: ("usb",) if p == pid else None
    assert _errno(lambda: _as(pid, moybyte.midi.uart)) == errno.EPERM
    devices.grants = lambda p: ("midi", "lease:uart1") if p == pid else None
    leases.LEASES.grants = devices.grants
    _as(pid, moybyte.midi.uart)
    assert P.line("midi") == "Play and hear MIDI instruments"

    class Ws:
        wifi = net = gpio = None
    assert "midi" in P.services(Ws())
    moybyte.end(pid)
    leases.stand_in(None, None)
    usb_sim.remove()
    gatt_sim.remove()
    assert moybyte.midi is None and "midi" not in P.services(Ws())
