"""`device/moy_ota.py`, EXECUTED on the host (#208's closing residue).

Until this file, the only lane that ran `begin`/`step`/`mark_valid`/the whole
download half was `tests/test_p4_on_glass.py`, which is gated on a board being
plugged in. A green CI tree therefore said nothing whatsoever about the
firmware update path -- the one code path in this repo whose failure mode is a
console that will not boot.

Most of OTA is not device-bound. `esp32.Partition`, `machine`, the socket, the
clock and the filesystem all stub cleanly at the import boundary (every one of
them is imported INSIDE the function that uses it, which is what makes the real
bodies runnable here with nothing transcribed). What is left over is named at
the bottom of this docstring.

What already had nets, and is deliberately NOT re-tested here:

  * `tests/test_ota_signing.py` -- the signature policy: the canonical bytes,
    tamper, the baked-vs-card asymmetry, the wrong-board refusal.
  * `tests/test_ota_health.py` -- `confirm_when_healthy`, the pending marker's
    lifetime, `finish`'s discard scoping, the "nothing published" arm.
  * `tests/test_ota_manifest.py` -- `_http_open`'s redirect following and
    GitHub's 5KB header block.

Those three own their invariants; the mutation sweep for this campaign
perturbed them too and recorded which file went red, but nothing is duplicated
into here.

THE MODEL OF THE FLASH. `_Slot` below records every `writeblocks` and refuses
`set_boot` on an image shorter than the length its header declares -- which is
what `esp_ota_set_boot_partition` does with `ESP_ERR_OTA_VALIDATE_FAILED`. The
header here is a MODEL (`0xE9`, then a little-endian total length at byte 4)
rather than the real 24-byte `esp_image_header_t`, whose segment table the
bootloader walks; what is faithful is the consequence, and the consequence is
the whole reason `step()`'s return polarity matters.

WHAT STAYS DEVICE-ONLY, and must not be read as covered by a green run here:

  * the real flash write. `esp32.Partition.writeblocks` erases a 4K page and
    programs it; nothing off-glass can show that the bytes landed, that the
    erase preceded the write, or that a brown-out mid-page leaves the slot in
    the state the bootloader expects.
  * `esp_ota_set_boot_partition`'s real validation -- it walks the image's
    segment table and checksum. `_Slot.set_boot` models only "shorter than
    declared", which is the shape a truncated install has.
  * the reboot, and the bootloader's rollback itself. `machine.reset()` is
    recorded here; whether the other slot comes up, and whether an image that
    never confirms is reverted, is the bootloader's behaviour and was verified
    on glass on both boards on 2026-08-02.
  * TLS. `ssl.wrap_socket` on the device verifies no certificate, which is the
    entire reason the manifest is signed; the fake network here is plaintext.
  * the SD/panel bus hazard `_with_sd` exists for. What is testable is the
    DISCIPLINE -- that every storage touch goes through the injected wrapper,
    and that a chunk's read and its write share one session -- and that is
    pinned below. `tests/test_moybyte_sd.py` owns the wrapper itself.

The residue after all of that is ~7% of the module's statements, and it is
almost entirely `except Exception: pass` arms on cleanup paths whose failure is
by design not observable (a close that could not happen, a log that threw).
`verify_sig`'s `except (OverflowError, ValueError)` around `to_bytes` looks
unreachable rather than untested: the modexp result is < n, so it always fits
the k bytes derived from the same modulus.
"""

import json
import struct
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

# The scripted-network doubles are already written once, for the redirect tests.
from test_ota_manifest import CSP, _FakeNet, _response  # noqa: E402
from test_ota_signing import TEST_KEYS, sign_with_test_key  # noqa: E402


