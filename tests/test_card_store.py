"""`device/card_store.py`, EXECUTED: a card as the cart store, with the internal
flash as the floor.

The P4 boards' TF slot has a bus of its own, so the whole mechanism is "mount a
card once at boot, or do not": what has to hold is that EVERY way a card can
fail -- no card, a card the SDMMC host will not initialise, a filesystem this
build cannot read, a mount that works over a store that cannot be seeded -- ends
on the internal store with one line saying which, and that a working card is
never second-guessed. The module has no top-level imports, so the real file
runs against the doubles below.
"""

import re
import sys
import types
from pathlib import Path

import pytest

from device import card_store
from runtime import device_boot

SD_ROOT = "/sd/moybyte/carts"
FLASH_ROOT = "/moy/carts"
OTA = "/moy/update"


class _Card:
    def __init__(self):
        self.deinits = 0

    def deinit(self):
        self.deinits += 1


@pytest.fixture
def vfs(monkeypatch):
    """A `vfs` module whose mount is scripted, and which records the mounts."""
    m = types.ModuleType("vfs")
    m.mounts = []
    m.error = None

    def mount(dev, path):
        if m.error is not None:
            raise m.error
        m.mounts.append((dev, path))

    m.mount = mount
    monkeypatch.setitem(sys.modules, "vfs", m)
    return m


@pytest.fixture(autouse=True)
def _status(monkeypatch):
    monkeypatch.setattr(card_store, "STATUS", "not attempted")


def test_a_mounted_card_is_true_and_says_so(vfs):
    card, lines = _Card(), []

    assert card_store.mount(lambda: card, say=lines.append) is True

    assert vfs.mounts == [(card, "/sd")]
    assert card_store.STATUS == "mounted"
    assert lines == ["SD: card mounted at /sd"]
    assert card.deinits == 0


def test_a_card_interface_that_will_not_construct_is_false_with_the_reason(vfs):
    lines = []

    def make():
        raise OSError(263, "ESP_ERR_TIMEOUT")

    assert card_store.mount(make, say=lines.append) is False

    assert vfs.mounts == []
    assert card_store.STATUS.startswith("no card interface: ")
    assert len(lines) == 1 and "internal flash" in lines[0]


def test_no_card_arrives_at_the_mount_as_ebusy(vfs):
    """A card object built with nothing in the slot (a host initialised, the
    card left to the first read) fails at the mount: FatFS's FR_NOT_READY,
    EBUSY."""
    vfs.error = OSError(16)
    card, lines = _Card(), []

    assert card_store.mount(lambda: card, say=lines.append) is False

    assert card.deinits == 1
    assert len(lines) == 1 and "no card answered" in lines[0]


@pytest.mark.parametrize("exc,why", [
    (OSError("moy_sd card_init failed: 263"), "no card answered"),
    (OSError(19), "no filesystem this build reads"),
    (OSError(5), "no card interface"),
])
def test_the_card_volume_fails_as_it_is_built_and_says_why(vfs, exc, why):
    """The store's card volume mounts its FATFS as `make_card` builds it, so an
    empty slot (moy_sd's card_init) and an unreadable filesystem arrive there,
    and the line names them as the mount's would."""
    def make():
        raise exc
    lines = []

    assert card_store.mount(make, say=lines.append) is False

    assert vfs.mounts == []
    assert len(lines) == 1 and why in lines[0] and "internal flash" in lines[0]


def test_a_filesystem_this_build_cannot_read_frees_the_host(vfs):
    """The mount failed with the card constructed: deinit() is what hands the
    SDMMC host back, and a failed mount that keeps it leaks it for the boot.
    ENODEV is FatFS's FR_NO_FILESYSTEM -- what an exFAT card said to a build
    with exFAT off."""
    vfs.error = OSError(19)
    card, lines = _Card(), []

    assert card_store.mount(lambda: card, say=lines.append) is False

    assert card.deinits == 1
    assert card_store.STATUS == "mount failed: OSError(19)"
    assert len(lines) == 1 and "no filesystem this build reads" in lines[0]


def test_a_card_that_cannot_deinit_still_falls_back(vfs):
    vfs.error = OSError(19)

    class _Stuck(_Card):
        def deinit(self):
            raise OSError("busy")

    assert card_store.mount(_Stuck, say=lambda _m: None) is False


