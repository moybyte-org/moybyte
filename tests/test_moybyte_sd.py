"""`device/moybyte_sd.py`, EXECUTED (#208, the single-consumer list).

This module holds the hazard that hard-hangs a T-Deck: SD and the panel share
one SPI host, so tearing the sdspi device down between ops -- or reconfiguring
the panel's CS -- corrupts the bus and DMA state `esp_lcd` needs, and the NEXT
panel flush hangs the board. No panic, no message: gray screen, dead USB. So
there is ONE lifecycle: `with_sd_live` / `mount_sd_live` ATTACH the card to the
host `esp_lcd` already owns, park only the unused LoRa CS, mount ONCE and never
tear down.

`moybyte_sd` has no top-level imports at all -- `machine`, `os`, `vfs` and
`moy_sd` are all pulled in lazily inside the functions -- so the real file runs
against the doubles below with nothing transcribed.

Not reachable from a host, and named here rather than left as silence: the
hazard ITSELF. Nothing off-glass can show that a second `sdspi_host_init_device`
wedges a live panel; what is testable is the discipline the module keeps in
order to never issue one, and that is what is pinned.
"""

import importlib.util
import os as _real_os
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SD_SRC = ROOT / "device" / "moybyte_sd.py"

TFT_CS = 12
SD_CS = 39
RADIO_CS = 9


# -- the doubles ---------------------------------------------------------------


class _Dev:
    """An SD card or an SPI bus, with an identity and a deinit counter."""

    def __init__(self, kind, log, **kw):
        self.kind = kind
        self.kw = kw
        self.deinits = 0
        self._log = log

    def deinit(self):
        self.deinits += 1
        self._log.append(("deinit", self.kind))


def _machine(log, pin_error=None, sd_error=None):
    m = types.ModuleType("machine")

    class Pin:
        OUT = "OUT"
        IN = "IN"
        PULL_UP = "PULL_UP"

        def __init__(self, gpio, mode=None, value=None):
            if pin_error is not None:
                raise pin_error
            log.append(("Pin", gpio, value))

    class SDCard(_Dev):
        def __init__(self, **kw):
            if sd_error is not None:
                raise sd_error
            _Dev.__init__(self, "sdcard", log, **kw)
            log.append(("SDCard", kw))

    class SPI:
        @staticmethod
        def Bus(**kw):
            log.append(("SPI.Bus", kw))
            return _Dev("spibus", log, **kw)

    m.Pin = Pin
    m.SDCard = SDCard
    m.SPI = SPI
    return m


def _os(log, mounted=False, statvfs=True, mkdir_error=None, umount_error=None):
    """`os`, with the two verbs this module reaches for.

    Unknown attributes fall through to the real `os` so that anything else in
    the process which imports `os` while the fake is installed still works;
    `statvfs` is the one name a variant deliberately HIDES, because the
    AttributeError arm of `_looks_mounted` is a real MicroPython build.
    """
    m = types.ModuleType("os")

    def mkdir(path):
        log.append(("mkdir", path))
        if mkdir_error is not None:
            raise mkdir_error

    def umount(path):
        log.append(("os.umount", path))
        if umount_error is not None:
            raise umount_error

    def mount(bd, path):
        log.append(("os.mount", path))

    m.mkdir = mkdir
    m.umount = umount
    m.mount = mount
    if statvfs:
        def _statvfs(path):
            log.append(("statvfs", path))
            if path == "/":
                return (4096,) * 10
            if mounted:
                return (512,) * 10
            raise OSError(2)
        m.statvfs = _statvfs

    hidden = () if statvfs else ("statvfs",)

    def __getattr__(name):
        if name in hidden:
            raise AttributeError(name)
        return getattr(_real_os, name)

    m.__getattr__ = __getattr__
    return m


def _vfs(log, mount_error=None, umount_error=None, has_umount=True):
    m = types.ModuleType("vfs")

    def mount(bd, path):
        log.append(("vfs.mount", path, bd))
        if mount_error is not None:
            raise mount_error

    def umount(path):
        log.append(("vfs.umount", path))
        if umount_error is not None:
            raise umount_error

    m.mount = mount
    if has_umount:
        m.umount = umount
    return m


def _moy_sd(log, sectors=15_523_840, sector_size=512, init_error=None):
    m = types.ModuleType("moy_sd")
    m.SECTOR_SIZE = sector_size

    def init(host, cs, khz):
        log.append(("moy_sd.init", host, cs, khz))
        if init_error is not None:
            raise init_error
        return sectors

    m.init = init
    m.read = lambda block, buf, n: log.append(("moy_sd.read", block, len(buf), n))
    m.write = lambda block, buf, n: log.append(("moy_sd.write", block, len(buf), n))
    m.deinit = lambda: log.append(("moy_sd.deinit",))
    return m