def _fresh():
    """A private copy of the module per test.

    Several tests write module globals (`OTA_PUBLIC_KEYS`, `BOARD`), and the
    identity constants are read at call time, so a shared instance would leak
    one test's build into the next. conftest's autouse `_no_local_build_stamp`
    keeps the committed identity even on a machine that has run build.sh.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "moy_ota_unit", ROOT / "device" / "moy_ota.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# -- the board doubles ---------------------------------------------------------

def app_image(payload=0, magic=0xE9):
    """An app-partition image: the magic byte, then a declared total length.

    A model of `esp_image_header_t` -- enough for the flash double to tell a
    whole image from a truncated one, which is the property every install test
    turns on.
    """
    body = bytes((i * 7 + 3) & 0xFF for i in range(payload))
    return bytes([magic]) + b"\x03\x02\x0f" + struct.pack("<I", 8 + payload) + body


class _Slot:
    """One app partition, recording what was flashed into it."""

    def __init__(self, label, size, card=None):
        self.label = label
        self.size = size
        self.card = card
        self.blocks = {}          # index -> the 4K page as it was programmed
        self.sessions_at_write = []
        self.booted = False
        self.write_error = None

    def info(self):
        # (type, subtype, addr, size, label, encrypted)
        return (0, 0x10, 0x20000, self.size, self.label, False)

    def writeblocks(self, idx, buf, offset=None):
        if self.write_error is not None:
            raise self.write_error
        # The updater reuses ONE buffer for every block, so a double that kept
        # the memoryview would end up with N references to the last chunk.
        self.blocks[idx] = bytes(buf)
        self.sessions_at_write.append(self.card.inside if self.card else None)

    def image(self):
        """The contiguous run of pages from index 0 -- a gap is a lost block,
        and the bootloader would read whatever the erase left behind."""
        out = bytearray()
        i = 0
        while i in self.blocks:
            out += self.blocks[i]
            i += 1
        return bytes(out)

    def set_boot(self):
        img = self.image()
        if len(img) < 8 or img[0] != 0xE9:
            raise OSError("ESP_ERR_OTA_VALIDATE_FAILED")
        if len(img) < struct.unpack("<I", img[4:8])[0]:
            raise OSError("ESP_ERR_OTA_VALIDATE_FAILED")
        self.booted = True


class _Esp32:
    """`esp32`, with the running slot and the one it would ping-pong to."""

    def __init__(self, running="ota_0", other="ota_1", size=4 * 1024 * 1024,
                 card=None):
        self.running = _Slot(running, size, card)
        self.other = _Slot(other, size, card)
        self.marked = 0
        self.mark_error = None
        esp = self

        class Partition:
            RUNNING = "RUNNING"

            def __new__(cls, which):
                assert which == cls.RUNNING
                return esp.running

            @staticmethod
            def mark_app_valid_cancel_rollback():
                if esp.mark_error is not None:
                    raise esp.mark_error
                esp.marked += 1

        self.Partition = Partition
        self.running.get_next_update = lambda: self.other


class _Card:
    """The injected `with_sd`: the wrapper every storage touch goes through.

    Counts sessions and reports whether one is OPEN, which is how "the read and
    the write of a chunk share one session" becomes an assertion rather than a
    hope.
    """

    def __init__(self):
        self.sessions = 0
        self.depth = 0
        self.fail = None
        self.results = []          # what each session handed back

    def __call__(self, fn):
        if self.fail is not None:
            raise self.fail
        self.sessions += 1
        self.depth += 1
        try:
            out = fn()
        finally:
            self.depth -= 1
        self.results.append(out)
        return out

    @property
    def inside(self):
        return self.depth > 0


def _install_esp32(monkeypatch, esp):
    mod = types.ModuleType("esp32")
    mod.Partition = esp.Partition
    monkeypatch.setitem(sys.modules, "esp32", mod)
    return mod


def _install_machine(monkeypatch):
    mod = types.ModuleType("machine")
    mod.resets = []
    mod.reset = lambda: mod.resets.append(1)
    monkeypatch.setitem(sys.modules, "machine", mod)
    return mod


def _fake_time(ticks=False, sleep_ms=True):
    """`time`, fast. Unknown names fall through to the real module so anything
    else importing `time` while this is installed still works."""
    import time as real_time

    m = types.ModuleType("time")
    m.slept = []
    if ticks:
        m.now = 1000
        m.ticks_ms = lambda: m.now
        m.ticks_diff = lambda a, b: a - b
    if sleep_ms:
        def _sleep_ms(ms):
            m.slept.append(ms)
            if ticks:
                m.now += ms
        m.sleep_ms = _sleep_ms

    def _sleep(sec):
        m.slept.append(int(sec * 1000))
    m.sleep = _sleep
    hidden = set()
    if not ticks:
        hidden |= {"ticks_ms", "ticks_diff"}
    if not sleep_ms:
        hidden.add("sleep_ms")

    def __getattr__(name):
        if name in hidden:
            raise AttributeError(name)
        return getattr(real_time, name)
    m.__getattr__ = __getattr__
    return m


@pytest.fixture
def board(tmp_path, monkeypatch):
    """An updater with a fake board under it: fake flash, a real staging
    directory, and a card that records its sessions."""
    mod = _fresh()
    card = _Card()
    esp = _Esp32(card=card)
    _install_esp32(monkeypatch, esp)
    d = tmp_path / "update"
    d.mkdir()
    u = mod.OtaUpdater(card, update_dir=str(d))
    return types.SimpleNamespace(mod=mod, u=u, esp=esp, card=card, dir=d)


def _staged(board, payload=9000, name="firmware.bin", magic=0xE9):
    blob = app_image(payload, magic)
    p = board.dir / name
    p.write_bytes(blob)
    return str(p), blob


def _run_install(u):
    """The console's own drive loop, verbatim from update_ui._pump_update:
    `more = u.step()` until it says there is nothing left."""
    steps = 0
    while u.step():
        steps += 1
        assert steps < 10000, "the install never terminated"
    return steps


# == the install state machine =================================================

def test_step_says_true_while_more_remains_and_false_at_the_end(board):
    """The single highest-value fact in the module. `update_ui` drives it as
    `more = u.step()`, so an inverted return does not fail -- it stops the loop
    after one chunk and calls finish() on a 32K stump."""
    path, blob = _staged(board, payload=9000)          # 3 blocks: 8+9000 bytes
    board.u.begin(path)
    assert board.u.step(max_blocks=1) is True
    assert board.u.step(max_blocks=1) is True
    assert board.u.step(max_blocks=1) is False         # the last, partial block
    assert board.u.done == len(blob)


def test_the_console_loop_flashes_the_whole_image(board):
    path, blob = _staged(board, payload=70000)
    assert board.u.begin(path) == len(blob)
    _run_install(board.u)
    assert board.u.done == len(blob)
    flashed = board.esp.other.image()
    assert flashed[:len(blob)] == blob
    assert board.u.finish() is True
    assert board.esp.other.booted is True


def test_a_truncated_install_is_refused_by_the_bootloader(board):
    """Why the polarity matters, stated as its consequence. A loop that stops
    early leaves a stump in the slot, and set_boot answers
    ESP_ERR_OTA_VALIDATE_FAILED -- the board keeps its old firmware, and the
    only evidence is an error string on the update screen."""
    path, blob = _staged(board, payload=70000)
    board.u.begin(path)
    board.u.step(max_blocks=1)                          # ...and stop, as an
    assert board.u.done < len(blob)                     # inverted return would
    assert board.u.finish() is False
    assert "VALIDATE" in board.u.error
    assert board.esp.other.booted is False


def test_the_last_partial_block_is_padded_with_erased_bytes(board):
    """A 4K page is written whole. The tail must be 0xFF -- what an erase
    leaves -- so the programmed page matches what the erase already put there
    rather than forcing zeroes the image does not contain."""
    path, blob = _staged(board, payload=4096 + 92)      # block 1 is 100 bytes
    board.u.begin(path)
    _run_install(board.u)
    tail = board.esp.other.blocks[1]
    assert tail[:100] == blob[4096:]
    assert set(tail[100:]) == {0xFF}


def test_an_image_that_is_an_exact_multiple_of_the_page_size(board):
    """The off-by-one that lives next to the polarity: when the image ends ON a
    4K boundary there is no short read to end the install, so the loop has to
    take a zero-length read as EOF -- and it must not write a padding page of
    0xFF past the end of the image."""
    path, blob = _staged(board, payload=8192 - 8)
    assert len(blob) % 4096 == 0
    board.u.begin(path)
    assert board.u.step(max_blocks=1) is True
    assert board.u.step(max_blocks=1) is True     # the image is now complete...
    assert board.u.step(max_blocks=1) is False    # ...and the empty read ends it
    assert board.u.done == len(blob)
    assert len(board.esp.other.blocks) == len(blob) // 4096
    assert board.esp.other.image() == blob        # not one 0xFF page longer
    assert board.u.finish() is True


def test_the_blocks_go_down_in_order_from_zero(board):
    path, _blob = _staged(board, payload=40000)
    board.u.begin(path)
    _run_install(board.u)
    assert sorted(board.esp.other.blocks) == list(range(len(board.esp.other.blocks)))


def test_the_running_slot_is_never_touched(board):
    """The whole safety model: the image the board is executing stays intact,
    so a failed or half-written update cannot brick it."""
    path, _blob = _staged(board, payload=40000)
    board.u.begin(path)
    _run_install(board.u)
    board.u.finish()
    assert board.esp.running.blocks == {}
    assert board.esp.running.booted is False
    assert board.esp.other.label == "ota_1"


def test_a_step_flashes_at_most_the_blocks_it_was_asked_for(board):
    """The console repaints the progress bar between steps, so a step that ran
    away with the whole image would freeze the screen for the install."""
    path, _blob = _staged(board, payload=100000)
    board.u.begin(path)
    board.u.step(max_blocks=2)
    assert len(board.esp.other.blocks) == 2
    board.u.step(max_blocks=3)
    assert len(board.esp.other.blocks) == 5


def test_a_chunks_read_and_its_write_share_one_card_session(board):
    """Flash writes do not touch the shared SPI bus but the SD reads do, so the
    read and the write are bracketed together -- one mount per step, not one
    per operation."""
    path, _blob = _staged(board, payload=40000)
    board.u.begin(path)
    before = board.card.sessions
    board.u.step(max_blocks=4)
    assert board.card.sessions == before + 1
    assert board.esp.other.sessions_at_write[-4:] == [True] * 4


def test_a_step_before_begin_reports_nothing_left_to_do(board):
    """Quietly. The console polls `step()` from the frame loop, so a stray call
    that manufactured an error string would put "Update didn't finish" on the
    screen of a kid who never started one."""
    assert board.u.step() is False
    assert board.u.error is None


def test_a_failed_read_cancels_the_install_and_names_the_error(board):
    """A card pulled mid-install. The slot is abandoned rather than left
    half-written and pointed at."""
    path, _blob = _staged(board, payload=40000)
    board.u.begin(path)
    board.u.step(max_blocks=1)
    board.card.fail = OSError(5)
    assert board.u.step() is False
    assert board.u.error == "OSError 5"
    assert board.u._part is None and board.u._f is None
    board.card.fail = None
    assert board.u.finish() is False              # nothing to point the boot at


def test_a_failed_flash_write_cancels_the_install(board):
    path, _blob = _staged(board, payload=40000)
    board.u.begin(path)
    board.esp.other.write_error = OSError("ESP_ERR_FLASH_OP_FAIL")
    assert board.u.step() is False
    assert "FLASH_OP_FAIL" in board.u.error
    assert board.u.done == 0


# -- what begin() refuses ------------------------------------------------------

def test_begin_arms_the_progress_bar_and_keeps_the_file_open(board):
    path, blob = _staged(board, payload=9000)
    assert board.u.begin(path) == len(blob)
    assert (board.u.total, board.u.done, board.u.path) == (len(blob), 0, path)
    assert board.u._f is not None and board.u._part is board.esp.other
    board.u.cancel()


def test_begin_refuses_an_image_bigger_than_the_slot(board):
    """It cannot fit, and finding that out block by block would mean discovering
    it with the slot already half-erased."""
    board.esp.other.size = 8192
    path, _blob = _staged(board, payload=20000)
    with pytest.raises(ValueError) as exc:
        board.u.begin(path)
    assert "slot" in str(exc.value)
    assert board.esp.other.blocks == {}


def test_begin_refuses_an_empty_image(board):
    """Named, not merely refused: a zero-byte file is a copy that never
    started, and "not an app image" would send the owner hunting for a corrupt
    download instead."""
    p = board.dir / "empty.bin"
    p.write_bytes(b"")
    with pytest.raises(ValueError) as exc:
        board.u.begin(str(p))
    assert "empty image" in str(exc.value)


def test_begin_refuses_a_file_that_is_not_an_app_image(board):
    """A kid copying a photo, a cart, or a half-downloaded .bin into the
    staging directory. The first byte of an ESP32 app image is 0xE9."""
    path, _blob = _staged(board, payload=4000, magic=0x50)
    with pytest.raises(ValueError) as exc:
        board.u.begin(path)
    assert "app image" in str(exc.value)
    assert board.u._f is None, "the rejected file was left open"
    # ...and the handle it opened to LOOK is closed. Nothing else will: the
    # updater dropped its only reference to it on the way out.
    opened = [r[0] for r in board.card.results if isinstance(r, tuple)]
    assert opened and all(f.closed for f in opened)


def test_begin_clears_the_error_from_a_previous_attempt(board):
    board.u.error = "sha256 mismatch"
    path, _blob = _staged(board, payload=4000)
    board.u.begin(path)
    assert board.u.error is None
    board.u.cancel()


def test_the_magic_byte_alone_cannot_tell_a_merged_image_apart(board):
    """Stated because it is the trap, not because it is a feature.

    `dist/<board>/moybyte_<board>.bin` is bootloader + partition table + app,
    merged for a cable flash; the OTA payload is the APP image beside it.
    Handing the merged one to `esp32.Partition` writes a bootloader into an app
    slot -- and the header check here will NOT catch it, because a bootloader
    is itself an ESP image and starts with the same 0xE9.

    So the guard is upstream, in what the publisher uploads, and it is pinned
    by the next test. This one records that the check in `begin` is a
    file-kind check and nothing more.
    """
    merged = board.dir / "merged.bin"
    merged.write_bytes(app_image(400))                 # a bootloader-shaped head
    assert board.u.begin(str(merged)) > 0
    board.u.cancel()


def test_the_published_payload_is_the_app_image_not_the_cable_merge():
    """One field per board, and getting it wrong ships a valid image that
    cannot boot -- into the slot the bootloader is about to jump to."""
    import publish_firmware_release as publish

    try:
        import tomllib
    except ImportError:                                # pragma: no cover
        import tomli as tomllib

    # DERIVED from the tree, not listed: every firmware directory that declares
    # a flashable image is a board that publishes, so a new one joins this check
    # by EXISTING. The hand-written map this replaced was three boards long the
    # day a fourth arrived, and the failure it protects against is precisely a
    # board that builds and quietly gets no manifest -- which is what kept the
    # Guition's first beta off the channel on 2026-08-20.
    boards = {}
    for path in sorted((ROOT / "firmware").glob("*/board.toml")):
        with open(path, "rb") as f:
            cfg = tomllib.load(f)
        if "flash" in cfg and cfg.get("board", {}).get("ota"):
            boards[cfg["board"]["ota"]] = cfg["flash"]["image"]
    assert len(boards) >= 4, "board discovery found %d" % len(boards)
    assert set(publish.OTA_IMAGES) == set(boards), (
        "a board that builds an image but has no OTA_IMAGES entry gets no "
        "manifest, silently, on every channel")
    for board_id, merged in boards.items():
        ota = publish.OTA_IMAGES[board_id]
        assert ota.endswith("_app.bin"), board_id
        assert Path(merged).name != ota, (
            "%s publishes its cable-flash merge as the OTA payload" % board_id)
        assert Path(merged).name == ota.replace("_app.bin", ".bin"), board_id


# -- finding an image the owner copied over -----------------------------------

def test_find_bin_picks_the_biggest_image_in_the_staging_directory(board):
    """The Phase-2 path: a kid drags a .bin onto the card. Biggest wins, which
    is how a complete image beats the half-copied one beside it."""
    (board.dir / "small.bin").write_bytes(b"\xe9" + b"\x00" * 100)
    (board.dir / "big.bin").write_bytes(b"\xe9" + b"\x00" * 5000)
    assert board.u.find_bin() == (str(board.dir / "big.bin"), 5001)


def test_find_bin_ignores_everything_that_is_not_an_image(board):
    """`ota.json` and `pending.json` live in this directory, and so does
    whatever else the owner put on the card."""
    (board.dir / "ota.json").write_text("{}", encoding="utf-8")
    (board.dir / "notes.txt").write_bytes(b"x" * 99999)
    (board.dir / "fw.BIN").write_bytes(b"\xe9" + b"\x00" * 10)
    assert board.u.find_bin() == (str(board.dir / "fw.BIN"), 11)


def test_find_bin_on_an_empty_or_missing_directory(board, tmp_path):
    """A board that has never been updated has no staging directory, which is
    the ORDINARY state -- so it must read as "nothing to install", never as an
    error the update screen puts in front of the kid."""
    assert board.u.find_bin() is None
    board.u.update_dir = str(tmp_path / "never-made")
    assert board.u.find_bin() is None
    assert board.u.error is None


def test_find_bin_skips_an_entry_it_cannot_stat(board):
    """A torn directory entry. One unreadable name must not hide the good
    image beside it, and must not raise on the path a kid reaches by tapping
    UPDATE FW.

    (A DIRECTORY named `*.bin` is a different case and is NOT skipped -- it
    stats fine, so it can win on size and be handed to `begin`, which fails at
    `open()` and surfaces as an error on the update screen. Recorded rather
    than asserted: the behaviour is confusing, not dangerous.)"""
    (board.dir / "gone.bin").symlink_to(board.dir / "nothing-here")
    (board.dir / "real.bin").write_bytes(b"\xe9" + b"\x00" * 40)
    assert board.u.find_bin() == (str(board.dir / "real.bin"), 41)


def test_find_bin_reports_a_card_that_will_not_mount(board):
    board.card.fail = OSError(5)
    assert board.u.find_bin() is None
    assert board.u.error == "OSError 5"


# -- teardown verbs ------------------------------------------------------------

def test_cancel_releases_the_file_and_forgets_the_slot(board):
    path, _blob = _staged(board, payload=40000)
    board.u.begin(path)
    board.u.step(max_blocks=1)
    f = board.u._f
    board.u.cancel()
    assert (board.u._part, board.u._f, board.u._block, board.u.done) == \
        (None, None, 0, 0)
    assert f.closed


def test_closing_a_file_that_is_already_gone_is_harmless(board):
    board.u._close_file()
    board.u._close_file()
    assert board.u._f is None


def test_finish_without_a_slot_reports_failure_quietly(board):
    """No slot means nothing was installed, which is not an error to report --
    and `update_ui` prints `u.error` verbatim when finish() says no."""
    assert board.u.finish() is False
    assert board.u.error is None


def test_reset_reboots_the_board(board, monkeypatch):
    machine = _install_machine(monkeypatch)
    board.u.reset()
    assert machine.resets == [1]


# == capability and identity ===================================================

def test_an_ota_build_names_the_slot_it_is_running(board):
    assert board.u.available() is True
    assert board.u.slot() == "ota_0"


def test_a_legacy_factory_build_has_nowhere_to_write(board, monkeypatch):
    """One app partition means no second slot, so the console hides the row
    rather than offering an update that would overwrite the running image."""
    esp = _Esp32(running="factory", card=board.card)
    _install_esp32(monkeypatch, esp)
    assert board.u.available() is False
    assert board.u.slot() == "factory"


def test_a_board_with_no_esp32_declines_instead_of_raising(board, monkeypatch):
    """The host imports this module (these tests do). Every esp32 touch is
    swallowed, so the shared console simply never shows the row."""
    monkeypatch.setitem(sys.modules, "esp32", None)
    assert board.u.available() is False
    assert board.u.slot() == "?"
    assert board.u.online_available() is False


def test_mark_valid_cancels_the_pending_rollback(board):
    assert board.u.mark_valid() is True
    assert board.esp.marked == 1


def test_mark_valid_swallows_a_board_that_cannot(board):
    """Already-valid, or not an OTA build at all. Neither is a failure worth
    stopping a boot for."""
    board.esp.mark_error = OSError("ESP_ERR_INVALID_STATE")
    assert board.u.mark_valid() is False


def test_the_online_row_needs_both_a_slot_and_a_radio(board):
    assert board.u.online_available() is False        # no wifi injected
    board.u.set_wifi(object())
    assert board.u.online_available() is True


def test_set_wifi_keeps_the_existing_autoconnect_unless_told(board):
    """`go_online=None` is "leave it alone", not "clear it" -- a board that
    re-injects its radio must not silently lose the saved-credentials dial."""
    dial = lambda: None                                # noqa: E731
    board.u.set_wifi(object(), dial)
    board.u.set_wifi(object())
    assert board.u._go_online is dial
    board.u.set_wifi(object(), lambda: "other")
    assert board.u._go_online is not dial


def test_the_version_label_prefers_the_stamped_one(board):
    m = board.mod
    assert board.u.version_label() == m.FIRMWARE_NAME
    m.FIRMWARE_LABEL = "beta 2026-08-29"
    assert board.u.version_label() == "beta 2026-08-29"
    m.FIRMWARE_LABEL = None
    m.FIRMWARE_NAME = None
    assert board.u.version_label() == "v%d" % m.FIRMWARE_VERSION
    assert board.u.version() == m.FIRMWARE_VERSION


def test_the_build_stamp_is_what_gives_an_image_its_identity(monkeypatch):
    """`build.sh` writes a gitignored `_ota_build` so the channel is a BUILD
    choice, not a per-branch source edit. Nothing else in the suite executes
    that import -- conftest neutralises it process-wide, precisely so a machine
    that has run build.sh does not read differently from one that has not.

    It matters because all four fields steer the update: the channel decides
    what a check is compared against, the board decides which manifest is even
    fetched (an app-partition image is Xtensa or RISC-V), the version orders
    two betas, and the label is the only string a human reads.
    """
    stamp = types.ModuleType("_ota_build")
    stamp.CHANNEL = "unstable"
    stamp.VERSION = 1785659788                      # a beta stamps a build epoch
    stamp.LABEL = "beta 2026-08-29"
    stamp.BOARD = "guition_s3"
    monkeypatch.setitem(sys.modules, "_ota_build", stamp)
    m = _fresh()
    u = m.OtaUpdater(lambda fn: fn())
    assert (u.channel(), u.version()) == ("unstable", 1785659788)
    assert u.version_label() == "beta 2026-08-29"
    assert m.BOARD == "guition_s3"
    assert m.default_manifest_url("unstable").endswith("/latest-guition_s3.json")


def test_an_empty_build_stamp_keeps_the_committed_identity(monkeypatch):
    """A half-written stamp must not blank the identity out: an image whose
    channel or board went missing would fetch the wrong manifest, or none."""
    stamp = types.ModuleType("_ota_build")
    stamp.CHANNEL = ""
    stamp.LABEL = ""
    stamp.BOARD = ""
    monkeypatch.setitem(sys.modules, "_ota_build", stamp)
    m = _fresh()
    assert (m.FIRMWARE_CHANNEL, m.BOARD) == ("stable", "tdeck")
    assert m.OtaUpdater(lambda fn: fn()).version_label() == m.FIRMWARE_NAME


def test_a_manifest_with_a_junk_version_is_not_newer(board):
    """Whatever arrives off the wire lands in int(). A crash here would be a
    denied update, and a silently-huge one would be a forced downgrade."""
    u = board.u
    assert u.offers({"version": "not a number", "channel": "stable"}) is False
    assert u.offers({"version": None, "channel": "stable"}) is False
    assert u.offers({}) is False                       # channel defaults to ours
    assert u.offers({"version": board.mod.FIRMWARE_VERSION + 1}) is True


# == how long "healthy" takes ==================================================
#
# `tests/test_ota_health.py` owns the confirm's SHAPE, and does it entirely
# through the two constants -- which is correct for the shape and leaves the
# VALUES unpinned: HEALTHY_LOOPS could be 1 and every one of those tests would
# still pass. These two are about the numbers.

def test_the_loop_threshold_is_a_real_wait_not_a_formality(board):
    """The confirm cancels the rollback, so it is the last moment the board can
    be saved from an image that comes up and then dies. A handful of iterations
    would confirm inside the boot itself; the constant is ~2-4s of frames on
    either board -- long enough that an ordinary crash lands inside it, short
    enough that nobody power-cycles first."""
    assert board.mod.HEALTHY_LOOPS >= 60
    for _ in range(30):
        assert board.u.confirm_when_healthy(5) is False
    assert board.esp.marked == 0


def test_the_paint_threshold_is_exactly_one(board):
    """MEASURED on the P4: an idle desktop had drawn ONE frame six seconds
    after boot, because the console repaints only when something changes. Any
    higher threshold rolls back every update that lands while nobody is poking
    at the console -- and one painted frame is already the whole of what #56
    was missing."""
    assert board.mod.HEALTHY_PAINTS == 1
    fired = [board.u.confirm_when_healthy(1)
             for _ in range(board.mod.HEALTHY_LOOPS)]
    assert fired.count(True) == 1
    assert board.esp.marked == 1


# == where the manifest comes from =============================================

def _card_cfg(board, cfg):
    (board.dir / "ota.json").write_text(json.dumps(cfg), encoding="utf-8")


def test_a_leftover_ota_json_reroutes_every_check(board):
    """The card WINS, deliberately -- it is how a classroom points a board at a
    LAN mirror. It is also why a forgotten ota.json makes a board that looks
    online silently never see the real channel again."""
    _card_cfg(board, {"channels": {"stable": "http://192.168.1.9:8000/l.json"}})
    url, from_card = board.u._manifest_source()
    assert (url, from_card) == ("http://192.168.1.9:8000/l.json", True)
    assert board.u.manifest_url() == url
    assert "github" not in url


def test_the_card_answers_for_the_channel_that_was_asked_for(board):
    _card_cfg(board, {"channels": {"stable": "http://h/s.json",
                                   "unstable": "http://h/u.json"}})
    assert board.u.manifest_url("unstable") == "http://h/u.json"
    assert board.u.manifest_url("stable") == "http://h/s.json"


def test_a_card_missing_this_channel_falls_back_within_itself(board):
    """A hand-written ota.json naming one host: asking for a channel it does
    not list must reach that host, not jump back to GitHub behind the owner."""
    _card_cfg(board, {"channels": {"lan": "http://h/only.json"}})
    url, from_card = board.u._manifest_source("unstable")
    assert (url, from_card) == ("http://h/only.json", True)


def test_the_running_channel_wins_over_stable_on_the_card(board):
    _card_cfg(board, {"channels": {"stable": "http://h/s.json",
                                   "unstable": "http://h/u.json"}})
    board.mod.FIRMWARE_CHANNEL = "unstable"
    assert board.u._manifest_source()[0] == "http://h/u.json"


def test_a_legacy_single_url_card_still_works(board):
    """The Phase-3 shape, from before there were two channels. A board in a
    drawer with that file on its card must not stop updating."""
    _card_cfg(board, {"manifest_url": "http://h/latest.json"})
    assert board.u._manifest_source() == ("http://h/latest.json", True)


def test_no_card_entry_means_the_baked_channel_url(board):
    url, from_card = board.u._manifest_source("unstable")
    assert from_card is False
    assert url == board.mod.default_manifest_url("unstable")
    assert url.endswith("/latest-tdeck.json")


@pytest.mark.parametrize("bad", ["not json at all", "{}", '{"channels": {}}'])
def test_an_unusable_card_file_falls_back_to_the_baked_url(board, bad):
    (board.dir / "ota.json").write_text(bad, encoding="utf-8")
    url, from_card = board.u._manifest_source()
    assert from_card is False and "github.com" in url


def test_a_card_that_cannot_be_read_at_all_falls_back(board):
    """No SD, or a mount that failed. The baked url is what makes a board
    straight off the flasher updatable with no host of the owner's."""
    board.card.fail = OSError(5)
    url, from_card = board.u._manifest_source()
    assert from_card is False and "github.com" in url


