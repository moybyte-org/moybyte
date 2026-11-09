"""The lease table (native/moy_kernel/moy_lease.h) and its Python face
(runtime/system/leases.py, docs/os_2026-11/hardware.md section 1): the board's
offers, a take and what refuses one, the sweep a process's exit, crash and kill
make, pins left safe, the radio's row (THE RADIO: the WiFi station's
holders, one row of tags), a kept machine object raising, `pins`, the permission
rows and their "full access" lines, /dev and /proc (runtime/system/devfs.py),
and a micropython-lib driver unchanged on a leased bus in the desktop
MicroPython. The machine objects are the host's stand-in
(runtime/host/lease_sim.py) over the C table's stand-in pins."""

import errno
import random
import subprocess
import sys
import time

import pytest

from runtime import app_perms as P
from runtime import devfs, leases, moybyte
from runtime import moy_input as mi
from runtime import moy_lease as N
from runtime import moy_log as L
from runtime import scheduler as S
from runtime.host import lease_sim
from unix_mp import ROOT, require_unix_mp, runtime_tree

sys.path.insert(0, str(ROOT / "tools"))
import board_config  # noqa: E402

P4 = "firmware/esp32_p4_wifi6_touch_lcd_7b"
SPEC = board_config.lease_spec(str(ROOT / P4))


class Guard:
    """moy_guard's face: a call aborts while `abort` is set."""

    def __init__(self):
        self.abort = False

    def call(self, fn, budget):
        return S.ABORTED if self.abort else fn()

    def cost(self):
        return 0


@pytest.fixture(autouse=True)
def board():
    """The Waveshare P4's rows offered over the stand-in; nothing held after."""
    leases.stand_in(lease_sim, SPEC)
    lease_sim.reset()
    yield
    for pid in list(leases.LEASES.held):
        leases.LEASES.sweep(pid)
    leases.LEASES.grants = None
    N.reset()
    leases.stand_in(None, None)


@pytest.fixture
def sched():
    s = S.Scheduler(moybyte.WAKES, Guard())
    yield s
    s.reset()
    moybyte.WAKES.ended = S.SCHED._ended
    moybyte.WAKES.opener = S.SCHED._open
    moybyte.WAKES.goal = S.SCHED._goal


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


# -- the table -------------------------------------------------------------------


def test_the_board_offers_its_lease_rows_and_a_unit_rides_the_gpio_row():
    names = [o[0] for o in N.offers()]
    assert names == ["gpio", "uart1", "spi2"]
    uart = leases.LEASES.offer("uart1")
    assert uart[1:4] == (N.UART, 1, (47, 48))
    gpio = leases.LEASES.offer("gpio")[4]
    assert set(uart[4]) <= set(gpio) and set(leases.LEASES.offer("spi2")[4]) <= set(gpio)
    assert board_config.lease_spec(str(ROOT / "firmware/lilygo_t_deck_plus_mainline")) is None


@pytest.mark.parametrize("spec", ["gpio", "gpio:", "gpio:1,1", "uart1:4", "uart:4,5",
                                  "spi2:1,2", "i2c0:1,2", "gpio:99", "gpio:1/gpio:2"])
def test_a_malformed_spec_offers_nothing(spec):
    assert _errno(lambda: N.offer(spec)) == errno.EINVAL
    assert N.offers() == []


def test_a_pin_and_a_unit_are_held_once():
    h = N.take(7, N.UART, 1, (47, 48))
    assert N.get(h)[:5] == (7, N.UART, 1, 0, (47, 48))
    assert _errno(lambda: N.take(8, N.GPIO, 0, (48,))) == errno.EBUSY
    assert _errno(lambda: N.take(8, N.UART, 1, (4, 5))) == errno.EBUSY
    assert _errno(lambda: N.take(8, N.UART, 2, (4, 5))) == errno.EINVAL      # not offered
    assert _errno(lambda: N.take(8, N.GPIO, 0, (34,))) == errno.EINVAL       # not in the row
    assert _errno(lambda: N.offer(SPEC)) == errno.EBUSY                      # offers stand while held
    g = N.take(8, N.GPIO, 0, (4, 5))
    assert N.of_pin(5) == g and N.of_pin(6) == 0
    N.drop(h)
    assert not N.live(h) and _errno(lambda: N.drop(h)) == errno.EBADF
    h2 = N.take(9, N.UART, 1, (20, 21))                                       # other pins of the row
    assert h2 != h and N.leases() == sorted([g, h2], key=lambda x: x & 0xFF)


