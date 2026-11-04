"""The `lora` device API (runtime/system/dev_lora.py over native/moy_net/
moy_lora.h and the LoRaWAN MAC moy_lorawan.h; docs/os_2026-11/hardware.md
section 1): raw packets out and in with RSSI and SNR on the stand-in radio,
one holder, the sweep, the permission; a LoRaWAN join and uplinks round-tripped
against a network server written apart from the C (runtime/host/lora_sim.py),
its receive windows timed by the driver on a test's clock; the AES and CMAC
under it against the RFC and an independent implementation."""

import ctypes
import errno
import os
from array import array

import pytest

from runtime import app_perms as P
from runtime import devices, moybyte
from runtime import moy_input as mi
from runtime import moy_log as L
from runtime import moy_lora as N
from runtime.host import lora_sim

DEV_EUI = bytes.fromhex("70B3D57ED0050001")
JOIN_EUI = bytes.fromhex("0000000000000000")
APP_KEY = bytes.fromhex("8E3F2A1B0C9D8E7F6A5B4C3D2E1F0A9B")
EARLY = 20                      # MOY_LORA_WIN_EARLY_MS


@pytest.fixture(autouse=True)
def radio():
    lora_sim.install()
    yield
    while N.owner():
        moybyte.end(N.owner())
    devices.grants = None
    lora_sim.remove()


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


# -- the cipher -------------------------------------------------------------------------


def _cmac_c(key, msg):
    out = (ctypes.c_uint8 * 16)()
    lib = N._lib()
    lib.moy_aes_cmac(key, msg, len(msg), out)
    return bytes(out)


def test_aes_cmac_matches_rfc_4493_and_an_independent_implementation():
    key = bytes.fromhex("2b7e151628aed2a6abf7158809cf4f3c")
    msg = bytes.fromhex("6bc1bee22e409f96e93d7e117393172aae2d8a571e03ac9c9eb76fac45af8e51"
                        "30c81c46a35ce411e5fbc1191a0a52eff69f2445df4f9b17ad2b417be66c3710")
    assert _cmac_c(key, b"").hex() == "bb1d6929e95937287fa37d129b756746"
    assert _cmac_c(key, msg[:16]).hex() == "070a16b46b4d4144f79bdd9dd04a287c"
    assert _cmac_c(key, msg[:40]).hex() == "dfa66747de9ae63030ca32611497c827"
    assert _cmac_c(key, msg).hex() == "51f0bebf7e3b9d92fc49741779363cfe"
    for n in (1, 15, 17, 31, 33, 100):
        k, m = os.urandom(16), os.urandom(n)
        assert _cmac_c(k, m) == lora_sim._cmac(k, m)


# -- raw packets ------------------------------------------------------------------------


def test_a_raw_packet_goes_out_with_the_holders_settings_and_is_reported_sent():
    pid = L.spawn("t.lora")
    _as(pid, lambda: moybyte.lora.config(freq=869525000, sf=10, power=10))
    _as(pid, lambda: moybyte.lora.send(b"hello mesh"))
    assert N.mode() == 2                                   # on the air
    assert _errno(lambda: _as(pid, lambda: moybyte.lora.send(b"x"))) == errno.EBUSY
    data, freq, sf, bw, sync, iq = lora_sim.sent()
    assert (data, freq, sf, bw, sync, iq) == (b"hello mesh", 869525000, 10, 125000, 0x12, False)
    N.step(0)
    assert N.mode() == N.IDLE


def test_a_listening_radio_hears_its_channel_with_rssi_and_snr_and_nothing_else():
    pid = L.spawn("t.lora")
    _as(pid, lambda: moybyte.lora.config(sf=7))
    _as(pid, moybyte.lora.listen)
    assert not lora_sim.air(b"other", 868300000, 7)          # another channel
    assert not lora_sim.air(b"other", 868100000, 9)          # another spreading factor
    assert lora_sim.air(b"ping", 868100000, 7, rssi=-97, snr=-26)
    N.step(0)
    buf, meta = bytearray(64), array("i", [0, 0, 0])
    assert _as(pid, lambda: moybyte.lora.recv(buf, meta)) == 4
    assert bytes(buf[:4]) == b"ping" and list(meta) == [-97, -26, 0]
    assert _as(pid, lambda: moybyte.lora.recv(buf)) is None
    # A send while listening goes out, then the radio listens again.
    _as(pid, lambda: moybyte.lora.send(b"pong"))
    assert lora_sim.sent()[0] == b"pong"
    N.step(1)
    assert N.mode() == N.LISTEN
    assert lora_sim.air(b"again", 868100000, 7)
    N.step(2)
    assert _as(pid, lambda: moybyte.lora.recv(buf))[:1] == (5,)


def test_bad_settings_are_refused():
    pid = L.spawn("t.lora")
    for kw in ({"sf": 6}, {"bw": 100000}, {"power": 23}, {"cr": 9}, {"freq": 10}):
        assert _errno(lambda: _as(pid, lambda: moybyte.lora.config(**kw))) == errno.EINVAL


def test_one_process_holds_the_radio_and_its_end_lets_it_go():
    a, b = L.spawn("t.a"), L.spawn("t.b")
    _as(a, moybyte.lora.listen)
    assert _errno(lambda: _as(b, moybyte.lora.listen)) == errno.EBUSY
    moybyte.end(a)
    assert N.owner() == 0
    _as(b, moybyte.lora.listen)
    assert N.owner() == b
    assert N.recv(a, bytearray(4), array("i", [0] * 3)) == -errno.EBADF