def test_a_board_with_no_channel_of_that_name_has_no_url(board):
    assert board.mod.default_manifest_url("nonesuch") is None
    assert board.u.manifest_url("nonesuch") is None


# == getting online ============================================================

class _Wifi:
    """The injected radio service. `status()[0]` is the truthy "connected"."""

    def __init__(self, up_after=0, error=None):
        self.up_after = up_after
        self.error = error
        self.asked = 0

    def status(self):
        self.asked += 1
        if self.error is not None:
            raise self.error
        return (self.asked > self.up_after, "192.168.1.5")


def test_a_network_that_comes_up_late_is_not_reported_offline(board, monkeypatch):
    """MEASURED on the P4 (2026-08-02, saved network, cold reset): connect()
    polls for 4s and gives up, and the link came up 1.5s AFTER it did. Without
    this wait a perfectly good network reads as "wifi offline"."""
    monkeypatch.setitem(sys.modules, "time", _fake_time())
    dialled = []
    board.u.set_wifi(_Wifi(up_after=6), lambda: dialled.append(1))
    assert board.u.ensure_online() is True
    assert dialled == [1]
    assert board.u.wifi_online() is True


def test_the_wait_is_bounded_and_gives_up(board, monkeypatch):
    """A console cannot sit behind a CHECKING screen forever."""
    fake = _fake_time()
    monkeypatch.setitem(sys.modules, "time", fake)
    board.u.set_wifi(_Wifi(up_after=10 ** 9), lambda: None)
    assert board.u.ensure_online() is False
    assert sum(fake.slept) <= board.mod.ONLINE_WAIT_MS


