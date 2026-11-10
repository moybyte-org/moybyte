"""Leases and device APIs by handle (docs/os_2026-11/hardware.md section 4):
what the `moybyte.dev_*` rows answer a compiled program, here called as its
process would be served -- the leases (gpio<n>, pins, uart<n>, spi<n>, i2c,
pwm<n>, adc<n>) and the lora and mic APIs by the kernel's handle table
(native/moy_kernel/moy_dev.h) over its buses' host twin
(runtime/host/bus_sim.py's far end) and the drivers' C, every other device
API by runtime/system/dev_handles.py -- over the host's stand-ins
(runtime/host/lease_sim.py, lora_sim.py, mic_sim.py) and the http client
against a local server. The
rows reached from each compiled tier are tests/test_wasm_app.py's,
tests/test_wasi_services.py's and tests/test_elf_commands.py's."""

import ctypes
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
from runtime.host import bus_sim, lease_sim
from unix_mp import ROOT

sys.path.insert(0, str(ROOT / "tools"))
import board_config  # noqa: E402

SPEC = board_config.lease_spec(str(ROOT / "firmware/esp32_p4_wifi6_touch_lcd_7b"))


@pytest.fixture(autouse=True)
def board():
    leases.stand_in(lease_sim, SPEC)
    lease_sim.reset()
    bus_sim.reset()
    yield
    for pid in list(H.HANDLES):
        moybyte.end(pid)
    for pid in list(leases.LEASES.held):
        leases.LEASES.sweep(pid)
    leases.LEASES.grants = None
    devices.grants = None
    _K().moy_dev_reset()
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


# The kernel's handle table, as the door's C half calls it (moy_app.c,
# dev_row): a process's pid and its grant's device permissions.
P_GPIO, P_PINS = 1 << 0, 1 << 1        # moy_dev.h's MOY_DEV_P_GPIO, MOY_DEV_P_PINS
EDGE_BYTES = 8
PASS = -1000                            # MOY_DEV_PASS: not the table's, the door's Python half's
_C, _S, _Z, _U = ctypes.c_char_p, ctypes.c_size_t, ctypes.c_int32, ctypes.c_uint32
_DEV_SIGS = (
    ("moy_dev_open", [_U, _U, _C, _S, _C, _S], _Z),
    ("moy_dev_close", [_U, _Z], _Z),
    ("moy_dev_read", [_U, _Z, ctypes.c_void_p, _S], _Z),
    ("moy_dev_write", [_U, _Z, _C, _S], _Z),
    ("moy_dev_xfer", [_U, _Z, _C, _S, ctypes.c_void_p, _S], _Z),
    ("moy_dev_perm_bit", [_C, _S], _U),
    ("moy_dev_set", [_U, _Z, _C, _S], _Z),
    ("moy_dev_get", [_U, _Z, _C, _S], _Z),
    ("moy_dev_sweep", [_U], _U),
    ("moy_dev_count", [], _U),
    ("moy_dev_reset", [], None),
)


def _K():
    lib = N._lib()
    for name, args, res in _DEV_SIGS:
        fn = getattr(lib, name)
        fn.argtypes, fn.restype = args, res
    return lib


def _bit(perm):
    b = perm.encode()
    return _K().moy_dev_perm_bit(b, len(b))


def _kopen(pid, name, cfg="", perms=None):
    if perms is None:
        perms = P_GPIO | P_PINS | _bit("lease:uart1") | _bit("lease:spi2")
    b, c = name.encode(), cfg.encode()
    return _K().moy_dev_open(pid, perms, b, len(b), c, len(c))


def _kwrite(pid, h, data):
    return _K().moy_dev_write(pid, h, data, len(data))


def _kxfer(pid, h, data, cap):
    buf = ctypes.create_string_buffer(max(cap, 1))
    n = _K().moy_dev_xfer(pid, h, data, len(data), buf, cap)
    return buf.raw[:n] if n >= 0 else n


def _kset(pid, h, cfg):
    b = cfg.encode()
    return _K().moy_dev_set(pid, h, b, len(b))


def _kget(pid, h, what):
    b = what.encode()
    return _K().moy_dev_get(pid, h, b, len(b))


def _kread(pid, h, cap):
    buf = ctypes.create_string_buffer(max(cap, 1))
    n = _K().moy_dev_read(pid, h, buf, cap)
    return buf.raw[:n] if n >= 0 else n


def _kclose(pid, h):
    return _K().moy_dev_close(pid, h)


def _end(pid):
    """Process `pid` ended: its Python handles and its kernel ones closed."""
    moybyte.end(pid)
    _K().moy_dev_sweep(pid)


# -- the leases -------------------------------------------------------------------------


def test_a_gpio_handle_drives_and_reads_its_pin():
    pid = L.spawn("t.gpio")
    h = _kopen(pid, "gpio6", "mode=out value=1")
    assert h > 0 and N.pin_state(6) & N.PIN_OUT and N.sim_get(6) == 1
    assert _kset(pid, h, "value=0") == 0 and _kget(pid, h, "value") == 0
    assert _kset(pid, h, "mode=in pull=up") == 0 and N.pin_state(6) & N.PIN_PU
    assert _kread(pid, h, 64) == b""                    # no edges asked for
    assert _kclose(pid, h) == 0 and N.pin_state(6) == N.PIN_SAFE
    assert _kget(pid, h, "value") == H.ABSENT


