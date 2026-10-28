"""`tools/board_flash.py` -- the cable-flash ORDER, and the per-board facts.

Never imported by anything (#208): the Makefile shells out to it, so the one
thing the tool exists to own -- otadata erased FIRST, the merged image second --
had no executable guard at all. A board that has taken an OTA runs from ota_1,
and a flash that writes ota_0 without clearing otadata boots the stale slot: it
looks exactly like a flash that did nothing. `tests/test_board_toml.py` checks
the [flash] DATA; this file runs the tool over it.

esptool is stubbed to a recorder, so every assertion is on the sequence of
commands the tool would have run, not on a return value.
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import p4_autotest                                              # noqa: E402
from tools import board_config, board_flash                     # noqa: E402

TDECK = ROOT / "firmware" / "lilygo_t_deck_plus_mainline"
P4 = ROOT / "firmware" / "esp32_p4_wifi6_touch_lcd_7b"
GUITION = ROOT / "firmware" / "guition_jc3248w535"
GUITION_P4 = ROOT / "firmware" / "guition_jc8012p4a1c"
WEB_RUNNER = ROOT / "firmware" / "web_runner"
BOARDS = {"tdeck": TDECK, "p4": P4, "guition-s3": GUITION,
          "guition-p4": GUITION_P4}

SUBCOMMANDS = ("erase_region", "write_flash", "read_flash")


class _Esptool:
    """Stands in for `subprocess` inside board_flash: records each argv and
    answers a scripted return code."""

    def __init__(self, codes=()):
        self.calls = []
        self._codes = list(codes)

    def call(self, cmd):
        self.calls.append(list(cmd))
        return self._codes.pop(0) if self._codes else 0


def _verbs(calls):
    return [next(a for a in c if a in SUBCOMMANDS) for c in calls]


def _arm(monkeypatch, tmp_path, board_dir, codes=(), image=True):
    """Point board_flash at a throwaway ROOT with a stub esptool, and put the
    board's declared image where it says it lives."""
    fake = _Esptool(codes)
    monkeypatch.setattr(board_flash, "subprocess", fake)
    monkeypatch.setattr(board_flash, "ROOT", tmp_path)
    fl = board_config.load(board_dir).get("flash")
    if image and fl:
        path = tmp_path / fl["image"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\xe9 an app image")
    return fake


def _flash(monkeypatch, tmp_path, board_dir, codes=(), image=True, **kw):
    fake = _arm(monkeypatch, tmp_path, board_dir, codes, image)
    kw.setdefault("verify", False)
    return board_flash.flash(str(board_dir), "/dev/fake", **kw), fake


def test_the_otadata_erase_runs_before_the_image_write(monkeypatch, tmp_path):
    """The invariant the tool exists for. Reversed, an OTA'd board writes ota_0
    and keeps booting the stale ota_1."""
    _rc, fake = _flash(monkeypatch, tmp_path, TDECK)
    assert _verbs(fake.calls) == ["erase_region", "write_flash"]


def test_the_erase_leaves_the_board_unreset_and_the_write_restarts_it(
        monkeypatch, tmp_path):
    """An erase that reset the board would leave it running from a cleared
    otadata before the image lands; the write is the step that restarts it, so
    the board leaves the cable running the slot just written."""
    _rc, fake = _flash(monkeypatch, tmp_path, P4)
    erase, write = fake.calls
    assert erase[erase.index("--after") + 1] == "no_reset"
    assert write[write.index("--after") + 1] == "hard_reset"


@pytest.mark.parametrize("board", sorted(BOARDS))
def test_every_flash_argument_comes_from_the_board_file(
        monkeypatch, tmp_path, board):
    """No fact is restated in the tool: chip, baud, both offsets, the otadata
    size and the image path all arrive from board.toml."""
    board_dir = BOARDS[board]
    cfg = board_config.load(board_dir)
    fl = cfg["flash"]
    _rc, fake = _flash(monkeypatch, tmp_path, board_dir)
    erase, write = fake.calls
    for cmd in (erase, write):
        assert cmd[:3] == [sys.executable, "-m", "esptool"]
        assert cmd[cmd.index("--chip") + 1] == cfg["board"]["chip"]
        assert cmd[cmd.index("--port") + 1] == "/dev/fake"
        assert cmd[cmd.index("--baud") + 1] == str(fl["baud"])
    assert erase[-3:] == ["erase_region", fl["otadata_offset"],
                          fl["otadata_size"]]
    assert write[-3:] == ["write_flash", fl["offset"],
                          str(tmp_path / fl["image"])]


def test_the_tdeck_alone_asks_for_the_usb_reset_it_measured(
        monkeypatch, tmp_path):
    """Measured 2026-08-17: `default_reset` write-times-out against a wedged
    USB-Serial/JTAG node while `usb_reset` connects. The P4's CH343 declares no
    `before` and must not grow one here."""
    _rc, tdeck = _flash(monkeypatch, tmp_path, TDECK)
    write = tdeck.calls[1]
    assert write[write.index("--before") + 1] == "usb_reset"
    assert "--before" not in tdeck.calls[0]
    _rc, p4 = _flash(monkeypatch, tmp_path, P4)
    assert "--before" not in p4.calls[1]


def test_a_failed_erase_stops_before_the_image_is_written(
        monkeypatch, tmp_path):
    """Writing over a failed erase produces the exact outcome the erase is
    there to prevent, and esptool's own exit code is the only warning."""
    rc, fake = _flash(monkeypatch, tmp_path, P4, codes=[1])
    assert rc == 1 and _verbs(fake.calls) == ["erase_region"]


def test_a_missing_image_refuses_instead_of_flashing(monkeypatch, tmp_path):
    fake = _arm(monkeypatch, tmp_path, GUITION, image=False)
    with pytest.raises(SystemExit) as exc:
        board_flash.flash(str(GUITION), "/dev/fake", verify=False)
    assert "build it first" in str(exc.value) and fake.calls == []


def test_a_board_with_no_flash_section_is_not_flashable(monkeypatch, tmp_path):
    """`firmware/web_runner` is a real board file with no cable to flash."""
    fake = _arm(monkeypatch, tmp_path, WEB_RUNNER)
    with pytest.raises(SystemExit) as exc:
        board_flash.flash(str(WEB_RUNNER), "/dev/fake", verify=False)
    assert "no [flash] section" in str(exc.value) and fake.calls == []


def test_a_board_declaring_no_otadata_region_writes_only_the_image(
        monkeypatch, tmp_path):
    """The erase is driven by the declaration, so a board with no otadata pair
    cannot have some other board's offset erased on it."""
    board_dir = tmp_path / "firmware" / "single_slot"
    board_dir.mkdir(parents=True)
    (board_dir / "board.toml").write_text(
        '[board]\nchip = "esp32s3"\nota = "single_slot"\n\n'
        '[flash]\nimage = "dist/single_slot/app.bin"\n'
        'offset = "0x0"\nbaud = 460800\n', encoding="utf-8")
    fake = _arm(monkeypatch, tmp_path, board_dir)
    board_flash.flash(str(board_dir), "/dev/fake", verify=False)
    assert _verbs(fake.calls) == ["write_flash"]


def test_monitor_opens_miniterm_at_the_declared_baud(monkeypatch, tmp_path):
    fake = _Esptool()
    monkeypatch.setattr(board_flash, "subprocess", fake)
    board_flash.monitor(str(P4), "/dev/fake")
    assert fake.calls == [[sys.executable, "-m", "serial.tools.miniterm",
                           "/dev/fake",
                           str(board_config.load(P4)["monitor"]["baud"])]]


# -- the pre-flash identity check --------------------------------------------
#
# The two S3 boards are the same chip behind the same usb id, so esptool's own
# probe cannot tell them apart and a T-Deck image on the Guition is a valid
# flash of the wrong firmware.


class _Identity:
    """The p4_autotest driver `_verify_identity` opens, reduced to the four
    members it touches. Instances are used AS the class."""

    def __init__(self, answer):
        self.answer = answer
        self.opened = []
        self.closed = 0

    def __call__(self, port, board_dir=None):
        self.opened.append((port, board_dir))
        return self

    def drain(self, secs):
        pass

    def identify(self, timeout=None):
        return self.answer

    def close(self):
        self.closed += 1


def test_a_board_that_answers_as_another_board_is_never_flashed(
        monkeypatch, tmp_path):
    ident = _Identity("guition_s3")
    monkeypatch.setattr(p4_autotest, "P4Board", ident)
    fake = _arm(monkeypatch, tmp_path, TDECK)
    with pytest.raises(SystemExit) as exc:
        board_flash.flash(str(TDECK), "/dev/fake")
    assert "guition_s3" in str(exc.value) and "tdeck" in str(exc.value)
    assert fake.calls == [] and ident.closed == 1


def test_a_board_that_confirms_its_identity_is_flashed(monkeypatch, tmp_path):
    ident = _Identity("tdeck")
    monkeypatch.setattr(p4_autotest, "P4Board", ident)
    fake = _arm(monkeypatch, tmp_path, TDECK)
    assert board_flash.flash(str(TDECK), "/dev/fake") == 0
    assert _verbs(fake.calls) == ["erase_region", "write_flash"]
    assert ident.closed == 1


def test_a_board_that_does_not_answer_is_flashed_anyway(monkeypatch, tmp_path):
    """A wedged board is this tool's ordinary customer: only a POSITIVE
    mismatch may refuse."""
    ident = _Identity(None)
    monkeypatch.setattr(p4_autotest, "P4Board", ident)
    _rc, fake = _flash(monkeypatch, tmp_path, TDECK, verify=True)
    assert _verbs(fake.calls) == ["erase_region", "write_flash"]
    assert ident.opened == [("/dev/fake", str(TDECK))]


def test_a_probe_that_raises_is_flashed_anyway(monkeypatch, tmp_path):
    """An attach_only S3 can drop its device node mid-probe while it
    re-enumerates -- the same wedged board, arriving as an exception instead of
    a silence. It must degrade the same way, and still close the port."""
    ident = _Identity(None)

    def _boom(timeout=None):
        raise OSError("[Errno 5] Input/output error")

    ident.identify = _boom
    monkeypatch.setattr(p4_autotest, "P4Board", ident)
    _rc, fake = _flash(monkeypatch, tmp_path, TDECK, verify=True)
    assert _verbs(fake.calls) == ["erase_region", "write_flash"]
    assert ident.closed == 1


def test_the_p4_is_never_opened_to_be_asked(monkeypatch, tmp_path):
    """Only attach_only boards are interrogated -- opening the CH343 reboots
    the board, and esptool's chip probe already guards a P4 image."""
    monkeypatch.setattr(p4_autotest, "P4Board", lambda *a, **k: pytest.fail(
        "opened a board whose open is not side-effect free"))
    _rc, fake = _flash(monkeypatch, tmp_path, P4, verify=True)
    assert _verbs(fake.calls) == ["erase_region", "write_flash"]


def test_main_routes_the_verbs_and_honours_no_verify(monkeypatch, tmp_path):
    seen = []
    monkeypatch.setattr(board_flash, "_verify_identity",
                        lambda *a: seen.append(a))
    fake = _arm(monkeypatch, tmp_path, TDECK)
    argv = ["board_flash.py", "flash", str(TDECK), "--port", "/dev/fake"]
    assert board_flash.main(argv + ["--no-verify"]) == 0
    assert seen == [] and _verbs(fake.calls) == ["erase_region", "write_flash"]
    assert board_flash.main(argv) == 0
    assert seen == [(str(TDECK), "/dev/fake")]
    assert board_flash.main(["board_flash.py", "monitor", str(TDECK),
                             "--port", "/dev/fake"]) == 0
    assert "miniterm" in fake.calls[-1][2]


def test_the_no_modem_wrapper_keeps_flow_control_off_and_splits_dtr_rts():
    """`tools/esptool_no_modem.py`: some S3 native USB serial nodes accept reads
    but reject the combined modem-control ioctls, so the wrapper opens every
    port with flow control off, makes pyserial's DTR/RTS state updates no-ops,
    and replaces esptool's combined RTS/DTR update with two separate calls.
    Imported against stubbed `serial`/`esptool` modules."""
    import importlib.util
    import types

    opened = []
    serial = types.ModuleType("serial")
    serialposix = types.ModuleType("serial.serialposix")

    class _Serial:
        pass

    serialposix.Serial = _Serial
    serial.serialposix = serialposix
    serial.serial_for_url = lambda *a, **kw: opened.append((a, kw)) or "port"
    esptool = types.ModuleType("esptool")
    reset = types.ModuleType("esptool.reset")

    class _Strategy:
        pass

    reset.ResetStrategy = _Strategy
    esptool.reset = reset
    esptool._main = lambda: 0
    stubs = {"serial": serial, "serial.serialposix": serialposix,
             "esptool": esptool, "esptool.reset": reset}
    saved = {k: sys.modules.get(k) for k in stubs}
    sys.modules.update(stubs)
    try:
        spec = importlib.util.spec_from_file_location(
            "esptool_no_modem", ROOT / "tools" / "esptool_no_modem.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        assert serial.serial_for_url("/dev/x", 115200, rtscts=True) == "port"
        assert opened == [(("/dev/x", 115200), {"rtscts": False, "dsrdtr": False})]
        assert _Serial._update_dtr_state(None) is None
        assert _Serial._update_rts_state(None) is None

        class _Port:
            def __init__(self):
                self.seq = []

            def setDTR(self, v):
                self.seq.append(("dtr", v))

            def setRTS(self, v):
                self.seq.append(("rts", v))

        strat = _Strategy()
        strat.port = _Port()
        _Strategy._setDTRandRTS(strat, dtr=True, rts=False)
        assert strat.port.seq == [("dtr", True), ("rts", False)]
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v


# ---- the store follows the table -------------------------------------------
#
# A filesystem does not mount at another offset or size, and `inisetup` only
# formats a first sector that reads all 0xFF: a cable flash that moves the store
# and leaves the old bytes there prints "filesystem appears to be corrupted" on
# every boot. These run the comparison on binary tables and the flash over it.

_BASE = [("nvs", 1, 2, 0x9000, 0x4000), ("otadata", 1, 0, 0xD000, 0x2000),
         ("phy_init", 1, 1, 0xF000, 0x1000)]
SLOTS_4M = _BASE + [("ota_0", 0, 0x10, 0x10000, 0x400000),
                    ("ota_1", 0, 0x11, 0x410000, 0x400000)]
SLOTS_6M = _BASE + [("ota_0", 0, 0x10, 0x10000, 0x600000),
                    ("ota_1", 0, 0x11, 0x610000, 0x600000)]
LISTED = SLOTS_4M + [("vfs", 1, 0x81, 0x810000, 0x7F0000)]


def _table(entries, md5=True):
    """A partition table sector as it sits at 0x8000: the entries, an MD5
    record, then erased flash."""
    blob = b"".join(
        board_flash._ENTRY.pack(b"\xaa\x50", typ, sub, off, size,
                                label.encode(), 0)
        for label, typ, sub, off, size in entries)
    if md5:
        blob += b"\xeb\xeb" + b"\xff" * 14 + b"\x00" * 16
    return blob.ljust(board_flash.PT_SIZE, b"\xff")


def test_the_store_of_a_table_with_no_vfs_is_the_tail_after_the_last_partition():
    assert board_flash.store_of(board_flash.parse_table(_table(SLOTS_4M))) \
        == (0x810000, None)
    assert board_flash.store_of(board_flash.parse_table(_table(SLOTS_6M))) \
        == (0xC10000, None)


def test_a_listed_vfs_is_the_store_and_a_blank_table_has_none():
    assert board_flash.store_of(board_flash.parse_table(_table(LISTED))) \
        == (0x810000, 0x7F0000)
    assert board_flash.store_of(board_flash.parse_table(b"\xff" * 0xC00)) is None


def test_a_table_that_moves_the_store_is_reported_with_the_new_place():
    old, new = _table(SLOTS_4M), _table(SLOTS_6M)
    assert board_flash.moved_store(old, new) == (0xC10000, None)
    assert board_flash.moved_store(b"\xff" * 0xC00, new) == (0xC10000, None)
    # the same offset at another size is another filesystem too
    resized = _table(SLOTS_4M + [("vfs", 1, 0x81, 0x810000, 0x400000)])
    assert board_flash.moved_store(_table(LISTED), resized) \
        == (0x810000, 0x400000)


def test_a_table_that_leaves_the_store_alone_is_not_reported():
    assert board_flash.moved_store(_table(SLOTS_6M), _table(SLOTS_6M)) is None
    assert board_flash.moved_store(_table(LISTED), _table(LISTED)) is None
    # a relabelled nvs does not touch the filesystem, and the md5 differs
    renamed = [("nvs2",) + SLOTS_6M[0][1:]] + SLOTS_6M[1:]
    assert board_flash.moved_store(_table(SLOTS_6M), _table(renamed, md5=False)) \
        is None


class _Board(_Esptool):
    """esptool with a board behind it: `read_flash` writes the table the board
    holds to the file named last."""

    def __init__(self, table, codes=()):
        super().__init__(codes)
        self.table = table

    def call(self, cmd):
        rc = super().call(cmd)
        if "read_flash" in cmd and not rc:
            Path(cmd[-1]).write_bytes(self.table)
        return rc


def _flash_over(monkeypatch, tmp_path, board_table, image_entries, codes=()):
    """Flash the P4 with an image whose table is `image_entries` onto a board
    holding `board_table`."""
    fake = _Board(board_table, codes)
    monkeypatch.setattr(board_flash, "subprocess", fake)
    monkeypatch.setattr(board_flash, "ROOT", tmp_path)
    fl = board_config.load(P4)["flash"]
    path = tmp_path / fl["image"]
    path.parent.mkdir(parents=True, exist_ok=True)
    at = board_flash.PT_OFFSET - int(fl["offset"], 0)
    path.write_bytes((b"\xe9" * at) + _table(image_entries) + b"\xe9" * 64)
    return board_flash.flash(str(P4), "/dev/fake", verify=False), fake


def test_a_flash_that_moves_the_store_erases_its_first_blocks_before_it_writes(
        monkeypatch, tmp_path, capsys):
    rc, fake = _flash_over(monkeypatch, tmp_path, _table(SLOTS_4M), SLOTS_6M)
    assert rc == 0
    assert _verbs(fake.calls) == ["read_flash", "erase_region", "erase_region",
                                  "write_flash"]
    read, otadata, store, write = fake.calls
    assert read[-3:-1] == ["0x8000", "0xc00"]
    assert otadata[-2:] == ["0xd000", "0x2000"]
    assert store[-3:] == ["erase_region", "0xc10000", "0x2000"]
    assert store[store.index("--after") + 1] == "no_reset"
    assert "moves the store" in capsys.readouterr().out


def test_a_flash_over_the_same_table_keeps_the_store(monkeypatch, tmp_path):
    rc, fake = _flash_over(monkeypatch, tmp_path, _table(SLOTS_6M), SLOTS_6M)
    assert rc == 0
    assert _verbs(fake.calls) == ["read_flash", "erase_region", "write_flash"]


def test_a_flash_onto_a_blank_chip_erases_the_new_store_too(
        monkeypatch, tmp_path):
    _rc, fake = _flash_over(monkeypatch, tmp_path, b"\xff" * 0xC00, SLOTS_6M)
    assert _verbs(fake.calls).count("erase_region") == 2


def test_a_board_whose_table_cannot_be_read_is_not_written(
        monkeypatch, tmp_path):
    rc, fake = _flash_over(monkeypatch, tmp_path, b"", SLOTS_6M, codes=[2])
    assert rc == 2 and _verbs(fake.calls) == ["read_flash"]


def test_an_image_without_a_table_is_flashed_as_before(monkeypatch, tmp_path):
    """An app-only image has no table to compare; nothing is read."""
    _rc, fake = _flash(monkeypatch, tmp_path, P4)
    assert _verbs(fake.calls) == ["erase_region", "write_flash"]