def test_an_already_connected_board_neither_dials_nor_waits(board, monkeypatch):
    fake = _fake_time()
    monkeypatch.setitem(sys.modules, "time", fake)
    dialled = []
    board.u.set_wifi(_Wifi(up_after=0), lambda: dialled.append(1))
    assert board.u.ensure_online() is True
    assert dialled == [] and fake.slept == []


def test_an_autoconnect_that_throws_still_gets_its_wait(board, monkeypatch):
    """The dial is best-effort. A radio that raises on connect may still be
    associating, and the wait is what finds out."""
    monkeypatch.setitem(sys.modules, "time", _fake_time())

    def _boom():
        raise OSError("ESP_ERR_WIFI_CONN")

    board.u.set_wifi(_Wifi(up_after=4), _boom)
    assert board.u.ensure_online() is True


def test_a_board_with_no_autoconnect_hook_still_waits(board, monkeypatch):
    monkeypatch.setitem(sys.modules, "time", _fake_time())
    board.u.set_wifi(_Wifi(up_after=3))
    assert board.u.ensure_online() is True


def test_a_micropython_without_sleep_ms_uses_plain_sleep(board, monkeypatch):
    """The host runs this module too, and CPython has no `time.sleep_ms`."""
    fake = _fake_time(sleep_ms=False)
    monkeypatch.setitem(sys.modules, "time", fake)
    board.u.set_wifi(_Wifi(up_after=3), lambda: None)
    assert board.u.ensure_online() is True
    assert fake.slept[:1] == [250]