def test_a_gpio_handle_reads_the_edges_its_trigger_names():
    was = moybyte.WAKES.table
    moybyte.bind(mi.kernel())
    try:
        pid = L.spawn("t.edges")
        h = _kopen(pid, "gpio5", "mode=in")
        assert _kset(pid, h, "edges=rising") == 0
        for v in (1, 0, 1, 0):
            N.sim_level(5, v)
        assert _kread(pid, h, 4) == H.FULL
        got = _kread(pid, h, 64)
        assert len(got) == 2 * EDGE_BYTES
        assert [struct.unpack_from("<II", got, k)[1] for k in (0, 8)] == [1, 1]
        assert _kread(pid, h, 64) == b""
        assert _kset(pid, h, "edges=off") == 0 and _kread(pid, h, 64) == b""
        assert _kset(pid, h, "edges=sideways") == H.BAD
    finally:
        moybyte.WAKES.table = None
        if was is not None:
            moybyte.bind(was)


def test_a_uart_handle_writes_and_reads_what_waits():
    pid = L.spawn("t.uart")
    h = _kopen(pid, "uart1", "baudrate=9600 parity=none")
    assert h > 0 and N.pin_state(47) & N.PIN_PERIPH and N.pin_state(48) & N.PIN_PERIPH
    bus_sim.loopback(1, True)
    assert _kwrite(pid, h, b"ping") == 4 and _kget(pid, h, "any") == 4
    assert _kread(pid, h, 2) == b"pi" and _kread(pid, h, 64) == b"ng"
    assert _kread(pid, h, 64) == b"" and bus_sim.sent(1) == b"ping"
    bus_sim.feed(1, b"pong")
    assert _kread(pid, h, 64) == b"pong"
    assert _kset(pid, h, "baudrate=115200 bits=7 parity=even stop=2 timeout=5") == 0
    assert _kset(pid, h, "speed=3") == H.BAD
    assert _kset(pid, h, "parity=0") == H.BAD
    assert _kset(pid, h, "tx=20") == H.BAD                # a bus's pins are its open's
    assert _kxfer(pid, h, b"x", 1) == H.BAD and _kget(pid, h, "baudrate") == H.BAD
    assert _kclose(pid, h) == 0 and N.pin_state(47) == N.PIN_SAFE


def test_a_uart_handle_takes_other_pins_of_the_gpio_row():
    pid = L.spawn("t.uart")
    h = _kopen(pid, "uart1", "tx=20 rx=21 rxbuf=1024")
    assert h > 0 and N.of_pin(20) and N.of_pin(21) and not N.of_pin(47)
    assert _kopen(L.spawn("t.other"), "uart1") == H.BUSY  # one unit, one holder
    assert _kclose(pid, h) == 0
    assert _kopen(pid, "uart1", "tx=7") == H.BAD          # not a pin of the row


def test_an_spi_handle_writes_reads_and_transfers_full_duplex():
    pid = L.spawn("t.spi")
    h = _kopen(pid, "spi2", "baudrate=1000000 polarity=0 phase=0")
    assert h > 0 and N.pin_state(49) & N.PIN_PERIPH
    assert _kwrite(pid, h, b"\x9f") == 1
    assert _kxfer(pid, h, b"\x01\x02\x03", 3) == b"\xff\xff\xff"
    assert _kxfer(pid, h, b"\x01\x02\x03", 2) == H.FULL
    assert _kread(pid, h, 2) == b"\xff\xff"
    assert bus_sim.spi_sent(2) == b"\x9f\x01\x02\x03\x00\x00"
    assert _kset(pid, h, "polarity=1 phase=1 firstbit=lsb baudrate=8000000") == 0
    assert _kset(pid, h, "sck=20") == H.BAD and _kset(pid, h, "bits=16") == H.BAD
    assert _kset(pid, h, "firstbit=1") == H.BAD and _kget(pid, h, "any") == H.BAD
    assert _kopen(pid, "spi2", "sck=20 mosi=20") == H.BAD


def test_an_i2c_handle_reads_a_register_of_the_device_at_its_address():
    pid = L.spawn("t.i2c")
    bus_sim.i2c(0x48, b"\x10\x20\x30\x40")
    h = _kopen(pid, "i2c", "scl=20 sda=21 freq=100000")
    assert _kread(pid, h, 1) == H.BAD                  # no address yet
    assert _kget(pid, h, "addr") == -1
    assert _kset(pid, h, "addr=0x48") == 0 and _kget(pid, h, "addr") == 0x48
    assert _kxfer(pid, h, b"\x02", 2) == b"\x30\x40"
    assert _kwrite(pid, h, b"\x00\x99") == 2 and bus_sim.i2c_regs(0x48)[0] == 0x99
    assert _kset(pid, h, "addr=0x50") == 0 and _kread(pid, h, 1) == H.ABSENT   # no ACK
    assert _kset(pid, h, "addr=128") == H.BAD and _kget(pid, h, "addr") == 0x50
    assert N.pin_state(20) & N.PIN_PERIPH and N.pin_state(21) & N.PIN_PERIPH
    assert _kopen(pid, "i2c", "scl=20") == H.BAD
    assert _kopen(pid, "i2c", "scl=22 sda=22") == H.BAD
    assert _kopen(L.spawn("t.other"), "i2c", "scl=22 sda=23") == H.BUSY   # one unit
    assert _kclose(pid, h) == 0
    h = _kopen(pid, "i2c", "scl=22 sda=23 addr=0x48")
    assert h > 0 and _kget(pid, h, "addr") == 0x48


