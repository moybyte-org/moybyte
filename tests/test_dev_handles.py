"""Leases and device APIs by handle (runtime/system/dev_handles.py,
docs/os_2026-11/hardware.md section 4): what the `moybyte.dev_*` rows answer
a compiled program, here called as its process would be served, over the
host's stand-ins (runtime/host/lease_sim.py, lora_sim.py, mic_sim.py) and the
http client against a local server. The rows reached from each compiled tier
are tests/test_wasm_app.py's, tests/test_wasi_services.py's and
tests/test_elf_commands.py's."""

import errno
import http.server
import struct
import sys
import threading

import pytest

from runtime import dev_handles as H
from runtime import devices, leases, moybyte
from runtime import moy_input as mi
from runtime import moy_lease as N
from runtime import moy_log as L
from runtime.host import lease_sim
from unix_mp import ROOT

sys.path.insert(0, str(ROOT / "tools"))
import board_config  # noqa: E402

SPEC = board_config.lease_spec(str(ROOT / "firmware/esp32_p4_wifi6_touch_lcd_7b"))


@pytest.fixture(autouse=True)
def board():
    leases.stand_in(lease_sim, SPEC)
    lease_sim.reset()
    yield
    for pid in list(H.HANDLES):
        moybyte.end(pid)
    for pid in list(leases.LEASES.held):
        leases.LEASES.sweep(pid)
    leases.LEASES.grants = None
    devices.grants = None
    N.reset()
    leases.stand_in(None, None)


def _as(pid, fn):
    was = L.current()
    L.current(pid)
    try:
        return fn()
    finally:
        L.current(was)


def _open(pid, name, cfg=""):
    return _as(pid, lambda: H.open(pid, name, cfg))


# -- the leases -------------------------------------------------------------------------


def test_a_gpio_handle_drives_and_reads_its_pin():
    pid = L.spawn("t.gpio")
    h = _open(pid, "gpio6", "mode=out value=1")
    assert h > 0 and N.pin_state(6) & N.PIN_OUT and N.sim_get(6) == 1
    assert H.set(pid, h, "value=0") == 0 and H.get(pid, h, "value") == 0
    assert H.set(pid, h, "mode=in pull=up") == 0 and N.pin_state(6) & N.PIN_PU
    assert H.read(pid, h, 64) == b""                    # no edges asked for
    assert H.close(pid, h) == 0 and N.pin_state(6) == N.PIN_SAFE
    assert H.get(pid, h, "value") == H.ABSENT


def test_a_gpio_handle_reads_the_edges_its_trigger_names():
    was = moybyte.WAKES.table
    moybyte.bind(mi.kernel())
    try:
        pid = L.spawn("t.edges")
        h = _open(pid, "gpio5", "mode=in")
        assert H.set(pid, h, "edges=rising") == 0
        for v in (1, 0, 1, 0):
            N.sim_level(5, v)
        assert H.read(pid, h, 4) == H.FULL
        got = H.read(pid, h, 64)
        assert len(got) == 2 * H.EDGE_BYTES
        assert [struct.unpack_from("<II", got, k)[1] for k in (0, 8)] == [1, 1]
        assert H.read(pid, h, 64) == b""
        assert H.set(pid, h, "edges=off") == 0 and H.read(pid, h, 64) == b""
        assert H.set(pid, h, "edges=sideways") == H.BAD
    finally:
        moybyte.WAKES.table = None
        if was is not None:
            moybyte.bind(was)


def test_a_uart_handle_writes_and_reads_what_waits():
    pid = L.spawn("t.uart")
    h = _open(pid, "uart1", "baudrate=9600 parity=none")
    lease_sim.loopback(1, True)
    assert H.write(pid, h, b"ping") == 4 and H.get(pid, h, "any") == 4
    assert H.read(pid, h, 2) == b"pi" and H.read(pid, h, 64) == b"ng"
    assert H.read(pid, h, 64) == b""
    assert H.set(pid, h, "baudrate=115200") == 0
    assert H.set(pid, h, "speed=3") == H.BAD