def test_a_radio_that_throws_reads_as_offline(board):
    board.u.set_wifi(_Wifi(error=OSError("no netif")))
    assert board.u.wifi_online() is False


def test_no_radio_at_all_reads_as_offline(board):
    assert board.u.wifi_online() is False


# == the manifest check ========================================================

def test_check_online_reports_a_channel_with_no_url(board):
    board.u._manifest_source = lambda channel=None: (None, False)
    assert board.u.check_online() is None
    assert board.u.error == "no manifest url"


def test_check_online_reports_an_offline_board(board, monkeypatch):
    monkeypatch.setitem(sys.modules, "time", _fake_time())
    board.u.set_wifi(_Wifi(up_after=10 ** 9), lambda: None)
    assert board.u.check_online() is None
    assert board.u.error == "wifi offline"


def test_a_manifest_that_is_not_json_is_an_error_not_a_crash(board):
    board.u.ensure_online = lambda: True
    board.u._http_get_text = lambda url, limit=8192: "<html>404 not found</html>"
    assert board.u.check_online() is None
    assert board.u.error and "no manifest" not in board.u.error


def test_check_online_remembers_where_the_url_came_from(board):
    """The C6 radio updater rides this same fetch and asks the same question of
    the same manifest: was this reached from a baked url, or from the card?"""
    board.u.ensure_online = lambda: True
    board.u._manifest_source = lambda channel=None: ("http://h/l.json", True)
    board.u._http_get_text = lambda url, limit=8192: json.dumps(
        {"version": 99, "channel": "stable"})
    assert board.u.check_online() is not None
    assert board.u.from_card is True


def test_a_whole_manifest_check_over_the_scripted_network(board, monkeypatch):
    """The real `_http_open` -> `_http_get_text` -> `check_online` chain, with
    the GitHub shape it meets in the field: a 302 to the CDN and a header block
    thousands of bytes long."""
    body = json.dumps(dict(version=board.mod.FIRMWARE_VERSION + 1,
                           channel="stable", board="tdeck",
                           size=17, sha256="ab" * 32, url="http://h/fw.bin"))
    net = _FakeNet(
        _response(302, [b"Location: https://cdn.example/latest-tdeck.json", CSP]),
        _response(200, [b"Content-Length: %d" % len(body)], body.encode()))
    monkeypatch.setitem(sys.modules, "socket", net)
    monkeypatch.setitem(sys.modules, "ssl", net)
    board.mod.OTA_PUBLIC_KEYS = ()                    # an unsigned dev build
    board.u.ensure_online = lambda: True
    got = board.u.check_online()
    assert got["version"] == board.mod.FIRMWARE_VERSION + 1
    assert board.u.error is None
    assert net.made[0].closed and net.made[1].closed


# == the streamed download =====================================================

