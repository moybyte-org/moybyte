"""`device/moybyte_sd.py`, EXECUTED (#208, the single-consumer list).

This module holds the hazard that hard-hangs a T-Deck: SD and the panel share
one SPI host, so tearing the sdspi device down between ops -- or reconfiguring
the panel's CS -- corrupts the bus and DMA state `esp_lcd` needs, and the NEXT
panel flush hangs the board. No panic, no message: gray screen, dead USB. Which
is why there are TWO lifecycles in one file, and why they must not be confused:

  * the PRE-DISPLAY path (`mount_sd` / `with_sd` / `read_first_project_source`)
    may drive every shared CS line and tears the card down afterwards, because
    the panel is not up yet;
  * the LIVE path (`with_sd_live` / `mount_sd_live`) ATTACHES the card to the
    host `esp_lcd` already owns, parks only the unused LoRa CS, mounts ONCE and
    never tears down.

The other net over this file is the source greps in
`tests/test_micropython_spike.py`, which assert that `Pin(TFT_CS, Pin.OUT,
value=1)` and `SDCard(spi_bus=...)` still appear SOMEWHERE in it. That is
exactly the shape #208 exists to stop: a substring cannot tell which of the two
lifecycles a line belongs to, and confusing them is the hang. So this suite aims
at WHICH path touches WHICH pin, and at the residency the live path is built
around.

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

    def files(self, mapping):
        """Shadow the builtin `open` in the module's own globals, which is what
        `read_first_project_source` and `_has_project_file` call."""
        def _open(path, mode="r"):
            if path not in mapping:
                raise OSError(2, path)
            return _Handle(mapping[path])
        self.mod.open = _open

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


class _Handle:
    def __init__(self, text):
        self.text = text

    def read(self):
        return self.text

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


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
    """20MHz, not the 800kHz the pre-display path uses: those are two different
    devices on two different lifecycles, and the constants are how they differ."""
    w.mod.mount_sd_live()
    assert ("moy_sd.init", w.mod.SPI_HOST, w.mod.SD_CS, 20000) in w.log
    assert w.mod.SPI_HOST == 1
    assert w.mod.SD_LIVE_FREQ_KHZ != w.mod.SD_FREQ


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


# -- the pre-display path ------------------------------------------------------


def test_the_pre_display_mount_deselects_every_shared_CS(w):
    """The panel is not up yet here, so this path MAY drive TFT_CS -- and must,
    or the display device answers the card's first clocks."""
    assert w.mod.mount_sd() == "/sd"
    assert w.pins()[:3] == [RADIO_CS, TFT_CS, SD_CS]


def test_the_pre_display_mount_builds_its_own_bus_when_there_is_no_display(w):
    w.mod.mount_sd()
    bus = [c for c in w.log if c[0] == "SPI.Bus"][0][1]
    assert bus == {"host": 1, "mosi": 41, "miso": 38, "sck": 40}
    card = [c for c in w.log if c[0] == "SDCard"][0][1]
    assert card["cs"] == SD_CS and card["freq"] == 800000


def test_the_pre_display_mount_is_a_no_op_on_an_already_mounted_card(w):
    w.os(mounted=True)
    assert w.mod.mount_sd() == "/sd"
    assert "SDCard" not in w.kinds()


def test_with_sd_over_an_already_mounted_card_adds_no_second_device(w):
    """Two SDCard devices on one host is the collision this file exists to
    avoid; the session still runs, and still unmounts on the way out."""
    w.os(mounted=True)
    assert w.mod.with_sd(lambda: "ok") == "ok"
    assert "SDCard" not in w.kinds()
    assert "vfs.umount" in w.kinds()


def test_an_existing_mount_directory_is_not_an_error_on_either_path(w):
    w.os(mkdir_error=OSError(17, "exists"))
    assert w.mod.mount_sd() == "/sd"
    assert w.mod.with_sd(lambda: "ok") == "ok"


def test_a_mount_failure_over_an_already_mounted_card_is_forgiven(w):
    """Racing a mount that already happened is not a failure; anything else is."""
    mounted = [False]

    def _statvfs(path):
        if path == "/":
            return (4096,) * 10
        if mounted[0]:
            return (512,) * 10
        raise OSError(2)

    def _mount(bd, path):
        mounted[0] = True                      # someone else won the race
        raise OSError(1, "busy")

    sys.modules["os"].statvfs = _statvfs
    sys.modules["vfs"].mount = _mount
    assert w.mod.mount_sd() == "/sd"


def test_a_real_mount_failure_is_raised(w):
    w.vfs(mount_error=OSError(1, "busy"))
    with pytest.raises(OSError):
        w.mod.mount_sd()


def test_with_sd_always_tears_the_card_down_even_when_fn_raises(w):
    """The pre-display lifecycle is the OPPOSITE of the live one: leaving an
    SDCard device on the bus is what collides with the first `esp_lcd` flush."""
    with pytest.raises(ValueError):
        w.mod.with_sd(lambda: (_ for _ in ()).throw(ValueError("boom")))
    kinds = w.kinds()
    assert "vfs.umount" in kinds
    assert kinds.count("deinit") == 2                     # the card and the bus
    assert w.pins()[-3:] == [RADIO_CS, TFT_CS, SD_CS]     # deselected afterwards
    assert kinds.index("vfs.umount") < kinds.index("deinit")