def test_an_spi_handle_writes_reads_and_transfers_full_duplex():
    pid = L.spawn("t.spi")
    h = _open(pid, "spi2", "baudrate=1000000 polarity=0 phase=0")
    assert H.write(pid, h, b"\x9f") == 1
    assert H.xfer(pid, h, b"\x01\x02\x03", 3) == b"\xff\xff\xff"
    assert H.xfer(pid, h, b"\x01\x02\x03", 2) == H.FULL
    assert H.read(pid, h, 2) == b"\xff\xff"
    assert lease_sim.SPI_LOG[1][:2] == [b"\x9f", b"\x01\x02\x03"]


def test_an_i2c_handle_reads_a_register_of_the_device_at_its_address():
    pid = L.spawn("t.i2c")
    lease_sim.I2C_DEV[0x48] = bytearray(b"\x10\x20\x30\x40")
    h = _open(pid, "i2c", "scl=20 sda=21 freq=100000")
    assert H.read(pid, h, 1) == H.BAD                  # no address yet
    assert H.set(pid, h, "addr=0x48") == 0 and H.get(pid, h, "addr") == 0x48
    assert H.xfer(pid, h, b"\x02", 2) == b"\x30\x40"
    assert H.write(pid, h, b"\x00\x99") == 2 and lease_sim.I2C_DEV[0x48][0] == 0x99
    assert H.set(pid, h, "addr=0x50") == 0 and H.read(pid, h, 1) == H.ABSENT   # no ACK
    assert N.pin_state(20) & N.PIN_PERIPH and N.pin_state(21) & N.PIN_PERIPH
    assert _open(pid, "i2c", "scl=20") == H.BAD


def test_a_pwm_and_an_adc_handle():
    pid = L.spawn("t.pwm")
    p = _open(pid, "pwm22", "freq=1000 duty=32768")
    assert H.get(pid, p, "freq") == 1000 and H.get(pid, p, "duty") == 32768
    assert H.set(pid, p, "duty=0") == 0 and H.get(pid, p, "duty") == 0
    lease_sim.ADC_LEVEL[23] = 1234
    a = _open(pid, "adc23")
    assert H.get(pid, a, "value") == 1234
    assert H.write(pid, a, b"x") == H.BAD


def test_a_name_or_cfg_the_console_does_not_take_is_bad():
    pid = L.spawn("t.bad")
    assert _open(pid, "uart9") == H.BAD
    assert _open(pid, "gpio") == H.BAD
    assert _open(pid, "gpio6", "mode=sideways") == H.BAD
    assert _open(pid, "gpio6", "nonsense") == H.BAD
    assert _open(pid, "toaster") == H.BAD
    assert N.held() == 0


def test_a_lease_needs_its_row_granted():
    pid = L.spawn("t.perm")
    leases.LEASES.grants = lambda p: ("lease:gpio",) if p == pid else None
    assert _open(pid, "uart1") == H.DENIED
    assert _open(pid, "gpio6") > 0 and _open(pid, "pwm22") > 0


def test_one_holder_across_the_tiers_a_python_object_or_a_handle():
    py, native = L.spawn("t.py"), L.spawn("t.native")
    u = _as(py, lambda: moybyte.lease("uart1"))
    assert _open(native, "uart1") == H.BUSY
    h = _open(native, "gpio6", "mode=out")
    with pytest.raises(OSError) as e:
        _as(py, lambda: moybyte.lease("gpio6"))
    assert e.value.errno == errno.EBUSY
    with pytest.raises(OSError) as e:
        _as(py, lambda: moybyte.lease("i2c", scl=6, sda=20))
    assert e.value.errno == errno.EBUSY
    leases.LEASES.drop(u)
    assert _open(native, "uart1") > 0
    assert H.close(native, h) == 0
    _as(py, lambda: moybyte.lease("gpio6"))


def test_a_handle_is_its_process_s_own():
    a, b = L.spawn("t.a"), L.spawn("t.b")
    h = _open(a, "gpio6", "mode=out")
    assert H.get(b, h, "value") == H.ABSENT and H.close(b, h) == H.ABSENT
    assert H.close(a, h) == 0 and H.close(a, h) == H.ABSENT


