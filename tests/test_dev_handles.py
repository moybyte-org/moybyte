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