class _Body:
    """A socket serving a body in slices, recording what was asked for."""

    def __init__(self, data, slice_at=4096):
        self.data, self.pos, self.slice_at = data, 0, slice_at
        self.asks = []
        self.closed = False
        self.error = None

    def read(self, n):
        if self.error is not None:
            raise self.error
        self.asks.append(n)
        chunk = self.data[self.pos:self.pos + min(n, self.slice_at)]
        self.pos += len(chunk)
        return chunk

    def close(self):
        self.closed = True


def _arm_download(board, blob, rest=b"", size=None, sha=None, code=200,
                  slice_at=4096):
    import hashlib

    sock = _Body(blob[len(rest):], slice_at)
    board.u._http_open = lambda url, hops=4: (sock, code, len(blob), rest)
    manifest = {"url": "http://h/fw.bin",
                "size": len(blob) if size is None else size,
                "sha256": hashlib.sha256(blob).hexdigest() if sha is None else sha}
    return sock, manifest


def test_the_download_streams_to_storage_and_never_holds_the_image(board):
    """A 3MB image on a board with tens of KB of free internal SRAM. The file
    has to grow as the socket is read, not appear at the end."""
    blob = app_image(60000)
    sock, manifest = _arm_download(board, blob)
    board.u.begin_download(manifest)
    dest = board.dir / board.mod.DOWNLOAD_NAME
    sizes = []
    while board.u.download_step():
        sizes.append(dest.stat().st_size)
    assert board.u.download_finish() == str(dest)
    assert dest.read_bytes() == blob
    assert len(sizes) > 2 and sizes == sorted(sizes) and sizes[0] < len(blob)
    assert max(sock.asks) <= board.mod.DL_CHUNK
    # A step is bounded by the chunk, not by the socket: the frame loop has to
    # get its repaint back, and the whole point is that the image never sits
    # in RAM in one piece.
    assert sizes[0] <= board.mod.DL_CHUNK
    # The chunk bound is spelled TWICE -- once as the loop's condition, once as
    # the read's argument (`max_bytes - len(buf)`) -- and the two have to agree.
    # When they don't, the tell is a client asking a socket for zero or fewer
    # bytes, which MicroPython's `read` does not define an answer to.
    assert min(sock.asks) > 0


def test_download_step_says_true_while_more_remains(board):
    blob = app_image(40000)
    _sock, manifest = _arm_download(board, blob)
    board.u.begin_download(manifest)
    seen = []
    while True:
        more = board.u.download_step()
        seen.append(more)
        if not more:
            break
    assert seen[:-1] == [True] * (len(seen) - 1) and seen[-1] is False
    assert board.u.dl_done == len(blob)


def test_the_body_read_alongside_the_headers_is_not_lost(board):
    """The header reader stops ON the blank line, but a chunked read would have
    swallowed the first bytes of the image with them -- so whatever came back
    with the headers is fed in before the first socket read."""
    blob = app_image(9000)
    _sock, manifest = _arm_download(board, blob, rest=blob[:300])
    board.u.begin_download(manifest)
    assert board.u.dl_done == 300
    while board.u.download_step():
        pass
    assert board.u.download_finish() is not None
    assert (board.dir / board.mod.DOWNLOAD_NAME).read_bytes() == blob


def test_a_short_download_is_refused(board):
    """A server that closed early. The bytes are a valid prefix of a real
    image, so nothing downstream would notice."""
    blob = app_image(9000)
    sock, manifest = _arm_download(board, blob)
    board.u.begin_download(manifest)
    sock.data = sock.data[:2000]                    # the connection drops
    while board.u.download_step():
        pass
    assert board.u.download_finish() is None
    assert board.u.error.startswith("size ")


def test_a_corrupted_download_is_refused_by_the_signed_hash(board):
    """The bytes are pinned by the sha256 the SIGNATURE covers, which is what
    makes an unsigned url (a LAN mirror) safe to point a board at."""
    board.mod.OTA_PUBLIC_KEYS = TEST_KEYS
    blob = app_image(9000)
    manifest = dict(board=board.mod.BOARD, channel="stable",
                    version=board.mod.FIRMWARE_VERSION + 1, size=len(blob),
                    sha256=__import__("hashlib").sha256(blob).hexdigest(),
                    url="http://h/fw.bin")
    manifest["sig"] = sign_with_test_key(manifest)
    assert board.u.verify_manifest(manifest, TEST_KEYS) is True

    tampered = bytearray(blob)
    tampered[500] ^= 0xFF                            # same length, other bytes
    sock, _m = _arm_download(board, bytes(tampered))
    board.u._http_open = lambda url, hops=4: (sock, 200, len(blob), b"")
    board.u.begin_download(manifest)
    while board.u.download_step():
        pass
    assert board.u.download_finish() is None
    assert board.u.error == "sha256 mismatch"


def test_a_manifest_with_no_url_cannot_start_a_download(board):
    with pytest.raises(ValueError):
        board.u.begin_download({"size": 10})


def test_a_refused_download_closes_the_socket_rather_than_leaking_it(board):
    """One socket is all the RAM budget allows for; a leaked one is the next
    check failing for no visible reason."""
    blob = app_image(100)
    sock, manifest = _arm_download(board, blob, code=403)
    with pytest.raises(ValueError) as exc:
        board.u.begin_download(manifest)
    assert "403" in str(exc.value)
    assert sock.closed is True


def test_the_size_falls_back_to_content_length(board):
    """A manifest without a size still gets a progress bar and a completeness
    check, off the response's own Content-Length."""
    blob = app_image(5000)
    _sock, manifest = _arm_download(board, blob, size=0)
    board.u.begin_download(manifest)
    assert board.u.dl_total == len(blob)


def test_a_read_failure_mid_stream_closes_everything(board):
    blob = app_image(40000)
    sock, manifest = _arm_download(board, blob)
    board.u.begin_download(manifest)
    board.u.download_step()
    sock.error = OSError(104)                        # ECONNRESET
    assert board.u.download_step() is False
    assert board.u.error == "OSError 104"
    assert sock.closed and board.u._dl_f is None


def test_a_storage_failure_mid_stream_closes_everything(board):
    """A card pulled mid-download, or a full internal filesystem."""
    blob = app_image(40000)
    sock, manifest = _arm_download(board, blob)
    board.u.begin_download(manifest)
    board.card.fail = OSError(28)                    # ENOSPC
    assert board.u.download_step() is False
    assert board.u.error == "OSError 28"
    assert sock.closed is True


def test_a_download_step_before_a_download_reports_nothing(board):
    assert board.u.download_step() is False


def test_cancelling_a_download_clears_the_bar(board):
    blob = app_image(40000)
    sock, manifest = _arm_download(board, blob)
    board.u.begin_download(manifest)
    board.u.download_step()
    board.u.download_cancel()
    assert (board.u.dl_done, board.u.dl_total) == (0, 0)
    assert sock.closed and board.u._sock is None and board.u._dl_f is None


def test_a_download_finished_over_a_dead_card_still_answers(board):
    """The close is best-effort through the SD wrapper, with a plain close as
    the fallback -- an unclosable file must not lose a good download."""
    blob = app_image(5000)
    _sock, manifest = _arm_download(board, blob)
    board.u.begin_download(manifest)
    while board.u.download_step():
        pass
    board.card.fail = OSError(5)
    assert board.u.download_finish() == str(board.dir / board.mod.DOWNLOAD_NAME)


def test_a_manifest_with_no_hash_is_accepted_on_size_alone(board):
    """The LAN dev loop's `make ota-publish-unstable` writes one; there is
    nothing to check it against, and refusing would break a key-free flow the
    signature policy deliberately keeps open."""
    blob = app_image(3000)
    _sock, manifest = _arm_download(board, blob, sha="")
    board.u.begin_download(manifest)
    while board.u.download_step():
        pass
    assert board.u.download_finish() is not None


