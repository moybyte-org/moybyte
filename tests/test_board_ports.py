"""The host half of the on-glass harness (tools/p4_autotest): which board is on
which port, and what its answers mean.

Port resolution exists because of 2026-08-24: the ttyACM numbers shuffled
between sessions and a measurement run drove the T-Deck believing it was the
P4 -- caught only by the boot log growing an SD card and a trackball. The
mitigation is two layers of DATA (the [serial] usb id narrows, the board's own
`_ota_build.BOARD` settles), and these tests pin the resolution logic and the
declarations.

Reply reading exists because of 2026-08-27: a device exception, a lost reply
and an unparseable value all came back from `pyval` as the same None, so a
board saying `NameError` in plain words once per command was read for a
fortnight as a flaky serial upload. These run the reader against a fake board
-- including one that interleaves the unsolicited output a real one prints --
with no hardware on the desk.
"""

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import p4_autotest  # noqa: E402

P4 = os.path.join(ROOT, "firmware", "esp32_p4_wifi6_touch_lcd_7b")
TDECK = os.path.join(ROOT, "firmware", "lilygo_t_deck_plus_mainline")
GUITION = os.path.join(ROOT, "firmware", "guition_jc3248w535")


# -- the declarations are data, and their SHAPE is part of the contract ------


def test_every_flashable_board_declares_a_usb_id():
    import re
    for d in (P4, TDECK, GUITION):
        usb = p4_autotest.declared_serial(d)["usb"]
        assert usb and re.match(r"^[0-9a-f]{4}:[0-9a-f]{4}$", usb), (d, usb)


def test_the_s3_twins_share_an_id_and_the_p4_does_not():
    """The AMBIGUITY IS A FACT, not a bug: both S3s enumerate as the SoC's own
    USB-Serial/JTAG, so usb id alone cannot split them -- which is exactly why
    find_port probes identities there and why verify_board() exists at all."""
    p4 = p4_autotest.declared_serial(P4)["usb"]
    td = p4_autotest.declared_serial(TDECK)["usb"]
    gu = p4_autotest.declared_serial(GUITION)["usb"]
    assert td == gu
    assert p4 != td


def test_declared_board_ids_are_the_ota_names():
    assert p4_autotest.declared_board_id(P4) == "p4"
    assert p4_autotest.declared_board_id(TDECK) == "tdeck"
    assert p4_autotest.declared_board_id(GUITION) == "guition_s3"


# -- usb_id_of walks sysfs ---------------------------------------------------


def test_usb_id_of_reads_a_fake_sysfs_tree(tmp_path):
    dev = tmp_path / "usbdev"
    dev.mkdir()
    (dev / "idVendor").write_text("1A86\n")     # case + newline are real-world
    (dev / "idProduct").write_text("55d3\n")
    iface = dev / "1-2:1.0"
    iface.mkdir()
    tty = tmp_path / "class_tty" / "ttyACM7"
    tty.mkdir(parents=True)
    (tty / "device").symlink_to(iface)
    got = p4_autotest.usb_id_of("/dev/ttyACM7", sys_tty=str(tmp_path / "class_tty"))
    assert got == "1a86:55d3"


def test_usb_id_of_answers_none_off_linux_or_for_non_usb(tmp_path):
    assert p4_autotest.usb_id_of("/dev/ttyACM9",
                                 sys_tty=str(tmp_path / "nope")) is None


# -- find_port resolution ----------------------------------------------------


def _usb_map(m):
    return lambda p: m.get(p)


def test_a_unique_usb_match_resolves_without_probing():
    got = p4_autotest.find_port(
        P4, ports=["/dev/ttyACM0", "/dev/ttyACM3"],
        usb_of=_usb_map({"/dev/ttyACM0": "303a:1001",
                         "/dev/ttyACM3": "1a86:55d3"}),
        prober=lambda p: pytest.fail("must not probe a unique match"))
    assert got == "/dev/ttyACM3"


def test_no_match_names_every_port_it_saw():
    with pytest.raises(RuntimeError) as e:
        p4_autotest.find_port(
            P4, ports=["/dev/ttyACM0"],
            usb_of=_usb_map({"/dev/ttyACM0": "303a:1001"}))
    assert "1a86:55d3" in str(e.value) and "/dev/ttyACM0" in str(e.value)


def test_twins_are_split_by_asking_each_board():
    answers = {"/dev/ttyACM1": "guition_s3", "/dev/ttyACM2": "tdeck"}
    got = p4_autotest.find_port(
        TDECK, ports=["/dev/ttyACM1", "/dev/ttyACM2"],
        usb_of=_usb_map({"/dev/ttyACM1": "303a:1001",
                         "/dev/ttyACM2": "303a:1001"}),
        prober=lambda p: answers[p])
    assert got == "/dev/ttyACM2"


def test_twins_with_no_matching_answer_refuse_with_the_picture():
    with pytest.raises(RuntimeError) as e:
        p4_autotest.find_port(
            TDECK, ports=["/dev/ttyACM1", "/dev/ttyACM2"],
            usb_of=_usb_map({"/dev/ttyACM1": "303a:1001",
                             "/dev/ttyACM2": "303a:1001"}),
            prober=lambda p: None)
    assert "tdeck" in str(e.value)