def test_the_process_s_end_closes_every_handle_and_leaves_the_pins_safe():
    was = moybyte.WAKES.table
    moybyte.bind(mi.kernel())
    try:
        pid = L.spawn("t.end")
        _open(pid, "gpio5", "mode=in")
        h = _open(pid, "gpio6", "mode=out value=1")
        assert H.set(pid, h, "edges=both") == 0
        _open(pid, "uart1")
        _open(pid, "i2c", "scl=20 sda=21")
        assert N.held(N.VM) == 4
        moybyte.end(pid)
        assert pid not in H.HANDLES and N.held() == 0
        assert [N.pin_state(p) for p in (5, 6, 20, 21, 47, 48)] == [N.PIN_SAFE] * 6
    finally:
        moybyte.WAKES.table = None
        if was is not None:
            moybyte.bind(was)


# -- the device APIs --------------------------------------------------------------------


def test_a_lora_handle_sends_and_reads_packets_with_their_rssi():
    from runtime import moy_lora
    from runtime.host import lora_sim
    devices.grants = devices.console = None
    lora_sim.install()
    try:
        pid, other = L.spawn("t.lora"), L.spawn("t.other")
        h = _open(pid, "lora", "sf=7")
        assert _open(pid, "lora") == H.BUSY                 # one handle on the one radio
        assert _open(other, "lora") == H.BUSY
        assert lora_sim.air(b"ping", 868100000, 7, rssi=-97, snr=-26)
        moy_lora.step(0)
        assert _as(pid, lambda: H.read(pid, h, 64)) == b"ping"
        assert H.get(pid, h, "rssi") == -97 and H.get(pid, h, "snr") == -26
        assert _as(pid, lambda: H.read(pid, h, 64)) == b""
        assert _as(pid, lambda: H.write(pid, h, b"pong")) == 4
        assert lora_sim.sent()[0] == b"pong"
        assert _as(pid, lambda: H.close(pid, h)) == 0 and moy_lora.owner() == 0
        devices.grants = lambda p: ()
        assert _open(other, "lora") == H.DENIED
    finally:
        devices.grants = None
        while moy_lora.owner():
            moybyte.end(moy_lora.owner())
        lora_sim.remove()


def test_a_mic_handle_reads_pcm16_and_its_end_lets_the_microphone_go():
    from runtime import moy_mic
    from runtime.host import mic_sim
    devices.grants = devices.console = None
    mic_sim.install(22050)
    try:
        pid = L.spawn("t.mic")
        h = _open(pid, "mic")
        assert moy_mic.owner() == pid and H.get(pid, h, "rate") == 22050
        mic_sim.push([1, -2, 3])
        got = _as(pid, lambda: H.read(pid, h, 7))           # whole samples only
        assert got == struct.pack("<3h", 1, -2, 3)
        assert _open(L.spawn("t.other"), "mic") == H.BUSY
        moybyte.end(pid)
        assert moy_mic.owner() == 0
    finally:
        mic_sim.remove()