def test_a_drop_leaves_every_pin_safe():
    h = N.take(7, N.SPI, 2, (49, 50, 51))
    lease_sim.SPI(1, sck=49, mosi=50, miso=51)
    assert N.pin_state(49) == N.PIN_IN | N.PIN_OUT | N.PIN_PERIPH
    N.drop(h)
    assert [N.pin_state(p) for p in (49, 50, 51)] == [N.PIN_SAFE] * 3


# -- the sweep -----------------------------------------------------------------------

HOLDER = """\
import moybyte
p = moybyte.lease("gpio4", mode=1 | 2, value=1)
u = moybyte.lease("uart1", baudrate=9600)
moybyte.every(5, lambda: None)
"""


def _holding(pid):
    assert {o.lease_name for o in leases.LEASES.held[pid]} == {"gpio4", "uart1"}
    assert N.pin_state(4) & N.PIN_OUT and N.pin_state(47) & N.PIN_PERIPH
    assert N.held(N.VM) == 2


def _swept(pid, kept):
    assert N.held() == 0 and pid not in leases.LEASES.held
    assert [N.pin_state(p) for p in (4, 47, 48)] == [N.PIN_SAFE] * 3
    assert _errno(lambda: kept.on()) == errno.EBADF
    assert _errno(lambda: kept.value()) == errno.EBADF


def _service(src, cid="t.lease"):
    return {"id": cid, "title": cid, "type": "service", "src": src, "runtime": "python",
            "permissions": ["lease:gpio", "lease:uart1"], "required": [], "path": "/c.moy"}


def test_an_exit_sweeps_the_leases_of_its_process(sched):
    pid = sched.start(_service(HOLDER))
    _holding(pid)
    kept = sched.procs[pid].ns["p"]
    sched.stop(pid)
    _swept(pid, kept)


def test_a_crash_sweeps_and_reads_the_leased_pin_back_safe(sched):
    pid = sched.start(_service(HOLDER + "def boom():\n    p.on()\n    raise ValueError('x')\n"
                                         "moybyte.every(1, boom)\n"))
    kept = sched.procs[pid].ns["p"]
    time.sleep(0.003)
    sched.tick(0)
    assert sched.procs[pid].code == S.EXIT_CRASHED
    _swept(pid, kept)


def test_a_kill_sweeps(sched):
    pid = sched.start(_service(HOLDER + "def moy_stop():\n    while True: pass\n"))
    kept = sched.procs[pid].ns["p"]
    sched.guard.abort = True
    sched.stop(pid)
    assert sched.procs[pid].code == S.EXIT_KILLED
    _swept(pid, kept)


def test_a_run_ending_sweeps_through_moybyte_end():
    pid = L.spawn("t.run")
    u = _as(pid, lambda: moybyte.lease("uart1"))
    moybyte.end(pid)
    assert _errno(lambda: u.write(b"x")) == errno.EBADF and N.held() == 0


# -- machine objects ------------------------------------------------------------------


def test_a_uart_lease_is_machine_uart_on_the_rows_pins():
    pid = L.spawn("t.uart")
    u = _as(pid, lambda: moybyte.lease("uart1", baudrate=9600))
    lease_sim.loopback(1, True)
    assert u.write(b"ping\n") == 5
    assert u.any() == 5 and u.readline() == b"ping\n" and u.read() is None
    assert N.get(u._h)[4] == (47, 48)
    leases.LEASES.sweep(pid)


def test_an_irq_wakes_its_process_on_each_edge_the_trigger_names():
    was = moybyte.WAKES.table
    moybyte.bind(mi.kernel())
    try:
        pid = L.spawn("t.irq")
        p = _as(pid, lambda: moybyte.lease("gpio5", mode=leases.Pin.IN))
        seen = []
        _as(pid, lambda: p.irq(lambda pin: seen.append(pin.value()), leases.Pin.IRQ_RISING))
        assert N.get(p._h)[5] and moybyte.WAKES.count(pid) == 1
        for v in (1, 0, 1, 0):
            N.sim_level(5, v)
        out = [0] * 8
        n = moybyte.WAKES.due(0, out)
        for wid in out[:n]:
            _as(pid, lambda: moybyte.WAKES.call(wid))
        assert len(seen) == 2
        moybyte.end(pid)
        assert moybyte.WAKES.count(pid) == 0 and N.held() == 0
    finally:
        moybyte.WAKES.table = None
        if was is not None:
            moybyte.bind(was)