def test_a_pwm_and_an_adc_handle():
    pid = L.spawn("t.pwm")
    p = _kopen(pid, "pwm22", "freq=1000 duty=32768")
    assert _kget(pid, p, "freq") == 1000 and _kget(pid, p, "duty") == 32768
    assert bus_sim.pwm(22) == (1000, 32768) and N.pin_state(22) & N.PIN_OUT
    assert _kset(pid, p, "duty=0") == 0 and _kget(pid, p, "duty") == 0
    assert _kset(pid, p, "duty=65536") == H.BAD and bus_sim.pwm(22) == (1000, 0)
    q = _kopen(pid, "pwm20")
    assert _kget(pid, q, "freq") == 5000 and _kget(pid, q, "duty") == 32768
    assert _kopen(pid, "pwm21") == H.FULL, "two PWM slots"
    assert _kclose(pid, q) == 0 and bus_sim.pwm(20) == (0, 0)
    bus_sim.adc(23, 1234)
    a = _kopen(pid, "adc23")
    assert _kget(pid, a, "value") == 1234
    assert _kwrite(pid, a, b"x") == H.BAD and _kset(pid, a, "x=1") == H.BAD
    assert _kopen(pid, "adc23") == H.BUSY and _kopen(pid, "adc21", "atten=3") == H.BAD


def test_a_pwm_handle_takes_its_channel_and_timer_from_the_ledc_book():
    """The kernel's LEDC book (native/moy_kernel/moy_ledc.h): a handle's
    channel from the top, held by its process; a second at the same frequency
    shares the first's timer, one at another takes its own; a machine.PWM
    channel (the VM's) beside them is never taken; a close gives both back."""
    from runtime.host import ledc_book as B
    book = B.lib()
    book.moy_ledc_reset()
    vm = book.moy_ledc_chan_take(B.VM, False)
    assert vm == 0
    pid = L.spawn("t.pwm")
    p = _kopen(pid, "pwm22", "freq=1000")
    q = _kopen(pid, "pwm20", "freq=1000")
    assert p > 0 and q > 0
    assert B.holders() == [B.VM, 0, 0, 0, 0, 0, pid, pid]
    t = book.moy_ledc_chan_timer(7)
    assert book.moy_ledc_chan_timer(6) == t and B.cfg(t)[0] == 1000
    assert book.moy_ledc_timer_users(t, -1) == 2
    assert _kset(pid, q, "freq=50") == 0 and bus_sim.pwm(20)[0] == 50
    u = book.moy_ledc_chan_timer(6)
    assert u != t and B.cfg(u)[0] == 50 and B.cfg(t)[0] == 1000
    assert _kclose(pid, q) == 0 and _kclose(pid, p) == 0
    assert B.holders() == [B.VM] + [0] * 7
    assert book.moy_ledc_timer_state(t) == B.FREE == book.moy_ledc_timer_state(u)
    book.moy_ledc_reset()

def test_a_name_or_cfg_the_console_does_not_take_is_bad():
    pid = L.spawn("t.bad")
    assert _kopen(pid, "uart9") == H.BAD and _kopen(pid, "spi1") == H.BAD
    assert _kopen(pid, "uart1", "baudrate=0") == H.BAD
    assert _open(pid, "gpio6") == H.BAD                   # the kernel's, never Python's
    assert _open(pid, "uart1") == H.BAD and _open(pid, "i2c") == H.BAD
    assert _kopen(pid, "gpio") == PASS and _open(pid, "gpio") == H.BAD   # not the kernel's
    assert _kopen(pid, "uart") == PASS and _kopen(pid, "uart123") == PASS
    assert _kopen(pid, "gpio6", "mode=sideways") == H.BAD
    assert _kopen(pid, "gpio6", "nonsense") == H.BAD
    assert _open(pid, "toaster") == H.BAD
    assert N.held() == 0 and _K().moy_dev_count() == 0


def test_a_lease_needs_its_row_granted():
    pid = L.spawn("t.perm")
    assert _kopen(pid, "uart1", perms=P_GPIO) == H.DENIED
    assert _kopen(pid, "spi2", perms=P_GPIO | _bit("lease:uart1")) == H.DENIED
    assert _kopen(pid, "uart1", perms=_bit("lease:uart1")) > 0
    assert _kopen(pid, "pwm22", perms=P_PINS) == H.DENIED
    assert _kopen(pid, "i2c", "scl=20 sda=21", perms=0) == H.DENIED
    assert _kopen(pid, "pwm22", perms=P_GPIO) > 0
    assert _kopen(pid, "gpio6", perms=P_PINS) == H.DENIED
    assert _kopen(pid, "gpio6", perms=P_GPIO) > 0
    assert _bit("lease:uart9") == 0