class _Web(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 -- the stdlib's name
        body = b"hello, moybyte" if self.path == "/hello" else b""
        self.send_response(200 if body else 404)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


@pytest.fixture
def web(tmp_path):
    from runtime import dev_http
    from ws_helpers import build_ws
    s = http.server.HTTPServer(("127.0.0.1", 0), _Web)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    build_ws(tmp_path)
    devices.grants = None
    yield "http://127.0.0.1:%d" % s.server_address[1]
    for pid in list({r.owner for r in dev_http._open.values()}):
        dev_http.sweep(pid)
    devices.grants = None
    s.shutdown()


def test_an_http_handle_reads_its_body_in_chunks(web):
    from runtime import dev_http
    pid = L.spawn("t.http")
    h = _open(pid, "http", "url=%s/hello cap=1024" % web)
    assert h > 0
    assert H.get(pid, h, "status") == 200 and H.get(pid, h, "length") == 14
    got = b""
    while True:
        c = _as(pid, lambda: H.read(pid, h, 5))
        if not c:
            break
        got += c
    assert got == b"hello, moybyte"
    miss = _open(pid, "http", "url=%s/nope" % web)
    assert H.get(pid, miss, "status") == 404 and H.read(pid, miss, 8) == b""
    assert _open(pid, "http", "cap=10") == H.BAD
    moybyte.end(pid)
    assert not [r for r in dev_http._open.values() if r.owner == pid]
    devices.grants = lambda p: ()
    assert _open(pid, "http", "url=%s/hello" % web) == H.DENIED


# -- BLE --------------------------------------------------------------------------------

HRM = "c0:ff:ee:00:00:01"
LAMP = "c0:ff:ee:00:00:02"


@pytest.fixture
def air():
    from runtime import moy_gatt
    from runtime.host import gatt_sim
    devices.grants = devices.console = None
    gatt_sim.install()
    gatt_sim.device(HRM, "Pulse", rssi=-48)
    gatt_sim.chr(HRM, 0x2A37, 0x0010, b"\x00\x48")
    gatt_sim.chr(HRM, 0x2A19, 0x0020, b"\x5a")
    gatt_sim.device(LAMP, "Lamp", rssi=-71)
    gatt_sim.chr(LAMP, "6e400002-b5a3-f393-e0a9-e50e24dcca9e", 0x0030, b"")
    was = moybyte.WAKES.table
    moybyte.bind(mi.kernel())
    yield gatt_sim
    for pid in list(H.HANDLES):
        moybyte.end(pid)
    while moy_gatt.owner():
        moybyte.end(moy_gatt.owner())
    devices.grants = None
    gatt_sim.remove()
    moybyte.WAKES.table = None
    if was is not None:
        moybyte.bind(was)


def _get(pid, h, what):
    return _as(pid, lambda: H.get(pid, h, what))


def _records(b):
    """A ble handle's read as [(event, a, b, bytes)]."""
    out, k = [], 0
    while k < len(b):
        ev, n, a, st = struct.unpack_from("<HHii", b, k)
        out.append((ev, a, st, bytes(b[k + H.BLE_HEAD:k + H.BLE_HEAD + n])))
        k += H.BLE_HEAD + n
    return out


def _ble(pid, h, cap=512):
    got = _as(pid, lambda: H.read(pid, h, cap))
    assert not isinstance(got, int), got
    return _records(got)


def test_a_ble_handle_scans_connects_finds_reads_writes_and_hears_notifications(air):
    from runtime import dev_ble as B
    pid = L.spawn("t.ble")
    h = _open(pid, "ble")
    assert h > 0 and _ble(pid, h) == []
    set_ = lambda cfg: _as(pid, lambda: H.set(pid, h, cfg))  # noqa: E731
    assert set_("scan=500") == 0
    assert _as(pid, lambda: H.read(pid, h, 8)) == H.FULL      # the first record waits
    assert _ble(pid, h) == [(B.EV_ADV, 0, -48, bytes.fromhex("c0ffee000001") + b"Pulse"),
                            (B.EV_ADV, 1, -71, bytes.fromhex("c0ffee000002") + b"Lamp"),
                            (B.EV_SCAN_DONE, 2, 0, b"")]
    assert set_("connect=" + HRM) == 0 and set_("find=2a19") == 0
    assert _ble(pid, h) == [(B.EV_CONNECTED, 1, 0, b""), (B.EV_FOUND, 0x20, 0, b"")]
    assert _get(pid, h, "connected") == 1
    assert set_("read=0x20") == 0
    assert _ble(pid, h) == [(B.EV_READ, 1, 0, b"\x5a")]
    assert set_("subscribe=0x10") == 0
    assert air.notify(HRM, 0x10, b"\x00\x4c") and air.notify(HRM, 0x10, b"\x00\x4d")
    assert _ble(pid, h, H.BLE_HEAD) == [(B.EV_WRITTEN, 0, 0, b"")]     # its CCCD
    one = H.BLE_HEAD + 2
    assert _ble(pid, h, one) == [(B.EV_NOTIFY, 0x10, 2, b"\x00\x4c")]
    assert _ble(pid, h, one) == [(B.EV_NOTIFY, 0x10, 2, b"\x00\x4d")]
    assert _as(pid, lambda: H.write(pid, h, b"x")) == H.BAD   # no write= yet
    assert set_("write=0x20 response=1") == 0
    assert _as(pid, lambda: H.write(pid, h, b"\x07")) == 1
    assert air.written(HRM, 0x20) == b"\x07"
    assert _ble(pid, h) == [(B.EV_WRITTEN, 0, 0, b"")]
    assert set_("find=0x9999") == 0
    assert _ble(pid, h) == [(B.EV_FOUND, 0, -errno.ENOENT, b"")]
    assert set_("connect=off") == 0 and _get(pid, h, "connected") == 0
    assert _ble(pid, h)[-1][0] == B.EV_DISCONNECTED
    assert set_("read=0x20") == H.ABSENT                    # no connection
    for bad in ("scan=1 find=2a19", "connect=c0:ff", "find=2a1g", "find=6e40-00", "read=0", "data=00",
                "warp=1", "subscribe=x"):
        assert set_(bad) == H.BAD, bad


def test_a_ble_handle_finds_by_128_bit_uuid_and_advertises(air):
    from runtime import dev_ble as B
    pid = L.spawn("t.ble")
    h = _open(pid, "ble", "connect=" + LAMP)
    assert _as(pid, lambda: H.set(pid, h, "find=6e400002-b5a3-f393-e0a9-e50e24dcca9e")) == 0
    assert _ble(pid, h)[-1] == (B.EV_FOUND, 0x30, 0, b"")
    assert _as(pid, lambda: H.set(pid, h, "write=0x30 response=0")) == 0
    assert _as(pid, lambda: H.write(pid, h, b"on")) == 2 and air.written(LAMP, 0x30) == b"on"
    assert _as(pid, lambda: H.set(pid, h, "advertise=moy data=ffff0102 interval=100")) == 0
    assert air.advertised() == bytes([2, 1, 6, 4, 9]) + b"moy" + bytes([5, 0xff]) + \
        b"\xff\xff\x01\x02"
    assert _as(pid, lambda: H.set(pid, h, "advertise=off")) == 0 and air.advertised() == b""


def test_one_ble_holder_its_permission_and_its_end(air):
    from runtime import dev_ble as B
    from runtime import moy_gatt
    a, b = L.spawn("t.a"), L.spawn("t.b")
    h = _open(a, "ble", "connect=" + HRM)
    assert moy_gatt.owner() == a and moy_gatt.state() == moy_gatt.CONNECTED
    assert _open(a, "ble") == H.BUSY                         # one handle on the one radio
    assert _open(b, "ble") == H.BUSY
    with pytest.raises(OSError) as e:
        _as(b, B.scan)                                       # Python's face too
    assert e.value.errno == errno.EBUSY
    moybyte.end(a)
    assert moy_gatt.owner() == 0 and moy_gatt.state() == 0 and a not in H.HANDLES
    assert _get(a, h, "connected") == H.ABSENT
    assert _open(b, "ble", "scan=0") == H.BAD and moy_gatt.owner() == 0   # nothing kept
    devices.grants = lambda p: ()
    assert _open(b, "ble") == H.DENIED
    devices.grants = None
    air.remove()
    assert _open(b, "ble") == H.ABSENT


# -- LoRaWAN ----------------------------------------------------------------------------

DEV_EUI = "70B3D57ED0050001"
JOIN_EUI = "0000000000000000"
APP_KEY = "8E3F2A1B0C9D8E7F6A5B4C3D2E1F0A9B"
JOIN = "join=otaa dev_eui=%s join_eui=%s app_key=%s" % (DEV_EUI, JOIN_EUI, APP_KEY)
EARLY = 20                      # MOY_LORA_WIN_EARLY_MS


@pytest.fixture
def radio():
    from runtime import moy_lora
    from runtime.host import lora_sim
    devices.grants = devices.console = None
    lora_sim.install()
    yield moy_lora, lora_sim
    for pid in list(H.HANDLES):
        moybyte.end(pid)
    while moy_lora.owner():
        moybyte.end(moy_lora.owner())
    devices.grants = None
    lora_sim.remove()


def _rx1(N, sim, reply, freq, sf, t0):
    """The MAC stepped from a transmission's end at `t0` into RX1, `reply`
    on the air there."""
    N.step(t0)
    due = N.step(t0)
    N.step(due)
    assert sim.air(reply, freq, sf)
    N.step(due + EARLY)


def test_a_lora_handle_joins_sends_uplinks_and_reads_a_downlink(radio):
    N, sim = radio
    pid = L.spawn("t.node")
    net = sim.Network(bytes.fromhex(DEV_EUI), bytes.fromhex(JOIN_EUI), bytes.fromhex(APP_KEY))
    h = _open(pid, "lora", "send=2")
    assert _as(pid, lambda: H.write(pid, h, b"early")) == H.ABSENT        # before a join
    assert _get(pid, h, "joined") == 0
    assert _as(pid, lambda: H.set(pid, h, JOIN)) == 0
    req, freq, sf, *_ = sim.sent()
    assert _get(pid, h, "busy") == 1
    _rx1(N, sim, net.answer(req), freq, sf, 1000)
    assert _get(pid, h, "joined") == 1 and _get(pid, h, "busy") == 0
    assert _as(pid, lambda: H.write(pid, h, b"temp=21.5")) == 9
    up, freq, sf, *_ = sim.sent()
    assert net.answer(up) is None and net.uplinks == [(0, 2, b"temp=21.5", False)]
    t = 20000
    N.step(t)
    while _get(pid, h, "busy") and t < 40000:                # its windows close empty
        t = N.step(t) or t + 1
    assert _get(pid, h, "busy") == 0 and _get(pid, h, "fcnt") == 1
    net.down(7, b"\x01\x02led")
    assert _as(pid, lambda: H.set(pid, h, "send=5 confirmed=1 dr=4")) == 0
    assert _as(pid, lambda: H.write(pid, h, b"\xff")) == 1
    assert _as(pid, lambda: H.write(pid, h, b"\xfe")) == H.BUSY          # its windows run
    up, freq, sf, *_ = sim.sent()
    assert sf == 8
    reply = net.answer(up)
    assert net.uplinks[-1] == (1, 5, b"\xff", True)
    _rx1(N, sim, reply, freq, sf, 50000)
    assert _as(pid, lambda: H.read(pid, h, 32)) == b"\x01\x02led"
    assert _get(pid, h, "port") == 7
    assert _as(pid, lambda: H.set(pid, h, "send=raw")) == 0
    assert _as(pid, lambda: H.write(pid, h, b"mesh")) == 4 and sim.sent()[0] == b"mesh"


def test_a_lora_handle_refuses_a_join_it_cannot_make(radio):
    pid = L.spawn("t.node")
    h = _open(pid, "lora")
    for bad in ("join=abp " + JOIN[10:], "join=otaa dev_eui=%s" % DEV_EUI,
                JOIN.replace(APP_KEY, APP_KEY[:-2]), JOIN.replace(DEV_EUI, "zz" * 8),
                "dev_eui=" + DEV_EUI, "send=0", "send=224", "confirmed=2", "dr=9"):
        assert _as(pid, lambda: H.set(pid, h, bad)) == H.BAD, bad
    devices.grants = lambda p: ()
    assert _open(L.spawn("t.other"), "lora", JOIN) == H.DENIED


# -- USB ---------------------------------------------------------------------------------

KBD, TTY = 1, 2


@pytest.fixture
def bus():
    from runtime import moy_usb
    from runtime.host import usb_sim
    devices.grants = devices.console = None
    usb_sim.install()
    was = moybyte.WAKES.table
    moybyte.bind(mi.kernel())
    yield usb_sim
    for pid in list(H.HANDLES):
        moybyte.end(pid)
    while moy_usb.owner():
        moybyte.end(moy_usb.owner())
    devices.grants = None
    usb_sim.remove()
    moybyte.WAKES.table = None
    if was is not None:
        moybyte.bind(was)


def _usb(pid, h, cap=2048):
    got = _as(pid, lambda: H.read(pid, h, cap))
    assert not isinstance(got, int), got
    return _records(got)


def test_a_usb_handle_lists_reads_a_keyboard_and_talks_to_a_serial_device(bus):
    from runtime import dev_usb as U
    bus.plug(KBD, U.HID, U.KEYBOARD, 0x046d, 0xc31c, "USB Keyboard")
    pid = L.spawn("t.usb")
    h = _open(pid, "usb")
    about = bytes((U.HID, U.KEYBOARD)) + struct.pack("<HH", 0x046d, 0xc31c) + b"USB Keyboard"
    assert _usb(pid, h) == [(U.EV_ATTACH, KBD, U.HID, about)]          # already plugged in
    bus.plug(TTY, U.CDC, 0, 0x2341, 0x0043, "Uno")
    bus.send(KBD, b"\x02\x00\x04\x00\x00\x00\x00\x00")                 # shift + a
    assert _as(pid, lambda: H.read(pid, h, 8)) == H.FULL                # the record waits
    uno = bytes((U.CDC, 0)) + struct.pack("<HH", 0x2341, 0x0043) + b"Uno"
    assert _usb(pid, h) == [(U.EV_ATTACH, TTY, U.CDC, uno),
                            (U.EV_REPORT, KBD, 8, b"\x02\x00\x04\x00\x00\x00\x00\x00")]
    set_ = lambda cfg: _as(pid, lambda: H.set(pid, h, cfg))  # noqa: E731
    assert _as(pid, lambda: H.write(pid, h, b"x")) == H.BAD             # no write= yet
    assert set_("serial=2 baud=31250 write=2") == 0
    assert _as(pid, lambda: H.write(pid, h, b"hello\n")) == 6
    assert bus.sent(TTY) == (b"hello\n", 31250)
    bus.send(TTY, b"ok ")
    bus.send(TTY, b"42\n")
    got = _usb(pid, h)
    assert got[0] == (U.EV_SENT, TTY, 0, b"")
    assert [r for r in got if r[0] == U.EV_DATA] == [(U.EV_DATA, TTY, 3, b"ok 42\n")]
    for bad in ("serial=1", "baud=9600", "write=0", "write=200", "warp=1"):    # a keyboard
        assert set_(bad) == H.BAD, bad                                          # is no line
    bus.unplug(KBD)
    assert _usb(pid, h) == [(U.EV_DETACH, KBD, 0, b"")]
    assert set_("serial=1") == H.ABSENT


def test_one_usb_holder_its_permission_its_end_and_its_absence(bus):
    a, b = L.spawn("t.a"), L.spawn("t.b")
    from runtime import moy_usb
    h = _open(a, "usb")
    assert h > 0 and moy_usb.owner() == a
    assert _open(a, "usb") == H.BUSY and _open(b, "usb") == H.BUSY
    with pytest.raises(OSError) as e:
        _as(b, moybyte.usb.devices)                                     # Python's face too
    assert e.value.errno == errno.EBUSY
    moybyte.end(a)
    assert moy_usb.owner() == 0 and a not in H.HANDLES
    assert _open(b, "usb", "serial=9") == H.ABSENT and moy_usb.owner() == 0   # nothing kept
    assert _open(b, "usb", "warp=1") == H.BAD and moy_usb.owner() == 0
    devices.grants = lambda p: ("ble",)
    assert _open(b, "usb") == H.DENIED
    devices.grants = None
    bus.remove()
    assert _open(b, "usb") == H.ABSENT


# -- MIDI --------------------------------------------------------------------------------

KEYS = "c0:ff:ee:00:00:09"
MIDI_IO = "7772e5db-3868-4112-a1a9-f2669d106bf3"


@pytest.fixture
def midi():
    from runtime import moy_midi
    from runtime.host import gatt_sim, midi_sim, usb_sim
    devices.grants = devices.console = None
    leases.LEASES.grants = None
    usb_sim.install()
    gatt_sim.install()
    gatt_sim.device(KEYS, "Keys", rssi=-40)
    gatt_sim.chr(KEYS, MIDI_IO, 0x0040, b"")
    was = moybyte.WAKES.table
    moybyte.bind(mi.kernel())
    yield midi_sim
    for pid in list(H.HANDLES):
        moybyte.end(pid)
    while moy_midi.owner():
        moybyte.end(moy_midi.owner())
    devices.grants = None
    usb_sim.remove()
    gatt_sim.remove()
    midi_sim.remove()
    moybyte.WAKES.table = None
    if was is not None:
        moybyte.bind(was)


def _wakes(pid):
    out = [0] * 8
    for wid in out[:moybyte.WAKES.due(0, out)]:
        _as(pid, lambda: moybyte.WAKES.call(wid))


def test_a_midi_handle_a_port_on_a_uart_reads_whole_messages(midi):
    from runtime import moy_midi
    pid = L.spawn("t.midi")
    h = _open(pid, "midi", "uart=uart1")
    port = _get(pid, h, "port")
    assert h > 0 and _get(pid, h, "state") == 1 and moy_midi.owner() == pid
    assert leases.LEASES.of(pid, "uart1") is not None            # the lease is the caller's
    midi.wire(port, b"\x90\x3c\x64\x3e\xf8\x50\x40\x00")          # running status, a clock
    midi.wire(port, b"\xf0\x7e\x01\x02\xf7\xc1\x05")              # sysex read past
    assert _as(pid, lambda: H.read(pid, h, 2)) == H.FULL          # less than one message
    assert _as(pid, lambda: H.read(pid, h, 7)) == b"\x90\x3c\x64\xf8\x00\x00"   # whole ones
    assert _as(pid, lambda: H.read(pid, h, 64)) == b"\x90\x3e\x50\x90\x40\x00\xc1\x05\x00"
    assert _as(pid, lambda: H.read(pid, h, 64)) == b""
    assert _as(pid, lambda: H.write(pid, h, b"\x80\x3c\x00")) == 3
    assert lease_sim.sent(1) == b"\x80\x3c\x00"
    assert _as(pid, lambda: H.write(pid, h, b"\x3c\x00")) == H.BAD             # no status
    assert _open(L.spawn("t.other"), "midi", "uart=uart1") == H.BUSY
    assert _as(pid, lambda: H.close(pid, h)) == 0
    assert moy_midi.owner() == 0 and leases.LEASES.of(pid, "uart1") is None


def test_midi_handles_a_usb_and_a_ble_port_each_its_own_messages(midi):
    from runtime import moy_midi
    from runtime.host import gatt_sim, usb_sim
    pid = L.spawn("t.midi")
    usb_sim.plug(3, 3, 0, 0x0499, 0x1503, "Keys 49")              # a USB-MIDI device
    u = _open(pid, "midi", "usb=3")
    b = _open(pid, "midi", "ble=" + KEYS)
    assert _get(pid, b, "state") == 0
    assert _as(pid, lambda: H.write(pid, b, b"\x80\x3c\x00")) == H.ABSENT     # not ready
    for _ in range(3):
        _wakes(pid)                                               # connected, found, subscribed
    assert _get(pid, b, "state") == 1
    usb_sim.send(3, b"\x09\x91\x40\x7f")
    assert gatt_sim.notify(KEYS, 0x0040, b"\x80\x81\x90\x3c\x64")
    _wakes(pid)                                                   # the packet decoded
    assert _as(pid, lambda: H.read(pid, u, 64)) == b"\x91\x40\x7f"
    assert _as(pid, lambda: H.read(pid, b, 64)) == b"\x90\x3c\x64"
    assert _as(pid, lambda: H.write(pid, u, b"\xb0\x07\x64")) == 3
    assert usb_sim.sent(3)[0] == b"\x0b\xb0\x07\x64"
    assert _as(pid, lambda: H.write(pid, b, b"\x80\x3c\x00")) == 3
    assert gatt_sim.written(KEYS, 0x0040)[2:] == b"\x80\x3c\x00"
    assert _as(pid, lambda: H.close(pid, u)) == 0 and moy_midi.owner() == pid   # b still open
    moybyte.end(pid)
    assert moy_midi.owner() == 0 and pid not in H.HANDLES


def test_a_midi_handle_refuses_what_it_cannot_open_and_keeps_nothing(midi):
    from runtime import moy_midi
    pid = L.spawn("t.midi")
    for bad in ("", "uart=uart1 usb=3", "usb=3 baud=9600", "uart=spi2", "usb=0", "ble=c0:ff",
                "warp=1"):
        assert _open(pid, "midi", bad) == H.BAD, bad
    assert _open(pid, "midi", "usb=9") == H.ABSENT                # no such device
    assert moy_midi.owner() == 0
    leases.LEASES.grants = lambda p: ()
    assert _open(pid, "midi", "uart=uart1") == H.DENIED and moy_midi.owner() == 0
    leases.LEASES.grants = None
    devices.grants = lambda p: ("usb",)
    assert _open(pid, "midi", "uart=uart1") == H.DENIED