def test_pins_lists_the_offer_and_who_holds_what():
    pid = L.spawn("t.pins")
    _as(pid, lambda: moybyte.lease("spi2"))
    rows = {r[0]: r for r in leases.LEASES.rows()}
    assert rows["spi2"][1:] == ((49, 50, 51), pid, "t.pins", "lease")
    assert rows["gpio50"][2:] == (pid, "t.pins", "lease")
    assert rows["uart1"][2] == 0 and rows["gpio4"][2] == 0
    text = moybyte.pins()
    assert "spi2   49,50,51  t.pins/%d lease" % pid in text.splitlines()


def test_the_pins_verbs_take_pins_for_the_caller_and_refuse_anothers():
    lp = leases.local_pins()
    a, b = L.spawn("t.a"), L.spawn("t.b")
    assert _as(a, lambda: lp.write(4, 1)) is True
    assert N.sim_get(4) == 1 and N.get(N.of_pin(4))[3] == N.PINS | N.VM
    assert _as(b, lambda: lp.write(4, 0)) is False and _as(b, lambda: lp.read(4)) is None
    assert _as(a, lambda: lp.read(4)) == 1
    assert _as(b, lambda: lp.read(6)) == 0 and not N.pin_state(6) & N.PIN_OUT
    assert _as(a, lambda: lp.write(34, 1)) is False          # not in the gpio row
    moybyte.end(a)
    assert N.pin_state(4) == N.PIN_SAFE
    leases.stand_in(None, None)
    assert leases.local_pins() is None                        # a host offers nothing


# -- permissions --------------------------------------------------------------------------


def test_a_lease_needs_its_row_granted():
    pid = L.spawn("t.perm")
    leases.LEASES.grants = lambda p: ("lease:gpio",) if p == pid else None
    assert _errno(lambda: _as(pid, lambda: moybyte.lease("uart1"))) == errno.EPERM
    _as(pid, lambda: moybyte.lease("gpio6"))
    _as(1, lambda: moybyte.lease("uart1"))                    # the shell is not gated


def test_a_lease_row_prompts_full_access_and_is_absent_where_not_offered():
    assert P.row("lease:uart1") is P.row("lease:gpio") is not None
    assert P.row("lease") is None
    assert P.line("lease:uart1") == "Full access to the console's uart1"
    cart = {"id": "t.drv", "runtime": "python", "permissions": ["lease:spi2", "pins"],
            "required": ["lease:spi2"]}
    assert P.prompt(cart)[0] == "Full access to the console's spi2"
    have = ("gpio",) + P.leasable()
    assert "lease:spi2" in have and "lease:uart1" in have
    assert P.check(cart, None, have) == (["lease:spi2", "pins"], [], None)
    granted, absent, refusal = P.check(cart, None, ("gpio",))
    assert absent == ["lease:spi2"] and 'needs "lease:spi2"' in refusal
    lua = dict(cart, runtime="lua", required=[])
    assert P.check(lua, None, have)[1] == ["lease:spi2"]   # no Lua row serves a lease


# -- /dev and /proc -------------------------------------------------------------------


def test_cat_dev_uart1_reads_what_its_peer_sent():
    lease_sim.feed(1, b"$GPGGA,1\r\n")
    assert devfs.cat("/dev/uart1") == "$GPGGA,1\r\n"
    assert N.held() == 0                                      # the file's lease ended with it
    assert "uart1" in devfs.listdir("/dev") and "gpio52" in devfs.listdir("/dev")
    assert "spi2" not in devfs.listdir("/dev")


def test_a_device_another_process_holds_is_busy_and_the_holder_reads_its_own():
    pid = L.spawn("t.dev")
    u = _as(pid, lambda: moybyte.lease("uart1"))
    assert _errno(lambda: devfs.open("/dev/uart1")) == errno.EBUSY
    lease_sim.feed(1, b"hi")
    with _as(pid, lambda: devfs.open("/dev/uart1", "r+")) as f:
        assert f.read() == b"hi"
        f.write(b"yo")
    assert N.live(u._h) and lease_sim.sent(1) == b"yo"


def test_a_pins_file_reads_its_level_and_a_write_drives_it():
    with devfs.open("/dev/gpio4", "w") as f:
        f.write(b"1\n")
        assert N.sim_get(4) == 1
    assert N.pin_state(4) == N.PIN_SAFE
    N.sim_level(5, 1)
    assert devfs.cat("/dev/gpio5") == "1\n"


def test_cat_proc_1_status():
    assert devfs.cat("/proc/1/status") == (
        "Name:\tshell\nPid:\t1\nKind:\tshell\nState:\trunning\nLeases:\t-\n")
    try:
        _proc_status(S.SCHED.start(_service(HOLDER, cid="t.st")))
    finally:
        S.SCHED.reset()