def test_a_build_with_no_vfs_module_mounts_through_os(monkeypatch):
    monkeypatch.setitem(sys.modules, "vfs", None)       # `import vfs` raises
    seen = []
    os_mod = types.ModuleType("os")
    os_mod.mount = lambda dev, path: seen.append((dev, path))
    monkeypatch.setitem(sys.modules, "os", os_mod)
    card = _Card()

    assert card_store.mount(lambda: card, say=lambda _m: None) is True
    assert seen == [(card, "/sd")]


# -- the store loader ----------------------------------------------------------


class _Store:
    CARTS_DIR = "/unused"

    def __init__(self, dead=()):
        self.dead = set(dead)
        self.calls = []

    def ensure_dirs(self, root):
        self.calls.append(("ensure_dirs", root))
        if root in self.dead:
            raise OSError(28)

    def sweep_store(self, root, seed=None):
        self.calls.append(("sweep", root))

    def seed(self, seed, root, shelf, progress=None):
        self.calls.append(("seed", root))
        return shelf

    def catalogue(self, root):
        self.calls.append(("catalogue", root))
        return [{"title": "a"}]

    def embedded_floor(self, seed):
        return list(seed)


class _Canvas:
    w, h = 320, 240

    def __getattr__(self, name):
        return lambda *a, **k: None


class _Comp:
    def flush(self):
        pass


def _load(make_card, store, capsys):
    boot = device_boot.DeviceBoot(_Canvas(), _Comp(), None, "Board")
    loader = card_store.carts_loader(make_card, [{"title": "seed"}],
                                     SD_ROOT, FLASH_ROOT, OTA)
    out = loader(boot, store)
    return out, capsys.readouterr().out


def test_a_card_is_the_store_and_the_ota_dir_stays_internal(vfs, capsys):
    store = _Store()

    (carts, root, ota), out = _load(_Card, store, capsys)

    assert root == SD_ROOT and ota == OTA
    assert ("seed", SD_ROOT) in store.calls
    assert not any(FLASH_ROOT in str(c) for c in store.calls), \
        "a working card is never second-guessed: one store per boot"
    assert "Board loaded 1 carts from SD" in out


def test_no_card_boots_on_the_internal_store(vfs, capsys):
    def make():
        raise OSError("no card")

    store = _Store()
    (carts, root, ota), out = _load(make, store, capsys)

    assert root == FLASH_ROOT and ota == OTA
    assert ("seed", FLASH_ROOT) in store.calls
    assert not any(SD_ROOT in str(c) for c in store.calls)
    assert "Board loaded 1 carts from flash" in out
    assert out.count("SD: ") == 1, "one clear line"


def test_an_unreadable_card_boots_on_the_internal_store(vfs, capsys):
    vfs.error = OSError(19)

    (carts, root, ota), out = _load(_Card, _Store(), capsys)

    assert root == FLASH_ROOT
    assert "no filesystem this build reads" in out and "from flash" in out


def test_a_card_that_mounts_but_cannot_be_seeded_falls_back_too(vfs, capsys):
    """Mounted, but the store directory cannot be made (a read-only card, a full
    one): the console must still get a writable store, not the read-only floor."""
    store = _Store(dead={SD_ROOT})

    (carts, root, ota), out = _load(_Card, store, capsys)

    assert root == FLASH_ROOT
    assert "SD carts unavailable" in out and "from flash" in out


# -- the two P4 boards ---------------------------------------------------------

P4_BOARDS = [
    ("esp32_p4_wifi6_touch_lcd_7b", "MOYBYTE_P4"),
    ("guition_jc8012p4a1c", "MOYBYTE_GUITION_P4"),
]


@pytest.mark.parametrize("board,variant", P4_BOARDS)
def test_a_p4_board_builds_exfat_and_the_card_driver(board, variant):
    """A card over 32 GB ships exFAT, and FatFS in the esp32 port leaves it off:
    without the define a read-only `vfs.mount` of one fails with ENODEV and the
    board silently stays on its flash store, which is the symptom a kid sees as
    "my new card does nothing". `lib/oofatfs/ffconf.h` reads MICROPY_FATFS_EXFAT
    and needs LFN, which the port already enables."""
    import re
    from pathlib import Path
    h = (Path(__file__).resolve().parent.parent / "firmware" / board / "boards"
         / variant / "mpconfigboard.h").read_text()
    assert re.search(r"#define\s+MICROPY_FATFS_EXFAT\s+\(1\)", h), board
    assert re.search(r"#define\s+MICROPY_HW_ENABLE_SDCARD\s+\(1\)", h), board


