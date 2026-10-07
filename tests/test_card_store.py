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


@pytest.fixture
def guition(monkeypatch):
    """The Guition's `tf_card` over a `moy_sd` that opens its SPI3 bus once
    and a `moy_store` whose card volume mounts or refuses."""
    state = types.SimpleNamespace(opened=[], cards=[], error=None)
    moy_sd = types.ModuleType("moy_sd")

    def open_(host, sck, mosi, miso, cs, khz):
        state.opened.append((host, sck, mosi, miso, cs, khz))
        return 1024
    moy_sd.open = open_
    moy_store = types.ModuleType("moy_store")

    def card(sectors, driver=None):
        if state.error is not None:
            raise state.error
        state.cards.append(sectors)
        return types.SimpleNamespace(sectors=sectors)
    moy_store.card = card
    monkeypatch.setitem(sys.modules, "moy_sd", moy_sd)
    monkeypatch.setitem(sys.modules, "moy_store", moy_store)
    state.mod = _module_part(GUITION_S3 / "modules" / "moy_runtime.py",
                             ["SD_PINS", "SD_SPI_HOST", "SD_FREQ_KHZ",
                              "SD_CARTS_ROOT", "tf_card"])
    return state


def test_the_guition_card_is_on_spi3_never_the_panels_host(guition):
    """SPI3 is host 2 (the card's pins); host 1 is SPI2, the panel's QSPI."""
    guition.mod["tf_card"]()
    assert guition.opened == [(2, 12, 11, 13, 10, 20000)]
    assert guition.cards == [1024]


def test_a_card_with_no_filesystem_leaves_the_bus_up_and_says_so(guition):
    """The store's volume refuses a card it cannot read; the bus stays up,
    since nothing here tears it down, and the next try opens it again."""
    guition.error = OSError(19)
    assert card_store.mount(guition.mod["tf_card"], say=lambda _m: None) is False
    assert "no card interface" in card_store.STATUS
    guition.error = None
    guition.mod["tf_card"]()
    assert len(guition.opened) == 2


@pytest.fixture
def p4(monkeypatch):
    """The P4 tier's `p4_card` over a `machine` whose mem32 records the LDO
    pokes, a `moy_sd` whose SDMMC slot comes up once, and a `moy_store` whose
    card volume mounts."""
    state = types.SimpleNamespace(log=[])

    class _Mem:
        def __init__(self):
            self.regs = {}

        def __getitem__(self, a):
            return self.regs.get(a, 0)

        def __setitem__(self, a, v):
            self.regs[a] = v
            state.log.append(("mem32", a, v))
    machine = types.ModuleType("machine")
    machine.mem32 = _Mem()
    moy_sd = types.ModuleType("moy_sd")

    def mmc(slot, clk, cmd, data, khz=20000):
        state.log.append(("mmc", slot, clk, cmd, tuple(data), khz))
        return 4096
    moy_sd.mmc = mmc
    moy_store = types.ModuleType("moy_store")

    def card(sectors, driver=None):
        state.log.append(("card", sectors, driver))
        return types.SimpleNamespace(sectors=sectors)
    moy_store.card = card
    time = types.ModuleType("time")
    time.sleep_ms = lambda ms: state.log.append(("sleep", ms))
    for name, mod in (("machine", machine), ("moy_sd", moy_sd),
                      ("moy_store", moy_store), ("time", time)):
        monkeypatch.setitem(sys.modules, name, mod)
    state.mod = _module_part(ROOT_DIR / "device" / "p4_desktop.py",
                             ["SD_SLOT", "SD_CLK", "SD_CMD", "SD_DATA", "SD_FREQ_KHZ",
                              "LDO4_REG", "p4_card"])
    return state


def test_the_p4_card_is_the_stores_volume_on_sdmmc_slot_0(p4):
    """Both P4s: LDO4 powered before the card's first command, then SDMMC
    slot 0 (slot 1 is the C6's) on GPIO39-44 through moy_sd, and the store's
    own card volume -- the kernel's FATFS and read cache -- over it, with no
    Python driver below it."""
    vol = p4.mod["p4_card"]()
    kinds = [e[0] for e in p4.log]
    assert kinds.index("mem32") < kinds.index("sleep") < kinds.index("mmc")
    ldo = [e[2] for e in p4.log if e[0] == "mem32"][-1]
    assert ldo & (1 << 7) and ldo & (1 << 8) and ldo & (1 << 14)
    assert ("mmc", 0, 43, 44, (39, 40, 41, 42), 20000) in p4.log
    assert p4.log[-1] == ("card", 4096, None)
    assert vol.sectors == 4096


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