def _proc_status(pid):
    text = devfs.cat("/proc/%d/status" % pid)
    assert "Name:\tt.st\n" in text and "State:\tbackground\n" in text
    assert "Leases:\tgpio4 uart1\n" in text
    assert str(pid) in devfs.listdir("/proc") and devfs.listdir("/proc/1") == ["log", "status"]
    assert "start t.st" in devfs.cat("/proc/%d/log" % pid)
    assert _errno(lambda: devfs.open("/proc/99999/status")) == errno.ENOENT


# -- on the VM ---------------------------------------------------------------------------

SSD1306 = (ROOT / ".build" / "unix_micropython" / "micropython" / "lib" / "micropython-lib"
           / "micropython" / "drivers" / "display" / "ssd1306")

VM_DRIVER = """
import sys
sys.path.insert(0, @TREE@)
sys.path.append(@HOST@)
sys.path.append(@DRIVER@)
import moy_lease, moy_log, moybyte, leases, lease_sim
leases.stand_in(lease_sim, @SPEC@)
pid = moy_log.spawn("oled")
moy_log.current(pid)
import ssd1306
spi = moybyte.lease("spi2", baudrate=1000000)
dc = moybyte.lease("gpio4")
res = moybyte.lease("gpio5")
cs = moybyte.lease("gpio6")
oled = ssd1306.SSD1306_SPI(128, 64, spi, dc, res, cs)
oled.fill(0)
oled.text("moybyte", 0, 0, 1)
oled.show()
log = lease_sim.SPI_LOG[1]
print("WROTE", len(log), sum(len(b) for b in log), moy_lease.sim_get(6))
moy_log.current(1)
moybyte.end(pid)
print("SAFE", [moy_lease.pin_state(p) for p in (4, 5, 6, 49, 50, 51)])
try:
    oled.show()
    print("SHOWED")
except OSError as e:
    print("RAISED", e.errno)
"""