@pytest.mark.parametrize("board,variant", P4_BOARDS)
def test_a_p4_board_stages_the_card_store(board, variant):
    from pathlib import Path
    from tools import board_config
    root = Path(__file__).resolve().parent.parent
    assert "card_store.py" in board_config.staged_modules(
        root / "firmware" / board, root)


# -- the two ESP32-S3 boards ---------------------------------------------------
#
# The Guition S3's card has an SPI host of its own (SPI3; the panel is QSPI on
# SPI2), so it takes this module. The T-Deck's shares the panel's host and
# keeps `moybyte_sd`: the one thing `mount` does that it must never do is hand
# a host back after a failed mount, which on that board is the teardown that
# hangs the next panel flush.

ROOT_DIR = Path(__file__).resolve().parent.parent
GUITION_S3 = ROOT_DIR / "firmware" / "guition_jc3248w535"
TDECK = ROOT_DIR / "firmware" / "lilygo_t_deck_plus_mainline"


def _header(board_dir, variant):
    return (board_dir / "boards" / variant / "mpconfigboard.h").read_text()


@pytest.mark.parametrize("board_dir,variant", [
    (GUITION_S3, "MOYBYTE_GUITION_S3"),
    (TDECK, "MOYBYTE_TDECK"),
])
def test_an_s3_board_builds_exfat(board_dir, variant):
    """exFAT is FatFS's, not the card driver's: the T-Deck's moy_sd block device
    reaches `vfs.mount` the same way, so a 64 GB card needs the define there too."""
    assert re.search(r"#define\s+MICROPY_FATFS_EXFAT\s+\(1\)",
                     _header(board_dir, variant)), variant


def test_the_guition_s3_builds_the_card_driver():
    assert re.search(r"#define\s+MICROPY_HW_ENABLE_SDCARD\s+\(1\)",
                     _header(GUITION_S3, "MOYBYTE_GUITION_S3"))


def test_the_guition_s3_stages_the_card_store_and_not_the_spi_attach():
    from tools import board_config
    staged = board_config.staged_modules(GUITION_S3, ROOT_DIR)
    assert "card_store.py" in staged
    assert "moybyte_sd.py" not in staged


def test_the_tdeck_keeps_its_own_bracketed_mount():
    """Its card shares the panel's SPI host: every store op runs in a session
    that drains the panel, the card is attached ONCE and never torn down.
    `card_store.mount` deinit()s on a failed mount, which is that teardown."""
    from tools import board_config
    staged = board_config.staged_modules(TDECK, ROOT_DIR)
    assert "moybyte_sd.py" in staged
    assert "card_store.py" not in staged
    assert "card_store" not in (ROOT_DIR / "device" / "moybyte_sd.py").read_text()


def _module_part(path, names):
    """The shipped top-level assignments and functions `names` of a module that
    cannot be imported on a host (its imports are generated or native), compiled
    alone."""
    import ast
    tree = ast.parse(path.read_text())
    keep = [n for n in tree.body
            if (isinstance(n, ast.FunctionDef) and n.name in names)
            or (isinstance(n, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id in names
                        for t in n.targets))]
    assert {getattr(n, "name", None) or n.targets[0].id for n in keep} == set(names)
    ns = {}
    exec(compile(ast.Module(keep, []), str(path), "exec"), ns)  # noqa: S102
    return ns


def _sd_world(monkeypatch, state):
    """A `moy_sd` whose `up()` brings the board's card up (native/moy_sd's C,
    from the board's own pins) and a `moy_store` whose card volume mounts or
    refuses."""
    moy_sd = types.ModuleType("moy_sd")

    def up():
        state.log.append(("up",))
        return 1024
    moy_sd.up = up
    moy_store = types.ModuleType("moy_store")

    def card(sectors, driver=None):
        if state.error is not None:
            raise state.error
        state.log.append(("card", sectors, driver))
        return types.SimpleNamespace(sectors=sectors)
    moy_store.card = card
    monkeypatch.setitem(sys.modules, "moy_sd", moy_sd)
    monkeypatch.setitem(sys.modules, "moy_store", moy_store)