def test_one_holder_across_the_tiers_a_python_object_or_a_handle():
    py, native = L.spawn("t.py"), L.spawn("t.native")
    u = _as(py, lambda: moybyte.lease("uart1"))
    assert _kopen(native, "uart1") == H.BUSY
    h = _kopen(native, "gpio6", "mode=out")
    with pytest.raises(OSError) as e:
        _as(py, lambda: moybyte.lease("gpio6"))
    assert e.value.errno == errno.EBUSY
    with pytest.raises(OSError) as e:
        _as(py, lambda: moybyte.lease("i2c", scl=6, sda=20))
    assert e.value.errno == errno.EBUSY
    leases.LEASES.drop(u)
    k = _kopen(native, "uart1")
    assert k > 0
    with pytest.raises(OSError) as e:
        _as(py, lambda: moybyte.lease("uart1"))
    assert e.value.errno == errno.EBUSY
    assert _kopen(native, "i2c", "scl=20 sda=6") == H.BUSY
    assert _kclose(native, h) == 0 and _kclose(native, k) == 0
    _as(py, lambda: moybyte.lease("gpio6"))


def test_a_handle_is_its_process_s_own():
    a, b = L.spawn("t.a"), L.spawn("t.b")
    h = _kopen(a, "gpio6", "mode=out")
    assert _kget(b, h, "value") == H.ABSENT and _kclose(b, h) == H.ABSENT
    assert _kclose(a, h) == 0 and _kclose(a, h) == H.ABSENT
    u = _kopen(a, "uart1")
    assert _kget(b, u, "any") == H.ABSENT and _kwrite(b, u, b"x") == H.ABSENT
    assert _kclose(b, u) == H.ABSENT
    assert _kclose(a, u) == 0 and _kclose(a, u) == H.ABSENT


def test_the_process_s_end_closes_every_handle_and_leaves_the_pins_safe():
    was = moybyte.WAKES.table
    moybyte.bind(mi.kernel())
    try:
        pid = L.spawn("t.end")
        _kopen(pid, "gpio5", "mode=in")
        h = _kopen(pid, "gpio6", "mode=out value=1")
        assert _kset(pid, h, "edges=both") == 0
        assert _kopen(pid, "uart1") > 0 and _kopen(pid, "spi2") > 0
        assert _kopen(pid, "i2c", "scl=20 sda=21") > 0
        assert _kopen(pid, "pwm22") > 0 and _kopen(pid, "adc23") > 0
        assert N.held() == 7 and N.held(N.VM) == 0, "the kernel's hold no VM stop off"
        _end(pid)
        assert N.held() == 0 and _K().moy_dev_count() == 0
        pins = (5, 6, 20, 21, 22, 23, 47, 48, 49, 50, 51)
        assert [N.pin_state(p) for p in pins] == [N.PIN_SAFE] * len(pins)
        assert bus_sim.pwm(22) == (0, 0)
        assert _kopen(L.spawn("t.next"), "i2c", "scl=20 sda=21") > 0, "the unit went back"
    finally:
        moybyte.WAKES.table = None
        if was is not None:
            moybyte.bind(was)


# -- the device APIs --------------------------------------------------------------------

LORA, MIC = 1 << 3, 1 << 2              # moy_dev.h's MOY_DEV_P_LORA, MOY_DEV_P_MIC


def test_a_lora_handle_sends_and_reads_packets_with_their_rssi(radio):
    N, sim = radio
    pid, other = L.spawn("t.lora"), L.spawn("t.other")
    h = _kopen(pid, "lora", "sf=7", LORA)
    assert h > 0 and N.owner() == pid
    assert _kopen(pid, "lora", "", LORA) == H.BUSY        # one handle on the one radio
    assert _kopen(other, "lora", "", LORA) == H.BUSY
    assert sim.air(b"ping", 868100000, 7, rssi=-97, snr=-26)
    N.step(0)
    assert _kread(pid, h, 64) == b"ping"
    assert _kget(pid, h, "rssi") == -97 and _kget(pid, h, "snr") == -26
    assert _kread(pid, h, 64) == b""
    assert _kwrite(pid, h, b"pong") == 4 and sim.sent()[0] == b"pong"
    assert _kset(pid, h, "freq=1") == H.BAD                # outside the radio's range
    assert _kset(pid, h, "sf=8 speed=3") == H.BAD and _kget(pid, h, "nope") == H.BAD
    assert _kclose(pid, h) == 0 and N.owner() == 0
    assert _kopen(other, "lora", "", 0) == H.DENIED
    assert _kopen(other, "lora", "sf=13", LORA) == H.BAD and N.owner() == 0


def test_a_lora_handle_ends_with_its_process(radio):
    N, sim = radio
    pid = L.spawn("t.lora")
    h = _kopen(pid, "lora", "", LORA)
    assert h > 0 and _K().moy_dev_count() == 1
    _end(pid)
    assert N.owner() == 0 and _K().moy_dev_count() == 0 and _kget(pid, h, "rssi") == H.ABSENT


def test_a_mic_handle_reads_pcm16_and_its_end_lets_the_microphone_go():
    from runtime import moy_mic
    from runtime.host import mic_sim
    mic_sim.install(22050)
    try:
        pid = L.spawn("t.mic")
        assert _kopen(pid, "mic", "", 0) == H.DENIED
        assert _kopen(pid, "mic", "gain=3", MIC) == H.BAD
        h = _kopen(pid, "mic", "", MIC)
        assert moy_mic.owner() == pid and _kget(pid, h, "rate") == 22050
        mic_sim.push([1, -2, 3])
        assert _kread(pid, h, 7) == struct.pack("<3h", 1, -2, 3)   # whole samples only
        assert _kread(pid, h, 64) == b"" and _kget(pid, h, "drops") == 0
        assert _kwrite(pid, h, b"x") == H.BAD
        assert _kopen(L.spawn("t.other"), "mic", "", MIC) == H.BUSY
        _end(pid)
        assert moy_mic.owner() == 0 and _K().moy_dev_count() == 0
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