def test_a_micropython_lib_driver_runs_unchanged_on_a_leased_bus(tmp_path):
    exe = require_unix_mp("moy_lease", why="a driver on the VM a board runs")
    assert (SSD1306 / "ssd1306.py").exists(), SSD1306
    script = tmp_path / "driver.py"
    script.write_text(VM_DRIVER.replace("@TREE@", repr(runtime_tree()))
                      .replace("@HOST@", repr(str(ROOT / "runtime" / "host")))
                      .replace("@DRIVER@", repr(str(SSD1306))).replace("@SPEC@", repr(SPEC)))
    done = subprocess.run([str(exe), str(script)], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stdout + done.stderr
    out = done.stdout.splitlines()
    wrote = out[0].split()
    assert wrote[0] == "WROTE" and int(wrote[2]) >= 1024 and wrote[3] == "1"   # the frame, cs up
    assert out[1] == "SAFE [1, 1, 1, 1, 1, 1]", out
    assert out[2] == "RAISED 9", out



# -- the radio ---------------------------------------------------------------------

def test_the_radios_tags_are_the_closed_set():
    assert N.WIFI_TAGS == ("web", "update", "settings", "cart", "link", "carts", "dev",
                           "http", "media", "ssh", "native", "net")
    w = N.Wifi()
    for i, tag in enumerate(N.WIFI_TAGS):
        assert w.hold(tag) == (1 << (i + 1)) - 1 and w.held(tag)
    assert w.holders() == list(N.WIFI_TAGS)


def test_the_radio_is_one_row_of_the_shells_holding_a_mask_of_tags():
    w = N.Wifi()
    assert w.mask() == 0 and not N.leases()
    assert w.hold("update") == w.hold("update") != 0
    w.hold("web")
    rows = [N.get(h) for h in N.leases()]
    assert [(r[0], r[1], r[4]) for r in rows] == [(1, N.WIFI, ())]
    assert N.device_name(N.WIFI) == "wifi"
    assert w.holders() == ["web", "update"] and w.held("web")
    assert N.held(N.VM) == 0                      # a VM stop is not refused by it
    assert w.release("update") != 0
    assert w.release("link") == w.mask()          # never held: not an error
    assert w.release("web") == 0 and w.holders() == [] and not N.leases()
    with pytest.raises(ValueError):
        w.hold("wasm")
    with pytest.raises(ValueError):
        w.release("nobody")
    with pytest.raises(TypeError):
        w.held(None)


def test_a_process_holds_the_radio_in_its_own_row_and_its_end_lets_exactly_it_go():
    """The shell's tags are one row; each process that holds the radio owns
    a row of its own, so the listing names it and its sweep takes its holds
    and no one else's. Owner 0 releases a tag from every process."""
    w = N.Wifi()
    w.hold("settings")
    w.hold("http", 7)
    w.hold("cart", 7)
    assert w.hold("http", 9) == w.hold("http", 9) == w.mask()
    assert sorted((r[0], r[1]) for r in (N.get(h) for h in N.leases())) == [
        (N.SHELL, N.WIFI), (7, N.WIFI), (9, N.WIFI)]
    assert w.holders() == ["settings", "cart:7", "http:7", "http:9"]
    assert N.sweep(7) == 1
    assert w.holders() == ["settings", "http:9"] and w.held("http") and not w.held("cart")
    w.hold("cart", 11)
    w.release("http")                             # the shell holds no http: 9's stays
    assert sorted(w.holders()) == ["cart:11", "http:9", "settings"]
    assert w.release("cart", 0) == w.mask() and w.holders() == ["settings", "http:9"]
    w.release("http", 9)
    assert w.release("settings") == 0 and not N.leases()
    with pytest.raises(OSError):
        w.hold("cart", 0)                         # a hold names its holder


def test_the_radios_row_takes_no_pin_and_holds_no_offer():
    N.Wifi().hold("ssh")
    h = N.take(2, N.GPIO, 0, (4,))
    assert N.of_pin(0) == 0 and N.of_pin(4) == h
    N.drop(h)
    N.offer(SPEC)                                 # only a pin or unit row holds the offers
    assert N.Wifi().holders() == ["ssh"]
    N.wifi_reset()
    assert N.Wifi().mask() == 0 and not N.leases()


def test_a_full_table_refuses_the_radio_and_the_console_reads_it_offline():
    pins = [p for name, cls, unit, named, every in N.offers() if cls == N.GPIO
            for p in every]
    taken = [N.take(2, N.GPIO, 0, (p,)) for p in pins[:N.ROWS_MAX]]
    assert len(taken) == N.ROWS_MAX
    assert _errno(lambda: N.Wifi().hold("web")) == errno.ENOSPC
    from runtime.console_spine import SpineVerbs

    class Radio:
        def radio_on(self):
            return True

    ws = SpineVerbs.__new__(SpineVerbs)
    ws.wifi, ws.leases = Radio(), N.Wifi()
    assert ws.wifi_hold("web") is False
    N.drop(taken[0])
    assert ws.wifi_hold("web") is True and ws.leases.holders() == ["web"]


def test_a_random_walk_of_the_radio_agrees_with_its_mask():
    """Holds and releases of every tag and of tags outside the table, in a
    seeded order: the row answers as a plain mask does, and is in the table
    exactly while the mask is not 0."""
    rnd = random.Random(1)
    w, mask = N.Wifi(), 0
    tags = list(N.WIFI_TAGS) + ["", "wasm", "we", "Dev", "carts "]
    for _ in range(4000):
        tag, hold = rnd.choice(tags), rnd.random() < 0.5
        if tag not in N.WIFI_TAGS:
            with pytest.raises(ValueError):
                (w.hold if hold else w.release)(tag)
            continue
        bit = 1 << N.WIFI_TAGS.index(tag)
        mask = mask | bit if hold else mask & ~bit
        assert (w.hold if hold else w.release)(tag) == mask == w.mask()
        assert len(N.leases()) == (1 if mask else 0)


VM_RADIO = """
import moy_lease as N
w = N.Wifi()
print("hold", w.hold("update"), w.hold("web"), w.hold("update"), ",".join(w.holders()))
print("held", w.held("web"), w.held("cart"), w.mask(), len(N.leases()))
print("release", w.release("link"), w.release("update"), w.release("web"), w.holders(),
      len(N.leases()))
w.hold("ssh")
print("procs", w.hold("http", 7), w.hold("cart", 9), w.holders(), N.sweep(7), w.holders(),
      w.release("cart", 0), w.release("ssh"), len(N.leases()))
for fn, arg in ((w.hold, "wasm"), (w.release, None), (w.held, "nobody")):
    try:
        fn(arg)
        print("taken")
    except ValueError:
        print("VALUE")
    except TypeError:
        print("TYPE")
"""


def test_the_radio_answers_alike_on_the_vm_a_board_runs(tmp_path):
    exe = require_unix_mp("moy_lease", why="the radio's row on the VM a board runs")
    script = tmp_path / "radio.py"
    script.write_text(VM_RADIO)
    done = subprocess.run([str(exe), str(script)], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stdout + done.stderr
    assert done.stdout.splitlines() == [
        "hold 2 3 3 web,update", "held True False 3 1", "release 3 1 0 [] 0",
        "procs 640 648 ['ssh', 'http:7', 'cart:9'] 1 ['ssh', 'cart:9'] 512 0 0",
        "VALUE", "TYPE", "VALUE"]