def test_with_sd_returns_the_value_and_deinits_in_the_same_pass(w):
    assert w.mod.with_sd(lambda: 42) == 42
    assert w.kinds().count("deinit") == 2


def test_a_borrowed_display_bus_is_never_deinited(w):
    """`with_sd(fn, spi_bus=...)` is handed the DISPLAY's bus; deiniting it
    would take the panel down with the card."""
    bus = _Dev("borrowed", w.log)
    w.mod.with_sd(lambda: None, spi_bus=bus)
    assert bus.deinits == 0
    assert "SPI.Bus" not in w.kinds()
    assert w.kinds().count("deinit") == 1                 # the card only


def test_the_first_project_source_wins_in_declared_order(w):
    w.files({"/sd/project.py": "second", "/sd/moybyte/main.py": "first"})
    assert w.mod.read_first_project_source() == ("/sd/moybyte/main.py", "first")


def test_no_project_source_reads_as_None_and_still_tears_down(w):
    w.files({})
    assert w.mod.read_first_project_source() is None
    assert "vfs.umount" in w.kinds()
    assert w.kinds().count("deinit") == 2


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


def test_a_build_without_statvfs_falls_back_to_probing_for_a_project_file(w):
    w.os(statvfs=False)
    w.files({"/sd/main.py": "src"})
    assert w.mod._looks_mounted(sys.modules["os"]) is True
    w.files({})
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


def test_unmount_uses_one_mechanism_not_both(w):
    """A second umount against an already-unmounted path is not harmless here:
    it is the pre-display path's last act before the panel comes up."""
    w.mod._unmount_if_possible(sys.modules["os"])
    assert w.kinds() == ["vfs.umount"]


def test_unmount_falls_through_to_os_when_vfs_refuses(w):
    w.vfs(umount_error=OSError(22))
    w.mod._unmount_if_possible(sys.modules["os"])
    assert w.kinds() == ["vfs.umount", "os.umount"]


def test_unmount_swallows_a_failure_from_both(w):
    w.vfs(umount_error=OSError(22))
    w.os(umount_error=OSError(22))
    w.mod._unmount_if_possible(sys.modules["os"])          # must not raise


def test_deinit_is_safe_on_absent_deinitless_and_exploding_objects(w):
    """The `obj is None` guard is an EQUIVALENT mutant and stays untested on
    purpose: `getattr(None, "deinit", None)` is None, so the next line returns
    anyway. Everything else here is a real arm -- these run in a `finally`
    where a raise would replace the caller's error."""
    class NoDeinit:
        pass

    class Explodes:
        def deinit(self):
            raise RuntimeError("bus wedged")

    w.mod._deinit_if_possible(None)
    w.mod._deinit_if_possible(NoDeinit())
    w.mod._deinit_if_possible(Explodes())
    dev = _Dev("card", w.log)
    w.mod._deinit_if_possible(dev)
    assert dev.deinits == 1


def test_the_display_bus_lookup_degrades_when_there_is_no_display_module(w):
    """`tdeck_display` is the LVGL fork's panel module and no longer exists in
    the tree (the panel is `moy_lcd` + `tdeck_panel` now), so on every shipped
    image this lookup answers None and the pre-display path builds its own bus.
    That is a degrade, not a failure -- but it means the branch below is the
    only one a board can take."""
    assert w.mod._display_spi_bus() is None
    assert not (ROOT / "device" / "tdeck_display.py").exists()


def test_a_display_that_owns_the_bus_lends_it_rather_than_a_second_one(w):
    mod = types.ModuleType("tdeck_display")
    bus = _Dev("display", w.log)
    mod.get_spi_bus = lambda: bus
    sys.modules["tdeck_display"] = mod
    try:
        assert w.mod._display_spi_bus() is bus
        w.mod.with_sd(lambda: None)
        assert "SPI.Bus" not in w.kinds()
        assert bus.deinits == 0
    finally:
        del sys.modules["tdeck_display"]


def test_the_final_deselect_survives_a_board_with_no_machine_module(w):
    """It runs in a `finally`; raising there would replace the caller's real
    error with an import failure."""
    sys.modules["machine"] = None
    w.mod._deselect_after_sd()


# -- the greps and the executable lane must point at the same file -------------


def test_the_executed_body_is_the_file_the_greps_read(w):
    """`test_micropython_spike` asserts `Pin(TFT_CS, Pin.OUT, value=1)` appears
    in this file and cannot say which lifecycle owns it; the live-path test at
    the top of this suite runs the body and can. Both are only looking at the
    same file for as long as this holds."""
    assert w.mod.__file__ == str(SD_SRC)
    assert "Pin(TFT_CS, Pin.OUT, value=1)" in SD_SRC.read_text(encoding="utf-8")