def test_the_download_creates_its_staging_directory(board, tmp_path):
    """The P4 and the Guition stage on internal flash, where /moy/update does
    not exist until the first update."""
    board.u.update_dir = str(tmp_path / "fresh" / "update")
    blob = app_image(2000)
    _sock, manifest = _arm_download(board, blob)
    with pytest.raises(OSError):
        board.u.begin_download(manifest)          # the PARENT is missing too
    board.u.update_dir = str(tmp_path / "made")
    _sock, manifest = _arm_download(board, blob)
    board.u.begin_download(manifest)
    assert Path(board.u.update_dir).is_dir()
    board.u.download_cancel()


# == the small HTTP client =====================================================

def test_a_non_200_manifest_fetch_names_the_status(board, monkeypatch):
    """`check_online` branches on exactly this string: 404/410 mean "nothing
    published for this board yet", anything else is a real failure."""
    net = _FakeNet(_response(404, [b"Content-Length: 9"], b"not found"))
    monkeypatch.setitem(sys.modules, "socket", net)
    monkeypatch.setitem(sys.modules, "ssl", net)
    assert board.u._http_get_text("https://h/latest.json") is None
    assert board.u.error == "http 404"
    assert net.made[0].closed is True


def test_the_manifest_socket_is_closed_on_the_way_out(board, monkeypatch):
    body = b'{"version": 9}'
    net = _FakeNet(_response(200, [b"Content-Length: %d" % len(body)], body))
    monkeypatch.setitem(sys.modules, "socket", net)
    monkeypatch.setitem(sys.modules, "ssl", net)
    assert board.u._http_get_text("http://h/l.json") == body.decode()
    assert net.made[0].closed is True


def test_an_oversized_manifest_stops_at_the_cap(board, monkeypatch):
    """A manifest is ~850 bytes. A server announcing megabytes of it -- by
    accident or otherwise -- must not be pulled into a board with tens of KB of
    free internal SRAM, so the cap overrides the Content-Length rather than
    trusting it."""
    net = _FakeNet(_response(200, [b"Content-Length: 400000"], b"x" * 400000))
    monkeypatch.setitem(sys.modules, "socket", net)
    monkeypatch.setitem(sys.modules, "ssl", net)
    txt = board.u._http_get_text("http://h/l.json", limit=2048)
    assert 2048 <= len(txt) <= 2048 + 512


@pytest.mark.parametrize("url, want", [
    ("https://h/a/b.json", ("https", "h", 443, "/a/b.json")),
    ("http://h/a", ("http", "h", 80, "/a")),
    ("https://h:8443/x", ("https", "h", 8443, "/x")),
    ("http://h", ("http", "h", 80, "/")),
])
def test_the_url_parser_answers_the_shapes_a_manifest_can_carry(board, url, want):
    assert board.u._parse_url(url) == want


def test_a_url_with_no_scheme_is_refused(board):
    """It comes out of a JSON file a human may have typed."""
    for bad in ("h/a", "ftp://h/a", ""):
        with pytest.raises(ValueError):
            board.u._parse_url(bad)


# -- what a hostile or broken server gets to try -------------------------------
#
# The header reader is a hand-rolled byte-at-a-time loop on a board with tens of
# KB of free internal SRAM, and it runs before anything has been verified. Every
# case here is "whatever came back off the wire" reaching it.

def test_a_header_block_past_the_cap_is_abandoned_not_read(board, monkeypatch):
    """The cap is 16K because GitHub's release redirect measured 5147 bytes,
    3626 of it one Content-Security-Policy header. A server that never sends
    the blank line must not be followed into the board's RAM."""
    endless = _response(200, [b"X-Pad: " + b"y" * 60000], b"body")
    net = _FakeNet(endless)
    monkeypatch.setitem(sys.modules, "socket", net)
    monkeypatch.setitem(sys.modules, "ssl", net)
    _sock, code, _clen, _rest = board.u._http_open("http://h/l.json")
    assert code == 200
    assert net.made[0].pos < 20000, "the reader kept going past its own cap"


def test_a_response_that_dies_mid_headers_does_not_hang(board, monkeypatch):
    """The socket closes with no blank line ever arriving -- the shape of a
    dropped WiFi association mid-check."""
    net = _FakeNet(b"HTTP/1.1 200 OK\r\nContent-Length: 900\r\n")
    monkeypatch.setitem(sys.modules, "socket", net)
    monkeypatch.setitem(sys.modules, "ssl", net)
    board.u.ensure_online = lambda: True
    board.u._manifest_source = lambda channel=None: ("http://h/l.json", True)
    assert board.u.check_online() is None
    assert board.u.error                       # reported, not raised


@pytest.mark.parametrize("raw, code", [
    (b"garbage\r\n\r\nbody", 0),                       # no status line at all
    (b"HTTP/1.1 nope OK\r\n\r\n", 0),                  # a status that is not a number
    (b"\r\n\r\n", 0),                                  # nothing but the blank line
    (b"HTTP/1.1 200 OK\r\nContent-Length: abc\r\n\r\nxy", 200),
    (b"HTTP/1.1 302 F\r\nLocation:\r\n\r\n", 302),     # a redirect to nowhere
])
def test_a_malformed_response_is_parsed_without_raising(board, monkeypatch,
                                                        raw, code):
    """A crash in the header parser is a denied update path, and it happens
    before a signature has been anywhere near the data."""
    net = _FakeNet(raw)
    monkeypatch.setitem(sys.modules, "socket", net)
    monkeypatch.setitem(sys.modules, "ssl", net)
    sock, got, clen, _rest = board.u._http_open("http://h/l.json")
    assert got == code and clen == 0
    sock.close()


# == the whole chain ===========================================================

def test_an_online_update_from_the_manifest_to_the_next_boot(board, monkeypatch):
    """Everything a real update does except the flash cells and the reboot:
    check the signed manifest, stream the image to storage, verify it, flash
    the INACTIVE slot, point the bootloader at it, reboot -- then come back up
    on the new slot and confirm from the frame loop."""
    import hashlib

    m = board.mod
    m.OTA_PUBLIC_KEYS = TEST_KEYS
    machine = _install_machine(monkeypatch)
    blob = app_image(70000)
    manifest = dict(board=m.BOARD, channel="stable",
                    version=m.FIRMWARE_VERSION + 1, size=len(blob),
                    sha256=hashlib.sha256(blob).hexdigest(),
                    url="http://h/fw.bin", label="0.9")
    manifest["sig"] = sign_with_test_key(manifest)

    board.u.ensure_online = lambda: True
    board.u._http_get_text = lambda url, limit=8192: json.dumps(manifest)
    got = board.u.check_online()
    assert got is not None and board.u.offers(got, "stable") is True

    sock = _Body(blob)
    board.u._http_open = lambda url, hops=4: (sock, 200, len(blob), b"")
    board.u.begin_download(got)
    while board.u.download_step():
        pass
    path = board.u.download_finish()
    assert path is not None

    board.u.begin(path)
    _run_install(board.u)
    assert board.u.finish() is True
    assert board.esp.other.image()[:len(blob)] == blob
    assert board.esp.running.blocks == {}
    assert not Path(path).exists(), "the consumed payload was hoarded"
    board.u.reset()
    assert machine.resets == [1]

    # ...the bootloader jumps to ota_1 and the new image comes up.
    nxt = m.OtaUpdater(board.card, update_dir=str(board.dir))
    esp2 = _Esp32(running="ota_1", other="ota_0", card=board.card)
    _install_esp32(monkeypatch, esp2)
    assert nxt.boot_check()[0] == "ok"
    fired = [nxt.confirm_when_healthy(1) for _ in range(m.HEALTHY_LOOPS)]
    assert fired.count(True) == 1
    assert esp2.marked == 1
    assert not Path(nxt._pending_path()).exists()


