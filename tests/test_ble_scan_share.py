"""The GAP master the keyboard machine and the `ble` API share
(native/moy_input/moy_scan.h), the real C driven through ops a test writes:
an app's scan joining the keyboard's background scan with the union of
their kinds, each scan ending at its own time, the air relaxed when the
denser one leaves; a connection attempt pausing the discovery and resuming
it, another side's attempt answering EBUSY meanwhile; a discovery the radio
ended on its own run again, or every scan ended when it cannot be."""

import ctypes

import pytest

from runtime import moy_gatt as N

KBD, APP = 0, 1
ACTIVE, DENSE = 1, 2
EBUSY = 16

_DISC = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_int)
_CANCEL = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p)
_DONE = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_int, ctypes.c_int)


class _Ops(ctypes.Structure):
    _fields_ = [("disc", _DISC), ("cancel", _CANCEL), ("done", _DONE)]


class Air:
    """The radio: what it was told, in order, and an answer for a discovery."""

    def __init__(self):
        lib = N._lib()
        for name, args, res in (
                ("moy_scan_size", [], ctypes.c_uint32),
                ("moy_scan_init", [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p], None),
                ("moy_scan_start", [ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
                                    ctypes.c_uint32, ctypes.c_uint32], ctypes.c_int),
                ("moy_scan_stop", [ctypes.c_void_p, ctypes.c_int], None),
                ("moy_scan_on", [ctypes.c_void_p, ctypes.c_int], ctypes.c_int),
                ("moy_scan_tick", [ctypes.c_void_p, ctypes.c_uint32], None),
                ("moy_scan_due", [ctypes.c_void_p, ctypes.c_uint32], ctypes.c_int32),
                ("moy_scan_connect", [ctypes.c_void_p, ctypes.c_int], ctypes.c_int),
                ("moy_scan_connected", [ctypes.c_void_p, ctypes.c_int], None),
                ("moy_scan_connecting", [ctypes.c_void_p], ctypes.c_int),
                ("moy_scan_lost", [ctypes.c_void_p, ctypes.c_int], None)):
            fn = getattr(lib, name)
            fn.argtypes, fn.restype = args, res
        self.lib = lib
        self.log = []
        self.refuse = {}
        self._ops = _Ops(_DISC(self._disc), _CANCEL(self._cancel), _DONE(self._done))
        self.s = ctypes.create_string_buffer(lib.moy_scan_size())
        lib.moy_scan_init(self.s, ctypes.addressof(self._ops), None)

    def _disc(self, ctx, flags):
        self.log.append(("disc", flags))
        return self.refuse.get(flags, 0)

    def _cancel(self, ctx):
        self.log.append(("cancel",))
        return 0

    def _done(self, ctx, client, err):
        self.log.append(("done", client, err))

    def __getattr__(self, name):
        fn = getattr(self.lib, "moy_scan_" + name)
        return lambda *a: fn(self.s, *a)

    def take(self):
        out, self.log = self.log, []
        return out


@pytest.fixture
def air():
    return Air()


def test_an_app_scan_joins_the_keyboards_and_each_ends_at_its_own_time(air):
    assert air.start(KBD, 0, 5000, 0) == 0
    assert air.take() == [("disc", 0)]                  # passive, 10% duty
    assert air.start(APP, ACTIVE | DENSE, 2000, 1000) == 0
    assert air.take() == [("cancel",), ("disc", ACTIVE | DENSE)]
    assert air.on(KBD) and air.on(APP)
    assert air.due(1000) == 2000
    air.tick(2999)
    assert air.take() == []
    air.tick(3000)                                      # the app's ends: the air relaxes
    assert air.take() == [("cancel",), ("disc", 0), ("done", APP, 0)]
    assert air.due(3000) == 2000
    air.tick(5000)
    assert air.take() == [("cancel",), ("done", KBD, 0)]
    assert air.due(5000) == -1


def test_a_scan_that_asks_no_more_than_the_air_gives_is_no_restart(air):
    air.start(KBD, ACTIVE | DENSE, 5000, 0)             # the picker's
    air.take()
    assert air.start(APP, ACTIVE | DENSE, 1000, 0) == 0
    assert air.take() == []
    air.stop(KBD)                                       # the machine found its keyboard
    assert air.take() == [("done", KBD, 0)]
    air.stop(APP)
    assert air.take() == [("cancel",), ("done", APP, 0)]
    air.stop(APP)
    assert air.take() == []


def test_a_connection_attempt_pauses_the_discovery_and_holds_the_master(air):
    air.start(KBD, 0, 5000, 0)
    air.start(APP, ACTIVE | DENSE, 4000, 0)
    air.take()
    assert air.connect(APP) == 0
    assert air.take() == [("cancel",)]
    assert air.connecting() == APP
    assert air.connect(KBD) == -EBUSY                   # the keyboard waits its retry
    air.connected(KBD)                                  # not its attempt: nothing
    assert air.take() == []
    air.connected(APP)
    assert air.take() == [("disc", ACTIVE | DENSE)]
    assert air.connecting() == -1
    air.tick(4000)                                      # a scan's end while connecting
    air.take()
    assert air.connect(KBD) == 0
    assert air.take() == [("cancel",)]
    air.tick(5000)
    assert air.take() == [("done", KBD, 0)]             # the air stays quiet
    air.connected(KBD)
    assert air.take() == []


def test_a_scan_the_radio_refuses_leaves_the_others_on(air):
    air.start(KBD, 0, 5000, 0)
    air.take()
    air.refuse = {ACTIVE | DENSE: -3}
    assert air.start(APP, ACTIVE | DENSE, 1000, 0) == -3
    assert not air.on(APP) and air.on(KBD)
    assert air.take() == [("cancel",), ("disc", ACTIVE | DENSE), ("disc", 0)]
    air.refuse = {0: -3, ACTIVE | DENSE: -3}           # nor the old one: every scan ends
    assert air.start(APP, ACTIVE | DENSE, 1000, 0) == -3
    assert air.take() == [("cancel",), ("disc", ACTIVE | DENSE), ("disc", 0),
                          ("done", KBD, -3)]
    assert not air.on(KBD)


def test_a_discovery_the_radio_ended_runs_again_or_ends_every_scan(air):
    air.start(APP, ACTIVE | DENSE, 1000, 0)
    air.take()
    air.lost(0)
    assert air.take() == [("disc", ACTIVE | DENSE)]
    air.refuse = {ACTIVE | DENSE: -5}
    air.lost(0)
    assert air.take() == [("disc", ACTIVE | DENSE), ("done", APP, -1)]
    assert not air.on(APP)