def test_the_radio_needs_the_permission_and_is_absent_without_one():
    pid = L.spawn("t.lora")
    devices.grants = lambda p: ("http",) if p == pid else None
    assert _errno(lambda: _as(pid, moybyte.lora.listen)) == errno.EPERM
    devices.grants = lambda p: ("lora",) if p == pid else None
    _as(pid, moybyte.lora.listen)
    assert P.line("lora") == "Send and receive on the LoRa radio"

    class Ws:
        wifi = net = gpio = None
    assert "lora" in P.services(Ws())
    moybyte.end(pid)
    lora_sim.remove()
    assert moybyte.lora is None and "lora" not in P.services(Ws())


# -- LoRaWAN ----------------------------------------------------------------------------


def _join(pid, net, window=1, dr=5, t0=1000):
    _as(pid, lambda: moybyte.lora.join(DEV_EUI, JOIN_EUI, APP_KEY, dr=dr))
    req, freq, sf, bw, sync, iq = lora_sim.sent()
    assert sync == 0x34 and not iq and freq in (868100000, 868300000, 868500000)
    accept = net.answer(req)
    assert accept is not None
    assert N.step(t0) == t0 + 5000 - EARLY                    # TX done: RX1 is due then
    assert not lora_sim.air(accept, freq, sf)                  # nobody listens yet
    N.step(t0 + 5000 - EARLY)                                  # RX1 open
    if window == 1:
        assert lora_sim.air(accept, freq, sf)
        N.step(t0 + 5000)
        return freq
    end = N.step(t0 + 5000)
    N.step(end)                                                # RX1 closes empty
    N.step(t0 + 6000 - EARLY)                                  # RX2: 869.525, SF12
    if window == 2:
        assert not lora_sim.air(accept, freq, sf)
        assert lora_sim.air(accept, lora_sim.EU868_RX2, 12)
        N.step(t0 + 6000)
    return freq


def test_a_join_round_trips_in_rx1_and_an_uplink_reaches_the_network():
    was = moybyte.WAKES.table
    moybyte.bind(mi.kernel())
    try:
        pid = L.spawn("t.node")
        net = lora_sim.Network(DEV_EUI, JOIN_EUI, APP_KEY)
        seen = []
        _as(pid, lambda: moybyte.lora.on(lambda *e: seen.append(e)))
        assert _errno(lambda: _as(pid, lambda: moybyte.lora.uplink(1, b"x"))) == errno.ENOTCONN
        _join(pid, net)
        assert _as(pid, moybyte.lora.joined) == (net.dev_addr, 0)
        out = [0] * 8
        for wid in out[:moybyte.WAKES.due(0, out)]:
            _as(pid, lambda: moybyte.WAKES.call(wid))
        assert seen and seen[-1][0] == moybyte.lora.EV_JOINED

        _as(pid, lambda: moybyte.lora.uplink(2, b"temp=21.5"))
        up = lora_sim.sent()
        assert up[4] == 0x34 and net.answer(up[0]) is None
        assert net.uplinks == [(0, 2, b"temp=21.5", False)] and not net.bad
        t = 20000
        N.step(t)                                              # its windows close empty
        while N.mode() != N.IDLE:
            t = N.step(t) or t + 1
        assert _as(pid, moybyte.lora.joined) == (net.dev_addr, 1)
    finally:
        moybyte.WAKES.table = None
        if was is not None:
            moybyte.bind(was)


def test_a_join_accept_in_rx2_and_a_confirmed_uplink_acked_with_a_downlink():
    pid = L.spawn("t.node")
    net = lora_sim.Network(DEV_EUI, JOIN_EUI, APP_KEY, rx2_dr=3,
                           cflist=[867100000, 867300000, 867500000, 867700000, 867900000])
    _join(pid, net, window=2)
    assert _as(pid, moybyte.lora.joined) is not None
    net.down(7, b"\x01\x02led")
    _as(pid, lambda: moybyte.lora.uplink(5, b"\xff", confirmed=True, dr=4))
    up, freq, sf, *_ = lora_sim.sent()
    assert sf == 8
    reply = net.answer(up)
    assert net.uplinks[-1] == (0, 5, b"\xff", True)
    t0 = 50000
    N.step(t0)
    N.step(t0 + 1000 - EARLY)                                   # RX1: the uplink's channel and DR
    assert lora_sim.air(reply, freq, sf)
    N.step(t0 + 1000)
    buf = bytearray(32)
    assert _as(pid, lambda: moybyte.lora.downlink(buf)) == (5, 7)
    assert bytes(buf[:5]) == b"\x01\x02led"
    assert N.mode() == N.IDLE


def test_a_join_nobody_answers_fails_after_rx2():
    pid = L.spawn("t.node")
    _as(pid, lambda: moybyte.lora.join(DEV_EUI, JOIN_EUI, APP_KEY))
    lora_sim.sent()
    t = N.step(0)
    while N.mode() != N.IDLE:
        t = N.step(t) or t + 1
    assert t < 8000
    assert _as(pid, moybyte.lora.joined) is None


def test_a_forged_join_accept_is_refused():
    pid = L.spawn("t.node")
    other = lora_sim.Network(DEV_EUI, JOIN_EUI, bytes(16))
    _as(pid, lambda: moybyte.lora.join(DEV_EUI, JOIN_EUI, APP_KEY))
    req, freq, sf, *_ = lora_sim.sent()
    assert other.answer(req) is None                            # its MIC fails there
    forged = other._join(req[:19] + lora_sim._cmac(bytes(16), req[:19])[:4])
    N.step(0)
    N.step(5000 - EARLY)
    assert lora_sim.air(forged, freq, sf)
    N.step(5000)
    assert _as(pid, moybyte.lora.joined) is None