# == helpers ===================================================================

def test_an_error_string_keeps_the_class_of_a_bare_errno(board):
    """An OSError's str() is often just "113", which on the glass reads as a
    number with no noun. The screen has room for 30 characters."""
    m = board.mod
    assert m._short(OSError(113)) == "OSError 113"
    assert m._short(ValueError("2 big")) == "ValueError 2 big"
    assert m._short(RuntimeError("")) == "RuntimeError"
    assert m._short(ValueError("not an app image")) == "not an app image"
    assert len(m._short(OSError("x" * 200))) == 48


def test_the_clock_helpers_answer_on_both_pythons(board, monkeypatch):
    """`_ms`/`_ms_since` exist because this module is imported by the host
    tests, so every device call needs a CPython answer too."""
    m = board.mod
    start = m._ms()
    assert isinstance(m._ms_since(start), int)

    fake = _fake_time(ticks=True)
    monkeypatch.setitem(sys.modules, "time", fake)
    t0 = m._ms()
    assert t0 == 1000
    fake.now += 37
    assert m._ms_since(t0) == 37


def test_logging_can_never_break_an_update(board, capsys):
    """The serial trace is the only window into the WiFi path on a board whose
    RX is dead under the desktop -- and a print that throws must not be what
    ends an install."""
    class _Explodes:
        def __str__(self):
            raise RuntimeError("nope")

    board.mod._log("fine")
    board.mod._log("bad", _Explodes())
    assert "Moybyte OTA: fine" in capsys.readouterr().out


# -- streaming straight into the slot ---------------------------------------
#
# The Zero could not update at all: its download staged to a FILE on a 2.38MB
# filesystem with ~180KB free, for a 2.27MB image, and died with OSError 28
# (ENOSPC) 184KB in. It could not have fit on an EMPTY volume. The inactive slot
# is 2.75MB and empty, which is what it is for -- so the bytes go there as they
# come off the wire. Half the flash writes on every other board too, since the
# staged path writes every byte twice.


class _Wire:
    """A socket that hands the body over in the sizes a network actually uses --
    never respecting a 4K page boundary, which is the whole reason there is a
    buffer between the wire and the partition."""

    def __init__(self, payload, sizes):
        self.buf = payload
        self.sizes = list(sizes)
        self.pos = 0
        self.closed = False

    def read(self, n):
        if self.pos >= len(self.buf):
            return b""
        take = self.sizes.pop(0) if self.sizes else n
        take = min(take, n, len(self.buf) - self.pos)
        out = self.buf[self.pos:self.pos + take]
        self.pos += take
        return out

    def close(self):
        self.closed = True


def _slot_board(tmp_path, monkeypatch, payload, sizes=(1, 4095, 700, 9000, 33)):
    """A board whose download goes to the SLOT, with `payload` on the wire."""
    import hashlib

    mod = _fresh()
    esp = _Esp32()
    _install_esp32(monkeypatch, esp)
    d = tmp_path / "update"
    d.mkdir(exist_ok=True)
    u = mod.OtaUpdater(lambda fn: fn(), update_dir=str(d))
    wire = _Wire(payload, sizes)
    u._http_open = lambda url, hops=4: (wire, 200, len(payload), b"")
    manifest = {"url": "https://x/f.bin", "size": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest()}
    return mod, u, esp, manifest


def _drain(u):
    while u.download_step():
        pass
    return u.download_finish()


def test_a_slot_download_reassembles_the_image_byte_for_byte(tmp_path, monkeypatch):
    """Socket reads arrive in whatever size the network gives; partition writes
    are 4K-aligned. The buffer between them is where an off-by-one silently
    corrupts an image that then does not boot."""
    import os as _os

    payload = _os.urandom(4096 * 3 + 1234)      # three whole pages and a short tail
    mod, u, esp, manifest = _slot_board(tmp_path, monkeypatch, payload)

    u.begin_download(manifest, to_slot=True)
    assert _drain(u) == mod.SLOT_STAGED, u.error

    img = esp.other.image()
    assert len(img) == 4096 * 4, "the tail must be written as a whole page"
    assert img[:len(payload)] == payload, "the image is not what arrived"
    assert set(img[len(payload):]) == {0xFF}, (
        "the tail is padded with something other than erased flash")
    # ...and NOTHING was staged on the filesystem, which is the entire point.
    assert not list((tmp_path / "update").iterdir()), \
        "a staging file was written after all -- on the Zero this is ENOSPC"


def test_a_slot_download_needs_no_second_pass_to_install(tmp_path, monkeypatch):
    """The bytes are already in the slot, so the install phase is the set_boot
    and nothing else. `step()` must report 'no work' rather than trying to read
    a file that was never opened."""
    # A REAL app image, not random bytes: set_boot validates the header on a
    # board and the flash double does too, which is the point of that double.
    payload = app_image(4096 * 2)
    mod, u, esp, manifest = _slot_board(tmp_path, monkeypatch, payload)
    u.begin_download(manifest, to_slot=True)
    assert _drain(u) == mod.SLOT_STAGED, u.error

    assert u.staged_in_slot() is True
    assert u.step() is False, "the install tried to flash an image already in place"
    assert u.finish() is True, u.error
    assert esp.other.booted is True


def test_a_slot_download_that_fails_its_hash_cannot_be_activated(tmp_path, monkeypatch):
    """The guard that matters. `finish()` activates whatever partition handle it
    finds, and a download that failed verification has left a PARTIAL image in
    the slot -- so a retry, or any stray finish(), would set_boot an image we
    just proved was wrong. Rollback would catch it; a guard leaning on the last
    line of defence is not a guard."""
    payload = b"z" * 8192
    mod, u, esp, manifest = _slot_board(tmp_path, monkeypatch, payload)
    manifest["sha256"] = "00" * 32              # never going to match

    u.begin_download(manifest, to_slot=True)
    assert _drain(u) is None
    assert "sha256" in (u.error or "")
    assert u.staged_in_slot() is False
    assert u.finish() is False, "a failed download stayed activatable"
    assert esp.other.booted is False, "set_boot ran on an image that failed its hash"


def test_an_image_too_big_for_the_slot_is_refused_before_the_transfer(tmp_path,
                                                                     monkeypatch):
    """Caught at begin, not discovered at the end of a transfer that could never
    have fit -- and the socket is closed rather than left open."""
    payload = b"q" * 4096
    mod, u, esp, manifest = _slot_board(tmp_path, monkeypatch, payload)
    esp.other.size = 1024
    manifest["size"] = 999999

    with pytest.raises(ValueError) as e:
        u.begin_download(manifest, to_slot=True)
    assert "slot" in str(e.value)
    assert u._sock is None, "the socket was left open on the refusal path"


def test_the_staged_file_path_still_works(tmp_path, monkeypatch):
    """The other half of the seam. UPDATE FW installs a .bin a human put on a
    card, and that path writes a file and flashes it in a second pass -- which
    is what boards with room still do, and what must not have moved."""
    import os as _os

    payload = _os.urandom(5000)
    mod, u, esp, manifest = _slot_board(tmp_path, monkeypatch, payload)

    u.begin_download(manifest)                  # to_slot defaults False
    got = _drain(u)
    assert got and got.endswith("firmware.bin"), u.error
    assert u.staged_in_slot() is False
    with open(got, "rb") as f:
        assert f.read() == payload