class World:
    """A fresh `moybyte_sd` plus a fresh fake hardware world.

    The module is re-loaded per test because its residency flag `_live_mounted`
    is MODULE state -- the very thing the live path is built around -- so a
    leaked one would make the second test in a file silently exercise nothing.
    """

    NAMES = ("machine", "os", "vfs", "moy_sd")

    def __init__(self):
        self.log = []
        self._saved = {n: sys.modules.get(n, KeyError) for n in self.NAMES}
        spec = importlib.util.spec_from_file_location("moybyte_sd_test", SD_SRC)
        self.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.mod)
        self.machine()
        self.os()
        self.vfs()
        self.moy_sd()

    def machine(self, **kw):
        sys.modules["machine"] = _machine(self.log, **kw)

    def os(self, **kw):
        sys.modules["os"] = _os(self.log, **kw)

    def vfs(self, **kw):
        sys.modules["vfs"] = _vfs(self.log, **kw)

    def no_vfs(self):
        sys.modules["vfs"] = None      # PEP 328: a None entry raises ImportError

    def moy_sd(self, **kw):
        sys.modules["moy_sd"] = _moy_sd(self.log, **kw)

    def pins(self):
        return [c[1] for c in self.log if c[0] == "Pin"]

    def kinds(self):
        return [c[0] for c in self.log]

    def restore(self):
        for name, prev in self._saved.items():
            if prev is KeyError:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = prev


@pytest.fixture
def w():
    world = World()
    try:
        yield world
    finally:
        world.restore()


# -- the live path: what it must NOT touch -------------------------------------


def test_the_live_session_parks_the_radio_CS_and_nothing_else(w):
    """The documented rule, and the one a grep cannot check: `TFT_CS` and
    `SD_CS` are driver-owned once the panel is up, and reconfiguring either
    through `Pin(...)` corrupts the shared bus. Only the unused LoRa CS is
    parked. The same file DOES drive all three -- in the pre-display path."""
    w.mod.with_sd_live(lambda: None)
    assert w.pins() == [RADIO_CS]
    assert TFT_CS not in w.pins() and SD_CS not in w.pins()


def test_the_live_mount_attaches_and_never_re_initialises_the_bus(w):
    """`machine.SDCard` re-runs `spi_bus_initialize()` on the host `esp_lcd`
    already owns; `moy_sd.init` attaches to it. Constructing either a bus or an
    SDCard here is the hang."""
    w.mod.with_sd_live(lambda: None)
    assert ("moy_sd.init", 1, SD_CS, 20000) in w.log
    assert "SDCard" not in w.kinds()
    assert "SPI.Bus" not in w.kinds()


def test_the_card_is_never_torn_down_after_a_live_session(w):
    """The residency doctrine: a per-op `sdspi_host_deinit` lands its write and
    then hangs the next panel flush. So no deinit, no unmount, no deselect."""
    w.mod.with_sd_live(lambda: None)
    w.mod.with_sd_live(lambda: None)
    after = w.kinds()
    assert "deinit" not in after
    assert "moy_sd.deinit" not in after
    assert "vfs.umount" not in after and "os.umount" not in after


def test_the_card_is_mounted_once_across_many_sessions(w):
    ran = []
    for _ in range(4):
        w.mod.with_sd_live(lambda: ran.append(1))
    assert len(ran) == 4
    assert w.kinds().count("moy_sd.init") == 1
    assert w.kinds().count("vfs.mount") == 1


def test_a_resident_session_costs_no_probe_at_all(w):
    """Once resident, a session is fn() and nothing else -- no statvfs, no
    mkdir, no Pin. The T-Deck takes this path per editor commit."""
    w.mod.with_sd_live(lambda: None)
    w.log.clear()
    w.mod.with_sd_live(lambda: None)
    assert w.log == []


def test_the_live_session_returns_what_fn_returned(w):
    assert w.mod.with_sd_live(lambda: "carts") == "carts"


def test_a_failing_op_leaves_the_card_resident(w):
    """No try/finally, deliberately: a raising fn must not trigger a teardown,
    because the teardown is the hazard. The exception is the caller's."""
    w.mod.with_sd_live(lambda: None)
    w.log.clear()
    with pytest.raises(ValueError):
        w.mod.with_sd_live(lambda: (_ for _ in ()).throw(ValueError("boom")))
    assert w.mod._live_mounted is True
    assert "moy_sd.init" not in w.kinds()


def test_a_card_someone_else_already_mounted_is_not_mounted_twice(w):
    w.os(mounted=True)
    w.mod.with_sd_live(lambda: None)
    assert "moy_sd.init" not in w.kinds()
    assert w.mod._live_mounted is True


def test_the_radio_CS_is_parked_before_the_card_is_attached(w):
    """A LoRa module still selected on the shared bus corrupts the first sdspi
    transaction, so the park cannot follow the mount."""
    w.mod.with_sd_live(lambda: None)
    assert w.kinds().index("Pin") < w.kinds().index("moy_sd.init")


def test_a_board_with_no_radio_pin_still_mounts(w):
    """The park is best-effort; losing it must not cost the card."""
    w.machine(pin_error=RuntimeError("no such pin"))
    w.mod.with_sd_live(lambda: None)
    assert "moy_sd.init" in w.kinds()


def test_an_existing_mount_directory_is_not_an_error(w):
    w.os(mkdir_error=OSError(17, "exists"))
    assert w.mod.with_sd_live(lambda: "ok") == "ok"