@pytest.fixture
def radio():
    from runtime import moy_lora
    from runtime.host import lora_sim
    lora_sim.install()
    yield moy_lora, lora_sim
    _K().moy_dev_reset()
    while moy_lora.owner():
        moybyte.end(moy_lora.owner())
    lora_sim.remove()


DEV_EUI = "70B3D57ED0050001"
JOIN_EUI = "0000000000000000"
APP_KEY = "8E3F2A1B0C9D8E7F6A5B4C3D2E1F0A9B"
JOIN = "join=otaa dev_eui=%s join_eui=%s app_key=%s" % (DEV_EUI, JOIN_EUI, APP_KEY)
EARLY = 20                      # MOY_LORA_WIN_EARLY_MS




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
    h = _kopen(pid, "lora", "send=2", LORA)
    assert _kwrite(pid, h, b"early") == H.ABSENT        # before a join
    assert _kget(pid, h, "joined") == 0
    assert _kset(pid, h, JOIN) == 0
    req, freq, sf, *_ = sim.sent()
    assert _kget(pid, h, "busy") == 1
    _rx1(N, sim, net.answer(req), freq, sf, 1000)
    assert _kget(pid, h, "joined") == 1 and _kget(pid, h, "busy") == 0
    assert _kwrite(pid, h, b"temp=21.5") == 9
    up, freq, sf, *_ = sim.sent()
    assert net.answer(up) is None and net.uplinks == [(0, 2, b"temp=21.5", False)]
    t = 20000
    N.step(t)
    while _kget(pid, h, "busy") and t < 40000:                # its windows close empty
        t = N.step(t) or t + 1
    assert _kget(pid, h, "busy") == 0 and _kget(pid, h, "fcnt") == 1
    net.down(7, b"\x01\x02led")
    assert _kset(pid, h, "send=5 confirmed=1 dr=4") == 0
    assert _kwrite(pid, h, b"\xff") == 1
    assert _kwrite(pid, h, b"\xfe") == H.BUSY          # its windows run
    up, freq, sf, *_ = sim.sent()
    assert sf == 8
    reply = net.answer(up)
    assert net.uplinks[-1] == (1, 5, b"\xff", True)
    _rx1(N, sim, reply, freq, sf, 50000)
    assert _kread(pid, h, 32) == b"\x01\x02led"
    assert _kget(pid, h, "port") == 7
    assert _kset(pid, h, "send=raw") == 0
    assert _kwrite(pid, h, b"mesh") == 4 and sim.sent()[0] == b"mesh"


def test_a_lora_handle_refuses_a_join_it_cannot_make(radio):
    pid = L.spawn("t.node")
    h = _kopen(pid, "lora", "", LORA)
    for bad in ("join=abp " + JOIN[10:], "join=otaa dev_eui=%s" % DEV_EUI,
                JOIN.replace(APP_KEY, APP_KEY[:-2]), JOIN.replace(DEV_EUI, "zz" * 8),
                "dev_eui=" + DEV_EUI, "send=0", "send=224", "confirmed=2", "dr=9"):
        assert _kset(pid, h, bad) == H.BAD, bad
    assert _kopen(L.spawn("t.other"), "lora", JOIN, 0) == H.DENIED


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
    _K().moy_dev_reset()
    if moy_midi.owner():
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


MIDI = 1 << 6                           # moy_dev.h's MOY_DEV_P_MIDI


def _midi_perms():
    return MIDI | _bit("lease:uart1")


def test_a_midi_handle_a_port_on_a_uart_reads_whole_messages(midi):
    from runtime import moy_midi
    pid = L.spawn("t.midi")
    h = _kopen(pid, "midi", "uart=uart1", _midi_perms())
    port = _kget(pid, h, "port")
    assert h > 0 and _kget(pid, h, "state") == 1 and moy_midi.owner() == pid
    assert N.of_pin(47) and N.of_pin(48)                          # the row is the caller's
    midi.wire(port, b"\x90\x3c\x64\x3e\xf8\x50\x40\x00")          # running status, a clock
    midi.wire(port, b"\xf0\x7e\x01\x02\xf7\xc1\x05")              # sysex read past
    assert _kread(pid, h, 2) == H.FULL                            # less than one message
    assert _kread(pid, h, 7) == b"\x90\x3c\x64\xf8\x00\x00"         # whole ones
    assert _kread(pid, h, 64) == b"\x90\x3e\x50\x90\x40\x00\xc1\x05\x00"
    assert _kread(pid, h, 64) == b""
    assert _kwrite(pid, h, b"\x80\x3c\x00") == 3 and bus_sim.sent(1) == b"\x80\x3c\x00"
    assert _kwrite(pid, h, b"\x3c\x00") == H.BAD                   # no status
    assert _kopen(L.spawn("t.other"), "midi", "uart=uart1", _midi_perms()) == H.BUSY
    assert _kclose(pid, h) == 0
    assert moy_midi.owner() == 0 and not N.of_pin(47)


