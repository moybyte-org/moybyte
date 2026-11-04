"""The `mic` device API (runtime/system/dev_mic.py over
native/moy_audio/moy_mic.h; docs/os_2026-11/hardware.md section 1): samples
the stand-in microphone pushes read back by the process holding it, one holder
at a time, drops counted, the permission, the sweep on a process's end, and the
row's absence on a console with no microphone."""

import errno
from array import array

import pytest

from runtime import app_perms as P
from runtime import devices, moybyte
from runtime import moy_log as L
from runtime import moy_mic as N
from runtime.host import mic_sim


@pytest.fixture(autouse=True)
def mic():
    devices.grants = devices.console = None     # a console an earlier test built
    mic_sim.install(22050)
    yield
    for pid in range(1, 400):
        if N.owner() == 0:
            break
        N.sweep(N.owner())
    devices.grants = None
    mic_sim.remove()


def _as(pid, fn):
    was = L.current()
    L.current(pid)
    try:
        return fn()
    finally:
        L.current(was)


def _errno(fn):
    with pytest.raises(OSError) as e:
        fn()
    return e.value.errno


def test_the_holder_reads_what_the_microphone_heard():
    pid = L.spawn("t.mic")
    buf = array("h", [0] * 64)
    assert _as(pid, lambda: moybyte.mic.read(buf)) == 0       # the first read takes it
    assert N.owner() == pid and moybyte.mic.holder() == pid
    sent = mic_sim.tone(440, 100)
    assert _as(pid, lambda: moybyte.mic.read(buf)) == 64
    assert list(buf) == sent[:64]
    assert _as(pid, lambda: moybyte.mic.read(buf)) == 36
    assert list(buf[:36]) == sent[64:]
    assert moybyte.mic.rate() == 22050


def test_nothing_is_kept_while_no_one_holds_it():
    mic_sim.tone(440, 50)
    pid = L.spawn("t.mic")
    assert _as(pid, moybyte.mic.open) == 22050
    assert N.pending() == 0


def test_one_process_holds_it_and_its_end_lets_it_go():
    a, b = L.spawn("t.a"), L.spawn("t.b")
    _as(a, moybyte.mic.open)
    _as(a, moybyte.mic.open)                                   # its holder again: nothing
    assert _errno(lambda: _as(b, moybyte.mic.open)) == errno.EBUSY
    assert _errno(lambda: _as(b, lambda: moybyte.mic.read(array("h", [0] * 4)))) == errno.EBUSY
    moybyte.end(a)
    assert N.owner() == 0
    _as(b, moybyte.mic.open)
    assert N.owner() == b


def test_a_slow_reader_loses_the_oldest_samples_and_they_are_counted():
    pid = L.spawn("t.mic")
    _as(pid, moybyte.mic.open)
    mic_sim.push(range(N.RING + 10))
    buf = array("h", [0] * 4)
    assert _as(pid, lambda: moybyte.mic.read(buf)) == 4 and list(buf) == [10, 11, 12, 13]
    assert moybyte.mic.drops() == 10


def test_close_lets_it_go_and_a_kept_reader_reads_nothing():
    pid = L.spawn("t.mic")
    _as(pid, moybyte.mic.open)
    _as(pid, moybyte.mic.close)
    assert N.owner() == 0
    assert N.read(pid, array("h", [0] * 4)) == -errno.EBADF


def test_the_microphone_needs_the_permission():
    pid = L.spawn("t.mic")
    devices.grants = lambda p: ("audio",) if p == pid else None
    assert _errno(lambda: _as(pid, moybyte.mic.open)) == errno.EPERM
    devices.grants = lambda p: ("mic",) if p == pid else None
    _as(pid, moybyte.mic.open)


def test_the_row_and_its_absence_without_a_microphone():
    assert P.line("mic") == "Record with the microphone"
    cart = {"id": "t.m", "runtime": "python", "permissions": ["mic"], "required": ["mic"]}

    class Ws:
        wifi = net = gpio = None
    assert "mic" in P.services(Ws())
    assert P.check(cart, None, P.services(Ws()))[0] == ["mic"]
    mic_sim.remove()
    assert moybyte.mic is None
    assert "mic" not in P.services(Ws())
    assert P.check(cart, None, P.services(Ws()))[2] is not None