def test_a_failed_attach_does_not_leave_the_session_marked_resident(w):
    """A latched flag over a card that never mounted would make every later
    session a silent no-op against an unmounted /sd."""
    w.moy_sd(init_error=OSError(19, "no card"))
    with pytest.raises(OSError):
        w.mod.with_sd_live(lambda: None)
    assert w.mod._live_mounted is False


# -- mount_sd_live ------------------------------------------------------------


def test_the_live_mount_uses_the_display_host_and_the_live_frequency(w):
    """Host 1 is the PANEL's host -- attaching there is the whole mechanism --
    and 20MHz is the attached device's rate."""
    w.mod.mount_sd_live()
    assert ("moy_sd.init", w.mod.SPI_HOST, w.mod.SD_CS, 20000) in w.log
    assert w.mod.SPI_HOST == 1
    assert w.mod.SD_LIVE_FREQ_KHZ == 20000


def test_the_live_mount_publishes_the_card_at_slash_sd(w):
    bd = w.mod.mount_sd_live()
    assert ("vfs.mount", "/sd", bd) in w.log
    assert bd.sectors == 15_523_840


# -- the block device ---------------------------------------------------------


def test_readblocks_asks_for_as_many_sectors_as_the_buffer_holds(w):
    bd = w.mod._NativeSDBlockDev(64)
    assert bd.readblocks(7, bytearray(2048)) == 0
    assert ("moy_sd.read", 7, 2048, 4) in w.log


def test_writeblocks_asks_for_as_many_sectors_as_the_buffer_holds(w):
    bd = w.mod._NativeSDBlockDev(64)
    assert bd.writeblocks(9, bytearray(1536)) == 0
    assert ("moy_sd.write", 9, 1536, 3) in w.log


def test_byte_offset_addressing_is_refused_without_touching_the_card(w):
    """FAT addresses whole 512-blocks here. Passing a byte offset through as if
    it were a block index would write the wrong sectors, so it must raise --
    and must not have issued the transfer first."""
    bd = w.mod._NativeSDBlockDev(64)
    for verb in (bd.readblocks, bd.writeblocks):
        with pytest.raises(OSError) as e:
            verb(0, bytearray(512), 16)
        assert e.value.args[0] == 22
    assert w.log == []


def test_the_ioctl_answers_the_two_questions_fat_asks(w):
    bd = w.mod._NativeSDBlockDev(4096)
    assert bd.ioctl(4, 0) == 4096          # BLOCK_COUNT
    assert bd.ioctl(5, 0) == 512           # BLOCK_SIZE
    for op in (1, 2, 3, 6):                # INIT / DEINIT / SYNC / BLOCK_ERASE
        assert bd.ioctl(op, 0) == 0


# -- the helpers ---------------------------------------------------------------


def test_an_unmountable_card_reads_as_not_mounted(w):
    w.os()
    assert w.mod._looks_mounted(sys.modules["os"]) is False


def test_a_mount_point_that_is_still_the_root_filesystem_is_not_a_card(w):
    """`os.mkdir("/sd")` succeeds on the internal VFS, so the directory
    EXISTING proves nothing -- the two statvfs must differ."""
    m = types.ModuleType("os")
    m.statvfs = lambda path: (4096,) * 10
    assert w.mod._looks_mounted(m) is False


def test_a_build_without_statvfs_reads_as_not_mounted(w):
    """The question cannot be answered there, and the live path's residency
    latch is what keeps a session to one attach anyway."""
    w.os(statvfs=False)
    assert w.mod._looks_mounted(sys.modules["os"]) is False


def test_mount_prefers_vfs_and_falls_back_to_os(w):
    bd = object()
    w.mod._mount(bd, "/sd")
    assert ("vfs.mount", "/sd", bd) in w.log

    w.no_vfs()
    w.log.clear()
    w.mod._mount(bd, "/sd")
    assert w.kinds() == ["os.mount"]

    w.vfs(has_umount=True)
    sys.modules["vfs"] = types.ModuleType("vfs")          # present, no mount
    w.log.clear()
    w.mod._mount(bd, "/sd")
    assert w.kinds() == ["os.mount"]


# -- the lifecycle that is gone ------------------------------------------------


def test_the_executed_body_is_the_shipped_file(w):
    assert w.mod.__file__ == str(SD_SRC)


def test_no_second_sd_lifecycle_grows_back(w):
    """`machine.SDCard` re-runs `spi_bus_initialize()` on the host `esp_lcd`
    owns; `machine.SPI.Bus` is a fork API mainline MicroPython does not have;
    `tdeck_display` died with the fork and `tests/test_staging_closure.py`
    forbids staging it. A pre-display path built on those three sat here
    unreachable, and its `with_sd` was what `moybyte_diag.dump_previous_to_serial`
    read through -- so the boot dump answered "(no previous diag log)" whatever
    was on the card."""
    src = SD_SRC.read_text(encoding="utf-8")
    for dead in ("SDCard(", "SPI.Bus(", "tdeck_display", "TFT_CS", "umount("):
        assert dead not in src, "%s is back in moybyte_sd" % dead