def test_a_non_attach_board_never_probes_bystanders():
    """Probing means OPENING, and opening a CH343 reboots its board -- a
    resolver must not reboot boards it is merely ruling out. Two CH343s is
    therefore a refusal, not a probe."""
    with pytest.raises(RuntimeError) as e:
        p4_autotest.find_port(
            P4, ports=["/dev/ttyACM3", "/dev/ttyACM5"],
            usb_of=_usb_map({"/dev/ttyACM3": "1a86:55d3",
                             "/dev/ttyACM5": "1a86:55d3"}),
            prober=lambda p: pytest.fail("probed a non-attach board"))
    assert "--port" in str(e.value)


def test_a_lone_twin_is_still_interrogated():
    """One 303a:1001 on the bus does NOT mean it is the board asked for --
    with the other S3 unplugged, the survivor 'uniquely' matches both names.
    Observed live 2026-08-24: the Guition resolved to the T-Deck's port."""
    with pytest.raises(RuntimeError) as e:
        p4_autotest.find_port(
            GUITION, ports=["/dev/ttyACM1"],
            usb_of=_usb_map({"/dev/ttyACM1": "303a:1001"}),
            prober=lambda p: "tdeck")
    assert "tdeck" in str(e.value) and "guition_s3" in str(e.value)


def test_a_lone_silent_twin_is_accepted_for_downstream_verify():
    """A board that does not answer might be wedged or mid-boot; find_port
    hands it over and P4Board.verify_board() is the second gate."""
    got = p4_autotest.find_port(
        GUITION, ports=["/dev/ttyACM1"],
        usb_of=_usb_map({"/dev/ttyACM1": "303a:1001"}),
        prober=lambda p: None)
    assert got == "/dev/ttyACM1"


# -- reading replies off a noisy wire ----------------------------------------


class _FakeBoard:
    """A board that answers one line per command it receives -- and prints the
    unsolicited lines a real one does while it does (PERF at every diag tick,
    the BLE keyboard's background scan retry). Implements the four members the
    driver touches: read/write/flush/close."""

    def __init__(self, answers, noise=()):
        self.port = "/dev/fake"
        self._answers = list(answers)
        self._noise = list(noise)
        self._out = b""
        self._partial = b""
        self.sent = []

    def write(self, data):
        self._partial += data          # the writer PACES, so a line arrives in
        while b"\n" in self._partial:  # slices; only a newline is a command
            cmd, self._partial = self._partial.split(b"\n", 1)
            self.sent.append(cmd.decode())
            for line in self._noise:
                self._out += line.encode() + b"\r\n"
            if self._answers:
                self._out += self._answers.pop(0).encode() + b"\r\n"
        return len(data)

    def flush(self):
        pass

    def read(self, n):
        out, self._out = self._out[:n], self._out[n:]
        return out

    def close(self):
        pass


def _driver(answers, noise=()):
    fake = _FakeBoard(answers, noise)
    return p4_autotest.P4Board(None, ser=fake), fake


NOISE = ("Moybyte BLE keyboard: scanning",
         "PERF fps=0/62 busy=3ms draw=33 flush=1 logic=0 render=0 cart=-")


def test_unsolicited_lines_do_not_swallow_a_reply():
    """Both of these really do land mid-exchange on a diag-enabled board (wire
    capture, 2026-08-27). A reply is found by what it SAYS, not by arriving
    alone."""
    board, _ = _driver(["PY 256"], noise=NOISE)
    assert board.pyval("len(x)") == 256
    assert board.last_error is None


def test_a_device_exception_arrives_in_the_devices_own_words():
    """The wire carried `NameError: name 'verify_sig' isn't defined` once per
    command for a fortnight and the harness threw the text away, so every
    assertion downstream read `assert None is False` and named nothing."""
    board, _ = _driver(["PY ERR NameError: name 'verify_sig' isn't defined"])
    assert board.pyval("_verify_manifest(M, K)") is None
    assert "verify_sig" in board.last_error

    board, _ = _driver(["PY ERR NameError: name 'verify_sig' isn't defined"])
    with pytest.raises(p4_autotest.DeviceError) as e:
        board.pyval("_verify_manifest(M, K)", strict=True)
    assert "verify_sig" in str(e.value)


def test_a_silent_board_is_not_the_same_answer_as_a_raising_one():
    """Three outcomes, three messages: the point of the error channel is that
    'the board raised' and 'the board said nothing' stop being one None."""
    board, _ = _driver([], noise=NOISE)          # answers nothing at all
    assert board.pyval("ws.frames", timeout=0.05) is None
    assert "no reply" in board.last_error
    with pytest.raises(p4_autotest.DeviceError):
        board.pyval("ws.frames", timeout=0.05, strict=True)


def test_a_chunked_upload_survives_the_same_noise():
    """The multi-line path is the one the OTA-verifier test drives, and the one
    that was blamed. Four round trips: the two setup lines, one chunk, the
    exec."""
    board, fake = _driver(["PY 1", "PY ok", "PY 1", "PY None"], noise=NOISE)
    assert board.pyexec("a = 1\nb = a + 1\n") is True
    assert board.last_error is None
    assert sum(1 for s in fake.sent if "ws._up.__setitem__" in s) == 1


def test_a_rejected_chunk_names_the_chunk():
    """A corrupted upload IS the failure this was misdiagnosed as, so when it
    does happen it has to say which chunk and what the board said."""
    board, _ = _driver(["PY 1", "PY ok", "PY ERR SyntaxError: invalid syntax"])
    assert board.pyexec("a = 1\nb = a + 1\n") is False
    assert "chunk 0" in board.last_error and "SyntaxError" in board.last_error
