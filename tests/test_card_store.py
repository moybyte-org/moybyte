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

import sys
import types

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
    """machine.SDCard() succeeds with nothing in the slot (it initialises the
    host and leaves the card to the first read), so an empty slot is the mount's
    failure: FatFS's FR_NOT_READY, EBUSY. Measured on both P4 boards, 2026-10-05."""
    vfs.error = OSError(16)
    card, lines = _Card(), []

    assert card_store.mount(lambda: card, say=lines.append) is False

    assert card.deinits == 1
    assert len(lines) == 1 and "no card answered" in lines[0]


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

    def seed_any(self, seed, root, progress=None):
        self.calls.append(("seed", root))

    def scan(self, root, src=True):
        self.calls.append(("scan", root))
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