def test_midi_handles_a_usb_and_a_ble_port_each_its_own_messages(midi):
    from runtime import moy_gatt, moy_midi, moy_usb
    from runtime.host import gatt_sim, usb_sim
    pid = L.spawn("t.midi")
    usb_sim.plug(3, 3, 0, 0x0499, 0x1503, "Keys 49")              # a USB-MIDI device
    u = _kopen(pid, "midi", "usb=3", MIDI)
    b = _kopen(pid, "midi", "ble=" + KEYS, MIDI)
    assert u > 0 and b > 0 and moy_usb.owner() == pid and moy_gatt.owner() == pid
    assert _kget(pid, b, "state") == 1                            # connected, found, subscribed
    usb_sim.send(3, b"\x09\x91\x40\x7f")
    assert gatt_sim.notify(KEYS, 0x0040, b"\x80\x81\x90\x3c\x64")   # decoded on the event
    assert _kread(pid, u, 64) == b"\x91\x40\x7f"
    assert _kread(pid, b, 64) == b"\x90\x3c\x64"
    assert _kwrite(pid, u, b"\xb0\x07\x64") == 3
    assert usb_sim.sent(3)[0] == b"\x0b\xb0\x07\x64"
    assert _kwrite(pid, b, b"\x80\x3c\x00") == 3
    assert gatt_sim.written(KEYS, 0x0040)[2:] == b"\x80\x3c\x00"
    assert _kclose(pid, u) == 0 and moy_midi.owner() == pid      # b still open
    assert moy_usb.owner() == 0                                   # the host it took, let go
    _end(pid)
    assert moy_midi.owner() == 0 and moy_gatt.owner() == 0


def test_a_ble_midi_port_reads_gone_once_its_link_drops(midi):
    from runtime import moy_gatt
    pid = L.spawn("t.midi")
    b = _kopen(pid, "midi", "ble=" + KEYS, MIDI)
    assert _kget(pid, b, "state") == 1
    moy_gatt.disconnect(pid)
    assert _kget(pid, b, "state") == 2 and _kwrite(pid, b, b"\x80\x3c\x00") == H.ABSENT


def test_a_ble_midi_port_keeps_its_link_through_the_vm_s_end(midi):
    """The BLE driver a handle took is no VM's (moy_lease_vm_stop keeps it,
    moy_dev_holds): its port's machine still decodes what the peripheral
    notifies, on the driver's events."""
    from runtime import moy_gatt
    from runtime.host import gatt_sim
    pid = L.spawn("t.midi")
    b = _kopen(pid, "midi", "ble=" + KEYS, MIDI)
    assert _kget(pid, b, "state") == 1 and moy_gatt.owner() == pid
    assert N.vm_stop() == 0 and moy_gatt.owner() == pid
    assert gatt_sim.notify(KEYS, 0x0040, b"\x80\x81\x90\x3c\x64")
    assert _kread(pid, b, 64) == b"\x90\x3c\x64"
    _end(pid)
    assert moy_gatt.owner() == 0


def test_midi_shares_its_one_holder_with_the_python_face(midi):
    from runtime import dev_midi, moy_midi
    from runtime.host import usb_sim
    pid = L.spawn("t.midi")
    usb_sim.plug(3, 3, 0, 0x0499, 0x1503, "Keys 49")
    h = _kopen(pid, "midi", "usb=3", MIDI)
    port = _as(pid, lambda: dev_midi.uart("uart1"))               # the face's port beside it
    assert moy_midi.ports(pid) == 2
    assert _kclose(pid, h) == 0 and moy_midi.owner() == pid      # the face's port keeps it
    _as(pid, lambda: dev_midi.close(port))
    assert moy_midi.ports(pid) == 0


def test_a_midi_handle_refuses_what_it_cannot_open_and_keeps_nothing(midi):
    from runtime import moy_midi
    pid = L.spawn("t.midi")
    for bad in ("", "uart=uart1 usb=3", "usb=3 baud=9600", "uart=spi2", "usb=0", "ble=c0:ff",
                "warp=1"):
        assert _kopen(pid, "midi", bad, _midi_perms()) == H.BAD, bad
    assert _kopen(pid, "midi", "usb=9", MIDI) == H.ABSENT          # no such device
    assert moy_midi.owner() == 0
    assert _kopen(pid, "midi", "uart=uart1", MIDI) == H.DENIED and moy_midi.owner() == 0
    assert _kopen(pid, "midi", "uart=uart1", _bit("lease:uart1")) == H.DENIED


# -- the camera --------------------------------------------------------------------------


def test_a_camera_handle_reads_whole_frames_and_its_end_lets_it_go():
    from runtime import moy_camera
    from runtime.host import camera_sim
    devices.grants = devices.console = None
    camera_sim.install(4, 2)
    try:
        pid, other = L.spawn("t.cam"), L.spawn("t.other")
        h = _open(pid, "camera")
        assert h > 0 and moy_camera.owner() == pid
        assert _get(pid, h, "w") == 4 and _get(pid, h, "h") == 2
        assert _as(pid, lambda: H.read(pid, h, 16)) == b""        # before the first frame
        frame = bytes(range(16))
        camera_sim.frame(frame)
        assert _as(pid, lambda: H.read(pid, h, 15)) == H.FULL     # whole frames only
        assert _as(pid, lambda: H.read(pid, h, 64)) == frame
        assert _open(pid, "camera") == H.BUSY and _open(other, "camera") == H.BUSY
        assert _open(other, "camera", "fps=30") == H.BAD
        moybyte.end(pid)
        assert moy_camera.owner() == 0
        devices.grants = lambda p: ("mic",)
        assert _open(other, "camera") == H.DENIED
        devices.grants = None
        camera_sim.remove()
        assert _open(other, "camera") == H.ABSENT
    finally:
        devices.grants = None
        camera_sim.remove()