@pytest.mark.parametrize("path,fn", [
    (GUITION_S3 / "modules" / "moy_runtime.py", "tf_card"),
    (ROOT_DIR / "device" / "p4_desktop.py", "p4_card"),
])
def test_a_card_is_the_stores_volume_over_the_c_bring_up(monkeypatch, path, fn):
    """The Guition S3's SPI3 card and both P4s' SDMMC slot 0: moy_sd's C
    brings the card up (the one bring-up the kernel's recovery floor calls
    with no VM) and the store's own card volume mounts over it, with no
    Python driver below it."""
    state = types.SimpleNamespace(log=[], error=None)
    _sd_world(monkeypatch, state)
    vol = _module_part(path, [fn])[fn]()
    assert state.log == [("up",), ("card", 1024, None)]
    assert vol.sectors == 1024


def test_a_card_with_no_filesystem_leaves_the_bus_up_and_says_so(monkeypatch):
    """The store's volume refuses a card it cannot read; nothing here tears
    the bus down, and the next try brings the card up again."""
    state = types.SimpleNamespace(log=[], error=OSError(19))
    _sd_world(monkeypatch, state)
    tf_card = _module_part(GUITION_S3 / "modules" / "moy_runtime.py", ["tf_card"])["tf_card"]
    assert card_store.mount(tf_card, say=lambda _m: None) is False
    assert "no card interface" in card_store.STATUS
    state.error = None
    tf_card()
    assert state.log.count(("up",)) == 2


@pytest.mark.parametrize("board,want", [
    (TDECK, {"MOY_HW_SPI2_SD_CS": 39, "MOY_HW_SPI2_RADIO_CS": 9}),
    (GUITION_S3, {"MOY_HW_SPI3_SCLK": 12, "MOY_HW_SPI3_MOSI": 11,
                  "MOY_HW_SPI3_MISO": 13, "MOY_HW_SPI3_CS": 10}),
    (ROOT_DIR / "firmware" / "esp32_p4_wifi6_touch_lcd_7b",
     {"MOY_HW_SDMMC0_CLK": 43, "MOY_HW_SDMMC0_CMD": 44, "MOY_HW_SDMMC0_D0": 39,
      "MOY_HW_SDMMC0_D1": 40, "MOY_HW_SDMMC0_D2": 41, "MOY_HW_SDMMC0_D3": 42}),
    (ROOT_DIR / "firmware" / "guition_jc8012p4a1c",
     {"MOY_HW_SDMMC0_CLK": 43, "MOY_HW_SDMMC0_CMD": 44, "MOY_HW_SDMMC0_D0": 39,
      "MOY_HW_SDMMC0_D1": 40, "MOY_HW_SDMMC0_D2": 41, "MOY_HW_SDMMC0_D3": 42}),
])
def test_the_card_c_reads_its_pins_from_the_boards_hardware_table(board, want):
    """native/moy_sd/moy_sd.c picks the card's shape and pins from the
    board.toml [hardware] rows (hw_defines): the T-Deck's `sd_cs` on the
    panel's spi2 is an attach, with the radio's CS parked; the Guition S3's
    spi3 a bus of its own (host 2, never the panel's SPI2); the P4s' sdmmc0
    slot 0 -- the C6's slot 1 is never named."""
    from tools import board_config
    d = dict(board_config.hw_defines(str(board)))
    assert {k: d.get(k) for k in want} == want
    shapes = [k for k in ("MOY_HW_SDMMC0_CLK", "MOY_HW_SPI2_SD_CS", "MOY_HW_SPI3_CS") if k in d]
    assert len(shapes) == 1, shapes


def test_the_p4s_name_the_ldo_that_powers_their_slot():
    """moy_sd's SDMMC bring-up switches the slot's LDO on before CMD0; without
    it the card times out whether one is there or not."""
    for b in ("esp32_p4_wifi6_touch_lcd_7b/boards/MOYBYTE_P4",
              "guition_jc8012p4a1c/boards/MOYBYTE_GUITION_P4"):
        text = (ROOT_DIR / "firmware" / b / "mpconfigboard.h").read_text()
        assert "#define MOY_SD_MMC_LDO_REG                  (0x501151D8u)" in text, b


@pytest.mark.parametrize("path,names", [
    (GUITION_S3 / "modules" / "moy_runtime.py", ["SD_CARTS_ROOT"]),
    (ROOT_DIR / "device" / "p4_desktop.py", ["SD_CARTS_ROOT"]),
])
def test_a_card_store_lives_where_the_t_deck_keeps_it(path, names):
    """One layout on every board: the store is `moy_carts.CARTS_DIR`, and the
    system documents beside it land in /sd/moybyte, not at the card's root."""
    from runtime import moy_carts
    root = _module_part(path, names)["SD_CARTS_ROOT"]
    assert root == moy_carts.CARTS_DIR, path.name
