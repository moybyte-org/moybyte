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
WEB_RUNNER = ROOT / "firmware" / "web_runner"
BOARDS = {"tdeck": TDECK, "p4": P4, "guition-s3": GUITION}

SUBCOMMANDS = ("erase_region", "write_flash")


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