# -- the media service -------------------------------------------------------------------

TONE = ROOT / "tests" / "fixtures" / "media" / "tone_1k.mp3"


@pytest.fixture
def media():
    from runtime import audio_binding, dev_media
    from runtime import moy_media
    if audio_binding.get() is None or not dev_media.present():
        pytest.skip("no media service on this host")
    devices.grants = devices.console = None
    yield dev_media
    for pid in list(H.HANDLES):
        moybyte.end(pid)
    devices.grants = None
    _shell_stops(dev_media)
    assert moy_media.pid() == 0


def _shell_stops(dev_media):
    """The stream stopped as the shell, the run given back."""
    from runtime import moy_media
    was = L.current()
    L.current(devices.SHELL)
    try:
        dev_media.stop()
    finally:
        L.current(was)
    for _ in range(50):
        if not moy_media.pid():
            break
        moy_media.pump()


def test_a_media_handle_plays_a_file_and_reads_what_plays(media):
    from runtime import moy_media
    pid = L.spawn("t.music")
    h = _open(pid, "media")
    assert _get(pid, h, "state") == 0 and _get(pid, h, "mine") == 0
    assert _as(pid, lambda: H.read(pid, h, 64)) == b""                 # no stream
    assert _as(pid, lambda: H.set(pid, h, "pause=1")) == H.ABSENT
    assert _as(pid, lambda: H.set(pid, h, "stop=1")) == 0
    probe = _as(pid, lambda: H.xfer(pid, h, str(TONE).encode(), 64))
    assert probe == struct.pack("<I", 2) + b"Tone\n"
    assert _as(pid, lambda: H.xfer(pid, h, str(TONE).encode(), 6)) == H.FULL
    assert _as(pid, lambda: H.xfer(pid, h, b"/no/such.mp3", 64)) == H.ABSENT
    assert _as(pid, lambda: H.write(pid, h, b"/no/such.mp3")) == H.ABSENT
    assert moy_media.pid() == 0
    assert _as(pid, lambda: H.write(pid, h, str(TONE).encode())) == len(str(TONE))
    assert moy_media.pid() not in (0, pid) and _get(pid, h, "mine") == 1
    assert _get(pid, h, "state") in (1, 2) and _get(pid, h, "entries") == 1
    assert _as(pid, lambda: H.set(pid, h, "volume=40 pause=1")) == 0
    assert _get(pid, h, "paused") == 1 and _get(pid, h, "volume") <= 40
    assert _as(pid, lambda: H.set(pid, h, "pause=0")) == 0 and _get(pid, h, "paused") == 0
    assert _as(pid, lambda: H.set(pid, h, "skip=next")) == H.ABSENT    # the list's end
    text = _as(pid, lambda: H.read(pid, h, 512)).decode().split("\n")
    assert len(text) == 5 and text[3] == str(TONE)
    assert _as(pid, lambda: H.read(pid, h, 4)) == H.FULL
    for bad in ("volume=101", "pause=2", "skip=up", "stop=0", "warp=1"):
        assert _as(pid, lambda: H.set(pid, h, bad)) == H.BAD, bad
    other = L.spawn("t.other")
    o = _open(other, "media")
    assert _get(other, o, "mine") == 0
    assert _as(other, lambda: H.set(other, o, "pause=1")) == 0         # a client acts on it
    assert _get(pid, h, "paused") == 1
    assert _as(other, lambda: H.write(other, o, str(TONE).encode())) > 0   # the last command wins
    assert _get(other, o, "mine") == 1 and _get(pid, h, "mine") == 0
    assert _open(pid, "media") == H.BUSY                               # one handle a process
    run = moy_media.pid()
    assert _as(pid, lambda: H.close(pid, h)) == 0                      # its close leaves it playing
    assert moy_media.pid() == run
    assert _as(other, lambda: H.set(other, o, "stop=1")) == 0
    assert moy_media.pid() == 0


def test_a_media_handle_needs_either_permission_and_its_end_leaves_the_stream(media,
                                                                              monkeypatch):
    from runtime import moy_media
    a, b = L.spawn("t.a"), L.spawn("t.b")
    devices.grants = lambda p: {a: {"media"}, b: {"media:url"}}.get(p, set())
    monkeypatch.setattr(devices, "cart_of",       # the tone is in a's own folder
                        lambda p: {"path": str(TONE.parent)} if p in (a, b) else None)
    assert _open(L.spawn("t.none"), "media") == H.DENIED
    hb = _open(b, "media")
    assert _as(b, lambda: H.write(b, hb, str(TONE).encode())) == H.DENIED      # a file is media's
    assert _as(b, lambda: H.write(b, hb, b"http://127.0.0.1:1/x")) == H.DENIED  # and http's
    ha = _open(a, "media")
    assert _as(a, lambda: H.write(a, ha, str(TONE).encode())) > 0
    run = moy_media.pid()
    assert run and media.now()["client"] == a
    moybyte.end(a)
    assert a not in H.HANDLES and moy_media.pid() == run       # the system's: it plays on


# -- the pins ----------------------------------------------------------------------------


def test_a_pins_handle_drives_and_reads_the_pins_it_takes():
    pid, py = L.spawn("t.pins"), L.spawn("t.py")
    h = _kopen(pid, "pins", "gpio6=1")
    assert h > 0 and N.pin_state(6) & N.PIN_OUT and N.sim_get(6) == 1
    assert _kset(pid, h, "gpio6=0 gpio20=1") == 0
    assert N.sim_get(6) == 0 and N.sim_get(20) == 1
    N.sim_level(5, 1)
    assert _kget(pid, h, "gpio5") == 1 and not N.pin_state(5) & N.PIN_OUT   # an input
    assert _kget(pid, h, "gpio6") == 0
    assert _as(py, lambda: leases.LocalPins(leases.LEASES).write(6, 1)) is False  # Python's
    for bad in ("gpio6=2", "pin6=1", "gpio=1", "warp", "gpio7=1"):          # 7: not the row's
        assert _kset(pid, h, bad) == H.BAD, bad
    assert _kget(pid, h, "value") == H.BAD
    assert _kopen(pid, "pins") == H.BUSY                      # one handle a process
    other = L.spawn("t.other")
    o = _kopen(other, "pins")
    assert _kset(other, o, "gpio6=1") == H.BUSY               # pid holds it
    assert _kclose(pid, h) == 0
    assert [N.pin_state(p) for p in (5, 6, 20)] == [N.PIN_SAFE] * 3
    assert _kset(other, o, "gpio6=1") == 0 and N.sim_get(6) == 1
    _end(other)
    assert N.pin_state(6) == N.PIN_SAFE and N.held() == 0


def test_a_pins_handle_needs_the_permission_and_pins_and_keeps_nothing_refused():
    pid = L.spawn("t.pins")
    assert _kopen(pid, "pins", "gpio6=1", perms=P_PINS) > 0  # no lease:gpio: pins is enough
    _end(pid)
    assert _kopen(pid, "pins", perms=P_GPIO) == H.DENIED
    assert _kopen(pid, "pins", "gpio6=1 gpio7=9") == H.BAD and N.held() == 0
    assert _open(pid, "pins") == H.BAD                       # the kernel's, never Python's
    leases.stand_in(None, None)
    assert _kopen(pid, "pins") == H.ABSENT


def test_a_media_handle_plays_only_from_its_own_folders(media, tmp_path, monkeypatch):
    """A gated process plays a file only from its cart's folder and the
    folder its `files` permission names: anything else is DENIED, there
    or not (so a refusal tells it nothing of what is there), a probe as
    much as a play, and a playlist in its folder naming a file outside it
    as much as the file. A URL is unchanged."""
    import shutil
    store = tmp_path / "store"
    carts = store / "carts"
    own = carts / "local.player.moy"
    own.mkdir(parents=True)
    music = store / "files" / "Music"
    music.mkdir(parents=True)
    docs = store / "files" / "Documents"
    docs.mkdir(parents=True)
    shutil.copy(TONE, own / "tone.mp3")
    shutil.copy(TONE, music / "song.mp3")
    shutil.copy(TONE, docs / "secret.mp3")
    (own / "list.m3u").write_text(str(docs / "secret.mp3") + "\n")
    pid = L.spawn("t.player")
    cart = {"path": str(own), "id": "local.player"}
    monkeypatch.setattr(devices, "grants",
                        lambda p: ("media", "files:music") if p == pid else None)
    monkeypatch.setattr(devices, "cart_of", lambda p: cart if p == pid else None)
    monkeypatch.setattr(devices, "console", type("C", (), {"carts_root": str(carts)})())
    h = _open(pid, "media")
    for path in (docs / "secret.mp3", docs / "missing.mp3", TONE,
                 own / ".." / ".." / "files" / "Documents" / "secret.mp3"):
        assert _as(pid, lambda: H.write(pid, h, str(path).encode())) == H.DENIED, path
        assert _as(pid, lambda: H.xfer(pid, h, str(path).encode(), 64)) == H.DENIED, path
    assert _as(pid, lambda: H.write(pid, h, str(own / "list.m3u").encode())) == H.DENIED
    from runtime import moy_media
    for _ in range(4):
        moy_media.pump()
    assert not moy_media.pid()                                  # the refused list left nothing
    assert _as(pid, lambda: H.write(pid, h, str(own / "missing.mp3").encode())) == H.ABSENT
    assert _as(pid, lambda: H.xfer(pid, h, str(music / "song.mp3").encode(), 64)) \
        == struct.pack("<I", 2) + b"Tone\n"
    src = str(own / "tone.mp3")
    assert _as(pid, lambda: H.write(pid, h, src.encode())) == len(src)
    assert _get(pid, h, "mine") == 1
    src = str(music / "song.mp3")
    assert _as(pid, lambda: H.write(pid, h, src.encode())) == len(src)
    assert _as(pid, lambda: H.close(pid, h)) == 0
